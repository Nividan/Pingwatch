"""
db/core.py — Single-writer queue, schema init, and user seeding.
"""

import os
import queue
import shutil
import sqlite3
import threading

from core.auth   import _hash_pw, _SESSIONS, _SESSIONS_LOCK
from core.config import DB_PATH, LOGS_DB_PATH
from core.logger import log
from db.backend  import is_pg

# ── Single-writer queues (Main DB + Logs DB) ─────────────────────────────────
_DB_QUEUE:   queue.Queue = queue.Queue()
_LOGS_QUEUE: queue.Queue = queue.Queue()


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


def _logs_writer_loop():
    """Drain _LOGS_QUEUE and execute each callable sequentially."""
    while True:
        fn = _LOGS_QUEUE.get()
        if fn is None:   # sentinel for clean shutdown (not yet used)
            break
        try:
            fn()
        except Exception as e:
            log.error(f"Logs DB writer error: {e}")


threading.Thread(target=_db_writer_loop,   daemon=True, name="db-main-writer").start()
threading.Thread(target=_logs_writer_loop, daemon=True, name="db-logs-writer").start()


def _db_enqueue(fn):
    """Queue a zero-argument callable for the Main DB writer thread.
    In PostgreSQL mode the callable is executed directly (PG MVCC
    handles concurrency)."""
    if is_pg():
        try:
            fn()
        except Exception as e:
            log.error(f"DB writer error: {e}")
    else:
        _DB_QUEUE.put(fn)


def _logs_enqueue(fn):
    """Queue a zero-argument callable for the Logs DB writer thread.
    In PostgreSQL mode the callable is executed directly."""
    if is_pg():
        try:
            fn()
        except Exception as e:
            log.error(f"Logs DB writer error: {e}")
    else:
        _LOGS_QUEUE.put(fn)


# ── Schema init ──────────────────────────────────────────────────

def db_init_pg():
    """Create PostgreSQL schemas, seed defaults.  Called instead of
    ``db_init()`` + ``logs_db_init()`` when the backend is PostgreSQL."""
    from db.pg_pool   import pg_conn
    from db.pg_schema import pg_create_main_schema, pg_create_logs_schema, pg_seed_defaults

    with pg_conn("public") as con:
        cur = con.cursor()
        pg_create_main_schema(cur)
        pg_create_logs_schema(cur)
        pg_seed_defaults(cur)
        cur.close()
    log.info("PG schema init complete")


def db_init():
    if is_pg():
        db_init_pg()
        return

    # ── Pre-migration safety backup (runs once per DB file) ──────────────
    _bak = str(DB_PATH) + ".pre_migrate.bak"
    if not os.path.exists(_bak) and os.path.exists(DB_PATH):
        try:
            shutil.copy2(DB_PATH, _bak)
            log.info(f"DB backup created: {_bak}")
        except Exception as _be:
            log.warning(f"DB pre-migration backup failed (non-fatal): {_be}")

    con = sqlite3.connect(DB_PATH, timeout=15)
    try:
        con.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads while probes write

        # Detect post-split installs early (migration already ran, or fresh install
        # that will be seeded below).  When True, skip creating the four logs tables
        # that now live in pingwatch_logs.db.
        _post_split = False
        try:
            _ps = con.execute(
                "SELECT value FROM app_settings WHERE key='db_split_complete'"
            ).fetchone()
            _post_split = _ps is not None and _ps[0] == '1'
        except Exception:
            pass   # app_settings doesn't exist yet on a brand-new DB

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
        if not _post_split:
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
                    direction TEXT DEFAULT 'down',
                    ack_state TEXT DEFAULT 'active',
                    ack_by    TEXT DEFAULT '',
                    ack_at    REAL DEFAULT 0
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
        if not _post_split:
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
            CREATE TABLE IF NOT EXISTS dashboards (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT    NOT NULL,
                name       TEXT    NOT NULL DEFAULT 'Default',
                sort_order INTEGER NOT NULL DEFAULT 0,
                widgets    TEXT    NOT NULL DEFAULT '[]'
            )""")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboards_user_name "
                    "ON dashboards(username, name)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_dashboards_user "
                    "ON dashboards(username, sort_order)")
        # ── Migration: dashboard_widgets → dashboards ───────────────
        try:
            old = con.execute("SELECT username, widgets FROM dashboard_widgets").fetchall()
            for r in old:
                con.execute(
                    "INSERT OR IGNORE INTO dashboards (username, name, sort_order, widgets) "
                    "VALUES (?, 'Default', 0, ?)", (r[0], r[1]))
            con.execute("DROP TABLE dashboard_widgets")
            con.commit()
            log.info("Migrated dashboard_widgets → dashboards")
        except Exception:
            pass  # already migrated or table never existed
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
            # Scheduled database backup
            ("db_backup_enabled",     "0"),
            ("db_backup_freq",        "daily"),
            ("db_backup_time",        "03:00"),
            ("db_backup_days",        "1,2,3,4,5,6,7"),
            ("db_backup_keep",        "7"),
            ("db_backup_last_ts",     ""),
            ("db_backup_last_result", ""),
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
            # LDAP / Active Directory authentication
            ("ldap_enabled",        "0"),
            ("ldap_server",         ""),
            ("ldap_port",           "389"),
            ("ldap_ssl",            "0"),   # 0=none, 1=LDAPS, 2=StartTLS
            ("ldap_base_dn",        ""),
            ("ldap_bind_dn",        ""),
            ("ldap_bind_pass",      ""),    # Fernet-encrypted
            ("ldap_user_filter",    "(sAMAccountName={username})"),
            ("ldap_domain",         ""),
            ("ldap_timeout",        "10"),
            ("ldap_debug",          "0"),   # 0=login events only, 1=full debug trace
            # Dual-DB split: '1' means logs tables live in pingwatch_logs.db
            # Seeded here so fresh installs never trigger an unnecessary migration
            ("db_split_complete",   "1"),
            # Data rollup (v0.8.0)
            ("retention_raw_days",     "7"),
            ("retention_5m_days",      "90"),
            ("retention_1h_days",      "1095"),
            ("max_workers_executor",   "64"),
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
        _flap_direction = [] if _post_split else [
            "ALTER TABLE flap_log ADD COLUMN direction TEXT DEFAULT 'down'",
        ]
        for stmt in [
            "ALTER TABLE devices ADD COLUMN webhook_url TEXT DEFAULT ''",
            "ALTER TABLE sensors ADD COLUMN http_expected_status INTEGER DEFAULT 0",
        ] + _flap_direction:
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
        # host_override — sensor host was manually set (not inherited from device)
        try:
            con.execute("ALTER TABLE sensors ADD COLUMN host_override INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass
        # snmp_unit — semantic unit for the OID (e.g. "bytes", "errors", "%", "count")
        try:
            con.execute("ALTER TABLE sensors ADD COLUMN snmp_unit TEXT DEFAULT ''")
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
        # VMware sensor fields
        for col in ("vmware_user", "vmware_password", "vmware_vm_id", "vmware_vm_name", "vmware_metric"):
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col} TEXT DEFAULT ''")
                con.commit()
            except Exception:
                pass
        # Device-level default credentials
        for col in ("snmp_community_default", "snmp_version_default", "vmware_user_default", "vmware_password_default"):
            try:
                con.execute(f"ALTER TABLE devices ADD COLUMN {col} TEXT DEFAULT ''")
                con.commit()
            except Exception:
                pass
        # Device secondary IPs (JSON array)
        try:
            con.execute("ALTER TABLE devices ADD COLUMN secondary_ips TEXT DEFAULT '[]'")
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
        # ── snmp_traps enrichment columns (migration — skipped in post-split mode) ──
        if not _post_split:
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
        # Fast lookup indexes on snmp_traps (skipped in post-split mode)
        if not _post_split:
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
        # Migration: DNS cache columns
        for _dns_col in [
            "ALTER TABLE ip_allocations ADD COLUMN dns_name        TEXT DEFAULT ''",
            "ALTER TABLE ip_allocations ADD COLUMN dns_resolved_at REAL DEFAULT 0",
        ]:
            try:
                con.execute(_dns_col)
                con.commit()
            except Exception:
                pass  # column already exists
        # Migration: LDAP domain-user support
        try:
            con.execute("ALTER TABLE users ADD COLUMN auth_type TEXT DEFAULT 'local'")
            con.commit()
        except Exception:
            pass
        try:
            con.execute("ALTER TABLE users ADD COLUMN domain TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass
        # Migration: user profiles + groups (v0.8+)
        for _col, _def in [
            ("full_name",        "TEXT DEFAULT ''"),
            ("email",            "TEXT DEFAULT ''"),
            ("group_id",         "INTEGER DEFAULT NULL"),
            ("theme_preference", "TEXT DEFAULT 'dark'"),
        ]:
            try:
                con.execute(f"ALTER TABLE users ADD COLUMN {_col} {_def}")
                con.commit()
            except Exception:
                pass
        # ── Alert Profiles (PRTG-style state-trigger system) ──────────
        # One-time cleanup of legacy condition-rule tables (idempotent)
        for _t in ("alert_rules", "alert_rule_conditions",
                   "alert_rule_actions", "alert_dedup"):
            try:
                con.execute(f"DROP TABLE IF EXISTS {_t}")
            except Exception:
                pass
        # Hard-replace alert_events: rule_id/rule_name → profile_id/stage_id/profile_name.
        # alert_events is rotational and the user has minimal history — full recreate is fine.
        try:
            _cols = {r[1] for r in con.execute("PRAGMA table_info(alert_events)").fetchall()}
            if _cols and ("rule_id" in _cols or "profile_id" not in _cols):
                con.execute("DROP TABLE alert_events")
        except Exception:
            pass
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id   INTEGER DEFAULT 0,
                stage_id     INTEGER DEFAULT 0,
                profile_name TEXT    DEFAULT '',
                did          TEXT    DEFAULT '',
                sid          TEXT    DEFAULT '',
                dname        TEXT    DEFAULT '',
                sname        TEXT    DEFAULT '',
                severity     TEXT    DEFAULT '',
                event_type   TEXT    DEFAULT '',
                state        TEXT    DEFAULT 'active',
                triggered_at REAL    DEFAULT 0,
                resolved_at  REAL    DEFAULT 0,
                ack_by       TEXT    DEFAULT '',
                ack_at       REAL    DEFAULT 0,
                detail       TEXT    DEFAULT '',
                repeat_count INTEGER DEFAULT 1
            )""")
        # Partial index for dedup lookups — only covers unresolved rows.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_events_active_sensor "
            "ON alert_events(did, sid) "
            "WHERE state IN ('active','acknowledged')"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_profiles (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                scope_type  TEXT    NOT NULL DEFAULT 'global',
                scope_value TEXT    DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  REAL    DEFAULT 0,
                updated_at  REAL    DEFAULT 0,
                UNIQUE(scope_type, scope_value)
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_profiles_scope "
            "ON alert_profiles(scope_type, scope_value)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_action_templates (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                atype      TEXT    NOT NULL,
                config     TEXT    NOT NULL DEFAULT '{}',
                created_at REAL    DEFAULT 0
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_profile_stages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id    INTEGER NOT NULL REFERENCES alert_profiles(id) ON DELETE CASCADE,
                trigger_state TEXT    NOT NULL,
                delay_s       INTEGER NOT NULL DEFAULT 0,
                repeat_min    INTEGER NOT NULL DEFAULT 0,
                action_ids    TEXT    NOT NULL DEFAULT '[]',
                sort_order    INTEGER NOT NULL DEFAULT 0
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_profile_stages_profile "
            "ON alert_profile_stages(profile_id)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_profile_state (
                sig            TEXT    PRIMARY KEY,
                first_fire_ts  REAL    DEFAULT 0,
                last_fire_ts   REAL    DEFAULT 0,
                fire_count     INTEGER DEFAULT 0,
                active_session TEXT    DEFAULT ''
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS maintenance_windows (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                scope_type  TEXT    DEFAULT 'all',
                scope_value TEXT    DEFAULT '',
                start_ts    REAL    NOT NULL,
                end_ts      REAL    NOT NULL,
                recurring   INTEGER DEFAULT 0,
                recur_days  TEXT    DEFAULT '',
                recur_start TEXT    DEFAULT '',
                recur_end   TEXT    DEFAULT '',
                created_by  TEXT    DEFAULT '',
                created_at  REAL    DEFAULT 0
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL UNIQUE,
                description  TEXT    DEFAULT '',
                ldap_dn      TEXT    DEFAULT '',
                default_role TEXT    DEFAULT 'viewer'
            )""")
        # ── Device license tracking ───────────────────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS device_licenses (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                did          TEXT NOT NULL,
                license_name TEXT NOT NULL,
                expiry_date  TEXT NOT NULL,
                note         TEXT DEFAULT '',
                warn_days    INTEGER DEFAULT 30,
                crit_days    INTEGER DEFAULT 0,
                last_status  TEXT DEFAULT 'ok',
                created_at   REAL DEFAULT 0,
                updated_at   REAL DEFAULT 0
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_dev_lic_did ON device_licenses(did)"
        )
        con.commit()
        # Migration: LDAP group mapping columns (v0.9+)
        for _col, _def in [
            ("ldap_dn",      "TEXT DEFAULT ''"),
            ("default_role",  "TEXT DEFAULT 'viewer'"),
        ]:
            try:
                con.execute(f"ALTER TABLE user_groups ADD COLUMN {_col} {_def}")
                con.commit()
            except Exception:
                pass
        # ── Migrate alert_profile_stages: action_id (int) → action_ids (json) ──
        try:
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info(alert_profile_stages)").fetchall()}
            if 'action_ids' not in cols and 'action_id' in cols:
                con.execute(
                    "ALTER TABLE alert_profile_stages "
                    "ADD COLUMN action_ids TEXT NOT NULL DEFAULT '[]'"
                )
                con.execute(
                    "UPDATE alert_profile_stages "
                    "SET action_ids = '[' || CAST(action_id AS TEXT) || ']' "
                    "WHERE action_id IS NOT NULL"
                )
                con.commit()
                log.info("DB migrate: alert_profile_stages action_id → action_ids")
        except Exception as _e:
            log.warning(f"DB migrate alert_profile_stages: {_e}")
        # ── Make action_id nullable (SQLite can't ALTER COLUMN — recreate table) ──
        try:
            cols_info = con.execute(
                "PRAGMA table_info(alert_profile_stages)").fetchall()
            aid_col = next((r for r in cols_info if r[1] == 'action_id'), None)
            if aid_col and aid_col[3] == 1:   # notnull == 1
                con.execute(
                    "ALTER TABLE alert_profile_stages "
                    "RENAME TO _aps_migrate_tmp"
                )
                con.execute("""
                    CREATE TABLE alert_profile_stages (
                        id            INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id    INTEGER NOT NULL
                                      REFERENCES alert_profiles(id) ON DELETE CASCADE,
                        trigger_state TEXT NOT NULL CHECK(trigger_state IN
                                      ('down','warning','down_recovered','warning_recovered')),
                        delay_s       INTEGER NOT NULL DEFAULT 0,
                        repeat_min    INTEGER NOT NULL DEFAULT 0,
                        action_ids    TEXT    NOT NULL DEFAULT '[]',
                        action_id     INTEGER,
                        sort_order    INTEGER NOT NULL DEFAULT 0
                    )""")
                con.execute(
                    "INSERT INTO alert_profile_stages "
                    "(id, profile_id, trigger_state, delay_s, repeat_min, "
                    " action_ids, action_id, sort_order) "
                    "SELECT id, profile_id, trigger_state, delay_s, repeat_min, "
                    "       action_ids, action_id, sort_order "
                    "FROM _aps_migrate_tmp"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_profile_stages_profile "
                    "ON alert_profile_stages(profile_id)"
                )
                con.execute("DROP TABLE _aps_migrate_tmp")
                con.commit()
                log.info("DB migrate: alert_profile_stages action_id made nullable")
        except Exception as _e:
            log.warning(f"DB migrate alert_profile_stages nullable: {_e}")
    finally:
        con.close()
    log.info("DB init: schema ready")


def db_seed_users():
    """Seed default admin user if not present; preload live sessions into memory."""
    import secrets as _sec

    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor("main") as cur:
            cur.execute("SELECT 1 FROM users WHERE username='admin'")
            if not cur.fetchone():
                pw = _sec.token_urlsafe(9)
                cur.execute(
                    "INSERT INTO users (username, pw_hash, role) VALUES (%s,%s,%s)",
                    ("admin", _hash_pw(pw), "admin")
                )
                print("=" * 51, flush=True)
                print(f"  Default admin password: {pw}", flush=True)
                print("  Change it in Settings -> Users -> Reset Password", flush=True)
                print("=" * 51, flush=True)
                log.warning("Default admin user created — password printed to terminal")
            cur.execute("DELETE FROM sessions")
            log.info("DB seed: all sessions cleared (server restarted)")
        return

    con = sqlite3.connect(DB_PATH, timeout=15)
    try:
        if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
            pw = _sec.token_urlsafe(9)
            con.execute(
                "INSERT INTO users (username, pw_hash, role) VALUES (?,?,?)",
                ("admin", _hash_pw(pw), "admin")
            )
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


def db_seed_alert_profiles():
    """Seed default action template + global Default profile if missing.

    Fresh installs ship with sane "alert me when something dies" behavior:
      - Action template "Email admin" → email to the 'admin' user group
      - Global profile "Default" with stages:
          • down @ 60s     → Email admin
          • down_recovered → Email admin
    """
    import time as _t
    import json as _json
    now = _t.time()
    # Default action targets the 'admin' user — its email is resolved at fire time
    cfg_email = _json.dumps({"to_users": ["admin"]})

    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor("main") as cur:
            cur.execute("SELECT COUNT(*) AS n FROM alert_profiles")
            row = cur.fetchone()
            if row and (row.get("n") if isinstance(row, dict) else row[0]):
                return
            cur.execute(
                "INSERT INTO alert_action_templates (name, atype, config, created_at) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name "
                "RETURNING id",
                ("Email admin", "email", cfg_email, now)
            )
            tpl_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO alert_profiles (name, scope_type, scope_value, enabled, "
                "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                ("Default", "global", "", 1, now, now)
            )
            prof_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO alert_profile_stages (profile_id, trigger_state, delay_s, "
                "repeat_min, action_ids, sort_order) VALUES (%s,%s,%s,%s,%s,%s)",
                (prof_id, "down", 60, 0, f"[{tpl_id}]", 0)
            )
            cur.execute(
                "INSERT INTO alert_profile_stages (profile_id, trigger_state, delay_s, "
                "repeat_min, action_ids, sort_order) VALUES (%s,%s,%s,%s,%s,%s)",
                (prof_id, "down_recovered", 0, 0, f"[{tpl_id}]", 1)
            )
        log.info("DB seed: default alert profile + Email admin template created")
        return

    con = sqlite3.connect(DB_PATH, timeout=15)
    try:
        n = con.execute("SELECT COUNT(*) FROM alert_profiles").fetchone()[0]
        if n:
            return
        cur = con.execute(
            "INSERT INTO alert_action_templates (name, atype, config, created_at) "
            "VALUES (?,?,?,?)",
            ("Email admin", "email", cfg_email, now)
        )
        tpl_id = cur.lastrowid
        cur = con.execute(
            "INSERT INTO alert_profiles (name, scope_type, scope_value, enabled, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            ("Default", "global", "", 1, now, now)
        )
        prof_id = cur.lastrowid
        con.execute(
            "INSERT INTO alert_profile_stages (profile_id, trigger_state, delay_s, "
            "repeat_min, action_ids, sort_order) VALUES (?,?,?,?,?,?)",
            (prof_id, "down", 60, 0, f"[{tpl_id}]", 0)
        )
        con.execute(
            "INSERT INTO alert_profile_stages (profile_id, trigger_state, delay_s, "
            "repeat_min, action_ids, sort_order) VALUES (?,?,?,?,?,?)",
            (prof_id, "down_recovered", 0, 0, f"[{tpl_id}]", 1)
        )
        con.commit()
        log.info("DB seed: default alert profile + Email admin template created")
    finally:
        con.close()


def logs_db_init():
    """Create the Logs DB schema (sensor_samples, flap_log, sensor_err_log, snmp_traps)."""
    if is_pg():
        return   # handled by db_init_pg()

    con = sqlite3.connect(LOGS_DB_PATH, timeout=15)
    try:
        con.execute("PRAGMA journal_mode=WAL")
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
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds_cov "
            "ON sensor_samples(did, sid, ts, ok, ms)"
        )
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
                direction TEXT DEFAULT 'down',
                ack_state TEXT DEFAULT 'active',
                ack_by    TEXT DEFAULT '',
                ack_at    REAL DEFAULT 0,
                resolved_at REAL DEFAULT 0,
                duration    REAL DEFAULT 0
            )""")
        # Migration: add columns to existing flap_log tables
        for _col, _def in [
            ("ack_state", "TEXT DEFAULT 'active'"),
            ("ack_by",    "TEXT DEFAULT ''"),
            ("ack_at",    "REAL DEFAULT 0"),
            ("resolved_at", "REAL DEFAULT 0"),
            ("duration",    "REAL DEFAULT 0"),
        ]:
            try:
                con.execute(f"ALTER TABLE flap_log ADD COLUMN {_col} {_def}")
                con.commit()
            except Exception:
                pass
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_err_log (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    TEXT,
                did   TEXT,
                sid   TEXT,
                sname TEXT,
                stype TEXT,
                msg   TEXT
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS snmp_traps (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                ts                 TEXT,
                src_ip             TEXT,
                dname              TEXT,
                community          TEXT,
                trap_oid           TEXT,
                detail             TEXT,
                vendor             TEXT DEFAULT '',
                product_family     TEXT DEFAULT '',
                trap_name          TEXT DEFAULT '',
                severity           TEXT DEFAULT 'info',
                category           TEXT DEFAULT '',
                probable_cause     TEXT DEFAULT '',
                recommended_action TEXT DEFAULT '',
                raw_varbinds       TEXT DEFAULT '[]',
                enriched           INTEGER DEFAULT 0,
                enterprise_oid     TEXT DEFAULT '',
                generic_trap_type  INTEGER DEFAULT -1,
                enriched_varbinds  TEXT DEFAULT '[]'
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_traps_src    ON snmp_traps(src_ip, ts)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_traps_vendor ON snmp_traps(vendor, ts)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_traps_oid    ON snmp_traps(trap_oid)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS logs_schema_version (
                version INTEGER PRIMARY KEY,
                applied TEXT    NOT NULL,
                notes   TEXT    DEFAULT ''
            )""")
        if not con.execute("SELECT 1 FROM logs_schema_version").fetchone():
            con.execute(
                "INSERT INTO logs_schema_version VALUES (1, datetime('now'), 'initial split')"
            )

        # ── Rollup tables (v0.8.0) ─────────────────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_samples_5m (
                ts           REAL    NOT NULL,
                did          TEXT    NOT NULL,
                sid          TEXT    NOT NULL,
                ok_count     INTEGER NOT NULL DEFAULT 0,
                fail_count   INTEGER NOT NULL DEFAULT 0,
                avg_ms       REAL,
                min_ms       REAL,
                max_ms       REAL,
                avg_ms_sq    REAL    DEFAULT 0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (did, sid, ts)
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_s5m_ts ON sensor_samples_5m(ts)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_samples_1h (
                ts           REAL    NOT NULL,
                did          TEXT    NOT NULL,
                sid          TEXT    NOT NULL,
                ok_count     INTEGER NOT NULL DEFAULT 0,
                fail_count   INTEGER NOT NULL DEFAULT 0,
                avg_ms       REAL,
                min_ms       REAL,
                max_ms       REAL,
                avg_ms_sq    REAL    DEFAULT 0,
                sample_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (did, sid, ts)
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_s1h_ts ON sensor_samples_1h(ts)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS rollup_state (
                tier    TEXT PRIMARY KEY,
                last_ts REAL NOT NULL DEFAULT 0
            )""")
        con.execute(
            "INSERT OR IGNORE INTO rollup_state (tier, last_ts) VALUES ('5m', 0)"
        )
        con.execute(
            "INSERT OR IGNORE INTO rollup_state (tier, last_ts) VALUES ('1h', 0)"
        )
        if not con.execute(
            "SELECT 1 FROM logs_schema_version WHERE version = 2"
        ).fetchone():
            con.execute(
                "INSERT INTO logs_schema_version VALUES "
                "(2, datetime('now'), 'v0.8.0 — rollup tables')"
            )
        con.commit()
    finally:
        con.close()
    log.info("Logs DB init: schema ready")
