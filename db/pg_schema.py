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
            username            TEXT PRIMARY KEY,
            pw_hash             TEXT NOT NULL,
            role                TEXT DEFAULT 'admin',
            auth_type           TEXT DEFAULT 'local',
            domain              TEXT DEFAULT '',
            full_name           TEXT DEFAULT '',
            email               TEXT DEFAULT '',
            group_id            INTEGER DEFAULT NULL,
            theme_preference    TEXT DEFAULT 'dark',
            totp_secret         TEXT DEFAULT '',
            totp_enabled        INTEGER DEFAULT 0,
            totp_recovery       TEXT DEFAULT '',
            totp_remember_hours INTEGER DEFAULT 9,
            external_id         TEXT DEFAULT NULL
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
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='main' AND table_name='users'
                             AND column_name='totp_remember_hours') THEN
                ALTER TABLE users ADD COLUMN totp_remember_hours INTEGER DEFAULT 9;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='main' AND table_name='users'
                             AND column_name='external_id') THEN
                ALTER TABLE users ADD COLUMN external_id TEXT DEFAULT NULL;
            END IF;
        END $$
    """)
    # SSO — unique index on external_id (partial, so local users with NULL don't collide)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id
            ON users(external_id) WHERE external_id IS NOT NULL
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token        TEXT PRIMARY KEY,
            username     TEXT NOT NULL,
            expires      DOUBLE PRECISION NOT NULL,
            ip           TEXT             DEFAULT '',
            user_agent   TEXT             DEFAULT '',
            device_label TEXT             DEFAULT '',
            created_at   DOUBLE PRECISION DEFAULT 0,
            last_active  DOUBLE PRECISION DEFAULT 0
        )""")
    # v1.0+ — additive columns for the Active Sessions UI; idempotent on existing installs
    for _sess_col in (
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS ip           TEXT             DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS user_agent   TEXT             DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS device_label TEXT             DEFAULT ''",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS created_at   DOUBLE PRECISION DEFAULT 0",
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_active  DOUBLE PRECISION DEFAULT 0",
    ):
        try:
            cur.execute(_sess_col)
        except Exception:
            pass

    # Bearer-token auth for scripts / CI / Terraform / remote probes.
    # token_hash is SHA-256 of the plaintext token (plaintext never stored).
    # scope gates access: 'read' = GET/HEAD/OPTIONS only, 'full' = any,
    # 'probe' = /api/agent/* only (distributed probes, v1.3).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id           SERIAL PRIMARY KEY,
            token_hash   TEXT NOT NULL UNIQUE,
            name         TEXT NOT NULL,
            username     TEXT NOT NULL,
            scope        TEXT NOT NULL CHECK (scope IN ('read','full','probe')),
            created_at   DOUBLE PRECISION NOT NULL,
            expires_at   DOUBLE PRECISION,
            last_used_at DOUBLE PRECISION,
            revoked_at   DOUBLE PRECISION,
            probe_id     TEXT DEFAULT NULL
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_user "
                "ON api_tokens(username)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_hash "
                "ON api_tokens(token_hash)")
    # Widen the scope CHECK on pre-v1.3 installs. The constraint was
    # auto-named at table creation — discover it by definition, drop, and
    # re-add with 'probe' included. Savepoint-guarded like other migrations.
    try:
        cur.execute("SAVEPOINT _api_scope")
        cur.execute("""
            SELECT con.conname, pg_get_constraintdef(con.oid) AS defn
            FROM pg_constraint con
            JOIN pg_class c      ON c.oid = con.conrelid
            JOIN pg_namespace n  ON n.oid = c.relnamespace
            WHERE n.nspname = 'main' AND c.relname = 'api_tokens'
              AND con.contype = 'c'
        """)
        for _ck in (cur.fetchall() or []):
            _nm  = _ck["conname"] if isinstance(_ck, dict) else _ck[0]
            _def = (_ck["defn"] if isinstance(_ck, dict) else _ck[1]) or ""
            if "scope" in _def and "'probe'" not in _def:
                cur.execute(f'ALTER TABLE api_tokens DROP CONSTRAINT "{_nm}"')
                cur.execute(
                    "ALTER TABLE api_tokens ADD CONSTRAINT api_tokens_scope_check "
                    "CHECK (scope IN ('read','full','probe'))")
        cur.execute("RELEASE SAVEPOINT _api_scope")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT _api_scope")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            did                      TEXT PRIMARY KEY,
            name                     TEXT,
            host                     TEXT,
            grp                      TEXT,
            site                     TEXT DEFAULT '',
            did_ctr                  INTEGER DEFAULT 0,
            webhook_url              TEXT DEFAULT '',
            alerts_muted             INTEGER DEFAULT 0,
            snmp_community_default   TEXT DEFAULT '',
            snmp_version_default     TEXT DEFAULT '',
            vmware_user_default      TEXT DEFAULT '',
            vmware_password_default  TEXT DEFAULT '',
            secondary_ips            TEXT DEFAULT '[]',
            external_id              TEXT DEFAULT NULL,
            discovered_at            DOUBLE PRECISION DEFAULT 0,
            discovered_from_cidr     TEXT DEFAULT '',
            parent_device_ids        TEXT DEFAULT '[]',
            parent_device_ports      TEXT DEFAULT '{}'
        )""")
    # Bulk-import external_id — idempotent add for pre-existing installs.
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='main' AND table_name='devices'
                             AND column_name='external_id') THEN
                ALTER TABLE devices ADD COLUMN external_id TEXT DEFAULT NULL;
            END IF;
        END $$
    """)
    # Site grouping (v1.0+) — Site → Group → Device hierarchy.
    cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS site TEXT DEFAULT ''")
    # Parent device linking (v1.0+, Live Map tree) — JSON array of device IDs.
    cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS parent_device_ids TEXT DEFAULT '[]'")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_devices_parent_ids ON devices(parent_device_ids)")
    # Per-parent port wiring (v1.x+, Live Map link info) — JSON dict keyed by
    # parent device id → {"lport": "<local>", "rport": "<remote>"}.
    cur.execute("ALTER TABLE devices ADD COLUMN IF NOT EXISTS parent_device_ports TEXT DEFAULT '{}'")
    # Partial unique index — NULL external_ids don't collide.
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_external_id
            ON devices(external_id) WHERE external_id IS NOT NULL
    """)

    # Sites metadata sidecar (v1.0+, Live Map NOC console).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sites (
            name         TEXT PRIMARY KEY,
            kind         TEXT NOT NULL DEFAULT 'lab',
            pinned       INTEGER NOT NULL DEFAULT 0,
            display_name TEXT NOT NULL DEFAULT '',
            sort_order   INTEGER NOT NULL DEFAULT 0,
            created_ts   BIGINT NOT NULL DEFAULT 0,
            updated_ts   BIGINT NOT NULL DEFAULT 0
        )
    """)

    # ── Distributed probes (v1.3) — remote agent registry + task queue ──
    # Remote agents run sensor probes in branch networks and ship results
    # back over HTTPS. probes = registry + enrollment + liveness;
    # agent_tasks = on-demand work queue (IPAM scans, discovery sweeps).
    # Scan results never land here — they flow into the in-memory _SCANS
    # registry exactly like local scans.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS probes (
            probe_id          TEXT PRIMARY KEY,
            name              TEXT NOT NULL,
            description       TEXT DEFAULT '',
            status            TEXT DEFAULT 'pending',
            enroll_token_hash TEXT DEFAULT NULL,
            enroll_expires    DOUBLE PRECISION DEFAULT NULL,
            token_id          INTEGER DEFAULT NULL,
            config_version    INTEGER DEFAULT 1,
            last_seen         DOUBLE PRECISION DEFAULT 0,
            last_checkin_ip   TEXT DEFAULT '',
            agent_version     TEXT DEFAULT '',
            protocol_version  INTEGER DEFAULT 0,
            os_info           TEXT DEFAULT '',
            capabilities      TEXT DEFAULT '{}',
            spool_depth       INTEGER DEFAULT 0,
            offline_alerted   INTEGER DEFAULT 0,
            clock_skew_s      DOUBLE PRECISION DEFAULT 0,
            created_at        DOUBLE PRECISION NOT NULL,
            created_by        TEXT DEFAULT ''
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_tasks (
            id            SERIAL PRIMARY KEY,
            probe_id      TEXT NOT NULL,
            task_type     TEXT NOT NULL,
            payload       TEXT DEFAULT '{}',
            state         TEXT DEFAULT 'pending',
            progress      TEXT DEFAULT '{}',
            error         TEXT DEFAULT '',
            created_by    TEXT DEFAULT '',
            created_at    DOUBLE PRECISION DEFAULT 0,
            dispatched_at DOUBLE PRECISION DEFAULT 0,
            finished_at   DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tasks_probe_state "
        "ON agent_tasks(probe_id, state)"
    )

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
            fail_after           INTEGER DEFAULT 2,
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
            smtp_tls             TEXT DEFAULT 'none',
            smtp_user            TEXT DEFAULT '',
            smtp_password        TEXT DEFAULT '',
            smtp_from            TEXT DEFAULT '',
            smtp_rcpt            TEXT DEFAULT '',
            smtp_test_level      TEXT DEFAULT 'ehlo',
            ssh_user             TEXT DEFAULT '',
            ssh_password         TEXT DEFAULT '',
            ssh_private_key      TEXT DEFAULT '',
            ssh_auth_type        TEXT DEFAULT 'password',
            ssh_test_level       TEXT DEFAULT 'banner',
            sftp_user            TEXT DEFAULT '',
            sftp_password        TEXT DEFAULT '',
            sftp_private_key     TEXT DEFAULT '',
            sftp_auth_type       TEXT DEFAULT 'password',
            sftp_test_level      TEXT DEFAULT 'open',
            sftp_remote_path     TEXT DEFAULT '',
            sftp_expected_sha256 TEXT DEFAULT '',
            radius_secret        TEXT DEFAULT '',
            radius_test_level    TEXT DEFAULT 'reachable',
            radius_username      TEXT DEFAULT '',
            radius_password      TEXT DEFAULT '',
            radius_nas_id        TEXT DEFAULT '',
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
        ("sensors", "anomaly_enabled",         "INTEGER DEFAULT 0"),
        ("sensors", "anomaly_sensitivity",     "INTEGER DEFAULT 2"),
        ("sensors", "anomaly_min_samples",     "INTEGER DEFAULT 50"),
        ("sensors", "smtp_tls",                "TEXT DEFAULT 'none'"),
        ("sensors", "smtp_user",               "TEXT DEFAULT ''"),
        ("sensors", "smtp_password",           "TEXT DEFAULT ''"),
        ("sensors", "smtp_from",               "TEXT DEFAULT ''"),
        ("sensors", "smtp_rcpt",               "TEXT DEFAULT ''"),
        ("sensors", "smtp_test_level",         "TEXT DEFAULT 'ehlo'"),
        ("sensors", "ssh_user",                "TEXT DEFAULT ''"),
        ("sensors", "ssh_password",            "TEXT DEFAULT ''"),
        ("sensors", "ssh_private_key",         "TEXT DEFAULT ''"),
        ("sensors", "ssh_auth_type",           "TEXT DEFAULT 'password'"),
        ("sensors", "ssh_test_level",          "TEXT DEFAULT 'banner'"),
        ("sensors", "sftp_user",               "TEXT DEFAULT ''"),
        ("sensors", "sftp_password",           "TEXT DEFAULT ''"),
        ("sensors", "sftp_private_key",        "TEXT DEFAULT ''"),
        ("sensors", "sftp_auth_type",          "TEXT DEFAULT 'password'"),
        ("sensors", "sftp_test_level",         "TEXT DEFAULT 'open'"),
        ("sensors", "sftp_remote_path",        "TEXT DEFAULT ''"),
        ("sensors", "sftp_expected_sha256",    "TEXT DEFAULT ''"),
        ("sensors", "radius_secret",           "TEXT DEFAULT ''"),
        ("sensors", "radius_test_level",       "TEXT DEFAULT 'reachable'"),
        ("sensors", "radius_username",         "TEXT DEFAULT ''"),
        ("sensors", "radius_password",         "TEXT DEFAULT ''"),
        ("sensors", "radius_nas_id",           "TEXT DEFAULT ''"),
        ("main.devices", "snmp_community_default",  "TEXT DEFAULT ''"),
        ("main.devices", "snmp_version_default",    "TEXT DEFAULT ''"),
        ("main.devices", "vmware_user_default",     "TEXT DEFAULT ''"),
        ("main.devices", "vmware_password_default", "TEXT DEFAULT ''"),
        ("main.devices", "secondary_ips",           "TEXT DEFAULT '[]'"),
        ("main.devices", "discovered_at",           "DOUBLE PRECISION DEFAULT 0"),
        ("main.devices", "discovered_from_cidr",    "TEXT DEFAULT ''"),
        # SNMPv3 device defaults (auth/priv passwords stored Fernet-encrypted)
        ("main.devices", "snmp_v3_user_default",       "TEXT DEFAULT ''"),
        ("main.devices", "snmp_v3_level_default",      "TEXT DEFAULT ''"),
        ("main.devices", "snmp_v3_auth_proto_default", "TEXT DEFAULT ''"),
        ("main.devices", "snmp_v3_auth_pass_default",  "TEXT DEFAULT ''"),
        ("main.devices", "snmp_v3_priv_proto_default", "TEXT DEFAULT ''"),
        ("main.devices", "snmp_v3_priv_pass_default",  "TEXT DEFAULT ''"),
        ("main.devices", "snmp_v3_context_default",    "TEXT DEFAULT ''"),
        # SNMPv3 per-sensor overrides (blank → inherit device default)
        ("sensors", "snmp_v3_user",       "TEXT DEFAULT ''"),
        ("sensors", "snmp_v3_level",      "TEXT DEFAULT ''"),
        ("sensors", "snmp_v3_auth_proto", "TEXT DEFAULT ''"),
        ("sensors", "snmp_v3_auth_pass",  "TEXT DEFAULT ''"),
        ("sensors", "snmp_v3_priv_proto", "TEXT DEFAULT ''"),
        ("sensors", "snmp_v3_priv_pass",  "TEXT DEFAULT ''"),
        ("sensors", "snmp_v3_context",    "TEXT DEFAULT ''"),
        # Auto-Discovery (v0.9.3+)
        ("ipam_subnets", "auto_discover",           "INTEGER DEFAULT 0"),
        ("ipam_subnets", "first_scan_approved",     "INTEGER DEFAULT 0"),
        ("ipam_subnets", "last_auto_scan_ts",       "TIMESTAMP DEFAULT NULL"),
        ("ipam_subnets", "dns_server",              "TEXT DEFAULT ''"),
        # IPAM VLAN tagging (v1.0+) — see db/core.py for rationale
        ("ipam_subnets", "vlan",                    "INTEGER DEFAULT 0"),
        # Auto-host-scan (v1.x+) — periodically sweep subnet for alive IPs
        # and populate ip_allocations with kind='discovered', WITHOUT creating
        # monitored devices. Independent of auto_discover: enable on networks
        # where you want visibility but no monitoring side-effects.
        ("ipam_subnets", "auto_host_scan",          "INTEGER DEFAULT 0"),
        # Distributed probes (v1.3) — assignment cascade sensor→device→site.
        # '' = inherit, 'central' = explicit pin back to central probing.
        ("main.devices", "probe_id",                "TEXT DEFAULT ''"),
        ("sensors",      "probe_id",                "TEXT DEFAULT ''"),
        ("main.sites",   "probe_id",                "TEXT DEFAULT ''"),
        ("main.api_tokens", "probe_id",             "TEXT DEFAULT NULL"),
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
        CREATE TABLE IF NOT EXISTS sensor_anomaly_baselines (
            did           TEXT NOT NULL,
            sid           TEXT NOT NULL,
            mean_ms       DOUBLE PRECISION,
            var_ms        DOUBLE PRECISION,
            sample_count  INTEGER DEFAULT 0,
            enabled_since DOUBLE PRECISION,
            updated_at    DOUBLE PRECISION,
            PRIMARY KEY (did, sid)
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")

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
            error_msg  TEXT    DEFAULT '',
            diff_lines INTEGER DEFAULT NULL
        )""")
    cur.execute("ALTER TABLE backup_runs ADD COLUMN IF NOT EXISTS diff_lines INTEGER DEFAULT NULL")
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
            id                  SERIAL PRIMARY KEY,
            cidr                TEXT UNIQUE NOT NULL,
            name                TEXT DEFAULT '',
            site                TEXT DEFAULT '',
            created_by          TEXT DEFAULT '',
            created_at          DOUBLE PRECISION DEFAULT 0,
            auto_discover       INTEGER DEFAULT 0,
            first_scan_approved INTEGER DEFAULT 0,
            last_auto_scan_ts   TIMESTAMP DEFAULT NULL,
            dns_server          TEXT DEFAULT '',
            auto_host_scan      INTEGER DEFAULT 0
        )""")
    cur.execute("ALTER TABLE ipam_subnets ADD COLUMN IF NOT EXISTS site TEXT DEFAULT ''")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ipam_subnets_cidr ON ipam_subnets(cidr)"
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ip_allocations (
            id              SERIAL PRIMARY KEY,
            subnet_id       INTEGER NOT NULL REFERENCES ipam_subnets(id),
            ip              TEXT NOT NULL,
            name            TEXT DEFAULT '',
            kind            TEXT DEFAULT '',
            modified_by     TEXT DEFAULT '',
            modified_at     DOUBLE PRECISION DEFAULT 0,
            device_id       TEXT DEFAULT '',
            dns_name        TEXT DEFAULT '',
            dns_resolved_at DOUBLE PRECISION DEFAULT 0,
            UNIQUE(subnet_id, ip)
        )""")
    cur.execute("ALTER TABLE ip_allocations ADD COLUMN IF NOT EXISTS kind TEXT DEFAULT ''")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ip_alloc_subnet ON ip_allocations(subnet_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_ip_alloc_device ON ip_allocations(device_id)"
    )

    # ── Alert Profiles (PRTG-style state-trigger system) ─────────────
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
    # suppress_reason carries the human-readable cause when state='suppressed'
    # (currently only maintenance windows; extend if other suppression sources
    # ever start logging rows).
    cur.execute("ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS suppress_reason TEXT DEFAULT ''")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_profiles (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            scope_type  TEXT NOT NULL DEFAULT 'global',
            scope_value TEXT DEFAULT '',
            enabled     INTEGER NOT NULL DEFAULT 1,
            exclusive   INTEGER NOT NULL DEFAULT 0,
            created_at  DOUBLE PRECISION DEFAULT 0,
            updated_at  DOUBLE PRECISION DEFAULT 0,
            UNIQUE(scope_type, scope_value)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_alert_profiles_scope "
        "ON alert_profiles(scope_type, scope_value)"
    )
    # Idempotent ALTER for existing PG installs (CREATE TABLE IF NOT EXISTS
    # above skips when the table already exists, missing the new column).
    cur.execute("ALTER TABLE alert_profiles ADD COLUMN IF NOT EXISTS exclusive INTEGER NOT NULL DEFAULT 0")
    # One-shot migration: set exclusive=1 on every pre-existing profile to
    # preserve "first-match wins" semantics. Gated via schema_version notes
    # so re-runs are no-ops.
    cur.execute("SELECT 1 FROM schema_version WHERE notes='exclusive_v1=done'")
    if not cur.fetchone():
        cur.execute("UPDATE alert_profiles SET exclusive=1")
        cur.execute(
            "INSERT INTO schema_version (version, applied, notes) "
            "VALUES (1001, NOW()::text, 'exclusive_v1=done') ON CONFLICT (version) DO NOTHING"
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
            enabled     INTEGER DEFAULT 1,
            created_by  TEXT DEFAULT '',
            created_at  DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute("ALTER TABLE maintenance_windows ADD COLUMN IF NOT EXISTS enabled INTEGER DEFAULT 1")

    # ── User Groups ──────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_groups (
            id               SERIAL PRIMARY KEY,
            name             TEXT NOT NULL UNIQUE,
            description      TEXT DEFAULT '',
            ldap_dn          TEXT DEFAULT '',
            radius_attribute TEXT DEFAULT '',
            radius_value     TEXT DEFAULT '',
            saml_group_value TEXT DEFAULT '',
            oidc_group_value TEXT DEFAULT '',
            default_role     TEXT DEFAULT 'viewer'
        )""")
    # Migration: LDAP / RADIUS / SAML / OIDC group mapping columns
    for _tbl, _col, _typedef in [
        ("user_groups", "ldap_dn",           "TEXT DEFAULT ''"),
        ("user_groups", "default_role",      "TEXT DEFAULT 'viewer'"),
        ("user_groups", "radius_attribute",  "TEXT DEFAULT ''"),
        ("user_groups", "radius_value",      "TEXT DEFAULT ''"),
        ("user_groups", "saml_group_value",  "TEXT DEFAULT ''"),
        ("user_groups", "oidc_group_value",  "TEXT DEFAULT ''"),
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

    # ── Trusted devices (Remember 2FA) ───────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trusted_devices (
            id           SERIAL PRIMARY KEY,
            username     TEXT    NOT NULL,
            token_hash   TEXT    NOT NULL UNIQUE,
            device_label TEXT    DEFAULT '',
            created_at   DOUBLE PRECISION DEFAULT 0,
            expires_at   DOUBLE PRECISION DEFAULT 0,
            last_used_at DOUBLE PRECISION DEFAULT 0,
            ip           TEXT    DEFAULT '',
            user_agent   TEXT    DEFAULT ''
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_trusted_dev_user "
        "ON trusted_devices(username)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_trusted_dev_exp "
        "ON trusted_devices(expires_at)"
    )

    # ── Reports: templates, schedules, generated history ────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_templates (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL,
            description TEXT DEFAULT '',
            config_json TEXT NOT NULL DEFAULT '{}',
            created_by  TEXT DEFAULT '',
            created_at  DOUBLE PRECISION DEFAULT 0,
            updated_at  DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_schedules (
            id               TEXT PRIMARY KEY,
            template_id      TEXT NOT NULL,
            name             TEXT NOT NULL,
            freq             TEXT NOT NULL DEFAULT 'monthly',
            time_str         TEXT NOT NULL DEFAULT '03:00',
            day_of_week      TEXT DEFAULT '1',
            day_of_month     INTEGER DEFAULT 1,
            period           TEXT NOT NULL DEFAULT 'last_month',
            timezone         TEXT DEFAULT '',
            recipient_group  INTEGER DEFAULT 0,
            recipient_emails TEXT DEFAULT '[]',
            subject_tpl      TEXT DEFAULT '',
            body_tpl         TEXT DEFAULT '',
            enabled          INTEGER DEFAULT 1,
            last_run_ts      DOUBLE PRECISION DEFAULT 0,
            next_run_ts      DOUBLE PRECISION DEFAULT 0,
            created_by       TEXT DEFAULT '',
            created_at       DOUBLE PRECISION DEFAULT 0,
            updated_at       DOUBLE PRECISION DEFAULT 0
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_history (
            id               TEXT PRIMARY KEY,
            template_id      TEXT,
            template_name    TEXT DEFAULT '',
            schedule_id      TEXT DEFAULT '',
            kind             TEXT DEFAULT '',
            generated_at     DOUBLE PRECISION NOT NULL,
            period_start     DOUBLE PRECISION DEFAULT 0,
            period_end       DOUBLE PRECISION DEFAULT 0,
            pdf_path         TEXT DEFAULT '',
            pdf_bytes        INTEGER DEFAULT 0,
            pdf_sha256       TEXT DEFAULT '',
            csv_path         TEXT DEFAULT '',
            csv_bytes        INTEGER DEFAULT 0,
            report_id        TEXT DEFAULT '',
            delivery_status  TEXT DEFAULT '',
            recipients_json  TEXT DEFAULT '[]',
            render_ms        INTEGER DEFAULT 0,
            error            TEXT DEFAULT '',
            triggered_by     TEXT DEFAULT ''
        )""")
    # Idempotent migrations — add columns to existing installs
    for _col, _typedef in [
        ("pdf_sha256", "TEXT DEFAULT ''"),
        ("csv_path",   "TEXT DEFAULT ''"),
        ("csv_bytes",  "INTEGER DEFAULT 0"),
        ("report_id",  "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute("SAVEPOINT _alter_rh")
            cur.execute(f"ALTER TABLE report_history ADD COLUMN {_col} {_typedef}")
            cur.execute("RELEASE SAVEPOINT _alter_rh")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT _alter_rh")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_hist_gen "
        "ON report_history(generated_at DESC)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_hist_tpl "
        "ON report_history(template_id)"
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
                value TEXT,
                rate  DOUBLE PRECISION
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
    # v0.9.7: per-probe rate for counter-type SNMP sensors.  NULL for
    # non-counter sensors and for the first probe after restart.
    cur.execute(
        "ALTER TABLE sensor_samples ADD COLUMN IF NOT EXISTS rate DOUBLE PRECISION"
    )

    # ── Rollup tables (v0.8.0) ──────────────────────────────────────
    # v0.9.6: added {avg,min,max,first,last}_value for sensors whose primary
    # display metric lives in sensor_samples.value (SNMP gauges, SNMP counter
    # rates, TLS days-until-expiry). first/last are bucket endpoints so
    # counter-rate sensors can derive (last - first) / bucket_duration.
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
            avg_value    DOUBLE PRECISION,
            min_value    DOUBLE PRECISION,
            max_value    DOUBLE PRECISION,
            first_value  DOUBLE PRECISION,
            last_value   DOUBLE PRECISION,
            avg_rate     DOUBLE PRECISION,
            min_rate     DOUBLE PRECISION,
            max_rate     DOUBLE PRECISION,
            PRIMARY KEY (did, sid, ts)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_s5m_ts ON sensor_samples_5m(ts)"
    )
    # Idempotent add for installs that predate v0.9.6 / v0.9.7.
    for _col in ("avg_value", "min_value", "max_value", "first_value", "last_value",
                 "avg_rate", "min_rate", "max_rate"):
        cur.execute(
            f"ALTER TABLE sensor_samples_5m ADD COLUMN IF NOT EXISTS {_col} DOUBLE PRECISION"
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
            avg_value    DOUBLE PRECISION,
            min_value    DOUBLE PRECISION,
            max_value    DOUBLE PRECISION,
            first_value  DOUBLE PRECISION,
            last_value   DOUBLE PRECISION,
            avg_rate     DOUBLE PRECISION,
            min_rate     DOUBLE PRECISION,
            max_rate     DOUBLE PRECISION,
            PRIMARY KEY (did, sid, ts)
        )""")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_s1h_ts ON sensor_samples_1h(ts)"
    )
    for _col in ("avg_value", "min_value", "max_value", "first_value", "last_value",
                 "avg_rate", "min_rate", "max_rate"):
        cur.execute(
            f"ALTER TABLE sensor_samples_1h ADD COLUMN IF NOT EXISTS {_col} DOUBLE PRECISION"
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
            duration    DOUBLE PRECISION DEFAULT 0,
            raw_data    TEXT
        )""")

    # flap_log migrations for existing databases
    for _col, _typedef in [
        ("resolved_at", "DOUBLE PRECISION DEFAULT 0"),
        ("duration",    "DOUBLE PRECISION DEFAULT 0"),
        ("raw_data",    "TEXT"),
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_err_log_did_sid ON sensor_err_log(did, sid)")

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
        ("snr_fail_after",     "2"),
        ("snr_recover_after",  "1"),
        ("max_flaps_display",  "50"),
        ("max_flap_entries",   "2000"),
        ("max_trap_entries",   "2000"),
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
        ("retention_raw_days",     "7"),
        ("retention_5m_days",      "90"),
        ("retention_1h_days",      "1095"),
        ("max_workers_executor",   "64"),
        # Retention tab — audit DB cap + log-file rotation
        ("audit_trim_cap",         "50000"),
        ("log_main_max_mb",        "10"),
        ("log_main_backups",       "14"),
        ("log_sensors_max_mb",     "20"),
        ("log_sensors_backups",    "5"),
        ("log_audit_days",         "365"),
        ("log_backup_max_mb",      "5"),
        ("log_backup_backups",     "5"),
        ("log_probes_max_mb",      "5"),
        ("log_probes_backups",     "5"),
        # Tunables surfaced in per-feature tabs
        ("smtp_timeout_s",                 "10"),
        ("pg_statement_timeout_s",         "30"),
        ("pg_pool_acquire_timeout_s",      "30"),
        ("auto_discover_scan_deadline_s", "300"),
        ("sftp_checksum_max_mb",           "10"),
        ("import_max_payload_mb",          "8"),
        # Distributed probes (v1.3) — optional probe-offline email
        ("probe_offline_email",            "0"),
        ("probe_offline_email_to",         ""),
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
