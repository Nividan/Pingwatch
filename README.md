# PingWatch – Real-Time Network Monitoring Platform

![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/github/license/Nividan/Pingwatch)
![Stars](https://img.shields.io/github/stars/Nividan/Pingwatch?style=social)
[![Built with Claude](https://img.shields.io/badge/Built%20with-Claude%20AI-orange?logo=anthropic)](https://claude.ai)


## Table of Contents

- [Project Overview](#project-overview)
- [Features](#features)
- [Technologies Used](#technologies-used)
- [Installation](#installation)
- [Usage](#usage)
- [HTTPS / TLS](#https--tls)
- [Syslog Forwarding](#syslog-forwarding)
- [LDAP / Active Directory Authentication](#ldap--active-directory-authentication)
- [IP Address Management (IPAM)](#ip-address-management-ipam)
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

> 🤖 This project was designed and built with [Claude AI](https://claude.ai) (Anthropic) as an AI-driven development experiment — from architecture to implementation.


## Features

- 📡 Real-time device monitoring via Server-Sent Events (SSE)
- 🔎 Multiple sensor types (ICMP, HTTP/S, TCP, TLS, SNMP, DNS, Banner)
- ⏱ Configurable monitoring intervals and debounce thresholds
- 📜 Historical event logging with flap and SNMP trap tracking
- 🚨 Email alerting via SMTP (configurable per device/sensor)
- 🌐 Web-based monitoring dashboard with live latency sparklines
- 🗺 Interactive Network Topology Manager (NTM) with draw.io-style editing
- 🔒 Role-based access control (viewer / operator / admin)
- 🔐 Native HTTPS / TLS 1.2+ with self-signed or imported certificates
- 📤 Database export and import (SQLite backup/restore, up to 2 GB)
- 🖥 Native desktop status window with optional system-tray icon
- 💾 Automated device configuration backup via SSH and Telnet with encrypted credential storage
- 🧙 Interactive first-run setup wizard (`start.bat` on Windows, `bash start.sh` on Linux/macOS → `setup_wizard.py`)
- 🐧 Native Linux / macOS support — headless server mode, systemd service, auto package-manager detection
- 📨 Syslog forwarding — RFC 5424 UDP/TCP forwarding of events to any syslog server
- 🔁 Server restart & shutdown from the web UI (Settings → General)
- 🏢 LDAP / Active Directory authentication — domain users log in with AD credentials; bind password encrypted at rest
- 🗂 IP Address Management (IPAM) — subnet tracking with per-IP name/allocation management and live ping-sweep integration

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
- **Web Server:** Python's built-in `http.server` (threading mode) + `ssl.SSLContext` for HTTPS
- **Database:** SQLite with WAL mode and a single-writer queue
- **Frontend:** Vanilla HTML, CSS, JavaScript (no build step)
- **Real-time updates:** Server-Sent Events (SSE)
- **TLS / HTTPS:** `cryptography` library (RSA-2048, X.509, Fernet key encryption)
- **System Tray:** pystray + Pillow *(optional)*
- **Network probes:** `socket`, `urllib`, `subprocess`, `pysnmp`
- **SSH backup:** `paramiko` *(required for backup feature)*
- **Credential encryption:** `cryptography` (Fernet) *(required for backup feature)*
- **LDAP / AD authentication:** `ldap3` *(optional — required only when LDAP auth is enabled)*


## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Nividan/Pingwatch.git
   ```
2. **Navigate into the project directory:**
   ```bash
   cd Pingwatch
   ```
3. **Run the launcher — the setup wizard starts automatically on first launch:**

   **Windows:**
   ```bat
   start.bat (with admin priv)
   ```

   **Linux / macOS:**
   ```bash
   sudo bash start.sh
   ```

The first-run wizard checks required packages, configures HTTP/HTTPS ports, generates a TLS certificate, sets firewall rules, and initialises the database — all interactively. Subsequent launches skip the wizard and go straight to the server.

To re-run the wizard on an existing install:

```bat
start.bat --setup          # Windows
```
```bash
bash start.sh --setup      # Linux / macOS
```

**Background service (Linux — equivalent of `pingwatch.pyw`):**
```bash
sudo bash start.sh --install-service     # install + start systemd service
sudo systemctl start pingwatch           # start
sudo systemctl stop pingwatch            # stop
sudo systemctl restart pingwatch         # restart
sudo systemctl status pingwatch          # check status
journalctl -u pingwatch -f               # live logs
sudo bash start.sh --uninstall-service   # remove service
```


## Usage

| Mode | Windows | Linux / macOS |
|------|---------|---------------|
| Foreground (console visible) | `start.bat` | `sudo bash start.sh` |
| Background / no console | `pythonw pingwatch.pyw` | `sudo bash start.sh --install-service` |
| Re-run setup wizard | `start.bat --setup` | `bash start.sh --setup` |

After startup, PingWatch is available at **https://localhost:8443** by default (HTTPS enabled on fresh installs).
The first-run password is printed to the console — change it immediately in **Settings → Users**.

### Linux / macOS Notes

- **Privileged ports** (< 1024) require root. The wizard warns at startup listing all affected ports (HTTP, HTTPS, SNMP). Use `sudo`, reconfigure to ports ≥ 1024, or use the systemd service with `AmbientCapabilities=CAP_NET_BIND_SERVICE`.
- **Headless mode** — the wizard asks whether a desktop GUI is needed. Selecting "no" skips tkinter / pystray / Pillow entirely. The server runs as a pure background process with no GUI warnings.
- **SNMP port 162** — requires root. Alternatively redirect with `iptables -t nat -A PREROUTING -p udp --dport 162 -j REDIRECT --to-port 1162` and use port 1162.
- **Package managers** — the wizard auto-detects `apt` / `dnf` / `yum` / `brew` and falls back to the system package manager when `pip` is unavailable or permissions are denied.


## HTTPS / TLS

PingWatch ships with native HTTPS support enabled by default on fresh installs.

### How it works

- The server wraps its built-in HTTP listener with `ssl.SSLContext` (TLS 1.2+ enforced, compression disabled).
- On startup, the certificate is discovered in this order:
  1. **Database** — previously generated or uploaded certificate (stored as PEM; private key Fernet-encrypted)
  2. **`certs/` folder** — `cert.pem` + `key.pem` (validated before use)
  3. **Auto-generate** — a new RSA-2048 self-signed certificate

### Certificate management

Navigate to **Settings → Networking → HTTPS / TLS**:

| Action | Description |
|--------|-------------|
| **Generate self-signed** | Fill in CN, Organization, OU, Country, State, Locality, validity period, and optional extra SANs (DNS names or IP addresses). Certificate is stored encrypted in the database. |
| **Upload certificate** | Paste an existing PEM certificate + private key. The pair is validated before saving. |
| **Enable / Disable HTTPS** | Toggle TLS on or off (restart required). |
| **HTTP → HTTPS redirect** | Optionally run a second lightweight HTTP listener on the HTTP port that redirects all traffic to HTTPS. |

### Subject Alternative Names (SANs)

When generating a self-signed certificate, the following SANs are always included automatically:
- The CN / hostname you specify
- `localhost` and `127.0.0.1`
- Your machine's local hostname

You can add extra SANs (additional DNS names or IP addresses) in both the setup wizard and the Settings UI — just enter one per line (UI) or comma-separated (wizard). No `DNS:` / `IP:` prefix needed; the type is detected automatically.

### Certificate expiry

PingWatch logs a **WARNING** at startup when the active certificate expires within 30 days, and an **ERROR** if it is already expired. Upload a new certificate in **Settings → Networking** to resolve it.

### Ports (defaults)

| Port | Protocol | Purpose |
|------|----------|---------|
| 7070 | HTTP | Dashboard (or redirect to HTTPS) |
| 8443 | HTTPS | Main TLS-secured dashboard |
| 1162 | UDP | SNMP trap reception |

All ports are configurable in **Settings → Networking** or during the first-run wizard.


## Syslog Forwarding

PingWatch can forward events to any syslog server in **RFC 5424** format over UDP or TCP.

Configure in **Settings → Syslog**:

| Setting | Description |
|---------|-------------|
| **Host / Port** | Remote syslog server address (default port 514) |
| **Protocol** | UDP (default) or TCP |
| **Min Severity** | Filter threshold — only events at or above this level are forwarded: `critical` / `warning` / `down` / `recovered` / `info` |

### Event mapping

| PingWatch event | Syslog severity |
|-----------------|-----------------|
| `flap_down` | WARNING |
| `flap_recovered` | NOTICE |
| `snmp_trap` | Mapped from trap severity |

### Implementation notes

- **Format:** RFC 5424, facility LOCAL0
- **Non-blocking:** daemon queue thread with a 500-entry bounded queue — monitor threads are never stalled
- **Zero-restart reconfiguration:** settings are re-read on every send; toggling or reconfiguring syslog takes effect immediately
- **Test button:** Settings → Syslog → **Send Test Message**


## LDAP / Active Directory Authentication

PingWatch supports optional LDAP/AD authentication so domain users can log in with their Active Directory credentials. Local users are unaffected — disabling or misconfiguring LDAP never locks out the local `admin` account.

### How it works

1. Admin enables LDAP in **Settings → Users → LDAP Settings** and saves the connection details.
2. Admin creates a **domain user** in **Settings → Users → Add User** by selecting *Auth type: Domain* and entering the AD username, domain, and role.
3. When the domain user logs in, PingWatch:
   - Binds as the service account to locate the user's full DN.
   - Binds again as the user with their supplied password to verify credentials.
   - Creates a session on success; returns 401 on any failure.
4. Local users continue to authenticate via PBKDF2-SHA256 password hash — the LDAP path is only taken for users whose `auth_type` is `ldap`.

### Login formats accepted

All three formats resolve to the same local username in the database:

| Format | Example |
|--------|---------|
| Plain username | `jsmith` |
| `DOMAIN\username` | `CORP\jsmith` |
| `username@domain` | `jsmith@corp.local` |

### Configuration (Settings → Users → LDAP Settings)

| Field | Description |
|-------|-------------|
| **Enable LDAP Authentication** | Master toggle — off by default |
| **Server** | LDAP/AD server hostname or IP |
| **Port** | 389 (plain/StartTLS) or 636 (LDAPS); auto-switches when security mode changes |
| **Security** | `None` / `LDAPS` (TLS-wrapped) / `StartTLS` (upgrade after connect) |
| **Base DN** | Root search base, e.g. `DC=corp,DC=local` |
| **Bind DN** | Service account DN, e.g. `CN=svc,OU=SvcAccounts,DC=corp,DC=local` |
| **Bind Password** | Service account password — encrypted with Fernet at rest, never returned by the API |
| **User Search Filter** | LDAP filter to locate the user; `{username}` is substituted with the RFC 4515-escaped username. Default: `(sAMAccountName={username})` |
| **NetBIOS Domain** | Optional — used as label when displaying domain users |
| **Timeout** | Connection timeout in seconds (default 10) |

### Test buttons

| Button | What it does |
|--------|-------------|
| **Test Connection** | Binds as the service account and reports success/failure inline — accepts unsaved form values so you can test before saving |
| **Test User Auth** | Opens a mini dialog to enter AD credentials and runs the full bind+search+user-bind flow |

### Security notes

- The bind password is Fernet-encrypted before being written to the database and decrypted in-memory only.
- The API never returns the bind password — only `ldap_bind_pass_set: true/false`.
- Usernames are RFC 4515-escaped before substitution into LDAP filter strings.
- An empty password is rejected immediately (prevents anonymous bind from succeeding).
- If the LDAP server is unreachable, domain users cannot log in; local users are unaffected.
- The `pw_hash` column stores the sentinel value `__ldap__` for domain users, which fails `_verify_pw` cleanly as defense-in-depth.

### Dependencies

```
ldap3>=2.9
```

`ldap3` is a pure-Python library with no OS-level dependencies. On Debian/Ubuntu with Python's PEP 668 "externally-managed" restriction:

```bash
pip install ldap3 --break-system-packages
# or inside a virtualenv: pip install ldap3
```

### LDAP API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ldap/settings` | LDAP configuration (bind password never returned) |
| `PATCH` | `/api/ldap/settings` | Save LDAP configuration |
| `POST` | `/api/ldap/test_connection` | Test service-account bind (accepts unsaved overrides) |
| `POST` | `/api/ldap/test_auth` | Test full user authentication flow |


## IP Address Management (IPAM)

PingWatch includes a built-in IPAM module for tracking IP address allocations across subnets.

### How it works

1. Navigate to the **IPAM** tab in the dashboard.
2. Add a subnet in CIDR notation (e.g. `192.168.1.0/24`).
3. PingWatch expands the subnet and pre-populates every host IP.
4. Click any IP row to assign a name/label (hostname, device, or description).
5. Monitored PingWatch devices are automatically linked to their IPAM entries — their device name populates the IP label when the device is created.

### Features

- Subnet list with allocation counts and utilisation percentage
- Per-subnet IP table with used/free visual distinction (green accent on allocated rows)
- Inline editing — click an IP to assign or clear its name
- Automatic sync — adding a device updates the matching IPAM entry if the IP falls in a managed subnet
- Subnets up to `/9` supported (larger subnets are rejected to prevent accidental expansion)

### IPAM API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/ipam/subnets` | List all subnets with allocation summary |
| `POST` | `/api/ipam/subnets` | Add a new subnet `{cidr, name}` |
| `DELETE` | `/api/ipam/subnets/<id>` | Remove subnet and all its allocations |
| `GET` | `/api/ipam/subnets/<id>/ips` | Get all IP allocations for a subnet |
| `PUT` | `/api/ipam/ips/<subnet_id>/<ip>` | Set or clear the name for an IP |


## Screenshots

### 📡 Network Dashboard
Real-time monitoring of all devices with live status, latency, and connectivity.
<img width="800" alt="Network Dashboard" src="https://github.com/user-attachments/assets/91e2237f-a3c8-447c-adbc-5d91e950f63a" />
<img width="800" alt="Network Dashboard 2" src="https://github.com/user-attachments/assets/276fc670-1425-4150-ae9d-21ee33da8565" />

### 🖥 Device Information
View detailed information for every device including IP address, latency, uptime, and custom notes.
<img width="800" alt="Device List" src="https://github.com/user-attachments/assets/06a38bfa-3dd1-431e-8dd1-60873d9624e8" />
<img width="800" alt="Device Detail" src="https://github.com/user-attachments/assets/3a027022-4a46-4fc2-b2e3-9f017b06a2e8" />
<img width="480" alt="Device Panel" src="https://github.com/user-attachments/assets/131ceef8-bb9c-4abb-9346-f993f409365f" />
<img width="480" alt="Device Panel 2" src="https://github.com/user-attachments/assets/c456c19b-348b-44f8-b68c-3ae9c48438af" />

### 📜 Event Logs
Centralized event logging with timestamps, severity levels, and device filtering.
<img width="800" alt="Event Log" src="https://github.com/user-attachments/assets/210e31ec-6367-4e60-bcbd-5257f36f5a5d" />
<img width="500" alt="Event Filter" src="https://github.com/user-attachments/assets/a9a1e8ef-6da1-40c2-b31c-e5a4548f5cbb" />
<img width="800" alt="Event Log 2" src="https://github.com/user-attachments/assets/3a26e38d-6f12-46db-9d46-11f27561d001" />
<img width="800" alt="Event Log 3" src="https://github.com/user-attachments/assets/c5ac9a0e-b959-458c-a568-74af1b8f24cd" />

### 🗺 Network Topology Visualization
NTM provides an interactive topology map where devices, switches, and servers are displayed visually with their connections.

**Monitor Live Device NTM**
<img width="800" alt="Live Topology Map" src="https://github.com/user-attachments/assets/2eff647b-befd-4c4c-b0e6-ee43adb1c713" />

**Draw.io style NTM**
<img width="800" alt="Topology Editor" src="https://github.com/user-attachments/assets/f42cb4f3-4167-4c91-b6d2-df635ad7c4ef" />

### Device Configuration Backup
PingWatch includes a built-in **Configuration Backup** system that connects to network devices over SSH or Telnet, retrieves their running configuration, stores it encrypted in the database, and tracks a full revision history.

<img width="800" alt="Backup Table" src="https://github.com/user-attachments/assets/0f94bfcd-d5e7-40aa-b950-c711c72f325b" />
<img width="480" alt="Backup Settings" src="https://github.com/user-attachments/assets/886afeb8-e44d-487d-92b9-7ab3dbddab04" />
<img width="480" alt="Config Viewer" src="https://github.com/user-attachments/assets/5026650f-be34-44aa-a017-c813cf75880d" />

### IP-Address Manager
Track and manage IP allocations across subnets — view utilisation at a glance, assign names to individual IPs, and see which addresses are linked to monitored devices.
<img width="681" height="492" alt="image" src="https://github.com/user-attachments/assets/42864325-72b5-4e7b-80ea-16637beb0d5f" />
<img width="2166" height="364" alt="image" src="https://github.com/user-attachments/assets/2397d53a-8891-4a96-a5f3-865dcb859f6c" />




---
## Device Configuration Backup
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
- SSH host keys are verified using **TOFU** (Trust On First Use) — stored in `ssh_known_hosts.txt` and rejected if they change.
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
        ├── core/                 ← Config, state, auth, TLS, logging
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
        └── db/                   ← SQLite persistence package
```

This design keeps each layer independently testable and allows new sensor types or route groups to be added without touching unrelated code.


## Core Components

### Backend

- **`server.py`** — HTTP dispatcher and application entry point.
  Serves static files, delegates every API route to a `routes/` module, and starts background threads. Wraps the HTTP listener with `ssl.SSLContext` when HTTPS is enabled.

- **`core/tls.py`** — TLS certificate management module.
  Handles RSA-2048 self-signed certificate generation (full X.509 subject + custom SANs), certificate discovery (DB → `certs/` folder → auto-generate), SSL context construction, certificate metadata parsing, pair validation, and expiry warnings.

- **`setup_wizard.py`** — Interactive first-run setup wizard.
  Now cross-platform (Windows / Linux / macOS). Guides new installs through package checks, HTTP/HTTPS port selection, TLS certificate setup, SNMP port configuration, firewall rules, and desktop shortcut creation. On Linux/macOS asks whether a desktop GUI is needed — if not, skips tkinter/pystray/Pillow entirely (headless server mode). Auto-detects system package manager (apt/dnf/yum/brew). Checks existing firewall rules before adding new ones. Writes all choices to the database and exits cleanly so the launcher can start `server.py`.

- **`core/app_state.py`** — Shared runtime globals (`STATE`, effective ports, TLS active flag, tray-icon reference).
  Prevents circular imports between `server.py` and `routes/`.

- **`core/state.py`** — In-memory runtime state manager.
  Holds all `Device` and `Sensor` objects, manages probe threads, and broadcasts SSE events to connected clients.

- **`monitoring/probes.py`** — Sensor engine.
  Implements every monitoring probe type: ICMP, HTTP/S, TCP, TLS, SNMP, DNS, Banner.

- **`backup/engine.py`** — Configuration backup engine.
  Opens SSH (paramiko) or Telnet connections to network devices, sends user-defined commands, collects output, and returns the result for storage. Supports TOFU SSH host key verification, password auth, keyboard-interactive auth (JUNOS), enable-mode escalation (Cisco), paging disable, and configurable per-command idle timeouts.

- **`core/auth.py`** — Authentication and session management.
  Handles login, password hashing (PBKDF2-SHA256), RBAC roles (`viewer` / `operator` / `admin`), active sessions, and domain-prefix stripping (`DOMAIN\user`, `user@domain`). Branches on `auth_type` to delegate LDAP users to `core/ldap_auth.py`.

- **`core/ldap_auth.py`** — LDAP / Active Directory authentication helpers.
  Provides `ldap_authenticate` (called at login for domain users), `ldap_test_connection` (admin connectivity test), and `ldap_test_auth_user` (full bind+search+user-bind flow). Supports plain LDAP, LDAPS, and StartTLS. Bind password decrypted in-memory only; never logged. `ldap3` import deferred inside functions so the library is optional — local users are unaffected if it is absent.

- **`monitoring/network_map.py`** — Network Topology Manager (NTM) backend.
  Manages topology pages, nodes, links, groups, and map settings stored in the database.

- **`snmp/receiver.py`** — SNMP trap listener.
  Binds a UDP socket on the configured SNMP port and injects incoming traps into the event pipeline.

- **`snmp/enricher.py`** — SNMP trap enrichment.
  Resolves OIDs to human-readable names, identifies the sending vendor, and annotates trap events with category and severity.

- **`monitoring/smtp_alert.py`** — Email alerting.
  Sends down/up notifications via SMTP when sensor states change. Rate-limits repeated SMTP failure logs (5-minute suppression per host).

- **`monitoring/syslog_client.py`** — RFC 5424 syslog forwarding client.
  Non-blocking daemon queue thread forwards PingWatch events to any syslog server via UDP or TCP. Severity filter, no-restart reconfiguration (settings re-read on every send), bounded queue (500 entries) so monitor threads are never blocked.

- **`core/logger.py`** — Central logging.
  Provides the application logger, audit logger, and an in-memory log buffer used by the desktop GUI.

- **`core/settings.py` / `core/config.py`** — Configuration layer.
  `config.py` holds file paths, compiled route regexes, and startup constants.
  `settings.py` provides a thread-safe runtime settings cache backed by the database.

- **`gui.py`** — Desktop status window.
  Lightweight tkinter window with a live log view, quick-launch button, and quit control.

- **`start.bat`** — Windows launcher. Elevates to admin, checks Python 3.8+, detects first run (or `--setup` flag), invokes `setup_wizard.py` when needed, then starts `server.py`.

- **`start.sh`** — Linux/macOS launcher. Python version check, first-run wizard detection, privileged-port warning (reads configured ports from DB). Flags: `--setup` (re-run wizard), `--install-service` (patch and install `pingwatch.service` via systemd, `CAP_NET_BIND_SERVICE`), `--uninstall-service`.

- **`pingwatch.pyw`** — Windows windowless launcher (no console window). Linux/macOS equivalent: `sudo bash start.sh --install-service`.

- **`pingwatch.service`** — systemd unit file. Patched and installed to `/etc/systemd/system/` by `start.sh --install-service`. Uses `AmbientCapabilities=CAP_NET_BIND_SERVICE` to bind privileged ports without running the process as root.

### Route Modules (`routes/`)

| Module | Endpoints handled |
|--------|-------------------|
| `auth.py` | `/api/login`, `/api/logout`, `/api/me`, `/api/users`, `/api/me/password` |
| `devices.py` | `/api/devices`, `/api/device`, `/api/devices/{did}`, `/api/sensors/{did}/*` |
| `monitoring.py` | `/events` (SSE), `/api/flaps`, `/api/traps`, `/api/events/summary`, `/api/snmp/*` |
| `settings.py` | `/api/settings`, `/api/server_info`, `/api/settings/smtp_test`, `/api/settings/syslog_test`, `/api/server/restart`, `/api/server/shutdown`, `/api/dashboard` |
| `tls.py` | `/api/tls`, `/api/tls/upload`, `/api/tls/generate` |
| `topology.py` | `/api/pages`, `/api/nodes`, `/api/links`, `/api/groups`, `/api/settings/{key}` |
| `export.py` | `/api/db/export`, `/api/db/import`, `/api/audit` |
| `backups.py` | `/api/backups`, `/api/backups/{did}`, `/api/backups/{did}/history`, `/api/backups/{did}/run`, `/api/backups/run/{id}` |
| `ldap.py` | `/api/ldap/settings`, `/api/ldap/test_connection`, `/api/ldap/test_auth` |
| `ipam.py` | `/api/ipam/subnets`, `/api/ipam/subnets/{id}`, `/api/ipam/subnets/{id}/ips`, `/api/ipam/ips/{subnet_id}/{ip}` |

### TLS API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/tls` | Certificate metadata + TLS settings (no private key) |
| `PATCH` | `/api/tls` | Update `tls_enabled`, `tls_port`, `http_redirect` |
| `POST` | `/api/tls/upload` | Upload and validate a new PEM cert + key pair |
| `POST` | `/api/tls/generate` | Generate a new self-signed certificate |

### Database Package (`db/`)

| Module | Responsibility |
|--------|----------------|
| `core.py` | Write-queue, schema init & migrations, user seeding |
| `persistence.py` | Device/sensor save, load, autosave loop |
| `samples.py` | Buffered probe writes, history & summary queries |
| `events.py` | Flap log, SNMP trap log, sensor error log |
| `users.py` | User management (local + LDAP domain users), app settings |
| `audit.py` | Audit log write & query |
| `backups.py` | Backup settings (encrypted), run history, 3-run retention |
| `trap_defs.py` | SNMP trap definition queries |
| `ipam.py` | Subnet and IP allocation management |
| `__init__.py` | Re-exports all public symbols (callers unchanged) |


## Frontend Structure

The frontend lives in `frontend/` and is served as a single inlined HTML page for the main dashboard, plus separate files for the NTM map.

| File | Purpose |
|------|---------|
| `index.html` | Main dashboard shell |
| `style.css` | Main application styling |
| `app.js` | Bootstrap, tab routing, shared app logic |
| `dashboard.js` | Customizable widget dashboard (device cards, sparklines, event summary, uptime bars, SLA, and more) |
| `devices.js` | Device list and detail panel |
| `sensors.js` | Sensor list and detail panel |
| `events.js` | Flap/trap/error event log viewer |
| `backups.js` | Backup table, settings modal, config viewer |
| `forms-device.js` | Add/edit device form |
| `forms-sensor.js` | Add/edit sensor form |
| `forms-settings.js` | Application settings form (including TLS/Networking and Users tabs) |
| `forms-users.js` | User management form — local and domain (LDAP) user creation with auth-type badge |
| `forms-ldap.js` | LDAP/AD settings modal — connection config, Test Connection, Test User Auth |
| `forms-io.js` | DB export/import form |
| `ipam.js` | IPAM tab — subnet list, per-subnet IP table, inline name editing |
| `forms-utils.js` | Shared form helpers |
| `bg.js` | Animated background canvas (aurora + radar) |
| `map.html` | Network Topology Manager shell |
| `map.css` | NTM styles |
| `map.js` | NTM canvas engine, drag-and-drop topology editor |


## High-Level Flow

1. User opens the **web dashboard** in a browser or the **desktop GUI**.
2. **`server.py`** receives every HTTP request and dispatches it to the matching `routes/` module. When HTTPS is enabled the socket is wrapped with `ssl.SSLContext`; a second lightweight HTTP server optionally redirects plain-HTTP traffic to HTTPS.
3. Route handlers read/update runtime objects in **`core/state.py`** and call **`db/`** for persistence.
4. Monitoring probes run on per-sensor background threads via **`monitoring/probes.py`**.
5. Probe results are pushed to connected browsers over **SSE** (`/events`).
6. State changes persist automatically through the autosave loop (every 60 s) and an immediate write-queue for high-priority operations.
7. **`monitoring/smtp_alert.py`** sends email alerts when sensors transition between up/down states.
8. **`monitoring/syslog_client.py`** forwards events to configured syslog server(s) via a non-blocking daemon queue.
9. **`snmp/receiver.py`** ingests asynchronous SNMP traps and routes them into the event pipeline via **`snmp/enricher.py`**.
10. **`backup/engine.py`** connects to devices on demand or on schedule, retrieves configuration, and stores it encrypted in the database via `db/backups.py`.


## Project Structure

```
pingwatch/
├── server.py               ← HTTP/HTTPS dispatcher + entry point
├── setup_wizard.py         ← First-run interactive setup wizard
├── gui.py                  ← Desktop status window (Windows/macOS)
├── pingwatch.pyw           ← Windows windowless launcher
├── start.bat               ← Windows console launcher (first-run detection)
├── start.sh                ← Linux/macOS launcher (foreground + service install)
├── pingwatch.service       ← systemd unit file for background service
├── .gitattributes          ← Line-ending enforcement (start.sh → LF)
├── requirements.txt        ← Python dependencies
├── ssh_known_hosts.txt     ← SSH TOFU host key store (auto-created)
│
├── core/                   ← Application core
│   ├── __init__.py
│   ├── config.py           ← Constants & route regexes
│   ├── settings.py         ← Runtime settings cache (DB-backed)
│   ├── logger.py           ← Central logging & in-memory buffer
│   ├── auth.py             ← Authentication & RBAC (local + LDAP branch)
│   ├── ldap_auth.py        ← LDAP/AD authentication helpers
│   ├── app_state.py        ← Shared runtime globals
│   ├── state.py            ← In-memory device/sensor state
│   └── tls.py              ← TLS certificate management
│
├── monitoring/             ← Active monitoring subsystem
│   ├── __init__.py
│   ├── probes.py           ← Sensor engine (ICMP, HTTP, TCP, …)
│   ├── smtp_alert.py       ← Email alerting
│   ├── syslog_client.py    ← RFC 5424 syslog forwarding client
│   └── network_map.py      ← NTM topology data layer
│
├── backup/                 ← Device configuration backup
│   ├── __init__.py
│   ├── engine.py           ← SSH / Telnet backup engine
│   ├── scheduler.py        ← Backup schedule runner
│   └── configs/            ← Exported config files per device (auto-created)
│       └── <Device Name>/  ← One subfolder per device
│
├── snmp/                   ← SNMP trap pipeline
│   ├── __init__.py
│   ├── receiver.py         ← UDP trap listener
│   ├── enricher.py         ← Trap enrichment & OID lookup
│   ├── vendor.py           ← Vendor fingerprinting
│   ├── catalog.py          ← OID catalog queries
│   └── seeds/              ← Built-in trap definitions
│       ├── __init__.py
│       ├── loader.py
│       ├── generic.py
│       ├── cisco.py
│       ├── fortinet.py
│       ├── juniper.py
│       └── apc.py
│
├── db/                     ← SQLite persistence package
│   ├── __init__.py         ← Re-exports all public symbols
│   ├── core.py             ← Write-queue, schema, migrations
│   ├── persistence.py      ← Device/sensor save & load
│   ├── samples.py          ← Probe sample buffer & queries
│   ├── events.py           ← Flap, trap, error logs
│   ├── users.py            ← User management (local + LDAP) & settings
│   ├── audit.py            ← Audit log
│   ├── backups.py          ← Backup settings & run history
│   ├── trap_defs.py        ← SNMP trap definition queries
│   └── ipam.py             ← Subnet & IP allocation management
│
├── routes/                 ← HTTP route handlers
│   ├── auth.py             ← Login, logout, users
│   ├── devices.py          ← Device & sensor CRUD
│   ├── monitoring.py       ← SSE, flaps, traps, SNMP
│   ├── settings.py         ← App settings, server info
│   ├── tls.py              ← TLS certificate management API
│   ├── topology.py         ← NTM pages/nodes/links/groups
│   ├── export.py           ← DB export/import, audit
│   ├── backups.py          ← Device config backup API
│   ├── ldap.py             ← LDAP/AD settings & test endpoints
│   └── ipam.py             ← IPAM subnet & IP allocation API
│
├── certs/                  ← Optional: drop cert.pem + key.pem here
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
    ├── forms-ldap.js
    ├── forms-io.js
    ├── forms-utils.js
    ├── ipam.js
    ├── bg.js
    ├── map.html
    ├── map.css
    └── map.js
```
