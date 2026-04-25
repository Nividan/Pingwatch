"""
monitoring/license_checker.py — License expiration check + alert profile dispatch.

Two-phase architecture:

  Phase A  Detect state transitions (ok/warn/crit).  On change: update DB,
           log flap event, SSE broadcast, clear old alert session.
  Phase B  Evaluate alert profile stages for ALL non-ok licenses — delay,
           repeat, and session tracking via the existing alert_profile_state
           table (same mechanism sensors use).

Called from:
  - routes/licenses.py POST/PATCH  (immediate, on user action)
  - db/persistence.py autosave_loop every ~6 hours  (time-driven transitions)
"""
from __future__ import annotations

import datetime
import time

from core.logger import log


# ── Public entry point ──────────────────────────────────────────

def check_license_expirations() -> None:
    from core.app_state import STATE
    from db.licenses import db_get_all_licenses, db_update_license_status
    from db.events   import db_log_flap, db_auto_resolve_flap, db_has_open_flap
    from db.alert_profiles import db_clear_stage_state_for_sensor

    licenses = db_get_all_licenses()
    if not licenses:
        return

    today = datetime.date.today()
    now   = time.time()
    ts    = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")

    transitioned: set = set()   # lic IDs that changed state on this run

    # ── Phase A: state transition detection (and stale-flap replay) ──
    for lic in licenses:
        try:
            expiry = datetime.date.fromisoformat(lic["expiry_date"])
        except (ValueError, TypeError):
            continue

        days_left = (expiry - today).days
        crit_days = int(lic.get("crit_days") or 0)
        warn_days = int(lic.get("warn_days") or 30)

        new_status = "ok"
        if days_left <= crit_days:
            new_status = "crit"
        elif days_left <= warn_days:
            new_status = "warn"

        old_status = lic.get("last_status") or "ok"
        transitioned_now = (new_status != old_status)

        did  = lic["did"]
        dev  = STATE.devices.get(did)
        dname = dev.name if dev else did
        host  = dev.host if dev else ""
        sid   = f"lic_{lic['id']}"

        # Replay path: license is still in a non-OK state but has no open
        # flap (operator resolved the original, or it was trimmed). Without
        # this, the Events tab stays empty for chronic problems even though
        # the License widget keeps showing the issue.
        replay = (
            not transitioned_now
            and new_status != "ok"
            and not db_has_open_flap(
                did, sid, directions=("license_warn", "license_crit"))
        )

        if not transitioned_now and not replay:
            continue

        # ── State changed (or replay) — persist ──
        if transitioned_now:
            db_update_license_status(lic["id"], new_status)
            lic["last_status"] = new_status   # keep in-memory copy in sync
            lic["updated_at"]  = now
            transitioned.add(lic["id"])

            # Clear old alert session (fresh start for escalation) — only
            # on a real transition. Replay must NOT reset the session, or
            # we'd re-spam the user every check while the issue persists.
            try:
                db_clear_stage_state_for_sensor(did, sid)
            except Exception as e:
                log.warning(f"license_checker: clear stage state error: {e}")

        # ── Build detail string ──
        if new_status == "ok":
            direction = "license_ok"
            detail = (
                f"License '{lic['license_name']}' renewed — "
                f"expires {lic['expiry_date']} ({days_left} days)"
            )
            log.info(f"LICENSE OK: {dname}/{lic['license_name']} — "
                     f"expires {lic['expiry_date']} ({days_left} days)")
            db_auto_resolve_flap(did, sid, ts,
                                 directions=("license_warn", "license_crit"))
            _fire_recovery(lic, old_status, did, sid, dname, host,
                           getattr(dev, "group", "") if dev else "")
        elif new_status == "crit":
            direction = "license_crit"
            if days_left < 0:
                detail = (
                    f"License '{lic['license_name']}' EXPIRED "
                    f"{-days_left} day{'s' if -days_left != 1 else ''} ago "
                    f"({lic['expiry_date']})"
                )
            else:
                detail = (
                    f"License '{lic['license_name']}' expires "
                    f"{lic['expiry_date']} "
                    f"({days_left} day{'s' if days_left != 1 else ''} left)"
                )
            if replay:
                log.info(f"LICENSE CRIT (replay): {dname}/{lic['license_name']} — "
                         f"recreating event after prior resolve")
            else:
                log.error(f"LICENSE CRIT: {dname}/{lic['license_name']} — {detail}")
        else:  # warn
            direction = "license_warn"
            detail = (
                f"License '{lic['license_name']}' expiring soon — "
                f"{lic['expiry_date']} "
                f"({days_left} day{'s' if days_left != 1 else ''} left)"
            )
            if replay:
                log.info(f"LICENSE WARN (replay): {dname}/{lic['license_name']} — "
                         f"recreating event after prior resolve")
            else:
                log.warning(
                    f"LICENSE WARN: {dname}/{lic['license_name']} — {detail}")

        # Log flap event (Events tab)
        if new_status != "ok":
            import json as _json
            from core.raw_data import build_flap_raw_data
            _lic_raw = build_flap_raw_data(
                None, None, direction,
                {"license_name": lic["license_name"],
                 "expires_at": lic.get("expiry_date"),
                 "days_left": days_left}
            )
            db_log_flap({
                "ts": ts, "did": did, "sid": sid,
                "dname": dname, "sname": lic["license_name"],
                "host": host, "stype": "license",
                "detail": detail, "direction": direction,
                "raw_data": _json.dumps(_lic_raw),
            })

        # SSE broadcast
        STATE._broadcast("license_status", {
            "did":          did,
            "license_id":   lic["id"],
            "license_name": lic["license_name"],
            "status":       new_status,
            "days_left":    days_left,
            "detail":       detail,
        })

    # ── Phase B: alert stage evaluation for ALL non-ok licenses ──
    for lic in licenses:
        if lic["last_status"] == "ok":
            continue
        just_transitioned = lic["id"] in transitioned
        status_since = now if just_transitioned else (lic.get("updated_at") or now)
        _evaluate_license_stages(lic, status_since, just_transitioned, now)


# ── Alert stage evaluator (delay + repeat + session tracking) ───

def _evaluate_license_stages(lic: dict, status_since: float,
                             just_transitioned: bool, now: float) -> None:
    """Evaluate alert profile stages for one non-ok license.

    Uses the same alert_profile_state table as the sensor engine for
    session tracking, delay, and repeat logic.
    """
    from core.app_state import STATE
    from db.alert_profiles import (
        db_get_profile_for_scope, db_get_action_template,
        db_get_stage_state, db_record_stage_fire,
    )
    from db.alert_events import db_log_event, db_has_acked_event
    from monitoring.alert_dispatchers import dispatch, check_maintenance

    did = lic["did"]
    dev = STATE.devices.get(did)

    # Respect device-level alert muting
    if dev and getattr(dev, "alerts_muted", False):
        return

    dname = dev.name if dev else did
    host  = dev.host if dev else ""
    grp   = getattr(dev, "group", "") if dev else ""
    sid   = f"lic_{lic['id']}"

    # Resolve profile: device → group → global cascade
    profile = (
        db_get_profile_for_scope("device", did)
        or (db_get_profile_for_scope("group", grp) if grp else None)
        or db_get_profile_for_scope("global", "")
    )
    if not profile or not profile.get("enabled", True):
        return
    stages = profile.get("stages") or []
    if not stages:
        return

    # Map license status → alert trigger states
    status = lic["last_status"]
    if status == "crit":
        trigger_states = ("down",)
    elif status == "warn":
        trigger_states = ("warning",)
    else:
        return

    _SEV   = {"down": "critical", "warning": "warning"}
    _ETYPE = {"down": "license_expired", "warning": "license_expiring"}

    # Build detail string for dispatchers
    try:
        expiry = datetime.date.fromisoformat(lic["expiry_date"])
        days_left = (expiry - datetime.date.today()).days
    except (ValueError, TypeError):
        days_left = 0
    if status == "crit" and days_left < 0:
        detail = (f"License '{lic['license_name']}' EXPIRED "
                  f"{-days_left}d ago ({lic['expiry_date']})")
    elif status == "crit":
        detail = (f"License '{lic['license_name']}' expires "
                  f"{lic['expiry_date']} ({days_left}d left)")
    else:
        detail = (f"License '{lic['license_name']}' expiring soon — "
                  f"{lic['expiry_date']} ({days_left}d left)")

    ctx = {
        "did": did, "sid": sid, "dname": dname, "sname": lic["license_name"],
        "host": host, "stype": "license", "grp": grp,
        "state": status, "ms": 0, "loss_pct": 0, "detail": detail,
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime(
                  "%Y-%m-%dT%H:%M:%SZ"),
        "severity":   _SEV.get(trigger_states[0], "info"),
        "event_type": _ETYPE.get(trigger_states[0], "license"),
        "expiry_date": lic.get("expiry_date", ""),
        "days_left":   days_left,
    }

    # Maintenance window check
    suppressed, mw_name = check_maintenance(ctx)
    if suppressed:
        log.debug(f"license_checker: alert suppressed by "
                  f"maintenance window {mw_name!r}")
        return

    session = str(int(status_since))
    first_stage_dispatched = False

    for stage in stages:
        trig = stage.get("trigger_state")
        if trig not in trigger_states:
            continue
        action_ids = stage.get("action_ids") or []
        if not action_ids:
            continue

        delay  = int(stage.get("delay_s") or 0)
        repeat = int(stage.get("repeat_min") or 0)

        # ── Delay check ──
        # On a fresh transition, bypass delay for the FIRST matching stage
        # (immediate notification). Subsequent stages respect their delays
        # and will fire on the next periodic run.
        if just_transitioned and not first_stage_dispatched:
            pass   # bypass delay — fire immediately
        elif (now - status_since) < delay:
            continue   # delay not yet elapsed

        # ── Session / repeat check ──
        state = db_get_stage_state(stage["id"], did, sid)
        should_fire = False
        if not state or state.get("active_session") != session:
            should_fire = True    # never fired in this session
        elif repeat > 0 and (now - (state.get("last_fire_ts") or 0)) >= (repeat * 60):
            should_fire = True    # repeat interval elapsed

        if not should_fire:
            continue

        # ── ACK gate ──
        # If the user has already ACK'd an event for this license, stay silent.
        # We still record the stage fire below so repeat timers advance correctly;
        # only the outbound dispatch (email/webhook/syslog) is suppressed.
        gated_by_ack = False
        try:
            if db_has_acked_event(profile["id"], did, sid):
                gated_by_ack = True
        except Exception as e:
            log.warning(f"license_checker: ack-gate check error: {e}")

        # ── Dispatch ──
        log.info(
            f"license_checker: dispatching profile={profile['name']!r} "
            f"stage={stage['id']} trigger={trig} "
            f"license={lic['license_name']!r} device={dname}"
            f"{' [first-alert]' if just_transitioned and not first_stage_dispatched else ''}"
            f"{' [repeat]' if state and state.get('active_session') == session else ''}"
            f"{' [ack-silenced]' if gated_by_ack else ''}"
        )
        if not gated_by_ack:
            for aid in action_ids:
                tpl = db_get_action_template(aid)
                if not tpl:
                    log.warning(f"license_checker: missing template {aid}")
                    continue
                try:
                    dispatch(tpl["atype"], tpl["config"], ctx)
                except Exception as e:
                    log.error(f"license_checker: dispatch error (aid={aid}): {e}")

        # Record fire + log alert event
        db_record_stage_fire(stage["id"], did, sid, session)
        try:
            first_fire = (not state or state.get("active_session") != session)
            if first_fire:
                db_log_event(profile["id"], stage["id"], profile["name"],
                             ctx, state="active")
        except Exception as e:
            log.warning(f"license_checker: db_log_event error: {e}")

        if just_transitioned and not first_stage_dispatched:
            first_stage_dispatched = True


# ── Recovery dispatch ───────────────────────────────────────────

def _fire_recovery(lic: dict, old_status: str, did: str, sid: str,
                   dname: str, host: str, grp: str) -> None:
    """Fire recovery stages when a license returns to ok.

    Mirrors the sensor engine's recovery logic: find recovery stages
    that match the previous failure type, dispatch once, auto-resolve
    the active alert event.
    """
    from db.alert_profiles import (
        db_get_profile_for_scope, db_get_action_template,
        db_get_stage_state, db_record_stage_fire,
        db_clear_stage_state_for_sensor,
    )
    from db.alert_events import db_auto_resolve_event
    from monitoring.alert_dispatchers import dispatch, check_maintenance

    profile = (
        db_get_profile_for_scope("device", did)
        or (db_get_profile_for_scope("group", grp) if grp else None)
        or db_get_profile_for_scope("global", "")
    )
    if not profile or not profile.get("enabled", True):
        return
    stages = profile.get("stages") or []
    if not stages:
        return

    # Map old failure status → recovery trigger
    if old_status == "crit":
        recovery_triggers = ("down_recovered",)
        failure_triggers  = ("down",)
    elif old_status == "warn":
        recovery_triggers = ("warning_recovered",)
        failure_triggers  = ("warning",)
    else:
        return

    # Only fire recovery if a failure stage actually fired previously
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

    try:
        expiry = datetime.date.fromisoformat(lic["expiry_date"])
        days_left = (expiry - datetime.date.today()).days
    except (ValueError, TypeError):
        days_left = 0
    detail = (f"License '{lic['license_name']}' renewed — "
              f"expires {lic['expiry_date']} ({days_left}d)")

    ctx = {
        "did": did, "sid": sid, "dname": dname, "sname": lic["license_name"],
        "host": host, "stype": "license", "grp": grp,
        "state": "ok", "ms": 0, "loss_pct": 0, "detail": detail,
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime(
                  "%Y-%m-%dT%H:%M:%SZ"),
        "severity": "recovery", "event_type": "license_ok",
        "expiry_date": lic.get("expiry_date", ""),
        "days_left":   days_left,
    }

    suppressed, mw_name = check_maintenance(ctx)
    if suppressed:
        log.debug(f"license_checker: recovery suppressed by "
                  f"maintenance window {mw_name!r}")
        return

    for stage in stages:
        trig = stage.get("trigger_state")
        if trig not in recovery_triggers:
            continue
        action_ids = stage.get("action_ids") or []
        if not action_ids:
            continue

        # Recovery fires once (check if already fired)
        rec_state = db_get_stage_state(stage["id"], did, sid)
        if rec_state and rec_state.get("fire_count", 0) > 0:
            continue

        log.info(f"license_checker: recovery dispatch "
                 f"profile={profile['name']!r} stage={stage['id']} "
                 f"license={lic['license_name']!r} device={dname}")
        for aid in action_ids:
            tpl = db_get_action_template(aid)
            if not tpl:
                continue
            try:
                dispatch(tpl["atype"], tpl["config"], ctx)
            except Exception as e:
                log.error(f"license_checker: recovery dispatch error: {e}")

        db_record_stage_fire(stage["id"], did, sid, "")

    # Auto-resolve alert event + clear all stage state
    try:
        db_auto_resolve_event(profile["id"], did, sid)
    except Exception as e:
        log.warning(f"license_checker: auto-resolve event error: {e}")
    try:
        db_clear_stage_state_for_sensor(did, sid)
    except Exception as e:
        log.warning(f"license_checker: clear stage state error: {e}")
