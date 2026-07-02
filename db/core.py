"""
db/core.py — Single-writer queue, schema init, and user seeding.
"""

import os
import queue
import shutil
import sqlite3
import threading
import time

from core.auth   import _hash_pw
from core.config import DB_PATH, LOGS_DB_PATH
from core.logger import log
from db.backend  import is_pg

# ── Single-writer queues (Main DB + Logs DB) ─────────────────────────────────
# Bounded: if the SQLite writer stalls (slow disk, AV scan, lock storm) an
# unbounded queue grows without limit — each queued autosave closure pins a
# full device/sensor snapshot. When full, new work is dropped with a
# rate-limited warning (bounded loss beats OOM, matching the sample buffer).
_DB_QUEUE:   queue.Queue = queue.Queue(maxsize=50_000)
_LOGS_QUEUE: queue.Queue = queue.Queue(maxsize=50_000)
_writers_stopped = False           # set by shutdown_writers()
_q_drop_logged   = {"main": 0.0, "logs": 0.0}


def _put_or_drop(q, fn, name):
    """Enqueue for the writer thread; drop with a warning when saturated.
    After shutdown_writers() the threads are gone — execute inline (best
    effort) so late writes aren't silently queued into the void."""
    if _writers_stopped:
        try:
            fn()
        except Exception as e:
            log.error(f"{name} DB write after writer shutdown failed: {e}")
        return
    try:
        q.put_nowait(fn)
    except queue.Full:
        now = time.time()
        if now - _q_drop_logged[name] > 60:
            log.warning(f"{name} DB writer queue full ({q.maxsize}) — dropping "
                        "write. Writer is stalled; check disk I/O and locks.")
            _q_drop_logged[name] = now


def _db_writer_loop():
    """Drain _DB_QUEUE and execute each callable sequentially."""
    try:
        while True:
            fn = _DB_QUEUE.get()
            if fn is None:   # sentinel for clean shutdown (not yet used)
                break
            try:
                fn()
            except Exception as e:
                log.error(f"DB writer error: {e}")
    except Exception as e:
        log.critical(f"DB writer thread crashed — writes will queue forever: {e}")


def _logs_writer_loop():
    """Drain _LOGS_QUEUE and execute each callable sequentially."""
    try:
        while True:
            fn = _LOGS_QUEUE.get()
            if fn is None:   # sentinel for clean shutdown (not yet used)
                break
            try:
                fn()
            except Exception as e:
                log.error(f"Logs DB writer error: {e}")
    except Exception as e:
        log.critical(f"Logs DB writer thread crashed — writes will queue forever: {e}")


_db_writer_thread   = threading.Thread(target=_db_writer_loop,   daemon=True, name="db-main-writer")
_logs_writer_thread = threading.Thread(target=_logs_writer_loop, daemon=True, name="db-logs-writer")
_db_writer_thread.start()
_logs_writer_thread.start()


def shutdown_writers(timeout: float = 10.0) -> dict:
    """Drain both SQLite writer queues and stop the writer threads.

    Sends the sentinel ``None`` each loop already checks for, then joins
    with ``timeout/2`` per thread. PG mode is a cheap no-op — the queues
    are never fed (calls execute inline) so join() returns immediately.

    Returns a summary dict the caller can log: pending-queue sizes at the
    moment of the sentinel, and whether each thread actually exited within
    the timeout. A False ``*_joined`` value means the DB is wedged and some
    writes were dropped — surface it in ops logs instead of silently
    proceeding.
    """
    global _writers_stopped
    half = max(0.1, timeout / 2)
    # Capture pending counts BEFORE enqueuing the sentinel — otherwise the
    # sentinel itself inflates the count by 1, producing the misleading
    # 'pending=1' on otherwise-idle queues.
    main_pending = _DB_QUEUE.qsize()
    logs_pending = _LOGS_QUEUE.qsize()
    _writers_stopped = True   # later enqueues execute inline instead of vanishing
    # Bounded put: on a wedged/full queue, don't hang shutdown forever.
    try: _DB_QUEUE.put(None, timeout=half)
    except queue.Full: pass
    try: _LOGS_QUEUE.put(None, timeout=half)
    except queue.Full: pass
    _db_writer_thread.join(timeout=half)
    _logs_writer_thread.join(timeout=half)
    return {
        "main_pending": main_pending,
        "logs_pending": logs_pending,
        "main_joined":  not _db_writer_thread.is_alive(),
        "logs_joined":  not _logs_writer_thread.is_alive(),
    }


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
        _put_or_drop(_DB_QUEUE, fn, "main")


def _logs_enqueue(fn):
    """Queue a zero-argument callable for the Logs DB writer thread.
    In PostgreSQL mode the callable is executed directly."""
    if is_pg():
        try:
            fn()
        except Exception as e:
            log.error(f"Logs DB writer error: {e}")
    else:
        _put_or_drop(_LOGS_QUEUE, fn, "logs")


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

        # Logs tables (flap_log, sensor_err_log, sensor_samples, snmp_traps) live
        # in pingwatch_logs.db — see logs_db_init() below. Main DB holds only
        # config, devices, users, IPAM, alerts.

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
        # Bearer-token auth for scripts / CI / Terraform / remote probes.
        # token_hash is SHA-256 of the plaintext token (plaintext never
        # stored). scope gates access: 'read' = GET/HEAD/OPTIONS only,
        # 'full' = any, 'probe' = /api/agent/* endpoints only (distributed
        # probes, v1.3), 'mcp' = /api/mcp read-only AI-agent tooling (v1.5).
        # probe_id links a probe-scoped token to its probe.
        con.execute("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash   TEXT NOT NULL UNIQUE,
                name         TEXT NOT NULL,
                username     TEXT NOT NULL,
                scope        TEXT NOT NULL CHECK(scope IN ('read','full','probe','mcp')),
                created_at   REAL NOT NULL,
                expires_at   REAL,
                last_used_at REAL,
                revoked_at   REAL,
                probe_id     TEXT DEFAULT NULL
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_user "
                    "ON api_tokens(username)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_hash "
                    "ON api_tokens(token_hash)")
        # ── MCP OAuth 2.1 (v1.5) — authorization server backing the claude.ai
        # remote-connector flow. Clients are admin-pre-registered; access
        # tokens are minted as mcp-scope api_tokens (above), so only the
        # short-lived authorization codes and rotating refresh tokens need
        # their own storage. Secrets/codes/refresh are stored as SHA-256
        # hashes, never plaintext. ────────────────────────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS mcp_oauth_clients (
                client_id          TEXT PRIMARY KEY,
                client_secret_hash TEXT NOT NULL,
                name               TEXT NOT NULL,
                redirect_uris      TEXT NOT NULL DEFAULT '[]',
                created_at         REAL NOT NULL,
                created_by         TEXT NOT NULL DEFAULT ''
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS mcp_oauth_codes (
                code_hash      TEXT PRIMARY KEY,
                client_id      TEXT NOT NULL,
                redirect_uri   TEXT NOT NULL,
                code_challenge TEXT NOT NULL,
                username       TEXT NOT NULL,
                scope          TEXT NOT NULL DEFAULT 'mcp',
                resource       TEXT DEFAULT '',
                expires_at     REAL NOT NULL,
                created_at     REAL NOT NULL
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS mcp_oauth_refresh (
                token_hash TEXT PRIMARY KEY,
                client_id  TEXT NOT NULL,
                username   TEXT NOT NULL,
                scope      TEXT NOT NULL DEFAULT 'mcp',
                expires_at REAL,
                created_at REAL NOT NULL,
                revoked_at REAL
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mcp_oauth_codes_exp "
                    "ON mcp_oauth_codes(expires_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mcp_oauth_refresh_client "
                    "ON mcp_oauth_refresh(client_id)")
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
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
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
        # db_log_audit trims per-insert with `ORDER BY ts DESC`; index ts so the
        # trim and the newest-first list query don't full-scan + sort.
        con.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")
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
                in_schedule  INTEGER DEFAULT 0,
                expected_content  TEXT    DEFAULT '',
                expected_is_regex INTEGER DEFAULT 0,
                min_bytes         INTEGER DEFAULT 0
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
                error_msg  TEXT    DEFAULT '',
                diff_lines INTEGER DEFAULT NULL
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
            ("snr_interval",       "60"),
            ("snr_timeout",        "10"),
            ("snr_fail_after",     "3"),
            ("snr_recover_after",  "2"),
            # Scale-safe per-type interval/timeout overrides for new sensors.
            # Types omitted here inherit the global Interval/Timeout above.
            ("snr_type_defaults",
             '{"ping":{"interval":30,"timeout":3},'
             '"dns":{"interval":60,"timeout":5},'
             '"snmp":{"interval":120,"timeout":15},'
             '"ssh":{"interval":120,"timeout":15},'
             '"sftp":{"interval":120,"timeout":15},'
             '"smtp":{"interval":120,"timeout":15},'
             '"vmware":{"interval":60,"timeout":10}}'),
            ("max_flaps_display",  "50"),
            ("max_flap_entries",   "2000"),
            ("max_trap_entries",   "2000"),
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
            ("ldap_tls_verify",     "0"),   # verify LDAPS/StartTLS cert (staged: default off)
            ("ldap_base_dn",        ""),
            ("ldap_bind_dn",        ""),
            ("ldap_bind_pass",      ""),    # Fernet-encrypted
            ("ldap_user_filter",    "(sAMAccountName={username})"),
            ("ldap_domain",         ""),
            ("ldap_timeout",        "10"),
            ("ldap_debug",          "0"),   # 0=login events only, 1=full debug trace
            # Data rollup (v0.8.0)
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
            # Tunables surfaced in per-feature tabs (SMTP/DB/Auto-Discovery/Sensors/Import)
            ("smtp_timeout_s",                 "10"),
            ("pg_statement_timeout_s",         "30"),
            ("pg_pool_acquire_timeout_s",      "30"),
            ("auto_discover_scan_deadline_s", "300"),
            ("sftp_checksum_max_mb",           "10"),
            ("import_max_payload_mb",          "8"),
            # Distributed probes (v1.3) — optional probe-offline email
            ("probe_offline_email",            "0"),
            ("probe_offline_email_to",         ""),
            # Startup grace: seconds after boot during which down/threshold
            # events are deferred (still-failing sensors emit at the end).
            # Soaks up restart blips (cold vCenter sessions etc.). 0 = off.
            ("startup_grace_s",                "60"),
            # Root-Cause Analysis (dependency correlation). When a device's
            # parents are all down, its own alerts are downstream symptoms.
            ("rca_suppress_downstream",        "1"),   # 1=suppress symptom alerts while root down
            ("rca_correlation_window_s",       "120"), # timing window for evidence + history clustering
        ]:
            if not con.execute("SELECT 1 FROM app_settings WHERE key=?", (_k,)).fetchone():
                con.execute("INSERT INTO app_settings VALUES (?,?)", (_k, _v))
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
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass
        # New sensor columns (debounce, thresholds, new probe fields)
        for col_def in [
            "fail_after    INTEGER DEFAULT 2",
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
        # SMTP sensor fields — auth'd probe; smtp_password is Fernet ciphertext
        for col_def in [
            "smtp_tls        TEXT DEFAULT 'none'",
            "smtp_user       TEXT DEFAULT ''",
            "smtp_password   TEXT DEFAULT ''",
            "smtp_from       TEXT DEFAULT ''",
            "smtp_rcpt       TEXT DEFAULT ''",
            "smtp_test_level TEXT DEFAULT 'ehlo'",
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col_def}")
                con.commit()
            except Exception:
                pass
        # SSH sensor fields — auth'd probe; password + private key both Fernet ciphertext
        for col_def in [
            "ssh_user        TEXT DEFAULT ''",
            "ssh_password    TEXT DEFAULT ''",
            "ssh_private_key TEXT DEFAULT ''",
            "ssh_auth_type   TEXT DEFAULT 'password'",
            "ssh_test_level  TEXT DEFAULT 'banner'",
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col_def}")
                con.commit()
            except Exception:
                pass
        # SFTP sensor fields — read-only probe, creds Fernet ciphertext
        for col_def in [
            "sftp_user            TEXT DEFAULT ''",
            "sftp_password        TEXT DEFAULT ''",
            "sftp_private_key     TEXT DEFAULT ''",
            "sftp_auth_type       TEXT DEFAULT 'password'",
            "sftp_test_level      TEXT DEFAULT 'open'",
            "sftp_remote_path     TEXT DEFAULT ''",
            "sftp_expected_sha256 TEXT DEFAULT ''",
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col_def}")
                con.commit()
            except Exception:
                pass
        # RADIUS sensor fields — AAA auth probe, shared secret + optional user creds
        for col_def in [
            "radius_secret        TEXT DEFAULT ''",
            "radius_test_level    TEXT DEFAULT 'reachable'",
            "radius_username      TEXT DEFAULT ''",
            "radius_password      TEXT DEFAULT ''",
            "radius_nas_id        TEXT DEFAULT ''",
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col_def}")
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
        # SNMPv3 device defaults (auth/priv passwords stored Fernet-encrypted)
        for col in ("snmp_v3_user_default", "snmp_v3_level_default",
                    "snmp_v3_auth_proto_default", "snmp_v3_auth_pass_default",
                    "snmp_v3_priv_proto_default", "snmp_v3_priv_pass_default",
                    "snmp_v3_context_default"):
            try:
                con.execute(f"ALTER TABLE devices ADD COLUMN {col} TEXT DEFAULT ''")
                con.commit()
            except Exception:
                pass
        # SNMPv3 per-sensor overrides — blank fields inherit device defaults
        for col in ("snmp_v3_user", "snmp_v3_level",
                    "snmp_v3_auth_proto", "snmp_v3_auth_pass",
                    "snmp_v3_priv_proto", "snmp_v3_priv_pass",
                    "snmp_v3_context"):
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {col} TEXT DEFAULT ''")
                con.commit()
            except Exception:
                pass
        # Device secondary IPs (JSON array)
        try:
            con.execute("ALTER TABLE devices ADD COLUMN secondary_ips TEXT DEFAULT '[]'")
            con.commit()
        except Exception:
            pass
        # Bulk-import external_id — links a device back to its source record in
        # PRTG / Zabbix / SolarWinds / a native JSON file. Stable across host +
        # name changes, so re-imports from the same source reconcile instead
        # of duplicating. Format: "<source>:<native_id>" (e.g. "prtg:2001").
        # NULL for manually-added and Discovery-added devices.
        try:
            con.execute("ALTER TABLE devices ADD COLUMN external_id TEXT DEFAULT NULL")
            con.commit()
        except Exception:
            pass
        # Partial unique index — NULL external_ids don't collide.
        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_devices_external_id "
                        "ON devices(external_id) WHERE external_id IS NOT NULL")
            con.commit()
        except Exception:
            pass
        # Auto-Discovery origin breadcrumb (v0.9.3+) — lets the device UI
        # answer "where did this come from + when?" without digging logs.
        for _ad_dev_col in [
            "ALTER TABLE devices ADD COLUMN discovered_at       REAL DEFAULT 0",
            "ALTER TABLE devices ADD COLUMN discovered_from_cidr TEXT DEFAULT ''",
        ]:
            try:
                con.execute(_ad_dev_col)
                con.commit()
            except Exception:
                pass  # column already exists
        # Active-sessions UI (v1.0+) — lets the user menu list and revoke
        # other browser/device sessions besides the current one.
        for _sess_col in [
            "ALTER TABLE sessions ADD COLUMN ip           TEXT DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN user_agent   TEXT DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN device_label TEXT DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN created_at   REAL DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN last_active  REAL DEFAULT 0",
        ]:
            try:
                con.execute(_sess_col)
                con.commit()
            except Exception:
                pass  # column already exists
        # Backups page enrichment (v1.0+) — surface a per-row "lines changed
        # vs previous successful backup" count so the redesigned Backups view
        # can populate its Diff column without computing diffs on the fly.
        try:
            con.execute("ALTER TABLE backup_runs ADD COLUMN diff_lines INTEGER DEFAULT NULL")
            con.commit()
        except Exception:
            pass  # column already exists
        # IPAM site grouping (v1.0+) — optional site/zone tag so the redesigned
        # IPAM sidebar can render collapsible per-site subnet groups.
        try:
            con.execute("ALTER TABLE ipam_subnets ADD COLUMN site TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass  # column already exists
        # IPAM VLAN tagging (v1.0+) — optional VLAN ID (1..4094) for cross-ref
        # with switch port config + topology. Sidebar shows "[VLAN N]" chip;
        # subnet search filter includes vlan text. 0/NULL = untagged.
        try:
            con.execute("ALTER TABLE ipam_subnets ADD COLUMN vlan INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass  # column already exists
        # Backup output validation (v1.4) — assert a real config came back, not
        # just a clean SSH/auth handshake. See backup/engine._validate_output.
        for _bk_col_def in (
            "expected_content TEXT DEFAULT ''",
            "expected_is_regex INTEGER DEFAULT 0",
            "min_bytes INTEGER DEFAULT 0",
        ):
            try:
                con.execute(f"ALTER TABLE backup_devices ADD COLUMN {_bk_col_def}")
                con.commit()
            except Exception:
                pass  # column already exists
        # Site grouping on devices (v1.0+) — Site → Group → Device hierarchy.
        # Free-text; sourced via autocomplete from UNION(ipam_subnets.site, devices.site).
        try:
            con.execute("ALTER TABLE devices ADD COLUMN site TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass  # column already exists
        # Parent device linking (v1.0+, Live Map tree) — JSON array of device IDs
        # that this device hangs off. Drives the SVG connection lines in the
        # Live Map drill-in. Empty array = orphan/root. Multi-parent for
        # dual-NIC/dual-homed devices. Group-level fallback lives in
        # topo_settings('pw_group_parents').
        try:
            con.execute("ALTER TABLE devices ADD COLUMN parent_device_ids TEXT DEFAULT '[]'")
            con.commit()
        except Exception:
            pass  # column already exists
        try:
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_devices_parent_ids ON devices(parent_device_ids)"
            )
            con.commit()
        except Exception:
            pass
        # Per-parent port wiring (v1.x+, Live Map link info). JSON dict keyed by
        # parent device id → {"lport": "<local>", "rport": "<remote>"}. Group
        # refs ("group:<name>") never appear here — wiring is per-device only.
        try:
            con.execute("ALTER TABLE devices ADD COLUMN parent_device_ports TEXT DEFAULT '{}'")
            con.commit()
        except Exception:
            pass  # column already exists
        # Sites metadata sidecar (v1.0+, Live Map NOC console).
        # Distinct site names still come from devices.site / ipam_subnets.site;
        # this table stores presentation metadata (kind, pinned, display_name)
        # so the new Live Map can colour the sidebar mosaic and sites-by-type
        # widget. Rows are created lazily by the Live Map rollup.
        con.execute("""
            CREATE TABLE IF NOT EXISTS sites (
                name         TEXT PRIMARY KEY,
                kind         TEXT NOT NULL DEFAULT 'lab',
                pinned       INTEGER NOT NULL DEFAULT 0,
                display_name TEXT NOT NULL DEFAULT '',
                sort_order   INTEGER NOT NULL DEFAULT 0,
                created_ts   INTEGER NOT NULL DEFAULT 0,
                updated_ts   INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Exclusive flag on alert profiles (v1.0+) — when an exclusive profile
        # matches the cascade, broader-scope profiles are not added. Lets users
        # opt out of the new default-additive cascade per-profile.
        try:
            con.execute("ALTER TABLE alert_profiles ADD COLUMN exclusive INTEGER NOT NULL DEFAULT 0")
            con.commit()
        except Exception:
            pass  # column already exists
        # One-shot migration: preserve "first-match wins" semantics for every
        # pre-existing profile by setting exclusive=1 on each. Gated via the
        # schema_version table so re-runs are no-ops. Fresh installs that
        # populate the Default profile do so AFTER this block (line ~1080
        # in this file) — fresh rows therefore stay exclusive=0 (additive).
        try:
            _existed = con.execute(
                "SELECT 1 FROM schema_version WHERE notes='exclusive_v1=done'"
            ).fetchone()
            if not _existed:
                con.execute("UPDATE alert_profiles SET exclusive=1")
                con.execute(
                    "INSERT OR IGNORE INTO schema_version VALUES (?, datetime('now'), ?)",
                    (1001, 'exclusive_v1=done')
                )
                con.commit()
        except Exception as _e:
            log.warning(f"exclusive_v1 migration skipped: {_e}")
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
        con.commit()
        # ── IPAM tables ───────────────────────────────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS ipam_subnets (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                cidr       TEXT UNIQUE NOT NULL,
                name       TEXT DEFAULT '',
                site       TEXT DEFAULT '',
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
                kind        TEXT DEFAULT '',
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
        # Migration: allocation kind (v1.0+) — '' (default/used), 'gateway',
        # 'reserved', 'conflict'. Drives the heatmap classification and pill
        # color in the redesigned IPAM page.
        try:
            con.execute("ALTER TABLE ip_allocations ADD COLUMN kind TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass  # column already exists
        # Migration: Auto-Discovery + per-subnet DNS columns on ipam_subnets (v0.9.3+)
        for _ad_col in [
            "ALTER TABLE ipam_subnets ADD COLUMN auto_discover       INTEGER DEFAULT 0",
            "ALTER TABLE ipam_subnets ADD COLUMN first_scan_approved INTEGER DEFAULT 0",
            "ALTER TABLE ipam_subnets ADD COLUMN last_auto_scan_ts   TEXT    DEFAULT NULL",
            "ALTER TABLE ipam_subnets ADD COLUMN dns_server          TEXT    DEFAULT ''",
            # Auto-host-scan (v1.x+) — independent of auto_discover. Sweeps for
            # alive IPs and writes ip_allocations rows with kind='discovered'.
            "ALTER TABLE ipam_subnets ADD COLUMN auto_host_scan      INTEGER DEFAULT 0",
        ]:
            try:
                con.execute(_ad_col)
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
        # Migration: user profiles + groups (v0.8+) and TOTP 2FA (v0.9.2+)
        for _col, _def in [
            ("full_name",           "TEXT DEFAULT ''"),
            ("email",               "TEXT DEFAULT ''"),
            ("group_id",            "INTEGER DEFAULT NULL"),
            ("theme_preference",    "TEXT DEFAULT 'dark'"),
            ("totp_secret",         "TEXT DEFAULT ''"),
            ("totp_enabled",        "INTEGER DEFAULT 0"),
            ("totp_recovery",       "TEXT DEFAULT ''"),
            ("totp_remember_hours", "INTEGER DEFAULT 9"),
            # SSO — federated identity subject. Format: "saml|<entity>|<nameid>" or
            # "oidc|<issuer>|<sub>". Local/LDAP/RADIUS users keep NULL.
            ("external_id",         "TEXT DEFAULT NULL"),
        ]:
            try:
                con.execute(f"ALTER TABLE users ADD COLUMN {_col} {_def}")
                con.commit()
            except Exception:
                pass
        # Unique index on external_id to prevent duplicate JIT provisioning.
        # Partial (WHERE NOT NULL) so local users with NULL external_id don't collide.
        try:
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id "
                        "ON users(external_id) WHERE external_id IS NOT NULL")
            con.commit()
        except Exception:
            pass
        # Anomaly detection — per-sensor opt-in config
        for _col, _def in [
            ("anomaly_enabled",     "INTEGER DEFAULT 0"),
            ("anomaly_sensitivity", "INTEGER DEFAULT 2"),
            ("anomaly_min_samples", "INTEGER DEFAULT 50"),
        ]:
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {_col} {_def}")
                con.commit()
            except Exception:
                pass
        # HTTPS cert-expiry thresholds (days remaining; 0 = off) — lets an
        # http sensor warn/crit on an approaching cert expiry alongside its
        # latency check, without a separate TLS sensor.
        for _col in ("cert_warn_days", "cert_crit_days"):
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {_col} INTEGER DEFAULT 0")
                con.commit()
            except Exception:
                pass
        # Pause persistence — 0 = paused (left stopped on restart), 1 = running.
        # Without this a paused device/sensor came back running after a restart.
        try:
            con.execute("ALTER TABLE sensors ADD COLUMN running INTEGER DEFAULT 1")
            con.commit()
        except Exception:
            pass
        # v1.5 SNMP template library — static value scale divisor (deci-°C,
        # KB→MB, RFC-3433 entity-sensor factors) + computed-percentage sensors
        # (two OIDs combined per snmp_pct_mode: used_total/used_free/free_total).
        for _col, _ddl in (("snmp_scale",    "REAL DEFAULT 0"),
                           ("snmp_oid2",     "TEXT DEFAULT ''"),
                           ("snmp_pct_mode", "TEXT DEFAULT ''")):
            try:
                con.execute(f"ALTER TABLE sensors ADD COLUMN {_col} {_ddl}")
                con.commit()
            except Exception:
                pass
        # Anomaly detection — EWMA baseline checkpoints (restored on startup)
        con.execute("""
            CREATE TABLE IF NOT EXISTS sensor_anomaly_baselines (
                did           TEXT NOT NULL,
                sid           TEXT NOT NULL,
                mean_ms       REAL,
                var_ms        REAL,
                sample_count  INTEGER DEFAULT 0,
                enabled_since REAL,
                updated_at    REAL,
                PRIMARY KEY (did, sid)
            )""")
        con.commit()
        # ── Alert Profiles (PRTG-style state-trigger system) ──────────
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
        # suppress_reason carries the human-readable cause when state='suppressed'
        # (currently only maintenance windows). Idempotent ALTER for existing dbs.
        try:
            con.execute("ALTER TABLE alert_events ADD COLUMN suppress_reason TEXT DEFAULT ''")
            con.commit()
        except Exception:
            pass  # column already exists
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
                enabled     INTEGER DEFAULT 1,
                created_by  TEXT    DEFAULT '',
                created_at  REAL    DEFAULT 0
            )""")
        # Migration: enabled flag on maintenance_windows so users can toggle
        # a window off without deleting it. Legacy rows default to enabled.
        try:
            con.execute("ALTER TABLE maintenance_windows ADD COLUMN enabled INTEGER DEFAULT 1")
            con.commit()
        except Exception:
            pass  # column already exists
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_groups (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT    NOT NULL UNIQUE,
                description      TEXT    DEFAULT '',
                ldap_dn          TEXT    DEFAULT '',
                radius_attribute TEXT    DEFAULT '',
                radius_value     TEXT    DEFAULT '',
                default_role     TEXT    DEFAULT 'viewer'
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
        # ── Trusted devices (Remember 2FA) ────────────────────────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS trusted_devices (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    NOT NULL,
                token_hash   TEXT    NOT NULL UNIQUE,
                device_label TEXT    DEFAULT '',
                created_at   REAL    DEFAULT 0,
                expires_at   REAL    DEFAULT 0,
                last_used_at REAL    DEFAULT 0,
                ip           TEXT    DEFAULT '',
                user_agent   TEXT    DEFAULT ''
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_trusted_dev_user "
            "ON trusted_devices(username)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_trusted_dev_exp "
            "ON trusted_devices(expires_at)"
        )
        # ── SNMP sensor templates (per-vendor OID bundles) ───────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS snmp_sensor_templates (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                vendor          TEXT NOT NULL DEFAULT '',
                description     TEXT DEFAULT '',
                items_json      TEXT NOT NULL DEFAULT '[]',
                source          TEXT NOT NULL DEFAULT 'user',
                builtin_key     TEXT DEFAULT '',
                enabled         INTEGER DEFAULT 1,
                created_by      TEXT DEFAULT '',
                created_at      REAL DEFAULT 0,
                updated_at      REAL DEFAULT 0,
                builtin_version TEXT DEFAULT '',
                user_modified   INTEGER DEFAULT 0
            )""")
        # Partial-unique index so re-seeding built-ins is an idempotent
        # upsert keyed on builtin_key (empty for user rows → not constrained).
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_snmptpl_builtin "
            "ON snmp_sensor_templates(builtin_key) WHERE builtin_key <> ''"
        )
        # v1.5 versioned seed refresh — shipped corrections reach un-edited
        # built-ins automatically; user_modified rows are never clobbered.
        for _col, _ddl in (("builtin_version", "TEXT DEFAULT ''"),
                           ("user_modified",   "INTEGER DEFAULT 0")):
            try:
                con.execute(f"ALTER TABLE snmp_sensor_templates ADD COLUMN {_col} {_ddl}")
                con.commit()
            except Exception:
                pass
        # ── Reports: templates, schedules, generated history ─────────
        con.execute("""
            CREATE TABLE IF NOT EXISTS report_templates (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                kind        TEXT NOT NULL,
                description TEXT DEFAULT '',
                config_json TEXT NOT NULL DEFAULT '{}',
                created_by  TEXT DEFAULT '',
                created_at  REAL DEFAULT 0,
                updated_at  REAL DEFAULT 0
            )""")
        con.execute("""
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
                last_run_ts      REAL DEFAULT 0,
                next_run_ts      REAL DEFAULT 0,
                created_by       TEXT DEFAULT '',
                created_at       REAL DEFAULT 0,
                updated_at       REAL DEFAULT 0
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS report_history (
                id               TEXT PRIMARY KEY,
                template_id      TEXT,
                template_name    TEXT DEFAULT '',
                schedule_id      TEXT DEFAULT '',
                kind             TEXT DEFAULT '',
                generated_at     REAL NOT NULL,
                period_start     REAL DEFAULT 0,
                period_end       REAL DEFAULT 0,
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
        # Idempotent migrations for existing installs — add the new columns
        # to report_history if it was created before they existed.
        for _col, _def in [
            ("pdf_sha256",  "TEXT DEFAULT ''"),
            ("csv_path",    "TEXT DEFAULT ''"),
            ("csv_bytes",   "INTEGER DEFAULT 0"),
            ("report_id",   "TEXT DEFAULT ''"),
        ]:
            try:
                con.execute(f"ALTER TABLE report_history ADD COLUMN {_col} {_def}")
            except Exception:
                pass
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_hist_gen "
            "ON report_history(generated_at DESC)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_report_hist_tpl "
            "ON report_history(template_id)"
        )
        con.commit()
        # Migration: LDAP group mapping columns (v0.9+) + RADIUS attribute mapping (v0.9.2+)
        # + SAML/OIDC SSO group mapping (v1.1+) — per-protocol group values so
        # admins can use different formats (e.g. SAML sends DNs, OIDC sends short names).
        for _col, _def in [
            ("ldap_dn",           "TEXT DEFAULT ''"),
            ("default_role",      "TEXT DEFAULT 'viewer'"),
            ("radius_attribute",  "TEXT DEFAULT ''"),
            ("radius_value",      "TEXT DEFAULT ''"),
            ("saml_group_value",  "TEXT DEFAULT ''"),
            ("oidc_group_value",  "TEXT DEFAULT ''"),
        ]:
            try:
                con.execute(f"ALTER TABLE user_groups ADD COLUMN {_col} {_def}")
                con.commit()
            except Exception:
                pass
        # ── Distributed probes (v1.3) ─────────────────────────────────
        # Remote agents that run sensor probes in branch networks and ship
        # results back over HTTPS. probes = registry + enrollment + liveness;
        # agent_tasks = on-demand work queue (IPAM scans, discovery sweeps).
        # Scan results never land here — they flow into the in-memory _SCANS
        # registry exactly like local scans.
        con.execute("""
            CREATE TABLE IF NOT EXISTS probes (
                probe_id          TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                description       TEXT DEFAULT '',
                status            TEXT DEFAULT 'pending',
                enroll_token_hash TEXT DEFAULT NULL,
                enroll_expires    REAL DEFAULT NULL,
                token_id          INTEGER DEFAULT NULL,
                config_version    INTEGER DEFAULT 1,
                last_seen         REAL DEFAULT 0,
                last_checkin_ip   TEXT DEFAULT '',
                agent_version     TEXT DEFAULT '',
                protocol_version  INTEGER DEFAULT 0,
                os_info           TEXT DEFAULT '',
                capabilities      TEXT DEFAULT '{}',
                spool_depth       INTEGER DEFAULT 0,
                offline_alerted   INTEGER DEFAULT 0,
                clock_skew_s      REAL DEFAULT 0,
                created_at        REAL NOT NULL,
                created_by        TEXT DEFAULT ''
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS agent_tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                probe_id      TEXT NOT NULL,
                task_type     TEXT NOT NULL,
                payload       TEXT DEFAULT '{}',
                state         TEXT DEFAULT 'pending',
                progress      TEXT DEFAULT '{}',
                error         TEXT DEFAULT '',
                created_by    TEXT DEFAULT '',
                created_at    REAL DEFAULT 0,
                dispatched_at REAL DEFAULT 0,
                finished_at   REAL DEFAULT 0
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_tasks_probe_state "
            "ON agent_tasks(probe_id, state)"
        )
        # ── Managed agent updates (v1.4) ──────────────────────────────
        # Per-probe update lifecycle + reported build identity + supervisor
        # capability, an audit log of every update attempt, and the campaign
        # orchestration tables (staged rollout / canary / auto-halt).
        for stmt in [
            "ALTER TABLE probes ADD COLUMN build_id TEXT DEFAULT ''",
            "ALTER TABLE probes ADD COLUMN supervisor INTEGER DEFAULT 0",
            "ALTER TABLE probes ADD COLUMN update_state TEXT DEFAULT ''",
            "ALTER TABLE probes ADD COLUMN update_target TEXT DEFAULT ''",
            "ALTER TABLE probes ADD COLUMN update_campaign_id INTEGER DEFAULT NULL",
            "ALTER TABLE probes ADD COLUMN update_attempt_id TEXT DEFAULT ''",
            "ALTER TABLE probes ADD COLUMN update_changed_at REAL DEFAULT 0",
            "ALTER TABLE probes ADD COLUMN update_error TEXT DEFAULT ''",
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass  # column already exists
        con.execute("""
            CREATE TABLE IF NOT EXISTS agent_update_reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                probe_id     TEXT NOT NULL,
                campaign_id  INTEGER DEFAULT NULL,
                attempt_id   TEXT DEFAULT '',
                outcome      TEXT DEFAULT '',
                from_build   TEXT DEFAULT '',
                to_build     TEXT DEFAULT '',
                target_build TEXT DEFAULT '',
                reason       TEXT DEFAULT '',
                log          TEXT DEFAULT '',
                ts           REAL DEFAULT 0
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_update_reports_probe "
            "ON agent_update_reports(probe_id, ts)"
        )
        con.execute("""
            CREATE TABLE IF NOT EXISTS update_campaigns (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT DEFAULT '',
                target_build  TEXT NOT NULL,
                package_sha256 TEXT DEFAULT '',
                canary        INTEGER DEFAULT 1,
                batch_size    INTEGER DEFAULT 5,
                halt_on_fail  INTEGER DEFAULT 1,
                window_secs   INTEGER DEFAULT 86400,
                probation_secs INTEGER DEFAULT 120,
                state         TEXT DEFAULT 'running',
                note          TEXT DEFAULT '',
                created_by    TEXT DEFAULT '',
                created_at    REAL DEFAULT 0,
                started_at    REAL DEFAULT 0,
                finished_at   REAL DEFAULT 0
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS campaign_probes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id  INTEGER NOT NULL,
                probe_id     TEXT NOT NULL,
                state        TEXT DEFAULT 'queued',
                attempt_id   TEXT DEFAULT '',
                wave         INTEGER DEFAULT 0,
                queued_at    REAL DEFAULT 0,
                started_at   REAL DEFAULT 0,
                finished_at  REAL DEFAULT 0,
                error        TEXT DEFAULT ''
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_probes_cs "
            "ON campaign_probes(campaign_id, state)"
        )
        # probe_id assignment columns — '' = inherit (sensor→device→site→
        # central), literal 'central' = explicit pin back to central probing.
        for stmt in [
            "ALTER TABLE devices ADD COLUMN probe_id TEXT DEFAULT ''",
            "ALTER TABLE sensors ADD COLUMN probe_id TEXT DEFAULT ''",
            "ALTER TABLE sites   ADD COLUMN probe_id TEXT DEFAULT ''",
        ]:
            try:
                con.execute(stmt)
                con.commit()
            except Exception:
                pass  # column already exists
        # api_tokens scope widening + probe_id. The original CHECK was
        # ('read','full'); v1.3 added 'probe' (+ the probe_id column), v1.5
        # adds 'mcp'. SQLite cannot ALTER a CHECK constraint → one-time table
        # rebuild, fired whenever the stored table SQL is missing 'mcp' (which
        # covers every older shape, with or without probe_id). The pre-migration
        # file backup at the top of db_init() already covers this DB.
        _tbl_sql_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='api_tokens'"
        ).fetchone()
        _tbl_sql = (_tbl_sql_row[0] if _tbl_sql_row else "") or ""
        if _tbl_sql and "'mcp'" not in _tbl_sql:
            try:
                _api_cols = [r[1] for r in con.execute("PRAGMA table_info(api_tokens)").fetchall()]
                _has_probe = "probe_id" in _api_cols
                con.execute("ALTER TABLE api_tokens RENAME TO api_tokens_old")
                con.execute("""
                    CREATE TABLE api_tokens (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        token_hash   TEXT NOT NULL UNIQUE,
                        name         TEXT NOT NULL,
                        username     TEXT NOT NULL,
                        scope        TEXT NOT NULL CHECK(scope IN ('read','full','probe','mcp')),
                        created_at   REAL NOT NULL,
                        expires_at   REAL,
                        last_used_at REAL,
                        revoked_at   REAL,
                        probe_id     TEXT DEFAULT NULL
                    )""")
                if _has_probe:
                    con.execute(
                        "INSERT INTO api_tokens (id, token_hash, name, username, scope,"
                        " created_at, expires_at, last_used_at, revoked_at, probe_id)"
                        " SELECT id, token_hash, name, username, scope, created_at,"
                        " expires_at, last_used_at, revoked_at, probe_id FROM api_tokens_old")
                else:
                    con.execute(
                        "INSERT INTO api_tokens (id, token_hash, name, username, scope,"
                        " created_at, expires_at, last_used_at, revoked_at)"
                        " SELECT id, token_hash, name, username, scope, created_at,"
                        " expires_at, last_used_at, revoked_at FROM api_tokens_old")
                con.execute("DROP TABLE api_tokens_old")
                con.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_user "
                            "ON api_tokens(username)")
                con.execute("CREATE INDEX IF NOT EXISTS idx_api_tokens_hash "
                            "ON api_tokens(token_hash)")
                con.commit()
                log.info("api_tokens migrated: scope CHECK widened to include 'mcp'")
            except Exception as _ae:
                con.rollback()
                log.error(f"api_tokens mcp-scope migration failed: {_ae}")
        con.commit()
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
                value TEXT,
                rate  REAL
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds "
            "ON sensor_samples(did, sid, ts)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_ds_cov "
            "ON sensor_samples(did, sid, ts, ok, ms)"
        )
        # v0.9.7: per-probe rate column for counter-type SNMP sensors.
        try:
            con.execute("ALTER TABLE sensor_samples ADD COLUMN rate REAL")
        except sqlite3.OperationalError:
            pass   # column already exists
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
                duration    REAL DEFAULT 0,
                raw_data    TEXT
            )""")
        # Migration: add columns to existing flap_log tables
        for _col, _def in [
            ("ack_state", "TEXT DEFAULT 'active'"),
            ("ack_by",    "TEXT DEFAULT ''"),
            ("ack_at",    "REAL DEFAULT 0"),
            ("resolved_at", "REAL DEFAULT 0"),
            ("duration",    "REAL DEFAULT 0"),
            ("raw_data",    "TEXT"),
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
        # db_log_err trims per-insert with a `(did,sid)` subquery on every
        # sensor error — without this index that's two full scans, and error
        # volume peaks exactly during outages.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_err_log_did_sid ON sensor_err_log(did, sid)"
        )
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
        # v0.9.6: added {avg,min,max,first,last}_value for sensors whose
        # primary display metric lives in sensor_samples.value (SNMP gauges,
        # SNMP counter rates, TLS days-until-expiry).
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
                avg_value    REAL,
                min_value    REAL,
                max_value    REAL,
                first_value  REAL,
                last_value   REAL,
                avg_rate     REAL,
                min_rate     REAL,
                max_rate     REAL,
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
                avg_value    REAL,
                min_value    REAL,
                max_value    REAL,
                first_value  REAL,
                last_value   REAL,
                avg_rate     REAL,
                min_rate     REAL,
                max_rate     REAL,
                PRIMARY KEY (did, sid, ts)
            )""")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_s1h_ts ON sensor_samples_1h(ts)"
        )
        # Idempotent add for installs that predate v0.9.6 / v0.9.7 — SQLite has
        # no IF NOT EXISTS on ADD COLUMN; swallow the "duplicate column" error.
        for _tbl in ("sensor_samples_5m", "sensor_samples_1h"):
            for _col in ("avg_value", "min_value", "max_value", "first_value", "last_value",
                         "avg_rate", "min_rate", "max_rate"):
                try:
                    con.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} REAL")
                except sqlite3.OperationalError:
                    pass   # column already exists
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
