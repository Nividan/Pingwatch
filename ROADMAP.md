# 🚀 PingWatch Roadmap

Completed work lives in [CHANGELOG.md](CHANGELOG.md). This file tracks planned work only.

---

## 🔴 High Priority

**Backend**
- **Sensor Templates** — named sensor bundles ("Web Server", "Domain Controller") stored in `app_settings`; per-row template picker in the Subnet Discovery result grid and Auto-Discovery sensor policy; Auto-Discovery can consume templates once they exist

---

## ⚙️ Medium Priority

**Backend**
- **Parent-Child Dependency Suppression** — optional `parent_device_id`; suppress child alert dispatch when parent is down; NTM-integrated parent picker
- **Further sample rollup** — hourly/daily buckets beyond the existing 5-min rollup for multi-year retention at minimal storage cost

**Integrations**
- **Teams First-Class Integration** — native adaptive-card dispatcher; include scheduled-report delivery (PDF + CSV sidecar) as a second delivery option on the schedule editor alongside email
- **Probe types to add** — `ldap` (bind test), `postgres` / `mysql` (connection test), `ntp` (time-sync check), `imap` / `pop3` (mailbox auth), `mssql` (connection test) — `smtp`, `ssh`, `sftp`, `radius` are shipped
- **API Tokens** — scoped REST tokens (read-only / full) for scripts, CI, Terraform

**UI**
- **Session Management Widget** — view + revoke active sessions from a dashboard widget or user-menu entry

---

## 🎨 Low Priority

**UI**
- Compact mode
- Accessible contrast mode
- Spacing / alignment cleanup
- **Bulk operations** — pause/resume/delete/move N sensors with checkboxes
- **Keyboard shortcuts** — `g d` → devices, `g e` → events, `/` → focus search
- **Favorites** — star a sensor; pinned at top of device card
- **IPv6 dashboards** — IPAM UI currently assumes IPv4

**Backend**
- **Distributed probes** — lightweight remote agent shipping results back to the central server
- **HA / clustering** — active-passive with shared PostgreSQL
