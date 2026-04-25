# 📋 PingWatch Changelog

Detailed implementation notes for every shipped feature. For the high-level roadmap and upcoming work see [ROADMAP.md](ROADMAP.md).

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
