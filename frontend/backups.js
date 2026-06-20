// ── Backups Tab ─────────────────────────────────────────────────────
// Manages device configuration backup listing, settings, triggering,
// and config viewing. All state is server-side; this file is pure UI.

let _bkDevices     = [];           // cached device list from /api/backups
let _bkRunning     = new Set();    // device IDs currently backing up
let _bkInited      = false;
let _bkKeepMax     = 3;            // backup_keep from settings (configs to keep per device)
let _bkGrpExpanded = { enabled: true, disabled: false }; // top-section collapse state
// Per-site collapse state, keyed `${section}:${site}` so the same site can be
// expanded in Enabled and collapsed in Disabled independently. Persisted in
// localStorage; missing keys default to "open" inside Enabled and "closed"
// inside Disabled to keep first-paint quiet for a long disabled list.
let _bkSiteCollapsed = new Set();
try {
  const raw = localStorage.getItem('pw_bk_site_collapsed');
  if (raw) _bkSiteCollapsed = new Set(JSON.parse(raw));
} catch {}
function _bkSaveSiteCollapsed() {
  try { localStorage.setItem('pw_bk_site_collapsed', JSON.stringify([..._bkSiteCollapsed])); } catch {}
}
function _bkToggleSite(key) {
  if (_bkSiteCollapsed.has(key)) _bkSiteCollapsed.delete(key);
  else _bkSiteCollapsed.add(key);
  _bkSaveSiteCollapsed();
  const collapsed = _bkSiteCollapsed.has(key);
  // Toggle the chevron on the header row and hide every device row tagged
  // with this site key. Header row itself stays visible so the user can
  // re-expand without searching the page.
  const idSafe = key.replace(/[^A-Za-z0-9_-]/g, '_');
  const arr = document.getElementById('bk-site-arr-' + idSafe);
  if (arr) arr.classList.toggle('open', !collapsed);
  document
    .querySelectorAll(`tr[data-bk-site-row="${CSS.escape(key)}"]`)
    .forEach(tr => { tr.style.display = collapsed ? 'none' : ''; });
}
const _BK_UNSITED = 'Unsited';

// ── Init / refresh ───────────────────────────────────────────────────
async function _bkInit() {
  _bkInited = true;
  const wrap = document.getElementById('bk-table-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="bk-loading">Loading…</div>';
  try {
    const [r, sr] = await Promise.all([fetch('/api/backups'), fetch('/api/settings')]);
    if (r.status === 401) { _onSessionExpired('Session expired'); return; }
    const d = await r.json();
    if (sr.ok) {
      const s = await sr.json();
      _bkKeepMax = parseInt(s.backup_keep) || 3;
    }
    _bkDevices = d.devices || [];
    _bkRenderHeader(_bkDevices);
    _bkRenderTable(_bkDevices);
  } catch (e) {
    _bkInited = false;  // allow retry on next tab click
    wrap.innerHTML = `<div class="bk-err">Failed to load: ${esc(String(e))}</div>`;
  }
}

// ── Header counts + KPI cards ────────────────────────────────────────
function _bkRenderHeader(devices) {
  const eligible = devices.filter(_bkIsEligible);
  // 24h window — count last_ts events within the last 24 hours
  const now = Date.now();
  const dayAgo = now - 86400000;
  let success24 = 0, failed24 = 0, totalSize = 0, sizedCount = 0, storageBytes = 0;
  let lastSweep = 0;
  eligible.forEach(d => {
    if (typeof d.last_size === 'number') {
      totalSize += d.last_size;
      sizedCount += 1;
      storageBytes += d.last_size * (d.run_count || 1);
    }
    const t = _bkParseTs(d.last_ts)?.getTime() || 0;
    if (t > lastSweep) lastSweep = t;
    if (t >= dayAgo) {
      if (d.last_success === true) success24 += 1;
      else if (d.last_success === false) failed24 += 1;
    }
  });
  const disabledCnt = eligible.filter(d => d.in_schedule !== true).length;
  const tracked = eligible.length;
  const avgKb = sizedCount ? Math.round((totalSize / sizedCount) / 1024 * 10) / 10 : 0;
  const storageStr = _bkFmtBytes(storageBytes);

  const sub = document.getElementById('bkSub');
  if (sub) {
    const sweepAgo = lastSweep ? _bkRelTime(new Date(lastSweep).toISOString()) : '—';
    sub.innerHTML = `${tracked} config${tracked===1?'':'s'} tracked · last sweep ${esc(sweepAgo)} · ${success24} succeeded${failed24?` · <span class="text-down">${failed24} failed</span>`:''}`;
  }

  const kpiRow = document.getElementById('bk-kpi-row');
  if (kpiRow) {
    kpiRow.innerHTML = `
      <div class="bk-kpi-card">
        <div class="bk-kpi-label">Successful (24h)</div>
        <div class="bk-kpi-val">${success24}</div>
      </div>
      <div class="bk-kpi-card">
        <div class="bk-kpi-label">Failed (24h)</div>
        <div class="bk-kpi-val ${failed24?'text-down':''}">${failed24}</div>
      </div>
      <div class="bk-kpi-card">
        <div class="bk-kpi-label">Disabled</div>
        <div class="bk-kpi-val">${disabledCnt}</div>
      </div>
      <div class="bk-kpi-card">
        <div class="bk-kpi-label">Avg size</div>
        <div class="bk-kpi-val">${avgKb}<span class="bk-kpi-unit">KB</span></div>
      </div>
      <div class="bk-kpi-card">
        <div class="bk-kpi-label">Storage used</div>
        <div class="bk-kpi-val">${storageStr}</div>
      </div>`;
  }
}

function _bkFmtBytes(n) {
  if (!n) return `0<span class="bk-kpi-unit">B</span>`;
  if (n < 1024) return `${n}<span class="bk-kpi-unit">B</span>`;
  if (n < 1024*1024) return `${(n/1024).toFixed(1)}<span class="bk-kpi-unit">KB</span>`;
  if (n < 1024*1024*1024) return `${(n/1024/1024).toFixed(1)}<span class="bk-kpi-unit">MB</span>`;
  return `${(n/1024/1024/1024).toFixed(2)}<span class="bk-kpi-unit">GB</span>`;
}

// ── Eligibility heuristic ─────────────────────────────────────────────
// Returns false for internet monitors that can never have SSH credentials.
function _bkIsEligible(dev) {
  if (dev.enabled || dev.username) return true; // explicitly configured
  const h = dev.host || '';
  return /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|127\.)/.test(h);
}

// ── Table ─────────────────────────────────────────────────────────────
function _bkRenderTable(devices) {
  const wrap = document.getElementById('bk-table-wrap');
  if (!wrap) return;

  if (!devices.length) {
    wrap.innerHTML = '<div class="bk-empty">No devices found. Add devices in the Devices tab first.</div>';
    return;
  }

  function buildRows(devList) {
    return devList.map(dev => {
      const isRunning = _bkRunning.has(dev.did);
      const eligible  = _bkIsEligible(dev);

      // Device cell — name + host + vendor/group chip
      const vendorChip = dev.group ? `<span class="bk-vendor">${esc(dev.group)}</span>` : '';
      const nameCell = dev.orphaned
        ? `<div class="bk-orphaned" title="Device no longer exists">⚠ ${esc(dev.did)}</div>`
        : `<div class="bk-dev-name"><strong>${esc(dev.name || dev.did)}</strong></div>
           <div class="bk-dev-sub mono">${esc(dev.host || '—')}${vendorChip ? ' · ' + vendorChip : ''}</div>`;

      // Last success
      const lastCell = (dev.last_success && dev.last_ts)
        ? `<span class="muted small">${_bkRelTime(dev.last_ts)}</span>`
        : '<span class="bk-never">—</span>';

      // Size
      const sizeKb = (typeof dev.last_size === 'number')
        ? `${(dev.last_size/1024).toFixed(1)}<span class="muted"> KB</span>`
        : '<span class="bk-never">—</span>';

      // Version — short tag from run id (best-effort visual placeholder; the
      // real config version is opaque, so we surface the run id as r###)
      const verCell = dev.last_run_id
        ? `<span class="bk-ver mono">r${esc(String(dev.last_run_id).slice(-4))}</span>`
        : '<span class="bk-never">—</span>';

      // 14-day strip — paint per-day status from backend; fall back to a
      // placeholder bar when no history is available yet.
      const stripCell = Array.isArray(dev.strip_14d) && dev.strip_14d.length
        ? _bkStripFromArr(dev.strip_14d)
        : _bkStripPlaceholder(dev);

      // Diff since — surface real line-count from the previous successful run.
      // Click opens the History modal so the user can pick which two runs to
      // diff (matches the existing compare workflow).
      const diffCell = (typeof dev.last_diff_lines === 'number')
        ? (dev.last_diff_lines === 0
            ? '<span class="muted small">no changes</span>'
            : `<a class="bk-diff-link" onclick="event.stopPropagation();_bkOpenHistory('${esc(dev.did)}')" title="Open backup history to compare runs">${dev.last_diff_lines} line${dev.last_diff_lines===1?'':'s'} changed</a>`)
        : '<span class="bk-never">—</span>';

      // Status pill
      const statusCell = isRunning
        ? '<span class="bk-pill running">running</span>'
        : (dev.last_success === true ? '<span class="bk-pill ok">OK</span>'
         : dev.last_success === false ? '<span class="bk-pill fail">Failed</span>'
         : dev.in_schedule ? '<span class="bk-pill pending">Pending</span>'
         : '<span class="bk-pill off">Disabled</span>');

      // Row click opens the latest config (if any). If no backup exists yet,
      // fall back to opening settings so the row stays useful.
      const rowAction = dev.last_run_id
        ? `_bkOpenViewer(${dev.last_run_id},'${esc(dev.did)}')`
        : `_bkOpenSettings('${esc(dev.did)}')`;
      const rowTitle  = dev.last_run_id
        ? 'Click to view latest config'
        : 'Click to configure — no backups yet';

      // Right-hand actions: Extract subnets + Run + Edit settings.
      // Extract is only meaningful once the device has a successful backup
      // (we need its config text), so we gate on last_run_id + last_success.
      const runBtn = eligible
        ? `<button class="iconbtn ${isRunning?'bk-btn-spin':''}" onclick="event.stopPropagation();_bkTriggerRun('${esc(dev.did)}')" ${isRunning?'disabled':''} title="Run backup now">${icon('play',13)}</button>`
        : '';
      const extractBtn = (dev.last_run_id && dev.last_success === true)
        ? `<button class="iconbtn rbac-op" onclick="event.stopPropagation();_bkExtractSubnets('${esc(dev.did)}','${esc(dev.name||dev.did)}')" title="Extract subnets from this config into IPAM">${icon('ipam',13)}</button>`
        : '';
      const setBtn = `<button class="iconbtn" onclick="event.stopPropagation();_bkOpenSettings('${esc(dev.did)}')" title="Edit device settings">${icon('edit',13)}</button>`;

      return `<tr onclick="${rowAction}" title="${rowTitle}">
        <td>${nameCell}</td>
        <td>${lastCell}</td>
        <td class="bk-mono">${sizeKb}</td>
        <td>${verCell}</td>
        <td>${stripCell}</td>
        <td>${diffCell}</td>
        <td>${statusCell}</td>
        <td class="bk-acts" onclick="event.stopPropagation()">${extractBtn}${runBtn}${setBtn}</td>
      </tr>`;
    }).join('');
  }

  // Bucket a list of devices by site for the secondary-axis grouping.
  // Returns a sorted array of [siteName, devList] tuples with Unsited last.
  function bucketBySite(devList) {
    const map = new Map();
    for (const d of devList) {
      const site = (d.site || '').trim() || _BK_UNSITED;
      if (!map.has(site)) map.set(site, []);
      map.get(site).push(d);
    }
    return [...map.entries()].sort(([a], [b]) => {
      if (a === _BK_UNSITED) return  1;
      if (b === _BK_UNSITED) return -1;
      return a.localeCompare(b);
    });
  }

  // Tag a device row's source <tr> output with the composite site key so
  // _bkToggleSite can hide/show every device in a site without touching
  // the header row. We splice the data-attribute into the open tag the
  // existing buildRows() produces — easier than fanning the attribute all
  // the way down through the row builder.
  function tagRowsWithSite(html, key) {
    return html.replace(/<tr /g, `<tr data-bk-site-row="${key}" `);
  }

  // Interleaved site-header rows + device rows inside a single section
  // <tbody>. Multiple <tbody> elements at the same <table> level are valid
  // HTML; *nesting* tbodies is not, so we keep everything as <tr> here.
  function buildSectionBody(sectionKey, devList) {
    const buckets = bucketBySite(devList);
    return buckets.map(([siteName, sd]) => {
      const compositeKey  = sectionKey + ':' + siteName;
      const defaultClosed = (sectionKey === 'disabled');
      const userToggled   = _bkSiteCollapsed.has(compositeKey);
      const collapsed     = defaultClosed ? !userToggled : userToggled;
      const idSafe        = compositeKey.replace(/[^A-Za-z0-9_-]/g, '_');
      const arrCls        = collapsed ? 'bk-grp-arr' : 'bk-grp-arr open';
      const rowStyle      = collapsed ? 'style="display:none"' : '';
      // Header row uses the same .bk-grp-hdr / .bk-site-hdr classes so the
      // existing CSS picks it up as a header band. The header is always
      // visible; only the device rows beneath it collapse.
      const header = `
        <tr class="bk-grp-hdr bk-site-hdr" onclick="_bkToggleSite('${compositeKey.replace(/'/g, "\\'")}')">
          <td colspan="8">
            <span class="${arrCls}" id="bk-site-arr-${idSafe}">▶</span>
            <span class="bk-grp-title bk-site-title">${esc(siteName)}</span>
            <span class="bk-grp-cnt">${sd.length}</span>
          </td>
        </tr>`;
      // buildRows returns the raw <tr> markup for each device; we splice
      // a data-bk-site-row attribute onto each so the toggle handler can
      // find them, plus an inline display:none when collapsed by default.
      let rows = tagRowsWithSite(buildRows(sd), compositeKey);
      if (collapsed) rows = rows.replace(/<tr /g, '<tr style="display:none" ');
      return header + rows;
    }).join('');
  }

  function buildSection(label, key, devList) {
    const expanded = _bkGrpExpanded[key] !== false;
    const arrClass = expanded ? 'bk-grp-arr open' : 'bk-grp-arr';
    const bodyAttr = expanded ? '' : ' class="bk-grp-collapsed"';
    const inner = devList.length
      ? buildSectionBody(key, devList)
      : '<tr><td colspan="8" class="muted" style="padding:10px 14px;font-size:12px">No devices</td></tr>';
    return `
      <tbody class="bk-grp-hdr" onclick="_bkToggleGroup('${key}')">
        <tr><td colspan="8">
          <span class="${arrClass}" id="bk-arr-${key}">▶</span>
          <span class="bk-grp-title">${label}</span>
          <span class="bk-grp-cnt">${devList.length}</span>
        </td></tr>
      </tbody>
      <tbody id="bk-grp-${key}"${bodyAttr}>${inner}</tbody>`;
  }

  const eligible     = devices.filter(_bkIsEligible);
  const enabledDevs  = eligible.filter(d => d.in_schedule === true);
  const disabledDevs = eligible.filter(d => d.in_schedule !== true);

  wrap.innerHTML = `
    <table class="bk-table">
      <thead>
        <tr>
          <th>Device</th>
          <th>Last success</th>
          <th>Size</th>
          <th>Version</th>
          <th>14-day history</th>
          <th>Diff since</th>
          <th>Status</th>
          <th></th>
        </tr>
      </thead>
      ${buildSection('Backup Enabled',  'enabled',  enabledDevs)}
      ${buildSection('Backup Disabled', 'disabled', disabledDevs)}
    </table>`;
}

// Paint a 14-day strip from the backend-provided array. Each entry is
// 'ok' / 'fail' / 'none' in oldest→newest order; the rightmost bar = today.
function _bkStripFromArr(arr) {
  const days = Math.min(14, arr.length);
  const bars = [];
  let ok = 0, fail = 0, miss = 0;
  for (let i = 0; i < days; i++) {
    const s = arr[i];
    const cls = (s === 'ok') ? 'ok' : (s === 'fail') ? 'fail' : 'miss';
    if (cls === 'ok') ok++; else if (cls === 'fail') fail++; else miss++;
    bars.push(`<span class="bk-strip-bar ${cls}"></span>`);
  }
  const tip = `Last 14 days · ${ok} ok · ${fail} fail · ${miss} no run`;
  return `<div class="bk-strip" title="${tip}">${bars.join('')}</div>`;
}

// Fallback strip when no per-day data is available yet (e.g. legacy installs
// before the v1.0 backend enrichment landed, or first-time render before the
// fetch completes). Bars reflect only the most recent run.
function _bkStripPlaceholder(dev) {
  if (!dev.last_run_id) {
    return '<div class="bk-strip empty"></div>';
  }
  const days = 14;
  const fail = dev.last_success === false;
  const bars = [];
  for (let i = 0; i < days; i++) {
    const isLast = i === days - 1;
    const cls = fail && isLast ? 'fail' : 'ok';
    bars.push(`<span class="bk-strip-bar ${cls}"></span>`);
  }
  return `<div class="bk-strip" title="Last 14 days (history not available yet)">${bars.join('')}</div>`;
}

function _bkToggleGroup(key) {
  const body = document.getElementById('bk-grp-' + key);
  const arr  = document.getElementById('bk-arr-' + key);
  if (!body) return;
  const nowCollapsed = body.classList.toggle('bk-grp-collapsed');
  _bkGrpExpanded[key] = !nowCollapsed;
  if (arr) arr.classList.toggle('open', !nowCollapsed);
}

function _bkStatusCell(dev, isRunning) {
  if (isRunning) return '<span class="bk-running">⟳ Running…</span>';
  if (dev.last_success === null) return '<span class="bk-never">—</span>';
  if (dev.last_success) return '<span class="bk-ok">✓ Success</span>';
  return `<span class="bk-fail" title="${esc(dev.last_error || '')}">✗ Failed</span>`;
}

/** Safely parse ISO timestamps — appends 'Z' only if no timezone info present. */
function _bkParseTs(isoStr) {
  if (!isoStr) return null;
  // Match Z, +HH:MM, or -HH:MM (Python isoformat produces the +HH:MM form)
  return new Date(/Z$|[+\-]\d{2}:\d{2}$/.test(isoStr) ? isoStr : isoStr + 'Z');
}

function _bkRelTime(isoStr) {
  if (!isoStr) return '—';
  const t = _bkParseTs(isoStr)?.getTime();
  if (!t || isNaN(t)) return '—';
  const diff = (Date.now() - t) / 1000;
  if (diff < 60)    return `${Math.round(diff)}s ago`;
  if (diff < 3600)  return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

// ── Extract subnets from the most recent backup ───────────────────────
// Calls the extractor on the device's latest successful config, then hands
// the resulting CSV off to the Import Subnets modal pre-filled. The user
// reviews + confirms there; this function never writes to IPAM directly.
async function _bkExtractSubnets(did, deviceName) {
  if (typeof window._ipamOpenImport !== 'function' && typeof _ipamOpenImport !== 'function') {
    toast('IPAM module not loaded', 'err');
    return;
  }
  toast('Extracting subnets…');
  let j;
  try {
    const r = await fetch(`/api/backups/${encodeURIComponent(did)}/extract-subnets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({}),
    });
    j = await r.json().catch(() => ({}));
    if (!r.ok) {
      toast(j.error || `Extract failed: ${r.statusText}`, 'err');
      return;
    }
  } catch (e) {
    toast('Extract request failed: ' + (e.message || e), 'err');
    return;
  }
  const vendor = j.vendor || '';
  const rows   = j.rows   || [];
  if (!rows.length) {
    toast(vendor
      ? `No subnets found in ${deviceName}'s ${vendor} config`
      : `Could not detect vendor for ${deviceName} — paste manually`,
      'warn');
    return;
  }
  const banner =
    `<strong>Pre-filled from ${esc(deviceName)}</strong>` +
    (vendor ? ` · vendor: ${esc(vendor)}` : '') +
    ` · ${rows.length} subnet${rows.length===1?'':'s'} found` +
    (j.source_site ? ` · site default: <strong>${esc(j.source_site)}</strong>` : '') +
    `<div style="margin-top:3px;opacity:0.85;font-size:11px">Review the rows below, uncheck anything you don't want, then click Import.</div>`;
  _ipamOpenImport({
    prefillText: j.csv || '',
    banner: banner,
    autoPreview: true,
  });
}

// ── Trigger backup ────────────────────────────────────────────────────
async function _bkTriggerRun(did) {
  _bkRunning.add(did);
  _bkUpdateRow(did);
  try {
    const r = await api('POST', `/api/backups/${did}/run`, {});
    if (!r.started) { toast('Failed to start backup', 'err'); _bkRunning.delete(did); _bkUpdateRow(did); }
  } catch (e) {
    toast('Backup request failed', 'err');
    _bkRunning.delete(did);
    _bkUpdateRow(did);
  }
}

async function _bkRunAll() {
  const enabled = _bkDevices.filter(d => d.enabled);
  if (!enabled.length) { toast('No enabled backup devices', 'warn'); return; }
  for (const dev of enabled) {
    _bkRunning.add(dev.did);
  }
  _bkRenderTable(_bkDevices);
  for (const dev of enabled) {
    await api('POST', `/api/backups/${dev.did}/run`, {});
    await new Promise(r => setTimeout(r, 200)); // small stagger
  }
}

function _bkUpdateRow(did) {
  // Re-render only the affected rows without full table rebuild
  const dev = _bkDevices.find(d => d.did === did);
  if (!dev) return;
  _bkRenderTable(_bkDevices); // simple full re-render (table is small)
}

// ── SSE handler ───────────────────────────────────────────────────────
function _bkOnBackupComplete(evt) {
  const did = evt.did;
  _bkRunning.delete(did);
  // Update the cached entry
  const entry = _bkDevices.find(d => d.did === did);
  if (entry) {
    entry.last_ts      = evt.ts;
    entry.last_success = evt.success;
    entry.last_error   = evt.error || '';
    entry.last_run_id  = evt.run_id || entry.last_run_id;
    entry.last_size    = evt.size || 0;
    // run_count is capped at _bkKeepMax because old entries are pruned on each run
    entry.run_count    = Math.min(_bkKeepMax, (entry.run_count || 0) + 1);
  }
  if (activeMainTab === 'backups') _bkRenderTable(_bkDevices);
  const name = entry?.name || did;
  if (evt.success) toast(`Backup complete: ${name}`, 'ok');
  else toast(`Backup failed: ${name} — ${evt.error || '?'}`, 'err');
}

// ── Settings Modal ────────────────────────────────────────────────────
async function _bkOpenSettings(did) {
  closeM('bk-settings');
  let d;
  try {
    const r = await fetch(`/api/backups/${did}`);
    if (!r.ok) { toast('Failed to load backup settings', 'err'); return; }
    d = await r.json();
  } catch { toast('Network error loading backup settings', 'err'); return; }
  const s = d.settings || {};
  const dev = _bkDevices.find(x => x.did === did) || {};

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'bk-settings';
  _overlayClose(o, ()=>closeM('bk-settings'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,560px)">
      <div class="mhd">
        <div class="mttl">⚙ Backup Settings — ${esc(dev.name || did)}</div>
        <button class="mclose" onclick="closeM('bk-settings')">✕</button>
      </div>
      <div class="mbdy" style="gap:10px">
        <div class="fr">
          <label class="fl">Enabled</label>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="bks-enabled" ${s.enabled ? 'checked' : ''}/>
            <span style="font-size:12px;color:var(--text2)">Enable scheduled and manual backups</span>
          </label>
        </div>
        <div class="fgrid" style="grid-template-columns:1fr 1fr">
          <div class="fr"><label class="fl">Method</label>
            <select id="bks-method" style="width:100%">
              <option value="ssh"    ${s.method==='ssh'?'selected':''}>SSH</option>
              <option value="telnet" ${s.method==='telnet'?'selected':''}>Telnet</option>
            </select>
            <div id="bks-telnet-warn" style="display:${s.method==='telnet'?'block':'none'};margin-top:4px;font-size:11px;color:var(--warn,#e8a735)">
              Telnet transmits credentials in plaintext. Use SSH when possible.
            </div>
          </div>
          <div class="fr"><label class="fl">Port</label>
            <input type="number" id="bks-port" value="${s.port || 22}" min="1" max="65535"/>
          </div>
        </div>
        <div class="fgrid" style="grid-template-columns:1fr 1fr">
          <div class="fr"><label class="fl">Username</label>
            <input type="text" id="bks-user" value="${esc(s.username || '')}" autocomplete="off"/>
          </div>
          <div class="fr"><label class="fl">Timeout (s)</label>
            <input type="number" id="bks-timeout" value="${s.timeout || 30}" min="5" max="300"/>
          </div>
        </div>
        <div class="fr">
          <label class="fl">Password <span style="color:var(--text3);font-size:10px">${s.has_password ? '(set — leave blank to keep)' : '(not set)'}</span></label>
          <input type="password" id="bks-pw" placeholder="${s.has_password ? '••••••••' : 'Enter password…'}" autocomplete="new-password"/>
        </div>
        <div class="fr">
          <label class="fl">Enable Password <span style="color:var(--text3);font-size:10px">(Cisco enable mode, optional)</span></label>
          <input type="password" id="bks-en" placeholder="${s.has_enable ? '••••••••' : 'Not set'}"/>
        </div>
        <div class="fr">
          <label class="fl">Paging Disable Command</label>
          <input type="text" id="bks-paging" value="${esc(s.paging_cmd || '')}" placeholder="e.g. terminal length 0"/>
        </div>
        <div class="fr">
          <label class="fl">Commands <span style="color:var(--text3);font-size:10px">(one per line)</span></label>
          <textarea id="bks-cmds" rows="4" style="font-family:monospace;font-size:11px">${esc((s.commands || ['show running-config']).join('\n'))}</textarea>
        </div>
        <div class="fr">
          <label class="fl">Expected Content <span style="color:var(--text3);font-size:10px">(optional — fail the backup if the output doesn't contain this)</span></label>
          <input type="text" id="bks-expected" value="${esc(s.expected_content || '')}" placeholder="e.g. hostname    or    ^end$ (regex)"/>
          <div style="display:flex;align-items:center;gap:28px;margin-top:10px;flex-wrap:wrap">
            <div style="display:flex;align-items:center;gap:8px">
              <label class="toggle"><input type="checkbox" id="bks-expected-regex" ${s.expected_is_regex ? 'checked' : ''}><span class="tsl"></span></label>
              <span style="font-size:12px;color:var(--text2)">Regex <span style="color:var(--text3)">(advanced)</span></span>
            </div>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-size:12px;color:var(--text2)">Min size</span>
              <input type="number" id="bks-minbytes" value="${s.min_bytes || 0}" min="0" max="10000000" style="max-width:96px"/>
              <span style="font-size:12px;color:var(--text3)">bytes</span>
            </div>
          </div>
          <div class="fh" style="margin-top:6px">An <b>empty</b> response is always marked failed. Substring match is case-insensitive. Min size 0 = off.</div>
        </div>
        <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:0">
          <div style="flex:1">
            <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Add to Scheduled Backup</div>
            <div class="fh" style="margin:0">Runs when the global backup schedule fires (Settings → Config Backup)</div>
          </div>
          <label class="toggle" style="flex-shrink:0"><input type="checkbox" id="bks-in-schedule" ${s.in_schedule ? 'checked' : ''}><span class="tsl"></span></label>
        </div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('bk-settings')">Cancel</button>
        ${dev.last_run_id ? `<button class="btn-s" onclick="closeM('bk-settings');_bkOpenHistory('${esc(did)}')">History</button>` : ''}
        <button class="btn-p" onclick="_bkSaveSettings('${esc(did)}')">Save Settings</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  const _mSel = document.getElementById('bks-method');
  if (_mSel) _mSel.onchange = () => {
    const w = document.getElementById('bks-telnet-warn');
    if (w) w.style.display = _mSel.value === 'telnet' ? 'block' : 'none';
  };
}

async function _bkSaveSettings(did) {
  const btn = document.querySelector('#bk-settings .btn-p');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  const cmdsRaw = (document.getElementById('bks-cmds')?.value || '').trim();
  const commands = cmdsRaw.split('\n').map(l => l.trim()).filter(Boolean);

  const payload = {
    enabled:         document.getElementById('bks-enabled')?.checked  || false,
    method:          document.getElementById('bks-method')?.value      || 'ssh',
    port:            parseInt(document.getElementById('bks-port')?.value) || 22,
    username:        document.getElementById('bks-user')?.value?.trim() || '',
    password:        document.getElementById('bks-pw')?.value          || '',
    enable_password: document.getElementById('bks-en')?.value          || '',
    paging_cmd:      document.getElementById('bks-paging')?.value?.trim() || '',
    commands,
    timeout:         parseInt(document.getElementById('bks-timeout')?.value) || 30,
    in_schedule:     document.getElementById('bks-in-schedule')?.checked || false,
    expected_content:  document.getElementById('bks-expected')?.value?.trim() || '',
    expected_is_regex: document.getElementById('bks-expected-regex')?.checked || false,
    min_bytes:         parseInt(document.getElementById('bks-minbytes')?.value) || 0,
  };

  const r = await api('PUT', `/api/backups/${did}`, payload);
  if (btn) { btn.disabled = false; btn.textContent = 'Save Settings'; }
  if (!r.ok) { toast('Failed to save settings', 'err'); return; }

  // Update local cache
  const entry = _bkDevices.find(d => d.did === did);
  if (entry) {
    entry.enabled     = payload.enabled;
    entry.method      = payload.method;
    entry.username    = payload.username;
    entry.in_schedule = payload.in_schedule;
    entry.has_password = entry.has_password || !!payload.password;
  }

  closeM('bk-settings');
  toast('Backup settings saved', 'ok');
  _bkRenderTable(_bkDevices);
}

// ── History Modal ─────────────────────────────────────────────────────
async function _bkOpenHistory(did) {
  closeM('bk-history');
  const r   = await fetch(`/api/backups/${did}/history`);
  const d   = await r.json();
  const dev = _bkDevices.find(x => x.did === did) || {};
  const runs = d.history || [];

  const rows = runs.map(run => `
    <tr>
      <td><input type="checkbox" class="bk-sel" value="${run.id}" onchange="_bkSelChange()"/></td>
      <td>${(_bkParseTs(run.ts)?.toLocaleString() ?? run.ts)}</td>
      <td>${run.success ? '<span class="bk-ok">✓</span>' : '<span class="bk-fail">✗</span>'}</td>
      <td class="bk-mono">${run.size_bytes ? (run.size_bytes / 1024).toFixed(1) + ' KB' : '—'}</td>
      <td class="bk-mono" style="font-size:10px">${run.sha256 ? run.sha256.slice(0,8) + '…' : '—'}</td>
      <td><button class="btn-sm" onclick="closeM('bk-history');_bkOpenViewer(${run.id},'${esc(did)}')">📄 View</button></td>
    </tr>`).join('') || '<tr><td colspan="6" style="color:var(--text3);text-align:center">No backups yet</td></tr>';

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'bk-history';
  o.onclick = e => { if (e.target === o) closeM('bk-history'); };
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,600px)">
      <div class="mhd">
        <div class="mttl">📋 Backup History — ${esc(dev.name || did)}</div>
        <button class="mclose" onclick="closeM('bk-history')">✕</button>
      </div>
      <div class="mbdy" style="padding:0;overflow:auto">
        <table class="bk-table" style="margin:0">
          <thead><tr>
            <th style="width:32px" title="Select 2 runs to compare">⬌</th>
            <th>Time</th><th>Status</th><th>Size</th><th>SHA256</th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('bk-history')">Close</button>
        <button class="btn-p" id="bk-cmp-btn" disabled onclick="_bkOpenDiff('${esc(did)}')">⬌ Compare Selected</button>
      </div>
    </div>`;
  document.body.appendChild(o);
}

function _bkSelChange() {
  const checked = document.querySelectorAll('.bk-sel:checked').length;
  const btn = document.getElementById('bk-cmp-btn');
  if (btn) btn.disabled = checked !== 2;
}

// ── Diff Viewer ───────────────────────────────────────────────────
async function _bkOpenDiff(did) {
  const ids = [...document.querySelectorAll('.bk-sel:checked')].map(c => +c.value);
  if (ids.length !== 2) return;

  const btn = document.getElementById('bk-cmp-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }

  let ra, rb;
  try {
    [ra, rb] = await Promise.all(ids.map(id => api('GET', `/api/backups/run/${id}`)));
  } catch {
    toast('Failed to load runs for comparison', 'err');
    if (btn) { btn.disabled = false; btn.textContent = '⬌ Compare Selected'; }
    return;
  }

  // Older = A (base), newer = B (current)
  const [runA, runB] = ra.run.ts <= rb.run.ts ? [ra.run, rb.run] : [rb.run, ra.run];
  const dev = _bkDevices.find(x => x.did === did) || {};

  if (!runA.config || !runB.config) {
    toast('One or both selected runs have no config content', 'warn');
    if (btn) { btn.disabled = false; btn.textContent = '⬌ Compare Selected'; }
    return;
  }

  const linesA = runA.config.split('\n');
  const linesB = runB.config.split('\n');

  // Use patience diff for large configs (avoids O(n²) LCS freeze)
  const diff = linesA.length > 3000 || linesB.length > 3000
    ? _bkComputeDiffLarge(linesA, linesB)
    : _bkComputeDiff(linesA, linesB);

  closeM('bk-history');
  _bkRenderDiffModal(runA, runB, diff, dev.name || did);
}

function _bkComputeDiff(linesA, linesB) {
  // LCS-based line diff — returns [{type:'eq'|'add'|'del', line, lnA, lnB}]
  const n = linesA.length, m = linesB.length;
  const dp = Array.from({length: n + 1}, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = linesA[i] === linesB[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0, lnA = 1, lnB = 1;
  while (i < n || j < m) {
    if (i < n && j < m && linesA[i] === linesB[j]) {
      out.push({type: 'eq',  line: linesA[i], lnA: lnA++, lnB: lnB++}); i++; j++;
    } else if (j < m && (i >= n || dp[i][j + 1] >= dp[i + 1][j])) {
      out.push({type: 'add', line: linesB[j], lnA: null,  lnB: lnB++}); j++;
    } else {
      out.push({type: 'del', line: linesA[i], lnA: lnA++, lnB: null}); i++;
    }
  }
  return out;
}

// Patience diff for large configs (O(n·d), same output format as _bkComputeDiff).
// 1. Find "anchor" lines unique in both files → longest increasing subsequence.
// 2. Between each anchor pair run small LCS segments (each segment is bounded).
function _bkComputeDiffLarge(linesA, linesB) {
  const cntA = new Map(), cntB = new Map();
  linesA.forEach(l => cntA.set(l, (cntA.get(l) || 0) + 1));
  linesB.forEach(l => cntB.set(l, (cntB.get(l) || 0) + 1));

  // Map unique-in-B lines to their index
  const lineToB = new Map();
  for (let j = 0; j < linesB.length; j++)
    if (cntB.get(linesB[j]) === 1 && cntA.get(linesB[j]) === 1)
      lineToB.set(linesB[j], j);

  // Collect anchor pairs from A (unique in both)
  const pairs = [];
  for (let i = 0; i < linesA.length; i++)
    if (lineToB.has(linesA[i]))
      pairs.push({ai: i, bi: lineToB.get(linesA[i])});

  // LIS of bi values → actual matched anchor sequence
  const matched = _bkLIS(pairs);

  const out = [];
  let ai = 0, bi = 0;
  for (const {ai: mAi, bi: mBi} of matched) {
    _bkDiffSeg(linesA, linesB, ai, mAi, bi, mBi, out);
    out.push({type: 'eq', line: linesA[mAi], lnA: mAi + 1, lnB: mBi + 1});
    ai = mAi + 1;
    bi = mBi + 1;
  }
  _bkDiffSeg(linesA, linesB, ai, linesA.length, bi, linesB.length, out);
  return out;
}

function _bkLIS(pairs) {
  // Longest Increasing Subsequence of pairs by .bi — returns the actual subsequence.
  if (!pairs.length) return [];
  const tails = [], tailI = [], pred = new Array(pairs.length).fill(-1);
  for (let i = 0; i < pairs.length; i++) {
    const v = pairs[i].bi;
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; tails[mid] < v ? lo = mid + 1 : hi = mid; }
    tails[lo] = v; tailI[lo] = i;
    if (lo > 0) pred[i] = tailI[lo - 1];
  }
  const result = [];
  for (let k = tailI[tails.length - 1]; k !== -1; k = pred[k])
    result.unshift(pairs[k]);
  return result;
}

function _bkDiffSeg(linesA, linesB, ai, aEnd, bi, bEnd, out) {
  const la = aEnd - ai, lb = bEnd - bi;
  if (la === 0 && lb === 0) return;
  if (la === 0) { for (let j = bi; j < bEnd; j++) out.push({type:'add', line:linesB[j], lnA:null,   lnB:j+1}); return; }
  if (lb === 0) { for (let i = ai; i < aEnd; i++) out.push({type:'del', line:linesA[i], lnA:i+1, lnB:null}); return; }
  if (la * lb <= 90000) {
    // Small segment — full LCS
    const dp = Array.from({length: la + 1}, () => new Uint32Array(lb + 1));
    for (let i = la - 1; i >= 0; i--)
      for (let j = lb - 1; j >= 0; j--)
        dp[i][j] = linesA[ai+i] === linesB[bi+j] ? dp[i+1][j+1]+1 : Math.max(dp[i+1][j], dp[i][j+1]);
    let i = 0, j = 0;
    while (i < la || j < lb) {
      if (i < la && j < lb && linesA[ai+i] === linesB[bi+j]) {
        out.push({type:'eq',  line:linesA[ai+i], lnA:ai+i+1, lnB:bi+j+1}); i++; j++;
      } else if (j < lb && (i >= la || dp[i][j+1] >= dp[i+1][j])) {
        out.push({type:'add', line:linesB[bi+j], lnA:null,   lnB:bi+j+1}); j++;
      } else {
        out.push({type:'del', line:linesA[ai+i], lnA:ai+i+1, lnB:null}); i++;
      }
    }
  } else {
    // Unusually large unanchored block — emit as bulk replace
    for (let i = ai; i < aEnd; i++) out.push({type:'del', line:linesA[i], lnA:i+1, lnB:null});
    for (let j = bi; j < bEnd; j++) out.push({type:'add', line:linesB[j], lnA:null, lnB:j+1});
  }
}

function _bkRenderDiffModal(runA, runB, diff, deviceName) {
  closeM('bk-diff');
  const tsA = _bkParseTs(runA.ts)?.toLocaleString() ?? runA.ts;
  const tsB = _bkParseTs(runB.ts)?.toLocaleString() ?? runB.ts;

  let adds = 0, dels = 0, diffHtml = '';
  if (diff) {
    adds = diff.filter(d => d.type === 'add').length;
    dels = diff.filter(d => d.type === 'del').length;
    diffHtml = _bkRenderDiffLines(diff);
  } else {
    diffHtml = `<div style="padding:24px;text-align:center;color:var(--text2)">No diff available</div>`;
  }

  // Detect vendor and generate rollback
  const vendor   = _bkDetectVendor(runA.config || runB.config || '');
  const rollback = diff ? _bkGenerateRollback(diff, vendor) : [];

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'bk-diff';
  o.onclick = e => { if (e.target === o) closeM('bk-diff'); };
  o.innerHTML = `
    <div class="mbox" style="width:min(96vw,980px);max-height:92vh;display:flex;flex-direction:column">
      <div class="mhd">
        <div class="mttl">⬌ Config Diff — ${esc(deviceName)}</div>
        <button class="mclose" onclick="closeM('bk-diff')">✕</button>
      </div>
      <div class="bk-diff-meta" style="flex-shrink:0">
        <div class="bk-diff-meta-row">
          <span class="bk-diff-lbl bk-dl-del-lbl">Base</span>
          <span>${esc(tsA)}</span>
          <span class="bk-mono" style="font-size:10px;color:var(--text3)">${runA.sha256 ? runA.sha256.slice(0,8) + '…' : '—'}</span>
          <span style="color:var(--text3)">${runA.size_bytes ? (runA.size_bytes/1024).toFixed(1)+' KB' : '—'}</span>
        </div>
        <div class="bk-diff-meta-row">
          <span class="bk-diff-lbl bk-dl-add-lbl">Current</span>
          <span>${esc(tsB)}</span>
          <span class="bk-mono" style="font-size:10px;color:var(--text3)">${runB.sha256 ? runB.sha256.slice(0,8) + '…' : '—'}</span>
          <span style="color:var(--text3)">${runB.size_bytes ? (runB.size_bytes/1024).toFixed(1)+' KB' : '—'}</span>
        </div>
        ${diff ? `<div class="bk-diff-summary" id="bk-diff-summary">
          <span class="bk-diff-adds" id="bk-diff-adds">+${adds} added</span>
          <span class="bk-diff-dels" id="bk-diff-dels">-${dels} removed</span>
          ${adds === 0 && dels === 0 ? '<span style="color:var(--text3)">No changes</span>' : ''}
          <label class="bk-enc-toggle" title="Hide ENC/password lines that change every backup (FortiGate, etc.)">
            <input type="checkbox" id="bk-enc-toggle" onchange="_bkToggleEncNoise()"/> Hide credential noise
          </label>
        </div>` : ''}
      </div>
      <div id="bk-diff-srch-bar" class="bk-srch-bar" style="flex-shrink:0">
        <input id="bk-diff-srch-inp" type="text" placeholder="Search diff…" oninput="_bkDiffSearch()" autocomplete="off"/>
        <span id="bk-diff-srch-cnt" class="bk-srch-cnt"></span>
        <button class="btn-sm" onclick="_bkDiffSearchNav(-1)" title="Previous">↑</button>
        <button class="btn-sm" onclick="_bkDiffSearchNav(+1)" title="Next">↓</button>
      </div>
      <div class="bk-diff-wrap" style="flex:1" id="bk-diff-body">${diffHtml}</div>
      ${rollback.length ? `
      <details class="bk-rollback" id="bk-rollback-details">
        <summary id="bk-rollback-summary">⚠ Rollback Command Preview (${rollback.cmdCount ?? rollback.length} commands)</summary>
        <div class="bk-rollback-warn" id="bk-rollback-warn">${_bkRollbackWarn(vendor)}</div>
        <pre class="bk-cfg-pre" id="bk-rollback-pre">${esc(rollback.join('\n'))}</pre>
        <div style="padding:6px 14px 10px"><button class="btn-sm" onclick="_bkCopyRollback()">📋 Copy Commands</button></div>
      </details>` : ''}
      <div class="mft"><button class="btn-s" onclick="closeM('bk-diff')">Close</button></div>
    </div>`;
  document.body.appendChild(o);
  // Store diff + vendor for expand/filter handlers
  if (diff) { const el = document.getElementById('bk-diff'); el._diffData = diff; el._vendor = vendor; }
}

// ── Vendor detection ──────────────────────────────────────────────
function _bkDetectVendor(config) {
  if (!config) return 'unknown';
  if (/#config-version=fg/i.test(config) || /#conf_file_ver=/i.test(config)) return 'fortigate';
  if (/^asa version |^pix version /im.test(config))                           return 'cisco-asa';
  if (/^## last commit:|^\s*set version \d+\.\d+[^;]/im.test(config))        return 'junos';
  if (/^# routeros/im.test(config))                                           return 'mikrotik';
  if (/^<config>/im.test(config))                                             return 'panos';
  if (/^! device:.*\beos\b/im.test(config))                                  return 'arista';
  return 'ios'; // Cisco IOS / IOS-XE / NX-OS — `no`-prefix CLIs
}

// Vendor-specific warning text shown in the rollback panel.
function _bkRollbackWarn(vendor) {
  const w = {
    fortigate:  'Generated for <strong>FortiGate CLI</strong>. Paste directly into the CLI console — config context is included automatically.',
    'cisco-asa':'Generated for <strong>Cisco ASA</strong>. Apply in <code>conf t</code> mode. Review sub-interface commands carefully.',
    junos:      '⚠ <strong>JunOS</strong> uses <code>delete</code>/<code>set</code> — commands shown are best-effort. Verify each line before applying.',
    mikrotik:   '⚠ <strong>MikroTik RouterOS</strong> uses a different syntax. Commands shown are approximations only — do not apply directly.',
    panos:      '⚠ <strong>Palo Alto</strong> uses XML-based config. Use candidate config or revert — do not apply these commands directly.',
    arista:     'Generated for <strong>Arista EOS</strong>. Apply in <code>conf t</code> mode.',
    ios:        'Generated for <strong>Cisco IOS / IOS-XE / NX-OS</strong>. Apply in <code>conf t</code> mode.',
    unknown:    'Review carefully before applying. Auto-generated from line diff only — vendor not detected.',
  };
  return w[vendor] || w.unknown;
}

// ── Rollback command generator ────────────────────────────────────
function _bkGenerateRollback(diff, vendor = 'ios') {
  if (vendor === 'fortigate') return _bkGenerateRollbackFortigate(diff);

  // IOS / IOS-XE / NX-OS / ASA / Arista EOS:
  // Walk diff tracking the current block-header (last non-indented `eq` line).
  // Rollback commands are grouped under their enclosing block so the output
  // includes the context line (e.g. "interface Ethernet1/8"), the negations,
  // then "end" + save command ready to paste.
  const GLOBAL = '\x00';
  let currentCtx = GLOBAL;
  const groups = new Map(); // context_line → [rollback_cmds]  (insertion-ordered)

  for (const {type, line} of diff) {
    const t = line.trim();
    if (!t || t.startsWith('!') || t.startsWith('#')) continue;

    const indented = line[0] === ' ' || line[0] === '\t';

    if (type === 'eq') {
      // Non-indented unchanged lines establish the current block context
      if (!indented) currentCtx = t;
      continue;
    }

    // Compute the rollback command: negate added lines, restore deleted lines
    const rollCmd = type === 'del' ? t : (t.startsWith('no ') ? t.slice(3) : `no ${t}`);

    // Indented changed lines belong to the current block; non-indented are global
    const ctx = indented ? currentCtx : GLOBAL;
    if (!groups.has(ctx)) groups.set(ctx, []);
    groups.get(ctx).push(rollCmd);
  }

  // Semantic count: rollback commands only (not context headers / end / wr)
  let cmdCount = 0;
  for (const cmds of groups.values()) cmdCount += cmds.length;

  // Build output — context header first, then the rollback commands beneath it
  const out = [];
  for (const [ctx, cmds] of groups) {
    if (ctx !== GLOBAL) out.push(ctx);
    out.push(...cmds);
  }

  // Append vendor-appropriate epilog
  if (out.length) {
    if (vendor === 'cisco-asa') {
      out.push('end');
      out.push('write mem');
    } else if (vendor === 'ios' || vendor === 'arista') {
      out.push('end');
      out.push('wr');
    } else if (vendor === 'junos') {
      out.push('commit');
    }
    // mikrotik / panos / unknown: no epilog — syntax differs too much
  }

  out.cmdCount = cmdCount; // used for the "N commands" summary label
  return out;
}

// FortiGate-aware rollback: tracks config/edit context blocks and emits
// `set field old_value` to restore changed/deleted fields and `unset field`
// for purely added fields. Deduplicates: if both add+del exist for the same
// field, the del (old value restore) takes precedence.
function _bkGenerateRollbackFortigate(diff) {
  const ctxStack = [];
  const groups   = new Map(); // contextKey → {context, dels: Map<field,cmd>, adds: Set<field>}

  const getField = t => { const m = t.match(/^set\s+(\S+)/i); return m ? m[1] : null; };
  const ctxKey   = () => ctxStack.join('\x00');
  const getGroup = () => {
    const k = ctxKey();
    if (!groups.has(k)) groups.set(k, {context: [...ctxStack], dels: new Map(), adds: new Set()});
    return groups.get(k);
  };

  for (const {type, line} of diff) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    if (type === 'eq') {
      if      (/^config\s/i.test(t))  ctxStack.push(t);
      else if (t === 'end')           { while (/^edit\s/i.test(ctxStack[ctxStack.length-1]||'')) ctxStack.pop(); ctxStack.pop(); }
      else if (/^edit\s/i.test(t))    { if (/^edit\s/i.test(ctxStack[ctxStack.length-1]||'')) ctxStack.pop(); ctxStack.push(t); }
      else if (t === 'next')          { if (/^edit\s/i.test(ctxStack[ctxStack.length-1]||'')) ctxStack.pop(); }
    } else {
      const f = getField(t);
      if (!f) continue;
      if (type === 'del') getGroup().dels.set(f, t);
      else                getGroup().adds.add(f);
    }
  }

  const cmds = [];
  for (const [, {context, dels, adds}] of groups) {
    const restore = [...dels.values()];
    const unsets  = [...adds].filter(f => !dels.has(f)).map(f => `unset ${f}`);
    if (!restore.length && !unsets.length) continue;
    let depth = 0;
    for (const ctx of context) { cmds.push('    '.repeat(depth) + ctx); depth++; }
    for (const cmd of [...restore, ...unsets]) cmds.push('    '.repeat(depth) + cmd);
    for (const ctx of [...context].reverse()) {
      depth--;
      cmds.push('    '.repeat(depth) + (/^edit\s/i.test(ctx) ? 'next' : 'end'));
    }
    cmds.push('');
  }
  return cmds.filter(c => c !== undefined);
}

function _bkCopyRollback() {
  const pre = document.getElementById('bk-rollback-pre');
  if (!pre) return;
  const text = pre.textContent;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => toast('Rollback commands copied', 'ok'),
      () => toast('Copy failed', 'err')
    );
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) toast('Rollback commands copied', 'ok');
      else toast('Copy failed', 'err');
    } catch { toast('Copy failed', 'err'); }
  }
}

function _bkRenderDiffLines(diff) {
  // Collapse long equal runs — show ±3 context lines around changes
  const CONTEXT = 3;
  // Mark which equal lines are near a change
  const n = diff.length;
  const show = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (diff[i].type !== 'eq') {
      for (let k = Math.max(0, i - CONTEXT); k <= Math.min(n - 1, i + CONTEXT); k++) show[k] = 1;
    }
  }
  let html = '';
  let i = 0;
  while (i < n) {
    if (!show[i] && diff[i].type === 'eq') {
      // Count collapsed run
      let j = i;
      while (j < n && !show[j] && diff[j].type === 'eq') j++;
      const count = j - i;
      html += `<div class="bk-dl bk-dl-ctx" onclick="_bkDiffExpand(this,${i},${j})" data-start="${i}" data-end="${j}">
        <span class="bk-dl-pfx" style="width:100%;padding:0 10px;font-size:10px">▸ ${count} unchanged line${count > 1 ? 's' : ''}</span>
      </div>`;
      i = j;
    } else {
      const d = diff[i];
      const ln = d.type === 'add' ? (d.lnB ?? '') : (d.lnA ?? '');
      const pfx = d.type === 'add' ? '+' : d.type === 'del' ? '-' : ' ';
      html += `<div class="bk-dl bk-dl-${d.type}">
        <span class="bk-dl-ln">${ln}</span>
        <span class="bk-dl-pfx">${pfx}</span>
        <span class="bk-dl-txt">${esc(d.line)}</span>
      </div>`;
      i++;
    }
  }
  return html || '<div style="padding:20px;text-align:center;color:var(--text3)">No changes between these two runs.</div>';
}

function _bkDiffExpand(el, start, end) {
  // Replace the expander row with the actual lines
  const body = document.getElementById('bk-diff-body');
  if (!body) return;
  // We need the diff stored somewhere — store it on the modal
  const modal = document.getElementById('bk-diff');
  if (!modal || !modal._diffData) return;
  const diff = modal._diffData;
  let html = '';
  for (let i = start; i < end; i++) {
    const d = diff[i];
    const ln = d.lnA ?? d.lnB ?? '';
    html += `<div class="bk-dl bk-dl-eq">
      <span class="bk-dl-ln">${ln}</span>
      <span class="bk-dl-pfx"> </span>
      <span class="bk-dl-txt">${esc(d.line)}</span>
    </div>`;
  }
  el.outerHTML = html;
}

// ── ENC noise filter ──────────────────────────────────────────────
// Matches FortiGate / Cisco / Junos credential lines that re-encrypt
// on every config export — not real changes.
const _BK_ENC_RE = /^\s*(set\s+(password|passwd|psksecret|key|private-key|certificate|ssh-public-key\d*)\s+ENC\b|set\s+private-key\b|[A-Za-z0-9+/=]{40,}\s*$)/i;

function _bkToggleEncNoise() {
  const container = document.getElementById('bk-diff');
  if (!container || !container._diffData) return;
  const hide = document.getElementById('bk-enc-toggle')?.checked;
  let diff = container._diffData;
  if (hide) diff = diff.filter(d => !_BK_ENC_RE.test(d.line));

  // Re-render diff body
  const body = document.getElementById('bk-diff-body');
  if (body) body.innerHTML = _bkRenderDiffLines(diff);

  // Update summary counts
  const adds = diff.filter(d => d.type === 'add').length;
  const dels = diff.filter(d => d.type === 'del').length;
  const addsEl = document.getElementById('bk-diff-adds');
  const delsEl = document.getElementById('bk-diff-dels');
  if (addsEl) addsEl.textContent = `+${adds} added`;
  if (delsEl) delsEl.textContent = `-${dels} removed`;

  // Update rollback preview
  const vendor   = container._vendor || 'ios';
  const rollback = _bkGenerateRollback(diff, vendor);
  const pre      = document.getElementById('bk-rollback-pre');
  const summary  = document.getElementById('bk-rollback-summary');
  const details  = document.getElementById('bk-rollback-details');
  if (pre) pre.textContent = rollback.join('\n');
  if (summary) summary.textContent = `⚠ Rollback Command Preview (${rollback.cmdCount ?? rollback.length} commands)`;
  if (details) details.style.display = rollback.length ? '' : 'none';

  // Reset search state
  _bkDiffMatches = []; _bkDiffSrchIdx = 0;
  const inp = document.getElementById('bk-diff-srch-inp');
  const cnt = document.getElementById('bk-diff-srch-cnt');
  if (inp) inp.value = '';
  if (cnt) cnt.textContent = '';
}

// ── Diff search ───────────────────────────────────────────────────
let _bkDiffMatches = [];
let _bkDiffSrchIdx = 0;

function _bkDiffSearch() {
  const inp = document.getElementById('bk-diff-srch-inp');
  const cnt = document.getElementById('bk-diff-srch-cnt');
  const body = document.getElementById('bk-diff-body');
  if (!inp || !body) return;
  const q = inp.value;
  // Remove previous highlights
  body.querySelectorAll('.bk-srch-hl').forEach(m => {
    m.replaceWith(document.createTextNode(m.textContent));
  });
  _bkDiffMatches = [];
  if (q.length < 2) { if (cnt) cnt.textContent = ''; return; }
  // Highlight in txt spans
  body.querySelectorAll('.bk-dl-txt').forEach(span => {
    const text = span.textContent;
    const lower = text.toLowerCase();
    const ql = q.toLowerCase();
    let pos = 0, out = '';
    while (pos < text.length) {
      const idx = lower.indexOf(ql, pos);
      if (idx < 0) { out += esc(text.slice(pos)); break; }
      const mid = _bkDiffMatches.length;
      out += esc(text.slice(pos, idx)) + `<mark class="bk-srch-hl" id="bk-dm${mid}">${esc(text.slice(idx, idx + q.length))}</mark>`;
      _bkDiffMatches.push(mid);
      pos = idx + q.length;
    }
    span.innerHTML = out;
  });
  _bkDiffSrchIdx = 0;
  if (cnt) cnt.textContent = _bkDiffMatches.length ? `1/${_bkDiffMatches.length}` : 'no matches';
  if (_bkDiffMatches.length) {
    const el = document.getElementById('bk-dm0');
    if (el) { el.classList.add('bk-srch-hl-cur'); el.scrollIntoView({block:'nearest'}); }
  }
}

function _bkDiffSearchNav(dir) {
  if (!_bkDiffMatches.length) return;
  document.getElementById(`bk-dm${_bkDiffSrchIdx}`)?.classList.remove('bk-srch-hl-cur');
  _bkDiffSrchIdx = (_bkDiffSrchIdx + dir + _bkDiffMatches.length) % _bkDiffMatches.length;
  const cnt = document.getElementById('bk-diff-srch-cnt');
  if (cnt) cnt.textContent = `${_bkDiffSrchIdx + 1}/${_bkDiffMatches.length}`;
  const el = document.getElementById(`bk-dm${_bkDiffSrchIdx}`);
  if (el) { el.classList.add('bk-srch-hl-cur'); el.scrollIntoView({block:'nearest', behavior:'smooth'}); }
}

// ── Config Viewer Modal ───────────────────────────────────────────────
let _bkViewerHistory = [];   // [{id,ts,success}] for prev/next navigation
let _bkViewerIdx     = 0;    // current index in history

async function _bkOpenViewer(runId, did) {
  closeM('bk-viewer');
  if (did) {
    // Load full history for nav
    const hr = await fetch(`/api/backups/${did}/history`);
    const hd = await hr.json();
    _bkViewerHistory = (hd.history || []);
    _bkViewerIdx     = _bkViewerHistory.findIndex(r => r.id === runId);
    if (_bkViewerIdx < 0) _bkViewerIdx = 0;
  }
  await _bkShowRun(runId, did);
}

async function _bkShowRun(runId, did) {
  closeM('bk-viewer');
  const r = await fetch(`/api/backups/run/${runId}`);
  const d = await r.json();
  const run = d.run;
  if (!run) { toast('Run not found', 'err'); return; }
  const dev = _bkDevices.find(x => x.did === (did || run.did)) || {};

  const hasPrev = _bkViewerIdx < _bkViewerHistory.length - 1;
  const hasNext = _bkViewerIdx > 0;
  const totalRuns = _bkViewerHistory.length;

  _bkSrchRaw = run.config || '';
  _bkSrchMatches = [];
  _bkSrchIdx = 0;

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'bk-viewer';
  o.onclick = e => { if (e.target === o) closeM('bk-viewer'); };
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,900px);max-height:90vh;display:flex;flex-direction:column">
      <div class="mhd">
        <div class="mttl">📄 ${esc(dev.name || run.did)} — Config Viewer</div>
        <button class="mclose" onclick="closeM('bk-viewer')">✕</button>
      </div>
      <div style="display:flex;gap:16px;padding:10px 16px;border-bottom:1px solid var(--border);font-size:11px;color:var(--text2);flex-shrink:0">
        <span><strong>Run:</strong> ${(_bkParseTs(run.ts)?.toLocaleString() ?? run.ts)}</span>
        <span><strong>Status:</strong> ${run.success ? '<span class="bk-ok">✓ Success</span>' : '<span class="bk-fail">✗ Failed</span>'}</span>
        <span><strong>Size:</strong> ${run.size_bytes ? (run.size_bytes / 1024).toFixed(1) + ' KB' : '—'}</span>
        <span><strong>SHA256:</strong> <code style="font-size:10px">${run.sha256 ? run.sha256.slice(0, 12) + '…' : '—'}</code></span>
        ${totalRuns > 1 ? `<span style="margin-left:auto;color:var(--text3)">${_bkViewerIdx + 1}/${totalRuns}</span>` : ''}
      </div>
      ${run.config ? `
      <div class="bk-srch-bar" style="flex-shrink:0">
        <input id="bk-srch-inp" type="text" placeholder="Search in config…" oninput="_bkViewSearch()" autocomplete="off"/>
        <span id="bk-srch-cnt" class="bk-srch-cnt"></span>
        <button class="btn-sm" onclick="_bkViewSearchNav(-1)" title="Previous match">↑</button>
        <button class="btn-sm" onclick="_bkViewSearchNav(+1)" title="Next match">↓</button>
      </div>` : ''}
      <div style="flex:1;overflow:auto;padding:12px 16px">
        ${run.config
          ? `<pre class="bk-cfg-pre" id="bk-cfg-body">${esc(run.config)}</pre>`
          : `<div style="color:var(--text3);font-style:italic;padding:20px;text-align:center">${esc(run.error_msg || 'No config content')}</div>`}
      </div>
      <div class="mft">
        ${hasPrev ? `<button class="btn-s" onclick="_bkNavViewer(-1,'${esc(did||run.did)}')">← Older</button>` : '<button class="btn-s" disabled>← Older</button>'}
        ${hasNext ? `<button class="btn-s" onclick="_bkNavViewer(1,'${esc(did||run.did)}')">Newer →</button>` : '<button class="btn-s" disabled>Newer →</button>'}
        ${totalRuns > 1 ? `<button class="btn-s" onclick="closeM('bk-viewer');_bkOpenHistory('${esc(did||run.did)}')" title="View all runs and compare two">⬌ History / Diff</button>` : ''}
        <button class="btn-s" onclick="_bkCopyConfig()">📋 Copy</button>
        <button class="btn-s" onclick="closeM('bk-viewer')">Close</button>
      </div>
    </div>`;
  document.body.appendChild(o);
}

function _bkNavViewer(dir, did) {
  // dir: -1 = older, +1 = newer
  _bkViewerIdx -= dir;
  _bkViewerIdx = Math.max(0, Math.min(_bkViewerHistory.length - 1, _bkViewerIdx));
  const run = _bkViewerHistory[_bkViewerIdx];
  if (run) _bkShowRun(run.id, did);
}

// ── In-viewer search ──────────────────────────────────────────────
let _bkSrchMatches = [];
let _bkSrchIdx     = 0;
let _bkSrchRaw     = '';   // raw config text, saved at render time

function _bkViewSearch() {
  const inp = document.getElementById('bk-srch-inp');
  const cnt = document.getElementById('bk-srch-cnt');
  const pre = document.getElementById('bk-cfg-body');
  if (!inp || !pre) return;
  const q = inp.value;
  if (q.length < 2) {
    // Restore plain text
    pre.innerHTML = esc(_bkSrchRaw);
    if (cnt) cnt.textContent = '';
    _bkSrchMatches = [];
    return;
  }
  const lower = q.toLowerCase();
  const lines  = _bkSrchRaw.split('\n');
  const parts  = [];
  _bkSrchMatches = [];
  lines.forEach((line, li) => {
    let out = '';
    let pos = 0;
    const lo = line.toLowerCase();
    while (pos < line.length) {
      const idx = lo.indexOf(lower, pos);
      if (idx < 0) { out += esc(line.slice(pos)); break; }
      out += esc(line.slice(pos, idx));
      out += `<mark class="bk-srch-hl" id="bk-m${_bkSrchMatches.length}">${esc(line.slice(idx, idx + q.length))}</mark>`;
      _bkSrchMatches.push(_bkSrchMatches.length);
      pos = idx + q.length;
    }
    parts.push(out);
  });
  pre.innerHTML = parts.join('\n');
  _bkSrchIdx = 0;
  if (cnt) cnt.textContent = _bkSrchMatches.length ? `${_bkSrchIdx + 1}/${_bkSrchMatches.length}` : 'no matches';
  _bkSrchScrollTo(0);
}

function _bkViewSearchNav(dir) {
  if (!_bkSrchMatches.length) return;
  _bkSrchIdx = (_bkSrchIdx + dir + _bkSrchMatches.length) % _bkSrchMatches.length;
  const cnt = document.getElementById('bk-srch-cnt');
  if (cnt) cnt.textContent = `${_bkSrchIdx + 1}/${_bkSrchMatches.length}`;
  _bkSrchScrollTo(_bkSrchIdx);
}

function _bkSrchScrollTo(idx) {
  const el = document.getElementById(`bk-m${idx}`);
  if (el) {
    // Remove active highlight from previous
    document.querySelectorAll('.bk-srch-hl-cur').forEach(e => e.classList.remove('bk-srch-hl-cur'));
    el.classList.add('bk-srch-hl-cur');
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

function _bkCopyConfig() {
  const pre = document.querySelector('#bk-viewer pre.bk-cfg-pre');
  if (!pre) return;
  // Use the raw stored text (avoids picking up HTML mark tags from search)
  const text = _bkSrchRaw || pre.textContent;
  // navigator.clipboard requires a secure context (HTTPS / localhost).
  // Fall back to the legacy execCommand API for plain-HTTP deployments.
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      ()  => toast('Config copied to clipboard', 'ok'),
      ()  => toast('Copy failed', 'err')
    );
  } else {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0';
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) toast('Config copied to clipboard', 'ok');
      else     toast('Copy failed', 'err');
    } catch (_e) {
      toast('Copy failed', 'err');
    }
  }
}

// ── Global config search ──────────────────────────────────────────
let _bkGsrchTimer = null;

function _bkGsrchInput() {
  const inp = document.getElementById('bk-gsrch-inp');
  const clr = document.getElementById('bk-gsrch-clear');
  if (!inp) return;
  const q = inp.value.trim();
  if (clr) clr.style.display = q ? '' : 'none';
  clearTimeout(_bkGsrchTimer);
  if (q.length < 3) {
    if (!q) _bkGsrchShowTable();  // restore device table if cleared
    return;
  }
  _bkGsrchTimer = setTimeout(() => _bkGsrchRun(q), 400);
}

function _bkGsrchClear() {
  const inp = document.getElementById('bk-gsrch-inp');
  const clr = document.getElementById('bk-gsrch-clear');
  if (inp) inp.value = '';
  if (clr) clr.style.display = 'none';
  clearTimeout(_bkGsrchTimer);
  _bkGsrchShowTable();
}

function _bkGsrchShowTable() {
  _bkRenderTable(_bkDevices);
}

async function _bkGsrchRun(q) {
  const wrap = document.getElementById('bk-table-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="bk-loading">Searching…</div>';
  try {
    const r = await api('GET', `/api/backups/search?q=${encodeURIComponent(q)}`);
    if (r.error) { wrap.innerHTML = `<div class="bk-err">${esc(r.error)}</div>`; return; }
    _bkGsrchRender(r.results || [], r.query || q);
  } catch (e) {
    wrap.innerHTML = `<div class="bk-err">Search failed: ${esc(String(e))}</div>`;
  }
}

function _bkGsrchRender(results, q) {
  const wrap = document.getElementById('bk-table-wrap');
  if (!wrap) return;
  if (!results.length) {
    wrap.innerHTML = `<div class="bk-empty">No matches found for "<strong>${esc(q)}</strong>"</div>`;
    return;
  }
  // Group by device for readability
  const byDev = {};
  for (const r of results) {
    (byDev[r.did] = byDev[r.did] || {name: r.device_name, rows: []}).rows.push(r);
  }
  const ql = q.toLowerCase();
  function hlLine(text) {
    // Highlight query term in result line
    const lo = text.toLowerCase();
    let out = '', pos = 0;
    while (pos < text.length) {
      const idx = lo.indexOf(ql, pos);
      if (idx < 0) { out += esc(text.slice(pos)); break; }
      out += esc(text.slice(pos, idx)) + `<mark class="bk-srch-hl">${esc(text.slice(idx, idx + q.length))}</mark>`;
      pos = idx + q.length;
    }
    return out;
  }
  let html = `<div class="bk-gsrch-banner">${results.length} match${results.length > 1 ? 'es' : ''} for "<strong>${esc(q)}</strong>"
    <button class="btn-sm" style="margin-left:12px" onclick="_bkGsrchClear()">✕ Clear</button></div>
    <table class="bk-table">
      <thead><tr><th>Device</th><th>Backup Time</th><th>Line</th><th>Content</th><th></th></tr></thead>
      <tbody>`;
  for (const [, {name, rows}] of Object.entries(byDev)) {
    for (const r of rows) {
      const ts = _bkParseTs(r.ts)?.toLocaleString() ?? r.ts;
      html += `<tr>
        <td><strong>${esc(name || r.did)}</strong></td>
        <td style="white-space:nowrap;font-size:11px">${esc(ts)}</td>
        <td class="bk-mono" style="color:var(--text3)">${r.line_no}</td>
        <td class="bk-mono" style="font-size:10px;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
            title="${esc(r.line_text)}">${hlLine(r.line_text)}</td>
        <td><button class="btn-sm" onclick="_bkGsrchOpenRun(${r.run_id},'${esc(r.did)}',${r.line_no})">📄 Open</button></td>
      </tr>`;
    }
  }
  html += '</tbody></table>';
  wrap.innerHTML = html;
}

async function _bkGsrchOpenRun(runId, did, lineNo) {
  await _bkOpenViewer(runId, did);
  // After viewer opens, pre-fill search with current query and jump to line
  const q = document.getElementById('bk-gsrch-inp')?.value?.trim();
  if (!q) return;
  setTimeout(() => {
    const inp = document.getElementById('bk-srch-inp');
    if (inp) {
      inp.value = q;
      _bkViewSearch();
    }
  }, 100);
}
