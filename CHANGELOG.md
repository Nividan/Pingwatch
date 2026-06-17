# 📋 PingWatch Changelog

Detailed implementation notes for every shipped feature. For the high-level roadmap and upcoming work see [ROADMAP.md](ROADMAP.md).

---

## v1.4 — Reliability & acknowledged state

Two monitoring-wide improvements built on top of the v1.3 probe work. The wire protocol is unchanged (still `1`), so v1.3 agents keep working — though the Probes page flags them "update available," and re-downloading picks up the agent-side reliability fixes below.

### Root-cause analysis (dependency correlation)

When upstream infrastructure fails — a core switch, a firewall, an ISP link — every device behind it goes down at once, and until now each appeared as an independent flap/alert with no hint they shared one cause. PingWatch now correlates the live down-set against the **same parent dependency graph the Live Map already draws** (`devices.parent_device_ids`, with the `pw_group_parents` fallback) to name the single **root** device behind each cluster of downs. The engine ([monitoring/root_cause.py](monitoring/root_cause.py), read-only over `STATE` + `flap_log`, reusing `_resolve_parents` / `infer_tier` / `_device_status` from [site_tree.py](monitoring/site_tree.py) + [site_rollup.py](monitoring/site_rollup.py)) walks each down device up its parent chain while every step is *explained by upstream*, attributes it to the topmost down ancestor, and clusters downs into incidents (one root + N impacted) with a confidence score and evidence (infrastructure tier, "root went down first" from `_down_since_ts`, link-down SNMP trap near the root). It is **redundancy-aware**: a device is only a downstream symptom when **all** its parents are down — a dual-homed device with any live uplink is treated as a genuine local fault and never attributed upward. Surfaced three ways: a new **Active Incidents (Root Cause)** dashboard widget ([dashboard.js](frontend/dashboard.js)), a **root-cause overlay** on the Live Map drill-in that pulses the root node and stripes its impacted subtree with a "⟁ Root cause" toggle ([livemap.js](frontend/livemap.js) / [livemap.css](frontend/livemap.css)), and a cross-device **collapse pass** in the Events view that bundles simultaneous downs under their root ([events.js](frontend/events.js)). Read-only endpoints `GET /api/incidents` (live) and `GET /api/incidents/history?window=24h` (reconstructed from `flap_log`, using today's graph since no historical topology snapshots are kept) live in [routes/livemap.py](routes/livemap.py). **PRTG-style dependency suppression** (default on, toggle in **Settings → Sensors → Root-Cause Analysis**) mirrors the maintenance-window gate in [alert_profile_engine.py](monitoring/alert_profile_engine.py) `_fire()`: while a root is down, downstream symptom alerts are still **recorded** (as `state='suppressed'`, `suppress_reason="Downstream of …"`) but no email/webhook/syslog is sent — the root alerts normally, recovery always dispatches, and symptoms resume firing the moment the root recovers. Two new dual-backend settings (`rca_suppress_downstream`, `rca_correlation_window_s`) seed in [db/core.py](db/core.py) + [db/pg_schema.py](db/pg_schema.py). Entirely server-side — the remote probe agent is unaffected (wire protocol unchanged).

### Apply scheduling settings to existing sensors

The per-type and global sensor defaults only ever seeded **new** sensors — existing sensors kept their stored values, with no way to mass-edit them (and `fail_after` / `recover_after` had no per-sensor editor at all, so they were effectively frozen at creation time). New **Settings → Sensors** actions fix that: a **⤓** button on each type row pushes that row's **Interval / Timeout** onto every existing sensor of that type, and an **"Apply to all existing sensors…"** button under Global Defaults pushes **Interval / Timeout / Fail-after / Recover-after** onto every sensor of every type. Backed by `POST /api/sensors/apply-interval` ([routes/settings.py](routes/settings.py), admin-only): each field is independently optional, timeout is clamped to ≤ the (new or existing) interval, and only sensors whose value actually changes are counted. Crucially it does **not** go through `update_sensor()`'s stop→edit→restart — the four fields are all read live by the probe loop (interval at reschedule, the rest at the next probe/result), so a bulk apply takes effect **within one cycle with no restart and no probe storm** (looping a restart over hundreds of sensors would re-create exactly the congestion the staggered-start work removed). Persists on both backends via `db_save`, audited as `sensor_bulk_apply`, and bumps every probe's `config_version` so remote-assigned sensors re-pull the new cadence on their next check-in. Warn/Crit stay per-sensor.

### Scale-safe default intervals for fresh installs

Fresh installs now seed sensible-at-scale monitoring defaults instead of the old 5s-everything. **Global defaults** move to **Interval 60s / Timeout 10s / Fail-after 3 / Recover-after 2** (was 5/4/2/1), and a new seeded `snr_type_defaults` gives per-type overrides where the cost profile differs: **Ping 30s/3s**, **DNS 60s/5s**, **SNMP / SSH / SFTP / SMTP 120s/15s**, **vmware 60s/10s** (HTTP/S, TCP, Banner inherit the 60s/10s global). The reasoning: detection time = interval × fail-after, so 60s × 3 = ~3 min to DOWN with transient-loss blips absorbed, and recover-after 2 damps single-poll flap — the right trade for tens of thousands of sensors (override the critical gear down per-type or per-sensor; keep the global conservative). The seeds change in [db/core.py](db/core.py) + [db/pg_schema.py](db/pg_schema.py) with matching read-fallbacks ([routes/settings.py](routes/settings.py), [routes/devices.py](routes/devices.py)) and `Sensor`/`add_sensor` constructor defaults ([core/state.py](core/state.py)); the "Reset to Defaults" button mirrors the same scheme. Both seed paths are **insert-if-absent** (`ON CONFLICT DO NOTHING` / `SELECT…WHERE key`), so **existing installs are never touched** — they keep their configured global, and only adopt the new scale-safe values via the apply buttons above. Existing sensors everywhere keep their stored values.

### Trustworthy backup status (output validation)

A device config backup used to report **OK** the moment SSH connected and authenticated — it never checked that the device actually returned a config. An empty reply, a one-line banner, or a rejected command (`% Invalid input`) all came back "successful," and an empty result even left a green run in history with no saved file. Backups now validate the captured output through a fail-fast ladder in [backup/engine.py](backup/engine.py) `_validate_output()`, called from `run_backup()` so SSH and Telnet are covered uniformly: **(1)** empty / whitespace-only output is *always* rejected; **(2)** an optional **minimum size** (bytes) catches short or partial responses; **(3)** an optional **expected-content** assertion — case-insensitive substring by default, or a **Regex (advanced)** toggle that reuses the `banner_regex` safety guard (compile-validate, 200-char cap, ReDoS match-timeout, fail-closed). A failing rung demotes the run to `failed` with a specific reason (`"empty response…"`, `"output too short (84 B < 200)"`, `"expected content not found"`). Three new per-device `backup_devices` columns (`expected_content`, `expected_is_regex`, `min_bytes`) ship as a dual-backend migration (SQLite `ALTER` + PG `_migrations`), validated on save in [routes/backups.py](routes/backups.py) and surfaced in the device's backup settings modal. Central-only — no agent path involved.

### Acknowledged-down state (PRTG-style)

ACKing an event now marks the *live sensor*, not just the event row: its tile renders muted red with a **✓ ACK** badge (tooltip: who + when), and the device card mutes with an ACK chip only when **every** failing sensor on it is acknowledged — one fresh failure keeps the card loud. The flag lives on the sensor runtime (set by the ACK endpoint, pushed over SSE so open browsers flip instantly), **clears automatically on recovery** (a future down starts loud again, including threshold/SNMP-state recoveries), and survives restarts via the unresolved-flap re-hydration in [db/events.py](db/events.py). Deliberately not grey — grey means paused/stale; muted red means *known down, someone owns it*.

### HTTPS sensor cert-expiry thresholds

The HTTP/S sensor can now warn/crit on an **approaching certificate expiry** — one sensor covers both endpoint health and cert lifetime, no separate TLS sensor needed. New per-sensor **Warn ≤ days** / **Crit ≤ days** fields (default **90 / 30** for new HTTPS sensors, off for existing ones until edited). The probe reports days-to-expiry via a bounded, best-effort TLS peek ([monitoring/probes.py](monitoring/probes.py) `_peer_cert_expiry_days`) that never turns a healthy HTTP check into a failure; the server folds cert severity into the existing threshold state as **worst-of** with latency/loss (cert only *escalates* — latency keeps attribution on a tie), so it rides the same Events/ack/resolve/alert pipeline with a *"Certificate expires in Nd"* detail. Works from remote probes too (the agent reports `cert_days`; the server owns the thresholds). Needs a verifiable cert (Verify SSL on) — a peek under CERT_NONE can't read the expiry date.

### Unified site picker

Every site field — IPAM subnet (add/edit), device (add/edit), group (add/edit + the add-group section), and the bulk "move to site" bar — is now one shared **combobox** ([frontend/forms-utils.js](frontend/forms-utils.js) `siteComboHtml`): a real click-to-open dropdown listing **all** sites, with keyboard nav, that still lets you type a brand-new site name (sites are free-text). It replaces the native `<datalist>`, which filtered-as-you-type and rendered inconsistently. Critically, there is now **one true site list**: every picker reads the same `/api/sites` union (IPAM subnets + devices + sites table) — the old IPAM-only `_ipamSiteDatalist()` (which showed just sites that already had subnets, hiding device-only/Live-Map sites) is gone. The dropdown panel is `position:fixed` so it never clips inside a scrollable modal. (The alert-profile / maintenance-window *scope* pickers keep their datalist — they select a site **or** group **or** device — but draw from the same single list.)

### IPAM CSV export

An **Export CSV** button on the IPAM page downloads every allocation across **all** subnets in one file — columns **Site, Subnet, IP Address, Name, DNS, Status, Licenses, Modified By, Last Modified**. The server ([routes/ipam.py](routes/ipam.py) `GET /api/ipam/export`) joins `ip_allocations` + `ipam_subnets` and folds in each linked device's licenses (`name (expiry; status)`), so the export is complete regardless of which subnet is loaded in the UI. Rows sort by site → subnet → numeric IP; Status mirrors the grid badge (Used / Gateway / Reserved / Discovered / …); UTF-8 BOM so Excel opens non-ASCII names cleanly. Read-only (viewer), audited as `ipam_export`.

### No more restart blips

After a restart the first probe cycle used to spray DOWN events that auto-resolved one cycle later (worst with vCenter: the in-memory pyvmomi session cache is cold, so every vmware sensor's first probe raced a cold session and false-failed with "VM not found" / "metric not available"). Fixed at the source plus a safety net:

- **vCenter warm-on-connect** ([vmware/client.py](vmware/client.py) `_warm_session`): every freshly-established session now forces `RetrieveContent()` + the perf-counter catalog + an inventory view *before* it's used, so the first real probe hits warm caches and succeeds. Server and agent both pre-warm each distinct vCenter at startup, one thread per vCenter, and stagger vmware sensors' **first** probe ~12s so the warm wins the race (the agent already staggered; the server now does too).
- **Startup grace window** (`startup_grace_s` setting, default 60s, 0=off): parks new down/threshold events during settling — probes run, samples record, tiles go red live, but the event row + alerts emit only if the sensor is *still* failing when the window closes, stamped with the **true transition time**; in-window recoveries vanish without a trace, and recoveries/auto-resolves always process immediately. The window no longer closes on a blind timer — it stays open until the **first probe cycle completes** (every central sensor has reported once), bounded by a hard cap, so slow first probes get parked instead of slipping out just after a fixed 60s ([core/state.py](core/state.py) `begin_startup_grace`/`_maybe_flush_grace`/`_flush_grace`).
- **Watchdog boot holdoff**: the probe watchdog withholds `probe_offline` verdicts until agents have had time to reconnect — right after boot every probe's persisted `last_seen` is stale by definition.

### vCenter sensors no longer flap as a herd

A device measuring vCenter could flap an entire host/VM's metrics DOWN together — a synchronized batch of *"timed out after 60s — vCenter is slow or overloaded"* events, all recovering ~50s later. Root cause: every `host_*`/VM sensor on a vCenter shares one session, and when that session needed a reconnect/revalidation during a momentary vCenter slow window, **every sibling sensor serialized behind the single-flight reconnect** and tripped the 60s probe cap together. (The 60s cap, added the day before, is what made the slowness visible as clean DOWN events instead of silent hangs.)

Two changes in [vmware/client.py](vmware/client.py) break the herd:
- **Non-blocking reconnect** — a probe that finds a reconnect already in flight for its vCenter no longer queues behind it; it raises `_ReconnectInProgress` and serves its last-good sample for that cycle. Exactly one thread rebuilds the session; the rest neither pile on nor time out.
- **Serve last-good on transient timeout** — on a `_ProbeTimeout` the probe returns the last cached sample (bounded to `_STALE_SERVE_MAX_S` = 180s, with the staleness shown in the detail) rather than flapping DOWN. Only a sustained outage beyond that window reports a real DOWN.

Net: a momentary vCenter slow window costs one reconnect plus a few seconds of slightly-stale samples, not a whole-device DOWN/RECOVERED storm. Distributed-probe agents pick this up by re-downloading their agent package (`vmware/client.py` ships in it verbatim).

### Safe deploys & crash-loop protection

A bad `git pull` (a syntax error in a pulled file) followed by a restart used to send the systemd unit into an **unbounded crash loop**: `Restart=on-failure` with `RestartSec=5` and — fatally — `StartLimitIntervalSec=0` (the start-rate limiter *disabled*) meant it relaunched every 5 seconds forever, each launch spraying a full startup banner into the logs until someone noticed. Three layers now prevent this:

- **Safe-deploy scripts** ([linux/deploy.sh](linux/deploy.sh), [windows/deploy.ps1](windows/deploy.ps1)) — the recommended way to update a running install on either platform. They pull (`--ff-only`), byte-compile every source file with `compileall`, and **only restart/relaunch if the compile is clean** — a broken pull leaves the currently-running instance untouched and prints the error, so a typo never causes downtime. The Linux script then confirms the service came back up, with a `journalctl` hint if it didn't.
- **systemd compile gate** ([linux/pingwatch.service](linux/pingwatch.service)) — `ExecStartPre` runs `compileall` before every start, so even a manual `systemctl restart` of broken code fails fast and cleanly instead of half-starting. `compileall` is purely syntactic (no imports, no side effects); the venv is excluded.
- **Bounded restarts** — `StartLimitIntervalSec=120` + `StartLimitBurst=5` replace the old unlimited setting: after 5 failed starts in 2 minutes systemd gives up and parks the unit in `failed`, so a genuinely broken deploy stops looping (and stops flooding the log) instead of retrying until manually halted.

Existing Linux installs pick up the unit hardening by re-running `sudo bash linux/start.sh --install-service` once (re-patches the unit + `daemon-reload`); routine updates then use `bash linux/deploy.sh`. Windows has no service supervisor — so no crash loop to bound — but `windows/deploy.ps1` provides the same compile-before-relaunch protection (a failed check skips the relaunch and keeps the old instance running).

### Audit log no longer dumps binary blobs

`settings_update` audit entries recorded a full `old → new` value for every changed setting — including `email_logo_data`, whose value is a base64 PNG data URI. Uploading a logo wrote the entire image (~2.4 MB on one real entry) as a single audit line, in both the audit **log file** and the capped audit **DB table**. The settings handler ([routes/settings.py](routes/settings.py) `_audit_val`) now masks secrets as before, **summarizes opaque blobs by size** (e.g. `email_logo_data: <2400022 bytes>`), and caps any other long value — so the trail still records *that* the logo changed without storing its contents. ([db/audit.py](db/audit.py) independently caps every detail field at 1024 chars as a backstop.)

---

## v1.3 — Distributed probes

Remote agents that run sensor probes inside branch offices, DR sites, and customer LANs, shipping results back over **outbound-only HTTPS** — nothing inbound is required at the branch. The central server stops doing per-probe work for remote sensors (scale) and gains reach into networks it can't route to.

### Architecture

- **Agent is dumb, server keeps the brains.** The agent schedules probes locally from a synced config and ships raw results `{ok, ms, value, detail, ts, rate?, snmp_type?}`. Debounce, thresholds, flap detection, alert profiles, and SSE all run through the existing pipeline: [core/state.py](core/state.py) `_run_once_inner` was split so its result-processing half — now `_process_result()` — can be fed remote results from the checkin handler with zero duplicated logic.
- **Assignment cascade** `sensor.probe_id → device.probe_id → site binding → central`, with the literal `'central'` as an explicit pin. Resolver + cached site map in [core/probe_assign.py](core/probe_assign.py); `probe_id` columns on devices/sensors/sites (both backends). The central scheduler skips probe-assigned sensors (`running` stays true so the UI doesn't show them paused); reassignment takes effect live via `apply_probe_assignment()`.
- **One transport rhythm.** `POST /api/agent/checkin` every ~10s carries the result batch and doubles as the heartbeat; the response piggybacks `config_version` + pending tasks. When a sensor's ok-state flips, the agent flushes immediately → server alerts within ~1s.
- **Offline story.** The agent spools to a bounded, restart-safe `spool.jsonl` and backfills **oldest-first** on reconnect: samples land with their original timestamps (gapless charts) but results older than a staleness cutoff are persisted sample-only — no event/alert replay of incidents that already ended. A per-sensor `_last_processed_ts` guard (lazy-seeded from `MAX(sensor_samples.ts)`) makes batch re-sends across server restarts duplicate-proof. The watchdog ([monitoring/probe_watchdog.py](monitoring/probe_watchdog.py)) fires exactly one `probe_offline` event after ~35s of silence; member sensors grey out as *stale*, never false-DOWN, and nothing ever auto-falls-back to central.
- **Remote IPAM scans + discovery.** A generic task channel (`agent_tasks` table → checkin pickup → chunked result upload) runs subnet sweeps **from the probe**: IPAM scans of subnets whose site is bound to a probe route there automatically; the Discovery page gains a "scan from" probe option. Results stream into the same in-memory `_SCANS` registry as local scans ([monitoring/subnet_discovery.py](monitoring/subnet_discovery.py)) — the poll/cancel/apply/bulk-add paths are untouched.
- **Remote device service scan.** The per-device Scan button (and the auto-scan after Add Device) follows the same cascade: when the device is measured from a probe, the scan runs **there** as a `device_scan` task — the handler long-polls the task channel and returns the usual service list (modal shows *"Scanned from 📡 ‹probe›"*), since central usually can't reach those hosts at all. Offline probe / too-old agent / timeout each produce a clear error instead of a false "No services found".

### Security

- **Enrollment**: admin creates a probe → one-time token (single use, 7-day expiry, bound to the probe record) baked into a downloadable pre-configured agent package; the agent exchanges it for a long-lived `pw_` bearer token with the new `probe` scope.
- **Scope jail**: probe tokens are valid **only** under `/api/agent/*` ([server.py](server.py) `_auth`/`_require`); the checkin handler additionally verifies every submitted `(did, sid)` resolves to that probe. The `api_tokens` scope CHECK was widened (`probe`) — table rebuild on SQLite, constraint swap on PG — and the user JOIN became a LEFT JOIN (probe principals have no user row, role stays empty).
- **TLS pinning**: the package embeds the server certificate's SHA-256; the agent verifies the peer cert fingerprint on every connection — self-signed-friendly MITM protection.

### Agent package

`agent/` in the repo + [core/agent_package.py](core/agent_package.py) builder: the Probes page downloads a zip containing `agent.py` (stdlib-only core), **verbatim copies** of `monitoring/probes.py`, `core/radius_auth.py`, and `vmware/` (tiny `core/` shims satisfy their imports), installers (`install.sh` → systemd, `install.bat` → Scheduled Task), and a generated `config.json`. ssh/sftp need paramiko, vmware needs pyvmomi, snmp needs the `snmpget` binary — the installers detect what's missing and offer to install **all of them** (interactive prompts default Yes; `--with-snmp` / `--with-ssh` / `--with-vmware` / `--all-optional` / `--no-optional` flags for unattended Linux installs), and the agent reports capabilities at checkin so the UI shows them as chips.

### UI

New **Probes** sidebar page (live status dot, last seen, agent version + *update available* badge, clock-skew warning, capability chips, spool depth, bound sites, sensor counts; add / download package / re-enroll / revoke / delete-with-reassign-dialog). "Measured from" dropdowns in the device, sensor, and site editors; a "via ‹probe›" pill on device cards with stale-grey rendering while the probe is offline; `probe_offline` / `probe_online` rows in Events. The Add/Edit Site dialog now uses the standard app modal style in the main app (the neon look remains Live-Map-only, [frontend/forms-site.js](frontend/forms-site.js)), and its probe dropdown no longer shows a duplicate "Central (this server)" entry.

### Observability

Dedicated **`logs/pingwatchprobes.log`** stream ([core/logger.py](core/logger.py), 5 MB × 5, `log_probes_max_mb`/`log_probes_backups` settings): enrollments, checkin/transport problems, rejected results, offline/online transitions, and task lifecycle — isolated from the main application log, with its own "Probes" tab in the Logs viewer. Branch-side, the agent keeps a rotating `agent.log` next to itself.

---

## v1.2 — Bug fixes

**Event ACK lost after service restart.** Sensor runtime state (`_alerted_down`, `_threshold_state`) is reset on every restart, so the first post-restart probe of a still-down sensor logged a brand-new `active` flap row alongside the previously-acknowledged one — looking to the operator like the ACK was dropped. New `db_load_unresolved_flap_state()` in [db/events.py](db/events.py) re-hydrates those flags (plus `_down_since_ts` / `_threshold_triggered_ts` for accurate durations) from unresolved `flap_log` rows during [db_load](db/persistence.py), on both SQLite and PG paths, before sensors start probing. Threshold precedence (crit over warn) matches the live escalation logic.

**Live-map link routing.** Several passes over the orthogonal SVG router in [frontend/livemap.js](frontend/livemap.js):

- *Wide fan-outs* — when a parent's children span much wider than the card (BladeCenter feeding hypervisors across the canvas), entry points are now ranked by child X and distributed evenly across the parent's bottom edge instead of clamped to the nearest corner. Eliminates duplicate exit points where two siblings on the same side stacked onto one X, and reflows automatically when groups are added or deleted.
- *Per-child sub-lanes + bus suppression* — with more than two children, each child gets its own Y row in the trunk band and the thick trunk-bus underlay is dropped; N horizontals read as N distinct lanes instead of one bar.
- *Line-count collapse with full hover data* — fan-outs with more than two children draw one line per child instead of one per port pair (4 children × 6 ports no longer means 24 parallel lines). The hover tooltip still lists every collapsed port mapping — the visual is reduced, never the data. Per-port parallel lines remain for peer links (LACP bundles between switches).
- *Hypervisor → VM breathing room* — the VM Clusters row gets a wider gap above it (`.sd-tier-vm` in [livemap.css](frontend/livemap.css)) since that band carries the densest routing.

---

## v1.1 — REST API tokens & dedicated API doc

Scoped Bearer-token authentication for scripts, CI, and Terraform — running alongside the existing browser cookie session, never replacing it. The full REST API reference moves out of DEVELOPER.md into its own [API.md](API.md).

### Bearer-token authentication

New `Authorization: Bearer pw_<token>` header path coexists with the cookie-session path inside a single resolver. A request is identified as either a `session` (implicit `full` scope) or an `api_token` (explicit `read` / `full` scope), and the rest of the stack treats them identically — same RBAC role check, same audit pipeline.

**Token shape.** `pw_` + `secrets.token_hex(32)` → 67 chars (visually distinct from session cookies). Plaintext is **shown once** in the create response; the DB stores only the SHA-256 hash (`_hash_token` in [core/auth.py](core/auth.py)). There is no recover-token flow — lose it and you revoke + regenerate.

**Scopes — method-based, enforced centrally.** Audit of every route module confirmed all mutating handlers use POST / PUT / PATCH / DELETE. A `read` token is therefore restricted in one place — `_require()` in [server.py](server.py) — to `GET / HEAD / OPTIONS`. No per-route declarations needed.

**SSE.** `EventSource` can't send custom headers, so an API token hitting `/events` is rejected with `400 {"error": "SSE requires cookie session"}` rather than failing the auth handshake silently. The block lives in both [`_auth()` and `_require()`](server.py) since `/events` calls `_auth` directly.

**Cache.** New `_API_TOKENS` dict + lock in [core/auth.py](core/auth.py) mirrors the `_SESSIONS` pattern but with different invalidation semantics (fixed `expires_at`, no sliding TTL). Cache TTL = 300s — that's how long revocation takes to propagate. Revoke explicitly evicts matching entries by hash via `auth_evict_api_token_hash()`.

### Database

New `api_tokens` table — dual SQLite ([db/core.py](db/core.py)) + PostgreSQL ([db/pg_schema.py](db/pg_schema.py)):

```sql
api_tokens(id PK, token_hash UNIQUE, name, username, scope CHECK('read','full'),
           created_at, expires_at NULL, last_used_at, revoked_at NULL)
```

with indexes on `username` and `token_hash`. CRUD lives in [db/api_tokens.py](db/api_tokens.py) using the existing `db.helpers` abstraction (one path, branches on `is_pg()` internally). Re-exported through [db/__init__.py](db/__init__.py).

### Routes — `routes/api_tokens.py`

Admin-only management endpoints, registered in the GET / POST / DELETE dispatch lists in [server.py](server.py):

- `GET /api/tokens` — list (no plaintext, no hash). `?user=<name>` filters.
- `POST /api/tokens` — `{name, scope, expires_at?}` → `201 {id, token: "pw_..."}` *(plaintext returned exactly once)*.
- `DELETE /api/tokens/{id}` — revoke (sets `revoked_at`, evicts cache).

A token-cannot-create-token rule blocks `POST /api/tokens` when the caller is itself an API token. Audit logs record `api_token_create` and `api_token_revoke` via [`db_log_audit`](db/audit.py); individual API calls are **not** audited (would be noise).

### Frontend — Settings → API Tokens tab

New `apitokens` admin-only tab in [frontend/forms-settings.js](frontend/forms-settings.js), wired into the Identity section after Users / Groups. Mirrors the Users-tab pattern: list table (Name · Owner · Scope · Created · Last used · Expires · [Revoke]) plus a "Generate Token" modal.

**One-time reveal.** The create response surfaces the plaintext in a dedicated modal with a copy-to-clipboard button, a warning that this is the only time the value will be shown, and a sample `curl` command using `location.origin`. Closing the modal removes the plaintext from the DOM permanently.

### Documentation

- New [API.md](API.md) at the repo root — feature-grouped REST reference with a "Getting started" preamble (create + use + revoke), an "Authentication" section explaining session vs. token + scope semantics, an SSE caveat, and an error-envelope convention. Token creation lives at the top of the "Authentication & API tokens" section so curl examples for any later endpoint can assume a token in hand.
- [DEVELOPER.md](DEVELOPER.md) lines 694–975 (the previous in-tree API tables) are replaced with a 3-line pointer stub to API.md. The architecture, schema, and contributor notes around it are unchanged.

### Verified

End-to-end backend test (synthetic SQLite DB + monkeypatched STATE) covers: create → auth check → unknown-token rejection → non-bearer rejection → list filters → revoke-with-cache-eviction → expired-token rejection. **9/9 pass.**

**Files**: [db/api_tokens.py](db/api_tokens.py), [db/core.py](db/core.py), [db/pg_schema.py](db/pg_schema.py), [db/__init__.py](db/__init__.py), [core/auth.py](core/auth.py), [server.py](server.py), [routes/api_tokens.py](routes/api_tokens.py), [frontend/forms-settings.js](frontend/forms-settings.js), [API.md](API.md), [DEVELOPER.md](DEVELOPER.md).

---

## v1.0 — New UI Design

Major visual refresh based on a hi-fi design prototype exported from claude.ai/design (see [MIGRATION_NOTES.md](MIGRATION_NOTES.md) for the full handoff history). Backend behavior is unchanged except for one additive endpoint (Active Sessions). All view-container IDs, RBAC class hooks, localStorage keys, and JSON contracts at `/api/*` are preserved.

### Live Map — new NOC console (M1a) and per-site drill-in (M1b)

Brand-new top-level page at `/livemap.html` (icon "Live Map" in the rail) that replaces the live overlay that used to live on the NTM tab. The old NTM page is now manual-only and renamed **Topology Design**. The Live Map is its own iframe — independent layout, independent state — so it doesn't fight the manual canvas editor for DOM ownership.

**M1a — NOC Overview.** Default route `#/noc`. Four hero stat cards (SITES, DEVICES, ACTIVE ALERTS, UPTIME · 24H) with stacked up/warn/down bars; **site-health mosaic** with `grid-auto-flow: dense` cells sized by `sqrt(deviceCount)` and tinted by worst-status (click to drill in); **OFF-Site internet widget** rendering pinned reachability checks (latency or "— timeout"); **Sites by Type** bars per kind; **Top Problem Sites** ranked by active alerts; **Recent Alerts** feed (last 8 events, time-ago text refreshed every 5 s, paused via `visibilitychange` when the iframe is hidden).

**M1b — Site Drill-In.** Route `#/site/<name>`. Flex-row tier layout: FIREWALL row centered, SWITCHES, HYPERVISORS (with IPMI inline at the trailing edge), VM CLUSTERS. Cluster cards show a mini status dot-grid (one cell per child device, auto-fit columns by `sqrt(count)`). Tier inference (`monitoring/site_tree.py`) is regex-based with a fallback to TIER_HYPERVISOR so unclassified servers still render as a cluster instead of disappearing. Internet-kind sites render as a flat reachability grid instead of forcing a tier structure that doesn't apply.

**Data plane.** `monitoring/site_rollup.py` produces `site_summary_list()` (per-site up/warn/down/alerts/devices) and `noc_summary()` (hero stats, by-kind, top problems, recent alerts, off-site). Reads `STATE.devices`, `alert_events` (active/acknowledged), and `flap_log` (uptime/flap/incident counts over 24 h). `flap_log.ts` is TEXT (ISO `YYYY-MM-DDTHH:MM:SSZ`), so `_iso_utc()` and `_parse_iso_ts()` bind ISO strings rather than epoch ints to keep both PG and SQLite comparators happy. `resolved_at` is REAL/DOUBLE — the rollup uses `COALESCE(resolved_at, 0) AS rts` and treats anything > 0 as resolved.

**Routes** (read-only, `viewer` role):
- `GET /api/livemap/sites` — sidebar mosaic + rollup
- `GET /api/livemap/noc/summary` — NOC widgets
- `GET /api/livemap/sites/{name}/tree` — drill-in tier tree

**SSE wiring.** `frontend/app.js` `_sseBatch` flush also `postMessage`s `{type:'lm_update'}` to the livemap iframe. The iframe coalesces 2 s windows, hashes the resulting payload (`name + up/warn/down/alerts + summary totals`), and skips re-render when the hash is unchanged — eliminates flicker on idle SSE bursts.

**Files**: [frontend/livemap.html](frontend/livemap.html), [frontend/livemap.css](frontend/livemap.css), [frontend/livemap.js](frontend/livemap.js), [monitoring/site_rollup.py](monitoring/site_rollup.py), [monitoring/site_tree.py](monitoring/site_tree.py), [routes/livemap.py](routes/livemap.py). Sidebar nav + iframe pause/resume + theme/postMessage relay added to [frontend/index.html](frontend/index.html), [frontend/icons.js](frontend/icons.js), [frontend/app.js](frontend/app.js), [server.py](server.py).

### Sites — metadata sidecar table + CRUD UI

A new `sites` table stores presentation metadata for the Live Map sidebar pills, mosaic tint, and Sites by Type widget. Distinct site names still come from `devices.site` and `ipam_subnets.site`; this table only carries `kind` (`internet`/`hq`/`dc`/`lab`/`pop`/`edge`/`office`), `pinned` flag, `display_name`, `sort_order`, and timestamps. Rows are auto-created lazily by the Live Map rollup the first time it encounters an unseen site name, so fresh installs need no seeding.

**Schema** ([db/core.py:577](db/core.py#L577), [db/pg_schema.py:153](db/pg_schema.py#L153)): idempotent `CREATE TABLE IF NOT EXISTS sites (...)` on both backends; PG uses `BIGINT` for timestamps, SQLite uses `INTEGER`. The IPAM `/api/sites` UNION was extended to include `sites.name` so the Devices and IPAM autocomplete pickers also see metadata-only sites.

**CRUD UI in the Devices tab.** Decision: site CRUD lives in Devices (not the Live Map sidebar) because the Devices tab is where users already manage device→site assignments. The Devices toolbar gets a `+ Site` button; each site header gets a cog (⚙ Edit Site) and a right-click context menu with Edit Site / Add Site. The modal ([frontend/forms-site.js](frontend/forms-site.js)) is loaded by both the main app and the Live Map iframe; it self-injects its own CSS when run outside the iframe (because `livemap.css` only loads inside it). After save it broadcasts to every refresh hook in the current context (`_refreshDevices`, `_lmRefresh`, and a `postMessage` to the livemap-frame).

**Cascade delete.** The delete flow first calls `GET /api/sites/meta/<name>/usage` to fetch `{devices, subnets}` counts and shows a second confirm modal with a cascade checkbox (defaulted ON when usage > 0). On confirm, `DELETE /api/sites/meta/<name>?cascade=1` clears `devices.site` and `ipam_subnets.site` for every row tagged with that name as well as removing the metadata row.

**Endpoints**: `GET /api/sites/meta` (merged with distinct names), `POST /api/sites/meta`, `PUT /api/sites/meta/{name}` (accepts `new_name` + `also_rename` for bulk-rename of `devices.site`), `GET /api/sites/meta/{name}/usage`, `DELETE /api/sites/meta/{name}?cascade=0|1`. All writes `operator` role + audit-logged. New `_to_int()` helper in [routes/sites.py](routes/sites.py) returns the default for non-numeric input so a garbage `pinned` value never crashes with a 500.

**Files**: [db/sites.py](db/sites.py), [routes/sites.py](routes/sites.py), [frontend/forms-site.js](frontend/forms-site.js); [routes/ipam.py](routes/ipam.py) (extends `/api/sites` UNION); [frontend/devices.js](frontend/devices.js) (toolbar button + site-header cog + context menu); [frontend/ipam.js](frontend/ipam.js) (empty-site placeholders for metadata-only sites).

### Expandable rail sidebar

The 56 px icon rail gets a toggle button that expands it to 180 px with tab name labels next to each icon. Expansion **pushes** content (does not overlay) — `#layout` uses `grid-template-columns: var(--rail-w) 1fr` and the expanded state is keyed off `#layout:has(> .rail.expanded)` (with a `.rail-expanded` class fallback on `#layout` for browsers without `:has()`). Preference persisted in `localStorage.pw_rail_expanded`. Implementation: [frontend/app.js](frontend/app.js) (`_railToggle` / `_railRestore`), [frontend/style.css](frontend/style.css) (`.rail.expanded` rules + grid override).

### Add Widget modal — redesign

The dashboard's "+ Add Widget" picker was rebuilt around a hi-fi design (see [design/handoff_add_widget_modal/README.md](design/handoff_add_widget_modal/README.md)). The new modal has a search input (Ctrl-K focus), category chips (ALL / RECENT / Charts / Status / Events / Reports / Network) with live counts, sectioned 3-column tile grid (RECENTLY USED · POPULAR · per-category), and a side-popout panel that renders a real DOM-backed mini-preview on hover.

**Registry extensions** in [frontend/dashboard.js](frontend/dashboard.js):
- `_DW_REG` entries gain `cat`, `desc`, `meta` (string list shown in the popout), `popular`, `isNew`.
- `_DW_CATS` palette + `_DW_CAT_ORDER` define the section order and category colours.
- `_DW_PREVIEW` registers 18 builders mapping every widget type to one of 12 reusable mini-render helpers (`_mpSparkline`, `_mpDevicePills`, `_mpUptimeBar`, `_mpGauge`, `_mpFlapList`, `_mpBars`, `_mpHeatmap`, `_mpDots`, `_mpSla`, `_mpInternet`, `_mpRing`, `_mpLicense`).

**Interactions.** Live search filters tiles and sections; chips toggle (re-clicking the active chip clears back to ALL); ↑↓ moves the hover state with scroll-into-view; Enter adds the highlighted tile; click-on-tile adds immediately; widgets requiring per-instance config (device/sensor selectors) still hand off to the existing config-form path. Recent tracking via `localStorage.pw_widget_recent` (capped at 6). Add-confirmation surfaces a `.mw-toast` slide-in inside the popout column so the picker can stay open for adding multiple widgets in a row. The picker's `_dwPickerAdd` and `_dwClosePicker` are closure-scoped (no `window` pollution); close buttons in the template carry `data-mw-close` and are wired with `addEventListener` after the modal is appended.

**Styles** ([frontend/style.css](frontend/style.css)): complete `.mw-*` block — modal shell, search, chips, body, sections, tiles, popout, foot, mini-preview helpers, `mw-toast` slide-in keyframes; full `:root[data-theme="light"]` override block for theme parity.

### Sensor History Chart widget

A configurable per-sensor chart widget for the dashboard. Picks a device + sensor and renders the same `sensor_chart` history view that lives in the Sensor detail panel, scoped to the dashboard's time-range selector. Avoids the flicker that an unthrottled SSE refresh would cause: refreshes guarded by `_dwChartLastFetch` with a 5-second minimum gap, and the time-range argument now correctly threads through `_dwTimeRangeMinutes()` instead of an undefined `w.cfg.minutes` reference.

### Topology Design — strip + bug fixes

The old NTM page is renamed **Topology Design** in the sidebar and is manual-only now. The PingWatch Live tab entry points (`switchToPingWatchPage`, `loadPingWatchPage`) and the dead REFRESH button were removed; the `.inc-*` CSS section (incident cards) was deleted from [map.css](frontend/map.css). About 500 lines of unreachable live-mode helpers (`renderPingWatchCanvas`, `_pwLiveUpdate`, `showPwDashboardPanel`) remain in [map.js](frontend/map.js) pending a post-1.0 cleanup — they're inert without the entry points but interconnected enough that surgical removal pre-release was too risky.

**Bug fix**: clicking "Topology Design" used to land on an empty Main tab; you had to switch to another tab and back to see devices/links. Root cause: with the live tab gone, `isPingWatchPage` defaults to `false`, and the boot path called `switchPage(pages[0].id)` while `currentPageId` was already `1` (the Main page id) — the early-return guard at the start of `switchPage` matched and never called `loadData()`. New `_pageDataLoaded` flag in [map.js](frontend/map.js) blocks the early-return until the first load completes.

### 1.0 pre-release audit fixes

Surgical fixes uncovered by the pre-1.0 audit pass. Full report lives in chat history; the highlights:

- **HIGH** — `db_rename_site_meta` was broken on PostgreSQL because `cursor.execute(...).fetchone()` chaining only works in SQLite (psycopg2's `execute` returns `None`). Split into two statements ([db/sites.py:135-148](db/sites.py#L135)).
- **HIGH** — `monitoring/network_map.py:_conn()` was used as `with _conn() as con:` everywhere, but `sqlite3.Connection.__exit__` only commits — it never closes. Refactored `_conn()` into a `@contextmanager` that commits on success, rolls back on failure, and *always* closes ([monitoring/network_map.py:10-26](monitoring/network_map.py#L10)). About 20 callers fixed by the single refactor.
- **HIGH** — Removed developer-name leak from the VM tier inference regex ([monitoring/site_tree.py:39](monitoring/site_tree.py#L39)).
- **MEDIUM** — Three SQLite helpers in [routes/export.py](routes/export.py) (`_validate_sqlite`, `_vacuum_file`, `_detect_db_kind`) only closed the connection on the happy path; wrapped each in `try/finally` so a malformed upload no longer leaks a handle. `_validate_sqlite` no longer returns `str(e)` either — generic message + server-side log.
- **MEDIUM** — `routes/ipam.py` subnet-add no longer surfaces `str(e)` (uses `e.args[0]` from the curated `ValueError` so the user-safe message survives but unrelated exceptions can't piggyback). LDAP test-connection / test-auth endpoints now return `"unexpected {ExceptionType}; check server log"` instead of leaking the raw message.
- **MEDIUM** — `_DW_PREVIEW` builder failures `console.warn` instead of failing silently so QA can notice regressions.
- **LOW** — `livemap.js` site-canvas click listener now binds once in `boot()` (was re-bound per site render — would accumulate after N navigations). `liveTickTimer` pauses on `visibilitychange` instead of burning CPU when the iframe is hidden. `routes/sites.py` `_to_int()` helper avoids 500s on garbage `pinned`/`sort_order` input. Add Widget modal's `_dwPickerAdd` / `_dwClosePicker` moved off `window` into closures.

### NTM Live — professional auto-layout (tier-ordered + orthogonal links)

The auto-layout that the NTM Live tab produces on a pristine canvas was redesigned to look like a NOC tool instead of a shelf-packed grid. Four changes work together:

1. **Orthogonal link routing.** Every link — manual + auto-discovered + bundled — now draws as a right-angle elbow (`<path d="M.. L.. L.. L..">`) instead of a straight `<line>`/cubic Bezier. Endpoints anchor on the rect edges facing the partner via `_edgeAnchor()`, so arrowheads land cleanly at the node boundary instead of stabbing inward. The `_orthoPath(x1,y1,x2,y2)` helper picks H-V, V-H, H-V-H, or V-H-V automatically based on the dx/dy ratio. Bundled links share the bundle's midX so N siblings collapse into one trunk — preserving the bundling effect the old cubic Bezier provided. Tunnel links (ZTNA / IPsec) keep their curved style as a distinct visual cue for encrypted overlays.
2. **Tier-ordered groups within each site.** Each group gets a tier 1..5 derived from the highest-priority topology role of any device in it (gateway=1, core=2, backbone=3, switch=4, endpoint=5 via `_groupTier()`). Inside a site frame, groups stack by tier — gateway/FW groups anchor the top, switches mid, endpoint groups (VMs, IPMI, servers) at the bottom — with `TIER_ROWGAP=30px` between tiers for visual separation.
3. **2D corner-based bin packing** (replaces fixed `COLS=3` shelf wrap). Within each tier band, groups sort by area descending; the largest group anchors the top-left of its tier, and smaller groups slot into the topmost-leftmost free corner alongside or below larger ones via the new `_fitFreeCorner()` helper. A tier with one huge group + several narrow ones used to draw the narrow ones in their own cramped rows below — now they pack alongside the huge one in the vertical gap to its right, dramatically reducing site height. The same primitive packs sites onto the canvas: with a small rank-0 site (e.g. OFF-Site/Internet, ~200×300px) plus a huge rank-2 site (e.g. main LAN, ~4500×2500px), the corner packer places the small site top-left and slots the large one beside it at top-right instead of forcing it onto a fresh row below — eliminating the dead horizontal whitespace.
4. **Site ranking — WAN/edge sites anchor the top.** Sites named `Internet`, `OFF-SITE`, `WAN`, `Cloud`, `External`, or `Edge` (case-insensitive) get rank 0; sites containing only gateway/core groups get rank 1; internal sites rank 2 (via `_siteRank()`). The sort comparator orders sites by rank ascending → device count descending → name, so the corner packer encounters rank-0 sites first and they naturally claim top-left positions.

**New button**: `⚡ AUTO-ARRANGE` in the dashboard panel actions ([frontend/map.js](frontend/map.js)). One click → confirmation dialog → wipes both `pwGroupOverrides` and `pwOverrides` → re-renders into the tier-based layout. Manual drags between clicks are preserved (existing `RESET LAYOUT` behavior reused via `autoArrangePwLayout()` wrapper that adds a position-count-aware confirmation).

**Out of scope for this redesign**: A* link routing that avoids crossing group rects (naive dogleg may cross intermediate groups — flag as follow-up if it looks bad in practice); tier-band background colors (deferred polish); Auto-Arrange undo (manual re-drag covers it).

**Files**: [frontend/map.js](frontend/map.js) (`_orthoPath`, `_edgeAnchor`, `_TIER_BY_ROLE`, `_groupTier`, `_WAN_KEYWORDS`, `_siteRank`, `_fitFreeCorner`, `autoArrangePwLayout`, rewrites of `renderPwAutoLinks`, `renderPwLinksInLayer`, `buildLink`, and `calcPwLayout` PASS 1+2). Pure frontend — no backend, no DB, no API changes.

### NTM Live — auto-link suppression + bundling

Two reductions applied at the dedup step of [`_pwComputeAutoLinks()` in frontend/map.js](frontend/map.js) to keep the auto-link layer readable on a multi-group canvas:

1. **Intra-group suppression** — pairs where src and tgt share the same `(site, group)` bucket are dropped. The group frame already implies adjacency; N individual fan-out lines inside one frame add noise without information.
2. **Cross-group bundling** — for the lines that DO cross group boundaries, the renderer collapses every `(source-group, target-device)` fan-out into a single representative link (first device of that group as src endpoint). A 25-device cluster all anchored to a switch in another group now draws ONE line instead of 25 parallel lines converging on one tile.

Together: a network with 80 devices in 10 groups all anchored to one switch goes from ~80 auto-links down to ~10 — typically a 80–90% line-count reduction while preserving the cross-group topology shape. Manual `pwLinks` are unaffected — they always render and continue to suppress matching auto-link pairs.

### IPAM — VLAN ID per subnet

Each subnet can now carry an optional 802.1Q VLAN ID (1..4094) for cross-reference with switch port config and topology. The sidebar shows a small `V{id}` chip next to the CIDR, and the subnet filter at the top of the sidebar matches against VLAN text (`vlan 100` or just `100` both work).

**Schema** ([db/core.py:557-563](db/core.py#L557), [db/pg_schema.py:276](db/pg_schema.py#L276)): idempotent `ALTER TABLE ipam_subnets ADD COLUMN vlan INTEGER DEFAULT 0` on both backends. `0` = untagged / no VLAN.

**Backend** ([db/ipam.py](db/ipam.py)):
- `_SUBNET_COLS` SELECT extended with `COALESCE(vlan,0) AS vlan`; row mappers return `vlan: int`.
- `db_add_subnet(...)` gains `vlan: int = 0` kwarg with 0..4094 clamping.
- `_SUBNET_UPDATABLE_FIELDS` gains `"vlan": ("INT_RANGE", (0, 4094))`; new `INT_RANGE` field kind clamps out-of-range values to 0 rather than dropping the field (recoverable behavior — silent drop would leave the old VLAN in place).

**Routes** ([routes/ipam.py](routes/ipam.py)): POST `/api/ipam/subnets` reads `body.vlan`; PATCH `/api/ipam/subnets/{id}` accepts `vlan` and audits as `vlan={N|untagged}`. Both validate to 1..4094 with out-of-range → 0 fallback.

**Frontend** ([frontend/ipam.js](frontend/ipam.js), [frontend/style.css](frontend/style.css)):
- Add/Edit Subnet modals get a number input (`min=1 max=4094`, blank = untagged).
- Sidebar card renders `<span class="ipam-subnet-vlan">V{id}</span>` chip inline next to the CIDR.
- Search filter matches `vlan {id}` and `{id}` against the VLAN field.
- Active card inverts the chip colors so the chip stays readable on the accent background.

### Edit Device modal — tabbed layout

The Edit Device modal grew tall enough to scroll past one viewport once Topology Role, Secondary IPs, Licenses, Alert Profile, and Default Credentials all stacked vertically. Replaced the single-column scrolling form with four tabs (matches the Settings modal's nav pattern):

- **General** — Device Name, Host/IP, Site, Group, Topology Role, Mute toggle, Alert Profile. The two paired fields per row (Host+Site, Group+Role) use `.fgrid` so the tab fits in one viewport.
- **Networking** — Secondary IP Addresses (was an auto-opened `<details>` block on the General tab). Counter chip on the tab label ("Networking (3)") via [`ed-tab-net-count`](frontend/forms-device.js).
- **Credentials** — Default Credentials section (SNMP community, SNMP version, v3 block, VMware user/pass) — was a `<details>` block below Alert Profile.
- **Licenses** — License management (was a `<details>` block). Counter chip on tab label via `ed-tab-lic-count`.

**Mechanics** ([forms-device.js](frontend/forms-device.js)):
- New `_edSwitchTab(name)` toggles `.ed-tab-pane` visibility and `.itab-active` class on the matching `.ed-tab` button. Stateless re-click is a no-op.
- `_edSipRender()` now updates the Networking tab counter (replaces the old details summary span lookup).
- `_edLicRender()` updates both the in-pane `ed-lic-count` and the new tab-button `ed-tab-lic-count`.
- `_edLicLoad()` no longer attempts to auto-open a `<details>` element — that section is now a tab and is reachable by user click.

**Styling** ([style.css](frontend/style.css)): `.ed-tabs` (flex row, 14px bottom margin) reuses `.itab` / `.itab-active` pill style from elsewhere in the codebase. `.ed-pane-hdr` + `.ed-pane-sub` give each non-General tab a consistent title/description pair. `.ed-tab-cnt` colors the count chip per active/inactive state.

All existing input IDs preserved (ed-n, ed-h, ed-site, ed-g, ed-role, ed-am, ed-snmp-comm, ed-snmp-ver, ed-v3-*, ed-vmw-*, ed-sip-*, ed-lic-*) so `submitEditDevice` and every cred/V3/lic helper continues to work without changes.

### NTM Live — auto-links from IPAM topology (4-tier enterprise model)

The Live map now infers network topology automatically from IPAM subnet membership and four role tags on `ip_allocations.kind`: `switch` (access), `backbone` (aggregation/distribution), `core` (central L3), `gateway` (edge/FW). Models the standard 3/4-tier enterprise topology where backbones aggregate floor switches into the core, and core fronts the firewall. User-drawn manual links continue to win visually; auto-links render as a dim dashed layer underneath. Eliminates the need to hand-draw every cable for the 80% obvious "device → switch → backbone → core → gateway" topology.

**Role model.** Reuses `ip_allocations.kind` ([`_KIND_OK` in routes/ipam.py:312](routes/ipam.py#L312) extended to include all four). Tag the access switch in each subnet as `switch`, aggregation switches as `backbone`, central L3 switches as `core`, the firewall/exit router as `gateway`. Each tier is optional — auto-link cascade skips unused tiers.

**Inference rules** (in [`_pwComputeAutoLinks()` in frontend/map.js](frontend/map.js)):
1. Each non-tagged device in a subnet fans out to the subnet's first tagged member, with priority **switch > backbone > core > gateway**. Covers the case where a "core subnet" has no `switch` tag — its devices anchor to a tagged core or backbone or gateway that lives in the same subnet.
2. Each `switch` uplinks to the first **backbone → core → gateway** in the same site (cascading fallback).
3. Each `backbone` uplinks to the first **core → gateway** in the same site.
4. Each `core` uplinks to the first **gateway** in the same site.
5. Cross-site mesh at the highest shared tier — `core ↔ core` across sites when any core exists anywhere, else `backbone ↔ backbone`.

Pairs already covered by a manual `pwLink` are dropped — no duplicate drawing.

**Backend additions** (all additive, no schema changes):
- [`db/ipam.py`](db/ipam.py) — `db_set_device_role(did, host, role) -> int` finds the IPAM allocation(s) for a device's host IP and UPDATEs `kind`; `db_get_device_roles() -> {did: kind}` returns a single map for the renderer and editor.
- [`routes/devices.py`](routes/devices.py) — new `PUT /api/device/{did}/role` accepts `{role: 'switch'|'gateway'|'backbone'|''}`, calls `db_set_device_role`, audited as `device_role`.
- [`routes/ipam.py`](routes/ipam.py) — new `GET /api/topology/roles` returns `{roles: {did: kind}}`. Read by both the map renderer and the device editor's Role dropdown loader.
- New regexes in [`core/config.py`](core/config.py): `_RE_TOPOLOGY_ROLES`, `_RE_DEVICE_ROLE`.

**Frontend — device editor Role dropdown** ([frontend/forms-device.js](frontend/forms-device.js)):
- Add modal: `<select id="ad-role">` with — None / Switch / Gateway / Backbone. PUTs after device creation only when non-empty.
- Edit modal: `<select id="ed-role">` lazy-loaded via `_loadDeviceRole(did)`; `data-orig` snapshot lets submit detect changes and PUT only when the role actually changed. 60s cache on `window._pwRolesCache`, invalidated after any successful PUT.

**Frontend — auto-link rendering** ([frontend/map.js](frontend/map.js), [frontend/map.html](frontend/map.html)):
- New globals `pwRoles` + `pwSubnets`, loaded in `loadPingWatchPage` Promise.all alongside the other settings fetches.
- Pure-JS IPv4-in-CIDR helpers (`_pwIpv4ToInt` / `_pwSubnetForIp`) — picks the most-specific (longest-prefix) matching subnet per device.
- New SVG layer `<g id="auto-links-layer">` in [map.html](frontend/map.html) between `groups-layer` and `links-layer` so auto-links render BELOW manual links.
- `renderPwAutoLinks()` draws each inferred link as a dashed line (`6 5` pattern), `stroke-opacity:0.35`, `pointer-events:none`. Colors: l2=cyan, l3=violet, wan=amber. Called from `renderLinks()` so auto-links auto-update on every drag/redraw.

### NTM Live — side-by-side packing for small sites

Replaces the strict vertical site stack (which left a huge empty right margin under small sites) with shelf-packing: small sites pack side-by-side under or beside larger ones, wrapping only when a row exceeds the widest site's width. Two-pass refactor in [`calcPwLayout()`](frontend/map.js): PASS 1 measures each site's natural width/height with per-entry offsets recorded but not committed; PASS 2 walks the sites in alphabetical order assigning final `(x, y)` and replaying offsets to produce group hints. `maxRowW = max(siteW)` so the widest site sets the wrap boundary. Preserves the user's "Reset Layout" deterministic ordering and all existing user-saved group positions (Phase 1 fixed-rect path is unchanged).

### Site hierarchy — Phase E: auto-discovery propagation

When auto-discovery creates a new device from an IPAM subnet, the subnet's `site` tag is now propagated onto the new device automatically. Existing devices in the subnet are NOT touched — only new finds inherit (back-filling would require a separate one-shot tool).

- [`monitoring/auto_discovery.py:545`](monitoring/auto_discovery.py#L545) — read `subnet["site"]` and pass it through to `_build_device_specs(..., site=_subnet_site)`.
- [`monitoring/auto_discovery.py:610`](monitoring/auto_discovery.py#L610) — `_build_device_specs()` gains `site=""` kwarg; threads through into each device spec dict.
- [`core/device_importer.py:189, 207`](core/device_importer.py#L189) — `create_devices_batch()` reads `item.get("site")` and passes it as kwarg to `STATE.add_device(name, host, group, site=site)`. Truncated to 80 chars to match the PATCH/bulk validator.

### Site hierarchy — Phase D++: per-site mini-grid placement (vertical stack)

Follow-up to Phase D+. The backdrops worked but they were wrapping an old single-row layout that ran every group through one global 3-col grid regardless of site, leaving big sites sprawling 7500px wide while small sites became tiny dots on the far right. The placement engine now thinks in **site cells**: each site is its own self-contained block with a 3-col mini-grid inside, and sites stack vertically on the canvas (alphabetical, Unsited last). Sized to content — small sites don't waste space, big sites grow downward.

- **Per-site mini-grid hint computation** in [`calcPwLayout`](frontend/map.js) — entries are grouped by site (siteBuckets), each site's groups laid out in a 3-col grid inside the site's content area, sites stack with `SITE_GAP=40px` vertical separation. `siteNaturalFrames` tracks each site's outer rectangle (header + content + padding) so backdrops align exactly with the placement.
- **Phase 2 anchor changed** — un-positioned groups now use their natural per-site grid hint directly instead of the old "right of bounding box" anchor (which was what flattened every Reset Layout into one horizontal strip regardless of site). User-positioned groups (Phase 1, with saved x/y) still take precedence — existing custom layouts are preserved.
- **Backdrop builder reuses placement constants** — `SITE_PAD` / `SITE_TITLE_H` are declared once at the top of the placement section and reused by the syntheticSites loop, so the rendered backdrop sits flush with the natural group layout.

### Site hierarchy — Phase D+: NTM Live tab site backdrops + Site Stats panel

Builds on Phase D's composite-key bucketing (groups already distinct per site). Adds actual VISIBLE site representation on the canvas + a Site Stats sidebar so a glance at the Live tab tells you "HQ is 6/8, DR is 8/8" without clicking around. Inspired by the new design's site-card grid but evolves rather than replaces the operational layout — every device + connection line stays visible.

- **calcPwLayout — entries sorted by site first** ([frontend/map.js:430](frontend/map.js#L430)) so "Reset Layout" naturally clusters groups belonging to the same site instead of interleaving them. Unsited entries sort last via a high-codepoint sentinel.
- **Per-site bounding box computation** in calcPwLayout: after group placement finalizes, walks `placedRects` and builds a min/max bbox per site, then emits `syntheticSites` entries with x/y/w/h padded for the title bar and breathing room (24px gap + 34px header). Unsited groups stay unwrapped — rendering a backdrop around them would just be noise.
- **Deterministic site colors** from an 8-color palette indexed by sort order, so identity is stable across reloads. Colors are muted purples/cyans/greens to stay distinct from the cyan/accent palette used by group frames.
- **New SVG layer `#sites-layer`** ([frontend/map.html:81](frontend/map.html#L81)) inserted between `bg-layer` and `groups-layer` so site backdrops render BEHIND group frames.
- **`renderSites()`** ([frontend/map.js](frontend/map.js)) — draws a tinted rounded rectangle per site with a solid header bar across the top carrying the site name (Orbitron, uppercase) and a device-count chip on the right (`N devices`). Non-interactive in v1 — positions track the underlying groups; no resize/drag handles to compete with the group-frame interactions.
- **`SITE STATS` panel** in the right dashboard ([frontend/map.js `_buildSiteStatsSection`](frontend/map.js)) — one row per site with a coloured status dot (green=all up, yellow=mixed, red=any down) + site name + `up/total` count. Section is omitted entirely when there are no sites, so single-site / all-Unsited fleets see the cleaner pre-existing layout.
- **CSS** in [frontend/map.css](frontend/map.css) — `.pw-site-stats` + `.pw-site-stat-row` + `.pw-site-stat-dot` + `.pw-site-stat-name` + `.pw-site-stat-val`, with light-theme softening.

### Site hierarchy — Phase D: NTM Live tab visible site separation

The Live map's auto-laid-out group frames now distinguish Site → Group buckets visibly. Same group name under different sites renders as distinct frames (mirroring the Devices tab), and each frame's title carries the site prefix (`HQ → Servers`). For v1, the layout stays single-axis — no nested outer site frame (that would require a bigger placement rewrite).

- [`frontend/map.js:379` `calcPwLayout()`](frontend/map.js#L379) — bucket key changes from bare `group` to composite `"<site><group>"`. New `gtitle` carries the human label (`HQ → Servers` or just `Servers` for Unsited). `syntheticGroup.name` now holds the friendly title; new `syntheticGroup.key` carries the composite for downstream lookups.
- **`pwGroupOverrides` keying** becomes composite. One-shot migration on first run: bare-key entries are re-keyed with leading `` (Unsited bucket) so saved positions are preserved. Gated via `localStorage.pw_group_overrides_site_v1='1'`.
- **Cleanup pass** (`_pwCleanupOrphanGroups`) — live key set now built as composite keys; stale entries evict correctly.
- **Color/reset handlers** (`setPwGroupColor` / `resetPwGroupColor`) — parameter renamed to `gkey` (composite); `groups.find(x => (x.key || x.name) === gkey)` falls back to `.name` for backwards-compat. Panel rendering reads `g.key || g.name` for `pwGroupOverrides` lookups.
- **Placement bookkeeping** at lines 573 + 594 — compares `sg.key === gname || sg.name === gname` so post-grow size/position updates target the right synthetic entry.

### Site hierarchy — Phase C: additive alert cascade + Site scope

The load-bearing piece of the Site hierarchy: alert profiles can now target a **Site** scope, and the cascade is **additive by default** so a NOC site-profile fires alongside a server-team group-profile on the same incident. Pre-existing profiles are migrated to `exclusive=true` so today's first-match behavior is preserved byte-for-byte until users explicitly opt in.

- **New column `alert_profiles.exclusive INTEGER NOT NULL DEFAULT 0`** on both backends. Idempotent `ALTER TABLE` ([db/core.py:567-572](db/core.py), [db/pg_schema.py:493](db/pg_schema.py) `ADD COLUMN IF NOT EXISTS`).
- **One-shot migration** (`exclusive_v1`) runs once per DB: `UPDATE alert_profiles SET exclusive=1` on all pre-existing rows, then `INSERT INTO schema_version (1001, …, 'exclusive_v1=done')` so re-runs are no-ops. Fresh-install Default profile is seeded AFTER the migration, so it stays exclusive=0 (additive).
- **`_VALID_SCOPES` extended to include `'site'`** in [routes/alert_profiles.py:42](routes/alert_profiles.py#L42). `_clean_profile_body` plumbs the new `exclusive` flag (defaults to False for new profiles).
- **DB layer** ([db/alert_profiles.py](db/alert_profiles.py)) — `_AP_COLS` extended; `_profile_row()` returns `exclusive: bool`; `db_save_profile()` writes the new column in both INSERT and UPDATE paths.
- **Engine cascade rewrite** ([monitoring/alert_profile_engine.py](monitoring/alert_profile_engine.py)):
  - New `resolve_profiles_for_sensor()` returns a **list** of matching profiles, narrowest → broadest (sensor → device → group → **site** → global). Walks every level, collecting every match. After each match, if `profile.exclusive` is True, broader-scope profiles are NOT added (narrower siblings already collected stay).
  - Old `resolve_profile_for_sensor()` kept as a back-compat wrapper returning the first (narrowest) match — no external caller signature broken.
  - Per-sensor cache fields: new `_resolved_profile_ids` / `_resolved_profiles` (lists), back-compat aliases `_resolved_profile_id` / `_resolved_profile` point at the first match.
  - `evaluate_and_fire()` iterates the cascade list, extracted per-profile stage logic into new `_evaluate_profile_stages()` helper. Post-recovery cleanup uses the narrowest profile as the representative (db_log_event dedups by `(did, sid)` so multi-profile fires share one event row, but dispatch fan-out is preserved — separate emails to separate recipients).
  - `_build_ctx()` now surfaces `dev.site` in the ctx dict so email/webhook templates and maintenance-window matching can use it.
- **Maintenance windows ride along** ([routes/maintenance_windows.py:19](routes/maintenance_windows.py#L19), [monitoring/alert_dispatchers.py:81-83](monitoring/alert_dispatchers.py)) — `_VALID_SCOPES` adds `'site'`; `check_maintenance()` matches `ctx['site']` for site-scoped windows. No DB change (scope_value is already free-text TEXT).
- **Frontend — profile editor** ([frontend/alerting.js](frontend/alerting.js)) — scope dropdown adds `Site`; scope_value input gets a `<datalist>` populated from `/api/sites` when scope is Site (or from in-memory groups when Group). New **Exclusive** checkbox with tooltip explaining the additive vs suppressive semantics. Profile list rows show an "Exclusive" pill next to the name when set.
- **Frontend — maintenance window editor** — scope dropdown adds `Site`; `_mwScopeInner()` renders a site datalist autocomplete for site scope. Window list rows show `Site: <name>` for site-scoped windows.
- **Sort order updated** in `_alPgRenderProfiles()`: `global → site → group → device → sensor`.

### Site hierarchy — Phase B: Devices tab UI nesting

Layers a collapsible **Site** wrapper above the existing Group rows on the Devices tab. Same group name under different sites renders as separate buckets (e.g. `HQ → Servers` and `DR-Site-2 → Servers` are two distinct visual sections — intentional per the user's enterprise model). Site assignment is via the device editor or new bulk-move-to-site action.

- **Device editor — Site input** ([frontend/forms-device.js](frontend/forms-device.js)) — both Add and Edit modals get a `Site` text input alongside the existing `Group` field. Uses a native `<datalist>` populated from `GET /api/sites` (cached on `window._pwSitesCache` with 60s TTL). Empty value = "Unsited". `POST /api/device` now accepts `site` ([routes/devices.py:287, 295, 327](routes/devices.py)); `STATE.add_device()` takes the new kwarg.
- **Site wrapper rendering** ([frontend/devices.js](frontend/devices.js)) — new helpers `_dgKey(site, group)`, `siteId(s)`, `sitebId(s)`, `_siteLabel(s)`. `ensureSiteSection(site)` builds `.site-wrap > .site-hdr + .site-body` and is called from `ensureGroupSection(group, site)` so each `.grp-wrap` nests inside its parent site's body instead of `#dpanels` directly. Composite `(site, group)` key is used for all `grpId/gridId/cntId` lookups — same group name under different sites becomes distinct DOM buckets. Empty `site` = "Unsited" sentinel.
- **Per-site collapse state** — new localStorage key `pw-site-collapsed` (Set of site values), mirrors the existing `pw-grp-collapsed` mechanic. Click site header to toggle. Group-level collapse state now keyed by composite (site,group) — a one-time reset of `pw-grp-collapsed`/`pw-grp-order` is expected on first load.
- **Drag-drop across sites** ([frontend/devices.js:1224](frontend/devices.js#L1224)) — `onDrop` now reads `dataset.site` AND `dataset.group` from the target grid. If either differs from the dragged device, both fields are PATCHed in a single request. Sites cache is invalidated on cross-site moves.
- **Group reorder constrained to within-site** ([frontend/devices.js:1281](frontend/devices.js#L1281)) — `onGrpDragOver` only allows reordering within the same `.site-body` parent. Cross-site moves require explicit Edit Device / bulk-move-to-site (avoids the complexity of inferring intent from a drag).
- **Rename group cascades across all sites** ([frontend/devices.js:1357](frontend/devices.js#L1357)) — `renameGroup()` iterates every `.grp-wrap[data-grp-name=oldName]` (possibly one per site that contains the group). Mute badges and selection checkboxes mirror the same multi-wrap pattern.
- **Bulk move to site** ([frontend/index.html:478](frontend/index.html#L478), [frontend/devices.js:343](frontend/devices.js#L343)) — bulk action bar gets a second `Move to site:` input alongside `Move to group:`. New handler `_bulkApplySiteMove()` POSTs `{action:'move', site:'<name>'}` (empty string is valid — clears to Unsited). Reuses the same `/api/devices/bulk` endpoint extended in Phase A.
- **Site editor in Edit Group modal** ([frontend/forms-group.js](frontend/forms-group.js)) — new "Site" section between Group Name and Alert Profile. Prefills from the group's devices: if they all share one site, that value shows; if mixed, the input stays blank and the helper text warns the user that saving will consolidate every device in the group to a single site. Datalist autocomplete from `/api/sites`. On save, bulk-PATCHes every device in the group via the existing `/api/devices/bulk` action=move with site (runs before the rename so the bulk-move keys on the pre-rename group name).
- **Group-level device icon default for NTM Live map** ([frontend/forms-group.js](frontend/forms-group.js), [frontend/map.js](frontend/map.js)) — new "Device Icon (NTM Live map)" dropdown in the Edit Group modal. Picks from the full 23-option icon list (Switch, Firewall, Server, Router, VM, Storage, IPMI, etc.). Persisted in a new top-level setting `pw_group_icons` keyed by plain group name (site-agnostic so the same group name across sites shares the icon). Resolution order on the Live map: per-device override → group default → existing name/group heuristic. Live-syncs to the open NTM iframe via a new `postMessage` `{type:'pw_group_icons'}` handler so the icon flips without a tab reload. Rename migration: when a group is renamed in the same save, the icon entry is re-keyed atomically (old name deleted, new name written).
- **CSS** ([frontend/style.css](frontend/style.css)) — `.site-wrap` / `.site-hdr` / `.site-arr` / `.site-label` / `.site-count` / `.site-body`. Bold larger header in `var(--card-soft)` chrome, collapsible body, count pill showing "N groups · M devices".

### Site hierarchy — Phase A: data model + sites API

Introduces a **Site** level above Group on devices (`Site → Group → Device` hierarchy). Phase A is data-layer only — no UI change yet. Subsequent phases ship the UI nesting (B), additive alert cascade (C), NTM Live tab nesting (D), and auto-discovery propagation (E).

- **New column `devices.site TEXT DEFAULT ''`** — idempotent `ALTER TABLE` on both backends ([db/core.py](db/core.py) try/except, [db/pg_schema.py](db/pg_schema.py) `ADD COLUMN IF NOT EXISTS`). Mirrors the existing `ipam_subnets.site` pattern. Empty = "Unsited"; no separate sites table.
- **`Device.site` field** ([core/state.py](core/state.py)) added to `__init__` (kwarg, defaults to `""`). Surfaced in `Device.to_dict()` so `/api/devices` returns it.
- **Persistence plumbed end-to-end** ([db/persistence.py](db/persistence.py)) — `site` added to PG INSERT column list + ON CONFLICT UPDATE clause, SQLite INSERT OR REPLACE (placeholder count bumped 22 → 23), and both load paths (`_pg_load` + SQLite `db_load`). `site` is appended to the end of the SELECT column list so existing positional reads aren't disrupted by the new column.
- **`PATCH /api/device/{did}` accepts `site`** ([routes/devices.py:463](routes/devices.py#L463)) — whitelisted alongside `group`, with length validation (max 80 chars).
- **`POST /api/devices/bulk` accepts `site`** for the `move` action — `group` and `site` are independently optional; at least one required. Allows bulk move-to-site without changing group, and vice versa.
- **New endpoint `GET /api/sites`** ([routes/ipam.py](routes/ipam.py)) — returns `{"sites": [...]}` as a case-insensitively sorted UNION of distinct non-empty values from `ipam_subnets.site` and `devices.site`. Used by upcoming Phase B autocomplete on the device editor, plus Phase C alert profile + maintenance window editors. Viewer-level (read-only).

### Design tokens & theme — [frontend/style.css](frontend/style.css)
- New `:root` palette: deeper black `--bg` (#0a0d12 vs #0d1117), brighter accent `--accent` (#4d9eff vs #2f81f7), refined status colors (`--up` #2ee5a3, `--down` #ff5c5c). Glow variants (`--up-glow`, `--warn-glow`, `--down-glow`, `--accent-glow`) added for new components
- Additive tokens: `--card`/`--card-soft`/`--card-strong`/`--card-hover`, `--inset`/`--inset-soft`, `--overlay`, `--text4`, `--border3`, `--accent-soft`, radii `--r-xs..xl/pill`, type scale `--fs-xs..3xl`, spacing `--sp-1..7`, motion `--ease/--dur/--dur-fast`, shadows `--shadow-sm/md/lg`, layout heights `--topbar-h/--rail-w/--tabbar-h`
- Density-aware overrides via `[data-density="compact"]` (default) and `[data-density="comfortable"]` on `<html>`. Toggle in user dropdown menu. Persisted as `pw_density` in localStorage, applied before paint via the FOUC bootstrap in `index.html`
- Light theme palette updated with parallel tokens. Theme switch still flips `<html data-theme>`; moved out of the user menu to a dedicated topbar icon button (`#tbThemeBtn`)
- Existing legacy tokens (`--card-bg`, `--panel-bg`, `--header-h`, etc.) kept as aliases pointing at the new tokens so every pre-v1.0 rule keeps resolving

### Shell — topbar + icon rail — [frontend/index.html](frontend/index.html), [frontend/icons.js](frontend/icons.js), [frontend/style.css](frontend/style.css)
- New topbar layout: brand wordmark with animated radar mark · command-palette stub · health bar (pill chrome) · clock · DB badge · status badges · log badge · theme toggle (sun/moon) · bell · user dropdown. Every internal id preserved (`tbVer`, `healthBar`, `hb-*`, `badgeCrit/Warn/Ack/Muted`, `logBadge`, `tb-user`, `usrDd`, etc.) so [app.js](frontend/app.js) selectors keep targeting the right elements
- Horizontal tab bar (`#mainTabs`) replaced with a **left icon rail** (`#rail`). Each rail button is `class="rail-btn"` with stroked-SVG icon, accent left-stripe + soft fill on active, hover tooltip. Tab IDs (`tabDashboard`, `tabDevices`, etc.) moved onto the rail buttons so `switchMainTab()` keeps toggling `.active` on the same elements. RBAC class hooks (`.rbac-admin` on Logs button) preserved
- New [frontend/icons.js](frontend/icons.js) — lucide-style SVG icon library (~40 icons). Single export `icon(name, size=16, attrs={})` returning an SVG string. Loaded first in the inline-JS chain ([server.py:69](server.py#L69)) so every later module can call it at module-load time. `_pwShellInit()` runs on DOMContentLoaded to populate rail/topbar/menu glyphs by id and `data-icon` attribute; re-runs on `themechange` for the topbar theme button
- New [frontend/charts.js](frontend/charts.js) — vanilla SVG `sparkline()`, `donut()`, `heatmap()` helpers (`PWChart` global plus `pwSparkline`/`pwDonut`/`pwHeatmap` shortcuts). Replaces the prototype's React-based chart components with pure-string SVG generators. No chart library
- `#layout` switched from flexbox to CSS grid (`grid-template-columns: var(--rail-w) minmax(0, 1fr)`)

### Dashboard — [frontend/dashboard.js](frontend/dashboard.js)
- All 17 widget types in `_DW_REG` get SVG icons (was emoji); icons render in widget headers and the Add Widget picker
- Widget chrome (`.dw-card` / `.dw-hdr` / `.dw-body`) dual-classed with new design vocabulary (`.widget` / `.widget-head` / `.widget-body`) — slightly more rounded corners (10px vs 8px), refined padding, hover accent. Header action buttons (✎ ⤢ ×) replaced with sharp SVG icons via `icon()`
- **New widget type: Fleet Status** — donut breakdown of Up/Warning/Down/Paused with legend rows. Auto-refreshes on device-status updates. Uses `pwDonut()`
- **New widget type: Latency Heatmap** — N devices × 30 time-buckets, color-ramped (green → amber → red). Configurable device limit (10/20/30). Caches per-widget for 30s to avoid request storms across the per-sensor `/history` fetches. Reads `p.ms` (or `p.value` fallback) and surfaces failed probes (`ok === false`) as red max-clamped cells
- **Global dashboard time-range** — `5m / 1h / 24h / 7d` segmented control in the dashboard tab bar. Persisted as `pw_dw_range`. Affects all time-aware widgets: `sensor_chart`, `sla_report`, `flap_detect`, `latency_heatmap` now read `_dwTimeRangeMinutes()` instead of per-widget config. Per-widget time fields removed from those registry entries. `_dwSetTimeRange()` re-renders every widget on change. Internal pill bar inside `sensor_chart` removed (global control is the single source of truth). `network_avail` (purpose-built 24h) and `event_count` (multi-period table) keep their hardcoded ranges
- Bugfix: fullscreen widget icon now uses `.innerHTML = reg.icon` (not `.textContent`) so SVG icons render rather than appear as raw markup

### Devices — [frontend/index.html](frontend/index.html), [frontend/devices.js](frontend/devices.js)
- `#devActBar` split into `.pagehead` (title "Devices" + live device-count sub + Add Device/Discover/Import/Group buttons) and `.dev-toolbar` (status pills + search + view toggle + select)
- Action buttons converted to `.btn primary` / `.btn` / `.btn ghost` with inline SVG icons (plus/zoom/upload). `.dev-status-pill` markup dual-classed with `.dev-status-btn` for new layout while keeping the existing `[data-st]`-driven styling. View toggle dual-classed `.seg view-toggle`. Search uses `.search` design with leading SVG glyph
- `_updateStatusPills()` now also fills `#devSub` with a live "N devices across M groups" line

### Events — [frontend/index.html](frontend/index.html)
- `.evt-view-hdr` dual-classed with `.pagehead`; "⚡ Events" emoji replaced with a `<h1>Events</h1>`. Action buttons (Resolve All / view-mode toggle / export) converted to `.btn ghost sm` + `.seg` with SVG icons

### Alerting — new top-level page ([frontend/alerting.js](frontend/alerting.js), [frontend/index.html](frontend/index.html), [frontend/icons.js](frontend/icons.js), [frontend/app.js](frontend/app.js), [frontend/style.css](frontend/style.css))
- New "Alerting" rail item between Events and Map, opens dedicated `#alertingView` page. Replaces the old "alerting → events" redirect
- Page-head with live subtitle (`N of M profiles active · K channels configured`) + `Test Alert` / `New Profile` buttons
- **2-column layout**: profiles + recent deliveries on left; channels card + escalation policy + quiet-hours hint on right
- **Alert Profiles section** — each row has a toggle switch, name, condition summary (`down ∨ warning · Global · 3 stages`), channel pills colored per atype (email/webhook/syslog/browser), and inline Edit / Test / Delete icon buttons. Filter input narrows by name/scope. Reuses existing CRUD via `openProfileEditor`/`_alertingProfTest`/`_alertingProfDelete`
- **Recent Deliveries table** — Time / Profile / Device / Severity / State columns pulled from `/api/alert/events?state=all&limit=50`. Refresh icon button reloads
- **Channels card** — lists action templates from `/api/alert/action-templates` with type icon (mail/webhook/syslog/bell), connection detail (to / url / host:port), and uppercase type label. Click a row to edit; `+` in the card head creates new
- **Escalation Policy card** — picks the default global profile (or first available) and renders its stages as numbered rows showing channel names + trigger + delay (`immediate` / `+30s` / `+10 min`)
- **Maintenance Windows card** — full live list (status dot, name, "In effect" pill, scope + cadence). Click row → edit modal. Plus button creates a new window. Reuses existing `/api/alert/window*` CRUD endpoints. Both the page card (`#al-pg-maint`) and the Settings tab (`#alrt-maint-list`) re-render from a single `_alertingLoadMaint()` so they stay in sync
- **Enable/Disable toggle on maintenance windows** — new `enabled` column on `maintenance_windows` (idempotent migration in both SQLite [db/core.py](db/core.py) and PG [db/pg_schema.py](db/pg_schema.py); legacy rows default to enabled=1). Per-row toggle switch on both surfaces; modal also gets an "Enabled" checkbox. `db_active_windows()` now filters out disabled windows so the alert dispatcher and auto-discovery never honor them. Toggle is optimistic with rollback on PATCH failure
- **Suppression reason on Recent Deliveries** — new `suppress_reason` column on `alert_events` (idempotent ALTER on both SQLite and PG). When `_fire()` in [monitoring/alert_profile_engine.py](monitoring/alert_profile_engine.py) suppresses a dispatch via the maintenance-window path, it now records `"Maintenance: <window name>"` alongside the existing `state="suppressed"` row. The Recent Deliveries table on the new Alerting page gets a **Notes** column that surfaces that reason (warn-tinted chip). Resolved rows show "Recovered in Xm/Xs/Xh", acked rows show "Ack by <user>", and rows with `repeat_count > 1` show "N× repeats" so the column stays useful for non-suppressed states too
- **24h health trend now reflects sensor impact** — [routes/monitoring.py](routes/monitoring.py) `/api/health/trend` previously returned `pct = sum(ok)/count(*)` from `sensor_samples` per hour, which was dominated by the healthy-sample denominator (a few sensors going down per hour barely moved the needle) and ignored threshold_crit events entirely (probe succeeded → ok=1, even though the metric value crossed a CRIT threshold). The handler now also queries `flap_log` for `direction IN ('down','threshold_crit')`, builds a per-hour distinct-(did,sid) set, and computes `sensor_pct = 100 × (1 − affected_sensors / total_active_sensors)` using the live `STATE.devices` running-sensor count as the denominator. Final `pct = min(sample_pct, sensor_pct)` so the bar visibly dips proportional to how many distinct sensors had issues that hour. `db_load_availability()` itself is unchanged — penalty is applied only in the trend endpoint to avoid affecting per-device availability and report chart consumers
- **Settings → Alert Profiles tab removed** — every section (profiles list, action templates, maintenance windows) is now duplicated on the new top-level Alerting page, so the Settings tab was redundant. Removed the sidebar button (`#stab-btn-alert-rules`), tab content (`_buildSettingsTab_alertRules()`), footer, `switchSettingsTab` plumbing, and the orphaned `_saveAlertBatching()` handler. **Notification Batching moved to a new sidebar card on the Alerting page** (`_alPgLoadBatching` / `_alPgRenderBatching` / `_alPgSaveBatching`) — same three fields (enabled toggle + window seconds + max size), same `/api/settings` PATCH contract, plus a status pill in the card head (`on · 60s / 20`) so the current config is visible without expanding the card. `_alertingLoadProfiles()` now also refreshes the Alerting page's `_alPgRender*` functions when mounted, so profile/template CRUD from any surface keeps the page in sync
- Existing Settings → Alert Profiles tab remains during the transition; the new top-level page is the recommended surface

### IPAM — [frontend/ipam.js](frontend/ipam.js), [frontend/style.css](frontend/style.css)
- `_ipamRenderShell()` rewritten with `.pagehead` (title bumped to "IP Address Management" + sub + Add Subnet/Edit/Refresh DNS/Remove buttons using `.btn primary`/`.btn`/`.btn ghost`/`.btn danger` with SVG icons)
- New 2-column layout (`.ipam-layout`): left **sidebar** with filter input + grouped subnet cards, right **main pane** with subnet header + 5-card KPI row (Total / In use / Reserved / Free / Utilization %) + Address heatmap + allocation table
- **Address heatmap** — grid of 22×22 cells (18×18 on narrow viewports), colored by classification: free / in-use (device-linked) / gateway / reserved / conflict. Clicking a cell filters the table to that IP. Legend renders above the grid
- **Collapsible per-site groups in sidebar** — subnets bucket by their `site` field. Each group renders as a chevron + name + count header; clicking toggles. Group containing the active subnet always force-expands so users don't lose context. Collapsed state persists to `pw_ipam_grp_collapsed`. Subnets without a site land under "Ungrouped" (sorted to the bottom)
- **Add Subnet / Edit Subnet modals** gain a Site / zone input with `<datalist>` autocomplete from existing site values
- **Per-subnet search** — typing in the main search box now filters the currently-selected subnet (editable rows) instead of hijacking the view to a read-only cross-subnet result list. Global cross-subnet search remains as the fallback when no subnet is selected
- **Allocation kind** — inline cell edit gains a kind picker (`Auto` / `Gateway` / `Reserved` / `Conflict`) below the name input. Status column in the IP table shows a colored kind pill (`Gateway` green, `Reserved` yellow, `Conflict` red) when set. Heatmap classification now prefers the explicit kind field; legacy auto-detection by name remains as fallback for untagged allocations

### Backend — IPAM site grouping ([db/ipam.py](db/ipam.py), [db/core.py](db/core.py), [db/pg_schema.py](db/pg_schema.py), [routes/ipam.py](routes/ipam.py))
- **Schema** — additive `site TEXT DEFAULT ''` column on `ipam_subnets`. Idempotent migrations in both SQLite (try/except `ALTER TABLE`) and PG (`ADD COLUMN IF NOT EXISTS`); fresh-install `CREATE TABLE` blocks updated to match. Empty string default = "Ungrouped" in the UI
- **DB layer** — `_SUBNET_COLS` selects `COALESCE(site, '') AS site`; both row mappers surface it. `db_add_subnet(cidr, name, user, site='')` accepts the optional value. `_SUBNET_UPDATABLE_FIELDS` adds `site: ('TEXT', 40)` so the existing whitelisted PATCH path picks it up
- **API** — `POST /api/ipam/subnets` reads `site` from the body (trimmed, max 40 chars). `PATCH /api/ipam/subnets/{id}` adds a `site` block (modal sends it on save). `GET` returns `site` on every subnet row alongside the other columns

### Backend — IPAM allocation kind ([db/ipam.py](db/ipam.py), [db/core.py](db/core.py), [db/pg_schema.py](db/pg_schema.py), [routes/ipam.py](routes/ipam.py))
- **Schema** — additive `kind TEXT DEFAULT ''` column on `ip_allocations`. Idempotent migrations in both backends; fresh `CREATE TABLE` updated. Values: `''` (auto / used / free), `'gateway'`, `'reserved'`, `'conflict'`
- **DB layer** — `db_get_subnet_ips` selects `COALESCE(kind,'') AS kind` and surfaces it in each allocation dict. `db_upsert_allocation(..., kind=None)` accepts an optional kind; `kind=None` (default) preserves any existing tag on UPDATE so device-sync paths can set just the name without clobbering a user-applied gateway/reserved label
- **API** — `PUT /api/ipam/ips/{subnet_id}/{ip}` reads `kind` from the body, whitelists against `{'', 'gateway', 'reserved', 'conflict'}`, and only threads it to the upsert when the client included the key

### Reports — [frontend/reports.js](frontend/reports.js)
- `_rptInit()` shell rewritten with `.pagehead` (title + sub + New button) + sub-tabs converted to `.seg`. The internal RPT sections catalogue is untouched

### Backups — [frontend/index.html](frontend/index.html), [frontend/backups.js](frontend/backups.js), [frontend/style.css](frontend/style.css)
- `#backupsView` toolbar split into `.pagehead` (title + Run All Enabled/Refresh buttons with SVG icons) + `#bk-toolbar` with `.search`-styled config search
- New design-style page: dynamic subtitle (`N configs tracked · last sweep Xm ago · Y succeeded · Z failed`), 5-card KPI row (Successful 24h / Failed 24h / Disabled / Avg size / Storage used)
- Table restructured to 8 design columns: **Device** (name + host + vendor chip) / **Last success** / **Size** / **Version** / **14-day history** (strip chart) / **Diff since** / **Status** (pill) / **Actions** (Run/View/Settings icon buttons). Status pill replaces the old text status; icon-only actions replace the three separate columns
- 14-day strip paints per-day status (ok/fail/none) from the backend's new `strip_14d` field; falls back to a single-bar placeholder for legacy installs
- "Diff since" cell shows real `last_diff_lines` count (`no changes` / `N lines changed`) computed at backup time; clicking opens the History modal so the user can pick which two runs to diff

### Logs — [frontend/logs.js](frontend/logs.js)
- `_logsInit()` rewritten with `.pagehead` (title + Live toggle / Refresh buttons), stream tabs converted to `.seg`, filters use `.pw-select`/`.search`/`.iconbtn` design

### User menu + Active Sessions — [frontend/index.html](frontend/index.html), [frontend/app.js](frontend/app.js), [frontend/forms-users.js](frontend/forms-users.js)
- New menu header: avatar (initials), name + email, role pill. New session row: "Session expires in Xh Ym · signed in via Local". Menu items have SVG icons (settings/user/lock/shield/activity/info/log_out) via `data-icon` attribute populated by `_pwShellInit`. Theme toggle removed (lives in topbar). New "Active Sessions" item between 2FA and the separator. New "Density: Compact/Comfortable" toggle replacing the old theme toggle slot
- `_refreshUsrMenuHeader()` runs on every menu open — avatar, name, email, role badge, session-expiry countdown. `S.me` populated from `/api/me` so name/email/role are available without re-fetching
- New `_openSessionsModal()` ([forms-users.js](frontend/forms-users.js)) — fetches `GET /api/me/sessions`, renders one row per session with device label + IP + last-active + revoke button. Current session shows a CURRENT pill and "— this session —" instead of revoke. "Sign out all other sessions" button calls `DELETE /api/me/sessions` (disabled when only the current session exists)
- New `_toggleDensity()` — flips `<html data-density>` between `compact` and `comfortable`, persists as `pw_density`. Density label in menu refreshes on each menu open

### Settings — [frontend/forms-settings.js](frontend/forms-settings.js)
- Modal title and all 14 sidebar tabs swapped from emoji to SVG icons via `icon()`. Internal tab content unchanged

### Backend — Active Sessions endpoint (the only `/api/*` change)
- **Schema** — 5 additive columns on `sessions` table: `ip`, `user_agent`, `device_label`, `created_at`, `last_active`. Idempotent migrations in both [db/core.py](db/core.py) (SQLite) and [db/pg_schema.py](db/pg_schema.py) (PG) per dual-backend pattern
- **Login capture** — [core/auth.py::_create_session()](core/auth.py) and `auth_login()` accept optional `ip`/`user_agent`/`device_label` kwargs. The main `/api/login` and `/api/login/totp` paths in [routes/auth.py](routes/auth.py) thread these through (parsed via `parse_user_agent_label` for the friendly "Chrome on Windows" label). SSO/LDAP/RADIUS auto-provision paths leave the fields blank for now
- **New helpers** in core/auth.py: `auth_list_user_sessions()`, `auth_revoke_session_by_id()` (enforces `username` server-side — cross-user revoke returns 404, no info leak), `auth_revoke_other_user_sessions()`
- **New endpoints** in [routes/auth.py](routes/auth.py): `GET /api/me/sessions` (returns `[{id, current, ip, user_agent, device_label, created_at, last_active, expires}, ...]`, `id` = SHA-256 token-hash matching the row's primary key), `DELETE /api/me/sessions/{id}` (returns 400 if trying to revoke the current session — use `/api/logout` instead), `DELETE /api/me/sessions` (revokes all but the current session, returns `{revoked: N}`)
- **Single-session caveat documented**: `_create_session()` still does `DELETE FROM sessions WHERE username=?` before inserting on each login. The new endpoints work — they just typically return 1 row. Removing single-session enforcement is a future security trade-off
- **Audit trail** — `session_revoked` and `sessions_revoke_others` audit events emitted to `db_log_audit`

### Backend — Backups page enrichment ([db/backups.py](db/backups.py), [backup/engine.py](backup/engine.py), [db/core.py](db/core.py), [db/pg_schema.py](db/pg_schema.py))
- **Schema** — additive `diff_lines INTEGER DEFAULT NULL` column on `backup_runs`. Idempotent migrations in both SQLite (try/except `ALTER TABLE`) and PG (`ADD COLUMN IF NOT EXISTS`); fresh-install `CREATE TABLE` blocks updated to match. NULL on legacy rows = "we don't know" — new runs populate the value naturally
- **Engine** — `do_backup()` now fetches the previous successful run's config via new `db_get_last_successful_config()` helper and stores `diff_lines = _count_diff_lines(prev, current)` on each new successful run. Uses stdlib `difflib.unified_diff` (no new dep). Identical configs → `0`; first backups + failed runs → NULL. Failure during diff computation is logged and doesn't break the save
- **List endpoint** — `db_get_backup_list()` (powers `GET /api/backups`) now returns two extra fields per device: `last_diff_lines` (int or null) and `strip_14d` (14-entry array of `ok`/`fail`/`none` in oldest→newest UTC-day order). Strip computed via one extra query per call (4th query in the existing function) bucketed in Python by `_build_14d_strip()`
- **Cost** — one extra SELECT per `/api/backups` call (~14-day window, indexed on `(did, ts DESC)`), one extra `db_get_last_successful_config()` query per backup run. Both bounded and cheap

### Out of scope (deferred to a future release)
- Map view (Live tab) — kept as-is per scope decision; the existing builder app inside the iframe inherits the new theme tokens but its chrome wasn't redesigned
- Alerting view — lives inside Settings; gets icon refresh only
- Notification stream behind the topbar bell — currently jumps to the Events tab as a stub
- Topology snapshots / version diff, drawio export, topology-vs-monitored discrepancy detection — all noted in MIGRATION_NOTES.md as future endpoints
- Full Settings modal rewrite to the prototype's grouped Platform/Identity/Monitoring/Connections layout — surface area too large for v1.0; tabs swapped from emoji to SVG icons only

---

## v0.9.6

### 🔌 Typed SNMP transitions — alert-profile + map + live-panel integration

Follow-up to the typed-event detector below. The original Scope note flagged that automatic email/webhook/syslog dispatch and live-map status were both still pending. This pass closes both gaps via a single hookup — driving `_threshold_state` from the typed detector — and removes a duplicate-alert side effect that surfaced once enum-state sensors started reporting non-primary values.

- **Threshold check skipped for non-numeric SNMP categories** — [core/state.py::_run_once](Pingwatch/core/state.py) — the legacy numeric threshold path now short-circuits when an SNMP sensor's category resolves to `enum_state` / `time_duration` / `text`. Comparing the raw enum code (e.g. `2`) against `crit_ms` previously fired a meaningless "Threshold Alert (crit) / 2" alongside the proper translated `state_down` event. The skip branch also seeds `_new_thr = s._threshold_state` so the transition block stays a no-op (otherwise the next probe would emit a spurious `threshold_ok` recovery)
- **Typed detector now drives `_threshold_state`** — [core/state.py::_run_once](Pingwatch/core/state.py) — the enum-state branch maps `state_down → "crit"`, `state_change → "warn"`, `state_up → "ok"` and calls `dev.invalidate_status()`. The downstream `device_status` SSE broadcast (already in `_probe_end_batch`) picks up the recomputed `Device.status` automatically — NTM map node colour and dashboard widgets switch without further plumbing. Crucially the detector does NOT broadcast `threshold_critical` / `threshold_warning` / `threshold_ok` SSE events; the existing `flap_state_*` broadcast remains the single source of truth so the duplicate-alert removal above stays effective
- **Alert profiles fire automatically** — `monitoring/alert_profile_engine.evaluate_and_fire()` runs immediately after the detector and reads `_threshold_state` directly, so the existing email + webhook + syslog dispatch fires its configured `down` / `warning` / `*_recovered` stages with no profile-engine changes. Closes the v0.9.6 "no alert-profile-engine integration yet" caveat for enum-state SNMP sensors
- **Active-tab filter** — [db/events.py::db_load_flaps](Pingwatch/db/events.py) — added `state_up` to the existing `WHERE direction NOT IN ('recovered','threshold_ok')` exclusion. Recovery rows were leaking into the Active list with ACK/Resolve buttons on a green "RECOVERY" event; now mirrors the legacy `recovered` exclusion so recoveries appear only in History
- **NTM Live panel SSE wiring** — [frontend/map.js](Pingwatch/frontend/map.js) — new handlers for `flap_state_down` / `flap_state_change` / `flap_state_up` populate the same `_pwSensorState` map the existing `threshold_critical` / `threshold_warning` / `threshold_ok` handlers use, so the right-side "ACTIVE INCIDENTS" panel counts typed transitions alongside legacy threshold events
- **Enum-label translation in incident card** — [frontend/map.js::_pwSensorIncidentVal](Pingwatch/frontend/map.js) — the THRESHOLD CRIT / WARN cards translate raw enum codes via the SNMP unit legend (or the well-known IF-MIB / UPS-MIB OID prefix fallback) before display: an `ifOperStatus` sensor now shows "down" instead of "2". Minimal in-place port of `_parseEnumLegend` / `_enumForOid` from `frontend/sensors.js` because the map iframe doesn't load `sensors.js` — keep the two copies in sync if the legend table grows
- **Enum-label translation in alert email** — [monitoring/smtp_alert.py::_snmp_display_value](Pingwatch/monitoring/smtp_alert.py) — the SNMP HTML body and plain-text body both routed `ctx['last_value']` straight to "Current Value", so down/recovery emails for an `ifOperStatus` sensor said `2` and `1` instead of `down` and `up`. New helper reuses the existing `core.state._effective_enum_legend_py` (lazy import) so the legend table stays in one place on the Python side; falls through unchanged for non-enum SNMP values and non-SNMP sensors
- **Enum-label translation in alert syslog** — [monitoring/alert_dispatchers.py::dispatch_syslog](Pingwatch/monitoring/alert_dispatchers.py) — same root cause: the alert-profile syslog dispatcher built `[ALERT] dname/sname — down — 2` because `ctx['detail']` for an SNMP sensor is the raw probe value. Inline translation via the same `_effective_enum_legend_py` helper before composing the message. (Direct `flap_state_down` syslog forwarding via `monitoring/syslog_client.py::_build_message` was already correct because it reads the typed detector's pre-formatted "State changed: up → down" detail off the flap dict.)
- **Email "Last Message" hero + plain-text Detail row** — [monitoring/smtp_alert.py::_build_alert_html](Pingwatch/monitoring/smtp_alert.py) and `_render_snmp_text` — both pulled `ctx['detail']` straight onto the page; the prominent red/green hero banner and the plain-text body still said `2` / `1` even after the "Current Value" row was fixed. Same translation applied so the entire email is internally consistent
- **Email Thresholds section hidden for non-numeric SNMP** — [monitoring/smtp_alert.py::_render_snmp_body](Pingwatch/monitoring/smtp_alert.py) — when an enum-state / time_duration / text sensor still carries leftover `warn_ms`/`crit_ms` config values, the email used to print misleading "Crit > 2" rows even though the engine now ignores those thresholds. The Thresholds section is suppressed for those categories using the existing `core.state._snmp_category_py` classifier. To support the `time_duration` / `text` paths the alert profile ctx now also carries `snmp_type` ([monitoring/alert_profile_engine.py::_build_ctx](Pingwatch/monitoring/alert_profile_engine.py))
- **Scope** — covers `enum_state` only. `reboot` (TimeTicks decrease) and `value_change` (text/OCTET-STRING change) remain transient/informational and continue not to drive `_threshold_state`; they fire flap events and webhooks as before. Counter-rate, gauge-numeric, and TLS sensors are unaffected (they always went through the numeric threshold path and still do)

### 🔒 SNMPv3 authentication — full USM support

Previously the SNMP Version dropdown offered `v3 (community)`, but `probe_snmp` only passed `-c community` — so a sensor set to v3 silently ran a v2c query with the community-string field, or simply failed. This pass adds complete User-Based Security Model (USM) support end-to-end: per-device defaults, per-sensor override, all three security levels, every net-snmp-supported auth/priv algorithm, and write-only passphrase handling.

- **Probes** — [monitoring/probes.py](Pingwatch/monitoring/probes.py) — new `_snmp_auth_args(community, version, v3_creds)` builds the full `-v3 -l level -u user -a auth -A authpass -x priv -X privpass -n context` argument vector. Whitelists protocol values (MD5 / SHA / SHA-224 / SHA-256 / SHA-384 / SHA-512 for auth; DES / AES / AES-192 / AES-256 for priv) and levels (noAuthNoPriv / authNoPriv / authPriv) before forwarding to the subprocess. Both `probe_snmp` and `snmpwalk_interfaces` accept an optional `v3_creds` dict; v1/v2c calls are bit-identical to before
- **Credential resolution** — [core/state.py::Sensor._resolve_snmp_v3_creds](Pingwatch/core/state.py) — per-sensor fields win; blank fields inherit from the parent `Device.snmp_v3_*_default`. Passphrases are Fernet-decrypted at the probe-time boundary (same key as VMware / SSH / RADIUS passwords), so plaintext never sits on the Sensor attribute or in the DB
- **Schema** — [db/pg_schema.py](Pingwatch/db/pg_schema.py) / [db/core.py](Pingwatch/db/core.py) — idempotent ADD COLUMN for `devices.snmp_v3_{user,level,auth_proto,auth_pass,priv_proto,priv_pass,context}_default` and `sensors.snmp_v3_{user,level,auth_proto,auth_pass,priv_proto,priv_pass,context}`. Both PG and SQLite migrations guarded; pre-existing installs pick up the columns on first restart without a manual step
- **Persistence** — [db/persistence.py](Pingwatch/db/persistence.py) — round-trip for both tables across both backends. Column lists and value tuples are append-only (no renumbering of existing positions), so older DB snapshots load cleanly with blank v3 fields
- **Routes** — [routes/devices.py](Pingwatch/routes/devices.py) — `/api/device` POST + PATCH and `/api/sensors/{did}` POST + `/api/sensors/{did}/{sid}` PATCH accept the new fields. Same whitelist validation as `probes.py`, returning HTTP 400 on bad enum values. Passphrases encrypt on the way in; empty-string means "keep existing" (placeholder-submit pattern matches the VMware / SSH flows). Responses never emit ciphertext — `has_snmp_v3_auth_pass` / `has_snmp_v3_priv_pass` boolean flags tell the UI when a stored value exists
- **Edit/Add Device form** — [frontend/forms-device.js](Pingwatch/frontend/forms-device.js) — SNMP Version dropdown label changed from `v3 (community)` to `v3`. Selecting v3 reveals an accent-bordered credential block (Username, Security Level, Auth Proto + Passphrase, Priv Proto + Passphrase, Context). Auth row shows only for authNoPriv / authPriv; priv row only for authPriv. Passphrase inputs show `(unchanged)` placeholder when a stored value exists, so re-saving without retyping doesn't clear the vault
- **Add/Edit Sensor form** — [frontend/forms-sensor.js](Pingwatch/frontend/forms-sensor.js) — same v3 block on the SNMP sensor tab, with inputs pre-filled from the device default (transparent to the user; the backend resolves the inheritance again at probe time). Discover Interfaces forwards the live v3 creds through `/api/snmp/interfaces` so users can validate credentials before committing a sensor
- **Scope** — backwards compatible with all existing v1/v2c sensors (they never hit the v3 code path). No DB migration required beyond schema-idempotent column adds. Tested shape: Cisco N5K (172.22.99.89) and FortiGate (172.22.99.254) — production devices that historically required community-string workarounds

### 🔬 Typed SNMP sensor rendering — five categories

Before this change, SNMP sensors rendered in one of two modes only: counter-rate (for `snmp_unit ∈ {bytes, errors, packets}`) or probe-latency fallback (everything else). That meant interface Oper Status, CPU %, temperature, memory, session count, UPS battery status, HA mode, uptime — every SNMP sensor that isn't a network traffic counter — silently displayed SNMP poll latency (~20-30ms) with "Avg ms / Max ms" axes and empty KPIs. Concrete reproducer: FortiGate `BS_LABS_TRUNK Oper Status` with a constant `value=1` (up) rendered a 393ms "max" figure as if the interface state were fluctuating. Fix: classify every SNMP sensor into one of five typed categories from `snmp_unit` and `snmp_type`, and render each category with appropriate KPIs, chart, and summary table.

- **Backend** — [core/state.py](Pingwatch/core/state.py) — `Sensor.snmp_type` field added, populated on each successful probe from the existing `probe_snmp` return value (no extra SNMP work). Included in `to_dict` so the frontend receives the ASN.1 type alongside `snmp_unit`. No DB schema change — type is transient runtime attribute
- **Frontend dispatcher** — [frontend/sensors.js](Pingwatch/frontend/sensors.js) — `_snmpCategory(unit, type)` routes to one of: **counter_rate** (bytes/errors/packets — unchanged), **enum_state** (unit regex `\d+=\w+` matches — ifOperStatus and every vendor variant of it, UPS battery status, HA mode, fan/PSU state), **gauge_numeric** (unit ∈ `{%,celsius,fahrenheit,dbm,count,seconds,minutes,hours,hz,volts,amps,ratio,rpm}`), **time_duration** (`snmp_type = TimeTicks` — sysUpTime), **text** (`OCTET STRING` or `unit = "string"` — sysName, sysDescr). Fallback is `gauge_numeric` with auto-scaled axis — never falls back to ms again
- **enum_state rendering** — step chart with primary-state (`--up` / green) vs non-primary (`--down` / red) horizontal segments and vertical warn-colored transition markers. KPIs: Current state (labeled), % time in primary state, transitions count, current-state age. Summary table: per-bucket % primary + transitions column. Works identically across Cisco, Juniper, Fortinet, and generic IF-MIB — all vendors use the same RFC 2863 enum format
- **gauge_numeric rendering** — line chart with unit-aware Y-axis (0-100 fixed for percent, auto-scaled with p95 headroom for everything else). Min/Max envelope band at rollup tier (uses v0.9.7 `min_value`/`max_value` aggregates). KPIs: Avg / Min / Max / Last, formatted via `_fmtGaugeValue` with unit suffix (`67%`, `42 °C`, `1.2G`, `8.2 GB`)
- **time_duration rendering** — uptime line with reboot markers (red dashed verticals where value decreased). KPIs: Current uptime (formatted as `Xd Yh`), Reboots count. TimeTicks are normalized from 1/100 seconds to seconds at display time
- **text rendering** — centered last-value banner; change-log summary table showing each (ts, old, new) transition. No chart
- **Overview tab** — [frontend/sensors.js::mVal](Pingwatch/frontend/sensors.js) — the "LAST" tile now formats through the enum legend / gauge formatter / duration formatter immediately on modal open, so an ifOperStatus sensor shows "up" instead of "1"
- **Edit/Add sensor modal** — [frontend/forms-sensor.js](Pingwatch/frontend/forms-sensor.js) — new visible "Display as" dropdown with grouped options (Counter / Gauge / Enum / Other) plus a "Custom enum" prompt for user-entered legends. Syncs automatically with the catalog-picker and discover-interfaces paths so pre-defined OIDs keep their unit. "Auto-detect" is the default — classifies from the probe's SNMP type on first poll
- **Catalog** — [snmp/catalog.py](Pingwatch/snmp/catalog.py) — `IF Operational Status` expanded to the full RFC 2863 enum (`1=up 2=down 3=testing 4=unknown 5=dormant 6=notPresent 7=lowerLayerDown`); new `IF Admin Status` entry; Cisco `OSPF Neighbors` converted from ambiguous `"state"` to the explicit 8-state OSPF-MIB enum; `System Uptime` (TimeTicks) sanity entry added
- **Scope** — same fix covers every vendor: Cisco, Juniper, Fortinet, standard RFC 1213, UPS (RFC 1628), generic HOST-RESOURCES. Because IF-MIB is a cross-vendor standard and the dispatcher is unit-pattern-driven rather than vendor-keyed, no per-vendor code path is needed

### 🚨 Typed SNMP event transitions — state change / reboot / value change

Follow-up to the typed rendering above. The new categories were display-only; they didn't fire flaps or alerts. That was a silent gap: an `ifOperStatus` transition from `1=up` to `2=down` leaves `s.alive = True` (because the SNMP probe itself succeeded — the device answered with value=2), so the connectivity flap pipeline never triggered. Interfaces going down were visible on the History chart but invisible to the Events tab and alert webhooks. This pass closes that gap for all three state-bearing categories.

- **Detector** — [core/state.py::_run_once](Pingwatch/core/state.py) — new block in the probe-ok branch that runs when `stype == 'snmp'` and the sensor has a resolved typed category. Tracks prev value per category (`_prev_enum_code`, `_prev_ticks`, `_prev_text_value`) on the Sensor object; on transition, emits a synthetic flap through the existing `db_log_flap` + SSE broadcast + webhook pipeline. No schema change — `flap_log.direction` was already free-text
- **New direction values** — `state_down` (enum primary → non-primary, e.g. ifOperStatus 1→2), `state_up` (non-primary → primary — auto-resolves matching `state_down` / `state_change` flaps), `state_change` (non-primary → different non-primary, e.g. testing → dormant), `reboot` (TimeTicks decreased by >1s — covers device reboots, with a 100-tick guardrail against the rare 497-day wrap boundary), `value_change` (text / OCTET STRING value differs from prev — truncated to 60 chars in detail)
- **Python helpers** — [core/state.py](Pingwatch/core/state.py) — `_snmp_category_py` / `_parse_enum_legend_py` / `_enum_primary_code_py` / `_fmt_duration_s` mirror the frontend dispatcher so both sides classify identically
- **Events tab rendering** — [frontend/events.js](Pingwatch/frontend/events.js) — `evtSeverity` maps the new directions: `state_down` / `reboot` = critical (red), `state_change` = warning (orange), `value_change` = info (blue), `state_up` = recovery (green). New icons: 🔻 state_down, 🔺 state_up, ↔️ state_change, ♻️ reboot, 📝 value_change. Group labels use "changed state" / "rebooted" / "value changed" instead of the generic "went down"
- **Alert-event matching** — `_matchAlertEvt` treats `state_down` / `state_change` / `reboot` as down-like and `state_up` as recovery-like, so alert-rule firings from these directions pair correctly with their sensor event in the Events UI
- **Dashboard badge severity** — [db/events.py](Pingwatch/db/events.py) `_FLAP_SEVERITY_SQL` extended so active `state_down` / `reboot` flaps count toward the critical badge total, `state_change` counts toward warning. Acknowledged / resolved semantics unchanged
- **Scope — no alert-profile-engine integration yet.** The new events fire SSE notifications, log to the Events tab, and trigger webhooks on the sensor's device. Automatic email/SMS/Slack via alert profiles requires profile-engine work (new trigger type beyond `down` / `warning`) — deferred. Users who need email today can add a webhook pointing to their mailer, or configure an alert rule on the sensor that watches for `_last_rate == None` + non-primary enum state (existing threshold mechanism handles gauge sensors)
- **Idempotency** — first probe after a restart has no `_prev_*` state so no event fires (prevents spurious "value changed" on every boot). Muted sensors still skip event emission. Probe failures (SNMP timeout etc.) don't touch the prev state — when SNMP recovers, the comparison is against the last *successful* value, so a momentary outage doesn't falsely register as a state change

### 📈 Peak probe-rate preservation through rollup tiers

Follow-up to the value-aggregate rollup below. After deploying v0.9.6, the FortiGate `port11 Out Traffic` reproducer showed `max 31.53 Mbps` at 24h view but `max 5.17 Mbps` at 3d — a 30-second burst inside a 5-minute bucket was being averaged away into a smoothed rate. Matches every professional tool's rollup behavior: RRDtool CFs (`AVERAGE`/`MAX`/`MIN`), Zabbix `trends.value_{min,avg,max}`, Prometheus/Thanos downsampling. Fix: compute rate at probe time (already done — just wasn't persisted), store it per-sample, aggregate min/avg/max per rollup bucket.

- **Schema** — [db/pg_schema.py](Pingwatch/db/pg_schema.py) + [db/core.py](Pingwatch/db/core.py) — added `rate` column to `sensor_samples` (DOUBLE PRECISION / REAL, nullable); added `avg_rate` / `min_rate` / `max_rate` to both `sensor_samples_5m` and `sensor_samples_1h`. Idempotent `ADD COLUMN IF NOT EXISTS` on PG, try/except swallow on SQLite — same pattern as earlier v0.9.6 additions
- **Probe pipeline** — [core/state.py](Pingwatch/core/state.py) `_run_once` now persists `s._last_rate` (already computed with correct `snmp_type`-aware wrap handling: `2**32` for Counter32, `2**64` for Counter64). The `db_buffer_sample()` call was moved below the ok/fail branch so the rate written reflects THIS probe's rate, not the previous one; the failure branch now clears `s._last_rate = None` so failed probes don't carry a stale rate. [db/samples.py](Pingwatch/db/samples.py) `db_buffer_sample(..., rate=None)` extended with optional kwarg; flush INSERT statements updated for both PG `execute_values` and SQLite `executemany`
- **Rollup worker** — [db/samples.py](Pingwatch/db/samples.py) — PG `_rollup_5m` / `_rollup_1h` use `AVG(rate) / MIN(rate) / MAX(rate)` (5m) and sample-count-weighted avg + MIN/MAX (1h, mirroring the v0.9.6 `avg_value` pattern). SQLite `_bucket_raw_rows` helper extended to track rate aggregates per bucket; 1h Python-side `hourly` defaultdict gains `rate_weighted_sum` / `rate_weight` / `min_rate` / `max_rate` fields
- **History API** — `_history_from_rollup` + `_history_from_raw` return the new columns. Rollup responses carry `avg_rate` / `min_rate` / `max_rate` (nullable for pre-v0.9.7 rows); raw responses carry `rate` (nullable for non-counter sensors and first-probe-after-restart rows)
- **Frontend** — [frontend/sensors.js](Pingwatch/frontend/sensors.js) — `_computeRateSamples` now emits `{ts, ok, rate, min, max, ms}` tuples. Prefers backend-computed rate (raw tier) and backend rate aggregates (rollup tier) when present; falls back to the v0.9.6 bucket-endpoint derivation / client-side counter diff for older rows. `_buildKpiBar`, `_drawHistCanvas` y-axis scaling, `_buildSummaryTable`, and the Stats bar all use per-sample `r.min` / `r.max` so "Max Mbps" KPI survives at every zoom level. New counter-tier Min/Max envelope band (previously gated to `!_isCounter`) — at raw tier it collapses to the avg line; at rollup tier it shows the true peak-to-trough range of probe rates within each bucket
- **Counter64 correctness** — latent frontend bug fixed as a side-effect: `_computeRateSamples`'s fallback path still hardcodes `4294967296` (Counter32 wrap), which silently under-reports multi-Gbps interfaces using `ifHCInOctets` (Counter64). The backend's probe-time rate calculation has always been correct per `snmp_type`; now that rate is persisted, the frontend prefers it and the client-side fallback is only used for pre-v0.9.7 rows
- **Diagnostics** — [core/state.py](Pingwatch/core/state.py) `get_runtime_snapshot()` exposes `peak_rate_coverage`: percentage of last-hour raw samples that carry a non-null `rate`. Climbs to ~95%+ after 2 probe cycles on counter-heavy deployments (some non-counter sensors keep `rate=NULL` permanently, which is correct)
- **Scope — forward-looking only.** Rollup rows predating v0.9.7 keep `avg_rate IS NULL`, and the frontend gracefully renders them using the v0.9.6 `(last_value - first_value) / bucket_s` smoothed-average fallback. No historical backfill — matches the RRDtool convention of populating a new Consolidation Function going forward. After ~3 months of steady operation every retained rollup row will have native rate aggregates

### 📈 Preserve sensor `value` through rollup tiers

SNMP traffic sensors (and any other sensor whose primary display metric lives in `sensor_samples.value` rather than `ms`) silently switched units at the rollup boundary — 24h view showed Mbps, 3d+ flipped to ms (poll latency) because `sensor_samples_5m` / `_1h` only aggregated `ms`.

- **Schema** — [db/pg_schema.py](Pingwatch/db/pg_schema.py) + [db/core.py](Pingwatch/db/core.py) — added `avg_value`, `min_value`, `max_value`, `first_value`, `last_value` columns to both `sensor_samples_5m` and `sensor_samples_1h`. Idempotent `ADD COLUMN IF NOT EXISTS` on PG, try/except swallow on SQLite
- **Rollup worker** — [db/samples.py](Pingwatch/db/samples.py) — `_rollup_5m` / `_rollup_1h` now aggregate value alongside ms. PG uses `CASE WHEN value ~ '^-?[0-9]+(\.[0-9]+)?$' THEN value::DOUBLE PRECISION END` to skip non-numeric rows (DNS IP, banner matches) and `ARRAY_AGG(... ORDER BY ts)` to pick bucket endpoints. SQLite rolls up row-wise in Python via the new `_bucket_raw_rows` helper (SQLite has no regex) — `_try_float` coerces, non-numeric rows contribute None to the aggregate
- **Backfill** — `db_rollup_backfill` gains a third trigger: if any rollup row has `avg_value IS NULL` while raw `value` still exists, reset `rollup_state.last_ts = 0` so the rollup reprocesses within the raw-retention window. Rows older than raw retention stay NULL (unrecoverable). Runs once on first startup after deploy
- **History API** — `_history_from_rollup` now selects + returns `avg_value` / `min_value` / `max_value` / `first_value` / `last_value` + `bucket_s` (300 or 3600). Raw rows still return `value`; rollup rows keep `value: null` so existing consumers don't silently break
- **Frontend** — [frontend/sensors.js](Pingwatch/frontend/sensors.js) — `_computeRateSamples` gains a rollup-tier branch that derives the per-bucket rate from `(last_value - first_value) / bucket_s` (same Counter32-wrap handling that already existed for raw tier). With that, `_isCounter` is true at rollup tier → `_buildKpiBar`, `_drawHistCanvas`, `_setupHistTooltip`, `_buildSummaryTable` all render Mbps / err/s / pkt/s across the full 1h → 3y range
- **Scope** — counter-rate SNMP (`snmp_unit` in `bytes` / `errors` / `packets`) is the reported bug and is fully fixed. SNMP gauges (`%`, `°C`, …) and TLS days-until-expiry have the backend aggregates ready, but their frontend rendering is pre-existing and unchanged by this commit (they already don't render `value` properly even at the raw tier — separate UI task)
- **Rollup chunking** — first deploy on production surfaced that resetting `rollup_state.last_ts = 0` for the migration made the very next `_rollup_5m()` try to aggregate the full raw-retention window in one INSERT → `canceling statement due to statement timeout` (30 s). Both tiers now cap per-call work (3 h for 5m tier, 3 d for 1h tier) and jump `last_ts` forward across empty gaps via a composite-index-backed `SELECT MIN(min_ts) FROM (SELECT MIN(ts) AS min_ts ... GROUP BY did, sid)` pattern (O(sensors) index seeks, not a seq scan). `_rollup_5m` / `_rollup_1h` now return a "more work remains" bool; `db_rollup_backfill` loops each tier to completion in-process on startup, logging progress every 20 chunks. Regular 5-min-interval rollup loop unaffected in normal operation (single 5-min window fits in one chunk)

### 🔢 5k-sensor scale pass

Targeted backend + frontend patches to take PingWatch from ~1–2k comfortable to 5k on a single production PG instance. No architectural rewrite — thread-based probe dispatch stays, frontend stays DOM-full.

**Backend**
- **Bounded retention trim** — [db/samples.py](Pingwatch/db/samples.py) — `DELETE … LIMIT 10 000` loop (via `ctid`/`rowid`) with commit between batches and a 50 ms yield, so the unpaged `DELETE FROM sensor_samples WHERE ts < ?` can no longer lock the table for seconds and spill the 50k sample buffer. Applies to both PG and SQLite, all three tiers (`sensor_samples`, `_5m`, `_1h`)
- **SSE broadcaster back-pressure** — [core/state.py](Pingwatch/core/state.py) — per-subscriber queue bumped 300 → 1000 for 5k-scale bursts; `put_nowait` now falls back to a 20 ms grace `put(timeout=0.02)` before eviction (absorbs brief GC/network hiccups), and evictions are logged at WARN for operator visibility
- **Profile invalidation debounce** — [routes/alert_profiles.py](Pingwatch/routes/alert_profiles.py) — `_invalidate()` now uses a 5 s `threading.Timer` to coalesce rapid bumps of `_profile_cache_ver`; a burst of N profile edits collapses into one 5k-sensor re-resolve storm instead of N
- **PG pool auto-scale** — [db/pg_pool.py](Pingwatch/db/pg_pool.py) + [server.py](Pingwatch/server.py) — `pg_init_pool(max_override=…)` + post-load reopen mirrors the probe-executor auto-scale pattern. Formula `max(30, min(150, workers // 4 + 20))` — 64 workers → 36 conns, 256 → 84, 512 → 148. Explicit `pg_pool_max` in `pingwatch.conf` still wins; new `get_pool_max()` helper exposes the live size
- **Auto-scale ordering fix** — [server.py](Pingwatch/server.py) — both probe executor and PG pool auto-scale were reading `STATE.devices` *before* `db_load(STATE)` populated it, so every startup silently logged `0 sensors` → pinned executor at 64 workers and pool at 36 conns regardless of real load. Latent since executor auto-scale first shipped; only surfaced when the new flush-duration WARN started firing on a deployment with 261 sensors. Fixed by counting sensors via `SELECT COUNT(*) FROM sensors` directly (tables exist by this point via `db_init`, but no probes are scheduled yet — pool reopen can't race in-flight cursors)
- **Alert-engine recovery-stage hot-path DB query** — [monitoring/alert_profile_engine.py](Pingwatch/monitoring/alert_profile_engine.py) — `evaluate_and_fire()` was calling `_had_prior_fire()` on every probe for every healthy sensor with a recovery stage defined, which meant one `db_get_stage_state` query per sensor per probe cycle. py-spy showed **every probe worker** parked in this call stack during bursts, saturating the PG pool and starving the sample-flush thread (root cause of the `Sample flush slow` WARNs that persisted after the ordering fix). The existing `sensor._alert_has_fired` in-memory flag already tracks whether a sensor has fire history — checking it before the DB call short-circuits >95% of the queries on healthy deployments. Post-fix probe workers idle between cycles instead of queuing on `pg_pool_sema`
- **Sample-flush duration observability** — [db/samples.py](Pingwatch/db/samples.py) + [core/state.py](Pingwatch/core/state.py) — each flush tracked in `_last_flush_ms` / `_last_flush_rows`; WARN log when a flush exceeds 2 s (indicates DB contention); surfaced in `get_runtime_snapshot()` so the Diagnostics tab shows it

**Frontend**
- **O(n) → O(1) sensor lookups in Devices tab** — [frontend/devices.js](Pingwatch/frontend/devices.js) — `listRowHTML()`, `_devSnrSummaryHtml()`, and `sSnrPreview()` switched from `Object.values(S.sensors).filter(s => s.device_id === did)` to the pre-maintained `S._devSensors[did]` Set. At 500 devices × 5k sensors this collapses ~5M comparisons per SSE batch into ~5k
- **Packet-loss widget memoization** — [frontend/dashboard.js](Pingwatch/frontend/dashboard.js) — `_dwRefreshPacketLoss()` now goes through a 5 s TTL cache keyed by `(threshold, limit)` so the O(sensors) filter + sort doesn't run on every 250 ms SSE batch
- **Health-bar pill throttle** — [frontend/app.js](Pingwatch/frontend/app.js) — `updatePills()` now throttled to 1 Hz (leading + trailing edge) instead of firing on every 250 ms SSE batch; visual pills don't need 4 Hz updates

---

## v0.9.5 — Quiet Hours

### 🧩 VMware Edit Metrics, verify_ssl, and log-badge regressions
- `verify_ssl` silently flipped to true on existing sensors — `ref.verify_ssl !== 0` returned true for JSON boolean `false`; replaced with truthy coercion
- Edit Metrics showed all checkboxes unchecked for mixed-metric hosts — refactored to use VMware MoID prefix (`vm-*` / `host-*` / `datastore-*`) as authoritative entity type
- Edit Metrics errored "catalogue not loaded yet" on cold sessions — new `_ensureVmwareCatalogue()` helper fetches catalogues on demand
- Edit Metrics demanded a password when the device already had one — modal now falls back to `vmware_password_default` when blank
- "VMware sensor created without SSL verification" warning reworded to neutral phrasing for both add and edit flows
- Log-entries badge stayed invisible after server restart — `_logBadgeInit` now detects restart (`seen > total`) and resets watermark; switching to Logs tab marks badge seen

### 🛑 Shutdown-ordering race — trailing "pool is closed" / "cursor already closed"
- Probe workers outran `pg_close_pool()` — added final `STATE._executor.shutdown(wait=True)` barrier before pool close
- Alert batcher's atexit drain ran after pool close — new `shutdown_sync()` drains inline before pool close

### 🛡 Audit hardening — security + resilience pass
- postMessage origin checks added to second listener in `map.js`; `theme.js` pins target to `window.location.origin`
- Error-string leakage fixed in VMware discover and TLS cert/key/PFX/CA parse paths — log server-side, return generic message
- Probe hard-timeout guard — `_run_once()` wraps probes in a daemon thread with per-stype floors (vmware 90 s, smtp 60 s, ssh 45 s, sftp 60 s, default 15 s)
- Webhook dispatcher queue — bounded `queue.Queue(maxsize=100)` + single dispatcher thread replaces per-event thread-spawn
- Alerting picker dropdown cleanup — new `_apCloseProfModal()` wrapper cleans `#_ap_picker_dd` on all 5 close paths
- PG partition DROP now uses `psycopg2.sql.Identifier` instead of f-string

### 📬 Alert batching + UI event collapse
- Notification batching singleton holds outbound email + webhook alerts for a short window, emits one combined notification per `(channel, destination, severity)` bucket
- 3 settings (Alert Profiles → Notification Batching): `alert_batch_enabled` (default on), `alert_batch_window_s` (default 60 s), `alert_batch_max_size` (default 20)
- Fail-safe routing — batcher errors fall through to existing per-event sender; a bug in batching cannot silence alerts
- Webhook batching is opt-in per template via "Batch-aware receiver" checkbox — default off so Slack/Teams templates aren't broken by array payloads
- Syslog and browser SSE never batch; event records (`alert_events`, `flap_log`, `sensor_err_log`) keep one row per event
- Batched email template renders severity-breakdown banner + striped table sorted critical → warning → info → recovery
- UI event collapse — new "Collapse related" filter toggle folds ≥3 related events within 30 s into one expandable row; traps stay individual
- In-memory queue; `atexit` drain on graceful shutdown (unclean crashes lose in-flight batches — events remain in DB)

### 🔧 Diagnostics tab — operator & support console
- New Settings → Diagnostics tab consolidating seven panels: System Overview, Database Health, Health Checks, Probe from Server, Recent Errors, Maintenance, Support Bundle
- System Overview — version, uptime, CPU/RAM/disk, worker count, scheduler heap depth, SSE listeners, sample-buffer fill, DB writer queue, per-DB on-disk size
- Database Health — per-table row counts + on-disk size (PG), last VACUUM, Run VACUUM / Backup DB buttons
- Health Checks — consolidated test panel for LDAP/RADIUS/SAML/OIDC/SMTP/Syslog/DB Backup remote + NTP + DNS with master Test All
- Probe from Server — interactive ping/TCP/HTTP/DNS/TLS tool (admin-only)
- Support Bundle — one-click `.zip` with logs, snapshot, settings (sanitized via deny-list + self-defence value scan)
- NTP check — inline SNTP v4 client (no new dep) reports drift (ok <5 s, warn 5–60 s, error >60 s)
- DNS resolver check via `dnspython` against system or custom resolver
- 8 new admin-only, audit-logged diagnostics endpoints; Debug Mode toggle relocated from Retention to Diagnostics → Maintenance

### Auto-Discovery activity polish
- Recent activity pane bounded to 320 px scroll pocket with sticky header
- Filter bar — event-type dropdown, actor substring, free-text search against target + detail (150 ms debounce)
- Scan-timeout audit trail — `auto_discovery_scan_timeout` written once per streak; log message suggests raising `auto_discover_scan_deadline_s` or splitting subnet
- IPAM edit modal warns on large subnets (≤ /20, 4096+ hosts) with auto-discovery enabled

### Event log LRU — never evict acknowledged flaps
- Trim-on-insert DELETE in `db_log_flap()` now only considers rows with `ack_state='resolved'` — active + acknowledged flaps kept indefinitely
- `max_flap_entries` default lifted 500 → 2000

### Retention & Performance settings tab
- New Settings → Retention tab consolidating deployment-sizing knobs across 5 sections: Database Retention, Event & Trap Limits, Log Files, Performance & Limits, Diagnostics
- Default bumps: `max_flaps_display` 20→50, `max_flap_entries` 500→2000, `max_trap_entries` 500→2000; `audit_trim_cap` now configurable (default 50 000)
- Audit log switched to `TimedRotatingFileHandler(when="midnight", backupCount=365)`
- Main app log backup count raised 5 → 14
- Live handler swap on startup via `reconfigure_from_settings()` — no restart required for size/retention changes
- Parameterised formerly-hardcoded constants: `smtp_timeout_s`, `pg_statement_timeout_s`, `pg_pool_acquire_timeout_s`, `auto_discover_scan_deadline_s`, `sftp_checksum_max_mb`, `import_max_payload_mb`
- 14 new `app_settings` keys with min/max validation in `PATCH /api/settings`

### PBKDF2 cost upgrade (200k → 600k)
- Raised iteration count to match OWASP 2023 minimum — not exposed as a setting
- Self-describing hash format `"iters:salt:hex"` — future cost bumps won't require migration
- Backwards-compatible verify — legacy 2-part `"salt:hex"` continues at 200k; no forced reset
- Transparent upgrade on successful login via `_maybe_rehash()`; LDAP/RADIUS/SAML/OIDC unaffected

### NTM live map — group-change orphan fix
- `fetch({keepalive:true})` dropped from `_pwSave` — bodies were hitting the 64 KB in-flight cap and piling up as "Pending"
- New `_nodeInsideGroup()` helper + orphan pre-pass in `calcPwLayout()` re-slots devices whose persisted coords fall outside their group rect
- Auto-grow now persists the grown rect's actual `(x, y)` — subsequent renders don't snap back to pre-grow size
- Post-grow overlap resolution — overlapping groups teleport via `_findFreeGroupSlot()` with child overrides translated by the same `(dx, dy)`

### Bulk device multi-select (Devices tab)
- ☑ Select toggle in Devices toolbar (operator+); `body.pw-select-mode` class gates all visual changes
- Per-card and per-group-header checkboxes (tri-state for group header); shift-click range select
- Sticky bulk-action bar shows count, hidden-by-filter count, group-name combobox, Resume/Pause/Delete buttons
- Ctrl+A selects visible cards; Esc exits select mode (gated on Devices tab, skipped while typing)
- Single endpoint `POST /api/devices/bulk` handles move/start/stop/delete with `{device_ids, action, group?}`; per-device results for partial-failure UI
- One audit entry per bulk call (`bulk_move` / `bulk_start` / `bulk_stop` / `bulk_delete`)
- Mute auto-propagates — moving from muted `Discovery-*` to an unmuted group immediately restores alerting
- Optimistic `pruneEmptyGroups()` after bulk-move removes emptied groups without refresh

### SAML 2.0 + OIDC Enterprise SSO
- Federated SSO alongside local/LDAP/RADIUS; tested with FortiAuthenticator, protocol-compliant for Okta, Entra ID, Keycloak, ADFS, OneLogin, PingFederate
- SAML 2.0 — SP-initiated, HTTP-POST binding, `pysaml2` + `signxml` (no `xmlsec1` system dep), `defusedxml` for XXE-safe parsing
- OIDC — Auth Code + PKCE (S256), JWKS JWT validation via `authlib.jose`, auto-discovery via `.well-known/openid-configuration`
- IdP metadata import by URL (TLS-verify-then-fallback), paste XML, upload file; errors propagated to UI verbatim
- SP signing cert generated from UI (RSA-2048 self-signed, 825-day); private key Fernet-encrypted; rotatable independently of TLS cert
- AuthnRequest signing with `<ds:Signature>` reordered to sit immediately after `<saml:Issuer>` per SAML 2.0 core §3.2.1
- Assertion signature verification tries multiple reference patterns for IdP compatibility (Okta vs FAC/ADFS)
- JIT provisioning — first SSO login creates `users` row with `pw_hash='__saml__'`/`'__oidc__'` and `external_id`; subsequent logins sync name/email/group/role
- Group → role mapping via new `user_groups.saml_group_value` / `oidc_group_value` columns; `allow_unmapped` gate
- Login-screen SSO buttons via new public `GET /api/settings/public_auth`; TOTP still applies; `🪪 SAML` + `🪙 OIDC` user-table badges
- 8 SAML endpoints + 6 OIDC endpoints; 31 settings keys total (17 SAML, 14 OIDC)
- `Handler._body()` now parses `application/x-www-form-urlencoded` alongside JSON (SAML ACS sends form-POST)

### Auth backend health checks
- Two-phase health surveillance for LDAP/RADIUS/SAML/OIDC — catches rotated IdP cert / revoked client secret / reshuffled JWKS before users hit errors
- Phase 1 (boot sanity) — synchronous, no network, before HTTP bind; config + local crypto checks; target <200 ms
- Phase 2 (hourly refresh) — daemon thread. LDAP: real service-account bind. OIDC: refetch discovery + JWKS. SAML: cert re-parse + 30-day expiry warn. RADIUS: config-only
- Configurable `auth_refresh_interval_min` (allow-list 0/15/30/60/240/720 min; 0 disables loop)
- "Run now" button wakes the loop's multi-event wait immediately
- Settings → Integrations "🩺 Auth Health Check" strip: interval dropdown, last-run indicator (green/yellow/"never"), Run now
- Thread-safe status via `_status_lock` on LDAP/RADIUS paths; graceful shutdown via `_stop` + `_wake` events

---

## v0.9.3 — Autonomous Discovery

### RADIUS Probe Sensor
- New sensor type `radius` with 2 test depths: `reachable` (any response proves host+port+secret) and `auth` (full PAP)
- Reuses existing RADIUS client via `radius_probe_once()` wrapper; no new deps
- Credentials Fernet-encrypted; API exposes only `has_radius_secret` / `has_radius_password` booleans
- `Access-Challenge` at auth level flagged in detail (`"auth: 2FA challenge required"`) — probe doesn't complete
- Smart defaults: port 1812, `warn_ms=500`, `crit_ms=2000`, `timeout=5s`, `test_level=reachable`
- Amber badge `#fbbf24` across all 7 CSS badge families; light-theme override `#b45309`

### Auto-Discovery — Periodic Subnet Scanning
- Scheduled subnet scanning with auto device creation — tick "Auto-discover new hosts" on any IPAM subnet, set a global interval, walk away; new hosts land in group `Discovery-<CIDR>` with ping + guessed service sensors
- Daemon thread `monitoring/auto_discovery.py` — start/stop/trigger_run_now/get_last_run_status; scans serialised via `_tick_lock`
- Safety rails: global enable (default off), global pause, first-scan cap (default 100), maintenance-window awareness, suppressed-hosts list (capped 500 FIFO)
- Per-IPAM-subnet `auto_discover` toggle + `first_scan_approved` + `last_auto_scan_ts` columns
- New Auto-Discovery settings tab — enable, interval dropdown, pause, first-scan cap, alert-on-new-device, reverse-DNS naming, maintenance behaviour, suppressed-hosts table
- `POST /api/auto-discovery/run-now` (admin, optional `subnet_id`); `GET /api/auto-discovery/status`
- Reverse-DNS naming uses PTR record; falls back to bare IP
- Audit trail — one `auto_discovery_tick` entry per scan (`found=N added=N suppressed=N duration=Ns`)

### Bug fixes & minor improvements
- `msColor` inverted-threshold support for TLS (days-until-expiry) and VMware datastore free-GB — added `inverted` flag
- `pystray` headless Linux crash — module-load raises `ValueError: Namespace Gtk not available`; broadened guard from `ImportError` to `Exception`
- Backup scheduler silent save miss — `_save_last_ts()` now logs at WARNING; first-fire flip logged at INFO
- SFTP probe chroot path — clarified `remote_path` must be session-relative with chrooted users
- Ad-blocker CSS conflict — `.ad-*` class names matched ad-blocker filter rules; renamed to `.disc-*`
- Auto-Discovery `found` count now included in scan stats + audit detail
- Group mute badge missing on auto-discovered groups — fixed by calling `_refreshGroupMuteBadge()` after `ensureGroupSection()`
- Port-443 TLS suggestion changed to `http` with `verify_ssl=False` — self-signed labs no longer fail out of the box
- Alert event `triggered_at` preserved across escalations — no longer pushed outside the 300 s correlation window
- Group mute now reflected in Events reason chip

### Top-level Logs tab + professional log viewer
- Logs extracted out of Settings into a top-level `📜 Logs` entry (admin-only); old Settings sub-tab removed
- Debug Mode toggle relocated to Settings → General
- New `frontend/logs.js` with toolbar, filter bar, status bar, body, floating "Jump to live" pill
- Smart scroll-follow — auto-attaches when at bottom, detaches on scroll up; pill + End key re-attach
- `min_level` query param with rank-based comparison (`DEBUG<INFO<WARNING<ERROR<CRITICAL`)
- API returns `file_size` + `rotated_count`; UI shows "Showing X of Y filtered (Z total) · 8.2 MB · +N new since open"
- Word-wrap toggle, clipboard copy, CSV/JSON export (of visible filtered view)
- Keyboard shortcuts — `/` focus search, `Esc` clear, `l` live, `r` refresh, `w` wrap, `End` jump-to-live (guarded against modals/inputs)
- Preferences persisted to `localStorage.pw_logs_prefs`

### SFTP Probe Sensor
- New sensor type `sftp` with 4 test depths: `open`, `list`, `stat`, `checksum` (SHA-256, read-only, ≤10 MB cap)
- Password or private-key auth (Ed25519/RSA/ECDSA PEM); credentials Fernet-encrypted at rest
- `checksum` streams 64 KB chunks into `hashlib.sha256()`; pre-flight `stat` enforces cap
- Phase-tagged failure detail (`"list: /backups not found"`, `"checksum: mismatch"`)
- Checksum-level minimum interval 60 s (server-enforced); form auto-bumps to 300 s and timeout 30 s
- Smart defaults: port 22, `warn_ms=2000`, `crit_ms=5000`, `timeout=10s`, `test_level=open`
- Rose badge `#fb7185`; new "File Transfer" sensor category

### SSH Probe Sensor
- New sensor type `ssh` with 3 test depths: `connect`, `banner` (captures version string), `auth`
- Password or private-key auth; both Fernet-encrypted; `_load_ssh_key()` helper shared with SFTP
- `paramiko` lazy-imported; graceful fallback when missing
- `MissingHostKeyPolicy` used (monitoring surface — consistent with backup engine TOFU)
- Smart defaults: port 22, `warn_ms=1500`, `crit_ms=4000`, `timeout=10s`, `test_level=connect`
- Lime badge `#a3e635`

### SMTP Probe Sensor
- New sensor type `smtp` with 5 test depths: `connect`, `ehlo`, `starttls`, `auth`, `mailfrom` (no mail delivered)
- TLS mode selector: plain / STARTTLS / SSL (port auto-suggest: 25 / 587 / 465)
- "Use system SMTP" button pre-fills host/port/TLS/username from Settings → Email
- Credentials Fernet-encrypted; phase-tagged failure detail
- Smart defaults: port 587, `warn_ms=2000`, `crit_ms=5000`, `timeout=15s`, `test_level=ehlo`, TLS `starttls`
- Pink badge `#f472b6`

### Bug fixes & minor improvements (probe engine)
- Alert profile engine "no dispatch — all stages gated" log spam downgraded from INFO to DEBUG
- PG integer column rejection on empty string — added `_int_or_none()` helper in `db/persistence.py`; `update_sensor()` treats `''` as `None` for numeric fields

---

## v0.9.2

### RADIUS Authentication (PAP + Access-Challenge 2FA)
- Third auth source alongside local and LDAP — configure in Settings → Integrations → RADIUS
- PAP only in v1 (covers FortiAuthenticator, NPS, FreeRADIUS, Cisco ISE); `pyrad` lazy-imported
- `Access-Challenge` 2FA — server prompt rendered in login UI; completing challenge satisfies 2FA and skips TOTP; multi-step chains supported
- Primary/secondary failover — retry on socket error/timeout; `Access-Reject` is definitive (no failover)
- Attribute → Group mapping via new `user_groups.radius_attribute` + `radius_value` columns; configurable default role / group
- Auto-provision via `db_add_radius_user()` on first successful login (`auth_type='radius'`, `pw_hash='__radius__'`)
- Realm munging — `radius_realm_prefix` / `radius_realm_suffix`; NAS-Identifier (default `"pingwatch"`)
- Test User Auth dialog displays returned attributes raw for mapping-table copy-paste
- Challenge state store — module-level dict (120 s TTL, `threading.Lock`)
- Status badge in Integrations tab; `🧾 RADIUS` user-table badge; reset-password suppressed for RADIUS users

### Remote DB Backup Upload (SFTP + SMB)
- After each successful local DB backup, uploads snapshot to a remote destination — Settings → Database → Remote Upload
- Two protocols: SFTP (paramiko, reuses TOFU store) and SMB (`smbprotocol`, lazy-imported)
- Runs in same backup thread after local `.db` write; failures logged but don't abort local backup
- Credentials Fernet-encrypted using the same `backup_enc_key` as device backup credentials
- Remote retention not managed by PingWatch (upload-only); status badge in Backup Status widget

### Backup Status widget — Database section
- Backup Status dashboard widget now includes a Database section below the device-config 2×2 KPI grid
- Last-run age color-coded: green (on-schedule), amber (overdue >1.5× interval — 36 h daily, 12 d weekly), red (errored)
- Next scheduled run time + remote upload status when enabled
- "Scheduled, never run yet" and "Disabled" in muted grey; click navigates to Settings → Database

### Bug fixes & minor improvements
- Shutdown race "pool closed" — `autosave_loop` switched from `time.sleep(60)` to `threading.Event.wait(60)` so shutdown can signal cancellation
- DB backup catch-up — `_should_fire()` now fires if due time has passed and no run exists for the current window
- Bundle export filename includes app version; `forms-io.js` reads `Content-Disposition` from server
- Stale login form after RADIUS logout — `_resetLoginForm()` clears TOTP/RADIUS prompt DOM and restores default `submitLogin`
- LDAP badge flipping to "error" on RADIUS logins — "user not found" downgraded to DEBUG and no longer calls `_record_err()`
- Viewer clicking Settings — `openSettings()` now guards on `S.role === 'admin'` and toasts "Settings is admin-only"

### Settings UI & observability improvements
- LDAP/AD status badge alongside SMTP and Syslog dots in Integrations tab
- Log time-range filter — added 3 h, 6 h, 12 h presets; default changed from "All time" to "6 h"
- Custom log date range via `datetime-local` pickers; sends `after=` / `before=` query params
- Removed redundant "Minimum Severity" dropdown from Syslog → Alert Event Forwarding
- Startup log dedup — removed duplicate "PostgreSQL pool ready" line

### Reports polish — aggregation, honest durations, manager-ready sections
- Custom report kind — grouped section-picker modal with `exec` / `tech` / `inv` presets
- Aggregated incident log — `_cluster_flaps_into_outages()` collapses consecutive bad-state events into one outage row
- Major Incidents — `_detect_major_incidents()` buckets DOWN events by minute, merges adjacent windows, emits one row per cluster (≥ N distinct devices)
- Suppress redundant outages — per-sensor outages inside a Major Incident window dropped for the same device
- Sensor configuration issues — `_classify_config_issues()` pattern-matches detail strings and routes to a dedicated section
- Device health scores — composite 0–100 (downtime up to 50, incident load up to 20, currently DOWN −20 / WARN −10); green ≥90 / amber ≥70 / red <70
- Honest "open" flag — outages only marked `ongoing=True` when sensor is still unhealthy; `durfmt_flap` renders unknown-non-ongoing as `—`
- `cleandetail` filter strips stale trailing `"ms"` from non-latency flap details
- Report History multi-select + bulk delete — tri-state header checkbox, sticky action bar; `POST /api/reports/history/bulk-delete` (admin, max 500/call)

---

## v0.9.1 and earlier

### Reports module (scheduled PDF / CSV exports)
- Three report kinds: Executive Summary, Technical/Operations, Inventory & Compliance
- Templates + schedules + history (three sub-tabs under top-level `📊 Reports`); role-gated (viewer browse, operator run, admin mutate)
- Rendered via WeasyPrint + Matplotlib; cover page, print-tuned CSS, per-section `@page` rules
- Tiered-samples awareness — auto-switches between `sensor_samples` / `_5m` / `_1h` via `_pick_table(minutes)`
- Browser preview + Run Now + Test Send; custom-range periods (`custom:<start>:<end>`)
- Compare-to-previous-period deltas on uptime/incidents/critical/warn/MTTR (coloured ↑/↓, inverted semantics for "lower is good")
- Incident severity filter (All / Warn+ / Crit-only); CSV sidecar (UTF-8 BOM for Excel); PDF signing (12-char Report ID + SHA-256)
- Retention auto-prune (`report_retention_days`, default 365); hourly sweep
- Storage path `$XDG_DATA_HOME/pingwatch/reports` (outside git checkout); `PW_REPORTS_DIR` override; tempdir fallback
- Cron-style scheduler: daily/weekly/monthly/quarterly; 90 s dedupe; staggered firing
- PDF/A compliance mode (1b/2b/3b); graceful fallback when WeasyPrint <62

### Anomaly detection (learned baselines)
- Per-sensor EWMA mean + variance (Welford-style); O(1) hot path, 3 floats per sensor
- Upper-tail z-test with variance floor; sensitivity dropdown (Strict/Balanced/Relaxed → k=3/4/6); 3-sample debounce
- Only promotes `ok → warn` — never fires crit; static thresholds remain authoritative for critical
- Cold-start suppression: no alerts until `min_samples` reached AND `anomaly_cold_start_hours` elapsed (50 samples + 24 h)
- Global kill switch + per-sensor opt-in; failed probes never update baseline
- Baseline checkpointed to `sensor_anomaly_baselines` hourly; restored on startup
- Supports `ping`, `tcp`, `http`, `dns`, `http_keyword`, `banner` (SNMP/TLS/VMware excluded in v1)
- `flap_log.direction='anomaly_warn'` with 🧠 badge + filter pill
- Bulk enable resets each baseline to fresh cold-start — prevents alert storms

### Two-factor authentication (TOTP) + trusted devices
- Optional per user; enforceable per role
- Setup flow: QR code + manual secret; TOTP verify at login
- 8 single-use recovery codes at enrolment; admin reset via `POST /api/users/{u}/totp/reset`
- `pw_trusted` HttpOnly SameSite=Strict cookie; raw token SHA-256 hashed before DB storage
- 9-hour default trust window; configurable up to 30 days; 0 = always prompt
- `trusted_devices` table — User-Agent parsed label, IP, last used, expiry; server-side revocation
- Auto-revoke on password change, 2FA disable, admin TOTP reset
- Expired-row sweep every 6 h via `autosave_loop`

### Light / Dark theme
- Full GitHub-Light palette via `:root[data-theme="light"]`; dark remains default
- Inline `<head>` bootstrap reads `localStorage.pw_theme` before CSS paints — no FOUC
- Hybrid persistence — `localStorage` for instant apply + `users.theme_preference` for cross-device; reconciled via `/api/me`
- `frontend/theme.js` public API: `getTheme`, `setTheme`, `toggleTheme`, `getCssVar`, `getCssRgb`
- Canvas drawers cache per-module RGB via `getCssRgb()`; invalidated on `themechange`
- Topology map iframe has its own synchronous bootstrap in `map.html`
- NTM animated background flips via `ntm_themechange`; offscreen hex cache invalidated on colour change

### Performance & scalability
- Auto-scaling probe executor — `max(64, min(512, sensor_count // 4))`; live resize; manual override in Settings
- `dev.status` cached and invalidated on state change — 2–5× CPU reduction for large devices
- Scheduler heap tombstones for deleted sensors
- Per-subscriber SSE sender threads
- Startup restore — per-sensor indexed seeks + batched `GROUP BY`; ~4 s startup
- `_pick_table` boundary moved 4320 → 1440 min — 3-day history routes to `_5m` (full coverage) instead of raw table (10k cap)
- Fixed rollup backfill triggering on every restart
- SSE batching (250 ms), status-change guard, in-place SVG updates, O(1) sensor lookups
- NTM LED blink moved to pure CSS `@keyframes`; packet-trace cooldown 4→6 s; link animation steps halved; SSE gated by `_ntmVisible`

### Code quality refactor
- `db/helpers.py` unified dual-backend query layer (`db_query`, `db_execute`, `db_upsert`, `db_cursor`, `_ph`)
- `core/constants.py` centralised probe/server constants
- `core/validation.py` server-side input validators
- `server.py Handler._error()` logs full exception server-side, returns generic message
- `forms-utils.js` — `msColor()`, `statusClass()`, `_lsGet()`, `_lsSet()` promoted to canonical shared helpers
- `app.js TIMINGS` frozen object replaces scattered magic numbers
- `openSettings()` refactored from ~600-line monolith into 10 focused `_buildSettingsTab_*()` functions
- `SELECT *` replaced with explicit columns in alert tables
- `_broadcast` accepts a list of `(event, data)` tuples per probe end

### Device License Tracking
- `device_licenses` table — name, expiry, note, warn/crit days, last_status; SQLite + PG schemas
- `monitoring/license_checker.py` fires `license_warn` / `license_crit` into `flap_log` (`stype='license'`) on state change; auto-resolves on renewal
- Runs every 6 h via `autosave_loop` and immediately after any license add/update
- Edit Device modal has collapsible Licenses section; IPAM table shows worst status badge
- License Overview dashboard widget — 4-KPI grid + sorted expiration table

### Multi-dashboard tabs
- Per-user named dashboards (up to 10) with tab bar; create/rename/delete via right-click
- New users get a "Default" dashboard with 8 starter widgets
- `dashboards` table replaces `dashboard_widgets`; idempotent startup migration

### Subnet Discovery
- Full mode (ping + DNS + MAC/OUI + port scan + device-type guess) + Ping-only (fast, /18–/16)
- Max /16 (65 534 hosts); auto-switches to Ping-only when host count >4096
- Multi-NIC duplicate detection via hostname fingerprinting — flagged rows pre-unchecked
- Per-device sensor review; bulk add via `POST /api/discovery/bulk-add`
- Dedicated `ThreadPoolExecutor(64)` isolated from probe pool; state auto-purged after 1 h
- Per-device group assignment — global default + per-row override

### Hierarchical alert profiles
- PRTG-style escalation stages: trigger state (Down/Warning/Recovered), per-stage delay, repeat interval, reusable action templates
- Cascade resolution: sensor → device → group → global; first match wins; cached, invalidated on profile change
- Recovery stage computes total downtime from session start and includes it in notification
- `alert_profile_state` table persists stage fire history across restarts
- Per-device and per-sensor profile override with one-click "Reset to inherited"

### VMware vSphere monitoring
- New sensor type via pyvmomi (optional dep)
- 16 metrics across CPU, Memory, Disk, Datastore, Network, System
- Session caching (25-min TTL) + metric caching (20-s TTL) — avoids redundant QueryPerf calls
- `SmartConnect()` capped at 60 s via `socket.setdefaulttimeout()`
- Grouped VM display — collapsible groups with per-metric rows, sparklines, uptime bars
- `mem_consumed_pct` uses `guestMemoryUsage` from VMware Tools (matches guest Task Manager)

### Config backup (lightweight NCM)
- SSH (paramiko) + Telnet with TOFU host key verification, enable-mode escalation, paging disable
- Diff viewer — line-level, ±3-line context, expandable equal sections
- Search inside config viewer + global config search across all stored configs
- Vendor-aware rollback: Cisco includes enclosing context block + `end` + `wr`; FortiGate uses `config/edit/set/next/end`
- Backup Status dashboard widget — OK/Failed/Never run/Enabled KPI counts

### LDAP Group Integration
- Import AD/LDAP groups with per-group role assignment; LDAP badge on imported groups
- Auto-provision unknown LDAP users in imported groups on first login
- Login-time sync of group, role, and display name
- Auto-disable on removal from all imported groups
- Background sync thread (configurable interval, default 60 min)
- Nested AD groups via `LDAP_MATCHING_RULE_IN_CHAIN` (optional toggle)
- Multi-group priority — user receives highest role among matched groups
- Test User Groups diagnostic for admins

### Dual-database architecture
- Main DB (`pingwatch.db` / `main` schema) — config, devices, users, IPAM, alerts
- Logs DB (`pingwatch_logs.db` / `logs` schema) — samples, flaps, traps, errors
- Independent write-queue threads per DB (SQLite); PG bypasses queues (MVCC)
- One-time safe migration from legacy single-DB; split export/import (Main, Logs, ZIP bundle)

### Air-gapped compatibility
- Self-hosted Google Fonts (Exo 2, JetBrains Mono, Orbitron, Share Tech Mono) as `.woff2`; CDN `<link>` tags removed
- `_inlineFontsForExport()` base64-embeds woff2 for offline PNG topology export
- CSP tightened: `style-src 'self' 'unsafe-inline'; font-src 'self';`

### GUI setup wizard (Windows)
- Dark-themed tkinter wizard — 6 steps: Welcome, Packages, Database, Network, Security, Summary
- Background threads for pip installs and PG connection tests
- Falls back to CLI `setup_wizard.py` when tkinter unavailable
- `windows/launcher.pyw` — Python launcher with admin elevation, first-run detection, port cleanup

### TLS sensor fixes
- Threshold direction fixed — alerts when days remaining drops **below** threshold (was inverted)
- Default thresholds corrected from 500/2000 (ms-style) to 30/7 (days)
- Chart threshold lines show "d" suffix; log messages include "days" unit
- Add Sensor tab switching updates threshold labels dynamically

### Events tab — Active / History split
- Inner Active/History tabs inside Sensor Events panel; Active badge shows live unresolved count
- SNMP traps without a linked alert rule default to History
- "Resolve All" hidden on History tab; tab selection persisted in `localStorage`

### Maintenance window improvements
- Scope field replaced with device/group dropdown
- One-time vs recurring time fields separated; recurring auto-sets start/end to now → +10 years
- List shows device name instead of ID for device-scoped windows

### SNMP improvements
- Interface discovery walks ifTable + ifXTable; auto-selects metric per interface
- Counter32/64 traffic OIDs display live rate (B/s → GB/s) with wraparound handling
- Non-numeric values shown in orange as misconfiguration hint
- Probe uses `-On` flag and stdout-only parsing for deterministic output

### Bug fixes & minor improvements
- Stop All device sensors — stopped sensors excluded from `Device.status`; device shows gray; `stop_device()` auto-resolves open flaps
- Sensor host linking — sensors inherit device host by default; manual override flag; device IP changes propagate
- Alert engine hardening — delayed DOWN emails skip deleted/stopped sensors during delay
- Sensor history KPI tiles reflect selected time window
- Bulk "Resolve All" on Events tab resolves all active events and flaps at once
- Device tile + dashboard widget loading shimmer
- Sensor history time-range fade — 250 ms minimum display
- Debug Mode checkbox auto-saves on toggle; reverts on API failure
- Event detail panel "Open Device" / "Sensor History" buttons restored
- Backup schedule dark mode styling fixed
- Project structure reorganised — `start.sh` + service → `linux/`; `start.bat` + `pingwatch.pyw` → `windows/`
