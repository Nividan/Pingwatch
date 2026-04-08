// ── Events Tab Enhanced Logic ─────────────────────────────────────
// Requires: FLAPS[], S.devices (from app.js), esc() (from devices.js or app.js)

// ── Severity ──────────────────────────────────────────────────────
function evtSeverity(d) {
  // Normalise: backend sends 'direction', SSE handlers set '_direction'
  const dir = d._direction || d.direction || '';
  if (dir === 'recovered')                             return 'recovery';
  if (dir === 'threshold_ok')                          return 'recovery';
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
  if (dir === 'threshold_ok') return '✅';
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

// ── Alert event cache (for tagging sensor event rows) ─────────────
let _alertEvtCache = [];
let _alertMap      = null;  // did::sid → [alert events]; null = not loaded yet

// ── Incident Investigation Panel state ────────────────────────────
let _evtDetailCurrent = null;
let _evtDetailTimer   = null;

function _buildAlertMap(alertEvents) {
  const map = {};
  for (const a of alertEvents) {
    if (!a.did) continue;
    const key = `${a.did}::${a.sid||''}`;
    if (!map[key]) map[key] = [];
    map[key].push(a);
  }
  return map;
}

function _matchAlertEvt(event) {
  if (!_alertMap || !event.did) return null;
  const key = `${event.did}::${event.sid||''}`;
  const candidates = _alertMap[key];
  if (!candidates || !candidates.length) return null;
  // ts is ISO UTC "2026-03-28T17:17:48Z"; triggered_at is unix seconds
  const evtSec = new Date(event.ts).getTime() / 1000;
  if (isNaN(evtSec)) return null;
  // Determine whether this sensor event is a down/threshold or recovery
  const dir = event._direction || event.direction || '';
  const isDown = dir === 'down' || dir === 'threshold';
  const isRecovered = dir === 'recovered' || dir === 'threshold_ok';
  // Alert fires after sensor event (queue delay); allow up to 5 min after, 60s before
  const WINDOW = 300;
  return candidates.find(a => {
    const lag = a.triggered_at - evtSec;
    if (lag < -60 || lag > WINDOW) return false;
    // Direction filter: only match alert event_type that aligns with sensor direction
    const et = (a.event_type || '').toLowerCase();
    if (isDown)      return et === 'down' || et === 'threshold_warning' || et === 'threshold_critical';
    if (isRecovered) return et === 'recovered' || et === 'threshold_ok';
    return true;
  }) || null;
}

async function _loadAlertCache() {
  try {
    const d = await api('GET', '/api/alert/events?state=all&limit=500');
    _alertEvtCache = d.events || [];
    _alertMap = _buildAlertMap(_alertEvtCache);
    _renderEvtView();
  } catch(_) {
    _alertMap = {};  // mark as attempted even on failure
  }
}

async function _refreshAlertCache() {
  try {
    const d = await api('GET', '/api/alert/events?state=all&limit=500');
    _alertEvtCache = d.events || [];
    _alertMap = _buildAlertMap(_alertEvtCache);
    _renderEvtView();
    const ac = await api('GET', '/api/alert/events/active');
    _alertEvtBadgeCount = (ac && ac.count) || 0;
    if (typeof _updateEvtBadge === 'function') _updateEvtBadge();
  } catch(_) {}
}

async function _evtAlertAck(id) {
  const d = await api('POST', `/api/alert/event/${id}/ack`);
  if (!d.ok) { toast(d.error || 'Failed to acknowledge', 'err'); return; }
  toast('Alert acknowledged', 'ok');
  await _refreshAlertCache();
  if (typeof _alertingLoadEvents === 'function' && _evtActiveSubTab === 'alert-history')
    _alertingLoadEvents(_alertEvtFilter ?? 'all', true);
}

async function _evtAlertResolve(id) {
  const d = await api('POST', `/api/alert/event/${id}/resolve`);
  if (!d.ok) { toast(d.error || 'Failed to resolve', 'err'); return; }
  toast('Alert resolved', 'ok');
  await _refreshAlertCache();
  if (typeof _alertingLoadEvents === 'function' && _evtActiveSubTab === 'alert-history')
    _alertingLoadEvents(_alertEvtFilter ?? 'all', true);
}

async function _evtFlapAck(flapId) {
  const d = await api('POST', `/api/flaps/${flapId}/ack`);
  if (!d.ok) { toast('Failed to acknowledge', 'err'); return; }
  toast('Acknowledged', 'ok');
  await _refreshFlapList();
  _renderEvtView();
}

async function _evtFlapResolve(flapId) {
  const d = await api('POST', `/api/flaps/${flapId}/resolve`);
  if (!d.ok) { toast('Failed to resolve', 'err'); return; }
  toast('Resolved', 'ok');
  await _refreshFlapList();
  _renderEvtView();
}

async function _evtResolveAll() {
  const alertCount = _alertEvtCache.filter(a => a.state === 'active' || a.state === 'acknowledged').length;
  const flapCount  = (typeof FLAPS !== 'undefined' ? FLAPS : []).filter(f => f.id && (f.ack_state || 'active') !== 'resolved').length;
  if (!alertCount && !flapCount) { toast('No active events to resolve', 'info'); return; }
  _pwConfirm(`Resolve all active alerts and flaps?`, async () => {
    try {
      const d = await api('POST', '/api/alert/events/resolve-all');
      if (!d.ok) { toast('Failed to resolve', 'err'); return; }
      const total = (d.alerts || 0) + (d.flaps || 0);
      toast(`Resolved ${total} event${total === 1 ? '' : 's'}`, 'ok');
      await _refreshAlertCache();
      await _refreshFlapList();
      _renderEvtView();
    } catch(e) { toast('Failed to resolve all', 'err'); }
  }, 'Resolve All');
}

// ── Events sub-tab state ──────────────────────────────────────────
let _evtActiveSubTab = (() => {
  try { return localStorage.getItem('pw_evt_subtab') || 'sensor-events'; } catch { return 'sensor-events'; }
})();

function _evtSubTab(name) {
  _evtActiveSubTab = name;
  try { localStorage.setItem('pw_evt_subtab', name); } catch(_) {}
  const panels = ['sensor-events', 'alert-history'];
  panels.forEach(p => {
    document.getElementById(`evtstab-btn-${p}`)?.classList.toggle('active', p === name);
    const el = document.getElementById(`evtstab-panel-${p}`);
    if (el) el.style.display = (p === name) ? 'flex' : 'none';
  });
  if (name === 'alert-history') {
    if (typeof _alertingLoadEvents === 'function')
      _alertingLoadEvents(_alertEvtFilter ?? 'all', true);
  }
  if (name === 'sensor-events' && _alertMap === null) {
    _loadAlertCache();
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
    const cutoffs = { '5m': 5*60*1000, '1h': 3600*1000, '24h': 86400*1000,
                      '7d': 7*86400*1000, '30d': 30*86400*1000 };
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
      if (ms != null) result = result.filter(d => {
        const t = new Date(d.ts).getTime();
        return !isNaN(t) && (now - t) < ms;
      });
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
let _evtViewMode = (()=>{ try{ return localStorage.getItem('pw_evt_view')||'table'; }catch{ return 'table'; } })();

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
  const durStr   = d.duration > 0 ? _fmtDuration(d.duration) : (d._duration != null ? _fmtDuration(d._duration) : null);
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
      '<th>Vendor</th><th>Detail</th><th>Duration</th><th>Alert</th>' +
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
    const durStr = d.duration > 0 ? _fmtDuration(d.duration) : (d._duration != null ? _fmtDuration(d._duration) : '—');
    // Build alert tag cell
    const alertEvt = _matchAlertEvt(d);
    let alertCell = '<td></td>';
    if (alertEvt) {
      const isActive = alertEvt.state === 'active';
      const svKey = {critical:'crit',warning:'warn',info:'info'}[alertEvt.severity]||'info';
      const tagCls = `aev-tag aev-tag-${svKey}${isActive?' aev-tag-active':''}`;
      const stCls  = {active:'aev-st-active',acknowledged:'aev-st-ack',resolved:'aev-st-res',suppressed:'aev-st-sup'}[alertEvt.state]||'aev-st-res';
      const stLabel= {active:'● active',acknowledged:'◐ ack',resolved:'✓ done',suppressed:'◌ sup'}[alertEvt.state]||alertEvt.state;
      const repeatBadge = alertEvt.repeat_count > 1
        ? `<span class="aev-repeat" title="Fired ${alertEvt.repeat_count} times">×${alertEvt.repeat_count}</span>`
        : '';
      const btns = isActive
        ? `<div class="aev-btns">` +
            `<button class="aev-btn-ack" onclick="event.stopPropagation();_evtAlertAck(${alertEvt.id})">✓ ACK</button>` +
            `<button class="aev-btn-res" onclick="event.stopPropagation();_evtAlertResolve(${alertEvt.id})">◉ Resolve</button>` +
          `</div>`
        : '';
      const resTag = alertEvt.state === 'resolved' ? `<span class="evt-res-tag">✓ Resolved</span>` : '';
      alertCell =
        `<td class="aev-cell">` +
          `<div class="${tagCls}">` +
            `<span class="aev-dot"></span>` +
            `<span class="aev-rule" title="${esc(alertEvt.profile_name)}">${esc(alertEvt.profile_name)}</span>` +
            `<span class="aev-st ${stCls}">${stLabel}</span>` +
            repeatBadge +
          `</div>` +
          resTag +
          btns +
        `</td>`;
    } else if (d.id) {
      const flapState = d.ack_state || 'active';
      const isActive  = flapState === 'active';
      const isAcked   = flapState === 'acknowledged';
      const stCls   = isActive ? 'aev-st-active' : isAcked ? 'aev-st-ack' : 'aev-st-res';
      const stLabel = isActive ? '● active' : isAcked ? '◐ ack' : '✓ done';
      const btns = (isActive || isAcked)
        ? `<div class="aev-btns">` +
            (isActive ? `<button class="aev-btn-ack" onclick="event.stopPropagation();_evtFlapAck(${d.id})">✓ ACK</button>` : '') +
            `<button class="aev-btn-res" onclick="event.stopPropagation();_evtFlapResolve(${d.id})">◉ Resolve</button>` +
          `</div>`
        : '';
      const resTag = flapState === 'resolved' ? `<span class="evt-res-tag">✓ Resolved</span>` : '';
      alertCell =
        `<td class="aev-cell">` +
          `<div class="aev-tag aev-tag-info${isActive?' aev-tag-active':''}">` +
            `<span class="aev-dot"></span>` +
            `<span class="aev-st ${stCls}">${stLabel}</span>` +
          `</div>` +
          resTag +
          btns +
        `</td>`;
    }
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
      `<td class="evt-td-dur">${durStr}</td>` +
      alertCell;
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
  // Kick off alert cache load on first render (fire-and-forget; re-renders when done)
  if (_alertMap === null) { _loadAlertCache(); }

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

// ── Incident Investigation Panel ──────────────────────────────────
function _openEvtDetail(d) {
  _evtDetailCurrent = d;
  const alertEvt = _matchAlertEvt(d);
  const panel = document.querySelector('#evtDetailModal .iip-panel');
  if (!panel) return;
  panel.innerHTML = _buildIIP(d, alertEvt);
  document.getElementById('evtDetailModal').style.display = 'flex';
  _startEvtDurTimer(d);
}

function _closeEvtDetail() {
  const m = document.getElementById('evtDetailModal');
  if (m) m.style.display = 'none';
  clearInterval(_evtDetailTimer);
  _evtDetailTimer = null;
  _evtDetailCurrent = null;
}

// Returns {secs, live}: secs = current duration value, live = whether it should tick
function _iipGetDuration(d) {
  const dir        = d._direction || d.direction || '';
  const isRecovery = dir === 'recovered' || dir === 'threshold_ok';
  const tsSec      = d.ts ? new Date(d.ts).getTime() / 1000 : 0;
  const dTs        = d.ts ? new Date(d.ts).getTime() : 0;

  // Recovery row: _calcDurations pre-computed down→up (only for plain 'recovered' direction)
  if (isRecovery && d._duration != null) {
    return { secs: d._duration, live: false };
  }

  if (d.did && d.sid && typeof FLAPS !== 'undefined') {
    if (isRecovery) {
      // Recovery/threshold_ok: look for the nearest DOWN/CRIT/WARN event before this ts
      // FLAPS is newest-first, so first match with ts < dTs is the closest prior down
      const down = FLAPS.find(f =>
        f.did === d.did && f.sid === d.sid &&
        (f._direction === 'down' || f._direction === 'threshold') &&
        new Date(f.ts).getTime() < dTs
      );
      if (down) {
        return { secs: Math.floor((dTs - new Date(down.ts).getTime()) / 1000), live: false };
      }
    } else {
      // Down/threshold: look for matching recovery AFTER this ts
      const rec = FLAPS.find(f =>
        f.did === d.did && f.sid === d.sid &&
        (f._direction === 'recovered' || f._direction === 'threshold_ok' ||
         f.direction  === 'recovered' || f.direction  === 'threshold_ok') &&
        new Date(f.ts).getTime() > dTs
      );
      if (rec) {
        return { secs: Math.floor((new Date(rec.ts).getTime() - dTs) / 1000), live: false };
      }
    }
  }

  // Resolved/acked with no matching event in FLAPS — use ack_at as end time
  const state = d.ack_state || 'active';
  if ((state === 'resolved' || state === 'acknowledged') && d.ack_at && tsSec) {
    return { secs: Math.max(0, Math.floor(d.ack_at - tsSec)), live: false };
  }

  // Still active with no recovery yet: live ticker from event start
  const secs = d.ts ? Math.max(0, Math.floor((Date.now() - dTs) / 1000)) : 0;
  return { secs, live: true };
}

function _startEvtDurTimer(d) {
  clearInterval(_evtDetailTimer);
  const { live } = _iipGetDuration(d);
  if (!live) return;  // static duration — no ticker needed
  _evtDetailTimer = setInterval(() => {
    const el = document.getElementById('iip-dur-live');
    if (!el) { clearInterval(_evtDetailTimer); return; }
    const sec = Math.max(0, Math.floor((Date.now() - new Date(d.ts).getTime()) / 1000));
    el.textContent = _fmtDuration(sec);
  }, 1000);
}

// Close panel on backdrop click (ignore mousedown-inside drags)
let _evtMdown = false;
document.addEventListener('mousedown', e => {
  const modal = document.getElementById('evtDetailModal');
  if (modal && modal.style.display === 'flex') _evtMdown = (e.target === modal);
});
document.addEventListener('click', e => {
  const modal = document.getElementById('evtDetailModal');
  if (modal && e.target === modal && _evtMdown) _closeEvtDetail();
});

function _buildIIP(d, alertEvt) {
  const icon   = evtIcon(d);
  const isTrap = d._direction === 'trap';
  const title  = esc((isTrap ? (d.dname||d.src_ip||'?') : (d.dname||'?')) +
                 ' / ' + (isTrap ? 'SNMP Trap' : (d.sname||'?')));
  return `
    <div class="iip-hdr">
      <span>${icon} ${title}</span>
      <button class="iip-close-btn" onclick="_closeEvtDetail()">✕</button>
    </div>
    <div class="iip-body">
      ${_iipStatus(d, alertEvt)}
      ${_iipIdentity(d)}
      ${isTrap ? _iipTrapEnrich(d) : _iipStability(d)}
      ${alertEvt ? _iipAlert(alertEvt) : ''}
      ${_iipDebug(d)}
    </div>
    <div class="iip-actions">
      <button class="iip-act-btn" onclick="_iipOpenDevice('${esc(d.did||'')}')">🖥 Open Device</button>
      ${!isTrap ? `<button class="iip-act-btn" onclick="_iipOpenHistory('${esc(d.did||'')}','${esc(d.sid||'')}')">📊 Sensor History</button>` : ''}
    </div>`;
}

function _iipStatus(d, alertEvt) {
  // Use the most-resolved state between flap-native and alert event
  const _rank    = { active: 0, acknowledged: 1, resolved: 2 };
  const flapSt   = d.ack_state || 'active';
  const alertSt  = alertEvt ? (alertEvt.state || 'active') : 'active';
  const state    = _rank[alertSt] >= _rank[flapSt] ? alertSt : flapSt;
  const isActive = state === 'active';
  const isAcked  = state === 'acknowledged';
  const isRes    = state === 'resolved';
  const badgeCls = isActive ? 'iip-st-active' : isAcked ? 'iip-st-ack' : 'iip-st-res';
  const badgeTxt = isActive ? '● Active' : isAcked ? '◐ Acknowledged' : '✓ Resolved';

  const utcStr = d.ts ? _iipFmtDt(d.ts) : '—';

  const { secs: durSecs } = _iipGetDuration(d);
  const initDur = _fmtDuration(durSecs);

  let ackmeta = '';
  if (isAcked && d.ack_by) {
    const ackTs = d.ack_at ? _iipFmtDt(new Date(d.ack_at * 1000)) : '';
    ackmeta = `<div class="iip-ack-meta">Acknowledged by <strong>${esc(d.ack_by)}</strong>${ackTs ? ' at ' + ackTs : ''}</div>`;
  }

  return `<div class="iip-section">
    <div class="iip-section-title">STATUS</div>
    <div class="iip-st-row"><span class="iip-st-badge ${badgeCls}">${badgeTxt}</span></div>
    <div class="iip-time-row"><span class="iip-mono">${esc(utcStr)}</span></div>
    <div class="iip-dur-row">Duration: <span id="iip-dur-live" class="iip-dur-live">${initDur}</span></div>
    ${ackmeta}
  </div>`;
}

function _iipIdentity(d) {
  const isTrap = d._direction === 'trap';
  const _dir   = d._direction || d.direction || '';
  const typeLabel = {
    down: 'Device / Sensor Down', recovered: 'Recovery',
    threshold: 'Threshold Alert (' + (d._thr_level||'') + ')',
    threshold_ok: 'Threshold Recovered', trap: 'SNMP Trap', info: 'Info'
  }[_dir] || _dir || '—';
  const host    = isTrap ? (d.src_ip||'—') : (d.host||'—');
  const sensor  = isTrap ? (d.trap_oid ? d.trap_oid.split('.').slice(-4).join('.') : 'SNMP Trap') : (d.sname||'—');
  const device  = isTrap ? (d.dname||d.src_ip||'Unknown') : (d.dname||'—');
  const message = d.detail || d.community || '—';
  const cp = (txt) => `<button class="iip-copy-btn" onclick="_iipCopy(${JSON.stringify(txt)})" title="Copy">📋</button>`;
  const row = (lbl, val, mono, extra='') =>
    `<div class="iip-id-row"><span class="iip-id-label">${lbl}</span><span${mono?' class="iip-mono"':''}>${val}</span>${extra}</div>`;
  return `<div class="iip-section">
    <div class="iip-section-title">IDENTITY</div>
    ${row('Device',   esc(device))}
    ${row('Sensor',   esc(sensor))}
    ${row('Host / IP', esc(host), true, host !== '—' ? cp(host) : '')}
    ${row('Type',     esc(typeLabel))}
    <div class="iip-id-row"><span class="iip-id-label">Message</span><span class="iip-msg">${esc(message)}</span>${message !== '—' ? cp(message) : ''}</div>
  </div>`;
}

function _iipStability(d) {
  if (!d.did || !d.sid) return '';
  const selfKey = _flapKey(d);
  const related = (typeof FLAPS !== 'undefined' ? FLAPS : [])
    .filter(f => f.did === d.did && f.sid === d.sid && _flapKey(f) !== selfKey)
    .sort((a, b) => new Date(b.ts) - new Date(a.ts))
    .slice(0, 7);
  if (!related.length) return '';

  const dirIcon = (f) => {
    const dir = f._direction || f.direction || '';
    if (dir === 'down' || dir === 'threshold') return `<span class="iip-tl-down">↓</span>`;
    if (dir === 'recovered' || dir === 'threshold_ok') return `<span class="iip-tl-up">↑</span>`;
    return '<span style="width:14px;display:inline-block">·</span>';
  };
  const dirLbl = (f) => {
    const dir = f._direction || f.direction || '';
    if (dir === 'threshold') return f._thr_level === 'crit' ? 'CRIT' : 'WARN';
    if (dir === 'threshold_ok') return 'THR OK';
    return (dir || '').toUpperCase().slice(0, 8);
  };
  const relTime = (ts) => {
    const sec = Math.floor((new Date(d.ts) - new Date(ts)) / 1000);
    if (Math.abs(sec) < 5) return 'same time';
    if (sec < 0)    return _fmtDuration(-sec) + ' after';
    if (sec < 60)   return sec + 's before';
    if (sec < 3600) return Math.floor(sec / 60) + 'm before';
    return Math.floor(sec / 3600) + 'h ' + Math.floor((sec % 3600) / 60) + 'm before';
  };

  const rows = related.map(f =>
    `<div class="iip-tl-row">
      ${dirIcon(f)}
      <span class="iip-tl-lbl">${dirLbl(f)}</span>
      <span class="iip-tl-ts iip-mono">${esc(_iipFmtDt(f.ts))}</span>
      <span class="iip-tl-rel">${relTime(f.ts)}</span>
    </div>`).join('');

  return `<div class="iip-section">
    <div class="iip-section-title">STABILITY</div>
    ${rows}
  </div>`;
}

function _iipTrapEnrich(d) {
  let html = '';
  if (d.vendor && d.vendor !== 'Unknown')
    html += `<div class="iip-id-row"><span class="iip-id-label">Vendor</span><span>${_vendorBadge(d)}${esc(d.product_family||'')}</span></div>`;
  if (d.trap_name)
    html += `<div class="iip-id-row"><span class="iip-id-label">Trap Name</span><span class="iip-mono">${esc(d.trap_name)}</span></div>`;
  if (d.enterprise_oid)
    html += `<div class="iip-id-row"><span class="iip-id-label">Enterprise OID</span><span class="iip-mono" style="font-size:10px">${esc(d.enterprise_oid)}</span></div>`;
  if (d.category)
    html += `<div class="iip-id-row"><span class="iip-id-label">Category</span><span class="evt-cat-badge">${esc(d.category)}</span></div>`;
  if (d.probable_cause)
    html += `<div class="iip-id-row" style="flex-direction:column;gap:3px"><span class="iip-id-label">Probable Cause</span><span style="color:var(--text2);font-size:12px">${esc(d.probable_cause)}</span></div>`;
  if (d.recommended_action)
    html += `<div class="iip-id-row" style="flex-direction:column;gap:3px"><span class="iip-id-label">Action</span><span style="color:var(--text2);font-size:12px">${esc(d.recommended_action)}</span></div>`;
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
    html += `<div class="iip-id-row" style="flex-direction:column;gap:3px">
      <span class="iip-id-label">Varbinds</span>
      <div class="evt-trap-raw-block">${vbHtml}</div></div>`;
  }
  if (!d.enriched)
    html += `<div class="iip-id-row"><span style="color:var(--text3);font-size:11px;font-style:italic">Unknown trap — no matching definition found</span></div>`;
  if (!html) return '';
  return `<div class="iip-section">
    <div class="iip-section-title">TRAP DETAILS</div>
    ${html}
  </div>`;
}

function _iipAlert(alertEvt) {
  const isActive = alertEvt.state === 'active';
  const isRes    = alertEvt.state === 'resolved';
  const stCls    = {active:'aev-st-active', acknowledged:'aev-st-ack', resolved:'aev-st-res'}[alertEvt.state] || '';
  const stLbl    = {active:'● Active', acknowledged:'◐ Acknowledged', resolved:'✓ Resolved'}[alertEvt.state] || alertEvt.state;
  const firedTs  = alertEvt.triggered_at ? _iipFmtDt(new Date(alertEvt.triggered_at * 1000)) : '—';
  const repeat   = (alertEvt.repeat_count || 1) > 1 ? `<span class="iip-repeat">×${alertEvt.repeat_count}</span>` : '';
  const btns = !isRes
    ? `<div class="iip-btns">` +
        (isActive ? `<button class="aev-btn-ack" onclick="_iipAlertAck(${alertEvt.id})">✓ ACK Alert</button>` : '') +
        `<button class="aev-btn-res" onclick="_iipAlertResolve(${alertEvt.id})">◉ Resolve Alert</button>` +
      `</div>` : '';
  return `<div class="iip-section">
    <div class="iip-section-title">ALERT RULE</div>
    <div class="iip-id-row">
      <span class="iip-id-label">Rule</span>
      <span>${esc(alertEvt.rule_name)}</span>
      <span class="aev-st ${stCls}" style="margin-left:6px">${stLbl}</span>
    </div>
    <div class="iip-id-row">
      <span class="iip-id-label">Fired</span>
      <span class="iip-mono">${esc(firedTs)}</span>${repeat}
    </div>
    ${btns}
  </div>`;
}

function _iipDebug(d) {
  const txt = d.detail || d.community || '';
  if (!txt) return '';
  return `<details class="iip-debug">
    <summary class="iip-debug-summary">▶ Debug / Raw Data</summary>
    <div class="evt-trap-raw-block" style="margin-top:8px;white-space:pre-wrap">${esc(txt)}</div>
  </details>`;
}

// Local-timezone datetime formatter (matches fmtTs in sensors.js)
function _iipFmtDt(ts) {
  try {
    const dt = new Date(ts);
    const p  = n => String(n).padStart(2, '0');
    return `${dt.getFullYear()}-${p(dt.getMonth()+1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}:${p(dt.getSeconds())}`;
  } catch { return ts || '—'; }
}

function _iipCopy(txt) {
  navigator.clipboard.writeText(txt).then(() => toast('Copied', 'ok')).catch(() => {});
}

async function _iipFlapAck(id) {
  await _evtFlapAck(id);
  if (_evtDetailCurrent) _openEvtDetail(_evtDetailCurrent);
}
async function _iipFlapResolve(id) {
  await _evtFlapResolve(id);
  if (_evtDetailCurrent) _openEvtDetail(_evtDetailCurrent);
}
async function _iipAlertAck(id) {
  await _evtAlertAck(id);
  if (_evtDetailCurrent) _openEvtDetail(_evtDetailCurrent);
}
async function _iipAlertResolve(id) {
  await _evtAlertResolve(id);
  if (_evtDetailCurrent) _openEvtDetail(_evtDetailCurrent);
}

function _iipOpenDevice(did) {
  _closeEvtDetail();
  switchMainTab('devices');
  if (did && typeof openDevWin === 'function') openDevWin(did);
}
function _iipOpenHistory(did, sid) {
  _closeEvtDetail();
  switchMainTab('devices');
  if (did && sid && typeof openDetail === 'function') openDetail(did, sid, 'history');
}

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
