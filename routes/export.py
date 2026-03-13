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

from config import DB_PATH, _RE_DB_EXPORT, _RE_DB_IMPORT, _RE_AUDIT
from db     import db_log_audit, db_get_audit
from logger import log


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
        _MAX_IMPORT = 512 * 1024 * 1024  # 512 MB
        n = int(h.headers.get("Content-Length", 0))
        if n > _MAX_IMPORT:
            log.warning(f"DB import: rejected — payload too large ({n} bytes)")
            h._json(413, {"error": "File too large (max 512 MB)"}); return True
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
        log.info(f"DB import: temp file written to {tmp} — sending response, will swap DB in 2 s")
        h._json(200, {"ok": True, "msg": "Database imported — server is restarting…"})
        try: h.wfile.flush()
        except Exception: pass
        def _restart():
            time.sleep(2.0)
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
            import app_state as _as
            if _as.tray_icon is not None:
                try: _as.tray_icon.stop()
                except Exception: pass
                time.sleep(0.3)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=_restart, daemon=True).start()
        return True

    # ── /api/audit GET ────────────────────────────────────────────
    if _RE_AUDIT.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user: return True
        h._json(200, {"entries": db_get_audit(200)})
        return True

    return False
