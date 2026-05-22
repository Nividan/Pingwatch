# PingWatch – Real-Time Network Monitoring Platform

![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/github/license/Nividan/Pingwatch)
![Stars](https://img.shields.io/github/stars/Nividan/Pingwatch?style=social)
[![Built with Claude](https://img.shields.io/badge/Built%20with-Claude%20AI-orange?logo=anthropic)](https://claude.ai)

PingWatch is a Python-based network monitoring platform for tracking the availability and health of network devices and services. It runs a lightweight built-in HTTPS server, stores data in SQLite, and streams live updates to a vanilla JS dashboard — no external web framework or build step required.

> Built with [Claude AI](https://claude.ai).

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
- [RADIUS Authentication](#radius-authentication)
- [SAML 2.0 / OIDC Single Sign-On](#saml-20--oidc-single-sign-on)
- [IP Address Management (IPAM)](#ip-address-management-ipam)
- [Device Configuration Backup](#device-configuration-backup)
- [Screenshots](#screenshots)
- [Architecture](#architecture)

---

## Features

- 📡 Real-time device monitoring via Server-Sent Events (SSE)
- 🔎 Multiple sensor types: ICMP, HTTP/S, TCP, TLS, SNMP, DNS, Banner, VMware, SMTP, SSH, SFTP, RADIUS
- 🔌 Searchable, categorized sensor type browser — keyword search and sensor category sections in the Add Sensor sidebar
- ⏱ Configurable monitoring intervals, debounce thresholds, and per-sensor defaults
- 📜 Historical event logging with flap and SNMP trap tracking
- 🚨 Hierarchical alert profiles — PRTG-style escalation stages with per-stage delays, repeat intervals, and reusable action templates (email, webhook, syslog, browser push); cascade resolution (sensor → device → group → global) so one profile covers everything; maintenance window suppression
- 📬 **Notification batching** — combines bursts of alerts into one email/webhook instead of spamming. Configurable window and batch size; opt-in per webhook template
- 📚 **Event collapse** — toggle to fold ≥3 related events within 30 s into one expandable row; works in both card and table modes
- 🧠 Learned-baseline anomaly detection — opt-in per sensor; detects latency deviations beyond static thresholds; master switch and bulk-enable in Settings
- 🏷 Alert tagging on sensor events — severity badge, profile name, and state shown inline; ACK / Resolve without leaving the Events tab; Events tab split into **Active** (unresolved, badge count) and **History** (resolved) inner tabs — SNMP traps without an alert rule go to History automatically
- 👥 User groups — assign members, use groups as alert email recipient lists; emails resolved at dispatch time
- 👤 User profiles — full name and email per user; self-service "Edit Profile" in the user menu
- 🎨 Light / Dark theme toggle — switch from the user menu; preference persisted per user and synced across browsers/devices (`users.theme_preference` column + `localStorage` cache); instant switch with no page reload or flash-of-unthemed-content
- 🌐 Web-based dashboard with live latency sparklines and customizable widgets; multi-dashboard tabs with rename/delete; new users get a starter layout
- 🗺 Interactive Network Topology Manager (NTM) with draw.io-style editing — manual page editor renamed to **Topology Design**
- 🛰 **Live Map** — dedicated NOC console at `/livemap.html`: hero stats (sites / devices / active alerts / 24h uptime), site-health mosaic sized by device count, OFF-Site reachability widget, sites-by-type bars, top problem sites, live alerts feed; click any site to drill in to a tier tree (Firewall → Switches → Hypervisor clusters → VM clusters + IPMI), with cluster cards showing a mini status dot-grid
- 🏷 **Sites metadata** — sidecar `sites` table with kind (DC / LAB / PoP / EDGE / OFFICE / HQ / INTERNET), pinned flag, and optional display name; Add Site / Edit Site managed from the Devices tab (right-click any site header → Edit Site); colors propagate to the Live Map mosaic, sidebar pills, and Sites by Type widget
- 📐 **Expandable rail sidebar** — toggle in the sidebar shows tab name labels next to icons; pushes content (does not overlay); preference persisted in `localStorage`
- ➕ **Redesigned Add Widget modal** — searchable widget picker with category chips (Charts / Status / Events / Reports / Network), Recently Used + Popular sections, 3-column tile grid, side-popout live preview, keyboard navigation (↑↓/Enter/Esc/Ctrl-K), one-click add with confirmation toast
- 🔒 Role-based access control: viewer / operator / admin
- 🔑 Two-factor authentication (TOTP) — optional per user, enforceable per role; QR enrolment, recovery codes, and trusted-device management
- 🔐 Native HTTPS / TLS 1.2+ with self-signed or imported certificates
- 📤 Database export and import (individual DBs or full ZIP bundle)
- 🖥 Native desktop status window with optional system-tray icon
- 💾 Automated device configuration backup via SSH/Telnet — encrypted credentials, revision history, diff viewer, and vendor-aware rollback
- 🔗 Sensor host linking — sensors inherit the device IP by default; setting a host manually marks it as overridden; clearing the host re-links it to the device
- 🔍 Per-device port scanner with configurable default ports (Settings → Sensors)
- 🧙 Interactive first-run setup wizard — GUI (tkinter, dark-themed) on Windows, CLI fallback on headless/SSH; handles packages, DB backend, ports, TLS, admin user
- 🐧 Native Linux/macOS support — headless mode, systemd service, auto package-manager detection
- 📨 Syslog forwarding — RFC 5424 UDP/TCP to any syslog server
- 🔁 Server restart and shutdown from the web UI (Settings → General)
- 🏢 LDAP / Active Directory authentication with encrypted bind credentials, group import, and auto-provisioning
- 🧾 RADIUS authentication (PAP + Access-Challenge 2FA) — primary/secondary server failover, attribute→role group mapping per-login, auto-provisioning; FortiAuthenticator, NPS, FreeRADIUS, and Cisco ISE compatible
- 🪪 **SAML 2.0** + 🪙 **OpenID Connect** federated SSO — IdP metadata import (URL / paste / file upload), SP metadata export, SP signing certificate management, signed AuthnRequests, JWKS auto-discovery + scheduled refresh; JIT provisioning with attribute→role group mapping; coexists with local + LDAP + RADIUS; tested with FortiAuthenticator, protocol-compliant for Okta / Entra ID / Keycloak / ADFS / OneLogin / PingFederate
- 🩺 **Auth backend health checks** — boot-time config + crypto sanity pass populates LDAP / RADIUS / SAML / OIDC status badges within seconds of restart; configurable scheduled refresh (default hourly) does live LDAP bind + OIDC discovery refetch + cert expiry monitoring; "Run now" button + last-run indicator in Settings → Integrations
- ☁️ Remote DB backup upload — automatically upload scheduled SQLite/PostgreSQL snapshots to an SFTP or SMB share after each local backup run; Fernet-encrypted credentials at rest
- 🗂 IP Address Management (IPAM) — subnet tracking with live ping-sweep integration; sortable columns (click headers) and filter dropdowns for Status (Used/Free) and Licenses
- 🔢 Auto-scaling probe executor — worker count scales automatically with sensor count (64–512 range); manual override available in Settings → General. PostgreSQL connection pool also auto-scales to match probe load
- 🏷 Device list status filter pills — All / Down / Warn / Up / Pause with live counts; composes with text search
- 📄 Device list pagination — 50 devices per page (user-selectable: 25/50/100); preference saved in `localStorage`
- 🖱 Sensor tile drag-to-reorder — drag sensor tiles inside a device window to rearrange; layout persists per device across sessions; device card top-3 preview respects custom order
- 🖥 VMware vSphere monitoring — discover VMs from vCenter/ESXi; 16 metrics across CPU, memory, disk, datastore, network, system; grouped display with smart thresholds and bulk add
- ✅ Bulk resolve — resolve all active alerts and flaps in one click from the Events tab
- 📊 Time-aware sensor KPI tiles — Avg / Min / Max latency tiles in the sensor history panel reflect the selected time window (12 h → 3 d → 7 d → 30 d → 90 d), matching the stats bar values
- 🔭 Subnet Discovery — scan a CIDR range (up to /16) for unmonitored hosts; Full (ping + DNS + port scan + device-type guess) and Ping-only modes; multi-NIC duplicate detection, per-device sensor review, per-row group assignment, one-click bulk add
- 🤖 **Scheduled Auto-Discovery** — configure IPAM subnets to scan automatically on a set interval (15 min – 24 h); new hosts are auto-added with ping + guessed service sensors; safety rails include global enable/pause, first-scan cap, suppressed-hosts list, and maintenance-window awareness
- ☑ **Bulk device multi-select** — toggle Select in the Devices toolbar to reveal checkboxes; bulk-move/pause/resume/delete with one request; respects search and status filters
- 📋 Device License Tracking — attach software/hardware licenses to devices with expiry dates and configurable warn/critical thresholds; 6-hourly status check fires Warning/Critical events with auto-resolve on renewal; status badges in the Edit Device modal and IPAM table; License Overview dashboard widget
- 📊 Scheduled PDF/CSV Reports — Executive / Technical / Inventory / Custom kinds; compare-to-previous deltas; incident aggregation and Device Health Scores; scheduled email delivery with bulk delete
- 🔧 **Diagnostics tab (Settings)** — System Overview, Database Health, consolidated Health Checks (LDAP/RADIUS/SAML/OIDC/SMTP/Syslog/DB Backup/NTP/DNS), Probe-from-Server tool, Recent Errors, Maintenance actions, and support bundle download
- 🪵 Professional log viewer — dedicated top-level **Logs** tab with live tail, minimum-level filter, time range, text search; word-wrap, copy, CSV/JSON export; keyboard shortcuts; real-time unread count

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
| **SMTP** | Layered mail-server probe — connect, EHLO, STARTTLS, AUTH, MAIL FROM round-trip (no mail sent) |
| **SSH** | Layered SSH probe — TCP connect, banner capture, password or private-key authentication |
| **SFTP** | SFTP subsystem probe — subsystem open, directory list, file stat, SHA-256 file integrity check (read-only) |
| **RADIUS** | AAA reachability + authentication probe — `reachable` (any response proves host/port/secret) or `auth` (full PAP login); flags `Access-Challenge` (2FA-gated) cleanly |

---

## Technologies

- **Backend:** Python 3.x stdlib — no third-party web framework
- **Web Server:** `http.server` (threading) + `ssl.SSLContext` for HTTPS
- **Database:** Dual-backend — SQLite WAL (default, zero-setup) or PostgreSQL (production/high-scale); dual-DB layout: `main` schema (config, devices, users, IPAM, alerts) + `logs` schema (sensor samples, flap log, SNMP traps)
- **Frontend:** Vanilla HTML, CSS, JavaScript — no build step
- **Real-time:** Server-Sent Events (SSE)
- **TLS:** `cryptography` (RSA-2048, X.509, Fernet encryption)
- **SSH backup + SSH/SFTP probes:** `paramiko`
- **PostgreSQL:** `psycopg2` *(optional — only needed when PostgreSQL backend is enabled)*
- **System tray:** `pystray` + `Pillow` *(optional)*
- **VMware:** `pyvmomi` *(optional — only needed when VMware sensors are enabled)*
- **LDAP/AD:** `ldap3` *(optional — only needed when LDAP auth is enabled)*
- **RADIUS:** `pyrad` *(optional — only needed when RADIUS auth or RADIUS sensor is enabled)*
- **SAML 2.0 SSO:** `pysaml2` + `signxml` *(optional — only needed when SAML is enabled; pure Python, no `xmlsec1` system dep)*
- **OIDC SSO:** `authlib` *(optional — only needed when OIDC is enabled)*
- **Remote backup:** `smbprotocol` *(optional — only needed for SMB remote DB backup uploads)*
- **PDF reports:** `weasyprint` + `Jinja2` + `matplotlib` *(optional — only needed for the Reports module)*

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

Forward events to any RFC 5424 syslog server over UDP or TCP. Configure host, port, and protocol in **Settings → Syslog**; application log forwarding has its own minimum-level filter. Non-blocking daemon queue — monitor threads are never stalled; changes apply without restart.

---

## LDAP / Active Directory Authentication

Domain users log in with AD credentials; local users are unaffected. Configure in **Settings → Users → LDAP Settings**: server, port, security mode, base DN, and search filter. Use **Test Connection** and **Test User Auth** to verify before saving.

**LDAP Group Integration:** Import AD groups via **Settings → Groups → Import from LDAP** and assign default roles. Enable auto-provision in LDAP Settings to create users automatically on first login. Background sync (default every 60 minutes) keeps group membership current. Optional nested group support (AD only). See [DEVELOPER.md](DEVELOPER.md) for implementation details.

---

## RADIUS Authentication

Domain users log in with RADIUS credentials; local and LDAP users are unaffected. Configure in **Settings → Integrations → RADIUS**: primary and optional secondary servers, timeouts, and retries. Support for `Access-Challenge` 2FA and attribute-based group mapping. Use **Test Connection** and **Test User Auth** to verify before saving. See [DEVELOPER.md](DEVELOPER.md) for attribute mapping details.

---

## SAML 2.0 / OIDC Single Sign-On

Federated enterprise SSO alongside local + LDAP + RADIUS. Tested with **FortiAuthenticator**; protocol-compliant for **Okta, Microsoft Entra ID, Keycloak, ADFS, OneLogin, PingFederate, Google Workspace, Shibboleth**. Configure in **Settings → Integrations → 🪪 SAML 2.0** or **🪙 OIDC**.

**SAML 2.0:** Import IdP metadata by URL, XML paste, or file upload. Generate SP signing certificate from the UI. Enable signed AuthnRequests as needed.

**OpenID Connect:** Paste issuer URL and auto-discover endpoints. Support for PKCE and scheduled JWKS refresh (default hourly) for key rotation.

**Common features:** Just-In-Time user provisioning on first SSO login. Group → role mapping with optional unmapped-user rejection. All methods coexist with local/LDAP/RADIUS. Health monitoring with status badges. See [DEVELOPER.md](DEVELOPER.md) for configuration details.

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
<img width="2539" height="892" alt="image" src="https://github.com/user-attachments/assets/05f0ec79-c657-4615-b0f1-90b6154ff2bd" />
<img width="2552" height="887" alt="image" src="https://github.com/user-attachments/assets/8e53dfa7-346d-4a63-ae24-300d74ef5947" />
<img width="1055" height="595" alt="image" src="https://github.com/user-attachments/assets/c8d69b3a-e8bd-4ab9-abb7-76cbadf40b69" />
<img width="910" height="716" alt="image" src="https://github.com/user-attachments/assets/4c9bfc88-dacc-4340-a0f8-81af7898ba9b" />
<img width="918" height="1052" alt="image" src="https://github.com/user-attachments/assets/a0b43954-fab7-4c44-b9ae-04bd3c17742b" />
<img width="905" height="255" alt="image" src="https://github.com/user-attachments/assets/4be6527e-81e8-4bc3-856d-a4cab7fc54d2" />
<img width="1047" height="1083" alt="image" src="https://github.com/user-attachments/assets/39506190-4bba-43b7-b9ec-23457a8bd73b" />

### 🖥 Device Information
<img width="2535" height="458" alt="image" src="https://github.com/user-attachments/assets/292cc408-7fea-4e4b-bc68-b17435ed15cc" />
<img width="2541" height="444" alt="image" src="https://github.com/user-attachments/assets/5ab32fb2-95dd-4d44-b0d1-66974c805cd2" />

### 📜 Event Logs
<img width="2541" height="444" alt="image" src="https://github.com/user-attachments/assets/28c27cf9-4f6a-48cd-9f56-0bc1132094a8" />

### 🗺 Network Topology Manager
<img width="2551" height="1277" alt="image" src="https://github.com/user-attachments/assets/952d9a56-d08f-4837-ada4-b11cecfe532b" />
<img width="2536" height="1265" alt="image" src="https://github.com/user-attachments/assets/7d62daf0-d0f2-4d84-8876-da668924c7be" />

### 💾 Device Configuration Backup
<img width="655" height="801" alt="image" src="https://github.com/user-attachments/assets/485aef55-4da2-4357-ac5b-a35501321b6f" />

### 🗂 IP Address Manager
<img width="2547" height="284" alt="image" src="https://github.com/user-attachments/assets/14d026a2-3b14-4685-bbfc-957fe2733272" />

### 🗂 Reports
<img width="2552" height="468" alt="image" src="https://github.com/user-attachments/assets/355df49f-fe19-4223-b10b-ef90e9c11d1a" />

### 🗂 Settings
<img width="1160" height="1124" alt="image" src="https://github.com/user-attachments/assets/ed37694e-765d-48a6-90bc-6d120eebe181" />
<img width="1198" height="764" alt="image" src="https://github.com/user-attachments/assets/6ed184cb-1a68-4bdc-baa8-a15ebc597031" />

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
        ├── reports/              ← PDF/CSV report engine, scheduler, email delivery
        └── db/                   ← Dual-backend persistence (SQLite / PostgreSQL)
```

See [DEVELOPER.md](DEVELOPER.md) for module responsibilities, API endpoints, database schema, and extension patterns.
