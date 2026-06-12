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
    st = body.get("status") or {}
    if isinstance(st, dict) and st:
        if st.get("os") is not None:
            fields["os_info"] = str(st["os"])[:200]
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
