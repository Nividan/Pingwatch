"""
db/core.py — Single-writer queue, schema init, and user seeding.
"""

import queue
import sqlite3
import threading
import time

from auth   import _hash_pw, _SESSIONS, _SESSIONS_LOCK
from config import DB_PATH
from logger import log

# ── Single-writer queue ───────────────────────────────────────────
_DB_QUEUE: queue.Queue = queue.Queue()


def _db_writer_loop():
    """Drain _DB_QUEUE and execute each callable sequentially."""
    while True:
        fn = _DB_QUEUE.get()
        if fn is None:   # sentinel for clean shutdown (not yet used)
            break
        try:
            fn()
        except Exception as e:
            log.error(f"DB writer error: {e}")


threading.Thread(target=_db_writer_loop, daemon=True).start()


def _db_enqueue(fn):
    """Queue a zero-argument callable for the single DB writer thread."""
    _DB_QUEUE.put(fn)


# ── Schema init ──────────────────────────────────────────────────

def db_init():
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads while probes write
        con.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                pw_hash  TEXT NOT NULL,
                role     TEXT DEFAULT 'admin'
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token    TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                expires  REAL NOT NULL
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                did TEXT PRIMARY KEY, name TEXT, host TEXT,
                grp TEXT, did_ctr INTEGER DEFAULT 0
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensors (
                did TEXT, sid TEXT, name TEXT, stype TEXT,
                host TEXT, port INTEGER, url TEXT,
                interval INTEGER, timeout INTEGER,
                verify_ssl INTEGER DEFAULT 1,
                snmp_community TEXT DEFAULT 'public',
                snmp_oid TEXT DEFAULT '1.3.6.1.2.1.1.1.0',
                snmp_version TEXT DEFAULT '2c',
                sid_ctr INTEGER DEFAULT 0,
                dns_query TEXT DEFAULT '',
                dns_record_type TEXT DEFAULT 'A',
                dns_server TEXT DEFAULT '',
                PRIMARY KEY (did, sid)
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS flap_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT,
                did       TEXT,
                sid       TEXT,
                dname     TEXT,
                sname     TEXT,
                host      TEXT,
                stype     TEXT,
                detail    TEXT,
                direction TEXT DEFAULT 'down'
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_err_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      TEXT,
                did     TEXT,
                sid     TEXT,
                sname   TEXT,
                stype   TEXT,
                msg     TEXT
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_samples (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    REAL    NOT NULL,
                did   TEXT    NOT NULL,
                sid   TEXT    NOT NULL,
                ok    INTEGER NOT NULL,
                ms    REAL,
                value TEXT
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds "
            "ON sensor_samples(did, sid, ts)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS snmp_traps (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT,
                src_ip    TEXT,
                dname     TEXT,
                community TEXT,
                trap_oid  TEXT,
                detail    TEXT
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     REAL    NOT NULL,
                actor  TEXT    NOT NULL,
                ip     TEXT    NOT NULL,
                action TEXT    NOT NULL,
                target TEXT    DEFAULT '',
                detail TEXT    DEFAULT ''
            )""")
        con.commit()
        # Seed defaults in app_settings if not present
        for _k, _v in [
            ("session_ttl",        "86400"),
            ("retention_days",     "365"),
            ("snr_interval",       "5"),
            ("snr_timeout",        "4"),
            ("snr_fail_after",     "1"),
            ("snr_recover_after",  "1"),
            ("max_flaps_display",  "20"),
            ("max_flap_entries",   "500"),
            ("max_trap_entries",   "500"),
            ("login_fail_max",     "5"),
            ("login_fail_window",  "60"),
            ("org_name",           ""),
            ("latency_good_ms",    "100"),
            ("latency_warn_ms",    "300"),
        ]:
            if not con.execute("SELECT 1 FROM app_settings WHERE key=?", (_k,)).fetchone():
                con.execute("INSERT INTO app_settings VALUES (?,?)", (_k, _v))
        con.commit()
        # Migrate: bump retention_days from old default 7 → 365 (only if user never changed it)
        con.execute("UPDATE app_settings SET value='365' WHERE key='retention_days' AND value='7'")
        con.commit()
        # Migrations — add columns when missing (safe to run on existing DBs)
        # users table
        try:
            con.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'admin'")
            con.commit()
        except Exception:
            pass
        # devices table
        try:
            con.execute("ALTER TABLE devices ADD COLUMN did_ctr INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass
        # sensors table — dns fields
        for col, default in [("dns_query", "''"), ("dns_record_type", "'A'"), ("dns_server", "''")]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col} TEXT DEFAULT {default}")
                con.commit()
            except Exception:
                pass
        for stmt in [
            "ALTER TABLE devices ADD COLUMN webhook_url TEXT DEFAULT ''",
            "ALTER TABLE sensors ADD COLUMN http_expected_status INTEGER DEFAULT 0",
            "ALTER TABLE flap_log ADD COLUMN direction TEXT DEFAULT 'down'",
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass
        # New sensor columns (debounce, thresholds, new probe fields)
        for col_def in [
            "fail_after    INTEGER DEFAULT 1",
            "recover_after INTEGER DEFAULT 1",
            "warn_ms       INTEGER",
            "crit_ms       INTEGER",
            "loss_warn_pct INTEGER DEFAULT 0",
            "loss_crit_pct INTEGER DEFAULT 0",
            "keyword       TEXT DEFAULT ''",
            "keyword_case  INTEGER DEFAULT 0",
            "banner_regex  TEXT DEFAULT ''",
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col_def}")
                con.commit()
            except Exception:
                pass
        # alerts_muted — disable alerts per sensor / device
        for stmt in [
            "ALTER TABLE sensors ADD COLUMN alerts_muted INTEGER DEFAULT 0",
            "ALTER TABLE devices ADD COLUMN alerts_muted INTEGER DEFAULT 0",
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass
    finally:
        con.close()
    log.info("DB init: schema ready")


def db_seed_users():
    """Seed default admin user if not present; preload live sessions into memory."""
    import secrets as _sec
    con = sqlite3.connect(DB_PATH)
    try:
        if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            pw = _sec.token_urlsafe(9)
            con.execute("INSERT INTO users VALUES (?,?,?)", ("admin", _hash_pw(pw), "admin"))
            con.commit()
            # Print to terminal only — never write the plaintext password to the log file
            print("=" * 51, flush=True)
            print(f"  Default admin password: {pw}", flush=True)
            print("  Change it in Settings -> Users -> Reset Password", flush=True)
            print("=" * 51, flush=True)
            log.warning("Default admin user created — password printed to terminal")
        rows = con.execute(
            "SELECT s.token, s.username, s.expires, COALESCE(u.role, 'admin') "
            "FROM sessions s LEFT JOIN users u ON s.username = u.username "
            "WHERE s.expires > ?",
            (time.time(),)
        ).fetchall()
        with _SESSIONS_LOCK:
            for token, username, expires, role in rows:
                _SESSIONS[token] = {"username": username, "expires": expires, "role": role}
        if rows:
            log.info(f"DB seed: restored {len(rows)} active session(s) into memory")
    finally:
        con.close()
