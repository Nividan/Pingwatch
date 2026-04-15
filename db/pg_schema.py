"""
db/pg_schema.py — PostgreSQL DDL for both schemas (main + logs).

Creates all tables with the full column set so no incremental ALTER TABLE
migrations are needed.  Called once at first PG startup.

v0.8.0: Added sensor_samples_5m, sensor_samples_1h, rollup_state tables.
        sensor_samples is now range-partitioned by ts (monthly partitions).
"""

import datetime
import time

from core.logger import log


def pg_create_main_schema(cur):
    """Create the 'main' schema and all config/state tables."""
    cur.execute("CREATE SCHEMA IF NOT EXISTS main")
    cur.execute("SET search_path TO main, public")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username         TEXT PRIMARY KEY,
            pw_hash          TEXT NOT NULL,
            role             TEXT DEFAULT 'admin',
            auth_type        TEXT DEFAULT 'local',
            domain           TEXT DEFAULT '',
            full_name        TEXT DEFAULT '',
            email            TEXT DEFAULT '',
            group_id         INTEGER DEFAULT NULL,
            theme_preference TEXT DEFAULT 'dark',
            totp_secret      TEXT DEFAULT '',
            totp_enabled     INTEGER DEFAULT 0,
            totp_recovery    TEXT DEFAULT ''
        )""")
    # Migration: add theme_preference for existing installs
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='main' AND table_name='users'
                  AND column_name='theme_preference'
            ) THEN
                ALTER TABLE users ADD COLUMN theme_preference TEXT DEFAULT 'dark';
            END IF;
        END $$
    """)
    # Migration: add TOTP columns for existing installs (2FA)
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='main' AND table_name='users'
                             AND column_name='totp_secret') THEN
                ALTER TABLE users ADD COLUMN totp_secret TEXT DEFAULT '';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='main' AND table_name='users'
                             AND column_name='totp_enabled') THEN
                ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='main' AND table_name='users'
                             AND column_name='totp_recovery') THEN
                ALTER TABLE users ADD COLUMN totp_recovery TEXT DEFAULT '';
            END IF;
        END $$
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token    TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            expires  DOUBLE PRECISION NOT NULL
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            did                      TEXT PRIMARY KEY,
            name                     TEXT,
            host                     TEXT,
            grp                      TEXT,
            did_ctr                  INTEGER DEFAULT 0,
            webhook_url              TEXT DEFAULT '',
            alerts_muted             INTEGER DEFAULT 0,
            snmp_community_default   TEXT DEFAULT '',
            snmp_version_default     TEXT DEFAULT '',
            vmware_user_default      TEXT DEFAULT '',
            vmware_password_default  TEXT DEFAULT '',
            secondary_ips            TEXT DEFAULT '[]'
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensors (
            did                  TEXT,
            sid                  TEXT,
            name                 TEXT,
            stype                TEXT,
            host                 TEXT,
            port                 INTEGER,
            url                  TEXT,
            interval             INTEGER,
            timeout              INTEGER,
            verify_ssl           INTEGER DEFAULT 1,
            snmp_community       TEXT DEFAULT 'public',
            snmp_oid             TEXT DEFAULT '1.3.6.1.2.1.1.1.0',
            snmp_version         TEXT DEFAULT '2c',
            sid_ctr              INTEGER DEFAULT 0,
            dns_query            TEXT DEFAULT '',
            dns_record_type      TEXT DEFAULT 'A',
            dns_server           TEXT DEFAULT '',
            http_expected_status INTEGER DEFAULT 0,
            fail_after           INTEGER DEFAULT 1,
            recover_after        INTEGER DEFAULT 1,
            warn_ms              INTEGER,
            crit_ms              INTEGER,
            loss_warn_pct        INTEGER DEFAULT 0,
            loss_crit_pct        INTEGER DEFAULT 0,
            keyword              TEXT DEFAULT '',
            keyword_case         INTEGER DEFAULT 0,
            banner_regex         TEXT DEFAULT '',
            alerts_muted         INTEGER DEFAULT 0,
            host_override        INTEGER DEFAULT 0,
            snmp_unit            TEXT DEFAULT '',
            vmware_user          TEXT DEFAULT '',
            vmware_password      TEXT DEFAULT '',
            vmware_vm_id         TEXT DEFAULT '',
            vmware_vm_name       TEXT DEFAULT '',
            vmware_metric        TEXT DEFAULT '',
            PRIMARY KEY (did, sid)
        )""")

    # ALTER TABLE migrations — each wrapped in a savepoint so a failure
    # (column already exists) does not abort the surrounding transaction.
    _migrations = [
        ("sensors", "vmware_user",            "TEXT DEFAULT ''"),
        ("sensors", "vmware_password",         "TEXT DEFAULT ''"),
        ("sensors", "vmware_vm_id",            "TEXT DEFAULT ''"),
        ("sensors", "vmware_vm_name",          "TEXT DEFAULT ''"),
        ("sensors", "vmware_metric",           "TEXT DEFAULT ''"),
        ("main.devices", "snmp_community_default",  "TEXT DEFAULT ''"),
        ("main.devices", "snmp_version_default",    "TEXT DEFAULT ''"),
        ("main.devices", "vmware_user_default",     "TEXT DEFAULT ''"),
        ("main.devices", "vmware_password_default", "TEXT DEFAULT ''"),
        ("main.devices", "secondary_ips",           "TEXT DEFAULT '[]'"),
    ]
    for _tbl, _col, _typedef in _migrations:
        try:
            cur.execute("SAVEPOINT _alter")
            cur.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_typedef}")
            cur.execute("RELEASE SAVEPOINT _alter")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _alter")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id     SERIAL PRIMARY KEY,
            ts     DOUBLE PRECISION NOT NULL,
            actor  TEXT NOT NULL,
            ip     TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT DEFAULT '',
            detail TEXT DEFAULT ''
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dashboards (
            id         SERIAL PRIMARY KEY,
            username   TEXT    NOT NULL,
            name       TEXT    NOT NULL DEFAULT 'Default',
            sort_order INTEGER NOT NULL DEFAULT 0,
            widgets    TEXT    NOT NULL DEFAULT '[]'
        )""")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboards_user_name "
                "ON dashboards(username, name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_dashboards_user "
                "ON dashboards(username, sort_order)")
    # ── Migration: dashboard_widgets → dashboards ───────────────
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name='dashboard_widgets'")
    if cur.fetchone():
        cur.execute("SELECT username, widgets FROM main.dashboard_widgets")
        old = cur.fetchall()
        for r in old:
            cur.execute(
                "INSERT INTO dashboards (username, name, sort_order, widgets) "
                "VALUES (%s, 'Default', 0, %s) ON CONFLICT (username, name) DO NOTHING",
                (r[0], r[1]))
        cur.execute("DROP TABLE main.dashboard_widgets")

    cur.execute("""
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
            schedule     TEXT    DEFAULT '',
            in_schedule  INTEGER DEFAULT 0
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS backup_runs (
            id         SERIAL PRIMARY KEY,
            did        TEXT    NOT NULL,
            ts         TEXT    NOT NULL,
            success    INTEGER DEFAULT 0,
            method     TEXT    DEFAULT '',
            size_bytes INTEGER DEFAULT 0,
            sha256     TEXT    DEFAULT '',
            config     TEXT    DEFAULT '',
            error_msg  TEXT    DEFAULT ''
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_backup_runs_did_ts "
        "ON backup_runs(did, ts DESC)"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied TEXT NOT NULL,
            notes   TEXT DEFAULT ''
        )""")

    # ── SNMP trap intelligence ───────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS enterprise_oid_map (
            id             SERIAL PRIMARY KEY,
            enterprise_oid TEXT NOT NULL UNIQUE,
            vendor         TEXT NOT NULL,
            product_family TEXT DEFAULT '',
            notes          TEXT DEFAULT ''
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_eom_oid "
        "ON enterprise_oid_map(enterprise_oid)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trap_definitions (
            id                 SERIAL PRIMARY KEY,
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
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_td_oid    ON trap_definitions(trap_oid)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_td_vendor ON trap_definitions(vendor)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trap_categories (
            name  TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            color TEXT DEFAULT ''
        )""")

    # ── IPAM ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ipam_subnets (
            id         SERIAL PRIMARY KEY,
            cidr       TEXT UNIQUE NOT NULL,
            name       TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ipam_subnets_cidr ON ipam_subnets(cidr)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ip_allocations (
            id              SERIAL PRIMARY KEY,
            subnet_id       INTEGER NOT NULL REFERENCES ipam_subnets(id),
            ip              TEXT NOT NULL,
            name            TEXT DEFAULT '',
            modified_by     TEXT DEFAULT '',
            modified_at     DOUBLE PRECISION DEFAULT 0,
            device_id       TEXT DEFAULT '',
            dns_name        TEXT DEFAULT '',
            dns_resolved_at DOUBLE PRECISION DEFAULT 0,
            UNIQUE(subnet_id, ip)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ip_alloc_subnet ON ip_allocations(subnet_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ip_alloc_device ON ip_allocations(device_id)"
    )

    # ── Alert Profiles (PRTG-style state-trigger system) ─────────────
    # One-time cleanup of legacy condition-rule tables (idempotent)
    for _legacy in ("alert_rules", "alert_rule_conditions",
                    "alert_rule_actions", "alert_dedup"):
        try:
            cur.execute("SAVEPOINT _drop_legacy")
            cur.execute(f"DROP TABLE IF EXISTS {_legacy} CASCADE")
            cur.execute("RELEASE SAVEPOINT _drop_legacy")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _drop_legacy")
    # Hard-replace alert_events if it still has the legacy rule_id column
    try:
        cur.execute("SAVEPOINT _replace_events")
        cur.execute("""
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'alert_events'
               AND column_name = 'rule_id'
        """)
        if cur.fetchone():
            cur.execute("DROP TABLE alert_events")
        cur.execute("RELEASE SAVEPOINT _replace_events")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT _replace_events")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_events (
            id           SERIAL PRIMARY KEY,
            profile_id   INTEGER DEFAULT 0,
            stage_id     INTEGER DEFAULT 0,
            profile_name TEXT DEFAULT '',
            did          TEXT DEFAULT '',
            sid          TEXT DEFAULT '',
            dname        TEXT DEFAULT '',
            sname        TEXT DEFAULT '',
            severity     TEXT DEFAULT '',
            event_type   TEXT DEFAULT '',
            state        TEXT DEFAULT 'active',
            triggered_at DOUBLE PRECISION DEFAULT 0,
            resolved_at  DOUBLE PRECISION DEFAULT 0,
            ack_by       TEXT DEFAULT '',
            ack_at       DOUBLE PRECISION DEFAULT 0,
            detail       TEXT DEFAULT '',
            repeat_count INTEGER DEFAULT 1
        )""")
    # Partial index for dedup lookups — only covers unresolved rows.
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_events_active_sensor "
        "ON alert_events(did, sid) "
        "WHERE state IN ('active','acknowledged')"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_profiles (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            scope_type  TEXT NOT NULL DEFAULT 'global',
            scope_value TEXT DEFAULT '',
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  DOUBLE PRECISION DEFAULT 0,
            updated_at  DOUBLE PRECISION DEFAULT 0,
            UNIQUE(scope_type, scope_value)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_profiles_scope "
        "ON alert_profiles(scope_type, scope_value)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_action_templates (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            atype      TEXT NOT NULL,
            config     TEXT NOT NULL DEFAULT '{}',
            created_at DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_profile_stages (
            id            SERIAL PRIMARY KEY,
            profile_id    INTEGER NOT NULL REFERENCES alert_profiles(id) ON DELETE CASCADE,
            trigger_state TEXT NOT NULL,
            delay_s       INTEGER NOT NULL DEFAULT 0,
            repeat_min    INTEGER NOT NULL DEFAULT 0,
            action_ids    TEXT NOT NULL DEFAULT '[]',
            sort_order    INTEGER NOT NULL DEFAULT 0
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_profile_stages_profile "
        "ON alert_profile_stages(profile_id)"
    )
    # Migrate action_id (int) → action_ids (json) for existing installs
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='main' AND table_name='alert_profile_stages'
                  AND column_name='action_ids'
            ) THEN
                ALTER TABLE alert_profile_stages ADD COLUMN action_ids TEXT NOT NULL DEFAULT '[]';
                UPDATE alert_profile_stages
                   SET action_ids = '['||action_id::text||']'
                 WHERE action_id IS NOT NULL;
            END IF;
        END $$
    """)
    # Drop NOT NULL from action_id so new inserts (which omit it) succeed
    cur.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='main' AND table_name='alert_profile_stages'
                  AND column_name='action_id' AND is_nullable = 'NO'
            ) THEN
                ALTER TABLE alert_profile_stages ALTER COLUMN action_id DROP NOT NULL;
            END IF;
        END $$
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_profile_state (
            sig            TEXT PRIMARY KEY,
            first_fire_ts  DOUBLE PRECISION DEFAULT 0,
            last_fire_ts   DOUBLE PRECISION DEFAULT 0,
            fire_count     INTEGER DEFAULT 0,
            active_session TEXT DEFAULT ''
        )""")

    # ── Maintenance Windows ──────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_windows (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            scope_type  TEXT DEFAULT 'all',
            scope_value TEXT DEFAULT '',
            start_ts    DOUBLE PRECISION NOT NULL,
            end_ts      DOUBLE PRECISION NOT NULL,
            recurring   INTEGER DEFAULT 0,
            recur_days  TEXT DEFAULT '',
            recur_start TEXT DEFAULT '',
            recur_end   TEXT DEFAULT '',
            created_by  TEXT DEFAULT '',
            created_at  DOUBLE PRECISION DEFAULT 0
        )""")

    # ── User Groups ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_groups (
            id           SERIAL PRIMARY KEY,
            name         TEXT NOT NULL UNIQUE,
            description  TEXT DEFAULT '',
            ldap_dn      TEXT DEFAULT '',
            default_role TEXT DEFAULT 'viewer'
        )""")
    # Migration: LDAP group mapping columns
    for _tbl, _col, _typedef in [
        ("user_groups", "ldap_dn",      "TEXT DEFAULT ''"),
        ("user_groups", "default_role",  "TEXT DEFAULT 'viewer'"),
    ]:
        try:
            cur.execute("SAVEPOINT _alter_ug")
            cur.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_typedef}")
            cur.execute("RELEASE SAVEPOINT _alter_ug")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _alter_ug")

    # ── Device license tracking ─────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_licenses (
            id           SERIAL PRIMARY KEY,
            did          TEXT NOT NULL,
            license_name TEXT NOT NULL,
            expiry_date  TEXT NOT NULL,
            note         TEXT DEFAULT '',
            warn_days    INTEGER DEFAULT 30,
            crit_days    INTEGER DEFAULT 0,
            last_status  TEXT DEFAULT 'ok',
            created_at   DOUBLE PRECISION DEFAULT 0,
            updated_at   DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_lic_did ON device_licenses(did)"
    )

    # ── Topology map tables ──────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topo_pages (
            id   SERIAL PRIMARY KEY,
            name TEXT NOT NULL
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topo_nodes (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            type       TEXT NOT NULL,
            x          DOUBLE PRECISION DEFAULT 200,
            y          DOUBLE PRECISION DEFAULT 200,
            properties TEXT DEFAULT '{}',
            page_id    INTEGER DEFAULT 1
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topo_links (
            id        SERIAL PRIMARY KEY,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            label     TEXT DEFAULT '',
            link_type TEXT DEFAULT 'trunk',
            page_id   INTEGER DEFAULT 1
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topo_groups (
            id      SERIAL PRIMARY KEY,
            name    TEXT NOT NULL,
            color   TEXT DEFAULT '#00d4ff',
            x       DOUBLE PRECISION,
            y       DOUBLE PRECISION,
            w       DOUBLE PRECISION,
            h       DOUBLE PRECISION,
            page_id INTEGER DEFAULT 1
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS topo_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )""")
    # Seed default topology page if missing
    cur.execute(
        "INSERT INTO topo_pages (id, name) VALUES (1, 'Main') ON CONFLICT (id) DO NOTHING"
    )

    # ── LDAP domain-user support (column already part of users above) ──

    log.info("PG main schema created")


def pg_create_logs_schema(cur):
    """Create the 'logs' schema and all high-volume tables."""
    cur.execute("CREATE SCHEMA IF NOT EXISTS logs")
    cur.execute("SET search_path TO logs, public")

    # sensor_samples — partitioned by month on ts (v0.8.0+).
    # For existing non-partitioned tables, pg_migrate_to_partitioned() handles
    # the conversion at startup.  Fresh installs get the partitioned version.
    cur.execute(
        "SELECT 1 FROM pg_class WHERE relname = 'sensor_samples' "
        "AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'logs')"
    )
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE sensor_samples (
                id    BIGSERIAL,
                ts    DOUBLE PRECISION NOT NULL,
                did   TEXT NOT NULL,
                sid   TEXT NOT NULL,
                ok    INTEGER NOT NULL,
                ms    DOUBLE PRECISION,
                value TEXT
            ) PARTITION BY RANGE (ts)""")
        pg_ensure_sample_partitions(cur)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds "
            "ON sensor_samples(did, sid, ts)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds_cov "
            "ON sensor_samples(did, sid, ts, ok, ms)"
        )

    # ── Rollup tables (v0.8.0) ──────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_samples_5m (
            ts           DOUBLE PRECISION NOT NULL,
            did          TEXT NOT NULL,
            sid          TEXT NOT NULL,
            ok_count     INTEGER NOT NULL DEFAULT 0,
            fail_count   INTEGER NOT NULL DEFAULT 0,
            avg_ms       DOUBLE PRECISION,
            min_ms       DOUBLE PRECISION,
            max_ms       DOUBLE PRECISION,
            avg_ms_sq    DOUBLE PRECISION DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (did, sid, ts)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_s5m_ts ON sensor_samples_5m(ts)"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_samples_1h (
            ts           DOUBLE PRECISION NOT NULL,
            did          TEXT NOT NULL,
            sid          TEXT NOT NULL,
            ok_count     INTEGER NOT NULL DEFAULT 0,
            fail_count   INTEGER NOT NULL DEFAULT 0,
            avg_ms       DOUBLE PRECISION,
            min_ms       DOUBLE PRECISION,
            max_ms       DOUBLE PRECISION,
            avg_ms_sq    DOUBLE PRECISION DEFAULT 0,
            sample_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (did, sid, ts)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_s1h_ts ON sensor_samples_1h(ts)"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rollup_state (
            tier    TEXT PRIMARY KEY,
            last_ts DOUBLE PRECISION NOT NULL DEFAULT 0
        )""")
    cur.execute(
        "INSERT INTO rollup_state (tier, last_ts) VALUES ('5m', 0) "
        "ON CONFLICT (tier) DO NOTHING"
    )
    cur.execute(
        "INSERT INTO rollup_state (tier, last_ts) VALUES ('1h', 0) "
        "ON CONFLICT (tier) DO NOTHING"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS flap_log (
            id        SERIAL PRIMARY KEY,
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
            ack_at    DOUBLE PRECISION DEFAULT 0,
            resolved_at DOUBLE PRECISION DEFAULT 0,
            duration    DOUBLE PRECISION DEFAULT 0
        )""")

    # flap_log migrations for existing databases
    for _col, _typedef in [
        ("resolved_at", "DOUBLE PRECISION DEFAULT 0"),
        ("duration",    "DOUBLE PRECISION DEFAULT 0"),
    ]:
        try:
            cur.execute("SAVEPOINT _flog_alter")
            cur.execute(f"ALTER TABLE flap_log ADD COLUMN {_col} {_typedef}")
            cur.execute("RELEASE SAVEPOINT _flog_alter")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _flog_alter")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_err_log (
            id    SERIAL PRIMARY KEY,
            ts    TEXT,
            did   TEXT,
            sid   TEXT,
            sname TEXT,
            stype TEXT,
            msg   TEXT
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS snmp_traps (
            id                   SERIAL PRIMARY KEY,
            ts                   TEXT,
            src_ip               TEXT,
            dname                TEXT,
            community            TEXT,
            trap_oid             TEXT,
            detail               TEXT,
            vendor               TEXT DEFAULT '',
            product_family       TEXT DEFAULT '',
            trap_name            TEXT DEFAULT '',
            severity             TEXT DEFAULT 'info',
            category             TEXT DEFAULT '',
            probable_cause       TEXT DEFAULT '',
            recommended_action   TEXT DEFAULT '',
            raw_varbinds         TEXT DEFAULT '[]',
            enriched             INTEGER DEFAULT 0,
            enterprise_oid       TEXT DEFAULT '',
            generic_trap_type    INTEGER DEFAULT -1,
            enriched_varbinds    TEXT DEFAULT '[]'
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_traps_src    ON snmp_traps(src_ip, ts)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_traps_vendor ON snmp_traps(vendor, ts)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_traps_oid    ON snmp_traps(trap_oid)"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs_schema_version (
            version INTEGER PRIMARY KEY,
            applied TEXT NOT NULL,
            notes   TEXT DEFAULT ''
        )""")

    log.info("PG logs schema created")


def pg_seed_defaults(cur):
    """Seed app_settings defaults and schema_version (idempotent)."""
    cur.execute("SET search_path TO main, public")

    # Schema version baseline
    cur.execute("SELECT 1 FROM schema_version LIMIT 1")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO schema_version VALUES (1, NOW()::text, 'baseline — PostgreSQL')"
        )

    _defaults = [
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
        ("backup_sched_enabled", "0"),
        ("backup_sched_freq",    "daily"),
        ("backup_sched_time",    "02:00"),
        ("backup_sched_days",    "1,2,3,4,5,6,7"),
        ("backup_keep",          "3"),
        ("db_backup_enabled",     "0"),
        ("db_backup_freq",        "daily"),
        ("db_backup_time",        "03:00"),
        ("db_backup_days",        "1,2,3,4,5,6,7"),
        ("db_backup_keep",        "7"),
        ("db_backup_last_ts",     ""),
        ("db_backup_last_result", ""),
        ("tls_enabled",          "1"),
        ("tls_port",             "8443"),
        ("tls_cert_pem",         ""),
        ("tls_key_pem_enc",      ""),
        ("tls_cert_source",      ""),
        ("tls_cn",               ""),
        ("http_redirect",        "1"),
        ("syslog_enabled",      "0"),
        ("syslog_host",         ""),
        ("syslog_port",         "514"),
        ("syslog_proto",        "udp"),
        ("syslog_min_severity", "warning"),
        ("ldap_enabled",        "0"),
        ("ldap_server",         ""),
        ("ldap_port",           "389"),
        ("ldap_ssl",            "0"),
        ("ldap_base_dn",        ""),
        ("ldap_bind_dn",        ""),
        ("ldap_bind_pass",      ""),
        ("ldap_user_filter",    "(sAMAccountName={username})"),
        ("ldap_domain",         ""),
        ("ldap_timeout",        "10"),
        ("ldap_debug",          "0"),
        ("db_split_complete",   "1"),
        ("retention_raw_days",     "7"),
        ("retention_5m_days",      "90"),
        ("retention_1h_days",      "1095"),
        ("max_workers_executor",   "64"),
    ]
    for k, v in _defaults:
        cur.execute(
            "INSERT INTO app_settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO NOTHING",
            (k, v),
        )

    # Logs schema version
    cur.execute("SET search_path TO logs, public")
    cur.execute("SELECT 1 FROM logs_schema_version LIMIT 1")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO logs_schema_version VALUES (1, NOW()::text, 'initial — PostgreSQL')"
        )
    cur.execute("SELECT 1 FROM logs_schema_version WHERE version = 2")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO logs_schema_version VALUES "
            "(2, NOW()::text, 'v0.8.0 — rollup tables + partitioned sensor_samples')"
        )

    log.info("PG defaults seeded")


# ── Partition management (v0.8.0) ───────────────────────────────────────


def pg_ensure_sample_partitions(cur):
    """Create monthly partitions for sensor_samples covering prev month
    through 2 months ahead.  Safe to call repeatedly (IF NOT EXISTS)."""
    cur.execute("SET search_path TO logs, public")
    now = datetime.datetime.utcnow()
    for delta in range(-1, 3):
        month = now.month + delta
        year = now.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        start = datetime.datetime(year, month, 1)
        if month == 12:
            end = datetime.datetime(year + 1, 1, 1)
        else:
            end = datetime.datetime(year, month + 1, 1)
        name = f"sensor_samples_{start.strftime('%Y%m')}"
        start_ts = start.timestamp()
        end_ts = end.timestamp()
        try:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {name} "
                f"PARTITION OF sensor_samples "
                f"FOR VALUES FROM ({start_ts}) TO ({end_ts})"
            )
        except Exception as e:
            # Partition may overlap with an existing one — skip silently
            if "overlap" not in str(e).lower():
                log.warning(f"Partition {name}: {e}")


def _pg_is_partitioned(cur):
    """Return True if logs.sensor_samples is a partitioned table."""
    cur.execute("""
        SELECT 1 FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        WHERE c.relname = 'sensor_samples'
          AND c.relnamespace = (
              SELECT oid FROM pg_namespace WHERE nspname = 'logs')
    """)
    return cur.fetchone() is not None


def _pg_do_copy(cur, pg_con, dt_start, max_ts, total):
    """Copy rows from sensor_samples_old into the new partitioned table month by month."""
    copied = 0
    dt = dt_start
    while dt.timestamp() <= max_ts + 86400:
        m_next = dt.month + 1
        y_next = dt.year + (m_next - 1) // 12
        m_next = ((m_next - 1) % 12) + 1
        dt_next = datetime.datetime(y_next, m_next, 1)
        cur.execute(
            "INSERT INTO sensor_samples (id, ts, did, sid, ok, ms, value) "
            "SELECT id, ts, did, sid, ok, ms, value "
            "FROM sensor_samples_old WHERE ts >= %s AND ts < %s",
            (dt.timestamp(), dt_next.timestamp()),
        )
        chunk = cur.rowcount
        copied += chunk
        if chunk > 0:
            log.info(f"  Copied {chunk} rows for {dt.strftime('%Y-%m')} ({copied}/{total})")
        pg_con.commit()
        dt = dt_next
    return copied


def _pg_has_old_table(cur):
    """Return True if sensor_samples_old exists (incomplete prior migration)."""
    cur.execute(
        "SELECT 1 FROM pg_class WHERE relname = 'sensor_samples_old' "
        "AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'logs')"
    )
    return cur.fetchone() is not None


def pg_migrate_to_partitioned(pg_con):
    """Convert a non-partitioned sensor_samples table to range-partitioned.

    Called once at startup.  If the table is already partitioned this is a
    no-op.  The migration:
      1. Rename old table → sensor_samples_old
      2. Create new partitioned sensor_samples
      3. Create monthly partitions covering the data range
      4. Copy data in monthly chunks
      5. Recreate indexes
      6. Drop old table

    If a previous run was interrupted (sensor_samples_old exists but the copy
    was not finished), the migration resumes from step 4 automatically.
    """
    cur = pg_con.cursor()
    cur.execute("SET search_path TO logs, public")
    # Disable statement timeout for this session — the data copy can take minutes
    cur.execute("SET statement_timeout = 0")

    has_old = _pg_has_old_table(cur)

    if _pg_is_partitioned(cur) and not has_old:
        pg_ensure_sample_partitions(cur)
        pg_con.commit()
        return  # already fully migrated

    if _pg_is_partitioned(cur) and has_old:
        # Previous run was interrupted after the rename+create but before the
        # copy finished.  Resume from the copy step.
        log.info("Resuming incomplete partition migration (sensor_samples_old found) …")
        cur.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM sensor_samples_old")
        row = cur.fetchone()
        min_ts, max_ts, total = row[0], row[1], row[2]
        if not min_ts:
            cur.execute("DROP TABLE sensor_samples_old")
            pg_con.commit()
            return
        log.info(f"  {total} rows to copy from sensor_samples_old")
        dt_start = datetime.datetime.utcfromtimestamp(min_ts).replace(day=1)
        # Ensure partitions exist for the full range before copying
        pg_ensure_sample_partitions(cur)
        pg_con.commit()
        _pg_do_copy(cur, pg_con, dt_start, max_ts, total)
        cur.execute("DROP INDEX IF EXISTS idx_samples_ds")
        cur.execute("DROP INDEX IF EXISTS idx_samples_ds_cov")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_ds ON sensor_samples(did, sid, ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_samples_ds_cov ON sensor_samples(did, sid, ts, ok, ms)")
        pg_con.commit()
        cur.execute("SELECT MAX(id) FROM sensor_samples")
        max_id = cur.fetchone()[0] or 0
        cur.execute("SELECT setval(pg_get_serial_sequence('sensor_samples', 'id'), %s, true)", (max(max_id, 1),))
        pg_con.commit()
        cur.execute("DROP TABLE sensor_samples_old")
        pg_con.commit()
        log.info("Partition migration resume complete")
        return

    # Check if the table exists at all (fresh install already partitioned)
    cur.execute(
        "SELECT 1 FROM pg_class WHERE relname = 'sensor_samples' "
        "AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'logs')"
    )
    if not cur.fetchone():
        pg_con.commit()
        return  # table doesn't exist yet — pg_create_logs_schema will create it

    log.info("Migrating sensor_samples to partitioned table …")

    # 1. Find data range
    cur.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM sensor_samples")
    row = cur.fetchone()
    min_ts, max_ts, total = row[0], row[1], row[2]
    if total == 0:
        min_ts = time.time()
        max_ts = time.time()
    log.info(f"  {total} rows, ts range {min_ts} — {max_ts}")

    # 2. Rename old table
    cur.execute("ALTER TABLE sensor_samples RENAME TO sensor_samples_old")
    # Drop old indexes (they are on the old table now)
    cur.execute("DROP INDEX IF EXISTS idx_samples_ds")
    cur.execute("DROP INDEX IF EXISTS idx_samples_ds_cov")
    pg_con.commit()

    # 3. Create new partitioned table
    cur.execute("""
        CREATE TABLE sensor_samples (
            id    BIGSERIAL,
            ts    DOUBLE PRECISION NOT NULL,
            did   TEXT NOT NULL,
            sid   TEXT NOT NULL,
            ok    INTEGER NOT NULL,
            ms    DOUBLE PRECISION,
            value TEXT
        ) PARTITION BY RANGE (ts)""")
    pg_con.commit()

    # 4. Create partitions covering the data range + future
    dt_start = datetime.datetime.utcfromtimestamp(min_ts).replace(day=1)
    dt_end = datetime.datetime.utcfromtimestamp(max_ts)
    # Extend 2 months into the future
    dt_future = datetime.datetime.utcnow()
    for _ in range(3):
        m = dt_future.month + 1
        y = dt_future.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        dt_future = dt_future.replace(year=y, month=m, day=1)
    if dt_future > dt_end:
        dt_end = dt_future

    dt = dt_start
    while dt <= dt_end:
        m_next = dt.month + 1
        y_next = dt.year + (m_next - 1) // 12
        m_next = ((m_next - 1) % 12) + 1
        dt_next = datetime.datetime(y_next, m_next, 1)
        name = f"sensor_samples_{dt.strftime('%Y%m')}"
        try:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {name} "
                f"PARTITION OF sensor_samples "
                f"FOR VALUES FROM ({dt.timestamp()}) TO ({dt_next.timestamp()})"
            )
        except Exception:
            pass  # overlap — skip
        dt = dt_next
    pg_con.commit()

    # 5. Copy data in monthly chunks
    copied = _pg_do_copy(cur, pg_con, dt_start, max_ts, total)

    # 6. Recreate indexes on partitioned table
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_ds "
        "ON sensor_samples(did, sid, ts)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_ds_cov "
        "ON sensor_samples(did, sid, ts, ok, ms)"
    )
    pg_con.commit()

    # 7. Reset sequence to continue from max id
    cur.execute("SELECT MAX(id) FROM sensor_samples")
    max_id = cur.fetchone()[0] or 0
    cur.execute(
        "SELECT setval(pg_get_serial_sequence('sensor_samples', 'id'), %s, true)",
        (max(max_id, 1),),
    )
    pg_con.commit()

    # 8. Drop old table
    cur.execute("DROP TABLE sensor_samples_old")
    pg_con.commit()

    log.info(f"Partition migration complete — {copied} rows migrated")
