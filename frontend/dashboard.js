// ── Availability fetch — shared promise prevents request storms ───
let _availFetchPromise = null;
function _fetchAvailability() {
  if (_availFetchPromise) return _availFetchPromise;
  _availFetchPromise = fetch('/api/availability?minutes=1440')
    .then(r => r.json())
    .catch(() => ({ availability: [] }))
    .finally(() => { _availFetchPromise = null; });
  return _availFetchPromise;
}

// ── Widget content swap (skip if unchanged, instant write otherwise) ─
function _dwSwap(body, html) {
  if (!body) return;
  if (body.innerHTML === html) return;
  body.innerHTML = html;
}

// ── Dashboard widget system ───────────────────────────────────────
// Widget registry — add entries here to support new widget types.
// Icons use icon() from icons.js (loaded earlier in inline-script chain).
const _DW_REG = {
  sensor_chart: {
    label: 'Sensor History Chart',
    icon:  icon('activity', 14),
    cat: 'charts',
    desc: "Time-series of any sensor's last 24h / 7d / 30d.",
    meta: ['any sensor', 'live'],
    popular: true,
    defaultCols: 2,
    fields: [
      { key: 'did', label: 'Device', type: 'device-select' },
      { key: 'sid', label: 'Sensor', type: 'sensor-select' },
    ],
    render:  (wid, cfg) => _dwRenderSensorChart(wid, cfg),
    refresh: (wid, cfg) => _dwLoadSensorChart(wid, cfg.did, cfg.sid, _dwTimeRangeMinutes()),
  },
  device_status: {
    label: 'Device Status',
    icon:  icon('devices', 14),
    cat: 'status',
    desc: 'Live list of selected devices with ping latency + state.',
    meta: ['any device', 'live'],
    defaultCols: 1,
    fields: [],
    render:  (wid, cfg) => _dwRenderDeviceStatus(wid),
    refresh: (wid, _cfg) => _dwRefreshDeviceStatus(wid),
  },
  network_avail: {
    label: 'Network Availability History (24h)',
    icon:  icon('shield', 14),
    cat: 'charts',
    desc: '24h availability bar — per-device uptime over time.',
    meta: ['all devices', '24h'],
    popular: true,
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRenderNetAvail(wid),
    refresh: (wid, _cfg) => _dwRefreshNetAvail(wid),
  },
  sensor_gauge: {
    label: 'Sensor Gauge',
    icon:  icon('cpu', 14),
    cat: 'charts',
    desc: 'Real-time dial for a single sensor with thresholds.',
    meta: ['any sensor', 'live'],
    defaultCols: 1,
    fields: [
      { key: 'did', label: 'Device', type: 'device-select' },
      { key: 'sid', label: 'Sensor', type: 'sensor-select' },
    ],
    render:  (wid, cfg) => _dwRefreshGauge(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshGauge(wid, cfg),
  },
  flap_events: {
    label: 'Recent Flap Events',
    icon:  icon('events', 14),
    cat: 'events',
    desc: 'Stream of devices that flapped state recently.',
    meta: ['live'],
    popular: true,
    defaultCols: 2,
    fields: [
      { key: 'limit', label: 'Max events', type: 'select',
        options: [{v:10,l:'10'},{v:25,l:'25'},{v:50,l:'50'}], def: 25 },
    ],
    render:  (wid, cfg) => _dwRefreshFlapEvents(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshFlapEvents(wid, cfg),
  },
  system_status: {
    label: 'System Status',
    icon:  icon('settings', 14),
    cat: 'status',
    desc: 'Overall PingWatch health — sample lag, queue depth, workers.',
    meta: ['self-monitor'],
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRenderSystemStatus(wid),
    refresh: (wid, _cfg) => _dwRenderSystemStatus(wid),
  },
  server_perf: {
    label: 'Server Performance',
    icon:  icon('cpu', 14),
    cat: 'charts',
    desc: 'CPU / RAM / disk / network bars for a single host.',
    meta: ['SNMP required', 'any host'],
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRenderServerPerf(wid),
    refresh: (wid, _cfg) => _dwFetchServerPerf(wid),
  },
  down_devices: {
    label: 'Down & Warning Devices',
    icon:  icon('alerts', 14),
    cat: 'events',
    desc: 'Live list of every device currently in a non-OK state.',
    meta: ['live'],
    popular: true,
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshDownDevices(wid),
    refresh: (wid, _cfg) => _dwRefreshDownDevices(wid),
  },
  active_incidents: {
    label: 'Active Incidents (Root Cause)',
    icon:  icon('alerts', 14),
    cat: 'events',
    desc: 'Correlated outages — the upstream root device behind each cluster of downs.',
    meta: ['live', 'RCA'],
    popular: true,
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshIncidents(wid),
    refresh: (wid, _cfg) => _dwRefreshIncidents(wid),
  },
  top_latency: {
    label: 'Slowest Ping Devices',
    icon:  icon('activity', 14),
    cat: 'events',
    desc: 'Top-N hosts by current ping latency.',
    meta: ['top-N', 'live'],
    defaultCols: 1,
    fields: [
      { key: 'limit', label: 'Show top', type: 'select',
        options: [{v:5,l:'5 devices'},{v:10,l:'10 devices'},{v:15,l:'15 devices'}], def: 10 },
    ],
    render:  (wid, cfg) => _dwRefreshTopLatency(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshTopLatency(wid, cfg),
  },
  event_count: {
    label: 'Event Summary',
    icon:  icon('reports', 14),
    cat: 'reports',
    desc: 'Aggregate event count by type and severity.',
    meta: ['last 24h'],
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshEventCount(wid),
    refresh: (wid, _cfg) => _dwRefreshEventCount(wid),
  },
  packet_loss: {
    label: 'Packet Loss',
    icon:  icon('activity', 14),
    cat: 'charts',
    desc: 'Per-device packet loss over time.',
    meta: ['any device'],
    defaultCols: 1,
    fields: [
      { key: 'limit',     label: 'Max sensors', type: 'select',
        options: [{v:5,l:'5'},{v:10,l:'10'},{v:20,l:'20'}], def: 10 },
      { key: 'threshold', label: 'Min loss',    type: 'select',
        options: [{v:1,l:'≥1%'},{v:5,l:'≥5%'},{v:10,l:'≥10%'}], def: 1 },
    ],
    render:  (wid, cfg) => _dwRefreshPacketLoss(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshPacketLoss(wid, cfg),
  },
  sla_report: {
    label: 'SLA Report',
    icon:  icon('check', 14),
    cat: 'reports',
    desc: '% uptime per service against your SLA target.',
    meta: ['30d', 'exportable'],
    defaultCols: 1,
    fields: [
      { key: 'did', label: 'Device', type: 'device-select' },
      { key: 'sid', label: 'Sensor', type: 'sensor-select' },
    ],
    render:  (wid, cfg) => _dwRenderSLA(wid, cfg),
    refresh: (wid, cfg) => _dwFetchSLA(wid, cfg),
  },
  flap_detect: {
    label: 'Flapping Devices',
    icon:  icon('refresh', 14),
    cat: 'events',
    desc: 'Devices with high flap counts in the window.',
    meta: ['top-N', '24h'],
    defaultCols: 1,
    fields: [
      { key: 'min_flaps', label: 'Min flaps', type: 'select',
        options: [{v:3,l:'3 events'},{v:5,l:'5 events'},{v:10,l:'10 events'}], def: 3 },
    ],
    render:  (wid, cfg) => _dwRefreshFlapDetect(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshFlapDetect(wid, cfg),
  },
  internet_health: {
    label: 'Internet Health',
    icon:  icon('map', 14),
    cat: 'network',
    desc: 'External reachability checks (DNS, HTTP, well-known hosts).',
    meta: ['live'],
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshInternetHealth(wid),
    refresh: (wid, _cfg) => _dwRefreshInternetHealth(wid),
  },
  ncm_status: {
    label: 'Backup Status',
    icon:  icon('backups', 14),
    cat: 'status',
    desc: 'Last backup result + size per device.',
    meta: ['nightly'],
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwNcmStatusRefresh(wid),
    refresh: (wid, _cfg) => _dwNcmStatusRefresh(wid),
  },
  license_overview: {
    label: 'License Overview',
    icon:  icon('reports', 14),
    cat: 'reports',
    desc: 'Seats used, expiry, feature flags.',
    meta: ['admin'],
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwLicenseOverviewRefresh(wid),
    refresh: (wid, _cfg) => _dwLicenseOverviewRefresh(wid),
  },
  fleet_status: {
    label: 'Fleet Status',
    icon:  icon('check', 14),
    cat: 'status',
    desc: 'All-devices health ring — up/warn/down summary.',
    meta: ['live'],
    popular: true,
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshFleetStatus(wid),
    refresh: (wid, _cfg) => _dwRefreshFleetStatus(wid),
  },
  latency_heatmap: {
    label: 'Latency Heatmap',
    icon:  icon('activity', 14),
    cat: 'charts',
    desc: 'Heatmap of ping latencies across many devices.',
    meta: ['top-100', 'live'],
    isNew: true,
    defaultCols: 2,
    fields: [
      { key: 'limit', label: 'Devices', type: 'select',
        options: [{v:10,l:'10'},{v:20,l:'20'},{v:30,l:'30'},{v:50,l:'50'},{v:100,l:'100'}], def: 20 },
      { key: 'group', label: 'Group', type: 'group-select', def: '' },
    ],
    render:  (wid, cfg) => _dwRefreshLatencyHeatmap(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshLatencyHeatmap(wid, cfg),
  },
};

// ── Category palette + section order ──────────────────────────────
const _DW_CATS = {
  charts:  { name: 'Charts',  c: 'var(--accent)'  },   // cyan
  status:  { name: 'Status',  c: 'var(--accent2)' },   // green
  events:  { name: 'Events',  c: 'var(--warn)'    },   // orange
  reports: { name: 'Reports', c: 'var(--purple)'  },   // purple
  network: { name: 'Network', c: 'var(--gold)'    },   // gold
};
const _DW_CAT_ORDER = ['charts', 'status', 'events', 'reports', 'network'];

// ── Persistence (server-side, per user, multi-dashboard) ─────────
let _dwDashboards = null;   // [{id, name, sort_order}] metadata
let _dwActiveId   = null;   // currently displayed dashboard id
let _dwWidgets    = null;   // widget array for active dashboard

function _dwLoad() {
  return _dwWidgets || [];
}
function _dwSave(widgets) {
  _dwWidgets = widgets;
  if (!_dwActiveId) return;
  fetch(`/api/dashboards/${_dwActiveId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ widgets }),
  }).catch(() => {});   // fire-and-forget; errors are silent
}
async function _dwInit() {
  // Clear any stale localStorage data left from the old storage scheme
  try { localStorage.removeItem('pw-dashboard'); } catch {}

  // Already initialized — just refresh content + restart tick
  if (_dwDashboards !== null && _dwWidgets !== null) {
    _dwLoad().forEach(w => { const r = _DW_REG[w.type]; if (r) r.refresh(w.id, w.cfg); });
    _dwStartTick();
    return;
  }

  // Phase 1: fetch dashboard list
  try {
    const r = await fetch('/api/dashboards');
    if (r.ok) {
      const data = await r.json();
      _dwDashboards = Array.isArray(data.dashboards) ? data.dashboards : [];
    } else {
      _dwDashboards = [];
    }
  } catch {
    _dwDashboards = [];
  }

  _dwRenderTabBar();

  // Phase 2: switch to last-active or first dashboard
  const savedId = parseInt(_lsGet('pw-dw-active') || '0', 10);
  const target = _dwDashboards.find(d => d.id === savedId) || _dwDashboards[0];
  if (target) {
    await _dwSwitchTo(target.id);
  } else {
    _dwWidgets = [];
    _dwRenderAll();
  }
}

// ── Global time range (affects opt-in widgets) ──────────────────
// Persist as 'pw_dw_range'. Widgets that want to follow it read
// window._dwTimeRange (string) or _dwTimeRangeMinutes() (number).
const _DW_RANGES = ['5m', '1h', '24h', '7d'];
function _dwTimeRangeMinutes() {
  switch (window._dwTimeRange) {
    case '5m':  return 5;
    case '1h':  return 60;
    case '24h': return 1440;
    case '7d':  return 10080;
    default:    return 60;
  }
}
function _dwSetTimeRange(r) {
  if (!_DW_RANGES.includes(r)) return;
  window._dwTimeRange = r;
  try { localStorage.setItem('pw_dw_range', r); } catch (_) {}
  // Update segmented control's active state
  document.querySelectorAll('#dw-range-seg button').forEach(b => {
    b.classList.toggle('active', b.dataset.range === r);
  });
  // Time-aware widgets re-render fully so their internal toolbars / labels reflect
  // the new period. Stateless widgets (gauges, status pills) re-refresh idempotently.
  _dwLoad().forEach(w => {
    const reg = _DW_REG[w.type];
    if (!reg) return;
    if (reg.render) reg.render(w.id, w.cfg);
    else            reg.refresh(w.id, w.cfg);
  });
}
// Bootstrap from localStorage (or default to 24h)
(function () {
  let r;
  try { r = localStorage.getItem('pw_dw_range'); } catch (_) {}
  window._dwTimeRange = _DW_RANGES.includes(r) ? r : '24h';
})();

// ── Tab bar ──────────────────────────────────────────────────────
function _dwRenderTabBar() {
  const bar = document.getElementById('dw-tab-bar');
  if (!bar || !_dwDashboards) return;
  const tabs = _dwDashboards.map(d =>
    `<button class="dw-dash-tab${d.id === _dwActiveId ? ' active' : ''}"
             data-id="${d.id}"
             onclick="_dwSwitchTo(${d.id})"
             oncontextmenu="_dwTabCtxMenu(event,${d.id})"
             title="${esc(d.name)}">${esc(d.name)}</button>`
  ).join('') +
  `<button class="dw-dash-tab dw-dash-add" onclick="_dwCreateDashboard()" title="New dashboard">${icon('plus',12)}</button>`;
  const rangeBtns = _DW_RANGES.map(r =>
    `<button data-range="${r}" class="${window._dwTimeRange === r ? 'active' : ''}"
             onclick="_dwSetTimeRange('${r}')">${r}</button>`
  ).join('');
  bar.innerHTML =
    `<div class="dw-tab-bar-tabs">${tabs}</div>` +
    `<div class="seg" id="dw-range-seg" title="Default time window for widgets that follow it">${rangeBtns}</div>` +
    `<button class="dw-add-btn" onclick="_dwOpenPicker()" title="Add a widget to this dashboard">${icon('plus',13)} Add Widget</button>`;
}

async function _dwSwitchTo(id) {
  _dwActiveId = id;
  _lsSet('pw-dw-active', id);

  // Stop current tick while loading
  if (_dwTickTimer) { clearInterval(_dwTickTimer); _dwTickTimer = null; }

  try {
    const r = await fetch(`/api/dashboards/${id}`);
    if (r.ok) {
      const data = await r.json();
      _dwWidgets = Array.isArray(data.widgets) ? data.widgets : [];
    } else {
      _dwWidgets = [];
    }
  } catch {
    _dwWidgets = [];
  }

  _dwRenderTabBar();
  _dwRenderAll();
}

// ── Dashboard name modal (shared by create + rename) ─────────────
function _dwOpenNameModal(title, value, btnLabel, onSubmit) {
  closeM('dw-name-modal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'dw-name-modal';
  _overlayClose(o, () => closeM('dw-name-modal'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,360px)">
      <div class="mhd">
        <div class="mttl">${title}</div>
        <button class="mclose" onclick="closeM('dw-name-modal')">✕</button>
      </div>
      <div class="mbdy" style="gap:12px">
        <div class="fr">
          <label class="fl">Dashboard Name</label>
          <input type="text" id="dw-name-inp" value="${esc(value)}" maxlength="50"
                 placeholder="e.g. NOC Overview" autocomplete="off"/>
        </div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('dw-name-modal')">Cancel</button>
        <button class="btn-p" id="dw-name-ok">${btnLabel}</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  const inp = document.getElementById('dw-name-inp');
  const btn = document.getElementById('dw-name-ok');
  const submit = () => {
    const v = inp.value.trim();
    if (!v) { toast('Name is required', 'warn'); return; }
    closeM('dw-name-modal');
    onSubmit(v);
  };
  btn.addEventListener('click', submit);
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  setTimeout(() => { inp.focus(); inp.select(); }, 50);
}

// ── Dashboard CRUD ───────────────────────────────────────────────
function _dwCreateDashboard() {
  _dwOpenNameModal('New Dashboard', '', 'Create', async (name) => {
    try {
      const r = await fetch('/api/dashboards', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (r.ok) {
        const d = await r.json();
        _dwDashboards.push({ id: d.id, name: d.name, sort_order: _dwDashboards.length });
        _dwRenderTabBar();
        await _dwSwitchTo(d.id);
      } else {
        const err = await r.json().catch(() => ({}));
        toast(err.error || 'Failed to create dashboard', 'err');
      }
    } catch { toast('Failed to create dashboard', 'err'); }
  });
}

function _dwRenameDashboard(id) {
  const dash = _dwDashboards.find(d => d.id === id);
  if (!dash) return;
  _dwOpenNameModal('Rename Dashboard', dash.name, 'Rename', async (name) => {
    if (name === dash.name) return;
    try {
      const r = await fetch(`/api/dashboards/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (r.ok) {
        dash.name = name;
        _dwRenderTabBar();
        toast('Dashboard renamed', 'ok');
      } else {
        const err = await r.json().catch(() => ({}));
        toast(err.error || 'Failed to rename', 'err');
      }
    } catch { toast('Failed to rename', 'err'); }
  });
}

function _dwDeleteDashboard(id) {
  if (_dwDashboards.length <= 1) { toast('Cannot delete the last dashboard', 'warn'); return; }
  const dash = _dwDashboards.find(d => d.id === id);
  if (!dash) return;
  closeM('dw-del-modal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'dw-del-modal';
  _overlayClose(o, () => closeM('dw-del-modal'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,380px)">
      <div class="mhd">
        <div class="mttl" style="color:var(--down)">Delete Dashboard</div>
        <button class="mclose" onclick="closeM('dw-del-modal')">✕</button>
      </div>
      <div class="mbdy">
        <p style="margin:0;font-size:13px;color:var(--text2)">
          Delete <strong style="color:var(--text)">${esc(dash.name)}</strong>?<br/>
          <span style="color:var(--text3);font-size:11px">All widgets on this dashboard will be removed.</span>
        </p>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('dw-del-modal')">Cancel</button>
        <button class="btn-p" style="background:var(--down)" id="dw-del-ok">Delete</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  document.getElementById('dw-del-ok').addEventListener('click', async () => {
    closeM('dw-del-modal');
    try {
      const r = await fetch(`/api/dashboards/${id}`, { method: 'DELETE' });
      if (r.ok) {
        _dwDashboards = _dwDashboards.filter(d => d.id !== id);
        _dwRenderTabBar();
        if (_dwActiveId === id) {
          const next = _dwDashboards[0];
          if (next) await _dwSwitchTo(next.id);
        }
        toast('Dashboard deleted', 'ok');
      } else {
        const err = await r.json().catch(() => ({}));
        toast(err.error || 'Failed to delete', 'err');
      }
    } catch { toast('Failed to delete', 'err'); }
  });
}

// ── Tab context menu (right-click) ──────────────────────────────
function _dwTabCtxMenu(e, id) {
  e.preventDefault();
  document.getElementById('dw-tab-ctx')?.remove();
  const dash = _dwDashboards.find(d => d.id === id);
  if (!dash) return;
  const isLast = _dwDashboards.length <= 1;
  const menu = document.createElement('div');
  menu.id = 'dw-tab-ctx';
  menu.className = 'dw-tab-ctx';
  menu.style.left = e.pageX + 'px';
  menu.style.top  = e.pageY + 'px';
  menu.innerHTML =
    `<div class="dw-ctx-item" onclick="_dwRenameDashboard(${id})">✎ Rename</div>` +
    `<div class="dw-ctx-item dw-ctx-danger${isLast ? ' disabled' : ''}"
          onclick="${isLast ? '' : `_dwDeleteDashboard(${id})`}">✕ Delete</div>`;
  document.body.appendChild(menu);
  setTimeout(() => document.addEventListener('click', function _rm() {
    menu.remove(); document.removeEventListener('click', _rm);
  }), 0);
}

// ── Dashboard-wide tick (15 s) ────────────────────────────────────
let _dwTickTimer = null;
function _dwStartTick() {
  if (_dwTickTimer) return;
  _dwTickTimer = setInterval(() => {
    if (activeMainTab !== 'dashboard') { clearInterval(_dwTickTimer); _dwTickTimer = null; return; }
    _dwLoad().forEach(w => { const r = _DW_REG[w.type]; if (r) r.refresh(w.id, w.cfg); });
  }, 10000);
}

// ── Grid render (gridstack.js) ───────────────────────────────────
// _dwGrid holds the active GridStack instance; destroyed and recreated
// on every full render (switching dashboards, adding/removing widgets).
let _dwGrid = null;
let _dwSaveTimer = null;

// Widgets created before the gridstack migration have no x/y/w/h fields.
// Assign sequential positions based on their cols hint so the upgrade is
// visually similar to the pre-migration layout. Returns true if anything
// was filled in (caller should persist).
function _dwEnsurePositions(widgets) {
  let changed = false;
  let x = 0, y = 0, rowH = 4;
  for (const w of widgets) {
    if (w.x !== undefined && w.y !== undefined && w.w !== undefined && w.h !== undefined) continue;
    const gw = w.w ?? (w.cols === 2 ? 6 : 3);
    const gh = w.h ?? 4;
    if (x + gw > 12) { x = 0; y += rowH; rowH = gh; }
    w.x = x; w.y = y; w.w = gw; w.h = gh;
    x += gw; rowH = Math.max(rowH, gh);
    changed = true;
  }
  return changed;
}

// Read positions back from gridstack and persist (debounced).
function _dwSaveGridPositions() {
  if (_dwSaveTimer) clearTimeout(_dwSaveTimer);
  _dwSaveTimer = setTimeout(() => {
    const grid = document.getElementById('dw-grid');
    if (!grid) return;
    const widgets = _dwLoad();
    const byId = new Map(widgets.map(w => [w.id, w]));
    grid.querySelectorAll('.grid-stack-item').forEach(el => {
      const id = el.getAttribute('gs-id');
      const w  = byId.get(id);
      if (!w) return;
      w.x = parseInt(el.getAttribute('gs-x'), 10) || 0;
      w.y = parseInt(el.getAttribute('gs-y'), 10) || 0;
      w.w = parseInt(el.getAttribute('gs-w'), 10) || w.w || 3;
      w.h = parseInt(el.getAttribute('gs-h'), 10) || w.h || 4;
    });
    widgets.sort((a, b) => (a.y - b.y) || (a.x - b.x));
    _dwSave(widgets);
  }, 400);
}

function _dwRenderAll() {
  const grid = document.getElementById('dw-grid');
  if (!grid) return;
  // Tear down previous gridstack instance (keeps DOM; we replace it below)
  if (_dwGrid) {
    try { _dwGrid.destroy(false); } catch {}
    _dwGrid = null;
  }
  // Clear any per-card intervals from previous render
  grid.querySelectorAll('.dw-card').forEach(c => { if (c._interval) { clearInterval(c._interval); c._interval = null; } });
  const widgets = _dwLoad();
  if (!widgets.length) {
    grid.innerHTML = '<div class="dw-empty">No widgets yet. Click <strong>+ Add Widget</strong> to get started.</div>';
    grid.classList.remove('grid-stack');
    return;
  }
  // One-shot migration for dashboards stored before gridstack
  if (_dwEnsurePositions(widgets)) _dwSave(widgets);
  grid.classList.add('grid-stack');
  grid.innerHTML = widgets.map(w => {
    const hasPos = (w.x !== undefined && w.y !== undefined);
    const gw = w.w ?? (w.cols === 2 ? 6 : 3);
    const gh = w.h ?? 4;
    const posAttrs = hasPos
      ? `gs-x="${w.x}" gs-y="${w.y}"`
      : `gs-auto-position="true"`;
    return `
      <div class="grid-stack-item" gs-id="${esc(w.id)}" ${posAttrs} gs-w="${gw}" gs-h="${gh}">
        <div class="grid-stack-item-content">
          <div class="dw-card widget" id="dw-${w.id}" data-wid="${w.id}">
            <div class="dw-hdr widget-head">
              <span class="dw-icon">${(_DW_REG[w.type]||{}).icon||icon('cpu',14)}</span>
              <span class="dw-title">${esc(w.title)}</span>
              <button class="dw-edit rbac-op" onclick="_dwOpenEdit('${w.id}')" title="Edit widget">${icon('settings',13)}</button>
              <button class="dw-exp"          onclick="_dwOpenFullscreen('${w.id}')" title="Expand widget">${icon('expand',13)}</button>
              <button class="dw-rm"           onclick="_dwRemove('${w.id}')" title="Remove widget">${icon('x',13)}</button>
            </div>
            <div class="dw-body" id="dw-body-${w.id}"></div>
          </div>
        </div>
      </div>`;
  }).join('');
  // Initialize gridstack — draggable by the header only so clicks on buttons
  // and interactive widget bodies still work normally.
  _dwGrid = GridStack.init({
    column: 12,
    cellHeight: 80,
    margin: 7,
    float: true,
    animate: true,
    resizable: { handles: 's,e,se' },
    draggable: { handle: '.dw-hdr' },
    alwaysShowResizeHandle: false,
  }, grid);
  _dwGrid.on('change', _dwSaveGridPositions);
  // If anything was auto-placed, flush the resulting positions back so reload is stable
  if (widgets.some(w => w.x === undefined || w.y === undefined)) _dwSaveGridPositions();
  // Render widget contents
  widgets.forEach(w => {
    const reg = _DW_REG[w.type];
    if (reg) reg.render(w.id, w.cfg);
  });
  // Shimmer loading overlay (unchanged semantics)
  const _stateEmpty = !Object.keys(S.sensors).length && !Object.keys(S.devices).length;
  if (!_dwDataArrived && _stateEmpty) {
    grid.querySelectorAll('.dw-body').forEach(el => el.classList.add('dw-loading'));
  }
  _dwStartTick();
}

function _dwRemove(wid) {
  const card = document.getElementById(`dw-${wid}`);
  if (card && card._interval) clearInterval(card._interval);
  const widgets = _dwLoad().filter(w => w.id !== wid);
  _dwSave(widgets);
  _dwRenderAll();
}

// ── Add widget — redesigned picker (search + chips + popout) ──────
// See design/handoff_add_widget_modal/README.md for the spec.

// Mini-preview registry. Each entry returns a DOM Element to drop into the
// 130px popout slot. Static renderers — no live data, no subscriptions —
// they just hint at what the widget will look like.
const _DW_PREVIEW = {
  sensor_chart:    () => _mpSparkline('var(--accent)'),
  device_status:   () => _mpDevicePills(),
  network_avail:   () => _mpUptimeBar(),
  sensor_gauge:    () => _mpGauge(),
  flap_events:     () => _mpFlapList(),
  system_status:   () => _mpSparkline('var(--accent2)'),
  server_perf:     () => _mpBars(),
  down_devices:    () => _mpDevicePills(),
  top_latency:     () => _mpDevicePills(),
  event_count:     () => _mpSla(),
  packet_loss:     () => _mpSparkline('var(--down)'),
  sla_report:      () => _mpSla(),
  flap_detect:     () => _mpFlapList(),
  internet_health: () => _mpInternet(),
  ncm_status:      () => _mpDots(),
  license_overview:() => _mpLicense(),
  fleet_status:    () => _mpRing(),
  latency_heatmap: () => _mpHeatmap(),
};

function _mpEl(html) {
  const t = document.createElement('div');
  t.className = 'mp';
  t.innerHTML = html;
  return t;
}
function _mpSparkline(color) {
  return _mpEl(
    `<span class="mw-po-preview-label">SENSOR · LAST 24H</span>
     <svg class="mp-sparkline" viewBox="0 0 240 120" preserveAspectRatio="none">
       <defs><linearGradient id="sg-${Math.random().toString(36).slice(2,7)}" x1="0" y1="0" x2="0" y2="1">
         <stop offset="0%" stop-color="${color}" stop-opacity="0.4"/>
         <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
       </linearGradient></defs>
       ${[0,30,60,90].map(y => `<line x1="0" y1="${y+10}" x2="240" y2="${y+10}" stroke="rgba(0,212,255,0.07)" stroke-width="1"/>`).join('')}
       <path d="M0,80 L20,75 L40,82 L60,40 L80,55 L100,30 L120,45 L140,25 L160,60 L180,35 L200,50 L220,28 L240,40 L240,120 L0,120 Z"
             fill="${color}" fill-opacity="0.15"/>
       <polyline points="0,80 20,75 40,82 60,40 80,55 100,30 120,45 140,25 160,60 180,35 200,50 220,28 240,40"
                 fill="none" stroke="${color}" stroke-width="1.5"/>
     </svg>`);
}
function _mpDevicePills() {
  const rows = [
    { n: 'core-sw-01',  c: 'var(--up)',   v: '12ms' },
    { n: 'edge-rtr-01', c: 'var(--down)', v: 'DOWN' },
    { n: 'fw-primary',  c: 'var(--up)',   v: '8ms'  },
    { n: 'esx-9a',      c: 'var(--warn)', v: '320ms'},
    { n: 'esx-1a',      c: 'var(--up)',   v: '4ms'  },
  ];
  const html = `<span class="mw-po-preview-label">9 DEVICES · 1 DOWN</span>
    <div class="mp-pills" style="padding-top:18px">${
    rows.map(d => `<div class="mp-pill" style="border-left-color:${d.c}">
      <span class="mp-pill-dot" style="background:${d.c};box-shadow:0 0 4px ${d.c}"></span>
      <span class="mp-pill-name">${d.n}</span>
      <span class="mp-pill-val" style="color:${d.c}">${d.v}</span>
    </div>`).join('')}</div>`;
  return _mpEl(html);
}
function _mpUptimeBar() {
  const bars = Array.from({length:48}, (_,i) => {
    const s = (i===17||i===18) ? 'var(--down)' : (i===32 ? 'var(--warn)' : 'var(--up)');
    return `<div style="flex:1;background:${s};opacity:.85;box-shadow:0 0 3px ${s}"></div>`;
  }).join('');
  return _mpEl(`
    <span class="mw-po-preview-label">24H · 99.42% UP</span>
    <div style="display:flex;flex-direction:column;padding:12px 6px;gap:10px;height:100%">
      <div style="display:flex;gap:1px;height:32px;margin-top:14px">${bars}</div>
      <div style="display:flex;justify-content:space-between;font-family:'JetBrains Mono',monospace;font-size:8px;color:rgba(255,255,255,0.35)">
        <span>00:00</span><span>12:00</span><span>NOW</span>
      </div>
    </div>`);
}
function _mpGauge() {
  return _mpEl(`<div class="mp-gauge">
    <svg viewBox="0 0 200 120" width="100%" height="100%">
      <defs><linearGradient id="gg-${Math.random().toString(36).slice(2,7)}" x1="0" x2="1">
        <stop offset="0" stop-color="var(--up)"/>
        <stop offset="0.5" stop-color="var(--warn)"/>
        <stop offset="1" stop-color="var(--down)"/>
      </linearGradient></defs>
      <path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="rgba(0,212,255,0.1)" stroke-width="10"/>
      <path d="M20 100 A80 80 0 0 1 140 35" fill="none" stroke="var(--warn)" stroke-width="10" stroke-linecap="round"/>
      <line x1="100" y1="100" x2="140" y2="50" stroke="var(--accent)" stroke-width="2"/>
      <circle cx="100" cy="100" r="4" fill="var(--accent)"/>
      <text x="100" y="80" font-family="Inter" font-weight="900" font-size="28" fill="#fff" text-anchor="middle">74</text>
      <text x="100" y="95" font-family="JetBrains Mono" font-size="9" fill="rgba(255,255,255,0.4)" text-anchor="middle">CPU %</text>
    </svg>
  </div>`);
}
function _mpFlapList() {
  const rows = [
    { t:'3m',  n:'ESXI-Shrek',    s:'DOWN', c:'var(--down)' },
    { t:'14m', n:'192.168.40.55', s:'DOWN', c:'var(--down)' },
    { t:'42m', n:'ESX-9B.ipmi',   s:'DOWN', c:'var(--down)' },
    { t:'1h',  n:'tlv-fw-01',     s:'UP',   c:'var(--up)'   },
    { t:'2h',  n:'cnn.com',       s:'WARN', c:'var(--warn)' },
  ];
  const html = `<span class="mw-po-preview-label">LAST FLAPS · 24H</span>
    <div class="mp-pills" style="padding-top:18px">${
    rows.map(f => `<div class="mp-pill" style="border-left-color:${f.c}">
      <span class="mp-pill-val" style="color:rgba(255,255,255,0.45);min-width:24px">${f.t}</span>
      <span class="mp-pill-name">${f.n}</span>
      <span class="mp-pill-val" style="color:${f.c}">● ${f.s}</span>
    </div>`).join('')}</div>`;
  return _mpEl(html);
}
function _mpBars() {
  const bars = [
    {n:'CPU', v:74, c:'var(--accent)'},
    {n:'RAM', v:62, c:'var(--accent2)'},
    {n:'DISK',v:48, c:'var(--gold)'},
    {n:'NET', v:31, c:'var(--purple)'},
  ];
  return _mpEl(`<div class="mp-bars">
    <span class="mw-po-preview-label" style="position:absolute;top:4px;left:6px">CPU · RAM · DISK · NET</span>
    ${bars.map(b => `<div class="mp-bar-row">
      <span class="mp-bar-name">${b.n}</span>
      <div class="mp-bar-track"><div class="mp-bar-fill" style="width:${b.v}%;background:${b.c};box-shadow:0 0 4px ${b.c}"></div></div>
      <span class="mp-bar-val">${b.v}%</span>
    </div>`).join('')}
  </div>`);
}
function _mpHeatmap() {
  const cells = Array.from({length:60}, () => {
    const r = Math.random();
    let c;
    if (r > 0.92) c = 'var(--down)';
    else if (r > 0.78) c = 'var(--warn)';
    else if (r > 0.4)  c = 'var(--accent)';
    else c = 'var(--up)';
    return `<div style="background:${c};opacity:${(0.3+r*0.7).toFixed(2)}"></div>`;
  }).join('');
  return _mpEl(`<span class="mw-po-preview-label">LATENCY · 10×6 DEVICES</span>
    <div class="mp-heat" style="padding-top:18px">${cells}</div>`);
}
function _mpDots() {
  const cells = Array.from({length:64}, (_,i) => {
    let c = 'var(--up)';
    if (i===4 || i===19) c = 'var(--down)';
    else if (i===27 || i===50) c = 'var(--warn)';
    return `<div style="background:${c};opacity:.85;box-shadow:0 0 3px ${c}"></div>`;
  }).join('');
  return _mpEl(`<span class="mw-po-preview-label">FLEET · 64 DEVICES</span>
    <div class="mp-dots" style="padding-top:18px">${cells}</div>`);
}
function _mpSla() {
  const rows = [
    {n:'Core',  v:99.98, c:'var(--up)'},
    {n:'Edge',  v:99.84, c:'var(--up)'},
    {n:'VPN',   v:99.41, c:'var(--warn)'},
    {n:'Backup',v:98.12, c:'var(--down)'},
  ];
  return _mpEl(`<div class="mp-bars">
    <span class="mw-po-preview-label" style="position:absolute;top:4px;left:6px">SLA · LAST 30 DAYS</span>
    ${rows.map(b => `<div class="mp-bar-row">
      <span class="mp-bar-name">${b.n}</span>
      <div class="mp-bar-track" style="background:rgba(0,255,157,0.05)">
        <div class="mp-bar-fill" style="width:${b.v}%;background:${b.c};box-shadow:0 0 4px ${b.c}"></div>
      </div>
      <span class="mp-bar-val">${b.v}%</span>
    </div>`).join('')}
  </div>`);
}
function _mpInternet() {
  const rows = [
    {n:'Google-DNS', c:'var(--up)',   v:'14ms'   },
    {n:'ynet.co.il', c:'var(--up)',   v:'42ms'   },
    {n:'cnn.com',    c:'var(--warn)', v:'TIMEOUT'},
    {n:'cloudflare', c:'var(--up)',   v:'11ms'   },
  ];
  const html = `<span class="mw-po-preview-label">INTERNET · 4 CHECKS</span>
    <div class="mp-pills" style="padding-top:18px">${
    rows.map(d => `<div class="mp-pill" style="border-left-color:${d.c}">
      <span class="mp-pill-dot" style="background:${d.c};box-shadow:0 0 4px ${d.c}"></span>
      <span class="mp-pill-name">${d.n}</span>
      <span class="mp-pill-val" style="color:${d.c}">${d.v}</span>
    </div>`).join('')}</div>`;
  return _mpEl(html);
}
function _mpRing() {
  return _mpEl(`<div class="mp-gauge">
    <svg viewBox="0 0 200 130" width="100%" height="100%">
      <circle cx="100" cy="65" r="48" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="14"/>
      <circle cx="100" cy="65" r="48" fill="none" stroke="var(--up)" stroke-width="14"
              stroke-dasharray="234 302" transform="rotate(-90 100 65)"/>
      <circle cx="100" cy="65" r="48" fill="none" stroke="var(--warn)" stroke-width="14"
              stroke-dasharray="14 302" stroke-dashoffset="-234" transform="rotate(-90 100 65)"/>
      <circle cx="100" cy="65" r="48" fill="none" stroke="var(--down)" stroke-width="14"
              stroke-dasharray="20 302" stroke-dashoffset="-248" transform="rotate(-90 100 65)"/>
      <text x="100" y="60" font-family="Inter" font-weight="900" font-size="22" fill="#fff" text-anchor="middle">84</text>
      <text x="100" y="76" font-family="JetBrains Mono" font-size="9" fill="rgba(255,255,255,0.45)" text-anchor="middle">DEVICES</text>
      <text x="40"  y="125" font-family="JetBrains Mono" font-size="9" fill="var(--up)">● 78 UP</text>
      <text x="100" y="125" font-family="JetBrains Mono" font-size="9" fill="var(--warn)" text-anchor="middle">● 2 WARN</text>
      <text x="160" y="125" font-family="JetBrains Mono" font-size="9" fill="var(--down)" text-anchor="end">● 4 DOWN</text>
    </svg>
  </div>`);
}
function _mpLicense() {
  return _mpEl(`<span class="mw-po-preview-label">SEATS · 64 / 100</span>
    <div style="padding:22px 10px 0">
      <div style="display:flex;gap:1px;height:22px;background:rgba(0,212,255,0.1);border:1px solid rgba(0,212,255,0.2)">
        <div style="width:64%;background:var(--accent);box-shadow:inset 0 0 8px rgba(255,255,255,0.2)"></div>
      </div>
      <div style="display:flex;justify-content:space-between;margin-top:4px;font-family:'JetBrains Mono',monospace;font-size:8px;color:rgba(255,255,255,0.45)">
        <span>Used 64</span><span>Free 36</span><span>EXP 2027-03-15</span>
      </div>
      <div style="margin-top:10px;padding:6px 8px;border:1px dashed rgba(255,107,53,0.3);font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--warn)">
        ⚠ Renewal in 287 days
      </div>
    </div>`);
}

// ── Recent-used tracking (localStorage, cap 6) ────────────────────
function _dwGetRecent() {
  try {
    const v = JSON.parse(localStorage.getItem('pw_widget_recent') || '[]');
    return Array.isArray(v) ? v.slice(0, 6) : [];
  } catch(_) { return []; }
}
function _dwPushRecent(type) {
  try {
    const cur = _dwGetRecent().filter(t => t !== type);
    cur.unshift(type);
    localStorage.setItem('pw_widget_recent', JSON.stringify(cur.slice(0, 6)));
  } catch(_) {}
}

// ── Open the redesigned Add Widget modal ──────────────────────────
function _dwOpenPicker() {
  const STATE = {
    query: '',
    cat:   'all',     // 'all' | 'recent' | <cat key>
    hover: null,      // type id of the currently-hovered tile
  };

  const overlay = document.createElement('div');
  overlay.id = 'dw-picker-overlay';
  overlay.className = 'mo mw-mo';
  overlay.innerHTML = `
    <div class="mw-stage" id="mw-stage">
      <div class="mw-modal" id="mw-modal">
        <div class="mw-head">
          <span class="mw-title">ADD WIDGET</span>
          <span class="mw-sub" id="mw-sub">› ${Object.keys(_DW_REG).length} types</span>
          <button class="mw-x" type="button" data-mw-close="1">✕</button>
        </div>
        <div class="mw-search-wrap">
          <div class="mw-search">
            <span class="mw-search-icon">⌕</span>
            <input id="mw-q" type="text" placeholder="Search widgets… (sensor, latency, sla, alert…)" autocomplete="off"/>
            <span class="mw-clear" id="mw-clear" style="display:none">✕</span>
            <span class="mw-kbd">Ctrl+K</span>
          </div>
        </div>
        <div class="mw-chips" id="mw-chips"></div>
        <div class="mw-body" id="mw-body"></div>
        <div class="mw-foot">
          <div class="mw-foot-info" id="mw-foot-info"></div>
          <button class="mw-btn mw-btn-ghost" type="button" data-mw-close="1">CANCEL</button>
          <button class="mw-btn mw-btn-primary" id="mw-add" type="button">+ ADD WIDGET</button>
        </div>
      </div>
      <div class="mw-popout-wrap" id="mw-popout-wrap"></div>
    </div>`;
  document.body.appendChild(overlay);
  _overlayClose(overlay, () => _dwClosePicker());

  const _q = (s) => String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

  function _filtered() {
    const q = STATE.query.trim().toLowerCase();
    return Object.entries(_DW_REG).filter(([type, reg]) => {
      const cat = reg.cat || 'charts';
      if (STATE.cat === 'recent') {
        if (!_dwGetRecent().includes(type)) return false;
      } else if (STATE.cat !== 'all' && cat !== STATE.cat) {
        return false;
      }
      if (!q) return true;
      return (reg.label || '').toLowerCase().includes(q) ||
             (reg.desc  || '').toLowerCase().includes(q) ||
             cat.toLowerCase().includes(q);
    });
  }

  function _renderChips() {
    const recentIds = _dwGetRecent();
    const recentCount = recentIds.length;
    const chips = [
      { id:'all',    label:'ALL',    c:'var(--accent)', count:Object.keys(_DW_REG).length, dot:false },
      { id:'recent', label:'RECENT', c:'var(--gold)',   count:recentCount,                 dot:true  },
      ..._DW_CAT_ORDER.map(k => ({
        id: k,
        label: _DW_CATS[k].name.toUpperCase(),
        c: _DW_CATS[k].c,
        count: Object.values(_DW_REG).filter(r => r.cat === k).length,
        dot: true,
      })),
    ];
    document.getElementById('mw-chips').innerHTML = chips.map(c => {
      const sel = c.id === STATE.cat;
      const dotHtml = c.dot ? `<span class="mw-chip-dot" style="background:${c.c}"></span>` : '';
      const style = sel ? `color:${c.c};border-color:${c.c};background:color-mix(in srgb,${c.c} 10%, transparent)` : '';
      return `<span class="mw-chip${sel?' sel':''}" data-cat="${c.id}" style="${style}">
        ${dotHtml}${c.label}<span class="mw-chip-count">${c.count}</span>
      </span>`;
    }).join('');
  }

  function _renderTile(type, reg) {
    const cat = _DW_CATS[reg.cat || 'charts'];
    const corner = reg.isNew
      ? '<span class="mw-tile-new">NEW</span>'
      : `<span class="mw-tile-cat">${cat.name.slice(0,4).toUpperCase()}</span>`;
    const hov = STATE.hover === type ? ' hov' : '';
    return `<div class="mw-tile${hov}" data-type="${type}" style="color:${cat.c}">
      <div class="mw-tile-icon">${reg.icon || ''}</div>
      <div class="mw-tile-name">${_q(reg.label)}</div>
      <div class="mw-tile-desc">${_q(reg.desc || '')}</div>
      ${corner}
    </div>`;
  }

  function _renderSection(label, glyph, color, items) {
    if (!items.length) return '';
    const tiles = items.map(([t, r]) => _renderTile(t, r)).join('');
    return `<div class="mw-section">
      <div class="mw-section-head" style="color:${color}">
        <span class="mw-section-icon">${glyph}</span>
        <span class="mw-section-label">${label}</span>
        <span class="mw-section-count">· ${items.length}</span>
        <span class="mw-section-line"></span>
      </div>
      <div class="mw-grid">${tiles}</div>
    </div>`;
  }

  function _renderBody() {
    const matched = _filtered();
    const body = document.getElementById('mw-body');
    document.getElementById('mw-sub').textContent = `› ${matched.length} of ${Object.keys(_DW_REG).length}`;
    if (!matched.length) {
      body.innerHTML = `<div class="mw-empty">
        NO WIDGETS MATCH "${_q(STATE.query)}"<br/>
        <span>try a different search or clear filters</span>
      </div>`;
      _renderFoot(matched.length);
      _renderPopout();
      return;
    }
    const recentIds = _dwGetRecent();
    const inRecent = matched.filter(([t]) => recentIds.includes(t))
                            .sort(([a],[b]) => recentIds.indexOf(a) - recentIds.indexOf(b));
    const inPopular = matched.filter(([_, r]) => r.popular)
                             .filter(([t]) => !recentIds.includes(t));
    let html = '';
    if (STATE.cat === 'all') {
      html += _renderSection('RECENTLY USED', '✦', 'var(--gold)',    inRecent);
      html += _renderSection('POPULAR',       '★', 'var(--accent2)', inPopular);
      _DW_CAT_ORDER.forEach(k => {
        const items = matched.filter(([t, r]) => {
          if (r.cat !== k) return false;
          if (recentIds.includes(t)) return false;
          if (r.popular) return false;
          return true;
        });
        html += _renderSection(_DW_CATS[k].name.toUpperCase(), '◆', _DW_CATS[k].c, items);
      });
    } else if (STATE.cat === 'recent') {
      html += _renderSection('RECENTLY USED', '✦', 'var(--gold)', inRecent);
    } else {
      html += _renderSection(_DW_CATS[STATE.cat].name.toUpperCase(), '◆',
                             _DW_CATS[STATE.cat].c, matched);
    }
    body.innerHTML = html;
    _renderFoot(matched.length);
    // Ensure hover is valid; default to first match if not in the visible set
    const visibleIds = matched.map(([t]) => t);
    if (!STATE.hover || !visibleIds.includes(STATE.hover)) {
      STATE.hover = visibleIds[0] || null;
      // Re-highlight the new hover tile
      body.querySelectorAll('.mw-tile').forEach(t =>
        t.classList.toggle('hov', t.getAttribute('data-type') === STATE.hover));
    }
    _renderPopout();
  }

  function _renderFoot(count) {
    const reg = STATE.hover ? _DW_REG[STATE.hover] : null;
    const label = reg ? reg.label.toUpperCase() : 'WIDGET';
    document.getElementById('mw-add').textContent = `+ ADD ${label}`;
    let info = `<b>${count}</b> widget${count===1?'':'s'}`;
    if (STATE.query) info += ` matching "${_q(STATE.query)}"`;
    if (STATE.cat !== 'all' && STATE.cat !== 'recent') {
      info += ` · ${_DW_CATS[STATE.cat]?.name || STATE.cat}`;
    }
    info += ' · <span style="opacity:0.6">hover any tile for preview</span>';
    document.getElementById('mw-foot-info').innerHTML = info;
  }

  function _renderPopout() {
    const wrap = document.getElementById('mw-popout-wrap');
    const type = STATE.hover;
    const reg = type ? _DW_REG[type] : null;
    if (!reg) {
      wrap.innerHTML = `<div class="mw-popout-empty">◇<br/>
        <span>HOVER A WIDGET<br/>TO PREVIEW</span></div>`;
      return;
    }
    const cat = _DW_CATS[reg.cat || 'charts'];
    wrap.innerHTML = `
      <div class="mw-popout" style="border-color:${cat.c}">
        <div class="mw-po-head">
          <div class="mw-po-icon" style="color:${cat.c}">${reg.icon || ''}</div>
          <div>
            <div class="mw-po-title">${_q(reg.label)}</div>
            <div class="mw-po-cat" style="color:${cat.c}">● ${cat.name.toUpperCase()}</div>
          </div>
        </div>
        <div class="mw-po-preview" id="mw-po-preview"></div>
        <div class="mw-po-desc">${_q(reg.desc || '')}</div>
        <div class="mw-po-meta">${(reg.meta||[]).map(m => `<span class="mw-po-meta-i">${_q(m)}</span>`).join('')}</div>
        <button class="mw-po-add" type="button">+ ADD TO DASHBOARD</button>
      </div>`;
    // Inject the mini-preview DOM element
    const builder = _DW_PREVIEW[type];
    if (builder) {
      try {
        const el = builder();
        if (el) document.getElementById('mw-po-preview').appendChild(el);
      } catch (e) {
        // Preview is decorative — don't block — but surface the error so a
        // regression doesn't ship silently.
        console.warn('[dashboard] preview builder failed for', type, e);
      }
    }
    wrap.querySelector('.mw-po-add').addEventListener('click', () => _dwPickerAdd(type));
  }

  function _toast(name) {
    const wrap = document.getElementById('mw-popout-wrap');
    if (!wrap) return;
    const t = document.createElement('div');
    t.className = 'mw-toast';
    t.textContent = `✓ ADDED · ${name.toUpperCase()}`;
    wrap.appendChild(t);
    setTimeout(() => t.remove(), 1800);
  }

  // Local closures — not on window. Avoids stale references when the picker
  // is opened twice in a row.
  function _dwPickerAdd(type) {
    const reg = _DW_REG[type];
    if (!reg) return;
    const needsConfig = (reg.fields || []).some(f =>
      f.type === 'device-select' || f.type === 'sensor-select');
    if (needsConfig) {
      // Hand off to the existing per-widget config form.
      _dwClosePicker();
      _dwSelectType(type);
      return;
    }
    // Direct-add: apply defaults from the field spec, no modal pop-up.
    const cfg = {};
    (reg.fields || []).forEach(f => { if (f.def !== undefined) cfg[f.key] = f.def; });
    const widgets = _dwLoad();
    widgets.push({
      id: Math.random().toString(36).slice(2,9),
      type, title: reg.label, cols: reg.defaultCols,
      w: reg.defaultCols === 2 ? 6 : 3, h: 4, cfg,
    });
    _dwSave(widgets);
    _dwPushRecent(type);
    _dwRenderAll();
    _toast(reg.label);
    // Keep the picker open so the user can add several in a row.
    _renderChips();
    _renderBody();
  }

  function _dwClosePicker() {
    document.removeEventListener('keydown', _onKey);
    document.getElementById('dw-picker-overlay')?.remove();
  }

  // Wire the close buttons that the template marked with data-mw-close
  overlay.querySelectorAll('[data-mw-close]').forEach(b =>
    b.addEventListener('click', _dwClosePicker));

  function _onKey(e) {
    // Ctrl+K / Cmd+K → focus search
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      document.getElementById('mw-q')?.focus();
      return;
    }
    if (e.key === 'Escape') { e.preventDefault(); _dwClosePicker(); return; }
    if (e.key === 'Enter')  {
      if (STATE.hover) { e.preventDefault(); _dwPickerAdd(STATE.hover); }
      return;
    }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      const tiles = Array.from(document.querySelectorAll('#mw-body .mw-tile'));
      if (!tiles.length) return;
      e.preventDefault();
      const idx = Math.max(0, tiles.findIndex(t => t.getAttribute('data-type') === STATE.hover));
      const next = e.key === 'ArrowDown'
        ? Math.min(tiles.length - 1, idx + 1)
        : Math.max(0, idx - 1);
      STATE.hover = tiles[next].getAttribute('data-type');
      tiles.forEach(t => t.classList.toggle('hov',
        t.getAttribute('data-type') === STATE.hover));
      tiles[next].scrollIntoView({ block: 'nearest' });
      _renderPopout();
      _renderFoot(tiles.length);
    }
  }
  document.addEventListener('keydown', _onKey);

  // Wire interactions on the modal shell
  const qInput = document.getElementById('mw-q');
  qInput.addEventListener('input', e => {
    STATE.query = e.target.value;
    document.getElementById('mw-clear').style.display = STATE.query ? '' : 'none';
    _renderChips();
    _renderBody();
  });
  document.getElementById('mw-clear').addEventListener('click', () => {
    STATE.query = '';
    qInput.value = '';
    document.getElementById('mw-clear').style.display = 'none';
    _renderChips();
    _renderBody();
  });
  document.getElementById('mw-chips').addEventListener('click', e => {
    const chip = e.target.closest('.mw-chip');
    if (!chip) return;
    const id = chip.getAttribute('data-cat');
    // Re-clicking the active chip OR clicking ALL clears the filter
    STATE.cat = (id === STATE.cat || id === 'all') ? 'all' : id;
    _renderChips();
    _renderBody();
  });
  document.getElementById('mw-body').addEventListener('mouseover', e => {
    const tile = e.target.closest('.mw-tile');
    if (!tile) return;
    const type = tile.getAttribute('data-type');
    if (type === STATE.hover) return;
    STATE.hover = type;
    document.querySelectorAll('#mw-body .mw-tile').forEach(t =>
      t.classList.toggle('hov', t.getAttribute('data-type') === STATE.hover));
    _renderPopout();
    _renderFoot(_filtered().length);
  });
  document.getElementById('mw-body').addEventListener('click', e => {
    const tile = e.target.closest('.mw-tile');
    if (!tile) return;
    _dwPickerAdd(tile.getAttribute('data-type'));
  });
  document.getElementById('mw-add').addEventListener('click', () => {
    if (STATE.hover) _dwPickerAdd(STATE.hover);
  });

  // First render — pick a sensible default hover (first widget)
  STATE.hover = Object.keys(_DW_REG)[0];
  _renderChips();
  _renderBody();
  // Auto-focus the search after the modal animates in
  setTimeout(() => qInput.focus(), 30);
}

function _dwSelectType(type) {
  document.getElementById('dw-picker-overlay')?.remove();
  const reg = _DW_REG[type];
  if (!reg) return;
  // Build config form from fields[]
  const fieldsHtml = reg.fields.map(f => {
    if (f.type === 'device-select') {
      const opts = Object.values(S.devices).map(d =>
        `<option value="${d.device_id}">${esc(d.name)} (${esc(d.host||'')})</option>`).join('');
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}" onchange="_dwCfgDeviceChange(this.value)">${opts}</select>
      </div>`;
    }
    if (f.type === 'sensor-select') {
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}"></select>
      </div>`;
    }
    if (f.type === 'select') {
      const opts = f.options.map(o => `<option value="${o.v}"${o.v===f.def?' selected':''}>${o.l}</option>`).join('');
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}">${opts}</select>
      </div>`;
    }
    if (f.type === 'group-select') {
      const cur = f.def || '';
      const groups = [...new Set(Object.values(S.devices).map(d => d.group || 'Default Group'))].sort();
      const opts = ['<option value="">All groups</option>']
        .concat(groups.map(g => `<option value="${esc(g)}"${g===cur?' selected':''}>${esc(g)}</option>`))
        .join('');
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}">${opts}</select>
      </div>`;
    }
    return '';
  }).join('');
  const noteHtml = reg.note
    ? `<div class="dw-cfg-note">${reg.note}</div>`
    : '';
  const titleDefault = reg.label;
  const html = `
    <div class="mo" id="dw-cfg-overlay">
      <div class="mbox" style="width:380px">
        <div class="mhd">
          <span class="mttl">${reg.icon} ${reg.label}</span>
          <button class="mclose" onclick="document.getElementById('dw-cfg-overlay').remove()">✕</button>
        </div>
        <div class="mbdy">
          ${noteHtml}
          <div class="fr">
            <label class="fl">Widget Title</label>
            <input id="dw-cfg-title" type="text" value="${titleDefault}" placeholder="Widget title">
          </div>
          ${fieldsHtml}
        </div>
        <div class="mft">
          <button class="btn-s" onclick="document.getElementById('dw-cfg-overlay').remove()">Cancel</button>
          <button class="btn-p" onclick="_dwConfirmAdd('${type}')">Add Widget</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  _overlayClose(document.getElementById('dw-cfg-overlay'), () => document.getElementById('dw-cfg-overlay')?.remove());
  // Populate sensor select for the initially-selected device
  const firstDev = Object.keys(S.devices)[0];
  if (firstDev && reg.fields.some(f => f.type === 'device-select')) _dwCfgDeviceChange(firstDev);
}

// _dwCfgDeviceChange defined below (supports optional preselectSid for edit mode)

function _dwConfirmAdd(type) {
  const reg = _DW_REG[type];
  if (!reg) return;
  const title = document.getElementById('dw-cfg-title')?.value.trim() || reg.label;
  const cfg = {};
  reg.fields.forEach(f => {
    const el = document.getElementById(`dw-cfg-${f.key}`);
    if (el) cfg[f.key] = f.type === 'select' || f.key === 'minutes' ? Number(el.value) : el.value;
  });
  // Auto-title: use sensor name if sensor_chart
  let finalTitle = title;
  if (type === 'sensor_chart' && cfg.did && cfg.sid) {
    const sen = S.sensors[`${cfg.did}/${cfg.sid}`];
    const dev = S.devices[cfg.did];
    if (sen && dev && title === reg.label) finalTitle = `${dev.name} / ${sen.name}`;
  }
  const widgets = _dwLoad();
  // x/y left undefined so gridstack auto-places the new widget at the first free cell.
  widgets.push({ id: Math.random().toString(36).slice(2, 9), type, title: finalTitle, cols: reg.defaultCols, w: reg.defaultCols === 2 ? 6 : 3, h: 4, cfg });
  _dwSave(widgets);
  if (typeof _dwPushRecent === 'function') _dwPushRecent(type);
  document.getElementById('dw-cfg-overlay')?.remove();
  _dwRenderAll();
}

// ── Edit existing widget ──────────────────────────────────────────
function _dwOpenEdit(wid) {
  const w = _dwLoad().find(x => x.id === wid);
  if (!w) return;
  const reg = _DW_REG[w.type];
  if (!reg) return;
  // Build the same config form as Add flow
  const fieldsHtml = reg.fields.map(f => {
    if (f.type === 'device-select') {
      const opts = Object.values(S.devices).map(d =>
        `<option value="${d.device_id}"${d.device_id===w.cfg.did?' selected':''}>${esc(d.name)} (${esc(d.host||'')})</option>`).join('');
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}" onchange="_dwCfgDeviceChange(this.value,'${w.cfg.sid}')">${opts}</select>
      </div>`;
    }
    if (f.type === 'sensor-select') {
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}"></select>
      </div>`;
    }
    if (f.type === 'select') {
      const opts = f.options.map(o =>
        `<option value="${o.v}"${String(o.v)===String(w.cfg[f.key]||f.def)?' selected':''}>${o.l}</option>`).join('');
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}">${opts}</select>
      </div>`;
    }
    if (f.type === 'group-select') {
      const cur = w.cfg[f.key] || '';
      const groups = [...new Set(Object.values(S.devices).map(d => d.group || 'Default Group'))].sort();
      const opts = ['<option value="">All groups</option>']
        .concat(groups.map(g => `<option value="${esc(g)}"${g===cur?' selected':''}>${esc(g)}</option>`))
        .join('');
      return `<div class="fr">
        <label class="fl">${f.label}</label>
        <select id="dw-cfg-${f.key}">${opts}</select>
      </div>`;
    }
    return '';
  }).join('');
  const html = `
    <div class="mo" id="dw-cfg-overlay">
      <div class="mbox" style="width:380px">
        <div class="mhd">
          <span class="mttl">${reg.icon} Edit — ${reg.label}</span>
          <button class="mclose" onclick="document.getElementById('dw-cfg-overlay').remove()">✕</button>
        </div>
        <div class="mbdy">
          <div class="fr">
            <label class="fl">Widget Title</label>
            <input id="dw-cfg-title" type="text" value="${esc(w.title)}" placeholder="Widget title">
          </div>
          ${fieldsHtml}
        </div>
        <div class="mft">
          <button class="btn-s" onclick="document.getElementById('dw-cfg-overlay').remove()">Cancel</button>
          <button class="btn-p" onclick="_dwSaveEdit('${wid}')">Save Changes</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  _overlayClose(document.getElementById('dw-cfg-overlay'), () => document.getElementById('dw-cfg-overlay')?.remove());
  // Pre-populate sensor select with the current device's sensors (and preserve selection)
  if (w.cfg.did && reg.fields.some(f => f.type === 'device-select'))
    _dwCfgDeviceChange(w.cfg.did, w.cfg.sid);
}

function _dwSaveEdit(wid) {
  const widgets = _dwLoad();
  const w = widgets.find(x => x.id === wid);
  if (!w) return;
  const reg = _DW_REG[w.type];
  if (!reg) return;
  w.title = document.getElementById('dw-cfg-title')?.value.trim() || w.title;
  reg.fields.forEach(f => {
    const el = document.getElementById(`dw-cfg-${f.key}`);
    if (el) w.cfg[f.key] = f.type === 'select' || f.key === 'minutes' ? Number(el.value) : el.value;
  });
  _dwSave(widgets);
  document.getElementById('dw-cfg-overlay')?.remove();
  // Update card header title in place
  const card = document.querySelector(`.dw-card[data-wid="${wid}"]`);
  if (card) {
    const titleEl = card.querySelector('.dw-title');
    if (titleEl) titleEl.textContent = w.title;
    const bodyEl = document.getElementById(`dw-body-${wid}`);
    if (bodyEl) reg.render(wid, w.cfg);
  }
}

// Patch _dwCfgDeviceChange to accept optional pre-selected sensor
function _dwCfgDeviceChange(did, preselectSid) {
  const sel = document.getElementById('dw-cfg-sid');
  if (!sel) return;
  const sensors = Object.values(S.sensors).filter(s => s.device_id === did);
  sel.innerHTML = sensors.map(s =>
    `<option value="${s.sensor_id}"${s.sensor_id===preselectSid?' selected':''}>${esc(s.name)} (${s.stype})</option>`).join('');
}

// ── Fullscreen expand ─────────────────────────────────────────────
let _dwFsInterval = null;

function _dwEnsureFullscreenModal() {
  if (document.getElementById('dw-fs-overlay')) return;
  document.body.insertAdjacentHTML('beforeend', `
    <div id="dw-fs-overlay" class="mo" style="display:none"
         onclick="if(event.target===this)_dwCloseFullscreen()">
      <div class="dw-fs-box">
        <div class="dw-fs-hdr">
          <span class="dw-fs-icon" id="dw-fs-icon"></span>
          <span class="dw-fs-title" id="dw-fs-title"></span>
          <button class="mclose" onclick="_dwCloseFullscreen()">✕</button>
        </div>
        <div class="dw-fs-body" id="dw-fs-body"></div>
      </div>
    </div>`);
}

function _dwOpenFullscreen(wid) {
  const w = _dwLoad().find(x => x.id === wid);
  if (!w) return;
  const reg = _DW_REG[w.type];
  if (!reg) return;
  _dwEnsureFullscreenModal();
  _dwCloseFullscreen(); // clear any previous
  document.getElementById('dw-fs-icon').innerHTML    = reg.icon;
  document.getElementById('dw-fs-title').textContent = w.title;
  const fsBody = document.getElementById('dw-fs-body');
  fsBody.innerHTML = '';
  // Use wid with a fs- prefix so IDs don't clash with the grid card
  const fsWid = 'fs-' + wid;
  fsBody.id = `dw-body-${fsWid}`;
  reg.render(fsWid, w.cfg);
  document.getElementById('dw-fs-overlay').style.display = 'flex';
  // Auto-refresh every 15 s while open
  _dwFsInterval = setInterval(() => reg.refresh(fsWid, w.cfg), 10000);
}

function _dwCloseFullscreen() {
  clearInterval(_dwFsInterval); _dwFsInterval = null;
  const ov = document.getElementById('dw-fs-overlay');
  if (!ov) return;
  ov.style.display = 'none';
  const fsBody = document.getElementById('dw-fs-body') || ov.querySelector('[id^="dw-body-fs-"]');
  if (fsBody) { fsBody.innerHTML = ''; fsBody.id = 'dw-fs-body'; }
}

// ── Widget: Sensor History Chart ──────────────────────────────────
// Time window now follows the global dashboard range (set in the tab bar).
function _dwRenderSensorChart(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  body.innerHTML = `
    <div class="dw-chart-toolbar">
      <span class="dm-hist-stats" id="dw-stats-${wid}"></span>
    </div>
    <canvas id="dw-canvas-${wid}" class="dm-hist-canvas dw-canvas" height="180"></canvas>
    <div id="dw-sum-${wid}" class="dm-hist-summary"></div>`;
  _dwLoadSensorChart(wid, cfg.did, cfg.sid, _dwTimeRangeMinutes());
}

async function _dwLoadSensorChart(wid, did, sid, minutes) {
  const canvas  = document.getElementById(`dw-canvas-${wid}`);
  const statsEl = document.getElementById(`dw-stats-${wid}`);
  const sumEl   = document.getElementById(`dw-sum-${wid}`);
  if (typeof _renderHistoryChart === 'function')
    await _renderHistoryChart(canvas, statsEl, sumEl, did, sid, minutes);
}

// ── Widget: Device Status ─────────────────────────────────────────
function _dwRenderDeviceStatus(wid) {
  _dwRefreshDeviceStatus(wid);
}

function _dwRefreshDeviceStatus(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const devs = Object.values(S.devices);
  const cnt = {up:0, down:0, warn:0, idle:0};
  devs.forEach(d => { const s = d.status||'idle'; cnt[s in cnt ? s : 'idle']++; });
  const rows = devs.map(d => {
    const st = d.status || 'idle';
    const ic = {ping:'◉',tcp:'⇌',http:'◈',snmp:'◎',dns:'⬡'}[d.stype] || '·';
    return `<div class="dw-ds-row">
      <span class="dw-ds-dot ${st}"></span>
      <span class="dw-ds-name">${esc(d.name)}</span>
      <span class="dw-ds-host">${esc(d.host||'')}</span>
      <span class="dw-ds-st ${st}">${st}</span>
    </div>`;
  }).join('');
  _dwSwap(body, `
    <div class="dw-ds-pills">
      <span class="dw-ds-pill up">${cnt.up} Up</span>
      <span class="dw-ds-pill down">${cnt.down} Down</span>
      <span class="dw-ds-pill warn">${cnt.warn} Warning</span>
      ${cnt.idle ? `<span class="dw-ds-pill idle">${cnt.idle} Idle</span>` : ''}
    </div>
    <div class="dw-ds-list">${rows || '<div style="color:var(--text3);font-size:11px;padding:8px">No devices</div>'}</div>`);
}

// ── Widget: Fleet Status (donut breakdown of device statuses) ────
function _dwRefreshFleetStatus(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const devs = Object.values(S.devices);
  const cnt = { up: 0, warn: 0, down: 0, pause: 0 };
  devs.forEach(d => {
    let s = d.status || 'pause';
    if (s === 'idle' || s === 'paused') s = 'pause';
    if (s in cnt) cnt[s]++;
  });
  const total = devs.length;
  if (!total) {
    body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;text-align:center">No devices</div>';
    return;
  }
  const segs = [
    { value: cnt.up,    color: 'var(--up)',    label: 'Up' },
    { value: cnt.warn,  color: 'var(--warn)',  label: 'Warning' },
    { value: cnt.down,  color: 'var(--down)',  label: 'Down' },
    { value: cnt.pause, color: 'var(--pause)', label: 'Paused' },
  ];
  const legendRows = segs.map(seg => `
    <div class="donut-legend-row">
      <span class="sw" style="background:${seg.color}"></span>
      <span class="lbl">${seg.label}</span>
      <span class="ct">${seg.value}</span>
      <span class="pct">${(seg.value / total * 100).toFixed(1)}%</span>
    </div>`).join('');
  _dwSwap(body, `
    <div class="donut-wrap" style="padding:6px 4px">
      <div class="donut">${pwDonut(segs, { size: 130, stroke: 16 })}</div>
      <div class="donut-legend">${legendRows}</div>
    </div>`);
}

// ── Widget: Latency Heatmap (devices × time buckets) ──────────────
// Caches per-widget so the 10s tick doesn't fire-storm /history requests.
const _dwHeatCache = {};   // wid → { ts, html }
const HEAT_CACHE_TTL = 30000;

async function _dwRefreshLatencyHeatmap(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const limit   = Number(cfg?.limit) || 20;
  const group   = cfg?.group || '';        // '' = all groups
  const minutes = _dwTimeRangeMinutes();   // follows global dashboard range

  // Serve from cache if fresh
  const cached = _dwHeatCache[wid];
  if (cached && (Date.now() - cached.ts) < HEAT_CACHE_TTL && cached.cfgKey === `${limit}/${minutes}/${group}`) {
    if (body.innerHTML !== cached.html) body.innerHTML = cached.html;
    return;
  }

  // Pick up to `limit` ping sensors, one per device (worst current latency wins ties).
  // When a group is configured, only devices in that group are considered.
  const pingByDevice = {};
  Object.values(S.sensors).forEach(s => {
    if (s.stype !== 'ping') return;
    if (group) {
      const dev = S.devices[s.device_id];
      if (!dev || (dev.group || 'Default Group') !== group) return;
    }
    const cur = pingByDevice[s.device_id];
    if (!cur || (Number(s.last_ms) || 0) > (Number(cur.last_ms) || 0)) pingByDevice[s.device_id] = s;
  });
  const sensors = Object.values(pingByDevice).slice(0, limit);
  if (!sensors.length) {
    const msg = group ? `No ping sensors in “${esc(group)}”` : 'No ping sensors yet';
    body.innerHTML = `<div style="color:var(--text3);font-size:11px;padding:8px;text-align:center">${msg}</div>`;
    return;
  }

  // Fetch each sensor's recent history in parallel
  // Limit small (~30 buckets) — server-side downsamples if needed
  const results = await Promise.all(sensors.map(s =>
    fetch(`/api/device/${s.device_id}/sensor/${s.sensor_id}/history?minutes=${minutes}&limit=120`)
      .then(r => r.ok ? r.json() : { samples: [] })
      .catch(() => ({ samples: [] }))
  ));

  const BUCKETS = 30;
  const lo = 0, hi = 200; // ms scale; cells above hi clamp red
  function colorFor(v) {
    if (v == null || isNaN(v)) return 'var(--bg3)';
    if (v < 20)  return 'var(--up)';
    if (v < 60)  return 'color-mix(in srgb, var(--up) 60%, var(--warn))';
    if (v < 100) return 'var(--warn)';
    if (v < hi)  return 'color-mix(in srgb, var(--warn) 50%, var(--down))';
    return 'var(--down)';
  }
  function alphaFor(v) {
    if (v == null || isNaN(v)) return 0.3;
    return Math.min(1, 0.30 + Math.min(1, v / 120) * 0.70);
  }
  function downsample(samples) {
    if (!samples || !samples.length) return new Array(BUCKETS).fill(null);
    // Bucket-average across the time window. Failed samples (ok=false) count
    // as a red marker (240ms+) so outages show up dramatically.
    const tail = samples.slice(-BUCKETS * 8);
    const out = new Array(BUCKETS).fill(null);
    const step = Math.max(1, Math.floor(tail.length / BUCKETS));
    for (let i = 0; i < BUCKETS; i++) {
      const a = i * step;
      const b = (i === BUCKETS - 1) ? tail.length : Math.min(tail.length, a + step);
      let sum = 0, n = 0;
      for (let k = a; k < b; k++) {
        const p = tail[k];
        if (!p) continue;
        // Ping samples carry the latency in `ms`; failed probes (ok === false)
        // surface as 240ms (max-clamped red) to indicate the outage visually.
        let v;
        if (p.ok === false) v = 240;
        else if (p.ms != null) v = Number(p.ms);
        else if (p.value != null) v = Number(p.value);
        else continue;
        if (!isNaN(v)) { sum += v; n++; }
      }
      out[i] = n ? sum / n : null;
    }
    return out;
  }

  const rowsHtml = sensors.map((s, i) => {
    const dev = S.devices[s.device_id];
    if (!dev) return '';
    const series = downsample(results[i].samples);
    const cells = series.map(v =>
      `<div class="heatmap-cell" title="${v == null ? '—' : v.toFixed(1) + 'ms'}"
         style="background:${colorFor(v)};opacity:${alphaFor(v)}"></div>`
    ).join('');
    const shortName = (dev.name || dev.host || '').split('.')[0];
    return `<div class="dw-heat-row">
      <span class="dw-heat-name" title="${esc(dev.name || dev.host || '')}">${esc(shortName)}</span>
      <div class="heatmap" style="grid-template-columns:repeat(${BUCKETS},1fr)">${cells}</div>
    </div>`;
  }).filter(Boolean).join('');

  const html = `
    <div class="dw-heat-wrap">
      ${rowsHtml}
      <div class="heatmap-legend"><span>0ms</span><div class="grad"></div><span>${hi}ms+</span></div>
    </div>`;
  _dwHeatCache[wid] = { ts: Date.now(), cfgKey: `${limit}/${minutes}/${group}`, html };
  if (body.innerHTML !== html) body.innerHTML = html;
}

// ── Reset dashboard state (called on re-authentication) ──────────
function _dwReset() {
  _dwDataArrived = false;
  _dwDashboards = null;
  _dwActiveId   = null;
  _dwWidgets    = null;
  if (_dwTickTimer) { clearInterval(_dwTickTimer); _dwTickTimer = null; }
}

// ── Loading shimmer clear (called when first real data arrives) ───
let _dwDataArrived = false;
function _dwClearLoading() {
  // Always remove the class — idempotent, handles races where the shimmer is
  // added AFTER an earlier _dwClearLoading call (e.g. _dwRenderAll runs late).
  const removed = document.querySelectorAll('.dw-body.dw-loading');
  if (removed.length) {
    console.debug(`[pw:dw] clearLoading: removing shimmer from ${removed.length} widget(s)`);
    removed.forEach(el => el.classList.remove('dw-loading'));
  }
  if (_dwDataArrived) return;
  _dwDataArrived = true;
  console.debug('[pw:dw] clearLoading: first data arrival — refreshing all widgets');
  // Refresh all widgets with real data (only on first call)
  _dwLoad().forEach(w => {
    const reg = _DW_REG[w.type];
    if (reg) reg.refresh(w.id, w.cfg);
  });
}

// ── SSE hooks (called from app.js event handlers) ─────────────────
// Sensor-chart widgets are throttled to one fetch every 5s per (did,sid)
// because /history is a multi-minute range query — re-fetching on every
// 250ms SSE batch was both wasteful AND produced visible flicker when a
// stale request landed after a fresher one (and previously also passed an
// undefined `minutes` arg, returning "No data" between scheduled refreshes).
const _dwChartLastFetch = {};   // `${did}/${sid}` → epoch ms of last fetch
const _DW_CHART_MIN_GAP = 5000;

function _dwOnSensorUpdate(did, sid) {
  if (!_dwDataArrived) _dwClearLoading();
  if (activeMainTab !== 'dashboard') return;
  let topLatencyDirty = false;
  _dwLoad().forEach(w => {
    if (w.type === 'sensor_chart' && w.cfg.did === did && w.cfg.sid === sid) {
      const k = did + '/' + sid;
      const now = Date.now();
      if ((now - (_dwChartLastFetch[k] || 0)) >= _DW_CHART_MIN_GAP) {
        _dwChartLastFetch[k] = now;
        // Always pass the live global range — w.cfg.minutes is not maintained.
        _dwLoadSensorChart(w.id, did, sid, _dwTimeRangeMinutes());
      }
    }
    if (w.type === 'sensor_gauge' && w.cfg.did === did && w.cfg.sid === sid)
      _dwRefreshGauge(w.id, w.cfg);
    if (w.type === 'network_avail')    _dwRefreshNetAvail(w.id);
    if (w.type === 'top_latency')      topLatencyDirty = true;
    if (w.type === 'packet_loss')      _dwRefreshPacketLoss(w.id, w.cfg);
    if (w.type === 'internet_health')  _dwRefreshInternetHealth(w.id);
  });
  if (topLatencyDirty)
    _dwLoad().filter(w => w.type === 'top_latency').forEach(w => _dwRefreshTopLatency(w.id, w.cfg));
}
function _dwOnDeviceUpdate() {
  if (!_dwDataArrived) _dwClearLoading();
  if (activeMainTab !== 'dashboard') return;
  _dwLoad().forEach(w => {
    if (w.type === 'device_status')   _dwRefreshDeviceStatus(w.id);
    if (w.type === 'network_avail')   _dwRefreshNetAvail(w.id);
    if (w.type === 'down_devices')    _dwRefreshDownDevices(w.id);
    if (w.type === 'internet_health') _dwRefreshInternetHealth(w.id);
    if (w.type === 'fleet_status')    _dwRefreshFleetStatus(w.id);
  });
}
function _dwOnFlapEvent() {
  if (activeMainTab !== 'dashboard') return;
  _dwLoad().forEach(w => {
    if (w.type === 'flap_events') _dwRefreshFlapEvents(w.id, w.cfg);
    if (w.type === 'event_count') _dwRefreshEventCount(w.id);
    if (w.type === 'flap_detect') _dwRefreshFlapDetect(w.id, w.cfg);
  });
}
function _dwOnSensorUpdateExtra(did) {
  if (activeMainTab !== 'dashboard') return;
  _dwLoad().forEach(w => {
    if (w.type === 'top_latency') _dwRefreshTopLatency(w.id, w.cfg);
  });
}

// ── Widget: Network Availability History (24h) ────────────────────
function _dwRenderNetAvail(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  body.innerHTML = `
    <div class="dw-na-wrap">
      <div class="dw-na-pct" id="dw-na-pct-${wid}">—<span class="dw-na-sym">%</span></div>
      <div class="dw-na-lbl">sensors online</div>
      <div class="dw-na-pills" id="dw-na-pills-${wid}"></div>
      <div class="dw-na-total" id="dw-na-total-${wid}"></div>
    </div>
    <canvas id="dw-na-canvas-${wid}" class="dw-na-canvas" height="80"></canvas>`;
  _dwRefreshNetAvail(wid);
}

function _dwRefreshNetAvail(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  // If scaffold not built yet (e.g. fullscreen open before render), build it first
  if (!document.getElementById(`dw-na-pct-${wid}`)) { _dwRenderNetAvail(wid); return; }
  const sensors = Object.values(S.sensors);
  const total = sensors.length;
  const up    = sensors.filter(s => s.alive === true).length;
  const down  = sensors.filter(s => s.alive === false).length;
  const idle  = sensors.filter(s => s.alive == null).length;
  const pct   = total ? Math.round(up / total * 100) : 0;
  const color = pct >= 90 ? 'var(--up)' : pct >= 70 ? 'var(--warn)' : 'var(--down)';
  const pctEl   = document.getElementById(`dw-na-pct-${wid}`);
  const pillsEl = document.getElementById(`dw-na-pills-${wid}`);
  const totalEl = document.getElementById(`dw-na-total-${wid}`);
  if (pctEl)   { pctEl.style.color = color; pctEl.innerHTML = `${pct}<span class="dw-na-sym">%</span>`; }
  if (pillsEl) pillsEl.innerHTML = `
    <span class="dw-ds-pill up">${up} Up</span>
    <span class="dw-ds-pill down">${down} Down</span>
    ${idle ? `<span class="dw-ds-pill idle">${idle} Idle</span>` : ''}`;
  if (totalEl) totalEl.textContent = `${total} sensor${total !== 1 ? 's' : ''} monitored`;
  // Throttle: only redraw chart once per 30s regardless of SSE event rate.
  // Text stats (pct, pills, total) update every call above; only the fetch+canvas is throttled.
  const _now = Date.now();
  if (_now - (_dwRefreshNetAvail._last || 0) < 30000) return;
  _dwRefreshNetAvail._last = _now;
  _dwDrawNetAvailChart(wid); // fire-and-forget
}

// Cache last availability payload so theme-change redraws skip the network.
let _lastAvailability = null;

// On theme change, redraw every visible net-avail canvas synchronously with
// the cached data so colors flip instantly instead of waiting on a fetch.
window.addEventListener('themechange', () => {
  _dwRefreshNetAvail._last = 0;
  (_dwWidgets || []).forEach(w => {
    if (w.type === 'network_avail') _dwDrawNetAvailChart(w.id, true);
  });
});

async function _dwDrawNetAvailChart(wid, useCache) {
  const canvas = document.getElementById(`dw-na-canvas-${wid}`);
  if (!canvas) return;
  let availability;
  if (useCache && _lastAvailability) {
    availability = _lastAvailability;
  } else {
    try {
      const d = await _fetchAvailability();
      availability = d.availability || [];
      _lastAvailability = availability;
    } catch { return; }
  }
  canvas.width = canvas.offsetWidth || 340;
  const W = canvas.width, H = canvas.height || 80;
  const ctx = canvas.getContext('2d');
  // Theme-aware colors — read fresh each paint so we pick up toggles
  const _gv = (n, d) => (window.getCssVar ? getCssVar(n) : '') || d;
  const _gr = (n, d) => (window.getCssRgb ? getCssRgb(n) : null) || d;
  const bg     = _gv('--bg',    '#0d1117');
  const text3  = _gv('--text3', '#484f58');
  const text2  = _gr('--text2', [139,148,158]);
  const upRgb   = _gr('--up',   [35,209,139]);
  const warnRgb = _gr('--warn', [240,165,0]);
  const downRgb = _gr('--down', [248,81,73]);
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);
  if (!availability.length) {
    ctx.fillStyle = text3; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('No historical data yet', W / 2, H / 2);
    return;
  }
  const BOT = 14, TOP = 4;
  const plotW = W - 8, plotH = H - TOP - BOT;
  const now = Date.now() / 1000, winStart = now - 1440 * 60;
  const xOf = ts  => 4 + ((ts - winStart) / (1440 * 60)) * plotW;
  const yOf = pct => TOP + plotH - (Math.min(100, Math.max(0, pct)) / 100) * plotH;
  const avgPct = availability.reduce((s, b) => s + b.pct, 0) / availability.length;
  const lineRgb = avgPct >= 90 ? upRgb : avgPct >= 70 ? warnRgb : downRgb;
  const lineColor = `rgb(${lineRgb.join(',')})`;
  const fillAlpha = `rgba(${lineRgb.join(',')},.18)`;
  const pts = availability.map(b => ({ x: xOf(b.ts + 1800), y: yOf(b.pct) }));
  if (pts.length < 2) return;
  // Filled area
  const g = ctx.createLinearGradient(0, TOP, 0, H - BOT);
  g.addColorStop(0, fillAlpha); g.addColorStop(1, `rgba(${lineRgb.join(',')},0)`);
  ctx.beginPath();
  ctx.moveTo(pts[0].x, H - BOT);
  pts.forEach(p => ctx.lineTo(p.x, p.y));
  ctx.lineTo(pts[pts.length - 1].x, H - BOT);
  ctx.closePath(); ctx.fillStyle = g; ctx.fill();
  // Line
  ctx.beginPath();
  pts.forEach((p, i) => i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y));
  ctx.strokeStyle = lineColor; ctx.lineWidth = 1.5; ctx.stroke();
  // X-axis time labels every 6h
  const startHour = Math.ceil(winStart / 3600) * 3600;
  ctx.fillStyle = `rgba(${text2.join(',')},.6)`; ctx.font = '8px sans-serif'; ctx.textAlign = 'center';
  for (let ts = startHour; ts <= now; ts += 6 * 3600) {
    const x = xOf(ts);
    if (x < 14 || x > W - 14) continue;
    const d = new Date(ts * 1000);
    ctx.fillText(d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), x, H - 2);
  }
}

// ── Widget: Sensor Gauge ──────────────────────────────────────────
function _dwRefreshGauge(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const s = S.sensors[`${cfg.did}/${cfg.sid}`];
  if (!s) { body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px">Sensor not found</div>'; return; }
  const st  = s.alive === true ? 'up' : s.alive === false ? 'down' : 'idle';
  const ms  = s.last_ms != null ? s.last_ms : s.value;
  const val = ms != null ? (Number.isInteger(ms) ? ms : ms.toFixed(1)) : '—';
  const unit = s.stype === 'tls' ? 'd' : 'ms';
  const color = st === 'up' ? 'var(--up)' : st === 'down' ? 'var(--down)' : st === 'warning' ? 'var(--warn)' : 'var(--text3)';
  const typeIco = {ping:'◉',tcp:'⇌',http:'◈',snmp:'◎',dns:'⬡',tls:'🔒',http_keyword:'K',banner:'B'}[s.stype] || '·';
  _dwSwap(body, `
    <div class="dw-gauge-wrap">
      <div class="dw-gauge-ring" style="--gc:${color}">
        <div class="dw-gauge-val" style="color:${color}">${val}<span class="dw-gauge-unit">${unit}</span></div>
      </div>
      <div class="dw-gauge-name">${esc(s.name)}</div>
      <div class="dw-gauge-st" style="color:${color}">${typeIco} ${st.toUpperCase()}</div>
    </div>`);
}

// ── Widget: Recent Flap Events ────────────────────────────────────
function _dwRefreshFlapEvents(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const limit = cfg?.limit || 25;
  const items = (typeof FLAPS !== 'undefined' ? FLAPS : []).slice(0, limit);
  if (!items.length) {
    body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;text-align:center">No events yet</div>';
    return;
  }
  const rows = items.map(d => {
    const dir = d._direction || 'down';
    let dotColor = 'var(--down)', label = 'DOWN';
    if (dir === 'recovered') { dotColor = 'var(--up)'; label = 'RECOVERED'; }
    else if (dir === 'trap')  { dotColor = '#8e44ad';  label = 'TRAP'; }
    else if (dir === 'threshold') {
      const isCrit = d._thr_level === 'crit';
      dotColor = isCrit ? 'var(--down)' : 'var(--warn)';
      label = isCrit ? 'CRIT' : 'WARN';
    }
    const _dtRaw = new Date(d.ts || '');
    const ts = isNaN(_dtRaw.getTime()) ? (d.ts || '').slice(11, 19) :
        `${String(_dtRaw.getUTCMonth()+1).padStart(2,'0')}-${String(_dtRaw.getUTCDate()).padStart(2,'0')} ${String(_dtRaw.getUTCHours()).padStart(2,'0')}:${String(_dtRaw.getUTCMinutes()).padStart(2,'0')}`;
    const name = d.sname || d.dname || '';
    return `<div class="dw-fe-row">
      <span class="dw-fe-dot" style="background:${dotColor}"></span>
      <span class="dw-fe-ts">${esc(ts)}</span>
      <span class="dw-fe-lbl dw-fe-${dir}">${label}</span>
      <span class="dw-fe-name">${esc(name)}</span>
    </div>`;
  }).join('');
  _dwSwap(body, `<div class="dw-fe-list">${rows}</div>`);
}

// ── Widget: System Status ─────────────────────────────────────────
function _dwRenderSystemStatus(wid) {
  _dwFetchSystemStatus(wid);
}

async function _dwFetchSystemStatus(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;

  let info = {};
  try { info = await (await fetch('/api/server_info')).json(); } catch {}

  // Guard: if fetch returned empty/zero data, don't overwrite a working display
  const hasData = info.version || info.uptime_s || info.devices;
  const alreadyRendered = !!document.getElementById(`dw-ss-uptime-${wid}`);
  if (alreadyRendered && !hasData) return;

  const _fmt  = b => b ? (b / 1048576).toFixed(2) + ' MB' : '—';
  const dbMB     = _fmt(info.db_size_bytes);
  const logsDbMB = _fmt(info.logs_db_size_bytes);
  const logMB    = _fmt(info.log_size_bytes);
  const _fmtUp = s => {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
  };
  const _fmtDT = () => {
    const n = new Date();
    const d = String(n.getDate()).padStart(2,'0'), mo = String(n.getMonth()+1).padStart(2,'0'), y = n.getFullYear();
    const h = String(n.getHours()).padStart(2,'0'), mi = String(n.getMinutes()).padStart(2,'0'), s = String(n.getSeconds()).padStart(2,'0');
    return `${d}/${mo}/${y}  ${h}:${mi}:${s}`;
  };

  if (alreadyRendered) {
    // ── Refresh path: targeted updates only — uptime ticker keeps running untouched ──
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set(`dw-ss-ver-${wid}`,  `v${info.version || '—'}`);
    set(`dw-ss-devs-${wid}`, info.devices || 0);
    set(`dw-ss-sens-${wid}`, info.sensors  || 0);
    set(`dw-ss-db-${wid}`,     dbMB);
    set(`dw-ss-logsdb-${wid}`, logsDbMB);
    set(`dw-ss-log-${wid}`,    logMB);
    return;
  }

  // ── First render: build full HTML + start live ticker ──
  let uptimeSecs = info.uptime_s || 0;
  body.innerHTML = `
    <div class="dw-ss-rows">
      <div class="dw-ss-row"><span class="dw-ss-lbl">Version</span>
        <span class="dw-ss-val accent" id="dw-ss-ver-${wid}">v${esc(info.version||'—')}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Date / Time</span>
        <span class="dw-ss-val" id="dw-ss-time-${wid}" style="font-variant-numeric:tabular-nums">${_fmtDT()}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Uptime</span>
        <span class="dw-ss-val" id="dw-ss-uptime-${wid}">${_fmtUp(uptimeSecs)}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Devices</span>
        <span class="dw-ss-val" id="dw-ss-devs-${wid}">${info.devices||0}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Sensors</span>
        <span class="dw-ss-val" id="dw-ss-sens-${wid}">${info.sensors||0}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Main DB</span>
        <span class="dw-ss-val" id="dw-ss-db-${wid}">${dbMB}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Logs DB</span>
        <span class="dw-ss-val" id="dw-ss-logsdb-${wid}">${logsDbMB}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Log Folder</span>
        <span class="dw-ss-val" id="dw-ss-log-${wid}">${logMB}</span></div>
    </div>`;
  // Live ticker — uptime counts up + date/time updates every second, no API calls needed
  const card = body.closest('.dw-card');
  if (card) {
    if (card._interval) clearInterval(card._interval);
    card._interval = setInterval(() => {
      const el = document.getElementById(`dw-ss-uptime-${wid}`);
      if (!el) { clearInterval(card._interval); card._interval = null; return; }
      uptimeSecs++;
      el.textContent = _fmtUp(uptimeSecs);
      const tel = document.getElementById(`dw-ss-time-${wid}`);
      if (tel) tel.textContent = _fmtDT();
    }, 1000);
  }
}

// ── Widget: Down & Warning Devices ────────────────────────────────
function _dwRefreshDownDevices(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const bad = Object.values(S.devices).filter(d => d.status === 'down' || d.status === 'warn');
  if (!bad.length) {
    body.innerHTML = '<div class="dw-dd-ok">✓ All devices healthy</div>';
    return;
  }
  const rows = bad.map(d => {
    const st = d.status;
    const failSensors = Object.values(S.sensors)
      .filter(s => s.device_id === d.device_id && s.alive === false)
      .map(s => esc(s.name)).join(', ');
    return `<div class="dw-dd-row">
      <span class="dw-ds-dot ${st}"></span>
      <div class="dw-dd-info">
        <span class="dw-dd-name">${esc(d.name)}</span>
        <span class="dw-dd-host">${esc(d.host||'')}</span>
        ${failSensors ? `<span class="dw-dd-sensors">${failSensors}</span>` : ''}
      </div>
      <span class="dw-ds-st ${st}">${st}</span>
    </div>`;
  }).join('');
  _dwSwap(body, `<div class="dw-dd-list">${rows}</div>`);
}

// ── Widget: Active Incidents (Root Cause) ─────────────────────────
// Compact epoch-seconds → "4m" / "2h" duration.
function _dwIncDur(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (s < 90)     return s + 's';
  if (s < 5400)   return Math.round(s / 60) + 'm';
  if (s < 172800) return Math.round(s / 3600) + 'h';
  return Math.round(s / 86400) + 'd';
}
async function _dwRefreshIncidents(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  let data = null;
  try {
    const r = await fetch('/api/incidents');
    if (r.ok) data = await r.json();
  } catch {}
  if (!data) {
    body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;text-align:center">Unavailable</div>';
    return;
  }
  // Only correlated incidents (a root with ≥1 downstream victim) are "root
  // causes" worth surfacing here; lone downs live in the Down Devices widget.
  const incidents = (data.incidents || []).filter(i => i.impacted_count > 0);
  if (!incidents.length) {
    body.innerHTML = '<div class="dw-dd-ok">✓ No correlated outages</div>';
    return;
  }
  const confLabel = { high: 'high', medium: 'likely', low: 'possible' };
  const rows = incidents.map(i => {
    const r = i.root;
    const tier = (r.tier || '').replace(/_/g, ' ');
    const reasons = (i.reasons || []).map(x => esc(x)).join(' · ');
    const shown = i.impacted.slice(0, 12).map(c => esc(c.name)).join(', ');
    const more = i.impacted_count > 12 ? ` +${i.impacted_count - 12} more` : '';
    const sub = [tier, r.site, r.down_since ? 'down ' + _dwIncDur(r.down_since) : '']
                  .filter(Boolean).map(esc).join(' · ');
    return `<div class="dw-inc">
      <div class="dw-inc-head" onclick="this.parentElement.classList.toggle('open')">
        <span class="dw-ds-dot down"></span>
        <div class="dw-inc-info">
          <span class="dw-inc-name">${esc(r.name)}</span>
          <span class="dw-inc-sub">${sub}</span>
        </div>
        <span class="dw-inc-conf dw-inc-conf-${esc(i.confidence)}" title="Root-cause confidence">${confLabel[i.confidence] || i.confidence}</span>
        <span class="dw-inc-count">${i.impacted_count}</span>
        <span class="dw-inc-chev">▸</span>
      </div>
      <div class="dw-inc-detail">
        ${reasons ? `<div class="dw-inc-reasons">${reasons}</div>` : ''}
        <div class="dw-inc-impacted"><b>Impacted:</b> ${shown}${more}</div>
      </div>
    </div>`;
  }).join('');
  _dwSwap(body, `<div class="dw-inc-list">${rows}</div>`);
}

// ── Widget: Slowest Ping Devices ──────────────────────────────────
function _dwRefreshTopLatency(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const limit = Number(cfg?.limit) || 10;
  // Collect ping sensors with valid last_ms, group by device, take max per device
  const byDevice = {};
  Object.values(S.sensors).forEach(s => {
    if (s.stype !== 'ping' || s.last_ms == null) return;
    const ms = Number(s.last_ms);
    if (!byDevice[s.device_id] || ms > byDevice[s.device_id].ms)
      byDevice[s.device_id] = { ms, did: s.device_id };
  });
  const sorted = Object.values(byDevice).sort((a, b) => b.ms - a.ms).slice(0, limit);
  if (!sorted.length) {
    body.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;text-align:center">No ping data yet</div>';
    return;
  }
  const maxMs = sorted[0].ms || 1;
  const rows = sorted.map(({ ms, did }) => {
    const dev = S.devices[did];
    if (!dev) return '';
    const pct = Math.min(100, Math.round(ms / maxMs * 100));
    const cls = typeof msC === 'function' ? msC(ms, {}) : (ms > 200 ? 'b' : ms > 50 ? 'w' : 'g');
    const colorMap = { g: 'var(--up)', w: 'var(--warn)', b: 'var(--down)', m: 'var(--text3)' };
    const color = colorMap[cls] || 'var(--text2)';
    return `<div class="dw-tl-row">
      <span class="dw-tl-name">${esc(dev.name)}</span>
      <div class="dw-tl-bar-wrap">
        <div class="dw-tl-bar" style="width:${pct}%;background:${color}"></div>
      </div>
      <span class="dw-tl-val" style="color:${color}">${ms}ms</span>
    </div>`;
  }).join('');
  _dwSwap(body, `<div class="dw-tl-list">${rows}</div>`);
}

// ── Widget: Event Summary ─────────────────────────────────────────
async function _dwRefreshEventCount(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const periods = [
    { label: 'Last 1 h',  key: '1h'  },
    { label: 'Last 24 h', key: '24h' },
    { label: 'Last 7 d',  key: '7d'  },
  ];
  const types = [
    { key: 'down',      label: 'Down',      color: 'var(--down)' },
    { key: 'recovered', label: 'Recovered', color: 'var(--up)'   },
    { key: 'threshold', label: 'Threshold', color: 'var(--warn)' },
    { key: 'trap',      label: 'SNMP Trap', color: '#a855f7'     },
  ];
  // Fetch counts directly from server (uses server-side time, unaffected by
  // client-side FLAPS array size or load-order race conditions)
  let summary = null;
  try {
    const r = await fetch('/api/events/summary');
    if (r.ok) summary = (await r.json()).summary;
  } catch {}
  function getCount(periodKey, typeKey) {
    if (summary && summary[periodKey]) return summary[periodKey][typeKey] || 0;
    // Fallback: count from client-side FLAPS if API is unavailable
    const flaps = (typeof FLAPS !== 'undefined' ? FLAPS : []);
    const msMap = { '1h': 3600000, '24h': 86400000, '7d': 604800000 };
    const since = Date.now() - (msMap[periodKey] || 86400000);
    return flaps.filter(f => {
      const t = new Date(f.ts).getTime();
      if (isNaN(t) || t < since) return false;
      return !typeKey || f._direction === typeKey;
    }).length;
  }
  const headerRow = `<div class="dw-ec-row dw-ec-hdr">
    <span class="dw-ec-lbl"></span>
    ${periods.map(p => `<span class="dw-ec-cell dw-ec-period">${p.label}</span>`).join('')}
  </div>`;
  const dataRows = types.map(t => `
    <div class="dw-ec-row">
      <span class="dw-ec-lbl" style="color:${t.color}">${t.label}</span>
      ${periods.map(p => {
        const n = getCount(p.key, t.key);
        return `<span class="dw-ec-cell" style="color:${n ? t.color : 'var(--text3)'}">${n}</span>`;
      }).join('')}
    </div>`).join('');
  const totalRow = `<div class="dw-ec-row dw-ec-total">
    <span class="dw-ec-lbl">Total</span>
    ${periods.map(p => {
      const n = types.reduce((s, t) => s + getCount(p.key, t.key), 0);
      return `<span class="dw-ec-cell">${n}</span>`;
    }).join('')}
  </div>`;
  _dwSwap(body, `<div class="dw-ec-table">${headerRow}${dataRows}${totalRow}</div>`);
}

// ── Widget: Packet Loss ───────────────────────────────────────────
// Packet-loss leaderboard cache — a full O(sensors) scan + sort per widget per
// SSE batch is wasteful for a UI that doesn't need 4 Hz freshness. 5 s TTL
// keyed by (threshold, limit) so widgets with different configs don't collide.
const _plCache  = new Map();
const _PL_TTL_MS = 5000;
function _plLossySensors(threshold, limit) {
  const key = `${threshold}:${limit}`;
  const now = Date.now();
  const hit = _plCache.get(key);
  if (hit && now - hit.ts < _PL_TTL_MS) return hit.lossy;
  const lossy = Object.values(S.sensors)
    .filter(s => s.stype === 'ping' && s.loss_pct != null && s.loss_pct >= threshold)
    .sort((a, b) => b.loss_pct - a.loss_pct)
    .slice(0, limit);
  _plCache.set(key, {ts: now, lossy});
  return lossy;
}

function _dwRefreshPacketLoss(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const limit     = Number(cfg?.limit)     || 10;
  const threshold = Number(cfg?.threshold) || 1;
  const lossy = _plLossySensors(threshold, limit);
  if (!lossy.length) {
    body.innerHTML = '<div class="dw-pl-ok">✓ No packet loss</div>';
    return;
  }
  const maxLoss = lossy[0].loss_pct || 1;
  const rows = lossy.map(s => {
    const dev   = S.devices[s.device_id];
    const barW  = Math.min(100, Math.round(s.loss_pct / maxLoss * 100));
    const color = s.loss_pct >= 20 ? 'var(--down)' : s.loss_pct >= 5 ? 'var(--warn)' : 'var(--warn)';
    return `<div class="dw-tl-row">
      <span class="dw-tl-name">${esc(dev?.name || s.device_id)}</span>
      <div class="dw-tl-bar-wrap"><div class="dw-tl-bar" style="width:${barW}%;background:${color}"></div></div>
      <span class="dw-tl-val" style="color:${color}">${s.loss_pct}%</span>
    </div>`;
  }).join('');
  _dwSwap(body, `<div class="dw-tl-list">${rows}</div>`);
}

// ── Widget: SLA Report ────────────────────────────────────────────
function _dwRenderSLA(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  body.innerHTML = `<div class="dw-sla-wrap" id="dw-sla-${wid}"><div class="dw-sla-loading">Loading…</div></div>`;
  _dwFetchSLA(wid, cfg);
}

async function _dwFetchSLA(wid, cfg) {
  const wrap = document.getElementById(`dw-sla-${wid}`);
  if (!wrap) return;
  const did  = cfg?.did;
  const sid  = cfg?.sid;
  const mins = _dwTimeRangeMinutes();   // follows global dashboard range
  if (!did || !sid) {
    wrap.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px">Select a device and sensor.</div>';
    return;
  }
  let summary = [];
  try {
    const r = await fetch(`/api/device/${did}/sensor/${sid}/summary?minutes=${mins}`);
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    summary = d.summary || [];
  } catch {
    wrap.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px;text-align:center">⟳ Waiting for data…</div>';
    return;
  }
  const totalOk   = summary.reduce((s, b) => s + (b.ok   || 0), 0);
  const totalFail = summary.reduce((s, b) => s + (b.fail || 0), 0);
  const totalAll  = totalOk + totalFail;
  if (!totalAll) {
    wrap.innerHTML = '<div style="color:var(--text3);font-size:11px;padding:8px">No data for this period.</div>';
    return;
  }
  const slaPct  = totalOk / totalAll * 100;
  const target  = 99;
  const delta   = slaPct - target;
  // crit only when more than 1 point below target or actively losing samples
  const state   = delta >= 0.5 ? 'ok' : delta >= -1 ? 'warn' : 'crit';
  const arrow   = delta >= 0 ? '▲' : '▼';
  const dtSec = Math.round(totalFail / totalAll * mins * 60);
  const dtH   = Math.floor(dtSec / 3600);
  const dtM   = String(Math.floor((dtSec % 3600) / 60)).padStart(2, '0');
  _dwSwap(wrap, `
    <div class="dw-sla-pct">${slaPct.toFixed(3)}<span class="dw-sla-sym">%</span></div>
    <div class="dw-sla-delta" data-state="${state}">${arrow} ${Math.abs(delta).toFixed(2)} from ${target}% target</div>
    <div class="dw-sla-bar-wrap"><div class="dw-sla-bar" data-state="${state}" style="width:${Math.min(100,slaPct).toFixed(2)}%"></div></div>
    <div class="dw-sla-stats">
      <span><span class="dw-sla-key">Downtime</span>${dtH}h ${dtM}m</span>
      <span><span class="dw-sla-key">Samples</span>${totalAll}</span>
    </div>`);
}

// ── Widget: Flapping Devices ──────────────────────────────────────
function _dwTimeAgo(epochMs) {
  const s = Math.round((Date.now() - epochMs) / 1000);
  if (s < 60) return s + 's ago';
  const m = Math.round(s / 60);
  if (m < 60) return m + 'm ago';
  return Math.round(m / 60) + 'h ago';
}

function _dwRefreshFlapDetect(wid, cfg) {
  const body      = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const windowH   = Math.max(1, _dwTimeRangeMinutes() / 60);   // follows global range, hours
  const minFlaps  = Number(cfg?.min_flaps) || 3;
  const cutoff    = Date.now() - windowH * 3600 * 1000;
  const allFlaps  = (typeof FLAPS !== 'undefined' ? FLAPS : []);
  const recent    = allFlaps.filter(f => {
    const t = new Date(f.ts).getTime();
    return !isNaN(t) && t >= cutoff &&
      (f._direction === 'down' || f._direction === 'recovered');
  });
  const byDev = {};
  recent.forEach(f => {
    if (!byDev[f.did]) byDev[f.did] = { count: 0, lastMs: 0, dname: f.dname };
    byDev[f.did].count++;
    const t = new Date(f.ts).getTime();
    if (t > byDev[f.did].lastMs) byDev[f.did].lastMs = t;
  });
  const flapping = Object.values(byDev)
    .filter(d => d.count >= minFlaps)
    .sort((a, b) => b.count - a.count);
  if (!flapping.length) {
    body.innerHTML = '<div class="dw-fd-ok">✓ No flapping devices</div>';
    return;
  }
  const rows = flapping.map(d => `
    <div class="dw-fd-row">
      <span class="dw-ds-dot down"></span>
      <div class="dw-fd-info">
        <span class="dw-fd-name">${esc(d.dname)}</span>
        <span class="dw-fd-meta">${d.count} flaps · last ${_dwTimeAgo(d.lastMs)}</span>
      </div>
      <span class="dw-fd-cnt">${d.count}</span>
    </div>`).join('');
  _dwSwap(body, `<div class="dw-fd-list">${rows}</div>`);
}

// ── Widget: Internet Health ───────────────────────────────────────
function _dwIsPrivate(host) {
  if (!host) return true;
  const h = host.split(':')[0].toLowerCase();
  if (h === 'localhost' || h === '::1' || h === '127.0.0.1') return true;
  if (h.endsWith('.local')) return true;
  if (/^10\./.test(h)) return true;
  if (/^192\.168\./.test(h)) return true;
  if (/^169\.254\./.test(h)) return true;
  const m = h.match(/^172\.(\d+)\./);
  if (m && Number(m[1]) >= 16 && Number(m[1]) <= 31) return true;
  return false;
}

function _dwRefreshInternetHealth(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  // Throttle: SSE fires this on every sensor update; health status changes rarely.
  const _now = Date.now();
  if (_now - (body._ihLastRefresh || 0) < 5000) return;
  body._ihLastRefresh = _now;
  const external = Object.values(S.sensors).filter(s => {
    let target = s.host;
    if (s.stype === 'http' && s.url) { try { target = new URL(s.url).hostname; } catch {} }
    return !_dwIsPrivate(target);
  });
  if (!external.length) {
    body.innerHTML = `<div class="dw-ih-wrap">
      <div class="dw-ih-badge idle">NO EXTERNAL SENSORS</div>
      <div class="dw-ih-sub">No external targets detected</div>
    </div>`;
    return;
  }
  const up    = external.filter(s => s.alive === true).length;
  const down  = external.filter(s => s.alive === false).length;
  const idle  = external.filter(s => s.alive == null).length;
  const total = external.length;
  const allUp = down === 0 && idle === 0;
  const badgeCls = allUp ? 'healthy' : down > 0 ? 'down' : 'degraded';
  const badgeTxt = allUp ? 'HEALTHY' : down > 0 ? 'DOWN' : 'DEGRADED';
  const failed = external.filter(s => s.alive === false);
  const failRows = failed.map(s => {
    const dev = S.devices[s.device_id];
    let target = s.host;
    if (s.stype === 'http' && s.url) { try { target = new URL(s.url).hostname; } catch {} }
    return `<div class="dw-ih-fail-row">
      <span class="dw-ds-dot down"></span>
      <span class="dw-ih-fail-name">${esc(dev?.name || s.device_id)}</span>
      <span class="dw-ih-fail-host">${esc(target || '')}</span>
    </div>`;
  }).join('');
  _dwSwap(body, `<div class="dw-ih-wrap">
    <div class="dw-ih-badge ${badgeCls}">${badgeTxt}</div>
    <div class="dw-ih-counts">
      <span style="color:var(--up)">${up} up</span> ·
      <span style="color:var(--down)">${down} down</span> ·
      <span style="color:var(--text3)">${idle} idle</span>
      <span style="color:var(--text3);font-size:10px"> / ${total} external</span>
    </div>
    ${failed.length ? `<div class="dw-ih-fail-list">${failRows}</div>` : ''}
  </div>`);
}

// ── Widget: Server Performance ───────────────────────────────────
function _dwRenderServerPerf(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  body.innerHTML = '<div class="dw-loading">Loading…</div>';
  _dwFetchServerPerf(wid);
}

async function _dwFetchServerPerf(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  let d = null;
  try {
    const r = await fetch('/api/system/perf');
    d = await r.json();
  } catch {}
  if (!d || d.error) {
    const msg = d?.error || 'Failed to load';
    body.innerHTML = `<div class="dw-err">${esc(msg)}</div>`;
    return;
  }
  const _fmtBytes = b => {
    if (b >= 1073741824) return (b / 1073741824).toFixed(1) + ' GB';
    return (b / 1048576).toFixed(0) + ' MB';
  };
  const _gauge = (pct, label, detail) => {
    const color = pct >= 90 ? 'var(--down)' : pct >= 70 ? 'var(--warn)' : 'var(--up)';
    return `<div class="dw-sp-row">
      <div class="dw-sp-hdr">
        <span class="dw-sp-lbl">${label}</span>
        <span class="dw-sp-pct" style="color:${color}">${pct}%</span>
      </div>
      <div class="dw-sp-bar-wrap">
        <div class="dw-sp-bar" style="width:${Math.min(pct,100)}%;background:${color}"></div>
      </div>
      <span class="dw-sp-detail">${detail}</span>
    </div>`;
  };
  const ramDetail  = `${_fmtBytes(d.ram_used)} / ${_fmtBytes(d.ram_total)}`;
  const diskDetail = `${_fmtBytes(d.disk_used)} / ${_fmtBytes(d.disk_total)}`;
  _dwSwap(body, `<div class="dw-sp-list">
    ${_gauge(d.cpu_pct,  'CPU',  `${d.cpu_pct}%`)}
    ${_gauge(d.ram_pct,  'RAM',  ramDetail)}
    ${_gauge(d.disk_pct, 'Disk', diskDetail)}
  </div>`);
}

// ── Backup Status Widget (device configs + DB scheduled backup) ──
async function _dwNcmStatusRefresh(wid) {
  const el = document.getElementById(`dw-body-${wid}`);
  if (!el) return;
  // Don't show "Loading…" on refresh — only on first render
  if (!el.children.length) el.innerHTML = '<div class="dw-loading">Loading…</div>';
  try {
    const [bk, cfg] = await Promise.all([
      api('GET', '/api/backups'),
      api('GET', '/api/settings'),
    ]);
    const devs = (bk.devices || []).filter(d => !d.orphaned);
    const total   = devs.length;
    const enabled = devs.filter(d => d.enabled).length;
    const ok      = devs.filter(d => d.last_success === true).length;
    const failed  = devs.filter(d => d.run_count > 0 && d.last_success === false).length;
    const never   = devs.filter(d => d.run_count === 0 && d.enabled).length;
    _dwSwap(el, `
      <div class="dw-bk-section-lbl">Device Configs</div>
      <div class="dw-ncm-grid">
        <div class="dw-ncm-kpi dw-ncm-ok">
          <span class="dw-ncm-n">${ok}</span>
          <span class="dw-ncm-l">OK</span>
        </div>
        <div class="dw-ncm-kpi dw-ncm-fail">
          <span class="dw-ncm-n">${failed}</span>
          <span class="dw-ncm-l">Failed</span>
        </div>
        <div class="dw-ncm-kpi dw-ncm-never">
          <span class="dw-ncm-n">${never}</span>
          <span class="dw-ncm-l">Never run</span>
        </div>
        <div class="dw-ncm-kpi dw-ncm-total">
          <span class="dw-ncm-n">${enabled}/${total}</span>
          <span class="dw-ncm-l">Enabled</span>
        </div>
      </div>
      ${_dwRenderDbBackup(cfg)}`);
  } catch {
    el.innerHTML = '<div class="dw-err">Failed to load backup status</div>';
  }
}

function _dwRenderDbBackup(cfg) {
  const enabled = !!cfg.db_backup_enabled;
  const lastTs  = cfg.db_backup_last_ts || '';
  const lastRes = cfg.db_backup_last_result || '';
  const remoteEnabled = !!cfg.db_backup_remote_enabled;
  const remoteTs      = cfg.db_backup_remote_last_ts || '';
  const remoteRes     = cfg.db_backup_remote_last_result || '';

  let statusClass = 'dw-db-muted';
  let mainLine = '';
  if (!enabled) {
    mainLine = 'Disabled';
  } else if (!lastTs) {
    mainLine = 'Scheduled, never run yet';
  } else {
    const ago = _dwAgoFromStampUnderscore(lastTs);
    const failed = (lastRes || '').toLowerCase().startsWith('error');
    const overdue = enabled && _dwIsDbBackupOverdue(lastTs, cfg.db_backup_freq || 'daily');
    if (failed) {
      statusClass = 'dw-db-fail';
      mainLine = `Last: ${ago} \u2718  ${esc(lastRes)}`;
    } else if (overdue) {
      statusClass = 'dw-db-warn';
      mainLine = `Last: ${ago} \u26A0 overdue`;
    } else {
      statusClass = 'dw-db-ok';
      mainLine = `Last: ${ago} \u2714`;
    }
  }

  let nextLine = '';
  if (enabled) {
    const next = _dwNextDbBackupLabel(cfg);
    if (next) nextLine = `<span class="dw-db-next">Next: ${esc(next)}</span>`;
  }

  let remoteLine = '';
  if (remoteEnabled) {
    const rType = (cfg.db_backup_remote_type || 'sftp').toUpperCase();
    if (!remoteTs && !remoteRes) {
      remoteLine = `<div class="dw-db-remote dw-db-muted">Remote (${esc(rType)}): not yet run</div>`;
    } else if ((remoteRes || '').toLowerCase().startsWith('error')) {
      remoteLine = `<div class="dw-db-remote dw-db-fail">Remote (${esc(rType)}): \u2718 ${esc(remoteRes)}</div>`;
    } else if (remoteTs) {
      remoteLine = `<div class="dw-db-remote dw-db-ok">Remote (${esc(rType)}): \u2714 uploaded ${esc(_dwAgoFromStampUnderscore(remoteTs))}</div>`;
    }
  }

  return `
    <div class="dw-bk-section-lbl" style="margin-top:10px">Database</div>
    <div class="dw-db-card" onclick="openSettings('database')" title="Open Database settings">
      <div class="dw-db-main ${statusClass}">
        <span>${mainLine}</span>
        ${nextLine}
      </div>
      ${remoteLine}
    </div>`;
}

// "2026-04-18_03-00-00" (db_backup_last_ts format) → "3h ago" style string.
function _dwAgoFromStampUnderscore(stamp) {
  if (!stamp) return 'never';
  // Convert underscore/dash format to ISO-ish
  const m = String(stamp).match(/^(\d{4})-(\d{2})-(\d{2})[_T](\d{2})-(\d{2})-(\d{2})$/);
  if (!m) return esc(stamp);
  const dt = new Date(Date.UTC(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]));
  // The stored timestamp is local server time; treat it as local by re-interpreting
  const local = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]);
  const diff = (Date.now() - local.getTime()) / 1000;
  if (diff < 0) return 'just now';
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

// Overdue = 1.5× the scheduled interval since last run (daily=36h, weekly=12d).
function _dwIsDbBackupOverdue(lastTs, freq) {
  const m = String(lastTs).match(/^(\d{4})-(\d{2})-(\d{2})[_T](\d{2})-(\d{2})-(\d{2})$/);
  if (!m) return false;
  const last = new Date(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6]);
  const diffSec = (Date.now() - last.getTime()) / 1000;
  const graceSec = (freq === 'weekly') ? 12 * 86400 : 36 * 3600;
  return diffSec > graceSec;
}

// Compute a human "Next: Sat 03:00" label for daily/weekly schedules.
function _dwNextDbBackupLabel(cfg) {
  const freq = cfg.db_backup_freq || 'daily';
  const timeStr = cfg.db_backup_time || '03:00';
  const [hh, mm] = timeStr.split(':').map(n => parseInt(n, 10) || 0);
  const now = new Date();
  const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const fmtClock = () => `${String(hh).padStart(2,'0')}:${String(mm).padStart(2,'0')}`;
  if (freq === 'daily') {
    const next = new Date(now); next.setHours(hh, mm, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    const sameDay = next.toDateString() === now.toDateString();
    return sameDay ? `Today ${fmtClock()}` :
           next.getDate() === now.getDate() + 1 ? `Tomorrow ${fmtClock()}` :
           `${dayNames[next.getDay()]} ${fmtClock()}`;
  }
  // weekly
  const daysStr = String(cfg.db_backup_days || '1,2,3,4,5,6,7');
  const days = new Set(daysStr.split(',').map(s => parseInt(s.trim(), 10)).filter(Boolean));
  if (!days.size) return '';
  // weekday(): 0=Sun .. 6=Sat; project uses 1=Mon .. 7=Sun
  const jsToProj = (d) => d === 0 ? 7 : d;
  for (let offset = 0; offset < 8; offset++) {
    const cand = new Date(now);
    cand.setDate(now.getDate() + offset);
    cand.setHours(hh, mm, 0, 0);
    if (cand <= now) continue;
    if (days.has(jsToProj(cand.getDay()))) {
      const sameDay = cand.toDateString() === now.toDateString();
      return sameDay ? `Today ${fmtClock()}` :
             offset === 1 ? `Tomorrow ${fmtClock()}` :
             `${dayNames[cand.getDay()]} ${fmtClock()}`;
    }
  }
  return '';
}

// ── License Overview Widget ──────────────────────────────────────
async function _dwLicenseOverviewRefresh(wid) {
  const el = document.getElementById(`dw-body-${wid}`);
  if (!el) return;
  if (!el.children.length) el.innerHTML = '<div class="dw-loading">Loading…</div>';
  try {
    const [sumR, licR] = await Promise.all([
      fetch('/api/licenses/summary'),
      fetch('/api/licenses'),
    ]);
    if (!sumR.ok || !licR.ok) throw new Error();
    const sum = await sumR.json();
    const { licenses } = await licR.json();
    const expiring = (licenses || []).filter(l => l.last_status === 'warn' || l.last_status === 'crit');
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const rows = expiring.map(l => {
      const devName = S.devices[l.did]?.name || l.did;
      const exp = new Date(l.expiry_date + 'T00:00:00');
      const daysLeft = Math.round((exp - today) / 86400000);
      const badge = l.last_status === 'crit'
        ? '<span class="ipam-lic-crit">Expired</span>'
        : '<span class="ipam-lic-warn">Expiring</span>';
      const daysStr = daysLeft < 0
        ? `${Math.abs(daysLeft)}d ago`
        : `${daysLeft}d left`;
      return `<tr>
        <td style="color:var(--text)">${esc(devName)}</td>
        <td>${esc(l.license_name)}</td>
        <td style="font-family:'Courier New',monospace;font-size:10px">${esc(l.expiry_date)}</td>
        <td style="text-align:right;color:var(--text2);font-size:10px;padding-right:6px">${daysStr}</td>
        <td>${badge}</td>
      </tr>`;
    }).join('');
    _dwSwap(el, `
      <div class="dw-ncm-grid">
        <div class="dw-ncm-kpi" style="border-color:var(--down-border)">
          <span class="dw-ncm-n" style="color:var(--down)">${sum.crit || 0}</span>
          <span class="dw-ncm-l">Expired</span>
        </div>
        <div class="dw-ncm-kpi" style="border-color:var(--warn-border)">
          <span class="dw-ncm-n" style="color:var(--warn)">${sum.warn || 0}</span>
          <span class="dw-ncm-l">Expiring</span>
        </div>
        <div class="dw-ncm-kpi dw-ncm-ok">
          <span class="dw-ncm-n">${sum.ok || 0}</span>
          <span class="dw-ncm-l">Valid</span>
        </div>
        <div class="dw-ncm-kpi dw-ncm-total">
          <span class="dw-ncm-n">${sum.total || 0}</span>
          <span class="dw-ncm-l">Total</span>
        </div>
      </div>
      ${expiring.length ? `<table class="dw-lic-tbl">
        <thead><tr>
          <th>Device</th><th>License</th><th>Expires</th><th>Days</th><th>Status</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>` : '<div class="dw-dd-ok" style="margin-top:8px">✓ No expiring licenses</div>'}
    `);
  } catch {
    el.innerHTML = '<div class="dw-err">Failed to load license data</div>';
  }
}
