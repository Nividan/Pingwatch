"""
routes/export.py — Database export, import, and audit log endpoints.

SQLite mode:
  GET  /api/db/export         — download Main DB (config only)
  GET  /api/db/export/logs    — download Logs DB (sensor history)
  GET  /api/db/export/bundle  — full restorable bundle: both DBs + secrets
                                (Fernet key, TLS certs, pingwatch.conf) + manifest,
                                AEAD-encrypted to .pwbk when a passphrase is set
  POST /api/db/import         — upload Main DB, Logs DB, ZIP bundle, or .pwbk

PostgreSQL mode:
  GET  /api/db/export         — pg_dump of main schema (.sql)
  GET  /api/db/export/logs    — pg_dump of logs schema (.sql)
  GET  /api/db/export/bundle  — full restorable bundle (.zip / .pwbk)
  POST /api/db/import         — bundle (.zip / .pwbk) restore via psql

Bundle encryption/format lives in core/backup_bundle.py (the single source of
truth shared with the scheduled backup job). The passphrase travels in the
X-Bundle-Passphrase request header, never the URL.

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
import re
import zipfile
from urllib.parse import urlparse, parse_qs

from core.config import (
    DB_PATH, LOGS_DB_PATH,
    _RE_DB_EXPORT, _RE_DB_EXPORT_LOGS, _RE_DB_EXPORT_BUNDLE,
    _RE_DB_IMPORT, _RE_AUDIT, _RE_LOGS,
)
from db          import db_log_audit, db_get_audit
from core.logger import log, LOG_FILES
from core        import app_state
from core.backup_bundle import (
    build_bundle, is_encrypted, decrypt_container, restore_secrets_from_zip,
)

_LOG_LINE_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+'
    r'(INFO|WARNING|WARN|ERROR|CRITICAL|DEBUG)\s+'
    r'(.*)'
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_body_spooled(h, n: int, max_mem: int = 32 * 1024 * 1024) -> bytes:
    """Read exactly n bytes from h.rfile via a SpooledTemporaryFile.

    Bodies under max_mem stay in RAM; larger ones spill to disk. This keeps
    the receive path's peak heap footprint small during a multi-GB upload
    (the alternative `h.rfile.read(n)` allocates the full payload at once).
    The caller still receives a bytes object at the end — passing the spool
    through to handlers would avoid the final materialization, but that's
    more invasive than this hardening pass.
    """
    spool = tempfile.SpooledTemporaryFile(max_size=max_mem, mode='w+b')
    try:
        remaining = n
        _CHUNK = 1024 * 1024
        while remaining > 0:
            chunk = h.rfile.read(min(_CHUNK, remaining))
            if not chunk:
                break
            spool.write(chunk)
            remaining -= len(chunk)
        spool.seek(0)
        return spool.read()
    finally:
        spool.close()


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
    from db.backend import get_config, pg_env as _pg_env
    cfg = get_config()
    fd, tmp = tempfile.mkstemp(suffix=".sql")
    os.close(fd)
    pgpass = None
    try:
        env, pgpass = _pg_env(cfg)
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
        if pgpass:
            try:
                os.unlink(pgpass)
            except OSError:
                pass
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
    con = None
    try:
        con = sqlite3.connect(path)
        ic = con.execute("PRAGMA integrity_check").fetchone()
        if ic[0] != "ok":
            msg = ic[0].lower()
            if "index" in msg and "entries" in msg:
                con.execute("REINDEX")
                ic2 = con.execute("PRAGMA integrity_check").fetchone()
                return "ok" if ic2[0] == "ok" else ic2[0]
            return ic[0]
        return "ok"
    except Exception as e:
        log.warning(f"DB import: integrity check failed on {path}: {e}")
        return "integrity check failed"
    finally:
        if con is not None:
            try: con.close()
            except Exception: pass


def _vacuum_file(path):
    """WAL checkpoint + VACUUM a file (best-effort)."""
    con = None
    try:
        con = sqlite3.connect(path, timeout=30)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
    except Exception as e:
        log.warning(f"DB import: VACUUM failed on {path} (non-fatal) — {e}")
    finally:
        if con is not None:
            try: con.close()
            except Exception: pass


def _detect_db_kind(path) -> str:
    """
    Classify an uploaded SQLite file:
      'old_single'  — legacy single-DB (has sensor_samples AND users)
      'main'        — new Main DB (has devices, no sensor_samples)
      'logs'        — new Logs DB (has sensor_samples, no users)
      'unknown'     — cannot determine
    """
    con = None
    try:
        con = sqlite3.connect(path)
        tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
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
    finally:
        if con is not None:
            try: con.close()
            except Exception: pass
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


def _handle_pg_bundle_import(h, raw_bytes: bytes):
    """Restore a PG bundle ZIP (pingwatch_main.sql + pingwatch_logs.sql) via psql."""
    import subprocess as _sp
    from db.backend import get_config

    log.info(f"DB import (PG): ZIP bundle — {len(raw_bytes):,} bytes")
    try:
        buf = io.BytesIO(raw_bytes)
        with zipfile.ZipFile(buf, "r") as zf:
            names = zf.namelist()
            if "manifest.json" in names:
                manifest = _json_mod.loads(zf.read("manifest.json"))
                log.info(f"DB import (PG): manifest: {manifest}")
                if manifest.get("backend") != "postgresql":
                    h._json(400, {"error": "Bundle was not exported from a PostgreSQL backend"}); return True
            tmp_main = tmp_logs = None
            if "pingwatch_main.sql" in names:
                fd, tmp_main = tempfile.mkstemp(suffix="_main.sql")
                os.write(fd, zf.read("pingwatch_main.sql"))
                os.close(fd)
            if "pingwatch_logs.sql" in names:
                fd, tmp_logs = tempfile.mkstemp(suffix="_logs.sql")
                os.write(fd, zf.read("pingwatch_logs.sql"))
                os.close(fd)
    except Exception as e:
        log.error(f"DB import (PG): ZIP extraction failed — {e}")
        h._json(400, {"error": "ZIP extraction failed"}); return True

    if not tmp_main and not tmp_logs:
        h._json(400, {"error": "No PostgreSQL dumps found in bundle (expected pingwatch_main.sql / pingwatch_logs.sql)"}); return True

    cfg = get_config()
    from db.backend import pg_env as _pg_env
    env, pgpass = _pg_env(cfg)
    base_cmd = [
        "psql",
        "-h", cfg["pg_host"],
        "-p", str(cfg["pg_port"]),
        "-U", cfg["pg_user"],
        "-d", cfg["pg_database"],
        "--no-password",
    ]

    def _restore(sql_file, schema):
        r = _sp.run(
            base_cmd + ["-c", f"DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema};"],
            env=env, capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"schema reset: {r.stderr.strip()}")
        r = _sp.run(base_cmd + ["-f", sql_file], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"psql restore: {r.stderr.strip()}")

    try:
        if tmp_main:
            log.info("DB import (PG): restoring main schema…")
            _restore(tmp_main, "main")
            log.info("DB import (PG): main schema restored")
        if tmp_logs:
            log.info("DB import (PG): restoring logs schema…")
            _restore(tmp_logs, "logs")
            log.info("DB import (PG): logs schema restored")
    except Exception as e:
        log.error(f"DB import (PG): restore failed — {e}")
        for t in (tmp_main, tmp_logs, pgpass):
            if t:
                try: os.unlink(t)
                except OSError: pass
        h._json(500, {"error": "PostgreSQL restore failed — check server logs"}); return True

    for t in (tmp_main, tmp_logs, pgpass):
        if t:
            try: os.unlink(t)
            except OSError: pass

    # Restore bundled secrets (Fernet key / TLS certs / pingwatch.conf) onto a
    # fresh box — conservative: never clobbers an existing key or conf.
    try:
        sec_actions = restore_secrets_from_zip(raw_bytes)
        if sec_actions:
            log.info("DB import (PG): secrets — " + "; ".join(sec_actions))
    except Exception as e:
        log.warning(f"DB import (PG): secret restore failed (non-fatal) — {e}")

    log.info("DB import (PG): complete — restarting…")
    h._json(200, {"ok": True, "msg": "PostgreSQL bundle imported — server is restarting…"})
    try:
        h.wfile.flush()
    except Exception:
        pass
    threading.Thread(target=_do_restart, args=(None, None), daemon=True).start()
    return True


def _resolve_export_passphrase(h) -> str:
    """Passphrase for a manual bundle export: request header wins, else the
    stored scheduled passphrase, else '' (caller emits a cleartext warning)."""
    pp = (h.headers.get("X-Bundle-Passphrase") or "").strip()
    if pp:
        return pp
    try:
        from core.settings import get as _cfg
        from db.backups   import decrypt_pw
        enc = _cfg("db_backup_passphrase_enc", "") or ""
        if enc:
            return decrypt_pw(enc) or ""
    except Exception:
        pass
    return ""


def _decrypt_if_needed(h, raw_bytes: bytes):
    """Unwrap a PWBK1 container to its inner ZIP using the X-Bundle-Passphrase
    header. Returns (inner_bytes, True) to continue, or (None, False) when a
    response has already been sent (missing/wrong passphrase, unavailable KDF)."""
    if not is_encrypted(raw_bytes):
        return raw_bytes, True
    pp = (h.headers.get("X-Bundle-Passphrase") or "").strip()
    if not pp:
        h._json(400, {"error": "This bundle is encrypted — a passphrase is required.",
                      "need_passphrase": True})
        return None, False
    try:
        return decrypt_container(raw_bytes, pp), True
    except ValueError as e:
        # Deliberately crafted, secret-free message (wrong passphrase / corrupt).
        h._json(400, {"error": str(e), "need_passphrase": True}); return None, False
    except RuntimeError as e:
        # KDF unavailable on this platform — actionable ops message, no secrets.
        h._json(400, {"error": str(e)}); return None, False
    except Exception as e:
        log.error(f"DB import: bundle decrypt error — {e}")
        h._json(400, {"error": "Bundle decryption failed — check server logs"}); return None, False


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
        _ver = app_state.APP_VERSION
        _ts  = time.strftime("%Y%m%d-%H%M%S")
        if is_pg():
            try:
                data  = _pg_dump_bytes('main')
                fname = f"pingwatch-main-v{_ver}-{_ts}.sql"
            except Exception as e:
                log.error(f"DB export (PG main): {e}")
                h._json(500, {"error": "Database export failed — check server logs"}); return True
        else:
            data  = _sqlite_backup_bytes(DB_PATH)
            fname = f"pingwatch-main-v{_ver}-{_ts}.db"
        _send_db(h, data, fname)
        return True

    # ── GET /api/db/export/logs — Logs DB ────────────────────────
    if _RE_DB_EXPORT_LOGS.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        db_log_audit(user, h.client_address[0], "db_export_logs")
        _ver = app_state.APP_VERSION
        _ts  = time.strftime("%Y%m%d-%H%M%S")
        if is_pg():
            try:
                data  = _pg_dump_bytes('logs')
                fname = f"pingwatch-logs-v{_ver}-{_ts}.sql"
            except Exception as e:
                log.error(f"DB export (PG logs): {e}")
                h._json(500, {"error": "Database export failed — check server logs"}); return True
        else:
            if not os.path.exists(LOGS_DB_PATH):
                h._json(404, {"error": "Logs DB does not exist yet"}); return True
            data  = _sqlite_backup_bytes(LOGS_DB_PATH)
            fname = f"pingwatch-logs-v{_ver}-{_ts}.db"
        _send_db(h, data, fname)
        return True

    # ── GET /api/db/export/bundle — full restorable bundle ───────
    # Both DBs + secrets (Fernet key, TLS certs, pingwatch.conf) + manifest.
    # Encrypted to a .pwbk container when a passphrase is available; otherwise
    # a plain .zip whose secrets are in the clear (logged loudly).
    if _RE_DB_EXPORT_BUNDLE.match(path) and method == "GET":
        user, _ = h._require("admin")
        if not user:
            return True
        db_log_audit(user, h.client_address[0], "db_export_bundle")
        try:
            pp = _resolve_export_passphrase(h)
            data, fname, encrypted = build_bundle(pp or None)
        except Exception as e:
            log.error(f"DB export bundle: {e}")
            h._json(500, {"error": "Database export failed — check server logs"}); return True
        if not encrypted:
            log.warning("DB export bundle: created WITHOUT a passphrase — it carries "
                        "the encryption key, TLS certs and pingwatch.conf in cleartext. "
                        "Store it securely or set a backup passphrase.")
        h.send_response(200)
        h.send_header("Content-Type", "application/octet-stream" if encrypted else "application/zip")
        h.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        h.send_header("X-Bundle-Encrypted", "1" if encrypted else "0")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
        return True

    # ── POST /api/db/import (PostgreSQL) ─────────────────────────
    if _RE_DB_IMPORT.match(path) and method == "POST" and is_pg():
        user, _ = h._require("admin")
        if not user:
            return True
        _MAX_IMPORT = 2 * 1024 * 1024 * 1024
        n = int(h.headers.get("Content-Length", 0))
        if n > _MAX_IMPORT:
            h._json(413, {"error": "File too large (max 2 GB)"}); return True
        if not n:
            h._json(400, {"error": "No data provided"}); return True
        content_type = h.headers.get("Content-Type", "")
        if "application/octet-stream" in content_type:
            raw_bytes = _read_body_spooled(h, n)
        else:
            try:
                body_imp = _json_mod.loads(h.rfile.read(n))
            except Exception:
                h._json(400, {"error": "Invalid JSON"}); return True
            raw_b64 = (body_imp.get("data") or "").strip()
            if not raw_b64:
                h._json(400, {"error": "No data provided"}); return True
            try:
                raw_bytes = base64.b64decode(raw_b64)
            except Exception:
                h._json(400, {"error": "Invalid base64 data"}); return True
        inner, _ok = _decrypt_if_needed(h, raw_bytes)
        if not _ok:
            return True
        if inner[:4] != b"PK\x03\x04":
            h._json(400, {"error": "PostgreSQL import requires a bundle (.zip or .pwbk) exported from PingWatch"}); return True
        db_log_audit(user, h.client_address[0], "db_import_pg")
        return _handle_pg_bundle_import(h, inner)

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
            raw_bytes = _read_body_spooled(h, n)
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

        # ── Encrypted bundle (.pwbk) — decrypt to the inner ZIP ───
        inner, _ok = _decrypt_if_needed(h, raw_bytes)
        if not _ok:
            return True
        raw_bytes = inner

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
            h._error(500, "Import failed", e, context="db_import"); return True

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

    # ── /api/log-badge GET ─────────────────────────────────────────
    if path == "/api/log-badge" and method == "GET":
        user, _ = h._require("operator")
        if not user:
            return True
        from core.logger import get_badge_total
        h._json(200, {"total": get_badge_total()})
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

        qs         = parse_qs(urlparse(h.path).query)
        f_level    = (qs.get("level",     [""])[0]).upper()
        f_minlvl   = (qs.get("min_level", [""])[0]).upper()
        f_after    = qs.get("after",  [""])[0]
        f_before   = qs.get("before", [""])[0]
        f_search   = qs.get("search", [""])[0].lower()
        f_limit    = min(int(qs.get("limit", ["2000"])[0] or 2000), 5000)

        # Multi-select level filter — exact-match set, e.g. levels=WARNING,ERROR.
        # WARN normalises to WARNING; selecting ERROR also matches CRITICAL (the
        # UI folds CRITICAL into the Error pill). Empty set = all levels.
        f_levels = set()
        for _lv in (qs.get("levels", [""])[0]).upper().split(","):
            _lv = "WARNING" if _lv.strip() == "WARN" else _lv.strip()
            if _lv:
                f_levels.add(_lv)
        if "ERROR" in f_levels:
            f_levels.add("CRITICAL")

        # Normalise WARN → WARNING for the minimum-level comparison.
        _LEVEL_RANK = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30,
                       "ERROR": 40, "CRITICAL": 50}
        f_minlvl_rank = _LEVEL_RANK.get(f_minlvl, 0)

        # Per-level tally over the time+search window, computed BEFORE the level
        # filter below — powers the faceted count badges so each level shows its
        # window total regardless of which levels are selected. CRITICAL folds
        # into ERROR to match the four-pill UI.
        counts = {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0}

        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as _lf:
                all_lines = _lf.readlines()
        except FileNotFoundError:
            all_lines = []

        total    = len(all_lines)
        filtered = []
        _pass    = False      # tracks whether last primary line passed

        for raw in all_lines:
            line = raw.rstrip("\n")
            if not line:
                continue
            ml = _LOG_LINE_RE.match(line)
            if not ml:
                # Continuation line — include if previous primary passed
                if _pass:
                    filtered.append(line)
                continue
            ts, lvl, msg = ml.group(1), ml.group(2), ml.group(3)
            _pass = False
            # Time + search gate first: these define the "window" the count
            # badges summarize.
            if f_after and ts <= f_after:
                continue
            if f_before and ts >= f_before:
                continue
            if f_search and f_search not in line.lower():
                continue
            # Tally per level for the window (before the level filter below).
            _cnt_lvl = ("WARNING" if lvl == "WARN" else
                        "ERROR"   if lvl == "CRITICAL" else lvl)
            if _cnt_lvl in counts:
                counts[_cnt_lvl] += 1
            # Level filter, applied last so it never skews the counts above.
            if f_levels and ("WARNING" if lvl == "WARN" else lvl) not in f_levels:
                continue
            # Legacy single-level params (kept for back-compat; the UI sends levels=).
            if f_level and lvl != f_level:
                continue
            if f_minlvl_rank and _LEVEL_RANK.get(lvl, 0) < f_minlvl_rank:
                continue
            _pass = True
            filtered.append(line)

        shown = filtered[-f_limit:]

        # File stats (size + rotation count). Failure-safe.
        import os as _os
        file_size = 0
        rotated_count = 0
        try:
            file_size = _os.path.getsize(fpath)
        except OSError:
            pass
        try:
            _dir = _os.path.dirname(fpath)
            _base = _os.path.basename(fpath)
            for _f in _os.listdir(_dir):
                if _f.startswith(_base + ".") and _f[len(_base)+1:].isdigit():
                    rotated_count += 1
        except OSError:
            pass

        h._json(200, {
            "log":      key,
            "lines":    "\n".join(shown),
            "total":    total,
            "filtered": len(filtered),
            "shown":    len(shown),
            "counts":         counts,
            "file_size":      file_size,
            "rotated_count":  rotated_count,
        })
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

    # Restore bundled secrets (Fernet key / TLS certs / pingwatch.conf) onto a
    # fresh box — conservative: never clobbers an existing key or conf.
    try:
        sec_actions = restore_secrets_from_zip(raw_bytes)
        if sec_actions:
            log.info("DB import: secrets — " + "; ".join(sec_actions))
    except Exception as e:
        log.warning(f"DB import: secret restore failed (non-fatal) — {e}")

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
