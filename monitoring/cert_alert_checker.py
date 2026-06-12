"""
monitoring/cert_alert_checker.py — Certificate expiry alert dispatch.

Mirrors monitoring/license_checker.py but for "system" certificates — ones
not tied to a device/sensor (SAML IdP/SP signing certs today; OIDC JWKS or
TLS front-end certs could plug in later).

Entry point: check_cert(cert_key, days_left, not_after, friendly_name)

Bucket transitions fire alert events through the global alert profile:
  ok -> expiring  (< 30 days)  severity=warning,   event_type=cert_expiring
  *  -> critical  (<  7 days)  severity=critical,  event_type=cert_critical
  *  -> expired   (<= 0 days)  severity=critical,  event_type=cert_expired
  *  -> ok        (recovery)   severity=recovery,  event_type=cert_ok

Transition state is in-memory only (module dict). A server restart re-learns
buckets; one re-notification per still-at-risk cert is acceptable, and
alert-profile session tracking prevents storms within an active incident.

Called from core/auth_health.py (_refresh_saml and boot_sanity_pass).
"""
from __future__ import annotations

import datetime
import threading
import time

from core.logger import log


# Module-level transition state. key = (scope, side) e.g. ("saml", "idp").
_last_threshold: dict[tuple[str, str], str] = {}
_state_lock = threading.Lock()


def _bucket(days: int) -> str:
    if days <= 0:
        return "expired"
    if days < 7:
        return "critical"
    if days < 30:
        return "expiring"
    return "ok"


# Bucket → (event_type, severity, trigger_state)
_BUCKET_META = {
    "expiring": ("cert_expiring", "warning",  "warning"),
    "critical": ("cert_critical", "critical", "down"),
    "expired":  ("cert_expired",  "critical", "down"),
    "ok":       ("cert_ok",       "recovery", "up"),
}


def check_cert(cert_key: tuple[str, str], days_left: int,
               not_after: str, friendly_name: str) -> None:
    """Evaluate one cert; emit alert event iff the bucket transitioned."""
    new_bucket = _bucket(int(days_left or 0))
    with _state_lock:
        old_bucket = _last_threshold.get(cert_key, "ok")
        if new_bucket == old_bucket:
            return
        _last_threshold[cert_key] = new_bucket

    scope, side = cert_key
    sid   = f"cert_{scope}_{side}"
    did   = "_system"
    dname = "PingWatch"
    sname = friendly_name

    log.info(f"cert_alert: {sname} bucket {old_bucket} -> {new_bucket} "
             f"(days_left={days_left}, not_after={not_after})")

    if new_bucket == "ok":
        _fire_recovery(cert_key, old_bucket, did, sid, dname, sname,
                       days_left, not_after)
        return

    _fire_alert(cert_key, old_bucket, new_bucket, did, sid, dname, sname,
                days_left, not_after)


def _build_ctx(did: str, sid: str, dname: str, sname: str,
               bucket: str, days_left: int, not_after: str) -> dict:
    event_type, severity, _trig = _BUCKET_META[bucket]
    if bucket == "expired":
        detail = f"{sname} has EXPIRED (was valid until {not_after})"
    elif bucket == "ok":
        detail = f"{sname} renewed — valid until {not_after} ({days_left}d left)"
    else:
        detail = f"{sname} expires in {days_left}d ({not_after})"
    return {
        "did": did, "sid": sid, "dname": dname, "sname": sname,
        "host": "", "stype": "cert", "grp": "",
        "state": bucket, "ms": 0, "loss_pct": 0, "detail": detail,
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime(
                  "%Y-%m-%dT%H:%M:%SZ"),
        "severity":    severity,
        "event_type":  event_type,
        "expiry_date": not_after,
        "days_left":   days_left,
    }


def _fire_alert(cert_key: tuple[str, str], old_bucket: str, new_bucket: str,
                did: str, sid: str, dname: str, sname: str,
                days_left: int, not_after: str) -> None:
    from db.alert_profiles import (
        db_get_profile_for_scope, db_get_action_template,
        db_get_stage_state, db_record_stage_fire,
        db_clear_stage_state_for_sensor,
    )
    from db.alert_events import db_log_event, db_has_acked_event
    from monitoring.alert_dispatchers import dispatch, check_maintenance

    profile = db_get_profile_for_scope("global", "")
    if not profile or not profile.get("enabled", True):
        log.debug(f"cert_alert: no enabled global profile, skipping dispatch for {sid}")
        return
    stages = profile.get("stages") or []
    if not stages:
        return

    # New bucket = new incident session — clear any prior session for clean escalation.
    try:
        db_clear_stage_state_for_sensor(did, sid)
    except Exception as e:
        log.warning(f"cert_alert: clear stage state error: {e}")

    ctx = _build_ctx(did, sid, dname, sname, new_bucket, days_left, not_after)
    _event_type, _severity, trigger = _BUCKET_META[new_bucket]
    trigger_states = (trigger,)

    suppressed, mw_name = check_maintenance(ctx)
    if suppressed:
        log.debug(f"cert_alert: suppressed by maintenance window {mw_name!r}")
        # Roll the bucket back so the next periodic check re-detects this
        # transition after the window ends. check_cert commits the bucket
        # BEFORE dispatch, so returning here without the rollback consumed
        # the transition — the next chance to notify was the NEXT bucket
        # (e.g. an "expiring" alert silenced at 30d resurfaced only at 7d).
        with _state_lock:
            if _last_threshold.get(cert_key) == new_bucket:
                _last_threshold[cert_key] = old_bucket
        return

    now = time.time()
    session = str(int(now))

    for stage in stages:
        trig = stage.get("trigger_state")
        if trig not in trigger_states:
            continue
        action_ids = stage.get("action_ids") or []
        if not action_ids:
            continue

        state = db_get_stage_state(stage["id"], did, sid)
        should_fire = (not state
                       or state.get("active_session") != session)
        if not should_fire:
            continue

        gated_by_ack = False
        try:
            if db_has_acked_event(profile["id"], did, sid):
                gated_by_ack = True
        except Exception as e:
            log.warning(f"cert_alert: ack-gate check error: {e}")

        log.info(f"cert_alert: dispatching profile={profile['name']!r} "
                 f"stage={stage['id']} trigger={trig} cert={sname}"
                 f"{' [ack-silenced]' if gated_by_ack else ''}")
        if not gated_by_ack:
            for aid in action_ids:
                tpl = db_get_action_template(aid)
                if not tpl:
                    log.warning(f"cert_alert: missing action template {aid}")
                    continue
                try:
                    dispatch(tpl["atype"], tpl["config"], ctx)
                except Exception as e:
                    log.error(f"cert_alert: dispatch error (aid={aid}): {e}")

        db_record_stage_fire(stage["id"], did, sid, session)
        try:
            db_log_event(profile["id"], stage["id"], profile["name"],
                         ctx, state="active")
        except Exception as e:
            log.warning(f"cert_alert: db_log_event error: {e}")


def _fire_recovery(cert_key: tuple[str, str], old_bucket: str,
                   did: str, sid: str, dname: str, sname: str,
                   days_left: int, not_after: str) -> None:
    """Fire recovery stages when a cert transitions back to ok."""
    from db.alert_profiles import (
        db_get_profile_for_scope, db_get_action_template,
        db_get_stage_state, db_record_stage_fire,
        db_clear_stage_state_for_sensor,
    )
    from db.alert_events import db_auto_resolve_event
    from monitoring.alert_dispatchers import dispatch, check_maintenance

    profile = db_get_profile_for_scope("global", "")
    if not profile or not profile.get("enabled", True):
        return
    stages = profile.get("stages") or []
    if not stages:
        return

    # Map old failure bucket → (recovery trigger, failure trigger it recovered from)
    if old_bucket in ("critical", "expired"):
        recovery_triggers = ("down_recovered",)
        failure_triggers  = ("down",)
    elif old_bucket == "expiring":
        recovery_triggers = ("warning_recovered",)
        failure_triggers  = ("warning",)
    else:
        return

    # Only fire recovery if a failure stage actually dispatched earlier
    had_prior = False
    for s in stages:
        if s.get("trigger_state") not in failure_triggers:
            continue
        st = db_get_stage_state(s["id"], did, sid)
        if st and st.get("fire_count", 0) > 0:
            had_prior = True
            break
    if not had_prior:
        return

    ctx = _build_ctx(did, sid, dname, sname, "ok", days_left, not_after)

    suppressed, mw_name = check_maintenance(ctx)
    if suppressed:
        log.debug(f"cert_alert: recovery suppressed by maintenance window {mw_name!r}")
        return

    for stage in stages:
        trig = stage.get("trigger_state")
        if trig not in recovery_triggers:
            continue
        action_ids = stage.get("action_ids") or []
        if not action_ids:
            continue

        rec_state = db_get_stage_state(stage["id"], did, sid)
        if rec_state and rec_state.get("fire_count", 0) > 0:
            continue

        log.info(f"cert_alert: recovery dispatch profile={profile['name']!r} "
                 f"stage={stage['id']} cert={sname}")
        for aid in action_ids:
            tpl = db_get_action_template(aid)
            if not tpl:
                continue
            try:
                dispatch(tpl["atype"], tpl["config"], ctx)
            except Exception as e:
                log.error(f"cert_alert: recovery dispatch error: {e}")

        db_record_stage_fire(stage["id"], did, sid, "")

    try:
        db_auto_resolve_event(profile["id"], did, sid)
    except Exception as e:
        log.warning(f"cert_alert: auto-resolve event error: {e}")
    try:
        db_clear_stage_state_for_sensor(did, sid)
    except Exception as e:
        log.warning(f"cert_alert: clear stage state error: {e}")
