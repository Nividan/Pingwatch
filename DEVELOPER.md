# PingWatch — Developer Reference

This document covers architecture, module responsibilities, API endpoints, and how to extend PingWatch. For end-user setup and features, see [README.md](README.md).

---

## Table of Contents

- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Backend Modules](#backend-modules)
- [Route Modules](#route-modules)
- [Database Package](#database-package)
- [Frontend Structure](#frontend-structure)
- [High-Level Request Flow](#high-level-request-flow)
- [API Reference](#api-reference)
- [Extending PingWatch](#extending-pingwatch)

---

## Architecture

```
Browser / Desktop GUI
        │
        ▼
  server.py  ──  routes/          ← HTTP dispatcher + route modules
        │
        ├── core/                 ← Config, state, auth, TLS, logging, settings
        │   ├── config.py         ← Constants & route regexes
        │   ├── state.py          ← In-memory runtime state
        │   ├── app_state.py      ← Shared runtime globals
        │   ├── auth.py           ← Session management & RBAC
        │   ├── tls.py            ← TLS certificate management
        │   ├── ldap_auth.py      ← LDAP/AD authentication helpers
        │   ├── logger.py         ← Central logging
        │   └── settings.py       ← Runtime settings cache
        │
        ├── monitoring/           ← Probes, alerting, topology, subnet discovery, license checking
        │   ├── probes.py              ← Sensor engine
        │   ├── subnet_discovery.py    ← Subnet scan engine (liveness, enrichment, dup detection)
        │   ├── alert_profile_engine.py ← PRTG-style profile evaluator (cascade, stage timing, dispatch)
        │   ├── alert_dispatchers.py   ← Reusable action dispatchers (email, webhook, syslog, browser)
        │   ├── smtp_alert.py          ← SMTP helper and email rendering
        │   ├── syslog_client.py       ← RFC 5424 syslog forwarding
        │   ├── license_checker.py     ← Periodic license expiration checker (6-hour autosave hook)
        │   └── network_map.py         ← NTM topology data layer
        │
        ├── backup/               ← Config backup engine
        │   ├── engine.py         ← SSH / Telnet backup engine
        │   └── scheduler.py      ← Backup schedule runner
        │
        ├── vmware/               ← VMware vSphere integration
        │   └── client.py         ← VM discovery, metric querying, session/metric caching
        │
        ├── snmp/                 ← SNMP trap pipeline
        │   ├── receiver.py       ← UDP trap listener
        │   ├── enricher.py       ← Trap enrichment & OID lookup
        │   ├── vendor.py         ← Vendor fingerprinting
        │   ├── catalog.py        ← OID catalog queries
        │   └── seeds/            ← Built-in trap definitions
        │
        └── db/                   ← Dual-backend persistence (SQLite default / PostgreSQL production)
                                       Main: config, devices, users, groups, IPAM, settings, alerts
                                       Logs: sensor samples, flap log, SNMP traps, error log
```

**Dual-backend:** SQLite (default, zero-setup) or PostgreSQL (production/high-scale), selected via `pingwatch.conf`. All `db/` modules implement both paths gated by `is_pg()`.

**Dual write-queue design (SQLite):** two independent queue threads — one for the Main DB (`pingwatch.db`) and one for the Logs DB (`pingwatch_logs.db`). Probe threads never block on DB writes; they enqueue a lambda and continue. PostgreSQL bypasses the queues (MVCC handles concurrency).

---

## Project Structure

```
pingwatch/
├── server.py               ← HTTP/HTTPS dispatcher + entry point
├── setup_wizard.py         ← First-run interactive setup wizard
├── gui.py                  ← Desktop status window (tkinter)
├── linux/
│   ├── start.sh            ← Linux/macOS launcher + service installer
│   └── pingwatch.service   ← systemd unit file
├── windows/
│   ├── start.bat           ← Windows console launcher
│   └── pingwatch.pyw       ← Windows windowless launcher
├── requirements.txt        ← Python dependencies
├── ssh_known_hosts.txt     ← SSH TOFU host key store (auto-created)
│
├── core/
│   ├── config.py           ← File paths, compiled route regexes, startup constants
│   ├── constants.py        ← Probe & server constants (PORT_MIN/MAX, PROBE_DEFAULT_INTERVAL, SENSOR_HISTORY_SIZE, …)
│   ├── validation.py       ← Server-side input validators (validate_port, validate_host, validate_interval, …)
│   ├── settings.py         ← Thread-safe runtime settings cache (DB-backed)
│   ├── logger.py           ← App logger, audit logger, in-memory log buffer
│   ├── auth.py             ← Login, PBKDF2-SHA256, RBAC, session management
│   ├── ldap_auth.py        ← LDAP/AD auth, group search, nested membership, background sync
│   ├── app_state.py        ← Shared globals: STATE, effective ports, TLS flag, tray ref
│   ├── state.py            ← In-memory Device/Sensor objects, probe threads, SSE broadcast
│   └── tls.py              ← RSA-2048 cert generation, DB→certs/→auto-generate discovery
│
├── monitoring/
│   ├── probes.py                ← All sensor probe types (ICMP, HTTP, TCP, TLS, SNMP, DNS, Banner)
│   ├── subnet_discovery.py      ← Subnet scan engine (liveness + enrichment + duplicate detection)
│   ├── alert_profile_engine.py  ← PRTG-style profile evaluator (cascade resolution, stage timing, dispatch hook)
│   ├── alert_dispatchers.py     ← Reusable action dispatchers (email, webhook, syslog, browser push); SSRF guard; maintenance-window check
│   ├── smtp_alert.py            ← SMTP connection helper and email rendering (used by alert_dispatchers)
│   ├── syslog_client.py         ← Non-blocking RFC 5424 forwarder, bounded 500-entry queue
│   ├── license_checker.py       ← License expiration checker: compares expiry dates, fires warn/crit/ok events into flap_log, SSE broadcast
│   └── network_map.py           ← Topology pages, nodes, links, groups (DB-backed)
│
├── backup/
│   ├── engine.py           ← SSH (paramiko) + Telnet connections, TOFU key verify,
│   │                          enable-mode escalation, paging disable, per-command idle timeout
│   ├── scheduler.py        ← Cron-expression schedule runner for backup jobs
│   ├── db_backup.py        ← WAL-safe SQLite DB snapshots via sqlite3.backup(); retention policy
│   └── database/           ← Timestamped DB snapshot files (auto-created)
│
├── vmware/
│   └── client.py           ← vSphere VM discovery, metric querying, session + metric caching (pyvmomi)
│
├── snmp/
│   ├── receiver.py         ← UDP socket on SNMP port, injects traps into pipeline
│   ├── enricher.py         ← OID resolution, vendor ID, category + severity annotation
│   ├── vendor.py           ← Vendor fingerprinting from trap OIDs
│   ├── catalog.py          ← OID catalog queries
│   └── seeds/              ← Built-in trap definitions (generic, cisco, fortinet, juniper, apc)
│
├── db/
│   ├── __init__.py         ← Re-exports all public symbols
│   ├── helpers.py          ← Unified dual-backend query helpers (db_query, db_execute, db_upsert, db_cursor, _ph)
│   ├── core.py             ← Dual write-queues, schema init, user seeding
│   ├── backend.py          ← Backend selection: is_pg(), load_config() from pingwatch.conf
│   ├── pg_pool.py          ← PostgreSQL connection pool; pg_conn() / pg_cursor() context managers
│   ├── pg_schema.py        ← PostgreSQL DDL — main + logs schemas, indexes, partitioned tables
│   ├── pg_migrate.py       ← One-time SQLite-to-PostgreSQL migration tool
│   ├── migration.py        ← One-time split: legacy single-DB → Main + Logs DB (SQLite)
│   ├── persistence.py      ← Device/sensor save & load
│   ├── samples.py          ← Buffered probe writes, history & summary queries
│   ├── events.py           ← Flap log, SNMP trap log, sensor error log
│   ├── users.py            ← User management (local + LDAP), profile (full_name, email), app_settings
│   ├── groups.py           ← User group CRUD, email resolution, LDAP group mapping
│   ├── audit.py            ← Audit log write & query
│   ├── backups.py          ← Backup settings (encrypted), run history, 3-run retention
│   ├── trap_defs.py        ← SNMP trap definition queries
│   ├── ipam.py             ← Subnet and IP allocation management
│   ├── alert_profiles.py   ← Alert profile + action template CRUD; stage state tracking (alert_profile_state)
│   ├── alert_events.py     ← Alert event log — dedup, ACK/resolve, auto-resolve on recovery, badge count
│   └── licenses.py         ← Per-device license CRUD + status update; db_license_summary() for widget/badge
│
├── routes/
│   ├── auth.py             ← Login, logout, users, user/self profile PATCH
│   ├── groups.py           ← User group CRUD, member assignment, LDAP group import
│   ├── devices.py          ← Device & sensor CRUD, port scan
│   ├── monitoring.py       ← SSE, flaps, traps, SNMP
│   ├── settings.py         ← App settings, server info, restart/shutdown
│   ├── tls.py              ← TLS certificate API
│   ├── topology.py         ← NTM pages/nodes/links/groups
│   ├── export.py           ← DB export/import, audit log
│   ├── backups.py          ← Device config backup API
│   ├── alert_profiles.py   ← Alert profile + action template CRUD, profile test-fire
│   ├── alert_events.py     ← Alert history, ACK/resolve
│   ├── maintenance_windows.py ← Maintenance window CRUD
│   ├── ldap.py             ← LDAP/AD settings, test, group search, user group lookup
│   ├── ipam.py             ← IPAM subnet & IP allocation API
│   ├── discovery.py        ← Subnet discovery scan + bulk device add
│   └── licenses.py         ← Device license CRUD + expiration check trigger
│
├── certs/                  ← Optional: drop cert.pem + key.pem here
│
└── frontend/               ← Web UI (served statically)
    ├── index.html
    ├── style.css
    ├── app.js              ← Bootstrap, tab routing, shared helpers
    ├── dashboard.js        ← Customizable widget dashboard
    ├── devices.js          ← Device list and detail panel
    ├── sensors.js          ← Sensor list, detail panel, history chart; KPI tiles reflect selected time range
    ├── events.js           ← Flap/trap/error event log viewer (Active / History inner tabs)
    ├── backups.js          ← Backup table, config viewer, diff, rollback
    ├── forms-device.js     ← Add/edit device form
    ├── forms-sensor.js     ← Add/edit sensor form
    ├── forms-settings.js   ← Settings modal (10 tabs)
    ├── forms-users.js      ← User management
    ├── forms-ldap.js       ← LDAP/AD settings modal
    ├── forms-io.js         ← DB export/import form
    ├── forms-utils.js      ← Shared form helpers
    ├── forms-discovery.js  ← Subnet discovery wizard modal
    ├── ipam.js             ← IPAM tab
    ├── bg.js               ← Animated background canvas
    ├── map.html            ← Network Topology Manager shell
    ├── map.css             ← NTM styles
    └── map.js              ← NTM canvas engine
```

---

## Backend Modules

### `server.py`
HTTP(S) dispatcher and application entry point. Serves static files, delegates every API route to a `routes/` module, and starts all background threads (probe engine, autosave, backup scheduler, SNMP receiver, syslog, LDAP sync). Wraps the HTTP listener with `ssl.SSLContext` when HTTPS is enabled; optionally runs a second lightweight HTTP server for HTTP→HTTPS redirect. At startup, auto-scales the probe `ThreadPoolExecutor` using `max(64, min(512, sensor_count // 4))`; a non-zero `max_workers_executor` setting overrides this.

`Handler._error(code, public_msg, exc=None, context="")` — centralised error responder: logs the full exception (type + message) server-side with optional context label, then returns `{"error": public_msg}` to the client. No internal detail is ever leaked to the response.

### `setup_wizard.py`
Cross-platform first-run wizard. Checks required packages, handles HTTP/HTTPS port selection (with Apache2/nginx conflict detection on Linux), TLS certificate setup (including HTTP→HTTPS redirect toggle), SNMP port configuration, firewall rules, desktop shortcut creation, and optional systemd service install (Linux only). Stops any running PingWatch service before modifying the database to prevent WAL conflicts. Fixes file ownership when run via `sudo`. Flags: `--setup` (re-run wizard), `--check` (package check only).

### `core/state.py`
In-memory runtime state. Holds all `Device` and `Sensor` objects, manages probe threads, and broadcasts SSE events to all connected clients. The probe loop calculates live traffic rates for Counter32/Counter64 SNMP OIDs (`_fmt_bps`, wraparound-safe delta / elapsed) and stores the formatted rate in `last_value`. `Sensor.host_override` tracks whether the host was set manually (not inherited from the device); device IP changes propagate to all non-overridden sensors.

`Device.status` property evaluates sensor states in priority order: any `alive=False` → `"down"`, any `_threshold_state="crit"` → `"down"`, any `_threshold_state="warn"` → `"warn"`, all `alive=True` → `"up"`. This ensures VMware and other threshold-bearing sensors correctly colour the device tile without requiring a probe failure.

### `core/constants.py`
Centralised probe and server constants: `PORT_MIN` / `PORT_MAX`, `PROBE_DEFAULT_INTERVAL`, `PROBE_DEFAULT_TIMEOUT`, `SENSOR_HISTORY_SIZE` (80 samples), `HISTORY_DEFAULT_MINUTES`, `SESSION_TTL_DEFAULT_SEC`. Import from here instead of scattering magic numbers across modules.

### `core/validation.py`
Server-side input validation helpers used by route handlers before persisting user-supplied values. Functions: `validate_port(v)`, `validate_host(v)`, `validate_interval(v)`, `validate_timeout(v)`, `validate_name(v, max_len)`. Each returns `(value, None)` on success or `(None, "error message")` on failure.

### `core/auth.py`
Authentication and session management. PBKDF2-SHA256 password hashing, RBAC roles (`viewer` / `operator` / `admin`), session store, domain-prefix stripping. Branches to `core/ldap_auth.py` for users with `auth_type = ldap`.

`auth_login()` handles two LDAP paths: (1) **existing LDAP users** — after successful bind, `_ldap_login_sync()` refreshes group/role/display_name from LDAP and rejects login if the user is no longer in any imported group; (2) **unknown users** — if `ldap_enabled` and `ldap_auto_provision` are set, the user is authenticated against LDAP, matched to an imported group, and created automatically via `db_add_ldap_user()`. A race-condition guard retries the normal login path if a concurrent INSERT wins the race.

### `core/ldap_auth.py`
LDAP/AD helpers. Supports plain LDAP, LDAPS, and StartTLS. Bind password decrypted in-memory only; never logged. `ldap3` import deferred inside functions — the library is optional and local users are unaffected if absent.

Key functions:
- `ldap_authenticate(username, password)` — returns `{"ok": True, "display_name", "email", "member_of", "dn"}` on success or `None` on failure (dict is truthy, None is falsy — backward-compatible).
- `ldap_test_connection(cfg)` / `ldap_test_auth_user(username, password, cfg)` — diagnostic helpers called from the settings UI.
- `ldap_search_groups(query, cfg)` — service-account bind, searches `ldap_group_base_dn` with `ldap_group_filter`; returns `(True, [{dn, cn, description, member_count}])` or `(False, error_msg)`.
- `ldap_get_user_info(username, cfg)` — fetches DN, displayName, mail, memberOf for a user; used by the Test User Groups diagnostic.
- `ldap_check_nested_membership(user_dn, group_dn, cfg)` — uses AD's `LDAP_MATCHING_RULE_IN_CHAIN` OID (`1.2.840.113556.1.4.1941`) to resolve recursive group membership.
- `_match_user_to_groups(member_of, user_dn, mapped_groups, cfg)` — finds the best-matching imported group for a user (direct DN match first, then nested fallback); picks the group with the highest role rank (admin > operator > viewer).
- `ldap_sync_groups()` — iterates all LDAP users in the DB, checks current AD group membership, updates or disables accounts as needed; returns `{"updated": N, "disabled": N, "errors": N}`.
- `ldap_sync_loop()` — daemon thread that runs `ldap_sync_groups()` on the `ldap_sync_interval` schedule; started by `server.py` on startup.

### `core/tls.py`
TLS certificate management. RSA-2048 self-signed certificate generation (full X.509 subject + custom SANs), certificate discovery (DB → `certs/` → auto-generate), SSL context construction, expiry warnings (30-day threshold).

### `monitoring/subnet_discovery.py`
Subnet discovery scan engine. Exposes `start_scan(cidr, skip_monitored, mode)`, `get_scan(scan_id)`, and `cancel_scan(scan_id)` as the public API. Scans run in a dedicated `ThreadPoolExecutor(64)` (`_SCAN_EXECUTOR`) isolated from `STATE._executor` so large scans cannot starve existing sensor probes.

**Two scan modes** — `full` (ping + reverse DNS + ARP MAC lookup + port scan using the existing `scan_ports` setting via `_get_scan_targets()` + device-type guess) and `ping` (ping + DNS + MAC only; designed for /18–/16 ranges where port enrichment would take hours).

**Three phases per scan:** (1) parallel ICMP liveness via `probe_ping()` across all candidate IPs; (2) per-alive-host enrichment — reverse DNS (`socket.gethostbyaddr`), ARP MAC (`arp -a` Windows / `arp -n` Linux), OUI vendor lookup from a built-in ~80-entry map, and an 8-worker inner thread pool for port probing with a 6 s per-host deadline; (3) multi-NIC duplicate detection — `_hostname_fingerprint()` strips NIC suffixes (`-mgmt`, `-data`, `-iscsi`, etc.) and domain labels to normalise hostnames; results are cross-referenced against existing devices and other scan rows; matches set `possible_duplicate_of` on the row.

Scan state (`_SCANS` dict, keyed by 16-char hex UUID) is in-memory and auto-purged after 1 hour. Maximum CIDR size is /16 (65 534 hosts); larger inputs are rejected at validation. The `run_subnet_scan()` helper wraps the full flow for future scheduled-scan use.

### `monitoring/probes.py`
All sensor probe types on per-sensor background threads: ICMP, HTTP/S (status + keyword), TCP, TLS (cert validity + handshake), SNMP OID polling (v1/v2c), DNS, Banner (regex match). VMware probing is handled by `vmware/client.py`, called from `core/state.py`. `probe_snmp` uses `-On` (numeric OID output), parses stdout only (avoids MIB-warning corruption), picks the last `=`-containing line, and returns `snmp_type` (e.g. `Counter32`, `Gauge32`, `STRING`) alongside the value so the state loop can calculate rates. `snmpwalk_interfaces` walks ifTable + ifXTable to return interface index, name, description, status, and speed.

### `backup/engine.py`
SSH (paramiko) and Telnet connections to network devices. Features: TOFU SSH host key verification, password and keyboard-interactive auth (JUNOS), enable-mode escalation (Cisco), paging disable command, per-command idle timeouts, configurable command list.

### `backup/db_backup.py`
Scheduled SQLite database backup. Uses `sqlite3.backup()` (WAL-safe — safe to run while the DB is being written) to snapshot both Main DB and Logs DB into timestamped files under `backup/database/`. Applies a configurable retention policy (default: keep 7 copies). Triggered by the scheduler and also callable on demand via `POST /api/db/backup/run`.

### `monitoring/alert_profile_engine.py`
Pure-functional profile evaluator driven by the probe loop. Called from `Sensor._run_once()` after each probe cycle. `resolve_profile_for_sensor()` walks the cascade (sensor → device → group → global), returns the first matching profile, and caches the result on the sensor object (`_resolved_profile_id` / `_resolved_profile_ver`); invalidated by bumping `STATE._profile_cache_ver` whenever any profile changes. `evaluate_and_fire()` checks each stage's trigger state, delay, and repeat interval against the sensor's `_down_since_ts` / `_threshold_triggered_ts` fields. Recovery stages fire once when the sensor returns to OK (provided a state-stage previously fired in the same session) and compute total downtime duration from the `active_session` stored in `alert_profile_state`. Post-recovery, all stage rows for that sensor are cleared and the active alert event is auto-resolved.

**Recovery path note:** `_fire()` uses `if recovery: ... else: db_log_event(...)` — the `else` guard is critical. Without it, `db_log_event(state="active")` would run immediately after `db_auto_resolve_event()`, re-creating the event and leaving a stale active alert visible in the Events tab.

### `monitoring/alert_dispatchers.py`
Reusable action dispatchers extracted from the legacy rules engine: `_dispatch_email`, `_dispatch_webhook`, `_dispatch_syslog`, `_dispatch_browser`. Called by `alert_profile_engine._fire()` after building the standard `ctx` dict. Also houses `check_maintenance(ctx)` (maintenance-window suppression) and `_is_private_ip()` (SSRF guard for webhook targets).

### `monitoring/smtp_alert.py`
SMTP connection helper and HTML email rendering. `_smtp_connect()` manages the server connection and TLS/auth handshake; `_build_email_html()` / `_build_email_text()` render the notification body. Rate-limits repeated SMTP failure logs (5-minute suppression per host). Used by `alert_dispatchers._dispatch_email`.

### `monitoring/syslog_client.py`
Non-blocking RFC 5424 forwarder. Daemon queue thread with 500-entry bounded queue — monitor threads never block. Settings re-read on every send; no restart needed to reconfigure.

### `monitoring/license_checker.py`
Periodic license expiration checker. `check_license_expirations()` fetches all licenses via `db_get_all_licenses()`, computes `days_left` for each, determines `new_status` (`ok` / `warn` / `crit`) using per-license `warn_days` / `crit_days` thresholds, and fires events only when status changes (deduplication via `last_status`).

On state change: calls `db_update_license_status()`, then `db_log_flap()` with `stype='license'` and `direction='license_warn'` or `'license_crit'`. On recovery (crit/warn → ok): calls `db_auto_resolve_flap()` to close the active event and logs `direction='license_ok'`. Broadcasts `STATE._broadcast("license_status", {...})` after every state change for real-time frontend updates.

Hooked into `db/persistence.py` `autosave_loop` at `_iter % 360 == 0` (every 6 hours). Also called immediately after `POST /api/device/{did}/licenses` and `PATCH /api/license/{id}` so a newly added or updated license is evaluated right away.

### `vmware/client.py`
VMware vSphere integration via pyvmomi (optional, lazy-imported). Provides VM discovery from vCenter/ESXi and real-time metric querying for 16 VM metrics across 6 categories (CPU, Memory, Disk, Datastore, Network, System). Session caching with 25-minute TTL avoids repeated logins; metric caching with 20-second TTL (matching vSphere's realtime sampling interval) avoids redundant QueryPerf calls when multiple sensors target the same VM. `vmware_probe()` returns the standard `{ok, ms, detail, value}` probe contract. `mem_consumed_pct` uses `quickStats.guestMemoryUsage` (actual guest OS memory from VMware Tools) with fallback to `mem.active.average`.

`SmartConnect()` is wrapped with `socket.setdefaulttimeout(60)` — caps the vSphere SOAP authentication handshake at 60 seconds and restores the previous timeout in a `finally` block. Without this, a slow or unresponsive vCenter could block the probe thread indefinitely.

### `snmp/`
- `receiver.py` — UDP socket on the SNMP port, injects raw traps into the pipeline
- `enricher.py` — OID resolution, vendor identification, severity/category annotation
- `vendor.py` — Vendor fingerprinting from enterprise OIDs
- `seeds/` — Built-in trap definitions for generic, Cisco, Fortinet, Juniper, APC

---

## Route Modules

| Module | Endpoints |
|--------|-----------|
| `auth.py` | `/api/login`, `/api/logout`, `/api/me`, `/api/users`, `/api/me/password`, `/api/me/profile`, `/api/users/{u}/profile` |
| `groups.py` | `/api/groups`, `/api/group`, `/api/group/{id}`, `/api/group/{id}/members`, `/api/user/group/import_ldap` |
| `devices.py` | `/api/devices`, `/api/device`, `/api/devices/{did}`, `/api/sensors/{did}/*`, `/api/device/{did}/scan` |
| `monitoring.py` | `/events` (SSE), `/api/flaps`, `/api/traps`, `/api/events/summary`, `/api/snmp/*`, `/api/vmware/metrics`, `/api/vmware/vms` |
| `settings.py` | `/api/settings`, `/api/server_info`, `/api/settings/smtp_test`, `/api/settings/syslog_test`, `/api/server/restart`, `/api/server/shutdown`, `/api/dashboard`, `/api/db/stats` |
| `tls.py` | `/api/tls`, `/api/tls/upload`, `/api/tls/generate` |
| `topology.py` | `/api/pages`, `/api/nodes`, `/api/links`, `/api/groups`, `/api/settings/{key}` |
| `export.py` | `/api/db/export`, `/api/db/export/logs`, `/api/db/export/bundle`, `/api/db/import`, `/api/audit` |
| `backups.py` | `/api/backups`, `/api/backups/{did}`, `/api/backups/{did}/history`, `/api/backups/{did}/run`, `/api/backups/run/{id}` |
| `alert_profiles.py` | `/api/alert/profiles`, `/api/alert/profile`, `/api/alert/profile/{id}`, `/api/alert/action-templates`, `/api/alert/action-template`, `/api/alert/action-template/{id}`, `/api/alert/profile/{id}/test` |
| `alert_events.py` | `/api/alert/events`, `/api/alert/events/active`, `/api/alert/events/resolve-all`, `/api/alert/event/{id}`, `/api/alert/event/{id}/ack`, `/api/alert/event/{id}/resolve` |
| `maintenance_windows.py` | `/api/alert/windows`, `/api/alert/window`, `/api/alert/window/{id}` |
| `ldap.py` | `/api/ldap/settings`, `/api/ldap/test_connection`, `/api/ldap/test_auth`, `/api/ldap/search_groups`, `/api/ldap/test_user_groups` |
| `ipam.py` | `/api/ipam/subnets`, `/api/ipam/subnets/{id}`, `/api/ipam/subnets/{id}/ips`, `/api/ipam/ips/{subnet_id}/{ip}` |
| `discovery.py` | `/api/discovery/scan`, `/api/discovery/scan/{id}`, `/api/discovery/bulk-add` |
| `licenses.py` | `/api/device/{did}/licenses`, `/api/license/{id}`, `/api/licenses`, `/api/licenses/summary`, `/api/licenses/check` |

---

## Database Package

PingWatch supports two database backends selected via `pingwatch.conf`. All DB modules implement both paths; the `is_pg()` helper gates which branch runs.

| SQLite | PostgreSQL |
|--------|------------|
| `pingwatch.db` (main) + `pingwatch_logs.db` (logs) | Single PG server, `main` schema + `logs` schema |
| `?` placeholders, tuple rows | `%s` placeholders, `RealDictCursor` dict rows |
| Write-queue serialization (`_db_enqueue` / `_logs_enqueue`) | MVCC — queues bypassed, direct connection via `pg_conn()` / `pg_cursor()` |
| No partitioning | `sensor_samples` range-partitioned by month (auto-created) |

| Module | Responsibility |
|--------|----------------|
| `helpers.py` | Unified dual-backend query helpers — `db_query`, `db_query_one`, `db_execute`, `db_executemany`, `db_upsert`, `db_cursor`; `_ph()` converts `?` → `%s` for PG. Use these instead of inline `if is_pg()` branches in new code. |
| `backend.py` | `is_pg()`, `load_config()` / `save_config()` — reads `pingwatch.conf` to select backend |
| `pg_pool.py` | PostgreSQL connection pool; `pg_conn()` (auto-commit/rollback) and `pg_cursor()` (auto-close) context managers |
| `pg_schema.py` | PostgreSQL DDL — main + logs schemas, indexes, monthly-partitioned `sensor_samples`, rollup tables (`sensor_samples_5m`, `sensor_samples_1h`) |
| `pg_migrate.py` | One-time SQLite → PostgreSQL migration: copies all tables, verifies row counts |
| `core.py` | Dual write-queues (main + logs), schema init for both DBs, user seeding |
| `migration.py` | One-time safe split of legacy single-DB into Main + Logs DB (SQLite only) |
| `persistence.py` | Device/sensor save, load, autosave loop; named-column INSERT for sensors (column-order safe across migrations); restores `host_override` flag. Startup restore uses per-sensor indexed seeks (`WHERE did=? AND sid=? ORDER BY ts DESC LIMIT 80`) to exploit the composite index, plus a single batched `GROUP BY` for availability stats — avoids full-table window-function scans that bypass the index on large tables. |
| `samples.py` | Buffered probe writes, history & summary queries; `_pick_table` routes ≤1 day to raw `sensor_samples`, longer ranges to `sensor_samples_5m` / `sensor_samples_1h`; rollup backfill runs once on first startup (skipped if rollup table already populated) |
| `events.py` | Flap log, SNMP trap log, sensor error log |
| `users.py` | User management (local + LDAP), user profiles (`full_name`, `email`), `app_settings` key/value store |
| `groups.py` | User group CRUD, member assignment, email resolution for alert dispatch. LDAP-mapped groups carry `ldap_dn` (the AD group DN) and `default_role`. `db_get_ldap_mapped_groups()` returns all groups with a non-empty `ldap_dn` — used during login and background sync for group matching. |
| `audit.py` | Audit log write & query |
| `backups.py` | Backup settings (Fernet-encrypted credentials), run history, 3-run retention |
| `trap_defs.py` | SNMP trap definition queries |
| `ipam.py` | Subnet and IP allocation management |
| `alert_profiles.py` | Alert profile CRUD, action template CRUD, stage state tracking (`alert_profile_state`) |
| `alert_events.py` | Alert event log — dedup, ACK/resolve, auto-resolve on recovery, badge count |
| `licenses.py` | `device_licenses` table CRUD — `db_get_licenses(did)`, `db_get_all_licenses()`, `db_add_license()`, `db_update_license()`, `db_delete_license()`, `db_delete_device_licenses(did)`, `db_update_license_status()` (internal), `db_license_summary()` |

### `app_settings` table

Settings are stored as plain key/value TEXT rows. The in-memory cache (`core/settings.py`) is updated on every write. Key settings:

| Key | Format | Description |
|-----|--------|-------------|
| `scan_ports` | `"ping,22,80,443,…"` | Ports probed by the device scanner; `ping` = ICMP. Default: all 15 built-in ports |
| `snr_type_defaults` | JSON string | Per-sensor-type default intervals/timeouts |
| `backup_enc_key` | Fernet key (base64) | Encryption key for device backup credentials |
| `ldap_bind_pass` | Fernet-encrypted | LDAP service-account bind password |
| `ldap_auto_provision` | `"1"` / `"0"` | Auto-create unknown LDAP users who belong to an imported group on first login |
| `ldap_group_base_dn` | DN string | Search base for LDAP group browsing (falls back to `ldap_base_dn` when empty) |
| `ldap_group_filter` | LDAP filter | Group object filter (AD default: `(objectClass=group)`, OpenLDAP: `(objectClass=groupOfNames)`) |
| `ldap_sync_interval` | integer string (minutes) | Background LDAP group sync interval in minutes; `"0"` = disabled |
| `ldap_nested_groups` | `"1"` / `"0"` | Enable recursive AD group membership via `LDAP_MATCHING_RULE_IN_CHAIN` (AD-specific) |
| `tls_enabled` | `"1"` / `"0"` | HTTPS toggle |
| `http_port` / `https_port` | integer string | Configured listen ports |
| `db_backup_enabled` | `"1"` / `"0"` | Scheduled SQLite DB backup toggle |
| `db_backup_freq` | `"daily"` / `"weekly"` | DB backup schedule frequency |
| `db_backup_time` | `"HH:MM"` | Time of day for scheduled DB backup |
| `db_backup_days` | `"1,2,3,4,5,6,7"` | Days of week for weekly DB backup |
| `db_backup_keep` | integer string | Number of DB backup snapshots to retain (default 7) |
| `max_workers_executor` | integer string | Probe worker override (4–512). `"0"` or absent = auto (`max(64, min(512, sensor_count // 4))`). Live resize on device add/delete — no restart needed. |

---

## Frontend Structure

The frontend is served as static files — no build step.

| File | Purpose |
|------|---------|
| `index.html` | Main dashboard shell — loads all JS/CSS |
| `style.css` | Application-wide styles and CSS variables |
| `app.js` | Bootstrap, tab routing, SSE connection, shared helpers (`api()`, `toast()`, `esc()`); `TIMINGS` frozen object centralises all SSE/UI timing constants (SSE batch interval, reconnect backoff, clock update rate, etc.) |
| `dashboard.js` | Customizable widget dashboard (device cards, sparklines, uptime bars, SLA); includes `license_overview` widget — 4-KPI grid (Expired / Expiring / Valid / Total) + sorted expiration table with device name, license name, expiry date, days remaining, and status badge |
| `devices.js` | Device list, detail panel, port scan modal; status filter pills (All/Down/Warn/Up/Pause) with SSE-live counts; device list pagination (25/50/100 per page, `localStorage`-persisted); filter + status + pagination compose cleanly |
| `sensors.js` | Sensor list, detail panel, history chart; SNMP tile shows formatted rate for counter OIDs and orange warning when a non-numeric string is returned (wrong OID indicator); device tile loading skeleton (shimmer) while fresh data loads; drag-to-reorder sensor tiles with layout saved to `localStorage` per device; VMware sensors render as collapsible VM groups with per-metric rows, sparklines, formatted values (`_fmtVmVal`), and group-level mute toggle; KPI tiles (Avg/Min/Max) compute from `samples` array to match the stats bar and reflect the selected time range — Avail, Loss%, Jitter remain from hourly `summary` aggregates |
| `events.js` | Flap/trap/error event log with filters; **inner Active / History tabs** — `_evtInnerTab` state (persisted in `localStorage`), `_evtSetInnerTab()` switcher, `_isEvtActive()` helper partitions flaps by `ack_state` and traps by matched alert state (unmatched traps → History); active count badge on Active tab; "Resolve All" hidden on History tab; alert tagging — matches sensor events to alert history (90 s window), renders severity badge + profile name + state inline, ACK/Resolve buttons on active rows, refreshes on SSE `ack_event`; resolved event duration uses `resolved_at` as fixed end time (stops counting); license event support — `license_ok`→recovery, `license_warn`→warning, `license_crit`→critical severity mapping; 📋 icon for `stype='license'`; "License" option in Type filter |
| `backups.js` | Backup table, config viewer, patience diff, credential noise toggle, vendor-aware rollback; Cisco/Arista rollback includes enclosing context block + `end` + `wr` |
| `forms-device.js` | Add/edit device modal; **Licenses section** — collapsible `<details>` with status badges (Valid / Expiring / Expired), days-remaining countdown, warn/crit day inputs, add/delete per license; `_edLicLoad()`, `_edLicRender()`, `_edLicAdd()`, `_edLicDel()`, `_edLicStatusBadge()` |
| `forms-sensor.js` | Add/edit sensor modal; SNMP interface discovery (walk + metric selector); single-selection auto-syncs OID input field; device-host fallback in discover and add-selected paths; VMware VM discovery with grouped metric checkboxes, smart threshold defaults (`_VM_THR_DEFAULTS`), and bulk sensor add |
| `forms-settings.js` | Settings modal (10 tabs: General, Users, Groups, Integrations, Database, Logs, Sensors, Networking, Config Backup, Alert Profiles); each tab is built by a dedicated `_buildSettingsTab_*()` function — `openSettings()` is a thin orchestrator. Logs tab: Debug Mode checkbox auto-saves on toggle via `_saveDebugMode()` (immediate `PATCH /api/settings`; reverts on failure). Groups tab: "Import from LDAP" button (visible only when LDAP is enabled), LDAP import modal with search + role assignment, LDAP badge on imported groups, LDAP-aware group editor (shows LDAP DN read-only, `default_role` dropdown, hides member checkboxes for LDAP-managed groups) |
| `forms-users.js` | User management, Change Password modal, self-service Edit Profile modal |
| `forms-ldap.js` | LDAP/AD settings modal including Group Integration section (auto-provision, nested groups, group base DN, group filter, sync interval) and Test User Groups sub-dialog |
| `forms-io.js` | DB export/import modal |
| `forms-utils.js` | Shared form utilities and canonical helper implementations: `esc()`, `closeM()`, `_overlayClose()`, `msColor()` (latency → CSS colour), `statusClass()` (status string → CSS class), `_lsGet()` / `_lsSet()` (localStorage helpers) — all other JS modules reference these rather than maintaining local copies |
| `forms-discovery.js` | Subnet Discovery wizard — 5-step modal: CIDR input + live validation, scan progress, filterable/sortable results table (IP, hostname, MAC/vendor, ports, Type column, multi-NIC ⚠ flags), per-device sensor review, bulk add; **per-device group assignment** — default group dropdown plus per-row group input with `_discGrpFocus`/`_discGrpBlur` datalist UX; `customGroups[ip]` overrides; accent border on overridden rows |
| `alerting.js` | Alert profiles editor (PRTG-style escalation table with delay / repeat / action columns), reusable action template editor (email with user+group checkbox pickers, webhook, syslog, browser push), alert event history viewer, maintenance windows |
| `forms-group.js` | Edit Group modal — group rename and per-group alert profile (inherit / override controls with "Edit profile…" button) |
| `ipam.js` | IPAM tab — subnet list, per-subnet IP table, inline editing; **Licenses column** — `_ipamLicenseMap` (did → worst status), `_ipamLoadLicenses()` fetches `/api/licenses` in parallel with subnet load, `_ipamLicBadge(did)` renders Valid/Expiring/Expired badge; refreshed on SSE `license_status` via `_ipamOnLicenseUpdate()` |
| `bg.js` | Animated background canvas (aurora + radar) |
| `map.js` | NTM canvas engine — drag-and-drop topology editor |

---

## High-Level Request Flow

1. Browser opens the dashboard — `server.py` serves `frontend/index.html` and static assets.
2. JS opens an SSE connection to `/events`; `server.py` registers the client in `core/state.py`.
3. API calls (`api('GET', '/api/...')`) are dispatched by `server.py` to the matching `routes/` module.
4. Route handlers read/write in-memory state via `core/state.py` and enqueue DB writes via the dual write-queue in `db/core.py`.
5. Probe threads in `monitoring/probes.py` run on per-sensor intervals, push results to the Logs DB write-queue, and broadcast state-change events over SSE.
6. `backup/scheduler.py` fires backup jobs on cron schedule; `backup/engine.py` connects to the device and returns config text to `db/backups.py`.
7. `snmp/receiver.py` listens on a UDP socket; traps are enriched by `snmp/enricher.py` and injected into the flap pipeline.
8. After each probe, `monitoring/alert_profile_engine.evaluate_and_fire()` resolves the alert profile for the sensor (cached; cascade: sensor → device → group → global), evaluates each stage's delay and repeat interval, and calls `monitoring/alert_dispatchers` to send email, webhook, syslog, or browser notifications. `monitoring/syslog_client.py` forwards events asynchronously on its own daemon queue.

---

## API Reference

### Devices & Sensors

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/devices` | List all devices with latest sensor states |
| `POST` | `/api/device` | Create a device |
| `GET` | `/api/devices/{did}` | Get device detail |
| `PATCH` | `/api/devices/{did}` | Update device |
| `DELETE` | `/api/devices/{did}` | Delete device |
| `GET` | `/api/sensors/{did}` | List sensors for a device |
| `POST` | `/api/sensors/{did}` | Add a sensor |
| `PATCH` | `/api/sensors/{did}/{sid}` | Update a sensor |
| `DELETE` | `/api/sensors/{did}/{sid}` | Delete a sensor |
| `POST` | `/api/device/{did}/scan` | Trigger port scan (async) |

### Settings

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/settings` | All app settings |
| `PATCH` | `/api/settings` | Update settings (partial) |
| `GET` | `/api/server_info` | Server version, uptime, DB stats |
| `POST` | `/api/settings/smtp_test` | Send a test email |
| `POST` | `/api/settings/syslog_test` | Send a test syslog message |
| `POST` | `/api/server/restart` | Restart the server process |
| `POST` | `/api/server/shutdown` | Shutdown the server process |
| `POST` | `/api/db/backup/run` | Trigger an immediate DB snapshot *(admin only)* |

### TLS

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tls` | Certificate metadata + TLS settings (no private key) |
| `PATCH` | `/api/tls` | Update `tls_enabled`, `tls_port`, `http_redirect` |
| `POST` | `/api/tls/upload` | Upload and validate a PEM cert + key pair |
| `POST` | `/api/tls/generate` | Generate a new self-signed certificate |

### Backups

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/backups` | List all devices with latest backup metadata |
| `GET` | `/api/backups/{did}` | Backup settings for a device (no plaintext passwords) |
| `PUT` | `/api/backups/{did}` | Save backup settings |
| `GET` | `/api/backups/{did}/history` | Backup run list for a device |
| `GET` | `/api/backups/run/{id}` | Full run record including config text |
| `POST` | `/api/backups/{did}/run` | Trigger immediate backup (async, rate-limited 30 s) |
| `DELETE` | `/api/backups/run/{id}` | Delete a backup run *(admin only)* |

### LDAP

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/ldap/settings` | admin | LDAP config (bind password never returned) including group integration fields |
| `PATCH` | `/api/ldap/settings` | admin | Save LDAP config |
| `POST` | `/api/ldap/test_connection` | admin | Test service-account bind |
| `POST` | `/api/ldap/test_auth` | admin | Test full user authentication flow |
| `POST` | `/api/ldap/search_groups` | admin | Browse/search LDAP directory for groups `{query}` → `{ok, groups: [{dn, cn, description, member_count}]}` |
| `POST` | `/api/ldap/test_user_groups` | admin | Look up a user's LDAP group memberships `{username}` → `{ok, display_name, email, groups: [dn, ...]}` |

### IPAM

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ipam/subnets` | List all subnets with allocation summary |
| `POST` | `/api/ipam/subnets` | Add a subnet `{cidr, name}` |
| `DELETE` | `/api/ipam/subnets/{id}` | Remove subnet and all allocations |
| `GET` | `/api/ipam/subnets/{id}/ips` | IP allocations for a subnet |
| `PUT` | `/api/ipam/ips/{subnet_id}/{ip}` | Set or clear the name for an IP |

### Device Licenses

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/device/{did}/licenses` | viewer | List licenses for a device |
| `POST` | `/api/device/{did}/licenses` | operator | Add license `{license_name, expiry_date, note?, warn_days?, crit_days?}` → `{id, licenses[]}` |
| `PATCH` | `/api/license/{id}` | operator | Update license fields |
| `DELETE` | `/api/license/{id}` | operator | Delete a license |
| `GET` | `/api/licenses` | viewer | All licenses across all devices (for dashboard widget and IPAM map) |
| `GET` | `/api/licenses/summary` | viewer | Counts by status `{ok, warn, crit, total}` |
| `POST` | `/api/licenses/check` | admin | Trigger immediate expiration check |

### User Profiles

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/me` | any | Own username, role, full_name, email |
| `PATCH` | `/api/me/profile` | any | Update own full_name and email |
| `PATCH` | `/api/users/{u}/profile` | admin or self | Update profile; admin can also set group_id and role |

### User Groups

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/groups` | viewer | List all groups with member_count |
| `POST` | `/api/group` | admin | Create group `{name, description}` |
| `PATCH` | `/api/group/{id}` | admin | Update group name / description |
| `DELETE` | `/api/group/{id}` | admin | Delete group; members are unassigned |
| `PUT` | `/api/group/{id}/members` | admin | Replace member list `{usernames: [...]}` |
| `POST` | `/api/user/group/import_ldap` | admin | Bulk-import LDAP groups `{groups: [{dn, cn, description, default_role}]}` — idempotent (skips existing DNs) → `{ok, imported, skipped, groups}` |

### Alert Profiles

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/alert/profiles` | viewer | List all profiles with scope and stage count |
| `POST` | `/api/alert/profile` | admin | Create profile |
| `GET` | `/api/alert/profile/{id}` | viewer | Get profile with all stages |
| `PATCH` | `/api/alert/profile/{id}` | admin | Update profile and stages |
| `DELETE` | `/api/alert/profile/{id}` | admin | Delete profile |
| `POST` | `/api/alert/profile/{id}/test` | admin | Test-fire all stages with synthetic event |
| `GET` | `/api/alert/action-templates` | viewer | List all action templates |
| `POST` | `/api/alert/action-template` | admin | Create action template |
| `PATCH` | `/api/alert/action-template/{id}` | admin | Update action template |
| `DELETE` | `/api/alert/action-template/{id}` | admin | Delete action template |

### Alert Events

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/alert/events` | viewer | Paginated event history |
| `GET` | `/api/alert/events/active` | viewer | Active / unresolved events |
| `POST` | `/api/alert/events/resolve-all` | operator | Resolve all active alert events and flaps |
| `POST` | `/api/alert/event/{id}/ack` | operator | Acknowledge event |
| `POST` | `/api/alert/event/{id}/resolve` | operator | Resolve event |

### VMware

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/vmware/metrics` | viewer | Available VM metrics with labels, units, groups |
| `POST` | `/api/vmware/vms` | operator | Discover VMs on a vCenter/ESXi host |

### Maintenance Windows

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/alert/windows` | viewer | List all maintenance windows |
| `POST` | `/api/alert/window` | admin | Create window |
| `PATCH` | `/api/alert/window/{id}` | admin | Update window |
| `DELETE` | `/api/alert/window/{id}` | admin | Delete window |

### Subnet Discovery

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/discovery/scan` | operator | Start a subnet scan `{cidr, skip_monitored, mode}` → `202 {scan_id}` |
| `GET` | `/api/discovery/scan/{id}` | viewer | Poll scan progress and results |
| `DELETE` | `/api/discovery/scan/{id}` | operator | Cancel a running scan |
| `POST` | `/api/discovery/bulk-add` | operator | Bulk-create up to 500 devices with sensors `{devices: [{name, host, group, sensors: [...]}]}` |

---

## Extending PingWatch

### Adding a new sensor type

1. **`monitoring/probes.py`** — add a `probe_<type>(sensor)` function that returns `(ok, latency_ms, detail)`.
2. **`core/config.py`** — add the type string to `SENSOR_TYPES`.
3. **`frontend/forms-sensor.js`** — add the type to the sensor-type selector and its config fields.
4. **`frontend/sensors.js`** — add display logic for the new type's detail string if needed.

### Adding a new settings key

1. **`db/users.py` → `db_save_settings` / `db_load_settings`** — the `app_settings` table is key/value TEXT, no schema change needed.
2. **`routes/settings.py`** — add the key to the GET response (with default) and to the PATCH handler (with validation).
3. **`core/settings.py`** — the runtime cache is populated from the DB on startup; no changes needed unless you need a typed accessor.
4. **Frontend** — add the UI control in `forms-settings.js` and read/write via `GET /api/settings` + `PATCH /api/settings`.

### Adding a new route module

1. Create `routes/<name>.py` with a `handle(h, method, path, body)` function.
2. Register it in `server.py` by adding a route regex in `core/config.py` and a dispatch call in `server.py`'s request handler.
3. Add it to the `routes/` table in this document.
