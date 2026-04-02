"""
routes/export.py — Database export, import, and audit log endpoints.

SQLite mode:
  GET  /api/db/export         — download Main DB (config only)
  GET  /api/db/export/logs    — download Logs DB (sensor history)
  GET  /api/db/export/bundle  — download ZIP bundle (both DBs + manifest)
  POST /api/db/import         — upload Main DB, Logs DB, or ZIP bundle

PostgreSQL mode:
  GET  /api/db/export         — pg_dump of main schema (.sql)
  GET  /api/db/export/logs    — pg_dump of logs schema (.sql)
  GET  /api/db/export/bundle  — ZIP bundle of both schema dumps
  POST /api/db/import         — not supported (returns 501 with instructions)

Both modes:
  GET  /api/audit             — audit log entries
  GET  /api/logs/{name}       — tail a log file
"""

import base64
import io
import json as _json_mod
import os
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile

from core.config import (
    DB_PATH, LOGS_DB_PATH,
    _RE_DB_EXPORT, _RE_DB_EXPORT_LOGS, _RE_DB_EXPORT_BUNDLE,
    _RE_DB_IMPORT, _RE_AUDIT, _RE_LOGS,
)
from db          import db_log_audit, db_get_audit
from core.logger import log, LOG_FILES


# ── Helpers ──────────────────────────────────────────────────────────────────

def _sqlite_backup_bytes(src_path) -> bytes:
    """Return a WAL-safe binary snapshot of a SQLite database."""
    import sqlite3 as _sq3
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = _sq3.connect(str(src_path))
        try:
            with _sq3.connect(tmp) as dst:
                src.backup(dst)
        finally:
            src.close()
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _pg_dump_bytes(schema: str) -> bytes:
    """Run pg_dump for one schema and return the SQL dump as bytes."""
    import subprocess as _sp
    from db.backend import get_config
    cfg = get_config()
    fd, tmp = tempfile.mkstemp(suffix=".sql")
    os.close(fd)
    try:
        env = {**os.environ, 'PGPASSWORD': cfg.get('pg_password', '')}
        cmd = [
            'pg_dump',
            '-h', cfg['pg_host'],
            '-p', str(cfg['pg_port']),
            '-U', cfg['pg_user'],
            '-d', cfg['pg_database'],
            '--schema', schema,
            '--no-password',
            '-f', tmp,
        ]
        result = _sp.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"pg_dump exited {result.returncode}")
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _send_db(h, data: bytes, filename: str):
    h.send_response(200)
    h.send_header("Content-Type", "application/octet-stream")
    h.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def _validate_sqlite(path) -> str:
    """
    Run integrity_check and attempt REINDEX if needed.
    Returns 'ok' on success, error string on failure.
    """
    try:
        con = sqlite3.connect(path)
        ic = con.execute("PRAGMA integrity_check").fetchone()
        if ic[0] != "ok":
            msg = ic[0].lower()
            if "index" in msg and "entries" in msg:
                con.execute("REINDEX")
                ic2 = con.execute("PRAGMA integrity_check").fetchone()
                con.close()
                return "ok" if ic2[0] == "ok" else ic2[0]
            con.close()
            return ic[0]
        con.close()
        return "ok"
    except Exception as e:
        return str(e)


def _vacuum_file(path):
    """WAL checkpoint + VACUUM a file (best-effort)."""
    try:
        con = sqlite3.connect(path, timeout=30)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
        con.close()
    except Exception as e:
        log.warning(f"DB import: VACUUM failed on {path} (non-fatal) — {e}")


def _detect_db_kind(path) -> str:
    """
    Classify an uploaded SQLite file:
      'old_single'  — legacy single-DB (has sensor_samples AND users)
      'main'        — new Main DB (has devices, no sensor_samples)
      'logs'        — new Logs DB (has sensor_samples, no users)
      'unknown'     — cannot determine
    """
    try:
        con = sqlite3.connect(path)
        tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        con.close()
        has_samples = "sensor_samples" in tables
        has_users   = "users" in tables
        has_devices = "devices" in tables
        if has_samples and has_users:
            return "old_single"
        if has_devices and not has_samples:
            return "main"
        if has_samples and not has_users:
            return "logs"
    except Exception:
        pass
    return "unknown"


def _stage_pending(src_tmp, pending_path):
    """Copy validated DB file to the .pending_* staging path."""
    import shutil as _shutil
    _shutil.copy2(src_tmp, pending_path)


def _do_restart(pending_main=None, pending_logs=None):
    """Stage pending file(s), flush samples, then restart the process."""
    time.sleep(2.0)
    if pending_main:
        import shutil as _sh
        try:
            _sh.copy2(pending_main[0], pending_main[1])
            log.info(f"DB import: staged Main DB pending at {pending_main[1]}")
        except Exception as e:
            log.error(f"DB import: failed to stage Main DB — {e}")
        try:
            os.unlink(pending_main[0])
        except OSError:
            pass
    if pending_logs:
        import shutil as _sh
        try:
            _sh.copy2(pending_logs[0], pending_logs[1])
            log.info(f"DB import: staged Logs DB pending at {pending_logs[1]}")
        except Exception as e:
            log.error(f"DB import: failed to stage Logs DB — {e}")
        try:
            os.unlink(pending_logs[0])
        except OSError:
            pass

    try:
        from db import db_flush_samples
        db_flush_samples()
    except Exception as _fe:
        log.warning(f"DB import: sample flush before restart failed — {_fe}")

    import core.app_state as _as
    if _as.tray_icon is not None:
        try:
            _as.tray_icon.stop()
        except Exception:
            pass
        time.sleep(0.3)

    _cmd = [sys.executable] + sys.argv
    log.info(f"DB import: restarting process — {sys.executable} {sys.argv}")
    if os.name == "nt":
        import subprocess as _sp
        _sp.Popen(_cmd, creationflags=_sp.CREATE_NEW_CONSOLE)
        os._exit(0)
    else:
        os.execv(sys.executable, _cmd)


# ── Route handler ─────────────────────────────────────────────────────────────

def handle(h, method, path, body):
    """Return True if this module handled the request, False otherwise."""
    from db.backend import is_pg

    # ── GET /api/db/export — Main DB ─────────────────────────────
    if _RE_DB_EXPORT.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        db_log_audit(user, h.client_address[0], "db_export_main")
        if is_pg():
            try:
                data  = _pg_dump_bytes('main')
                fname = "pingwatch-main-" + time.strftime("%Y%m%d-%H%M%S") + ".sql"
            except Exception as e:
                log.error(f"DB export (PG main): {e}")
                h._json(500, {"error": "Database export failed — check server logs"}); return True
        else:
            data  = _sqlite_backup_bytes(DB_PATH)
            fname = "pingwatch-main-" + time.strftime("%Y%m%d-%H%M%S") + ".db"
        _send_db(h, data, fname)
        return True

    # ── GET /api/db/export/logs — Logs DB ────────────────────────
    if _RE_DB_EXPORT_LOGS.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        db_log_audit(user, h.client_address[0], "db_export_logs")
        if is_pg():
            try:
                data  = _pg_dump_bytes('logs')
                fname = "pingwatch-logs-" + time.strftime("%Y%m%d-%H%M%S") + ".sql"
            except Exception as e:
                log.error(f"DB export (PG logs): {e}")
                h._json(500, {"error": "Database export failed — check server logs"}); return True
        else:
            if not os.path.exists(LOGS_DB_PATH):
                h._json(404, {"error": "Logs DB does not exist yet"}); return True
            data  = _sqlite_backup_bytes(LOGS_DB_PATH)
            fname = "pingwatch-logs-" + time.strftime("%Y%m%d-%H%M%S") + ".db"
        _send_db(h, data, fname)
        return True

    # ── GET /api/db/export/bundle — ZIP with both DBs ────────────
    if _RE_DB_EXPORT_BUNDLE.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        db_log_audit(user, h.client_address[0], "db_export_bundle")
        ts = time.strftime("%Y%m%d-%H%M%S")

        if is_pg():
            try:
                main_data = _pg_dump_bytes('main')
                logs_data = _pg_dump_bytes('logs')
            except Exception as e:
                log.error(f"DB export bundle (PG): {e}")
                h._json(500, {"error": "Database export failed — check server logs"}); return True
            manifest = {
                "version":    1,
                "backend":    "postgresql",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "has_main":   True,
                "has_logs":   bool(logs_data),
            }
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("pingwatch_main.sql",  main_data)
                if logs_data:
                    zf.writestr("pingwatch_logs.sql", logs_data)
                zf.writestr("manifest.json", _json_mod.dumps(manifest, indent=2).encode())
        else:
            main_data = _sqlite_backup_bytes(DB_PATH)
            logs_data = _sqlite_backup_bytes(LOGS_DB_PATH) if os.path.exists(LOGS_DB_PATH) else b""

            # Determine schema versions
            try:
                _mc = sqlite3.connect(DB_PATH)
                sv_main = (_mc.execute(
                    "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
                ).fetchone() or (1,))[0]
                _mc.close()
            except Exception:
                sv_main = 1
            sv_logs = 1
            if logs_data:
                try:
                    _lc = sqlite3.connect(LOGS_DB_PATH)
                    sv_logs = (_lc.execute(
                        "SELECT version FROM logs_schema_version ORDER BY version DESC LIMIT 1"
                    ).fetchone() or (1,))[0]
                    _lc.close()
                except Exception:
                    sv_logs = 1

            manifest = {
                "version":      1,
                "backend":      "sqlite",
                "created_at":   time.strftime("%Y-%m-%dT%H:%M:%S"),
                "schema_main":  sv_main,
                "schema_logs":  sv_logs,
                "has_main":     True,
                "has_logs":     bool(logs_data),
            }
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("pingwatch_main.db",  main_data)
                if logs_data:
                    zf.writestr("pingwatch_logs.db", logs_data)
                zf.writestr("manifest.json", _json_mod.dumps(manifest, indent=2).encode())

        zip_bytes = buf.getvalue()
        fname = f"pingwatch-bundle-{ts}.zip"
        h.send_response(200)
        h.send_header("Content-Type", "application/zip")
        h.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        h.send_header("Content-Length", str(len(zip_bytes)))
        h.end_headers()
        h.wfile.write(zip_bytes)
        return True

    # ── POST /api/db/import ───────────────────────────────────────
    if _RE_DB_IMPORT.match(path) and method == "POST" and is_pg():
        user, _ = h._require("admin")
        if not user:
            return True
        h._json(501, {
            "error": (
                "Direct DB import is not supported for PostgreSQL. "
                "To restore, stop the server and run: "
                "psql -h HOST -U pingwatch -d pingwatch -f dump.sql"
            )
        })
        return True

    if _RE_DB_IMPORT.match(path) and method == "POST":
        user, _ = h._require("admin")
        if not user:
            return True
        db_log_audit(user, h.client_address[0], "db_import")
        log.info(f"DB import: request received from {h.client_address[0]} by '{user}'")

        _MAX_IMPORT = 2 * 1024 * 1024 * 1024   # 2 GB
        n = int(h.headers.get("Content-Length", 0))
        if n > _MAX_IMPORT:
            log.warning(f"DB import: rejected — payload too large ({n} bytes)")
            h._json(413, {"error": "File too large (max 2 GB)"}); return True
        if not n:
            h._json(400, {"error": "No data provided"}); return True

        content_type = h.headers.get("Content-Type", "")

        if "application/octet-stream" in content_type:
            log.info(f"DB import: reading {n:,} raw bytes from client")
            raw_bytes = h.rfile.read(n)
        else:
            # Legacy JSON/base64 path
            try:
                body_imp = _json_mod.loads(h.rfile.read(n))
            except (ValueError, Exception):
                log.warning("DB import: rejected — invalid JSON payload")
                h._json(400, {"error": "Invalid JSON"}); return True
            raw_b64 = (body_imp.get("data") or "").strip()
            if not raw_b64:
                log.warning("DB import: rejected — no data field in payload")
                h._json(400, {"error": "No data provided"}); return True
            try:
                raw_bytes = base64.b64decode(raw_b64)
            except Exception:
                log.warning("DB import: rejected — base64 decode failed")
                h._json(400, {"error": "Invalid base64 data"}); return True

        # ── ZIP bundle detection ──────────────────────────────────
        if raw_bytes[:4] == b"PK\x03\x04":
            return _handle_bundle_import(h, raw_bytes)

        # ── Single SQLite file ────────────────────────────────────
        if not raw_bytes[:16].startswith(b"SQLite format 3\x00"):
            log.warning(f"DB import: rejected — not a SQLite or ZIP file (header: {raw_bytes[:16]!r})")
            h._json(400, {"error": "Not a valid SQLite database file or ZIP bundle"}); return True

        log.info(f"DB import: received {len(raw_bytes):,} bytes — writing to temp file")
        fd, tmp = tempfile.mkstemp(suffix=".db")
        try:
            _CHUNK = 4 * 1024 * 1024
            for _off in range(0, len(raw_bytes), _CHUNK):
                os.write(fd, raw_bytes[_off:_off + _CHUNK])
            os.close(fd)
            del raw_bytes
        except Exception as e:
            log.error(f"DB import: failed to write temp file — {e}")
            try:
                os.unlink(tmp)
            except OSError:
                pass
            h._json(500, {"error": str(e)}); return True

        # Validate
        ok_msg = _validate_sqlite(tmp)
        if ok_msg != "ok":
            try:
                os.unlink(tmp)
            except OSError:
                pass
            log.warning(f"DB import: integrity check failed — {ok_msg}")
            h._json(400, {"error": f"Database integrity check failed: {ok_msg}"}); return True

        _vacuum_file(tmp)

        kind = _detect_db_kind(tmp)
        log.info(f"DB import: detected DB kind = '{kind}'")

        if kind == "logs":
            _pending = str(LOGS_DB_PATH) + ".pending_logs_import"
            msg = "Logs DB imported — server is restarting…"
            h._json(200, {"ok": True, "msg": msg})
            try:
                h.wfile.flush()
            except Exception:
                pass
            threading.Thread(
                target=_do_restart,
                args=(None, (tmp, _pending)),
                daemon=True
            ).start()
        else:
            # old_single, main, or unknown → stage as Main DB import
            # (old_single will be split by migration on next startup)
            _pending = str(DB_PATH) + ".pending_import"
            msg = "Database imported — server is restarting…"
            h._json(200, {"ok": True, "msg": msg})
            try:
                h.wfile.flush()
            except Exception:
                pass
            threading.Thread(
                target=_do_restart,
                args=((tmp, _pending), None),
                daemon=True
            ).start()
        return True

    # ── /api/audit GET ────────────────────────────────────────────
    if _RE_AUDIT.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        h._json(200, {"entries": db_get_audit(200)})
        return True

    # ── /api/logs/{logname} GET ───────────────────────────────────
    m = _RE_LOGS.match(path)
    if m and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        key   = m.group(1)
        fpath = LOG_FILES.get(key)
        if not fpath:
            h._json(404, {"error": "unknown log"}); return True
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as _lf:
                lines = _lf.readlines()
            tail = "".join(lines[-500:])
        except FileNotFoundError:
            tail = ""
        h._json(200, {"log": key, "lines": tail})
        return True

    return False


def _handle_bundle_import(h, raw_bytes: bytes):
    """Handle a ZIP bundle upload containing Main DB + Logs DB + manifest."""
    log.info(f"DB import: ZIP bundle detected — {len(raw_bytes):,} bytes")
    try:
        buf = io.BytesIO(raw_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            manifest_data = None
            if "manifest.json" in names:
                manifest_data = _json_mod.loads(zf.read("manifest.json"))
                log.info(f"DB import: bundle manifest: {manifest_data}")

            tmp_main = tmp_logs = None

            if "pingwatch_main.db" in names:
                fd, tmp_main = tempfile.mkstemp(suffix="_main.db")
                os.write(fd, zf.read("pingwatch_main.db"))
                os.close(fd)
            if "pingwatch_logs.db" in names:
                fd, tmp_logs = tempfile.mkstemp(suffix="_logs.db")
                os.write(fd, zf.read("pingwatch_logs.db"))
                os.close(fd)
    except Exception as e:
        log.error(f"DB import: ZIP extraction failed — {e}")
        h._json(400, {"error": f"ZIP extraction failed: {e}"}); return True

    # Validate each extracted DB
    for label, tmp in (("Main", tmp_main), ("Logs", tmp_logs)):
        if not tmp:
            continue
        ok_msg = _validate_sqlite(tmp)
        if ok_msg != "ok":
            for _t in (tmp_main, tmp_logs):
                if _t:
                    try:
                        os.unlink(_t)
                    except OSError:
                        pass
            log.warning(f"DB import: {label} DB integrity check failed — {ok_msg}")
            h._json(400, {"error": f"{label} DB integrity check failed: {ok_msg}"}); return True
        _vacuum_file(tmp)

    pending_main = (tmp_main, str(DB_PATH) + ".pending_import") if tmp_main else None
    pending_logs = (tmp_logs, str(LOGS_DB_PATH) + ".pending_logs_import") if tmp_logs else None

    log.info(f"DB import: bundle validated — main={bool(tmp_main)}, logs={bool(tmp_logs)}")
    h._json(200, {"ok": True, "msg": "Bundle imported — server is restarting…"})
    try:
        h.wfile.flush()
    except Exception:
        pass
    threading.Thread(
        target=_do_restart,
        args=(pending_main, pending_logs),
        daemon=True
    ).start()
    return True
