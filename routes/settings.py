"""
routes/settings.py — Application settings and server info endpoints.

Handles: /api/settings (GET/PATCH), /api/server_info (GET),
         /api/settings/smtp_test (POST).
"""

import json
import os
import time

import app_state
from config import DB_PATH, BIND
from db     import _db_enqueue, db_log_audit, db_save_settings
from logger import log
import settings as _settings


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── /api/settings GET ─────────────────────────────────────────
    if path == "/api/settings" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        h._json(200, {
            "session_ttl":    _settings.get("session_ttl", 86400),
            "retention_days": _settings.get("retention_days", 7),
            "port":           app_state.effective_port,
            "http_port":      app_state.effective_port,
            "snmp_port":      app_state.effective_snmp_port,
            "bind":           BIND,
            "db_path":        str(DB_PATH),
            "smtp_host":       _settings.get("smtp_host", ""),
            "smtp_port":       _settings.get("smtp_port", 587),
            "smtp_tls":        _settings.get("smtp_tls",  "starttls"),
            "smtp_user":       _settings.get("smtp_user", ""),
            "smtp_from":       _settings.get("smtp_from", ""),
            "smtp_to":         _settings.get("smtp_to",   ""),
            "smtp_pass_set":   bool(_settings.get("smtp_pass", "")),
            "smtp_down_delay": int(_settings.get("smtp_down_delay", 10)),
            # Group A — sensor defaults
            "snr_interval":      int(_settings.get("snr_interval",      5)),
            "snr_timeout":       int(_settings.get("snr_timeout",       4)),
            "snr_fail_after":    int(_settings.get("snr_fail_after",    1)),
            "snr_recover_after": int(_settings.get("snr_recover_after", 1)),
            # Group B — event & history limits
            "max_flaps_display": int(_settings.get("max_flaps_display", 20)),
            "max_flap_entries":  int(_settings.get("max_flap_entries",  500)),
            "max_trap_entries":  int(_settings.get("max_trap_entries",  500)),
            # Group C — security
            "login_fail_max":    int(_settings.get("login_fail_max",    5)),
            "login_fail_window": int(_settings.get("login_fail_window", 60)),
            # Group D — branding
            "org_name":          _settings.get("org_name", ""),
            # Group E — latency colour thresholds
            "latency_good_ms":   int(_settings.get("latency_good_ms", 100)),
            "latency_warn_ms":   int(_settings.get("latency_warn_ms", 300)),
            # Group F — per-type sensor defaults
            "snr_type_defaults": json.loads(_settings.get("snr_type_defaults", "{}")),
        })
        return True

    # ── /api/settings PATCH ───────────────────────────────────────
    if path == "/api/settings" and method == "PATCH":
        user, _ = h._require("admin")
        if not user: return True
        ttl = body.get("session_ttl")
        if ttl is not None:
            try:
                ttl = int(ttl)
                if ttl < 60:
                    h._json(400, {"error": "session_ttl must be at least 60 seconds"}); return True
            except (ValueError, TypeError):
                h._json(400, {"error": "session_ttl must be an integer"}); return True
            _settings.load({"session_ttl": ttl})
            _db_enqueue(lambda: db_save_settings({"session_ttl": ttl}))
        if "retention_days" in body:
            try:
                days = max(1, int(body["retention_days"]))
            except (ValueError, TypeError):
                h._json(400, {"error": "retention_days must be an integer"}); return True
            _settings.load({"retention_days": days})
            _db_enqueue(lambda: db_save_settings({"retention_days": days}))
        if "http_port" in body:
            try:
                _hp = max(1, min(65535, int(body["http_port"])))
            except (ValueError, TypeError):
                h._json(400, {"error": "http_port must be an integer"}); return True
            _settings.load({"http_port": _hp})
            _db_enqueue(lambda _v=_hp: db_save_settings({"http_port": _v}))
        if "snmp_port" in body:
            try:
                _sp = max(1, min(65535, int(body["snmp_port"])))
            except (ValueError, TypeError):
                h._json(400, {"error": "snmp_port must be an integer"}); return True
            _settings.load({"snmp_port": _sp})
            _db_enqueue(lambda _v=_sp: db_save_settings({"snmp_port": _v}))
        if "snr_type_defaults" in body:
            _raw = body["snr_type_defaults"]
            if isinstance(_raw, dict):
                _raw = json.dumps(_raw)
            _settings.load({"snr_type_defaults": _raw})
            _db_enqueue(lambda _v=_raw: db_save_settings({"snr_type_defaults": _v}))
        for _k in (
            "smtp_host", "smtp_port", "smtp_tls", "smtp_user", "smtp_from", "smtp_to", "smtp_down_delay",
            "snr_interval", "snr_timeout", "snr_fail_after", "snr_recover_after",
            "max_flaps_display", "max_flap_entries", "max_trap_entries",
            "login_fail_max", "login_fail_window",
            "org_name", "latency_good_ms", "latency_warn_ms",
        ):
            if _k in body:
                _val = str(body[_k]).strip()
                _settings.load({_k: _val})
                _db_enqueue(lambda _k=_k, _v=_val: db_save_settings({_k: _v}))
        _pw = (body.get("smtp_pass") or "").strip()
        if _pw:
            _settings.load({"smtp_pass": _pw})
            _db_enqueue(lambda _p=_pw: db_save_settings({"smtp_pass": _p}))
        db_log_audit(user, h.client_address[0], 'settings_update', '', str(list(body.keys())))
        log.info("Settings updated by %s: %s", user, list(body.keys()))
        h._json(200, {"ok": True})
        return True

    # ── /api/settings/smtp_test POST ─────────────────────────────
    if path == "/api/settings/smtp_test" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from smtp_alert import test_smtp
        cfg = {
            "host":      (body.get("smtp_host") or "").strip(),
            "port":      body.get("smtp_port", 587),
            "tls":       (body.get("smtp_tls")  or "starttls").strip(),
            "user":      (body.get("smtp_user") or "").strip(),
            "password":  (body.get("smtp_pass") or _settings.get("smtp_pass", "")).strip(),
            "from_addr": (body.get("smtp_from") or "").strip(),
            "to_addr":   (body.get("smtp_to")   or "").strip(),
        }
        ok, msg = test_smtp(cfg)
        h._json(200 if ok else 500, {"ok": ok, "msg": msg})
        return True

    # ── /api/server_info GET ──────────────────────────────────────
    if path == "/api/server_info" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        STATE = app_state.STATE
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
        _log_bytes = sum(
            os.path.getsize(os.path.join(_log_dir, f))
            for f in os.listdir(_log_dir)
            if os.path.isfile(os.path.join(_log_dir, f))
        ) if os.path.isdir(_log_dir) else 0
        h._json(200, {
            "version":        app_state.APP_VERSION,
            "uptime_s":       int(time.time() - app_state.SERVER_START),
            "devices":        len(STATE.devices),
            "sensors":        sum(len(d.sensors) for d in STATE.devices.values()),
            "db_size_bytes":  os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
            "log_size_bytes": _log_bytes,
        })
        return True

    return False
