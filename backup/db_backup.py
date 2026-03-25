"""
backup/db_backup.py — Safe scheduled SQLite database backup with retention.

Uses sqlite3's built-in .backup() API (same pattern as routes/export.py) for a
consistent snapshot that is safe to run while the DB is being written (WAL mode).

Backup files:
  backup/database/pingwatch-main-YYYY-MM-DD_HH-MM-SS.sqlite  (Main DB)
  backup/database/pingwatch-logs-YYYY-MM-DD_HH-MM-SS.sqlite  (Logs DB)
"""

import datetime
import os
import sqlite3
import threading

_running_lock = threading.Lock()


def _backup_one(src_path, dest_path, label, log):
    """
    Copy one SQLite DB to dest_path for a consistent WAL-safe snapshot.

    Primary:  VACUUM INTO (SQLite 3.27+) — single-connection, atomic, no temp dest file.
    Fallback: Connection.backup() API   — used on older SQLite (<3.27).
    """
    src_str  = str(src_path)
    dest_str = str(dest_path)
    con = sqlite3.connect(src_str, timeout=30)
    try:
        try:
            # Single-connection approach: SQLite writes directly to dest_str
            safe = dest_str.replace("'", "''")
            con.execute(f"VACUUM INTO '{safe}'")
        except sqlite3.OperationalError as _ve:
            if 'syntax error' not in str(_ve).lower():
                raise  # real error, not a missing-feature error
            # SQLite < 3.27 fallback
            log.debug("DB backup: VACUUM INTO unsupported — falling back to backup() API")
            dst = sqlite3.connect(dest_str, timeout=30)
            try:
                con.backup(dst)
            finally:
                dst.close()
    finally:
        con.close()
    size = os.path.getsize(dest_path)
    log.info(f"DB backup: {label} success — {os.path.basename(dest_path)} ({size:,} bytes)")
    return size


def do_db_backup() -> tuple:
    """
    Create timestamped snapshots of both the Main DB and the Logs DB.
    Returns (ok: bool, message: str). Never raises.
    Both DBs are backed up in the same call (atomic pair).
    """
    if not _running_lock.acquire(blocking=False):
        return False, "Backup already in progress"
    try:
        from core.config import DB_PATH, LOGS_DB_PATH, DB_BACKUP_DIR
        from core.logger import log_backup as log

        os.makedirs(DB_BACKUP_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

        main_file = f"pingwatch-main-{ts}.sqlite"
        logs_file = f"pingwatch-logs-{ts}.sqlite"
        main_dest = os.path.join(DB_BACKUP_DIR, main_file)
        logs_dest = os.path.join(DB_BACKUP_DIR, logs_file)

        log.info(f"DB backup: starting — Main → {main_dest}")
        _backup_one(DB_PATH, main_dest, "Main", log)

        # Logs DB may not exist yet on fresh installs — skip gracefully
        if os.path.exists(LOGS_DB_PATH):
            log.info(f"DB backup: starting — Logs → {logs_dest}")
            _backup_one(LOGS_DB_PATH, logs_dest, "Logs", log)
        else:
            log.info("DB backup: Logs DB not present — skipping logs backup")

        _enforce_db_retention(log)
        _record_result(ts, "ok")
        return True, f"Backup saved: {main_file}, {logs_file}"

    except Exception as e:
        try:
            from core.logger import log_backup as log
            log.error(f"DB backup: failed — {e}")
        except Exception:
            pass
        _record_result("", f"error: {e}")
        return False, str(e)
    finally:
        _running_lock.release()


def _enforce_db_retention(log):
    from core.config   import DB_BACKUP_DIR
    from core.settings import get as _cfg
    keep = max(1, int(_cfg('db_backup_keep', 7) or 7))

    for prefix in ('pingwatch-main-', 'pingwatch-logs-', 'pingwatch-db-'):
        try:
            files = sorted(
                f for f in os.listdir(DB_BACKUP_DIR)
                if f.startswith(prefix) and f.endswith('.sqlite')
            )
        except Exception:
            continue
        for fname in files[:-keep] if len(files) > keep else []:
            try:
                os.remove(os.path.join(DB_BACKUP_DIR, fname))
                log.info(f"DB backup: deleted old backup {fname}")
            except Exception as exc:
                log.warning(f"DB backup: could not delete {fname}: {exc}")


def _record_result(ts: str, result: str):
    """Persist last backup time and result to app_settings (best-effort)."""
    try:
        from core.settings import load as _sl
        from db import _db_enqueue, db_save_settings
        data = {'db_backup_last_ts': ts, 'db_backup_last_result': result}
        _sl(data)
        _db_enqueue(lambda d=data: db_save_settings(d))
    except Exception:
        pass
