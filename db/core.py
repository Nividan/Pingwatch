"""
db/core.py — Single-writer queue, schema init, and user seeding.
"""

import os
import queue
import shutil
import sqlite3
import threading
import time

from core.auth   import _hash_pw, _SESSIONS, _SESSIONS_LOCK
from core.config import DB_PATH
from core.logger import log

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
    # ── Pre-migration safety backup (runs once per DB file) ──────────────
    _bak = str(DB_PATH) + ".pre_migrate.bak"
    if not os.path.exists(_bak) and os.path.exists(DB_PATH):
        try:
            shutil.copy2(DB_PATH, _bak)
            log.info(f"DB backup created: {_bak}")
        except Exception as _be:
            log.warning(f"DB pre-migration backup failed (non-fatal): {_be}")

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
        # Covering index: includes ok and ms so startup history/count queries
        # never need to touch the main-table heap pages.  Eliminates thousands
        # of random-read I/Os and reduces startup from minutes to seconds.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds_cov "
            "ON sensor_samples(did, sid, ts, ok, ms)"
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS dashboard_widgets (
                username TEXT PRIMARY KEY,
                widgets  TEXT NOT NULL DEFAULT '[]'
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS backup_devices (
                did          TEXT PRIMARY KEY,
                enabled      INTEGER DEFAULT 0,
                method       TEXT    DEFAULT 'ssh',
                port         INTEGER DEFAULT 22,
                username     TEXT    DEFAULT '',
                password_enc TEXT    DEFAULT '',
                enable_enc   TEXT    DEFAULT '',
                commands     TEXT    DEFAULT '["show running-config"]',
                paging_cmd   TEXT    DEFAULT '',
                timeout      INTEGER DEFAULT 30,
                schedule     TEXT    DEFAULT ''
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS backup_runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                did        TEXT    NOT NULL,
                ts         TEXT    NOT NULL,
                success    INTEGER DEFAULT 0,
                method     TEXT    DEFAULT '',
                size_bytes INTEGER DEFAULT 0,
                sha256     TEXT    DEFAULT '',
                config     TEXT    DEFAULT '',
                error_msg  TEXT    DEFAULT ''
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS ix_backup_runs_did_ts "
            "ON backup_runs(did, ts DESC)"
        )
        con.commit()
        # ── Schema version table (added in v0.7) ─────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied TEXT NOT NULL,
                notes   TEXT DEFAULT ''
            )""")
        # Seed version 1 if empty — covers both fresh installs and old DBs
        if not con.execute("SELECT 1 FROM schema_version").fetchone():
            con.execute("INSERT INTO schema_version VALUES (1, datetime('now'), 'baseline — cross-platform release')")
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
            ("latency_good_ms",      "100"),
            ("latency_warn_ms",      "300"),
            # Global backup scheduler settings
            ("backup_sched_enabled", "0"),
            ("backup_sched_freq",    "daily"),
            ("backup_sched_time",    "02:00"),
            ("backup_sched_days",    "1,2,3,4,5,6,7"),
            ("backup_keep",          "3"),
            # TLS / HTTPS settings
            ("tls_enabled",          "1"),   # 0=HTTP only, 1=HTTPS enabled (default on for fresh installs)
            ("tls_port",             "8443"), # HTTPS listening port
            ("tls_cert_pem",         ""),    # PEM certificate (plain text)
            ("tls_key_pem_enc",      ""),    # PEM private key (Fernet-encrypted)
            ("tls_cert_source",      ""),    # "generated" | "imported" | "uploaded"
            ("tls_cn",               ""),    # CN/hostname used when generating self-signed cert
            ("http_redirect",        "1"),   # 0=off, 1=redirect HTTP→HTTPS (default on for fresh installs)
            # Syslog forwarding
            ("syslog_enabled",      "0"),
            ("syslog_host",         ""),
            ("syslog_port",         "514"),
            ("syslog_proto",        "udp"),
            ("syslog_min_severity", "warning"),
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
        # backup_devices — replace per-device schedule with global-schedule flag
        try:
            con.execute("ALTER TABLE backup_devices ADD COLUMN in_schedule INTEGER DEFAULT 0")
            # Promote old per-device daily/weekly → participates in global schedule
            con.execute("UPDATE backup_devices SET in_schedule=1 WHERE schedule IN ('daily','weekly')")
            con.commit()
        except Exception:
            pass
        # ── SNMP trap intelligence — new tables (v0.6.1) ─────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS enterprise_oid_map (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                enterprise_oid TEXT NOT NULL UNIQUE,
                vendor         TEXT NOT NULL,
                product_family TEXT DEFAULT '',
                notes          TEXT DEFAULT ''
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_eom_oid "
            "ON enterprise_oid_map(enterprise_oid)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS trap_definitions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                trap_oid           TEXT NOT NULL UNIQUE,
                trap_name          TEXT NOT NULL,
                vendor             TEXT DEFAULT '',
                product_family     TEXT DEFAULT '',
                severity           TEXT DEFAULT 'info',
                category           TEXT DEFAULT '',
                probable_cause     TEXT DEFAULT '',
                description        TEXT DEFAULT '',
                recommended_action TEXT DEFAULT '',
                varbind_hints      TEXT DEFAULT '{}',
                mib_name           TEXT DEFAULT '',
                source             TEXT DEFAULT 'builtin'
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_td_oid    ON trap_definitions(trap_oid)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_td_vendor ON trap_definitions(vendor)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS trap_categories (
                name  TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                color TEXT DEFAULT ''
            )""")
        # ── snmp_traps enrichment columns (migration) ─────────────────
        for _col in [
            "ALTER TABLE snmp_traps ADD COLUMN vendor          TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN product_family  TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN trap_name       TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN severity        TEXT DEFAULT 'info'",
            "ALTER TABLE snmp_traps ADD COLUMN category        TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN probable_cause  TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN recommended_action TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN raw_varbinds         TEXT DEFAULT '[]'",
            "ALTER TABLE snmp_traps ADD COLUMN enriched             INTEGER DEFAULT 0",
            "ALTER TABLE snmp_traps ADD COLUMN enterprise_oid       TEXT DEFAULT ''",
            "ALTER TABLE snmp_traps ADD COLUMN generic_trap_type    INTEGER DEFAULT -1",
            "ALTER TABLE snmp_traps ADD COLUMN enriched_varbinds    TEXT DEFAULT '[]'",
        ]:
            try:
                con.execute(_col)
                con.commit()
            except Exception:
                pass
        # Fast lookup indexes on snmp_traps
        for _idx in [
            "CREATE INDEX IF NOT EXISTS idx_traps_src    ON snmp_traps(src_ip, ts)",
            "CREATE INDEX IF NOT EXISTS idx_traps_vendor ON snmp_traps(vendor, ts)",
            "CREATE INDEX IF NOT EXISTS idx_traps_oid    ON snmp_traps(trap_oid)",
        ]:
            try:
                con.execute(_idx)
            except Exception:
                pass
        con.commit()
        # ── IPAM tables ───────────────────────────────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS ipam_subnets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                cidr       TEXT UNIQUE NOT NULL,
                name       TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at REAL DEFAULT 0
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ipam_subnets_cidr ON ipam_subnets(cidr)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS ip_allocations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                subnet_id   INTEGER NOT NULL REFERENCES ipam_subnets(id),
                ip          TEXT NOT NULL,
                name        TEXT DEFAULT '',
                modified_by TEXT DEFAULT '',
                modified_at REAL DEFAULT 0,
                UNIQUE(subnet_id, ip)
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ip_alloc_subnet ON ip_allocations(subnet_id)"
        )
        # Migration: device_id column (links auto-populated entries to a device)
        try:
            con.execute("ALTER TABLE ip_allocations ADD COLUMN device_id TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass  # column already exists
        try:
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ip_alloc_device ON ip_allocations(device_id)"
            )
            con.commit()
        except Exception:
            pass
        con.commit()
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
        # Clear all sessions on startup — everyone must log in again after a restart
        con.execute("DELETE FROM sessions")
        con.commit()
        log.info("DB seed: all sessions cleared (server restarted)")
    finally:
        con.close()
