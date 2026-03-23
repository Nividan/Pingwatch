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

## 🔥 High Priority
- Separate database
  - Main DB
  - Sensor Logs DB

## ⚙️ Medium Priority
- Fix sensor tile alignment
- Improve user box

## 🎨 Low Priority
- Fix history icon
- Add filter arrows (events tab)
- Add Home button