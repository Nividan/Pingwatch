"""
backup/db_backup.py — Safe scheduled SQLite database backup with retention.

Uses sqlite3's built-in .backup() API (same pattern as routes/export.py) for a
consistent snapshot that is safe to run while the DB is being written (WAL mode).

Backup files: backup/database/pingwatch-db-YYYY-MM-DD_HH-MM-SS.sqlite
"""

import datetime
import os
import sqlite3
import threading

_running_lock = threading.Lock()


def do_db_backup() -> tuple:
    """
    Create a timestamped .sqlite snapshot of the live database.
    Returns (ok: bool, message: str). Never raises.
    """
    if not _running_lock.acquire(blocking=False):
        return False, "Backup already in progress"
    try:
        from core.config import DB_PATH, DB_BACKUP_DIR
        from core.logger import log_backup as log

        os.makedirs(DB_BACKUP_DIR, exist_ok=True)
        ts       = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"pingwatch-db-{ts}.sqlite"
        dest     = os.path.join(DB_BACKUP_DIR, filename)

        log.info(f"DB backup: starting \u2192 {dest}")
        src = sqlite3.connect(str(DB_PATH))
        try:
            with sqlite3.connect(dest) as dst:
                src.backup(dst)
        finally:
            src.close()

        size = os.path.getsize(dest)
        log.info(f"DB backup: success \u2014 {filename} ({size:,} bytes)")

        _enforce_db_retention(log)
        _record_result(ts, "ok")
        return True, f"Backup saved: {filename}"

    except Exception as e:
        try:
            from core.logger import log_backup as log
            log.error(f"DB backup: failed \u2014 {e}")
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
    try:
        files = sorted(
            f for f in os.listdir(DB_BACKUP_DIR)
            if f.startswith('pingwatch-db-') and f.endswith('.sqlite')
        )
    except Exception:
        return
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
