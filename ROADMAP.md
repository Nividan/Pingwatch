# 🚀 PingWatch Roadmap

## ✅ Completed

- HTTPS / TLS support — self-signed cert generation, import, auto-discovery
- Syslog forwarding — RFC 5424, UDP/TCP, severity filter
- Linux / macOS support — `start.sh`, systemd service, headless setup wizard
- LDAP / Active Directory authentication — LDAPS/StartTLS, encrypted bind password, auto-provision
- LDAP Group Integration — import AD groups, login-time sync, background sync, nested groups
- IP Address Management (IPAM) — subnet tracking, reverse DNS, live ping-sweep integration
- Dual-database architecture — SQLite (default) + PostgreSQL; split Main/Logs DBs; export/import
- Network Topology Manager (NTM) — draw.io-style editor, live status overlay, PNG export
- User profiles and groups — full name/email per user, self-service editor, group CRUD
- User profile dropdown — role badge, theme toggle, Change Password, Edit Profile
- Advanced alerting — hierarchical PRTG-style profiles; cascade resolution; reusable action templates; maintenance windows
- Config backup (lightweight NCM) — SSH/Telnet, diff viewer, global search, vendor-aware rollback
- SNMP improvements — interface discovery, Counter32/64 rate display, wrong-OID detection
- VMware vSphere monitoring — 16 metrics, VM discovery, grouped display, smart thresholds
- Device License Tracking — per-device licenses, expiry alerts, IPAM badge, dashboard widget
- Multi-dashboard tabs — per-user named dashboards, right-click rename/delete, starter widgets
- Subnet Discovery — CIDR scan, Full/Ping-only modes, per-device sensor review, bulk add
- Anomaly detection — opt-in per sensor; EWMA learned baseline; cold-start suppression
- Two-factor authentication (TOTP) — optional/enforceable per role, recovery codes, trusted devices
- Light / Dark theme — full CSS variable palette, canvas/iframe sync, cross-device persistence
- Reports module — scheduled PDF/CSV exports; Executive / Technical / Inventory kinds; WeasyPrint + Matplotlib; email delivery; PDF/A compliance mode
- Air-gapped compatibility — self-hosted fonts, zero CDN dependencies, offline install guide
- Professional SMTP alert emails — hero logo, status banner, breadcrumb path, stats grid
- GUI setup wizard (Windows) — dark-themed tkinter, 6-step flow, background package installs
- Performance & scalability — auto-scaling probe executor, SSE batching, startup restore fix

---

## 🔴 High Priority

- **Auto-Discovery with Sensor Templates** — named sensor bundles ("Web Server", "Domain Controller") stored in `app_settings`; per-row template picker in the discovery result grid

---

## ⚙️ Medium Priority

- **Parent-Child Dependency Suppression** — optional `parent_device_id`; suppress child alert dispatch when parent is down; NTM-integrated parent picker
- **Teams First-Class Integration** — native adaptive-card dispatcher; include scheduled-report delivery (PDF + CSV sidecar) as a second delivery option on the schedule editor alongside email
- **Session Management Widget** — view + revoke active sessions from a dashboard widget or user-menu entry
- **Probe types to add** — `smtp` (MAIL FROM round-trip), `ldap` (bind test), `postgres` / `mysql` (connection test)
- **SAML / OIDC SSO** — enterprise SSO alongside LDAP
- **API Tokens** — scoped REST tokens (read-only / full) for scripts, CI, Terraform
- **Further sample rollup** — hourly/daily buckets beyond the existing 5-min rollup for multi-year retention at minimal storage cost

---

## 🎨 Low Priority

- Compact mode
- Accessible contrast mode
- Spacing / alignment cleanup
- **Bulk operations** — pause/resume/delete/move N sensors with checkboxes
- **Keyboard shortcuts** — `g d` → devices, `g e` → events, `/` → focus search
- **Favorites** — star a sensor; pinned at top of device card
- **IPv6 dashboards** — IPAM UI currently assumes IPv4
- **Distributed probes** — lightweight remote agent shipping results back to the central server
- **HA / clustering** — active-passive with shared PostgreSQL
