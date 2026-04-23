# 📋 PingWatch Changelog

Detailed implementation notes for every shipped feature. For the high-level roadmap and upcoming work see [ROADMAP.md](ROADMAP.md).

---

## 🛡 Audit hardening — post-0.9.5 security + resilience pass

Targeted follow-up to the 2026-04-02 audit covering the 428 commits shipped since. Every finding below was verified in code before fixing; five initial "candidates" from the automated scan turned out to already be handled correctly (sample-buffer drop-oldest, `Device._cached_status` + `invalidate_status()`, RADIUS route error handling, auto-discovery single-caller, alert_batcher per-item fallback) and were not modified.

- **postMessage origin checks (frontend)** — the second `message` listener in [frontend/map.js](Pingwatch/frontend/map.js) (pw_reload_pages handler) was missing the `e.origin !== window.location.origin` guard that its sibling at line 41 already uses; [frontend/theme.js](Pingwatch/frontend/theme.js) was posting the theme-change event to the same-origin map iframe with target `'*'`. Both now match the rest of the codebase — origin check on inbound, origin-pinned target on outbound
- **Error-string leakage to API clients** — `routes/monitoring.py` (VMware discover: 2 sites) and `routes/tls.py` (cert / key / PFX / CA parse: 9 sites) were returning `str(e)` / `f"…{e}"` to the client, exposing pyVmomi stack fragments, vCenter hostnames, `cryptography`-lib parse details. Every site now logs the full exception via `log.warning(...)` / `log.error(...)` and returns a generic string (`"VMware connection failed"`, `"Certificate format invalid"`, `"PFX parse failed (check password and file)"`, etc.), matching the rule documented in [CLAUDE.md](Pingwatch/CLAUDE.md#coding-conventions)
- **Probe hard-timeout guard** — `core/state.py::_run_once()` previously called `result = s.probe()` directly on the worker thread. Every probe type has its own socket/timeout, but a misbehaving stack (stuck DNS resolution, TLS handshake hanging mid-read) could outlive it and pin one of the 64 executor workers. Now wraps the call in a daemon `pw-probe-{did}-{sid}` thread and `join(timeout=(s.timeout or 5) + 3)`; if the join times out, the worker returns to the pool with `{"ok": False, "detail": "Probe exceeded hard timeout"}` and logs a WARN to `log_sensors`. The orphan probe thread continues to run and dies on its own internal timeout — a small transient cost vs. the worst case of losing the whole pool
- **Webhook dispatcher queue** — `core/state.py::_run_once()`'s flap-down path used to do `threading.Thread(target=_send_webhook, ...).start()` once per event. Under a flap storm (uplink drop, hundreds of sensors going DOWN together) that pattern could fork hundreds of threads within seconds. New module-level `_WEBHOOK_Q = queue.Queue(maxsize=100)` + single `pw-webhook` dispatcher thread (lazy-started on first call via `_enqueue_webhook()`): bounded concurrency, overload is observable (`Webhook queue full (cap=100), dropping event for ...` WARN), and the dispatcher thread lifetime matches the process
- **Alerting picker dropdown cleanup** — `frontend/alerting.js::_apPickerOpen()` appends `#_ap_picker_dd` to `document.body` and registers a `document`-level click listener. If the user opened the picker and then closed the profile modal (X button, Cancel, or backdrop) without clicking anywhere else, the dropdown stayed orphaned on body and the capture-phase listener remained registered. New `_apCloseProfModal()` wrapper cleans both before calling `closeM('alrt-prof-modal')`; all 5 close call-sites (backdrop / X / Cancel / post-save / reopen-guard) now funnel through it
- **PG partition DROP identifier quoting** — `db/samples.py::_clean_pg()` built its expired-partition `DROP TABLE IF EXISTS {...}` via f-string with the relname from `pg_class`. Input is PG-internal and therefore trusted, but the pattern deviated from the "all queries parameterised / identifiers via `psycopg2.sql`" discipline. Now uses `psycopg2.sql.SQL(...).format(sql.Identifier(part[1]))` for consistency
- **Files** — [frontend/map.js](Pingwatch/frontend/map.js) (origin guard on second message listener); [frontend/theme.js](Pingwatch/frontend/theme.js) (`'*'` → `window.location.origin`); [routes/monitoring.py](Pingwatch/routes/monitoring.py) (VMware RuntimeError + ConnectionError paths); [routes/tls.py](Pingwatch/routes/tls.py) (9 cert/key parse sites across install / install-signed / PFX / CA paths); [core/state.py](Pingwatch/core/state.py) (probe hard-timeout wrapper around `s.probe()`; new `_WEBHOOK_Q` + `_webhook_dispatcher_loop` + `_enqueue_webhook`; flap-down call-site switched to `_enqueue_webhook()`); [frontend/alerting.js](Pingwatch/frontend/alerting.js) (new `_apCloseProfModal()` + all 5 close call-sites); [db/samples.py](Pingwatch/db/samples.py) (`sql.Identifier` for partition drop)

---

## 📬 Alert batching + UI event collapse (v0.9.5 "Quiet Hours")

- **Problem:** when a switch dies, 12 sensors behind it all page at once — 12 emails, 12 webhook POSTs, 12 rows in the activity feed. Alert-storm fatigue is the #1 way operators stop trusting monitoring
- **Backend — notification batching.** New `monitoring/alert_batcher.py` singleton holds outbound email + webhook alerts for a short window and emits one combined notification per `(channel, destination, severity)` bucket. A burst of 12 downs → 1 batched "12 alerts (12 critical)" email. 12 recoveries → 1 batched recovery email. **Symmetric by design** — batching both sides means the spam doesn't shift from downs to recoveries
- **3 settings** in Settings → Alert Profiles → Notification Batching:
  - `alert_batch_enabled` (bool, default **on**) — master switch; off = pre-0.9.5 behaviour, immediate per-event sends
  - `alert_batch_window_s` (int, default **60**, range 5–3600) — how long to hold the first alert before flushing
  - `alert_batch_max_size` (int, default **20**, range 2–500) — flush early when a bucket fills
- **Fail-safe routing.** `alert_dispatchers.dispatch()` tries to enqueue email/webhook; on any batcher error (disabled, internal fault, enqueue failure) it falls straight through to the existing per-event sender. A bug in batching cannot silence alerts — same invariant as the existing `try/except` around `_DISPATCHERS[atype]`
- **Webhook batching is opt-in per template** via the new **Batch-aware receiver** checkbox in the webhook action-template editor. Default off — Slack/Teams/generic hooks expect one alert per POST and would break on array payloads. When on, the receiver gets `POST {count, severity_counts, severity_label, window_start_ts, window_end_ts, alerts: [ctx, ctx, ...]}` with an `X-PingWatch-Batch: 1` header
- **Syslog and browser SSE never batch** — syslog wants discrete RFC 5424 events downstream; browser is low-spam and user-visible. Both flow through `dispatch()` unchanged
- **Event records are never collapsed.** `alert_events`, `flap_log`, `sensor_err_log` keep one row per event regardless of batching — forensics and audit stay granular
- **Batched email template** — `send_rule_email_batch()` renders a wider layout (760 px) with severity-breakdown banner (`12 alerts (10 critical, 2 warning)`), intro blurb explaining why it's a digest, and a striped table sorted critical → warning → info → recovery. Per-row: severity pill, device, sensor, event type, truncated detail (180 chars), local-timezone timestamp. Plain-text fallback included for text-only clients
- **Frontend — UI event collapse.** New `Collapse related` filter-bar toggle in Events view (default on, persisted in `localStorage['pw_evt_collapse']`) folds ≥3 related events within a 30-second window into one expandable `<details>` row:
  - **Same-sensor flapping** — `sensor-X flapped 5× in 28s`
  - **Device outage** — `switch-A: 5 sensors went down within 22s`
  - Traps stay individual (one-shot events, not a burst)
  - Group rows show worst-severity pill + label + count badge + time; click the chevron to expand into the inner event list
  - Works in both card and table view modes; uses native `<details>/<summary>` — zero custom JS for the expand/collapse
- **Persistence across restart** — in-memory queue, not persisted. On graceful shutdown an `atexit` handler drains pending buckets synchronously. Unclean crashes lose any in-flight batches (documented limitation); the un-batched events are already in `alert_events` / `flap_log` for recovery
- **6-scenario smoke test (isolated)** — batching disabled → passthrough; 3-event burst → 1 batched email; webhook without `batch_aware` → immediate; webhook with `batch_aware` → 1 batched POST; 5 events → max-size early flush; solo event → per-event format (unchanged). All green
- **Version bump** `0.9.3 → 0.9.5` · version name `"Autonomous Discovery" → "Quiet Hours"`
- **Files** — **NEW** [monitoring/alert_batcher.py](Pingwatch/monitoring/alert_batcher.py) (singleton + daemon flusher + atexit drain); [monitoring/alert_dispatchers.py](Pingwatch/monitoring/alert_dispatchers.py) (router now tries batcher-then-fallback for email/webhook; `dispatch_email_batch` + `dispatch_webhook_batch`); [monitoring/smtp_alert.py](Pingwatch/monitoring/smtp_alert.py) (`_build_batch_html` + `send_rule_email_batch`); [routes/settings.py](Pingwatch/routes/settings.py) (3 new settings in GET; int loop + explicit bool handler in PATCH); [frontend/forms-settings.js](Pingwatch/frontend/forms-settings.js) (new "Notification Batching" card at top of Alert Profiles tab + `_saveAlertBatching()` + footer Save button); [frontend/alerting.js](Pingwatch/frontend/alerting.js) (`Batch-aware receiver` checkbox on webhook editor); [frontend/events.js](Pingwatch/frontend/events.js) (`_collapseEvents` + `_buildEvtGroupCard` + `_buildEvtGroupTableRow` + `_onEvtCollapseToggle` + toggle sync); [frontend/index.html](Pingwatch/frontend/index.html) (Collapse-related checkbox in filter bar); [frontend/style.css](Pingwatch/frontend/style.css) (`.evt-group*` styles + `.evt-collapse-tog`); [core/app_state.py](Pingwatch/core/app_state.py) (version bump)

---

## 🔧 Diagnostics tab — operator & support console

- New dedicated **🔧 Diagnostics** tab in Settings consolidating seven operator/support panels in one place. Replaces the scattered "Debug Mode in Retention + auth badges in Integrations + no way to see sample-buffer pressure" status quo
- **System Overview** — version / uptime / Python + platform / hostname / CPU+RAM+disk (via psutil) / worker count / scheduler heap depth / SSE listener count / sample-buffer fill / DB writer queue depth / per-DB on-disk size. One `GET /api/diagnostics/snapshot` round-trip; manual ↻ Refresh button
- **Database Health** — per-table row counts for `sensor_samples`, `flap_log`, `sensor_err_log`, `snmp_traps`, `audit_log`, `alert_events`, `devices`, `sensors`, `users`; per-table on-disk size (PG only, via `pg_total_relation_size()`); last VACUUM timestamp; **Run VACUUM** and **Backup DB now** buttons
- **Health Checks** — consolidated test panel for LDAP / RADIUS / SAML / OIDC / SMTP / Syslog / DB Backup remote / **NTP** / **DNS resolver** with status badge + last-checked + per-row Test button + master **Test All**. Additive — existing Test buttons in the Integrations / SMTP / etc. panels stay where they are
- **Probe from Server** — interactive ping / TCP / HTTP / DNS / TLS debug tool ("why can't PingWatch see 10.0.0.5?") that wraps `monitoring/probes.py` verbatim. Admin-only; no SSRF restrictions because probing internal targets is the whole point
- **Recent Errors** — two-pane feed: last 50 ERROR+ lines from `pingwatch.log` and last 50 rows from `sensor_err_log`, newest first
- **Maintenance** — Debug Mode toggle (moved here from Retention) + one-shot buttons: Refresh OIDC discovery, Refresh all auth backends, Run auto-discovery now, Force VACUUM, Clear caches. Each action writes a `diagnostics_*` audit entry
- **Support Bundle** — one-click **Download diagnostics bundle (.zip)** containing logs (current file, tail-capped at 10 MB), `snapshot.json`, `db_stats.json`, `recent_errors.json`, `settings_sanitized.json`, and a `manifest.json` documenting the redaction policy. Deny-list sanitizer scrubs any key matching `*_enc` / `*secret*` / `*_pass*` / `fernet_key` / `*cert_pem*` / `*key_pem*` / `webhook_*` / `*token*` / `*client_secret*`, and a self-defence value scan replaces `-----BEGIN` and `gAAAAA` markers even if a key slipped through
- **NTP check** — inline SNTP v4 client (no new dep; ~30 lines of `socket` + `struct` per RFC 5905) queries the configured `ntp_server` (default `pool.ntp.org`) and reports local-vs-server drift. Thresholds: ok < 5 s, warn 5–60 s, error > 60 s or invalid stratum. Intended to pair with a planned Networking-tab `ntp_server` setting
- **DNS resolver check** — resolves `diag_dns_test_host` (default `pingwatch.mokedbs.com`) against the system resolver, or against a custom resolver when `dns_server` is configured. Uses `dnspython` (already a dep)
- **8 new endpoints** (all admin-only, all audit-logged): `GET /api/diagnostics/snapshot`, `GET /api/diagnostics/db-stats`, `GET /api/diagnostics/recent-errors`, `POST /api/diagnostics/probe`, `POST /api/diagnostics/action/{vacuum|clear-caches|refresh-auth}`, `POST /api/diagnostics/test/ntp`, `POST /api/diagnostics/test/dns`, `GET /api/diagnostics/bundle`
- **Runtime-snapshot helper** added to `core/state.py::MonitorState.get_runtime_snapshot()` — cheap read-only peek at scheduler heap / tombstones / SSE list / executor max. Used by the snapshot endpoint and the support bundle
- **Debug Mode relocated** — removed from the Retention tab's old Diagnostics block; now lives in Maintenance on the new Diagnostics tab. Same `_saveDebugMode()` immediate-save handler, no setting-key change
- **Files** — **NEW** [routes/diagnostics.py](Pingwatch/routes/diagnostics.py); [core/config.py](Pingwatch/core/config.py) (8 new route regexes); [core/state.py](Pingwatch/core/state.py) (`get_runtime_snapshot()`); [server.py](Pingwatch/server.py) (module registration in GET + POST dispatch); [frontend/forms-settings.js](Pingwatch/frontend/forms-settings.js) (sidebar button + tab body + ~400 lines of section renderers + tab-init hook; removed Diagnostics block from Retention); [frontend/style.css](Pingwatch/frontend/style.css) (`.diag-card`, `.diag-badge-*`, probe tool + recent-errors grid; responsive collapse under 800 px)

---

## Auto-Discovery activity polish — bounded scroll, timeout audit, large-subnet warning

- **Recent activity pane** bounded to a 320 px scroll pocket with sticky header so the Save / Close buttons stay visible on systems with months of scheduler ticks
- **Filter bar** — event-type dropdown (seeded from `_AD_ACTION_LABELS`), actor substring match, free-text search against target + detail; 150 ms debounce on the text inputs; Clear button; `Showing N of M` counter
- **Scan-timeout audit trail** — when a subnet's scan exceeds `auto_discover_scan_deadline_s` (default 300 s), an `auto_discovery_scan_timeout` audit row is written **only on the first timeout in a streak** (cleared on the next successful scan). Prevents a /16 timing out every tick from drowning the audit log; the log warning now also tells the admin what to do ("raise `auto_discover_scan_deadline_s` in Settings → Retention or split the subnet"). New `⏱ Scan timed out` label in the activity pane's event-type dropdown
- **IPAM edit modal — large-subnet warning** — when Auto-Discover is enabled on an IPv4 prefix ≤ /20 (4096+ hosts), a warn-themed block appears with the host count and suggests raising the scan deadline or splitting the subnet. Catches the silent-failure footgun at configuration time instead of after-the-fact
- **Files** — [monitoring/auto_discovery.py](Pingwatch/monitoring/auto_discovery.py) (`_subnet_timeout_flag` streak state, audit emit, improved log msg); [frontend/forms-settings.js](Pingwatch/frontend/forms-settings.js) (filter bar markup + `_adAct*` state/renderers + new label); [frontend/style.css](Pingwatch/frontend/style.css) (`#disc-activity-wrap`, `.disc-act-filter`, sticky `thead`); [frontend/ipam.js](Pingwatch/frontend/ipam.js) (`#ipam-ad-size-warn` block + `_syncConfirm()` toggle)

---

## Event log LRU — never evict acknowledged flaps

- **Bug:** long-lived ACKed events (e.g. a FortiGate "Expired license" flap 613 days old) could be silently deleted by the `flap_log` trim query when background sensor churn pushed the row count past `max_flap_entries`. Events ACKed but not resolved represent known-but-monitored conditions — losing them broke the ACK → auto-clear workflow
- **Fix:** the trim-on-insert `DELETE` query in `db/events.py::db_log_flap()` now only considers rows with `ack_state='resolved'` — both for the `WHERE` and for the inner `LIMIT` subquery. Active + acknowledged flaps are kept indefinitely; only fully resolved history is bounded by the cap
- **Default bump** — `max_flap_entries` default lifted 500 → 2000 to match the broader Retention-tab defaults landed in the Retention settings refresh
- **Files** — [db/events.py](Pingwatch/db/events.py) (dual-backend trim query rewrite)

---

## Retention & Performance settings tab

- New dedicated **🗃️ Retention** tab in Settings — consolidates deployment-sizing knobs that previously lived scattered across General (Data Retention, Event & History Limits, Debug Mode) and newly exposes log-file rotation, the audit DB cap, and six per-feature performance tunables that used to be hardcoded
- **Five sections** — Database Retention (raw / 5-min / 1-hour tier days), Event & Trap Limits (UI display cap + DB caps for flaps/traps/audit), Log Files (size + backup count per log file; ⚠ Restart required badge next to the heading), Performance & Limits (SMTP/PG timeouts, auto-discovery scan deadline, SFTP checksum cap, import payload cap — all live-apply), Diagnostics (Debug Mode toggle)
- **Default bumps for fresh installs** — `max_flaps_display` 20→50, `max_flap_entries` 500→2000, `max_trap_entries` 500→2000; audit DB cap raised from a hardcoded `LIMIT 2000` to a configurable `audit_trim_cap` defaulting to 50 000. Existing installs keep their current values (seed defaults use `INSERT OR IGNORE` / `ON CONFLICT DO NOTHING`)
- **Audit log file retention** — switched from `RotatingFileHandler` (5 MB × 5 backups) to `TimedRotatingFileHandler(when="midnight", backupCount=365)` for compliance-friendly daily rotation with 1 year of history
- **Main app log file** — `pingwatch.log` backup count raised 5 → 14 (~30 days worst-case coverage at 10 MB each, 140 MB total cap)
- **Live handler swap on startup** — new `core/logger.py::reconfigure_from_settings()` called from `server.py` right after the settings cache loads; for each of the four file handlers (`_fh` / `_sh` / `_ah` / `_bkh`) creates a fresh handler with the user-configured sizes/retention, swaps it in, and closes the old one. No-op when values match import-time defaults so clean startups don't churn file handles
- **Parameterised hardcoded constants** that previously required source edits — `smtp_timeout_s` (monitoring/smtp_alert.py), `pg_statement_timeout_s` + `pg_pool_acquire_timeout_s` (db/pg_pool.py), `auto_discover_scan_deadline_s` (monitoring/auto_discovery.py), `sftp_checksum_max_mb` (monitoring/probes.py), `import_max_payload_mb` (routes/imports.py). All read from the settings cache at call time — changes apply live without restart
- **14 new `app_settings` keys** — 8 for log rotation + audit cap (restart-required for the 7 rotation knobs; `audit_trim_cap` is live), 6 for per-feature tunables (all live)
- **Validation bounds in `PATCH /api/settings`** — every new key has a min/max clamp so a typo can't set `log_main_max_mb=-1` or `smtp_timeout_s=86400`
- **Files** — [db/core.py](Pingwatch/db/core.py) + [db/pg_schema.py](Pingwatch/db/pg_schema.py) (14 new seed defaults); [routes/settings.py](Pingwatch/routes/settings.py) (GET + PATCH plumbing with bounded validators); [db/audit.py](Pingwatch/db/audit.py) (trim cap read from `audit_trim_cap`); [core/logger.py](Pingwatch/core/logger.py) (`TimedRotatingFileHandler` for audit + `reconfigure_from_settings()`); [server.py](Pingwatch/server.py) (calls reconfigure after settings load); [monitoring/smtp_alert.py](Pingwatch/monitoring/smtp_alert.py) + [db/pg_pool.py](Pingwatch/db/pg_pool.py) + [monitoring/auto_discovery.py](Pingwatch/monitoring/auto_discovery.py) + [monitoring/probes.py](Pingwatch/monitoring/probes.py) + [routes/imports.py](Pingwatch/routes/imports.py) (parameterised reads); [frontend/forms-settings.js](Pingwatch/frontend/forms-settings.js) (new `_buildSettingsTab_retention` + `_saveRetention` handler; removed Data Retention / Event & History Limits / Logging sections from General)

---

## PBKDF2 cost upgrade (200k → 600k) with transparent hash migration

- **Security:** raised PBKDF2-SHA256 iteration count from 200 000 to 600 000 to match the OWASP 2023 minimum for PBKDF2-SHA256. Not exposed as a user setting — letting admins weaken it is a footgun
- **Self-describing hash format** — stored hashes are now `"iters:salt:hex"` instead of `"salt:hex"`. Future cost bumps won't require another migration because `_verify_pw()` reads the iteration count from the stored value
- **Backwards-compatible verify** — legacy 2-part `"salt:hex"` entries continue to verify at 200k. No forced password reset; no lockouts after upgrade
- **Transparent upgrade on login** — new `_maybe_rehash(username, password, stored)` in `core/auth.py`: after a successful `_verify_pw()` in the local-login path, if the stored hash is legacy-format or below the current iteration target, the password is re-hashed at 600k and the `users.pw_hash` row is UPDATE'd. Failures are non-fatal — login still proceeds, next login retries the upgrade
- **Scope** — local auth only. LDAP / RADIUS / SAML / OIDC don't store passwords locally (pw_hash is `__ldap__` / `__radius__` / `__saml__` / `__oidc__`) so they're unaffected
- **Files** — [core/auth.py](Pingwatch/core/auth.py) — new `_PBKDF2_ITERATIONS` / `_PBKDF2_LEGACY_ITERATIONS` constants, `_hash_pw()` emits 3-part format, `_verify_pw()` handles both 2-part and 3-part, `_needs_rehash()` + `_maybe_rehash()` hooked into `auth_login()`

---

## NTM live map — group-change orphan fix

- **Bug:** after changing a device's group from Edit Device, the device occasionally rendered outside every group rect on the NTM live map. Reload-after-change reproduced it; affected layouts had two overlapping group rects
- **Cause #1 — `fetch({keepalive:true})` 64 KB in-flight cap** — `PATCH /api/settings/pw_node_overrides` + `pw_group_overrides` bodies piled up and silently stayed "Pending" forever (visible as multiple 0.0 KB pending requests in DevTools → Network). Fix: removed `keepalive: true` from `_pwSave`
- **Cause #2 — stale override coordinates** — when a device's group changed, its persisted `(x, y)` (relative to its old group rect) would land outside the new group rect, or fall inside a sibling group when two rects overlapped
- **Orphan pre-pass** — new `_nodeInsideGroup(ovr, nodeType, groupRect)` helper; inserted a pass in `calcPwLayout()` between group-building and slot-precompute: for every device, if its centre is outside its own group's rect OR inside any other group's rect, delete `ovr.x / ovr.y` so the Phase-3 placer re-slots it fresh into the correct group
- **Auto-grow now persists the moved origin** — when a group auto-grows to fit newly-added children, the `pwGroupOverrides` write uses the grown rect's actual `(x, y)` instead of the original pre-grow anchor, so a subsequent re-render doesn't snap the grown rect back to its smaller size
- **Post-grow overlap resolution** — after auto-grow, if the grown rect overlaps a sibling group, `_findFreeGroupSlot()` teleports the whole group and every child device's override gets translated by the same `(dx, dy)` so intra-group layout is preserved
- **Files** — [frontend/map.js](Pingwatch/frontend/map.js) — removed `keepalive: true` from `_pwSave`; added `_nodeInsideGroup`; added orphan pre-pass; auto-grow persistence fix; post-grow overlap resolution; tagged `placedRects` entries with `gname` so sibling rects can be iterated

---

## Bulk device multi-select (Devices tab)

- Motivation: Auto-Discovery drops new hosts into an auto-muted `Discovery-<cidr>` group. Promoting them one-at-a-time to real production groups (or bulk-pausing / bulk-deleting lab noise) was 15–100 modal round-trips per scan. Shipping a multi-select workflow removes that friction
- **☑ Select toggle in the Devices toolbar** — operator+ only (`rbac-op`). Clicking flips `body.pw-select-mode`; all CSS conditionals key off that one class, so the feature adds zero visual noise in the default browsing state
- **Per-card + per-group-header checkboxes** — `.dc-sel-cb` absolutely-positioned top-left on each device card (grid + list view), `.grp-sel-cb` inline in the group header next to the drag handle. Group-header checkbox has tri-state visuals (empty / checked / partial dash) matching OS file-manager conventions
- **Bulk-action bar** — sticky footer that appears when ≥1 device is selected. Shows `N selected`, `(M hidden by filter)` when any ticked cards are filter-hidden, Clear + Exit buttons, a group-name combobox (free-text + `<datalist>` populated from existing groups), and Resume / Pause / Delete buttons. Delete is the only action that prompts for confirmation
- **Shift-click range select** — DOM-order range between the last clicked card and the current one. Spans filter-hidden cards intentionally (file-manager semantics)
- **Collapsed-group select-all** — group-header checkbox selects every device in the group, including ones hidden by a collapsed grid. Uses `S.devices` (not DOM) as the source of truth
- **Keyboard shortcuts** — `Ctrl+A` (or `Cmd+A`) selects every visible card on the Devices tab (respects both the status-pill filter and the search box); auto-turns select mode on if it wasn't. `Esc` exits select mode. Both gate on `activeMainTab==='devices'` and never fire while typing in an input
- **One backend endpoint for all four actions** — `POST /api/devices/bulk` takes `{device_ids:[…], action:"move"|"start"|"stop"|"delete", group?:"…"}`. Validates `device_ids` length (1–1000), `action` enum, and `group` length (1–80 chars, required only for `move`). Returns `{ok, applied, failed, results:[{did, ok, reason?}]}` so partial failures are surfaced to the UI
- **Per-action implementation** — `move` updates `dev.group` under `STATE._lock` + fires one `db_save` + one `devices_bulk_updated` SSE broadcast. `start` / `stop` call existing `STATE.start_device` / `stop_device` (outside the lock — those helpers acquire it internally). `delete` replicates the full single-device DELETE path per device: IPAM sync, `topo_prune_pw_links`, event/flap resolution, Auto-Discovery `suppress_host()` for `discovery:*` devices, `STATE._broadcast('device_deleted')`. Writes ONE audit entry per bulk call (`bulk_move` / `bulk_start` / `bulk_stop` / `bulk_delete`) instead of N — keeps the audit tab readable on large-batch ops
- **Mute auto-propagation** — moving a device from a muted `Discovery-*` group to an unmuted production group immediately restores alerting, because `is_group_muted(dev.group)` is already evaluated at probe/alert time against the device's current group (`core/state.py:832`, `monitoring/alert_profile_engine.py:284`). Zero extra code on the bulk path
- **Empty-group cleanup** — optimistic client-side `pruneEmptyGroups()` call after a bulk-move runs — the emptied `Discovery-*` group section disappears from the UI without a page refresh
- **Files** — [routes/devices.py](Pingwatch/routes/devices.py) (new `/api/devices/bulk` handler, ~120 lines); [frontend/index.html](Pingwatch/frontend/index.html) (☑ Select toolbar button + `#bulkBar` footer); [frontend/devices.js](Pingwatch/frontend/devices.js) (multi-select state, `_toggleSelectMode`, `_cardClick`, `_toggleCard`, `_toggleGroupSel`, `_rangeSelect`, `_refreshCardSelVisual`, `_refreshGroupSelVisual`, `_updateBulkBar`, `_bulkApplyMove`, `_bulkAction`, `_bulkDeleteConfirm`, `cardHTML` + `listRowHTML` + `ensureGroupSection` extensions, Ctrl+A/Esc hooks); [frontend/style.css](Pingwatch/frontend/style.css) (`.bulk-bar`, `.dc-sel-cb`, `.grp-sel-cb`, `body.pw-select-mode`, `.dc.selected`)

---

## SAML 2.0 + OIDC Enterprise SSO

- Federated SSO alongside local / LDAP / RADIUS — admins paste IdP metadata (or a discovery URL), generate an SP signing cert from the UI, hand the SP metadata XML to the IdP admin, and the whole org signs in via the enterprise IdP. Tested with FortiAuthenticator; protocol-compliant for Okta, Entra ID, Keycloak, ADFS, OneLogin, PingFederate
- **SAML 2.0** — SP-initiated flow, HTTP-POST binding; `pysaml2` + `signxml` (pure Python — no `xmlsec1` system dep, works on Windows dev + Linux prod with `pip install`); `defusedxml` for XXE-safe metadata parsing
- **OIDC** — Authorization Code flow with PKCE (S256), JWKS-based JWT validation via `authlib.jose`, auto-discovery via `.well-known/openid-configuration` (cached + scheduled refresh)
- **IdP metadata import** — three sources: by URL (with TLS-verify-then-fallback-unverified for self-signed internal IdPs like FAC; the IdP signing cert pinned post-import is the actual security boundary), paste XML, upload XML file. Errors propagated to the UI verbatim instead of generic "Request failed"
- **SP metadata export** — `GET /api/saml/metadata` returns the XML blob (`application/samlmetadata+xml`) for IdP admins to consume. `<md:AssertionConsumerService>` + `<md:KeyDescriptor use="signing"/encryption">` always emitted when the cert is present
- **SP signing cert** — generated from the settings UI (RSA-2048 self-signed, 825-day) via existing `core.tls.generate_self_signed_cert`; private key Fernet-encrypted at rest in `app_settings.saml_sp_key_pem_enc`; rotatable independently of the TLS cert
- **AuthnRequest signing** — when `saml_sign_authn_requests=1`, every AuthnRequest is signed with `signxml.XMLSigner` (RSA-SHA256, exclusive c14n, enveloped). `<ds:Signature>` reordered to sit immediately after `<saml:Issuer>` per SAML 2.0 core spec section 3.2.1 (signxml's default first-child position breaks FAC / ADFS strict-schema validation)
- **Assertion signature verification** — `signxml.XMLVerifier` tries multiple reference patterns (1 ref / 2 refs / no constraint) so we work with IdPs that sign assertion-only (Okta) and IdPs that sign Response + Assertion (FAC, ADFS); each attempt's full exception logged at WARNING server-side for diagnosis
- **JIT provisioning** — first successful SSO login creates a `users` row with `pw_hash='__saml__'` / `'__oidc__'` and `external_id='saml|<entity>|<nameid>'` / `'oidc|<issuer>|<sub>'`; subsequent logins look up by `external_id` and sync `full_name`, `email`, group/role from the IdP (matches the LDAP/RADIUS pattern)
- **Group → role mapping** — extends the existing `user_groups` table with `saml_group_value` + `oidc_group_value` columns (additive migration on both SQLite and PG); shared `core.sso_common.sso_provision_or_sync()` does case-insensitive matching against the configured SAML attribute or OIDC `groups` claim; falls back to `default_role` when `allow_unmapped=1`, rejects login when `allow_unmapped=0`
- **Login screen SSO buttons** — login page fetches new public endpoint `GET /api/settings/public_auth` (no auth required; only returns enabled flags + display names) and renders one button per enabled IdP above the local form; container hidden when no SSO is configured (zero-config installs look identical to today)
- **TOTP still applies** — IdP-provided identity flows through the existing TOTP gate; trusted-device cookie works unchanged
- **User table SSO badges** — `🪪 SAML` (purple) + `🪙 OIDC` (green) join existing 🌐 Domain (LDAP) + 🧾 RADIUS + 🔑 Local; reset-password button hidden for SSO users (extends `isRemote` logic)
- **Coexistence** — local + LDAP + RADIUS + SAML + OIDC all active simultaneously; admin enables/disables each independently; local admin login remains as break-glass
- **8 SAML endpoints** — `/api/saml/login` (302 to IdP), `/api/saml/acs` (POST consumer), `/api/saml/metadata` (GET), `/api/saml/settings` (GET/PATCH), `/api/saml/metadata/import` (URL/XML/file), `/api/saml/sp_cert/generate`, `/api/saml/test`
- **6 OIDC endpoints** — `/api/oidc/login`, `/api/oidc/callback`, `/api/oidc/settings` (GET/PATCH), `/api/oidc/discovery/refresh`, `/api/oidc/test`
- **31 settings keys total** (17 SAML, 14 OIDC) — config + cached IdP material + display name + provisioning policy
- **Form-encoded body parsing** — `server.py::Handler._body()` now detects `application/x-www-form-urlencoded` (SAML ACS sends form-POST) and parses via `urllib.parse.parse_qs` alongside the existing JSON path
- `core/saml_auth.py` NEW; `core/oidc_auth.py` NEW; `core/sso_common.py` NEW; `routes/saml.py` NEW; `routes/oidc.py` NEW; `frontend/forms-saml.js` NEW; `frontend/forms-oidc.js` NEW; `db/users.py` (`db_add_sso_user`, `db_get_user_by_external_id`, `db_update_external_id`); `db/groups.py` (`db_get_saml_mapped_groups`, `db_get_oidc_mapped_groups`); `db/core.py` + `db/pg_schema.py` (`users.external_id` + `user_groups.{saml,oidc}_group_value` migrations); `routes/settings.py` (`/api/settings/public_auth`, `saml_status` / `oidc_status` in `/api/settings`); `frontend/forms-settings.js` (SAML/OIDC sub-tabs, badges); `frontend/index.html` + `frontend/app.js` (login-screen SSO button render); `requirements.txt` + `core/setup_logic.py` + `linux/start.sh` register `pysaml2>=7.5`, `signxml>=3.2`, `authlib>=1.3`

---

## Auth backend health checks (boot sanity + scheduled refresh)

- Two-phase health surveillance for **LDAP / RADIUS / SAML / OIDC**, addresses the gap where a rotated IdP cert / revoked client secret / reshuffled JWKS key silently breaks SSO until the first user hits the error
- **Phase 1 — boot sanity pass** — synchronous, fast, no network. Runs once in `server.py::main()` after settings load and before HTTP listener bind. Per-backend config + local crypto checks (cert parses + not expired, decrypt blob succeeds, required library importable, URL well-formed). Populates the existing `_record_ok` / `_record_err` status state so the four Integrations badges show real state within ~2 s of boot. Target runtime <200 ms total
- **Phase 2 — hourly refresh** — single daemon thread. **LDAP**: real service-account bind via `ldap_test_connection()`. **OIDC**: refetch discovery + JWKS via `oidc_refresh_discovery()` (the entire reason OIDC has auto-discovery — catches key rotation before it bites). **SAML**: cert re-parse + 30-day expiry warn (no network — FAC being down doesn't mean our config is broken). **RADIUS**: config-only (skip network — phantom auth events in the RADIUS server logs are intrusive for a 1-per-hour poll)
- **Configurable interval** — `auth_refresh_interval_min` setting in `app_settings`; allow-list `0 / 15 / 30 / 60 / 240 / 720` minutes; `0` disables the loop but keeps the boot sanity pass. Default 60 min. Validated in `PATCH /api/settings` — typo cannot create a 1-second busy loop
- **"Run now" button** — `POST /api/auth/health/run_now` (admin) sets a `_wake` event the loop's multi-event wait picks up immediately; refresh fires within seconds without restart
- **UI strip** — new "🩺 Auth Health Check" header at the top of Settings → Integrations tab (above the LDAP / RADIUS / SAML / OIDC sub-tabs since it's a shared knob): interval dropdown, "Last run: 2m ago" indicator (green if within 2× the interval, yellow if older, "never" before first refresh), Run now button. `auth_refresh_last_ts` returned in `/api/settings` as the max `last_ok_ts` across all four backends
- **Logging matrix** — INFO on boot per backend ("LDAP config valid at startup"), WARNING when cert <30 days, ERROR when cert expired or crypto broken; refresh DEBUG on success, WARNING/ERROR on failure; full traceback if the loop body crashes (loop self-recovers and continues)
- **Thread-safe status** — `_status_lock` added to `core/ldap_auth.py` and `core/radius_auth.py` (SAML/OIDC already had locks) so the new refresh thread doesn't race the live login path on `_record_ok` / `_record_err` reads
- **Graceful shutdown** — `_stop` event + `_wake` event let the multi-event wait exit instantly on `SIGTERM`; integrated with existing shutdown sequence in `server.py` alongside `stop_ldap_sync` / `stop_scheduler`
- `core/auth_health.py` NEW; `server.py` (boot pass + start/stop hooks); `routes/settings.py` (interval validator, `_get_auth_refresh_last_ts()`, `/api/auth/health/run_now`); `core/ldap_auth.py` + `core/radius_auth.py` (status locks); `frontend/forms-settings.js` (Integrations tab strip + handlers)

---

## RADIUS Probe Sensor

- New sensor type `radius` — verifies a RADIUS server with **2 layered test depths**: `reachable` (sends a deliberately bogus Access-Request and accepts any response — Accept / Reject / Challenge — as proof the host + port + shared secret are correct) and `auth` (full PAP authentication with stored credentials)
- Reuses the existing RADIUS client from `core/radius_auth.py` via a thin public wrapper `radius_probe_once(host, port, secret, username, password, nas_id, timeout, retries)`; no new dependencies
- Credentials Fernet-encrypted at rest (`radius_secret`, `radius_password`); API exposes `has_radius_secret` / `has_radius_password` booleans only
- **Access-Challenge handling** — at `auth` level, an `Access-Challenge` response is flagged in detail (`"auth: 2FA challenge required (token/push)"`) — the probe doesn't try to complete it (would need server state), but the user knows the server is alive and 2FA-gated
- Smart defaults: port `1812`, `warn_ms=500`, `crit_ms=2000`, `timeout=5s`, `test_level=reachable`, NAS-Identifier `pingwatch-probe`
- Amber-colored badge `#fbbf24` across all 7 CSS badge families; light-theme override `#b45309`
- 5 new DB columns: `radius_secret`, `radius_test_level`, `radius_username`, `radius_password`, `radius_nas_id`
- `monitoring/probes.py` + `core/state.py` + `core/radius_auth.py` + `db/core.py` + `db/pg_schema.py` + `db/persistence.py` + `routes/devices.py` + `frontend/forms-sensor.js` + `frontend/sensors.js` + `frontend/forms-settings.js` + `frontend/style.css` updated

---

## Auto-Discovery — Periodic Subnet Scanning (v0.9.3)

- Scheduled subnet scanning with automatic device creation — admins tick **Auto-discover new hosts** on any IPAM subnet, set a global interval (15 / 30 / 60 / 240 / 720 / 1440 min), and walk away; new hosts are added to group `Discovery-<CIDR>` with a ping sensor plus guessed service sensors (HTTP port 80, HTTPS port 443 with `verify_ssl=False`, SNMP port 161)
- **Daemon thread** (`monitoring/auto_discovery.py`) — `start_loop()` / `stop_loop()` / `trigger_run_now()` / `get_last_run_status()`; wakes every N minutes, iterates IPAM subnets with `auto_discover=1`, runs the existing `subnet_discovery.start_scan()` pipeline, funnels results through `create_devices_batch()`; scans are serialised (one subnet at a time, `_tick_lock` prevents race with `run-now`)
- **Safety rails** — global enable (`auto_discover_enabled`, default **off** — no surprise devices after upgrade); global pause (`auto_discover_paused`) short-circuits the inner loop without stopping the daemon; first-scan device cap (`auto_discover_first_scan_cap`, default 100) aborts the first scan on a new subnet if it would create more devices than the cap and flags `first_scan_pending` in the UI; maintenance-window awareness (`auto_discover_during_maint = skip | run`); suppressed-hosts list prevents manually deleted auto-discovered devices from being re-added
- **Suppressed-hosts list** — when a device whose `origin="auto_discovery"` is deleted, the host IP + name are appended to `auto_discover_suppressed_hosts` (JSON list, capped at 500 FIFO). Admins can remove entries via Settings → Auto-Discovery to permit re-discovery. List persisted in `app_settings`
- **Per-IPAM-subnet toggle** — `subnets.auto_discover` (SQLite `ALTER TABLE` + PG `IF NOT EXISTS` migration) + `subnets.first_scan_approved` (bypass cap after admin review) + `subnets.last_auto_scan_ts`; toggled via `POST /api/ipam/subnet/<id>/auto-discover`; checkbox column added to the IPAM subnet table in `ipam.js`
- **Auto-Discovery settings tab** — new tab in the Settings modal (mirrors Auth Health pattern); controls: global enable, interval dropdown, pause toggle, first-scan cap, alert-on-new-device, reverse-DNS naming, maintenance behaviour, "Last run / Run now / Run stats"; suppressed-hosts table with per-host Remove button; read-only list of enabled subnets with last-scan time and devices-added count
- **Run-now endpoint** — `POST /api/auto-discovery/run-now` (admin; optional `{subnet_id}`) triggers an immediate tick in the daemon thread; returns `{triggered, subnets_queued}`; returns 202 "already running" when a tick is in progress
- **Status endpoint** — `GET /api/auto-discovery/status` returns `{enabled, paused, last_run_ts, last_run_stats: {subnets_scanned, hosts_found, devices_added, devices_suppressed, errors}, currently_running, next_run_ts, suppressed_hosts}`
- **Reverse-DNS naming** (`auto_discover_use_ptr`, default on) — PTR record used as device name; falls back to bare IP when no PTR exists
- **Audit trail** — every scan tick writes one `auto_discovery_tick` audit entry (`found=N added=N suppressed=N duration=Ns`); every auto-added device gets an audit entry via the existing `create_devices_batch` path; first-scan cap hit and first-scan approval each write their own entries
- **Alert-on-new-device** (`auto_discover_alert_on_new`, default off) — logs one `alert_events` row with `event_type="device_auto_added"` per new host when enabled
- `monitoring/auto_discovery.py` NEW; `routes/auto_discovery.py` NEW; `db/core.py` + `db/pg_schema.py` (three new `subnets` columns); `db/ipam.py` (`db_set_auto_discover`, `db_set_subnet_last_scan`, `db_approve_first_scan`, updated `db_list_subnets`); `routes/settings.py` (new keys in GET/PATCH); `routes/ipam.py` (toggle endpoint); `server.py` (start/stop hooks, route registration); `frontend/forms-settings.js` (Auto-Discovery tab); `frontend/ipam.js` (Auto-Discover column + checkbox); `frontend/style.css` (`.disc-*` class family)

---

## Bug fixes & minor improvements (v0.9.3)

- **`msColor` inverted-threshold support** — the canonical sensor-color helper at `frontend/forms-utils.js::msColor()` only knew "high value = bad" — so for inverted-threshold sensors (TLS days-until-expiry, VMware datastore free-GB) the LAST tile flipped red the moment a viewer switched History → Overview. Added an `inverted` flag for `stype='tls'` and `stype='vmware' AND vmware_metric.startsWith('dstore_')`; uses `<=` instead of `>=` for crit/warn comparison. Cosmetic-only — no event change
- **`pystray` headless Linux crash** — `import pystray` at `server.py:21` was guarded with `except ImportError`, but on a fresh `pip install pystray` on a headless server, pystray's `__init__.py` runs `Icon = backend().Icon` at module-load time which raises `ValueError: Namespace Gtk not available`. Broadened to `except Exception` with a comment explaining why both ImportError and ValueError happen
- **Backup scheduler — silent save miss** — `_save_last_ts()` swallowed save failures at `log.debug` level, so a corrupted writer queue / DB error left no trace. Bumped to `log.warning`. Also added a one-shot INFO log when `last_fired` flips from `None` → real timestamp ("first device-backup timestamp recorded — previous value was missing; restarts will now show this date") so a missing-row mystery is observable next time it happens
- **SFTP probe chroot path documentation** — clarified that with chrooted SFTP users (e.g. OpenSSH `ChrootDirectory /home/user`), the `remote_path` must be session-relative (`/tmp/file.txt`), not absolute from the host filesystem (`/home/user/tmp/file.txt`). The probe was correct; user confusion only
- **Ad-blocker CSS conflict** — `.ad-*` CSS class names matched ad-blocker filter rules (`display: none !important`), silently hiding the Auto-Discovery settings panel. Renamed the entire family to `.disc-*` in `style.css` and `forms-settings.js`
- **Auto-Discovery `found` count missing from stats** — scan summary only showed `added=N`; admins couldn't distinguish "nothing live on subnet" from "all hosts already monitored". Added `found=N` to the per-subnet stat dict, daemon `log.info` line, and the `auto_discovery_tick` audit entry detail string
- **Group mute badge missing on auto-discovered groups** — groups created by Auto-Discovery (or manual Subnet Discovery) had mute enabled in `muted_groups` but the group header badge didn't appear until a page refresh or manual toggle. Fixed by calling `_loadMutedGroups().then(() => _refreshGroupMuteBadge(group))` immediately after `ensureGroupSection()` appends the new group panel in `devices.js`
- **Port-443 TLS → HTTPS sensor suggestion** — `_suggest_sensors()` in `subnet_discovery.py` was proposing a `tls` sensor for port 443. On lab networks with self-signed certificates the TLS probe always fails unless the admin manually unchecks "Verify SSL". Changed to propose an `http` sensor with `url=https://…:443` and `verify_ssl=False`; admins can re-enable SSL verification via Edit Sensor
- **Alert event `triggered_at` overwritten on escalation** — stage 2+ escalations were updating `triggered_at` to the escalation timestamp, pushing it outside the 300-second correlation window that `_matchAlertEvt()` in the Events tab uses to link a flap row to its alert event. Frontend showed "○ No rule" even when an alert had fired. Fixed: `db_log_event()` no longer updates `triggered_at` on the escalation (repeat) path; the original fire time is preserved across all stages
- **Group mute not reflected in Events reason chip** — the reason chip only checked sensor-level (`alerts_muted`) and device-level mute, not group mute (`muted_groups`). Events on sensors in a muted group were labelled "○ No rule" instead of "🔕 Muted". Fixed by adding a `_grpMuted` check against `window._mutedGroups` in `events.js`

---

## Top-level Logs tab + professional log viewer

- **Extracted out of Settings** — the Logs view is no longer a Settings sub-tab; it's a top-level navigation entry (`📜 Logs`, admin-only via the `rbac-admin` class) inserted after Reports in `frontend/index.html` + `frontend/app.js::switchMainTab`. Sub-tab, footer, `_buildSettingsTab_logs()` renderer, and all `_logFilter` / `_switchLogTab` / `_loadLogTab` / `_toggleLogLive` / `_stopLogLive` / `_exportLog*` / `_logDownload` helpers removed from `forms-settings.js`. The old `logs` entry is gone from the Settings `tabs` array and the sidebar; the `stab-footer-logs` footer is removed
- **Debug Mode toggle relocated** — moved from the former Logs sub-tab to Settings → General (new "Logging" section with inline link to the new Logs tab). `_saveDebugMode()` wiring unchanged
- **NEW file `frontend/logs.js`** — registered in `server.py::_JS_FILES` between `reports.js` and `alerting.js` so `_logsInit` is defined before `app.js` references it. Renders a toolbar (stream sub-tabs + Live/Refresh), filter bar, status bar, body, and a floating "Jump to live" pill inside `#logsView`
- **Smart scroll-follow** — `_lvBindScrollFollow()` tracks whether the body is near the bottom (< 20 px threshold); auto-attaches when the user scrolls to the end, auto-detaches when they scroll up. The `.lv-jump` pill fades/slides in when detached; `End` key or clicking the pill re-attaches. Live-tail appends without yanking the viewport
- **Minimum-level filter** — `/api/logs/{key}` gained a `min_level` query param with rank-based comparison (`DEBUG<INFO<WARNING<ERROR<CRITICAL`) in `routes/export.py`; level dropdown reads "DEBUG+ / INFO+ / WARNING+ / ERROR+ / CRITICAL only". The old strict-equal `level=` param is still honoured for backwards compatibility
- **File stats in API response** — `/api/logs/{key}` now also returns `file_size` (bytes on disk) and `rotated_count` (`.1`, `.2`, … backup files next to the live log). Frontend renders `Showing X of Y filtered (Z total) · 8.2 MB · +N new since open` in `.lv-status`; "new since open" uses a page-load snapshot of `total`
- **Word-wrap toggle** — `.lv-body.nowrap` swaps the line rule from `pre-wrap` to `pre` and disables word breaks on the message; toggled by `_lvToggleWrap()` (key `w`)
- **Copy visible to clipboard** — `_lvCopy()` grabs `body.innerText` and writes via `navigator.clipboard.writeText`; success/failure toast
- **CSV / JSON export** — `_lvExport(fmt)` iterates the rendered `.lv-line` DOM (so it exports exactly what's on screen after filtering) and streams a timestamped blob download
- **Keyboard shortcuts** — `_lvBindKeys()` registers a single `document.keydown` listener: `/` focuses search, `Esc` clears filters, `l` toggles live, `r` refreshes, `w` toggles wrap, `End` jumps to live. Guarded so it doesn't fire when a modal is open or when typing in any input other than the log search box
- **Preferences persisted** — active stream, filter (time range / min level / search / custom range), wrap, and follow flag are all stored to `localStorage.pw_logs_prefs` via `_lvPrefsSave()` / `_lvPrefsLoad()`; restored on `_logsInit`
- **Badge click retargeted** — `_openLogBadge()` in `app.js` pre-sets `_lvFilter.minLevel = 'WARNING'` and calls `switchMainTab('logs')` instead of opening the Settings modal
- **Polling lifecycle** — live timer auto-stops when switching away via `_logsDeactivate()` called from `switchMainTab` (also when the `#lvBody` element disappears)
- **CSS renamed** — `.log-*` / `.ll-*` rules in `style.css` replaced by `.lv-*` (Logs Viewer); `@keyframes log-live-pulse` renamed `lv-live-pulse`. New `#logsView` block + floating pill styling (`.lv-jump`, `.lv-jump.show`)
- `routes/export.py` (`min_level` param + file stats), `frontend/logs.js` NEW, `frontend/app.js` (switchMainTab branch + `_logsDeactivate` + `_openLogBadge` rewire), `frontend/forms-settings.js` (Logs tab + all helpers removed; Debug Mode moved to General), `frontend/index.html` (tab button + `#logsView`), `frontend/style.css` (`.lv-*` rules replace `.log-*` / `.ll-*`), `server.py` (`_JS_FILES` includes `logs.js`) updated

---

## SFTP Probe Sensor

- New sensor type `sftp` — verifies the SFTP subsystem on a remote host with **4 layered test depths**: `open` (auth + subsystem), `list` (directory listing), `stat` (file metadata + size), `checksum` (SHA-256 integrity check, read-only, ≤ 10 MB cap)
- Password or private-key auth (Ed25519 / RSA / ECDSA PEM); credentials Fernet-encrypted at rest (`sftp_password`, `sftp_private_key`); API serializes only `has_sftp_password` / `has_sftp_private_key` booleans — no plaintext ever returned
- `checksum` depth streams via 65 536-byte chunks into `hashlib.sha256()`; a pre-flight `stat` check enforces the 10 MB cap and returns `"checksum: file exceeds 10MB cap"` rather than pinning the probe thread on a large file
- Phase-tagged failure detail on every error (e.g. `"open: subsystem not enabled"`, `"list: /backups not found"`, `"checksum: mismatch (got a1b2…, expected f0e1…)"`)
- **Interval policy for `checksum` level** — minimum 60 s (server rejects lower with `400`); form auto-bumps interval to 300 s and timeout to 30 s when `checksum` is selected (leaves higher values untouched)
- Smart defaults: port `22`, `warn_ms=2000`, `crit_ms=5000`, `timeout=10s` (30 s when checksum), `test_level=open`, `auth_type=password`
- Rose-colored badge `#fb7185` across all 7 CSS badge families; light-theme override `#be123c`
- New "**File Transfer**" sensor category introduced in the Add Sensor sidebar
- 7 new DB columns: `sftp_user`, `sftp_password`, `sftp_private_key`, `sftp_auth_type`, `sftp_test_level`, `sftp_remote_path`, `sftp_expected_sha256` — SQLite `ALTER TABLE` + PG `CREATE TABLE` / migrations
- `monitoring/probes.py` + `core/state.py` + `db/core.py` + `db/pg_schema.py` + `db/persistence.py` + `routes/devices.py` + `frontend/forms-sensor.js` + `frontend/sensors.js` + `frontend/forms-settings.js` + `frontend/style.css` updated

---

## SSH Probe Sensor

- New sensor type `ssh` — 3 layered test depths: `connect` (TCP + SSH handshake), `banner` (captures SSH version string in detail), `auth` (password or private-key login)
- Password or private-key auth (Ed25519 / RSA / ECDSA PEM); both credentials Fernet-encrypted at rest; API exposes `has_ssh_password` / `has_ssh_private_key` booleans only
- `_load_ssh_key()` helper in `monitoring/probes.py` loads multi-type PEM from `io.StringIO` — reused by SFTP probe
- `paramiko` lazy-imported inside `probe_ssh()`; graceful `"paramiko not installed — run setup wizard"` fallback
- `MissingHostKeyPolicy` used (monitoring surface — not a MITM gate; consistent with backup engine TOFU)
- Smart defaults: port `22`, `warn_ms=1500`, `crit_ms=4000`, `timeout=10s`, `test_level=connect`, `auth_type=password`
- Lime-colored badge `#a3e635` across all 7 CSS badge families; light-theme override `#4d7c0f`
- 5 new DB columns: `ssh_user`, `ssh_password`, `ssh_private_key`, `ssh_auth_type`, `ssh_test_level`
- `monitoring/probes.py` + `core/state.py` + `db/core.py` + `db/pg_schema.py` + `db/persistence.py` + `routes/devices.py` + `frontend/forms-sensor.js` + `frontend/sensors.js` + `frontend/forms-settings.js` + `frontend/style.css` updated

---

## SMTP Probe Sensor

- New sensor type `smtp` — 5 layered test depths: `connect` (TCP), `ehlo` (EHLO handshake), `starttls` (STARTTLS upgrade), `auth` (login), `mailfrom` (MAIL FROM round-trip — no mail delivered)
- TLS mode selector: plain / STARTTLS / SSL (port auto-suggestion: 25 / 587 / 465)
- **"Use system SMTP"** button pre-fills host, port, TLS mode, and username from the system SMTP settings (Settings → Email)
- Credentials Fernet-encrypted at rest (`smtp_password`); API exposes `has_smtp_password` boolean only
- Phase-tagged failure detail (e.g. `"auth: 535 Authentication failed"`, `"starttls: server does not support STARTTLS"`)
- Smart defaults: port `587`, `warn_ms=2000`, `crit_ms=5000`, `timeout=15s`, `test_level=ehlo`, TLS `starttls`
- Pink-colored badge `#f472b6` across all 7 CSS badge families; light-theme override `#be185d`
- 6 new DB columns: `smtp_host`, `smtp_port`, `smtp_tls`, `smtp_test_level`, `smtp_user`, `smtp_password`
- `monitoring/probes.py` + `core/state.py` + `db/core.py` + `db/pg_schema.py` + `db/persistence.py` + `routes/devices.py` + `frontend/forms-sensor.js` + `frontend/sensors.js` + `frontend/forms-settings.js` + `frontend/style.css` updated

---

## Bug fixes & minor improvements (probe engine)

- **Alert profile engine log spam** — "no dispatch — all stages gated" was firing at INFO on every probe cycle for every sensor in a non-OK state. Changed `_diag_log()` from `log.info()` to `log.debug()` in `monitoring/alert_profile_engine.py`; the messages are still emitted when debug mode is on, they just no longer pollute the default INFO stream
- **PG integer column rejection on empty string** — `update_sensor()` and save paths in `db/persistence.py` could pass `''` for numeric fields (port, interval, timeout, warn_ms, crit_ms) when a form field was blank. PostgreSQL rejects `''` for `INTEGER` columns. Fixed: added `_int_or_none(v)` helper at the top of `persistence.py` (coerces `''` / `None` / non-numeric → `None`); applied to all numeric fields in every save tuple. `update_sensor()` in `core/state.py` treats `''` the same as `None` for the same field set

---

## RADIUS Authentication (PAP + Access-Challenge 2FA)

- Third auth source alongside local and LDAP — configure in **Settings → Integrations → RADIUS** (🧾 sub-tab with live status badge)
- PAP authentication only in v1 (covers FortiAuthenticator, NPS, FreeRADIUS, Cisco ISE in default configs); `pyrad` lazy-imported — installations without it load cleanly
- **Access-Challenge 2FA** — if the RADIUS server responds with `Access-Challenge` (FortiAuthenticator token, Duo, RSA SecurID, Azure NPS extension), the login UI presents the server's prompt; entering the OTP completes auth. Successfully completing a challenge satisfies 2FA and skips the app's built-in TOTP for that session. Multi-step challenges (chained prompts) are supported
- **Primary / secondary failover** — `_try_server()` retries on socket error or timeout before failing over to secondary; `Access-Reject` is treated as a definitive answer (no failover). Configurable `radius_timeout` and `radius_retries` per server
- **Attribute → Group mapping** — each PingWatch group can carry `radius_attribute` + `radius_value` columns (new `user_groups` columns, both SQLite and PostgreSQL, with one-shot migration). On every successful login, returned RADIUS attributes are matched first-match against mapped groups to assign role; configurable `radius_default_role` and `radius_default_group_id` as fallback
- **Auto-provision** — `radius_auto_provision=1` auto-creates a local user row (`auth_type='radius'`, `pw_hash='__radius__'`) on first successful RADIUS login, via `db_add_radius_user()` mirroring `db_add_ldap_user()`
- **Realm munging** — `radius_realm_prefix` / `radius_realm_suffix` transform the username before it leaves PingWatch (e.g. prepend `DOMAIN\` or append `@corp.local`)
- **NAS-Identifier** — sent as `radius_nas_identifier` (default `"pingwatch"`)
- **Test User Auth dialog** — admin runs a full authentication against the live RADIUS server; returned attributes are displayed raw so they can be copied into the mapping table
- **Challenge state store** — module-level `_CHALLENGES` dict (TTL 120 s, `threading.Lock`) persists `State` blobs between HTTP requests, same pattern as the TOTP challenge store
- **Status badge** — Integrations tab shows `ok` / `error` / `configured` / `unconfigured`; updated by `_record_ok()` / `_record_err()` hooks in `core/radius_auth.py`; `GET /api/settings` includes `radius_status`
- **Add User modal** — "RADIUS" option in the `#au-type` dropdown, gated on `radius_enabled`; hides password fields
- **User table badge** — `🧾 RADIUS` badge (amber) alongside existing Local and Domain (LDAP) badges; reset-password button suppressed for RADIUS users
- **Login dispatch order** — explicit RADIUS users (`auth_type='radius'`) always go through RADIUS; existing local/LDAP users use their own path; RADIUS auto-prov is attempted only for completely unknown users (does not interfere with LDAP auto-prov)
- **Debug logging** — `radius_debug=1` or global `debug_mode` emits attribute-by-attribute comparison traces at DEBUG, matching the LDAP debug level
- `core/radius_auth.py` NEW; `routes/radius.py` NEW; `frontend/forms-radius.js` NEW; `db/users.py` + `db/groups.py` + `db/core.py` + `db/pg_schema.py` extended; `core/auth.py` + `routes/auth.py` extended; `server.py`, `frontend/forms-settings.js`, `frontend/forms-users.js`, `frontend/app.js` updated; `requirements.txt` + `core/setup_logic.py` + `linux/start.sh` register `pyrad>=2.4`

---

## Remote DB Backup Upload (SFTP + SMB)

- After each successful local DB backup run, PingWatch can automatically upload the snapshot to a remote destination — configure in **Settings → Database → Remote Upload**
- Two protocols: **SFTP** (paramiko, reuses existing TOFU host-key store from `backup/engine.py`) and **SMB** (`smbprotocol` / `smbclient`, lazy-imported — `smbprotocol>=1.10` added to `requirements.txt` and the setup wizards)
- Upload runs in the same backup thread immediately after the local `.db` file is written; failures are logged but do not abort the local backup
- Remote credentials (password) Fernet-encrypted at rest using the same `backup_enc_key` as device backup credentials
- Remote path, share (SMB), host, port, and username all configurable; retention on the remote side is not managed by PingWatch (upload-only)
- Status badge in the Backup Status widget DB section shows the outcome of the most recent remote upload attempt

---

## Backup Status widget — Database section

- The existing **Backup Status** dashboard widget now includes a **Database** section below the device-config 2×2 KPI grid
- Shows last run age with color coding: green (on-schedule), amber (overdue — > 1.5× configured interval: 36 h for daily, 12 d for weekly), red (last run errored)
- Shows next scheduled run time and, when remote upload is enabled, the remote upload status
- "Scheduled, never run yet" and "Disabled" states rendered in muted grey
- Clicking the DB card navigates directly to **Settings → Database**
- Backed by parallel fetch of `/api/backups` + `/api/settings` in `_dwNcmStatusRefresh()`; new helpers `_dwAgoFromStampUnderscore()`, `_dwIsDbBackupOverdue()`, `_dwNextDbBackupLabel()`

---

## Bug fixes & minor improvements (v0.9.2)

- **Shutdown race — pool closed error** — `autosave_loop` used `time.sleep(60)` that blocked past pool shutdown. Fixed: replaced with `threading.Event.wait(60)` (`_autosave_stop`); `stop_autosave()` signals the event and is called by `server.py` shutdown before `shutdown_writers()` + `pg_close_pool()`
- **DB backup catch-up** — `_should_fire()` used exact-minute matching; a missed window (restart or stall) was silently skipped. Fixed with catch-up semantics: fires if due time has passed and no run exists for the current window
- **Bundle export filename** — the DB export bundle now includes the app version in the filename (e.g. `pingwatch-bundle-v0.9.2-2026-04-18.zip`). `frontend/forms-io.js` now reads the `Content-Disposition` header from the export response and prefers the server-supplied filename over a hardcoded one
- **Stale login form after RADIUS logout** — signing out left the login button's `onclick` pointing at the RADIUS challenge submit. Fixed: `_resetLoginForm()` helper clears any TOTP/RADIUS prompt DOM and restores the default `submitLogin` handler; called from `showLogin()` whenever `!keepInput`
- **LDAP badge flipping to "error" on RADIUS logins** — `ldap_authenticate` logged WARNING "user not found" and called `_record_err()` when a RADIUS-only user wasn't in LDAP. Fixed: downgraded to DEBUG and removed the `_record_err` call for the not-found case (not an LDAP error)
- **Viewer clicking Settings** — Settings button was visible to all roles but silently did nothing for non-admins. Fixed: `openSettings()` now guards on `S.role === 'admin'` and shows a clear toast ("Settings is admin-only") for other roles

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
