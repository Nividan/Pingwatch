"""
monitoring/auto_discovery.py — scheduled subnet scanning with auto-add.

Runs a daemon thread that, on a configurable interval, iterates IPAM subnets
flagged `auto_discover=1`, uses the existing manual-Discovery scanner to find
new hosts, and funnels them through `core.device_importer.create_devices_batch`.

Architecture:

  start_loop() / stop_loop() / trigger_run_now()   ← lifecycle
  _refresh_loop()                                  ← thread body (polls wake/stop)
  _tick()                                          ← one scan pass
    _inside_maintenance_window()
    for each enabled subnet:
      _scan_subnet(subnet)                         ← returns per-subnet stats
        subnet_discovery.start_scan + poll
        _filter_suppressed()
        _apply_first_scan_cap()
        _build_device_specs()
        create_devices_batch()
        optional alert_on_new per created device

Safety rails:
  - Master enable: `auto_discover_enabled`
  - Emergency pause: `auto_discover_paused`
  - First-scan cap per subnet (one-time, admin-approved to override)
  - Suppressed-hosts list (populated on manual delete of auto-added devices)
  - Maintenance-window awareness (configurable)
  - _tick_lock prevents concurrent ticks (run-now + scheduler race)

Reused pieces (see plan):
  monitoring/subnet_discovery.py  — start_scan, get_scan, _suggest_sensors
  core/device_importer.py         — create_devices_batch (dedup, IPAM sync)
  backup/scheduler.py + core/auth_health.py — daemon pattern

External_id convention: auto-added devices get `external_id = "discovery:<ip>"`.
That's the signal `routes/devices.py` DELETE uses to decide whether to call
`suppress_host()`.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from typing import Any

import core.settings as _settings
from core.logger import log
from db import (
    _db_enqueue, db_save_settings,
    db_log_audit, db_list_subnets, db_set_subnet_last_scan,
)


def _persist_setting(key: str, value: str) -> None:
    """Update in-memory settings cache + enqueue a DB write. Mirrors the
    pattern used throughout routes/settings.py.
    """
    _settings.load({key: value})
    _db_enqueue(lambda k=key, v=value: db_save_settings({k: v}))


# ── Thread lifecycle state ─────────────────────────────────────────

_stop        = threading.Event()
_wake        = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()

# Serializes tick execution so run-now can't race the scheduler.
_tick_lock   = threading.Lock()

# Per-subnet "previous scan timed out" flag. Used to suppress repeat audit
# entries when a subnet keeps timing out every tick (e.g. /16 with default
# 5-min deadline). One audit row per failure streak; cleared on success.
# Single-threaded access (only mutated from the auto-discovery thread).
_subnet_timeout_flag: dict[int, bool] = {}

# Last-run stats for the settings-UI status endpoint.
_last_run_stats: dict = {
    "subnets_scanned":    0,
    "devices_added":      0,
    "devices_suppressed": 0,
    "first_scan_cap_hits": 0,
    "errors":             0,
    "duration_s":         0.0,
}
_currently_running = False


# ── Public lifecycle API ───────────────────────────────────────────

def start_loop() -> None:
    """Spawn the background scheduler thread. Idempotent."""
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _wake.clear()
        _thread = threading.Thread(target=_refresh_loop,
                                    name="auto-discovery",
                                    daemon=True)
        _thread.start()
        log.info("Auto-Discovery loop started")


def stop_loop(timeout: float = 5.0) -> None:
    """Signal the loop to exit and wait up to `timeout` seconds."""
    global _thread
    _stop.set()
    _wake.set()
    with _thread_lock:
        t = _thread
    if t and t.is_alive():
        t.join(timeout=timeout)
    log.info("Auto-Discovery loop stopped")


def trigger_run_now() -> bool:
    """External signal — skip the current wait and run a tick now.

    Returns False if a tick is already in progress (call was a no-op). The
    route layer translates False into a 202 "already running" response.
    """
    if _tick_lock.locked():
        return False
    _wake.set()
    return True


def get_last_run_status() -> dict:
    """Return a dict suitable for the settings-UI status endpoint."""
    interval = _get_interval_min()
    last_ts = _settings.get("auto_discover_last_ts", "") or ""
    next_ts = ""
    try:
        if last_ts and interval > 0:
            # last_ts is stored as epoch seconds (str) — produce next in same form.
            last_epoch = float(last_ts)
            next_ts = str(last_epoch + interval * 60)
    except (TypeError, ValueError):
        next_ts = ""
    return {
        "enabled":          bool(int(_settings.get("auto_discover_enabled", 0) or 0)),
        "paused":           bool(int(_settings.get("auto_discover_paused",  0) or 0)),
        "interval_min":     interval,
        "last_run_ts":      last_ts,
        "next_run_ts":      next_ts,
        "last_run_stats":   dict(_last_run_stats),
        "currently_running": _currently_running,
        "suppressed_hosts": get_suppressed_hosts(),
    }


# ── Suppressed-hosts list (populated on manual delete) ────────────

def get_suppressed_hosts() -> list:
    """Return the current list of hosts Auto-Discovery will NOT re-add."""
    raw = _settings.get("auto_discover_suppressed_hosts", "") or ""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def suppress_host(host: str, name: str, actor: str) -> bool:
    """Add a host to the suppressed list. Called from routes/devices.py DELETE
    when the deleted device's external_id starts with 'discovery:'.
    Idempotent — duplicate entries are collapsed. Capped at 500 (FIFO).
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    entries = get_suppressed_hosts()
    # De-dupe by host — most recent entry wins
    entries = [e for e in entries if (e.get("host") or "").lower() != host]
    entries.append({
        "host":           host,
        "name":           (name or "").strip(),
        "suppressed_at":  time.time(),
        "suppressed_by":  (actor or "").strip()[:64],
    })
    # FIFO cap
    if len(entries) > 500:
        entries = entries[-500:]
    try:
        _persist_setting("auto_discover_suppressed_hosts", json.dumps(entries))
        log.info(f"Auto-Discovery: suppressed host {host} "
                 f"(manual delete by {actor or '?'})")
        return True
    except Exception as e:
        log.error(f"Auto-Discovery: failed to persist suppressed host: {e}")
        return False


def unsuppress_host(host: str) -> bool:
    """Remove a host from the suppressed list. Returns True if the host was
    present (and removed), False if it wasn't in the list.
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    entries = get_suppressed_hosts()
    new = [e for e in entries if (e.get("host") or "").lower() != host]
    if len(new) == len(entries):
        return False
    try:
        _persist_setting("auto_discover_suppressed_hosts", json.dumps(new))
        return True
    except Exception as e:
        log.error(f"Auto-Discovery: failed to persist unsuppress: {e}")
        return False


# ── Thread loop body ──────────────────────────────────────────────

def _refresh_loop() -> None:
    """Thread body. First tick delayed briefly so the HTTP listener and other
    boot tasks finish before we start scanning + logging.
    """
    _wait_any(5.0)

    while not _stop.is_set():
        try:
            _tick()
        except Exception as e:
            log.error(f"Auto-Discovery tick crashed: {e}\n{traceback.format_exc()}")

        interval_min = _get_interval_min()
        # interval 0 / disabled → poll every 5 min so a re-enable picks up
        wait_s = float(interval_min * 60) if interval_min > 0 else 300.0
        _wait_any(wait_s)
        _wake.clear()


def _wait_any(timeout: float) -> None:
    """Wait for either _stop or _wake, up to timeout seconds."""
    end = time.time() + timeout
    while not _stop.is_set() and not _wake.is_set():
        remaining = end - time.time()
        if remaining <= 0:
            return
        if _stop.wait(timeout=min(remaining, 1.0)):
            return
        if _wake.is_set():
            return


def _get_interval_min() -> int:
    """Read + clamp the configured interval (minutes)."""
    try:
        v = int(_settings.get("auto_discover_interval_min", 60) or 60)
    except (TypeError, ValueError):
        v = 60
    allowed = (0, 15, 30, 60, 240, 720, 1440, 4320, 10080)
    return v if v in allowed else 60


# ── One tick — scan every enabled subnet ──────────────────────────

def _tick() -> None:
    """Run one scan pass across all enabled subnets. Safe to call from the
    scheduler thread or from /api/auto-discovery/run-now.
    """
    global _currently_running
    # Lock first so a wake signal racing with a scheduled tick doesn't double-run.
    if not _tick_lock.acquire(blocking=False):
        log.debug("Auto-Discovery: tick skipped — another tick is running")
        return
    try:
        _currently_running = True
        if not int(_settings.get("auto_discover_enabled", 0) or 0):
            return
        if int(_settings.get("auto_discover_paused", 0) or 0):
            log.debug("Auto-Discovery: tick skipped (paused)")
            return
        if _inside_maintenance_window() and \
           (_settings.get("auto_discover_during_maint", "skip") or "skip") == "skip":
            log.info("Auto-Discovery: tick skipped — maintenance window active "
                     "(auto_discover_during_maint=skip)")
            return

        subnets = [s for s in db_list_subnets()
                   if int(s.get("auto_discover") or 0) == 1]
        if not subnets:
            return

        t0 = time.time()
        totals = {
            "subnets_scanned": 0, "hosts_found": 0, "devices_added": 0,
            "devices_suppressed": 0, "first_scan_cap_hits": 0, "errors": 0,
        }
        for subnet in subnets:
            if _stop.is_set():
                break
            try:
                stats = _scan_subnet(subnet)
                totals["subnets_scanned"]     += 1
                totals["hosts_found"]         += stats.get("found", 0)
                totals["devices_added"]       += stats.get("added", 0)
                totals["devices_suppressed"]  += stats.get("suppressed", 0)
                totals["first_scan_cap_hits"] += (1 if stats.get("cap_hit") else 0)
                totals["errors"]              += stats.get("errors", 0)
            except Exception as e:
                totals["errors"] += 1
                log.error(f"Auto-Discovery: subnet {subnet.get('cidr')!r} "
                          f"scan failed: {e}\n{traceback.format_exc()}")
        duration = time.time() - t0

        # Commit last-run stats + timestamp
        _last_run_stats.update({**totals, "duration_s": round(duration, 2)})
        try:
            _persist_setting("auto_discover_last_ts", str(time.time()))
        except Exception as e:
            log.warning(f"Auto-Discovery: failed to save last_ts: {e}")

        # One audit row per tick, summarizing the pass
        try:
            db_log_audit(
                "system", "",
                "auto_discovery_tick",
                f"subnets={totals['subnets_scanned']} "
                f"found={totals['hosts_found']} "
                f"added={totals['devices_added']} "
                f"suppressed={totals['devices_suppressed']} "
                f"cap_hits={totals['first_scan_cap_hits']} "
                f"errors={totals['errors']} "
                f"duration={duration:.1f}s"
            )
        except Exception:
            pass

        if totals["devices_added"] or totals["errors"] or totals["first_scan_cap_hits"]:
            log.info(
                f"Auto-Discovery tick: subnets={totals['subnets_scanned']} "
                f"found={totals['hosts_found']} "
                f"added={totals['devices_added']} "
                f"suppressed={totals['devices_suppressed']} "
                f"cap_hits={totals['first_scan_cap_hits']} "
                f"errors={totals['errors']} "
                f"({duration:.1f}s)"
            )
        else:
            log.debug(
                f"Auto-Discovery tick: {totals['subnets_scanned']} subnets, "
                f"found={totals['hosts_found']} new hosts, "
                f"none added ({duration:.1f}s)"
            )
    finally:
        _currently_running = False
        _tick_lock.release()


def _inside_maintenance_window() -> bool:
    """Return True if *right now* falls inside an active maintenance window.

    `db_active_windows()` only filters by the overall start_ts/end_ts range;
    for recurring windows, that range is typically years long. We re-apply
    the recur_days + recur_start/recur_end rules here, mirroring what
    monitoring.alert_dispatchers.in_maintenance_window() does.
    """
    import datetime as _dt
    try:
        from db.maintenance_windows import db_active_windows
        windows = db_active_windows()
    except Exception as e:
        log.warning(f"Auto-Discovery: maintenance-window check failed: {e}")
        return False
    if not windows:
        return False

    now_dt  = _dt.datetime.now()
    now_day = now_dt.isoweekday()   # 1=Mon..7=Sun
    now_t   = now_dt.time()

    for w in windows:
        if not w.get("recurring"):
            return True    # non-recurring + in-range ⇒ active
        days = [d.strip() for d in str(w.get("recur_days", "")).split(",") if d.strip()]
        if days and str(now_day) not in days:
            continue
        rs = w.get("recur_start", "")
        re_ = w.get("recur_end", "")
        if not rs or not re_:
            return True    # recurring but no time bounds ⇒ all-day on matching weekday
        try:
            rs_t = _dt.datetime.strptime(rs, "%H:%M").time()
            re_t = _dt.datetime.strptime(re_, "%H:%M").time()
        except ValueError:
            continue
        if rs_t <= re_t:
            if rs_t <= now_t <= re_t:
                return True
        else:   # crosses midnight
            if now_t >= rs_t or now_t <= re_t:
                return True
    return False


# ── One subnet scan ────────────────────────────────────────────────

# Max wall-clock we'll spend waiting for a subnet scan to finish.
# Manual-Discovery full-mode on a /24 is typically under 60s; padding to 5min
# handles slow /22s + network latency while preventing runaway loops.
# The deadline is user-tunable via setting `auto_discover_scan_deadline_s`
# (bounded 30..3600); this constant is the fallback when the setting is missing.
_SCAN_WAIT_DEADLINE_S = 300.0
_SCAN_POLL_INTERVAL_S = 1.5


def _scan_deadline_s() -> float:
    try:
        import core.settings as _s
        return float(max(30, min(3600, int(_s.get("auto_discover_scan_deadline_s", 300) or 300))))
    except Exception:
        return _SCAN_WAIT_DEADLINE_S


def _scan_subnet(subnet: dict) -> dict:
    """Scan a single subnet and auto-add any new hosts. Returns stats dict.

    Shape: {added, suppressed, cap_hit, errors}.
    """
    from monitoring.subnet_discovery import start_scan, get_scan
    from core.device_importer import create_devices_batch

    cidr = (subnet.get("cidr") or "").strip()
    sid  = subnet.get("id")
    stats = {"added": 0, "found": 0, "suppressed": 0, "cap_hit": False, "errors": 0}
    if not cidr:
        return stats

    scan_id, err = start_scan(cidr, skip_monitored=True, mode="full")
    if err or not scan_id:
        log.warning(f"Auto-Discovery: start_scan failed for {cidr}: {err}")
        stats["errors"] = 1
        return stats

    # Wait for the scan to finish (or deadline / shutdown).
    _dl = _scan_deadline_s()
    deadline = time.time() + _dl
    while time.time() < deadline:
        if _stop.is_set():
            log.info(f"Auto-Discovery: abandoning in-flight scan {scan_id} "
                     f"on {cidr} (shutdown)")
            return stats
        st = get_scan(scan_id)
        if not st:
            log.warning(f"Auto-Discovery: scan {scan_id} vanished from registry")
            stats["errors"] = 1
            return stats
        state = st.get("state")
        if state in ("done", "error", "cancelled"):
            break
        time.sleep(_SCAN_POLL_INTERVAL_S)
    else:
        log.warning(f"Auto-Discovery: scan {scan_id} on {cidr} exceeded "
                    f"{_dl}s deadline — raise auto_discover_scan_deadline_s "
                    f"in Settings → Retention or split the subnet into smaller blocks")
        stats["errors"] = 1
        # Audit entry on the first timeout in a streak. Suppresses repeats so a
        # /16 timing out every tick doesn't drown the audit log.
        if sid is not None and not _subnet_timeout_flag.get(sid):
            _subnet_timeout_flag[sid] = True
            try:
                db_log_audit(
                    "system", "",
                    "auto_discovery_scan_timeout",
                    f"cidr={cidr} deadline={int(_dl)}s — raise "
                    f"auto_discover_scan_deadline_s or split the subnet"
                )
            except Exception:
                pass
        return stats

    if state != "done":
        log.warning(f"Auto-Discovery: scan on {cidr} ended in state {state!r}")
        stats["errors"] = 1
        return stats

    # Reached "done" — clear any prior timeout flag so a future timeout streak
    # gets its own audit entry.
    if sid is not None:
        _subnet_timeout_flag.pop(sid, None)

    # Filter results: drop suppressed hosts.
    raw_results = st.get("results") or []
    stats["found"] = len(raw_results)
    allowed_results, skipped = _filter_suppressed(raw_results)
    stats["suppressed"] = skipped

    if not allowed_results:
        _commit_last_scan_ts(sid)
        return stats

    # First-scan cap.
    first_scan_approved = int(subnet.get("first_scan_approved") or 0) == 1
    last_ts             = subnet.get("last_auto_scan_ts")
    is_first_scan       = not first_scan_approved and not last_ts
    if is_first_scan:
        cap = _get_first_scan_cap()
        if cap > 0 and len(allowed_results) > cap:
            stats["cap_hit"] = True
            log.warning(
                f"Auto-Discovery: first-scan cap hit on {cidr} — "
                f"{len(allowed_results)} hosts found, cap is {cap}. "
                f"Approve first scan in Settings to override."
            )
            try:
                db_log_audit(
                    "system", "",
                    "auto_discovery_cap_hit",
                    f"cidr={cidr} found={len(allowed_results)} cap={cap}"
                )
            except Exception:
                pass
            # We DO update last_auto_scan_ts so the settings UI shows "last scan"
            # even when capped — admins can tell the daemon is running.
            _commit_last_scan_ts(sid)
            return stats

    # Build device specs.
    use_ptr = bool(int(_settings.get("auto_discover_use_ptr", 1) or 1))
    group   = _group_name_for_cidr(cidr)

    # Per-subnet DNS override — when set, re-resolve each host's PTR record
    # against the specified DNS server instead of relying on the system
    # resolver's answer (which `_enrich_host` already cached in `hostname`).
    dns_override = (subnet.get("dns_server") or "").strip() if use_ptr else ""
    if dns_override:
        for r in allowed_results:
            ip = (r.get("ip") or "").strip()
            if not ip:
                continue
            alt = _resolve_ptr(ip, dns_override)
            if alt:
                r["hostname"] = alt   # mutates the scan result in place

    device_specs = _build_device_specs(allowed_results, group, use_ptr, cidr=cidr)
    if not device_specs:
        _commit_last_scan_ts(sid)
        return stats

    # Pre-mute brand-new Discovery groups so freshly-found devices don't
    # immediately page. Admin unmutes from the Edit Group modal after
    # triage. Only the *first* time a given Discovery-<cidr> group is
    # created: if it already exists in STATE, we leave the mute state
    # alone (admin may have deliberately unmuted it).
    try:
        from core.app_state import STATE as _STATE
        with _STATE._lock:
            group_exists = any((d.group or "") == group
                               for d in _STATE.devices.values())
        if not group_exists:
            _add_group_to_muted(group)
    except Exception as _me:
        log.warning(f"Auto-Discovery: could not pre-mute group {group!r}: {_me}")

    # Hand off to the shared bulk creator. Dedup against STATE + IPAM sync
    # happen inside create_devices_batch.
    try:
        res = create_devices_batch(device_specs, default_group=group)
    except Exception as e:
        log.error(f"Auto-Discovery: create_devices_batch failed on {cidr}: {e}")
        stats["errors"] = 1
        return stats

    created = res.get("created") or []
    errors  = res.get("errors")  or []
    stats["added"]  = len(created)
    stats["errors"] = len(errors)

    if created:
        log.info(f"Auto-Discovery: {len(created)} new device(s) in {cidr} "
                 f"(group={group!r})")

    # Optional alert-on-new-device.
    if created and int(_settings.get("auto_discover_alert_on_new", 0) or 0):
        _emit_new_device_alerts(created, cidr)

    _commit_last_scan_ts(sid)
    return stats


def _filter_suppressed(results: list) -> tuple[list, int]:
    """Drop scan-result rows whose IP is on the suppressed list.
    Returns (kept, skipped_count).
    """
    suppressed = {(e.get("host") or "").strip().lower()
                  for e in get_suppressed_hosts()}
    if not suppressed:
        return list(results), 0
    kept: list = []
    skipped = 0
    for r in results or []:
        ip = (r.get("ip") or "").strip().lower()
        if ip and ip in suppressed:
            skipped += 1
            continue
        kept.append(r)
    return kept, skipped


def _build_device_specs(results: list, group: str, use_ptr: bool,
                         cidr: str = "") -> list:
    """Convert _enrich_host result rows into create_devices_batch input shape.

    `cidr` feeds the per-device origin breadcrumb (`discovered_from_cidr`)
    so the device UI can show "Auto-discovered from 10.0.0.0/24".
    """
    from monitoring.subnet_discovery import _suggest_sensors
    specs: list = []
    now = time.time()
    for r in results or []:
        ip = (r.get("ip") or "").strip()
        if not ip:
            continue
        hostname = (r.get("hostname") or "").strip() if use_ptr else ""
        name  = hostname or ip
        ports = r.get("ports") or []
        sensors = _suggest_sensors(ip, hostname, ports)
        # _suggest_sensors tags ping as enabled=True and others as enabled=False;
        # for auto-discovery we want ALL suggestions active — strip the flag.
        for s in sensors:
            s.pop("enabled", None)
        specs.append({
            "external_id":          f"discovery:{ip}",
            "name":                 name,
            "host":                 ip,
            "group":                group,
            "sensors":              sensors,
            "discovered_at":        now,
            "discovered_from_cidr": cidr,
        })
    return specs


def _resolve_ptr(ip: str, dns_server: str, timeout: float = 2.5) -> str:
    """Reverse-DNS lookup for `ip` against a specific DNS server.

    Returns the resolved hostname (without trailing dot) or '' on any
    failure. IPv4 only in v1 — IPv6 reverse zones live under .ip6.arpa
    and aren't worth the extra path length until we see a need.
    """
    ip = (ip or "").strip()
    if not ip or "." not in ip:
        return ""
    try:
        octets = ip.split(".")
        if len(octets) != 4:
            return ""
        arpa = ".".join(reversed(octets)) + ".in-addr.arpa"
    except Exception:
        return ""
    try:
        from monitoring.probes import probe_dns
        r = probe_dns(host="", query=arpa, record_type="PTR",
                      dns_server=dns_server, timeout=timeout)
    except Exception as e:
        log.debug(f"Auto-Discovery: PTR via {dns_server} for {ip} failed: {e}")
        return ""
    if not r or not r.get("ok"):
        return ""
    val = (r.get("value") or "").strip().rstrip(".")
    return val


def _add_group_to_muted(group: str) -> None:
    """Append `group` to app_settings.muted_groups if not already present.
    Mirrors the JSON-list + 500-cap pattern used for suppressed_hosts.
    Silent on failure — mute is a convenience, not a correctness requirement.
    """
    if not group:
        return
    raw = _settings.get("muted_groups", "") or ""
    lst: list = []
    if raw:
        try:
            v = json.loads(raw)
            if isinstance(v, list):
                lst = [str(g) for g in v if isinstance(g, str)]
        except Exception:
            lst = []
    if group in lst:
        return
    lst.append(group)
    if len(lst) > 500:
        lst = lst[-500:]
    try:
        _persist_setting("muted_groups", json.dumps(lst))
        log.info(f"Auto-Discovery: new group {group!r} muted by default "
                 f"(admin unmutes via Edit Group once triaged)")
    except Exception as e:
        log.warning(f"Auto-Discovery: failed to persist muted group: {e}")


def _group_name_for_cidr(cidr: str) -> str:
    """Derive a group name from a CIDR. Slash replaced with underscore because
    PingWatch group names are used in URL paths in a few places.
    """
    return f"Discovery-{(cidr or '').replace('/', '_')}"


def _commit_last_scan_ts(subnet_id: int | None) -> None:
    """Persist the last-scan timestamp on a subnet. Non-fatal on failure."""
    if subnet_id is None:
        return
    try:
        db_set_subnet_last_scan(int(subnet_id),
                                time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception as e:
        log.warning(f"Auto-Discovery: last_auto_scan_ts write failed "
                    f"(subnet_id={subnet_id}): {e}")


def _get_first_scan_cap() -> int:
    """Read + clamp the first-scan cap. 0 = disabled."""
    try:
        v = int(_settings.get("auto_discover_first_scan_cap", 100) or 100)
    except (TypeError, ValueError):
        return 100
    if v < 0:
        return 100
    if v > 1000:
        return 1000
    return v


def _emit_new_device_alerts(created: list, cidr: str) -> None:
    """Fire an `alert_events` row per newly-created device."""
    from db.alert_events import db_log_event
    for c in created or []:
        try:
            db_log_event(
                profile_id=0, stage_id=0,
                profile_name="Auto-Discovery",
                ctx={
                    "did":        c.get("did", ""),
                    "sid":        "",
                    "dname":      c.get("name", ""),
                    "sname":      "",
                    "severity":   "info",
                    "event_type": "device_auto_added",
                    "detail":     f"Discovered in {cidr} at {c.get('host', '')}",
                },
                state="active",
            )
        except Exception as e:
            log.warning(f"Auto-Discovery: alert emit failed for "
                        f"{c.get('host', '?')}: {e}")


# ── Helpers exposed for tests / run-now route ─────────────────────

def run_now_blocking() -> dict:
    """Run one tick synchronously (for the run-now route's 'wait-for-result'
    mode if ever desired). Returns the updated _last_run_stats dict.
    NOT currently wired into an endpoint — the route uses trigger_run_now()
    so the caller gets an immediate 202 + polls /status.
    """
    _tick()
    return dict(_last_run_stats)
