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
- 🚨 Hierarchical alert profiles — PRTG-style escalation stages with per-stage delays and repeat intervals; cascade resolution (sensor → device → group → global) so one global profile covers everything while individual scopes can override; reusable action templates (email, webhook, syslog, browser push); maintenance window suppression
- 🧠 Learned-baseline anomaly detection (opt-in per sensor) — EWMA-based upper-tail detection for latency deviations beyond static thresholds; fires `warn` only, never overrides crit; admin controls in Settings → Sensors (master switch, auto-enable, bulk enable)
- 🏷 Alert tagging on sensor events — severity badge, profile name, and state shown inline; ACK / Resolve without leaving the Events tab; Events tab split into **Active** (unresolved, badge count) and **History** (resolved) inner tabs — SNMP traps without an alert rule go to History automatically
- 👥 User groups — assign members, use groups as alert email recipient lists; emails resolved at dispatch time
- 👤 User profiles — full name and email per user; self-service "Edit Profile" in the user menu
- 🎨 Light / Dark theme toggle — switch from the user menu; preference persisted per user and synced across browsers/devices (`users.theme_preference` column + `localStorage` cache); instant switch with no page reload or flash-of-unthemed-content
- 🌐 Web-based dashboard with live latency sparklines, customizable widgets, and multi-dashboard tabs — create named dashboards (e.g. "NOC", "Server Room") per user; tab bar with right-click rename/delete; new users get a pre-populated default layout
- 🗺 Interactive Network Topology Manager (NTM) with draw.io-style editing
- 🔒 Role-based access control: viewer / operator / admin
- 🔑 Two-factor authentication (TOTP) — optional per user, enforceable per role; QR enrolment, recovery codes, admin reset; revocable "Remember this device" trusted-device tokens
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
- 🧾 RADIUS authentication (PAP + Access-Challenge 2FA) — primary/secondary server failover, attribute→role group mapping per-login, auto-provisioning; FortiAuthenticator, NPS, FreeRADIUS, and Cisco ISE compatible
- 🪪 **SAML 2.0** + 🪙 **OpenID Connect** federated SSO — IdP metadata import (URL / paste / file upload), SP metadata export, SP signing certificate management, signed AuthnRequests, JWKS auto-discovery + scheduled refresh; JIT provisioning with attribute→role group mapping; coexists with local + LDAP + RADIUS; tested with FortiAuthenticator, protocol-compliant for Okta / Entra ID / Keycloak / ADFS / OneLogin / PingFederate
- 🩺 **Auth backend health checks** — boot-time config + crypto sanity pass populates LDAP / RADIUS / SAML / OIDC status badges within seconds of restart; configurable scheduled refresh (default hourly) does live LDAP bind + OIDC discovery refetch + cert expiry monitoring; "Run now" button + last-run indicator in Settings → Integrations
- ☁️ Remote DB backup upload — automatically upload scheduled SQLite/PostgreSQL snapshots to an SFTP or SMB share after each local backup run; Fernet-encrypted credentials at rest
- 🗂 IP Address Management (IPAM) — subnet tracking with live ping-sweep integration; sortable columns (click headers) and filter dropdowns for Status (Used/Free) and Licenses
- 🔢 Auto-scaling probe executor — worker count scales automatically with sensor count (1 per 4 sensors, 64–512 range); manual override available in Settings → General
- 🏷 Device list status filter pills — All / Down / Warn / Up / Pause with live counts; composes with text search
- 📄 Device list pagination — 50 devices per page (user-selectable: 25/50/100); preference saved in `localStorage`
- 🖱 Sensor tile drag-to-reorder — drag sensor tiles inside a device window to rearrange; layout persists per device across sessions; device card top-3 preview respects custom order
- 🖥 VMware vSphere monitoring — discover VMs from vCenter/ESXi; 16 metrics across CPU, memory, disk, datastore, network, system; grouped display with smart thresholds and bulk add
- ✅ Bulk resolve — resolve all active alerts and flaps in one click from the Events tab
- 📊 Time-aware sensor KPI tiles — Avg / Min / Max latency tiles in the sensor history panel reflect the selected time window (12 h → 3 d → 7 d → 30 d → 90 d), matching the stats bar values
- 🔭 Subnet Discovery — scan a CIDR range (up to /16) for unmonitored hosts; Full (ping + DNS + port scan + device-type guess) and Ping-only modes; multi-NIC duplicate detection, per-device sensor review, per-row group assignment, one-click bulk add
- 🤖 **Scheduled Auto-Discovery** — configure IPAM subnets to scan automatically on a set interval (15 min – 24 h); new hosts are auto-added with a ping sensor plus guessed service sensors (HTTP, HTTPS, SNMP); safety rails include global enable/pause toggle, first-scan device cap with admin approval step, suppressed-hosts list (manually deleted devices are never re-added), and maintenance-window awareness; audit log entry per scan tick; optional alert event per auto-added device
- ☑ **Bulk device multi-select** — toggle ☑ Select in the Devices toolbar to reveal checkboxes on every card and group header; tick individual devices, shift-click ranges, or tick a whole group's header to bulk-move to a new/existing group, bulk-pause, bulk-resume, or bulk-delete; `Ctrl+A` selects all visible (respects the search + status-pill filter), `Esc` exits; one `POST /api/devices/bulk` request per bulk action with one audit entry
- 📋 Device License Tracking — attach software/hardware licenses to devices with expiry dates and configurable warn/critical thresholds; 6-hourly status check fires Warning/Critical events with auto-resolve on renewal; status badges in the Edit Device modal and IPAM table; License Overview dashboard widget
- 📊 Scheduled PDF/CSV Reports — Executive / Technical / Inventory / Custom kinds rendered via WeasyPrint + Matplotlib; period picker with compare-to-previous deltas; CSV sidecar; aggregated incident log, Major Incidents clustering, Device Health Scores; deterministic Report ID + SHA-256 fingerprint; PDF/A-1b/2b/3b compliance mode; scheduled email delivery; History tab with bulk delete. See [DEVELOPER.md](DEVELOPER.md#reports) for architecture
- 🪵 Professional log viewer — dedicated top-level **Logs** tab (admin-only) for application / sensors / audit / backup streams; live tail with smart scroll-follow ("Jump to live" pill auto-appears when you scroll up); minimum-level filter (DEBUG+ → CRITICAL only), time range + custom datetime range, text search with highlighting; word-wrap toggle, copy / CSV / JSON export; keyboard shortcuts (`/` focus search, `l` live, `r` refresh, `w` wrap, `End` jump-to-live); status bar shows on-disk file size, rotation count, and "+N new since open"; preferences persisted per browser

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

## RADIUS Authentication

Domain users log in with RADIUS credentials; local and LDAP users are unaffected. Configure in **Settings → Integrations → RADIUS**: primary server (host/port/shared secret), optional secondary server for automatic failover, timeout, retries, NAS-Identifier, and realm prefix/suffix. Shared secrets are Fernet-encrypted at rest.

Use **Test Connection** to verify connectivity (the server only needs to *respond* — not accept) and **Test User Auth** to run a full authentication including any Access-Challenge 2FA steps, with the returned attributes displayed so you can build mappings.

### RADIUS Group Mapping

RADIUS has no group-enumeration API; instead, group assignment is driven by attributes returned on each `Access-Accept`:

- **Attribute → Group mapping** — in the RADIUS panel's mapping table, assign each PingWatch group an attribute name (e.g. `Fortinet-Group-Name`) and value (e.g. `pingwatch-admins`). On every successful login, PingWatch matches the returned attributes to the first mapped group, and assigns the user that group and its default role.
- **Default role / group** — if no mapping matches, the user receives the configured default role and default group.
- **Auto-provision** — enable "Auto-provision" and unknown RADIUS users are created automatically on first successful login (no manual user creation required).
- **Access-Challenge 2FA** — if the RADIUS server issues an `Access-Challenge` (FortiAuthenticator token, Duo, RSA SecurID, Azure NPS extension), the login screen presents the server's prompt and collects the OTP. Successfully completing a challenge skips the app's built-in TOTP step for that login.
- **Primary/secondary failover** — on timeout or socket error, PingWatch transparently retries against the secondary server (if configured). `Access-Reject` is treated as a definitive answer and does not trigger failover.

---

## SAML 2.0 / OIDC Single Sign-On

Federated enterprise SSO alongside local + LDAP + RADIUS. Tested with **FortiAuthenticator**; protocol-compliant for **Okta, Microsoft Entra ID (Azure AD), Keycloak, ADFS, OneLogin, PingFederate, Google Workspace, Shibboleth**. Configure each independently in **Settings → Integrations → 🪪 SAML 2.0** or **🪙 OIDC**.

### SAML 2.0 (SP-initiated)

- **IdP metadata import** — three sources: **By URL** (auto-falls-back to TLS-unverified for self-signed internal IdPs; the IdP signing cert pinned post-import is the actual signature trust anchor), **Paste XML**, or **Upload XML file**. Extracted entityID, SSO URL, and signing cert auto-populate the form.
- **SP metadata export** — `GET /api/saml/metadata` returns the XML blob; download from the UI, hand to your IdP admin.
- **SP signing certificate** — generate from the UI (RSA-2048, 825-day, self-signed); private key Fernet-encrypted at rest. Rotatable independently of the TLS cert.
- **Signed AuthnRequests** — when enabled, every request is signed with `signxml` (RSA-SHA256, exclusive c14n). Required by FAC and ADFS in default configs.
- **Assertion verification** — IdP signing cert pinned per-provider; signature checked on every login; `NotOnOrAfter` + `Audience` + `Issuer` validated; assertion-only and Response+Assertion signing patterns both supported.
- **TLS dependency** — `pysaml2 >= 7.5` + `signxml >= 3.2` (pure Python — no `xmlsec1`, no system deps; `pip install` is enough).

### OpenID Connect

- **Auto-discovery** — paste the issuer URL, click *Auto-discover*; PingWatch fetches `.well-known/openid-configuration` + JWKS and populates all endpoint fields.
- **Authorization Code + PKCE (S256)** — no client_secret in the redirect; `state` + `nonce` validated; ID token verified against the JWKS via `authlib.jose`.
- **Scheduled JWKS refresh** — discovery + JWKS re-fetched on the configured interval (default hourly), so key rotation is picked up before the first user hits a broken validation.
- **Dependency** — `authlib >= 1.3`.

### Common to both

- **JIT provisioning** — first successful SSO login auto-creates a local user row with `external_id = "saml|<entity>|<nameid>"` or `"oidc|<issuer>|<sub>"`; subsequent logins look up by external_id and sync display name, email, and group/role from the IdP.
- **Group → role mapping** — extends the existing groups table with `saml_group_value` / `oidc_group_value` columns. SAML attribute or OIDC `groups` claim values are matched case-insensitively against your mapped groups; first match assigns the role. Configurable default role + "reject unmapped users" policy.
- **TOTP still applies** — IdP-provided identity flows through PingWatch's TOTP gate (if the user has 2FA enabled); the trusted-device cookie works unchanged.
- **Login screen** — when at least one SSO method is enabled, the login form shows a **Sign in with {IdP}** button above the local form. With nothing configured, the login page looks identical to today.
- **Coexistence** — local + LDAP + RADIUS + SAML + OIDC all active simultaneously; admin enables/disables each independently. Local admin login is always available as a break-glass.
- **Health monitoring** — boot-time sanity pass + scheduled refresh (default hourly) revalidates certs, JWKS, and configuration; failures logged at WARNING/ERROR; status badge in Settings → Integrations turns yellow at <30 days to cert expiry.

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

- **`server.py`** — HTTP(S) dispatcher, starts all background threads
- **`gui_setup.py`** — tkinter GUI setup wizard (dark-themed, 6-step flow)
- **`setup_wizard.py`** — cross-platform CLI setup wizard (fallback for headless/SSH)
- **`core/setup_logic.py`** — shared setup logic (packages, ports, DB init) used by both wizards
- **`monitoring/probes.py`** — all sensor probe types on per-sensor threads (VMware probes via `vmware/client.py`)
- **`backup/engine.py`** — SSH/Telnet connections, TOFU host key verification, enable-mode escalation
- **`core/auth.py`** — PBKDF2-SHA256 local auth + LDAP branch via `core/ldap_auth.py`
- **`snmp/`** — UDP trap listener, OID enrichment, vendor fingerprinting
- **`db/`** — dual-backend persistence: Main DB (config/settings) + Logs DB (samples/events)
