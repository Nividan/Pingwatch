"""
monitoring/alert_profile_engine.py — PRTG-style alert profile engine.

Pure-functional helpers driven by the probe loop. There is no thread of its
own — every probe cycle calls evaluate_and_fire() once for the active sensor.

Concepts:

    Profile         A complete escalation policy bound to one scope.
    Stage           One row inside a profile: trigger_state + delay + repeat
                    + action_template_id.
    Session         The contiguous period a sensor has been in a failing state.
                    Identified by str(sensor._down_since_ts). Resets when the
                    sensor recovers and goes down again.
    Cascade         sensor → device → group → global. First match wins. If no
                    profile resolves, no alerts fire (intentional "off" state).

Cache invalidation:

    Sensor caches its resolved profile id on _resolved_profile_id /
    _resolved_profile_ver. Any profile write bumps STATE._profile_cache_ver,
    forcing every sensor to re-resolve on its next probe.
"""
from __future__ import annotations


import threading
import time

from core.logger import log


# ── Scope-level profile cache ─────────────────────────────────────
# Keyed by (scope_type, scope_value) → profile dict (or None).
# Invalidated when _profile_cache_ver changes. Thread-safe via a lock;
# reads are fast (held briefly), writes only happen on cache miss or version bump.

_scope_cache: dict = {}          # (scope_type, scope_value) → profile | None
_scope_cache_ver: int = -1       # last version the cache was built for
_scope_cache_lock = threading.Lock()


def _get_scope_cached(scope_type: str, scope_value: str, cur_ver: int):
    """Return profile from scope cache; None if cached as 'not found'; _MISS if not queried yet or stale."""
    with _scope_cache_lock:
        if _scope_cache_ver != cur_ver:
            return _MISS   # cache is stale — caller must re-query
        key = (scope_type, scope_value)
        if key not in _scope_cache:
            return _MISS   # never queried this scope under this version
        return _scope_cache[key]


def _set_scope_cached(scope_type: str, scope_value: str, cur_ver: int, profile):
    global _scope_cache_ver
    with _scope_cache_lock:
        if _scope_cache_ver != cur_ver:
            # Another thread already rebuilt the cache for a newer version; discard
            return
        _scope_cache[(scope_type, scope_value)] = profile
        _scope_cache_ver = cur_ver


def _invalidate_scope_cache(cur_ver: int):
    """Clear scope cache when the profile version has advanced."""
    global _scope_cache, _scope_cache_ver
    with _scope_cache_lock:
        if _scope_cache_ver != cur_ver:
            _scope_cache = {}
            _scope_cache_ver = cur_ver


_MISS = object()   # sentinel: cache miss (not found vs. known-None)


def _scope_get_profile(scope_type, scope_value, cur_ver, db_get_profile_for_scope):
    """Lookup a scope in the cache; fall back to DB on miss."""
    cached = _get_scope_cached(scope_type, scope_value, cur_ver)
    if cached is _MISS:
        # Cache is stale — rebuild entry from DB
        profile = db_get_profile_for_scope(scope_type, scope_value)
        _set_scope_cached(scope_type, scope_value, cur_ver, profile)
        return profile
    # cached is either a profile dict or None (explicitly cached as not found)
    return cached


# ── Profile resolution (cascade) ─────────────────────────────────

def resolve_profile_for_sensor(dev, sensor) -> dict | None:
    """Return the profile that applies to this sensor, or None.

    Two-level cache:
      1. Per-sensor: sensor._resolved_profile (full dict) — zero DB hits on
         the hot probe path once resolved for this version.
      2. Scope-level: _scope_cache — first probe for each unique scope queries
         the DB once; all subsequent sensors sharing that scope get a cache hit.
    Both caches are invalidated when STATE._profile_cache_ver changes (on any
    profile write).
    """
    from core.app_state import STATE
    from db.alert_profiles import db_get_profile_for_scope

    cur_ver = getattr(STATE, "_profile_cache_ver", 0)
    if (getattr(sensor, "_resolved_profile_ver", -1) == cur_ver
            and getattr(sensor, "_resolved_profile_id", None) is not None):
        pid = sensor._resolved_profile_id
        if pid == 0:
            return None
        # Return the cached profile dict — no DB hit on the hot probe path
        return getattr(sensor, "_resolved_profile", None)

    # Version changed (or first time) — invalidate scope cache if needed
    _invalidate_scope_cache(cur_ver)

    profile = None
    did = dev.did if hasattr(dev, "did") else getattr(dev, "device_id", "")
    sid = sensor.sensor_id

    # 1. sensor scope
    profile = _scope_get_profile("sensor", f"{did}/{sid}", cur_ver, db_get_profile_for_scope)
    # 2. device scope
    if not profile:
        profile = _scope_get_profile("device", did, cur_ver, db_get_profile_for_scope)
    # 3. group scope
    if not profile and getattr(dev, "group", ""):
        profile = _scope_get_profile("group", dev.group, cur_ver, db_get_profile_for_scope)
    # 4. global
    if not profile:
        profile = _scope_get_profile("global", "", cur_ver, db_get_profile_for_scope)

    sensor._resolved_profile_id  = profile["id"] if profile else 0
    sensor._resolved_profile     = profile           # cache full dict, not just id
    sensor._resolved_profile_ver = cur_ver
    return profile


# ── Context builder (mirrors the legacy alert_engine ctx shape) ──

_SEV_BY_STATE = {
    "down":              "critical",
    "warning":           "warning",
    "down_recovered":    "recovery",
    "warning_recovered": "recovery",
}

_EVENT_TYPE_BY_STATE = {
    "down":              "down",
    "warning":           "threshold_warning",
    "down_recovered":    "recovered",
    "warning_recovered": "threshold_ok",
}


def _build_ctx(dev, sensor, current_state: str, trigger_state: str,
               duration_s=None) -> dict:
    """Build the dispatcher ctx dict from live sensor state."""
    import datetime
    did = getattr(dev, "did", None) or getattr(dev, "device_id", "")
    ctx = {
        "did":       did,
        "sid":       sensor.sensor_id,
        "dname":     getattr(dev, "name", ""),
        "sname":     sensor.name,
        "host":      sensor.host,
        "stype":     sensor.stype,
        "grp":       getattr(dev, "group", ""),
        "state":     sensor._threshold_state,
        "ms":        sensor.last_ms,
        "loss_pct":  getattr(sensor, "loss_pct", 0),
        "detail":    sensor.last_detail,
        "ts":        datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "severity":  _SEV_BY_STATE.get(trigger_state, "info"),
        "event_type": _EVENT_TYPE_BY_STATE.get(trigger_state, trigger_state),
        # Enriched fields for professional email template
        "interval":      getattr(sensor, "interval", None),
        "warn_ms":       getattr(sensor, "warn_ms", None),
        "crit_ms":       getattr(sensor, "crit_ms", None),
        "loss_warn_pct": getattr(sensor, "loss_warn_pct", 0),
        "loss_crit_pct": getattr(sensor, "loss_crit_pct", 0),
        "total":         sensor.total,
        "success":       sensor.success,
        "uptime_pct":    round(sensor.success / sensor.total * 100, 1) if sensor.total else None,
        "avg_ms":        sensor.avg_ms,
        "min_ms":        sensor.min_ms,
        "max_ms":        sensor.max_ms,
        "alive":         sensor.alive,
        # Value-oriented sensor fields consumed by the email template's
        # type-specific renderers (TLS days, SNMP value/unit/OID, port).
        "last_value":    getattr(sensor, "last_value", None),
        "snmp_unit":     getattr(sensor, "snmp_unit", ""),
        "snmp_oid":      getattr(sensor, "snmp_oid", ""),
        "snmp_type":     getattr(sensor, "snmp_type", ""),
        "port":          getattr(sensor, "port", None),
    }
    if duration_s is not None:
        ctx["duration_s"] = int(duration_s)
    return ctx


# ── Current-state classifier ─────────────────────────────────────

def _classify(sensor) -> tuple:
    """Return (current_state, session_started_ts).

    current_state ∈ {"down", "warning", "ok"}
    session_started_ts is float epoch — None if state is ok.

    Mapping:
        sensor unreachable (ICMP/TCP fail)  → "down"
        threshold_state == "crit"           → "down"   (critical = same urgency as down)
        threshold_state == "warn"           → "warning"
    """
    if sensor._down_since_ts:
        return "down", sensor._down_since_ts
    if sensor._threshold_state == "crit" and sensor._threshold_triggered_ts:
        return "down", sensor._threshold_triggered_ts
    if sensor._threshold_state == "warn" and sensor._threshold_triggered_ts:
        return "warning", sensor._threshold_triggered_ts
    return "ok", None


def _session_key(ts: float | None) -> str:
    return str(int(ts)) if ts else ""


def _get_session_start_ts(stages, target_state, did, sid, db_get_stage_state):
    """Return the epoch timestamp when the failing session started, or None.

    Reads `active_session` from the first state-stage that fired; that field
    stores str(int(down_since_ts)) so we can recover the start time.
    """
    for s in stages:
        if s["trigger_state"] != target_state:
            continue
        st = db_get_stage_state(s["id"], did, sid)
        if st and st.get("fire_count", 0) > 0:
            session = st.get("active_session", "")
            if session:
                try:
                    return float(session)
                except (ValueError, TypeError):
                    pass
    return None


# ── Stage evaluator ──────────────────────────────────────────────

import re as _re
_DIAG_NUM_RE = _re.compile(r'\d+')

def _diag_log(sensor, label: str, msg: str) -> None:
    """Emit a DEBUG diagnostic when the reason CATEGORY changes.

    Rate-limited via sensor._alert_diag_last_reason. Varying numbers (elapsed
    seconds, delays) are normalized out of the key so a countdown like
    "0s so far"→"11s so far"→"27s so far" is treated as one stable reason.
    The logged message still shows the real numbers; only the comparison
    key is normalized.

    These "why didn't this alert fire" explanations are useful during triage
    but produce dozens of lines per oscillating sensor — kept at DEBUG so
    they don't drown out real Alert dispatch / recovery events at INFO.
    """
    key = _DIAG_NUM_RE.sub('N', msg)
    prev = getattr(sensor, "_alert_diag_last_reason", None)
    if key == prev:
        return
    sensor._alert_diag_last_reason = key
    log.debug(f"alert_profile_engine: {label} — {msg}")


def evaluate_and_fire(dev, sensor) -> None:
    """Run the profile evaluator for this sensor on this probe cycle.

    Called from Sensor._run_once after the existing flap/threshold blocks
    have already updated _down_since_ts / _threshold_triggered_ts.
    """
    did = getattr(dev, "did", None) or getattr(dev, "device_id", "")
    sid = sensor.sensor_id
    label = f"{getattr(dev, 'name', did)}/{getattr(sensor, 'name', sid)}"
    is_failing = bool(sensor._down_since_ts) or sensor._threshold_state in ("crit", "warn")

    from core.state import is_group_muted
    _grp = getattr(dev, "group", "") or ""
    _grp_muted = is_group_muted(_grp)
    if sensor.alerts_muted or getattr(dev, "alerts_muted", False) or _grp_muted:
        if is_failing:
            _diag_log(sensor, label, f"no dispatch — alerts muted "
                      f"(sensor={sensor.alerts_muted}, "
                      f"device={getattr(dev, 'alerts_muted', False)}, "
                      f"group={_grp_muted})")
        return

    profile = resolve_profile_for_sensor(dev, sensor)
    if not profile:
        if is_failing:
            dev_group = getattr(dev, "group", "") or "(none)"
            searched = (f"sensor={did}/{sid}, device={did}, group={dev_group!r}, global")
            try:
                from db.alert_profiles import db_list_profiles
                rows = db_list_profiles() or []
                dump = (", ".join(
                    f"id={r['id']} name={r.get('name','')!r} "
                    f"scope={r.get('scope_type','?')}:{r.get('scope_value','') or '-'} "
                    f"enabled={r.get('enabled',1)}"
                    for r in rows) or "(no profiles in DB)")
            except Exception as e:
                dump = f"(db_list_profiles failed: {e})"
            _diag_log(sensor, label,
                      f"no dispatch — no profile resolved. Searched: {searched}. "
                      f"Profiles in DB: {dump}")
        return
    if not profile.get("enabled", True):
        if is_failing:
            _diag_log(sensor, label, f"no dispatch — profile {profile['name']!r} is disabled")
        return
    stages = profile.get("stages") or []
    if not stages:
        if is_failing:
            _diag_log(sensor, label, f"no dispatch — profile {profile['name']!r} has no stages")
        return

    current_state, started_ts = _classify(sensor)
    now = time.time()

    from db.alert_profiles import (
        db_get_stage_state, db_record_stage_fire,
        db_clear_stage_state_for_sensor,
    )
    from db.alert_events  import db_log_event, db_auto_resolve_event
    from monitoring.alert_dispatchers import dispatch, check_maintenance

    fired_recovery = False
    fired_any      = False
    matched_trig   = False
    skip_reasons   = []

    for stage in stages:
        trig    = stage["trigger_state"]
        delay   = int(stage.get("delay_s") or 0)
        repeat  = int(stage.get("repeat_min") or 0)
        sid_key = stage["id"]

        action_ids        = stage.get("action_ids") or []
        is_state_stage    = trig in ("down", "warning") and bool(action_ids)
        is_recovery_stage = trig in ("down_recovered", "warning_recovered") and bool(action_ids)

        # ── State stages: fire while sensor is in matching state ──
        if is_state_stage:
            if current_state != trig:
                continue
            matched_trig = True
            if (now - started_ts) < delay:
                skip_reasons.append(f"stage#{sid_key} delay {delay}s not elapsed "
                                    f"({now - started_ts:.0f}s so far)")
                log.debug(f"alert: {did}/{sid} stage {sid_key} "
                          f"delay {delay}s not elapsed ({now - started_ts:.0f}s so far)")
                continue
            session = _session_key(started_ts)
            state = db_get_stage_state(sid_key, did, sid)
            should_fire = False
            if not state or state.get("active_session") != session:
                should_fire = True   # never fired in this session
            elif repeat > 0 and (now - state.get("last_fire_ts", 0)) >= (repeat * 60):
                should_fire = True   # repeat interval elapsed
            if not should_fire:
                skip_reasons.append(f"stage#{sid_key} already fired this session "
                                    f"(repeat={repeat}min)")
                continue
            first_fire = (not state or state.get("active_session") != session)
            fired_any = True
            _fire(stage, dev, sensor, trig, did, sid, session, profile,
                  dispatch, check_maintenance, db_log_event,
                  db_record_stage_fire, first_fire_in_session=first_fire)

        # ── Recovery stages: fire once when sensor is fully OK ──
        elif is_recovery_stage:
            target_state = "down" if trig == "down_recovered" else "warning"
            # Only fire on full recovery to ok — warn↔crit transitions are
            # escalations within the same session, not recoveries. Firing
            # warning_recovered on warning→crit would resolve the active event
            # mid-incident and cause the next stage to insert a fresh row.
            if current_state != "ok":
                continue
            # Fast path: a sensor that has never fired in this process run
            # cannot have a prior state to recover from. Skip the per-stage
            # `db_get_stage_state` reads entirely. `_alert_has_fired` is set
            # True inside _fire() and cleared after post-recovery cleanup, so
            # it correctly tracks "does this sensor have live stage history".
            # Without this short-circuit, every healthy sensor runs N DB
            # queries on every probe cycle — at 5k sensors with a pool of 36,
            # that's how sample-flush starved and tripped the slow-flush WARN.
            if not getattr(sensor, "_alert_has_fired", False):
                continue
            # Did any state-stage of the matching trigger fire previously?
            if not _had_prior_fire(stages, target_state, sid_key, did, sid,
                                   db_get_stage_state):
                continue
            session = ""  # recovery stages aren't session-bound
            recovery_state = db_get_stage_state(sid_key, did, sid)
            if recovery_state and recovery_state.get("fire_count", 0) > 0:
                continue   # already fired this recovery
            start_ts = _get_session_start_ts(stages, target_state, did, sid,
                                             db_get_stage_state)
            duration_s = (now - start_ts) if start_ts else None
            _fire(stage, dev, sensor, trig, did, sid, session, profile,
                  dispatch, check_maintenance, db_log_event,
                  db_record_stage_fire, recovery=True, duration_s=duration_s)
            fired_recovery = True

    # Diagnostic: sensor is failing but nothing dispatched — explain once
    # (then stay silent until the reason changes).
    if current_state != "ok" and not fired_any:
        if not matched_trig:
            trigs = sorted({s["trigger_state"] for s in stages
                            if s["trigger_state"] in ("down", "warning")})
            reason = (f"no dispatch — no stages match current_state={current_state} "
                      f"(profile {profile['name']!r} has triggers: {trigs or 'none'})")
        else:
            reason = (f"no dispatch — state={current_state} matched but all stages gated: "
                      f"{'; '.join(skip_reasons) if skip_reasons else 'unknown'}")
        _diag_log(sensor, label, reason)

    # When sensor is fully OK, auto-resolve any active alert event and
    # clear per-stage history so a future failure starts a fresh session.
    # This must run even if no recovery stage dispatched (e.g. profile has
    # no recovery stage, or its recovery stage has no action templates).
    if current_state == "ok":
        # Reset the diag-throttle so the next failing session logs its first reason.
        sensor._alert_diag_last_reason = None
        # Fast path: if no stage has ever fired for this sensor (in this process
        # run), there is nothing to clean up — skip the per-stage DB reads.
        # _alert_has_fired is set True inside _fire() and cleared here after cleanup.
        if not getattr(sensor, "_alert_has_fired", False):
            return
        should_cleanup = fired_recovery
        if not should_cleanup:
            for s in stages:
                if s["trigger_state"] not in ("down", "warning"):
                    continue
                st = db_get_stage_state(s["id"], did, sid)
                if st and st.get("fire_count", 0) > 0:
                    should_cleanup = True
                    break
        if should_cleanup:
            try:
                db_clear_stage_state_for_sensor(did, sid)
                db_auto_resolve_event(profile["id"], did, sid)
                sensor._alert_has_fired = False   # no active state left in DB
            except Exception as e:
                log.warning(f"alert_profile_engine: post-recovery cleanup error: {e}")


def _had_prior_fire(stages, target_state, recovery_stage_id,
                    did, sid, db_get_stage_state) -> bool:
    """Return True if any state-stage matching target_state previously fired
    for this sensor (any session)."""
    for s in stages:
        if s["trigger_state"] != target_state:
            continue
        st = db_get_stage_state(s["id"], did, sid)
        if st and st.get("fire_count", 0) > 0:
            return True
    return False


def _fire(stage, dev, sensor, trig, did, sid, session, profile,
          dispatch, check_maintenance, db_log_event, db_record_stage_fire,
          recovery: bool = False, duration_s=None,
          first_fire_in_session: bool = False) -> None:
    """Build context, check maintenance, dispatch the action, log the event."""
    from db.alert_profiles import db_get_action_template
    from db.alert_events  import db_has_acked_event

    # Mark that this sensor has active alert state in DB (cleared after cleanup).
    # Used to skip the per-stage DB reads in the OK-state cleanup path for
    # sensors that have never fired (the dominant case).
    sensor._alert_has_fired = True

    ctx = _build_ctx(dev, sensor, sensor._threshold_state, trig,
                     duration_s=duration_s)

    suppressed, mw_name = check_maintenance(ctx)
    if suppressed:
        try:
            db_log_event(profile["id"], stage["id"], profile["name"],
                         ctx, state="suppressed")
        except Exception as e:
            log.warning(f"alert_profile_engine: db_log_event (suppressed) error: {e}")
        log.info(f"alert_profile_engine: stage {stage['id']} "
                 f"suppressed by maintenance window {mw_name!r}")
        # Still mark as fired so we don't keep retrying every probe
        db_record_stage_fire(stage["id"], did, sid, session)
        return

    # If the user has already ACK'd an event for this sensor, keep silent:
    # no dispatches (no emails / webhooks / syslog / browser pings). The event
    # row is still updated below so repeat_count reflects reality. Recovery
    # stages always dispatch — they're the signal that the incident is over.
    gated_by_ack = False
    if not recovery:
        try:
            if db_has_acked_event(profile["id"], did, sid):
                gated_by_ack = True
        except Exception as e:
            log.warning(f"alert_profile_engine: ack-gate check error: {e}")

    # Mid-incident escalation gate: if the session key changed (e.g. warn→crit
    # resets _threshold_triggered_ts) but an active/acked event already exists,
    # this is NOT a new failure — suppress dispatch to prevent duplicate emails.
    # Repeat-interval fires (first_fire_in_session=False) always dispatch.
    if not recovery and not gated_by_ack and first_fire_in_session:
        try:
            from db.alert_events import db_has_active_event
            if db_has_active_event(did, sid):
                gated_by_ack = True
                log.debug(f"alert_profile_engine: stage {stage['id']} suppressed "
                          f"(mid-incident escalation, active event already exists)")
        except Exception as e:
            log.warning(f"alert_profile_engine: active-event gate check error: {e}")

    if not gated_by_ack:
        log.info(f"Alert dispatch: profile={profile['name']!r} stage={stage['id']} "
                 f"trigger={trig} device={did} sensor={sid}"
                 f"{' [recovery]' if recovery else ''}")
        for aid in (stage.get("action_ids") or []):
            tpl = db_get_action_template(aid)
            if not tpl:
                log.warning(
                    f"alert_profile_engine: stage {stage['id']} references missing "
                    f"template {aid} — skipped"
                )
                continue
            try:
                dispatch(tpl["atype"], tpl["config"], ctx)
            except Exception as e:
                log.error(f"alert_profile_engine: dispatch error for template {aid}: {e}")

    try:
        if recovery:
            # Recovery stages auto-resolve any active event for this profile
            from db.alert_events import db_auto_resolve_event
            db_auto_resolve_event(profile["id"], did, sid)
        else:
            db_log_event(profile["id"], stage["id"], profile["name"], ctx,
                         state="active")
    except Exception as e:
        log.warning(f"alert_profile_engine: db_log_event error: {e}")

    db_record_stage_fire(stage["id"], did, sid, session)
