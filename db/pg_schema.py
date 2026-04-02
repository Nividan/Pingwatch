"""
db/pg_schema.py — PostgreSQL DDL for both schemas (main + logs).

Creates all tables with the full column set so no incremental ALTER TABLE
migrations are needed.  Called once at first PG startup.
"""

from core.logger import log


def pg_create_main_schema(cur):
    """Create the 'main' schema and all config/state tables."""
    cur.execute("CREATE SCHEMA IF NOT EXISTS main")
    cur.execute("SET search_path TO main, public")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username  TEXT PRIMARY KEY,
            pw_hash   TEXT NOT NULL,
            role      TEXT DEFAULT 'admin',
            auth_type TEXT DEFAULT 'local',
            domain    TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            email     TEXT DEFAULT '',
            group_id  INTEGER DEFAULT NULL
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token    TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            expires  DOUBLE PRECISION NOT NULL
        )""")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            did          TEXT PRIMARY KEY,
            name         TEXT,
            host         TEXT,
            grp          TEXT,
            did_ctr      INTEGER DEFAULT 0,
            webhook_url  TEXT DEFAULT '',
            alerts_muted INTEGER DEFAULT 0
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
            PRIMARY KEY (did, sid)
        )""")

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
        CREATE TABLE IF NOT EXISTS dashboard_widgets (
            username TEXT PRIMARY KEY,
            widgets  TEXT NOT NULL DEFAULT '[]'
        )""")

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

    # ── Alert Rules Engine ───────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_rules (
            id              SERIAL PRIMARY KEY,
            name            TEXT NOT NULL,
            enabled         INTEGER DEFAULT 1,
            severity        TEXT DEFAULT 'warning',
            condition_logic TEXT DEFAULT 'AND',
            cooldown_s      INTEGER DEFAULT 300,
            sort_order      INTEGER DEFAULT 0,
            created_at      DOUBLE PRECISION DEFAULT 0,
            updated_at      DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_rule_conditions (
            id         SERIAL PRIMARY KEY,
            rule_id    INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
            field      TEXT NOT NULL,
            op         TEXT NOT NULL,
            value      TEXT NOT NULL DEFAULT '',
            sort_order INTEGER DEFAULT 0
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_rule_actions (
            id         SERIAL PRIMARY KEY,
            rule_id    INTEGER NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
            atype      TEXT NOT NULL,
            config     TEXT NOT NULL DEFAULT '{}',
            sort_order INTEGER DEFAULT 0
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_events (
            id           SERIAL PRIMARY KEY,
            rule_id      INTEGER DEFAULT 0,
            rule_name    TEXT DEFAULT '',
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_dedup (
            sig        TEXT PRIMARY KEY,
            last_fired DOUBLE PRECISION DEFAULT 0,
            fire_count INTEGER DEFAULT 1
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
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT ''
        )""")

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sensor_samples (
            id    BIGSERIAL PRIMARY KEY,
            ts    DOUBLE PRECISION NOT NULL,
            did   TEXT NOT NULL,
            sid   TEXT NOT NULL,
            ok    INTEGER NOT NULL,
            ms    DOUBLE PRECISION,
            value TEXT
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_ds "
        "ON sensor_samples(did, sid, ts)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_ds_cov "
        "ON sensor_samples(did, sid, ts, ok, ms)"
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
            ack_at    DOUBLE PRECISION DEFAULT 0
        )""")

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

    log.info("PG defaults seeded")
