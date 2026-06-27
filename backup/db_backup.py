"""
backup/db_backup.py — Safe scheduled database backup with retention.

Each run writes ONE self-contained, importable bundle to backup/database/:

  backup/database/pingwatch-bundle-YYYY-MM-DD_HH-MM-SS.pwbk   (encrypted)
  backup/database/pingwatch-bundle-YYYY-MM-DD_HH-MM-SS.zip    (no passphrase)

The bundle carries both databases (SQLite snapshot / pg_dump per schema) plus
the secrets needed for a full restore on a fresh server — the Fernet key, TLS
certs, and pingwatch.conf — so worst-case recovery is "import this file in the
UI", not a sequence of psql commands. When a backup passphrase is configured
(Settings → Database) the whole bundle is AEAD-encrypted (Argon2id/Scrypt +
AES-256-GCM); without one it's a plain ZIP and we log loudly, because it then
contains those secrets in cleartext. Format lives in core/backup_bundle.py,
shared with the manual export route so the two never diverge.
"""

import datetime
import getpass
import os
import subprocess
import tempfile
import threading

_running_lock = threading.Lock()


def _check_backup_dir_writable(path: str) -> None:
    """Verify the backup directory exists and is writable by the current process.

    Raises PermissionError with an actionable message if not. ``os.makedirs(...,
    exist_ok=True)`` does NOT detect this case — it returns silently if the
    directory already exists, even when the existing owner is different from
    the current user. The actual permission failure then surfaces deep inside
    pg_dump or shutil.copy2 with a confusing path-only error like
    ``[Errno 13] Permission denied: '/home/nive/Pingwatch/backup/database/...'``.

    Catching it here lets us point operators directly at the fix.
    """
    if not os.path.isdir(path):
        # makedirs raised something we didn't catch — re-raise with context
        raise PermissionError(f"Backup directory does not exist and could not be created: {path}")
    if not os.access(path, os.W_OK | os.X_OK):
        try:
            st = os.stat(path)
            owner_uid = st.st_uid
            try:
                import pwd
                owner_name = pwd.getpwuid(owner_uid).pw_name
            except Exception:
                owner_name = str(owner_uid)
        except Exception:
            owner_name = "unknown"
        try:
            current_user = getpass.getuser()
        except Exception:
            current_user = f"uid={os.getuid() if hasattr(os, 'getuid') else '?'}"
        raise PermissionError(
            f"Backup directory not writable by service user "
            f"'{current_user}' (owned by '{owner_name}'): {path} — "
            f"fix with: sudo chown -R {current_user} {path}"
        )
    # The os.access check is advisory on some filesystems (NFS, ACLs); confirm
    # by attempting a real write — catches edge cases where access() lies.
    try:
        fd, tmp = tempfile.mkstemp(dir=path, prefix='.writetest-', suffix='.tmp')
        os.close(fd)
        os.unlink(tmp)
    except OSError as e:
        raise PermissionError(
            f"Backup directory probe-write failed at {path}: {e} — "
            f"check ownership and ACLs"
        )


def _backup_pg_schema(cfg, schema, dest_path, label, log):
    """Run pg_dump for one schema and write to dest_path.

    Retained as a stable helper: the managed-upgrade snapshotter
    (core/upgrade.create_snapshot) calls this to dump the pre-upgrade PG state
    to a file. Keep the (cfg, schema, dest_path, label, log) signature."""
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
    pgpass = None
    try:
        from db.backend import pg_env as _pg_env
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
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"pg_dump exited {result.returncode}")
        if same_fs:
            os.replace(tmp, dest_str)
        else:
            _sh.copy2(tmp, dest_str)
        moved = True
    finally:
        if pgpass:
            try:
                os.unlink(pgpass)
            except OSError:
                pass
        if not moved or not same_fs:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    size = os.path.getsize(dest_str)
    log.info(f"DB backup: {label} success — {os.path.basename(dest_str)} ({size:,} bytes)")
    return size


def _write_atomic(dest_path: str, data: bytes) -> None:
    """Write bytes to dest_path atomically (temp in same dir → os.replace).

    Falls back to the system temp dir + copy when the backup dir can't host the
    temp file, matching the resilience of the previous per-file writers."""
    import shutil as _sh
    dest_dir = os.path.dirname(dest_path)
    try:
        fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix='.tmp')
        same_fs = True
    except OSError:
        fd, tmp = tempfile.mkstemp(suffix='.tmp')
        same_fs = False
    os.close(fd)
    moved = False
    try:
        with open(tmp, 'wb') as f:
            f.write(data)
        if same_fs:
            os.replace(tmp, dest_path)
        else:
            _sh.copy2(tmp, dest_path)
        moved = True
    finally:
        if not moved or not same_fs:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _resolve_backup_passphrase(log) -> str:
    """Read the configured backup passphrase (Fernet-decrypted), or '' if unset."""
    try:
        from core.settings import get as _cfg
        from db.backups   import decrypt_pw
        enc = _cfg('db_backup_passphrase_enc', '') or ''
        return (decrypt_pw(enc) or '') if enc else ''
    except Exception as e:
        log.warning(f"DB backup: could not read backup passphrase — {e}")
        return ''


def do_db_backup() -> tuple:
    """
    Write one timestamped, importable bundle of the database(s) + secrets.
    Returns (ok: bool, message: str). Never raises.
    """
    if not _running_lock.acquire(blocking=False):
        return False, "Backup already in progress"
    try:
        from core.config import DB_BACKUP_DIR
        from core.logger import log_backup as log
        from core.backup_bundle import build_bundle

        os.makedirs(DB_BACKUP_DIR, exist_ok=True)
        # Pre-flight: verify writability NOW (with a clear remediation hint)
        # rather than letting the write surface a confusing bare PermissionError.
        _check_backup_dir_writable(DB_BACKUP_DIR)
        ts = datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

        passphrase = _resolve_backup_passphrase(log)
        log.info("DB backup: building bundle…")
        data, _name, encrypted = build_bundle(passphrase or None)
        if not encrypted:
            log.warning("DB backup: writing an UNENCRYPTED bundle — it contains the "
                        "encryption key, TLS certs and pingwatch.conf in cleartext. "
                        "Set a backup passphrase in Settings → Database to protect it.")

        # Name with the sortable timestamp FIRST so retention's lexical sort is
        # chronological regardless of the app version embedded by build_bundle.
        ext = "pwbk" if encrypted else "zip"
        fname = f"pingwatch-bundle-{ts}.{ext}"
        dest = os.path.join(DB_BACKUP_DIR, fname)
        _write_atomic(dest, data)
        size = os.path.getsize(dest)
        log.info(f"DB backup: bundle saved — {fname} "
                 f"({size:,} bytes, {'encrypted' if encrypted else 'PLAINTEXT'})")

        written = [dest]
        _remote_upload_if_enabled(written, ts, log)
        _enforce_db_retention(log)
        _record_result(ts, "ok")
        return True, f"Backup saved: {fname}"

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

    # 'pingwatch-bundle-' is the current artifact; the legacy per-schema prefixes
    # are still swept so pre-bundle backups age out under the same retention.
    for prefix in ('pingwatch-bundle-', 'pingwatch-main-', 'pingwatch-logs-', 'pingwatch-db-'):
        for ext in ('.pwbk', '.zip', '.sqlite', '.sql'):
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
    """Persist last backup time and result to app_settings (best-effort).

    Only overwrites db_backup_last_ts when ts is non-empty — this preserves the
    scheduler's catch-up marker on error paths (empty ts means "don't touch").
    """
    try:
        from core.settings import load as _sl
        from db import _db_enqueue, db_save_settings
        data = {'db_backup_last_result': result}
        if ts:
            data['db_backup_last_ts'] = ts
        _sl(data)
        _db_enqueue(lambda d=data: db_save_settings(d))
    except Exception:
        pass


def _remote_upload_if_enabled(local_paths: list, ts: str, log) -> None:
    """Push local backups to the configured remote destination. Non-fatal."""
    try:
        from core.settings import get as _cfg
        if not int(_cfg('db_backup_remote_enabled', 0) or 0):
            return
        if not local_paths:
            _record_remote_result("", "error: no files to upload")
            return
        from .remote_upload import do_remote_upload
        ok, msg = do_remote_upload(local_paths)
        if ok:
            log.info(f"DB backup: remote upload OK — {msg}")
            _record_remote_result(ts, "ok")
        else:
            log.warning(f"DB backup: remote upload failed — {msg}")
            _record_remote_result("", f"error: {msg}")
    except Exception as e:
        log.error(f"DB backup: remote upload crashed — {e}", exc_info=True)
        _record_remote_result("", "error: remote upload crashed")


def _record_remote_result(ts: str, result: str):
    """Persist remote-upload last time and result (best-effort)."""
    try:
        from core.settings import load as _sl
        from db import _db_enqueue, db_save_settings
        data = {'db_backup_remote_last_result': result}
        if ts:
            data['db_backup_remote_last_ts'] = ts
        _sl(data)
        _db_enqueue(lambda d=data: db_save_settings(d))
    except Exception:
        pass
