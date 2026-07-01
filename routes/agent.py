"""
routes/agent.py — Agent-facing API for distributed probes.

    POST /api/agent/enroll              one-time token → long-lived probe token
    POST /api/agent/checkin             results batch + heartbeat + task pickup
    GET  /api/agent/config              full sensor config for this probe
    POST /api/agent/tasks/<id>/result   chunked task (scan) result upload

All endpoints except enroll require a probe-scoped Bearer token; server.py's
scope jail guarantees those tokens reach nothing outside /api/agent/*.

The checkin handler is the injection point into the monitoring state
machine: each accepted result runs through STATE._process_result() — the
same debounce/threshold/flap/alert/SSE pipeline local probes use. A
per-probe lock keeps one probe's results strictly serialized (the sensor
single-writer invariant), while different probes ingest concurrently.
"""
import hashlib
import json
import re
import secrets
import threading
import time

from core.app_state import STATE, APP_VERSION, PROBE_PROTOCOL_VERSION
from core.logger import log, log_probes
from core.probe_assign import effective_probe
from db.audit import db_log_audit
from db.probes import (
    db_get_probe, db_probe_checkin, db_set_probe_enrolled,
    db_consume_enroll_token, db_dispatch_pending_tasks, db_get_task,
    db_set_task_state,
)

_RE_TASK_RESULT = re.compile(r"^/api/agent/tasks/(\d+)/result$")

_MAX_RESULTS_PER_CHECKIN = 5000
_MAX_TASK_ROWS_PER_CHUNK = 2000

# ── Per-probe ingest locks ────────────────────────────────────────
# Serialize result batches per probe (timeout-retry overlap would otherwise
# race two handler threads into the same sensors). Different probes ingest
# concurrently. Entries are tiny and probes are few — no eviction needed.
_PROBE_LOCKS: dict = {}
_PROBE_LOCKS_GUARD = threading.Lock()


def _probe_lock(probe_id: str) -> threading.Lock:
    with _PROBE_LOCKS_GUARD:
        lk = _PROBE_LOCKS.get(probe_id)
        if lk is None:
            lk = threading.Lock()
            _PROBE_LOCKS[probe_id] = lk
        return lk


# ── Device-scan waiters ───────────────────────────────────────────
# The per-device service scan (Devices → Scan) is a synchronous UX: the
# browser POSTs and waits for the service list. When the device routes to a
# remote probe, the scan becomes an agent task — these waiters let the HTTP
# handler thread block until the agent posts the result (or a timeout).
_DEVSCAN_WAITERS: dict = {}      # task_id → {"evt", "rows", "error"}
_DEVSCAN_LOCK = threading.Lock()

# Service rows a probe may return for a device scan — mirror of the stypes
# central's own scan can produce (routes/devices.py _SCAN_TARGETS).
_DEVSCAN_STYPES = frozenset({"ping", "tcp", "http", "tls", "banner"})

# Task types whose result is awaited synchronously by an HTTP handler thread
# through _DEVSCAN_WAITERS (vs. the async _SCANS registry used by subnet
# discovery). Both are one-shot "run on the probe, return to the browser"
# operations that central can't perform for branch-local devices.
_SYNC_WAITER_TASKS = frozenset({"device_scan", "snmp_interfaces", "snmp_discover"})


def _devscan_complete(task_id: int, rows, error: str = ""):
    """Hand a finished device-scan result to whoever is waiting on it."""
    with _DEVSCAN_LOCK:
        w = _DEVSCAN_WAITERS.get(task_id)
        if not w:
            return
        if rows:
            w["rows"].extend(rows)
        if error:
            w["error"] = error
        w["evt"].set()


def run_remote_device_scan(probe: dict, host: str, targets: list,
                           created_by: str, timeout: float = 25.0) -> tuple:
    """Run a device service scan on a remote probe and wait for the result.

    Creates a device_scan agent task (picked up at the next ~10s checkin),
    blocks the calling handler thread until the agent uploads the service
    list, and returns (services, ""). On failure returns (None, reason).
    The scan-port targets ride in the payload so the agent honors the
    server's scan_ports setting without having settings access.
    """
    from db.probes import db_create_task
    pid = probe["probe_id"]
    payload = {"host": str(host)[:253],
               "targets": [{"name": str(t.get("name") or "")[:64],
                            "stype": str(t.get("stype") or ""),
                            "port": t.get("port"),
                            "tout": t.get("tout", 2)} for t in targets[:64]]}
    tid = db_create_task(pid, "device_scan", json.dumps(payload), created_by)
    if not tid:
        return None, "could not queue the scan task"
    evt = threading.Event()
    with _DEVSCAN_LOCK:
        _DEVSCAN_WAITERS[tid] = {"evt": evt, "rows": [], "error": ""}
    try:
        if not evt.wait(timeout=timeout):
            # Cancel so the agent drops it (cancelled_tasks rides the next
            # checkin) and a late upload gets a clean 409 instead of a waiter.
            db_set_task_state(tid, "cancelled")
            log_probes.warning(
                f"device_scan task {tid} on {probe.get('name')} ({pid}) timed "
                f"out after {timeout:.0f}s (host={host})")
            return None, (f"Probe '{probe.get('name')}' did not return scan "
                          f"results within {timeout:.0f}s — it may be busy or "
                          "reconnecting. Try again, or check the Probes page.")
        with _DEVSCAN_LOCK:
            w = _DEVSCAN_WAITERS.get(tid) or {}
        if w.get("error"):
            return None, str(w["error"])
        # Sanitize: the probe is trusted-ish, but keep the same shape and
        # bounds central's local scan produces.
        services = []
        for r in w.get("rows", [])[:64]:
            if not isinstance(r, dict):
                continue
            stype = str(r.get("stype") or "")
            if stype not in _DEVSCAN_STYPES:
                continue
            port = r.get("port")
            if port is not None:
                try: port = int(port)
                except (TypeError, ValueError): port = None
            ms = r.get("ms")
            if ms is not None:
                try: ms = float(ms)
                except (TypeError, ValueError): ms = None
            services.append({"stype": stype,
                             "name": str(r.get("name") or stype)[:64],
                             "port": port, "ms": ms,
                             "detail": str(r.get("detail") or "")[:512]})
        return services, ""
    finally:
        with _DEVSCAN_LOCK:
            _DEVSCAN_WAITERS.pop(tid, None)


def run_remote_snmp_interfaces(probe: dict, host: str, community: str, port: int,
                               version: str, v3_creds, created_by: str,
                               timeout: float = 25.0) -> tuple:
    """Run an SNMP interface-discovery walk on a remote probe and wait for it.

    Mirrors run_remote_device_scan: queues an snmp_interfaces agent task (picked
    up at the next ~10s checkin), blocks the calling handler thread until the
    agent uploads the interface list, and returns (interfaces, ""). On failure
    returns (None, reason). The walk runs ON the probe so discovery traffic
    egresses from the branch — central usually can't even reach the device.

    v3_creds (resolved + decrypted by the caller) rides in the payload so the
    agent walks with the same credentials a local walk would use.
    """
    from db.probes import db_create_task
    pid = probe["probe_id"]
    payload = {"host": str(host)[:253],
               "community": str(community or "public")[:128],
               "port": int(port),
               "version": str(version),
               "v3_creds": v3_creds if isinstance(v3_creds, dict) else None}
    tid = db_create_task(pid, "snmp_interfaces", json.dumps(payload), created_by)
    if not tid:
        return None, "could not queue the discovery task"
    evt = threading.Event()
    with _DEVSCAN_LOCK:
        _DEVSCAN_WAITERS[tid] = {"evt": evt, "rows": [], "error": ""}
    try:
        if not evt.wait(timeout=timeout):
            db_set_task_state(tid, "cancelled")
            log_probes.warning(
                f"snmp_interfaces task {tid} on {probe.get('name')} ({pid}) timed "
                f"out after {timeout:.0f}s (host={host})")
            return None, (f"Probe '{probe.get('name')}' did not return interface "
                          f"discovery within {timeout:.0f}s — it may be busy, "
                          "reconnecting, or running an older agent build that "
                          "doesn't support remote SNMP discovery.")
        with _DEVSCAN_LOCK:
            w = _DEVSCAN_WAITERS.get(tid) or {}
        if w.get("error"):
            return None, str(w["error"])
        # Sanitize to the same shape monitoring.probes.snmpwalk_interfaces
        # produces locally — the agent is trusted-ish but keep bounds tight.
        ifaces = []
        for r in w.get("rows", [])[:4096]:
            if not isinstance(r, dict):
                continue
            try:
                idx = int(r.get("index"))
            except (TypeError, ValueError):
                continue
            try:
                speed_raw = int(r.get("speed_raw") or 0)
            except (TypeError, ValueError):
                speed_raw = 0
            ifaces.append({"index": idx,
                           "name":   str(r.get("name")   or "")[:128],
                           "descr":  str(r.get("descr")  or "")[:256],
                           "alias":  str(r.get("alias")  or "")[:256],
                           "status": str(r.get("status") or "")[:32],
                           "speed":  str(r.get("speed")  or "")[:32],
                           "speed_raw": speed_raw})
        return ifaces, ""
    finally:
        with _DEVSCAN_LOCK:
            _DEVSCAN_WAITERS.pop(tid, None)


def run_remote_snmp_discover(probe: dict, host: str, items: list, community: str,
                             port: int, version: str, v3_creds, created_by: str,
                             timeout: float = 25.0, op_timeout: int = 10) -> tuple:
    """Discover a device against a template's items on a remote probe and wait
    for the result. Mirrors run_remote_snmp_interfaces but carries the item list
    in the payload and returns sanitized candidate rows (the same shape
    monitoring.probes.snmp_discover_template produces). Returns (candidates, "")
    or (None, reason). `op_timeout` is the per-OID snmp timeout the agent uses
    (lower for the large device-wide scan); `timeout` is the waiter budget."""
    from db.probes import db_create_task
    pid = probe["probe_id"]
    payload = {"host": str(host)[:253],
               "community": str(community or "public")[:128],
               "port": int(port),
               "version": str(version),
               "v3_creds": v3_creds if isinstance(v3_creds, dict) else None,
               "op_timeout": int(op_timeout),
               "items": items if isinstance(items, list) else []}
    tid = db_create_task(pid, "snmp_discover", json.dumps(payload), created_by)
    if not tid:
        return None, "could not queue the discovery task"
    evt = threading.Event()
    with _DEVSCAN_LOCK:
        _DEVSCAN_WAITERS[tid] = {"evt": evt, "rows": [], "error": ""}
    try:
        if not evt.wait(timeout=timeout):
            db_set_task_state(tid, "cancelled")
            log_probes.warning(
                f"snmp_discover task {tid} on {probe.get('name')} ({pid}) timed "
                f"out after {timeout:.0f}s (host={host})")
            return None, (f"Probe '{probe.get('name')}' did not return template "
                          f"discovery within {timeout:.0f}s — it may be busy, "
                          "reconnecting, or running an older agent build that "
                          "doesn't support remote SNMP discovery.")
        with _DEVSCAN_LOCK:
            w = _DEVSCAN_WAITERS.get(tid) or {}
        if w.get("error"):
            return None, str(w["error"])
        cands = []
        for r in w.get("rows", [])[:4096]:
            c = _sanitize_candidate(r)
            if c:
                cands.append(c)
        return cands, ""
    finally:
        with _DEVSCAN_LOCK:
            _DEVSCAN_WAITERS.pop(tid, None)


def _sanitize_candidate(r) -> dict:
    """Bound + type-coerce one discovery candidate row from a (trusted-ish)
    probe into the shape the frontend renders."""
    if not isinstance(r, dict):
        return None
    kind = "table" if r.get("kind") == "table" else "scalar"
    oid = str(r.get("oid") or "").strip()
    if not oid:
        return None

    def _num(v):
        if v is None or v == "":
            return None
        try:
            return float(v) if "." in str(v) else int(v)
        except (TypeError, ValueError):
            return None

    c = {"kind": kind,
         "item_label": str(r.get("item_label") or "")[:120],
         "oid": oid[:255],
         "unit": str(r.get("unit") or "")[:64],
         "warn": _num(r.get("warn")), "crit": _num(r.get("crit")),
         "interval": _num(r.get("interval")),
         "group": str(r.get("group") or "")[:80],
         "suggested_name": str(r.get("suggested_name") or "")[:120]}
    if kind == "scalar":
        c["value"] = str(r.get("value") or "")[:120]
        c["snmp_type"] = str(r.get("snmp_type") or "")[:32]
    else:
        try:
            c["index"] = int(r.get("index"))
        except (TypeError, ValueError):
            return None
        c["row_name"] = str(r.get("row_name") or "")[:128]
        c["hc_oid"] = str(r.get("hc_oid") or "")[:255]
        c["speed_auto_threshold"] = bool(r.get("speed_auto_threshold"))
        try:
            c["speed_raw"] = int(r.get("speed_raw") or 0)
        except (TypeError, ValueError):
            c["speed_raw"] = 0
    return c


# ── Enroll rate limiting (per IP, mirrors the login limiter) ─────
_ENROLL_LOCK = threading.Lock()
_ENROLL_LOG: dict = {}     # ip → [timestamp, ...]
_ENROLL_WINDOW = 60
_ENROLL_MAX = 5


def _enroll_rate_limited(ip: str) -> bool:
    with _ENROLL_LOCK:
        now = time.time()
        _ENROLL_LOG[ip] = [t for t in _ENROLL_LOG.get(ip, [])
                           if now - t < _ENROLL_WINDOW]
        for _old in [k for k, v in _ENROLL_LOG.items() if not v and k != ip]:
            del _ENROLL_LOG[_old]
        if len(_ENROLL_LOG[ip]) >= _ENROLL_MAX:
            return True
        _ENROLL_LOG[ip].append(now)
        return False


def _require_probe(h):
    """Authenticate a probe-scoped Bearer token → probe row dict or None
    (response already sent). Revoked probes always get 401."""
    from core.auth import auth_check_api_token
    tok = h._get_bearer()
    info = auth_check_api_token(tok) if tok else None
    if not info or info.get("scope") != "probe" or not info.get("probe_id"):
        h._json(401, {"error": "unauthorized"})
        return None
    probe = db_get_probe(info["probe_id"])
    if not probe or probe.get("status") == "revoked":
        h._json(401, {"error": "unauthorized"})
        return None
    return probe


def _status_fields(body: dict) -> dict:
    """Whitelisted probe-row updates from the checkin payload."""
    fields = {}
    av = body.get("agent_version")
    if av is not None:
        fields["agent_version"] = str(av)[:64]
    pv = body.get("protocol_version")
    if isinstance(pv, int):
        fields["protocol_version"] = pv
    bid = body.get("build_id")
    if bid is not None:
        fields["build_id"] = str(bid)[:64]
    st = body.get("status") or {}
    if isinstance(st, dict) and st:
        if st.get("os") is not None:
            fields["os_info"] = str(st["os"])[:200]
        if "supervisor" in st:
            fields["supervisor"] = 1 if st.get("supervisor") else 0
        caps = st.get("capabilities")
        if isinstance(caps, dict):
            fields["capabilities"] = json.dumps(
                {str(k)[:32]: bool(v) for k, v in list(caps.items())[:16]})
        sd = st.get("spool_depth")
        if sd is not None:
            try: fields["spool_depth"] = max(0, int(sd))
            except (TypeError, ValueError): pass
        an = st.get("agent_now")
        if an is not None:
            try: fields["clock_skew_s"] = round(float(an) - time.time(), 3)
            except (TypeError, ValueError): pass
    return fields


def _ingest_results(probe: dict, results: list) -> tuple:
    """Validate + inject one batch through the state machine.

    Returns (accepted, rejected). Rejected results are still acked to the
    agent — they're permanently unprocessable (unknown sensor, stale
    assignment, duplicate ts), so retrying would loop forever.
    """
    pid = probe["probe_id"]
    accepted = rejected = 0
    now = time.time()
    try:
        results.sort(key=lambda r: float(r.get("ts") or 0)
                     if isinstance(r, dict) else 0)
    except Exception:
        pass
    for r in results:
        if not isinstance(r, dict):
            rejected += 1; continue
        did = str(r.get("did") or "")
        sid = str(r.get("sid") or "")
        try:
            ts = float(r.get("ts") or 0)
        except (TypeError, ValueError):
            rejected += 1; continue
        if ts <= 0:
            rejected += 1; continue
        with STATE._lock:
            dev = STATE.devices.get(did)
            s = dev.sensors.get(sid) if dev else None
        if not s or effective_probe(dev, s) != pid:
            rejected += 1; continue     # deleted sensor / stale assignment
        if ts > now + 5:                # clamp future ts (agent clock skew)
            ts = now
        ms = r.get("ms")
        if ms is not None:
            try: ms = float(ms)
            except (TypeError, ValueError): ms = None
        val = r.get("value")
        if isinstance(val, str):
            val = val[:512]
        result = {"ok": bool(r.get("ok")), "ms": ms,
                  "detail": str(r.get("detail") or "")[:512], "value": val}
        if r.get("snmp_type"):
            result["snmp_type"] = str(r["snmp_type"])[:32]
        if r.get("rate") is not None:
            try: result["rate"] = float(r["rate"])
            except (TypeError, ValueError): pass
        if r.get("cert_days") is not None:   # HTTPS cert-expiry (http sensors)
            try: result["cert_days"] = int(r["cert_days"])
            except (TypeError, ValueError): pass
        # Spooled backfill older than the staleness cutoff is persisted as
        # samples only — no state/event/alert replay of finished history.
        stale_cutoff = max(120.0, 2.0 * float(s.interval or 5))
        state_eval = ts >= (now - stale_cutoff)
        try:
            if STATE._process_result(did, sid, result, ts_float=ts,
                                     source="probe", state_eval=state_eval):
                accepted += 1
            else:
                rejected += 1
        except Exception as e:
            log_probes.error(f"agent result for {did}/{sid} failed: "
                      f"{type(e).__name__}: {e}")
            rejected += 1
    return accepted, rejected


def _apply_task_progress(probe: dict, tasks: list):
    """Apply agent-reported task progress/errors from the checkin body."""
    pid = probe["probe_id"]
    for t in tasks[:50]:
        if not isinstance(t, dict):
            continue
        try:
            tid = int(t.get("task_id"))
        except (TypeError, ValueError):
            continue
        task = db_get_task(tid)
        if not task or task.get("probe_id") != pid:
            continue
        st = str(t.get("state") or "")
        if st == "running" and task["state"] in ("dispatched", "running"):
            prog = t.get("progress")
            db_set_task_state(tid, "running",
                              progress=json.dumps(prog)[:2048]
                              if isinstance(prog, dict) else None)
            if isinstance(prog, dict):
                _scan_progress(task, prog)
        elif st == "error" and task["state"] not in ("done", "error", "cancelled"):
            err = str(t.get("error") or "task failed")[:512]
            db_set_task_state(tid, "error", error=err)
            if task.get("task_type") == "device_scan":
                # An agent predating device_scan reports "unsupported task" —
                # translate so the scan modal explains the fix.
                if "unsupported task" in err:
                    err = (f"The agent on this probe is too old for remote "
                           f"device scans — download a fresh package from "
                           f"the Probes page and re-run install.")
                _devscan_complete(tid, None, err)
            else:
                _scan_error(task, err)


def _scan_progress(task: dict, prog: dict):
    try:
        from monitoring.subnet_discovery import update_remote_scan
        payload = json.loads(task.get("payload") or "{}")
        if payload.get("scan_id"):
            update_remote_scan(payload["scan_id"], prog)
    except Exception:
        pass


def _scan_error(task: dict, err: str):
    try:
        from monitoring.subnet_discovery import complete_remote_scan
        payload = json.loads(task.get("payload") or "{}")
        if payload.get("scan_id"):
            complete_remote_scan(payload["scan_id"], [], error=err)
    except Exception:
        pass


def handle(h, method, path, body) -> bool:
    # ── POST /api/agent/enroll ────────────────────────────────────
    if path == "/api/agent/enroll" and method == "POST":
        ip = h.client_address[0]
        if _enroll_rate_limited(ip):
            h._json(429, {"error": "too many enrollment attempts"})
            return True
        tok = str(body.get("enrollment_token") or "")
        proto = body.get("protocol_version")
        if proto != PROBE_PROTOCOL_VERSION:
            h._json(409, {"error": "protocol_unsupported",
                          "server_protocol": PROBE_PROTOCOL_VERSION,
                          "server_version": APP_VERSION})
            return True
        if not tok or len(tok) > 128:
            h._json(401, {"error": "unauthorized"})
            return True
        tok_hash = hashlib.sha256(tok.encode()).hexdigest()
        probe = db_consume_enroll_token(tok_hash)
        if not probe:
            log_probes.warning(f"Probe enrollment rejected from {ip} (bad/expired token)")
            h._json(401, {"error": "unauthorized"})
            return True
        pid = probe["probe_id"]
        # Replace any previous live token (re-enroll path).
        if probe.get("token_id"):
            try:
                from db.api_tokens import db_revoke_api_token
                from core.auth import auth_evict_api_token_hash
                old = db_revoke_api_token(int(probe["token_id"]))
                if old:
                    auth_evict_api_token_hash(old)
            except Exception:
                pass
        bearer = "pw_" + secrets.token_hex(32)
        bearer_hash = hashlib.sha256(bearer.encode()).hexdigest()
        from db.api_tokens import db_create_api_token
        token_id = db_create_api_token(bearer_hash, f"probe:{probe['name']}",
                                       f"probe:{pid}", "probe",
                                       expires_at=None, probe_id=pid)
        if not token_id:
            h._error(500, "enrollment failed", context="agent_enroll")
            return True
        db_set_probe_enrolled(pid, token_id)
        db_probe_checkin(pid, ip, _status_fields(body))
        db_log_audit(f"probe:{pid}", ip, "probe_enroll", probe["name"],
                     f"agent_version={str(body.get('agent_version') or '')[:32]}")
        log_probes.info(f"Probe enrolled: {probe['name']} ({pid}) from {ip}")
        STATE._broadcast("probe_status", {"probe_id": pid, "connected": True,
                                          "status": "enrolled"})
        fresh = db_get_probe(pid) or probe
        h._json(200, {"ok": True, "probe_id": pid, "probe_token": bearer,
                      "server_time": time.time(),
                      "server_version": APP_VERSION,
                      "config_version": int(fresh.get("config_version") or 1)})
        return True

    # ── POST /api/agent/checkin ───────────────────────────────────
    if path == "/api/agent/checkin" and method == "POST":
        probe = _require_probe(h)
        if not probe: return True
        pid = probe["probe_id"]
        proto = body.get("protocol_version")
        if proto != PROBE_PROTOCOL_VERSION:
            h._json(409, {"error": "protocol_unsupported",
                          "server_protocol": PROBE_PROTOCOL_VERSION,
                          "server_version": APP_VERSION})
            return True
        results = body.get("results") or []
        if not isinstance(results, list) or len(results) > _MAX_RESULTS_PER_CHECKIN:
            h._json(400, {"error": "bad results batch"})
            return True

        with _probe_lock(pid):
            accepted, rejected = _ingest_results(probe, results)

        fields = _status_fields(body)
        was_disconnected = (time.time() - float(probe.get("last_seen") or 0) >
                            60) or int(probe.get("offline_alerted") or 0)
        if int(probe.get("offline_alerted") or 0):
            fields["offline_alerted"] = 0
        db_probe_checkin(pid, h.client_address[0], fields)
        if was_disconnected:
            try:
                from monitoring.probe_watchdog import emit_probe_online
                emit_probe_online(db_get_probe(pid) or probe)
            except Exception as e:
                log_probes.debug(f"probe_online emit failed: {e}")

        _apply_task_progress(probe, body.get("tasks") or [])
        tasks = [{"task_id": t["id"], "task_type": t["task_type"],
                  "payload": json.loads(t.get("payload") or "{}")}
                 for t in db_dispatch_pending_tasks(pid)]
        # Recently-cancelled tasks so the agent can abort a sweep mid-flight.
        from db.helpers import db_query
        _cx = db_query("main",
                       "SELECT id FROM agent_tasks WHERE probe_id = ? "
                       "AND state = 'cancelled' AND finished_at > ?",
                       (pid, time.time() - 300))
        cancelled = [int(r["id"]) for r in _cx]

        fresh = db_get_probe(pid) or probe
        h._json(200, {"ok": True, "server_time": time.time(),
                      "server_version": APP_VERSION,
                      "config_version": int(fresh.get("config_version") or 1),
                      "acked": accepted, "rejected": rejected,
                      "tasks": tasks, "cancelled_tasks": cancelled})
        return True

    # ── GET /api/agent/config ─────────────────────────────────────
    if path == "/api/agent/config" and method == "GET":
        probe = _require_probe(h)
        if not probe: return True
        pid = probe["probe_id"]
        # Read the version BEFORE building the sensor list: a concurrent
        # assignment change then yields (old list, old version) and the agent
        # re-pulls next cycle — never (stale list, new version).
        cfg_version = int(probe.get("config_version") or 1)
        try:
            sensors = _build_agent_config(pid)
        except Exception as e:
            h._error(500, "config build failed", e, context="agent_config")
            return True
        h._json(200, {"ok": True, "config_version": cfg_version,
                      "server_time": time.time(),
                      "checkin_interval": 10,
                      "sensors": sensors})
        return True

    # ── GET /api/agent/package — managed-update payload download ──
    if path == "/api/agent/package" and method == "GET":
        probe = _require_probe(h)
        if not probe: return True
        from urllib.parse import parse_qs, urlparse as _up
        want = (parse_qs(_up(h.path).query).get("build", [""])[0] or "").strip()
        try:
            from core.agent_package import build_release_payload
            data, build_id, sha = build_release_payload()
        except Exception as e:
            h._error(500, "package build failed", e, context="agent_package")
            return True
        # A campaign pins the target build; if the server's current payload no
        # longer matches (code changed since launch), refuse rather than ship a
        # different build — the agent aborts and the campaign halts. (The agent
        # also checksum-verifies, so this is belt-and-suspenders.)
        if want and want != build_id:
            h._json(409, {"error": "build_unavailable", "current_build": build_id})
            return True
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("X-PingWatch-Build", build_id)
        h.send_header("X-PingWatch-SHA256", sha)
        h.send_header("Cache-Control", "no-store")
        h._sec_headers()
        h.end_headers()
        h.wfile.write(data)
        return True

    # ── POST /api/agent/update-report — terminal update outcome ───
    if path == "/api/agent/update-report" and method == "POST":
        probe = _require_probe(h)
        if not probe: return True
        pid = probe["probe_id"]
        rep = body if isinstance(body, dict) else {}
        outcome = str(rep.get("outcome") or "")[:32]
        from db.probes import (db_record_update_report,
                               db_set_probe_update_state,
                               db_set_campaign_probe_state)
        db_record_update_report(pid, rep)
        term = {"success": "succeeded",
                "rolled_back": "rolled_back"}.get(outcome, outcome or "")
        db_set_probe_update_state(
            pid, term, target=str(rep.get("target_build") or "")[:64],
            error="" if outcome == "success" else str(rep.get("reason") or "")[:500])
        cid = rep.get("campaign_id")
        if cid:
            try:
                db_set_campaign_probe_state(
                    int(cid), pid,
                    "succeeded" if outcome == "success" else "rolled_back",
                    error=str(rep.get("reason") or ""), finished=True)
            except (TypeError, ValueError):
                pass
        log_probes.info("probe %s update report: %s (%s -> %s)", pid, outcome,
                        rep.get("from_build"), rep.get("to_build"))
        STATE._broadcast("probe_status", {"probe_id": pid, "update_state": term})
        h._json(200, {"ok": True})
        return True

    # ── POST /api/agent/tasks/<id>/result ─────────────────────────
    m = _RE_TASK_RESULT.match(path)
    if m and method == "POST":
        probe = _require_probe(h)
        if not probe: return True
        tid = int(m.group(1))
        task = db_get_task(tid)
        if not task or task.get("probe_id") != probe["probe_id"]:
            h._json(404, {"error": "task not found"})
            return True
        if task["state"] in ("done", "cancelled", "expired"):
            h._json(409, {"error": f"task is {task['state']}"})
            return True
        rows = body.get("rows") or []
        if not isinstance(rows, list) or len(rows) > _MAX_TASK_ROWS_PER_CHUNK:
            h._json(400, {"error": "bad chunk"})
            return True
        done  = bool(body.get("done"))
        error = str(body.get("error") or "")[:512]
        # Sync waiter tasks (device_scan, snmp_interfaces) bypass the _SCANS
        # registry — their result goes straight to the handler thread blocked
        # in run_remote_device_scan / run_remote_snmp_interfaces. The helper
        # re-bounds the rows when it drains them, so accept a full chunk here.
        if task.get("task_type") in _SYNC_WAITER_TASKS:
            with _DEVSCAN_LOCK:
                w = _DEVSCAN_WAITERS.get(tid)
                if w and rows:
                    w["rows"].extend(rows[:_MAX_TASK_ROWS_PER_CHUNK])
            if done or error:
                db_set_task_state(tid, "error" if error else "done",
                                  error=error)
                _devscan_complete(tid, None, error)
            elif task["state"] != "running":
                db_set_task_state(tid, "running")
            h._json(200, {"ok": True})
            return True
        try:
            payload = json.loads(task.get("payload") or "{}")
        except Exception:
            payload = {}
        scan_id = payload.get("scan_id") or ""
        try:
            from monitoring.subnet_discovery import (
                append_remote_scan_rows, complete_remote_scan)
            if rows and scan_id:
                append_remote_scan_rows(scan_id, rows)
            if done or error:
                if scan_id:
                    complete_remote_scan(scan_id, None, error=error)
                db_set_task_state(tid, "error" if error else "done", error=error)
            elif task["state"] != "running":
                db_set_task_state(tid, "running")
        except Exception as e:
            h._error(500, "task result processing failed", e,
                     context="agent_task_result")
            return True
        h._json(200, {"ok": True})
        return True

    return False


def _build_agent_config(pid: str) -> list:
    """Sensor configs for every running sensor whose effective probe == pid.

    Device-default fallbacks are resolved and Fernet credentials decrypted
    server-side so the agent stays dumb — transport is TLS with agent-side
    certificate pinning. Sensors are included even when the agent lacks the
    capability (flagged via 'requires'); the agent reports those as failed
    probes with a clear detail string.
    """
    from db.backups import decrypt_pw

    def _dec(v):
        try:
            return decrypt_pw(v) if v else ""
        except Exception:
            return ""

    with STATE._lock:
        pairs = [(dev, s) for dev in STATE.devices.values()
                 for s in dev.sensors.values()]
    out = []
    for dev, s in pairs:
        if not s.running or effective_probe(dev, s) != pid:
            continue
        cfg = {
            "did": s.device_id, "sid": s.sensor_id,
            "name": s.name, "stype": s.stype,
            "host": s.host, "port": s.port, "url": s.url,
            "interval": int(s.interval or 5),
            "timeout": int(s.timeout or 4),
            "verify_ssl": bool(s.verify_ssl),
        }
        if s.stype == "snmp":
            cfg.update({
                "snmp_community": s.snmp_community or
                                  getattr(dev, "snmp_community_default", "") or "public",
                "snmp_oid": s.snmp_oid,
                "snmp_version": s.snmp_version or
                                getattr(dev, "snmp_version_default", "") or "2c",
                "snmp_unit": s.snmp_unit or "",
            })
            if cfg["snmp_version"] == "3":
                try:
                    cfg["snmp_v3"] = s._resolve_snmp_v3_creds()
                except Exception:
                    cfg["snmp_v3"] = None
            cfg["requires"] = "snmpget"
        elif s.stype == "dns":
            cfg.update({"dns_query": s.dns_query or s.host,
                        "dns_record_type": s.dns_record_type or "A",
                        "dns_server": s.dns_server or ""})
        elif s.stype in ("http", "http_keyword"):
            cfg.update({"http_expected_status": int(s.http_expected_status or 0),
                        "keyword": s.keyword or "",
                        "keyword_case": bool(s.keyword_case)})
            if s.stype == "http":
                # Tell the agent to also report cert days-to-expiry; the
                # server still owns the warn/crit thresholds.
                cfg["cert_check"] = bool(getattr(s, "cert_warn_days", 0)
                                         or getattr(s, "cert_crit_days", 0))
        elif s.stype == "banner":
            cfg["banner_regex"] = s.banner_regex or ""
        elif s.stype == "smtp":
            cfg.update({"smtp_tls": s.smtp_tls or "none",
                        "smtp_user": s.smtp_user or "",
                        "smtp_password": _dec(s.smtp_password),
                        "smtp_from": s.smtp_from or "",
                        "smtp_rcpt": s.smtp_rcpt or "",
                        "smtp_test_level": s.smtp_test_level or "ehlo"})
        elif s.stype == "ssh":
            cfg.update({"ssh_user": s.ssh_user or "",
                        "ssh_password": _dec(s.ssh_password),
                        "ssh_private_key": _dec(s.ssh_private_key),
                        "ssh_auth_type": s.ssh_auth_type or "password",
                        "ssh_test_level": s.ssh_test_level or "banner",
                        "requires": "paramiko"})
        elif s.stype == "sftp":
            cfg.update({"sftp_user": s.sftp_user or "",
                        "sftp_password": _dec(s.sftp_password),
                        "sftp_private_key": _dec(s.sftp_private_key),
                        "sftp_auth_type": s.sftp_auth_type or "password",
                        "sftp_test_level": s.sftp_test_level or "open",
                        "sftp_remote_path": s.sftp_remote_path or "",
                        "sftp_expected_sha256": s.sftp_expected_sha256 or "",
                        "requires": "paramiko"})
        elif s.stype == "radius":
            cfg.update({"radius_secret": _dec(s.radius_secret),
                        "radius_test_level": s.radius_test_level or "reachable",
                        "radius_username": s.radius_username or "",
                        "radius_password": _dec(s.radius_password),
                        "radius_nas_id": s.radius_nas_id or "pingwatch"})
        elif s.stype == "vmware":
            cfg.update({"vmware_user": s.vmware_user or
                                       getattr(dev, "vmware_user_default", ""),
                        "vmware_password": _dec(s.vmware_password or
                                                getattr(dev, "vmware_password_default", "")),
                        "vmware_vm_id": s.vmware_vm_id or "",
                        "vmware_metric": s.vmware_metric or "",
                        "vmware_disk_path": getattr(s, "vmware_disk_path", "") or "",
                        "requires": "pyvmomi"})
        out.append(cfg)
    return out
