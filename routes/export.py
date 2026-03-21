"""
routes/export.py — Database export, import, and audit log endpoints.

Handles: /api/db/export (GET), /api/db/import (POST), /api/audit (GET).
"""

import base64
import os
import sys
import tempfile
import threading
import time

from core.config import DB_PATH, _RE_DB_EXPORT, _RE_DB_IMPORT, _RE_AUDIT, _RE_LOGS
from db          import db_log_audit, db_get_audit
from core.logger import log, LOG_FILES


def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""

    # ── /api/db/export GET ────────────────────────────────────────
    if _RE_DB_EXPORT.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        db_log_audit(user, h.client_address[0], 'db_export')
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
        h.send_response(200)
        h.send_header("Content-Type", "application/octet-stream")
        h.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
        return True

    # ── /api/db/import POST ───────────────────────────────────────
    if _RE_DB_IMPORT.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user: return True
        db_log_audit(user, h.client_address[0], 'db_import')
        log.info(f"DB import: request received from {h.client_address[0]} by '{user}'")
        _MAX_IMPORT = 2 * 1024 * 1024 * 1024  # 2 GB
        n = int(h.headers.get("Content-Length", 0))
        if n > _MAX_IMPORT:
            log.warning(f"DB import: rejected — payload too large ({n} bytes)")
            h._json(413, {"error": "File too large (max 2 GB)"}); return True
        if not n:
            h._json(400, {"error": "No data provided"}); return True

        content_type = h.headers.get("Content-Type", "")

        if "application/octet-stream" in content_type:
            # ── New path: raw binary upload (no base64 overhead) ──
            log.info(f"DB import: reading {n:,} raw bytes from client")
            db_bytes = h.rfile.read(n)
        else:
            # ── Legacy path: JSON envelope with base64-encoded data ──
            try:
                import json as _json
                body_imp = _json.loads(h.rfile.read(n))
            except (ValueError, Exception):
                log.warning("DB import: rejected — invalid JSON payload")
                h._json(400, {"error": "Invalid JSON"}); return True
            raw_b64 = (body_imp.get("data") or "").strip()
            if not raw_b64:
                log.warning("DB import: rejected — no data field in payload")
                h._json(400, {"error": "No data provided"}); return True
            try:
                db_bytes = base64.b64decode(raw_b64)
            except Exception:
                log.warning("DB import: rejected — base64 decode failed")
                h._json(400, {"error": "Invalid base64 data"}); return True

        if not db_bytes[:16].startswith(b"SQLite format 3\x00"):
            log.warning(f"DB import: rejected — not a SQLite file (header: {db_bytes[:16]!r})")
            h._json(400, {"error": "Not a valid SQLite database file"}); return True
        log.info(f"DB import: received {len(db_bytes):,} bytes — writing to temp file")
        fd, tmp = tempfile.mkstemp(suffix=".db")
        try:
            # Write in 4 MB chunks to avoid one giant write() call
            _CHUNK = 4 * 1024 * 1024
            for _off in range(0, len(db_bytes), _CHUNK):
                os.write(fd, db_bytes[_off:_off + _CHUNK])
            os.close(fd)
            del db_bytes   # release the buffer as soon as it's on disk
        except Exception as e:
            log.error(f"DB import: failed to write temp file — {e}")
            try: os.unlink(tmp)
            except OSError: pass
            h._json(500, {"error": str(e)}); return True
        try:
            import sqlite3 as _sq3_v
            _vc = _sq3_v.connect(tmp)
            _ic = _vc.execute("PRAGMA integrity_check").fetchone()
            if _ic[0] != "ok":
                _msg = _ic[0].lower()
                if "index" in _msg and "entries" in _msg:
                    log.warning(f"DB import: index inconsistency ({_ic[0]}) — attempting REINDEX")
                    _vc.execute("REINDEX")
                    _ic2 = _vc.execute("PRAGMA integrity_check").fetchone()
                    if _ic2[0] == "ok":
                        log.info("DB import: REINDEX repaired the database — proceeding")
                    else:
                        _vc.close()
                        try: os.unlink(tmp)
                        except OSError: pass
                        log.warning(f"DB import: integrity check failed after REINDEX — {_ic2[0]}")
                        h._json(400, {"error": f"Database integrity check failed: {_ic2[0]}"}); return True
                else:
                    _vc.close()
                    try: os.unlink(tmp)
                    except OSError: pass
                    log.warning(f"DB import: integrity check failed — {_ic[0]}")
                    h._json(400, {"error": f"Database integrity check failed: {_ic[0]}"}); return True
            _vc.close()
        except Exception as _ve:
            try: os.unlink(tmp)
            except OSError: pass
            log.warning(f"DB import: validation error — {_ve}")
            h._json(400, {"error": f"Database validation failed: {_ve}"}); return True
        # Compact the imported file before it becomes the live DB
        try:
            _vvac = _sq3_v.connect(tmp)
            _vvac.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _vvac.execute("VACUUM")
            _vvac.close()
            log.debug("DB import: VACUUM complete on imported file")
        except Exception as _ve:
            log.warning(f"DB import: VACUUM failed (non-fatal) — {_ve}")
        log.info(f"DB import: temp file written to {tmp} — sending response, will swap DB in 2 s")
        h._json(200, {"ok": True, "msg": "Database imported — server is restarting…"})
        try: h.wfile.flush()
        except Exception: pass
        def _restart():
            time.sleep(2.0)
            # On Windows the live DB file is locked by open SQLite connections, so
            # os.replace() directly would fail with "Access is denied".
            # Instead, copy the validated+VACUUMed file to a ".pending_import" path
            # in the same directory as the live DB (same drive = rename on next boot).
            # server.py:main() picks it up before db_init() opens any connections.
            import shutil as _shutil
            _pending = str(DB_PATH) + ".pending_import"
            try:
                _shutil.copy2(tmp, _pending)
                log.info(f"DB import: staged pending file at {_pending}")
            except Exception as _e:
                log.error(f"DB import: failed to stage pending file — {_e}")
            try:
                os.unlink(tmp)
            except OSError:
                pass
            log.info(f"DB import: restarting process — {sys.executable} {sys.argv}")
            try:
                from db import db_flush_samples
                db_flush_samples()
            except Exception as _fe:
                log.warning(f"DB import: sample flush before restart failed — {_fe}")
            import core.app_state as _as
            if _as.tray_icon is not None:
                try: _as.tray_icon.stop()
                except Exception: pass
                time.sleep(0.3)
            _cmd = [sys.executable] + sys.argv
            if os.name == 'nt':
                # Windows: os.execv is unreliable — spawn a new process then exit
                import subprocess as _sp
                _sp.Popen(_cmd, creationflags=_sp.CREATE_NEW_CONSOLE)
                os._exit(0)
            else:
                os.execv(sys.executable, _cmd)
        threading.Thread(target=_restart, daemon=True).start()
        return True

    # ── /api/audit GET ────────────────────────────────────────────
    if _RE_AUDIT.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        h._json(200, {"entries": db_get_audit(200)})
        return True

    # ── /api/logs/{logname} GET ───────────────────────────────────
    m = _RE_LOGS.match(path)
    if m and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        key   = m.group(1)
        fpath = LOG_FILES.get(key)
        if not fpath:
            h._json(404, {"error": "unknown log"}); return True
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as _lf:
                lines = _lf.readlines()
            tail = ''.join(lines[-500:])
        except FileNotFoundError:
            tail = ''
        h._json(200, {"log": key, "lines": tail})
        return True

    return False
