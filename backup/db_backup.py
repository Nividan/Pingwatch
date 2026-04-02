"""
backup/db_backup.py — Safe scheduled database backup with retention.

SQLite mode:
  Uses sqlite3's built-in .backup() API (WAL-safe snapshot).
  backup/database/pingwatch-main-YYYY-MM-DD_HH-MM-SS.sqlite
  backup/database/pingwatch-logs-YYYY-MM-DD_HH-MM-SS.sqlite

PostgreSQL mode:
  Uses pg_dump to create SQL dumps.
  backup/database/pingwatch-main-YYYY-MM-DD_HH-MM-SS.sql  (main schema)
  backup/database/pingwatch-logs-YYYY-MM-DD_HH-MM-SS.sql  (logs schema)
"""

import datetime
import os
import sqlite3
import subprocess
import threading

_running_lock = threading.Lock()


def _backup_one(src_path, dest_path, label, log):
    """
    Copy one SQLite DB to dest_path for a consistent WAL-safe snapshot.

    Mirrors routes/export.py: backup() into a pre-created temp file, then
    move it to the final path.  Connecting to a pre-existing file avoids the
    SQLite CANTOPEN error that occurs when SQLite tries to create a new file
    in a directory it cannot write to (e.g. owned by a different user).
    """
    import shutil as _sh, tempfile as _tmp
    src_str  = str(src_path)
    dest_str = str(dest_path)
    dest_dir = os.path.dirname(dest_str)

    # Prefer a temp file in the same directory so os.replace() is atomic.
    # Fall back to the system temp dir if dest_dir is not writable.
    try:
        fd, tmp = _tmp.mkstemp(dir=dest_dir, suffix='.sqlite.tmp')
        same_fs = True
    except OSError:
        fd, tmp = _tmp.mkstemp(suffix='.sqlite.tmp')
        same_fs = False
    os.close(fd)

    moved = False
    try:
        src = sqlite3.connect(src_str, timeout=30)
        try:
            with sqlite3.connect(tmp) as dst:
                src.backup(dst)
        finally:
            src.close()
        if same_fs:
            os.replace(tmp, dest_str)
        else:
            _sh.copy2(tmp, dest_str)
        moved = True
    finally:
        if not moved or not same_fs:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    size = os.path.getsize(dest_str)
    log.info(f"DB backup: {label} success — {os.path.basename(dest_str)} ({size:,} bytes)")
    return size


def _backup_pg_schema(cfg, schema, dest_path, label, log):
    """Run pg_dump for one schema and write to dest_path."""
    import shutil as _sh, tempfile as _tmp
    dest_str = str(dest_path)
    dest_dir = os.path.dirname(dest_str)

    try:
        fd, tmp = _tmp.mkstemp(dir=dest_dir, suffix='.sql.tmp')
        same_fs = True
    except OSError:
        fd, tmp = _tmp.mkstemp(suffix='.sql.tmp')
        same_fs = False
    os.close(fd)

    moved = False
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
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"pg_dump exited {result.returncode}")
        if same_fs:
            os.replace(tmp, dest_str)
        else:
            _sh.copy2(tmp, dest_str)
        moved = True
    finally:
        if not moved or not same_fs:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    size = os.path.getsize(dest_str)
    log.info(f"DB backup: {label} success — {os.path.basename(dest_str)} ({size:,} bytes)")
    return size


def do_db_backup() -> tuple:
    """
    Create timestamped snapshots of the database(s).
    Returns (ok: bool, message: str). Never raises.
    """
    if not _running_lock.acquire(blocking=False):
        return False, "Backup already in progress"
    try:
        from core.config import DB_PATH, LOGS_DB_PATH, DB_BACKUP_DIR
        from core.logger import log_backup as log
        from db.backend import is_pg, get_config

        os.makedirs(DB_BACKUP_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

        if is_pg():
            cfg = get_config()
            main_file = f"pingwatch-main-{ts}.sql"
            logs_file = f"pingwatch-logs-{ts}.sql"
            main_dest = os.path.join(DB_BACKUP_DIR, main_file)
            logs_dest = os.path.join(DB_BACKUP_DIR, logs_file)

            log.info(f"DB backup: starting PG — main schema → {main_dest}")
            _backup_pg_schema(cfg, 'main', main_dest, "Main (PG)", log)

            log.info(f"DB backup: starting PG — logs schema → {logs_dest}")
            _backup_pg_schema(cfg, 'logs', logs_dest, "Logs (PG)", log)
        else:
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
        for ext in ('.sqlite', '.sql'):
            try:
                files = sorted(
                    f for f in os.listdir(DB_BACKUP_DIR)
                    if f.startswith(prefix) and f.endswith(ext)
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
