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
import threading
import time as _time
from urllib.parse import urlparse, parse_qs

import core.app_state as app_state
from core.config import (
    _RE_DEVICE, _RE_DEVICE_ACTION, _RE_DEVICE_LOGS,
    _RE_SENSOR, _RE_SENSOR_ACTION, _RE_SENSOR_ITEM,
    _RE_SENSOR_HISTORY, _RE_SENSOR_SUMMARY, _RE_DEVICE_SCAN,
    _RE_SENSOR_LOGS, _RE_AVAILABILITY,
)
from db     import (
    _db_enqueue, db_save, db_log_audit,
    db_load_err_logs, db_clear_err_logs, db_clear_sensor_err_logs,
    db_clear_device_traps, db_load_history, db_load_summary, db_load_availability,
)
from db.ipam import ipam_sync_device_add, ipam_sync_device_update, ipam_sync_device_delete
from core.logger import log
from monitoring.probes import probe_ping, probe_tcp, probe_http, probe_tls, probe_banner

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
        did = STATE.add_device(name, host, group)
        with STATE._lock:
            if did in STATE.devices:
                STATE.devices[did].webhook_url = webhook_url
        _db_enqueue(lambda: db_save(STATE))
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
            if "host" in body:
                h2 = body["host"].strip()
                if len(h2) > 253:
                    h._json(400, {"error": "host too long (max 253)"}); return True
                if not h._valid_host(h2):
                    h._json(400, {"error": "invalid host"}); return True
                dev.host = h2
                # Propagate new host to all sensors that haven't been manually overridden
                for _s in dev.sensors.values():
                    if not _s.host_override:
                        _s.host = h2
            _dev_edit_name = dev.name
            _new_host = dev.host
            _new_name = dev.name
        _db_enqueue(lambda: db_save(STATE))
        _d = did
        _db_enqueue(lambda: ipam_sync_device_update(_d, _old_host, _new_host, _new_name))
        db_log_audit(user, h.client_address[0], 'device_edit', _dev_edit_name)
        h._json(200, {"status": "updated"})
        return True

    # ── /api/devices/{did} DELETE ─────────────────────────────────
    if m and method == "DELETE":
        user, _ = h._require("operator")
        if not user: return True
        ddid = m.group(1)
        with STATE._lock:
            dd     = STATE.devices.get(ddid)
            ddname = dd.name if dd else ddid
        STATE.remove_device(ddid)
        _db_enqueue(lambda: db_save(STATE))
        _dd = ddid
        _db_enqueue(lambda: ipam_sync_device_delete(_dd))
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
                  "fail_after", "recover_after", "warn_ms", "crit_ms",
                  "loss_warn_pct", "loss_crit_pct",
                  "keyword", "keyword_case", "banner_regex", "alerts_muted"]:
            if k in body: kwargs[k] = body[k]
        if "port" in body: kwargs["port"] = body["port"]
        if "type" in body: kwargs["stype"] = body["type"]
        if "interval" in kwargs:
            kwargs["interval"] = max(1, min(3600, int(kwargs["interval"])))
        if "timeout" in kwargs:
            iv = int(kwargs.get("interval", body.get("interval", 5)))
            kwargs["timeout"] = max(1, min(iv, int(kwargs["timeout"])))
        if kwargs.get("banner_regex"):
            try:
                re.compile(kwargs["banner_regex"])
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
        h._json(200, {"status": "updated"})
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
        iv    = max(1, min(3600, int(body.get("interval", 5))))
        to    = max(1, min(iv, int(body.get("timeout", 4))))
        vssl  = bool(body.get("verify_ssl", True))
        comm  = body.get("snmp_community", "public")
        oid   = body.get("snmp_oid", "1.3.6.1.2.1.1.1.0")
        sver  = body.get("snmp_version", "2c")
        xstat = int(body.get("http_expected_status", 0))
        fa    = max(1, int(body.get("fail_after",    1) or 1))
        ra    = max(1, int(body.get("recover_after", 1) or 1))
        wms   = int(body["warn_ms"])  if body.get("warn_ms")  else None
        cms   = int(body["crit_ms"])  if body.get("crit_ms")  else None
        lwp   = int(body.get("loss_warn_pct", 0) or 0)
        lcp   = int(body.get("loss_crit_pct", 0) or 0)
        kw    = body.get("keyword", "")
        kwc   = bool(body.get("keyword_case", False))
        bnr   = body.get("banner_regex", "")
        if bnr:
            try:
                re.compile(bnr)
            except re.error as _re_err:
                h._json(400, {"error": f"Invalid banner_regex: {_re_err}"}); return True
        if stype == "http" and url and not h._valid_url(url):
            h._json(400, {"error": "url must start with http:// or https://"}); return True
        if stype in ("ping", "tcp", "snmp", "dns") and host and not h._valid_host(host):
            h._json(400, {"error": "invalid host"}); return True
        sid = STATE.add_sensor(did, name, stype, host, port, url,
                               iv, to, vssl, comm, oid, sver,
                               fail_after=fa, recover_after=ra,
                               warn_ms=wms, crit_ms=cms,
                               loss_warn_pct=lwp, loss_crit_pct=lcp,
                               keyword=kw, keyword_case=kwc, banner_regex=bnr)
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
        _db_enqueue(lambda: db_save(STATE))
        _dev_name = dev.name if dev else did
        db_log_audit(user, h.client_address[0], 'sensor_create', f"{_dev_name}/{name}")
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
