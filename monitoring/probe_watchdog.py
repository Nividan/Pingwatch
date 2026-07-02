"""
monitoring/probe_watchdog.py — liveness watchdog for distributed probes.

A daemon loop (started from server.py main()) sweeps every 10s:

  • probes enrolled but silent for PROBE_OFFLINE_AFTER_S → exactly one
    'probe_offline' event (flap-shaped → Events page, SSE badge, optional
    email) — their sensors render grey/stale in the UI, never false-DOWN;
  • stale agent_tasks (pending/dispatched > 1h) → 'expired'.

The matching 'probe_online' is emitted from routes/agent.py on the next
successful checkin (emit_probe_online below), which also auto-resolves the
open probe_offline row so Events shows a closed incident with duration.

Probe events ride the existing flap pipeline with did='probe:<probe_id>'
and stype='probe' — db_log_flap / db_auto_resolve_flap / pushFlap all work
untouched.
"""
import datetime
import threading
import time

from core.app_state import STATE
from core.logger import log, log_probes

PROBE_OFFLINE_AFTER_S = 35     # ~3 missed 10s checkins
_SWEEP_INTERVAL_S = 10
_TASK_EXPIRE_S = 3600

# A branch agent whose clock is off by more than the stale-backfill cutoff
# (max(120s, 2*interval); floor 120s) has its live results filed as backfill,
# silently disabling alerting for that branch. routes/agent.py now corrects the
# sample timestamps, but a large offset still means broken NTP on the branch —
# warn at half the 120s floor so it's visible before it bites the tightest
# (short-interval) sensor.
PROBE_CLOCK_SKEW_WARN_S = 60

_stop = threading.Event()
_BOOT_MONO = time.monotonic()
# probe_ids currently flagged for clock skew (in-memory throttle; one alert per
# crossing, cleared on recovery). Resets on restart, which re-surfaces a still-
# skewed probe once — intentional.
_skew_alerted = set()


def _startup_holdoff_s() -> float:
    """No probe_offline verdicts until the server has been up long enough
    for agents to reconnect: right after a restart every probe's persisted
    last_seen is necessarily stale (the watchdog wasn't watching while the
    server was down), so the first sweep would false-flag them all. Honors
    startup_grace_s when it's larger than the offline threshold."""
    try:
        import core.settings as _settings
        grace = float(_settings.get("startup_grace_s", "60") or 0)
    except Exception:
        grace = 60.0
    return max(PROBE_OFFLINE_AFTER_S + 10.0, grace)


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _probe_flap(probe: dict, direction: str, detail: str) -> dict:
    return {
        "did": "probe:" + probe["probe_id"], "sid": "",
        "dname": probe.get("name") or probe["probe_id"],
        "sname": "Probe connection",
        "host": probe.get("last_checkin_ip") or "",
        "stype": "probe", "ts": _iso(time.time()),
        "detail": detail, "direction": direction,
        "grp": "", "consec_count": 1,
    }


def emit_probe_offline(probe: dict):
    from db import _logs_enqueue
    from db.events import db_log_flap
    silent_for = time.time() - float(probe.get("last_seen") or 0)
    flap = _probe_flap(probe, "probe_offline",
                       f"Probe stopped checking in ({int(silent_for)}s silent) — "
                       f"its sensors are stale, not down")
    log_probes.warning(f"PROBE OFFLINE: {probe.get('name')} ({probe['probe_id']}) — "
                f"last seen {int(silent_for)}s ago")
    _logs_enqueue(lambda _f=flap: db_log_flap(_f))
    STATE._broadcast("probe_offline", flap)
    STATE._broadcast("probe_status", {"probe_id": probe["probe_id"],
                                      "connected": False,
                                      "status": probe.get("status") or "enrolled",
                                      "last_seen": float(probe.get("last_seen") or 0)})
    _maybe_email(probe, silent_for)


def emit_probe_online(probe: dict):
    from db import _logs_enqueue
    from db.events import db_log_flap, db_auto_resolve_flap
    flap = _probe_flap(probe, "probe_online", "Probe reconnected")
    log_probes.info(f"PROBE ONLINE: {probe.get('name')} ({probe['probe_id']})")
    _ts = flap["ts"]
    _did = flap["did"]
    _logs_enqueue(lambda: db_auto_resolve_flap(_did, "", _ts,
                                               directions=("probe_offline",)))
    _logs_enqueue(lambda _f=flap: db_log_flap(_f))
    STATE._broadcast("probe_online", flap)
    STATE._broadcast("probe_status", {"probe_id": probe["probe_id"],
                                      "connected": True,
                                      "status": probe.get("status") or "enrolled",
                                      "last_seen": time.time()})


def _maybe_email(probe: dict, silent_for: float):
    """Optional email on probe-offline (settings: probe_offline_email=1 +
    probe_offline_email_to). The alert-profile engine is sensor-scoped and
    doesn't fit probe events — a direct send via send_rule_email is the
    deliberate v1 simplification."""
    import core.settings as _settings
    if str(_settings.get("probe_offline_email", "0")) != "1":
        return
    to_addrs = str(_settings.get("probe_offline_email_to", "") or "").strip()
    if not to_addrs:
        return
    try:
        from monitoring.smtp_alert import send_rule_email
        name = probe.get("name") or probe["probe_id"]
        send_rule_email(
            to_addrs,
            f"[PingWatch] Probe offline: {name}",
            (f"Remote probe '{name}' ({probe['probe_id']}) has stopped "
             f"checking in (silent for {int(silent_for)}s).\n"
             f"Last seen IP: {probe.get('last_checkin_ip') or 'unknown'}\n\n"
             f"Sensors assigned to this probe show stale data until it "
             f"reconnects."),
            {"event_type": "probe_offline", "severity": "critical",
             "dname": name, "sname": "Probe connection", "stype": "probe"})
    except Exception as e:
        log_probes.warning(f"probe_offline email failed: {type(e).__name__}: {e}")


def _check_clock_skew(p: dict):
    """Warn (once per crossing, audited) when an enrolled probe's clock offset
    exceeds the threshold; clear when it comes back. Visibility layer on top of
    the ts correction in routes/agent.py — a big offset means the branch's NTP
    is broken and the correction is being leaned on."""
    from db import db_log_audit
    pid = p["probe_id"]
    try:
        skew = float(p.get("clock_skew_s"))
    except (TypeError, ValueError):
        return   # never reported (old agent) — nothing to judge
    name = p.get("name") or pid
    ip = p.get("last_checkin_ip") or ""
    if abs(skew) > PROBE_CLOCK_SKEW_WARN_S:
        if pid not in _skew_alerted:
            _skew_alerted.add(pid)
            log_probes.warning(
                f"PROBE CLOCK SKEW: {name} ({pid}) clock is {skew:+.0f}s off "
                f"server time — check NTP on the branch host")
            db_log_audit(f"probe:{pid}", ip, "probe_clock_skew", name,
                         f"skew={skew:+.0f}s")
    elif pid in _skew_alerted:
        _skew_alerted.discard(pid)
        log_probes.info(f"PROBE CLOCK SKEW cleared: {name} ({pid}) ({skew:+.0f}s)")
        db_log_audit(f"probe:{pid}", ip, "probe_clock_skew_cleared", name,
                     f"skew={skew:+.0f}s")


def _sweep():
    from db.probes import db_list_probes, db_probe_checkin, db_expire_stale_tasks
    from db.helpers import db_execute
    now = time.time()
    # Boot holdoff — agents reconnect within ~10s of the server coming back;
    # only after the holdoff is silence meaningful. Task expiry still runs.
    holdoff = (time.monotonic() - _BOOT_MONO) < _startup_holdoff_s()
    for p in (() if holdoff else db_list_probes()):
        if p.get("status") != "enrolled":
            continue
        last_seen = float(p.get("last_seen") or 0)
        if not last_seen:
            continue   # enrolled but never checked in — enrollment just happened
        if (now - last_seen) > PROBE_OFFLINE_AFTER_S and \
                not int(p.get("offline_alerted") or 0):
            db_execute("main",
                       "UPDATE probes SET offline_alerted = 1 WHERE probe_id = ?",
                       (p["probe_id"],))
            emit_probe_offline(p)
        # Only judge skew for probes that are checking in (a stale last_seen
        # means an old, possibly-unrelated reading).
        if (now - last_seen) <= PROBE_OFFLINE_AFTER_S:
            _check_clock_skew(p)
    expired = db_expire_stale_tasks(_TASK_EXPIRE_S)
    if expired:
        log_probes.warning(f"agent tasks expired by watchdog: {expired}")
    # Advance managed-update campaigns (staged rollout / canary / auto-halt).
    try:
        from monitoring.update_orchestrator import campaign_tick
        campaign_tick()
    except Exception as e:
        log_probes.error(f"campaign tick failed: {type(e).__name__}: {e}")


def probe_watchdog_loop():
    """Daemon thread body. Start once from server.py main()."""
    log_probes.info("Probe watchdog started")
    while not _stop.wait(_SWEEP_INTERVAL_S):
        try:
            _sweep()
        except Exception as e:
            log_probes.error(f"probe watchdog sweep failed: {type(e).__name__}: {e}")


def stop_probe_watchdog():
    _stop.set()
