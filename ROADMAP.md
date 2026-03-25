# 🚀 PingWatch Roadmap

## ✅ Completed
- Fix export PNG (NTM)
- Create new log file for backup
- Add backup settings (global schedule, retention)
- Export backup configs to `/config-backup/`
- Add SNMP device type + trap database
- start.bat setup wizard
- HTTPS support (auto-generate/import cert)
- Syslog integration (RFC 5424, UDP/TCP, severity filter, test button)
- Linux / macOS support (v0.7.1)
  - Cross-platform setup wizard (apt/dnf/yum/brew, headless mode)
  - `start.sh` launcher with `--install-service` / `--uninstall-service`
  - systemd service with `CAP_NET_RAW` + `CAP_NET_BIND_SERVICE`
  - Server restart & shutdown from web UI (Settings → General)
- LDAP / Active Directory authentication
  - Service-account bind + user search + user bind flow
  - LDAPS, StartTLS, and plain LDAP support
  - Encrypted bind password storage (Fernet)
  - Domain user creation with role assignment
  - Test Connection & Test User Auth from Settings UI
  - Accepts `user`, `DOMAIN\user`, and `user@domain` login formats
- IP Address Management (IPAM)
  - Subnet management (CIDR) with host IP expansion
  - Per-IP name/allocation tracking with inline editing
  - Utilisation summary per subnet
  - Auto-sync with monitored devices
  - Reverse-DNS lookup column with background batch resolution
  - Per-subnet Refresh DNS button with live polling (operator role)
  - DNS hostname search support
- Dual-database architecture
  - Main DB (`pingwatch.db`) — config, devices, users, IPAM, SNMP reference
  - Logs DB (`pingwatch_logs.db`) — sensor samples, flap log, SNMP traps, errors
  - Independent write-queue threads per DB
  - One-time safe migration from legacy single-DB
  - Split export/import (Main DB, Logs DB, ZIP bundle with manifest)
  - DB stats API with row counts per table
  - Dual-DB UI in Settings → Database tab
  - Scheduled backup covers both DBs; fixed backup `CANTOPEN` error
- Network Topology Map (NTM) improvements
  - Device name overflow fix — all node types truncate long names with ellipsis
  - Backbone Switch: fixed phantom default VLAN badges when no VLANs configured
  - Firewall / Switch / Backbone Switch: optional Primary / Secondary role badge
  - Edit Link: fixed source and target device changes not being saved

## ⚙️ Medium Priority
- Fix sensor tile alignment
- Improve user box

## 🎨 Low Priority
- Fix history icon
- Add filter arrows (events tab)
- Add Home button