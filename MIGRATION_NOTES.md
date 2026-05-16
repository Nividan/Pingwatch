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
