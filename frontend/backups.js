// ── Backups Tab ─────────────────────────────────────────────────────
// Manages device configuration backup listing, settings, triggering,
// and config viewing. All state is server-side; this file is pure UI.

let _bkDevices     = [];           // cached device list from /api/backups
let _bkRunning     = new Set();    // device IDs currently backing up
let _bkInited      = false;
let _bkKeepMax     = 3;            // backup_keep from settings (configs to keep per device)
let _bkGrpExpanded = { enabled: true, disabled: false }; // section collapse state

// ── Init / refresh ───────────────────────────────────────────────────
async function _bkInit() {
  _bkInited = true;
  const wrap = document.getElementById('bk-table-wrap');
  if (!wrap) return;
  wrap.innerHTML = '<div class="bk-loading">Loading…</div>';
  try {
    const [r, sr] = await Promise.all([fetch('/api/backups'), fetch('/api/settings')]);
    if (r.status === 401) { showLogin('Session expired'); return; }
    const d = await r.json();
    if (sr.ok) {
      const s = await sr.json();
      _bkKeepMax = parseInt(s.backup_keep) || 3;
    }
    _bkDevices = d.devices || [];
    _bkRenderTable(_bkDevices);
  } catch (e) {
    _bkInited = false;  // allow retry on next tab click
    wrap.innerHTML = `<div class="bk-err">Failed to load: ${esc(String(e))}</div>`;
  }
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
      const isRunning  = _bkRunning.has(dev.did);
      const eligible   = _bkIsEligible(dev);
      const scheduled  = dev.enabled && dev.in_schedule
        ? '<span class="bk-dot-on" title="In schedule">✓</span>'
        : '<span class="bk-never">—</span>';
      const timeCell   = dev.last_ts ? _bkRelTime(dev.last_ts) : '<span class="bk-never">—</span>';
      const statusCell = _bkStatusCell(dev, isRunning);
      const enabledDot = !eligible
        ? '<span class="bk-never" title="Internet monitor — not SSH-accessible">N/A</span>'
        : dev.enabled
          ? '<span class="bk-dot-on" title="Backup enabled">●</span>'
          : '<span class="bk-dot-off" title="Backup disabled">○</span>';
      const cnt = dev.run_count || 0;
      const cntCell = dev.enabled
        ? `<span class="bk-cnt ${cnt >= _bkKeepMax ? 'bk-cnt-full' : ''}" title="${cnt} saved, max ${_bkKeepMax}">${cnt}/${_bkKeepMax}</span>`
        : '<span class="bk-never">—</span>';

      const nameCell = dev.orphaned
        ? `<span class="bk-orphaned" title="Device no longer exists — backup config is stale. Open settings to delete it.">⚠ Device not found (${esc(dev.did)})</span>`
        : `<strong>${esc(dev.name || dev.did)}</strong>`;
      return `<tr onclick="_bkOpenSettings('${esc(dev.did)}')" title="Click to configure">
        <td>${enabledDot} ${nameCell}</td>
        <td class="bk-mono">${esc(dev.host || '—')}</td>
        <td style="text-align:center">${scheduled}</td>
        <td>${timeCell}</td>
        <td>${statusCell}</td>
        <td style="text-align:center">${cntCell}</td>
        <td onclick="event.stopPropagation()" style="text-align:center">
          ${dev.last_run_id
            ? `<button class="btn-sm" onclick="_bkOpenViewer(${dev.last_run_id}, '${esc(dev.did)}')" title="View latest config">📄</button>`
            : `<button class="btn-sm" disabled title="No backups yet">📄</button>`}
        </td>
        <td onclick="event.stopPropagation()" style="text-align:center">
          ${eligible
            ? `<button class="btn-sm ${isRunning ? 'bk-btn-spin' : ''}"
                  onclick="_bkTriggerRun('${esc(dev.did)}')"
                  ${isRunning ? 'disabled' : ''} title="Run backup now">
                ${isRunning ? '⟳' : '▶'}
              </button>`
            : '<span class="bk-never" title="Not SSH-accessible">—</span>'}
        </td>
      </tr>`;
    }).join('');
  }

  function buildSection(label, key, devList) {
    const expanded = _bkGrpExpanded[key] !== false;
    const arrClass = expanded ? 'bk-grp-arr open' : 'bk-grp-arr';
    const bodyAttr = expanded ? '' : ' class="bk-grp-collapsed"';
    return `
      <tbody class="bk-grp-hdr" onclick="_bkToggleGroup('${key}')">
        <tr><td colspan="8">
          <span class="${arrClass}" id="bk-arr-${key}">▶</span>
          <span class="bk-grp-title">${label}</span>
          <span class="bk-grp-cnt">${devList.length}</span>
        </td></tr>
      </tbody>
      <tbody id="bk-grp-${key}"${bodyAttr}>${buildRows(devList)}</tbody>`;
  }

  const eligible     = devices.filter(_bkIsEligible);
  const enabledDevs  = eligible.filter(d => d.in_schedule === true);
  const disabledDevs = eligible.filter(d => d.in_schedule !== true);

  wrap.innerHTML = `
    <table class="bk-table">
      <thead>
        <tr>
          <th>Device Name</th>
          <th>IP Address</th>
          <th style="text-align:center">Scheduled</th>
          <th>Last Backup</th>
          <th>Last Status</th>
          <th style="text-align:center">Saved</th>
          <th style="text-align:center">Config</th>
          <th style="text-align:center">Run</th>
        </tr>
      </thead>
      ${buildSection('Backup Enabled',  'enabled',  enabledDevs)}
      ${buildSection('Backup Disabled', 'disabled', disabledDevs)}
    </table>`;
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

  // Generate rollback commands
  const rollback = diff ? _bkGenerateRollback(diff) : [];

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
        <summary id="bk-rollback-summary">⚠ Rollback Command Preview (${rollback.length} commands)</summary>
        <div class="bk-rollback-warn">Review carefully before applying. Auto-generated from line diff only — not all commands may be valid for your platform.</div>
        <pre class="bk-cfg-pre" id="bk-rollback-pre">${esc(rollback.join('\n'))}</pre>
        <div style="padding:6px 14px 10px"><button class="btn-sm" onclick="_bkCopyRollback()">📋 Copy Commands</button></div>
      </details>` : ''}
      <div class="mft"><button class="btn-s" onclick="closeM('bk-diff')">Close</button></div>
    </div>`;
  document.body.appendChild(o);
  // Store diff for expand-collapsed-lines handler
  if (diff) document.getElementById('bk-diff')._diffData = diff;
}

// ── Rollback command generator ────────────────────────────────────
function _bkGenerateRollback(diff) {
  const cmds = [];
  for (const {type, line} of diff) {
    const t = line.trim();
    if (!t || t.startsWith('!') || t.startsWith('#')) continue;
    if (type === 'del') {
      // Line existed in old, gone in new → restore it
      cmds.push(t);
    } else if (type === 'add') {
      // Line was added in new → remove it
      cmds.push(t.startsWith('no ') ? t.slice(3) : `no ${t}`);
    }
  }
  return cmds;
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
  const rollback = _bkGenerateRollback(diff);
  const pre = document.getElementById('bk-rollback-pre');
  const summary = document.getElementById('bk-rollback-summary');
  const details = document.getElementById('bk-rollback-details');
  if (pre) pre.textContent = rollback.join('\n');
  if (summary) summary.textContent = `⚠ Rollback Command Preview (${rollback.length} commands)`;
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
