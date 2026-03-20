// ── Events Tab Enhanced Logic ─────────────────────────────────────
// Requires: FLAPS[], S.devices (from app.js), esc() (from devices.js or app.js)

// ── Severity ──────────────────────────────────────────────────────
function evtSeverity(d) {
  // Normalise: backend sends 'direction', SSE handlers set '_direction'
  const dir = d._direction || d.direction || '';
  if (dir === 'recovered')                             return 'recovery';
  if (dir === 'down')                                  return 'critical';
  if (dir === 'threshold' && d._thr_level === 'crit') return 'critical';
  if (dir === 'threshold' && d._thr_level === 'warn') return 'warning';
  if (dir === 'trap') {
    // Use enriched severity if available
    const s = d.severity || 'warning';
    return s === 'critical' ? 'critical' : s === 'info' ? 'info' : 'warning';
  }
  return 'info';
}

const _SEV_LABEL = { critical: 'CRITICAL', warning: 'WARNING', recovery: 'RECOVERY', info: 'INFO' };

// ── Icons ─────────────────────────────────────────────────────────
const _EVT_ICONS = {
  ping: '🖥', tcp: '🔌', http: '🌐', snmp: '📡',
  dns: '🌐', tls: '🔌', http_keyword: '🌐', banner: '🔌'
};
const _VENDOR_ICONS = {
  'Fortinet': '🛡', 'Cisco': '🔵', 'Juniper': '🟠',
  'APC': '⚡', 'HPE': '🔷', 'Aruba': '🔷', 'Ubiquiti': '📶',
  'MikroTik': '🔴', 'VMware': '☁️', 'Net-SNMP': '🐧',
  'Palo Alto': '🔥', 'Generic': '📡'
};
function evtIcon(d) {
  const dir = d._direction || d.direction || '';
  if (dir === 'trap') return _VENDOR_ICONS[d.vendor] || '📡';
  if (dir === 'threshold') return '⚠️';
  return _EVT_ICONS[d.stype] || '⚠️';
}
function _trapLabel(d) {
  if (d.trap_name) return d.trap_name;
  return d.trap_oid ? d.trap_oid.split('.').slice(-4).join('.') : 'trap';
}
function _vendorBadge(d) {
  if (!d.vendor || d.vendor === 'Unknown') return '';
  return `<span class="evt-vendor-badge">${esc(d.vendor)}</span> `;
}

// ── Duration ──────────────────────────────────────────────────────
function _fmtDuration(secs) {
  if (!secs && secs !== 0) return '—';
  secs = Math.round(secs);
  if (secs < 60)   return secs + 's';
  if (secs < 3600) return Math.floor(secs/60) + 'm ' + (secs%60) + 's';
  const h = Math.floor(secs/3600);
  const m = Math.floor((secs%3600)/60);
  return h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
}

function _calcDurations(events) {
  // Walk newest-first. When a recovery is found, look for the next (older) matching down.
  const recMap = {}; // sid -> {ts, idx} of pending recovery
  for (let i = 0; i < events.length; i++) {
    const d = events[i];
    if (!d.sid) continue;
    if (d._direction === 'recovered') {
      recMap[d.sid] = { ts: new Date(d.ts).getTime(), idx: i };
    } else if (d._direction === 'down' && recMap[d.sid]) {
      const rec = recMap[d.sid];
      const secs = Math.abs((rec.ts - new Date(d.ts).getTime()) / 1000);
      events[rec.idx]._duration = secs;   // duration only on the RECOVERY row
      delete recMap[d.sid];
    }
  }
}

// ── Filter state ──────────────────────────────────────────────────
const EVT_FILTER = {
  timeRange: '24h',  // '5m'|'1h'|'24h'|'all'|'custom'
  fromTs: null,
  toTs: null,
  group: '',
  device: '',
  type: '',          // ''|'down'|'recovered'|'threshold'|'trap'
  severity: '',      // ''|'critical'|'warning'|'recovery'|'info'
  search: '',
  vendor: '',
  category: ''
};

function _applyEvtFilters() {
  // Make shallow copy so we can add _duration without mutating FLAPS
  let result = FLAPS.map(d => Object.assign({}, d));

  // Time range
  if (EVT_FILTER.timeRange !== 'all') {
    const cutoffs = { '5m': 5*60*1000, '1h': 3600*1000, '24h': 86400*1000 };
    const now = Date.now();
    if (EVT_FILTER.timeRange === 'custom') {
      if (EVT_FILTER.fromTs) {
        result = result.filter(d => {
          const t = new Date(d.ts).getTime();
          return t >= EVT_FILTER.fromTs && (!EVT_FILTER.toTs || t <= EVT_FILTER.toTs);
        });
      }
    } else {
      const ms = cutoffs[EVT_FILTER.timeRange];
      if (ms) result = result.filter(d => (now - new Date(d.ts).getTime()) < ms);
    }
  }

  // Device group
  if (EVT_FILTER.group && S && S.devices) {
    const didsInGroup = new Set(
      Object.values(S.devices)
        .filter(dev => (dev.group||'') === EVT_FILTER.group)
        .map(dev => dev.device_id)
    );
    result = result.filter(d => d.did && didsInGroup.has(d.did));
  }

  // Device name
  if (EVT_FILTER.device) {
    const q = EVT_FILTER.device.toLowerCase();
    result = result.filter(d => (d.dname||'').toLowerCase().includes(q));
  }

  // Event type
  if (EVT_FILTER.type) {
    result = result.filter(d => (d._direction || d.direction) === EVT_FILTER.type);
  }

  // Severity
  if (EVT_FILTER.severity) {
    result = result.filter(d => evtSeverity(d) === EVT_FILTER.severity);
  }

  // Vendor filter (traps only)
  if (EVT_FILTER.vendor) {
    result = result.filter(d => (d.vendor||'') === EVT_FILTER.vendor);
  }

  // Category filter (traps only)
  if (EVT_FILTER.category) {
    result = result.filter(d => (d.category||'') === EVT_FILTER.category);
  }

  // Full-text search
  if (EVT_FILTER.search.trim()) {
    const q = EVT_FILTER.search.trim().toLowerCase();
    result = result.filter(d =>
      (d.dname||'').toLowerCase().includes(q) ||
      (d.sname||'').toLowerCase().includes(q) ||
      (d.detail||'').toLowerCase().includes(q) ||
      (d.host||'').toLowerCase().includes(q) ||
      (d.src_ip||'').toLowerCase().includes(q) ||
      (d.trap_oid||'').toLowerCase().includes(q)
    );
  }

  _calcDurations(result);
  return result;
}

// ── Restore persisted filter state ────────────────────────────────
(function() {
  try {
    const saved = JSON.parse(localStorage.getItem('pw_evt_filter') || 'null');
    if (saved) Object.assign(EVT_FILTER, saved);
  } catch(_) {}
})();

// ── View mode ─────────────────────────────────────────────────────
let _evtViewMode = (()=>{ try{ return localStorage.getItem('pw_evt_view')||'card'; }catch{ return 'card'; } })();

function _setEvtViewMode(mode) {
  _evtViewMode = mode;
  try { localStorage.setItem('pw_evt_view', mode); } catch(_) {}
  document.getElementById('evtBtnCard')?.classList.toggle('active', mode==='card');
  document.getElementById('evtBtnTable')?.classList.toggle('active', mode==='table');
  _renderEvtView();
}

// ── Dropdown population ───────────────────────────────────────────
function _populateEvtGroupDropdown() {
  const sel = document.getElementById('evtFGroup');
  if (!sel) return;
  const current = EVT_FILTER.group || sel.value;
  const groups = new Set();
  if (S && S.devices) Object.values(S.devices).forEach(d => { if(d.group) groups.add(d.group); });
  const opts = ['<option value="">All Groups</option>'];
  [...groups].sort().forEach(g => {
    opts.push(`<option value="${esc(g)}"${g===current?' selected':''}>${esc(g)}</option>`);
  });
  sel.innerHTML = opts.join('');
}

function _populateEvtDeviceDropdown() {
  const sel = document.getElementById('evtFDevice');
  if (!sel) return;
  const current = EVT_FILTER.device || sel.value;

  // If a group is selected, restrict to devices in that group
  let allowed = null;
  if (EVT_FILTER.group && S && S.devices) {
    const didsInGroup = new Set(
      Object.values(S.devices)
        .filter(dev => (dev.group||'') === EVT_FILTER.group)
        .map(dev => dev.device_id)
    );
    allowed = new Set();
    FLAPS.forEach(d => { if (d.did && didsInGroup.has(d.did) && d.dname) allowed.add(d.dname); });
  }

  const names = new Set(
    FLAPS.map(d => d.dname||'').filter(n => n && (!allowed || allowed.has(n)))
  );

  // If the selected device is no longer in the filtered list, clear it
  if (current && !names.has(current)) {
    EVT_FILTER.device = '';
  }

  const opts = ['<option value="">All Devices</option>'];
  [...names].sort().forEach(n => {
    opts.push(`<option value="${esc(n)}"${n===current?' selected':''}>${esc(n)}</option>`);
  });
  sel.innerHTML = opts.join('');
}

// ── Filter change handler ─────────────────────────────────────────
function _onEvtFilterChange() {
  EVT_FILTER.timeRange = document.getElementById('evtFTime')?.value || '24h';
  // Read group first, snapshot device, repopulate (cascades group→device), then re-read device
  EVT_FILTER.group     = document.getElementById('evtFGroup')?.value || '';
  EVT_FILTER.device    = document.getElementById('evtFDevice')?.value || ''; // snapshot before rebuild
  _populateEvtDeviceDropdown(); // may auto-clear EVT_FILTER.device if it left the group
  EVT_FILTER.device    = document.getElementById('evtFDevice')?.value || ''; // final value
  EVT_FILTER.type      = document.getElementById('evtFType')?.value || '';
  EVT_FILTER.severity  = document.getElementById('evtFSev')?.value || '';
  EVT_FILTER.search    = document.getElementById('evtFSearch')?.value || '';
  EVT_FILTER.vendor    = document.getElementById('evtFVendor')?.value || '';
  EVT_FILTER.category  = document.getElementById('evtFCat')?.value || '';

  // Custom date range
  const fromEl = document.getElementById('evtFFrom');
  const toEl   = document.getElementById('evtFTo');
  if (fromEl && toEl) {
    const show = EVT_FILTER.timeRange === 'custom';
    fromEl.style.display = show ? '' : 'none';
    toEl.style.display   = show ? '' : 'none';
    EVT_FILTER.fromTs = show && fromEl.value ? new Date(fromEl.value).getTime() : null;
    EVT_FILTER.toTs   = show && toEl.value   ? new Date(toEl.value).getTime()   : null;
  }

  // Persist filter state across refreshes
  try { localStorage.setItem('pw_evt_filter', JSON.stringify(EVT_FILTER)); } catch(_) {}

  _renderEvtView();
}

function _clearEvtFilters() {
  EVT_FILTER.timeRange='24h'; EVT_FILTER.fromTs=null; EVT_FILTER.toTs=null;
  EVT_FILTER.group=''; EVT_FILTER.device=''; EVT_FILTER.type='';
  EVT_FILTER.severity=''; EVT_FILTER.search='';
  EVT_FILTER.vendor=''; EVT_FILTER.category='';
  try { localStorage.removeItem('pw_evt_filter'); } catch(_) {}
  const ids = ['evtFTime','evtFGroup','evtFDevice','evtFType','evtFSev','evtFSearch','evtFFrom','evtFTo','evtFVendor','evtFCat'];
  ids.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.tagName === 'SELECT') {
      if (id === 'evtFTime') el.value = '24h';
      else el.value = '';
    } else {
      el.value = '';
    }
    if (id === 'evtFFrom' || id === 'evtFTo') el.style.display = 'none';
  });
  _renderEvtView();
}

// ── Card builder ──────────────────────────────────────────────────
function _buildEvtCard(d) {
  const sev  = evtSeverity(d);
  const icon = evtIcon(d);
  const [date, time] = (d.ts||'').split(' ');
  const dispTime = d.ts ? (typeof fmtTs==='function' ? fmtTs(d.ts) : (d.ts.split('T')[1]||time||d.ts)) : (time||'');
  const dispDate = d.ts ? (d.ts.split('T')[0]||date||'') : (date||'');
  const isTrap   = (d._direction || d.direction) === 'trap';
  const dispName = isTrap ? esc(d.dname||d.src_ip||'Unknown') : esc(d.dname||'');
  const dispSub  = isTrap
    ? (icon + ' ' + esc(_trapLabel(d)))
    : (icon + ' ' + esc(d.sname||''));
  const dispHost = isTrap ? esc(d.src_ip||'') : esc(d.host||'');
  const durStr   = d._duration != null ? _fmtDuration(d._duration) : null;
  const unknownCls = (isTrap && !d.enriched) ? ' evt-trap-unknown' : '';

  const row = document.createElement('div');
  row.className = 'evt-row';
  row.style.cursor = 'pointer';
  row.onclick = () => _openEvtDetail(d);
  row.innerHTML =
    '<div class="evt-top">' +
      `<span class="evt-sev-badge ${sev}">${_SEV_LABEL[sev]||sev.toUpperCase()}</span>` +
      '<div class="evt-name' + unknownCls + '">' + (isTrap ? _vendorBadge(d) : '') + dispName + ' · ' + dispSub + '</div>' +
      (isTrap && d.category ? `<span class="evt-cat-badge">${esc(d.category)}</span>` : '') +
      (durStr ? `<span class="evt-dur">${durStr}</span>` : '') +
      '<div class="evt-time">' + dispTime + '</div>' +
    '</div>' +
    '<div class="evt-host">' + dispHost + '</div>' +
    '<div class="evt-detail">' + esc(d.detail||'') + '</div>' +
    '<div class="evt-time" style="padding-left:16px;font-size:12px;color:var(--text2)">' + dispDate + '</div>';
  return row;
}

// ── Table builder ─────────────────────────────────────────────────
function _buildEvtTable(events) {
  const wrap = document.createElement('div');
  wrap.style.overflowX = 'auto';
  const tbl = document.createElement('table');
  tbl.className = 'evt-table';
  tbl.innerHTML =
    '<thead><tr>' +
      '<th>Sev</th><th>Time</th><th>Device</th><th>Trap / Sensor</th>' +
      '<th>Vendor</th><th>Detail</th><th>Duration</th>' +
    '</tr></thead>';
  const tbody = document.createElement('tbody');
  events.forEach(d => {
    const sev  = evtSeverity(d);
    const icon = evtIcon(d);
    const isTrap = d._direction === 'trap';
    const [date, time] = (d.ts||'').split(' ');
    const dispTime = d.ts ? (typeof fmtTs==='function' ? fmtTs(d.ts) : (time||d.ts)) : (time||'');
    const dispDate = d.ts ? (d.ts.split('T')[0]||date||'') : (date||'');
    const dispSub  = isTrap
      ? (icon + ' ' + esc(_trapLabel(d)) + (!d.enriched ? ' <em style="opacity:.5">(unknown)</em>' : ''))
      : (icon + ' ' + esc(d.sname||''));
    const vendorCell = isTrap
      ? (d.vendor && d.vendor !== 'Unknown' ? _vendorBadge(d) + (d.category ? `<span class="evt-cat-badge">${esc(d.category)}</span>` : '') : '—')
      : '—';
    const durStr = d._duration != null ? _fmtDuration(d._duration) : '—';
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.onclick = () => _openEvtDetail(d);
    tr.innerHTML =
      `<td><span class="evt-sev-badge ${sev}">${_SEV_LABEL[sev]||sev.toUpperCase()}</span></td>` +
      `<td class="evt-td-time">${dispTime}<br><span style="color:var(--text3);font-size:10px">${dispDate}</span></td>` +
      `<td>${esc(isTrap ? (d.dname||d.src_ip||'Unknown') : (d.dname||''))}</td>` +
      `<td>${dispSub}</td>` +
      `<td>${vendorCell}</td>` +
      `<td>${esc(d.detail||'')}</td>` +
      `<td class="evt-td-dur">${durStr}</td>`;
    tbody.appendChild(tr);
  });
  tbl.appendChild(tbody);
  wrap.appendChild(tbl);
  return wrap;
}

// ── Main render ───────────────────────────────────────────────────
function _renderEvtView() {
  const list = document.getElementById('evtList');
  if (!list) return;

  _populateEvtGroupDropdown();
  _populateEvtDeviceDropdown();

  // Sync restored filter values to DOM controls (needed after page refresh)
  const _fmtLocal = ts => ts ? new Date(ts).toISOString().slice(0,16) : '';
  const _syncMap = {
    evtFTime: EVT_FILTER.timeRange, evtFGroup: EVT_FILTER.group,
    evtFDevice: EVT_FILTER.device,  evtFType: EVT_FILTER.type,
    evtFSev: EVT_FILTER.severity,   evtFSearch: EVT_FILTER.search,
    evtFFrom: _fmtLocal(EVT_FILTER.fromTs),
    evtFTo:   _fmtLocal(EVT_FILTER.toTs),
    evtFVendor: EVT_FILTER.vendor,  evtFCat: EVT_FILTER.category
  };
  Object.entries(_syncMap).forEach(([id, val]) => {
    const el = document.getElementById(id);
    if (el && el.value !== (val||'')) el.value = val || '';
  });
  const _showCustom = EVT_FILTER.timeRange === 'custom';
  ['evtFFrom','evtFTo'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = _showCustom ? '' : 'none';
  });

  const events = _applyEvtFilters();

  // Update result count
  const countEl = document.getElementById('evtCount');
  if (countEl) countEl.textContent = events.length + ' event' + (events.length===1?'':'s');

  // Update view mode buttons
  document.getElementById('evtBtnCard')?.classList.toggle('active', _evtViewMode==='card');
  document.getElementById('evtBtnTable')?.classList.toggle('active', _evtViewMode==='table');

  if (!events.length) {
    list.innerHTML = '<div class="evt-empty">No events match the current filters.</div>';
    return;
  }

  list.innerHTML = '';
  if (_evtViewMode === 'table') {
    list.appendChild(_buildEvtTable(events));
  } else {
    events.forEach(d => list.appendChild(_buildEvtCard(d)));
  }
}

// ── Detail modal ──────────────────────────────────────────────────
function _openEvtDetail(d) {
  const sev  = evtSeverity(d);
  const isTrap = d._direction === 'trap';
  const host = isTrap ? (d.src_ip||'—') : (d.host||'—');
  const sensor = isTrap
    ? (d.trap_oid ? d.trap_oid.split('.').slice(-4).join('.') : 'SNMP Trap')
    : (d.sname||'—');
  const _dir = d._direction || d.direction || '';
  const typeLabel = {
    down: 'Device / Sensor Down', recovered: 'Recovery',
    threshold: 'Threshold Alert (' + (d._thr_level||'') + ')',
    trap: 'SNMP Trap', info: 'Info'
  }[_dir] || _dir || '—';
  const durStr = d._duration != null ? _fmtDuration(d._duration) : '—';

  const set = (id, html) => { const el=document.getElementById(id); if(el) el.innerHTML=html; };
  set('evtDtlSev',    `<span class="evt-sev-badge ${sev}">${_SEV_LABEL[sev]||sev.toUpperCase()}</span>`);
  set('evtDtlTs',     esc(d.ts||'—'));
  set('evtDtlDur',    esc(durStr));
  set('evtDtlDev',    esc(isTrap ? (d.dname||d.src_ip||'Unknown') : (d.dname||'—')));
  set('evtDtlHost',   esc(host));
  set('evtDtlSensor', esc(sensor));
  set('evtDtlType',   esc(typeLabel));
  set('evtDtlMsg',    esc(d.detail||d.community||'—'));

  // ── Trap-specific enrichment rows ─────────────────────────────
  const trapExtra = document.getElementById('evtDtlTrapExtra');
  if (trapExtra) {
    if (isTrap) {
      let html = '';
      if (d.vendor && d.vendor !== 'Unknown')
        html += `<div class="evt-dtl-row"><span class="evt-dtl-label">Vendor</span><span>${_vendorBadge(d)}${esc(d.product_family||'')}</span></div>`;
      if (d.trap_name)
        html += `<div class="evt-dtl-row"><span class="evt-dtl-label">Trap Name</span><span style="font-family:monospace">${esc(d.trap_name)}</span></div>`;
      if (d.enterprise_oid)
        html += `<div class="evt-dtl-row"><span class="evt-dtl-label">Enterprise OID</span><span style="font-family:monospace;font-size:11px">${esc(d.enterprise_oid)}</span></div>`;
      if (d.category)
        html += `<div class="evt-dtl-row"><span class="evt-dtl-label">Category</span><span class="evt-cat-badge">${esc(d.category)}</span></div>`;
      if (d.probable_cause)
        html += `<div class="evt-dtl-row" style="flex-direction:column;gap:3px"><span class="evt-dtl-label">Probable Cause</span><span style="color:var(--text2);font-size:12px">${esc(d.probable_cause)}</span></div>`;
      if (d.recommended_action)
        html += `<div class="evt-dtl-row" style="flex-direction:column;gap:3px"><span class="evt-dtl-label">Action</span><span style="color:var(--text2);font-size:12px">${esc(d.recommended_action)}</span></div>`;
      const _vbSrc = (d.enriched_varbinds && d.enriched_varbinds !== '[]') ? d.enriched_varbinds : d.raw_varbinds;
      if (_vbSrc && _vbSrc !== '[]') {
        let vbHtml = '';
        try {
          const vbs = JSON.parse(_vbSrc);
          vbHtml = vbs.map(v => {
            const label = v.name
              ? `${esc(v.name)} <span class="evt-oid-hint">(${esc(v.oid)})</span>`
              : esc(v.oid);
            return `<div>${label} = ${esc(String(v.value))}</div>`;
          }).join('');
        } catch { vbHtml = esc(_vbSrc); }
        html += `<div class="evt-dtl-row" style="flex-direction:column;gap:3px">` +
          `<span class="evt-dtl-label">Varbinds</span>` +
          `<div class="evt-trap-raw-block">${vbHtml}</div></div>`;
      }
      if (!d.enriched)
        html += `<div class="evt-dtl-row"><span style="color:var(--text3);font-size:11px;font-style:italic">Unknown trap — no matching definition found</span></div>`;
      trapExtra.innerHTML = html;
      trapExtra.style.display = '';
    } else {
      trapExtra.style.display = 'none';
    }
  }

  const modal = document.getElementById('evtDetailModal');
  if (modal) { modal.style.display = 'flex'; }
}

function _closeEvtDetail() {
  const modal = document.getElementById('evtDetailModal');
  if (modal) modal.style.display = 'none';
}

// Close modal on backdrop click (ignore mousedown-inside drags)
let _evtMdown = false;
document.addEventListener('mousedown', e => {
  const modal = document.getElementById('evtDetailModal');
  if (modal && modal.style.display === 'flex') _evtMdown = (e.target === modal);
});
document.addEventListener('click', e => {
  const modal = document.getElementById('evtDetailModal');
  if (modal && e.target === modal && _evtMdown) _closeEvtDetail();
});

// ── Export ────────────────────────────────────────────────────────
function _evtExportCsv() {
  const events = _applyEvtFilters();
  const header = 'Time,Severity,Device,Host,Sensor,Type,Detail,Duration\n';
  const rows = events.map(d => {
    const sev = evtSeverity(d);
    const isTrap = d._direction === 'trap';
    return [
      d.ts||'', sev,
      isTrap ? (d.dname||d.src_ip||'') : (d.dname||''),
      isTrap ? (d.src_ip||'') : (d.host||''),
      isTrap ? (d.trap_oid||'') : (d.sname||''),
      d._direction||'',
      d.detail||'',
      d._duration != null ? _fmtDuration(d._duration) : ''
    ].map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',');
  });
  _evtDownload('pingwatch-events.csv', header + rows.join('\n'), 'text/csv');
}

function _evtExportJson() {
  const events = _applyEvtFilters();
  const out = events.map(d => {
    const o = Object.assign({}, d);
    o.severity = evtSeverity(d);
    if (o._duration != null) o.duration_fmt = _fmtDuration(o._duration);
    return o;
  });
  _evtDownload('pingwatch-events.json', JSON.stringify(out, null, 2), 'application/json');
}


function _evtDownload(filename, content, mime) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], { type: mime }));
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
