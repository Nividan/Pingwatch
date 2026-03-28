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
        ├── monitoring/           ← Probes, alerting, topology
        │   ├── probes.py         ← Sensor engine
        │   ├── alert_engine.py   ← Rules-based alert engine (conditions, dispatch, cooldown)
        │   ├── smtp_alert.py     ← Email notifications
        │   ├── syslog_client.py  ← RFC 5424 syslog forwarding
        │   └── network_map.py    ← NTM topology data layer
        │
        ├── backup/               ← Config backup engine
        │   ├── engine.py         ← SSH / Telnet backup engine
        │   └── scheduler.py      ← Backup schedule runner
        │
        ├── snmp/                 ← SNMP trap pipeline
        │   ├── receiver.py       ← UDP trap listener
        │   ├── enricher.py       ← Trap enrichment & OID lookup
        │   ├── vendor.py         ← Vendor fingerprinting
        │   ├── catalog.py        ← OID catalog queries
        │   └── seeds/            ← Built-in trap definitions
        │
        └── db/                   ← SQLite persistence package (dual-DB)
                                       Main DB: config, devices, users, groups, IPAM, settings
                                       Logs DB: sensor samples, flap log, SNMP traps, error log
```

**Dual write-queue design:** two independent queue threads — one for the Main DB (`pingwatch.db`) and one for the Logs DB (`pingwatch_logs.db`). Probe threads never block on DB writes; they enqueue a lambda and continue.

---

## Project Structure

```
pingwatch/
├── server.py               ← HTTP/HTTPS dispatcher + entry point
├── setup_wizard.py         ← First-run interactive setup wizard
├── gui.py                  ← Desktop status window (tkinter)
├── pingwatch.pyw           ← Windows windowless launcher
├── start.bat               ← Windows console launcher
├── start.sh                ← Linux/macOS launcher + service installer
├── pingwatch.service       ← systemd unit file
├── requirements.txt        ← Python dependencies
├── ssh_known_hosts.txt     ← SSH TOFU host key store (auto-created)
│
├── core/
│   ├── config.py           ← File paths, compiled route regexes, startup constants
│   ├── settings.py         ← Thread-safe runtime settings cache (DB-backed)
│   ├── logger.py           ← App logger, audit logger, in-memory log buffer
│   ├── auth.py             ← Login, PBKDF2-SHA256, RBAC, session management
│   ├── ldap_auth.py        ← ldap_authenticate / ldap_test_connection / ldap_test_auth_user
│   ├── app_state.py        ← Shared globals: STATE, effective ports, TLS flag, tray ref
│   ├── state.py            ← In-memory Device/Sensor objects, probe threads, SSE broadcast
│   └── tls.py              ← RSA-2048 cert generation, DB→certs/→auto-generate discovery
│
├── monitoring/
│   ├── probes.py           ← All sensor probe types (ICMP, HTTP, TCP, TLS, SNMP, DNS, Banner)
│   ├── smtp_alert.py       ← Down/up email alerts with 5-min failure-log suppression
│   ├── syslog_client.py    ← Non-blocking RFC 5424 forwarder, bounded 500-entry queue
│   └── network_map.py      ← Topology pages, nodes, links, groups (DB-backed)
│
├── backup/
│   ├── engine.py           ← SSH (paramiko) + Telnet connections, TOFU key verify,
│   │                          enable-mode escalation, paging disable, per-command idle timeout
│   ├── scheduler.py        ← Cron-expression schedule runner for backup jobs
│   ├── db_backup.py        ← WAL-safe SQLite DB snapshots via sqlite3.backup(); retention policy
│   └── database/           ← Timestamped DB snapshot files (auto-created)
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
│   ├── core.py             ← Dual write-queues, schema init, user seeding
│   ├── migration.py        ← One-time split: legacy single-DB → Main + Logs DB
│   ├── persistence.py      ← Device/sensor save & load
│   ├── samples.py          ← Buffered probe writes, history & summary queries
│   ├── events.py           ← Flap log, SNMP trap log, sensor error log
│   ├── users.py            ← User management (local + LDAP), profile (full_name, email), app_settings
│   ├── groups.py           ← User group CRUD and email resolution for alert dispatch
│   ├── audit.py            ← Audit log write & query
│   ├── backups.py          ← Backup settings (encrypted), run history, 3-run retention
│   ├── trap_defs.py        ← SNMP trap definition queries
│   └── ipam.py             ← Subnet and IP allocation management
│
├── routes/
│   ├── auth.py             ← Login, logout, users, user/self profile PATCH
│   ├── groups.py           ← User group CRUD and member assignment
│   ├── devices.py          ← Device & sensor CRUD, port scan
│   ├── monitoring.py       ← SSE, flaps, traps, SNMP
│   ├── settings.py         ← App settings, server info, restart/shutdown
│   ├── tls.py              ← TLS certificate API
│   ├── topology.py         ← NTM pages/nodes/links/groups
│   ├── export.py           ← DB export/import, audit log
│   ├── backups.py          ← Device config backup API
│   ├── alert_rules.py      ← Alert rules CRUD, toggle, test-fire
│   ├── alert_events.py     ← Alert history, ACK/resolve
│   ├── maintenance_windows.py ← Maintenance window CRUD
│   ├── ldap.py             ← LDAP/AD settings & test endpoints
│   └── ipam.py             ← IPAM subnet & IP allocation API
│
├── certs/                  ← Optional: drop cert.pem + key.pem here
│
└── frontend/               ← Web UI (served statically)
    ├── index.html
    ├── style.css
    ├── app.js              ← Bootstrap, tab routing, shared helpers
    ├── dashboard.js        ← Customizable widget dashboard
    ├── devices.js          ← Device list and detail panel
    ├── sensors.js          ← Sensor list and detail panel
    ├── events.js           ← Flap/trap/error event log viewer
    ├── backups.js          ← Backup table, config viewer, diff, rollback
    ├── forms-device.js     ← Add/edit device form
    ├── forms-sensor.js     ← Add/edit sensor form
    ├── forms-settings.js   ← Settings modal (9 tabs)
    ├── forms-users.js      ← User management
    ├── forms-ldap.js       ← LDAP/AD settings modal
    ├── forms-io.js         ← DB export/import form
    ├── forms-utils.js      ← Shared form helpers
    ├── ipam.js             ← IPAM tab
    ├── bg.js               ← Animated background canvas
    ├── map.html            ← Network Topology Manager shell
    ├── map.css             ← NTM styles
    └── map.js              ← NTM canvas engine
```

---

## Backend Modules

### `server.py`
HTTP(S) dispatcher and application entry point. Serves static files, delegates every API route to a `routes/` module, and starts all background threads (probe engine, autosave, backup scheduler, SNMP receiver, syslog). Wraps the HTTP listener with `ssl.SSLContext` when HTTPS is enabled; optionally runs a second lightweight HTTP server for HTTP→HTTPS redirect.

### `setup_wizard.py`
Cross-platform first-run wizard. Checks required packages, handles HTTP/HTTPS port selection (with Apache2/nginx conflict detection on Linux), TLS certificate setup (including HTTP→HTTPS redirect toggle), SNMP port configuration, firewall rules, desktop shortcut creation, and optional systemd service install (Linux only). Stops any running PingWatch service before modifying the database to prevent WAL conflicts. Fixes file ownership when run via `sudo`. Flags: `--setup` (re-run wizard), `--check` (package check only).

### `core/state.py`
In-memory runtime state. Holds all `Device` and `Sensor` objects, manages probe threads, and broadcasts SSE events to all connected clients.

### `core/auth.py`
Authentication and session management. PBKDF2-SHA256 password hashing, RBAC roles (`viewer` / `operator` / `admin`), session store, domain-prefix stripping. Branches to `core/ldap_auth.py` for users with `auth_type = ldap`.

### `core/ldap_auth.py`
LDAP/AD helpers: `ldap_authenticate`, `ldap_test_connection`, `ldap_test_auth_user`. Supports plain LDAP, LDAPS, and StartTLS. Bind password decrypted in-memory only; never logged. `ldap3` import deferred inside functions — the library is optional and local users are unaffected if absent.

### `core/tls.py`
TLS certificate management. RSA-2048 self-signed certificate generation (full X.509 subject + custom SANs), certificate discovery (DB → `certs/` → auto-generate), SSL context construction, expiry warnings (30-day threshold).

### `monitoring/probes.py`
All sensor probe types on per-sensor background threads: ICMP, HTTP/S (status + keyword), TCP, TLS (cert validity + handshake), SNMP OID polling (v1/v2c), DNS, Banner (regex match).

### `backup/engine.py`
SSH (paramiko) and Telnet connections to network devices. Features: TOFU SSH host key verification, password and keyboard-interactive auth (JUNOS), enable-mode escalation (Cisco), paging disable command, per-command idle timeouts, configurable command list.

### `backup/db_backup.py`
Scheduled SQLite database backup. Uses `sqlite3.backup()` (WAL-safe — safe to run while the DB is being written) to snapshot both Main DB and Logs DB into timestamped files under `backup/database/`. Applies a configurable retention policy (default: keep 7 copies). Triggered by the scheduler and also callable on demand via `POST /api/db/backup/run`.

### `monitoring/alert_engine.py`
Rules-based alert engine. A bounded daemon queue receives events from `core/state.py` on every sensor state change. The worker thread evaluates all enabled rules against each event: condition matching (AND/OR), maintenance window suppression, cooldown/deduplication (DB-persisted), and multi-action dispatch. Actions: email (group-resolved + raw addresses), HTTP webhook (SSRF-guarded), syslog, and browser push notification via SSE. Rules are cached in memory with a 30-second TTL; `invalidate_rules_cache()` forces an immediate reload after saves.

### `monitoring/smtp_alert.py`
Down/up email alerts via SMTP when sensor states change. Rate-limits repeated SMTP failure logs (5-minute suppression per host).

### `monitoring/syslog_client.py`
Non-blocking RFC 5424 forwarder. Daemon queue thread with 500-entry bounded queue — monitor threads never block. Settings re-read on every send; no restart needed to reconfigure.

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
| `groups.py` | `/api/groups`, `/api/group`, `/api/group/{id}`, `/api/group/{id}/members` |
| `devices.py` | `/api/devices`, `/api/device`, `/api/devices/{did}`, `/api/sensors/{did}/*`, `/api/device/{did}/scan` |
| `monitoring.py` | `/events` (SSE), `/api/flaps`, `/api/traps`, `/api/events/summary`, `/api/snmp/*` |
| `settings.py` | `/api/settings`, `/api/server_info`, `/api/settings/smtp_test`, `/api/settings/syslog_test`, `/api/server/restart`, `/api/server/shutdown`, `/api/dashboard`, `/api/db/stats` |
| `tls.py` | `/api/tls`, `/api/tls/upload`, `/api/tls/generate` |
| `topology.py` | `/api/pages`, `/api/nodes`, `/api/links`, `/api/groups`, `/api/settings/{key}` |
| `export.py` | `/api/db/export`, `/api/db/export/logs`, `/api/db/export/bundle`, `/api/db/import`, `/api/audit` |
| `backups.py` | `/api/backups`, `/api/backups/{did}`, `/api/backups/{did}/history`, `/api/backups/{did}/run`, `/api/backups/run/{id}` |
| `alert_rules.py` | `/api/alert/rules`, `/api/alert/rule`, `/api/alert/rule/{id}`, `/api/alert/rule/{id}/toggle`, `/api/alert/rule/{id}/test` |
| `alert_events.py` | `/api/alert/events`, `/api/alert/events/active`, `/api/alert/event/{id}`, `/api/alert/event/{id}/ack`, `/api/alert/event/{id}/resolve` |
| `maintenance_windows.py` | `/api/alert/windows`, `/api/alert/window`, `/api/alert/window/{id}` |
| `ldap.py` | `/api/ldap/settings`, `/api/ldap/test_connection`, `/api/ldap/test_auth` |
| `ipam.py` | `/api/ipam/subnets`, `/api/ipam/subnets/{id}`, `/api/ipam/subnets/{id}/ips`, `/api/ipam/ips/{subnet_id}/{ip}` |

---

## Database Package

| Module | Responsibility |
|--------|----------------|
| `core.py` | Dual write-queues (main + logs), schema init for both DBs, user seeding |
| `migration.py` | One-time safe split of legacy single-DB into Main + Logs DB |
| `persistence.py` | Device/sensor save, load, autosave loop |
| `samples.py` | Buffered probe writes, history & summary queries |
| `events.py` | Flap log, SNMP trap log, sensor error log |
| `users.py` | User management (local + LDAP), user profiles (`full_name`, `email`), `app_settings` key/value store |
| `groups.py` | User group CRUD, member assignment, email resolution for alert dispatch |
| `audit.py` | Audit log write & query |
| `backups.py` | Backup settings (Fernet-encrypted credentials), run history, 3-run retention |
| `trap_defs.py` | SNMP trap definition queries |
| `ipam.py` | Subnet and IP allocation management |

### `app_settings` table

Settings are stored as plain key/value TEXT rows. The in-memory cache (`core/settings.py`) is updated on every write. Key settings:

| Key | Format | Description |
|-----|--------|-------------|
| `scan_ports` | `"ping,22,80,443,…"` | Ports probed by the device scanner; `ping` = ICMP. Default: all 15 built-in ports |
| `snr_type_defaults` | JSON string | Per-sensor-type default intervals/timeouts |
| `backup_enc_key` | Fernet key (base64) | Encryption key for device backup credentials |
| `ldap_bind_pass` | Fernet-encrypted | LDAP service-account bind password |
| `tls_enabled` | `"1"` / `"0"` | HTTPS toggle |
| `http_port` / `https_port` | integer string | Configured listen ports |
| `db_backup_enabled` | `"1"` / `"0"` | Scheduled SQLite DB backup toggle |
| `db_backup_freq` | `"daily"` / `"weekly"` | DB backup schedule frequency |
| `db_backup_time` | `"HH:MM"` | Time of day for scheduled DB backup |
| `db_backup_days` | `"1,2,3,4,5,6,7"` | Days of week for weekly DB backup |
| `db_backup_keep` | integer string | Number of DB backup snapshots to retain (default 7) |

---

## Frontend Structure

The frontend is served as static files — no build step.

| File | Purpose |
|------|---------|
| `index.html` | Main dashboard shell — loads all JS/CSS |
| `style.css` | Application-wide styles and CSS variables |
| `app.js` | Bootstrap, tab routing, SSE connection, shared helpers (`api()`, `toast()`, `esc()`) |
| `dashboard.js` | Customizable widget dashboard (device cards, sparklines, uptime bars, SLA) |
| `devices.js` | Device list, detail panel, port scan modal |
| `sensors.js` | Sensor list, detail panel, history chart |
| `events.js` | Flap/trap/error event log with filters |
| `backups.js` | Backup table, config viewer, patience diff, credential noise toggle, vendor-aware rollback |
| `forms-device.js` | Add/edit device modal |
| `forms-sensor.js` | Add/edit sensor modal |
| `forms-settings.js` | Settings modal (10 tabs: General, Users, Groups, SMTP, Database, Logs, Sensors, Networking, Config Backup, Syslog, Alert Rules) |
| `forms-users.js` | User management, Change Password modal, self-service Edit Profile modal |
| `forms-ldap.js` | LDAP/AD settings modal |
| `forms-io.js` | DB export/import modal |
| `forms-utils.js` | Shared form utilities (field validation, common UI helpers) |
| `alerting.js` | Alert rules editor (conditions, collapsible action blocks with group chip selector), alert history viewer, maintenance windows |
| `ipam.js` | IPAM tab — subnet list, per-subnet IP table, inline editing |
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
8. `monitoring/smtp_alert.py` and `monitoring/syslog_client.py` react to state-change events from their own listener threads.

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

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ldap/settings` | LDAP config (bind password never returned) |
| `PATCH` | `/api/ldap/settings` | Save LDAP config |
| `POST` | `/api/ldap/test_connection` | Test service-account bind |
| `POST` | `/api/ldap/test_auth` | Test full user authentication flow |

### IPAM

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ipam/subnets` | List all subnets with allocation summary |
| `POST` | `/api/ipam/subnets` | Add a subnet `{cidr, name}` |
| `DELETE` | `/api/ipam/subnets/{id}` | Remove subnet and all allocations |
| `GET` | `/api/ipam/subnets/{id}/ips` | IP allocations for a subnet |
| `PUT` | `/api/ipam/ips/{subnet_id}/{ip}` | Set or clear the name for an IP |

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

### Alert Rules

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/alert/rules` | viewer | List all rules |
| `POST` | `/api/alert/rule` | admin | Create rule |
| `GET` | `/api/alert/rule/{id}` | viewer | Get single rule |
| `PATCH` | `/api/alert/rule/{id}` | admin | Update rule |
| `DELETE` | `/api/alert/rule/{id}` | admin | Delete rule |
| `POST` | `/api/alert/rule/{id}/toggle` | operator | Enable / disable rule |
| `POST` | `/api/alert/rule/{id}/test` | admin | Test-fire all actions with synthetic event |
| `POST` | `/api/alert/rules` | admin | Reorder rules `{order: [id, ...]}` |

### Alert Events

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/alert/events` | viewer | Paginated event history |
| `GET` | `/api/alert/events/active` | viewer | Active / unresolved events |
| `POST` | `/api/alert/event/{id}/ack` | operator | Acknowledge event |
| `POST` | `/api/alert/event/{id}/resolve` | operator | Resolve event |

### Maintenance Windows

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/alert/windows` | viewer | List all maintenance windows |
| `POST` | `/api/alert/window` | admin | Create window |
| `PATCH` | `/api/alert/window/{id}` | admin | Update window |
| `DELETE` | `/api/alert/window/{id}` | admin | Delete window |

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

1. Create `routes/<name>.py` with a `handle(method, path, body, req)` function.
2. Register it in `server.py` by adding a route regex in `core/config.py` and a dispatch call in `server.py`'s request handler.
3. Add it to the `routes/` table in this document.
