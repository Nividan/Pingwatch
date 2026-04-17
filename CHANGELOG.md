# 📋 PingWatch Changelog

Detailed implementation notes for every shipped feature. For the high-level roadmap and upcoming work see [ROADMAP.md](ROADMAP.md).

---

## Settings UI & observability improvements

- **LDAP/AD status badge** — Integrations tab now shows a live status dot for LDAP/AD alongside the existing SMTP and Syslog dots; `core/ldap_auth.py` tracks the last success/failure timestamp via `_record_ok()` / `_record_err()` hooks wired into `ldap_test_connection`, `ldap_authenticate`, and `ldap_sync_groups`; `get_ldap_status()` returns `ok` / `error` / `configured` / `unconfigured`; `GET /api/settings` now includes `ldap_status` alongside `smtp_status` and `syslog_status`
- **Log time-range filter expanded** — added 3 h, 6 h, and 12 h presets; default changed from "All time" to "6 h"; `_logFilter` initialised with `timeRange: '6h'`; `_clearLogFilters()` resets to 6 h
- **Custom log date range** — new "Custom range…" option in the log time filter reveals inline `datetime-local` pickers (From / To); sends `after=` / `before=` query params to the log API (T → space conversion for backend compatibility); `_logFilter.customFrom` / `_logFilter.customTo` managed by `_onLogFilterChange()`
- **Syslog settings cleanup** — removed the redundant "Minimum Severity" dropdown from the "Alert Event Forwarding" section; "Application Log Forwarding" retains its "Minimum Level" filter
- **Startup log deduplication** — removed a redundant `"PostgreSQL pool ready"` log line from `server.py` that duplicated the message already emitted by `db/pg_pool.py::pg_init_pool()` (which includes `min=`/`max=` pool size detail)

---

## Reports polish — aggregation, honest durations, manager-ready sections

- **Custom report kind** — grouped section-picker modal (Availability / Incidents / Health / Inventory / Other) with `exec` / `tech` / `inv` presets; per-section options (top-N, booleans, thresholds); saved in the template's `config.sections` + `config.options` and round-tripped through the editor
- **Aggregated incident log** — `_cluster_flaps_into_outages(flaps, idle_gap_s=300, currently_bad=...)` collapses consecutive bad-state events for the same `(did, sid)` into one outage row; 365-day reports drop from ~500 rows to a scannable handful; raw per-event table rendered below when the template opts in (`show_individual_events`)
- **Major Incidents** — `_detect_major_incidents(flaps, min_devices=10, gap_minutes=5, currently_bad=...)` buckets DOWN events by minute, merges adjacent windows, and emits one row per cluster (≥ N distinct devices); pure stats only — no root-cause inference; exposes `_dids_affected` for suppression and `groups_affected` resolved via live `STATE.devices` (no stype leak)
- **Suppress redundant outages** — `_suppress_outages_in_majors(outages, majors)` drops per-sensor outages whose first event falls inside a Major Incident window for the same device, so a 60-device cluster no longer emits 130+ duplicate per-sensor rows
- **Sensor configuration issues** — `_classify_config_issues(flaps)` pattern-matches detail strings (`Unknown metric:`, `SSL error — try disabling Verify SSL`, `Metric … not available`, `CERTIFICATE_VERIFY_FAILED`, `Invalid OID format`) and routes them out of the incident stream into their own section; rolled up by `(did, issue_type)` with a `sensor_count` column so one root cause renders as one row
- **Device health scores** — `_device_health_scores(availability, flaps, limit)` computes a composite 0–100 per device: downtime up to 50, incident load up to 20, currently DOWN −20 / WARN −10; banded green ≥ 90 / amber ≥ 70 / red < 70; rendered as coloured pill chips (`.hs-good` / `.hs-warn` / `.hs-bad`) sorted worst-first
- **Honest "open" flag** — `_currently_bad_sensor_keys()` reads live STATE once per report; outages and raw flaps only mark `ongoing=True` when the sensor is still unhealthy now. Historical rows where `resolved_at=0` simply because older builds didn't stamp resolutions no longer claim to be open; they render as `—` (unknown duration)
- **`durfmt_flap` filter** — renders durations honestly: `"open"` only when `ongoing=True`, `"<1s"` for sub-second resolves, `durfmt(int(d))` for known durations, `"—"` for unknown-and-not-ongoing
- **`cleandetail` filter** — strips a stale trailing `"ms"` suffix from non-latency flap details (e.g. `"Memory Consumed: 8192.0ms"` → `"Memory Consumed: 8192.0"`) when the string mentions a non-latency keyword; historical probe builds wrote every value with `"ms"` regardless of unit and those rows can't be retroactively rewritten, so the cleanup happens at render time
- **All polish features apply across templates** — Executive gets Major Incidents + Device Health + a config-issue one-line callout; Technical gets all blocks including the raw-event drill-down; Inventory gets Device Health; Custom gates each block behind `meta.sections`
- **Report History multi-select + bulk delete** — checkboxes per row, tri-state "select all" in the header, sticky action bar showing selection count with Delete-selected + Clear buttons; new `POST /api/reports/history/bulk-delete` endpoint (`{ids:[…]}`, admin-only, capped at 500 per call, returns `{deleted, missing}`); single audit entry per batch

---

## Reports module (scheduled PDF / CSV exports)

- Three report kinds: **Executive Summary**, **Technical / Operations**, **Inventory & Compliance**
- Templates + schedules + history (three sub-tabs under a top-level `📊 Reports` tab); audit-logged CRUD; role-gated (viewer browse, operator run, admin mutate)
- Rendered via WeasyPrint (HTML + CSS → PDF) and Matplotlib (charts → inline PNG data URIs); cover page + print-tuned stylesheet (`reports/templates/report.css`); per-section `@page` rules so section headers don't bleed into the footer
- Tiered samples awareness — `_availability_by_device` and `_latency_percentiles` auto-switch between `sensor_samples` / `sensor_samples_5m` / `sensor_samples_1h` via `_pick_table(minutes)` so a 1-year report actually finds data
- Browser preview (`POST /api/reports/preview` returns inlined-CSS HTML) + Run Now + Test Send (PDF attached)
- Custom-range periods (`custom:<start>:<end>` via a datetime-local picker) alongside last_7d / last_30d / last_90d / last_month / last_quarter / last_year / month_to_date
- Compare-to-previous-period deltas on uptime / incidents / critical / warn / MTTR — rendered as coloured ↑/↓ arrows with inverted semantics for "lower is good" metrics
- Incident severity filter (All / Warn+ / Crit-only) applied to both the main period and the previous-period compare set
- CSV sidecar (multi-section, UTF-8 BOM for Excel) — saved next to the PDF, attached to scheduled emails, downloadable from the History tab
- Report signing — deterministic 12-char Report ID + SHA-256 of the rendered PDF bytes; both persisted to `report_history` and surfaced in the History UI (tooltip on the ID pill shows the full hash)
- Retention auto-prune — `report_retention_days` setting (default 365); hourly sweep removes expired history rows + PDF/CSV files on disk
- Storage path resolves to `$XDG_DATA_HOME/pingwatch/reports` / `~/.local/share/pingwatch/reports` (outside the git checkout) so `git pull` as root can't break write access; `PW_REPORTS_DIR` env override; tempdir fallback with a probe-write check
- Cron-style scheduler thread: daily / weekly (day-of-week mask) / monthly (day-of-month) / quarterly cadences; 90 s dedupe; staggered firing
- PDF/A-1b / 2b / 3b compliance mode — per-template `pdfa_mode` config; graceful fallback to standard PDF when WeasyPrint < 62
- Branding reuses existing `email_logo_data` + `org_name`; per-report footer text + brand colour in Settings → Email

---

## Anomaly detection (learned baselines)

- Per-sensor EWMA mean + variance (Welford-style update); O(1) hot path, 3 floats per sensor — safe at 10k-sensor scale
- Upper-tail z-test with variance floor (`max(σ, 10 ms, 0.2·μ)`); sensitivity dropdown (Strict/Balanced/Relaxed → k=3/4/6); 3-sample debounce
- Can only promote `ok → warn` — never fires crit; static thresholds remain authoritative for critical alerts
- Cold-start suppression: no alerts until `min_samples` reached AND `anomaly_cold_start_hours` elapsed (default 50 samples + 24 h)
- Global kill switch (`anomaly_global_enabled`) + per-sensor opt-in; failed probes never update the baseline
- Baseline checkpointed to `sensor_anomaly_baselines` hourly via `autosave_loop`; restored on startup so a restart doesn't reset learning
- Supported sensor types: `ping`, `tcp`, `http`, `dns`, `http_keyword`, `banner` (SNMP / TLS / VMware excluded in v1)
- `flap_log.direction='anomaly_warn'` distinguishes anomaly warnings from static-threshold warnings (🧠 badge + filter pill in Events tab)
- UI: collapsible "🧠 Anomaly Detection" section on the sensor edit modal; baseline band overlay on sensor history chart
- Settings → Sensors tab: global master switch, cold-start/checkpoint knobs, defaults for new sensors, one-click bulk enable
- Bulk enable — `POST /api/anomaly/bulk-enable` resets each baseline to a fresh cold-start window, preventing alert storms
- `POST /api/sensors/{did}/{sid}/anomaly/reset` — wipe in-memory + DB baseline (operator role)

---

## Two-factor authentication (TOTP) + trusted devices

- Optional per user; enforceable per role (viewer / operator / admin)
- Setup flow: QR code + manual secret; TOTP verify step at login
- Recovery codes — 8 single-use backup codes at enrolment; admin reset via `POST /api/users/{u}/totp/reset`
- Audit log entries for all 2FA events (enable, disable, login, recovery use, admin reset)
- `pw_trusted` HttpOnly SameSite=Strict cookie; raw token SHA-256 hashed before DB storage
- Default 9-hour trust window (one workday); configurable up to 30 days; 0 = always prompt
- `trusted_devices` table — device label (User-Agent parsed), IP, last used, expiry; server-side revocation
- Auto-revoke on: password change, 2FA disable, admin TOTP reset
- Trusted Devices UI in 2FA settings — per-device Revoke + Revoke All; current device flagged
- Background sweep of expired rows every 6 hours via `autosave_loop`

---

## Light / Dark theme

- Full GitHub-Light CSS variable palette via `:root[data-theme="light"]` in `style.css`; dark remains default
- Inline `<head>` bootstrap reads `localStorage.pw_theme` before CSS paints — prevents flash-of-unthemed-content
- Hybrid persistence — `localStorage` for instant apply + `users.theme_preference` for cross-device sync; reconciled via `/api/me` on login
- `frontend/theme.js` — public API: `getTheme`, `setTheme`, `toggleTheme`, `getCssVar`, `getCssRgb`; loaded first in JS bundle
- `PATCH /api/me/theme` endpoint fired in background by `setTheme()`
- Canvas drawers (`bg.js`, `dashboard.js`, `sensors.js`, `map.js`) maintain per-module RGB caches via `getCssRgb()`; invalidated on `themechange` event — open history charts redraw immediately on theme flip
- Topology map iframe (`map.css`) — full light palette; synchronous `<head>` bootstrap in `map.html` reads shared `localStorage.pw_theme`
- NTM animated background (`_NTM_BG` palette) — hex grid, matrix streams, particles, ring pulses all flip via `ntm_themechange`; offscreen hex cache invalidated on colour change
- Semantic surface tokens: `--card-bg`, `--panel-bg`, `--modal-overlay`, `--surface-inset`, etc.
- Login-screen wordmark gradient converted to `var(--accent)` / `var(--accent-hover)` for AA contrast in light mode

---

## Performance & scalability

- Auto-scaling probe executor — `max(64, min(512, sensor_count // 4))`; live resize on device add/delete; manual override in Settings → General
- `dev.status` cached on the sensor object and invalidated on state change — 2–5× CPU reduction for large devices
- Scheduler heap tombstones for deleted sensors — stale entries no longer accumulate in the min-heap
- Per-subscriber SSE sender threads — slow browsers no longer block the probe loop
- `db/persistence.py` startup restore — per-sensor indexed seeks + single batched `GROUP BY` (avoids full-table window-function scan that caused 50 s startup on PostgreSQL); startup time ~4 s
- `_pick_table` boundary moved from 4320 → 1440 min — 3-day history routes to `sensor_samples_5m` (full coverage) instead of raw table (10k-row cap)
- Fixed rollup backfill triggering on every restart — condition now checks `sensor_samples_5m` row count instead of stale `MIN(ts)` gap detection
- SSE batching (250 ms), status-change guard, in-place SVG updates, O(1) sensor lookups
- NTM: LED blink moved to pure CSS `@keyframes`; packet-trace cooldown 4 → 6 s; link animation step counts halved; SSE threshold events gated by `_ntmVisible`

---

## Code quality refactor

- `db/helpers.py` — unified dual-backend query layer (`db_query`, `db_query_one`, `db_execute`, `db_executemany`, `db_upsert`, `db_cursor`, `_ph`); eliminates per-module `if is_pg()` boilerplate
- `core/constants.py` — centralised probe/server constants (`PORT_MIN/MAX`, `PROBE_DEFAULT_INTERVAL`, `SENSOR_HISTORY_SIZE`, etc.)
- `core/validation.py` — server-side input validators (`validate_port`, `validate_host`, `validate_interval`, `validate_timeout`, `validate_name`)
- `server.py Handler._error()` — full exception logged server-side, generic message returned to client
- `frontend/forms-utils.js` — `msColor()`, `statusClass()`, `_lsGet()`, `_lsSet()` promoted to canonical shared helpers
- `frontend/app.js TIMINGS` — frozen object of all SSE/UI timing constants; replaces scattered magic numbers
- `frontend/forms-settings.js` — `openSettings()` refactored from ~600-line monolith into 10 focused `_buildSettingsTab_*()` functions
- `SELECT *` replaced with explicit column lists in `db/alert_events.py` and `db/alert_profiles.py`
- `_broadcast` refactored to accept a list of `(event, data)` tuples per probe end

---

## Device License Tracking

- `device_licenses` table — `id`, `did`, `license_name`, `expiry_date`, `note`, `warn_days` (default 30), `crit_days` (default 0), `last_status`, `created_at`, `updated_at`; SQLite + PostgreSQL schemas
- `monitoring/license_checker.py` — compares expiry dates against today; fires `license_warn` / `license_crit` events into `flap_log` (`stype='license'`) on state change; auto-resolves on renewal; deduplication via `last_status`
- Runs every 6 hours via `autosave_loop` and immediately after any license add/update
- Edit Device modal — collapsible Licenses section: status badges (Valid / Expiring / Expired), days-remaining countdown, warn/crit thresholds per license
- IPAM table — Licenses column shows worst status badge per linked device; refreshed on SSE `license_status`
- License Overview dashboard widget — 4-KPI grid (Expired / Expiring / Valid / Total) + sorted expiration table

---

## Multi-dashboard tabs

- Per-user named dashboards (up to 10) with tab bar; create / rename / delete via right-click context menu
- New users auto-created a "Default" dashboard with 8 starter widgets
- `dashboards` table replaces `dashboard_widgets`; idempotent migration on startup
- API: `GET/POST /api/dashboards`, `GET/PUT/PATCH/DELETE /api/dashboards/{id}`, `PUT /api/dashboards/reorder`

---

## Subnet Discovery

- Full mode (ping + DNS + MAC/OUI + port scan + device-type guess) and Ping-only mode (fast, for /18–/16 ranges)
- Max scan size /16 (65 534 hosts) with tiered runtime warning banners; auto-switches to Ping-only when host count > 4 096
- Multi-NIC duplicate detection via hostname fingerprinting — flagged rows pre-unchecked, inline ⚠ note
- Per-device sensor review step; bulk add endpoint (`POST /api/discovery/bulk-add`)
- Dedicated `ThreadPoolExecutor(64)` isolated from sensor probe pool; in-memory state auto-purged after 1 h
- Per-device group assignment — global default + per-row override; accent border on overridden rows

---

## Hierarchical alert profiles

- PRTG-style escalation stages: trigger state (Down / Warning / Recovered), per-stage delay (seconds), repeat interval (minutes), reusable action templates
- Cascade resolution: sensor → device → group → global; first match wins; result cached and invalidated on profile change
- Recovery stage computes total downtime from session start; includes it in the notification
- `alert_profile_state` table persists stage fire history across restarts
- Recovery path uses `else:` guard — prevents `db_log_event(state="active")` running immediately after `db_auto_resolve_event()`
- Per-device and per-sensor profile override with one-click "Reset to inherited"

---

## VMware vSphere monitoring

- New sensor type: VMware — connects to vCenter/ESXi via pyvmomi (optional dependency)
- 16 metrics across CPU, Memory, Disk, Datastore, Network, System categories
- Session caching (25-min TTL) + metric caching (20-s TTL) — avoids redundant QueryPerf calls
- `SmartConnect()` capped at 60 s via `socket.setdefaulttimeout()` — prevents indefinite hang on first connect
- Grouped VM display — collapsible VM groups with per-metric rows, sparklines, uptime bars
- `mem_consumed_pct` uses `guestMemoryUsage` from VMware Tools (matches guest OS Task Manager)
- Device-level status correctly reflects VMware threshold states (crit → Down, warn → Warn)

---

## Config backup (lightweight NCM)

- SSH (paramiko) + Telnet with TOFU host key verification, enable-mode escalation, paging disable
- Diff viewer — line-level diff, ±3-line context, expandable equal sections
- Search inside config viewer; global config search across all stored configs
- Vendor-aware rollback: Cisco includes enclosing context block + `end` + `wr`; FortiGate uses `config/edit/set/next/end` blocks
- Backup Status dashboard widget — OK / Failed / Never run / Enabled KPI counts

---

## LDAP Group Integration

- Import AD/LDAP groups with per-group role assignment; LDAP badge on imported groups
- Auto-provision: unknown LDAP users in imported groups created automatically on first login
- Login-time sync: group, role, and display name refreshed from LDAP on every login
- Auto-disable: login rejected and account suspended when user removed from all imported groups
- Background sync thread (configurable interval, default 60 min)
- Nested AD groups via `LDAP_MATCHING_RULE_IN_CHAIN` (optional toggle)
- Multi-group priority: user receives the highest role among matched groups
- Test User Groups diagnostic — admin can look up a user's LDAP group memberships from the UI

---

## Dual-database architecture

- Main DB (`pingwatch.db` / `main` schema) — config, devices, users, IPAM, alerts
- Logs DB (`pingwatch_logs.db` / `logs` schema) — sensor samples, flap log, SNMP traps, errors
- Independent write-queue threads per DB (SQLite); PostgreSQL bypasses queues (MVCC)
- One-time safe migration from legacy single-DB; split export/import (Main, Logs, ZIP bundle)
- `db/helpers.py` unified query layer — `db_query`, `db_execute`, `db_upsert`, `db_cursor`, `_ph()`

---

## Air-gapped compatibility

- Self-hosted Google Fonts (Exo 2, JetBrains Mono, Orbitron, Share Tech Mono) as `.woff2` in `frontend/fonts/`; all CDN `<link>` tags removed
- `map.js::_inlineFontsForExport()` base64-embeds local woff2 files for offline PNG topology export
- CSP tightened: `style-src 'self' 'unsafe-inline'; font-src 'self';` — no external origins
- Air-gapped installation guide in `README.md`

---

## GUI setup wizard (Windows)

- Dark-themed tkinter wizard (`gui_setup.py`) — 6-step flow: Welcome, Packages, Database, Network, Security, Summary
- Background threads for pip installs and PostgreSQL connection tests
- Falls back to CLI `setup_wizard.py` when tkinter is unavailable
- `windows/launcher.pyw` — Python-based launcher with admin elevation, first-run detection, port cleanup

---

## TLS sensor fixes

- Threshold direction fixed — alerts when days remaining drops **below** threshold (was inverted)
- Default thresholds corrected from 500/2000 (ms-style) to 30/7 (days)
- Chart threshold lines show "d" suffix; log messages include "days" unit
- Add Sensor tab switching updates threshold labels dynamically for TLS sensors

---

## Events tab — Active / History split

- Inner tabs ("Active" / "History") inside Sensor Events panel; Active tab badge shows live unresolved count
- SNMP traps without a linked alert rule default to History (informational, not actionable)
- "Resolve All" button hidden on History tab; tab selection persisted in `localStorage`

---

## Maintenance window improvements

- Scope field replaced with device/group dropdown — no more typing raw IDs
- One-time vs recurring time fields separated; recurring windows auto-set start/end to now → +10 years
- List shows device name instead of device ID for device-scoped windows

---

## SNMP improvements

- Interface discovery — walks ifTable + ifXTable; auto-selects metric per interface
- Counter32 / Counter64 traffic OIDs display live rate (B/s → GB/s) via delta calculation with wraparound handling
- Non-numeric SNMP values shown in orange as a misconfiguration hint
- Probe uses `-On` flag and stdout-only parsing for deterministic output regardless of MIB environment

---

## Bug fixes & minor improvements

- Stop All device sensors — stopped sensors excluded from `Device.status`; device shows gray (unknown) instead of red (down); `stop_device()` auto-resolves open flap events
- Sensor host linking — sensors inherit device host by default; setting a host manually marks it overridden; device IP changes propagate automatically to all linked sensors
- Alert engine hardening — delayed DOWN emails skip if sensor deleted or stopped during delay window; rule engine verifies sensor/device still exists before dispatching
- Sensor history KPI tiles — Avg/Min/Max reflect the selected time window (12h / 3d / 7d / 30d / 90d)
- Bulk resolve — "Resolve All" on Events tab resolves all active alert events and flaps in one click
- Device tile loading skeleton — shimmer animation while fresh data fetches in parallel
- Dashboard widget loading shimmer — widgets show "Loading…" overlay during initial data fetch
- Sensor history time-range fade — smooth opacity transition with guaranteed 250 ms minimum display
- Debug Mode checkbox auto-saves on toggle — no Save button required; reverts on API failure
- Event detail panel "Open Device" and "Sensor History" buttons restored
- Backup schedule dark mode styling fixed
- Project structure reorganised — `start.sh` + service file → `linux/`; `start.bat` + `pingwatch.pyw` → `windows/`
