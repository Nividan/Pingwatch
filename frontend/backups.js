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
      <td>${(_bkParseTs(run.ts)?.toLocaleString() ?? run.ts)}</td>
      <td>${run.success ? '<span class="bk-ok">✓</span>' : '<span class="bk-fail">✗</span>'}</td>
      <td class="bk-mono">${run.size_bytes ? (run.size_bytes / 1024).toFixed(1) + ' KB' : '—'}</td>
      <td class="bk-mono" style="font-size:10px">${run.sha256 ? run.sha256.slice(0,8) + '…' : '—'}</td>
      <td><button class="btn-sm" onclick="closeM('bk-history');_bkOpenViewer(${run.id},'${esc(did)}')">📄 View</button></td>
    </tr>`).join('') || '<tr><td colspan="5" style="color:var(--text3);text-align:center">No backups yet</td></tr>';

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'bk-history';
  o.onclick = e => { if (e.target === o) closeM('bk-history'); };
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,560px)">
      <div class="mhd">
        <div class="mttl">📋 Backup History — ${esc(dev.name || did)}</div>
        <button class="mclose" onclick="closeM('bk-history')">✕</button>
      </div>
      <div class="mbdy" style="padding:0;overflow:auto">
        <table class="bk-table" style="margin:0">
          <thead><tr><th>Time</th><th>Status</th><th>Size</th><th>SHA256</th><th></th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
      <div class="mft"><button class="btn-s" onclick="closeM('bk-history')">Close</button></div>
    </div>`;
  document.body.appendChild(o);
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
      <div style="flex:1;overflow:auto;padding:12px 16px">
        ${run.config
          ? `<pre class="bk-cfg-pre">${esc(run.config)}</pre>`
          : `<div style="color:var(--text3);font-style:italic;padding:20px;text-align:center">${esc(run.error_msg || 'No config content')}</div>`}
      </div>
      <div class="mft">
        ${hasPrev ? `<button class="btn-s" onclick="_bkNavViewer(-1,'${esc(did||run.did)}')">← Older</button>` : '<button class="btn-s" disabled>← Older</button>'}
        ${hasNext ? `<button class="btn-s" onclick="_bkNavViewer(1,'${esc(did||run.did)}')">Newer →</button>` : '<button class="btn-s" disabled>Newer →</button>'}
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

function _bkCopyConfig() {
  const pre = document.querySelector('#bk-viewer pre.bk-cfg-pre');
  if (!pre) return;
  const text = pre.textContent;
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
