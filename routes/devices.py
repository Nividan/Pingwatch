"""
routes/devices.py — Device and sensor CRUD + port-scan endpoint.

Handles: /api/devices (GET), /api/device (POST), /api/devices/{did} (GET/PATCH/DELETE),
         /api/devices/{did}/(start|stop) (POST), /api/devices/{did}/scan (POST),
         /api/sensors/{did} (POST), /api/sensors/{did}/{sid} (PATCH/DELETE),
         /api/sensors/{did}/{sid}/(start|stop) (POST),
         /api/sensors/{did}/{sid}/history (GET), /api/sensors/{did}/{sid}/summary (GET),
         /api/devices/{did}/logs (GET/DELETE), /api/sensors/{did}/{sid}/logs (DELETE),
         /api/start (POST), /api/stop (POST).
"""

import re
import re as _re_mod
import threading
import time as _time
from urllib.parse import urlparse, parse_qs

_RE_SENSOR_ANOMALY_RESET = _re_mod.compile(r"^/api/sensors/([^/]+)/([^/]+)/anomaly/reset$")

import core.app_state as app_state
from core.config import (
    _RE_DEVICE, _RE_DEVICE_ACTION, _RE_DEVICE_LOGS, _RE_DEVICE_SIP,
    _RE_SENSOR, _RE_SENSOR_ACTION, _RE_SENSOR_ITEM,
    _RE_SENSOR_HISTORY, _RE_SENSOR_SUMMARY, _RE_DEVICE_SCAN,
    _RE_SENSOR_LOGS, _RE_AVAILABILITY,
)
from db     import (
    _db_enqueue, db_save, db_log_audit,
    db_load_err_logs, db_clear_err_logs, db_clear_sensor_err_logs,
    db_clear_device_traps, db_load_history, db_load_summary, db_load_availability,
    db_resolve_events_by_sensor, db_resolve_flaps_by_sensor,
    db_clear_stage_state_for_sensor,
    db_reset_anomaly_baseline,
)
from db.ipam import ipam_sync_device_add, ipam_sync_device_update, ipam_sync_device_delete
from monitoring.network_map import topo_prune_pw_links
from core.logger import log
from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_tls, probe_banner

_vmware_ssl_warned = set()

# ── Port-scan target list ─────────────────────────────────────────
_SCAN_TARGETS = [
    {"name": "Ping",       "stype": "ping",   "port": None,  "tout": 3},
    {"name": "FTP",        "stype": "banner", "port": 21,    "tout": 2},
    {"name": "SSH",        "stype": "banner", "port": 22,    "tout": 2},
    {"name": "SMTP",       "stype": "tcp",    "port": 25,    "tout": 2},
    {"name": "DNS",        "stype": "tcp",    "port": 53,    "tout": 2},
    {"name": "HTTP",       "stype": "http",   "port": 80,    "tout": 4},
    {"name": "HTTPS/TLS",  "stype": "tls",    "port": 443,   "tout": 4},
    {"name": "RDP",        "stype": "tcp",    "port": 3389,  "tout": 2},
    {"name": "MySQL",      "stype": "tcp",    "port": 3306,  "tout": 2},
    {"name": "PostgreSQL", "stype": "tcp",    "port": 5432,  "tout": 2},
    {"name": "Redis",      "stype": "banner", "port": 6379,  "tout": 2},
    {"name": "MongoDB",    "stype": "tcp",    "port": 27017, "tout": 2},
    {"name": "LDAP",       "stype": "tcp",    "port": 389,   "tout": 2},
    {"name": "HTTP-Alt",   "stype": "http",   "port": 8080,  "tout": 4},
    {"name": "HTTPS-Alt",  "stype": "tls",    "port": 8443,  "tout": 4},
]

STATE = app_state.STATE

_SCAN_DEFAULTS_STR = "ping,21,22,25,53,80,443,3389,3306,5432,6379,27017,389,8080,8443"


def _get_scan_targets():
    """Return scan target list from the scan_ports setting, falling back to defaults."""
    import core.settings as _settings
    raw = (_settings.get("scan_ports") or "").strip()
    if not raw:
        return _SCAN_TARGETS

    _known = {t["port"]: t for t in _SCAN_TARGETS}
    _known[None] = _SCAN_TARGETS[0]   # ping entry keyed by None

    targets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if entry.lower() == "ping":
            targets.append(_known[None])
        else:
            try:
                port = int(entry)
            except ValueError:
                continue
            if port in _known:
                targets.append(_known[port])
            else:
                targets.append({"name": f"Port {port}", "stype": "tcp",
                                 "port": port, "tout": 2})
    return targets or _SCAN_TARGETS


def _maybe_resize_executor():
    """Re-evaluate auto worker count after sensor count changes.

    No-op when max_workers_executor is set to a manual value (>= 4).
    Called via _db_enqueue so it runs after the STATE is already saved.
    """
    import concurrent.futures as _cf
    import core.settings as _settings
    _mw_override = int(_settings.get("max_workers_executor", 0) or 0)
    if _mw_override >= 4:
        return  # manual override in effect
    _count = sum(len(d.sensors) for d in STATE.devices.values())
    _mw = max(64, min(512, _count // 4 or 64))
    if STATE._executor._max_workers != _mw:
        STATE._executor = _cf.ThreadPoolExecutor(
            max_workers=_mw, thread_name_prefix='pw-sensor'
        )
        STATE._scheduler._executor = STATE._executor
        log.info(f"Executor auto-resized to {_mw} workers ({_count} sensors)")


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    STATE = app_state.STATE  # always current reference

    # ── /api/devices GET ─────────────────────────────────────────
    if path == "/api/devices" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"devices": STATE.all_devices()})
        return True

    # ── /api/start / /api/stop POST ──────────────────────────────
    if path == "/api/start" and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        STATE.start_all()
        db_log_audit(user, h.client_address[0], 'sensors_start')
        h._json(200, {"status": "started"})
        return True

    if path == "/api/stop" and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        STATE.stop_all()
        db_log_audit(user, h.client_address[0], 'sensors_stop')
        h._json(200, {"status": "stopped"})
        return True

    # ── /api/device POST (create) ─────────────────────────────────
    if path == "/api/device" and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        name        = body.get("name", "").strip()
        host        = body.get("host", "").lower().strip()
        group       = body.get("group", "Default Group")
        webhook_url = body.get("webhook_url", "").strip()
        if not name or not host:
            h._json(400, {"error": "name and host required"}); return True
        if len(name) > 255:
            h._json(400, {"error": "name too long (max 255)"}); return True
        if len(host) > 253:
            h._json(400, {"error": "host too long (max 253)"}); return True
        if webhook_url and len(webhook_url) > 2048:
            h._json(400, {"error": "webhook_url too long (max 2048)"}); return True
        if not h._valid_host(host):
            h._json(400, {"error": "invalid host — use a hostname or IP address"}); return True
        snmp_community_default  = body.get("snmp_community_default", "").strip()
        snmp_version_default    = body.get("snmp_version_default", "").strip()
        if snmp_version_default not in ("", "1", "2c", "3"):
            snmp_version_default = ""
        vmware_user_default     = body.get("vmware_user_default", "").strip()
        vmware_password_default = body.get("vmware_password_default", "")
        if vmware_password_default:
            from db.backups import encrypt_pw
            vmware_password_default = encrypt_pw(vmware_password_default)
        did = STATE.add_device(name, host, group)
        with STATE._lock:
            if did in STATE.devices:
                STATE.devices[did].webhook_url = webhook_url
                STATE.devices[did].snmp_community_default  = snmp_community_default
                STATE.devices[did].snmp_version_default    = snmp_version_default
                STATE.devices[did].vmware_user_default     = vmware_user_default
                STATE.devices[did].vmware_password_default = vmware_password_default
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        _did, _name, _host = did, name, host
        _db_enqueue(lambda: ipam_sync_device_add(_did, _name, _host))
        db_log_audit(user, h.client_address[0], 'device_create', name)
        h._json(200, {"did": did})
        return True

    # ── /api/devices/{did}/logs GET ──────────────────────────────
    m = _RE_DEVICE_LOGS.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {"logs": db_load_err_logs(m.group(1))})
        return True

    # ── /api/devices/{did}/logs DELETE ───────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        did = m.group(1)
        _db_enqueue(lambda: db_clear_err_logs(did))
        with STATE._lock:
            _dev  = STATE.devices.get(did)
            _host = _dev.host if _dev else None
        if _host:
            _h = _host
            _db_enqueue(lambda: db_clear_device_traps(_h))
        db_log_audit(user, h.client_address[0], 'logs_clear', did)
        h._json(200, {"ok": True})
        return True

    # ── /api/devices/{did}/scan POST ─────────────────────────────
    m = _RE_DEVICE_SCAN.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did = m.group(1)
        dev = STATE.get_device(did)
        if not dev:
            h._json(404, {"error": "not found"}); return True
        host    = dev.host
        results = []
        _lock   = threading.Lock()

        def _scan_one(t):
            stype, port, tout = t["stype"], t["port"], t["tout"]
            try:
                if stype == "ping":
                    r = probe_ping(host, timeout=tout)
                elif stype == "tcp":
                    r = probe_tcp(host, port, timeout=tout)
                elif stype == "http":
                    url = f"http://{host}" if port == 80 else f"http://{host}:{port}"
                    r = probe_http(url, timeout=tout, verify_ssl=False)
                elif stype == "tls":
                    r = probe_tls(host, port, timeout=tout)
                elif stype == "banner":
                    r = probe_banner(host, port, timeout=tout)
                else:
                    return
            except Exception as e:
                r = {"ok": False, "ms": None, "detail": str(e)[:80]}
            if r.get("ok"):
                with _lock:
                    results.append({
                        "stype": stype, "name": t["name"],
                        "port":  port,  "ms":   r.get("ms"),
                        "detail": r.get("detail", ""),
                    })

        targets = _get_scan_targets()
        threads = [threading.Thread(target=_scan_one, args=(t,), daemon=True)
                   for t in targets]
        deadline = _time.monotonic() + 8  # 8s total for all probes
        for th in threads: th.start()
        for th in threads:
            remaining = deadline - _time.monotonic()
            if remaining > 0: th.join(timeout=remaining)
        h._json(200, {"did": did, "host": host, "services": results})
        return True

    # ── /api/devices/{did}/(start|stop) POST ─────────────────────
    m = _RE_DEVICE_ACTION.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did, action = m.group(1), m.group(2)
        if action == "start": STATE.start_device(did)
        else:                  STATE.stop_device(did)
        h._json(200, {"status": action})
        return True

    # ── /api/devices/{did} GET ────────────────────────────────────
    m = _RE_DEVICE.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        dev = STATE.get_device(m.group(1))
        if dev: h._json(200, dev.to_dict())
        else:   h._json(404, {"error": "not found"})
        return True

    # ── /api/devices/{did} PATCH ──────────────────────────────────
    if m and method == "PATCH":
        user, _ = h._require("operator")
        if not user: return True
        did = m.group(1)
        with STATE._lock:
            dev = STATE.devices.get(did)
            if not dev:
                h._json(404, {"error": "device not found"}); return True
            _old_host = dev.host
            _old_name = dev.name
            if "group" in body: dev.group = body["group"]
            if "name" in body:
                _n = str(body["name"]).strip()
                if len(_n) > 255:
                    h._json(400, {"error": "name too long (max 255)"}); return True
                dev.name = _n
            if "webhook_url" in body:
                _w = body["webhook_url"].strip()
                if _w and len(_w) > 2048:
                    h._json(400, {"error": "webhook_url too long (max 2048)"}); return True
                dev.webhook_url = _w
            if "alerts_muted" in body: dev.alerts_muted = bool(body["alerts_muted"])
            if "snmp_community_default" in body:
                dev.snmp_community_default = str(body["snmp_community_default"]).strip()
            if "snmp_version_default" in body:
                _sv = str(body["snmp_version_default"]).strip()
                if _sv in ("", "1", "2c", "3"):
                    dev.snmp_version_default = _sv
            if "vmware_user_default" in body:
                dev.vmware_user_default = str(body["vmware_user_default"]).strip()
            if "vmware_password_default" in body:
                _vpw = body["vmware_password_default"]
                if _vpw:
                    from db.backups import encrypt_pw
                    dev.vmware_password_default = encrypt_pw(_vpw)
                # empty string = keep existing (don't clear)
            if "host" in body:
                h2 = body["host"].strip()
                if len(h2) > 253:
                    h._json(400, {"error": "host too long (max 253)"}); return True
                if not h._valid_host(h2):
                    h._json(400, {"error": "invalid host"}); return True
                from core.orchestration import propagate_device_host
                propagate_device_host(dev, h2)
            if "secondary_ips" in body:
                sips = body["secondary_ips"]
                if not isinstance(sips, list):
                    h._json(400, {"error": "secondary_ips must be a list"}); return True
                if len(sips) > 50:
                    h._json(400, {"error": "too many secondary IPs (max 50)"}); return True
                cleaned = []
                for _sip in sips:
                    _sip = str(_sip).strip().lower()
                    if not _sip or len(_sip) > 253:
                        continue
                    if not h._valid_host(_sip):
                        h._json(400, {"error": f"invalid secondary IP: {_sip}"}); return True
                    if _sip == dev.host.lower():
                        continue  # skip — same as primary host
                    if _sip not in cleaned:
                        cleaned.append(_sip)
                dev.secondary_ips = cleaned
            _dev_edit_name = dev.name
            _new_host = dev.host
            _new_name = dev.name
        _db_enqueue(lambda: db_save(STATE))
        _d = did
        _db_enqueue(lambda: ipam_sync_device_update(_d, _old_host, _new_host, _new_name))
        db_log_audit(user, h.client_address[0], 'device_edit', _dev_edit_name)
        if "alerts_muted" in body:
            STATE._broadcast("device_status", {"did": did, "status": dev.status})
        h._json(200, {"status": "updated"})
        return True

    # ── /api/device/{did}/secondary-ip POST ────────────────────────
    m_sip = _RE_DEVICE_SIP.match(path)
    if m_sip and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        did = m_sip.group(1)
        ip = str(body.get("ip", "")).strip().lower()
        if not ip or len(ip) > 253:
            h._json(400, {"error": "ip is required"}); return True
        if not h._valid_host(ip):
            h._json(400, {"error": "invalid IP address"}); return True
        with STATE._lock:
            dev = STATE.devices.get(did)
            if not dev:
                h._json(404, {"error": "device not found"}); return True
            if ip == dev.host.lower():
                h._json(400, {"error": "IP is already the primary host"}); return True
            sips = list(getattr(dev, "secondary_ips", []) or [])
            if ip in sips:
                h._json(200, {"status": "already_present", "secondary_ips": sips}); return True
            if len(sips) >= 50:
                h._json(400, {"error": "too many secondary IPs (max 50)"}); return True
            sips.append(ip)
            dev.secondary_ips = sips
            _dev_name = dev.name
        _db_enqueue(lambda: db_save(STATE))
        db_log_audit(user, h.client_address[0], 'device_edit', _dev_name)
        h._json(200, {"status": "added", "secondary_ips": sips})
        return True

    # ── /api/devices/{did} DELETE ─────────────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        ddid = m.group(1)
        with STATE._lock:
            dd     = STATE.devices.get(ddid)
            ddname = dd.name if dd else ddid
        # Collect sensor IDs before removing the device (for event cleanup)
        with STATE._lock:
            _sensor_ids = list(dd.sensors.keys()) if dd else []
        STATE.remove_device(ddid)
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        _dd = ddid
        _db_enqueue(lambda: ipam_sync_device_delete(_dd))
        _db_enqueue(lambda: topo_prune_pw_links(_dd))
        # Auto-resolve active events/flaps for all sensors in the deleted device
        for _sid in _sensor_ids:
            _db_enqueue(lambda _d=_dd, _s=_sid: db_resolve_events_by_sensor(_d, _s))
            _db_enqueue(lambda _d=_dd, _s=_sid: db_resolve_flaps_by_sensor(_d, _s))
            _db_enqueue(lambda _d=_dd, _s=_sid: db_clear_stage_state_for_sensor(_d, _s))
        db_log_audit(user, h.client_address[0], 'device_delete', ddname)
        h._json(200, {"status": "ok"})
        return True

    # ── /api/sensors/{did}/{sid}/history GET ─────────────────────
    m = _RE_SENSOR_HISTORY.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        _qs = parse_qs(urlparse(h.path).query)
        try:
            minutes = max(1, int(_qs.get("minutes", ["1440"])[0]))
            limit   = max(1, min(10000, int(_qs.get("limit", ["1000"])[0])))
        except (ValueError, TypeError):
            h._json(400, {"error": "invalid query parameter"}); return True
        h._json(200, {"samples": db_load_history(did, sid, minutes, limit)})
        return True

    # ── /api/sensors/{did}/{sid}/summary GET ─────────────────────
    m = _RE_SENSOR_SUMMARY.match(path)
    if m and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        _qs = parse_qs(urlparse(h.path).query)
        try:
            minutes = max(1, int(_qs.get("minutes", ["1440"])[0]))
        except (ValueError, TypeError):
            h._json(400, {"error": "invalid query parameter"}); return True
        h._json(200, {"summary": db_load_summary(did, sid, minutes)})
        return True

    # ── /api/sensors/{did}/{sid}/logs DELETE ─────────────────────
    m = _RE_SENSOR_LOGS.match(path)
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        _db_enqueue(lambda: db_clear_sensor_err_logs(did, sid))
        h._json(200, {"ok": True})
        return True

    # ── /api/sensors/{did}/{sid}/(start|stop) POST ───────────────
    m = _RE_SENSOR_ACTION.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did, sid, action = m.group(1), m.group(2), m.group(3)
        if action == "start": STATE.start_sensor(did, sid)
        else:                  STATE.stop_sensor(did, sid)
        h._json(200, {"status": action})
        return True

    # ── /api/sensors/{did}/{sid} PATCH ────────────────────────────
    m = _RE_SENSOR_ITEM.match(path)
    if m and method == "PATCH":
        user, _ = h._require("operator")
        if not user: return True
        did, sid = m.group(1), m.group(2)
        kwargs = {}
        for k in ["name", "stype", "host", "url", "interval", "timeout",
                  "verify_ssl", "snmp_community", "snmp_oid", "snmp_version",
                  "dns_query", "dns_record_type", "dns_server",
                  "http_expected_status",
                  "warn_ms", "crit_ms",
                  "loss_warn_pct", "loss_crit_pct",
                  "keyword", "keyword_case", "banner_regex", "alerts_muted",
                  "snmp_unit",
                  "vmware_user", "vmware_vm_id", "vmware_vm_name", "vmware_metric",
                  "vmware_disk_path",
                  "anomaly_enabled", "anomaly_sensitivity", "anomaly_min_samples"]:
            if k in body: kwargs[k] = body[k]
        # Normalize anomaly fields to safe ranges
        if "anomaly_enabled" in kwargs:
            kwargs["anomaly_enabled"] = 1 if kwargs["anomaly_enabled"] else 0
        if "anomaly_sensitivity" in kwargs:
            try:
                _s = int(kwargs["anomaly_sensitivity"])
                kwargs["anomaly_sensitivity"] = 1 if _s < 1 else (3 if _s > 3 else _s)
            except (TypeError, ValueError):
                h._json(400, {"error": "anomaly_sensitivity must be 1, 2, or 3"}); return True
        if "anomaly_min_samples" in kwargs:
            try:
                _m = int(kwargs["anomaly_min_samples"])
                kwargs["anomaly_min_samples"] = max(5, min(10000, _m))
            except (TypeError, ValueError):
                h._json(400, {"error": "anomaly_min_samples must be an integer"}); return True
        # VMware password: encrypt if provided, skip if empty (keep existing)
        if body.get("vmware_password"):
            from db.backups import encrypt_pw
            kwargs["vmware_password"] = encrypt_pw(body["vmware_password"])
        if "port" in body: kwargs["port"] = body["port"]
        if "type" in body: kwargs["stype"] = body["type"]
        try:
            from core.validation import validate_interval, validate_port
            if "interval" in kwargs:
                kwargs["interval"] = validate_interval(kwargs["interval"], 1, 3600)
            if "timeout" in kwargs:
                iv = int(kwargs.get("interval", body.get("interval", 5)))
                kwargs["timeout"] = max(1, min(iv, int(kwargs["timeout"])))
            if "port" in kwargs and kwargs["port"] not in (None, ""):
                kwargs["port"] = validate_port(kwargs["port"])
        except ValueError as _ve:
            h._json(400, {"error": str(_ve)}); return True
        except (TypeError,):
            h._json(400, {"error": "interval and timeout must be integers"}); return True
        if kwargs.get("banner_regex"):
            if len(kwargs["banner_regex"]) > 200:
                h._json(400, {"error": "banner_regex too long (max 200 chars)"}); return True
            try:
                _bpat = re.compile(kwargs["banner_regex"])
                _sm_r = [None]
                def _sm(_p=_bpat): _sm_r[0] = bool(_p.search("a" * 100))
                _smt = threading.Thread(target=_sm, daemon=True)
                _smt.start(); _smt.join(1.0)
                if _smt.is_alive():
                    h._json(400, {"error": "banner_regex is too complex"}); return True
            except re.error as _re_err:
                h._json(400, {"error": f"Invalid banner_regex: {_re_err}"}); return True
        ok = STATE.update_sensor(did, sid, **kwargs)
        if not ok:
            h._json(404, {"error": "sensor not found"}); return True
        _db_enqueue(lambda: db_save(STATE))
        with STATE._lock:
            _se_dev   = STATE.devices.get(did)
            _se_dname = _se_dev.name if _se_dev else did
            _se_sname = (_se_dev.sensors[sid].name
                         if _se_dev and sid in _se_dev.sensors else sid)
        db_log_audit(user, h.client_address[0], 'sensor_edit', f"{_se_dname}/{_se_sname}")
        if "alerts_muted" in body and _se_dev:
            STATE._broadcast("device_status", {"did": did, "status": _se_dev.status})
        h._json(200, {"status": "updated"})
        return True

    # ── /api/sensors/{did}/{sid}/anomaly/reset POST ───────────────
    mar = _RE_SENSOR_ANOMALY_RESET.match(path) if _RE_SENSOR_ANOMALY_RESET else None
    if mar and method == "POST":
        user, _ = h._require("operator")
        if not user: return True
        rdid, rsid = mar.group(1), mar.group(2)
        with STATE._lock:
            rdev = STATE.devices.get(rdid)
            rsen = rdev.sensors.get(rsid) if rdev else None
            if not rsen:
                h._json(404, {"error": "sensor not found"}); return True
            from monitoring.anomaly import reset_baseline as _anom_reset
            _anom_reset(rsen)
            _rdname, _rsname = rdev.name, rsen.name
        _db_enqueue(lambda: db_reset_anomaly_baseline(rdid, rsid))
        db_log_audit(user, h.client_address[0], 'anomaly_baseline_reset', f"{_rdname}/{_rsname}")
        h._json(200, {"ok": True, "baseline_reset": True})
        return True

    # ── /api/sensors/{did}/{sid} DELETE ──────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        sdid, ssid = m.group(1), m.group(2)
        with STATE._lock:
            sd     = STATE.devices.get(sdid)
            sdname = sd.name if sd else sdid
            ssname = sd.sensors[ssid].name if sd and ssid in sd.sensors else ssid
        STATE.remove_sensor(sdid, ssid)
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        # Auto-resolve active events/flaps for the deleted sensor
        _sd, _ss = sdid, ssid
        _db_enqueue(lambda: db_resolve_events_by_sensor(_sd, _ss))
        _db_enqueue(lambda: db_resolve_flaps_by_sensor(_sd, _ss))
        _db_enqueue(lambda: db_clear_stage_state_for_sensor(_sd, _ss))
        db_log_audit(user, h.client_address[0], 'sensor_delete', f"{sdname}/{ssname}")
        h._json(200, {"status": "ok"})
        return True

    # ── /api/sensors/{did} POST (create sensor) ───────────────────
    m = _RE_SENSOR.match(path)
    if m and method == "POST":
        user, role = h._require("operator")
        if not user: return True
        did   = m.group(1)
        name  = body.get("name", "").strip()
        stype = body.get("type", "ping")
        dev   = STATE.get_device(did)
        host  = body.get("host") or (dev.host if dev else None)
        port  = body.get("port")
        url   = (body.get("url") or "").strip() or None
        try:
            iv    = max(1, min(3600, int(body.get("interval", 5))))
            to    = max(1, min(iv, int(body.get("timeout", 4))))
        except (TypeError, ValueError):
            h._json(400, {"error": "interval and timeout must be integers"}); return True
        vssl  = bool(body.get("verify_ssl", True))
        comm  = (body.get("snmp_community") or "").strip()
        if not comm:
            comm = (dev.snmp_community_default if dev and dev.snmp_community_default else "public")
        oid   = body.get("snmp_oid", "1.3.6.1.2.1.1.1.0")
        sver  = body.get("snmp_version", "2c")
        sunit = body.get("snmp_unit", "")
        try:
            xstat = int(body.get("http_expected_status", 0))
            wms   = int(body["warn_ms"])  if body.get("warn_ms")  else None
            cms   = int(body["crit_ms"])  if body.get("crit_ms")  else None
            lwp   = int(body.get("loss_warn_pct", 0) or 0)
            lcp   = int(body.get("loss_crit_pct", 0) or 0)
        except (TypeError, ValueError):
            h._json(400, {"error": "Numeric fields must be integers"}); return True
        kw    = body.get("keyword", "")
        kwc   = bool(body.get("keyword_case", False))
        bnr   = body.get("banner_regex", "")
        # VMware fields
        vm_user      = body.get("vmware_user", "")
        vm_pw        = body.get("vmware_password", "")
        vm_vmid      = body.get("vmware_vm_id", "")
        vm_vmname    = body.get("vmware_vm_name", "")
        vm_metric    = body.get("vmware_metric", "")
        vm_disk_path = body.get("vmware_disk_path", "")
        _vm_pw_from_device = False
        if stype == "vmware" and dev:
            if not vm_user and dev.vmware_user_default:
                vm_user = dev.vmware_user_default
            if not vm_pw and dev.vmware_password_default:
                vm_pw = dev.vmware_password_default  # already encrypted
                _vm_pw_from_device = True
        if stype == "vmware" and vm_pw and not _vm_pw_from_device:
            from db.backups import encrypt_pw
            vm_pw = encrypt_pw(vm_pw)
        if bnr:
            if len(bnr) > 200:
                h._json(400, {"error": "banner_regex too long (max 200 chars)"}); return True
            try:
                _bpat = re.compile(bnr)
                _sm_r = [None]
                def _sm(_p=_bpat): _sm_r[0] = bool(_p.search("a" * 100))
                _smt = threading.Thread(target=_sm, daemon=True)
                _smt.start(); _smt.join(1.0)
                if _smt.is_alive():
                    h._json(400, {"error": "banner_regex is too complex"}); return True
            except re.error as _re_err:
                h._json(400, {"error": f"Invalid banner_regex: {_re_err}"}); return True
        if stype == "http" and url and not h._valid_url(url):
            h._json(400, {"error": "url must start with http:// or https://"}); return True
        if stype in ("ping", "tcp", "snmp", "dns") and host and not h._valid_host(host):
            h._json(400, {"error": "invalid host"}); return True
        try:
            import core.settings as _settings
            _fa = int(_settings.get("snr_fail_after",    2) or 2)
            _ra = int(_settings.get("snr_recover_after", 1) or 1)
        except (TypeError, ValueError):
            _fa, _ra = 2, 1
        sid = STATE.add_sensor(did, name, stype, host, port, url,
                               iv, to, vssl, comm, oid, sver,
                               fail_after=_fa, recover_after=_ra,
                               warn_ms=wms, crit_ms=cms,
                               loss_warn_pct=lwp, loss_crit_pct=lcp,
                               keyword=kw, keyword_case=kwc, banner_regex=bnr,
                               snmp_unit=sunit,
                               vmware_user=vm_user, vmware_password=vm_pw,
                               vmware_vm_id=vm_vmid, vmware_vm_name=vm_vmname,
                               vmware_metric=vm_metric, vmware_disk_path=vm_disk_path)
        if not sid:
            h._json(404, {"error": "device not found"}); return True
        with STATE._lock:
            dev2 = STATE.devices.get(did)
            if dev2 and sid in dev2.sensors:
                s2 = dev2.sensors[sid]
                s2.dns_query            = body.get("dns_query", "")
                s2.dns_record_type      = body.get("dns_record_type", "A")
                s2.dns_server           = body.get("dns_server", "")
                s2.http_expected_status = xstat
                # Optional anomaly config on create (UI enables post-creation; API may set here).
                if "anomaly_enabled" in body:
                    s2.anomaly_enabled = 1 if body["anomaly_enabled"] else 0
                else:
                    import core.settings as _anom_st_mod
                    if int(_anom_st_mod.get("anomaly_default_new_sensors", 0) or 0):
                        from monitoring.anomaly import SUPPORTED_STYPES as _ANOM_STYPES
                        if s2.stype in _ANOM_STYPES:
                            s2.anomaly_enabled = 1
                if "anomaly_sensitivity" in body:
                    try:
                        _sv = int(body["anomaly_sensitivity"])
                        s2.anomaly_sensitivity = 1 if _sv < 1 else (3 if _sv > 3 else _sv)
                    except (TypeError, ValueError):
                        pass
                if "anomaly_min_samples" in body:
                    try:
                        s2.anomaly_min_samples = max(5, min(10000, int(body["anomaly_min_samples"])))
                    except (TypeError, ValueError):
                        pass
        _db_enqueue(lambda: db_save(STATE))
        _db_enqueue(_maybe_resize_executor)
        _dev_name = dev.name if dev else did
        db_log_audit(user, h.client_address[0], 'sensor_create', f"{_dev_name}/{name}")
        _h = host or "unknown"
        if stype == "vmware" and not vssl and _h not in _vmware_ssl_warned:
            _vmware_ssl_warned.add(_h)
            log.warning("VMware sensor created without SSL verification for %s — enable Verify SSL for production use", _h)
        h._json(200, {"sid": sid})
        return True

    # ── GET /api/availability ─────────────────────────────────────────
    if _RE_AVAILABILITY.match(path) and method == 'GET':
        user, _ = h._require('viewer')
        if not user: return True
        qs = parse_qs(urlparse(h.path).query)
        try:
            minutes = max(1, int(qs.get('minutes', ['1440'])[0]))
        except (ValueError, TypeError):
            h._json(400, {'error': 'bad param'}); return True
        h._json(200, {'availability': db_load_availability(minutes)})
        return True

    return False
