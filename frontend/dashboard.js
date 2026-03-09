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
    label: 'Network Availability',
    icon:  '✦',
    defaultCols: 1,
    fields: [],
    render:  (wid, _cfg) => _dwRefreshNetAvail(wid),
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
};

// ── Persistence ───────────────────────────────────────────────────
function _dwLoad() {
  try { return JSON.parse(localStorage.getItem('pw-dashboard') || '[]'); } catch { return []; }
}
function _dwSave(widgets) {
  try { localStorage.setItem('pw-dashboard', JSON.stringify(widgets)); } catch {}
}

// ── Dashboard-wide tick (30 s) ────────────────────────────────────
let _dwTickTimer = null;
function _dwStartTick() {
  if (_dwTickTimer) return;
  _dwTickTimer = setInterval(() => {
    if (activeMainTab !== 'dashboard') { clearInterval(_dwTickTimer); _dwTickTimer = null; return; }
    _dwLoad().forEach(w => { const r = _DW_REG[w.type]; if (r) r.refresh(w.id, w.cfg); });
  }, 30000);
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
    <div class="dw-card${w.cols === 2 ? ' dw-wide' : ''}" id="dw-${w.id}"
         draggable="true"
         ondragstart="_dwDragStart(event,'${w.id}')"
         ondragover="_dwDragOver(event)"
         ondrop="_dwDrop(event,'${w.id}')">
      <div class="dw-hdr">
        <span class="dw-icon">${(_DW_REG[w.type]||{}).icon||'◧'}</span>
        <span class="dw-title">${esc(w.title)}</span>
        <button class="dw-rm" onclick="_dwRemove('${w.id}')" title="Remove widget">×</button>
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
function _dwDragStart(e, wid) {
  _dwDragSrcId = wid;
  e.dataTransfer.effectAllowed = 'move';
  document.getElementById(`dw-${wid}`)?.classList.add('dw-dragging');
}
function _dwDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}
function _dwDrop(e, targetId) {
  e.preventDefault();
  document.querySelectorAll('.dw-dragging').forEach(el => el.classList.remove('dw-dragging'));
  if (!_dwDragSrcId || _dwDragSrcId === targetId) { _dwDragSrcId = null; return; }
  const widgets = _dwLoad();
  const si = widgets.findIndex(w => w.id === _dwDragSrcId);
  const ti = widgets.findIndex(w => w.id === targetId);
  if (si < 0 || ti < 0) { _dwDragSrcId = null; return; }
  const [moved] = widgets.splice(si, 1);
  widgets.splice(ti, 0, moved);
  _dwSave(widgets);
  _dwDragSrcId = null;
  _dwRenderAll();
}

// ── Add widget — picker + config ──────────────────────────────────
function _dwOpenPicker() {
  const typeCards = Object.entries(_DW_REG).map(([type, reg]) => `
    <div class="dw-type-card" onclick="_dwSelectType('${type}')">
      <div class="dw-type-icon">${reg.icon}</div>
      <div class="dw-type-label">${reg.label}</div>
    </div>`).join('');
  const html = `
    <div class="mo" id="dw-picker-overlay" onclick="if(event.target===this)this.remove()">
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
  const titleDefault = reg.label;
  const html = `
    <div class="mo" id="dw-cfg-overlay" onclick="if(event.target===this)this.remove()">
      <div class="mbox" style="width:380px">
        <div class="mhd">
          <span class="mttl">${reg.icon} ${reg.label}</span>
          <button class="mclose" onclick="document.getElementById('dw-cfg-overlay').remove()">✕</button>
        </div>
        <div class="mbdy">
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
  // Populate sensor select for the initially-selected device
  const firstDev = Object.keys(S.devices)[0];
  if (firstDev && reg.fields.some(f => f.type === 'device-select')) _dwCfgDeviceChange(firstDev);
}

function _dwCfgDeviceChange(did) {
  const sel = document.getElementById('dw-cfg-sid');
  if (!sel) return;
  const sensors = Object.values(S.sensors).filter(s => s.device_id === did);
  sel.innerHTML = sensors.map(s =>
    `<option value="${s.sensor_id}">${esc(s.name)} (${s.stype})</option>`).join('');
}

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
  _dwLoad().forEach(w => {
    if (w.type === 'sensor_chart' && w.cfg.did === did && w.cfg.sid === sid)
      _dwLoadSensorChart(w.id, did, sid, w.cfg.minutes);
    if (w.type === 'sensor_gauge' && w.cfg.did === did && w.cfg.sid === sid)
      _dwRefreshGauge(w.id, w.cfg);
    if (w.type === 'network_avail')
      _dwRefreshNetAvail(w.id);
  });
}
function _dwOnDeviceUpdate() {
  if (activeMainTab !== 'dashboard') return;
  _dwLoad().forEach(w => {
    if (w.type === 'device_status') _dwRefreshDeviceStatus(w.id);
    if (w.type === 'network_avail') _dwRefreshNetAvail(w.id);
  });
}
function _dwOnFlapEvent() {
  if (activeMainTab !== 'dashboard') return;
  _dwLoad().forEach(w => {
    if (w.type === 'flap_events') _dwRefreshFlapEvents(w.id, w.cfg);
  });
}

// ── Widget: Network Availability ──────────────────────────────────
function _dwRefreshNetAvail(wid) {
  const body = document.getElementById(`dw-body-${wid}`);
  if (!body) return;
  const sensors = Object.values(S.sensors);
  const total = sensors.length;
  const up    = sensors.filter(s => s.alive === true).length;
  const down  = sensors.filter(s => s.alive === false).length;
  const warn  = 0;
  const idle  = sensors.filter(s => s.alive == null).length;
  const pct   = total ? Math.round(up / total * 100) : 0;
  const color = pct >= 90 ? 'var(--up)' : pct >= 70 ? 'var(--warn)' : 'var(--down)';
  body.innerHTML = `
    <div class="dw-na-wrap">
      <div class="dw-na-pct" style="color:${color}">${pct}<span class="dw-na-sym">%</span></div>
      <div class="dw-na-lbl">sensors online</div>
      <div class="dw-na-pills">
        <span class="dw-ds-pill up">${up} Up</span>
        <span class="dw-ds-pill down">${down} Down</span>
        ${warn  ? `<span class="dw-ds-pill warn">${warn} Warn</span>` : ''}
        ${idle  ? `<span class="dw-ds-pill idle">${idle} Idle</span>` : ''}
      </div>
      <div class="dw-na-total">${total} sensor${total!==1?'s':''} monitored</div>
    </div>`;
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
    const ts   = (d.ts||'').split(' ')[1] || d.ts || '';
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
  const up = info.uptime_s || 0;
  const h  = Math.floor(up / 3600), m = Math.floor((up % 3600) / 60), s = up % 60;
  const uptimeStr = h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
  const dbMB  = info.db_size_bytes  ? (info.db_size_bytes  / 1048576).toFixed(2) + ' MB' : '—';
  const logMB = info.log_size_bytes ? (info.log_size_bytes / 1048576).toFixed(2) + ' MB' : '—';
  body.innerHTML = `
    <div class="dw-ss-rows">
      <div class="dw-ss-row"><span class="dw-ss-lbl">Version</span><span class="dw-ss-val accent">v${esc(info.version||'—')}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Uptime</span><span class="dw-ss-val">${uptimeStr}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Devices</span><span class="dw-ss-val">${info.devices||0}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Sensors</span><span class="dw-ss-val">${info.sensors||0}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">DB Size</span><span class="dw-ss-val">${dbMB}</span></div>
      <div class="dw-ss-row"><span class="dw-ss-lbl">Log Size</span><span class="dw-ss-val">${logMB}</span></div>
    </div>`;
}
