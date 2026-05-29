# PingWatch REST API

Reference for driving PingWatch from scripts, CI pipelines, and Terraform. The
SPA itself uses these same endpoints over a cookie session — for everything
else, use a Bearer API token.

> Architecture, schema, and contributor notes live in
> [DEVELOPER.md](DEVELOPER.md). This file is the user-facing API contract.

---

## Getting started

### 1. Create a token

Settings → **API Tokens** (admin only) → **Generate Token**:

- **Name** — a label so you can tell tokens apart in the list.
- **Scope** — `read` (GET / HEAD only) or `full` (any HTTP method, capped by the owning user's role).
- **Expires** — optional. Revoke is the primary kill switch.

The plaintext token is shown **exactly once** in the create response. PingWatch
only stores its SHA-256 hash; there is no recover-token flow. Lost it? Revoke
and generate a new one.

### 2. Use it

```sh
T="pw_<your-token>"

# Read endpoint — works for both read and full scopes
curl -H "Authorization: Bearer $T" https://pingwatch.example.com/api/devices

# Write endpoint — needs scope=full
curl -X POST -H "Authorization: Bearer $T" \
     -H "Content-Type: application/json" \
     -d '{"name":"sw1","host":"10.0.0.1","group":"core"}' \
     https://pingwatch.example.com/api/device
```

A read-only token attempting a write returns `403 {"error": "read-only token cannot perform writes"}`.

### 3. Revoke when done

```sh
curl -X DELETE -H "Authorization: Bearer $ADMIN_T" \
     https://pingwatch.example.com/api/tokens/42
```

Revocation propagates within `~5s` (cache TTL). All active clients begin
getting `401` on the next request.

---

## Conventions

### Authentication

Two parallel mechanisms — both go through the same auth pipeline:

| Mechanism      | Header / Cookie               | Scope           | Where used        |
|----------------|-------------------------------|-----------------|-------------------|
| Cookie session | `Cookie: session=<id>`        | implicit `full` | Browser SPA       |
| Bearer token   | `Authorization: Bearer pw_…`  | `read` or `full` | Scripts / CI / TF |

Cookie sessions slide their TTL forward on use; tokens use a fixed
`expires_at` (or never).

### Scopes & methods

- `read` tokens accept **GET / HEAD / OPTIONS** only. Every other method
  returns `403`.
- `full` tokens accept any method, still subject to the owner's RBAC role.
- Cookie sessions always run with implicit `full` scope.

### Roles

`viewer (0) < operator (1) < admin (2)`. Each endpoint lists its minimum role.
A `full` token authenticated as an `operator` cannot reach `admin`-only routes.

### Error envelope

All errors return JSON of the shape:

```json
{ "error": "human-readable reason" }
```

with the matching HTTP status (`400`/`401`/`403`/`404`/`409`/`500`).

### Server-Sent Events (`/events`)

The live event stream uses cookie session auth only. `EventSource` cannot send
custom headers, so an API-token request to `/events` is rejected with
`400 {"error": "SSE requires cookie session"}`. For non-browser clients, poll
the relevant `/api/...` history endpoints instead.

### Content type

- Requests with a body MUST send `Content-Type: application/json`.
- Responses are always JSON unless explicitly stated (e.g. `GET /api/saml/metadata` returns XML, report downloads return PDF / CSV).

### Audit

Token creation and revocation are written to the audit log
(`api_token_create`, `api_token_revoke`). Individual API requests are **not**
audited (would be too noisy); use server logs for that.

---

## Authentication & API tokens

| Method   | Path                          | Min role | Description                                                                 |
|----------|-------------------------------|----------|-----------------------------------------------------------------------------|
| `POST`   | `/api/login`                  | public   | Local user login `{username, password}` → sets `session` cookie             |
| `POST`   | `/api/logout`                 | viewer   | Invalidate the current session                                              |
| `GET`    | `/api/me`                     | viewer   | Own username, role, full_name, email, theme_preference                      |
| `GET`    | `/api/tokens`                 | admin    | List API tokens (no plaintext, no hash); `?user=<name>` to filter           |
| `POST`   | `/api/tokens`                 | admin    | Create `{name, scope:'read'\|'full', expires_at?}` → `201 {id, token:"pw_…"}` **(plaintext returned once)** |
| `DELETE` | `/api/tokens/{id}`            | admin    | Revoke a token (sets `revoked_at`, evicts cache)                            |

`POST /api/tokens` is rejected when the calling principal is itself an API
token — a token cannot mint another token. Use a cookie session.

`GET /api/settings/public_auth` (no auth required) returns
`{saml_enabled, saml_display_name, oidc_enabled, oidc_display_name}` for the
pre-login screen to render SSO buttons.

---

## Devices & Sensors

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/devices` | viewer | List all devices with latest sensor states |
| `POST` | `/api/device` | operator | Create a device |
| `GET` | `/api/devices/{did}` | viewer | Get device detail |
| `PATCH` | `/api/devices/{did}` | operator | Update device |
| `DELETE` | `/api/devices/{did}` | operator | Delete device |
| `POST` | `/api/devices/bulk` | operator | Bulk action across up to 1000 devices `{device_ids:[], action:"move"\|"start"\|"stop"\|"delete", group?:"…"}` → `{ok, applied, failed, results:[{did, ok, reason?}]}`; one audit entry per call |
| `GET` | `/api/sensors/{did}` | viewer | List sensors for a device |
| `POST` | `/api/sensors/{did}` | operator | Add a sensor |
| `PATCH` | `/api/sensors/{did}/{sid}` | operator | Update a sensor (accepts `anomaly_enabled`, `anomaly_sensitivity`, `anomaly_min_samples`) |
| `DELETE` | `/api/sensors/{did}/{sid}` | operator | Delete a sensor |
| `POST` | `/api/sensors/{did}/{sid}/anomaly/reset` | operator | Wipe the learned anomaly baseline (in-memory + DB row) |
| `POST` | `/api/device/{did}/scan` | operator | Trigger port scan (async) |
| `POST` | `/api/anomaly/bulk-enable` | admin | Enable anomaly detection on every supported sensor that's currently off; resets each baseline to a fresh cold-start window |

---

## Monitoring data

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/events` | viewer | Server-Sent Events stream of sensor updates and alerts. **Cookie session only** — API tokens get 400 |
| `GET` | `/api/flaps` | viewer | Recent flaps (state transitions) |
| `GET` | `/api/traps` | viewer | Recent SNMP traps |

---

## Live Map (NOC console)

Read-only endpoints feeding the NOC console at `/livemap.html`. Updates fan
out from `app.js` via `postMessage({type:'lm_update'})` to the iframe; the
iframe debounces and re-fetches on a 2-second flush.

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/livemap/sites` | viewer | Per-site rollup with metadata — `{sites: [{name, kind, pinned, display_name, devices, up, warn, down, alerts}]}` |
| `GET` | `/api/livemap/noc/summary` | viewer | NOC widgets payload — `{sites:{up,warn,down,total}, devices:{...}, alerts:{active,down,warn,ack}, uptime_24h, flaps_24h, incidents_24h, by_kind:{...}, top_problems:[...], recent_alerts:[...], off_site:[...]}` |
| `GET` | `/api/livemap/sites/{name}/tree` | viewer | Tier tree for one site — `{site:{...}, isp:[...], wan_switches:[...], firewalls:[...], core_switches:[...], switches:[...], chassis:[clusters], hypervisors:[clusters], vm_clusters:[clusters], ipmi:[clusters], other:[devices]}` |

---

## Alerts

### Alert profiles

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/alert/profiles` | viewer | List all profiles with scope and stage count |
| `POST` | `/api/alert/profile` | admin | Create profile |
| `GET` | `/api/alert/profile/{id}` | viewer | Get profile with all stages |
| `PATCH` | `/api/alert/profile/{id}` | admin | Update profile and stages |
| `DELETE` | `/api/alert/profile/{id}` | admin | Delete profile |
| `POST` | `/api/alert/profile/{id}/test` | admin | Test-fire all stages with synthetic event |
| `GET` | `/api/alert/action-templates` | viewer | List all action templates |
| `POST` | `/api/alert/action-template` | admin | Create action template |
| `PATCH` | `/api/alert/action-template/{id}` | admin | Update action template |
| `DELETE` | `/api/alert/action-template/{id}` | admin | Delete action template |

### Alert events

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/alert/events` | viewer | Paginated event history |
| `GET` | `/api/alert/events/active` | viewer | Active / unresolved events |
| `POST` | `/api/alert/events/resolve-all` | operator | Resolve all active alert events and flaps |
| `POST` | `/api/alert/event/{id}/ack` | operator | Acknowledge event |
| `POST` | `/api/alert/event/{id}/resolve` | operator | Resolve event |

### Maintenance windows

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/alert/windows` | viewer | List all maintenance windows |
| `POST` | `/api/alert/windows` | admin | Create window |
| `PATCH` | `/api/alert/window/{id}` | admin | Update window |
| `DELETE` | `/api/alert/window/{id}` | admin | Delete window |

---

## Configuration

### Settings

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/settings` | viewer | All app settings |
| `PATCH` | `/api/settings` | admin | Update settings (partial) |
| `GET` | `/api/server_info` | viewer | Server version, uptime, DB stats |
| `POST` | `/api/settings/smtp_test` | admin | Send a test email |
| `POST` | `/api/settings/syslog_test` | admin | Send a test syslog message |
| `POST` | `/api/server/restart` | admin | Restart the server process |
| `POST` | `/api/server/shutdown` | admin | Shutdown the server process |
| `POST` | `/api/db/backup/run` | admin | Trigger an immediate DB snapshot |

### TLS

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/tls` | admin | Certificate metadata + TLS settings (no private key) |
| `PATCH` | `/api/tls` | admin | Update `tls_enabled`, `tls_port`, `http_redirect` |
| `POST` | `/api/tls/upload` | admin | Upload and validate a PEM cert + key pair |
| `POST` | `/api/tls/generate` | admin | Generate a new self-signed certificate |

---

## Infrastructure

### Device config backups

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/backups` | operator | List all devices with latest backup metadata |
| `GET` | `/api/backups/{did}` | operator | Backup settings for a device (no plaintext passwords) |
| `PUT` | `/api/backups/{did}` | operator | Save backup settings |
| `GET` | `/api/backups/{did}/history` | operator | Backup run list for a device |
| `GET` | `/api/backups/run/{id}` | operator | Full run record including config text |
| `POST` | `/api/backups/{did}/run` | operator | Trigger immediate backup (async, rate-limited 30s) |
| `DELETE` | `/api/backups/run/{id}` | admin | Delete a backup run |

### Reports

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/reports/templates` | viewer | List all report templates |
| `POST` | `/api/reports/template` | admin | Create template `{name, kind, config_json}` |
| `GET` | `/api/reports/template/{id}` | viewer | Get template detail |
| `PATCH` | `/api/reports/template/{id}` | admin | Update template |
| `DELETE` | `/api/reports/template/{id}` | admin | Delete template |
| `GET` | `/api/reports/schedules` | viewer | List all report schedules |
| `POST` | `/api/reports/schedule` | admin | Create schedule |
| `PATCH` | `/api/reports/schedule/{id}` | admin | Update schedule (incl. enable/disable) |
| `DELETE` | `/api/reports/schedule/{id}` | admin | Delete schedule |
| `GET` | `/api/reports/history` | viewer | Paginated report history; optional `template_id` filter |
| `GET` | `/api/reports/history/{id}` | viewer | Single history record |
| `GET` | `/api/reports/history/{id}/download` | viewer | Download the generated PDF |
| `GET` | `/api/reports/history/{id}/csv` | viewer | Download the CSV sidecar (if generated) |
| `DELETE` | `/api/reports/history/{id}` | admin | Delete one history row + its PDF / CSV files |
| `POST` | `/api/reports/history/bulk-delete` | admin | Delete many history rows `{ids:[…]}` — capped at 500 per call; returns `{deleted, missing}` |
| `POST` | `/api/reports/run` | operator | Ad-hoc Run Now `{template_id}` — renders, saves PDF, returns history row |
| `POST` | `/api/reports/preview` | operator | Render HTML preview `{template_id}` — returns full HTML with inlined CSS (no PDF) |
| `POST` | `/api/reports/test-send` | operator | Test email delivery `{template_id, recipients}` — renders and emails PDF without saving history |

### Dashboards

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/dashboards` | viewer | List user's dashboards (id, name only); auto-creates "Default" with starter widgets for new users |
| `POST` | `/api/dashboards` | viewer | Create dashboard `{name}` (max 10 per user) |
| `GET` | `/api/dashboards/{id}` | viewer | Get dashboard widgets |
| `PUT` | `/api/dashboards/{id}` | viewer | Save widgets `{widgets: [...]}` |
| `PATCH` | `/api/dashboards/{id}` | viewer | Rename dashboard `{name}` |
| `DELETE` | `/api/dashboards/{id}` | viewer | Delete dashboard (rejects if last) |
| `PUT` | `/api/dashboards/reorder` | viewer | Reorder tabs `{ids: [3, 1, 2]}` |

---

## Discovery & IPAM

### IPAM

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/ipam/subnets` | operator | List all subnets with allocation summary |
| `POST` | `/api/ipam/subnets` | operator | Add a subnet `{cidr, name}` |
| `DELETE` | `/api/ipam/subnets/{id}` | operator | Remove subnet and all allocations |
| `GET` | `/api/ipam/subnets/{id}/ips` | operator | IP allocations for a subnet |
| `PUT` | `/api/ipam/ips/{subnet_id}/{ip}` | operator | Set or clear the name for an IP |

### Subnet discovery

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `POST` | `/api/discovery/scan` | operator | Start a subnet scan `{cidr, skip_monitored, mode}` → `202 {scan_id}` |
| `GET` | `/api/discovery/scan/{id}` | viewer | Poll scan progress and results |
| `DELETE` | `/api/discovery/scan/{id}` | operator | Cancel a running scan |
| `POST` | `/api/discovery/bulk-add` | operator | Bulk-create up to 500 devices with sensors `{devices: [{name, host, group, sensors: [...]}]}` |

### Sites (Live Map metadata)

The `sites` sidecar table stores presentation metadata only — distinct site
names still come from `devices.site` and `ipam_subnets.site`. Rows are created
lazily by the Live Map rollup when it sees an unseen site name.

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/sites/meta` | viewer | List metadata rows merged with every distinct site name → `{sites: [{name, kind, pinned, display_name, sort_order, created_ts, updated_ts}], kinds: [...]}` |
| `POST` | `/api/sites/meta` | operator | Create site `{name, kind, pinned?, display_name?, sort_order?}` — kinds: `internet`/`hq`/`dc`/`lab`/`pop`/`edge`/`office` |
| `PUT` | `/api/sites/meta/{name}` | operator | Update site; accepts `kind`, `pinned`, `display_name`, `sort_order`, `new_name` (rename), `also_rename` (bulk-update `devices.site`) |
| `GET` | `/api/sites/meta/{name}/usage` | viewer | `{devices, subnets}` — counts for the delete-confirm UI |
| `DELETE` | `/api/sites/meta/{name}` | operator | Delete metadata row; optional `?cascade=1` also clears `devices.site` and `ipam_subnets.site` |

### Device licenses

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/device/{did}/licenses` | viewer | List licenses for a device |
| `POST` | `/api/device/{did}/licenses` | operator | Add license `{license_name, expiry_date, note?, warn_days?, crit_days?}` → `{id, licenses[]}` |
| `PATCH` | `/api/license/{id}` | operator | Update license fields |
| `DELETE` | `/api/license/{id}` | operator | Delete a license |
| `GET` | `/api/licenses` | viewer | All licenses across all devices |
| `GET` | `/api/licenses/summary` | viewer | Counts by status `{ok, warn, crit, total}` |
| `POST` | `/api/licenses/check` | admin | Trigger immediate expiration check |

### VMware

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/vmware/metrics` | viewer | Available VM metrics with labels, units, groups |
| `POST` | `/api/vmware/vms` | operator | Discover VMs on a vCenter / ESXi host |

---

## Identity providers

### LDAP

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/ldap/settings` | admin | LDAP config (bind password never returned), including group integration fields |
| `PATCH` | `/api/ldap/settings` | admin | Save LDAP config |
| `POST` | `/api/ldap/test_connection` | admin | Test service-account bind |
| `POST` | `/api/ldap/test_auth` | admin | Test full user authentication flow |
| `POST` | `/api/ldap/search_groups` | admin | Browse / search LDAP directory for groups `{query}` → `{ok, groups: [{dn, cn, description, member_count}]}` |
| `POST` | `/api/ldap/test_user_groups` | admin | Look up a user's LDAP group memberships `{username}` → `{ok, display_name, email, groups: [dn, ...]}` |

### RADIUS

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/radius/settings` | admin | RADIUS config (shared secrets never returned; `radius_secret_set` / `radius_secret2_set` sentinels indicate if set) |
| `PATCH` | `/api/radius/settings` | admin | Save RADIUS config; empty secret = keep existing; non-empty = Fernet-encrypt and replace |
| `POST` | `/api/radius/test_connection` | admin | Send a bogus packet and verify the server responds `{ok, message}` |
| `POST` | `/api/radius/test_auth` | admin | Run a full authentication `{username, password}` → `{ok, attrs?, challenge?, message}` |
| `POST` | `/api/radius/test_auth_challenge` | admin | Continue a test-auth challenge `{challenge_id, response}` → same shape |
| `GET` | `/api/radius/attribute_mappings` | admin | List all groups with their RADIUS mappings + `available_groups` (unmapped) |
| `POST` | `/api/radius/attribute_mappings` | admin | Set or clear mapping for a group `{group_id, attribute, value}` |

`POST /api/login` returns `{radius_challenge: true, challenge_id, prompt}` when
RADIUS issues an Access-Challenge; complete it with
`POST /api/login/radius_challenge {challenge_id, response}`.

### SAML 2.0 SSO

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET`  | `/api/saml/login` | public | Build AuthnRequest (signed if `saml_sign_authn_requests=1`), return auto-submitting HTML form 302'd to the IdP SSO URL |
| `POST` | `/api/saml/acs` | public | Assertion Consumer Service — validates RelayState, verifies signature against pinned IdP cert, runs `sso_provision_or_sync`, issues session cookie, 302 → `/`. TOTP gate honoured |
| `GET`  | `/api/saml/metadata` | public | SP metadata XML (`application/samlmetadata+xml`) |
| `GET`  | `/api/saml/settings` | admin | Read config (SP private key never returned; `saml_sp_key_pem_set` boolean indicates if set) |
| `PATCH` | `/api/saml/settings` | admin | Partial update (allow-listed keys only) |
| `POST` | `/api/saml/metadata/import` | admin | `{source: "url"\|"xml"\|"file", url?, xml?}` → fetches/parses IdP metadata, stores entity_id + SSO URL + signing cert |
| `POST` | `/api/saml/sp_cert/generate` | admin | Generate a fresh RSA-2048 self-signed SP signing cert (825-day); returns the public PEM for display |
| `POST` | `/api/saml/test` | admin | Dry-run validation: cert expiry, signxml import, AuthnRequest build smoke test → `{ok, message, detail}` |

### OIDC SSO

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET`  | `/api/oidc/login` | public | Build authorization URL with PKCE (S256) + state + nonce; 302 to IdP authorization endpoint |
| `GET`  | `/api/oidc/callback` | public | Receives `code` + `state`; exchanges code for tokens; validates ID token via JWKS; runs `sso_provision_or_sync`; issues session cookie |
| `GET`  | `/api/oidc/settings` | admin | Read config (`oidc_client_secret_set` boolean only) |
| `PATCH` | `/api/oidc/settings` | admin | Partial update; if `oidc_issuer_url` changes, auto-refresh discovery |
| `POST` | `/api/oidc/discovery/refresh` | admin | Re-fetch `.well-known/openid-configuration` + JWKS; persist to `oidc_discovery_cache` |
| `POST` | `/api/oidc/test` | admin | Validates issuer reachability, parses discovery + JWKS → `{ok, message, detail}` |

### Auth backend health

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `POST` | `/api/auth/health/run_now` | admin | Wakes the refresh loop's multi-event wait so a full health pass runs immediately (results land in the status badges within seconds) |

`auth_refresh_interval_min` (allow-listed: `0` / `15` / `30` / `60` / `240` /
`720` minutes) is set via the standard `PATCH /api/settings`. `0` disables the
hourly loop but the boot sanity pass still runs.

---

## Users & profiles

### User profiles

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `PATCH` | `/api/me/profile` | viewer | Update own `full_name` and `email` (also accepts optional `theme_preference`) |
| `PATCH` | `/api/me/theme` | viewer | Update own theme preference `{theme: "dark"\|"light"}` — fired in the background by `setTheme()` |
| `PATCH` | `/api/users/{u}/profile` | admin or self | Update profile; admin can also set group_id and role |

### Two-factor authentication

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `POST` | `/api/me/totp/setup` | viewer | Generate TOTP secret + QR code URI `{secret, qr_uri}`. Idempotent before verification. |
| `POST` | `/api/me/totp/verify` | viewer | Confirm enrolment `{code, secret}`. Activates 2FA and returns 8 single-use recovery codes. |
| `POST` | `/api/me/totp/disable` | viewer | Disable 2FA for self `{password}`. Revokes all trusted devices for the user. |
| `POST` | `/api/users/{u}/totp/reset` | admin | Disable 2FA for `{u}` and revoke all their trusted devices. |
| `POST` | `/api/login/totp` | public | Complete a TOTP challenge `{challenge_id, code, remember?, remember_hours?}`. On success sets `session` cookie; optionally sets `pw_trusted` cookie when `remember=true`. |

### Trusted devices

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/me/trusted-devices` | viewer | List own trusted devices — label, IP, last used, expiry; includes `remember_hours` preference; current device flagged with `current: true` |
| `DELETE` | `/api/me/trusted-devices` | viewer | Revoke all own trusted devices and clear the `pw_trusted` cookie |
| `DELETE` | `/api/me/trusted-devices/{id}` | viewer | Revoke one trusted device; clears `pw_trusted` cookie if the request matches the current device |
| `PATCH` | `/api/me/totp/remember-hours` | viewer | Set personal default remember duration `{hours: 9}` (0–720; 0 = always prompt TOTP) |

### User groups

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/groups` | viewer | List all groups with `member_count` |
| `POST` | `/api/group` | admin | Create group `{name, description}` |
| `PATCH` | `/api/group/{id}` | admin | Update group name / description |
| `DELETE` | `/api/group/{id}` | admin | Delete group; members are unassigned |
| `PUT` | `/api/group/{id}/members` | admin | Replace member list `{usernames: [...]}` |
| `POST` | `/api/user/group/import_ldap` | admin | Bulk-import LDAP groups `{groups: [{dn, cn, description, default_role}]}` — idempotent (skips existing DNs) → `{ok, imported, skipped, groups}` |

---

## Diagnostics

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/diagnostics/snapshot` | admin | System health snapshot — CPU / memory / disk / network / DB state |
| `GET` | `/api/diagnostics/db-stats` | admin | Row counts, table sizes, queue depths |
| `POST` | `/api/diagnostics/probe` | admin | One-off probe `{type, host, …}` — run a sensor probe without saving |
| `POST` | `/api/diagnostics/action/{name}` | admin | Trigger maintenance actions (clear caches, force flush, run VACUUM, etc.) |
| `GET` | `/api/diagnostics/bundle` | admin | Download a support bundle (logs + config + DB stats) for ticket attachments |

---

## Audit & logs

| Method | Path | Min role | Description |
|--------|------|----------|-------------|
| `GET` | `/api/audit` | admin | Paginated audit log — actor, IP, action, target, detail |
| `GET` | `/api/logs/{name}` | admin | Tail rotating log files (`main`, `sensors`) |
| `GET` | `/api/log-badge` | viewer | Recent error / warning counts for the topbar badge |
