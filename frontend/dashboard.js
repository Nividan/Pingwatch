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

// ── Dashboard widget system ───────────────────────────────────────
// Widget registry — add entries here to support new widget types
const _DW_REG = {
  sensor_chart: {
    label: 'Sensor History Chart',
    icon:  '📈',
    defaultCols: 2,
    fields: [
      { key: 'did',     label: 'Device', type: 'device-select' },
      { key: 'sid',     label: 'Sensor', type: 'sensor-select' },
      { key: 'minutes', label: 'Period', type: 'select',
        options: [{v:60,l:'1h'},{v:360,l:'6h'},{v:1440,l:'24h'},{v:10080,l:'7d'},{v:43200,l:'30d'}],
        def: 1440 },
    ],
    render:  (wid, cfg) => _dwRenderSensorChart(wid, cfg),
    refresh: (wid, cfg) => _dwLoadSensorChart(wid, cfg.did, cfg.sid, cfg.minutes),
  },
  device_status: {
    label: 'Device Status',
    icon:  '⊞',
    defaultCols: 1,
    fields: [],
    render:  (wid, cfg) => _dwRenderDeviceStatus(wid),
    refresh: (wid, _cfg) => _dwRefreshDeviceStatus(wid),
  },
  network_avail: {
    label: 'Network Availability History (24h)',
    icon:  '✦',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRenderNetAvail(wid),
    refresh: (wid, _cfg) => _dwRefreshNetAvail(wid),
  },
  sensor_gauge: {
    label: 'Sensor Gauge',
    icon:  '◉',
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
    icon:  '⚡',
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
    icon:  '⚙',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRenderSystemStatus(wid),
    refresh: (wid, _cfg) => _dwRenderSystemStatus(wid),
  },
  server_perf: {
    label: 'Server Performance',
    icon:  '🖥',
    defaultCols: 1,
    note:  'Requires <strong>psutil</strong> on the PingWatch server.<br>Install with: <code>pip install psutil</code>',
    fields: [],
    render:  (wid, _cfg) => _dwRenderServerPerf(wid),
    refresh: (wid, _cfg) => _dwFetchServerPerf(wid),
  },
  down_devices: {
    label: 'Down & Warning Devices',
    icon:  '⚠',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshDownDevices(wid),
    refresh: (wid, _cfg) => _dwRefreshDownDevices(wid),
  },
  top_latency: {
    label: 'Slowest Ping Devices',
    icon:  '⏱',
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
    icon:  '📊',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshEventCount(wid),
    refresh: (wid, _cfg) => _dwRefreshEventCount(wid),
  },
  packet_loss: {
    label: 'Packet Loss',
    icon:  '◎',
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
    icon:  '◈',
    defaultCols: 1,
    fields: [
      { key: 'did',    label: 'Device', type: 'device-select' },
      { key: 'sid',    label: 'Sensor', type: 'sensor-select' },
      { key: 'period', label: 'Period', type: 'select',
        options: [{v:1440,l:'24h'},{v:10080,l:'7d'},{v:43200,l:'30d'},{v:129600,l:'90d'}],
        def: 10080 },
    ],
    render:  (wid, cfg) => _dwRenderSLA(wid, cfg),
    refresh: (wid, cfg) => _dwFetchSLA(wid, cfg),
  },
  flap_detect: {
    label: 'Flapping Devices',
    icon:  '🔁',
    defaultCols: 1,
    fields: [
      { key: 'window',    label: 'Time window', type: 'select',
        options: [{v:1,l:'1h'},{v:6,l:'6h'},{v:24,l:'24h'}], def: 6 },
      { key: 'min_flaps', label: 'Min flaps',   type: 'select',
        options: [{v:3,l:'3 events'},{v:5,l:'5 events'},{v:10,l:'10 events'}], def: 3 },
    ],
    render:  (wid, cfg) => _dwRefreshFlapDetect(wid, cfg),
    refresh: (wid, cfg) => _dwRefreshFlapDetect(wid, cfg),
  },
  internet_health: {
    label: 'Internet Health',
    icon:  '🌐',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshInternetHealth(wid),
    refresh: (wid, _cfg) => _dwRefreshInternetHealth(wid),
  },
  ncm_status: {
    label: 'Backup Status',
    icon:  '💾',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwNcmStatusRefresh(wid),
    refresh: (wid, _cfg) => _dwNcmStatusRefresh(wid),
  },
};

// ── Persistence (server-side, per user) ───────────────────────────
let _dwWidgets = null;   // in-memory cache; null = not yet loaded

function _dwLoad() {
  return _dwWidgets || [];
}
function _dwSave(widgets) {
  _dwWidgets = widgets;
  fetch('/api/dashboard', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ widgets }),
  }).catch(() => {});   // fire-and-forget; errors are silent
}
async function _dwInit() {
  // Clear any stale localStorage data left from the old storage scheme
  try { localStorage.removeItem('pw-dashboard'); } catch {}

  // Already initialized — DOM widgets exist; just refresh content + restart tick
  // (avoids tearing down/rebuilding DOM and resetting live counters on every tab switch)
  if (_dwWidgets !== null) {
    _dwLoad().forEach(w => { const r = _DW_REG[w.type]; if (r) r.refresh(w.id, w.cfg); });
    _dwStartTick();
    return;
  }

  // First load — fetch layout from server
  try {
    const r = await fetch('/api/dashboard');
    if (r.ok) {
      const data = await r.json();
      _dwWidgets = Array.isArray(data.widgets) ? data.widgets : [];
    } else {
      _dwWidgets = [];
    }
  } catch {
    _dwWidgets = [];
  }
  _dwRenderAll();
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

// ── Grid render ───────────────────────────────────────────────────
function _dwRenderAll() {
  const grid = document.getElementById('dw-grid');
  if (!grid) return;
  // Clear any per-card intervals from previous render
  grid.querySelectorAll('.dw-card').forEach(c => { if (c._interval) { clearInterval(c._interval); c._interval = null; } });
  const widgets = _dwLoad();
  if (!widgets.length) {
    grid.innerHTML = '<div class="dw-empty">No widgets yet. Click <strong>＋ Add Widget</strong> to get started.</div>';
    return;
  }
  grid.innerHTML = widgets.map(w => `
    <div class="dw-card${w.cols === 2 ? ' dw-wide' : ''}" id="dw-${w.id}" data-wid="${w.id}"
         draggable="true"
         ondragstart="_dwDragStart(event,'${w.id}')"
         ondragover="_dwDragOver(event,'${w.id}')"
         ondragleave="_dwDragLeave(event)"
         ondragend="_dwDragEnd(event)"
         ondrop="_dwDrop(event,'${w.id}')">
      <div class="dw-hdr">
        <span class="dw-icon">${(_DW_REG[w.type]||{}).icon||'◧'}</span>
        <span class="dw-title">${esc(w.title)}</span>
        <button class="dw-edit rbac-op" onclick="_dwOpenEdit('${w.id}')" title="Edit widget">✎</button>
        <button class="dw-exp"          onclick="_dwOpenFullscreen('${w.id}')" title="Expand widget">⤢</button>
        <button class="dw-rm"           onclick="_dwRemove('${w.id}')" title="Remove widget">×</button>
      </div>
      <div class="dw-body" id="dw-body-${w.id}"></div>
    </div>`).join('');
  widgets.forEach(w => {
    const reg = _DW_REG[w.type];
    if (reg) reg.render(w.id, w.cfg);
  });
  _dwStartTick();
}

function _dwRemove(wid) {
  const card = document.getElementById(`dw-${wid}`);
  if (card && card._interval) clearInterval(card._interval);
  const widgets = _dwLoad().filter(w => w.id !== wid);
  _dwSave(widgets);
  _dwRenderAll();
}

// ── Drag-and-drop reorder ─────────────────────────────────────────
let _dwDragSrcId = null;
let _dwDropDone  = false;

function _dwDragStart(e, wid) {
  _dwDragSrcId = wid;
  _dwDropDone  = false;
  e.dataTransfer.effectAllowed = 'move';
  setTimeout(() => {
    document.getElementById(`dw-${wid}`)?.classList.add('dw-dragging');
    // Append a drop-here placeholder at the end of the grid
    const grid = document.getElementById('dw-grid');
    if (grid && !document.getElementById('dw-placeholder')) {
      const ph = document.createElement('div');
      ph.id        = 'dw-placeholder';
      ph.className = 'dw-card dw-placeholder';
      ph.innerHTML = '<span style="pointer-events:none">↓ Drop here</span>';
      ph.addEventListener('dragover',  _dwPlaceholderOver);
      ph.addEventListener('dragleave', _dwDragLeave);
      ph.addEventListener('drop',      _dwPlaceholderDrop);
      grid.appendChild(ph);
    }
  }, 0);
}

function _dwDragOver(e, targetId) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  if (!_dwDragSrcId || _dwDragSrcId === targetId) return;
  const grid  = document.getElementById('dw-grid');
  const srcEl = document.getElementById(`dw-${_dwDragSrcId}`);
  const tgtEl = document.getElementById(`dw-${targetId}`);
  if (!srcEl || !tgtEl || !grid) return;
  grid.querySelectorAll('.dw-drop-target').forEach(el => el.classList.remove('dw-drop-target'));
  tgtEl.classList.add('dw-drop-target');
  const rect = tgtEl.getBoundingClientRect();
  if (e.clientY < rect.top + rect.height / 2) {
    grid.insertBefore(srcEl, tgtEl);
  } else {
    grid.insertBefore(srcEl, tgtEl.nextSibling);
  }
}

function _dwPlaceholderOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  if (!_dwDragSrcId) return;
  const grid  = document.getElementById('dw-grid');
  const srcEl = document.getElementById(`dw-${_dwDragSrcId}`);
  const ph    = document.getElementById('dw-placeholder');
  if (!srcEl || !ph || !grid) return;
  grid.querySelectorAll('.dw-drop-target').forEach(el => el.classList.remove('dw-drop-target'));
  ph.classList.add('dw-drop-target');
  // Move dragged card just before the placeholder (append to real cards)
  grid.insertBefore(srcEl, ph);
}

function _dwPlaceholderDrop(e) {
  e.preventDefault();
  _dwDropDone = true;
  _dwCleanupDrag();
  _dwSaveDomOrder();
  _dwDragSrcId = null;
}

function _dwDragLeave(e) {
  if (!e.relatedTarget || !e.currentTarget.contains(e.relatedTarget)) {
    e.currentTarget.classList.remove('dw-drop-target');
  }
}

function _dwDrop(e, targetId) {
  e.preventDefault();
  _dwDropDone = true;
  _dwCleanupDrag();
  if (!_dwDragSrcId) return;
  _dwSaveDomOrder();
  _dwDragSrcId = null;
}

function _dwDragEnd(e) {
  _dwCleanupDrag();
  if (!_dwDropDone) _dwRenderAll(); // cancelled — restore
  _dwDragSrcId = null;
  _dwDropDone  = false;
}

function _dwCleanupDrag() {
  document.querySelectorAll('.dw-dragging, .dw-drop-target').forEach(el =>
    el.classList.remove('dw-dragging', 'dw-drop-target'));
  document.getElementById('dw-placeholder')?.remove();
}

function _dwSaveDomOrder() {
  const grid = document.getElementById('dw-grid');
  if (!grid) return;
  const newOrder  = [...grid.querySelectorAll('.dw-card[data-wid]')].map(el => el.dataset.wid);
  const widgets   = _dwLoad();
  const reordered = newOrder.map(id => widgets.find(w => w.id === id)).filter(Boolean);
  _dwSave(reordered);
}

// ── Add widget — picker + config ──────────────────────────────────
function _dwOpenPicker() {
  const typeCards = Object.entries(_DW_REG).map(([type, reg]) => `
    <div class="dw-type-card" onclick="_dwSelectType('${type}')">
      <div class="dw-type-icon">${reg.icon}</div>
      <div class="dw-type-label">${reg.label}</div>
    </div>`).join('');
  const html = `
    <div class="mo" id="dw-picker-overlay">
      <div class="mbox" style="width:440px">
        <div class="mhd">
          <span class="mttl">Add Widget</span>
          <button class="mclose" onclick="document.getElementById('dw-picker-overlay').remove()">✕</button>
        </div>
        <div class="mbdy">
          <div class="dw-type-grid">${typeCards}</div>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  _overlayClose(document.getElementById('dw-picker-overlay'), () => document.getElementById('dw-picker-overlay')?.remove());
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
  widgets.push({ id: Math.random().toString(36).slice(2, 9), type, title: finalTitle, cols: reg.defaultCols, cfg });
  _dwSave(widgets);
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
  document.getElementById('dw-fs-icon').textContent  = reg.icon;
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
function _dwRenderSensorChart(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const periodOpts = [{v:60,l:'1h'},{v:360,l:'6h'},{v:1440,l:'24h'},{v:10080,l:'7d'},{v:43200,l:'30d'}];
  const mins = cfg.minutes || 1440;
  body.innerHTML = `
    <div class="dw-chart-toolbar">
      <div class="dm-hist-pills" style="margin:0">
        ${periodOpts.map(p =>
          `<button class="dm-hist-pill${p.v === mins ? ' active' : ''}"
             onclick="_dwChartPick('${wid}','${cfg.did}','${cfg.sid}',${p.v})">${p.l}</button>`
        ).join('')}
      </div>
      <span class="dm-hist-stats" id="dw-stats-${wid}"></span>
    </div>
    <canvas id="dw-canvas-${wid}" class="dm-hist-canvas dw-canvas" height="180"></canvas>
    <div id="dw-sum-${wid}" class="dm-hist-summary"></div>`;
  _dwLoadSensorChart(wid, cfg.did, cfg.sid, mins);
}

function _dwChartPick(wid, did, sid, minutes) {
  document.querySelectorAll(`#dw-body-${wid} .dm-hist-pill`)
    .forEach(b => b.classList.toggle('active', +b.dataset.m === minutes || b.textContent === _dwMinLabel(minutes)));
  // Update stored cfg
  const widgets = _dwLoad();
  const w = widgets.find(w => w.id === wid);
  if (w) { w.cfg.minutes = minutes; _dwSave(widgets); }
  _dwLoadSensorChart(wid, did, sid, minutes);
}

function _dwMinLabel(m) { return m<=60?'1h':m<=360?'6h':m<=1440?'24h':m<=10080?'7d':'30d'; }

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
  body.innerHTML = `
    <div class="dw-ds-pills">
      <span class="dw-ds-pill up">${cnt.up} Up</span>
      <span class="dw-ds-pill down">${cnt.down} Down</span>
      <span class="dw-ds-pill warn">${cnt.warn} Warning</span>
      ${cnt.idle ? `<span class="dw-ds-pill idle">${cnt.idle} Idle</span>` : ''}
    </div>
    <div class="dw-ds-list">${rows || '<div style="color:var(--text3);font-size:11px;padding:8px">No devices</div>'}</div>`;
}

// ── SSE hooks (called from app.js event handlers) ─────────────────
function _dwOnSensorUpdate(did, sid) {
  if (activeMainTab !== 'dashboard') return;
  let topLatencyDirty = false;
  _dwLoad().forEach(w => {
    if (w.type === 'sensor_chart' && w.cfg.did === did && w.cfg.sid === sid)
      _dwLoadSensorChart(w.id, did, sid, w.cfg.minutes);
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
  if (activeMainTab !== 'dashboard') return;
  _dwLoad().forEach(w => {
    if (w.type === 'device_status')   _dwRefreshDeviceStatus(w.id);
    if (w.type === 'network_avail')   _dwRefreshNetAvail(w.id);
    if (w.type === 'down_devices')    _dwRefreshDownDevices(w.id);
    if (w.type === 'internet_health') _dwRefreshInternetHealth(w.id);
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

async function _dwDrawNetAvailChart(wid) {
  const canvas = document.getElementById(`dw-na-canvas-${wid}`);
  if (!canvas) return;
  let availability = [];
  try {
    const d = await _fetchAvailability();
    availability = d.availability || [];
  } catch { return; }
  canvas.width = canvas.offsetWidth || 340;
  const W = canvas.width, H = canvas.height || 80;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#0d1117'; ctx.fillRect(0, 0, W, H);
  if (!availability.length) {
    ctx.fillStyle = '#484f58'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('No historical data yet', W / 2, H / 2);
    return;
  }
  const BOT = 14, TOP = 4;
  const plotW = W - 8, plotH = H - TOP - BOT;
  const now = Date.now() / 1000, winStart = now - 1440 * 60;
  const xOf = ts  => 4 + ((ts - winStart) / (1440 * 60)) * plotW;
  const yOf = pct => TOP + plotH - (Math.min(100, Math.max(0, pct)) / 100) * plotH;
  const avgPct = availability.reduce((s, b) => s + b.pct, 0) / availability.length;
  const lineColor = avgPct >= 90 ? '#23d18b' : avgPct >= 70 ? '#f0a500' : '#f85149';
  const fillAlpha = avgPct >= 90 ? 'rgba(35,209,139,.18)' : avgPct >= 70 ? 'rgba(240,165,0,.18)' : 'rgba(248,81,73,.18)';
  const pts = availability.map(b => ({ x: xOf(b.ts + 1800), y: yOf(b.pct) }));
  if (pts.length < 2) return;
  // Filled area
  const g = ctx.createLinearGradient(0, TOP, 0, H - BOT);
  g.addColorStop(0, fillAlpha); g.addColorStop(1, 'rgba(0,0,0,0)');
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
  ctx.fillStyle = 'rgba(139,148,158,.6)'; ctx.font = '8px sans-serif'; ctx.textAlign = 'center';
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
  body.innerHTML = `
    <div class="dw-gauge-wrap">
      <div class="dw-gauge-ring" style="--gc:${color}">
        <div class="dw-gauge-val" style="color:${color}">${val}<span class="dw-gauge-unit">${unit}</span></div>
      </div>
      <div class="dw-gauge-name">${esc(s.name)}</div>
      <div class="dw-gauge-st" style="color:${color}">${typeIco} ${st.toUpperCase()}</div>
    </div>`;
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
      dotColor = isCrit ? '#e74c3c' : '#f39c12';
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
  body.innerHTML = `<div class="dw-fe-list">${rows}</div>`;
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
  body.innerHTML = `<div class="dw-dd-list">${rows}</div>`;
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
  body.innerHTML = `<div class="dw-tl-list">${rows}</div>`;
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
  body.innerHTML = `<div class="dw-ec-table">${headerRow}${dataRows}${totalRow}</div>`;
}

// ── Widget: Packet Loss ───────────────────────────────────────────
function _dwRefreshPacketLoss(wid, cfg) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const limit     = Number(cfg?.limit)     || 10;
  const threshold = Number(cfg?.threshold) || 1;
  const lossy = Object.values(S.sensors)
    .filter(s => s.stype === 'ping' && s.loss_pct != null && s.loss_pct >= threshold)
    .sort((a, b) => b.loss_pct - a.loss_pct)
    .slice(0, limit);
  if (!lossy.length) {
    body.innerHTML = '<div class="dw-pl-ok">✓ No packet loss</div>';
    return;
  }
  const maxLoss = lossy[0].loss_pct || 1;
  const rows = lossy.map(s => {
    const dev   = S.devices[s.device_id];
    const barW  = Math.min(100, Math.round(s.loss_pct / maxLoss * 100));
    const color = s.loss_pct >= 20 ? 'var(--down)' : s.loss_pct >= 5 ? 'var(--warn)' : '#f0a500';
    return `<div class="dw-tl-row">
      <span class="dw-tl-name">${esc(dev?.name || s.device_id)}</span>
      <div class="dw-tl-bar-wrap"><div class="dw-tl-bar" style="width:${barW}%;background:${color}"></div></div>
      <span class="dw-tl-val" style="color:${color}">${s.loss_pct}%</span>
    </div>`;
  }).join('');
  body.innerHTML = `<div class="dw-tl-list">${rows}</div>`;
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
  const mins = Number(cfg?.period) || 10080;
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
  const slaFixed = slaPct.toFixed(3);
  const slaColor = slaPct >= 99.5 ? 'var(--up)' : slaPct >= 99 ? 'var(--warn)' : 'var(--down)';
  const slaLabel = slaPct >= 99.9 ? 'SLA 99.9%' : slaPct >= 99.5 ? 'SLA 99.5%' : slaPct >= 99 ? 'SLA 99%' : 'Below 99%';
  const dtSec = Math.round(totalFail / totalAll * mins * 60);
  const dtH   = Math.floor(dtSec / 3600);
  const dtM   = String(Math.floor((dtSec % 3600) / 60)).padStart(2, '0');
  wrap.innerHTML = `
    <div class="dw-sla-pct" style="color:${slaColor}">${slaFixed}<span class="dw-sla-sym">%</span></div>
    <div class="dw-sla-tier" style="color:${slaColor}">${slaLabel}</div>
    <div class="dw-sla-bar-wrap"><div class="dw-sla-bar" style="width:${Math.min(100,slaPct).toFixed(2)}%;background:${slaColor}"></div></div>
    <div class="dw-sla-stats">
      <span><span class="dw-sla-key">Downtime</span>${dtH}h ${dtM}m</span>
      <span><span class="dw-sla-key">Samples</span>${totalAll}</span>
    </div>`;
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
  const windowH   = Number(cfg?.window)    || 6;
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
  body.innerHTML = `<div class="dw-fd-list">${rows}</div>`;
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
  body.innerHTML = `<div class="dw-ih-wrap">
    <div class="dw-ih-badge ${badgeCls}">${badgeTxt}</div>
    <div class="dw-ih-counts">
      <span style="color:var(--up)">${up} up</span> ·
      <span style="color:var(--down)">${down} down</span> ·
      <span style="color:var(--text3)">${idle} idle</span>
      <span style="color:var(--text3);font-size:10px"> / ${total} external</span>
    </div>
    ${failed.length ? `<div class="dw-ih-fail-list">${failRows}</div>` : ''}
  </div>`;
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
  body.innerHTML = `<div class="dw-sp-list">
    ${_gauge(d.cpu_pct,  'CPU',  `${d.cpu_pct}%`)}
    ${_gauge(d.ram_pct,  'RAM',  ramDetail)}
    ${_gauge(d.disk_pct, 'Disk', diskDetail)}
  </div>`;
}

// ── NCM Backup Status Widget ─────────────────────────────────────
async function _dwNcmStatusRefresh(wid) {
  const el = document.getElementById(`dw-body-${wid}`);
  if (!el) return;
  el.innerHTML = '<div class="dw-loading">Loading…</div>';
  try {
    const r = await api('GET', '/api/backups');
    const devs = (r.devices || []).filter(d => !d.orphaned);
    const total   = devs.length;
    const enabled = devs.filter(d => d.enabled).length;
    const ok      = devs.filter(d => d.last_success === true).length;
    const failed  = devs.filter(d => d.run_count > 0 && d.last_success === false).length;
    const never   = devs.filter(d => d.run_count === 0 && d.enabled).length;
    el.innerHTML = `
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
      </div>`;
  } catch {
    el.innerHTML = '<div class="dw-err">Failed to load backup status</div>';
  }
}
