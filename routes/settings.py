"""
routes/settings.py — Application settings and server info endpoints.

Handles: /api/settings (GET/PATCH), /api/server_info (GET),
         /api/settings/smtp_test (POST).
"""

import json
import os
import socket
import sys
import threading
import time

import core.app_state as app_state
from core.config import DB_PATH, LOGS_DB_PATH, BIND, PORT, _RE_DB_STATS
from db          import _db_enqueue, db_log_audit, db_save_settings, db_get_dashboard, db_save_dashboard
from core.logger import log
import core.settings as _settings

# Prime psutil CPU counter so first real call returns a meaningful value
try:
    import psutil as _psutil
    _psutil.cpu_percent(interval=None)
except Exception:
    pass


def _get_smtp_status() -> dict:
    from monitoring.smtp_alert import get_smtp_status
    return get_smtp_status()


def _get_syslog_status() -> dict:
    from monitoring.syslog_client import get_syslog_status
    return get_syslog_status()


def _get_effective_workers() -> int:
    """Return the number of probe workers currently in use."""
    try:
        return app_state.STATE._executor._max_workers
    except Exception:
        return 64


def _local_ip() -> str:
    """Return the LAN IP used to reach the outside world.
    Falls back to BIND if detection fails."""
    if BIND and BIND != '0.0.0.0':
        return BIND          # server is bound to a specific interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return BIND


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
            "http_port":      int(_settings.get("http_port", PORT)),
            "snmp_port":      app_state.effective_snmp_port,
            "bind":           _local_ip(),
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
            # Group F2 — port scanner
            "scan_ports": str(_settings.get("scan_ports",
                "ping,21,22,25,53,80,443,3389,3306,5432,6379,27017,389,8080,8443")),
            # Group G — global backup scheduler
            "backup_sched_enabled": int(_settings.get("backup_sched_enabled", 0)),
            "backup_sched_freq":    _settings.get("backup_sched_freq",  "daily"),
            "backup_sched_time":    _settings.get("backup_sched_time",  "02:00"),
            "backup_sched_days":    str(_settings.get("backup_sched_days",  "1,2,3,4,5,6,7")),
            "backup_keep":          int(_settings.get("backup_keep", 3)),
            # Group I — scheduled database backup
            "db_backup_enabled":     int(_settings.get("db_backup_enabled",     0) or 0),
            "db_backup_freq":        _settings.get("db_backup_freq",        "daily"),
            "db_backup_time":        _settings.get("db_backup_time",        "03:00"),
            "db_backup_days":        str(_settings.get("db_backup_days",    "1,2,3,4,5,6,7")),
            "db_backup_keep":        int(_settings.get("db_backup_keep",    7) or 7),
            "db_backup_last_ts":     _settings.get("db_backup_last_ts",     ""),
            "db_backup_last_result": _settings.get("db_backup_last_result", ""),
            # Group H — syslog forwarding
            "syslog_host":         _settings.get("syslog_host",         ""),
            "syslog_port":         int(_settings.get("syslog_port",         514) or 514),
            "syslog_proto":        _settings.get("syslog_proto",        "udp"),
            "syslog_min_severity": _settings.get("syslog_min_severity", "warning"),
            # Group H2 — syslog app-log forwarding
            "syslog_app_logs":        int(_settings.get("syslog_app_logs", 0) or 0),
            "syslog_app_log_level":   _settings.get("syslog_app_log_level",   "info"),
            "syslog_app_log_sources": json.loads(
                _settings.get("syslog_app_log_sources", '["app","audit","backup"]') or
                '["app","audit","backup"]'
            ),
            # Integration runtime status (in-memory, resets on restart)
            "smtp_status":   _get_smtp_status(),
            "syslog_status": _get_syslog_status(),
            # Group J — data rollup / retention tiers (v0.8.0)
            "retention_raw_days":    int(_settings.get("retention_raw_days", 7) or 7),
            "retention_5m_days":     int(_settings.get("retention_5m_days", 90) or 90),
            "retention_1h_days":     int(_settings.get("retention_1h_days", 1095) or 1095),
            "max_workers_executor":  int(_settings.get("max_workers_executor", 0) or 0),
            "max_workers_executor_effective": _get_effective_workers(),
            # Group K — debug mode
            "debug_mode": int(_settings.get("debug_mode", 0) or 0),
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
        if "scan_ports" in body:
            _sp_raw = str(body["scan_ports"]).strip()
            try:
                for _tok in [t.strip() for t in _sp_raw.split(",") if t.strip()]:
                    if _tok.lower() != "ping":
                        _p = int(_tok)
                        if not (1 <= _p <= 65535):
                            raise ValueError(f"Port out of range: {_p}")
            except (ValueError, TypeError) as _e:
                h._json(400, {"error": f"Invalid scan_ports: {_e}"}); return True
            _settings.load({"scan_ports": _sp_raw})
            _db_enqueue(lambda _v=_sp_raw: db_save_settings({"scan_ports": _v}))
        # Backup scheduler settings
        if "syslog_app_logs" in body:
            _sal = "1" if body["syslog_app_logs"] else "0"
            _settings.load({"syslog_app_logs": _sal})
            _db_enqueue(lambda _v=_sal: db_save_settings({"syslog_app_logs": _v}))
        if "syslog_app_log_level" in body:
            _sall = str(body["syslog_app_log_level"]).lower().strip()
            if _sall not in ("debug", "info", "warning", "error"):
                h._json(400, {"error": "syslog_app_log_level must be debug/info/warning/error"}); return True
            _settings.load({"syslog_app_log_level": _sall})
            _db_enqueue(lambda _v=_sall: db_save_settings({"syslog_app_log_level": _v}))
        if "syslog_app_log_sources" in body:
            _sals_raw = body["syslog_app_log_sources"]
            if isinstance(_sals_raw, list):
                _sals = json.dumps([s for s in _sals_raw if s in ("app", "audit", "backup")])
            else:
                _sals = '["app","audit","backup"]'
            _settings.load({"syslog_app_log_sources": _sals})
            _db_enqueue(lambda _v=_sals: db_save_settings({"syslog_app_log_sources": _v}))
        if "backup_sched_enabled" in body:
            _bse = "1" if body["backup_sched_enabled"] else "0"
            _settings.load({"backup_sched_enabled": _bse})
            _db_enqueue(lambda _v=_bse: db_save_settings({"backup_sched_enabled": _v}))
        if "backup_keep" in body:
            try:
                _bk = str(max(1, min(50, int(body["backup_keep"]))))
            except (ValueError, TypeError):
                h._json(400, {"error": "backup_keep must be an integer"}); return True
            _settings.load({"backup_keep": _bk})
            _db_enqueue(lambda _v=_bk: db_save_settings({"backup_keep": _v}))
        for _k in ("backup_sched_freq", "backup_sched_time", "backup_sched_days"):
            if _k in body:
                _val = str(body[_k]).strip()
                _settings.load({_k: _val})
                _db_enqueue(lambda _k=_k, _v=_val: db_save_settings({_k: _v}))
        # Scheduled database backup settings
        if "db_backup_enabled" in body:
            _v = "1" if body["db_backup_enabled"] else "0"
            _settings.load({"db_backup_enabled": _v})
            _db_enqueue(lambda v=_v: db_save_settings({"db_backup_enabled": v}))
        if "db_backup_keep" in body:
            try:
                _v = str(max(1, min(50, int(body["db_backup_keep"]))))
            except (ValueError, TypeError):
                h._json(400, {"error": "db_backup_keep must be an integer"}); return True
            _settings.load({"db_backup_keep": _v})
            _db_enqueue(lambda v=_v: db_save_settings({"db_backup_keep": v}))
        for _k in ("db_backup_freq", "db_backup_time", "db_backup_days"):
            if _k in body:
                _val = str(body[_k]).strip()
                _settings.load({_k: _val})
                _db_enqueue(lambda _k=_k, _v=_val: db_save_settings({_k: _v}))
        for _k in (
            "smtp_host", "smtp_port", "smtp_tls", "smtp_user", "smtp_from", "smtp_to", "smtp_down_delay",
            "snr_interval", "snr_timeout",
            "max_flaps_display", "max_flap_entries", "max_trap_entries",
            "login_fail_max", "login_fail_window",
            "org_name", "latency_good_ms", "latency_warn_ms",
            "syslog_host", "syslog_port", "syslog_proto", "syslog_min_severity",
        ):
            if _k in body:
                _val = str(body[_k]).strip()
                _settings.load({_k: _val})
                _db_enqueue(lambda _k=_k, _v=_val: db_save_settings({_k: _v}))
        _pw = (body.get("smtp_pass") or "").strip()
        if _pw:
            from db.backups import encrypt_pw as _enc_smtp_pw
            _pw_enc = _enc_smtp_pw(_pw)
            _settings.load({"smtp_pass": _pw_enc})
            _db_enqueue(lambda _p=_pw_enc: db_save_settings({"smtp_pass": _p}))
        # Data rollup retention tiers (v0.8.0)
        for _k, _min, _max in [
            ("retention_raw_days", 1, 365),
            ("retention_5m_days",  7, 1825),
            ("retention_1h_days",  30, 3650),
        ]:
            if _k in body:
                try:
                    _v = max(_min, min(_max, int(body[_k])))
                except (ValueError, TypeError):
                    h._json(400, {"error": f"{_k} must be an integer"}); return True
                _settings.load({_k: _v})
                _db_enqueue(lambda _k=_k, _v=_v: db_save_settings({_k: str(_v)}))
        if "max_workers_executor" in body:
            try:
                _mw_raw = int(body["max_workers_executor"])
                # 0 = auto; 4-512 = manual override
                _mw = 0 if _mw_raw < 4 else min(512, _mw_raw)
            except (ValueError, TypeError):
                h._json(400, {"error": "max_workers_executor must be 0 (auto) or 4-512"}); return True
            _settings.load({"max_workers_executor": _mw})
            _db_enqueue(lambda _v=_mw: db_save_settings({"max_workers_executor": str(_v)}))
        if "debug_mode" in body:
            _dm = "1" if body["debug_mode"] else "0"
            _settings.load({"debug_mode": _dm})
            _db_enqueue(lambda _v=_dm: db_save_settings({"debug_mode": _v}))
            from core.logger import set_debug_mode
            set_debug_mode(_dm == "1")
        db_log_audit(user, h.client_address[0], 'settings_update', '', str(list(body.keys())))
        h._json(200, {"ok": True})
        return True

    # ── /api/settings/syslog_test POST ───────────────────────────
    if path == "/api/settings/syslog_test" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from monitoring.syslog_client import send_test_syslog
        ok, msg = send_test_syslog()
        h._json(200 if ok else 500, {"ok": ok, "msg": msg})
        return True

    # ── /api/settings/smtp_test POST ─────────────────────────────
    if path == "/api/settings/smtp_test" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from monitoring.smtp_alert import test_smtp
        from db.backups import decrypt_pw as _dec_smtp_pw
        cfg = {
            "host":      (body.get("smtp_host") or "").strip(),
            "port":      body.get("smtp_port", 587),
            "tls":       (body.get("smtp_tls")  or "starttls").strip(),
            "user":      (body.get("smtp_user") or "").strip(),
            "password":  (body.get("smtp_pass") or _dec_smtp_pw(_settings.get("smtp_pass", ""))).strip(),
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
        from db.backend import is_pg
        if is_pg():
            from db.pg_pool import pg_cursor
            _sz_q = (
                "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint AS sz "
                "FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind = 'r'"
            )
            try:
                with pg_cursor("main") as _cur:
                    _cur.execute(_sz_q, ("main",))
                    _main_sz = _cur.fetchone()["sz"]
                    _cur.execute(_sz_q, ("logs",))
                    _logs_sz = _cur.fetchone()["sz"]
            except Exception:
                _main_sz = _logs_sz = 0
        else:
            _main_sz = os.path.getsize(DB_PATH)      if os.path.exists(DB_PATH)      else 0
            _logs_sz = os.path.getsize(LOGS_DB_PATH) if os.path.exists(LOGS_DB_PATH) else 0
        h._json(200, {
            "version":        app_state.APP_VERSION,
            "version_name":   app_state.APP_VERSION_NAME,
            "uptime_s":       int(time.time() - app_state.SERVER_START),
            "devices":        len(STATE.devices),
            "sensors":        sum(len(d.sensors) for d in STATE.devices.values()),
            "db_size_bytes":      _main_sz,
            "logs_db_size_bytes": _logs_sz,
            "log_size_bytes":     _log_bytes,
        })
        return True

    # ── /api/system/perf GET ─────────────────────────────────────
    if path == "/api/system/perf" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        try:
            import psutil
            cpu  = psutil.cpu_percent(interval=None)
            ram  = psutil.virtual_memory()
            _dp  = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            disk = psutil.disk_usage(_dp)
            h._json(200, {
                "cpu_pct":    round(cpu, 1),
                "ram_pct":    round(ram.percent, 1),
                "ram_used":   ram.used,
                "ram_total":  ram.total,
                "disk_pct":   round(disk.percent, 1),
                "disk_used":  disk.used,
                "disk_total": disk.total,
            })
        except ImportError:
            h._json(503, {"error": "psutil not installed — run: pip install psutil"})
        except Exception as e:
            log.error(f"System stats error: {e}")
            h._json(500, {"error": "Failed to collect system stats — check server logs"})
        return True

    # ── /api/dashboard GET ────────────────────────────────────────
    if path == "/api/dashboard" and method == "GET":
        user, _ = h._require("viewer")
        if not user: return True
        raw = db_get_dashboard(user)
        try:
            widgets = json.loads(raw)
        except Exception:
            widgets = []
        h._json(200, {"widgets": widgets})
        return True

    # ── /api/dashboard PUT ────────────────────────────────────────
    if path == "/api/dashboard" and method == "PUT":
        user, _ = h._require("viewer")
        if not user: return True
        widgets = body.get("widgets")
        if not isinstance(widgets, list):
            h._json(400, {"error": "widgets must be an array"}); return True
        widgets_json = json.dumps(widgets)
        _db_enqueue(lambda _u=user, _j=widgets_json: db_save_dashboard(_u, _j))
        h._json(200, {"ok": True})
        return True

    # ── /api/server/restart POST ──────────────────────────────────
    if path == "/api/server/restart" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        log.info(f"Server restart requested by '{user}'")
        h._json(200, {"ok": True, "msg": "Server is restarting…"})
        try: h.wfile.flush()
        except Exception: pass
        def _do_restart():
            time.sleep(1.5)
            from db import db_flush_samples
            try: db_flush_samples()
            except Exception: pass
            if app_state.tray_icon is not None:
                try: app_state.tray_icon.stop()
                except Exception: pass
                time.sleep(0.2)
            _cmd = [sys.executable] + sys.argv
            if os.name == "nt":
                import subprocess as _sp
                _sp.Popen(_cmd, creationflags=_sp.CREATE_NEW_CONSOLE)
                os._exit(0)
            else:
                os.execv(sys.executable, _cmd)
        threading.Thread(target=_do_restart, daemon=True).start()
        return True

    # ── /api/server/shutdown POST ─────────────────────────────────
    if path == "/api/server/shutdown" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        log.info(f"Server shutdown requested by '{user}'")
        h._json(200, {"ok": True, "msg": "Server is shutting down…"})
        try: h.wfile.flush()
        except Exception: pass
        def _do_shutdown():
            time.sleep(1.0)
            from db import db_flush_samples
            try: db_flush_samples()
            except Exception: pass
            if app_state.tray_icon is not None:
                try: app_state.tray_icon.stop()
                except Exception: pass
            os._exit(0)
        threading.Thread(target=_do_shutdown, daemon=True).start()
        return True

    # ── /api/db/backup/run POST ───────────────────────────────────────
    if path == "/api/db/backup/run" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from backup.db_backup import do_db_backup
        ok, msg = do_db_backup()
        h._json(200 if ok else 500, {"ok": ok, "msg": msg})
        return True

    # ── GET /api/db/stats ─────────────────────────────────────────
    if _RE_DB_STATS.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        from db.backend import is_pg
        if is_pg():
            from db.pg_pool import pg_cursor
            try:
                _sz_q = (
                    "SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)::bigint AS sz "
                    "FROM pg_catalog.pg_class c "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relkind = 'r'"
                )
                with pg_cursor("main") as cur:
                    cur.execute(_sz_q, ("main",))
                    main_sz = cur.fetchone()["sz"]
                    cur.execute(_sz_q, ("logs",))
                    logs_sz = cur.fetchone()["sz"]
                with pg_cursor("logs") as cur:
                    def _pg_cnt(table):
                        try:
                            cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
                            return cur.fetchone()["cnt"]
                        except Exception:
                            return 0
                    h._json(200, {
                        "main": {"path": "PostgreSQL", "size": main_sz},
                        "logs": {
                            "path": "PostgreSQL (logs schema)",
                            "size": logs_sz,
                            "samples":    _pg_cnt("sensor_samples"),
                            "samples_5m": _pg_cnt("sensor_samples_5m"),
                            "samples_1h": _pg_cnt("sensor_samples_1h"),
                            "flaps":      _pg_cnt("flap_log"),
                            "traps":      _pg_cnt("snmp_traps"),
                            "errors":     _pg_cnt("sensor_err_log"),
                        },
                    })
            except Exception as e:
                log.error(f"db/stats PG error: {e}")
                h._json(500, {"error": "Failed to collect database stats — check server logs"})
            return True
        import sqlite3 as _sq3
        def _db_size(p):
            try:
                return os.path.getsize(p) if os.path.exists(p) else 0
            except Exception:
                return 0
        def _row_count(p, table):
            try:
                c = _sq3.connect(p)
                n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                c.close()
                return n
            except Exception:
                return 0
        h._json(200, {
            "main": {
                "path":     str(DB_PATH),
                "size":     _db_size(DB_PATH),
            },
            "logs": {
                "path":         str(LOGS_DB_PATH),
                "size":         _db_size(LOGS_DB_PATH),
                "samples":      _row_count(LOGS_DB_PATH, "sensor_samples"),
                "samples_5m":   _row_count(LOGS_DB_PATH, "sensor_samples_5m"),
                "samples_1h":   _row_count(LOGS_DB_PATH, "sensor_samples_1h"),
                "flaps":        _row_count(LOGS_DB_PATH, "flap_log"),
                "traps":        _row_count(LOGS_DB_PATH, "snmp_traps"),
                "errors":       _row_count(LOGS_DB_PATH, "sensor_err_log"),
            },
        })
        return True

    # ── GET /api/settings/db ─────────────────────────────────────
    if path == "/api/settings/db" and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        from db.backend import is_pg, get_config
        cfg = get_config()
        result = {
            "backend": "postgresql" if is_pg() else "sqlite",
        }
        if is_pg():
            result["pg_host"]     = cfg.get("pg_host", "")
            result["pg_port"]     = cfg.get("pg_port", 5432)
            result["pg_database"] = cfg.get("pg_database", "")
            result["pg_user"]     = cfg.get("pg_user", "")
        else:
            result["db_path"]      = str(DB_PATH)
            result["logs_db_path"] = str(LOGS_DB_PATH)
            result["db_size"]      = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
            result["logs_db_size"] = os.path.getsize(LOGS_DB_PATH) if os.path.exists(LOGS_DB_PATH) else 0
        h._json(200, result)
        return True

    # ── POST /api/settings/db/test ───────────────────────────────
    if path == "/api/settings/db/test" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from db.pg_pool import pg_test_connection
        try:
            _pg_port = int(body.get("port", 5432))
        except (TypeError, ValueError):
            h._json(400, {"ok": False, "error": "port must be an integer"}); return True
        ok, err = pg_test_connection(
            str(body.get("host", "localhost")).strip(),
            _pg_port,
            str(body.get("database", "pingwatch")).strip(),
            str(body.get("user", "pingwatch")).strip(),
            str(body.get("password", "")),
        )
        h._json(200, {"ok": ok, "error": err})
        return True

    # ── POST /api/settings/db/migrate ────────────────────────────
    if path == "/api/settings/db/migrate" and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        from db.pg_pool import pg_test_connection
        host   = str(body.get("host", "localhost")).strip()
        try:
            port = int(body.get("port", 5432))
        except (TypeError, ValueError):
            h._json(400, {"ok": False, "error": "port must be an integer"}); return True
        dbname = str(body.get("database", "pingwatch")).strip()
        pg_user = str(body.get("user", "pingwatch")).strip()
        pw     = str(body.get("password", ""))

        # Test connection
        ok, err = pg_test_connection(host, port, dbname, pg_user, pw)
        if not ok:
            h._json(400, {"ok": False, "error": f"Connection failed: {err}"})
            return True

        # Run migration
        try:
            from db.pg_migrate import migrate_sqlite_to_pg
            pg_cfg = {
                "pg_host": host, "pg_port": port, "pg_database": dbname,
                "pg_user": pg_user, "pg_password": pw,
            }
            success, msg = migrate_sqlite_to_pg(str(DB_PATH), str(LOGS_DB_PATH), pg_cfg)
            if success:
                # Update config to switch backend
                from db.backend import save_config, load_config
                save_config({
                    "db_backend": "postgresql",
                    "pg_host": host, "pg_port": port, "pg_database": dbname,
                    "pg_user": pg_user, "pg_password": pw,
                })
                load_config()
                db_log_audit(user, h.client_address[0], 'db_migrate', '', 'sqlite_to_pg')
                h._json(200, {"ok": True, "msg": msg, "restart_required": True})
            else:
                h._json(500, {"ok": False, "error": msg})
        except Exception as e:
            log.error(f"Migration failed: {e}")
            h._json(500, {"ok": False, "error": "Migration failed — check server logs"})
        return True

    return False
