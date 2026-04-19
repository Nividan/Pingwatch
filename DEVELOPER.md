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
        │   ├── radius_auth.py    ← RADIUS authentication helpers (PAP + Access-Challenge 2FA)
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
        ├── reports/              ← PDF/CSV report engine
        │   ├── data.py           ← Data assembly (availability, incidents, latency, inventory)
        │   ├── engine.py         ← Jinja2 + WeasyPrint HTML/PDF renderer; PDF/A-1b/2b/3b support
        │   ├── charts.py         ← Matplotlib chart builders → base64 PNG data URIs
        │   ├── runner.py         ← Orchestration: render → persist → email delivery
        │   ├── scheduler.py      ← Cron-style scheduler + hourly retention prune
        │   ├── delivery.py       ← SMTP delivery with PDF + CSV attachments
        │   ├── csv_export.py     ← Multi-section CSV sidecar (UTF-8 BOM, Excel-safe)
        │   └── templates/        ← Jinja2 HTML templates + print-first CSS
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
├── setup_wizard.py         ← First-run CLI setup wizard (headless/SSH fallback)
├── gui_setup.py            ← First-run tkinter GUI setup wizard (dark-themed, 6 steps)
├── gui.py                  ← Desktop status window (tkinter)
├── linux/
│   ├── start.sh            ← Linux/macOS launcher + service installer
│   └── pingwatch.service   ← systemd unit file
├── windows/
│   ├── start.bat           ← Windows shim (calls launcher.pyw)
│   ├── launcher.pyw        ← Python-based launcher (admin elevation, wizard, port cleanup)
│   └── pingwatch.pyw       ← Windows windowless launcher (direct server start)
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
│   ├── radius_auth.py      ← RADIUS auth (PAP + Access-Challenge 2FA), failover, challenge store, status tracker
│   ├── setup_logic.py      ← Shared setup logic (packages, ports, DB init) for CLI + GUI wizards
│   ├── app_state.py        ← Shared globals: STATE, effective ports, TLS flag, tray ref
│   ├── state.py            ← In-memory Device/Sensor objects, probe threads, SSE broadcast
│   └── tls.py              ← RSA-2048 cert generation, DB→certs/→auto-generate discovery
│
├── monitoring/
│   ├── probes.py                ← All sensor probe types (ICMP, HTTP, TCP, TLS, SNMP, DNS, Banner, SMTP, SSH, SFTP)
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
├── reports/
│   ├── data.py             ← Data assembly: availability, incidents, latency, inventory; tiered table routing
│   ├── engine.py           ← render_html() / render_pdf(); Jinja2 env with custom filters; PDF/A-1b/2b/3b via WeasyPrint
│   ├── charts.py           ← Matplotlib Agg → base64 PNG: availability_trend, severity_donut, incident_timeline, top_bar, latency_percentile_bar
│   ├── runner.py           ← render_from_template(), run_template_now(), run_schedule(); SHA-256 fingerprint + Report ID
│   ├── scheduler.py        ← Daemon thread: daily/weekly/monthly/quarterly cadence, 90 s dedupe, hourly retention prune
│   ├── delivery.py         ← send_report_email() with PDF + optional CSV attachments; recipient resolution
│   ├── csv_export.py       ← build_csv_sidecar(): multi-section UTF-8 BOM CSV (metadata, availability, incidents, latency, traps, TLS, inventory)
│   └── templates/          ← base.html, executive.html, technical.html, inventory.html, report.css (print-first @page layout)
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
│   ├── licenses.py         ← Per-device license CRUD + status update; db_license_summary() for widget/badge
│   └── reports.py          ← Report template/schedule/history CRUD; 18 functions (db_list/get/create/update/delete for templates, schedules, history; prune, record_run, set_enabled)
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
│   ├── radius.py           ← RADIUS settings, test connection, test auth, attribute mappings
│   ├── ipam.py             ← IPAM subnet & IP allocation API
│   ├── discovery.py        ← Subnet discovery scan + bulk device add
│   ├── licenses.py         ← Device license CRUD + expiration check trigger
│   └── reports.py          ← Report template/schedule/history CRUD; preview; Run Now; test-send; PDF/CSV download
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
    ├── forms-radius.js     ← RADIUS settings modal, attribute mapping table, test auth dialog
    ├── forms-io.js         ← DB export/import form
    ├── forms-utils.js      ← Shared form helpers
    ├── forms-discovery.js  ← Subnet discovery wizard modal
    ├── reports.js          ← Reports tab (Templates / Schedules / History sub-tabs); template editor modal with grouped section picker + presets for the Custom kind; preview; Run Now; test-send; history download; History multi-select + bulk delete (sticky action bar, tri-state "select all"); PDF compliance select
    ├── ipam.js             ← IPAM tab
    ├── bg.js               ← Animated background canvas
    ├── map.html            ← Network Topology Manager shell
    ├── map.css             ← NTM styles
    ├── map.js              ← NTM canvas engine
    └── fonts/              ← Self-hosted woff2 files — Exo 2, JetBrains Mono, Orbitron, Share Tech Mono (no CDN dependency; air-gapped safe)
```

---

## Backend Modules

### `server.py`
HTTP(S) dispatcher and application entry point. Serves static files, delegates every API route to a `routes/` module, and starts all background threads (probe engine, autosave, backup scheduler, SNMP receiver, syslog, LDAP sync). Wraps the HTTP listener with `ssl.SSLContext` when HTTPS is enabled; optionally runs a second lightweight HTTP server for HTTP→HTTPS redirect. At startup, auto-scales the probe `ThreadPoolExecutor` using `max(64, min(512, sensor_count // 4))`; a non-zero `max_workers_executor` setting overrides this.

`Handler._error(code, public_msg, exc=None, context="")` — centralised error responder: logs the full exception (type + message) server-side with optional context label, then returns `{"error": public_msg}` to the client. No internal detail is ever leaked to the response.

`Handler._send_with_cookies(code, data, cookies)` — sends a JSON response with multiple `Set-Cookie` headers; `cookies` is a list of pre-formatted cookie strings. Used by login and 2FA endpoints that must set both the `session` and `pw_trusted` cookies atomically in a single response.

### `setup_wizard.py`
Cross-platform first-run CLI wizard. Checks required packages, handles HTTP/HTTPS port selection (with Apache2/nginx conflict detection on Linux), TLS certificate setup (including HTTP→HTTPS redirect toggle), SNMP port configuration, firewall rules, desktop shortcut creation, and optional systemd service install (Linux only). Stops any running PingWatch service before modifying the database to prevent WAL conflicts. Fixes file ownership when run via `sudo`. Flags: `--setup` (re-run wizard), `--check` (package check only). Logic delegated to `core/setup_logic.py`.

### `gui_setup.py`
Dark-themed tkinter GUI setup wizard. 6-step flow (Welcome → Packages → Database → Network → Security → Summary) with frame-swapping `WizardController`. Step indicator dots, Back/Next/Finish navigation. Background threads for pip installs and PG connection tests. Imports all logic from `core/setup_logic.py`. Falls back to CLI `setup_wizard.py` if tkinter is unavailable. Entry point: `run_wizard() -> bool`.

### `core/setup_logic.py`
Shared non-UI setup logic used by both `setup_wizard.py` (CLI) and `gui_setup.py` (tkinter). Pure-functional helpers: `PACKAGES` list, `check_import()`, `pip_install()`, `port_in_use()`, `kill_port_processes()`, `detect_pg_server()`, `test_pg_connection()`, `generate_pg_password()`, `initialize_database()`, `save_wizard_config()`. Long-running functions accept optional `progress_cb` for GUI updates.

### `windows/launcher.pyw`
Python-based Windows launcher replacing start.bat logic. Admin elevation via `ctypes.windll.shell32.ShellExecuteW`, first-run detection (`db.backend.needs_setup()`), GUI wizard launch with CLI fallback, port cleanup via `core.setup_logic.kill_port_processes()`, then `server.main()`. The `.pyw` extension suppresses the console window.

### `core/state.py`
In-memory runtime state. Holds all `Device` and `Sensor` objects, manages probe threads, and broadcasts SSE events to all connected clients. The probe loop calculates live traffic rates for Counter32/Counter64 SNMP OIDs (`_fmt_bps`, wraparound-safe delta / elapsed) and stores the formatted rate in `last_value`. `Sensor.host_override` tracks whether the host was set manually (not inherited from the device); device IP changes propagate to all non-overridden sensors.

`Device.status` property evaluates sensor states in priority order: any `alive=False` → `"down"`, any `_threshold_state="crit"` → `"down"`, any `_threshold_state="warn"` → `"warn"`, all `alive=True` → `"up"`. Only active (running, non-muted) sensors contribute — stopped sensors are excluded so a fully-stopped device shows `"unknown"` (gray) rather than `"down"`. `stop_device()` broadcasts an SSE `device_status` event immediately after stopping all sensors and auto-resolves open flap events via `db_resolve_flaps_by_sensor()` so the Events tab clears without manual intervention.

### `core/constants.py`
Centralised probe and server constants: `PORT_MIN` / `PORT_MAX`, `PROBE_DEFAULT_INTERVAL`, `PROBE_DEFAULT_TIMEOUT`, `SENSOR_HISTORY_SIZE` (80 samples), `HISTORY_DEFAULT_MINUTES`, `SESSION_TTL_DEFAULT_SEC`. Import from here instead of scattering magic numbers across modules.

### `core/validation.py`
Server-side input validation helpers used by route handlers before persisting user-supplied values. Functions: `validate_port(v)`, `validate_host(v)`, `validate_interval(v)`, `validate_timeout(v)`, `validate_name(v, max_len)`. Each returns `(value, None)` on success or `(None, "error message")` on failure.

### `core/auth.py`
Authentication and session management. PBKDF2-SHA256 password hashing, RBAC roles (`viewer` / `operator` / `admin`), session store, domain-prefix stripping. Branches to `core/ldap_auth.py` for `auth_type='ldap'` users and to `core/radius_auth.py` for `auth_type='radius'` users.

`radius_login_phase1(username, password)` starts a RADIUS login: on `Access-Accept` it calls `_radius_post_auth()` to resolve group/role from returned attributes, auto-provision the user if needed, and issue a session (skipping the built-in TOTP check when the RADIUS server itself issued an `Access-Challenge`). On `Access-Challenge` it stores the challenge in `_RADIUS_LOGIN_CTX` (120 s TTL) and returns `{radius_challenge: true, challenge_id, prompt}`. `radius_login_phase2(challenge_id, response)` continues the flow.

`_radius_resolve_role(attrs)` walks `attrs` against all RADIUS-mapped groups via `db_find_group_by_radius(attr, value)`; first match wins. Falls back to `radius_default_group_id` → `radius_default_role`. Full attribute comparison trace emitted at DEBUG.

`parse_user_agent_label(ua)` — pure-string parser that converts a User-Agent header into a human-readable device label (e.g. `"Chrome on Windows"`, `"Firefox on Linux"`). No library dependency. Used when inserting a new trusted-device record so the Trusted Devices list shows a meaningful name instead of the raw UA string.

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
- `get_ldap_status()` — returns `{state, last_ok_ts, last_err_ts, last_err_msg}` for the Integrations status badge. State is `ok` (last activity was a success), `error` (last activity was a failure and occurred after the last success), `configured` (config present but no activity yet), or `unconfigured` (no server configured). Updated automatically by `_record_ok()` / `_record_err()` hooks wired into `ldap_test_connection`, `ldap_authenticate`, and `ldap_sync_groups`.

### `core/radius_auth.py`
RADIUS authentication helpers (pyrad, lazy-imported). Implements PAP only (covers FortiAuthenticator, NPS, FreeRADIUS, Cisco ISE in default configs).

Key functions:
- `radius_authenticate(username, password)` — sends `Access-Request` to primary (then secondary on socket/timeout error); returns `{ok: True, attrs, challenge: None}` on `Access-Accept`, `{ok: False, challenge: {id, prompt, state}}` on `Access-Challenge`, or `None` on `Access-Reject`.
- `radius_continue_challenge(challenge_id, user_response)` — echoes stored `State` blob + user response; same return shape (supports multi-step challenges).
- `radius_test_connection(cfg_overrides)` — sends a deliberately bogus `Access-Request`; any server response (Accept, Reject, or Challenge) proves host + port + shared secret are correct.
- `radius_test_auth(username, password)` — same as `radius_authenticate` but always uses live config; called from the admin Test Auth dialog.
- `get_radius_status()` — returns `{state, last_ok_ts, last_err_ts, last_err_msg}` (same schema as `get_ldap_status()`). Updated by `_record_ok()` / `_record_err()` hooks.

**Challenge store** — `_CHALLENGES` dict (module-level, `threading.Lock`) maps `challenge_id` → `{username, state, prompt, created_ts, server_idx, nas_id}`. TTL 120 s; expired entries rejected on lookup.

**Failover** — `_try_server(host, port, secret, ...)` wraps one server attempt with `radius_retries` + `radius_timeout`. Wrapper calls primary first; falls through to secondary only on socket error or timeout. `Access-Reject` is treated as a definitive answer.

**Realm munging** — `_apply_realm(cfg, username)` prepends `radius_realm_prefix` and appends `radius_realm_suffix` before the packet leaves PingWatch.

### `core/tls.py`
TLS certificate management. RSA-2048 self-signed certificate generation (full X.509 subject + custom SANs), certificate discovery (DB → `certs/` → auto-generate), SSL context construction, expiry warnings (30-day threshold).

### `monitoring/subnet_discovery.py`
Subnet discovery scan engine. Exposes `start_scan(cidr, skip_monitored, mode)`, `get_scan(scan_id)`, and `cancel_scan(scan_id)` as the public API. Scans run in a dedicated `ThreadPoolExecutor(64)` (`_SCAN_EXECUTOR`) isolated from `STATE._executor` so large scans cannot starve existing sensor probes.

**Two scan modes** — `full` (ping + reverse DNS + ARP MAC lookup + port scan using the existing `scan_ports` setting via `_get_scan_targets()` + device-type guess) and `ping` (ping + DNS + MAC only; designed for /18–/16 ranges where port enrichment would take hours).

**Three phases per scan:** (1) parallel ICMP liveness via `probe_ping()` across all candidate IPs; (2) per-alive-host enrichment — reverse DNS (`socket.gethostbyaddr`), ARP MAC (`arp -a` Windows / `arp -n` Linux), OUI vendor lookup from a built-in ~80-entry map, and an 8-worker inner thread pool for port probing with a 6 s per-host deadline; (3) multi-NIC duplicate detection — `_hostname_fingerprint()` strips NIC suffixes (`-mgmt`, `-data`, `-iscsi`, etc.) and domain labels to normalise hostnames; results are cross-referenced against existing devices and other scan rows; matches set `possible_duplicate_of` on the row.

Scan state (`_SCANS` dict, keyed by 16-char hex UUID) is in-memory and auto-purged after 1 hour. Maximum CIDR size is /16 (65 534 hosts); larger inputs are rejected at validation. The `run_subnet_scan()` helper wraps the full flow for future scheduled-scan use.

### `monitoring/probes.py`
All sensor probe types on per-sensor background threads: ICMP, HTTP/S (status + keyword), TCP, TLS (cert validity + handshake), SNMP OID polling (v1/v2c), DNS, Banner (regex match), SMTP, SSH, SFTP. VMware probing is handled by `vmware/client.py`, called from `core/state.py`. `probe_snmp` uses `-On` (numeric OID output), parses stdout only (avoids MIB-warning corruption), picks the last `=`-containing line, and returns `snmp_type` (e.g. `Counter32`, `Gauge32`, `STRING`) alongside the value so the state loop can calculate rates. `snmpwalk_interfaces` walks ifTable + ifXTable to return interface index, name, description, status, and speed.

`probe_smtp(host, port, tls_mode, test_level, user, password, mail_from, timeout)` — 5 layered depths (`connect` → `ehlo` → `starttls` → `auth` → `mailfrom`); each depth runs all prior steps. `smtplib` only — no new dependencies. Phase-tagged failure detail (e.g. `"auth: 535 Authentication failed"`).

`probe_ssh(host, port, test_level, auth_type, user, password, private_key, timeout)` — 3 depths (`connect` → `banner` → `auth`); `banner` depth captures the SSH version string in `detail`. `paramiko` lazy-imported. `_load_ssh_key()` helper handles Ed25519 / RSA / ECDSA PEM from `io.StringIO`.

`probe_sftp(host, port, user, password, private_key, auth_type, test_level, remote_path, expected_sha256, timeout)` — 4 depths (`open` → `list` → `stat` → `checksum`). `checksum` level streams at most 10 MB via 65 536-byte chunks into `hashlib.sha256()`; files larger than the cap return `"checksum: file exceeds 10MB cap"` (pre-flight `stat` check). All operations are read-only. `paramiko` lazy-imported; shares `_load_ssh_key()` with SSH probe.

### `backup/engine.py`
SSH (paramiko) and Telnet connections to network devices. Features: TOFU SSH host key verification, password and keyboard-interactive auth (JUNOS), enable-mode escalation (Cisco), paging disable command, per-command idle timeouts, configurable command list.

### `backup/db_backup.py`
Scheduled SQLite database backup. Uses `sqlite3.backup()` (WAL-safe — safe to run while the DB is being written) to snapshot both Main DB and Logs DB into timestamped files under `backup/database/`. Applies a configurable retention policy (default: keep 7 copies). Triggered by the scheduler and also callable on demand via `POST /api/db/backup/run`.

### `monitoring/alert_profile_engine.py`
Pure-functional profile evaluator driven by the probe loop. Called from `Sensor._run_once()` after each probe cycle. `resolve_profile_for_sensor()` walks the cascade (sensor → device → group → global), returns the first matching profile, and caches the result on the sensor object (`_resolved_profile_id` / `_resolved_profile_ver`); invalidated by bumping `STATE._profile_cache_ver` whenever any profile changes. `evaluate_and_fire()` checks each stage's trigger state, delay, and repeat interval against the sensor's `_down_since_ts` / `_threshold_triggered_ts` fields. Recovery stages fire once when the sensor returns to OK (provided a state-stage previously fired in the same session) and compute total downtime duration from the `active_session` stored in `alert_profile_state`. Post-recovery, all stage rows for that sensor are cleared and the active alert event is auto-resolved.

**Recovery path note:** `_fire()` uses `if recovery: ... else: db_log_event(...)` — the `else` guard is critical. Without it, `db_log_event(state="active")` would run immediately after `db_auto_resolve_event()`, re-creating the event and leaving a stale active alert visible in the Events tab.

### `monitoring/anomaly.py`
Opt-in per-sensor learned-baseline detector. Pure function — no I/O — so the probe hot path stays O(1). `evaluate_anomaly(sensor, current_ms)` updates the sensor's EWMA mean + variance (Welford-style, adaptive α: 0.10 → 0.02 → 0.01 as `_anom_count` grows) and returns `"ok"` or `"warn"` (never `"crit"`). Upper-tail z-test with variance floor `max(σ, 10 ms, 0.2·μ)` and 3-sample debounce; sensitivity knob maps to k ∈ {3, 4, 6}.

Invoked from `core/state.py::_run_once()` **only when** the probe succeeded, `sensor.last_ms` is valid, the static threshold evaluation returned `"ok"`, and the sensor type is in `SUPPORTED_STYPES = {ping, tcp, http, dns, http_keyword, banner}`. This "static wins" precedence rule is the load-bearing invariant — anomaly can promote `ok → warn` but never overrides a static warn/crit, so alerts never double-fire.

Baseline state lives on the `Sensor` object as `_anom_mean`, `_anom_var`, `_anom_count`, `_anom_enabled_since`, `_anom_consec_fails`, `_anom_dirty`. Runtime only — never persisted directly via `db_save()`. Hourly checkpoint via `db_checkpoint_anomaly_baselines(STATE)` writes the dirty rows into `sensor_anomaly_baselines` (dual-backend upsert); restore on startup via `db_load_anomaly_baselines(STATE)` called right after sensors are loaded. A restart therefore does not destroy learning.

Cold-start suppression is enforced in two layers: (1) sample-count bootstrap via `sensor.anomaly_min_samples` (default 50), and (2) a time window via the global `anomaly_cold_start_hours` setting (default 24). The global kill switch `anomaly_global_enabled` short-circuits all firings without restart. Failed probes never update the baseline — a short outage does not inflate σ and mask the real follow-up anomaly.

When anomaly causes the `_threshold_state` transition to `"warn"`, the sensor sets `_anom_caused_warn = True` for that probe; the flap-log emit branch in `_run_once()` reads the flag and writes `direction='anomaly_warn'` instead of `'threshold_warn'`. The Events tab (app.js normalization + events.js branches) maps `anomaly_warn` to `_direction='anomaly'` / `_thr_level='warn'` and renders the "🧠 Anomaly" pill / filter.

**Mass-enable paths.** Two admin-scoped entry points bypass sensor-by-sensor clicking: (1) `anomaly_default_new_sensors` setting — when on, the sensor POST path in `routes/devices.py` sets `anomaly_enabled=1` on newly created sensors whose `stype` is in `SUPPORTED_STYPES`; (2) `POST /api/anomaly/bulk-enable` in `routes/settings.py` walks `STATE.devices.*.sensors.*`, flips `anomaly_enabled=1` on every off-and-supported sensor, and calls `reset_baseline()` on each so the cold-start clock ticks from the click — no alert storm possible because the full 24 h suppression window applies uniformly. Audit entry `anomaly_bulk_enable` records `enabled=N skipped=M`.

### `monitoring/alert_dispatchers.py`
Reusable action dispatchers extracted from the legacy rules engine: `_dispatch_email`, `_dispatch_webhook`, `_dispatch_syslog`, `_dispatch_browser`. Called by `alert_profile_engine._fire()` after building the standard `ctx` dict. Also houses `check_maintenance(ctx)` (maintenance-window suppression) and `_is_private_ip()` (SSRF guard for webhook targets).

### `monitoring/smtp_alert.py`
SMTP connection helper and professional HTML email rendering. `_smtp_connect()` manages the server connection and TLS/auth handshake. The email template uses a PRTG-inspired layout: hero logo section (up to 2 MB, centered, company name below), colored status banner with timestamp, sensor breadcrumb path (Group > Device > Sensor), severity-tinted detail callout box, and a 4-section stats grid (Sensor Details, Performance, Thresholds, Statistics). Section builders (`_html_logo_section`, `_html_status_banner`, `_html_breadcrumb`, `_html_detail_box`, `_html_stats_grid`, `_html_footer`) compose the final HTML. Subject line uses configured company name. Rate-limits repeated SMTP failure logs (5-minute suppression per host). Used by `alert_dispatchers._dispatch_email`.

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

### `reports/`

PDF/CSV report engine. All modules are optional at import time — missing WeasyPrint/Jinja2/Matplotlib produces a clear `RuntimeError` only when a report is actually rendered.

**`reports/data.py`** — assembles `build_report_context(kind, period, filters, config)` into a flat dict consumed by Jinja2 templates. Helpers: `_pick_table(minutes)` routes availability and latency queries to the correct tiered table (`sensor_samples` / `sensor_samples_5m` / `sensor_samples_1h`) so a 1-year report finds data; `_epoch_to_iso()` / `_parse_ts()` bridge between Unix epoch (used in context) and ISO-8601 strings (stored in `flap_log.ts`); `_filter_flaps_by_severity()` applies the incident severity filter to both main and compare periods; `_inventory_*` helpers resolve device IDs to display names via `STATE.devices`. Polish helpers: `_classify_config_issues(flaps)` splits sensor-misconfig noise (Unknown metric / SSL verify / bad OID) out of the incident stream and rolls up by `(did, issue_type)` with a `sensor_count` column; `_cluster_flaps_into_outages(flaps, currently_bad=...)` collapses consecutive bad-state events for the same `(did, sid)` into one outage row (5-min idle gap) and only flags `ongoing=True` when the sensor is still unhealthy in live STATE; `_detect_major_incidents(flaps, min_devices, gap_minutes, currently_bad=...)` clusters simultaneous device-DOWN events into one row (pure stats — no root-cause guessing); `_suppress_outages_in_majors(outages, majors)` drops per-sensor outages that fall inside a Major Incident window for the same device, so the Incident Log doesn't duplicate what Major Incidents already summarises; `_device_health_scores(availability, flaps, limit)` computes a composite 0–100 score per device (downtime up to 50, incident load up to 20, currently DOWN −20 or WARN −10, band ≥90/≥70/<70); `_currently_bad_sensor_keys()` reads live STATE once per report and returns `{(did, sid)}` for sensors that are alive=False or threshold warn/crit — reused by clustering, major-incident detection, and raw-flap `is_open` tagging so historical rows with a missing `resolved_at` never render as "open".

**`reports/engine.py`** — `render_html(kind, ctx, embed_charts, inline_css)` renders via a cached Jinja2 environment with custom filters (`datefmt`, `durfmt`, `msfmt`, `pctfmt`, `statuspct`, `severity_class`, `deltafmt`, `trapname`, `durfmt_flap`, `cleandetail`). `durfmt_flap(duration, ongoing)` renders incident durations honestly — `"open"` only when `ongoing=True`, `"<1s"` for sub-second resolves, the usual `durfmt` for known values, and `"—"` when the duration is unknown AND the sensor isn't currently bad (historical rows that never got a resolve stamp). `cleandetail(s, maxlen=80)` strips a stale trailing `"ms"` suffix from non-latency flap details (e.g. `"Memory Consumed: 8192.0ms"` → `"Memory Consumed: 8192.0"`) — older probe builds wrote every value with `"ms"` regardless of unit; the filter applies only when the detail mentions a non-latency metric keyword (memory, uptime, disk usage/read/write/…-latency, network rx/tx, power consumption, bytes) so real ping/TCP latency strings are left untouched. `render_pdf(kind, ctx, pdfa_mode)` wraps WeasyPrint; when `pdfa_mode` is `"pdf/a-1b"` / `"pdf/a-2b"` / `"pdf/a-3b"` it passes `pdf_variant=` to `write_pdf()`; falls back to standard PDF on `TypeError` (WeasyPrint < 62) or any other exception and logs a warning — the report always goes out.

**`reports/charts.py`** — Matplotlib `Agg` backend only (no display); each function returns a base64-encoded `data:image/png;base64,…` URI ready to embed in the HTML template.

**`reports/runner.py`** — `render_from_template(template, period_override, triggered_by)` → `(pdf_bytes, ctx, ms)`. `run_template_now(template_id)` renders, saves PDF+CSV to `REPORTS_DIR`, inserts a history row, and returns the row dict (no email). `run_schedule(sch)` renders, saves, inserts a pending history row, resolves recipients, calls `send_report_email()`, then updates the delivery status. Both paths compute SHA-256 of the PDF bytes and a deterministic 12-char Report ID: `sha256(template_id | period_start | period_end | generated_at)[:12].upper()`.

**`reports/scheduler.py`** — daemon thread; checks all enabled schedules every 60 s; fires schedules whose `next_run_ts` is due; 90-second dedupe guard prevents double-firing across restarts; supports daily / weekly (day-of-week bitmask) / monthly (day-of-month) / quarterly cadences. Also runs `db_prune_report_history()` hourly (removes history rows + PDF/CSV files on disk older than `report_retention_days`, default 365).

**`reports/delivery.py`** — `send_report_email(recipients, subject, body, pdf_bytes, pdf_filename, csv_bytes, csv_filename)` builds a `multipart/mixed` MIME message, reuses the shared SMTP connection from `monitoring/smtp_alert.py`; returns `(ok, error_str)`.

**`reports/csv_export.py`** — `build_csv_sidecar(ctx)` → `bytes`. Sections: metadata header → Availability by device → Incident summary → Worst/noisiest devices → Latency percentiles → SNMP traps → TLS certificates → Incident log → Device inventory. UTF-8 BOM prepended so Excel auto-detects the encoding.

**Storage path** — `REPORTS_DIR` resolves to `$PW_REPORTS_DIR` → `$XDG_DATA_HOME/pingwatch/reports` → `~/.local/share/pingwatch/reports`. Lives outside the git checkout so `git pull` as root never makes it unwritable. `_ensure_dir()` in `runner.py` falls back to a per-user temp dir and logs a warning if the primary path is not writable.

---

## Route Modules

| Module | Endpoints |
|--------|-----------|
| `auth.py` | `/api/login`, `/api/login/totp`, `/api/logout`, `/api/me`, `/api/users`, `/api/me/password`, `/api/me/profile`, `/api/me/theme`, `/api/users/{u}/profile`, `/api/me/totp/setup`, `/api/me/totp/verify`, `/api/me/totp/disable`, `/api/me/totp/remember-hours`, `/api/me/trusted-devices`, `/api/me/trusted-devices/{id}`, `/api/users/{u}/totp/reset` |
| `groups.py` | `/api/groups`, `/api/group`, `/api/group/{id}`, `/api/group/{id}/members`, `/api/user/group/import_ldap` |
| `devices.py` | `/api/devices`, `/api/device`, `/api/devices/{did}`, `/api/sensors/{did}/*`, `/api/sensors/{did}/{sid}/anomaly/reset`, `/api/device/{did}/scan` |
| `monitoring.py` | `/events` (SSE), `/api/flaps`, `/api/traps`, `/api/events/summary`, `/api/snmp/*`, `/api/vmware/metrics`, `/api/vmware/vms` |
| `settings.py` | `/api/settings`, `/api/server_info`, `/api/settings/smtp_test`, `/api/settings/syslog_test`, `/api/server/restart`, `/api/server/shutdown`, `/api/dashboards`, `/api/dashboards/{id}`, `/api/dashboards/reorder`, `/api/db/stats`, `/api/anomaly/bulk-enable` |
| `tls.py` | `/api/tls`, `/api/tls/upload`, `/api/tls/generate` |
| `topology.py` | `/api/pages`, `/api/nodes`, `/api/links`, `/api/groups`, `/api/settings/{key}` |
| `export.py` | `/api/db/export`, `/api/db/export/logs`, `/api/db/export/bundle`, `/api/db/import`, `/api/audit` |
| `backups.py` | `/api/backups`, `/api/backups/{did}`, `/api/backups/{did}/history`, `/api/backups/{did}/run`, `/api/backups/run/{id}` |
| `alert_profiles.py` | `/api/alert/profiles`, `/api/alert/profile`, `/api/alert/profile/{id}`, `/api/alert/action-templates`, `/api/alert/action-template`, `/api/alert/action-template/{id}`, `/api/alert/profile/{id}/test` |
| `alert_events.py` | `/api/alert/events`, `/api/alert/events/active`, `/api/alert/events/resolve-all`, `/api/alert/event/{id}`, `/api/alert/event/{id}/ack`, `/api/alert/event/{id}/resolve` |
| `maintenance_windows.py` | `/api/alert/windows`, `/api/alert/window`, `/api/alert/window/{id}` |
| `ldap.py` | `/api/ldap/settings`, `/api/ldap/test_connection`, `/api/ldap/test_auth`, `/api/ldap/search_groups`, `/api/ldap/test_user_groups` |
| `radius.py` | `/api/radius/settings`, `/api/radius/test_connection`, `/api/radius/test_auth`, `/api/radius/test_auth_challenge`, `/api/radius/attribute_mappings` |
| `ipam.py` | `/api/ipam/subnets`, `/api/ipam/subnets/{id}`, `/api/ipam/subnets/{id}/ips`, `/api/ipam/ips/{subnet_id}/{ip}` |
| `discovery.py` | `/api/discovery/scan`, `/api/discovery/scan/{id}`, `/api/discovery/bulk-add` |
| `licenses.py` | `/api/device/{did}/licenses`, `/api/license/{id}`, `/api/licenses`, `/api/licenses/summary`, `/api/licenses/check` |
| `reports.py` | `/api/reports/templates`, `/api/reports/template`, `/api/reports/template/{id}`, `/api/reports/schedules`, `/api/reports/schedule`, `/api/reports/schedule/{id}`, `/api/reports/history`, `/api/reports/history/{id}`, `/api/reports/history/{id}/download`, `/api/reports/history/{id}/csv`, `/api/reports/history/bulk-delete`, `/api/reports/run`, `/api/reports/preview`, `/api/reports/test-send` |

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
| `users.py` | User management (local + LDAP + RADIUS), user profiles (`full_name`, `email`, `theme_preference`), `app_settings` key/value store, multi-dashboard CRUD (`dashboards` table — list/get/create/rename/delete/save/reorder); TOTP helpers (`db_get_totp`, `db_set_totp`, `db_clear_totp`); trusted-device helpers (`db_add_trusted_device`, `db_lookup_trusted_device`, `db_touch_trusted_device`, `db_list_trusted_devices`, `db_revoke_trusted_device`, `db_revoke_trusted_devices`, `db_sweep_expired_trusted_devices`, `db_get_remember_hours`, `db_set_remember_hours`); `db_add_radius_user(username, role, group_id)` inserts with `auth_type='radius'`, `pw_hash='__radius__'` (mirrors `db_add_ldap_user`) |
| `groups.py` | User group CRUD, member assignment, email resolution for alert dispatch. LDAP-mapped groups carry `ldap_dn`; RADIUS-mapped groups carry `radius_attribute` + `radius_value`. `db_get_ldap_mapped_groups()` returns all LDAP-mapped groups. `db_find_group_by_radius(attribute, value)` returns the first group whose mapping matches a returned RADIUS attribute. `db_get_radius_mapped_groups()` returns all RADIUS-mapped groups. |
| `audit.py` | Audit log write & query |
| `backups.py` | Backup settings (Fernet-encrypted credentials), run history, 3-run retention |
| `trap_defs.py` | SNMP trap definition queries |
| `ipam.py` | Subnet and IP allocation management |
| `alert_profiles.py` | Alert profile CRUD, action template CRUD, stage state tracking (`alert_profile_state`) |
| `alert_events.py` | Alert event log — dedup, ACK/resolve, auto-resolve on recovery, badge count |
| `licenses.py` | `device_licenses` table CRUD — `db_get_licenses(did)`, `db_get_all_licenses()`, `db_add_license()`, `db_update_license()`, `db_delete_license()`, `db_delete_device_licenses(did)`, `db_update_license_status()` (internal), `db_license_summary()` |
| `reports.py` | `report_templates` / `report_schedules` / `report_history` table CRUD — `db_list/get/create/update/delete_report_template`, `db_*_report_schedule`, `db_list/get/add_report_history`, `db_update_report_history_delivery`, `db_prune_report_history`, `db_record_schedule_run`, `db_set_schedule_enabled` |

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
| `report_footer_text` | text | Custom text shown in the PDF report footer (e.g. "Confidential — Internal Use Only") |
| `report_brand_color` | `"#rrggbb"` | Accent colour used in the report cover page and headings (defaults to `#2f81f7`) |
| `report_retention_days` | integer string | How many days to keep report history rows + PDF/CSV files on disk (default `"365"`) |
| `radius_enabled` | `"1"` / `"0"` | RADIUS authentication master toggle |
| `radius_server` | str | Primary RADIUS host |
| `radius_port` | integer string | Primary RADIUS port (default `"1812"`) |
| `radius_secret_enc` | str | Fernet-encrypted shared secret (primary) |
| `radius_server2` | str | Optional secondary RADIUS host |
| `radius_port2` | integer string | Secondary RADIUS port |
| `radius_secret2_enc` | str | Fernet-encrypted shared secret (secondary) |
| `radius_timeout` | integer string | Seconds per attempt (default `"5"`) |
| `radius_retries` | integer string | Retries per server before failover (default `"3"`) |
| `radius_nas_identifier` | str | `NAS-Identifier` attribute value (default `"pingwatch"`) |
| `radius_realm_prefix` | str | Prepended to username before sending (e.g. `"DOMAIN\\"`) |
| `radius_realm_suffix` | str | Appended to username before sending (e.g. `"@corp.local"`) |
| `radius_auto_provision` | `"1"` / `"0"` | Auto-create local user row on first successful RADIUS login |
| `radius_default_role` | str | Fallback role when no attribute mapping matches (default `"viewer"`) |
| `radius_default_group_id` | integer string | Fallback group when no attribute mapping matches |
| `radius_debug` | `"1"` / `"0"` | Verbose RADIUS debug logging |
| `db_backup_remote_enabled` | `"1"` / `"0"` | Upload DB backup to remote destination after each local run |
| `db_backup_remote_type` | `"sftp"` / `"smb"` | Remote transfer protocol |
| `db_backup_remote_host` | str | Remote server hostname or IP |
| `db_backup_remote_port` | integer string | Remote port (SFTP default `"22"`, SMB default `"445"`) |
| `db_backup_remote_user` | str | Remote username |
| `db_backup_remote_pass_enc` | str | Fernet-encrypted remote password |
| `db_backup_remote_path` | str | Remote destination directory |
| `db_backup_remote_share` | str | SMB share name (SMB only) |

---

## Frontend Structure

The frontend is served as static files — no build step.

| File | Purpose |
|------|---------|
| `index.html` | Main dashboard shell — loads all JS/CSS |
| `style.css` | Application-wide styles and CSS variables |
| `app.js` | Bootstrap, tab routing, SSE connection, shared helpers (`api()`, `toast()`, `esc()`); `TIMINGS` frozen object centralises all SSE/UI timing constants (SSE batch interval, reconnect backoff, clock update rate, etc.); reconciles `theme_preference` from `/api/me` into `setTheme(..., {sync:false})` after login |
| `theme.js` | Theme manager — public API `getTheme()` / `setTheme(t, opts)` / `toggleTheme()` / `getCssVar(name)` / `getCssRgb(name)`. `setTheme` writes `<html data-theme>`, persists `localStorage.pw_theme`, postMessages the map iframe, dispatches a `themechange` `CustomEvent`, refreshes the user-menu button label, and fires `PATCH /api/me/theme` in the background (skipped when `opts.sync===false` to avoid echo when mirroring the server value). `getCssRgb()` parses `#rgb` / `#rrggbb` / `rgb()` / `rgba()` values into `[r,g,b]` tuples — used by canvas modules that need `rgba(${rgb.join(',')},${alpha})` template literals. An inline bootstrap script in `<head>` applies the attribute synchronously before CSS paints — prevents FOUC. Loaded first in the JS bundle so downstream modules can call `getCssVar()` / `getCssRgb()` during init. |
| `dashboard.js` | Customizable widget dashboard with **multi-dashboard tabs** — per-user named dashboards (up to 10) with tab bar, right-click rename/delete context menu, localStorage-persisted active tab; new users get a pre-populated "Default" dashboard with 8 starter widgets; `_dwDashboards` / `_dwActiveId` / `_dwWidgets` state; API: `/api/dashboards`; includes `license_overview` widget — 4-KPI grid (Expired / Expiring / Valid / Total) + sorted expiration table. `backup_status` widget (`_dwNcmStatusRefresh`) fetches `/api/backups` + `/api/settings` in parallel; renders the existing device-config 2×2 KPI grid (OK/Failed/Never/Enabled) plus a **Database** section (`_dwRenderDbBackup`) showing last-run age (color-coded green/amber/red by overdue threshold), next scheduled run, and optional remote upload status line; clicking the DB card navigates to Settings → Database. Availability sparkline / mini-chart canvases read theme colours fresh each paint via `getCssVar()` / `getCssRgb()` (`--bg`, `--text3`, `--up`, `--warn`, `--down`) so strips and gradients recolour on the next widget refresh after a theme flip |
| `devices.js` | Device list, detail panel, port scan modal; status filter pills (All/Down/Warn/Up/Pause) with SSE-live counts; device list pagination (25/50/100 per page, `localStorage`-persisted); filter + status + pagination compose cleanly |
| `sensors.js` | Sensor list, detail panel, history chart; SNMP tile shows formatted rate for counter OIDs and orange warning when a non-numeric string is returned (wrong OID indicator); device tile loading skeleton (shimmer) while fresh data loads; drag-to-reorder sensor tiles with layout saved to `localStorage` per device; VMware sensors render as collapsible VM groups with per-metric rows, sparklines, formatted values (`_fmtVmVal`), and group-level mute toggle; KPI tiles (Avg/Min/Max) compute from `samples` array to match the stats bar and reflect the selected time range — Avail, Loss%, Jitter remain from hourly `summary` aggregates. History chart + sparkline canvases maintain a module-level `_SCC` RGB cache (`accent` / `up` / `warn` / `down` / `text` / `bg` / `bg2`) populated via `getCssRgb()` and invalidated on the `themechange` event; the listener iterates `_histCache` and calls `dmHistRedraw(did, sid)` on every open chart so all visible history modals repaint immediately after a theme toggle |
| `events.js` | Flap/trap/error event log with filters; **inner Active / History tabs** — `_evtInnerTab` state (persisted in `localStorage`), `_evtSetInnerTab()` switcher, `_isEvtActive()` helper partitions flaps by `ack_state` and traps by matched alert state (unmatched traps → History); active count badge on Active tab; "Resolve All" hidden on History tab; alert tagging — matches sensor events to alert history (90 s window), renders severity badge + profile name + state inline, ACK/Resolve buttons on active rows, refreshes on SSE `ack_event`; resolved event duration uses `resolved_at` as fixed end time (stops counting); license event support — `license_ok`→recovery, `license_warn`→warning, `license_crit`→critical severity mapping; 📋 icon for `stype='license'`; "License" option in Type filter |
| `backups.js` | Backup table, config viewer, patience diff, credential noise toggle, vendor-aware rollback; Cisco/Arista rollback includes enclosing context block + `end` + `wr` |
| `forms-device.js` | Add/edit device modal; **Licenses section** — collapsible `<details>` with status badges (Valid / Expiring / Expired), days-remaining countdown, warn/crit day inputs, add/delete per license; `_edLicLoad()`, `_edLicRender()`, `_edLicAdd()`, `_edLicDel()`, `_edLicStatusBadge()` |
| `forms-sensor.js` | Add/edit sensor modal; SNMP interface discovery (walk + metric selector); single-selection auto-syncs OID input field; device-host fallback in discover and add-selected paths; VMware VM discovery with grouped metric checkboxes, smart threshold defaults (`_VM_THR_DEFAULTS`), and bulk sensor add |
| `forms-settings.js` | Settings modal (10 tabs: General, Users, Groups, Integrations, Database, Logs, Sensors, Networking, Config Backup, Alert Profiles); each tab is built by a dedicated `_buildSettingsTab_*()` function — `openSettings()` is a thin orchestrator (admin-only; non-admins receive a toast). Integrations tab: SMTP / Syslog / LDAP / RADIUS sub-tabs each carry a live status dot (`ibadge-{id}`) and status bar updated by `_renderIntegStatus()`; LDAP and RADIUS dots reflect `ldap_status` / `radius_status` from `GET /api/settings`. RADIUS panel: server settings, shared secrets (sentinelized), failover, realm munging, auto-provision, default role/group, attribute→group mapping table (backed by `forms-radius.js`). User table: `isRadius` check renders `🧾 RADIUS` badge; `isRemote` guard suppresses reset-password button for both LDAP and RADIUS users. Logs tab: time-range filter offers 5 m / 15 m / 1 h / 3 h / 6 h (default) / 12 h / 24 h presets plus a **Custom range** option that reveals inline datetime-local pickers (From / To); `_logFilter.customFrom` / `_logFilter.customTo` are sent as `after=` / `before=` params to the log API. Debug Mode checkbox auto-saves on toggle via `_saveDebugMode()`. Groups tab: "Import from LDAP" button (visible only when LDAP is enabled), LDAP import modal with search + role assignment, LDAP badge on imported groups, LDAP-aware group editor (shows LDAP DN read-only, `default_role` dropdown, hides member checkboxes for LDAP-managed groups) |
| `forms-users.js` | User management, Change Password modal, self-service Edit Profile modal; Add User modal `#au-type` offers "RADIUS" option (visible only when `radius_enabled=1`); selecting RADIUS hides password fields |
| `forms-ldap.js` | LDAP/AD settings modal including Group Integration section (auto-provision, nested groups, group base DN, group filter, sync interval) and Test User Groups sub-dialog |
| `forms-radius.js` | RADIUS settings panel: `_loadRadiusPanel()` + `saveRadiusSettings()` + `testRadiusConnection()`; attribute→group mapping table (`_loadRadiusMappings`, `_radiusMappingRow`, `_saveRadiusMapping`); Test User Auth dialog with Access-Challenge step (`openRadiusTestAuth`, `submitRadiusTestAuth`, `_renderRadiusTestAuthResult`, `_submitRadiusTestAuthChallenge`) |
| `forms-io.js` | DB export/import modal |
| `forms-utils.js` | Shared form utilities and canonical helper implementations: `esc()`, `closeM()`, `_overlayClose()`, `msColor()` (latency → CSS colour), `statusClass()` (status string → CSS class), `_lsGet()` / `_lsSet()` (localStorage helpers) — all other JS modules reference these rather than maintaining local copies |
| `forms-discovery.js` | Subnet Discovery wizard — 5-step modal: CIDR input + live validation, scan progress, filterable/sortable results table (IP, hostname, MAC/vendor, ports, Type column, multi-NIC ⚠ flags), per-device sensor review, bulk add; **per-device group assignment** — default group dropdown plus per-row group input with `_discGrpFocus`/`_discGrpBlur` datalist UX; `customGroups[ip]` overrides; accent border on overridden rows |
| `reports.js` | Reports tab (Templates / Schedules / History sub-tabs). Template editor modal: kind, period (including custom `datetime-local` range picker), severity filter, recipients, CSV sidecar checkbox, PDF compliance select (Standard / PDF/A-1b / 2b / 3b); browser preview in a new tab; Run Now with spinner + elapsed counter; test-send modal with recipient input and inline status; history table with download buttons. Helper modals: `_rptConfirm()`, `_rptNotify()`, `_rptShowProgress()` replace browser alert/confirm/prompt. |
| `alerting.js` | Alert profiles editor (PRTG-style escalation table with delay / repeat / action columns), reusable action template editor (email with user+group checkbox pickers, webhook, syslog, browser push), alert event history viewer, maintenance windows |
| `forms-group.js` | Edit Group modal — group rename and per-group alert profile (inherit / override controls with "Edit profile…" button) |
| `ipam.js` | IPAM tab — subnet list, per-subnet IP table, inline editing; **sortable columns** (click headers, ▲/▼ arrows) on all 7 columns with IP-numeric, alpha, and date comparators; **filter dropdowns** on Status (All/Used/Free) and Licenses (All/Valid/Expiring/Expired/None); sort + filter + text search compose together; **Licenses column** — `_ipamLicenseMap` (did → worst status), `_ipamLicBadge(did)` renders Valid/Expiring/Expired badge; refreshed on SSE `license_status` |
| `bg.js` | Animated background canvas (aurora + radar). Theme-aware: RGB colour cache populated via `getCssRgb()` from `--accent` / `--up` / `--text2`, refreshed by a `themechange` listener — the next RAF frame picks up the new palette without a page reload |
| `map.js` | NTM canvas engine — drag-and-drop topology editor. Iframe theme sync: parent postMessage (`{type:'theme', value}`) + localStorage bootstrap (same-origin with parent) set `<html data-theme>` on arrival/load. Animated background palettes — two frozen objects (`_NTM_BG_PALETTES.dark` / `.light`) feeding an active `_NTM_BG` reference; `_ntmRefreshBgPalette()` swaps it and dispatches `ntm_themechange`. `initMainBg` (hex grid, matrix streams, particles, ring pulses, scan line, corner HUD, base fill) and `initDashBg` (side-panel particles + connections + scan bar) read `_NTM_BG` every frame; the `ntm_themechange` listener zeroes `hexCacheW/H` so the offscreen hex cache rebuilds with the new stroke colour on the next frame |
| `fonts/` | Self-hosted `.woff2` font files (Exo 2, JetBrains Mono, Orbitron, Share Tech Mono). Referenced by `@font-face` rules in `style.css` + `map.css` + inline `<style>` in `setup.html`. No external CDN — PingWatch has zero network dependencies at runtime (air-gapped safe). CSP reflects this: `style-src 'self' 'unsafe-inline'; font-src 'self';` in `server.py`. For PNG topology exports, `map.js::_inlineFontsForExport()` base64-embeds the Orbitron + Share Tech Mono woff2 files so exported images render correctly outside the app. |

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
| `PATCH` | `/api/sensors/{did}/{sid}` | Update a sensor (accepts `anomaly_enabled`, `anomaly_sensitivity`, `anomaly_min_samples`) |
| `DELETE` | `/api/sensors/{did}/{sid}` | Delete a sensor |
| `POST` | `/api/sensors/{did}/{sid}/anomaly/reset` | Wipe the learned anomaly baseline (in-memory + DB row); operator role |
| `POST` | `/api/device/{did}/scan` | Trigger port scan (async) |
| `POST` | `/api/anomaly/bulk-enable` | Enable anomaly detection on every supported sensor that's currently off; resets each baseline to a fresh cold-start window; admin role |

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

### Dashboards

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/dashboards` | List user's dashboards (id, name only); auto-creates "Default" with starter widgets for new users |
| `POST` | `/api/dashboards` | Create dashboard `{name}` (max 10 per user) |
| `GET` | `/api/dashboards/{id}` | Get dashboard widgets |
| `PUT` | `/api/dashboards/{id}` | Save widgets `{widgets: [...]}` |
| `PATCH` | `/api/dashboards/{id}` | Rename dashboard `{name}` |
| `DELETE` | `/api/dashboards/{id}` | Delete dashboard (rejects if last) |
| `PUT` | `/api/dashboards/reorder` | Reorder tabs `{ids: [3, 1, 2]}` |

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

### RADIUS

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/radius/settings` | admin | RADIUS config (shared secrets never returned; `radius_secret_set` / `radius_secret2_set` sentinels indicate if set) |
| `PATCH` | `/api/radius/settings` | admin | Save RADIUS config; empty secret = keep existing; non-empty = Fernet-encrypt and replace |
| `POST` | `/api/radius/test_connection` | admin | Send a bogus packet and verify the server responds `{ok, message}` |
| `POST` | `/api/radius/test_auth` | admin | Run a full authentication `{username, password}` → `{ok, attrs?, challenge?, message}` |
| `POST` | `/api/radius/test_auth_challenge` | admin | Continue a test-auth challenge `{challenge_id, response}` → same shape |
| `GET` | `/api/radius/attribute_mappings` | admin | List all groups with their RADIUS mappings + `available_groups` (unmapped) |
| `POST` | `/api/radius/attribute_mappings` | admin | Set or clear mapping for a group `{group_id, attribute, value}` |

Also extends `POST /api/login` to return `{radius_challenge: true, challenge_id, prompt}` when RADIUS issues an `Access-Challenge`, and adds `POST /api/login/radius_challenge {challenge_id, response}` to complete it.

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

### Reports

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/reports/templates` | viewer | List all report templates |
| `POST` | `/api/reports/template` | admin | Create template `{name, kind, config_json}` |
| `GET` | `/api/reports/template/{id}` | viewer | Get template detail |
| `PATCH` | `/api/reports/template/{id}` | admin | Update template |
| `DELETE` | `/api/reports/template/{id}` | admin | Delete template |
| `GET` | `/api/reports/schedules` | viewer | List all report schedules |
| `POST` | `/api/reports/schedule` | admin | Create schedule |
| `PATCH` | `/api/reports/schedule/{id}` | admin | Update schedule (incl. enable/disable) |
| `DELETE` | `/api/reports/schedule/{id}` | admin | Delete schedule |
| `GET` | `/api/reports/history` | viewer | Paginated report history; optional `template_id` filter |
| `GET` | `/api/reports/history/{id}` | viewer | Single history record |
| `GET` | `/api/reports/history/{id}/download` | viewer | Download the generated PDF |
| `GET` | `/api/reports/history/{id}/csv` | viewer | Download the CSV sidecar (if generated) |
| `DELETE` | `/api/reports/history/{id}` | admin | Delete one history row + its PDF/CSV files |
| `POST` | `/api/reports/history/bulk-delete` | admin | Delete many history rows `{ids:[…]}` — capped at 500 per call; returns `{deleted, missing}` |
| `POST` | `/api/reports/run` | operator | Ad-hoc Run Now `{template_id}` — renders, saves PDF, returns history row |
| `POST` | `/api/reports/preview` | operator | Render HTML preview `{template_id}` — returns full HTML with inlined CSS (no PDF) |
| `POST` | `/api/reports/test-send` | operator | Test email delivery `{template_id, recipients}` — renders and emails PDF without saving history |

### User Profiles

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/me` | any | Own username, role, full_name, email, theme_preference |
| `PATCH` | `/api/me/profile` | any | Update own full_name and email (also accepts optional `theme_preference`) |
| `PATCH` | `/api/me/theme` | any | Update own theme preference `{theme: "dark"\|"light"}` — fired in the background by `setTheme()` |
| `PATCH` | `/api/users/{u}/profile` | admin or self | Update profile; admin can also set group_id and role |

### Two-Factor Authentication

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/api/me/totp/setup` | any | Generate TOTP secret + QR code URI `{secret, qr_uri}`. Idempotent — safe to call multiple times before verification. |
| `POST` | `/api/me/totp/verify` | any | Confirm enrolment `{code, secret}`. Activates 2FA and returns 8 single-use recovery codes. |
| `POST` | `/api/me/totp/disable` | any | Disable 2FA for self `{password}`. Revokes all trusted devices for the user. |
| `POST` | `/api/users/{u}/totp/reset` | admin | Admin: disable 2FA for `{u}` and revoke all their trusted devices. |
| `POST` | `/api/login/totp` | — | Complete a TOTP challenge `{challenge_id, code, remember?, remember_hours?}`. On success sets `session` cookie; optionally sets `pw_trusted` cookie when `remember=true`. |

### Trusted Devices

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/me/trusted-devices` | viewer | List own trusted devices — label, IP, last used, expiry; includes `remember_hours` preference; current device flagged with `current: true` |
| `DELETE` | `/api/me/trusted-devices` | viewer | Revoke all own trusted devices and clear the `pw_trusted` cookie |
| `DELETE` | `/api/me/trusted-devices/{id}` | viewer | Revoke one trusted device; clears `pw_trusted` cookie if the request matches the current device |
| `PATCH` | `/api/me/totp/remember-hours` | viewer | Set personal default remember duration `{hours: 9}` (0–720; 0 = always prompt TOTP) |

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
| `POST` | `/api/alert/windows` | admin | Create window |
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

1. **`monitoring/probes.py`** — add a `probe_<type>(...)` function returning `{ok, ms, detail, value?}`. Lazy-import any optional dependency inside the function.
2. **`core/state.py`** — add type-specific fields to `Sensor.__init__`; add a dispatch branch in `Sensor.probe()` (decrypt secrets via `decrypt_pw()` at call time); expose non-secret fields + `has_*` booleans in `to_dict()`; extend `add_sensor()` signature and `Sensor()` call; add new fields to the `editable` list in `update_sensor()`.
3. **`db/core.py`** — add idempotent `ALTER TABLE ADD COLUMN` migrations for every new column (guards with `try/except` so re-runs are safe).
4. **`db/pg_schema.py`** — add columns to the `sensors` `CREATE TABLE` block and append them to the `_migrations` list.
5. **`db/persistence.py`** — extend all 4 save/load paths (PG save, PG load, SQLite save, SQLite load) with the new columns at the tail of each tuple. Use `_int_or_none(v)` for any numeric field in save tuples — empty strings from the API would otherwise cause PG `invalid input syntax for type integer` errors.
6. **`routes/devices.py`** — accept new fields in the POST (create) and PATCH (update) handlers; pass secrets through `encrypt_pw()` before handing to STATE. Add server-side validation as needed (e.g. SFTP checksum level requires `interval ≥ 60 s`).
7. **`frontend/forms-sensor.js`** — append a **6-tuple** `[key, name, sub, icon, category, keywords]` to `_types`; add the type string to the `selType()` array (omitting it keeps the form panel permanently hidden); add a `fg-<type>` form block; extend `collectSensorForm()` with a payload branch for the new type.
8. **`frontend/sensors.js`** — add `sIco('<type>')` → icon character so the sensor tile renders the right glyph.
9. **`frontend/forms-settings.js`** — add `<type>: warn_ms` to `_SDR_WARN_DEF`, `<type>: crit_ms` to `_SDR_CRIT_DEF`, and an entry to `_SDR_META` so the sensor defaults table in Settings shows a row for the new type.
10. **`frontend/style.css`** — add colored badge CSS across **all 7 class families**: `.stl-tbdg.<type>`, `.s-ico.<type>`, `.dc-snr-ico.<type>`, `.dc-snr.snr-t-<type>`, `.dlr-snr.snr-t-<type>`, `.dm-tbdg.<type>`, `.sdr-row[data-type=<type>]`, plus a light-theme override block for AA contrast on white. Use a color not already taken by an existing type (ping=blue, http=yellow, tcp=teal, tls=purple, snmp=orange, dns=sky, banner=indigo, vmware=cyan, smtp=pink, ssh=lime, sftp=rose).

### Adding a new settings key

1. **`db/users.py` → `db_save_settings` / `db_load_settings`** — the `app_settings` table is key/value TEXT, no schema change needed.
2. **`routes/settings.py`** — add the key to the GET response (with default) and to the PATCH handler (with validation).
3. **`core/settings.py`** — the runtime cache is populated from the DB on startup; no changes needed unless you need a typed accessor.
4. **Frontend** — add the UI control in `forms-settings.js` and read/write via `GET /api/settings` + `PATCH /api/settings`.

### Adding a new route module

1. Create `routes/<name>.py` with a `handle(h, method, path, body)` function.
2. Register it in `server.py` by adding a route regex in `core/config.py` and a dispatch call in `server.py`'s request handler.
3. Add it to the `routes/` table in this document.
