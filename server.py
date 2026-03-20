"""
server.py — HTTP dispatcher, static file serving, and application entry point.

Run:  python server.py
      Browser opens at http://localhost:7070

Linux/Mac may need: sudo python3 server.py  (for ICMP ping)
"""

import http.server
import json
import os
import signal
import sys
import traceback
import threading
import time
import webbrowser
from urllib.parse import urlparse

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY = True
except ImportError:
    _TRAY = False

import core.settings as _settings
from core.auth       import auth_check, auth_check_role
from core.config     import BIND, DB_PATH, FRONTEND_DIR, PORT, SYS, TLS_PORT_DEFAULT, _RE_DB_IMPORT
from core.logger     import log
from monitoring.network_map import init_topo_db, migrate_topo_from_file
from db              import (
    _db_enqueue, autosave_loop,
    db_init, db_load, db_seed_users,
    db_load_settings, db_save,
)

import core.app_state as app_state
from core.app_state import STATE

# ── RBAC role ranking ─────────────────────────────────────────────
_ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}

# ── Static file mime types ────────────────────────────────────────
_STATIC_TYPES = {
    ".css":   "text/css",
    ".js":    "application/javascript",
    ".png":   "image/png",
    ".ico":   "image/x-icon",
    ".svg":   "image/svg+xml",
    ".woff":  "font/woff",
    ".woff2": "font/woff2",
}

# ── JS files inlined into index.html ─────────────────────────────
_JS_FILES = [
    "bg.js", "devices.js", "sensors.js",
    "forms-utils.js", "forms-device.js", "forms-sensor.js",
    "forms-settings.js", "forms-io.js", "forms-users.js",
    "dashboard.js", "events.js", "backups.js", "app.js",
]

_MAP_HTML_PATH = os.path.join(FRONTEND_DIR, 'map.html')


def _load_map_html() -> bytes:
    with open(_MAP_HTML_PATH, 'rb') as f:
        return f.read()


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


# ── Custom server: silences browser-disconnect noise ─────────────

class QuietServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer that suppresses noisy browser-disconnect errors."""

    _IGNORED = ('ConnectionAbortedError', 'ConnectionResetError', 'BrokenPipeError')

    def handle_error(self, request, client_address):
        if any(e in traceback.format_exc() for e in self._IGNORED):
            return
        super().handle_error(request, client_address)


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
        """Return (username, role) if user meets min_role, else send error."""
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

    import re as _re
    _HOST_RE = _re.compile(r'^[a-zA-Z0-9._\-]+(:\d+)?$')

    @staticmethod
    def _valid_host(h): return bool(h and Handler._HOST_RE.match(h))
    @staticmethod
    def _valid_url(u):  return bool(u and (u.startswith("http://") or u.startswith("https://")))

    _ALLOWED_ORIGINS = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}

    def _origin(self):
        """Return the request Origin only if it is whitelisted, else None."""
        o = self.headers.get("Origin", "")
        return o if o in self._ALLOWED_ORIGINS else None

    def _sec_headers(self):
        """Emit security headers on every response."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "   # blob: needed for canvas/PNG export
            "worker-src blob:;"              # blob: needed for canvas toBlob()
        )

    def _json(self, code, data):
        body   = json.dumps(data).encode()
        origin = self._origin()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        self._sec_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        origin = self._origin()
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS,PATCH")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self._sec_headers()
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
        from routes import auth, devices, monitoring, settings, topology, export, backups
        p = urlparse(self.path).path

        # ── Main dashboard HTML (inlined CSS + JS) ────────────────
        if p in ("/", "/index.html"):
            body = _load_html()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._sec_headers()
            self.end_headers()
            self.wfile.write(body)
            return

        # ── NTM map HTML (served only to authenticated users) ─────
        if p == '/map':
            if not self._auth(): return
            data = _load_map_html()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(data))
            self._sec_headers()
            self.end_headers()
            self.wfile.write(data)
            return

        # ── Generic static files from frontend/ (CSS, JS, …) ─────
        ext = os.path.splitext(p)[1].lower()
        if ext in _STATIC_TYPES:
            from pathlib import Path as _Path
            _base = _Path(FRONTEND_DIR).resolve()
            fp_path = (_base / p.lstrip('/')).resolve()
            fp = str(fp_path)
            # Safety check: resolve() collapses .. and symlinks before prefix test
            if str(fp_path).startswith(str(_base)) and fp_path.is_file():
                with open(fp, 'rb') as _f:
                    data = _f.read()
                self.send_response(200)
                self.send_header('Content-Type', _STATIC_TYPES[ext])
                self.send_header('Content-Length', len(data))
                self._sec_headers()
                self.end_headers()
                self.wfile.write(data)
                return

        # ── API routes ────────────────────────────────────────────
        from routes import tls as _tls_mod
        for mod in (auth, devices, monitoring, settings, topology, export, backups, _tls_mod):
            if mod.handle(self, 'GET', p, {}):
                return

        self._json(404, {"error": "not found"})

    # ── POST ──────────────────────────────────────────────────────
    def do_POST(self):
        from routes import auth, devices, monitoring, settings, topology, export, backups, tls as _tls_mod
        p = urlparse(self.path).path

        # DB import reads its own oversized body before we call _body()
        if _RE_DB_IMPORT.match(p):
            if export.handle(self, 'POST', p, {}):
                return

        body = self._body()

        for mod in (auth, devices, monitoring, settings, topology, export, backups, _tls_mod):
            if mod.handle(self, 'POST', p, body):
                return

        self._json(404, {"error": "not found"})

    # ── PATCH ─────────────────────────────────────────────────────
    def do_PATCH(self):
        from routes import auth, devices, settings, topology, tls as _tls_mod
        p    = urlparse(self.path).path
        body = self._body()

        for mod in (auth, devices, settings, topology, _tls_mod):
            if mod.handle(self, 'PATCH', p, body):
                return

        self._json(404, {"error": "not found"})

    # ── PUT ───────────────────────────────────────────────────────
    def do_PUT(self):
        from routes import topology, settings, backups
        p    = urlparse(self.path).path
        body = self._body()

        if settings.handle(self, 'PUT', p, body):
            return
        if topology.handle(self, 'PUT', p, body):
            return
        if backups.handle(self, 'PUT', p, body):
            return

        self._json(404, {"error": "not found"})

    # ── DELETE ────────────────────────────────────────────────────
    def do_DELETE(self):
        from routes import auth, devices, topology, backups
        p = urlparse(self.path).path

        for mod in (auth, devices, topology, backups):
            if mod.handle(self, 'DELETE', p, {}):
                return

        self._json(404, {"error": "not found"})


# ── Tray icon image ──────────────────────────────────────────────

def _make_tray_icon():
    """Generate a 64×64 radar-style icon matching the app colour scheme."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, 62, 62], fill=(47, 129, 247, 255))
    d.ellipse([14, 14, 50, 50], outline=(255, 255, 255, 180), width=2)
    d.ellipse([24, 24, 40, 40], outline=(255, 255, 255, 220), width=2)
    d.ellipse([29, 29, 35, 35], fill=(255, 255, 255, 255))
    return img


# ── HTTP → HTTPS redirect helper ─────────────────────────────────

def _start_http_redirect(http_port: int, https_port: int):
    """
    Start a tiny HTTP server on http_port that returns 301 redirects to the
    HTTPS server on https_port.  Runs in a daemon thread — fails silently if
    the port is already in use (the main HTTPS server still works).
    """
    import http.server as _hs

    class _RedirectHandler(_hs.BaseHTTPRequestHandler):
        def do_GET(self):  self._redirect()
        def do_POST(self): self._redirect()
        def do_HEAD(self): self._redirect()
        def _redirect(self):
            host = (self.headers.get("Host") or "localhost").split(":")[0]
            target = f"https://{host}:{https_port}{self.path}"
            self.send_response(301)
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, *_): pass   # suppress access log noise

    try:
        _rsrv = _hs.ThreadingHTTPServer((BIND, http_port), _RedirectHandler)
        threading.Thread(target=_rsrv.serve_forever, daemon=True,
                         name="http-redirect").start()
        log.info(f"HTTP→HTTPS redirect active: http://:{http_port} → https://:{https_port}")
    except OSError as _e:
        log.warning(f"HTTP redirect server could not bind to port {http_port}: {_e}")


# ── Entry point ───────────────────────────────────────────────────

def main():
    if SYS in ("Linux", "Darwin") and os.geteuid() != 0:
        log.warning("ICMP ping may need root on this OS.")
        log.warning("If pings fail: sudo python3 server.py")

    # ── Apply pending DB import (Windows-safe two-step swap) ─────────
    _pending = str(DB_PATH) + ".pending_import"
    if os.path.exists(_pending):
        try:
            for _ext in ('', '-wal', '-shm'):
                _cur = str(DB_PATH) + _ext
                if os.path.exists(_cur):
                    os.unlink(_cur)
            os.replace(_pending, str(DB_PATH))
            log.info("DB import: applied pending import → live DB")
        except Exception as _pe:
            log.error(f"DB import: failed to apply pending import — {_pe}")

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
    try:
        from snmp.seeds.loader import load_all_seeds
        load_all_seeds()
    except Exception as _se:
        log.error(f"SNMP seed load failed: {_se}")
    _settings.load(db_load_settings())

    app_state.effective_port      = int(_settings.get("http_port",  PORT))
    app_state.effective_snmp_port = int(_settings.get("snmp_port",  162))
    log.info(f"Database: {DB_PATH}")

    # ── Bind HTTP port FIRST — fail fast before loading state ──────
    try:
        server = QuietServer((BIND, app_state.effective_port), Handler)
    except OSError:
        log.error(
            f"Cannot bind to port {app_state.effective_port} — port may be in use. "
            "Close other instances or change the HTTP port in Settings -> Networking."
        )
        return

    # ── TLS / HTTPS ─────────────────────────────────────────────────
    app_state.tls_active = False
    _tls_enabled = int(_settings.get("tls_enabled", 0))
    if not _tls_enabled:
        log.info("TLS disabled — serving plain HTTP")
    if _tls_enabled:
        from core.tls import (discover_or_generate_cert, build_ssl_context,
                              check_cert_expiry_warn)
        from db.backups import encrypt_pw
        from db import db_save_settings
        _tls_port = int(_settings.get("tls_port", TLS_PORT_DEFAULT))
        _org_name = _settings.get("org_name", "PingWatch") or "PingWatch"
        import socket as _sock
        _hostname = _settings.get("tls_cn", "") or _sock.gethostname() or "localhost"
        try:
            _cert_pem, _key_pem, _source = discover_or_generate_cert(_org_name, _hostname)
            # Persist freshly discovered/generated certs to DB
            if _source in ("generated", "imported"):
                _key_enc = encrypt_pw(_key_pem)
                db_save_settings({
                    "tls_cert_pem":    _cert_pem,
                    "tls_key_pem_enc": _key_enc,
                    "tls_cert_source": _source,
                })
                _settings.load({
                    "tls_cert_pem":    _cert_pem,
                    "tls_key_pem_enc": _key_enc,
                    "tls_cert_source": _source,
                })
                log.info(f"TLS: certificate ({_source}) saved to database")
            check_cert_expiry_warn(_cert_pem)
            # Close the plain HTTP socket and rebind on the TLS port
            server.server_close()
            try:
                server = QuietServer((BIND, _tls_port), Handler)
            except OSError:
                log.error(
                    f"Cannot bind to TLS port {_tls_port} — port may be in use or requires admin. "
                    f"Falling back to HTTP on port {app_state.effective_port}."
                )
                server = QuietServer((BIND, app_state.effective_port), Handler)
                _tls_enabled = 0
            else:
                _ssl_ctx = build_ssl_context(_cert_pem, _key_pem)
                server.socket = _ssl_ctx.wrap_socket(server.socket, server_side=True)
                app_state.effective_port = _tls_port
                app_state.tls_active = True
                log.info(f"HTTPS server ready on port {_tls_port} (cert source: {_source})")
        except Exception as _tls_err:
            log.error(f"TLS startup failed — falling back to HTTP: {_tls_err}", exc_info=True)

    # ── Optional HTTP → HTTPS redirect server ───────────────────────
    if app_state.tls_active and int(_settings.get("http_redirect", 0)):
        _http_port = int(_settings.get("http_port", PORT))
        _https_port = app_state.effective_port
        _start_http_redirect(_http_port, _https_port)

    # ── Load state & start background threads ──────────────────────
    _t0 = time.time()
    db_load(STATE)
    log.info(f"State loaded in {time.time()-_t0:.2f}s — {len(STATE.devices)} device(s)")
    threading.Thread(target=autosave_loop, args=(STATE,), daemon=True).start()
    from snmp.receiver import trap_receiver_loop
    threading.Thread(
        target=trap_receiver_loop,
        args=(STATE, app_state.effective_snmp_port),
        daemon=True,
    ).start()
    from backup.scheduler import start_scheduler
    start_scheduler()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    _scheme = "https" if app_state.tls_active else "http"
    _local_url = f"{_scheme}://127.0.0.1:{app_state.effective_port}"
    log.info(f"PingWatch ready -> {_local_url}")

    # ── GUI ────────────────────────────────────────────────────────
    from core.logger import log_buffer
    try:
        from gui import StatusWindow
        _GUI = True
    except ImportError:
        _GUI = False
        log.warning(
            "tkinter not available — status window disabled. "
            "Install python3-tk (Linux) or python-tk (macOS) to enable."
        )

    _headless_stop = threading.Event()

    if _TRAY:
        def _open(*_):
            webbrowser.open(_local_url)

        def _quit(*_):
            if app_state.tray_icon is not None:
                try: app_state.tray_icon.stop()
                except Exception: pass
            if _GUI:
                win.destroy()
            else:
                _headless_stop.set()

        def _show(*_):
            if _GUI: win.show()

        menu = pystray.Menu(
            pystray.MenuItem("PingWatch  ·  Network Monitor", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Status Window", _show),
            pystray.MenuItem("Open Dashboard", _open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _quit),
        )

        app_state.tray_icon = pystray.Icon("PingWatch", _make_tray_icon(), "PingWatch", menu)
        signal.signal(signal.SIGINT, lambda *_: _quit())
        threading.Thread(target=app_state.tray_icon.run, daemon=True).start()
    else:
        log.warning("pystray/Pillow not found — no tray icon. Use the Status Window to quit.")
        def _quit(*_):
            if _GUI:
                win.destroy()
            else:
                _headless_stop.set()
        signal.signal(signal.SIGINT, lambda *_: _quit())

    if _GUI:
        win = StatusWindow(STATE, log_buffer, PORT, quit_fn=_quit)
        win.build_and_show()
    else:
        log.info("Running headlessly — press Ctrl+C to stop.")
        _headless_stop.wait()

    # ── Graceful shutdown ─────────────────────────────────────────
    log.info("Shutting down...")
    STATE.stop_all()
    db_save(STATE)
    log.info("Configuration saved.")
    server.shutdown()


if __name__ == "__main__":
    main()
