# PingWatch – AI Build Network Monitoring Platform

![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![License](https://img.shields.io/github/license/Nividan/Pingwatch)
![Stars](https://img.shields.io/github/stars/Nividan/Pingwatch?style=social)


## Table of Contents

- [Project Overview](#project-overview)
- [Features](#features)
- [Technologies Used](#technologies-used)
- [Installation](#installation)
- [Usage](#usage)
- [Screenshots](#screenshots)
- [Device Configuration Backup](#device-configuration-backup)
- [Architecture](#architecture)
- [Core Components](#core-components)
- [Frontend Structure](#frontend-structure)
- [High-Level Flow](#high-level-flow)
- [Project Structure](#project-structure)



## Project Overview

PingWatch is a Python-based network monitoring platform designed to track the availability and health of network devices and services.

The system supports multiple sensor types such as ICMP (ping), HTTP/HTTPS checks, TCP port checks, SNMP, DNS, TLS, and banner probes.
Collected data is displayed in a web-based dashboard that provides real-time event streaming, device management, latency history charts, an interactive network topology visualizer, and automated device configuration backup.


## Features

- 📡 Real-time device monitoring via Server-Sent Events (SSE)
- 🔎 Multiple sensor types (ICMP, HTTP/S, TCP, TLS, SNMP, DNS, Banner)
- ⏱ Configurable monitoring intervals and debounce thresholds
- 📜 Historical event logging with flap and SNMP trap tracking
- 🚨 Email alerting via SMTP (configurable per device/sensor)
- 🌐 Web-based monitoring dashboard with live latency sparklines
- 🗺 Interactive Network Topology Manager (NTM) with draw.io-style editing
- 🔒 Role-based access control (viewer / operator / admin)
- 📤 Database export and import (SQLite backup/restore)
- 🖥 Native desktop status window with optional system-tray icon
- 💾 Automated device configuration backup via SSH and Telnet with encrypted credential storage

### Supported Sensor Types

| Sensor | Description |
|--------|-------------|
| **Ping (ICMP)** | Round-trip latency and packet-loss monitoring |
| **HTTP / HTTPS** | Status code, keyword, and response-time checks |
| **TCP Port** | Port reachability and connection-time checks |
| **TLS** | Certificate validity and TLS handshake checks |
| **SNMP** | OID polling (v1/v2c) |
| **DNS** | Record lookup and resolution-time checks |
| **Banner** | Raw TCP banner capture with optional regex match |


## Technologies Used

- **Backend:** Python 3.x (stdlib only — no third-party web framework)
- **Web Server:** Python's built-in `http.server` (threading mode)
- **Database:** SQLite with WAL mode and a single-writer queue
- **Frontend:** Vanilla HTML, CSS, JavaScript (no build step)
- **Real-time updates:** Server-Sent Events (SSE)
- **System Tray:** pystray + Pillow *(optional)*
- **Network probes:** `socket`, `urllib`, `subprocess`, `pysnmp`
- **SSH backup:** `paramiko` *(required for backup feature)*
- **Credential encryption:** `cryptography` (Fernet) *(required for backup feature)*


## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Nividan/Pingwatch.git
   ```
2. **Navigate into the project directory:**
   ```bash
   cd Pingwatch
   ```
3. **Install dependencies and start the server:**
   ```bash
   start.bat
   ```

> `start.bat` automatically installs `paramiko` and `cryptography` if they are not already present.


## Usage

```bash
# With console window (shows log output)
start.bat

# Without console window (runs as a background desktop app)
pythonw pingwatch.pyw
```

After startup, PingWatch opens at **http://localhost:7070** by default.
The first-run password is printed to the console — change it immediately in **Settings → Users**.

> **Linux / macOS:** ICMP ping requires root privileges.
> ```bash
> sudo python3 server.py
> ```


## Screenshots

### 📡 Network Dashboard
Real-time monitoring of all devices with live status, latency, and connectivity.
<img width="2201" height="671" alt="image" src="https://github.com/user-attachments/assets/91e2237f-a3c8-447c-adbc-5d91e950f63a" />
<img width="2207" height="571" alt="image" src="https://github.com/user-attachments/assets/276fc670-1425-4150-ae9d-21ee33da8565" />

### 🖥 Device Information
View detailed information for every device including IP address, latency, uptime, and custom notes.
<img width="1366" height="863" alt="image" src="https://github.com/user-attachments/assets/06a38bfa-3dd1-431e-8dd1-60873d9624e8" />
<img width="1138" height="563" alt="image" src="https://github.com/user-attachments/assets/3a027022-4a46-4fc2-b2e3-9f017b06a2e8" />
<img width="1071" height="875" alt="image" src="https://github.com/user-attachments/assets/131ceef8-bb9c-4abb-9346-f993f409365f" />
<img width="1054" height="907" alt="image" src="https://github.com/user-attachments/assets/c456c19b-348b-44f8-b68c-3ae9c48438af" />

### 📜 Event Logs
Centralized event logging with timestamps, severity levels, and device filtering.
<img width="2211" height="656" alt="image" src="https://github.com/user-attachments/assets/210e31ec-6367-4e60-bcbd-5257f36f5a5d" />
<img width="625" height="428" alt="image" src="https://github.com/user-attachments/assets/a9a1e8ef-6da1-40c2-b31c-e5a4548f5cbb" />
<img width="2203" height="661" alt="image" src="https://github.com/user-attachments/assets/3a26e38d-6f12-46db-9d46-11f27561d001" />
<img width="1379" height="472" alt="image" src="https://github.com/user-attachments/assets/c5ac9a0e-b959-458c-a568-74af1b8f24cd" />

### 🗺 Network Topology Visualization
NTM provides an interactive topology map where devices, switches, and servers are displayed visually with their connections.

**Monitor Live Device NTM**
<img width="2143" height="1195" alt="image" src="https://github.com/user-attachments/assets/2eff647b-befd-4c4c-b0e6-ee43adb1c713" />

**Draw.io style NTM**
<img width="2200" height="1209" alt="image" src="https://github.com/user-attachments/assets/f42cb4f3-4167-4c91-b6d2-df635ad7c4ef" />

### 💾 Device Config Backup




---

## Device Configuration Backup

PingWatch includes a built-in **Configuration Backup** system that connects to network devices over SSH or Telnet, retrieves their running configuration, stores it encrypted in the database, and tracks a full revision history.

### How It Works

1. Navigate to the **Backups** tab in the dashboard.
2. Click the ⚙ icon on any device row to open its backup settings.
3. Configure the connection method, credentials, commands, and optional schedule.
4. Click **Run Now** or wait for the scheduled trigger.
5. Click any device row to open the **Config Viewer** and browse revision history.

### Backup Settings per Device

| Field | Description |
|-------|-------------|
| **Method** | `ssh` or `telnet` |
| **Host / Port** | Taken from the monitored device — override port if needed |
| **Username** | Login username for the device |
| **Password** | Stored AES-encrypted (Fernet) in the database — never in plaintext on disk or in memory |
| **Enable password** | Optional second-stage enable password (Cisco-style) |
| **Paging command** | Sent once after login to disable paging (e.g. `terminal length 0` for Cisco, `set cli screen-length 0` for JUNOS) |
| **Commands** | One command per line — each is sent in sequence and its output collected |
| **Timeout** | Per-connection timeout in seconds (default 30 s) |
| **Schedule** | Cron-style expression for automatic runs (e.g. `0 2 * * *` = daily at 02:00) |

### Device Compatibility

| Vendor / OS | Method | Notes |
|-------------|--------|-------|
| **Cisco IOS / IOS-XE** | SSH or Telnet | Paging: `terminal length 0` · Enable password supported |
| **Cisco NX-OS** | SSH | Paging: `terminal length 0` |
| **Juniper JUNOS** | SSH | Login as a CLI user (non-root). Paging: `set cli screen-length 0`. Supports both `password` and `keyboard-interactive` auth |
| **Fortinet FortiGate** | SSH | Paging: `config system console` + `set output standard` |
| **Any SSH/Telnet device** | SSH or Telnet | Supply the correct commands for your platform |

> **JUNOS root users:** The root account drops into a BSD shell. Either use a dedicated CLI user account, or prefix your command with `cli -c "show configuration | display set | no-more"`.

### Security

- Passwords are encrypted at rest using **Fernet symmetric encryption** (AES-128-CBC + HMAC-SHA256).
- The encryption key is auto-generated on first use and stored in the `app_settings` table.
- Plaintext passwords are **never** written to disk, log files, or held in server memory beyond the duration of a single backup run.
- Credentials are fetched fresh from the database on every backup trigger.
- All backup actions are recorded in the **audit log**.

### Config Viewer

Each device keeps the **3 most recent** backup runs (older runs are automatically purged from the database). Click any device row in the Backups table to open the Config Viewer:

- Browse revisions with **← Older / Newer →** navigation
- View run timestamp, success/fail status, config size, and **SHA-256** integrity hash
- **📋 Copy** button copies the full config text to the clipboard
- Real-time status updates via SSE — the table refreshes automatically when a backup completes

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/backups` | List all devices with latest backup metadata |
| `GET` | `/api/backups/<did>` | Get backup settings for a device (no plaintext passwords) |
| `PUT` | `/api/backups/<did>` | Save backup settings |
| `GET` | `/api/backups/<did>/history` | List backup run metadata for a device |
| `GET` | `/api/backups/run/<id>` | Full run record including config text |
| `POST` | `/api/backups/<did>/run` | Trigger an immediate backup (async, rate-limited 30 s) |
| `DELETE` | `/api/backups/run/<id>` | Delete a specific backup run *(admin only)* |

---

## Architecture

PingWatch follows a layered, modular architecture:

```
Browser / Desktop GUI
        │
        ▼
  server.py  ──  routes/          ← HTTP dispatcher + route modules
        │
        ├── state.py              ← In-memory runtime state (devices, sensors)
        ├── probes.py             ← Sensor engine (ICMP, HTTP, TCP, …)
        ├── backup_engine.py      ← SSH / Telnet config backup engine
        ├── db/                   ← SQLite persistence package
        ├── auth.py               ← Session management & RBAC
        ├── network_map.py        ← Topology (NTM) data layer
        ├── smtp_alert.py         ← Email notifications
        └── trap_receiver.py      ← SNMP trap ingestion
```

This design keeps each layer independently testable and allows new sensor types or route groups to be added without touching unrelated code.


## Core Components

### Backend

- **`server.py`** — HTTP dispatcher and application entry point.
  Serves static files, delegates every API route to a `routes/` module, and starts background threads.

- **`app_state.py`** — Shared runtime globals (`STATE`, effective ports, tray-icon reference).
  Prevents circular imports between `server.py` and `routes/`.

- **`state.py`** — In-memory runtime state manager.
  Holds all `Device` and `Sensor` objects, manages probe threads, and broadcasts SSE events to connected clients.

- **`probes.py`** — Sensor engine.
  Implements every monitoring probe type: ICMP, HTTP/S, TCP, TLS, SNMP, DNS, Banner.

- **`backup_engine.py`** — Configuration backup engine.
  Opens SSH (paramiko) or Telnet connections to network devices, sends user-defined commands, collects output, and returns the result for storage. Supports password auth, keyboard-interactive auth (JUNOS), enable-mode escalation (Cisco), paging disable, and configurable per-command idle timeouts.

- **`auth.py`** — Authentication and session management.
  Handles login, password hashing (bcrypt-style), RBAC roles (`viewer` / `operator` / `admin`), and active sessions.

- **`network_map.py`** — Network Topology Manager (NTM) backend.
  Manages topology pages, nodes, links, groups, and map settings stored in the database.

- **`trap_receiver.py`** — SNMP trap listener.
  Binds a UDP socket on the configured SNMP port and injects incoming traps into the event pipeline.

- **`smtp_alert.py`** — Email alerting.
  Sends down/up notifications via SMTP when sensor states change.

- **`logger.py`** — Central logging.
  Provides the application logger, audit logger, and an in-memory log buffer used by the desktop GUI.

- **`settings.py` / `config.py`** — Configuration layer.
  `config.py` holds file paths, compiled route regexes, and startup constants.
  `settings.py` provides a thread-safe runtime settings cache backed by the database.

- **`gui.py`** — Desktop status window.
  Lightweight tkinter window with a live log view, quick-launch button, and quit control.

- **`pingwatch.pyw` / `start.bat`** — Launch helpers.

### Route Modules (`routes/`)

| Module | Endpoints handled |
|--------|-------------------|
| `auth.py` | `/api/login`, `/api/logout`, `/api/me`, `/api/users`, `/api/me/password` |
| `devices.py` | `/api/devices`, `/api/device`, `/api/devices/{did}`, `/api/sensors/{did}/*` |
| `monitoring.py` | `/events` (SSE), `/api/flaps`, `/api/traps`, `/api/snmp/*` |
| `settings.py` | `/api/settings`, `/api/server_info`, `/api/settings/smtp_test` |
| `topology.py` | `/api/pages`, `/api/nodes`, `/api/links`, `/api/groups`, `/api/settings/{key}` |
| `export.py` | `/api/db/export`, `/api/db/import`, `/api/audit` |
| `backups.py` | `/api/backups`, `/api/backups/{did}`, `/api/backups/{did}/history`, `/api/backups/{did}/run`, `/api/backups/run/{id}` |

### Database Package (`db/`)

| Module | Responsibility |
|--------|---------------|
| `core.py` | Write-queue, schema init & migrations, user seeding |
| `persistence.py` | Device/sensor save, load, autosave loop |
| `samples.py` | Buffered probe writes, history & summary queries |
| `events.py` | Flap log, SNMP trap log, sensor error log |
| `users.py` | User management, app settings |
| `audit.py` | Audit log write & query |
| `backups.py` | Backup settings (encrypted), run history, 3-run retention |
| `__init__.py` | Re-exports all public symbols (callers unchanged) |


## Frontend Structure

The frontend lives in `frontend/` and is served as a single inlined HTML page for the main dashboard, plus separate files for the NTM map.

| File | Purpose |
|------|---------|
| `index.html` | Main dashboard shell |
| `style.css` | Main application styling |
| `app.js` | Bootstrap, tab routing, shared app logic |
| `dashboard.js` | Device cards, live latency sparklines |
| `devices.js` | Device list and detail panel |
| `sensors.js` | Sensor list and detail panel |
| `events.js` | Flap/trap/error event log viewer |
| `backups.js` | Backup table, settings modal, config viewer |
| `forms-device.js` | Add/edit device form |
| `forms-sensor.js` | Add/edit sensor form |
| `forms-settings.js` | Application settings form |
| `forms-users.js` | User management form |
| `forms-io.js` | DB export/import form |
| `forms-utils.js` | Shared form helpers |
| `bg.js` | Animated background canvas (aurora + radar) |
| `map.html` | Network Topology Manager shell |
| `map.css` | NTM styles |
| `map.js` | NTM canvas engine, drag-and-drop topology editor |


## High-Level Flow

1. User opens the **web dashboard** in a browser or the **desktop GUI**.
2. **`server.py`** receives every HTTP request and dispatches it to the matching `routes/` module.
3. Route handlers read/update runtime objects in **`state.py`** and call **`db/`** for persistence.
4. Monitoring probes run on per-sensor background threads via **`probes.py`**.
5. Probe results are pushed to connected browsers over **SSE** (`/events`).
6. State changes persist automatically through the autosave loop (every 60 s) and an immediate write-queue for high-priority operations.
7. **`smtp_alert.py`** sends email alerts when sensors transition between up/down states.
8. **`trap_receiver.py`** ingests asynchronous SNMP traps and routes them into the event pipeline.
9. **`backup_engine.py`** connects to devices on demand or on schedule, retrieves configuration, and stores it encrypted in the database via `db/backups.py`.


## Project Structure

```
pingwatch/
├── server.py               ← HTTP dispatcher + entry point
├── app_state.py            ← Shared runtime globals
├── state.py                ← In-memory device/sensor state
├── probes.py               ← Sensor engine
├── backup_engine.py        ← SSH / Telnet config backup engine
├── auth.py                 ← Authentication & RBAC
├── network_map.py          ← NTM topology data layer
├── trap_receiver.py        ← SNMP trap listener
├── smtp_alert.py           ← Email alerting
├── logger.py               ← Logging
├── settings.py             ← Runtime settings cache
├── config.py               ← Constants & route regexes
├── snmp_catalog.py         ← SNMP OID catalog
├── gui.py                  ← Desktop status window
├── pingwatch.pyw           ← Windowless launcher
├── start.bat               ← Console launcher
├── requirements.txt        ← Python dependencies
│
├── db/                     ← SQLite persistence package
│   ├── __init__.py         ← Re-exports all public symbols
│   ├── core.py             ← Write-queue, schema, migrations
│   ├── persistence.py      ← Device/sensor save & load
│   ├── samples.py          ← Probe sample buffer & queries
│   ├── events.py           ← Flap, trap, error logs
│   ├── users.py            ← User management & settings
│   ├── audit.py            ← Audit log
│   └── backups.py          ← Backup settings & run history
│
├── routes/                 ← HTTP route handlers
│   ├── auth.py             ← Login, logout, users
│   ├── devices.py          ← Device & sensor CRUD
│   ├── monitoring.py       ← SSE, flaps, traps, SNMP
│   ├── settings.py         ← App settings, server info
│   ├── topology.py         ← NTM pages/nodes/links/groups
│   ├── export.py           ← DB export/import, audit
│   └── backups.py          ← Device config backup API
│
└── frontend/               ← Web UI (served statically)
    ├── index.html
    ├── style.css
    ├── app.js
    ├── dashboard.js
    ├── devices.js
    ├── sensors.js
    ├── events.js
    ├── backups.js
    ├── forms-device.js
    ├── forms-sensor.js
    ├── forms-settings.js
    ├── forms-users.js
    ├── forms-io.js
    ├── forms-utils.js
    ├── bg.js
    ├── map.html
    ├── map.css
    └── map.js
```
