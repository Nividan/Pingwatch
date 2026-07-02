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
    Cascade         sensor → device → group → site → global, narrowest to
                    broadest. ADDITIVE by default — every matching profile in
                    the cascade fires independently, so a NOC-level site
                    profile can dispatch alongside a server-team group profile
                    on the same incident. If a matched profile has
                    `exclusive=True`, broader-scope profiles are NOT added
                    (narrower siblings already collected stay). If no profile
                    resolves at any level, no alerts fire (intentional "off"
                    state).
                    Pre-v1.0 profiles are auto-migrated to exclusive=True so
                    today's first-match-wins behavior is preserved byte-for-
                    byte until users explicitly opt in to additive cascade.

Cache invalidation:

    Sensor caches its resolved profile list on _resolved_profiles /
    _resolved_profile_ver. Any profile write bumps STATE._profile_cache_ver,
    forcing every sensor to re-resolve on its next probe. _resolved_profile
    (singular) is preserved as a back-compat alias pointing at the first
    profile in the list — used by the resolve_profile_for_sensor() wrapper.
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

# (stage_id, did, sid, session) tuples whose maintenance suppression has been
# logged — prevents one suppressed-event row per probe cycle while a window
# is open. Bounded (cleared past 4096 entries; worst case one duplicate log).
_suppressed_logged: set = set()


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

def resolve_profiles_for_sensor(dev, sensor) -> list:
    """Return every profile that applies to this sensor, narrowest-first.

    Walks sensor → device → group → site → global, collecting every matching
    profile. Stops adding broader scopes after the first match with
    `exclusive=True` — narrower matches already in the list stay.

    Pre-v1.0 profiles are migrated to exclusive=True so the cascade short-
    circuits at the first match (same as the old single-profile behavior).
    New profiles default to exclusive=False (truly additive).

    Two-level cache:
      1. Per-sensor: sensor._resolved_profiles (list of dicts) — zero DB hits
         on the hot probe path once resolved for this version.
      2. Scope-level: _scope_cache — first probe for each unique scope queries
         the DB once; all subsequent sensors sharing that scope get a cache hit.
    Both caches are invalidated when STATE._profile_cache_ver changes (on any
    profile write).
    """
    from core.app_state import STATE
    from db.alert_profiles import db_get_profile_for_scope

    cur_ver = getattr(STATE, "_profile_cache_ver", 0)
    cached_ids = getattr(sensor, "_resolved_profile_ids", None)
    if cached_ids is not None and getattr(sensor, "_resolved_profile_ver", -1) == cur_ver:
        return getattr(sensor, "_resolved_profiles", []) or []

    # Version changed (or first time) — invalidate scope cache if needed
    _invalidate_scope_cache(cur_ver)

    did   = dev.did if hasattr(dev, "did") else getattr(dev, "device_id", "")
    sid   = sensor.sensor_id
    group = getattr(dev, "group", "") or ""
    site  = getattr(dev, "site",  "") or ""

    profiles: list = []
    # Walk narrowest → broadest. Empty scope_value skips that level entirely
    # (no group / no site assigned). 'global' is always queried last unless
    # an earlier exclusive profile short-circuits the cascade.
    cascade = [
        ("sensor", f"{did}/{sid}"),
        ("device", did),
        ("group",  group),
        ("site",   site),
        ("global", ""),
    ]
    for scope_type, scope_value in cascade:
        if scope_value == "" and scope_type != "global":
            continue
        p = _scope_get_profile(scope_type, scope_value, cur_ver, db_get_profile_for_scope)
        if p:
            profiles.append(p)
            if p.get("exclusive", False):
                break  # don't add broader-scope profiles

    sensor._resolved_profile_ids = [p["id"] for p in profiles]
    sensor._resolved_profiles    = profiles
    # Back-compat singleton fields — pointed at the first (narrowest) match.
    sensor._resolved_profile_id  = profiles[0]["id"] if profiles else 0
    sensor._resolved_profile     = profiles[0] if profiles else None
    sensor._resolved_profile_ver = cur_ver
    return profiles


def resolve_profile_for_sensor(dev, sensor) -> dict | None:
    """Back-compat wrapper — returns the narrowest matching profile or None.

    Some external call sites still expect a single profile. The new cascade
    is additive, but those callers only care about "is there at least one
    profile that applies?" semantics, which the narrowest match preserves.
    """
    profiles = resolve_profiles_for_sensor(dev, sensor)
    return profiles[0] if profiles else None


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
        # Site (v1.0+) — surfaces in maintenance-window scope=site matching
        # and is available to email/webhook templates.
        "site":      getattr(dev, "site", ""),
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


def _resolve_orphan_event_if_ok(sensor, did, sid) -> None:
    """Resolve a lingering open alert_event when the sensor is healthy but no
    ENABLED profile governs it — i.e. the governing profile was disabled or
    deleted while an incident was open. The normal recovery cleanup lives past
    the no-profile / all-disabled early returns, so without this the
    'active'/'acknowledged' row never resolves (it survives restarts too). No
    dispatch — there's no enabled profile to dispatch through.

    Bounded to one DB probe per process run per sensor (unless the sensor
    actually fired), so it isn't a per-cycle DB hit on the cold no-profile path.
    """
    try:
        if _classify(sensor)[0] != "ok":
            return   # still failing — the incident is real, keep it open
        if not getattr(sensor, "_alert_has_fired", False):
            if getattr(sensor, "_orphan_checked", False):
                return
            sensor._orphan_checked = True
        from db.alert_events import db_has_active_event, db_resolve_events_by_sensor
        from db.alert_profiles import db_clear_stage_state_for_sensor
        if db_has_active_event(did, sid):
            db_clear_stage_state_for_sensor(did, sid)
            db_resolve_events_by_sensor(did, sid)
            sensor._alert_has_fired = False
            log.info(f"alert_profile_engine: resolved orphaned event for "
                     f"{did}/{sid} (no enabled profile governs it)")
    except Exception as e:
        log.warning(f"alert_profile_engine: orphan resolve error: {e}")


def evaluate_and_fire(dev, sensor) -> None:
    """Run the profile evaluator for this sensor on this probe cycle.

    Called from Sensor._run_once after the existing flap/threshold blocks
    have already updated _down_since_ts / _threshold_triggered_ts.

    v1.0+: walks ALL matching profiles in the cascade (sensor → device →
    group → site → global), firing each one's stages independently. A
    matched profile with `exclusive=True` stops the cascade for broader
    scopes — see resolve_profiles_for_sensor().
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

    profiles = resolve_profiles_for_sensor(dev, sensor)
    if not profiles:
        if is_failing:
            dev_group = getattr(dev, "group", "") or "(none)"
            dev_site  = getattr(dev, "site",  "") or "(none)"
            searched = (f"sensor={did}/{sid}, device={did}, "
                        f"group={dev_group!r}, site={dev_site!r}, global")
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
        # No profile matches at all (all deleted). Resolve any event a
        # now-deleted profile left open, once the sensor is healthy.
        _resolve_orphan_event_if_ok(sensor, did, sid)
        return

    current_state, started_ts = _classify(sensor)
    now = time.time()

    from db.alert_profiles import (
        db_get_stage_state, db_record_stage_fire,
        db_clear_stage_state_for_sensor,
    )
    from db.alert_events  import db_log_event
    from monitoring.alert_dispatchers import dispatch, check_maintenance

    fired_recovery = False
    fired_any      = False
    matched_trig   = False
    skip_reasons   = []
    any_enabled    = False  # at least one matched profile had stages enabled

    for profile in profiles:
        if not profile.get("enabled", True):
            if is_failing:
                _diag_log(sensor, label,
                          f"profile {profile['name']!r} (scope={profile.get('scope_type')}"
                          f":{profile.get('scope_value','-')}) is disabled — skipped")
            continue
        stages = profile.get("stages") or []
        if not stages:
            if is_failing:
                _diag_log(sensor, label,
                          f"profile {profile['name']!r} has no stages — skipped")
            continue
        any_enabled = True
        _r, _f, _m, _skips = _evaluate_profile_stages(
            profile, stages, dev, sensor, did, sid, current_state, started_ts, now,
            db_get_stage_state, db_record_stage_fire, db_log_event,
            dispatch, check_maintenance,
        )
        fired_recovery = fired_recovery or _r
        fired_any      = fired_any      or _f
        matched_trig   = matched_trig   or _m
        skip_reasons.extend(_skips)

    if not any_enabled:
        # All matched profiles disabled or stageless. Normally a no-op — but if
        # the profile was disabled/deleted mid-incident, resolve the now-orphan
        # event once the sensor is healthy so it doesn't linger 'active' forever.
        _resolve_orphan_event_if_ok(sensor, did, sid)
        return

    # Below this point, the rest of the function uses the narrowest profile
    # as the "representative" for diag logging and cleanup. db_log_event
    # already dedups on (did, sid) so multi-profile fires share one event row.
    profile = profiles[0]
    stages  = profile.get("stages") or []

    # Diagnostic: sensor is failing but nothing dispatched — explain once
    # (then stay silent until the reason changes). The reason is computed
    # against the narrowest profile; cross-profile silence is rare in
    # practice but the same skip_reasons list is shared across all profiles.
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
        # Fast path: if no stage fired in this process run there is usually
        # nothing to clean up. But _alert_has_fired is in-memory only — after
        # a restart mid-incident (or a profile edit that regenerated stage
        # ids) an orphaned active event would otherwise keep the sensor
        # alert-dead forever via the duplicate gate. Check the DB once per
        # process run per sensor to catch those orphans.
        if not getattr(sensor, "_alert_has_fired", False):
            if getattr(sensor, "_alert_cleanup_checked", False):
                return
            sensor._alert_cleanup_checked = True
        should_cleanup = fired_recovery
        if not should_cleanup:
            for s in stages:
                if s["trigger_state"] not in ("down", "warning"):
                    continue
                st = db_get_stage_state(s["id"], did, sid)
                if st and st.get("fire_count", 0) > 0:
                    should_cleanup = True
                    break
        if not should_cleanup:
            # Stage state may reference regenerated stage ids (profile saves
            # delete + reinsert stages) — fall back to the event table itself.
            try:
                from db.alert_events import db_has_active_event
                should_cleanup = db_has_active_event(did, sid)
            except Exception:
                should_cleanup = False
        if should_cleanup:
            try:
                from db.alert_events import db_resolve_events_by_sensor
                db_clear_stage_state_for_sensor(did, sid)
                # Resolve by (did, sid), not profile id: db_log_event dedups
                # the event row per sensor and overwrites its profile_id with
                # the last-firing profile, so a profile-scoped resolve could
                # miss the row and leave it active forever.
                db_resolve_events_by_sensor(did, sid)
                sensor._alert_has_fired = False   # no active state left in DB
            except Exception as e:
                log.warning(f"alert_profile_engine: post-recovery cleanup error: {e}")


# Per-profile stages loop, extracted from evaluate_and_fire so the additive
# cascade can call it once per matched profile. Returns
# (fired_recovery, fired_any, matched_trig, skip_reasons) so the caller can
# decide whether post-recovery cleanup needs to run.
def _evaluate_profile_stages(profile, stages, dev, sensor, did, sid,
                             current_state, started_ts, now,
                             db_get_stage_state, db_record_stage_fire,
                             db_log_event, dispatch, check_maintenance):
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
            if current_state != "ok":
                continue
            if not getattr(sensor, "_alert_has_fired", False):
                continue
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

    return fired_recovery, fired_any, matched_trig, skip_reasons


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

    # Recovery stages are exempt from maintenance suppression (as they are from
    # the RCA gate below): a recovery signals the incident is OVER, and dropping
    # it while deferring the DOWN left operators believing something was still
    # down after it had recovered inside a window. No spurious "recovered" noise
    # results — a recovery stage only reaches here when a DOWN actually fired
    # (_had_prior_fire needs fire_count>0), and a DOWN suppressed by maintenance
    # never records a fire, so a blip whose DOWN was itself suppressed emits no
    # recovery.
    suppressed, mw_name = check_maintenance(ctx)
    if suppressed and not recovery:
        reason = f"Maintenance: {mw_name}" if mw_name else "Maintenance window"
        # Log the suppressed event once per (stage, sensor, session) — the
        # engine re-enters here every probe cycle while the window is open.
        _supp_key = (stage["id"], did, sid, session)
        if _supp_key not in _suppressed_logged:
            if len(_suppressed_logged) > 4096:
                _suppressed_logged.clear()   # bounded; worst case re-logs once
            _suppressed_logged.add(_supp_key)
            try:
                db_log_event(profile["id"], stage["id"], profile["name"],
                             ctx, state="suppressed", suppress_reason=reason)
            except Exception as e:
                log.warning(f"alert_profile_engine: db_log_event (suppressed) error: {e}")
            log.info(f"alert_profile_engine: stage {stage['id']} "
                     f"suppressed by maintenance window {mw_name!r}")
        # Deliberately do NOT db_record_stage_fire here: marking the stage as
        # fired silenced the whole incident — an outage that started inside a
        # window stayed silent forever after the window ended. Leaving the
        # stage un-fired makes the first probe after the window dispatch
        # normally; the session dedup still prevents storms.
        return

    # ── Root-cause dependency suppression ────────────────────────────
    # A device whose parents are ALL down is a downstream symptom of the
    # upstream outage, not an independent fault. Suppress its symptom
    # dispatches (still recorded as 'suppressed', just no email/webhook/syslog)
    # while the root is down. Recovery stages always dispatch — they signal the
    # incident is over. Mirrors the maintenance gate above, including the
    # deliberate do-NOT-record-stage-fire so the first probe after the root
    # recovers dispatches normally. Engine-managed toggle lives inside
    # suppressed_root_for() (returns None when disabled).
    if not recovery:
        try:
            from monitoring.root_cause import suppressed_root_for
            _rca_root = suppressed_root_for(ctx.get("did", ""))
        except Exception as e:
            _rca_root = None
            log.debug(f"alert_profile_engine: RCA suppression check error: {e}")
        if _rca_root:
            _rname = _rca_root.get("name") or _rca_root.get("did") or "upstream"
            reason = f"Downstream of {_rname} (root cause)"
            _supp_key = ("rca", stage["id"], did, sid, session)
            if _supp_key not in _suppressed_logged:
                if len(_suppressed_logged) > 4096:
                    _suppressed_logged.clear()
                _suppressed_logged.add(_supp_key)
                try:
                    db_log_event(profile["id"], stage["id"], profile["name"],
                                 ctx, state="suppressed", suppress_reason=reason)
                except Exception as e:
                    log.warning(f"alert_profile_engine: db_log_event (RCA suppressed) error: {e}")
                log.info(f"alert_profile_engine: stage {stage['id']} suppressed "
                         f"(downstream of {_rname})")
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

    # Mid-incident duplicate gate: when the session key changes WITHOUT an
    # intervening recovery (e.g. restart re-hydration resets _down_since_ts),
    # the same stage would re-fire and duplicate its notification. Gate only
    # when THIS stage already fired for the still-open incident: its stage
    # state survives (cleared only by the OK-path cleanup) and an active
    # event exists. Gating on the active event alone was wrong — stage 1's
    # event silenced every later escalation stage, the additive cascade, and
    # warn→crit severity escalation (the engine degenerated to "first stage
    # of the narrowest profile, once").
    if not recovery and not gated_by_ack and first_fire_in_session:
        try:
            from db.alert_profiles import db_get_stage_state
            from db.alert_events import db_has_active_event
            _prior = db_get_stage_state(stage["id"], did, sid)
            if (_prior and _prior.get("fire_count", 0) > 0
                    and db_has_active_event(did, sid)):
                gated_by_ack = True
                log.debug(f"alert_profile_engine: stage {stage['id']} suppressed "
                          f"(same stage already fired for this open incident — "
                          f"session key changed without recovery)")
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
            # Recovery resolves ALL active events for the sensor: the event
            # row is deduped per (did, sid) and its profile_id is overwritten
            # by the last-firing profile, so a profile-scoped resolve could
            # miss the row and leave it active forever.
            from db.alert_events import db_resolve_events_by_sensor
            db_resolve_events_by_sensor(did, sid)
        else:
            db_log_event(profile["id"], stage["id"], profile["name"], ctx,
                         state="active")
    except Exception as e:
        log.warning(f"alert_profile_engine: db_log_event error: {e}")

    db_record_stage_fire(stage["id"], did, sid, session)
