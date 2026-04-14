# PingWatch – Real-Time Network Monitoring Platform

![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/github/license/Nividan/Pingwatch)
![Stars](https://img.shields.io/github/stars/Nividan/Pingwatch?style=social)
[![Built with Claude](https://img.shields.io/badge/Built%20with-Claude%20AI-orange?logo=anthropic)](https://claude.ai)

PingWatch is a Python-based network monitoring platform for tracking the availability and health of network devices and services. It runs a lightweight built-in HTTPS server, stores data in SQLite, and streams live updates to a vanilla JS dashboard — no external web framework or build step required.

> 🤖 This project was designed and built with [Claude AI](https://claude.ai) (Anthropic) as an AI-driven development experiment — from architecture to implementation.

---

## Table of Contents

- [Features](#features)
- [Supported Sensor Types](#supported-sensor-types)
- [Technologies](#technologies)
- [Installation](#installation)
- [Usage](#usage)
- [HTTPS / TLS](#https--tls)
- [Syslog Forwarding](#syslog-forwarding)
- [LDAP / Active Directory Authentication](#ldap--active-directory-authentication)
- [IP Address Management (IPAM)](#ip-address-management-ipam)
- [Device Configuration Backup](#device-configuration-backup)
- [Screenshots](#screenshots)
- [Architecture](#architecture)

---

## Features

- 📡 Real-time device monitoring via Server-Sent Events (SSE)
- 🔎 Multiple sensor types: ICMP, HTTP/S, TCP, TLS, SNMP, DNS, Banner, VMware
- ⏱ Configurable monitoring intervals, debounce thresholds, and per-sensor defaults
- 📜 Historical event logging with flap and SNMP trap tracking
- 🚨 Hierarchical alert profiles — PRTG-style escalation stages with per-stage delays and repeat intervals; cascade resolution (sensor → device → group → global) so one global profile covers everything while individual scopes can override; reusable action templates (email, webhook, syslog, browser push); maintenance window suppression
- 🏷 Alert tagging on sensor events — severity badge, profile name, and state shown inline; ACK / Resolve without leaving the Events tab; Events tab split into **Active** (unresolved, badge count) and **History** (resolved) inner tabs — SNMP traps without an alert rule go to History automatically
- 👥 User groups — assign members, use groups as alert email recipient lists; emails resolved at dispatch time
- 👤 User profiles — full name and email per user; self-service "Edit Profile" in the user menu
- 🎨 Light / Dark theme toggle — switch from the user menu; preference persisted per user and synced across browsers/devices (`users.theme_preference` column + `localStorage` cache); instant switch with no page reload or flash-of-unthemed-content
- 🌐 Web-based dashboard with live latency sparklines, customizable widgets, and multi-dashboard tabs — create named dashboards (e.g. "NOC", "Server Room") per user; tab bar with right-click rename/delete; new users get a pre-populated default layout
- 🗺 Interactive Network Topology Manager (NTM) with draw.io-style editing
- 🔒 Role-based access control: viewer / operator / admin
- 🔐 Native HTTPS / TLS 1.2+ with self-signed or imported certificates
- 📤 Database export and import (individual DBs or full ZIP bundle)
- 🖥 Native desktop status window with optional system-tray icon
- 💾 Automated device configuration backup via SSH/Telnet — encrypted credentials, revision history, diff viewer, and vendor-aware rollback with full interface context (`interface X / no … / end / wr`)
- 🔗 Sensor host linking — sensors inherit the device IP by default; setting a host manually marks it as overridden; clearing the host re-links it to the device
- 🔍 Per-device port scanner with configurable default ports (Settings → Sensors)
- 🧙 Interactive first-run setup wizard — GUI (tkinter, dark-themed) on Windows, CLI fallback on headless/SSH; handles packages, DB backend, ports, TLS, admin user
- 🐧 Native Linux/macOS support — headless mode, systemd service, auto package-manager detection
- 📨 Syslog forwarding — RFC 5424 UDP/TCP to any syslog server
- 🔁 Server restart and shutdown from the web UI (Settings → General)
- 🏢 LDAP / Active Directory authentication with encrypted bind credentials, group import, and auto-provisioning
- 🗂 IP Address Management (IPAM) — subnet tracking with live ping-sweep integration; sortable columns (click headers) and filter dropdowns for Status (Used/Free) and Licenses
- 🔢 Auto-scaling probe executor — worker count scales automatically with sensor count (1 per 4 sensors, 64–512 range); manual override available in Settings → General
- 🏷 Device list status filter pills — All / Down / Warn / Up / Pause with live counts; composes with text search
- 📄 Device list pagination — 50 devices per page (user-selectable: 25/50/100); preference saved in `localStorage`
- 🖱 Sensor tile drag-to-reorder — drag sensor tiles inside a device window to rearrange; layout persists per device across sessions; device card top-3 preview respects custom order
- 🖥 VMware vSphere monitoring — discover VMs from vCenter/ESXi, 16 metrics across CPU, memory, disk, datastore, network, and system; grouped VM display with collapsible rows, per-metric smart thresholds, bulk add, and group-level mute toggle
- ✅ Bulk resolve — resolve all active alerts and flaps in one click from the Events tab
- 📊 Time-aware sensor KPI tiles — Avg / Min / Max latency tiles in the sensor history panel reflect the selected time window (12 h → 3 d → 7 d → 30 d → 90 d), matching the stats bar values
- 🔭 Subnet Discovery — scan a CIDR range for unmonitored hosts; two modes (Full: ping + DNS + port scan + device-type guess; Ping only: fast scan for large networks); multi-select results table with MAC/vendor, open ports, multi-NIC duplicate detection, per-device sensor review, and one-click bulk add; **per-device group assignment** — set a default group for the entire batch or override individual rows; maximum scan size /16 (65 534 hosts) with tiered runtime warnings and cancellation support
- 📋 Device License Tracking — attach software/hardware licenses to any device with expiry dates, configurable warn/critical thresholds (days before expiry), and free-text notes; automatic status check every 6 hours fires Warning/Critical events into the Events tab (deduplication via `last_status` — only fires on state change); recovery event auto-resolves the active alert when a license is renewed; license status badges (Valid / Expiring / Expired) in the Edit Device modal and IPAM table; License Overview dashboard widget shows KPI counts and a sorted table of upcoming expirations; real-time SSE updates on status change

### Supported Sensor Types

| Sensor | Description |
|--------|-------------|
| **Ping (ICMP)** | Round-trip latency and packet-loss monitoring |
| **HTTP / HTTPS** | Status code, keyword, and response-time checks |
| **TCP Port** | Port reachability and connection-time checks |
| **TLS** | Certificate validity and TLS handshake checks |
| **SNMP** | OID polling (v1/v2c); Counter32/Counter64 traffic OIDs display live rate (B/s – GB/s); interface discovery with metric auto-select; wrong-OID detection |
| **DNS** | Record lookup and resolution-time checks |
| **Banner** | Raw TCP banner capture with optional regex match |
| **VMware** | vSphere VM monitoring — CPU, memory, disk, datastore latency, network, uptime, power state; auto-discovery from vCenter/ESXi |

---

## Technologies

- **Backend:** Python 3.x stdlib — no third-party web framework
- **Web Server:** `http.server` (threading) + `ssl.SSLContext` for HTTPS
- **Database:** Dual-backend — SQLite WAL (default, zero-setup) or PostgreSQL (production/high-scale); dual-DB layout: `main` schema (config, devices, users, IPAM, alerts) + `logs` schema (sensor samples, flap log, SNMP traps)
- **Frontend:** Vanilla HTML, CSS, JavaScript — no build step
- **Real-time:** Server-Sent Events (SSE)
- **TLS:** `cryptography` (RSA-2048, X.509, Fernet encryption)
- **SSH backup:** `paramiko`
- **PostgreSQL:** `psycopg2` *(optional — only needed when PostgreSQL backend is enabled)*
- **System tray:** `pystray` + `Pillow` *(optional)*
- **VMware:** `pyvmomi` *(optional — only needed when VMware sensors are enabled)*
- **LDAP/AD:** `ldap3` *(optional — only needed when LDAP auth is enabled)*

---

## Installation

```bash
git clone https://github.com/Nividan/Pingwatch.git
cd Pingwatch
```

**Windows:**
```bat
windows\start.bat
```

**Linux / macOS:**
```bash
sudo bash linux/start.sh
```

On Windows, `start.bat` launches via a Python-based launcher (`windows/launcher.pyw`) that handles admin elevation, first-run detection, and port cleanup — no console window. The first-run wizard (GUI on Windows, CLI on Linux) checks packages, configures ports, generates a TLS certificate, and initialises the database. Subsequent launches skip the wizard. To re-run it:

```bash
windows\start.bat --setup        # Windows
sudo bash linux/start.sh --setup      # Linux / macOS
sudo bash linux/start.sh --check      # Re-check required packages only
```

**Background service (Linux):**
```bash
sudo bash linux/start.sh --install-service     # install + start systemd service
sudo systemctl start|stop|restart|status pingwatch
journalctl -u pingwatch -f                     # live logs
sudo bash linux/start.sh --uninstall-service
```

### Air-Gapped Installation

PingWatch has **zero external runtime dependencies** — no CDN fonts, no telemetry, no update checks. It runs fully offline once installed. The only step that needs internet is fetching Python packages, which you do once on a connected machine and transfer to the air-gapped target.

**On an internet-connected machine:**

1. Install the matching Python version (3.8+) and clone/download the repo.
2. Pre-download all Python dependencies as wheels:
   ```bash
   pip download -r requirements.txt -d ./wheels
   ```
3. Copy the entire `Pingwatch/` folder (including `./wheels/`) to the air-gapped host (USB, approved file share, etc.).

**On the air-gapped host:**

1. Install Python 3.8+ from an offline installer (Windows `.exe` / Linux `.deb` / `.rpm`).
2. Install PostgreSQL (optional — skip for SQLite) and `net-snmp` (optional — skip if no SNMP sensors) from your organization's internal package mirror or offline installer.
3. Install the pre-downloaded wheels:
   ```bash
   pip install --no-index --find-links ./wheels -r requirements.txt
   ```
4. Launch PingWatch normally (`windows\start.bat` or `sudo bash linux/start.sh`). The first-run wizard will detect that all packages are present and skip the download step.

**Configuration notes for air-gapped environments:**

| Feature | How to configure |
|---------|-----------------|
| **TLS certificate** | Use the built-in self-signed generator (Settings → Networking → HTTPS / TLS) or import your internal PKI certificate. Do **not** use ACME / Let's Encrypt — it requires internet. |
| **Email alerts** | Point SMTP to your internal mail relay, or skip email and use webhook / syslog / browser push instead. |
| **Webhook alerts** | Target internal URLs only. The built-in SSRF guard rejects public/external IPs. |
| **DNS probes** | Point at your internal DNS servers. |
| **Device backups** | SSH/Telnet to internal devices — already LAN-only. |

All monitoring features (ICMP, HTTP, TCP, TLS, SNMP, DNS, Banner, VMware), the dashboard, topology map, IPAM, alerting, and backup engine work identically online and offline.

---

## Usage

| Mode | Windows | Linux / macOS |
|------|---------|---------------|
| Foreground | `windows\start.bat` | `sudo bash linux/start.sh` |
| Background | `pythonw windows\pingwatch.pyw` | `sudo bash linux/start.sh --install-service` |
| Re-run wizard | `windows\start.bat --setup` | `bash linux/start.sh --setup` |

After startup, PingWatch is available at **https://localhost:8443** (default). The first-run password is printed to the console — change it immediately in **Settings → Users**.

**Linux notes:**
- Ports < 1024 require root or `CAP_NET_BIND_SERVICE` (the systemd service handles this automatically).
- Headless mode skips tkinter/pystray/Pillow entirely — select "no desktop GUI" in the wizard.
- SNMP port 162 requires root. Alternatively: `iptables -t nat -A PREROUTING -p udp --dport 162 -j REDIRECT --to-port 1162`.

---

## HTTPS / TLS

TLS 1.2+ is enabled by default. Certificate discovery order: database → `certs/cert.pem` + `key.pem` → auto-generated self-signed.

Manage in **Settings → Networking → HTTPS / TLS**: generate self-signed (with custom SANs), upload an existing PEM pair, enable/disable TLS, or enable HTTP→HTTPS redirect. PingWatch logs a warning 30 days before expiry.

Default ports: HTTP `7070`, HTTPS `8443`, SNMP trap `1162` (all configurable).

---

## Syslog Forwarding

Forward events to any RFC 5424 syslog server via UDP or TCP. Configure in **Settings → Syslog**: host, port, protocol, and minimum severity (`critical` / `warning` / `down` / `recovered` / `info`). Non-blocking daemon queue — monitor threads are never stalled. Changes take effect immediately without restart.

---

## LDAP / Active Directory Authentication

Domain users log in with AD credentials; local users are unaffected. Configure in **Settings → Users → LDAP Settings**: server, port, security mode (None/LDAPS/StartTLS), base DN, bind DN, bind password (Fernet-encrypted at rest), and user search filter. Accepted login formats: `jsmith`, `CORP\jsmith`, `jsmith@corp.local`.

Use **Test Connection** to verify the service-account bind and **Test User Auth** to run the full authentication flow before saving.

### LDAP Group Integration

Import AD/LDAP groups into PingWatch and tie them to PingWatch roles and notification groups:

- **Import groups** — use **Settings → Groups → Import from LDAP** to browse and import AD groups. Each imported group gets an LDAP badge and a configurable default role (viewer / operator / admin).
- **Auto-provision** — enable "Auto-provision" in LDAP Settings and any LDAP user who belongs to an imported group is created automatically on first login with the matching role, display name, and email. No manual user creation required.
- **Login-time sync** — on every LDAP login, PingWatch refreshes the user's group assignment, role, and display name from LDAP. If the user is removed from all imported groups in AD, login is rejected and the account is suspended (local admin accounts are always unaffected).
- **Background sync** — a configurable background thread (default every 60 minutes) reconciles all LDAP users against current AD group membership without waiting for a login.
- **Nested groups** — optional AD recursive membership check using `LDAP_MATCHING_RULE_IN_CHAIN` (AD only).
- **Multi-group priority** — users in multiple imported groups receive the highest role (admin > operator > viewer).
- **Test User Groups** — admin diagnostic dialog: enter a username and see exactly which LDAP groups they belong to.

---

## IP Address Management (IPAM)

Track IP allocations across subnets. Navigate to the **IPAM** tab, add a subnet in CIDR notation (up to `/9`), and PingWatch expands every host IP. Click any row to assign a name/label. Monitored devices are automatically linked to their IPAM entries when created.

---

## Device Configuration Backup

Connects to network devices over SSH or Telnet, retrieves the running configuration, and stores it encrypted in the database with a full revision history.

### Supported Devices

| Vendor / OS | Method | Notes |
|-------------|--------|-------|
| **Cisco IOS / IOS-XE** | SSH or Telnet | Paging: `terminal length 0` · Enable password supported |
| **Cisco NX-OS** | SSH | Paging: `terminal length 0` |
| **Juniper JUNOS** | SSH | Paging: `set cli screen-length 0` · keyboard-interactive auth supported |
| **Fortinet FortiGate** | SSH | Paging: `config system console` + `set output standard` · context-aware rollback |
| **Any SSH/Telnet device** | SSH or Telnet | Supply the correct commands for your platform |

### Backup Settings

| Field | Description |
|-------|-------------|
| **Method** | `ssh` or `telnet` |
| **Username / Password** | Password stored AES-Fernet encrypted — never in plaintext |
| **Enable password** | Optional second-stage enable (Cisco-style) |
| **Paging command** | Sent once after login to disable paging |
| **Commands** | One per line — collected in sequence |
| **Schedule** | Cron expression for automatic runs (e.g. `0 2 * * *`) |

### Config Viewer

Click any device row in the Backups tab to open the Config Viewer:

- Browse revisions with **← Older / Newer →** navigation
- View timestamp, status, config size, and SHA-256 hash
- **Diff view** — side-by-side comparison between any two revisions using a patience diff algorithm (handles large configs efficiently)
- **Hide credential noise** — suppresses `set password ENC` / `set psksecret ENC` lines that FortiGate re-encrypts on every export, so only real changes appear in the diff
- **Vendor-aware rollback** — generates restore commands in the correct syntax for the detected vendor (FortiGate: `config/edit/set/next/end` blocks; others: `no <command>`)

### Security

- Passwords Fernet-encrypted at rest; never written to disk or logs beyond the duration of a backup run
- SSH host keys verified via TOFU (stored in `ssh_known_hosts.txt`; rejected if changed)
- All backup actions recorded in the audit log

---

## Screenshots

### 📡 Network Dashboard
<img width="800" alt="Network Dashboard" src="https://github.com/user-attachments/assets/91e2237f-a3c8-447c-adbc-5d91e950f63a" />
<img width="800" alt="Network Dashboard 2" src="https://github.com/user-attachments/assets/276fc670-1425-4150-ae9d-21ee33da8565" />

### 🖥 Device Information
<img width="800" alt="Device List" src="https://github.com/user-attachments/assets/06a38bfa-3dd1-431e-8dd1-60873d9624e8" />
<img width="800" alt="Device Detail" src="https://github.com/user-attachments/assets/3a027022-4a46-4fc2-b2e3-9f017b06a2e8" />
<img width="480" alt="Device Panel" src="https://github.com/user-attachments/assets/131ceef8-bb9c-4abb-9346-f993f409365f" />
<img width="480" height="1140" alt="image" src="https://github.com/user-attachments/assets/e8d2eab4-257d-43a7-802b-adda20a7764b" />
<img width="480" height="278" alt="image" src="https://github.com/user-attachments/assets/235cd301-7d46-4926-839f-9c8a4f3702f0" />
<img width="800" height="963" alt="image" src="https://github.com/user-attachments/assets/ab494a71-8d03-4c7c-8a44-5fcc2b3948c1" />


### 📜 Event Logs
<img width="800" alt="Event Log" src="https://github.com/user-attachments/assets/210e31ec-6367-4e60-bcbd-5257f36f5a5d" />
<img width="800" alt="Event Log 2" src="https://github.com/user-attachments/assets/3a26e38d-6f12-46db-9d46-11f27561d001" />

### 🗺 Network Topology Manager
<img width="800" alt="Live Topology Map" src="https://github.com/user-attachments/assets/2eff647b-befd-4c4c-b0e6-ee43adb1c713" />
<img width="800" alt="Topology Editor" src="https://github.com/user-attachments/assets/f42cb4f3-4167-4c91-b6d2-df635ad7c4ef" />

### 💾 Device Configuration Backup
<img width="800" alt="Backup Table" src="https://github.com/user-attachments/assets/0f94bfcd-d5e7-40aa-b950-c711c72f325b" />
<img width="480" alt="Backup Settings" src="https://github.com/user-attachments/assets/886afeb8-e44d-487d-92b9-7ab3dbddab04" />
<img width="480" alt="Config Viewer" src="https://github.com/user-attachments/assets/5026650f-be34-44aa-a017-c813cf75880d" />
<img width="500" height="800" alt="Config Diff" src="https://github.com/user-attachments/assets/920450a2-def2-4de0-a822-37c87167a25a" />

### 🗂 IP Address Manager
<img width="500" height="492" alt="IPAM" src="https://github.com/user-attachments/assets/42864325-72b5-4e7b-80ea-16637beb0d5f" />
<img width="800" height="364" alt="IPAM Subnets" src="https://github.com/user-attachments/assets/2397d53a-8891-4a96-a5f3-865dcb859f6c" />

### Setup GUI Wizard
<img width="816" height="806" alt="image" src="https://github.com/user-attachments/assets/ef9d9527-9944-467e-8757-4382761aff80" />


---

## Architecture

> For a full developer reference — module descriptions, API endpoints, DB schema, and how to extend PingWatch — see [DEVELOPER.md](DEVELOPER.md).

```
Browser / Desktop GUI
        │
        ▼
  server.py  ──  routes/          ← HTTP dispatcher + route modules
        │
        ├── core/                 ← Config, state, auth, TLS, logging
        ├── monitoring/           ← Probes, alerting, syslog, topology
        ├── vmware/               ← vSphere VM discovery + metric probing
        ├── backup/               ← SSH/Telnet backup engine + scheduler
        ├── snmp/                 ← Trap receiver, enricher, OID catalog
        └── db/                   ← Dual-backend persistence (SQLite / PostgreSQL)
```

- **`server.py`** — HTTP(S) dispatcher, starts all background threads
- **`gui_setup.py`** — tkinter GUI setup wizard (dark-themed, 6-step flow)
- **`setup_wizard.py`** — cross-platform CLI setup wizard (fallback for headless/SSH)
- **`core/setup_logic.py`** — shared setup logic (packages, ports, DB init) used by both wizards
- **`monitoring/probes.py`** — all sensor probe types on per-sensor threads (VMware probes via `vmware/client.py`)
- **`backup/engine.py`** — SSH/Telnet connections, TOFU host key verification, enable-mode escalation
- **`core/auth.py`** — PBKDF2-SHA256 local auth + LDAP branch via `core/ldap_auth.py`
- **`snmp/`** — UDP trap listener, OID enrichment, vendor fingerprinting
- **`db/`** — dual-backend persistence: Main DB (config/settings) + Logs DB (samples/events)
