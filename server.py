"""
server.py — HTTP request handler and application entry point.

Run:  python server.py
      Browser opens at http://localhost:7070

Linux/Mac may need: sudo python3 server.py  (for ICMP ping)
"""

import base64
import http.server
import json
import os
import queue
import re
import signal
import sys
import tempfile
import traceback
import threading
import time
import webbrowser
from urllib.parse import urlparse, parse_qs  

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY = True
except ImportError:
    _TRAY = False

_tray_icon = None   # set in main(); used by restart to stop icon before exec

import settings as _settings
from auth import auth_check, auth_check_role, auth_login, auth_logout, auth_revoke_user_sessions, auth_verify_current
from network_map import (
    init_topo_db, migrate_topo_from_file,
    topo_get_pages, topo_insert_page, topo_update_page, topo_delete_page,
    topo_get_nodes, topo_insert_node, topo_update_node, topo_delete_node,
    topo_get_links, topo_insert_link, topo_update_link, topo_delete_link,
    topo_get_groups, topo_insert_group, topo_update_group, topo_delete_group,
    topo_get_setting, topo_upsert_setting,
)
from logger import log
from config import (
    BIND, DB_PATH, FRONTEND_DIR, PORT, SYS,
    _RE_DEVICE, _RE_DEVICE_ACTION, _RE_DEVICE_LOGS,
    _RE_SENSOR, _RE_SENSOR_ACTION, _RE_SENSOR_ITEM,
    _RE_USER, _RE_USER_PW, _RE_ME_PW,
    _RE_SENSOR_HISTORY, _RE_SENSOR_SUMMARY, _RE_DEVICE_SCAN,
    _RE_SENSOR_LOGS, _RE_DB_EXPORT, _RE_DB_IMPORT, _RE_AUDIT,
)
from probes import probe_ping, probe_tcp, probe_http, probe_tls, probe_banner

# ── RBAC role ranking ─────────────────────────────────────────────
_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}

# ── Login rate limiting ────────────────────────────────────────────
_FAIL_LOCK   = threading.Lock()
_FAIL_LOG: dict = {}   # ip -> [timestamp, ...]
_FAIL_WINDOW = 60      # seconds
_FAIL_MAX    = 5       # attempts before lockout
from db import (
    _db_enqueue, autosave_loop,
    db_init, db_load, db_load_err_logs, db_load_flaps, db_load_traps,
    db_load_settings, db_save_settings,
    db_list_users, db_add_user, db_delete_user, db_set_password,
    db_save, db_seed_users,
    db_load_history, db_load_summary,
    db_clear_err_logs, db_clear_sensor_err_logs, db_clear_device_traps,
    db_log_audit, db_get_audit,
)
from state import MonitorState

# ── Version ────────────────────────────────────────────────────────
APP_VERSION   = "0.5"
_SERVER_START = time.time()

# ── Application state ─────────────────────────────────────────────
STATE = MonitorState()

# ── Effective ports (may be overridden by settings at startup) ─────
_effective_port      = PORT
_effective_snmp_port = 162


# ── Helper: serve the frontend HTML from disk ─────────────────────
_JS_FILES = ["bg.js", "devices.js", "sensors.js", "forms-utils.js", "forms-device.js", "forms-sensor.js", "forms-settings.js", "forms-io.js", "forms-users.js", "dashboard.js", "events.js", "app.js"]

_MAP_HTML_PATH = os.path.join(FRONTEND_DIR, 'map.html')

def _load_map_html() -> bytes:
    with open(_MAP_HTML_PATH, 'rb') as f:
        return f.read()

# ── Topology route regexes ────────────────────────────────────────
_RE_TOPO_PAGE    = re.compile(r'^/api/pages/(\d+)$')
_RE_TOPO_NODE    = re.compile(r'^/api/nodes/(\d+)$')
_RE_TOPO_LINK    = re.compile(r'^/api/links/(\d+)$')
_RE_TOPO_GROUP   = re.compile(r'^/api/groups/(\d+)$')
_RE_TOPO_SETTING = re.compile(r'^/api/settings/([^/]+)$')

def _load_html() -> bytes:
    base  = os.path.join(FRONTEND_DIR, "index.html")
    css_f = os.path.join(FRONTEND_DIR, "style.css")
    with open(base, "r", encoding="utf-8") as f:
        html = f.read()
    with open(css_f, "r", encoding="utf-8") as f:
        html = html.replace("<!-- STYLE_INJECT -->", f"<style>{f.read()}</style>", 1)
    js_parts = []
    for name in _JS_FILES:
        with open(os.path.join(FRONTEND_DIR, name), "r", encoding="utf-8") as f:
            js_parts.append(f.read())
    html = html.replace("<!-- SCRIPT_INJECT -->", f"<script>{''.join(js_parts)}</script>", 1)
    return html.encode("utf-8")


# ── Custom server: silences browser-disconnect noise on Windows ───

class QuietServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer that suppresses noisy browser-disconnect errors."""

    _IGNORED = ('ConnectionAbortedError', 'ConnectionResetError', 'BrokenPipeError')

    def handle_error(self, request, client_address):
        if any(e in traceback.format_exc() for e in self._IGNORED):
            return
        super().handle_error(request, client_address)


# ── Device scan targets ───────────────────────────────────────────
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

# ── HTTP Handler ─────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    # ── Auth helpers ──────────────────────────────────────────────
    def _get_token(self):
        for part in self.headers.get("Cookie", "").split(";"):
            p = part.strip()
            if p.startswith("session="):
                return p[8:]
        return None

    def _auth(self):
        """Return username if authenticated, else send 401 and return None."""
        user = auth_check(self._get_token())
        if not user:
            self._json(401, {"error": "unauthorized"})
        return user

    def _auth_role(self):
        """Return (username, role) or (None, None) after sending 401."""
        user = auth_check(self._get_token())
        if not user:
            self._json(401, {"error": "unauthorized"}); return None, None
        role = auth_check_role(self._get_token()) or "viewer"
        return user, role

    def _require(self, min_role="viewer"):
        """Return (username, role) if user meets min_role, else send error and return (None, None)."""
        user, role = self._auth_role()
        if not user: return None, None
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(min_role, 0):
            self._json(403, {"error": f"Requires {min_role} role"}); return None, None
        return user, role

    def _send_with_cookie(self, code, data, cookie):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    _HOST_RE = re.compile(r'^[a-zA-Z0-9._\-]+(:\d+)?$')
    _OID_RE  = re.compile(r'^\d+(\.\d+)*$')
    _COMM_RE = re.compile(r'^[a-zA-Z0-9@#\-_]{1,64}$')

    @staticmethod
    def _valid_host(h): return bool(h and Handler._HOST_RE.match(h))
    @staticmethod
    def _valid_url(u):  return bool(u and (u.startswith("http://") or u.startswith("https://")))

    def _origin(self):
        return self.headers.get("Origin") or "*"

    def _json(self, code, data):
        body = json.dumps(data).encode()
        origin = self._origin()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", origin)
        if origin != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        origin = self._origin()
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS,PATCH")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if origin != "*":
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.end_headers()

    def do_OPTIONS(self): self._cors()

    _MAX_BODY = 1_048_576  # 1 MB

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > self._MAX_BODY:
                return {}
            return json.loads(self.rfile.read(n)) if n else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    # ── GET ───────────────────────────────────────────────────────
    def do_GET(self):
        p = urlparse(self.path).path

        if p in ("/", "/index.html"):
            body = _load_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if p == "/api/me":
            user = auth_check(self._get_token())
            if user:
                role = auth_check_role(self._get_token()) or "viewer"
                return self._json(200, {"username": user, "role": role})
            return self._json(401, {"error": "unauthorized"})

        if not self._auth(): return

        # ── Topology ──────────────────────────────────────────────
        if p == '/map':
            data = _load_map_html()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
            return

        if p == '/api/pages':
            return self._json(200, topo_get_pages())

        _qs = urlparse(self.path).query
        _page_id = None
        if _qs:
            import urllib.parse as _up
            _pv = _up.parse_qs(_qs).get('page')
            if _pv:
                try: _page_id = int(_pv[0])
                except ValueError: pass

        if p == '/api/nodes':
            return self._json(200, topo_get_nodes(_page_id))

        if p == '/api/links':
            return self._json(200, topo_get_links(_page_id))

        if p == '/api/groups':
            return self._json(200, topo_get_groups(_page_id))

        m = _RE_TOPO_SETTING.match(p)
        if m:
            row = topo_get_setting(m.group(1))
            return self._json(200, row) if row else self._json(404, {'error': 'not found'})

        if p == "/api/devices":
            user, _ = self._require("viewer");
            if not user: return
            return self._json(200, {"devices": STATE.all_devices()})
        if p == "/api/server_info":
            user, _ = self._require("viewer")
            if not user: return
            _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            _log_bytes = sum(
                os.path.getsize(os.path.join(_log_dir, f))
                for f in os.listdir(_log_dir)
                if os.path.isfile(os.path.join(_log_dir, f))
            ) if os.path.isdir(_log_dir) else 0
            return self._json(200, {
                "version":        APP_VERSION,
                "uptime_s":       int(time.time() - _SERVER_START),
                "devices":        len(STATE.devices),
                "sensors":        sum(len(d.sensors) for d in STATE.devices.values()),
                "db_size_bytes":  os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
                "log_size_bytes": _log_bytes,
            })

        if p == "/api/flaps":
            user, _ = self._require("viewer")
            if not user: return
            return self._json(200, {"flaps": db_load_flaps()})
        if p == "/api/traps":
            user, _ = self._require("viewer")
            if not user: return
            return self._json(200, {"traps": db_load_traps()})

        if _RE_AUDIT.match(p):
            user, _ = self._require("admin")
            if not user: return
            return self._json(200, {"entries": db_get_audit(200)})
        if p == "/api/snmp/catalog":
            from snmp_catalog import SNMP_CATALOG
            return self._json(200, {"catalog": SNMP_CATALOG})

        if p == "/api/settings":
            user, _ = self._require("viewer")
            if not user: return
            return self._json(200, {
                "session_ttl":    _settings.get("session_ttl", 86400),
                "retention_days": _settings.get("retention_days", 7),
                "port":           _effective_port,
                "http_port":      _effective_port,
                "snmp_port":      _effective_snmp_port,
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

        if p == "/api/users":
            user, _ = self._require("admin")
            if not user: return
            return self._json(200, {"users": db_list_users()})

        m = _RE_DEVICE_LOGS.match(p)
        if m:
            user, _ = self._require("viewer")
            if not user: return
            return self._json(200, {"logs": db_load_err_logs(m.group(1))})

        m = _RE_DEVICE.match(p)
        if m:
            user, _ = self._require("viewer")
            if not user: return
            dev = STATE.get_device(m.group(1))
            if dev: return self._json(200, dev.to_dict())
            return self._json(404, {"error": "not found"})

        m = _RE_SENSOR_HISTORY.match(p)
        if m:
            user, _ = self._require("viewer")
            if not user: return
            did, sid = m.group(1), m.group(2)
            _qs = parse_qs(urlparse(self.path).query)
            try:
                minutes = max(1, int(_qs.get("minutes", ["1440"])[0]))
                limit   = max(1, min(10000, int(_qs.get("limit", ["1000"])[0])))
            except (ValueError, TypeError):
                return self._json(400, {"error": "invalid query parameter"})
            return self._json(200, {"samples": db_load_history(did, sid, minutes, limit)})

        m = _RE_SENSOR_SUMMARY.match(p)
        if m:
            user, _ = self._require("viewer")
            if not user: return
            did, sid = m.group(1), m.group(2)
            _qs = parse_qs(urlparse(self.path).query)
            try:
                minutes = max(1, int(_qs.get("minutes", ["1440"])[0]))
            except (ValueError, TypeError):
                return self._json(400, {"error": "invalid query parameter"})
            return self._json(200, {"summary": db_load_summary(did, sid, minutes)})

        if _RE_DB_EXPORT.match(p):
            user, _ = self._require("admin")
            if not user: return
            db_log_audit(user, self.client_address[0], 'db_export')
            import sqlite3 as _sq3
            fd, tmp = tempfile.mkstemp(suffix=".db")
            os.close(fd)
            try:
                src = _sq3.connect(DB_PATH)
                dst = _sq3.connect(tmp)
                src.backup(dst)
                src.close(); dst.close()
                with open(tmp, "rb") as fh:
                    data = fh.read()
            finally:
                try: os.unlink(tmp)
                except OSError: pass
            fname = "pingwatch-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".db"
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if p == "/events":
            if not self._auth(): return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            q = STATE.subscribe()
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except Exception:
                pass
            finally:
                STATE.unsubscribe(q)
            return

        self._json(404, {"error": "not found"})

    # ── POST ──────────────────────────────────────────────────────
    def do_POST(self):
        p = urlparse(self.path).path

        if _RE_DB_IMPORT.match(p):
            user, _ = self._require("admin")
            if not user: return
            db_log_audit(user, self.client_address[0], 'db_import')
            log.info(f"DB import: request received from {self.client_address[0]} by '{user}'")
            _MAX_IMPORT = 512 * 1024 * 1024  # 512 MB
            n = int(self.headers.get("Content-Length", 0))
            if n > _MAX_IMPORT:
                log.warning(f"DB import: rejected — payload too large ({n} bytes)")
                return self._json(413, {"error": "File too large (max 512 MB)"})
            try:
                body_imp = json.loads(self.rfile.read(n)) if n else {}
            except (ValueError, json.JSONDecodeError):
                log.warning("DB import: rejected — invalid JSON payload")
                return self._json(400, {"error": "Invalid JSON"})
            raw_b64 = (body_imp.get("data") or "").strip()
            if not raw_b64:
                log.warning("DB import: rejected — no data field in payload")
                return self._json(400, {"error": "No data provided"})
            try:
                db_bytes = base64.b64decode(raw_b64)
            except Exception:
                log.warning("DB import: rejected — base64 decode failed")
                return self._json(400, {"error": "Invalid base64 data"})
            if not db_bytes[:16].startswith(b"SQLite format 3\x00"):
                log.warning(f"DB import: rejected — not a SQLite file (header: {db_bytes[:16]!r})")
                return self._json(400, {"error": "Not a valid SQLite database file"})
            log.info(f"DB import: decoded {len(db_bytes):,} bytes — writing to temp file")
            fd, tmp = tempfile.mkstemp(suffix=".db")
            try:
                os.write(fd, db_bytes)
                os.close(fd)
            except Exception as e:
                log.error(f"DB import: failed to write temp file — {e}")
                try: os.unlink(tmp)
                except OSError: pass
                return self._json(500, {"error": str(e)})
            try:
                import sqlite3 as _sq3_v
                _vc = _sq3_v.connect(tmp)
                _ic = _vc.execute("PRAGMA integrity_check").fetchone()
                if _ic[0] != "ok":
                    # Index-only inconsistencies can be repaired with REINDEX
                    _msg = _ic[0].lower()
                    if "index" in _msg and "entries" in _msg:
                        log.warning(f"DB import: index inconsistency detected ({_ic[0]}) — attempting REINDEX")
                        _vc.execute("REINDEX")
                        _ic2 = _vc.execute("PRAGMA integrity_check").fetchone()
                        if _ic2[0] == "ok":
                            log.info("DB import: REINDEX repaired the database — proceeding")
                        else:
                            _vc.close()
                            try: os.unlink(tmp)
                            except OSError: pass
                            log.warning(f"DB import: integrity check failed after REINDEX — {_ic2[0]}")
                            return self._json(400, {"error": f"Database integrity check failed: {_ic2[0]}"})
                    else:
                        _vc.close()
                        try: os.unlink(tmp)
                        except OSError: pass
                        log.warning(f"DB import: integrity check failed — {_ic[0]}")
                        return self._json(400, {"error": f"Database integrity check failed: {_ic[0]}"})
                _vc.close()
            except Exception as _ve:
                try: os.unlink(tmp)
                except OSError: pass
                log.warning(f"DB import: validation error — {_ve}")
                return self._json(400, {"error": f"Database validation failed: {_ve}"})
            log.info(f"DB import: temp file written to {tmp} — sending response, will swap DB in 2 s")
            self._json(200, {"ok": True, "msg": "Database imported — server is restarting…"})
            try: self.wfile.flush()
            except Exception: pass
            def _restart():
                time.sleep(2.0)
                # Swap DB right before exec so autosave can't overwrite imported data
                log.info(f"DB import: swapping {tmp} → {DB_PATH}")
                try:
                    os.replace(tmp, str(DB_PATH))
                    log.info("DB import: DB file replaced successfully")
                    for _ext in ('-wal', '-shm'):
                        _jpath = str(DB_PATH) + _ext
                        try:
                            os.unlink(_jpath)
                            log.info(f"DB import: removed stale journal file {_jpath}")
                        except OSError:
                            pass
                except Exception as _e:
                    log.error(f"DB import: failed to replace DB file — {_e}")
                log.info(f"DB import: restarting process — {sys.executable} {sys.argv}")
                try:
                    from db import db_flush_samples
                    db_flush_samples()
                except Exception as _fe:
                    log.warning(f"DB import: sample flush before restart failed — {_fe}")
                if _tray_icon is not None:
                    try: _tray_icon.stop()
                    except Exception: pass
                    time.sleep(0.3)   # let pystray remove the icon before exec
                os.execv(sys.executable, [sys.executable] + sys.argv)
            threading.Thread(target=_restart, daemon=True).start()
            return

        body = self._body()

        # Public auth endpoints
        if p == "/api/login":
            ip = self.client_address[0]
            with _FAIL_LOCK:
                now = time.time()
                _fail_window = int(_settings.get("login_fail_window", _FAIL_WINDOW))
                _fail_max    = int(_settings.get("login_fail_max",    _FAIL_MAX))
                _FAIL_LOG[ip] = [t for t in _FAIL_LOG.get(ip, []) if now - t < _fail_window]
                if len(_FAIL_LOG[ip]) >= _fail_max:
                    return self._json(429, {"error": f"Too many failed attempts. Try again in {_fail_window} s."})
            username = body.get("username", "").strip()
            password = body.get("password", "")
            token    = auth_login(username, password)
            if not token:
                with _FAIL_LOCK:
                    _FAIL_LOG.setdefault(ip, []).append(time.time())
                db_log_audit(username, ip, 'login_fail', username)
                log.warning(f"Login FAILED: {username!r} from {ip}")
                return self._json(401, {"error": "Invalid username or password"})
            with _FAIL_LOCK:
                _FAIL_LOG.pop(ip, None)
            db_log_audit(username, ip, 'login_ok', username)
            log.info(f"Login OK: {username!r} from {ip}")
            return self._send_with_cookie(
                200, {"ok": True, "username": username},
                f"session={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age={_settings.get('session_ttl', 86400)}"
            )

        if p == "/api/logout":
            token = self._get_token()
            _me_logout = auth_check(token) or "anonymous"
            if token: auth_logout(token)
            db_log_audit(_me_logout, self.client_address[0], 'logout', _me_logout)
            return self._send_with_cookie(200, {"ok": True},
                                          "session=; HttpOnly; Path=/; Max-Age=0")

        if p == "/api/device":
            user, role = self._require("operator")
            if not user: return
            name        = body.get("name", "").strip()
            host        = body.get("host", "").lower().strip()
            group       = body.get("group", "Default Group")
            webhook_url = body.get("webhook_url", "").strip()
            if not name or not host:
                return self._json(400, {"error": "name and host required"})
            if not self._valid_host(host):
                return self._json(400, {"error": "invalid host — use a hostname or IP address"})
            did = STATE.add_device(name, host, group)
            with STATE._lock:
                if did in STATE.devices:
                    STATE.devices[did].webhook_url = webhook_url
            _db_enqueue(lambda: db_save(STATE))
            db_log_audit(user, self.client_address[0], 'device_create', name)
            log.info(f"Device added: {name!r} ({host})")
            return self._json(200, {"did": did})

        m = _RE_DEVICE_ACTION.match(p)
        if m:
            user, role = self._require("operator")
            if not user: return
            did, action = m.group(1), m.group(2)
            if action == "start": STATE.start_device(did)
            else:                 STATE.stop_device(did)
            return self._json(200, {"status": action})

        m = _RE_SENSOR.match(p)
        if m:
            user, role = self._require("operator")
            if not user: return
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
                    return self._json(400, {"error": f"Invalid banner_regex: {_re_err}"})
            if stype == "http" and url and not self._valid_url(url):
                return self._json(400, {"error": "url must start with http:// or https://"})
            if stype in ("ping", "tcp", "snmp", "dns") and host and not self._valid_host(host):
                return self._json(400, {"error": "invalid host"})
            sid   = STATE.add_sensor(did, name, stype, host, port, url,
                                     iv, to, vssl, comm, oid, sver,
                                     fail_after=fa, recover_after=ra,
                                     warn_ms=wms, crit_ms=cms,
                                     loss_warn_pct=lwp, loss_crit_pct=lcp,
                                     keyword=kw, keyword_case=kwc, banner_regex=bnr)
            if not sid: return self._json(404, {"error": "device not found"})
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
            db_log_audit(user, self.client_address[0], 'sensor_create', f"{_dev_name}/{name}")
            log.info(f"Sensor added: {name!r} on {_dev_name}")
            return self._json(200, {"sid": sid})

        m = _RE_SENSOR_ACTION.match(p)
        if m:
            user, role = self._require("operator")
            if not user: return
            did, sid, action = m.group(1), m.group(2), m.group(3)
            if action == "start": STATE.start_sensor(did, sid)
            else:                 STATE.stop_sensor(did, sid)
            return self._json(200, {"status": action})

        m = _RE_DEVICE_SCAN.match(p)
        if m:
            user, role = self._require("operator")
            if not user: return
            did = m.group(1)
            dev = STATE.get_device(did)
            if not dev:
                return self._json(404, {"error": "not found"})
            host = dev.host
            results = []
            _lock = threading.Lock()

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

            threads = [threading.Thread(target=_scan_one, args=(t,), daemon=True)
                       for t in _SCAN_TARGETS]
            for th in threads: th.start()
            for th in threads: th.join(timeout=7)
            return self._json(200, {"did": did, "host": host, "services": results})

        if p == "/api/snmp/interfaces":
            user, _ = self._require("operator")
            if not user: return
            from probes import snmpwalk_interfaces
            _host = (body.get("host")      or "").strip()
            _comm = (body.get("community") or "public").strip()
            _port = int(body.get("port")   or 161)
            _ver  = (body.get("version")   or "2c").strip()
            if not _host:
                return self._json(400, {"error": "host required"})
            _ifaces = snmpwalk_interfaces(_host, _comm, _port, timeout=10, version=_ver)
            if _ifaces is None:
                return self._json(503, {"error": "snmpwalk not found — install net-snmp"})
            return self._json(200, {"interfaces": _ifaces})

        if p == "/api/settings/smtp_test":
            user, _ = self._require("admin")
            if not user: return
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
            return self._json(200 if ok else 500, {"ok": ok, "msg": msg})

        if p == "/api/start":
            user, role = self._require("operator")
            if not user: return
            STATE.start_all()
            db_log_audit(user, self.client_address[0], 'sensors_start')
            return self._json(200, {"status": "started"})
        if p == "/api/stop":
            user, role = self._require("operator")
            if not user: return
            STATE.stop_all()
            db_log_audit(user, self.client_address[0], 'sensors_stop')
            return self._json(200, {"status": "stopped"})

        if p == "/api/users":
            user, role = self._require("admin")
            if not user: return
            username = body.get("username", "").strip()
            password = body.get("password", "")
            new_role = body.get("role", "admin")
            if new_role not in ("viewer", "operator", "admin"):
                new_role = "admin"
            if not username or not password:
                return self._json(400, {"error": "username and password required"})
            ok = db_add_user(username, password, new_role)
            if not ok:
                return self._json(409, {"error": "username already exists"})
            log.info(f"User created: {username} (role={new_role})")
            db_log_audit(user, self.client_address[0], 'user_create', username, f"role={new_role}")
            return self._json(200, {"ok": True})

        # ── Topology ──────────────────────────────────────────────
        if p in ('/api/pages', '/api/nodes', '/api/links', '/api/groups'):
            user, _ = self._require("operator")
            if not user: return

        if p == '/api/pages':
            if not body.get('name'):
                return self._json(400, {'error': 'name required'})
            _pg = topo_insert_page(body['name'])
            db_log_audit(user, self.client_address[0], 'ntm_page_create', body['name'])
            return self._json(201, _pg)

        if p == '/api/nodes':
            if not body.get('name') or not body.get('type'):
                return self._json(400, {'error': 'name and type required'})
            node = topo_insert_node(body['name'], body['type'], body.get('x', 200), body.get('y', 200), body.get('properties', {}), body.get('page_id', 1))
            return self._json(201, node)

        if p == '/api/links':
            link = topo_insert_link(body['source_id'], body['target_id'], body.get('label', ''), body.get('link_type', 'trunk'), body.get('page_id', 1))
            return self._json(201, link)

        if p == '/api/groups':
            if not body.get('name'):
                return self._json(400, {'error': 'name required'})
            grp = topo_insert_group(body['name'], body.get('color', '#00d4ff'), body.get('x', 100), body.get('y', 100), body.get('w', 300), body.get('h', 200), body.get('page_id', 1))
            return self._json(201, grp)

        self._json(404, {"error": "not found"})

    # ── PATCH ─────────────────────────────────────────────────────
    def do_PATCH(self):
        p    = urlparse(self.path).path
        body = self._body()

        m = _RE_DEVICE.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
            did = m.group(1)
            with STATE._lock:
                dev = STATE.devices.get(did)
                if not dev: return self._json(404, {"error": "device not found"})
                if "group"       in body: dev.group       = body["group"]
                if "name"        in body: dev.name        = body["name"]
                if "webhook_url"  in body: dev.webhook_url  = body["webhook_url"].strip()
                if "alerts_muted" in body: dev.alerts_muted = bool(body["alerts_muted"])
                if "host" in body:
                    h = body["host"].strip()
                    if not self._valid_host(h):
                        return self._json(400, {"error": "invalid host"})
                    dev.host = h
                _dev_edit_name = dev.name
            _db_enqueue(lambda: db_save(STATE))
            db_log_audit(user, self.client_address[0], 'device_edit', _dev_edit_name)
            return self._json(200, {"status": "updated"})

        m = _RE_SENSOR_ITEM.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
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
                    return self._json(400, {"error": f"Invalid banner_regex: {_re_err}"})
            ok = STATE.update_sensor(did, sid, **kwargs)
            if not ok: return self._json(404, {"error": "sensor not found"})
            _db_enqueue(lambda: db_save(STATE))
            with STATE._lock:
                _se_dev = STATE.devices.get(did)
                _se_dname = _se_dev.name if _se_dev else did
                _se_sname = _se_dev.sensors[sid].name if _se_dev and sid in _se_dev.sensors else sid
            db_log_audit(user, self.client_address[0], 'sensor_edit', f"{_se_dname}/{_se_sname}")
            return self._json(200, {"status": "updated"})

        if p == "/api/settings":
            user, _ = self._require("admin")
            if not user: return
            ttl = body.get("session_ttl")
            if ttl is not None:
                try:
                    ttl = int(ttl)
                    if ttl < 60:
                        return self._json(400, {"error": "session_ttl must be at least 60 seconds"})
                except (ValueError, TypeError):
                    return self._json(400, {"error": "session_ttl must be an integer"})
                _settings.load({"session_ttl": ttl})
                _db_enqueue(lambda: db_save_settings({"session_ttl": ttl}))
            if "retention_days" in body:
                try:
                    days = max(1, int(body["retention_days"]))
                except (ValueError, TypeError):
                    return self._json(400, {"error": "retention_days must be an integer"})
                _settings.load({"retention_days": days})
                _db_enqueue(lambda: db_save_settings({"retention_days": days}))
            if "http_port" in body:
                try:
                    _hp = max(1, min(65535, int(body["http_port"])))
                except (ValueError, TypeError):
                    return self._json(400, {"error": "http_port must be an integer"})
                _settings.load({"http_port": _hp})
                _db_enqueue(lambda _v=_hp: db_save_settings({"http_port": _v}))
            if "snmp_port" in body:
                try:
                    _sp = max(1, min(65535, int(body["snmp_port"])))
                except (ValueError, TypeError):
                    return self._json(400, {"error": "snmp_port must be an integer"})
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
            db_log_audit(user, self.client_address[0], 'settings_update', '', str(list(body.keys())))
            return self._json(200, {"ok": True})

        if _RE_ME_PW.match(p):
            me = auth_check(self._get_token())
            if not me: return self._json(401, {"error": "unauthorized"})
            current = body.get("current_password", "")
            new_pw  = body.get("password", "")
            if not current or not new_pw:
                return self._json(400, {"error": "current_password and password required"})
            if len(new_pw) < 8:
                return self._json(400, {"error": "Password must be at least 8 characters"})
            if not auth_verify_current(me, current):
                return self._json(400, {"error": "Current password is incorrect"})
            db_set_password(me, new_pw)
            log.info(f"User {me} changed their own password")
            db_log_audit(me, self.client_address[0], 'pass_change', me)
            return self._json(200, {"ok": True})

        m = _RE_USER_PW.match(p)
        if m:
            user, _ = self._require("admin")
            if not user: return
            username = m.group(1)
            password = body.get("password", "")
            if not password:
                return self._json(400, {"error": "password required"})
            if len(password) < 8:
                return self._json(400, {"error": "Password must be at least 8 characters"})
            db_set_password(username, password)
            auth_revoke_user_sessions(username)
            log.info(f"Password reset for user: {username}")
            db_log_audit(user, self.client_address[0], 'pass_reset', username)
            return self._json(200, {"ok": True})

        # ── Topology (PATCH acts as PUT for topo settings) ────────
        m = _RE_TOPO_SETTING.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
            topo_upsert_setting(m.group(1), body.get('value'))
            return self._json(200, {'key': m.group(1), 'value': body.get('value')})

        self._json(404, {"error": "not found"})

    # ── PUT (topology editor updates) ─────────────────────────────
    def do_PUT(self):
        p    = urlparse(self.path).path
        user, _ = self._require("operator")
        if not user: return
        body = self._body()

        m = _RE_TOPO_PAGE.match(p)
        if m:
            page = topo_update_page(int(m.group(1)), body.get('name', ''))
            return self._json(200, page) if page else self._json(404, {'error': 'not found'})

        m = _RE_TOPO_NODE.match(p)
        if m:
            node = topo_update_node(int(m.group(1)), body.get('name'), body.get('type'), body.get('x'), body.get('y'), body.get('properties'))
            return self._json(200, node) if node else self._json(404, {'error': 'not found'})

        m = _RE_TOPO_LINK.match(p)
        if m:
            link = topo_update_link(int(m.group(1)), body.get('label', ''), body.get('link_type', 'trunk'))
            return self._json(200, link) if link else self._json(404, {'error': 'not found'})

        m = _RE_TOPO_GROUP.match(p)
        if m:
            grp = topo_update_group(int(m.group(1)), body.get('name'), body.get('color'), body.get('x'), body.get('y'), body.get('w'), body.get('h'))
            return self._json(200, grp) if grp else self._json(404, {'error': 'not found'})

        m = _RE_TOPO_SETTING.match(p)
        if m:
            topo_upsert_setting(m.group(1), body.get('value'))
            return self._json(200, {'key': m.group(1), 'value': body.get('value')})

        self._json(404, {"error": "not found"})

    # ── DELETE ────────────────────────────────────────────────────
    def do_DELETE(self):
        p = urlparse(self.path).path

        m = _RE_SENSOR_LOGS.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
            did, sid = m.group(1), m.group(2)
            _db_enqueue(lambda: db_clear_sensor_err_logs(did, sid))
            return self._json(200, {"ok": True})

        m = _RE_DEVICE_LOGS.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
            did = m.group(1)
            _db_enqueue(lambda: db_clear_err_logs(did))
            with STATE._lock:
                _dev = STATE.devices.get(did)
                _host = _dev.host if _dev else None
            if _host:
                _h = _host
                _db_enqueue(lambda: db_clear_device_traps(_h))
            db_log_audit(user, self.client_address[0], 'logs_clear', did)
            return self._json(200, {"ok": True})

        m = _RE_SENSOR_ITEM.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
            _sdid, _ssid = m.group(1), m.group(2)
            with STATE._lock:
                _sd = STATE.devices.get(_sdid)
                _sdname = _sd.name if _sd else _sdid
                _ssname = _sd.sensors[_ssid].name if _sd and _ssid in _sd.sensors else _ssid
            STATE.remove_sensor(_sdid, _ssid)
            _db_enqueue(lambda: db_save(STATE))
            db_log_audit(user, self.client_address[0], 'sensor_delete', f"{_sdname}/{_ssname}")
            log.info(f"Sensor deleted: {_ssname!r} on {_sdname}")
            return self._json(200, {"status": "ok"})

        m = _RE_DEVICE.match(p)
        if m:
            user, _ = self._require("operator")
            if not user: return
            _ddid = m.group(1)
            with STATE._lock:
                _dd = STATE.devices.get(_ddid)
                _ddname = _dd.name if _dd else _ddid
            STATE.remove_device(_ddid)
            _db_enqueue(lambda: db_save(STATE))
            db_log_audit(user, self.client_address[0], 'device_delete', _ddname)
            log.info(f"Device deleted: {_ddname!r}")
            return self._json(200, {"status": "ok"})

        m = _RE_USER.match(p)
        if m:
            me, _ = self._require("admin")
            if not me: return
            username = m.group(1)
            if username == me:
                return self._json(400, {"error": "cannot delete your own account"})
            users = db_list_users()
            if len(users) <= 1:
                return self._json(400, {"error": "cannot delete the last user"})
            ok = db_delete_user(username)
            if not ok:
                return self._json(404, {"error": "user not found"})
            log.info(f"User deleted: {username}")
            db_log_audit(me, self.client_address[0], 'user_delete', username)
            return self._json(200, {"ok": True})

        # ── Topology ──────────────────────────────────────────────
        if _RE_TOPO_PAGE.match(p) or _RE_TOPO_NODE.match(p) or _RE_TOPO_LINK.match(p) or _RE_TOPO_GROUP.match(p):
            user, _ = self._require("operator")
            if not user: return

        m = _RE_TOPO_PAGE.match(p)
        if m:
            _pg_id = int(m.group(1))
            _pg_name = next((pg['name'] for pg in topo_get_pages() if pg['id'] == _pg_id), str(_pg_id))
            topo_delete_page(_pg_id)
            db_log_audit(user, self.client_address[0], 'ntm_page_delete', _pg_name)
            return self._json(200, {'ok': True})

        m = _RE_TOPO_NODE.match(p)
        if m:
            topo_delete_node(int(m.group(1)))
            return self._json(200, {'ok': True})

        m = _RE_TOPO_LINK.match(p)
        if m:
            topo_delete_link(int(m.group(1)))
            return self._json(200, {'ok': True})

        m = _RE_TOPO_GROUP.match(p)
        if m:
            topo_delete_group(int(m.group(1)))
            return self._json(200, {'ok': True})

        self._json(404, {"error": "not found"})


# ── Tray icon image ──────────────────────────────────────────────

def _make_tray_icon():
    """Generate a 64×64 radar-style icon matching the app colour scheme."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=(47, 129, 247, 255))       # blue circle
    d.ellipse([14, 14, 50, 50], outline=(255, 255, 255, 180), width=2)  # outer ring
    d.ellipse([24, 24, 40, 40], outline=(255, 255, 255, 220), width=2)  # inner ring
    d.ellipse([29, 29, 35, 35], fill=(255, 255, 255, 255))     # centre dot
    return img


# ── Entry point ───────────────────────────────────────────────────

def main():
    if SYS in ("Linux", "Darwin") and os.geteuid() != 0:
        log.warning("ICMP ping may need root on this OS.")
        log.warning("If pings fail: sudo python3 server.py")

    db_init()
    try:
        init_topo_db()
    except Exception as _e:
        log.error(f"init_topo_db failed: {_e}", exc_info=True)
    try:
        migrate_topo_from_file()
    except Exception as _e:
        log.error(f"migrate_topo_from_file failed: {_e}", exc_info=True)
    db_seed_users()
    _settings.load(db_load_settings())
    global _effective_port, _effective_snmp_port
    _effective_port      = int(_settings.get("http_port",  PORT))
    _effective_snmp_port = int(_settings.get("snmp_port",  162))
    log.info(f"Database: {DB_PATH}")

    # ── Bind HTTP port FIRST — fail fast before loading state ──────
    try:
        server = QuietServer((BIND, _effective_port), Handler)
    except OSError:
        log.error(f"Cannot bind to port {_effective_port} — port may be in use. Close other instances or change the HTTP port in Settings → Networking.")
        return

    # ── Load state & start background threads ──────────────────────
    _t0 = time.time()
    db_load(STATE)
    log.info(f"State loaded in {time.time()-_t0:.2f}s — {len(STATE.devices)} device(s)")
    threading.Thread(target=autosave_loop, args=(STATE,), daemon=True).start()
    from trap_receiver import trap_receiver_loop
    threading.Thread(target=trap_receiver_loop, args=(STATE, _effective_snmp_port), daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _local_url = f"http://127.0.0.1:{_effective_port}"
    log.info(f"PingWatch ready → {_local_url}")

    # ── Import GUI (tkinter — stdlib, always available) ────────────
    from gui import StatusWindow
    from logger import log_buffer

    if _TRAY:
        # pystray runs on a background daemon thread; tkinter owns main thread
        def _open(*_):
            webbrowser.open(_local_url)

        def _quit(*_):
            if _tray_icon is not None:
                try: _tray_icon.stop()
                except Exception: pass
            win.destroy()

        def _show(*_):
            win.show()

        menu = pystray.Menu(
            pystray.MenuItem("PingWatch  ·  Network Monitor", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Status Window", _show),
            pystray.MenuItem("Open Dashboard", _open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        )

        global _tray_icon
        _tray_icon = pystray.Icon("PingWatch", _make_tray_icon(), "PingWatch", menu)
        signal.signal(signal.SIGINT, lambda *_: _quit())
        threading.Thread(target=_tray_icon.run, daemon=True).start()
    else:
        # No tray — Quit button in the GUI window is the only exit
        log.warning("pystray/Pillow not found — no tray icon. Use the Status Window to quit.")
        def _quit(*_):
            win.destroy()
        signal.signal(signal.SIGINT, lambda *_: _quit())

    # ── Status window (blocks on main thread until destroyed) ──────
    win = StatusWindow(STATE, log_buffer, PORT, quit_fn=_quit)
    win.build_and_show()

    # ── Graceful shutdown ─────────────────────────────────────────
    log.info("Shutting down...")
    STATE.stop_all()
    db_save(STATE)
    log.info("Configuration saved.")
    server.shutdown()


if __name__ == "__main__":
    main()
