"""
db/backups.py — Backup device settings and run history CRUD.

Credentials (password, enable password) are Fernet-encrypted.
Plaintext is NEVER returned by any public function — callers receive
has_password / has_enable booleans instead.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from core.config import DB_PATH, SECRETS_DIR
from core.logger import log
from db.backend  import is_pg

# ── Fernet encryption ────────────────────────────────────────────────
_fernet_instance = None

_BACKUP_KEY_FILE = os.path.join(SECRETS_DIR, "backup_enc.key")


def _read_key_from_db() -> bytes | None:
    """Legacy path — read the key from app_settings (used only for migration)."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute("SELECT value FROM app_settings WHERE key='backup_enc_key'")
                row = cur.fetchone()
            return row["value"].encode() if row else None
        except Exception as e:
            log.warning(f"backup key DB read failed (PG): {e}")
            return None
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        try:
            row = con.execute(
                "SELECT value FROM app_settings WHERE key='backup_enc_key'"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None   # app_settings table not yet created (first-run edge case)
        return row[0].encode() if row else None
    finally:
        if con: con.close()


def _delete_key_from_db() -> None:
    """Drop the legacy app_settings.backup_enc_key row after migration."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute("DELETE FROM app_settings WHERE key='backup_enc_key'")
        except Exception as e:
            log.warning(f"backup key DB delete failed (PG): {e}")
        return
    con = None
    try:
        con = sqlite3.connect(DB_PATH, timeout=15)
        con.execute("DELETE FROM app_settings WHERE key='backup_enc_key'")
        con.commit()
    except Exception as e:
        log.warning(f"backup key DB delete failed (SQLite): {e}")
    finally:
        if con: con.close()


def _write_key_file(key: bytes) -> None:
    """Persist the Fernet key to SECRETS_DIR/backup_enc.key with 0600 perms.

    The parent dir is created with 0700. On Windows chmod is best-effort
    (Windows uses ACLs, not POSIX bits) — we rely on the fact that
    SECRETS_DIR is under the user profile, same trust model as REPORTS_DIR.
    """
    os.makedirs(SECRETS_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(SECRETS_DIR, 0o700)
    except Exception:
        pass   # best-effort on Windows
    # O_EXCL so we never silently clobber an existing key file.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(_BACKUP_KEY_FILE, flags, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    try:
        os.chmod(_BACKUP_KEY_FILE, 0o600)
    except Exception:
        pass


def _get_fernet():
    """Resolve the backup-credential Fernet key.

    Precedence (first hit wins):
      1. ``PW_BACKUP_ENC_KEY`` env var (raw Fernet key).
      2. Key file at SECRETS_DIR/backup_enc.key (chmod 0600).
      3. One-shot migration: if a legacy ``app_settings.backup_enc_key``
         row exists, copy it to the key file and DELETE the row — so the
         key no longer lives beside its ciphertext in DB dumps.
      4. Generate a fresh key and write it to the key file.

    The key is cached in ``_fernet_instance`` for the life of the process.
    """
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        log.error("cryptography package not installed — run: pip install cryptography")
        raise

    key: bytes | None = None

    # 1. Env var
    env_key = os.environ.get("PW_BACKUP_ENC_KEY", "").strip()
    if env_key:
        key = env_key.encode()
        log.info("Backup encryption key loaded from PW_BACKUP_ENC_KEY env var")

    # 2. Key file
    if key is None and os.path.isfile(_BACKUP_KEY_FILE):
        try:
            with open(_BACKUP_KEY_FILE, "rb") as f:
                key = f.read().strip()
            log.info(f"Backup encryption key loaded from {_BACKUP_KEY_FILE}")
        except Exception as e:
            log.error(f"Failed to read backup key file: {e}")
            raise

    # 3. Legacy migration from app_settings
    if key is None:
        legacy = _read_key_from_db()
        if legacy:
            key = legacy
            try:
                _write_key_file(key)
                _delete_key_from_db()
                log.info(
                    f"Migrated backup encryption key from app_settings to "
                    f"{_BACKUP_KEY_FILE} and removed DB row"
                )
            except FileExistsError:
                # Race: another worker wrote the file between our checks.
                # Trust the on-disk key; don't delete the DB row this time.
                log.warning("Key file appeared during migration; re-reading")
                with open(_BACKUP_KEY_FILE, "rb") as f:
                    key = f.read().strip()
            except Exception as e:
                log.error(
                    f"Key migration failed, leaving DB row in place: {e}"
                )
                # Fall through — we still have the key in hand, so decrypts
                # keep working this boot. Next boot will retry the migration.

    # 4. Generate fresh
    if key is None:
        key = Fernet.generate_key()
        try:
            _write_key_file(key)
            log.info(f"Generated fresh backup encryption key at {_BACKUP_KEY_FILE}")
        except Exception as e:
            log.critical(
                f"Could not persist backup encryption key to {_BACKUP_KEY_FILE}: "
                f"{e} — encrypted credentials saved this boot will be "
                f"unrecoverable on restart"
            )
            raise

    _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_pw(plaintext: str) -> str:
    """Encrypt a password string; returns '' for empty input."""
    if not plaintext:
        return ''
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_pw(ciphertext: str) -> str:
    """Decrypt a password string; returns '' for empty input."""
    if not ciphertext:
        return ''
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception as e:
        log.warning(f"backup decrypt_pw failed: {e}")
        return ''


# ── DB helpers ───────────────────────────────────────────────────────

def _con():
    return sqlite3.connect(DB_PATH, timeout=15)


# ── Public API ───────────────────────────────────────────────────────

def _build_14d_strip(rows, dids):
    """
    Reduce raw (did, ts_iso, success) rows from the last 14 days into a
    {did: [status_per_day, ...]} map where each list is exactly 14 entries
    in oldest→newest UTC-day order. Status per day:
        'ok'   — any successful run that day
        'fail' — only failures that day, no success
        'none' — no run that day
    """
    import datetime as _dt
    today = _dt.datetime.now(_dt.timezone.utc).date()
    days = [today - _dt.timedelta(days=i) for i in range(13, -1, -1)]
    # bucket[(did, date)] = 'ok' | 'fail'
    bucket = {}
    for did, ts_iso, success in rows:
        if not ts_iso:
            continue
        try:
            # ts column is ISO string; tolerate either Z or +HH:MM or naive
            s = ts_iso
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            d = _dt.datetime.fromisoformat(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            day = d.astimezone(_dt.timezone.utc).date()
        except Exception:
            continue
        key = (did, day)
        if success:
            bucket[key] = 'ok'  # any success wins
        elif key not in bucket:
            bucket[key] = 'fail'
    out = {}
    for did in dids:
        out[did] = [bucket.get((did, d), 'none') for d in days]
    return out


def db_get_backup_list() -> list:
    """
    Return list of all devices joined with their latest backup run metadata.
    Never includes decrypted passwords. Each entry also carries
    `last_diff_lines` (int or None) and `strip_14d` (14-entry list of
    'ok'/'fail'/'none' for the Backups page strip chart).
    """
    import datetime as _dt
    cutoff_iso = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=14)).isoformat()

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT did, enabled, method, port, username, password_enc, "
                    "enable_enc, commands, paging_cmd, timeout, "
                    "COALESCE(in_schedule, 0) AS in_schedule "
                    "FROM backup_devices"
                )
                cfg_map = {r["did"]: r for r in cur.fetchall()}

                cur.execute(
                    "SELECT did, id, ts, success, size_bytes, error_msg, diff_lines "
                    "FROM backup_runs "
                    "WHERE id IN (SELECT MAX(id) FROM backup_runs GROUP BY did)"
                )
                latest_map = {r["did"]: r for r in cur.fetchall()}

                cur.execute("SELECT did, COUNT(*) AS cnt FROM backup_runs GROUP BY did")
                count_map = {r["did"]: r["cnt"] for r in cur.fetchall()}

                cur.execute(
                    "SELECT did, ts, success FROM backup_runs WHERE ts >= %s",
                    (cutoff_iso,)
                )
                strip_rows = [(r["did"], r["ts"], bool(r["success"])) for r in cur.fetchall()]

            strip_map = _build_14d_strip(strip_rows, cfg_map.keys())

            result = []
            for did, cfg in cfg_map.items():
                lr = latest_map.get(did)
                result.append({
                    "did": did,
                    "enabled": bool(cfg["enabled"]),
                    "method": cfg["method"],
                    "port": cfg["port"],
                    "username": cfg["username"],
                    "has_password": bool(cfg["password_enc"]),
                    "has_enable": bool(cfg["enable_enc"]),
                    "commands": _parse_cmds(cfg["commands"]),
                    "paging_cmd": cfg["paging_cmd"],
                    "timeout": cfg["timeout"],
                    "in_schedule": bool(cfg["in_schedule"]),
                    "last_run_id": lr["id"] if lr else None,
                    "last_ts": lr["ts"] if lr else None,
                    "last_success": bool(lr["success"]) if lr else None,
                    "last_size": lr["size_bytes"] if lr else None,
                    "last_error": lr["error_msg"] if lr else None,
                    "last_diff_lines": (lr["diff_lines"] if lr else None),
                    "run_count": count_map.get(did, 0),
                    "strip_14d": strip_map.get(did, ['none']*14),
                })
            return result
        except Exception as e:
            log.error(f"backup get_list error: {e}")
            return []

    con = _con()
    try:
        rows = con.execute(
            "SELECT did, enabled, method, port, username, password_enc, "
            "enable_enc, commands, paging_cmd, timeout, "
            "COALESCE(in_schedule, 0) "
            "FROM backup_devices"
        ).fetchall()
        cfg_map = {r[0]: r for r in rows}

        latest = con.execute(
            "SELECT did, id, ts, success, size_bytes, error_msg, diff_lines "
            "FROM backup_runs "
            "WHERE id IN (SELECT MAX(id) FROM backup_runs GROUP BY did)"
        ).fetchall()
        latest_map = {r[0]: r for r in latest}

        counts = con.execute(
            "SELECT did, COUNT(*) FROM backup_runs GROUP BY did"
        ).fetchall()
        count_map = {r[0]: r[1] for r in counts}

        strip_rows = con.execute(
            "SELECT did, ts, success FROM backup_runs WHERE ts >= ?",
            (cutoff_iso,)
        ).fetchall()
        strip_map = _build_14d_strip(
            [(r[0], r[1], bool(r[2])) for r in strip_rows],
            cfg_map.keys()
        )

        result = []
        for did, cfg in cfg_map.items():
            _, enabled, method, port, username, password_enc, enable_enc, \
                commands, paging_cmd, timeout, in_schedule = cfg
            lr = latest_map.get(did)
            result.append({
                "did": did,
                "enabled": bool(enabled),
                "method": method,
                "port": port,
                "username": username,
                "has_password": bool(password_enc),
                "has_enable": bool(enable_enc),
                "commands": _parse_cmds(commands),
                "paging_cmd": paging_cmd,
                "timeout": timeout,
                "in_schedule": bool(in_schedule),
                "last_run_id": lr[1] if lr else None,
                "last_ts": lr[2] if lr else None,
                "last_success": bool(lr[3]) if lr else None,
                "last_size": lr[4] if lr else None,
                "last_error": lr[5] if lr else None,
                "last_diff_lines": (lr[6] if lr else None),
                "run_count": count_map.get(did, 0),
                "strip_14d": strip_map.get(did, ['none']*14),
            })
        return result
    finally:
        con.close()


def db_get_backup_settings(did: str, *, with_secrets: bool = False) -> dict | None:
    """Return settings for one device.

    By default (with_secrets=False) the dict never contains decrypted
    passwords — only has_password / has_enable boolean flags — safe to
    send to the API / frontend.

    Pass with_secrets=True (internal use only, e.g. backup_engine) to
    also include the raw password_enc / enable_enc ciphertext fields so
    the engine can decrypt them.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT did, enabled, method, port, username, password_enc, "
                    "enable_enc, commands, paging_cmd, timeout, "
                    "COALESCE(in_schedule, 0) AS in_schedule, "
                    "COALESCE(expected_content, '') AS expected_content, "
                    "COALESCE(expected_is_regex, 0) AS expected_is_regex, "
                    "COALESCE(min_bytes, 0) AS min_bytes "
                    "FROM backup_devices WHERE did=%s", (did,)
                )
                r = cur.fetchone()
            if not r:
                return None
            result = {
                "did": r["did"], "enabled": bool(r["enabled"]), "method": r["method"],
                "port": r["port"], "username": r["username"],
                "has_password": bool(r["password_enc"]), "has_enable": bool(r["enable_enc"]),
                "commands": _parse_cmds(r["commands"]),
                "paging_cmd": r["paging_cmd"], "timeout": r["timeout"],
                "in_schedule": bool(r["in_schedule"]),
                "expected_content": r["expected_content"] or '',
                "expected_is_regex": bool(r["expected_is_regex"]),
                "min_bytes": int(r["min_bytes"] or 0),
            }
            if with_secrets:
                result["password_enc"] = r["password_enc"] or ''
                result["enable_enc"]   = r["enable_enc"] or ''
            return result
        except Exception as e:
            log.error(f"backup get_settings error (did={did}): {e}")
            return None

    con = _con()
    try:
        r = con.execute(
            "SELECT did, enabled, method, port, username, password_enc, "
            "enable_enc, commands, paging_cmd, timeout, "
            "COALESCE(in_schedule, 0), "
            "COALESCE(expected_content, ''), "
            "COALESCE(expected_is_regex, 0), "
            "COALESCE(min_bytes, 0) "
            "FROM backup_devices WHERE did=?", (did,)
        ).fetchone()
        if not r:
            return None
        result = {
            "did": r[0], "enabled": bool(r[1]), "method": r[2],
            "port": r[3], "username": r[4],
            "has_password": bool(r[5]), "has_enable": bool(r[6]),
            "commands": _parse_cmds(r[7]),
            "paging_cmd": r[8], "timeout": r[9],
            "in_schedule": bool(r[10]),
            "expected_content": r[11] or '',
            "expected_is_regex": bool(r[12]),
            "min_bytes": int(r[13] or 0),
        }
        if with_secrets:
            result["password_enc"] = r[5] or ''
            result["enable_enc"]   = r[6] or ''
        return result
    finally:
        con.close()


def db_save_backup_settings(did: str, data: dict):
    """
    UPSERT backup settings for a device.
    Pass password='' to keep the existing encrypted value.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT password_enc, enable_enc FROM backup_devices WHERE did=%s", (did,)
                )
                existing = cur.fetchone()
            old_pw = existing["password_enc"] if existing else ''
            old_en = existing["enable_enc"] if existing else ''

            new_pw_plain = data.get('password', '')
            new_en_plain = data.get('enable_password', '')
            password_enc = encrypt_pw(new_pw_plain) if new_pw_plain else old_pw
            enable_enc   = encrypt_pw(new_en_plain) if new_en_plain else old_en

            commands_json = json.dumps(
                data.get('commands', ['show running-config'])
                if isinstance(data.get('commands'), list)
                else [data.get('commands', 'show running-config')]
            )
            with pg_cursor('main') as cur:
                cur.execute("""
                    INSERT INTO backup_devices
                        (did, enabled, method, port, username, password_enc, enable_enc,
                         commands, paging_cmd, timeout, in_schedule,
                         expected_content, expected_is_regex, min_bytes)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(did) DO UPDATE SET
                        enabled=EXCLUDED.enabled, method=EXCLUDED.method, port=EXCLUDED.port,
                        username=EXCLUDED.username, password_enc=EXCLUDED.password_enc,
                        enable_enc=EXCLUDED.enable_enc, commands=EXCLUDED.commands,
                        paging_cmd=EXCLUDED.paging_cmd, timeout=EXCLUDED.timeout,
                        in_schedule=EXCLUDED.in_schedule,
                        expected_content=EXCLUDED.expected_content,
                        expected_is_regex=EXCLUDED.expected_is_regex,
                        min_bytes=EXCLUDED.min_bytes
                """, (
                    did,
                    1 if data.get('enabled') else 0,
                    data.get('method', 'ssh'),
                    int(data.get('port', 22)),
                    data.get('username', ''),
                    password_enc, enable_enc,
                    commands_json,
                    data.get('paging_cmd', ''),
                    int(data.get('timeout', 30)),
                    1 if data.get('in_schedule') else 0,
                    (data.get('expected_content') or '')[:200],
                    1 if data.get('expected_is_regex') else 0,
                    int(data.get('min_bytes', 0) or 0),
                ))
        except Exception as e:
            log.error(f"backup save_settings error (did={did}): {e}")
            raise
        return

    con = _con()
    try:
        existing = con.execute(
            "SELECT password_enc, enable_enc FROM backup_devices WHERE did=?", (did,)
        ).fetchone()

        old_pw  = existing[0] if existing else ''
        old_en  = existing[1] if existing else ''

        new_pw_plain  = data.get('password', '')
        new_en_plain  = data.get('enable_password', '')
        password_enc  = encrypt_pw(new_pw_plain) if new_pw_plain else old_pw
        enable_enc    = encrypt_pw(new_en_plain) if new_en_plain else old_en

        commands_json = json.dumps(
            data.get('commands', ['show running-config'])
            if isinstance(data.get('commands'), list)
            else [data.get('commands', 'show running-config')]
        )

        con.execute("""
            INSERT INTO backup_devices
                (did, enabled, method, port, username, password_enc, enable_enc,
                 commands, paging_cmd, timeout, in_schedule,
                 expected_content, expected_is_regex, min_bytes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(did) DO UPDATE SET
                enabled=excluded.enabled, method=excluded.method, port=excluded.port,
                username=excluded.username, password_enc=excluded.password_enc,
                enable_enc=excluded.enable_enc, commands=excluded.commands,
                paging_cmd=excluded.paging_cmd, timeout=excluded.timeout,
                in_schedule=excluded.in_schedule,
                expected_content=excluded.expected_content,
                expected_is_regex=excluded.expected_is_regex,
                min_bytes=excluded.min_bytes
        """, (
            did,
            1 if data.get('enabled') else 0,
            data.get('method', 'ssh'),
            int(data.get('port', 22)),
            data.get('username', ''),
            password_enc,
            enable_enc,
            commands_json,
            data.get('paging_cmd', ''),
            int(data.get('timeout', 30)),
            1 if data.get('in_schedule') else 0,
            (data.get('expected_content') or '')[:200],
            1 if data.get('expected_is_regex') else 0,
            int(data.get('min_bytes', 0) or 0),
        ))
        con.commit()
    finally:
        con.close()


def db_get_backup_history(did: str) -> list:
    """Return list of backup run metadata (no config text) for one device."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT id, ts, success, method, size_bytes, sha256, error_msg "
                    "FROM backup_runs WHERE did=%s ORDER BY ts DESC",
                    (did,)
                )
                return [
                    {"id": r["id"], "ts": r["ts"], "success": bool(r["success"]),
                     "method": r["method"], "size_bytes": r["size_bytes"],
                     "sha256": r["sha256"], "error_msg": r["error_msg"]}
                    for r in cur.fetchall()
                ]
        except Exception as e:
            log.error(f"backup get_history error (did={did}): {e}")
            return []

    con = _con()
    try:
        rows = con.execute(
            "SELECT id, ts, success, method, size_bytes, sha256, error_msg "
            "FROM backup_runs WHERE did=? ORDER BY ts DESC",
            (did,)
        ).fetchall()
        return [
            {"id": r[0], "ts": r[1], "success": bool(r[2]), "method": r[3],
             "size_bytes": r[4], "sha256": r[5], "error_msg": r[6]}
            for r in rows
        ]
    finally:
        con.close()


def db_get_backup_run(run_id: int) -> dict | None:
    """Return full backup run including config text."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT id, did, ts, success, method, size_bytes, sha256, config, error_msg "
                    "FROM backup_runs WHERE id=%s", (run_id,)
                )
                r = cur.fetchone()
            if not r:
                return None
            return {
                "id": r["id"], "did": r["did"], "ts": r["ts"], "success": bool(r["success"]),
                "method": r["method"], "size_bytes": r["size_bytes"], "sha256": r["sha256"],
                "config": r["config"], "error_msg": r["error_msg"],
            }
        except Exception as e:
            log.error(f"backup get_run error (id={run_id}): {e}")
            return None

    con = _con()
    try:
        r = con.execute(
            "SELECT id, did, ts, success, method, size_bytes, sha256, config, error_msg "
            "FROM backup_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not r:
            return None
        return {
            "id": r[0], "did": r[1], "ts": r[2], "success": bool(r[3]),
            "method": r[4], "size_bytes": r[5], "sha256": r[6],
            "config": r[7], "error_msg": r[8],
        }
    finally:
        con.close()


def db_get_last_successful_config(did: str) -> str | None:
    """
    Return the `config` text of the most recent successful backup for `did`,
    or None when no successful run exists yet. Used by the engine to compute
    the per-run `diff_lines` count without re-fetching device output.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT config FROM backup_runs "
                    "WHERE did=%s AND success=1 "
                    "ORDER BY ts DESC LIMIT 1",
                    (did,)
                )
                r = cur.fetchone()
            return r["config"] if r else None
        except Exception as e:
            log.error(f"backup get_last_successful_config error (did={did}): {e}")
            return None

    con = _con()
    try:
        r = con.execute(
            "SELECT config FROM backup_runs "
            "WHERE did=? AND success=1 ORDER BY ts DESC LIMIT 1",
            (did,)
        ).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def db_save_backup_run(did: str, result: dict) -> int:
    """
    INSERT a backup run result. Enforces per-device retention based on
    the global 'backup_keep' setting (default 3).
    Returns the new run's id.

    Optional `diff_lines` key in `result` stores the number of lines that
    changed vs the previous successful run. When omitted, the column stays
    NULL (legacy callers + first backups + failed runs).
    """
    from core.settings import get as _cfg
    keep = max(1, int(_cfg('backup_keep', 3)))
    diff_lines = result.get('diff_lines')  # None or int

    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "INSERT INTO backup_runs (did, ts, success, method, size_bytes, sha256, config, error_msg, diff_lines) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        did,
                        result.get('ts', ''),
                        1 if result.get('success') else 0,
                        result.get('method', ''),
                        result.get('size_bytes', 0),
                        result.get('sha256', ''),
                        result.get('config', ''),
                        result.get('error_msg', ''),
                        diff_lines,
                    )
                )
                new_id = cur.fetchone()["id"]
                # Enforce configurable retention
                cur.execute(
                    "DELETE FROM backup_runs WHERE did=%s AND id NOT IN "
                    "(SELECT id FROM backup_runs WHERE did=%s ORDER BY ts DESC LIMIT %s)",
                    (did, did, keep)
                )
            return new_id
        except Exception as e:
            log.error(f"backup save_run error (did={did}): {e}")
            raise

    con = _con()
    try:
        cur = con.execute(
            "INSERT INTO backup_runs (did, ts, success, method, size_bytes, sha256, config, error_msg, diff_lines) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                did,
                result.get('ts', ''),
                1 if result.get('success') else 0,
                result.get('method', ''),
                result.get('size_bytes', 0),
                result.get('sha256', ''),
                result.get('config', ''),
                result.get('error_msg', ''),
                diff_lines,
            )
        )
        new_id = cur.lastrowid
        # Enforce configurable retention
        con.execute(
            "DELETE FROM backup_runs WHERE did=? AND id NOT IN "
            "(SELECT id FROM backup_runs WHERE did=? ORDER BY ts DESC LIMIT ?)",
            (did, did, keep)
        )
        con.commit()
        return new_id
    finally:
        con.close()


def db_write_config_file(did: str, device_name: str, ts_str: str, config_text: str):
    """
    Write config to configs/{device_name}/config_{ts}.txt and prune old files
    to match the backup_keep retention setting.
    """
    from core.config import CONFIGS_DIR
    from core.settings import get as _cfg

    safe_name = re.sub(r'[^\w\-]', '_', device_name) or did
    dev_dir = os.path.join(CONFIGS_DIR, safe_name)
    os.makedirs(dev_dir, exist_ok=True)

    # config_2026-03-14_02-00.txt
    ts_file = ts_str[:16].replace('T', '_').replace(':', '-')
    fpath = os.path.join(dev_dir, f'config_{ts_file}.txt')
    try:
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(config_text)
    except OSError as e:
        log.warning(f"backup: could not write config file {fpath}: {e}")
        return

    # Enforce file-level retention
    keep = max(1, int(_cfg('backup_keep', 3)))
    try:
        files = sorted(
            Path(dev_dir).glob('config_*.txt'),
            key=lambda p: p.stat().st_mtime
        )
        while len(files) > keep:
            files.pop(0).unlink(missing_ok=True)
    except OSError as e:
        log.warning(f"backup: could not prune config files in {dev_dir}: {e}")


def db_delete_backup_run(run_id: int):
    """Delete a specific backup run."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute("DELETE FROM backup_runs WHERE id=%s", (run_id,))
        except Exception as e:
            log.error(f"backup delete_run error (id={run_id}): {e}")
        return

    con = _con()
    try:
        con.execute("DELETE FROM backup_runs WHERE id=?", (run_id,))
        con.commit()
    finally:
        con.close()


def db_search_configs(q: str, limit: int = 50) -> list:
    """
    Full-text search inside successful backup configs.
    Returns up to *limit* matches as [{run_id, did, ts, line_no, line_text}].
    Scans the most recent 200 successful runs to keep response times bounded.
    """
    if is_pg():
        from db.pg_pool import pg_cursor
        rows = []
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "SELECT id, did, ts, config FROM backup_runs "
                    "WHERE success=1 AND config LIKE %s ORDER BY ts DESC LIMIT 200",
                    (f'%{q}%',)
                )
                ql = q.lower()
                for r in cur.fetchall():
                    for i, line in enumerate((r["config"] or '').splitlines(), 1):
                        if ql in line.lower():
                            rows.append({
                                'run_id':    r["id"],
                                'did':       r["did"],
                                'ts':        r["ts"],
                                'line_no':   i,
                                'line_text': line.strip(),
                            })
                            if len(rows) >= limit:
                                break
                    if len(rows) >= limit:
                        break
        except Exception as e:
            log.error(f"backup search_configs error: {e}")
        return rows

    con = _con()
    rows = []
    try:
        cur = con.execute(
            "SELECT id, did, ts, config FROM backup_runs "
            "WHERE success=1 AND config LIKE ? ORDER BY ts DESC LIMIT 200",
            (f'%{q}%',)
        )
        ql = q.lower()
        for run_id, did, ts, config in cur:
            for i, line in enumerate(config.splitlines(), 1):
                if ql in line.lower():
                    rows.append({
                        'run_id':    run_id,
                        'did':       did,
                        'ts':        ts,
                        'line_no':   i,
                        'line_text': line.strip(),
                    })
                    if len(rows) >= limit:
                        break
            if len(rows) >= limit:
                break
    finally:
        con.close()
    return rows


def db_ensure_backup_device(did: str):
    """Ensure a backup_devices row exists for a device (with defaults)."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor('main') as cur:
                cur.execute(
                    "INSERT INTO backup_devices(did) VALUES(%s) ON CONFLICT(did) DO NOTHING",
                    (did,)
                )
        except Exception as e:
            log.error(f"backup ensure_device error (did={did}): {e}")
        return

    con = _con()
    try:
        con.execute(
            "INSERT OR IGNORE INTO backup_devices(did) VALUES(?)", (did,)
        )
        con.commit()
    finally:
        con.close()


# ── Private helpers ──────────────────────────────────────────────────

def _parse_cmds(raw: str) -> list:
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else [str(v)]
    except Exception:
        return [raw] if raw else ['show running-config']
