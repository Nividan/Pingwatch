# Migration Notes — v1.0 UI Redesign

Tracks the visual refresh of the PingWatch SPA against the design bundle exported from claude.ai/design (locally extracted to `design/`, git-ignored).

**Version bump:** `0.9.7` → `1.0.0` (frontend-only — backend has one small additive change in Phase 4a)

**Plan file (local):** `C:\Users\Niv\.claude\plans\fetch-this-design-file-glimmering-dream.md`

## Hard constraints (do not violate)

1. **Backend frozen** except for Phase 4a (sessions endpoint). No other route, schema, or worker changes.
2. **Vanilla JS only** — no React/Vue, no build step, no `node_modules`, no CDN-runtime dependencies. Self-hosted fonts in `frontend/fonts/` stay.
3. **JSON contracts at `/api/*` are immutable** for this redesign (other than the additive Phase 4a endpoint).
4. **localStorage keys preserved**: `pw_theme`, `pw_tab`, `pw-dev-view`, `pw_page_size`, `pw_evt_subtab`, `pw_evt_inner_tab`, `pw_evt_filter`, `pw_evt_view`, `pw_evt_collapse`, `pw_logs_prefs`, `logBadgeSeen`, `pw_form_*`.
5. **New localStorage keys (additive)**: `pw_density`, `pw_dash_layout`, `pw_dev_layout`, `pw_evt_layout`. Default to `compact` for density.
6. **No hash routes added** — navigation stays via `switchMainTab(id)` from `app.js`.
7. **RBAC class hooks preserved**: `.rbac-admin`, `.rbac-op`, `.rbac-operator`.
8. **Theme contract preserved**: `<html data-theme="dark|light">` driven by `localStorage.pw_theme`, synced via `PATCH /api/me/theme`.
9. **Per memory `feedback_cross_platform`**: any new code runs on Windows + Linux.
10. **Per memory `feedback_dual_backend_pattern`**: any DB change implements both SQLite + PG paths.
11. **Per memory `feedback_error_messages`**: never leak `str(e)` to clients; log server-side, return generic message.
12. **Per memory `feedback_sqlite_try_finally`**: every `sqlite3.connect()` wraps in `try/finally con.close()`.

## Preserved DOM IDs (queried by JS — must stay byte-identical)

**Topbar / health / badges:**
`tbVer`, `healthBar`, `hb-bar-wrap`, `hb-bar-fill`, `hb-pct`, `hb-label`, `hb-pills`, `hb-up`, `hb-dn`, `hb-wn`, `hb-devices-sep`, `hb-devices-lbl`, `hb-spark-sep`, `hb-spark-lbl`, `hb-trend-arrow`, `hb-spark`, `badgeCrit`, `badgeCritCnt`, `badgeWarn`, `badgeWarnCnt`, `badgeAck`, `badgeAckCnt`, `badgeMuted`, `badgeMutedCnt`, `logBadge`, `logBadgeCnt`.

**User dropdown:**
`tb-user`, `usrDd`, `usrDdBtn`, `usrDdMenu`, `usr-dd-name`, `usr-dd-badge`, `usrThemeBtn`.

**Tabs / views:**
`tabDashboard`, `tabDevices`, `tabEvents`, `tabMap`, `tabBackups`, `tabIpam`, `tabReports`, `tabLogs`, `dashboardView`, `eventsView`, `mapView`, `backupsView`, `ipamView`, `reportsView`, `logsView`, `mainTabs`, `dpanels`, `devActBar`, `dw-tab-bar`, `dw-grid`, `evtList`, `emptyMain`, `map-frame`, `dev-ctx-menu`.

**Auth / login / splash / banner / toast:**
`login-screen`, `login-user`, `login-pass`, `login-btn`, `login-err`, `sso-methods`, `sso-divider`, `cbn`, `pw-splash`, `pw-splash-msg`, `toast`.

## Backend changes — Phase 4a spec (the only backend touch)

### Need

The new User menu has an "Active sessions" sub-modal listing every active token for the current user, with per-row revoke + "Sign out all other sessions" action.

### Endpoints

```
GET    /api/me/sessions
       → 200 [{id, current, device, ip, user_agent, signed_in_at, last_active, expires_at}, ...]

DELETE /api/me/sessions/{id}
       → 204 if revoked
       → 404 if not theirs (don't leak existence with 403)
       → 400 if trying to revoke own current session (use POST /api/logout instead)

DELETE /api/me/sessions
       → 204, revokes all sessions of current user EXCEPT the requesting one
       → returns count revoked in optional response body
```

### Implementation locations

- New handlers in [routes/auth.py](routes/auth.py) — alongside the existing trusted-devices block (line ~811). Follow the same dispatch pattern (`if path == "/api/me/sessions" and method == "GET": ...`).
- DB reads: the session-token table that `auth.py` already maintains. Per CLAUDE.md: "session tokens (SHA-256 in DB)".
- If the existing schema lacks `device` / `user_agent` / `last_active` columns: add via idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in [db/core.py](db/core.py) (SQLite path) and [db/pg_schema.py](db/pg_schema.py) (PG path). Both must land in the same commit per `feedback_dual_backend_pattern`.
- Login handler (in `auth.py`) — when persisting a new session token, capture `User-Agent` header (truncate to 255 chars) and remote IP. `last_active` updates on every authed request (cheap UPDATE keyed on session-hash).
- `current` flag = token-hash of the authenticated request matches the row's hash.

### Verification

```bash
# 1. List own sessions
curl -s -b cookies.txt http://localhost:7070/api/me/sessions | jq

# 2. Revoke a non-current session — next request from that token must 401
curl -X DELETE -b cookies.txt http://localhost:7070/api/me/sessions/42
curl -b OTHER_cookies.txt http://localhost:7070/api/me   # → 401

# 3. Cross-user revoke must 404 (not 403)
curl -X DELETE -b cookies.txt http://localhost:7070/api/me/sessions/ANOTHER_USERS_ID
# → 404 Not Found

# 4. Revoke all-others
curl -X DELETE -b cookies.txt http://localhost:7070/api/me/sessions
```

### Backwards compatibility

- Pre-1.0 clients never call these endpoints — purely additive.
- Existing sessions on upgrade: any pre-existing token row with NULL `device` / `user_agent` shows `"Unknown device"` in the UI; doesn't break the row.
- Existing `POST /api/logout` continues to revoke the current session only.

## Backend changes — Phase 4c spec (backups page enrichment)

### Need

The redesigned Backups view ([frontend/backups.js](frontend/backups.js)) wants two pieces of metadata per device that the existing `/api/backups` payload doesn't carry:

1. **14-day history strip** — a 14-element array of per-day status (`ok` / `fail` / `none`) so the strip-chart cell can paint real activity instead of a placeholder.
2. **Diff since last successful** — line-count delta between the latest successful run and the run immediately preceding it. Surfaced as the "Diff since" column ("no changes" or "N lines changed").

Both can be derived from the existing `backup_runs` table — no new table needed.

### Schema additions

One new column on `backup_runs`, idempotent migration in both DB paths:

```sql
ALTER TABLE backup_runs ADD COLUMN diff_lines INTEGER DEFAULT NULL
```

- SQLite: try/except wrapping the ALTER in [db/core.py](db/core.py) alongside the existing pattern around line 519.
- PG: `ADD COLUMN IF NOT EXISTS` in [db/pg_schema.py](db/pg_schema.py).
- `CREATE TABLE` blocks updated so fresh installs get the column too.
- NULL on legacy rows is meaningful: "we don't know" — UI renders em-dash. New runs after the migration land with an integer.

### Engine change

[backup/engine.py](backup/engine.py) — when persisting a successful run, look up the immediately-previous *successful* run's `config` for the same `did` and compute the unified-diff line count (added + removed). Store the integer on the new row.

- Use `difflib.unified_diff` (stdlib — no new dep). Count lines starting with `+` or `-` excluding `+++` / `---` headers.
- Skip the computation when:
  - No previous successful run exists → `diff_lines = NULL` (first backup)
  - The current run is a failure → leave `diff_lines = NULL`
  - Configs are byte-identical → `diff_lines = 0`
- Computation cost: O(N) on config size. Typical network device configs are 10-100 KB → milliseconds. Done after the SSH grab on a background thread that's already off the request path.

### `db_save_backup_run` change

Accept an optional `diff_lines` in the `result` dict and write it into the INSERT. Falls back to `NULL` when not provided (preserves the old caller contract).

### `db_get_backup_list` change

Two new keys per device in the response:

```jsonc
{
  "did": "...",
  // ... existing fields ...
  "last_diff_lines": 38,           // or null when unknown / first backup
  "strip_14d": ["ok","ok","none","fail","ok", ... 14 entries]
}
```

Compute both with the same single extra query per call (the existing function already does 3 queries — we add a 4th):

```sql
SELECT did, ts, success
FROM backup_runs
WHERE ts >= ?                       -- 14 days ago, ISO format
ORDER BY ts ASC
```

Then in Python, bucket each row into `(did, date)` and reduce to `ok` (any success that day) / `fail` (only failures that day) / `none` (no row for that day). Walk the last 14 calendar days in UTC to emit the array in oldest→newest order.

`last_diff_lines` is read straight off the latest-run map that's already built — no extra query, just include `diff_lines` in the existing `SELECT id, ts, success, size_bytes, error_msg` query.

### Frontend changes

[frontend/backups.js](frontend/backups.js):

- Replace the `_bkStripPlaceholder()` placeholder with one that reads `dev.strip_14d` and paints 14 bars colored by status. Missing/short array falls back to the placeholder pattern.
- Replace the diff-since em-dash cell with:
  - `dev.last_diff_lines === 0` → `<span class="muted">no changes</span>`
  - `dev.last_diff_lines > 0` → clickable text linking into the diff modal already implemented at `_bkOpenDiff(did)`
  - `null/undefined` → em-dash

### Verification

```bash
# After running a backup at least twice, the new fields appear:
curl -s -b cookies.txt http://localhost:7070/api/backups | jq '.devices[0] | {did, last_diff_lines, strip_14d}'

# 14-day strip is always exactly 14 entries (or absent if no runs at all):
curl -s -b cookies.txt http://localhost:7070/api/backups | jq '.devices[] | .strip_14d | length' | sort -u
```

Manual UI check: open Backups tab, the strip column shows actual per-day bars, the diff column shows "no changes" / "N lines changed" or em-dash for legacy/first-time devices.

### Backwards compatibility

- Pre-1.0 clients ignore the two extra fields — additive.
- Legacy rows have `diff_lines = NULL` until their next successful run lands. No backfill needed; the next scheduled sweep populates them naturally.
- Existing `db_save_backup_run` callers that don't pass `diff_lines` continue to work — column just stays NULL.

## Backend changes — Phase 4d spec (IPAM site grouping)

### Need

The redesigned IPAM page ([frontend/ipam.js](frontend/ipam.js)) shows subnets in a left sidebar. With more than a handful of subnets the flat list gets unwieldy — users want to group subnets by location/site/zone (e.g. "NYC", "SJC", "DC1") and collapse groups they're not currently looking at.

The existing `ipam_subnets` table only carries `cidr` + `name` + `dns_server` + auto-discovery flags — no concept of a site. We add one optional column rather than parsing the name.

### Schema additions

One new column on `ipam_subnets`, idempotent migration in both DB paths:

```sql
ALTER TABLE ipam_subnets ADD COLUMN site TEXT DEFAULT ''
```

- SQLite: try/except ALTER in [db/core.py](db/core.py) alongside the existing additive-column block.
- PG: `ADD COLUMN IF NOT EXISTS` in [db/pg_schema.py](db/pg_schema.py).
- Fresh-install `CREATE TABLE` blocks updated so new installs get the column.
- Empty-string default = "ungrouped" — UI renders these under an "Other" / "Ungrouped" group.

### DB layer ([db/ipam.py](db/ipam.py))

- Add `site` to `_SUBNET_COLS` (`COALESCE(site, '') AS site`).
- Surface `site` in `_row_to_subnet_pg` and the SQLite row mapper.
- `db_add_subnet(cidr, name, user, site='')` accepts the optional site.
- `db_update_subnet(...)` extended to accept site updates (same pattern as `dns_server`).

### API ([routes/ipam.py](routes/ipam.py))

- `POST /api/ipam/subnets` — accepts `{cidr, name, site}` (site optional, empty string default).
- `PATCH /api/ipam/subnets/{id}` — accepts `site` alongside `name`/`dns_server`/`auto_discover`.
- `GET /api/ipam/subnets` — payload already includes whatever columns `_SUBNET_COLS` selects; `site` rides along automatically.

No new endpoint needed.

### Frontend changes ([frontend/ipam.js](frontend/ipam.js), [frontend/style.css](frontend/style.css))

- **Add Subnet modal** gets a free-form Site input + `<datalist>` of existing sites for autocomplete. Trimmed + uppercased on save for consistency.
- **Edit Subnet modal** same input, pre-populated from the current value.
- **Sidebar** groups subnets by `site` (case-insensitive). Each group renders a collapsible header (chevron + name + count); clicking toggles. Subnets without a site land under "Ungrouped".
- Collapse state persisted to `pw_ipam_grp_collapsed` (localStorage; array of collapsed site names).
- Subnet filter still works — when a filter narrows the list, groups with zero visible cards are hidden entirely.

### Verification

```bash
# 1. Migration safety — re-running boot doesn't fail
curl -s -b cookies.txt http://localhost:7070/api/ipam/subnets | jq '.subnets[0] | keys'
# should include "site"

# 2. Create + read back
curl -s -b cookies.txt -H 'Content-Type: application/json' \
     -X POST -d '{"cidr":"10.99.0.0/24","name":"test-net","site":"DC1"}' \
     http://localhost:7070/api/ipam/subnets
curl -s -b cookies.txt http://localhost:7070/api/ipam/subnets | jq '.subnets[] | select(.cidr=="10.99.0.0/24")'

# 3. Update site via PATCH
curl -s -b cookies.txt -H 'Content-Type: application/json' \
     -X PATCH -d '{"site":"DC2"}' \
     http://localhost:7070/api/ipam/subnets/<id>
```

Manual UI check: open IPAM tab, sidebar shows collapsible groups; click a header to toggle; filter shrinks groups; Add Subnet modal accepts a Site value that surfaces in the right group.

### Backwards compatibility

- Pre-1.0 clients ignore the new field — additive.
- Legacy subnet rows have `site = ''` until a user edits them, in which case they land under "Ungrouped" — never lost.
- API callers that don't send `site` in POST/PATCH still work — the field stays empty.

## Backend changes — Phase 4e spec (Site hierarchy on devices — Phase A only)

### Need

PingWatch devices today live in a single flat `group` field (`devices.grp`, free-text). For enterprise-scale deployments we want a parent **Site** axis (Site → Group → Device) so:
- The Devices tab can nest groups under sites for clearer visual organization.
- Alert profiles can fire at the Site level (e.g. NOC for HQ-Site) additively with Group-level profiles (e.g. server team for Servers) — see Phase C.
- Auto-discovery can auto-tag new devices with the subnet's site — see Phase E.

This entry covers **Phase A only** (data model + autocomplete endpoint, zero user-visible change). Phases B/C/D/E ship in follow-up commits and will get their own entries.

### Schema additions

One new column on `devices`, idempotent migration in both DB paths:

```sql
ALTER TABLE devices ADD COLUMN site TEXT DEFAULT ''
```

- SQLite: try/except ALTER in [db/core.py](db/core.py) right after the existing `ipam_subnets.site` migration.
- PG: `ADD COLUMN IF NOT EXISTS` in [db/pg_schema.py](db/pg_schema.py) plus column in fresh-install `CREATE TABLE`.
- Empty-string default = "Unsited" — Phase B UI renders these under an "Unsited" bucket (last in sort order).

### State + persistence ([core/state.py](core/state.py), [db/persistence.py](db/persistence.py))

- `Device.__init__(..., site="")` — new kwarg, defaults to empty string.
- `Device.to_dict()` exposes `"site"` so `/api/devices` returns it.
- `dev_rows` tuple (used by both PG `execute_values` and SQLite `executemany`) gains `site` between `grp` and `_sid_ctr`.
- PG INSERT column list adds `,site`; ON CONFLICT clause adds `site=EXCLUDED.site`.
- SQLite INSERT OR REPLACE column list adds `,site`; placeholder count 22 → 23.
- Both load paths (`_pg_load`, SQLite `db_load`) append `COALESCE(site,'') AS site` at the END of the SELECT — positional reads of earlier columns unchanged. New column accessed via `row[22]` or via the tuple unpack with `site` as the last element.

### API ([routes/devices.py](routes/devices.py), [routes/ipam.py](routes/ipam.py))

- `PATCH /api/device/{did}` whitelists `site` alongside `group` (max 80 chars).
- `POST /api/devices/bulk` action=`move` accepts `site` independently of `group` — at least one required. Empty string is a valid `site` value meaning "Unsited" (clears the field).
- **New endpoint** `GET /api/sites` (viewer-level): returns `{"sites": ["DR-Site-2","HQ", ...]}`, case-insensitively sorted UNION of distinct non-empty values from `ipam_subnets.site` and `devices.site`. Lives in [routes/ipam.py](routes/ipam.py) since IPAM was the original home of the `site` concept.

### Verification

```bash
# /api/sites returns IPAM sites (devices still empty)
curl -s http://localhost:7070/api/sites -b cookies.txt
# {"sites":["DR-Site-2","HQ"]}

# PATCH a device with a site, GET it back
curl -X PATCH http://localhost:7070/api/devices/d3 -b cookies.txt \
     -H "Content-Type: application/json" -d '{"site":"HQ"}'
curl -s http://localhost:7070/api/devices/d3 -b cookies.txt | grep site
# "site": "HQ"

# /api/sites now reflects the device assignment too
curl -s http://localhost:7070/api/sites -b cookies.txt
# {"sites":["DR-Site-2","HQ"]}

# Bulk move 3 devices to a new site without touching group
curl -X POST http://localhost:7070/api/devices/bulk -b cookies.txt \
     -H "Content-Type: application/json" \
     -d '{"action":"move","device_ids":["d3","d5","d8"],"site":"Branch-3"}'
```

### Backwards compatibility

- Pre-1.0 clients ignore the new `site` field on GET — purely additive.
- Existing `devices.grp` values untouched; only the new column is added.
- API callers that don't send `site` in PATCH/bulk still work.
- Upgrade path: idempotent migration runs on next start; existing devices keep `site=''` until edited.

### From → to version

- Source: v1.0 (post-Phase 4d, IPAM site grouping shipped)
- Target: v1.0 (Phase A is intra-release; the full Site Hierarchy completes across Phases A–E, each a separate commit but all under v1.0)

## Deferred (NOT in this release)

| # | Need | Notes |
|---|------|-------|
| 2 | Notification stream for bell icon | Stub for v1.0 — bell either visual-only or piggybacks `/api/alert/events/active`. Real notifications inbox endpoint is v1.1 work. |
| 3 | Storage size estimate per retention | Compute client-side from constants in [frontend/forms-settings.js](frontend/forms-settings.js). No endpoint needed. |
| 4 | Topology snapshots / version diff | Map builder unchanged this cycle. Schema add + endpoints are v1.1+ work. |
| 5 | Topology vs monitored discrepancy | Same as #4 — out of scope. |
| 6 | drawio/Visio XML round-trip | Lower priority per design chat. |
| 7 | Command palette functionality (Ctrl+K) | Topbar input is visual stub for v1.0. Real search across devices/sensors/IPs is v1.1+. |

## Phase log (filled in as we commit)

- [ ] **Phase 0 — Audit & lockdown** — design bundle copied to `design/` (gitignored), baseline snapshot saved, this doc written.
- [ ] **Phase 1 — Design tokens & theme** — `:root` block in `style.css` replaced with refined token set; `[data-density]` overrides added; FOUC bootstrap extended.
- [ ] **Phase 2 — Shell** — topbar redesigned, icon rail replaces tab bar, `icons.js` added.
- [ ] **Phase 3 — Per-view migrations** — Dashboard, Devices, Events, Map (Live tab), IPAM, Alerting, Reports, Backups, Logs.
- [x] **Phase 4a — Sessions endpoint** — `GET/DELETE /api/me/sessions` added per spec above. Implementation:
  - Schema: 5 new columns added to `sessions` table (`ip`, `user_agent`, `device_label`, `created_at`, `last_active`) — idempotent migrations in [db/core.py](db/core.py) (SQLite) + [db/pg_schema.py](db/pg_schema.py) (PG).
  - Login capture: `_create_session()` and `auth_login()` in [core/auth.py](core/auth.py) accept optional `ip` / `user_agent` / `device_label` kwargs. The main `/api/login` and `/api/login/totp` paths in [routes/auth.py](routes/auth.py) thread these through. SSO/LDAP/RADIUS auto-provision paths leave them blank for now (UI shows "Unknown device") — these can be enhanced incrementally.
  - Helpers: `auth_list_user_sessions()`, `auth_revoke_session_by_id()`, `auth_revoke_other_user_sessions()` added to [core/auth.py](core/auth.py).
  - Endpoints in [routes/auth.py](routes/auth.py): `GET /api/me/sessions`, `DELETE /api/me/sessions/{token_hash}`, `DELETE /api/me/sessions`. `id` is the SHA-256 token-hash (matches the row's primary key).
  - **Single-session caveat**: the existing `_create_session()` does `DELETE FROM sessions WHERE username=?` before inserting, so each user can only have ONE active session at a time. The new endpoints work correctly — they'll just usually return 1 row. Removing the single-session enforcement is a separate decision (security trade-off) and out of scope for v1.0.
  - Verification: see "curl smoke test" examples in the v1.0 spec block above.
- [ ] **Phase 4b — Settings & User menu** — grouped Settings modal, new User menu with Sessions sub-modal.
- [ ] **Phase 5 — Tweaks, polish, verification** — density tweaks, layout variants, RBAC sanity, golden-path smoke test.

## Design bundle location

`design/` (local-only, gitignored). Contains:
- `pingwatch/README.md` — handoff bundle instructions
- `pingwatch/chats/chat1.md` — full design conversation (read for intent)
- `pingwatch/project/PingWatch.html` + `*.jsx` + `*.css` — the React+JSX prototype (visual spec only — do not load in production)
- `pingwatch/project/screenshots/` — reference visuals
- `baseline/style.css` + `baseline/index.html` — snapshot of the pre-redesign frontend for visual diff at the end

## How to use this document during implementation

1. Before each phase, re-read the relevant section.
2. After each phase, check the box in "Phase log" with the commit SHA.
3. If you find a backend assumption that conflicts with the design — stop, write it here under a new "Discovered gaps" section, and surface to the user. Do not invent endpoints.
4. After Phase 5, archive this doc into `CHANGELOG.md` under the v1.0.0 entry and delete it.
