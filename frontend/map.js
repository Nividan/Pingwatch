// ═══════════════════════════ STATE ═══════════════════════════
let nodes = [], links = [], groups = [], nodeMap = {}, groupMap = {};
let pwOverrides = {};      // { device_id: {x, y, color?} }  — persisted via /api/settings/pw_node_overrides
let pwGroupOverrides = {}; // { groupName: {x, y, w, h, color?} } — persisted via /api/settings/pw_group_overrides
let pwLinks = [];          // [ {id, src_did, tgt_did, link_type, label?} ] — persisted via /api/settings/pw_links
const PW_INTERNET_DID = '__internet__'; // synthetic Internet cloud node (always present)
let linkDraw = null;
let selectedEl = null; // { type:'node'|'link'|'group', data }
let editingNodeId = null, editingLinkId = null, editingGroupId = null;
// Viewport transform
let vp = { scale: 1, tx: 0, ty: 0 };
// Multi-select
let multiSelect = new Set();
let rubberBand = null, mmPan = null, dragMultiStart = [], groupDrag = null;
// Undo / redo
const undoStack = [], redoStack = [];
// Context menu target
let ctxTargetNode = null;

// Single source of truth for VLAN colors — loaded from DB on boot
let VLAN_COLORS = { '10': '#00d4ff', '20': '#ff8c00', '30': '#a855f7', '40': '#ffd700' };

// Generation counter — incremented on every tab switch so in-flight loadData /
// loadPingWatchPage calls from a previous switch self-cancel when superseded.
let _pageGen = 0;

// ═══════════════════════════ PINGWATCH LIVE TAB ═══════════════════════════
let isPingWatchPage = true;   // true while PingWatch live tab is active
let pwDevices = [];           // cached device list from /api/devices
let _pwDevMap = {};           // device_id → device object (O(1) lookup)
let pwSSE = null;             // SSE EventSource for live status updates
let _selectedPwDid = null;   // device_id currently shown in panel
let _pwTraceSrcDid = null;   // trace source override (null = auto-detect by name)
let _pwActiveTraces = 0;     // concurrent animation count

// ── Performance: canvas pause/resume via postMessage from parent ──────────
let _ntmVisible = true;
// Resume callbacks — set by initDashBg / initMainBg so the loops restart
// immediately when coming back from a paused state.
let _resumeDashBg = null, _resumeMainBg = null;
window.addEventListener('message', e => {
  if (e.data?.type === 'ntm_pause')  _ntmVisible = false;
  if (e.data?.type === 'ntm_resume') {
    _ntmVisible = true;
    _resumeDashBg?.();
    _resumeMainBg?.();
    // Catchup: apply any device status updates that arrived while map was paused
    if (Array.isArray(e.data.devices)) {
      for (const {did, status} of e.data.devices) {
        const dev = _pwDevMap[did];
        if (dev && dev.status !== status) {
          dev.status = status;
          _schedulePwLiveUpdate(dev.device_id);
        }
      }
    }
  }
});
// Also pause when this document's own visibility changes (e.g. OS switch)
let _bgPaused = false;
document.addEventListener('visibilitychange', () => {
  _bgPaused = document.hidden;
  if (!document.hidden) { _resumeDashBg?.(); _resumeMainBg?.(); }
});

// ── Performance: batch _pwLiveUpdate calls — one DOM pass per rAF ─────────
const _pendingLiveUpdates = new Set();
let   _liveUpdateRaf = null;
function _schedulePwLiveUpdate(did) {
  _pendingLiveUpdates.add(did);
  if (!_liveUpdateRaf) {
    _liveUpdateRaf = requestAnimationFrame(() => {
      _liveUpdateRaf = null;
      _pendingLiveUpdates.forEach(id => _pwLiveUpdate(id));
      _pendingLiveUpdates.clear();
    });
  }
}

// ═══════════════════════════ PAGES ═══════════════════════════
let pages = [], currentPageId = 1;

async function loadPages() {
  try {
    pages = await api('GET', '/api/pages');
  } catch(e) { pages = []; }
  if (!pages.length) {
    const pg = await api('POST', '/api/pages', { name: 'Main' });
    pages = [pg];
  }
  if (!pages.find(pg => pg.id === currentPageId)) currentPageId = pages[0].id;
  renderPageBar();
}

function renderPageBar() {
  const bar = document.getElementById('page-bar');
  bar.innerHTML = '';
  // ── PingWatch live tab (always first) ──────────────────────────
  const pwTab = document.createElement('div');
  pwTab.className = 'page-tab' + (isPingWatchPage ? ' active' : '');
  const dot = document.createElement('span');
  dot.id = 'pw-tab-dot';
  dot.style.cssText = 'display:inline-block;width:7px;height:7px;border-radius:50%;background:#00ff9d;margin-right:5px;vertical-align:middle;box-shadow:0 0 5px #00ff9d;flex-shrink:0;';
  pwTab.appendChild(dot);
  pwTab.appendChild(document.createTextNode('PingWatch'));
  pwTab.onclick = () => switchToPingWatchPage();
  bar.appendChild(pwTab);
  // ── DB topology pages ──────────────────────────────────────────
  for (const pg of pages) {
    const tab = document.createElement('div');
    tab.className = 'page-tab' + (pg.id === currentPageId && !isPingWatchPage ? ' active' : '');
    const lbl = document.createElement('span');
    lbl.className = 'page-tab-name';
    lbl.title = pg.name;
    lbl.textContent = pg.name;
    tab.appendChild(lbl);
    tab.addEventListener('click', () => switchPage(pg.id));
    tab.addEventListener('dblclick', e => { e.stopPropagation(); renamePage(pg.id, pg.name); });
    tab.addEventListener('contextmenu', e => { e.preventDefault(); e.stopPropagation(); _tabMenu(e, pg); });
    bar.appendChild(tab);
  }
  const addBtn = document.createElement('button');
  addBtn.id = 'page-add-btn';
  addBtn.title = 'New topology page';
  addBtn.textContent = '+';
  addBtn.onclick = addPage;
  bar.appendChild(addBtn);
  // Panel + Fullscreen buttons (right-aligned)
  const panelBtn = document.createElement('button');
  panelBtn.id = 'page-panel-btn';
  panelBtn.textContent = '☰ PANEL';
  panelBtn.onclick = togglePanel;
  bar.appendChild(panelBtn);
  const fsBtn = document.createElement('button');
  fsBtn.id = 'page-fs-btn';
  _ntmUpdateFsBtn(fsBtn);
  fsBtn.onclick = _ntmToggleFs;
  bar.appendChild(fsBtn);
}

async function switchPage(id) {
  if (id === currentPageId && !isPingWatchPage) return;
  isPingWatchPage = false;
  _selectedPwDid = null;
  stopPwSSE();
  currentPageId = id;
  sessionStorage.setItem('ntm_active_tab', String(id));
  renderPageBar();
  undoStack.length = 0; redoStack.length = 0;
  await loadData();
  fitToView();
}

async function addPage() {
  const name = prompt('New page name:');
  if (!name?.trim()) return;
  try {
    const pg = await api('POST', '/api/pages', { name: name.trim() });
    pages.push(pg);
    currentPageId = pg.id;
    renderPageBar();
    undoStack.length = 0; redoStack.length = 0;
    await loadData();
  } catch(e) { toast('⚠ ' + e.message); }
}

async function renamePage(id, oldName) {
  const name = prompt('Rename page:', oldName);
  if (!name?.trim() || name.trim() === oldName) return;
  try {
    await api('PUT', `/api/pages/${id}`, { name: name.trim() });
    const pg = pages.find(p => p.id === id);
    if (pg) pg.name = name.trim();
    renderPageBar();
  } catch(e) { toast('⚠ ' + e.message); }
}

async function deletePage(id, name) {
  _confirm(`Delete page "<b>${name}</b>" and all its devices, links and groups?`, async () => {
  try {
    await api('DELETE', `/api/pages/${id}`);
    pages = pages.filter(p => p.id !== id);
    if (currentPageId === id) currentPageId = pages[0].id;
    renderPageBar();
    undoStack.length = 0; redoStack.length = 0;
    await loadData();
  } catch(e) { toast('⚠ ' + e.message); }
  });
}

// ═══════════════════════════ PINGWATCH LIVE TAB FUNCTIONS ═══════════════════════════

async function switchToPingWatchPage() {
  isPingWatchPage = true;
  selectedEl = null;
  sessionStorage.setItem('ntm_active_tab', 'pw');
  renderPageBar();
  await loadPingWatchPage();
}

async function loadPingWatchPage() {
  const gen = ++_pageGen;
  try {
    const [data, ovrRes, grpRes, lnkRes] = await Promise.all([
      fetch('/api/devices').then(r => r.json()),
      api('GET', '/api/settings/pw_node_overrides').catch(() => null),
      api('GET', '/api/settings/pw_group_overrides').catch(() => null),
      api('GET', '/api/settings/pw_links').catch(() => null),
    ]);
    if (gen !== _pageGen) return; // superseded by a newer tab switch
    pwDevices = data.devices || [];
    _pwDevMap = {}; pwDevices.forEach(d => _pwDevMap[d.device_id] = d);
    pwOverrides = ovrRes?.value || {};
    pwGroupOverrides = grpRes?.value || {};
    pwLinks = lnkRes?.value || [];
  } catch(e) { if (gen !== _pageGen) return; pwDevices = []; _pwDevMap = {}; }
  renderPingWatchCanvas();
  startPwSSE();
  fitToView();
}

function pwStatusColor(status) {
  if (status === 'up')   return '#00ff9d';
  if (status === 'down') return '#ff3333';
  return '#888888';
}

// did/sid → "ok"|"warn"|"crit"  — updated by SSE threshold events
const _pwSensorState = {};

// Unified PW link stroke resolver — priority: down > crit > warn > type color
function _pwLinkStroke(lk, srcDev, tgtDev) {
  if (srcDev?.status === 'down' || tgtDev?.status === 'down') return '#ff3333';
  const states = [lk.sensor_in, lk.sensor_out]
    .filter(Boolean)
    .map(key => _pwSensorState[key] || 'ok');
  if (states.includes('crit')) return '#a855f7';
  if (states.includes('warn')) return '#c084fc';
  return lcfg(lk.link_type || 'trunk').stroke;
}

// Apply stroke + width + pulse animation to a .link-main SVG element
function _pwApplyLinkEl(lineEl, lk, srcDev, tgtDev) {
  const stroke = _pwLinkStroke(lk, srcDev, tgtDev);
  const isCrit = stroke === '#a855f7';
  const isWarn = stroke === '#c084fc';
  const baseW  = lcfg(lk.link_type || 'trunk').width;
  lineEl.setAttribute('stroke', stroke);
  lineEl.setAttribute('stroke-width', isCrit ? baseW * 2.5 : isWarn ? baseW * 1.8 : baseW);
  lineEl.classList.toggle('pw-bw-crit', isCrit);
  lineEl.classList.toggle('pw-bw-warn', isWarn);
}

function pwDeviceType(dev) {
  const n = (dev.name  || '').toLowerCase();
  const g = (dev.group || '').toLowerCase();
  // Group name is most reliable signal
  if (/cloud|internet|wan|isp/.test(g)) return 'cloud';
  if (/firewall|fortigate|fortinet|asa|checkpoint|palo.?alto/.test(g)) return 'firewall';
  if (/\bswitch(es)?\b/.test(g)) return 'switch';
  // Device name fallback
  if (/cloud|internet|wan\d|isp/.test(n)) return 'cloud';
  if (/^forti|^asa\d|^palo|^fw[- _\d]|firewall/.test(n)) return 'firewall';
  if (/^ex-\d|^sw-\d|2960|3560|3750|nexus|catalyst|^ex\d|2200|2300|48p|48t/.test(n)) return 'switch';
  return 'server';
}

function deviceToNode(dev, x, y) {
  const col       = pwStatusColor(dev.status);
  const ovr       = pwOverrides[dev.device_id] || pwOverrides[String(dev.device_id)];
  const type      = ovr?.node_type || pwDeviceType(dev);
  const nameColor = dev.status === 'down'    ? '#ff9999'
                  : dev.status === 'unknown' ? '#888888' : undefined;
  return {
    id: 'pw_' + dev.device_id,
    name: dev.name,
    type,
    x: ovr?.x ?? x,
    y: ovr?.y ?? y,
    _pwDid: dev.device_id,
    properties: {
      ip: dev.host,
      ip_color: col,
      name_color: nameColor,
      color: ovr?.color ?? (dev.status === 'down' ? '#ff3333' : dev.status === 'unknown' ? '#888888' : undefined),
    }
  };
}

function calcPwLayout(devices) {
  const byGroup = {};
  for (const dev of devices) {
    const g = dev.group || 'Default Group';
    (byGroup[g] = byGroup[g] || []).push(dev);
  }
  const COLS = 3, PAD = 50, GGAP = 90, ROWGAP = 80, STARTX = 60, STARTY = 60, MAX_ROWS = 5;
  const syntheticNodes = [], syntheticGroups = [];
  // Smart-placement dirty flags — batched save at end of pass
  let _pwNodeDirty = false, _pwGroupDirty = false;

  // Pre-compute each group's slot dimensions based on actual device types
  const entries = Object.entries(byGroup).map(([gname, devs]) => {
    const sizes = devs.map(d => nsize(pwDeviceType(d), null) || { w: 170, h: 95 });
    const NW    = Math.max(...sizes.map(s => s.w)) + 30;   // slot width  = widest node + gap
    const NH    = Math.max(...sizes.map(s => s.h)) + 28;   // slot height = tallest node + gap
    const nrows = Math.min(devs.length, MAX_ROWS);          // column-major: max 5 rows per column
    const ncols = Math.ceil(devs.length / MAX_ROWS);
    return { gname, devs, NW, NH,
             w: ncols * NW + PAD * 2,
             h: nrows * NH + PAD * 2 + 28 };   // 28px = group title bar
  });

  // Row-based layout: accumulate X across each row to prevent overlap
  let gi = 0, rowY = STARTY;
  for (let r = 0; r < entries.length; r += COLS) {
    const row  = entries.slice(r, r + COLS);
    const rowH = Math.max(...row.map(e => e.h));
    let gx = STARTX;
    for (const { gname, devs, NW, NH, w, h } of row) {
      const govr = pwGroupOverrides[gname] || {};
      const gax = govr.x ?? gx, gay = govr.y ?? rowY;
      syntheticGroups.push({ id: 'pw_g_' + gi, name: gname,
                             x: gax, y: gay,
                             w: govr.w ?? w,  h: govr.h ?? h,
                             color: govr.color || '#00d4ff' });

      // Build the "occupied" list from devices that already have (x,y) overrides
      const occupied = [];
      let hasAnyOverride = false;
      for (const dev of devs) {
        const ovr = pwOverrides[dev.device_id];
        if (ovr?.x != null && ovr?.y != null) {
          const nt = ovr.node_type || pwDeviceType(dev);
          const s  = nsize(nt, null) || { w: 170, h: 95 };
          occupied.push({ x: ovr.x, y: ovr.y, w: s.w, h: s.h });
          hasAnyOverride = true;
        }
      }

      if (!hasAnyOverride) {
        // Pristine group — column-major grid (max MAX_ROWS per column)
        devs.forEach((dev, i) => {
          const dc = Math.floor(i / MAX_ROWS), dr = i % MAX_ROWS;
          syntheticNodes.push(deviceToNode(dev,
            gax + PAD + dc * NW,
            gay + PAD + 28 + dr * NH));
        });
      } else {
        // Customized group — smart placement for un-overridden devices
        const groupRect = { x: gax, y: gay, w: govr.w ?? w, h: govr.h ?? h };
        const INNER_PAD = 24, TITLE_H = 28;

        // 1) Push all overridden nodes first (deviceToNode pulls from ovr)
        for (const dev of devs) {
          const ovr = pwOverrides[dev.device_id];
          if (ovr?.x != null && ovr?.y != null) {
            syntheticNodes.push(deviceToNode(dev, 0, 0));  // ovr wins inside deviceToNode
          }
        }

        // 2) Place each unplaced device into a free slot (grow group if needed)
        for (const dev of devs) {
          const ovr = pwOverrides[dev.device_id];
          if (ovr?.x != null && ovr?.y != null) continue;
          const nt = ovr?.node_type || pwDeviceType(dev);
          const sz = nsize(nt, null) || { w: 170, h: 95 };

          let pos = _findFreeSlotInGroup(groupRect, occupied, sz.w, sz.h);
          if (!pos) {
            // Group is full → grow vertically (and widen if the node is too wide)
            const maxY = occupied.reduce((m, o) => Math.max(m, o.y + o.h),
                                         groupRect.y + TITLE_H);
            pos = { x: groupRect.x + INNER_PAD, y: maxY + 20 };
            const neededH = (pos.y + sz.h + INNER_PAD) - groupRect.y;
            const neededW = Math.max(groupRect.w, sz.w + 2 * INNER_PAD);
            groupRect.h = Math.max(groupRect.h, neededH);
            groupRect.w = neededW;
            pwGroupOverrides[gname] = { ...(pwGroupOverrides[gname] || govr),
                                        x: gax, y: gay,
                                        w: groupRect.w, h: groupRect.h };
            _pwGroupDirty = true;
            // Patch the syntheticGroups entry so this render reflects the new size
            const sg = syntheticGroups[syntheticGroups.length - 1];
            if (sg && sg.name === gname) { sg.w = groupRect.w; sg.h = groupRect.h; }
          }

          pwOverrides[dev.device_id] = { ...(pwOverrides[dev.device_id] || {}),
                                         x: pos.x, y: pos.y };
          _pwNodeDirty = true;
          occupied.push({ x: pos.x, y: pos.y, w: sz.w, h: sz.h });
          syntheticNodes.push(deviceToNode(dev, pos.x, pos.y));
        }
      }

      gx += w + GGAP;
      gi++;
    }
    rowY += rowH + ROWGAP;
  }

  // Batched persistence — one PATCH per settings key per layout pass
  if (_pwNodeDirty)  _pwSave('pw_node_overrides',  pwOverrides);
  if (_pwGroupDirty) _pwSave('pw_group_overrides', pwGroupOverrides);

  return { syntheticNodes, syntheticGroups };
}

function renderPingWatchCanvas() {
  if (!isPingWatchPage) return;
  if (dragNode || groupDrag || linkDraw) { _pwRenderPending = true; return; }
  const { syntheticNodes, syntheticGroups } = calcPwLayout(pwDevices);
  nodes = syntheticNodes;
  links = [];
  groups = syntheticGroups;
  nodeMap = Object.fromEntries(nodes.map(n => [n.id, n]));
  groupMap = Object.fromEntries(groups.map(g => [g.id, g]));
  // Inject internet cloud only when at least one link connects to it
  const _hasInternetLinks = pwLinks.some(
    l => l.src_did === PW_INTERNET_DID || l.tgt_did === PW_INTERNET_DID
  );
  if (_hasInternetLinks) {
    const _iOvr = pwOverrides[PW_INTERNET_DID] || {};
    const _iNode = {
      id: 'pw_internet', name: 'Internet', type: 'cloud',
      x: _iOvr.x ?? 60, y: _iOvr.y ?? 60,
      _pwDid: PW_INTERNET_DID,
      properties: { ip_color: '#ffd700' }
    };
    nodes.push(_iNode);
    nodeMap['pw_internet'] = _iNode;
  }
  render();
  const _animL = document.getElementById('anim-layer');
  if (_animL) { _animL.innerHTML = ''; _pwActiveTraces = 0; }
  updateHeaderStats();
  if (selectedEl?.type === 'group') {
    const fg = groupMap[selectedEl.data.id];
    if (fg) showGroupPanel(fg); else showPwDashboardPanel();
  } else if (selectedEl?.type === 'pwlink') {
    showPwLinkPanel(selectedEl.data.id);
  } else if (_selectedPwDid) {
    showPwNodePanel(_selectedPwDid);
  } else if (multiSelect.size > 0) {
    showMultiPanel();
  } else {
    showPwDashboardPanel();
  }
}

function showPwDashboardPanel() {
  const up   = pwDevices.filter(d => d.status === 'up').length;
  const down = pwDevices.filter(d => d.status === 'down').length;
  const unk  = pwDevices.length - up - down;
  document.getElementById('panel-title').textContent = 'PINGWATCH LIVE';
  document.getElementById('panel-icon').textContent = '◉';
  document.getElementById('panel-body').innerHTML = `
    <div class="dash-stat-grid">
      <div class="dash-stat-card">
        <div class="dash-stat-val" style="color:#00ff9d">${up}</div>
        <div class="dash-stat-label">UP</div>
      </div>
      <div class="dash-stat-card">
        <div class="dash-stat-val" style="color:#ff3333">${down}</div>
        <div class="dash-stat-label">DOWN</div>
      </div>
      <div class="dash-stat-card">
        <div class="dash-stat-val" style="color:#888">${unk}</div>
        <div class="dash-stat-label">UNKNOWN</div>
      </div>
    </div>
    <div class="dash-section" style="margin-top:12px">
      <div class="dash-section-title" style="margin-bottom:8px">ACTIVE INCIDENTS</div>
      <div id="pw-incident-list">${_buildIncidentList()}</div>
    </div>
    <div style="margin-top:14px;text-align:center">
      <button class="btn btn-primary" style="font-size:9px;padding:5px 12px;letter-spacing:1px" onclick="loadPingWatchPage()">REFRESH</button>
    </div>
  `;
  document.getElementById('panel-actions').innerHTML = `
    <div style="display:flex;gap:8px;width:100%">
      <button class="btn" style="flex:1;font-size:10px;letter-spacing:1px" onclick="exportPwLayout()">⬇ EXPORT</button>
      <button class="btn" style="flex:1;font-size:10px;letter-spacing:1px" onclick="document.getElementById('pw-layout-import-file').click()">⬆ IMPORT</button>
    </div>
    <button class="btn" style="width:100%;font-size:10px;letter-spacing:1px;margin-top:6px" onclick="resetPwLayout()">↺ RESET LAYOUT</button>
  `;
  // Restart dashboard canvas (paused while node/link panel was showing)
  _resumeDashBg?.();
}

function _buildIncidentList() {
  const downDevs = pwDevices.filter(d => d.status === 'down' && !d.alerts_muted);
  const threshInc = [];
  for (const key of Object.keys(_pwSensorState)) {
    const state = _pwSensorState[key];
    if (state === 'ok') continue;
    const slash = key.indexOf('/');
    const did = key.slice(0, slash);
    const sid = key.slice(slash + 1);
    const dev = _pwDevMap[did];
    if (!dev) continue;
    const sensor = (dev.sensors || []).find(s => s.sensor_id === sid);
    if (!sensor || sensor.alerts_muted || dev.alerts_muted) continue;
    threshInc.push({ dev, sensor, state });
  }
  const critInc = threshInc.filter(x => x.state === 'crit');
  const warnInc = threshInc.filter(x => x.state === 'warn');
  if (!downDevs.length && !critInc.length && !warnInc.length) {
    return `<div class="inc-all-clear">
      <div class="inc-all-clear-icon">✓</div>
      <div class="inc-all-clear-txt">ALL SYSTEMS<br>OPERATIONAL</div>
    </div>`;
  }
  let html = '<div class="inc-list">';
  for (const dev of downDevs) {
    const failed = (dev.sensors || []).filter(s => s.alive === false).map(s => escXml(s.name)).join(' · ');
    html += `<div class="inc-card inc-card-down">
      <div class="inc-card-hdr"><span class="inc-pulse inc-pulse-down"></span><span class="inc-hdr-txt">DEVICE DOWN</span></div>
      <div class="inc-name">${escXml(dev.name)}</div>
      <div class="inc-meta">${escXml(dev.host)}</div>
      ${failed ? `<div class="inc-sensors">${failed}</div>` : ''}
    </div>`;
  }
  for (const { dev, sensor } of critInc) {
    const val = sensor.last_value ? escXml(String(sensor.last_value)) : '';
    html += `<div class="inc-card inc-card-crit">
      <div class="inc-card-hdr"><span class="inc-pulse inc-pulse-crit"></span><span class="inc-hdr-txt">THRESHOLD CRIT</span></div>
      <div class="inc-name">${escXml(dev.name)}</div>
      <div class="inc-meta">${escXml(sensor.name)}</div>
      ${val ? `<div class="inc-val">${val}</div>` : ''}
    </div>`;
  }
  for (const { dev, sensor } of warnInc) {
    const val = sensor.last_value ? escXml(String(sensor.last_value)) : '';
    html += `<div class="inc-card inc-card-warn">
      <div class="inc-card-hdr"><span class="inc-pulse inc-pulse-warn"></span><span class="inc-hdr-txt">THRESHOLD WARN</span></div>
      <div class="inc-name">${escXml(dev.name)}</div>
      <div class="inc-meta">${escXml(sensor.name)}</div>
      ${val ? `<div class="inc-val inc-val-warn">${val}</div>` : ''}
    </div>`;
  }
  html += '</div>';
  return html;
}

function _refreshIncidentList() {
  const el = document.getElementById('pw-incident-list');
  if (el) el.innerHTML = _buildIncidentList();
}

function showPwNodePanel(did) {
  if (did === PW_INTERNET_DID) {
    _selectedPwDid = did;
    document.getElementById('panel-title').textContent = 'INTERNET';
    document.getElementById('panel-icon').textContent = '☁';
    document.getElementById('panel-body').innerHTML = `
      <div class="field-group">
        <div class="field-label">TYPE</div>
        <span style="color:var(--gold);font-family:'Share Tech Mono',monospace;font-size:11px;">Cloud / Internet</span>
      </div>
      <div class="field-group" style="margin-top:8px">
        <div class="field-label">POSITION</div>
        <span style="color:rgba(255,255,255,0.4);font-size:10px;font-family:'Share Tech Mono',monospace">Drag to reposition</span>
      </div>
    `;
    document.getElementById('panel-actions').innerHTML = '';
    return;
  }
  const dev = pwDevices.find(d => d.device_id === did);
  if (!dev) return;
  _selectedPwDid = did;
  const col = pwStatusColor(dev.status);
  const pwNode = nodes.find(n => n._pwDid === did);
  const currentType = pwNode?.type || 'server';
  const _typeOpts = [
    ['switch','Switch'],['bb-switch','Backbone Switch'],['firewall','Firewall'],
    ['wan-switch','WAN Switch'],['server','Server'],['pc','PC / Workstation'],
    ['laptop','Laptop'],['ap','WiFi Access Point'],['connector','Cato Connector'],
    ['remote-pc','Remote PC'],['cloud','Cloud / Internet'],
    ['router','Router / Gateway'],['vm','Virtual Machine'],['appliance','Network Appliance'],
    ['storage','Storage / NAS'],['phone','IP Phone / VoIP'],['camera','IP Camera / CCTV'],
    ['printer','Printer / MFP'],['load-balancer','Load Balancer'],['hypervisor','Hypervisor / ESXi'],
    ['ups','UPS / PDU'],['container','Container Host'],['ipmi','IPMI / BMC'],
  ].map(([v,l])=>`<option value="${v}"${v===currentType?' selected':''}>${l}</option>`).join('');
  const sensors = dev.sensors || [];
  const sRows = sensors.map(s => {
    const sc = s.alive === true ? '#00ff9d' : s.alive === false ? '#ff3333' : '#888';
    const ms = s.last_ms != null ? s.last_ms.toFixed(0) + 'ms' : '—';
    return `<tr>
      <td style="padding:3px 6px;font-size:9px;color:rgba(255,255,255,0.7)">${escXml(s.name)}</td>
      <td style="padding:3px 6px;font-size:9px;color:rgba(0,212,255,0.6)">${escXml(s.stype)}</td>
      <td style="padding:3px 6px;font-size:9px;color:${sc};font-weight:600">${s.alive === true ? 'UP' : s.alive === false ? 'DOWN' : '—'}</td>
      <td style="padding:3px 6px;font-size:9px;color:rgba(255,255,255,0.4)">${ms}</td>
    </tr>`;
  }).join('');
  document.getElementById('panel-title').textContent = dev.name.toUpperCase();
  document.getElementById('panel-icon').textContent = '◉';
  document.getElementById('panel-body').innerHTML = `
    <div class="field-group">
      <div class="field-label">STATUS</div>
      <span style="color:${col};font-family:'Share Tech Mono',monospace;font-size:11px;font-weight:700">${(dev.status||'unknown').toUpperCase()}</span>
    </div>
    <div class="field-group">
      <div class="field-label">HOST / IP</div>
      <span style="color:${col};font-family:'Share Tech Mono',monospace;font-size:11px">${escXml(dev.host)}</span>
    </div>
    ${(dev.secondary_ips||[]).length ? `
    <div class="field-group">
      <div class="field-label">SECONDARY IPS</div>
      <div style="display:flex;flex-direction:column;gap:2px">${(dev.secondary_ips||[]).map(ip=>`<span style="color:rgba(0,212,255,0.7);font-family:'Share Tech Mono',monospace;font-size:10px">${escXml(ip)}</span>`).join('')}</div>
    </div>` : ''}
    <div class="field-group">
      <div class="field-label">GROUP</div>
      <span style="color:rgba(255,255,255,0.5);font-family:'Share Tech Mono',monospace;font-size:10px">${escXml(dev.group||'Default Group')}</span>
    </div>
    <div class="field-group">
      <div class="field-label">DEVICE ICON</div>
      <select style="background:#0d1a2e;color:#e2e8f0;border:1px solid rgba(0,212,255,0.3);border-radius:4px;padding:4px 6px;font-family:'Share Tech Mono',monospace;font-size:10px;width:100%;cursor:pointer"
             onchange="setPwNodeType('${did}',this.value)">${_typeOpts}</select>
    </div>
    ${sensors.length ? `
    <div class="dash-section" style="margin-top:10px">
      <div class="dash-section-title" style="margin-bottom:4px">SENSORS</div>
      <table style="width:100%;border-collapse:collapse">
        <thead><tr>
          <th style="padding:2px 6px;font-family:'Share Tech Mono',monospace;font-size:8px;color:rgba(0,212,255,0.4);text-align:left;font-weight:400">NAME</th>
          <th style="padding:2px 6px;font-family:'Share Tech Mono',monospace;font-size:8px;color:rgba(0,212,255,0.4);text-align:left;font-weight:400">TYPE</th>
          <th style="padding:2px 6px;font-family:'Share Tech Mono',monospace;font-size:8px;color:rgba(0,212,255,0.4);text-align:left;font-weight:400">ST</th>
          <th style="padding:2px 6px;font-family:'Share Tech Mono',monospace;font-size:8px;color:rgba(0,212,255,0.4);text-align:left;font-weight:400">MS</th>
        </tr></thead>
        <tbody>${sRows}</tbody>
      </table>
    </div>` : ''}
    <div class="field-group" style="margin-top:12px">
      <div class="field-label">COLOR OVERRIDE</div>
      <div style="display:flex;align-items:center;gap:8px;">
        <input type="color" value="${pwOverrides[did]?.color || '#00d4ff'}"
               onchange="setPwNodeColor('${did}',this.value)"
               style="width:36px;height:24px;cursor:pointer;border:none;background:none;padding:0"/>
        ${pwOverrides[did]?.color
          ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="resetPwNodeColor('${did}')">Reset</button>`
          : `<span style="color:rgba(255,255,255,0.3);font-size:10px;font-family:'Share Tech Mono',monospace">auto (status color)</span>`}
      </div>
    </div>
  `;
  document.getElementById('panel-actions').innerHTML = '';
}

function startPwSSE() {
  stopPwSSE();
  try {
    pwSSE = new EventSource('/events');
    pwSSE.addEventListener('device_status', e => {
      if (!isPingWatchPage || !_ntmVisible) return;
      const d = JSON.parse(e.data);
      const dev = _pwDevMap[d.did];
      if (dev) { dev.status = d.status; _schedulePwLiveUpdate(dev.device_id); }
    });
    pwSSE.addEventListener('sensor', e => {
      if (!isPingWatchPage || !_ntmVisible) return;
      const s = JSON.parse(e.data);
      const dev = _pwDevMap[s.device_id];
      if (dev) {
        const idx = (dev.sensors||[]).findIndex(x => x.sensor_id === s.sensor_id);
        if (idx >= 0) dev.sensors[idx] = s; else (dev.sensors = dev.sensors||[]).push(s);
        _pwFireTrace(s.device_id, s.alive);
      }
      // Keep threshold state map in sync from sensor SSE (initial + ongoing)
      const key = `${s.device_id}/${s.sensor_id}`;
      if (s.threshold_state) _pwSensorState[key] = s.threshold_state;
    });
    // Threshold events → re-color any PW link assigned to that sensor
    ['threshold_critical', 'threshold_warning', 'threshold_ok'].forEach(evt => {
      pwSSE.addEventListener(evt, e => {
        if (!isPingWatchPage || !_ntmVisible) return;
        const d = JSON.parse(e.data);
        const state = evt === 'threshold_ok' ? 'ok' : evt === 'threshold_warning' ? 'warn' : 'crit';
        _pwSensorThresholdUpdate(d.did, d.sid, state);
      });
    });
  } catch(e) {}
}

function _pwSensorThresholdUpdate(did, sid, state) {
  const key = `${did}/${sid}`;
  _pwSensorState[key] = state;
  pwLinks.filter(lk => lk.sensor_in === key || lk.sensor_out === key).forEach(lk => {
    const lineEl = document.querySelector(`[data-pwlid="${CSS.escape(lk.id)}"] .link-main`);
    if (!lineEl) return;
    const srcD = _pwDevMap[lk.src_did], tgtD = _pwDevMap[lk.tgt_did];
    _pwApplyLinkEl(lineEl, lk, srcD, tgtD);
  });
  if (!_selectedPwDid && !selectedEl && !_pwInputFocused()) _refreshIncidentList();
}

function stopPwSSE() {
  if (pwSSE) { try { pwSSE.close(); } catch(e) {} pwSSE = null; }
}

// ── Connect device to Internet cloud ──────────────────────────────────────────

function connectDeviceToInternet(pwDid) {
  const did = String(pwDid);
  // Already connected?
  if (pwLinks.some(l =>
    (String(l.src_did) === did && l.tgt_did === PW_INTERNET_DID) ||
    (l.src_did === PW_INTERNET_DID && String(l.tgt_did) === did)
  )) {
    toast('Already connected to Internet');
    return;
  }
  // Place cloud above all nodes (centered horizontally, above topmost node)
  if (!pwOverrides[PW_INTERNET_DID]) {
    const xs = nodes.map(n => n.x).filter(x => isFinite(x));
    const ys = nodes.map(n => n.y).filter(y => isFinite(y));
    const cx = xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : 60;
    const topY = ys.length ? Math.min(...ys) : 60;
    pwOverrides[PW_INTERNET_DID] = { x: Math.max(20, cx - 30), y: Math.max(20, topY - 180) };
    _pwSave('pw_node_overrides', pwOverrides);
  }
  // Create internet link
  const newLink = {
    id: 'pwl_' + Date.now(),
    src_did: did, tgt_did: PW_INTERNET_DID,
    link_type: 'internet', label: ''
  };
  pwLinks.push(newLink);
  _pwSave('pw_links', pwLinks);
  renderPingWatchCanvas();
  toast('Connected to Internet');
}

// ═══════════════════════════ PACKET TRACE ANIMATION ═══════════════════════════

function _pwGetTraceSrc() {
  if (_pwTraceSrcDid) return String(_pwTraceSrcDid);
  const pw = pwDevices.find(d => /pingwatch/i.test(d.name));
  return pw ? String(pw.device_id) : null;
}

function _pwSetTraceSrc(did) {
  _pwTraceSrcDid = String(did);
  const dev = pwDevices.find(d => String(d.device_id) === _pwTraceSrcDid);
  toast('Trace source: ' + (dev?.name || did));
}

function _pwFindPath(fromDid, toDid) {
  if (fromDid === toDid) return null;
  const adj = {};
  pwLinks.forEach(lk => {
    const s = String(lk.src_did), t = String(lk.tgt_did);
    (adj[s] = adj[s] || []).push(t);
    (adj[t] = adj[t] || []).push(s);
  });
  const visited = new Set([fromDid]);
  const queue = [[fromDid, [fromDid]]];
  while (queue.length) {
    const [curr, path] = queue.shift();
    if (curr === toDid) return path;
    for (const next of (adj[curr] || [])) {
      if (!visited.has(next)) { visited.add(next); queue.push([next, [...path, next]]); }
    }
  }
  return null;
}

// ── Trace animation performance limits ───────────────────────────────────────
// Max 3 concurrent dots (was 6) — each runs its own rAF loop at 30fps
const PW_MAX_TRACES = 2;
// Per-device cooldown: only one trace per device per 6 seconds.
// With 62 devices probing every 5s, fewer concurrent traces = less RAF pressure.
const _pwTraceCooldown = 6000;
const _pwTraceLastFired = new Map();

function _pwFireTrace(toDid, alive) {
  if (_pwActiveTraces >= PW_MAX_TRACES) return;
  const now = Date.now();
  if ((now - (_pwTraceLastFired.get(toDid) || 0)) < _pwTraceCooldown) return;
  _pwTraceLastFired.set(toDid, now);
  const srcDid = _pwGetTraceSrc();
  if (!srcDid || String(srcDid) === String(toDid)) return;
  const path = _pwFindPath(srcDid, String(toDid));
  if (!path || path.length < 2) return;
  const color = alive === true ? '#00ff9d' : alive === false ? '#ff3333' : '#aaaaaa';
  _pwAnimateTrace(path, color);
}

function _pwAnimateTrace(pathDids, color) {
  const pts = pathDids.map(did => {
    const node = nodeMap[_pwNodeId(did)];
    return node ? nodeCenter(node) : null;
  });
  // Abort if any hop resolves to null (stale link pointing at a deleted device).
  // Silently filtering nulls would draw an arc that skips intermediate nodes.
  if (pts.some(p => p === null)) return;
  if (pts.length < 2) return;
  const layer = document.getElementById('anim-layer');
  if (!layer) return;
  _pwActiveTraces++;
  const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  dot.setAttribute('r', '5');
  dot.setAttribute('fill', color);
  dot.style.filter = `drop-shadow(0 0 5px ${color})`;
  dot.style.pointerEvents = 'none';
  layer.appendChild(dot);
  const segDur = 250;
  // Throttle trace rAF to 30fps (was 60fps) — halves SVG write rate
  const _TRACE_MS = 1000 / 30;
  let segIdx = 0, startTs = null;
  function step(ts) {
    if (!startTs) startTs = ts;
    const t = Math.min((ts - startTs) / segDur, 1);
    const p0 = pts[segIdx], p1 = pts[segIdx + 1];
    dot.setAttribute('cx', (p0.x + (p1.x - p0.x) * t).toFixed(1));
    dot.setAttribute('cy', (p0.y + (p1.y - p0.y) * t).toFixed(1));
    if (t < 1) {
      setTimeout(() => requestAnimationFrame(step), _TRACE_MS);
    } else if (segIdx + 2 < pts.length) {
      segIdx++; startTs = ts;
      setTimeout(() => requestAnimationFrame(step), _TRACE_MS);
    } else {
      dot.remove();
      _pwBurstAt(pts[pts.length - 1], color, layer);
      _pwActiveTraces--;
    }
  }
  requestAnimationFrame(step);
}

function _pwBurstAt(pt, color, layer) {
  const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  ring.setAttribute('cx', pt.x); ring.setAttribute('cy', pt.y);
  ring.setAttribute('r', '6'); ring.setAttribute('fill', 'none');
  ring.setAttribute('stroke', color); ring.setAttribute('stroke-width', '2');
  ring.style.pointerEvents = 'none';
  layer.appendChild(ring);
  let t0 = null;
  const dur = 400, _BURST_MS = 1000 / 30; // 30fps burst
  function expand(ts) {
    if (!t0) t0 = ts;
    const t = (ts - t0) / dur;
    if (t >= 1) { ring.remove(); return; }
    ring.setAttribute('r', (6 + t * 15).toFixed(1));
    ring.style.opacity = (1 - t).toFixed(2);
    setTimeout(() => requestAnimationFrame(expand), _BURST_MS);
  }
  requestAnimationFrame(expand);
}

function _pwInputFocused() {
  const el = document.activeElement;
  return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT')
             && !!document.getElementById('side-panel')?.contains(el);
}

function _pwLiveUpdate(did) {
  const node = nodeMap['pw_' + did];
  const dev  = _pwDevMap[did];
  if (!node || !dev) return;

  // ── Status-change guard — skip DOM work if nothing changed ──
  if (node._lastPwStatus === dev.status) return;
  node._lastPwStatus = dev.status;

  const col = pwStatusColor(dev.status);
  const nameColor = dev.status === 'down'    ? '#ff9999'
                  : dev.status === 'unknown' ? '#888888' : undefined;
  const ovr = pwOverrides[did];
  node.properties.ip_color   = col;
  node.properties.name_color = nameColor;
  if (!ovr?.color) {
    node.properties.color = dev.status === 'down'    ? '#ff3333'
                          : dev.status === 'unknown' ? '#888888' : undefined;
  }

  // ── In-place SVG attribute update (avoids full innerHTML rebuild) ──
  const el = document.getElementById('node-' + node.id);
  if (el) {
    // Update IP text color
    const ipEl = el.querySelector('[data-pw-ip]');
    if (ipEl) ipEl.setAttribute('fill', col);

    // Update name text color
    const nameEl = el.querySelector('[data-pw-name]');
    if (nameEl) nameEl.setAttribute('fill', nameColor || nameEl.getAttribute('data-pw-origfill') || '#e0e0e0');

    // Update color filter (hue-rotate) on existing wrapper <g>
    const innerG = el.firstElementChild;
    if (innerG) {
      const filterG = innerG.querySelector('g[style]');
      if (node.properties.color) {
        const deg = Math.round(getHue(node.properties.color) - 37);
        const f = 'grayscale(1) sepia(1) hue-rotate('+deg+'deg) saturate(2.5) brightness(1.05)';
        if (filterG) {
          filterG.style.filter = f;
        } else {
          // Wrapper doesn't exist yet (device was UP at render time) — create it now
          _applyNodeColorFilter(el, node);
        }
      } else if (filterG) {
        filterG.style.filter = '';
      }
    }
    // No innerHTML rebuild, no event listener teardown/reattach
  }

  // Update PW tab dot color
  const dot = document.getElementById('pw-tab-dot');
  if (dot) {
    const anyDown = pwDevices.some(d => d.status === 'down');
    dot.style.background = anyDown ? '#ff3333' : '#00ff9d';
    dot.style.boxShadow  = anyDown ? '0 0 5px #ff3333' : '0 0 5px #00ff9d';
  }

  // Update connected link stroke colors
  pwLinks
    .filter(lk => lk.src_did === did || lk.tgt_did === did)
    .forEach(lk => {
      const lineEl = document.querySelector(`[data-pwlid="${CSS.escape(lk.id)}"] .link-main`);
      if (!lineEl) return;
      const otherDid = lk.src_did === did ? lk.tgt_did : lk.src_did;
      const otherDev = _pwDevMap[otherDid];
      const srcD = lk.src_did === did ? dev : otherDev;
      const tgtD = lk.src_did === did ? otherDev : dev;
      _pwApplyLinkEl(lineEl, lk, srcD, tgtD);
    });

  // Update right panel only if relevant and no input is focused
  if (_selectedPwDid === did && !_pwInputFocused()) {
    showPwNodePanel(did);
  } else if (!_selectedPwDid && !selectedEl && multiSelect.size === 0 && !_pwInputFocused()) {
    showPwDashboardPanel();
  }
}

// ═══════════════════════════ API ═══════════════════════════
// NOTE: This is an iframe-local copy. The canonical version lives in app.js.
// Keep the two implementations in sync if you change error handling.
async function api(method, path, body) {
  const r = await fetch(path, {
    method, credentials: 'include', headers: {'Content-Type':'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) {
    // Iframe can't show login; reload top-level window so the parent's auth flow runs
    if (window.top && window.top !== window) window.top.location.reload();
    return {};
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(err.error || r.statusText);
  }
  return r.json();
}

// Fire-and-forget save for PingWatch settings.
// Uses keepalive:true so the request survives page refresh,
// and shows a toast on failure so errors are never silent.
function _pwSave(key, value) {
  fetch('/api/settings/' + key, {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
    keepalive: true,
  }).then(r => {
    if (!r.ok) r.json().catch(() => ({ error: r.statusText }))
               .then(e => toast('⚠ Save failed (' + key + '): ' + (e.error || r.statusText)));
  }).catch(e => toast('⚠ Save failed (' + key + '): ' + e.message));
}

async function loadData() {
  const gen = ++_pageGen;
  try {
    const [nodesData, linksData, settingsData, groupsData] = await Promise.all([
      api('GET', `/api/nodes?page=${currentPageId}`),
      api('GET', `/api/links?page=${currentPageId}`),
      api('GET', '/api/settings/vlan_colors').catch(() => null),
      api('GET', `/api/groups?page=${currentPageId}`).catch(() => []),
    ]);
    if (gen !== _pageGen) return; // superseded by a newer tab switch
    nodes = nodesData;
    links = linksData;
    groups = groupsData;
    if (settingsData?.value) VLAN_COLORS = settingsData.value;
    nodeMap = Object.fromEntries(nodes.map(n => [n.id, n]));
    groupMap = Object.fromEntries(groups.map(g => [g.id, g]));
    applyVlanStyles();
    render();
    filterNodes(document.getElementById('search-input')?.value || '');
    updateHeaderStats();
    if (!selectedEl) showDashboardPanel();
  } catch (e) {
    if (gen === _pageGen) toast('⚠ Failed to load: ' + e.message);
  }
}

// Injects/updates a <style> block so CSS .tag.vlanXX classes stay in sync
function applyVlanStyles() {
  let el = document.getElementById('vlan-dynamic-styles');
  if (!el) {
    el = document.createElement('style');
    el.id = 'vlan-dynamic-styles';
    document.head.appendChild(el);
  }
  el.textContent = Object.entries(VLAN_COLORS).map(([id, c]) =>
    `.tag.vlan${id} { color:${c}; border-color:${c}; }`
  ).join('\n');
  refreshVlanDatalist();
}
const LINK_CFG = {
  trunk:    { stroke:'#00ff9d', cls:'link-trunk',    marker:'arr-green',  width:2.5 },
  access:   { stroke:'#00d4ff', cls:'link-access',   marker:'arr-blue',   width:2   },
  ha:       { stroke:'#ff3366', cls:'link-ha',        marker:'arr-red',    width:2   },
  internet: { stroke:'#ffd700', cls:'link-internet',  marker:'arr-gold',   width:1.5 },
  wifi:     { stroke:'#a855f7', cls:'link-wifi',      marker:'arr-purple', width:1.5 },
  tunnel:   { stroke:'#a855f7', cls:'link-tunnel',    marker:'arr-purple', width:1.5 },
};
function lcfg(t) { return LINK_CFG[t] || LINK_CFG.access; }

// ═══════════════════════════ NODE SIZE ═══════════════════════════
const NODE_SIZE = {
  cloud:      { w:226,  h:100 },
  'remote-pc':{ w:170,  h:55  },
  'wan-switch':{ w:220, h:60  },
  firewall:   { w:155,  h:76  },
  'bb-switch':{ w:300,  h:88  },
  switch:     { w:230,  h:74  },
  connector:  { w:165,  h:68  },
  ap:         { w:170,  h:62  },
  server:     { w:170,  h:95  },
  pc:         { w:145,  h:56  },
  laptop:     { w:180,  h:58  },
  'info-box': { w:210,  h:120 },
  router:     { w:160,  h:68  },
  vm:         { w:160,  h:68  },
  appliance:  { w:160,  h:68  },
  storage:    { w:160,  h:68  },
  phone:      { w:160,  h:68  },
  camera:     { w:160,  h:68  },
  printer:    { w:160,  h:68  },
  'load-balancer':{ w:160, h:68 },
  hypervisor: { w:160,  h:68  },
  ups:        { w:160,  h:68  },
  container:  { w:160,  h:68  },
  ipmi:       { w:160,  h:68  },
};
function nsize(type, node) {
  if (type === 'info-box' && node) {
    const lines = Array.isArray(node.properties?.lines) ? node.properties.lines : [];
    const h = Math.max(80, 25 + lines.length * 14 + 10);
    return { w: 210, h };
  }
  if (!node) return NODE_SIZE[type] || { w: 160, h: 60 };
  const vH = _vlanH(node.properties);
  switch (type) {
    case 'ap':         return { w: 170, h: 62 + vH };
    case 'connector':  return { w: 165, h: 68 + vH };
    case 'firewall':   return { w: 155, h: 76 + vH };
    case 'wan-switch': return { w: 220, h: 60 + vH };
    case 'switch':     return { w: 230, h: 74 + Math.max(0, vH - 16) };
    case 'router':     return { w: 160, h: 68 + vH };
    case 'vm':         return { w: 160, h: 68 + vH };
    case 'appliance':  return { w: 160, h: 68 + vH };
    default: return NODE_SIZE[type] || { w: 160, h: 60 };
  }
}

// ═══════════════════════════ LINK ENDPOINTS ═══════════════════════════
function nodeCenter(node) {
  const s = nsize(node.type, node);
  return { x: node.x + s.w/2, y: node.y + s.h/2 };
}

// ═══════════════════════════ SMART NODE PLACEMENT (PW LIVE) ═══════════════════════════
// Axis-aligned rectangle overlap test with optional padding
function _rectsOverlap(a, b, pad = 0) {
  return !(a.x + a.w + pad <= b.x ||
           b.x + b.w + pad <= a.x ||
           a.y + a.h + pad <= b.y ||
           b.y + b.h + pad <= a.y);
}

// Row-major scan of a group's interior for a non-colliding slot.
// groupRect: {x, y, w, h}   occupied: array of {x, y, w, h}
// Returns {x, y} or null if nothing fits.
function _findFreeSlotInGroup(groupRect, occupied, newW, newH) {
  const INNER_PAD = 24;    // distance from group border
  const TITLE_H   = 28;    // group title bar
  const STEP      = 20;    // candidate grid step (px)
  const COLLIDE_PAD = 12;  // breathing room between nodes
  const x0 = groupRect.x + INNER_PAD;
  const y0 = groupRect.y + INNER_PAD + TITLE_H;
  const x1 = groupRect.x + groupRect.w - INNER_PAD - newW;
  const y1 = groupRect.y + groupRect.h - INNER_PAD - newH;
  if (x1 < x0 || y1 < y0) return null; // group smaller than the node
  for (let y = y0; y <= y1; y += STEP) {
    for (let x = x0; x <= x1; x += STEP) {
      const r = { x, y, w: newW, h: newH };
      let hit = false;
      for (const o of occupied) {
        if (_rectsOverlap(r, o, COLLIDE_PAD)) { hit = true; break; }
      }
      if (!hit) return { x, y };
    }
  }
  return null;
}

// ═══════════════════════════ RENDER ═══════════════════════════
function render() {
  resizeSVG();
  renderGroups();
  renderLinks();
  renderNodes();
}

function resizeSVG() { /* no-op: zoom/pan via viewport group */ }

function renderLinks() {
  const layer = document.getElementById('links-layer');
  const lblLayer = document.getElementById('link-labels-layer');
  layer.innerHTML = '';
  lblLayer.innerHTML = '';

  // Detect parallel links — group by sorted node pair
  const pairCount = {}, pairSeen = {}, pairIndex = {};
  for (const lk of links) {
    const key = Math.min(lk.source_id,lk.target_id)+','+Math.max(lk.source_id,lk.target_id);
    pairCount[key] = (pairCount[key]||0) + 1;
  }
  for (const lk of links) {
    const key = Math.min(lk.source_id,lk.target_id)+','+Math.max(lk.source_id,lk.target_id);
    pairSeen[key] = pairSeen[key]||0;
    pairIndex[lk.id] = pairSeen[key]++;
  }

  links.forEach((lk, globalIdx) => {
    const src = nodeMap[lk.source_id];
    const tgt = nodeMap[lk.target_id];
    if (!src || !tgt) return;
    const idx = pairIndex[lk.id];
    // Line only (no label)
    const g = document.createElementNS('http://www.w3.org/2000/svg','g');
    g.setAttribute('class','link-g');
    g.setAttribute('data-id', lk.id);
    g.innerHTML = buildLink(src, tgt, lk, idx, globalIdx, true);
    g.addEventListener('click', e => { e.stopPropagation(); selectLink(lk); });
    if (selectedEl && selectedEl.type==='link' && selectedEl.data.id===lk.id) {
      g.querySelector('.link-main')?.setAttribute('stroke-width', (parseFloat(g.querySelector('.link-main')?.getAttribute('stroke-width')||2)+2)+'');
    }
    layer.appendChild(g);
    // Label in top layer
    const lblSvg = buildLinkLabel(src, tgt, lk, idx, globalIdx);
    if (lblSvg) {
      const lg = document.createElementNS('http://www.w3.org/2000/svg','g');
      lg.setAttribute('class','link-label-g');
      lg.setAttribute('data-id', lk.id);
      lg.innerHTML = lblSvg;
      lg.addEventListener('click', e => { e.stopPropagation(); selectLink(lk); });
      lblLayer.appendChild(lg);
    }
  });
  if (isPingWatchPage) renderPwLinksInLayer(layer, lblLayer);
}

function _pwNodeId(did) {
  return did === PW_INTERNET_DID ? 'pw_internet' : 'pw_' + did;
}
function _pwDevName(did) {
  if (did === PW_INTERNET_DID) return 'Internet';
  const d = pwDevices.find(x => String(x.device_id) === String(did));
  return d ? d.name : String(did);
}

function renderPwLinksInLayer(layer, lblLayer) {
  const tArr = [0.30, 0.50, 0.70];
  let pwIdx = 0;
  pwLinks.forEach(lk => {
    const src = nodeMap[_pwNodeId(lk.src_did)];
    const tgt = nodeMap[_pwNodeId(lk.tgt_did)];
    if (!src || !tgt) return;
    const sc = nodeCenter(src), tc = nodeCenter(tgt);
    const cfg = lcfg(lk.link_type || 'trunk');
    const srcDev = pwDevices.find(d => d.device_id === lk.src_did);
    const tgtDev = pwDevices.find(d => d.device_id === lk.tgt_did);
    const stroke = _pwLinkStroke(lk, srcDev, tgtDev);
    const bwCls  = stroke === '#a855f7' ? 'pw-bw-crit' : stroke === '#c084fc' ? 'pw-bw-warn' : '';
    const lw     = stroke === '#a855f7' ? cfg.width * 2.5 : stroke === '#c084fc' ? cfg.width * 1.8 : cfg.width;
    const gg = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    gg.setAttribute('class', 'link-g pw-link');
    gg.setAttribute('data-pwlid', lk.id);
    gg.innerHTML = `
      <line class="link-hit" x1="${sc.x}" y1="${sc.y}" x2="${tc.x}" y2="${tc.y}" stroke="transparent" stroke-width="12"/>
      <line class="link-main ${cfg.cls} ${bwCls}" x1="${sc.x}" y1="${sc.y}" x2="${tc.x}" y2="${tc.y}"
        stroke="${stroke}" stroke-width="${lw}" marker-end="url(#${cfg.marker})" opacity="0.8"/>
    `;
    gg.addEventListener('click', e => { e.stopPropagation(); showPwLinkPanel(lk.id); });
    layer.appendChild(gg);
    // Label in top layer
    if (lk.label && lblLayer) {
      const lbw = lk.label.length * 5.4 + 4;
      const pos = _pickLabelPos(sc, tc, lbw, tArr[pwIdx % 3]);
      const lg = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      lg.setAttribute('class','link-label-g pw-link-label');
      lg.innerHTML = `<rect x="${(pos.lbx-2)}" y="${(pos.lby-8)}" width="${lbw.toFixed(0)}" height="11" rx="2" fill="rgba(5,10,20,0.82)"/><text x="${pos.lbx}" y="${pos.lby}" fill="${cfg.stroke}" font-family="Share Tech Mono" font-size="9" opacity="0.9">${escXml(lk.label)}</text>`;
      lg.addEventListener('click', e => { e.stopPropagation(); showPwLinkPanel(lk.id); });
      lblLayer.appendChild(lg);
    }
    pwIdx++;
  });
}

function buildLink(src, tgt, lk, idx=0, globalIdx=0, noLabel=false) {
  const c1 = nodeCenter(src);
  const c2 = nodeCenter(tgt);

  if (lk.link_type === 'tunnel') {
    return buildTunnel(c1, c2, lk);
  }
  const cfg = lcfg(lk.link_type);
  const sel = (selectedEl?.type==='link' && selectedEl?.data.id===lk.id);
  const w = sel ? cfg.width+2 : cfg.width;

  if (idx === 0) {
    // First (or only) link — always straight
    const tArr = [0.30, 0.50, 0.70];
    const tL   = tArr[globalIdx % 3];
    const ldx  = c2.x - c1.x, ldy = c2.y - c1.y;
    const llen = Math.sqrt(ldx*ldx + ldy*ldy) || 1;
    const lnx  = -ldy/llen, lny = ldx/llen;
    const lbx  = (c1.x + ldx*tL + lnx*8).toFixed(1);
    const lby  = (c1.y + ldy*tL + lny*8 - 2).toFixed(1);
    const lbw  = lk.label ? (lk.label.length * 5.4 + 4).toFixed(0) : 0;
    return `
      <line class="link-hit" x1="${c1.x}" y1="${c1.y}" x2="${c2.x}" y2="${c2.y}" stroke="transparent" stroke-width="12"/>
      <line class="link-main ${cfg.cls}" x1="${c1.x}" y1="${c1.y}" x2="${c2.x}" y2="${c2.y}"
        stroke="${cfg.stroke}" stroke-width="${w}" marker-end="url(#${cfg.marker})" opacity="0.8"/>
      ${(!noLabel && lk.label) ? `<rect x="${(lbx-2)}" y="${(lby-8)}" width="${lbw}" height="11" rx="2" fill="rgba(5,10,20,0.82)"/><text x="${lbx}" y="${lby}" fill="${cfg.stroke}" font-family="Share Tech Mono" font-size="9" opacity="0.9">${escXml(lk.label)}</text>` : ''}
    `;
  }

  // Subsequent parallel links — curve away from the straight one, one side only
  const dx = c2.x-c1.x, dy = c2.y-c1.y;
  const len = Math.sqrt(dx*dx+dy*dy)||1;
  const nx = -dy/len, ny = dx/len;
  const off = idx * 50;
  const qx = (c1.x+c2.x)/2 + nx*off;
  const qy = (c1.y+c2.y)/2 + ny*off;
  const tL = 0.65;
  const lbx = ((1-tL)*(1-tL)*c1.x + 2*(1-tL)*tL*qx + tL*tL*c2.x + nx*10 + 4).toFixed(1);
  const lby = ((1-tL)*(1-tL)*c1.y + 2*(1-tL)*tL*qy + tL*tL*c2.y + ny*10 - 4).toFixed(1);
  const lbwP = lk.label ? (lk.label.length * 5.4 + 4).toFixed(0) : 0;
  return `
    <path class="link-hit" d="M${c1.x},${c1.y} Q${qx.toFixed(1)},${qy.toFixed(1)} ${c2.x},${c2.y}" fill="none" stroke="transparent" stroke-width="12"/>
    <path class="link-main ${cfg.cls}" d="M${c1.x},${c1.y} Q${qx.toFixed(1)},${qy.toFixed(1)} ${c2.x},${c2.y}"
      fill="none" stroke="${cfg.stroke}" stroke-width="${w}" marker-end="url(#${cfg.marker})" opacity="0.8"/>
    ${(!noLabel && lk.label) ? `<rect x="${(lbx-2)}" y="${(lby-8)}" width="${lbwP}" height="11" rx="2" fill="rgba(5,10,20,0.82)"/><text x="${lbx}" y="${lby}" fill="${cfg.stroke}" font-family="Share Tech Mono" font-size="9" opacity="0.9">${escXml(lk.label)}</text>` : ''}
  `;
}

// Returns true if label rect overlaps any node bounding box
function _labelHitsAnyNode(rx, ry, rw, rh) {
  for (const node of nodes) {
    const sz = nsize(node.type, node);
    if (rx < node.x + sz.w && rx + rw > node.x &&
        ry < node.y + sz.h && ry + rh > node.y) return true;
  }
  return false;
}

// Tries candidate t-positions and returns the first that avoids all node boxes
function _pickLabelPos(c1, c2, lbw, tPrefer, qx, qy) {
  const ldx = c2.x - c1.x, ldy = c2.y - c1.y;
  const llen = Math.sqrt(ldx*ldx + ldy*ldy) || 1;
  const lnx = -ldy/llen, lny = ldx/llen;
  for (const t of [tPrefer, 0.40, 0.60, 0.30, 0.70, 0.20, 0.80, 0.10, 0.90]) {
    const px = qx !== undefined
      ? (1-t)*(1-t)*c1.x + 2*(1-t)*t*qx + t*t*c2.x
      : c1.x + ldx*t;
    const py = qx !== undefined
      ? (1-t)*(1-t)*c1.y + 2*(1-t)*t*qy + t*t*c2.y
      : c1.y + ldy*t;
    const lbx = px + lnx*8, lby = py + lny*8 - 2;
    if (!_labelHitsAnyNode(lbx - 2, lby - 8, lbw, 11))
      return { lbx: lbx.toFixed(1), lby: lby.toFixed(1) };
  }
  // fallback: preferred position regardless
  const t = tPrefer;
  const px = qx !== undefined ? (1-t)*(1-t)*c1.x+2*(1-t)*t*qx+t*t*c2.x : c1.x+ldx*t;
  const py = qx !== undefined ? (1-t)*(1-t)*c1.y+2*(1-t)*t*qy+t*t*c2.y : c1.y+ldy*t;
  return { lbx: (px+lnx*8).toFixed(1), lby: (py+lny*8-2).toFixed(1) };
}

function buildLinkLabel(src, tgt, lk, idx, globalIdx) {
  if (!lk.label || lk.link_type === 'tunnel') return '';
  const c1 = nodeCenter(src), c2 = nodeCenter(tgt);
  const cfg = lcfg(lk.link_type);
  const lbw = lk.label.length * 5.4 + 4;

  let pos;
  if (idx === 0) {
    pos = _pickLabelPos(c1, c2, lbw, 0.50);
  } else {
    const dx = c2.x-c1.x, dy = c2.y-c1.y, len = Math.sqrt(dx*dx+dy*dy)||1;
    const nx = -dy/len, ny = dx/len;
    pos = _pickLabelPos(c1, c2, lbw, 0.65,
      (c1.x+c2.x)/2 + nx*idx*50, (c1.y+c2.y)/2 + ny*idx*50);
  }
  return `<rect x="${(pos.lbx-2)}" y="${(pos.lby-8)}" width="${lbw.toFixed(0)}" height="11" rx="2" fill="rgba(5,10,20,0.82)"/><text x="${pos.lbx}" y="${pos.lby}" fill="${cfg.stroke}" font-family="Share Tech Mono" font-size="9" opacity="0.9">${escXml(lk.label)}</text>`;
}

function buildTunnel(p1, p2, lk) {
  const dx=p2.x-p1.x, dy=p2.y-p1.y, len=Math.sqrt(dx*dx+dy*dy)||1;
  const nx=-dy/len*7, ny=dx/len*7;
  const mx=(p1.x+p2.x)/2, my=(p1.y+p2.y)/2;
  const ux=dx/len, uy=dy/len;
  const nties=Math.max(2,Math.floor(len/30));
  let ties='';
  for(let i=1;i<nties;i++){
    const t=i/nties;
    const tx=p1.x+dx*t, ty=p1.y+dy*t;
    ties+=`<line x1="${(tx+nx).toFixed(1)}" y1="${(ty+ny).toFixed(1)}" x2="${(tx-nx).toFixed(1)}" y2="${(ty-ny).toFixed(1)}" stroke="#a855f7" stroke-width="1" opacity="0.4"/>`;
  }
  const ax1=(p2.x-ux*10+nx*0.9).toFixed(1), ay1=(p2.y-uy*10+ny*0.9).toFixed(1);
  const ax2=(p2.x-ux*10-nx*0.9).toFixed(1), ay2=(p2.y-uy*10-ny*0.9).toFixed(1);
  const bx=(mx+ny*10+15).toFixed(1), by=(my-nx*10-20).toFixed(1);
  return `
    <line class="link-hit" x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" stroke="transparent" stroke-width="16"/>
    <line x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" stroke="#c084fc" stroke-width="14" opacity="0.05"/>
    <line class="link-main link-tunnel"  x1="${(p1.x+nx).toFixed(1)}" y1="${(p1.y+ny).toFixed(1)}" x2="${(p2.x+nx).toFixed(1)}" y2="${(p2.y+ny).toFixed(1)}" stroke="#a855f7" stroke-width="1.5" opacity="0.7"/>
    <line class="link-tunnel2" x1="${(p1.x-nx).toFixed(1)}" y1="${(p1.y-ny).toFixed(1)}" x2="${(p2.x-nx).toFixed(1)}" y2="${(p2.y-ny).toFixed(1)}" stroke="#a855f7" stroke-width="1.5" opacity="0.7"/>
    ${ties}
    <polygon points="${p2.x.toFixed(1)},${p2.y.toFixed(1)} ${ax1},${ay1} ${ax2},${ay2}" fill="#a855f7" opacity="0.85"/>
    <g transform="translate(${(mx-20).toFixed(1)},${(my-13).toFixed(1)})">
      <rect x="0" y="0" width="40" height="26" rx="3" fill="rgba(10,5,25,0.92)" stroke="#a855f7" stroke-width="1.5"/>
      <path d="M11,0 Q11,-10 20,-10 Q29,-10 29,0" fill="none" stroke="#a855f7" stroke-width="2"/>
      <circle cx="20" cy="13" r="4" fill="none" stroke="#c084fc" stroke-width="1.5"/>
      <rect x="18" y="13" width="4" height="6" rx="1" fill="#c084fc" opacity="0.7"/>
    </g>
    <g transform="translate(${bx},${by})">
      <rect x="0" y="0" width="90" height="34" rx="3" fill="rgba(10,5,25,0.85)" stroke="rgba(168,85,247,0.5)" stroke-width="1"/>
      <text x="45" y="13" text-anchor="middle" fill="#c084fc" font-family="Orbitron" font-size="8" letter-spacing="1">ZTNA TUNNEL</text>
      <text x="45" y="26" text-anchor="middle" fill="rgba(168,85,247,0.6)" font-family="Share Tech Mono" font-size="8">🔒 ENCRYPTED</text>
    </g>
  `;
}

function _applyNodeColorFilter(el, node) {
  if (!node.properties?.color) return;
  const deg = Math.round(getHue(node.properties.color) - 37);
  const filterStr = `grayscale(1) sepia(1) hue-rotate(${deg}deg) saturate(2.5) brightness(1.05)`;
  const innerG = el.firstElementChild;
  if (!innerG) return;
  const gfx = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  gfx.style.filter = filterStr;
  [...innerG.children].forEach(child => {
    if (child.tagName.toLowerCase() !== 'text') gfx.appendChild(child);
  });
  innerG.insertBefore(gfx, innerG.firstChild);
}

// When set, renderOutsideLabel appends labels here instead of returning inline SVG
let _labelLayerTarget = null;

function renderNodes() {
  if (dragNode) return;
  const layer = document.getElementById('nodes-layer');
  const labelsLayer = document.getElementById('node-labels-layer');
  layer.innerHTML = '';
  labelsLayer.innerHTML = '';
  _labelLayerTarget = labelsLayer;
  nodes.forEach(node => {
    const g = document.createElementNS('http://www.w3.org/2000/svg','g');
    g.setAttribute('class','node-g');
    g.setAttribute('id','node-'+node.id);
    g.setAttribute('transform',`translate(${node.x},${node.y})`);
    g.setAttribute('data-id',node.id);
    const sel = selectedEl?.type==='node' && selectedEl?.data.id===node.id;
    if (multiSelect.has(node.id)) g.classList.add('multi-selected');
    else g.classList.remove('multi-selected');
    g.innerHTML = buildNode(node, sel);
    if (multiSelect.has(node.id)) {
      const sz = nsize(node.type, node);
      const sr = document.createElementNS('http://www.w3.org/2000/svg','rect');
      sr.setAttribute('x','-4'); sr.setAttribute('y','-4');
      sr.setAttribute('width', sz.w + 8); sr.setAttribute('height', sz.h + 8);
      sr.setAttribute('rx','6'); sr.setAttribute('fill','none');
      sr.setAttribute('stroke','#00ff9d'); sr.setAttribute('stroke-width','1.5');
      sr.setAttribute('stroke-dasharray','6,3'); sr.setAttribute('pointer-events','none');
      g.appendChild(sr);
    }
    _applyNodeColorFilter(g, node);
    g.addEventListener('mousedown', e => startDrag(e, node));
    g.addEventListener('click', e => {
      e.stopPropagation();
      if (e.shiftKey) { toggleMultiSelect(node); return; }
      if (isPingWatchPage) { showPwNodePanel(node._pwDid); return; }
      multiSelect.clear(); selectNode(node);
    });
    g.addEventListener('touchstart', e => startDrag(e, node), {passive:false});
    layer.appendChild(g);
  });
  _labelLayerTarget = null;

  // LED blink — use single shared interval instead of one per node
  clearLedIntervals();
  _startLedBlink();
}

// LED blink is now pure CSS (@keyframes ledBlink in map.css)
function clearLedIntervals() {}
function _startLedBlink() {}

// ═══════════════════════════ NODE RENDERERS ═══════════════════════════

// Shared VLAN badge helper — used by every renderer that supports p.vlan
// cx = center-x of the badge; y = top-y of the badge rect
// lx = left edge of badge, aligned with the text content of the device
function _vlanIds(raw) {
  return String(raw || '').match(/\d+/g) || [];
}
function _vlanH(p) {
  const n = _vlanIds((p || {}).vlan).length;
  return n > 5 ? 30 : n > 0 ? 16 : 0;
}
// Truncate a device name to fit within availW pixels.
// charPx: estimated pixels-per-character for the target font/size.
function _truncName(name, availW, charPx) {
  charPx = charPx || 7.0;
  const max = Math.max(6, Math.floor(availW / charPx));
  return name.length > max ? name.slice(0, max - 1) + '\u2026' : name;
}

function vlanBadge(p, lx, y) {
  const ids = _vlanIds(p.vlan);
  if (!ids.length) return '';
  const BW = 30, GAP = 4, ROW = 14;
  return ids.slice(0, 10).map((v, i) => {
    const row = Math.floor(i / 5), col = i % 5;
    const c   = VLAN_COLORS[v] || '#00d4ff';
    const rgb = hexToRgb(c);
    const bx  = lx + col * (BW + GAP);
    const by  = y  + row * ROW;
    return `<g>
      <rect x="${bx}" y="${by}" width="${BW}" height="11" rx="2"
        fill="rgba(${rgb},0.15)" stroke="${c}" stroke-width="0.7"/>
      <text x="${(bx + BW/2).toFixed(1)}" y="${by + 8}" text-anchor="middle"
        fill="${c}" font-family="Share Tech Mono" font-size="8">V${escXml(v)}</text>
    </g>`;
  }).join('');
}

function buildNode(node, sel) {
  const p = node.properties || {};
  const selFilter = sel ? 'style="filter:drop-shadow(0 0 8px rgba(0,212,255,0.8))"' : '';

  switch(node.type) {
    case 'cloud':      return renderCloud(node, p, selFilter);
    case 'firewall':   return renderFirewall(node, p, selFilter);
    case 'wan-switch': return renderWanSwitch(node, p, selFilter);
    case 'bb-switch':  return renderBBSwitch(node, p, selFilter);
    case 'switch':     return renderSwitch(node, p, selFilter);
    case 'connector':  return renderConnector(node, p, selFilter);
    case 'ap':         return renderAP(node, p, selFilter);
    case 'server':     return renderServer(node, p, selFilter);
    case 'pc':         return renderPC(node, p, selFilter);
    case 'laptop':     return renderLaptop(node, p, selFilter);
    case 'remote-pc':  return renderRemotePC(node, p, selFilter);
    case 'info-box':   return renderInfoBox(node, p, selFilter);
    case 'router':     return renderRouter(node, p, selFilter);
    case 'vm':         return renderVM(node, p, selFilter);
    case 'appliance':  return renderAppliance(node, p, selFilter);
    case 'storage':    return renderStorage(node, p, selFilter);
    case 'phone':      return renderPhone(node, p, selFilter);
    case 'camera':     return renderCamera(node, p, selFilter);
    case 'printer':    return renderPrinter(node, p, selFilter);
    case 'load-balancer': return renderLoadBalancer(node, p, selFilter);
    case 'hypervisor': return renderHypervisor(node, p, selFilter);
    case 'ups':        return renderUPS(node, p, selFilter);
    case 'container':  return renderContainer(node, p, selFilter);
    case 'ipmi':       return renderIPMI(node, p, selFilter);
    default:           return renderGeneric(node, p, selFilter);
  }
}

function renderCloud(node, p, sf) {
  return `<g ${sf}>
    <circle cx="75" cy="58" r="22" fill="url(#cloudGrad)" stroke="#2a6aaa" stroke-width="1.2"/>
    <circle cx="115" cy="52" r="28" fill="url(#cloudGrad)" stroke="#2a6aaa" stroke-width="1.2"/>
    <circle cx="152" cy="58" r="20" fill="url(#cloudGrad)" stroke="#2a6aaa" stroke-width="1.2"/>
    <circle cx="95" cy="44" r="26" fill="url(#cloudGrad)" stroke="#2a6aaa" stroke-width="1.2"/>
    <circle cx="133" cy="42" r="24" fill="url(#cloudGrad)" stroke="#2a6aaa" stroke-width="1.2"/>
    <rect x="55" y="58" width="117" height="22" fill="url(#cloudGrad)" stroke="none"/>
    <path d="M55,75 Q55,58 75,58 Q75,40 95,38 Q98,28 115,28 Q128,24 133,32 Q145,28 152,38 Q168,40 168,58 Q172,58 172,75 Z" fill="none" stroke="#3a9fd5" stroke-width="1.8" filter="url(#glow-cloud)" opacity="0.9"/>
    <g transform="translate(114,50)">
      <circle cx="0" cy="0" r="13" fill="rgba(0,40,80,0.6)" stroke="#00d4ff" stroke-width="1.5"/>
      <ellipse cx="0" cy="0" rx="13" ry="5" fill="none" stroke="#00d4ff" stroke-width="0.8" opacity="0.5"/>
      <line x1="0" y1="-13" x2="0" y2="13" stroke="#00d4ff" stroke-width="0.8" opacity="0.5"/>
      <line x1="-13" y1="0" x2="13" y2="0" stroke="#00d4ff" stroke-width="0.8" opacity="0.5"/>
      <circle cx="0" cy="0" r="2.5" fill="#00d4ff" opacity="0.9"/>
    </g>
    <text data-pw-name data-pw-origfill="#7dd3fc" x="113" y="96" text-anchor="middle" fill="${p.name_color||'#7dd3fc'}" font-family="Orbitron" font-size="12" font-weight="700" letter-spacing="2" filter="url(#glow-blue)">${escXml(_truncName(node.name, 117, 10))}</text>
  </g>`;
}

function renderFirewall(node, p, sf) {
  const isPrimary = p.status === 'PRIMARY';
  const isSecondary = p.status === 'SECONDARY';
  const statusColor = isPrimary ? '#ff6b6b' : 'rgba(255,107,107,0.7)';
  const statusText = isPrimary ? '● PRIMARY' : isSecondary ? '○ SECONDARY' : '';
  const H = 76 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="155" height="${H}" rx="4" fill="rgba(60,10,15,0.92)" stroke="#ff3366" stroke-width="1.5" filter="url(#glow-red)"/>
    <rect x="0" y="0" width="155" height="${H}" rx="4" fill="url(#scanlines)"/>
    <path d="M16,12 L30,8 L44,12 L44,28 Q37,38 30,40 Q23,38 16,28 Z" fill="none" stroke="#ff3366" stroke-width="1.5"/>
    <path d="M23,22 L28,27 L38,17" fill="none" stroke="#ff6b6b" stroke-width="1.5"/>
    <text data-pw-name data-pw-origfill="#fca5a5" x="52" y="24" fill="${p.name_color||'#fca5a5'}" font-family="Exo 2" font-size="13" font-weight="700">${escXml(_truncName(node.name, 95))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 50, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${statusText ? `<text x="52" y="66" fill="${statusColor}" font-family="Share Tech Mono" font-size="9">${statusText}</text>` : ''}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="153" height="2" rx="1" fill="rgba(255,51,102,0.3)"/>
  </g>`;
}

function renderWanSwitch(node, p, sf) {
  const H = 60 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="220" height="${H}" rx="4" fill="rgba(10,25,50,0.95)" stroke="#00d4ff" stroke-width="1.5" filter="url(#glow-blue)"/>
    <rect x="0" y="0" width="220" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(12,12)">
      <rect x="0" y="8" width="5" height="12" rx="1" fill="#00d4ff" opacity="0.8"/>
      <rect x="7" y="4" width="5" height="16" rx="1" fill="#00d4ff"/>
      <rect x="14" y="8" width="5" height="12" rx="1" fill="#00d4ff" opacity="0.8"/>
      <rect x="21" y="4" width="5" height="16" rx="1" fill="#00d4ff"/>
      <rect x="28" y="8" width="5" height="12" rx="1" fill="#00d4ff" opacity="0.8"/>
    </g>
    <text data-pw-name data-pw-origfill="#7dd3fc" x="55" y="23" fill="${p.name_color||'#7dd3fc'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 157))}</text>
    ${renderSubtitleAndIP(p, 55, 38, 48, 'rgba(255,255,255,0.5)', 'rgba(255,255,255,0.65)')}
    <g transform="translate(8,${_vlanH(p) > 0 ? 52 : 44})">
      ${[0,11,22,33,44,55].map(x=>`<rect x="${x}" y="0" width="8" height="6" rx="1" fill="#00d4ff" opacity="${x<22?0.6:0.2}" class="led-blink"/>`).join('')}
    </g>
    ${vlanBadge(p, 55, H - 19)}
    <rect x="1" y="1" width="218" height="2" rx="1" fill="rgba(0,212,255,0.2)"/>
  </g>`;
}

function renderBBSwitch(node, p, sf) {
  const W = 300;
  const H = 88;

  // Parse all VLAN IDs from the vlan string field (e.g. "VLAN10 VLAN20 VLAN100")
  const rawVlan = String(p.vlan || '').trim();
  const vlans = rawVlan ? (rawVlan.match(/\d+/g) || []) : [];

  // Dynamic badge width: min 28px, wider for longer labels (each char ~5.5px + 8px padding)
  const badges = vlans.map((v, i) => {
    const vid = String(v).replace(/^V/i,'');  // handles both "V10" and "10"
    const c = VLAN_COLORS[vid] || '#00d4ff';
    const label = `V${vid}`;
    const bw = Math.max(28, label.length * 5.5 + 8);
    const xOffset = vlans.slice(0, i).reduce((acc, prev) => {
      const pvid = String(prev).replace(/^V/i,'');
      const pl = `V${pvid}`;
      return acc + Math.max(28, pl.length * 5.5 + 8) + 4;
    }, 0);
    return `
      <rect x="${xOffset}" y="0" width="${bw}" height="12" rx="2"
        fill="rgba(${hexToRgb(c)},0.15)" stroke="${c}" stroke-width="0.5"/>
      <text x="${xOffset + bw/2}" y="9" text-anchor="middle"
        fill="${c}" font-family="Share Tech Mono" font-size="8">${escXml(label)}</text>
    `;
  }).join('');

  // Use stable alternating opacities — randomizing inside render causes flicker on redraws
  const ledOpacities = [1, 0.4, 1, 0.4, 1, 0.4];

  const bbIsPrimary   = p.status === 'PRIMARY';
  const bbIsSecondary = p.status === 'SECONDARY';
  const bbStatusColor = bbIsPrimary ? '#00ff9d' : 'rgba(0,255,157,0.5)';
  const bbStatusText  = bbIsPrimary ? '● PRIMARY' : bbIsSecondary ? '○ SECONDARY' : '';

  return `<g ${sf}>
    <rect x="0" y="0" width="${W}" height="${H}" rx="4"
      fill="rgba(5,30,18,0.95)" stroke="#00ff9d" stroke-width="2" filter="url(#glow-green)"/>
    <rect x="0" y="0" width="${W}" height="${H}" rx="4" fill="url(#scanlines)"/>

    <g transform="translate(12,14)">
      ${[0,1,2].map(row=>`
        <rect x="0" y="${row*10}" width="36" height="6" rx="1"
          fill="rgba(0,255,157,0.3)" stroke="#00ff9d" stroke-width="1"/>
        ${[4,10,16,22,28,34].map((cx,ci)=>`<circle cx="${cx}" cy="${row*10+3}" r="1.5"
          fill="#00ff9d" class="led-blink" opacity="${ledOpacities[ci]}"/>`).join('')}
      `).join('')}
    </g>

    <text data-pw-name data-pw-origfill="#6ee7b7" x="62" y="28" fill="${p.name_color||'#6ee7b7'}" font-family="Exo 2" font-size="14" font-weight="700">${escXml(_truncName(node.name, 230, 8.5))}</text>

    ${bbStatusText ? `<text x="${W - 8}" y="16" text-anchor="end" fill="${bbStatusColor}" font-family="Share Tech Mono" font-size="9">${bbStatusText}</text>` : ''}

    ${renderSubtitleAndIP(p, 62, 44, 57, 'rgba(255,255,255,0.5)', 'rgba(255,255,255,0.65)')}

    <!-- moved DOWN so it never overlaps IP -->
    <g transform="translate(62,72)">${badges}</g>

    <rect x="1" y="1" width="${W-2}" height="2" rx="1" fill="rgba(0,255,157,0.3)"/>
  </g>`;
}

function renderSwitch(node, p, sf) {
  const { w, h } = nsize('switch', node);

  // tighter vertical rhythm
  const nameY = 22;
  const subY  = 36;
  const ipY   = 48;

  const swIsPrimary   = p.status === 'PRIMARY';
  const swIsSecondary = p.status === 'SECONDARY';
  const swStatusColor = swIsPrimary ? '#00ff9d' : 'rgba(0,255,157,0.5)';
  const swStatusText  = swIsPrimary ? '● PRIMARY' : swIsSecondary ? '○ SECONDARY' : '';

  return `<g ${sf}>
    <rect x="0" y="0" width="${w}" height="${h}" rx="4"
      fill="rgba(5,35,20,0.92)" stroke="#00ff9d" stroke-width="1.5" filter="url(#glow-green)"/>
    <rect x="0" y="0" width="${w}" height="${h}" rx="4" fill="url(#scanlines)"/>

    <g transform="translate(12,9)">
      ${[0,8,16].map(y=>`<rect x="0" y="${y}" width="30" height="4.5" rx="1"
        fill="rgba(0,255,157,0.25)" stroke="#00ff9d" stroke-width="1"/>`).join('')}
      ${[5,12,19,26].map((cx,i)=>`<circle cx="${cx-1}" cy="2.2" r="1.4"
        fill="${i===3?'#ffd700':'#00ff9d'}" class="led-blink" opacity="${i%2===0?1:0.5}"/>`).join('')}
    </g>

    <text data-pw-name data-pw-origfill="#6ee7b7" x="50" y="${nameY}" fill="${p.name_color||'#6ee7b7'}" font-family="Exo 2" font-size="11.5" font-weight="600">
      ${escXml(_truncName(node.name, w - 58, 6.5))}
    </text>

    ${swStatusText ? `<text x="${w - 6}" y="13" text-anchor="end" fill="${swStatusColor}" font-family="Share Tech Mono" font-size="8">${swStatusText}</text>` : ''}

    ${renderSubtitleAndIP(p, 50, subY, ipY, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}

    ${vlanBadge(p, 50, h - 19)}

    <rect x="1" y="1" width="${w-2}" height="2" rx="1" fill="rgba(0,255,157,0.2)"/>
  </g>`;
}

function renderConnector(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="165" height="${H}" rx="4" fill="rgba(40,30,5,0.92)" stroke="#ffd700" stroke-width="1.5" filter="url(#glow-gold)"/>
    <rect x="0" y="0" width="165" height="${H}" rx="4" fill="url(#scanlines)"/>
    <polygon points="28,10 44,26 28,42 12,26" fill="none" stroke="#ffd700" stroke-width="1.5"/>
    <polygon points="28,16 38,26 28,36 18,26" fill="rgba(255,215,0,0.15)" stroke="#ffd700" stroke-width="1"/>
    <circle cx="28" cy="26" r="3" fill="#ffd700"/>
    <text data-pw-name data-pw-origfill="#fde68a" x="52" y="22" fill="${p.name_color||'#fde68a'}" font-family="Exo 2" font-size="12" font-weight="700">${escXml(_truncName(node.name, 105))}</text>
    ${renderSubtitleAndIP(p, 52, 37, 51, '#ffd700', 'rgba(255,255,255,0.6)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="163" height="2" rx="1" fill="rgba(255,215,0,0.3)"/>
  </g>`;
}

function renderAP(node, p, sf) {
  const H = 62 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="170" height="${H}" rx="4" fill="rgba(15,10,35,0.92)" stroke="#a855f7" stroke-width="1.5"/>
    <rect x="0" y="0" width="170" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(14,14)">
      <path d="M4,26 Q17,6 30,26" fill="none" stroke="#a855f7" stroke-width="2" opacity="0.4"/>
      <path d="M9,26 Q17,12 25,26" fill="none" stroke="#a855f7" stroke-width="2" opacity="0.7"/>
      <path d="M13,26 Q17,18 21,26" fill="none" stroke="#a855f7" stroke-width="2"/>
      <circle cx="17" cy="28" r="2.5" fill="#a855f7"/>
    </g>
    <text data-pw-name data-pw-origfill="#d8b4fe" x="52" y="24" fill="${p.name_color||'#d8b4fe'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 110))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="168" height="2" rx="1" fill="rgba(168,85,247,0.3)"/>
  </g>`;
}

function renderServer(node, p, sf) {
  const acc = toHexColor(p.accent, '#00d4ff');
  const rgb = hexToRgb(acc);
  const nameColor = toHexColor(p.name_color, '#93c5fd');
  const H = 52;  // chassis height

  return `<g ${sf}>
    <!-- chassis -->
    <rect x="0" y="0" width="170" height="${H}" rx="2"
      fill="rgba(8,20,35,0.92)" stroke="${acc}" stroke-width="1.5"/>
    ${[6,20,34].map(y=>`<rect x="8" y="${y}" width="154" height="10" rx="1"
      fill="rgba(${rgb},0.1)" stroke="rgba(${rgb},0.4)" stroke-width="0.5"/>`).join('')}
    <circle cx="148" cy="11" r="2" fill="${acc}" class="led-blink"/>
    <circle cx="148" cy="25" r="2" fill="${acc}" class="led-blink"/>
    <circle cx="148" cy="39" r="2" fill="#ffd700" class="led-blink"/>
	${renderOutsideLabel(node, p, 85, H, { nameFill: nameColor, glow:'url(#glow-blue)' })}
  </g>`;
}

function renderPC(node, p, sf) {
  return `<g ${sf}>
    <rect x="0" y="0" width="145" height="56" rx="4" fill="rgba(8,16,30,0.92)" stroke="#00d4ff" stroke-width="1.5"/>
    <rect x="20" y="5" width="105" height="36" rx="2" fill="rgba(0,212,255,0.08)" stroke="#00d4ff" stroke-width="1"/>
    <rect x="26" y="10" width="93" height="26" rx="1" fill="rgba(0,0,0,0.5)"/>
    <line x1="30" y1="17" x2="90" y2="17" stroke="rgba(0,212,255,0.3)" stroke-width="1"/>
    <line x1="30" y1="23" x2="80" y2="23" stroke="rgba(0,212,255,0.2)" stroke-width="1"/>
    <rect x="68" y="41" width="10" height="8" rx="1" fill="rgba(0,212,255,0.3)"/>
    <rect x="58" y="48" width="30" height="3" rx="1" fill="rgba(0,212,255,0.3)"/>
	${renderOutsideLabel(node, p, 72, 56, { nameFill:'#93c5fd', glow:'' })}
  </g>`;
}

function renderLaptop(node, p, sf) {
  return `<g ${sf}>
    <rect x="0" y="0" width="180" height="58" rx="4" fill="rgba(20,10,35,0.92)" stroke="#a855f7" stroke-width="1.5"/>
    <rect x="18" y="5" width="95" height="38" rx="2" fill="rgba(168,85,247,0.08)" stroke="#a855f7" stroke-width="1"/>
    <rect x="24" y="9" width="83" height="30" rx="1" fill="rgba(0,0,0,0.6)"/>
    <line x1="32" y1="18" x2="90" y2="18" stroke="rgba(168,85,247,0.4)" stroke-width="1"/>
    <line x1="32" y1="24" x2="85" y2="24" stroke="rgba(168,85,247,0.3)" stroke-width="1"/>
    <rect x="12" y="43" width="108" height="6" rx="2" fill="rgba(168,85,247,0.2)" stroke="#a855f7" stroke-width="0.5"/>
    <g transform="translate(128,6)">
      <path d="M8,22 Q17,8 26,22" fill="none" stroke="#a855f7" stroke-width="1.5" opacity="0.4"/>
      <path d="M11,22 Q17,13 23,22" fill="none" stroke="#a855f7" stroke-width="1.5" opacity="0.7"/>
      <path d="M14,22 Q17,17 20,22" fill="none" stroke="#a855f7" stroke-width="1.5"/>
      <circle cx="17" cy="24" r="2" fill="#a855f7"/>
    </g>
	${renderOutsideLabel(node, p, 90, 58, { nameFill:'#d8b4fe', glow:'' })}
  </g>`;
}

function renderRemotePC(node, p, sf) {
  return `<g ${sf}>
    <rect x="0" y="0" width="145" height="56" rx="4" fill="rgba(8,16,30,0.92)" stroke="#00d4ff" stroke-width="1.5"/>
    <rect x="20" y="5" width="105" height="36" rx="2" fill="rgba(0,212,255,0.08)" stroke="#00d4ff" stroke-width="1"/>
    <rect x="26" y="10" width="93" height="26" rx="1" fill="rgba(0,0,0,0.5)"/>
    <line x1="30" y1="17" x2="90" y2="17" stroke="rgba(0,212,255,0.3)" stroke-width="1"/>
    <line x1="30" y1="23" x2="80" y2="23" stroke="rgba(0,212,255,0.2)" stroke-width="1"/>
    <rect x="68" y="41" width="10" height="8" rx="1" fill="rgba(0,212,255,0.3)"/>
    <rect x="58" y="48" width="30" height="3" rx="1" fill="rgba(0,212,255,0.3)"/>
	
    ${renderOutsideLabel(
      node,
      { ...p, subtitle: p.subtitle || (!p.ip ? 'Cato ZTNA Client' : '') },
      72.5,
      56,
      { nameFill:'#e2e8f0', glow:'' }
    )}

    <rect x="1" y="1" width="143" height="3" rx="2" fill="rgba(0,212,255,0.1)"/>
  </g>`;
}

function renderInfoBox(node, p, sf) {
  const lines = Array.isArray(p.lines) ? p.lines : [];
  const h = Math.max(80, 25 + lines.length * 14 + 10);

  return `<g ${sf}>
    <rect x="0" y="0" width="210" height="${h}" rx="4"
      fill="rgba(40,8,12,0.85)" stroke="rgba(255,51,102,0.3)"
      stroke-width="1" stroke-dasharray="4,3"/>
    <text data-pw-name data-pw-origfill="#ff6b6b" x="10" y="16" fill="#ff6b6b" font-family="Orbitron" font-size="9" letter-spacing="1">${escXml(_truncName(node.name, 192, 6.5))}</text>
    ${lines.map((l,i) => {
      const color = (l && l.color) ? String(l.color) : 'rgba(255,255,255,0.6)';
      const text  = (l && l.text)  ? String(l.text)  : '';
      return `
        <rect x="6" y="${22 + i*14}" width="3" height="10" rx="1" fill="${escXml(color)}"/>
        <text x="14" y="${30 + i*14}" fill="${escXml(color)}" font-family="Share Tech Mono" font-size="9">${escXml(text)}</text>
      `;
    }).join('')}
  </g>`;
}

function renderGeneric(node, p, sf) {
  const H = 60 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(10,20,35,0.92)" stroke="#00d4ff" stroke-width="1.5"/>
    <text data-pw-name data-pw-origfill="#93c5fd" x="80" y="28" text-anchor="middle" fill="${p.name_color||'#93c5fd'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 160))}</text>
	${p.subtitle ? svgTextLine(80, 45, p.subtitle, (p.subtitle_color||'rgba(255,255,255,0.4)'), 9) : ''}
	${(!p.subtitle && p.ip) ? svgTextLine(80, 45, p.ip, (p.ip_color||'rgba(255,255,255,0.4)'), 9) : ''}
	${(!p.subtitle && !p.ip) ? svgTextLine(80, 45, node.type, 'rgba(255,255,255,0.35)', 9) : ''}
    ${vlanBadge(p, 20, H - 19)}
  </g>`;
}

function renderRouter(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(5,18,38,0.92)" stroke="#00d4ff" stroke-width="1.5" filter="url(#glow-blue)"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,16)">
      <circle cx="15" cy="15" r="13" fill="none" stroke="#00d4ff" stroke-width="1.2"/>
      <ellipse cx="15" cy="15" rx="13" ry="5" fill="none" stroke="#00d4ff" stroke-width="0.7" opacity="0.5"/>
      <line x1="2" y1="15" x2="28" y2="15" stroke="#00d4ff" stroke-width="0.7" opacity="0.5"/>
      <line x1="15" y1="2" x2="15" y2="28" stroke="#00d4ff" stroke-width="0.7" opacity="0.5"/>
      <polygon points="28,12 34,15 28,18" fill="#00d4ff"/>
    </g>
    <text data-pw-name data-pw-origfill="#7dd3fc" x="52" y="24" fill="${p.name_color||'#7dd3fc'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(0,212,255,0.2)"/>
  </g>`;
}

function renderVM(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(5,25,22,0.92)" stroke="#2dd4bf" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,12)">
      <rect x="0" y="16" width="28" height="14" rx="2" fill="rgba(45,212,191,0.15)" stroke="#2dd4bf" stroke-width="1.2"/>
      <rect x="3" y="9" width="22" height="12" rx="2" fill="rgba(45,212,191,0.1)" stroke="#2dd4bf" stroke-width="1" opacity="0.7"/>
      <rect x="6" y="2" width="16" height="12" rx="2" fill="rgba(45,212,191,0.07)" stroke="#2dd4bf" stroke-width="1" opacity="0.4"/>
      <circle cx="14" cy="23" r="2" fill="#2dd4bf" opacity="0.8"/>
    </g>
    <text data-pw-name data-pw-origfill="#99f6e4" x="52" y="24" fill="${p.name_color||'#99f6e4'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(45,212,191,0.2)"/>
  </g>`;
}

function renderAppliance(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(25,12,5,0.92)" stroke="#fb923c" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,14)">
      <circle cx="15" cy="15" r="13" fill="none" stroke="#fb923c" stroke-width="1.2"/>
      <circle cx="15" cy="15" r="6" fill="rgba(251,146,60,0.2)" stroke="#fb923c" stroke-width="1"/>
      <circle cx="15" cy="15" r="2" fill="#fb923c"/>
      ${[0,60,120,180,240,300].map(a=>{
        const r=Math.PI*a/180, x1=(15+7*Math.cos(r)).toFixed(1), y1=(15+7*Math.sin(r)).toFixed(1),
              x2=(15+12*Math.cos(r)).toFixed(1), y2=(15+12*Math.sin(r)).toFixed(1);
        return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#fb923c" stroke-width="1.5" opacity="0.7"/>`;
      }).join('')}
    </g>
    <text data-pw-name data-pw-origfill="#fed7aa" x="52" y="24" fill="${p.name_color||'#fed7aa'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(251,146,60,0.2)"/>
  </g>`;
}

// ── Storage / NAS ────────────────────────────────────────────
function renderStorage(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(30,20,5,0.92)" stroke="#f59e0b" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,12)">
      <rect x="2" y="0" width="26" height="8" rx="3" fill="rgba(245,158,11,0.15)" stroke="#f59e0b" stroke-width="1.2"/>
      <rect x="0" y="10" width="30" height="8" rx="3" fill="rgba(245,158,11,0.2)" stroke="#f59e0b" stroke-width="1.2"/>
      <rect x="2" y="20" width="26" height="8" rx="3" fill="rgba(245,158,11,0.15)" stroke="#f59e0b" stroke-width="1.2"/>
      <circle cx="24" cy="14" r="2" fill="#f59e0b" class="led-blink"/>
    </g>
    <text data-pw-name data-pw-origfill="#fcd34d" x="52" y="24" fill="${p.name_color||'#fcd34d'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(245,158,11,0.2)"/>
  </g>`;
}

// ── IP Phone / VoIP ──────────────────────────────────────────
function renderPhone(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(5,25,25,0.92)" stroke="#14b8a6" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,12)">
      <path d="M4,8 Q4,4 8,4 L12,4 Q14,4 14,6 L14,12 Q14,14 12,14 L10,14 Q6,18 6,22 Q6,26 10,26 L12,26 Q14,26 14,28 L14,34 Q14,36 12,36 L8,36 Q4,36 4,32 Z" fill="none" stroke="#14b8a6" stroke-width="1.5"/>
      <path d="M18,10 Q24,4 30,10" fill="none" stroke="#14b8a6" stroke-width="1.2" opacity="0.4"/>
      <path d="M20,12 Q24,8 28,12" fill="none" stroke="#14b8a6" stroke-width="1.2" opacity="0.7"/>
      <circle cx="24" cy="14" r="1.5" fill="#14b8a6"/>
    </g>
    <text data-pw-name data-pw-origfill="#5eead4" x="52" y="24" fill="${p.name_color||'#5eead4'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(20,184,166,0.2)"/>
  </g>`;
}

// ── IP Camera / CCTV ─────────────────────────────────────────
function renderCamera(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(15,12,35,0.92)" stroke="#818cf8" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,12)">
      <rect x="0" y="6" width="22" height="16" rx="2" fill="rgba(129,140,248,0.15)" stroke="#818cf8" stroke-width="1.2"/>
      <circle cx="11" cy="14" r="5" fill="none" stroke="#818cf8" stroke-width="1.2"/>
      <circle cx="11" cy="14" r="2" fill="#818cf8" opacity="0.8"/>
      <polygon points="22,10 30,6 30,22 22,18" fill="rgba(129,140,248,0.2)" stroke="#818cf8" stroke-width="1"/>
    </g>
    <text data-pw-name data-pw-origfill="#c7d2fe" x="52" y="24" fill="${p.name_color||'#c7d2fe'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(129,140,248,0.2)"/>
  </g>`;
}

// ── Printer / MFP ────────────────────────────────────────────
function renderPrinter(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(15,18,22,0.92)" stroke="#94a3b8" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,10)">
      <rect x="2" y="0" width="24" height="10" rx="1" fill="rgba(148,163,184,0.1)" stroke="#94a3b8" stroke-width="1"/>
      <rect x="0" y="10" width="28" height="14" rx="2" fill="rgba(148,163,184,0.15)" stroke="#94a3b8" stroke-width="1.2"/>
      <rect x="4" y="24" width="20" height="6" rx="1" fill="rgba(148,163,184,0.1)" stroke="#94a3b8" stroke-width="0.8"/>
      <rect x="6" y="-4" width="16" height="6" rx="1" fill="rgba(148,163,184,0.08)" stroke="#94a3b8" stroke-width="0.8"/>
      <circle cx="23" cy="17" r="1.5" fill="#94a3b8" class="led-blink"/>
    </g>
    <text data-pw-name data-pw-origfill="#cbd5e1" x="52" y="24" fill="${p.name_color||'#cbd5e1'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(148,163,184,0.2)"/>
  </g>`;
}

// ── Load Balancer ────────────────────────────────────────────
function renderLoadBalancer(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(30,8,20,0.92)" stroke="#ec4899" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,12)">
      <circle cx="4" cy="14" r="3" fill="#ec4899" opacity="0.8"/>
      <line x1="7" y1="14" x2="16" y2="14" stroke="#ec4899" stroke-width="1.5"/>
      <line x1="16" y1="14" x2="28" y2="6" stroke="#ec4899" stroke-width="1.2"/>
      <line x1="16" y1="14" x2="28" y2="14" stroke="#ec4899" stroke-width="1.2"/>
      <line x1="16" y1="14" x2="28" y2="22" stroke="#ec4899" stroke-width="1.2"/>
      <polygon points="26,4 32,6 26,8" fill="#ec4899"/>
      <polygon points="26,12 32,14 26,16" fill="#ec4899"/>
      <polygon points="26,20 32,22 26,24" fill="#ec4899"/>
    </g>
    <text data-pw-name data-pw-origfill="#f9a8d4" x="52" y="24" fill="${p.name_color||'#f9a8d4'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(236,72,153,0.2)"/>
  </g>`;
}

// ── Hypervisor / ESXi ────────────────────────────────────────
function renderHypervisor(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(8,15,40,0.92)" stroke="#3b82f6" stroke-width="1.5" filter="url(#glow-blue)"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,10)">
      <rect x="0" y="0" width="30" height="30" rx="2" fill="rgba(59,130,246,0.1)" stroke="#3b82f6" stroke-width="1.2"/>
      <rect x="3" y="3" width="24" height="7" rx="1" fill="rgba(59,130,246,0.15)" stroke="#3b82f6" stroke-width="0.8"/>
      <rect x="3" y="12" width="24" height="7" rx="1" fill="rgba(59,130,246,0.12)" stroke="#3b82f6" stroke-width="0.8" opacity="0.8"/>
      <rect x="3" y="21" width="24" height="7" rx="1" fill="rgba(59,130,246,0.08)" stroke="#3b82f6" stroke-width="0.8" opacity="0.6"/>
      <circle cx="23" cy="6.5" r="1.5" fill="#3b82f6" class="led-blink"/>
      <circle cx="23" cy="15.5" r="1.5" fill="#3b82f6" class="led-blink" opacity="0.7"/>
      <circle cx="23" cy="24.5" r="1.5" fill="#ffd700" class="led-blink" opacity="0.5"/>
    </g>
    <text data-pw-name data-pw-origfill="#93c5fd" x="52" y="24" fill="${p.name_color||'#93c5fd'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(59,130,246,0.2)"/>
  </g>`;
}

// ── UPS / PDU ────────────────────────────────────────────────
function renderUPS(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(15,22,8,0.92)" stroke="#84cc16" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,10)">
      <rect x="4" y="0" width="22" height="30" rx="3" fill="rgba(132,204,22,0.1)" stroke="#84cc16" stroke-width="1.2"/>
      <rect x="10" y="-3" width="10" height="4" rx="1" fill="rgba(132,204,22,0.3)" stroke="#84cc16" stroke-width="0.8"/>
      <path d="M18,10 L13,18 L17,18 L12,26" fill="none" stroke="#84cc16" stroke-width="1.8" stroke-linecap="round"/>
      <circle cx="22" cy="4" r="1.5" fill="#84cc16" class="led-blink"/>
    </g>
    <text data-pw-name data-pw-origfill="#bef264" x="52" y="24" fill="${p.name_color||'#bef264'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(132,204,22,0.2)"/>
  </g>`;
}

// ── Container Host ───────────────────────────────────────────
function renderContainer(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(5,20,30,0.92)" stroke="#38bdf8" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,10)">
      <path d="M2,20 L15,26 L28,20 L28,8 L15,2 L2,8 Z" fill="rgba(56,189,248,0.1)" stroke="#38bdf8" stroke-width="1.2"/>
      <line x1="2" y1="8" x2="15" y2="14" stroke="#38bdf8" stroke-width="0.8" opacity="0.5"/>
      <line x1="28" y1="8" x2="15" y2="14" stroke="#38bdf8" stroke-width="0.8" opacity="0.5"/>
      <line x1="15" y1="14" x2="15" y2="26" stroke="#38bdf8" stroke-width="0.8" opacity="0.5"/>
      <path d="M8,11 L15,14.5 L22,11" fill="none" stroke="#38bdf8" stroke-width="0.6" stroke-dasharray="2,2" opacity="0.6"/>
      <path d="M8,16 L15,19.5 L22,16" fill="none" stroke="#38bdf8" stroke-width="0.6" stroke-dasharray="2,2" opacity="0.4"/>
    </g>
    <text data-pw-name data-pw-origfill="#7dd3fc" x="52" y="24" fill="${p.name_color||'#7dd3fc'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(56,189,248,0.2)"/>
  </g>`;
}

// ── IPMI / BMC ───────────────────────────────────────────────
function renderIPMI(node, p, sf) {
  const H = 68 + _vlanH(p);
  return `<g ${sf}>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="rgba(30,10,10,0.92)" stroke="#ef4444" stroke-width="1.5"/>
    <rect x="0" y="0" width="160" height="${H}" rx="4" fill="url(#scanlines)"/>
    <g transform="translate(10,10)">
      <rect x="6" y="6" width="18" height="18" rx="2" fill="rgba(239,68,68,0.12)" stroke="#ef4444" stroke-width="1.2"/>
      <circle cx="15" cy="15" r="3" fill="#ef4444" opacity="0.8"/>
      ${[10,15,20].map(y=>`<line x1="0" y1="${y}" x2="6" y2="${y}" stroke="#ef4444" stroke-width="1.2" opacity="0.6"/>`).join('')}
      ${[10,15,20].map(y=>`<line x1="24" y1="${y}" x2="30" y2="${y}" stroke="#ef4444" stroke-width="1.2" opacity="0.6"/>`).join('')}
      ${[10,15,20].map(x=>`<line x1="${x}" y1="0" x2="${x}" y2="6" stroke="#ef4444" stroke-width="1.2" opacity="0.6"/>`).join('')}
      ${[10,15,20].map(x=>`<line x1="${x}" y1="24" x2="${x}" y2="30" stroke="#ef4444" stroke-width="1.2" opacity="0.6"/>`).join('')}
    </g>
    <text data-pw-name data-pw-origfill="#fca5a5" x="52" y="24" fill="${p.name_color||'#fca5a5'}" font-family="Exo 2" font-size="12" font-weight="600">${escXml(_truncName(node.name, 100))}</text>
    ${renderSubtitleAndIP(p, 52, 40, 52, 'rgba(255,255,255,0.45)', 'rgba(255,255,255,0.65)')}
    ${vlanBadge(p, 52, H - 19)}
    <rect x="1" y="1" width="158" height="2" rx="1" fill="rgba(239,68,68,0.2)"/>
  </g>`;
}

// ═══════════════════════════ DRAG ═══════════════════════════
const svg = document.getElementById('topo-svg');
let dragNode = null, dragSVGStart = null, dragNodeStart = null, rafDrag = null, _pwRenderPending = false;

function getSVGPt(e) {
  const pt = svg.createSVGPoint();
  const src = e.touches ? e.touches[0] : e;
  pt.x = src.clientX; pt.y = src.clientY;
  const vport = document.getElementById('viewport');
  return pt.matrixTransform(vport.getScreenCTM().inverse());
}

function startDrag(e, node) {
  if (e.shiftKey) return;  // shift+drag = rubber-band, not single-node drag
  e.preventDefault();
  if (e.altKey) { startLinkDraw(e, node); return; }
  dragNode = node;
  dragSVGStart = getSVGPt(e);
  dragNodeStart = { x: node.x, y: node.y };
  if (multiSelect.has(node.id) && multiSelect.size > 1) {
    dragMultiStart = [...multiSelect].map(id => nodeMap[id]).filter(Boolean)
      .map(n => ({ node: n, x0: n.x, y0: n.y }));
  } else {
    dragMultiStart = [];
  }
  svg.style.cursor = 'grabbing';
}

function doDrag(e) {
  if (linkDraw) return;
  if (!dragNode) return;
  const pt = getSVGPt(e); // capture coords immediately before RAF delay
  if (rafDrag) cancelAnimationFrame(rafDrag);
  rafDrag = requestAnimationFrame(() => {
    rafDrag = null;
    const dx = pt.x - dragSVGStart.x;
    const dy = pt.y - dragSVGStart.y;
    if (dragMultiStart.length > 0) {
      dragMultiStart.forEach(({ node, x0, y0 }) => {
        node.x = x0 + dx;
        node.y = y0 + dy;
        document.getElementById('node-' + node.id)
          ?.setAttribute('transform', `translate(${node.x},${node.y})`);
        document.getElementById('node-label-' + node.id)
          ?.setAttribute('transform', `translate(${node.x},${node.y})`);
      });
    } else {
      dragNode.x = dragNodeStart.x + dx;
      dragNode.y = dragNodeStart.y + dy;
      document.getElementById('node-'+dragNode.id)
        ?.setAttribute('transform',`translate(${dragNode.x},${dragNode.y})`);
      document.getElementById('node-label-'+dragNode.id)
        ?.setAttribute('transform',`translate(${dragNode.x},${dragNode.y})`);
    }
    renderLinks();
    resizeSVG();
  });
}

async function endDrag() {
  if (linkDraw) return;
  if (rafDrag) { cancelAnimationFrame(rafDrag); rafDrag = null; }
  if (dragNode) {
    if (isPingWatchPage && dragNode._pwDid) {
      pwOverrides[dragNode._pwDid] = { ...(pwOverrides[dragNode._pwDid] || {}), x: dragNode.x, y: dragNode.y };
      _pwSave('pw_node_overrides', pwOverrides);
      dragNode = null; dragMultiStart = [];
      svg.style.cursor = 'default';
      if (_pwRenderPending) { _pwRenderPending = false; renderPingWatchCanvas(); }
      return;
    }
    const snap = dragMultiStart.length > 0
      ? dragMultiStart.map(({ node, x0, y0 }) => ({ id:node.id, x0, y0, x1:node.x, y1:node.y }))
      : [{ id:dragNode.id, x0:dragNodeStart.x, y0:dragNodeStart.y, x1:dragNode.x, y1:dragNode.y }];
    try {
      await Promise.all(snap.map(s => api('PUT',`/api/nodes/${s.id}`,{x:s.x1,y:s.y1})));
      pushAction(
        async () => { await Promise.all(snap.map(s=>api('PUT',`/api/nodes/${s.id}`,{x:s.x0,y:s.y0}))); await loadData(); },
        async () => { await Promise.all(snap.map(s=>api('PUT',`/api/nodes/${s.id}`,{x:s.x1,y:s.y1}))); await loadData(); }
      );
    } catch (err) { toast('⚠ Save failed: ' + err.message); }
    dragNode = null; dragMultiStart = [];
    svg.style.cursor = 'default';
  }
}

window.addEventListener('mousemove', e => { doMMPan(e); doGroupDrag(e); doLinkDraw(e); doRubberBand(e); doDrag(e); });
window.addEventListener('mouseup',   e => { endMMPan(e); endGroupDrag(e); endLinkDraw(e); endRubberBand(e); endDrag(e); });
window.addEventListener('touchmove', e => { if(dragNode){e.preventDefault();doDrag(e);} }, {passive:false});
window.addEventListener('touchend', endDrag);
svg.addEventListener('click', e => { if (!e.shiftKey) { multiSelect.clear(); } deselect(); });

// Canvas pan (left or middle) + Shift+left rubber-band
svg.addEventListener('mousedown', e => {
  if (e.button === 1) { // middle mouse = pan (keep for power users)
    e.preventDefault();
    mmPan = { sx: e.clientX, sy: e.clientY, tx0: vp.tx, ty0: vp.ty };
    svg.style.cursor = 'grabbing';
    return;
  }
  if (e.button !== 0 || e.altKey || linkDraw) return;
  if (e.shiftKey) {
    // Shift+drag always starts rubber-band regardless of node/group under cursor
  } else {
    const overNode = findNodeAtEvent(e);
    if (overNode) return;
    const overGroup = findGroupAtEvent(e);
    if (overGroup) return;
    // left drag on empty canvas = pan
    e.preventDefault();
    mmPan = { sx: e.clientX, sy: e.clientY, tx0: vp.tx, ty0: vp.ty };
    svg.style.cursor = 'grabbing';
    return;
  }
  // Shift + left drag = rubber-band selection
  const pt = getSVGPt(e);
  rubberBand = { x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y };
  const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
  rect.id = 'rubber-band';
  rect.setAttribute('fill','rgba(0,212,255,0.07)');
  rect.setAttribute('stroke','rgba(0,212,255,0.5)');
  rect.setAttribute('stroke-width','1');
  rect.setAttribute('stroke-dasharray','4,3');
  rect.setAttribute('x', pt.x); rect.setAttribute('y', pt.y);
  rect.setAttribute('width','0'); rect.setAttribute('height','0');
  document.getElementById('viewport').appendChild(rect);
  e.preventDefault();
});

// ═══════════════════════════ SELECTION ═══════════════════════════
function selectNode(node) {
  selectedEl = { type:'node', data: node };
  renderNodes();
  renderLinks();
  showNodePanel(node);
}

function toggleMultiSelect(node) {
  if (multiSelect.has(node.id)) multiSelect.delete(node.id);
  else multiSelect.add(node.id);
  selectedEl = null;
  renderNodes();
  renderLinks();
  if (multiSelect.size > 0) showMultiPanel();
  else deselect();
}

function selectLink(lk) {
  selectedEl = { type:'link', data: lk };
  renderNodes();
  renderLinks();
  showLinkPanel(lk);
}

function deselect() {
  selectedEl = null;
  if (multiSelect.size > 0) { showMultiPanel(); return; }
  if (isPingWatchPage) { _selectedPwDid = null; showPwDashboardPanel(); return; }
  renderNodes();
  renderLinks();
  document.getElementById('panel-title').textContent = 'SELECT AN ELEMENT';
  document.getElementById('panel-icon').textContent = '◈';
  showDashboardPanel();
}

function showNodePanel(node) {
  const p = node.properties || {};
  document.getElementById('panel-title').textContent = node.name.toUpperCase();
  document.getElementById('panel-icon').textContent = '◉';
  document.getElementById('panel-body').innerHTML = `
    <div class="field-group"><div class="field-label">TYPE</div><span style="color:var(--accent);font-family:Share Tech Mono,monospace;font-size:11px;">${escXml(node.type)}</span></div>
    <div class="field-group"><div class="field-label">POSITION</div><span style="color:rgba(255,255,255,0.5);font-family:Share Tech Mono,monospace;font-size:10px;">x:${Math.round(node.x)} y:${Math.round(node.y)}</span></div>
    ${p.ip ? `<div class="field-group"><div class="field-label">IP ADDRESS</div><span style="color:${escXml(p.ip_color||'var(--accent2)')};font-family:Share Tech Mono,monospace;font-size:11px;">${escXml(p.ip)}</span></div>` : ''}
    ${p.subtitle ? `<div class="field-group"><div class="field-label">SUBTITLE</div><span style="color:${escXml(p.subtitle_color||'rgba(255,255,255,0.5)')};font-family:Share Tech Mono,monospace;font-size:10px;">${escXml(p.subtitle)}</span></div>` : ''}
    <div class="field-group"><div class="field-label">CONNECTED LINKS</div>
      ${links.filter(l=>l.source_id===node.id||l.target_id===node.id).map(l=>{
        const other = nodeMap[l.source_id===node.id ? l.target_id : l.source_id];
        const cfg = lcfg(l.link_type);
        return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">
          <div style="width:16px;height:2px;background:${cfg.stroke};"></div>
          <span style="font-family:Share Tech Mono,monospace;font-size:9px;color:rgba(255,255,255,0.5);">${escXml(other?.name||'?')}</span>
        </div>`;
      }).join('') || '<span style="color:rgba(255,255,255,0.25);font-family:Share Tech Mono,monospace;font-size:10px;">None</span>'}
    </div>
    <div class="field-group">
      <div class="field-label">NOTES</div>
      <textarea id="node-notes-ta" class="node-notes-ta" placeholder="Add notes…" onblur="saveNodeNotes(${node.id},this.value)">${escXml(p.notes||'')}</textarea>
    </div>
  `;
  document.getElementById('panel-actions').innerHTML = `
    <button class="btn btn-primary" style="flex:1" onclick="openEditNode(${node.id})">✎ EDIT</button>
    <button class="btn btn-danger" style="flex:1" onclick="deleteNode(${node.id})">✕ DELETE</button>

    <div style="width:100%;text-align:center;font-family:Share Tech Mono,monospace;font-size:9px;color:rgba(0,212,255,0.35);letter-spacing:1px;padding-top:6px;">ALT+DRAG → LINK</div>
  `;
}

async function saveNodeNotes(nodeId, text) {
  const node = nodes.find(n => n.id === nodeId);
  if (!node) return;
  const newProps = Object.assign({}, node.properties || {}, { notes: text });
  node.properties = newProps;
  try {
    await api('PUT', `/api/nodes/${nodeId}`, { name: node.name, type: node.type, x: node.x, y: node.y, properties: newProps });
  } catch(e) { toast('⚠ Failed to save notes'); }
}

function showLinkPanel(lk) {
  const src = nodeMap[lk.source_id];
  const tgt = nodeMap[lk.target_id];
  const cfg = lcfg(lk.link_type);
  document.getElementById('panel-title').textContent = 'LINK';
  document.getElementById('panel-icon').textContent = '⟷';
  document.getElementById('panel-body').innerHTML = `
    <div class="field-group"><div class="field-label">TYPE</div><span style="color:${cfg.stroke};font-family:Share Tech Mono,monospace;font-size:11px;">${lk.link_type.toUpperCase()}</span></div>
    <div class="field-group"><div class="field-label">FROM</div><span style="color:var(--accent);font-family:Share Tech Mono,monospace;font-size:11px;">${src?.name||'?'}</span></div>
    <div class="field-group"><div class="field-label">TO</div><span style="color:var(--accent);font-family:Share Tech Mono,monospace;font-size:11px;">${tgt?.name||'?'}</span></div>
    ${lk.label ? `<div class="field-group"><div class="field-label">LABEL</div><span style="color:rgba(255,255,255,0.6);font-family:Share Tech Mono,monospace;font-size:11px;">${escXml(lk.label)}</span></div>` : ''}
  `;
  document.getElementById('panel-actions').innerHTML = `
    <button class="btn btn-primary" style="flex:1" onclick="openEditLink(${lk.id})">✎ EDIT</button>
    <button class="btn btn-danger" style="flex:1" onclick="deleteLink(${lk.id})">✕ DELETE</button>
  `;
}

// ── Tab right-click context menu ─────────────────────────────────────────────
function _tabMenu(e, pg) {
  document.getElementById('_tab_ctx_menu')?.remove();
  const menu = document.createElement('div');
  menu.id = '_tab_ctx_menu';
  menu.style.cssText = `position:fixed;left:${e.clientX}px;top:${e.clientY}px;background:#1a2035;border:1px solid rgba(0,212,255,0.2);border-radius:6px;padding:4px 0;z-index:9999;min-width:140px;box-shadow:0 4px 16px rgba(0,0,0,0.5);`;
  const items = [
    { icon: '✏', label: 'Rename', action: () => renamePage(pg.id, pg.name), danger: false },
    ...(pages.length > 1 ? [{ icon: '🗑', label: 'Delete', action: () => deletePage(pg.id, pg.name), danger: true }] : []),
  ];
  items.forEach(item => {
    const row = document.createElement('div');
    row.style.cssText = `padding:7px 14px;cursor:pointer;font-size:11px;font-family:'Share Tech Mono',monospace;letter-spacing:.5px;color:${item.danger ? '#ff5555' : 'rgba(0,212,255,0.8)'};display:flex;align-items:center;gap:8px;`;
    row.innerHTML = `<span>${item.icon}</span><span>${item.label}</span>`;
    row.onmouseenter = () => row.style.background = 'rgba(0,212,255,0.07)';
    row.onmouseleave = () => row.style.background = '';
    row.onclick = () => { close(); item.action(); };
    menu.appendChild(row);
  });
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  if (r.right  > window.innerWidth)  menu.style.left = (window.innerWidth  - r.width  - 8) + 'px';
  if (r.bottom > window.innerHeight) menu.style.top  = (window.innerHeight - r.height - 8) + 'px';
  const close = () => menu.remove();
  setTimeout(() => document.addEventListener('click', close, { once: true }), 0);
  document.addEventListener('keydown', ev => { if (ev.key === 'Escape') close(); }, { once: true });
}

// ── Shared confirm dialog (replaces window.confirm — blocked on remote HTTP) ──
function _confirm(msg, onYes, yesLabel='Yes, Delete', danger=true) {
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  const btnColor = danger ? '#ff4444' : 'var(--accent,#00d4ff)';
  ov.innerHTML = `<div style="background:#1a2035;border:1px solid #2a3448;border-radius:10px;padding:24px 28px;max-width:360px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:13px;color:#c0cce0;margin-bottom:18px;">${msg}</div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button id="_cfm_no"  style="padding:7px 18px;border-radius:6px;border:1px solid #2a3448;background:transparent;color:#8899aa;cursor:pointer;font-size:12px;">Cancel</button>
      <button id="_cfm_yes" style="padding:7px 18px;border-radius:6px;border:none;background:${btnColor};color:#fff;cursor:pointer;font-weight:600;font-size:12px;">${yesLabel}</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  const close = () => document.body.removeChild(ov);
  ov.querySelector('#_cfm_no').onclick  = close;
  ov.querySelector('#_cfm_yes').onclick = () => { close(); onYes(); };
}

// ═══════════════════════════ CRUD OPS ═══════════════════════════
async function deleteNode(id) {
  _confirm('Delete this device and all its links?', async () => {
  const node = nodeMap[id];
  const nodeLinks = links.filter(l => l.source_id===id || l.target_id===id);
  try {
    await api('DELETE',`/api/nodes/${id}`);
    pushAction(
      async () => {
        const r = await api('POST','/api/nodes',{name:node.name,type:node.type,x:node.x,y:node.y,properties:node.properties});
        for (const lk of nodeLinks) {
          await api('POST','/api/links',{
            source_id: lk.source_id===id ? r.id : lk.source_id,
            target_id: lk.target_id===id ? r.id : lk.target_id,
            label: lk.label, link_type: lk.link_type
          });
        }
        await loadData();
      }, null
    );
    selectedEl = null; multiSelect.clear();
    await loadData();
    deselect();
    toast('Device deleted');
  } catch (e) {
    toast('⚠ Delete failed: ' + e.message);
  }
  });
}

async function deleteLink(id) {
  _confirm('Delete this link?', async () => {
    const lk = links.find(l => l.id===id);
    try {
      await api('DELETE',`/api/links/${id}`);
      if (lk) pushAction(
        async () => { await api('POST','/api/links',{source_id:lk.source_id,target_id:lk.target_id,label:lk.label,link_type:lk.link_type}); await loadData(); },
        async () => { await api('DELETE',`/api/links/${id}`); await loadData(); }
      );
      selectedEl = null;
      await loadData();
      deselect();
      toast('Link deleted');
    } catch (e) {
      toast('⚠ Delete failed: ' + e.message);
    }
  });
}

// ═══════════════════════════ MODALS ═══════════════════════════
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function openAddNode() {
  editingNodeId = null;
  document.getElementById('modal-node-title').textContent = 'ADD DEVICE';
  document.getElementById('node-save-btn').textContent = 'ADD DEVICE';
  document.getElementById('node-name').value = '';
  document.getElementById('node-type').value = 'switch';
  document.getElementById('node-ip-color').value = '#00ff9d';
  document.getElementById('node-ip').value = '';
  document.getElementById('node-subtitle-color').value = '#ffffff';
  document.getElementById('node-subtitle').value = '';
  document.getElementById('node-vlan').value = '';
  renderVlanChips();
  const _fwsReset = document.getElementById('node-fw-status');
  if (_fwsReset) _fwsReset.value = '';
  document.getElementById('node-name-color-enabled').checked = false;
  document.getElementById('node-name-color').value = '#00d4ff';
  document.getElementById('node-color-enabled').checked = false;
  document.getElementById('node-color').value = '#00d4ff';
  setInfoEditorVisible(document.getElementById('node-type').value);
  clearInfoLines(); // reset
  if (document.getElementById('node-type').value === 'info-box') {
    loadInfoLines([], { blankTemplate: true });
  }
  openModal('modal-node');
}

function openEditNode(id) {
  const node = nodeMap[id];
  if (!node) return;
  editingNodeId = id;
  document.getElementById('modal-node-title').textContent = 'EDIT DEVICE';
  document.getElementById('node-save-btn').textContent = 'SAVE CHANGES';
  document.getElementById('node-name').value = node.name;
  document.getElementById('node-type').value = node.type;
  document.getElementById('node-ip-color').value =
	toHexColor(node.properties?.ip_color, '#00ff9d');
  document.getElementById('node-ip').value = node.properties?.ip || '';
  document.getElementById('node-subtitle-color').value =
	toHexColor(node.properties?.subtitle_color, '#ffffff');
  document.getElementById('node-subtitle').value = node.properties?.subtitle || '';
  document.getElementById('node-vlan').value = node.properties?.vlan || '';
  renderVlanChips();
  const _fws = document.getElementById('node-fw-status');
  if (_fws) _fws.value = node.properties?.status || '';
  const _nnc = node.properties?.name_color || null;
  document.getElementById('node-name-color-enabled').checked = !!_nnc;
  document.getElementById('node-name-color').value = _nnc || '#00d4ff';
  const _nc = node.properties?.color || null;
  document.getElementById('node-color-enabled').checked = !!_nc;
  document.getElementById('node-color').value = _nc || '#00d4ff';
  setInfoEditorVisible(node.type);
  if (node.type === 'info-box') loadInfoLines(node.properties?.lines || []);
  else clearInfoLines();
  openModal('modal-node');
}

async function saveNode() {
  const name = document.getElementById('node-name').value.trim();
  const type = document.getElementById('node-type').value;

  const ipColor = toHexColor(document.getElementById('node-ip-color').value, '#00ff9d');
  const ip = document.getElementById('node-ip').value.trim();

  const subtitleColor = toHexColor(document.getElementById('node-subtitle-color').value, '#ffffff');
  const subtitle = document.getElementById('node-subtitle').value.trim();

  const vlan = document.getElementById('node-vlan').value.trim();
  const nameColorEnabled = document.getElementById('node-name-color-enabled').checked;
  const nameColor = nameColorEnabled ? document.getElementById('node-name-color').value : null;
  const nodeColorEnabled = document.getElementById('node-color-enabled').checked;
  const nodeColor = nodeColorEnabled ? document.getElementById('node-color').value : null;

  const infoLines = (type === 'info-box') ? collectInfoLinesFromUI() : null;
  const fwStatus = (type === 'firewall' || type === 'switch' || type === 'bb-switch')
    ? (document.getElementById('node-fw-status')?.value || '')
    : null;

  if (!name) { toast('Device name is required'); return; }

  const applyField = (obj, key, val) => {
    if (val) obj[key] = val;
    else delete obj[key];
  };

  try {
    if (editingNodeId) {
      const existing = nodeMap[editingNodeId];
      if (!existing) return;

      const nextProps = { ...(existing.properties || {}) };

      if (type !== 'info-box') {
        applyField(nextProps, 'ip_color', ipColor);
        applyField(nextProps, 'ip', ip);
        applyField(nextProps, 'subtitle_color', subtitleColor);
        applyField(nextProps, 'subtitle', subtitle);
        applyField(nextProps, 'name_color', nameColor);
        applyField(nextProps, 'vlan', vlan);
        applyField(nextProps, 'color', nodeColor);
        if (fwStatus !== null) applyField(nextProps, 'status', fwStatus);
        delete nextProps.lines;
      } else {
        delete nextProps.ip;
        delete nextProps.ip_color;
        delete nextProps.subtitle;
        delete nextProps.subtitle_color;
        delete nextProps.vlan;
        nextProps.lines = infoLines || [];
      }

      const before_n = { ...existing, properties: { ...(existing.properties || {}) } };
      await api('PUT', `/api/nodes/${editingNodeId}`, {
        name, type, x: existing.x, y: existing.y,
        properties: nextProps
      });
      pushAction(
        async () => { await api('PUT',`/api/nodes/${editingNodeId}`,{name:before_n.name,type:before_n.type,x:before_n.x,y:before_n.y,properties:before_n.properties}); await loadData(); },
        async () => { await api('PUT',`/api/nodes/${editingNodeId}`,{name,type,x:existing.x,y:existing.y,properties:nextProps}); await loadData(); }
      );
      toast('Device updated');
    } else {
      const props = {};
      if (type !== 'info-box') {
        applyField(props, 'ip_color', ipColor);
        applyField(props, 'ip', ip);
        applyField(props, 'subtitle_color', subtitleColor);
        applyField(props, 'subtitle', subtitle);
        applyField(props, 'vlan', vlan);
        applyField(props, 'color', nodeColor);
        if (fwStatus !== null) applyField(props, 'status', fwStatus);
      } else {
        props.lines = infoLines || [];
      }
      const newNX = 200 + Math.random() * 400, newNY = 200 + Math.random() * 300;
      const newN = await api('POST', '/api/nodes', { name, type, x:newNX, y:newNY, properties:props, page_id:currentPageId });
      pushAction(
        async () => { await api('DELETE',`/api/nodes/${newN.id}`); await loadData(); },
        async () => { await api('POST','/api/nodes',{name,type,x:newNX,y:newNY,properties:props,page_id:currentPageId}); await loadData(); }
      );
      toast('Device added');
    }
  } catch (e) {
    toast('⚠ Save failed: ' + e.message);
    return;
  }

  closeModal('modal-node');
  try {
    await loadData();
  } catch (e) {
    toast('⚠ Reload failed: ' + e.message);
  }
  deselect();
}

// ═══════════════════════════ VLAN COLORS ═══════════════════════════

function buildVlanRow(vid, color) {
  const safeId = String(vid).replace(/[^a-z0-9]/gi, '');
  const row = document.createElement('div');
  row.className = 'vlan-row';
  row.dataset.vid = vid;
  row.style.cssText = 'display:grid;grid-template-columns:80px 48px 1fr 32px;gap:10px;align-items:center;margin-bottom:8px;';
  row.innerHTML = `
    <span style="font-family:Share Tech Mono,monospace;font-size:11px;font-weight:700;color:${escXml(color)};"
      class="vlan-row-label">VLAN ${escXml(String(vid))}</span>
    <input type="color" class="field-color vlan-color-inp" value="${escXml(toHexColor(color,'#00d4ff'))}"
      style="width:48px;height:34px;" />
    <div class="vlan-row-bar" style="height:10px;border-radius:3px;background:${escXml(color)};border:1px solid ${escXml(color)};box-shadow:0 0 6px ${escXml(color)}40;"></div>
    <button class="icon-btn vlan-del-btn" title="Remove VLAN ${escXml(String(vid))}"
      style="width:32px;height:34px;border-color:var(--danger);color:var(--danger);">✕</button>
  `;
  const inp = row.querySelector('.vlan-color-inp');
  const bar = row.querySelector('.vlan-row-bar');
  const lbl = row.querySelector('.vlan-row-label');
  inp.addEventListener('input', () => {
    bar.style.background = inp.value;
    bar.style.borderColor = inp.value;
    bar.style.boxShadow = `0 0 6px ${inp.value}40`;
    lbl.style.color = inp.value;
  });
  row.querySelector('.vlan-del-btn').addEventListener('click', () => row.remove());
  return row;
}

function openVlanColors() {
  const host = document.getElementById('vlan-color-rows');
  host.innerHTML = '';
  // Sort numerically
  Object.entries(VLAN_COLORS)
    .sort((a, b) => parseInt(a[0]) - parseInt(b[0]))
    .forEach(([vid, color]) => host.appendChild(buildVlanRow(vid, color)));

  document.getElementById('new-vlan-id').value = '';
  document.getElementById('new-vlan-color').value = '#00d4ff';
  openModal('modal-vlan');
}

function addVlanRow() {
  const idInp = document.getElementById('new-vlan-id');
  const colorInp = document.getElementById('new-vlan-color');
  const raw = idInp.value.trim().replace(/[^0-9]/g, '');
  if (!raw) { idInp.focus(); return; }
  const vid = raw;

  // Check for duplicates
  const existing = [...document.querySelectorAll('#vlan-color-rows .vlan-row')];
  if (existing.some(r => r.dataset.vid === vid)) {
    idInp.style.borderColor = 'var(--danger)';
    idInp.title = 'VLAN already exists';
    setTimeout(() => { idInp.style.borderColor = ''; idInp.title = ''; }, 1500);
    return;
  }

  document.getElementById('vlan-color-rows').appendChild(buildVlanRow(vid, colorInp.value));
  idInp.value = '';
  idInp.focus();
}

async function saveVlanColors() {
  const newColors = {};
  document.querySelectorAll('#vlan-color-rows .vlan-row').forEach(row => {
    const vid = row.dataset.vid;
    const color = row.querySelector('.vlan-color-inp').value;
    if (vid) newColors[vid] = color;
  });
  if (Object.keys(newColors).length === 0) {
    toast('⚠ At least one VLAN is required');
    return;
  }
  try {
    await api('PUT', '/api/settings/vlan_colors', { value: newColors });
    VLAN_COLORS = newColors;
    applyVlanStyles();
    refreshVlanDatalist();
    render();
    closeModal('modal-vlan');
    toast('VLAN colors saved');
  } catch (e) {
    toast('⚠ Save failed: ' + e.message);
  }
}

// Keeps the <datalist> on the device form in sync with VLAN_COLORS
function refreshVlanDatalist() { /* dropdown now built dynamically by vlanDropdownFilter */ }

function renderVlanChips() {
  const hidden = document.getElementById('node-vlan');
  const wrap = document.getElementById('vlan-chips');
  if (!wrap || !hidden) return;
  const ids = _vlanIds(hidden.value);
  wrap.innerHTML = ids.map(v => {
    const c = VLAN_COLORS[v] || '#00d4ff';
    const rgb = hexToRgb(c);
    return `<span style="display:inline-flex;align-items:center;gap:3px;padding:1px 6px;border-radius:3px;background:rgba(${rgb},0.15);border:1px solid ${c};font-family:'Share Tech Mono',monospace;font-size:9px;color:${c};">V${escXml(v)}<span onclick="removeVlanChip('${escXml(v)}')" style="cursor:pointer;opacity:0.6;font-size:11px;line-height:1;margin-left:1px;">×</span></span>`;
  }).join('');
}
function addVlanChip(raw) {
  const m = raw.match(/\d+/);
  if (!m) return;
  const v = m[0];
  const hidden = document.getElementById('node-vlan');
  const ids = _vlanIds(hidden.value);
  if (!ids.includes(v)) { ids.push(v); hidden.value = ids.join(' '); }
  const inp = document.getElementById('vlan-chip-inp');
  if (inp) inp.value = '';
  vlanDropdownHide();
  renderVlanChips();
}
function vlanDropdownFilter(q) {
  const dd = document.getElementById('vlan-dropdown');
  if (!dd) return;
  const ids = Object.keys(VLAN_COLORS).sort((a,b) => parseInt(a) - parseInt(b));
  const filter = q.replace(/\D/g, '');
  const matches = ids.filter(v => !filter || v.startsWith(filter));
  if (!matches.length) { dd.style.display = 'none'; return; }
  dd.innerHTML = matches.map(v => {
    const c = VLAN_COLORS[v] || '#00d4ff';
    return `<div onclick="addVlanChip('${v}')" style="padding:5px 8px;cursor:pointer;font-family:'Share Tech Mono',monospace;font-size:10px;color:${c};border-bottom:1px solid rgba(0,212,255,0.08);" onmouseenter="this.style.background='rgba(0,212,255,0.08)'" onmouseleave="this.style.background=''">VLAN${v}</div>`;
  }).join('');
  dd.style.display = 'block';
}
function vlanDropdownHide() {
  const dd = document.getElementById('vlan-dropdown');
  if (dd) dd.style.display = 'none';
}
function vlanDropdownToggle() {
  const dd = document.getElementById('vlan-dropdown');
  if (!dd) return;
  if (dd.style.display === 'none') {
    vlanDropdownFilter(document.getElementById('vlan-chip-inp')?.value || '');
    document.getElementById('vlan-chip-inp')?.focus();
  } else {
    vlanDropdownHide();
  }
}
function removeVlanChip(v) {
  const hidden = document.getElementById('node-vlan');
  hidden.value = _vlanIds(hidden.value).filter(x => x !== v).join(' ');
  renderVlanChips();
}
function vlanChipKey(e) {
  if (e.key === 'Enter' || e.key === ' ' || e.key === ',') {
    e.preventDefault();
    addVlanChip(e.target.value.trim());
  } else if (e.key === 'Backspace' && !e.target.value) {
    const ids = _vlanIds(document.getElementById('node-vlan').value);
    if (ids.length) removeVlanChip(ids[ids.length - 1]);
  }
}

function openAddLink(presetSrcId, presetTgtId) {
  editingLinkId = null;
  document.getElementById('modal-link-title').textContent = 'ADD LINK';
  document.getElementById('link-save-btn').textContent = 'ADD LINK';
  document.getElementById('link-label').value = '';
  document.getElementById('link-type').value = 'access';
  populateNodeSelects(presetSrcId, presetTgtId);
  openModal('modal-link');
}

function openEditLink(id) {
  const lk = links.find(l=>l.id===id);
  if (!lk) return;
  editingLinkId = id;
  document.getElementById('modal-link-title').textContent = 'EDIT LINK';
  document.getElementById('link-save-btn').textContent = 'SAVE CHANGES';
  document.getElementById('link-label').value = lk.label || '';
  document.getElementById('link-type').value = lk.link_type;
  populateNodeSelects(lk.source_id, lk.target_id);
  openModal('modal-link');
}

function populateNodeSelects(selSrc, selTgt) {
  const srcSel = document.getElementById('link-source');
  const tgtSel = document.getElementById('link-target');
  // Default target to first node that isn't the source
  const defaultTgt = selTgt ?? nodes.find(n => n.id !== selSrc)?.id ?? selSrc;
  srcSel.innerHTML = nodes.map(n=>`<option value="${n.id}" ${n.id===selSrc?'selected':''}>${escXml(n.name)}</option>`).join('');
  tgtSel.innerHTML = nodes.map(n=>`<option value="${n.id}" ${n.id===defaultTgt?'selected':''}>${escXml(n.name)}</option>`).join('');
}

async function saveLink() {
  const source_id = parseInt(document.getElementById('link-source').value);
  const target_id = parseInt(document.getElementById('link-target').value);
  const label = document.getElementById('link-label').value.trim();
  const link_type = document.getElementById('link-type').value;
  if (source_id === target_id) { toast('Source and target must be different'); return; }
  try {
    if (editingLinkId) {
      const beforeLk = links.find(l=>l.id===editingLinkId);
      await api('PUT',`/api/links/${editingLinkId}`, { source_id, target_id, label, link_type });
      if (beforeLk) pushAction(
        async () => { await api('PUT',`/api/links/${editingLinkId}`,{source_id:beforeLk.source_id,target_id:beforeLk.target_id,label:beforeLk.label,link_type:beforeLk.link_type}); await loadData(); },
        async () => { await api('PUT',`/api/links/${editingLinkId}`,{source_id,target_id,label,link_type}); await loadData(); }
      );
      toast('Link updated');
    } else {
      const newLk = await api('POST','/api/links', { source_id, target_id, label, link_type, page_id:currentPageId });
      pushAction(
        async () => { await api('DELETE',`/api/links/${newLk.id}`); await loadData(); },
        async () => { await api('POST','/api/links',{source_id,target_id,label,link_type,page_id:currentPageId}); await loadData(); }
      );
      toast('Link added');
    }
  } catch (e) {
    toast('⚠ Save failed: ' + e.message);
    return;
  }
  closeModal('modal-link');
  try {
    await loadData();
  } catch (e) {
    toast('⚠ Reload failed: ' + e.message);
  }
  deselect();
}

// ═══════════════════════════ HELPERS ═══════════════════════════
function svgTextLine(x, y, text, fill, size = 9, weight = 'normal', cls = '') {
  if (!text) return '';
  const safe = String(text)
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;');
  return `<text x="${x}" y="${y}" fill="${fill}" font-family="Share Tech Mono" font-size="${size}" font-weight="${weight}"${cls ? ` class="${cls}"` : ''}>${safe}</text>`;
}

function escXml(s) {
  return String(s ?? '')
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;')
    .replaceAll('"','&quot;')
    .replaceAll("'","&#39;");
}

function renderOutsideLabel(node, p, cx, baseH, opts = {}) {
  const nameFill = p.name_color || opts.nameFill || '#93c5fd';
  const nameSize = opts.nameSize || 13;
  const line2Size = opts.line2Size || 10;
  const glow = opts.glow || 'url(#glow-blue)';

  const yName = baseH + 16;
  const yLine2 = yName + 14;

  const sub = (p.subtitle || '').trim();
  const ip  = (p.ip || '').trim();

  // Show subtitle on line2, IP on line3 if both present; otherwise whichever exists
  const line2Text  = sub || ip;
  const line2IsIP  = !sub && !!ip;
  const line2Color = sub ? (p.subtitle_color || 'rgba(180,220,255,0.45)')
                         : (p.ip_color || '#00d4ff');
  const line3Text  = (sub && ip) ? ip : '';
  const yLine3     = yLine2 + 13;

  // VLAN badge sits below the last text line
  const yBadge = (line3Text ? yLine3 : line2Text ? yLine2 : yName) + 8;

  // Truncate name to fit within node width — Exo 2 bold 13px ≈ 7.5px/char
  const nodeW    = opts.nodeW || (cx * 2);
  const maxChars = Math.max(8, Math.floor(nodeW / 7.5));
  const rawName  = node.name || '';
  const dispName = rawName.length > maxChars ? rawName.slice(0, maxChars - 1) + '\u2026' : rawName;

  const origFill = opts.nameFill || '#93c5fd';
  const svgContent = `
    <text data-pw-name data-pw-origfill="${origFill}" x="${cx}" y="${yName}" text-anchor="middle"
      fill="${nameFill}" font-family="Exo 2" font-size="${nameSize}"
      font-weight="700"${glow ? ` filter="${glow}"` : ''}>${escXml(dispName)}</text>

    ${line2Text ? `
      <text${line2IsIP ? ' data-pw-ip' : ''} x="${cx}" y="${yLine2}" text-anchor="middle"
        fill="${line2Color}" font-family="Share Tech Mono"
        font-size="${line2Size}"${line2IsIP ? ' font-weight="700" filter="url(#glow-blue)"' : ' opacity="0.85"'}>${escXml(line2Text)}</text>
    ` : ''}

    ${line3Text ? `
      <text data-pw-ip x="${cx}" y="${yLine3}" text-anchor="middle"
        fill="${p.ip_color || '#00d4ff'}" font-family="Share Tech Mono"
        font-size="${line2Size}" font-weight="700" filter="url(#glow-blue)">${escXml(line3Text)}</text>
    ` : ''}

    ${vlanBadge(p, cx - 23, yBadge)}
  `;

  // If a label layer is active, redirect labels there so they render above all node bodies
  if (_labelLayerTarget) {
    const lg = document.createElementNS('http://www.w3.org/2000/svg','g');
    lg.setAttribute('id', 'node-label-' + node.id);
    lg.setAttribute('transform', `translate(${node.x},${node.y})`);
    lg.innerHTML = svgContent;
    _labelLayerTarget.appendChild(lg);
    return '';
  }
  return svgContent;
}

function isHexColor(s) {
  return /^#[0-9a-fA-F]{6}$/.test(String(s || ''));
}

// Returns a guaranteed #RRGGBB
function toHexColor(value, fallback = '#00d4ff') {
  const v = String(value || '').trim();

  // already #RRGGBB
  if (isHexColor(v)) return v;

  // support #RGB shorthand
  const m3 = /^#([0-9a-fA-F]{3})$/.exec(v);
  if (m3) {
    const [r,g,b] = m3[1].split('');
    return `#${r}${r}${g}${g}${b}${b}`;
  }

  // optionally: support rgb()/rgba() input (for legacy DB values)
  const mRgb = /^rgba?\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})/i.exec(v);
  if (mRgb) {
    const r = Math.max(0, Math.min(255, parseInt(mRgb[1], 10)));
    const g = Math.max(0, Math.min(255, parseInt(mRgb[2], 10)));
    const b = Math.max(0, Math.min(255, parseInt(mRgb[3], 10)));
    return `#${[r,g,b].map(n => n.toString(16).padStart(2,'0')).join('')}`;
  }

  // unknown -> fallback
  return fallback;
}

function setInfoEditorVisible(type) {
  const info = document.getElementById('info-editor');
  const ip = document.getElementById('field-ip');
  const sub = document.getElementById('field-subtitle');
  const vlan = document.getElementById('field-vlan');
  const fwStatus = document.getElementById('field-fw-status');

  if (type === 'info-box') {
    info.classList.add('open');
    ip.style.display = 'none';
    sub.style.display = 'none';
    vlan.style.display = 'none';
    if (fwStatus) fwStatus.style.display = 'none';
  } else {
    info.classList.remove('open');
    ip.style.display = '';
    sub.style.display = '';
    vlan.style.display = '';
    if (fwStatus) fwStatus.style.display = (type === 'firewall' || type === 'switch' || type === 'bb-switch') ? '' : 'none';
	clearInfoLines();
  }
}

function addInfoLine(text = '', color = '#00d4ff') {
  const host = document.getElementById('info-lines');
  if (!host) return;

  const row = document.createElement('div');
  row.className = 'info-row';
  row.innerHTML = `
    <input class="info-color" type="color" value="${escXml(color)}" />
    <input class="info-text" type="text" placeholder="e.g. VLAN10 GW: 192.168.10.254" value="${escXml(text)}" />
    <button class="icon-btn" type="button" title="Remove">✕</button>
  `;

  row.querySelector('button').addEventListener('click', () => row.remove());
  host.appendChild(row);
}

function clearInfoLines() {
  const host = document.getElementById('info-lines');
  if (host) host.innerHTML = '';
}

function loadInfoLines(lines = []) {
  clearInfoLines();
  if (!Array.isArray(lines) || lines.length === 0) {
    // nice default starter lines
    addInfoLine('', '#ffffff');
    return;
  }

  // If you previously saved rgba(...) it won't work in <input type="color">.
  // We'll coerce those to a safe default if needed.
  lines.forEach(l => {
	const t = (l && l.text) ? String(l.text) : '';
	const c = toHexColor(l?.color, '#00d4ff');
	addInfoLine(t, c);
  });
}

function collectInfoLinesFromUI() {
  const host = document.getElementById('info-lines');
  if (!host) return [];

  const out = [];
  host.querySelectorAll('.info-row').forEach(row => {
    const color = toHexColor(row.querySelector('.info-color')?.value, '#00d4ff');
    const text = (row.querySelector('.info-text')?.value || '').trim();
    if (text) out.push({ text, color });
  });
  return out;
}

function hexToRgb(hex) {
  const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return r ? `${parseInt(r[1],16)},${parseInt(r[2],16)},${parseInt(r[3],16)}` : '0,212,255';
}

function renderSubtitleAndIP(p, x, ySubtitle, yIP, subtitleFill, ipFill) {
  const sub = (p.subtitle || '').trim();
  const ip  = (p.ip || '').trim();

  const subColor = p.subtitle_color || subtitleFill;
  const ipColor  = p.ip_color || ipFill;

  let out = '';

  if (sub)
    out += svgTextLine(x, ySubtitle, sub, subColor, 9);

  if (ip)
    out += `<text data-pw-ip x="${x}" y="${sub ? yIP : ySubtitle}" fill="${p.ip_color || '#00d4ff'}" font-family="Share Tech Mono" font-size="9" font-weight="700" filter="url(#glow-blue)">${String(ip).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}</text>`;

  return out;
}

let _toastTimer = null;
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = (msg.startsWith('⚠') ? '' : '✓ ') + msg;
  t.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove('show'), 2500);
}

function togglePanel() {
  document.getElementById('side-panel').classList.toggle('collapsed');
}

// Keyboard shortcuts
window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const searchEl = document.getElementById('search-input');
    if (searchEl?.value) { searchEl.value = ''; filterNodes(''); return; }
    closeModal('modal-node'); closeModal('modal-link'); closeModal('modal-group');
    multiSelect.clear(); deselect();
  }
  if (e.ctrlKey && e.key === '0') { e.preventDefault(); vp={scale:1,tx:0,ty:0}; applyViewport(); }
  if (e.ctrlKey && e.key === 'a' && e.target.tagName !== 'INPUT') {
    e.preventDefault(); nodes.forEach(n => multiSelect.add(n.id));
    renderNodes(); showMultiPanel();
  }
  if (e.ctrlKey && e.key === 'z' && !e.shiftKey && e.target.tagName !== 'INPUT') { e.preventDefault(); doUndo(); }
  if (e.ctrlKey && (e.key === 'y' || (e.key === 'z' && e.shiftKey)) && e.target.tagName !== 'INPUT') { e.preventDefault(); doRedo(); }
  if ((e.key === 'Delete' || e.key === 'Backspace') && e.target.tagName !== 'INPUT') {
    if (multiSelect.size > 0) { deleteSelectedNodes(); return; }
    if (selectedEl?.type === 'node') deleteNode(selectedEl.data.id);
    if (selectedEl?.type === 'link') deleteLink(selectedEl.data.id);
    if (selectedEl?.type === 'group') deleteGroupById(selectedEl.data.id);
  }
});

// Close modals on overlay click (ignore mousedown-inside drags)
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  let _mdown = false;
  overlay.addEventListener('mousedown', e => { _mdown = (e.target === overlay); });
  overlay.addEventListener('click',     e => { if (e.target === overlay && _mdown) overlay.classList.remove('open'); });
});

// ═══════════════════════════ CONTEXT MENU ═══════════════════════════
const ctxMenu = document.getElementById('ctx-menu');

function showCtxMenu(x, y) {
  ctxMenu.style.display = 'block';
  const mw = ctxMenu.offsetWidth  || 160;
  const mh = ctxMenu.offsetHeight || 110;
  ctxMenu.style.left = (x + mw > window.innerWidth  ? x - mw : x) + 'px';
  ctxMenu.style.top  = (y + mh > window.innerHeight ? y - mh : y) + 'px';
}
function hideCtxMenu() { ctxMenu.style.display = 'none'; }
function ctxAction(fn) { hideCtxMenu(); fn(); }

document.getElementById('canvas-wrap').addEventListener('contextmenu', e => {
  e.preventDefault();
  if (isPingWatchPage) {
    const n = findNodeAtEvent(e);
    if (n && n._pwDid) {
      const bulkHtml = multiSelect.size > 0
        ? `<div class="ctx-sep"></div><div class="ctx-item" style="color:var(--gold)" onclick="ctxAction(()=>bulkLinkSelectedTo('${n._pwDid}'))">⟷ LINK ${multiSelect.size} SELECTED → THIS</div>`
        : '';
      const _inetItem = n._pwDid !== PW_INTERNET_DID
        ? `<div class="ctx-item" style="color:#ffd700" onclick="ctxAction(()=>connectDeviceToInternet('${n._pwDid}'))">🌐 CONNECT TO INTERNET</div>`
        : '';
      ctxMenu.innerHTML = `
        <div class="ctx-item" style="color:var(--accent2)" onclick="ctxAction(()=>showPwNodePanel('${n._pwDid}'))">✎ EDIT COLOR</div>
        <div class="ctx-item" style="color:var(--gold)" onclick="ctxAction(()=>ctxDrawLinkFrom('${n.id}'))">⟷ DRAW LINK</div>
        ${_inetItem}
        <div class="ctx-item" style="color:#ffd700" onclick="ctxAction(()=>_pwSetTraceSrc('${n._pwDid}'))">◉ SET AS TRACE SOURCE</div>
        ${bulkHtml}
      `;
      showCtxMenu(e.clientX, e.clientY);
      return;
    }
    const g = findGroupAtEvent(e);
    if (g && String(g.id).startsWith('pw_g_')) {
      ctxMenu.innerHTML = `
        <div class="ctx-item" style="color:var(--accent2)" onclick="ctxAction(()=>showGroupPanel(groupMap['${g.id}']))">✎ EDIT COLOR</div>
      `;
      showCtxMenu(e.clientX, e.clientY);
      return;
    }
    return;
  }
  ctxTargetNode = findNodeAtEvent(e);
  const n = ctxTargetNode;
  const g = !n ? findGroupAtEvent(e) : null;
  const multiHtml = multiSelect.size > 0
    ? `<div class="ctx-sep"></div><div class="ctx-item ctx-danger" onclick="ctxAction(deleteSelectedNodes)">✕ DELETE SELECTED (${multiSelect.size})</div>` : '';
  if (n) {
    ctxMenu.innerHTML = `
      <div class="ctx-item" style="color:var(--accent)" onclick="ctxAction(()=>duplicateNode(${n.id}))">⧉ DUPLICATE</div>
      <div class="ctx-item" style="color:var(--accent2)" onclick="ctxAction(()=>openEditNode(${n.id}))">✎ EDIT</div>
      <div class="ctx-item" style="color:var(--gold)" onclick="ctxAction(()=>ctxDrawLinkFrom(${n.id}))">⟷ DRAW LINK</div>
      <div class="ctx-sep"></div>
      <div class="ctx-item ctx-danger" onclick="ctxAction(()=>deleteNode(${n.id}))">✕ DELETE</div>
      ${multiHtml}
    `;
  } else if (g) {
    ctxMenu.innerHTML = `
      <div class="ctx-item" style="color:var(--accent2)" onclick="ctxAction(()=>editGroupOpen(${g.id}))">✎ EDIT GROUP</div>
      <div class="ctx-sep"></div>
      <div class="ctx-item ctx-danger" onclick="ctxAction(()=>deleteGroupById(${g.id}))">✕ DELETE GROUP</div>
      ${multiHtml}
    `;
  } else {
    ctxMenu.innerHTML = `
      <div class="ctx-item ctx-green"  onclick="ctxAction(openAddNode)">+ ADD DEVICE</div>
      <div class="ctx-item ctx-gold"   onclick="ctxAction(openAddLink)">+ ADD LINK</div>
      <div class="ctx-item ctx-purple" onclick="ctxAction(openVlanColors)">◈ VLAN COLORS</div>
      <div class="ctx-item" style="color:var(--accent)" onclick="ctxAction(openAddGroup)">◧ ADD GROUP</div>
      <div class="ctx-sep"></div>
      <div class="ctx-item ctx-muted" onclick="ctxAction(exportJSON)">⬇ EXPORT JSON</div>
      <div class="ctx-item ctx-muted" onclick="ctxAction(exportSVG)">⎙ EXPORT SVG</div>
      <div class="ctx-item ctx-muted" onclick="ctxAction(()=>document.getElementById('import-file').click())">⬆ IMPORT JSON</div>
      ${multiHtml}
    `;
  }
  showCtxMenu(e.clientX, e.clientY);
});
document.addEventListener('click',   hideCtxMenu);
document.addEventListener('mousedown', e => { if (!ctxMenu.contains(e.target)) hideCtxMenu(); });
document.addEventListener('keydown',  e => { if (e.key === 'Escape') hideCtxMenu(); });


// ═══════════════════════════ LINK DRAW ═══════════════════════════
function findNodeAtEvent(e) {
  const pt = getSVGPt(e);
  return nodes.find(n => {
    const s = nsize(n.type, n);
    return pt.x >= n.x && pt.x <= n.x + s.w && pt.y >= n.y && pt.y <= n.y + s.h;
  });
}

function findGroupAtEvent(e) {
  const pt = getSVGPt(e);
  return groups.find(g => pt.x >= g.x && pt.x <= g.x + g.w && pt.y >= g.y && pt.y <= g.y + g.h);
}

function startLinkDraw(e, srcNode) {
  linkDraw = { srcNode };
  svg.style.cursor = 'crosshair';
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.id = 'link-draw-temp';
  line.setAttribute('stroke', 'rgba(0,212,255,0.7)');
  line.setAttribute('stroke-width', '2');
  line.setAttribute('stroke-dasharray', '6,4');
  const cn = nodeCenter(srcNode);
  const pt = getSVGPt(e);
  line.setAttribute('x1', cn.x); line.setAttribute('y1', cn.y);
  line.setAttribute('x2', pt.x); line.setAttribute('y2', pt.y);
  document.getElementById('links-layer').appendChild(line);
}

function doLinkDraw(e) {
  if (!linkDraw) return;
  const line = document.getElementById('link-draw-temp');
  if (!line) return;
  const pt = getSVGPt(e);
  line.setAttribute('x2', pt.x);
  line.setAttribute('y2', pt.y);
  document.querySelectorAll('.node-g.link-hover').forEach(g => g.classList.remove('link-hover'));
  const tgt = findNodeAtEvent(e);
  if (tgt && tgt.id !== linkDraw.srcNode.id) {
    document.getElementById('node-' + tgt.id)?.classList.add('link-hover');
  }
}

function endLinkDraw(e) {
  if (!linkDraw) return;
  document.getElementById('link-draw-temp')?.remove();
  document.querySelectorAll('.node-g.link-hover').forEach(g => g.classList.remove('link-hover'));
  svg.style.cursor = 'default';
  const tgt = findNodeAtEvent(e);
  const src = linkDraw.srcNode;
  linkDraw = null;
  if (_pwRenderPending) { _pwRenderPending = false; renderPingWatchCanvas(); }
  if (tgt && tgt.id !== src.id) {
    if (isPingWatchPage && src._pwDid && tgt._pwDid) {
      _pwLinkModal(src, tgt, (link_type, label) => {
        const newLink = { id: 'pwl_' + Date.now(), src_did: src._pwDid, tgt_did: tgt._pwDid, link_type, label };
        pwLinks.push(newLink);
        _pwSave('pw_links', pwLinks);
        renderLinks();
        showPwLinkPanel(newLink.id);
      });
    } else {
      openAddLink(src.id, tgt.id);
    }
  }
}

function _pwLinkModal(src, tgt, onSave) {
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML = `<div style="background:#1a2035;border:1px solid #2a3448;border-radius:10px;padding:24px 28px;max-width:340px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:13px;color:#c0cce0;margin-bottom:16px;font-weight:600;letter-spacing:1px;">NEW LINK</div>
    <div style="font-size:11px;color:rgba(0,212,255,0.7);font-family:'Share Tech Mono',monospace;margin-bottom:14px;">${escXml(src.name)} → ${escXml(tgt.name)}</div>
    <div style="margin-bottom:12px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">LINK TYPE</div>
      <select id="_pwlm_type" style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;">
        ${['trunk','access','internet','ztna','ha_cluster'].map(t=>`<option value="${t}" style="background:#0d1a2e;color:#e2e8f0;">${t}</option>`).join('')}
      </select>
    </div>
    <div style="margin-bottom:18px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">LABEL (optional)</div>
      <input id="_pwlm_label" type="text" placeholder="e.g. VLAN10, WAN1…"
        style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;box-sizing:border-box;"/>
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button id="_pwlm_no"  style="padding:7px 18px;border-radius:6px;border:1px solid #2a3448;background:transparent;color:#8899aa;cursor:pointer;font-size:12px;">Cancel</button>
      <button id="_pwlm_yes" style="padding:7px 18px;border-radius:6px;border:none;background:var(--accent,#00d4ff);color:#000;cursor:pointer;font-weight:600;font-size:12px;">ADD LINK</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  const close = () => document.body.removeChild(ov);
  ov.querySelector('#_pwlm_no').onclick = close;
  ov.querySelector('#_pwlm_yes').onclick = () => {
    const type  = ov.querySelector('#_pwlm_type').value;
    const label = ov.querySelector('#_pwlm_label').value.trim();
    close();
    onSave(type, label);
  };
  setTimeout(() => ov.querySelector('#_pwlm_label').focus(), 50);
}


// ═══════════════════════════ BULK LINK ═══════════════════════════

function _createBulkPwLinks(srcDids, tgtDid, linkType, label) {
  const existingPairs = new Set(
    pwLinks.flatMap(l => [l.src_did + '→' + l.tgt_did, l.tgt_did + '→' + l.src_did])
  );
  let created = 0;
  const base = Date.now();
  for (const srcDid of srcDids) {
    if (String(srcDid) === String(tgtDid)) continue;
    const key = srcDid + '→' + tgtDid;
    if (existingPairs.has(key)) continue;
    pwLinks.push({
      id: 'pwl_' + base + '_' + created,
      src_did: srcDid, tgt_did: tgtDid,
      link_type: linkType, label
    });
    existingPairs.add(key);
    existingPairs.add(tgtDid + '→' + srcDid);
    created++;
  }
  if (created > 0) {
    _pwSave('pw_links', pwLinks);
    renderLinks();
  }
  return created;
}

function _getSelectedPwDids() {
  return [...multiSelect]
    .map(nid => nodeMap[nid])
    .filter(n => n && n._pwDid)
    .map(n => n._pwDid);
}

function openBulkLinkModal() {
  if (multiSelect.size === 0) return;
  const selectedDids = _getSelectedPwDids();
  if (!selectedDids.length) { toast('No PW devices selected'); return; }

  const selectedSet = new Set(selectedDids.map(String));
  const targets = pwDevices
    .filter(d => !selectedSet.has(String(d.device_id)))
    .map(d => ({ did: d.device_id, name: d.name }))
    .sort((a, b) => a.name.localeCompare(b.name));

  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML = `<div style="background:#1a2035;border:1px solid #2a3448;border-radius:10px;padding:24px 28px;max-width:400px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:13px;color:#c0cce0;margin-bottom:6px;font-weight:600;letter-spacing:1px;">BULK LINK</div>
    <div style="font-size:11px;color:rgba(0,212,255,0.7);font-family:'Share Tech Mono',monospace;margin-bottom:14px;">${selectedDids.length} devices → select target</div>
    <div style="margin-bottom:12px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">TARGET DEVICE</div>
      <input id="_blm_search" type="text" placeholder="Search devices…"
        style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;box-sizing:border-box;margin-bottom:4px;"/>
      <select id="_blm_target" size="6"
        style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:4px;font-size:11px;border-radius:4px;">
        ${targets.map(t => `<option value="${t.did}" style="background:#0d1a2e;color:#e2e8f0;padding:2px 4px;">${escXml(t.name)}</option>`).join('')}
      </select>
    </div>
    <div style="margin-bottom:12px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">LINK TYPE</div>
      <select id="_blm_type" style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;">
        ${['access','trunk','internet','ztna','ha_cluster'].map(t => `<option value="${t}" style="background:#0d1a2e;color:#e2e8f0;">${t}</option>`).join('')}
      </select>
    </div>
    <div style="margin-bottom:18px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">LABEL (optional)</div>
      <input id="_blm_label" type="text" placeholder="e.g. IPMI, Mgmt…"
        style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;box-sizing:border-box;"/>
    </div>
    <div id="_blm_status" style="font-size:10px;color:rgba(255,255,255,0.3);margin-bottom:10px;font-family:'Share Tech Mono',monospace;min-height:14px;"></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button id="_blm_no" style="padding:7px 18px;border-radius:6px;border:1px solid #2a3448;background:transparent;color:#8899aa;cursor:pointer;font-size:12px;">Cancel</button>
      <button id="_blm_yes" disabled style="padding:7px 18px;border-radius:6px;border:none;background:var(--accent,#00d4ff);color:#000;cursor:pointer;font-weight:600;font-size:12px;">ADD LINKS</button>
    </div>
  </div>`;
  document.body.appendChild(ov);

  const close    = () => document.body.removeChild(ov);
  const searchEl = ov.querySelector('#_blm_search');
  const selectEl = ov.querySelector('#_blm_target');
  const statusEl = ov.querySelector('#_blm_status');
  const yesBtn   = ov.querySelector('#_blm_yes');

  searchEl.addEventListener('input', () => {
    const q = searchEl.value.trim().toLowerCase();
    for (const opt of selectEl.options) {
      opt.hidden = q ? !opt.text.toLowerCase().includes(q) : false;
    }
  });

  function updateStatus() {
    const tgtDid = selectEl.value;
    if (!tgtDid) { statusEl.textContent = 'Select a target device'; yesBtn.disabled = true; return; }
    const existingPairs = new Set(
      pwLinks.flatMap(l => [l.src_did + '→' + l.tgt_did, l.tgt_did + '→' + l.src_did])
    );
    let newCount = 0, skipCount = 0;
    for (const srcDid of selectedDids) {
      if (String(srcDid) === String(tgtDid)) { skipCount++; continue; }
      if (existingPairs.has(srcDid + '→' + tgtDid)) { skipCount++; }
      else { newCount++; }
    }
    statusEl.textContent = newCount > 0
      ? `Will create ${newCount} link(s)` + (skipCount ? ` (${skipCount} skipped)` : '')
      : 'All links already exist';
    yesBtn.disabled = newCount === 0;
    yesBtn.textContent = newCount > 0 ? `ADD ${newCount} LINKS` : 'ADD LINKS';
  }
  selectEl.addEventListener('change', updateStatus);
  updateStatus();

  ov.querySelector('#_blm_no').onclick = close;
  yesBtn.onclick = () => {
    const tgtDid   = selectEl.value;
    const linkType = ov.querySelector('#_blm_type').value;
    const label    = ov.querySelector('#_blm_label').value.trim();
    if (!tgtDid) return;
    close();
    const created = _createBulkPwLinks(selectedDids, tgtDid, linkType, label);
    multiSelect.clear(); renderNodes(); deselect();
    toast(created > 0 ? `${created} link(s) created` : 'All links already exist');
  };

  setTimeout(() => searchEl.focus(), 50);
}

function bulkLinkSelectedTo(tgtDid) {
  const selectedDids = _getSelectedPwDids().filter(d => String(d) !== String(tgtDid));
  if (!selectedDids.length) { toast('No valid devices to link'); return; }

  const tgtName = _pwDevName(tgtDid);
  const ov = document.createElement('div');
  ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML = `<div style="background:#1a2035;border:1px solid #2a3448;border-radius:10px;padding:24px 28px;max-width:340px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:13px;color:#c0cce0;margin-bottom:6px;font-weight:600;letter-spacing:1px;">BULK LINK</div>
    <div style="font-size:11px;color:rgba(0,212,255,0.7);font-family:'Share Tech Mono',monospace;margin-bottom:14px;">${selectedDids.length} devices → ${escXml(tgtName)}</div>
    <div style="margin-bottom:12px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">LINK TYPE</div>
      <select id="_blm2_type" style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;">
        ${['access','trunk','internet','ztna','ha_cluster'].map(t => `<option value="${t}" style="background:#0d1a2e;color:#e2e8f0;">${t}</option>`).join('')}
      </select>
    </div>
    <div style="margin-bottom:18px;">
      <div style="font-size:10px;color:rgba(255,255,255,0.4);letter-spacing:1px;margin-bottom:5px;">LABEL (optional)</div>
      <input id="_blm2_label" type="text" placeholder="e.g. IPMI, Mgmt…"
        style="width:100%;background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:6px 8px;font-size:12px;border-radius:4px;box-sizing:border-box;"/>
    </div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button id="_blm2_no" style="padding:7px 18px;border-radius:6px;border:1px solid #2a3448;background:transparent;color:#8899aa;cursor:pointer;font-size:12px;">Cancel</button>
      <button id="_blm2_yes" style="padding:7px 18px;border-radius:6px;border:none;background:var(--accent,#00d4ff);color:#000;cursor:pointer;font-weight:600;font-size:12px;">ADD ${selectedDids.length} LINKS</button>
    </div>
  </div>`;
  document.body.appendChild(ov);

  const close = () => document.body.removeChild(ov);
  ov.querySelector('#_blm2_no').onclick = close;
  ov.querySelector('#_blm2_yes').onclick = () => {
    const linkType = ov.querySelector('#_blm2_type').value;
    const label    = ov.querySelector('#_blm2_label').value.trim();
    close();
    const created = _createBulkPwLinks(selectedDids, tgtDid, linkType, label);
    multiSelect.clear(); renderNodes(); deselect();
    toast(created > 0 ? `${created} link(s) created` : 'All links already exist');
  };

  setTimeout(() => ov.querySelector('#_blm2_label').focus(), 50);
}


// ═══════════════════════════ VIEWPORT ═══════════════════════════
function applyViewport() {
  document.getElementById('viewport')
    .setAttribute('transform', `translate(${vp.tx},${vp.ty}) scale(${vp.scale})`);
  const hud = document.getElementById('zoom-hud');
  if (hud) hud.textContent = Math.round(vp.scale * 100) + '%';
}

function fitToView() {
  if (!nodes.length && !groups.length) return;
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  nodes.forEach(n => {
    const s = nsize(n.type, n);
    minX=Math.min(minX,n.x); minY=Math.min(minY,n.y);
    maxX=Math.max(maxX,n.x+s.w); maxY=Math.max(maxY,n.y+s.h);
  });
  groups.forEach(g => {
    minX=Math.min(minX,g.x); minY=Math.min(minY,g.y);
    maxX=Math.max(maxX,g.x+g.w); maxY=Math.max(maxY,g.y+g.h);
  });
  const wrap = document.getElementById('canvas-wrap');
  const cw = wrap.clientWidth || 800, ch = wrap.clientHeight || 600;
  const pad = 80;
  const scaleX = (cw - pad*2) / (maxX - minX || 1);
  const scaleY = (ch - pad*2) / (maxY - minY || 1);
  vp.scale = Math.min(2, Math.max(0.1, Math.min(scaleX, scaleY)));
  vp.tx = pad + (cw - pad*2 - (maxX-minX)*vp.scale) / 2 - minX * vp.scale;
  vp.ty = pad + (ch - pad*2 - (maxY-minY)*vp.scale) / 2 - minY * vp.scale;
  applyViewport();
}

// Wheel zoom on canvas-wrap
document.getElementById('canvas-wrap').addEventListener('wheel', e => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.1 : 0.909;
  const rect = document.getElementById('canvas-wrap').getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  vp.tx = mx - (mx - vp.tx) * factor;
  vp.ty = my - (my - vp.ty) * factor;
  vp.scale = Math.min(4, Math.max(0.1, vp.scale * factor));
  applyViewport();
}, { passive: false });

// Middle-mouse pan
function doMMPan(e) {
  if (!mmPan) return;
  vp.tx = mmPan.tx0 + (e.clientX - mmPan.sx);
  vp.ty = mmPan.ty0 + (e.clientY - mmPan.sy);
  applyViewport();
}
function endMMPan(e) {
  if (!mmPan) return;
  mmPan = null;
  svg.style.cursor = 'default';
}

// ═══════════════════════════ RUBBER-BAND SELECT ═══════════════════════════
function doRubberBand(e) {
  if (!rubberBand) return;
  const pt = getSVGPt(e);
  rubberBand.x1 = pt.x; rubberBand.y1 = pt.y;
  const rect = document.getElementById('rubber-band');
  if (!rect) return;
  const x = Math.min(rubberBand.x0, rubberBand.x1);
  const y = Math.min(rubberBand.y0, rubberBand.y1);
  const w = Math.abs(rubberBand.x1 - rubberBand.x0);
  const h = Math.abs(rubberBand.y1 - rubberBand.y0);
  rect.setAttribute('x', x); rect.setAttribute('y', y);
  rect.setAttribute('width', w); rect.setAttribute('height', h);
}
function endRubberBand(e) {
  if (!rubberBand) return;
  document.getElementById('rubber-band')?.remove();
  const x0 = Math.min(rubberBand.x0, rubberBand.x1);
  const y0 = Math.min(rubberBand.y0, rubberBand.y1);
  const x1 = Math.max(rubberBand.x0, rubberBand.x1);
  const y1 = Math.max(rubberBand.y0, rubberBand.y1);
  rubberBand = null;
  if (x1 - x0 < 5 && y1 - y0 < 5) return; // too small = accidental click
  nodes.forEach(n => {
    const s = nsize(n.type, n);
    const nx0=n.x, ny0=n.y, nx1=n.x+s.w, ny1=n.y+s.h;
    if (nx1 > x0 && nx0 < x1 && ny1 > y0 && ny0 < y1) multiSelect.add(n.id);
  });
  renderNodes();
  if (multiSelect.size > 0) showMultiPanel();
}

// ═══════════════════════════ GROUPS ═══════════════════════════
function renderGroups() {
  const layer = document.getElementById('groups-layer');
  if (!layer) return;
  layer.innerHTML = '';
  groups.forEach(g => {
    const gg = document.createElementNS('http://www.w3.org/2000/svg','g');
    gg.setAttribute('class','group-g');
    gg.setAttribute('id','group-'+g.id);

    const rect = document.createElementNS('http://www.w3.org/2000/svg','rect');
    rect.setAttribute('rx','6');
    rect.setAttribute('fill', g.color + '26');
    rect.setAttribute('stroke', g.color);
    rect.setAttribute('stroke-width','1.5');
    rect.setAttribute('stroke-dasharray','6,4');
    gg.appendChild(rect);

    const lbl = document.createElementNS('http://www.w3.org/2000/svg','text');
    lbl.setAttribute('fill', g.color);
    lbl.setAttribute('font-size','11');
    lbl.setAttribute('font-family',"'Share Tech Mono',monospace");
    lbl.setAttribute('font-weight','bold');
    lbl.textContent = g.name;
    gg.appendChild(lbl);

    const handle = document.createElementNS('http://www.w3.org/2000/svg','rect');
    handle.setAttribute('class','group-resize-handle');
    handle.setAttribute('width','12'); handle.setAttribute('height','12');
    handle.setAttribute('fill', g.color); handle.setAttribute('opacity','0.6');
    handle.setAttribute('rx','2');
    gg.appendChild(handle);

    updateGroupAttrs(gg, g); // set x/y/w/h on all children

    gg.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      e.stopPropagation(); e.preventDefault();
      const pt = getSVGPt(e);
      const isResize = e.target === handle;
      // Find nodes whose centre sits inside this group at drag-start
      const contained = isResize ? [] : Object.values(nodeMap).filter(n =>
        n.x >= g.x && n.x <= g.x + g.w && n.y >= g.y && n.y <= g.y + g.h
      ).map(n => ({ node: n, x0: n.x, y0: n.y }));
      groupDrag = {
        group: g, isResize,
        sx: pt.x, sy: pt.y,
        ox: g.x, oy: g.y, ow: g.w, oh: g.h,
        contained
      };
    });
    gg.addEventListener('click', e => {
      e.stopPropagation();
      selectedEl = { type:'group', data:g };
      showGroupPanel(g);
    });
    layer.appendChild(gg);
  });
}

function updateGroupAttrs(gg, g) {
  const [rect, lbl, handle] = gg.children;
  rect.setAttribute('x', g.x);   rect.setAttribute('y', g.y);
  rect.setAttribute('width', g.w); rect.setAttribute('height', g.h);
  lbl.setAttribute('x', g.x + 10); lbl.setAttribute('y', g.y + 18);
  handle.setAttribute('x', g.x + g.w - 12); handle.setAttribute('y', g.y + g.h - 12);
}

function doGroupDrag(e) {
  if (!groupDrag) return;
  const pt = getSVGPt(e);
  const g = groupDrag.group;
  const dx = pt.x - groupDrag.sx, dy = pt.y - groupDrag.sy;
  if (groupDrag.isResize) {
    g.w = Math.max(80, groupDrag.ow + dx);
    g.h = Math.max(50, groupDrag.oh + dy);
  } else {
    g.x = groupDrag.ox + dx;
    g.y = groupDrag.oy + dy;
    // Move contained nodes with the group
    for (const { node, x0, y0 } of groupDrag.contained) {
      node.x = x0 + dx;
      node.y = y0 + dy;
      const t = `translate(${node.x},${node.y})`;
      document.getElementById('node-' + node.id)?.setAttribute('transform', t);
      document.getElementById('node-label-' + node.id)?.setAttribute('transform', t);
    }
    if (groupDrag.contained.length) renderLinks();
  }
  const gg = document.getElementById('group-' + g.id);
  if (gg) updateGroupAttrs(gg, g);
}

function endGroupDrag(e) {
  if (!groupDrag) return;
  const g = groupDrag.group;
  const contained = groupDrag.contained;
  groupDrag = null;
  if (isPingWatchPage && String(g.id).startsWith('pw_g_')) {
    // Save group position override by name (stable key)
    pwGroupOverrides[g.name] = { ...(pwGroupOverrides[g.name] || {}), x: g.x, y: g.y, w: g.w, h: g.h };
    _pwSave('pw_group_overrides', pwGroupOverrides);
    // Save moved nodes too
    for (const { node } of contained) {
      if (node._pwDid) {
        pwOverrides[node._pwDid] = { ...(pwOverrides[node._pwDid] || {}), x: node.x, y: node.y };
      }
    }
    if (contained.length) _pwSave('pw_node_overrides', pwOverrides);
    if (_pwRenderPending) { _pwRenderPending = false; renderPingWatchCanvas(); }
    return;
  }
  api('PUT', `/api/groups/${g.id}`, { x:g.x, y:g.y, w:g.w, h:g.h }).catch(()=>{});
  // Persist moved nodes
  for (const { node } of contained) {
    api('PUT', `/api/nodes/${node.id}`, { x: node.x, y: node.y }).catch(()=>{});
  }
}

function openAddGroup() {
  editingGroupId = null;
  document.getElementById('modal-group-title').textContent = 'ADD GROUP';
  document.getElementById('group-save-btn').textContent = 'ADD GROUP';
  document.getElementById('group-name').value = '';
  document.getElementById('group-color').value = '#00d4ff';
  openModal('modal-group');
}

async function saveGroup() {
  const name = document.getElementById('group-name').value.trim();
  if (!name) { toast('Group name required'); return; }
  const color = document.getElementById('group-color').value;
  try {
    if (editingGroupId) {
      const ex = groupMap[editingGroupId];
      await api('PUT', `/api/groups/${editingGroupId}`, { name, color, x:ex.x, y:ex.y, w:ex.w, h:ex.h });
      toast('Group updated');
    } else {
      const wrap = document.getElementById('canvas-wrap');
      const cx = ((wrap.clientWidth || 800) / 2 - vp.tx) / vp.scale;
      const cy = ((wrap.clientHeight || 600) / 2 - vp.ty) / vp.scale;
      await api('POST', '/api/groups', { name, color, x: cx - 150, y: cy - 100, w: 300, h: 200, page_id: currentPageId });
      toast('Group added');
    }
    closeModal('modal-group');
    editingGroupId = null;
    await loadData();
  } catch (e) { toast('⚠ ' + e.message); }
}

async function deleteGroupById(id) {
  _confirm('Delete this group zone?', async () => {
  try {
    await api('DELETE', `/api/groups/${id}`);
    selectedEl = null;
    await loadData();
    deselect();
    toast('Group deleted');
  } catch (e) { toast('⚠ ' + e.message); }
  });
}

function showGroupPanel(g) {
  if (!g) return;
  const isPwGroup = String(g.id).startsWith('pw_g_');
  selectedEl = { type: 'group', data: g };
  if (isPwGroup) _selectedPwDid = null;
  document.getElementById('panel-title').textContent = g.name.toUpperCase();
  document.getElementById('panel-icon').textContent = '◧';
  const curColor = isPwGroup ? (pwGroupOverrides[g.name]?.color || g.color) : g.color;
  // Escape name for safe embedding inside a JS string in an HTML attribute
  const jsGname = g.name.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  document.getElementById('panel-body').innerHTML = `
    <div class="field-group"><div class="field-label">NAME</div><span style="color:var(--accent);font-family:'Share Tech Mono',monospace;font-size:11px;">${escXml(g.name)}</span></div>
    <div class="field-group"><div class="field-label">COLOR</div><span style="color:${escXml(curColor)};font-family:'Share Tech Mono',monospace;font-size:11px;">&#9632; ${escXml(curColor)}</span></div>
    <div class="field-group"><div class="field-label">SIZE</div><span style="color:rgba(255,255,255,0.5);font-family:'Share Tech Mono',monospace;font-size:10px;">${Math.round(g.w)} × ${Math.round(g.h)}</span></div>
    ${isPwGroup ? `
    <div class="field-group" style="margin-top:12px">
      <div class="field-label">COLOR OVERRIDE</div>
      <div style="display:flex;align-items:center;gap:8px;">
        <input type="color" value="${curColor}"
               onchange="setPwGroupColor('${jsGname}',this.value)"
               style="width:36px;height:24px;cursor:pointer;border:none;background:none;padding:0"/>
        ${pwGroupOverrides[g.name]?.color
          ? `<button class="btn" style="font-size:10px;padding:2px 8px" onclick="resetPwGroupColor('${jsGname}')">Reset</button>`
          : `<span style="color:rgba(255,255,255,0.3);font-size:10px;font-family:'Share Tech Mono',monospace">auto</span>`}
      </div>
    </div>` : ''}
  `;
  document.getElementById('panel-actions').innerHTML = isPwGroup ? '' : `
    <button class="btn btn-primary" style="flex:1" onclick="editGroupOpen('${g.id}')">✎ EDIT</button>
    <button class="btn btn-danger" style="flex:1" onclick="deleteGroupById('${g.id}')">✕ DELETE</button>
  `;
}

function editGroupOpen(id) {
  const g = groupMap[id]; if (!g) return;
  editingGroupId = id;
  document.getElementById('modal-group-title').textContent = 'EDIT GROUP';
  document.getElementById('group-save-btn').textContent = 'SAVE GROUP';
  document.getElementById('group-name').value = g.name;
  document.getElementById('group-color').value = g.color;
  openModal('modal-group');
}

// ═══════════════════════════ PINGWATCH TAB OVERRIDES ═══════════════════════════
function setPwNodeType(did, newType) {
  const sid = String(did);
  pwOverrides[sid] = { ...(pwOverrides[sid] || {}), node_type: newType };
  _pwSave('pw_node_overrides', pwOverrides);
  const node = nodes.find(n => String(n._pwDid) === sid);
  if (node) node.type = newType;
  renderPingWatchCanvas();
}

function setPwNodeColor(did, color) {
  const sid = String(did);
  pwOverrides[sid] = { ...(pwOverrides[sid] || {}), color };
  _pwSave('pw_node_overrides', pwOverrides);
  renderPingWatchCanvas();
  if (String(_selectedPwDid) === sid) showPwNodePanel(sid);
}
function resetPwNodeColor(did) {
  const sid = String(did);
  if (pwOverrides[sid]) {
    delete pwOverrides[sid].color;
    if (!Object.keys(pwOverrides[sid]).length) delete pwOverrides[sid];
  }
  _pwSave('pw_node_overrides', pwOverrides);
  renderPingWatchCanvas();
  if (String(_selectedPwDid) === sid) showPwNodePanel(sid);
}

function resetPwLayout() {
  pwOverrides = {};
  pwGroupOverrides = {};
  selectedEl = null;
  _selectedPwDid = null;
  _pwSave('pw_node_overrides', {});
  _pwSave('pw_group_overrides', {});
  renderPingWatchCanvas();
  fitToView();
  toast('Layout reset to auto');
}

function setPwGroupColor(gname, color) {
  pwGroupOverrides[gname] = { ...(pwGroupOverrides[gname] || {}), color };
  _pwSave('pw_group_overrides', pwGroupOverrides);
  renderPingWatchCanvas();
  // Re-show panel with updated color
  const g = groups.find(x => x.name === gname);
  if (g) showGroupPanel(g);
}

function resetPwGroupColor(gname) {
  if (pwGroupOverrides[gname]) {
    delete pwGroupOverrides[gname].color;
    if (!Object.keys(pwGroupOverrides[gname]).length) delete pwGroupOverrides[gname];
  }
  _pwSave('pw_group_overrides', pwGroupOverrides);
  renderPingWatchCanvas();
  const g = groups.find(x => x.name === gname);
  if (g) showGroupPanel(g);
}

function showPwLinkPanel(lkId) {
  const lk = pwLinks.find(l => l.id === lkId);
  if (!lk) return;
  selectedEl = { type: 'pwlink', data: lk };
  _selectedPwDid = null;
  document.getElementById('panel-title').textContent = 'PW LINK';
  document.getElementById('panel-icon').textContent = '⟷';
  document.getElementById('panel-body').innerHTML = `
    <div class="field-group"><div class="field-label">FROM</div><span style="color:var(--accent);font-family:'Share Tech Mono',monospace;font-size:11px;">${escXml(_pwDevName(lk.src_did))}</span></div>
    <div class="field-group"><div class="field-label">TO</div><span style="color:var(--accent);font-family:'Share Tech Mono',monospace;font-size:11px;">${escXml(_pwDevName(lk.tgt_did))}</span></div>
    <div class="field-group"><div class="field-label">TYPE</div>
      <select onchange="setPwLinkType('${lkId}',this.value)" style="background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:4px 8px;font-size:11px;border-radius:3px;">
        ${['trunk','access','internet','ztna','ha_cluster'].map(t=>`<option value="${t}" style="background:#0d1a2e;color:#e2e8f0;"${lk.link_type===t?' selected':''}>${t}</option>`).join('')}
      </select>
    </div>
    <div class="field-group"><div class="field-label">LABEL</div>
      <input type="text" value="${escXml(lk.label||'')}" placeholder="optional label"
        onchange="setPwLinkLabel('${lkId}',this.value)"
        style="background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:4px 8px;font-size:11px;border-radius:3px;width:100%;box-sizing:border-box;"/>
    </div>
    <div class="field-group"><div class="field-label">IN TRAFFIC</div>
      <select id="pw-lk-si" style="background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:4px 8px;font-size:11px;border-radius:3px;width:100%;">
        ${_pwSensorOpts(lk, 'sensor_in')}
      </select>
    </div>
    <div class="field-group"><div class="field-label">OUT TRAFFIC</div>
      <select id="pw-lk-so" style="background:#0d1a2e;border:1px solid rgba(255,255,255,0.15);color:#e2e8f0;padding:4px 8px;font-size:11px;border-radius:3px;width:100%;">
        ${_pwSensorOpts(lk, 'sensor_out')}
      </select>
    </div>
  `;
  document.getElementById('panel-actions').innerHTML = `
    <button class="btn btn-danger" style="flex:1" onclick="deletePwLink('${lkId}')">✕ DELETE</button>
    <button class="btn btn-primary" style="flex:1" onclick="setPwLinkSensors('${lkId}')">💾 SAVE SENSORS</button>
  `;
}
function deletePwLink(lkId) {
  pwLinks = pwLinks.filter(l => l.id !== lkId);
  _pwSave('pw_links', pwLinks);
  renderLinks();
  deselect();
}
function setPwLinkType(lkId, type) {
  const lk = pwLinks.find(l => l.id === lkId);
  if (!lk) return;
  lk.link_type = type;
  _pwSave('pw_links', pwLinks);
  renderLinks();
}
function setPwLinkLabel(lkId, label) {
  const lk = pwLinks.find(l => l.id === lkId);
  if (!lk) return;
  lk.label = label;
  _pwSave('pw_links', pwLinks);
  renderLinks();
}
function _pwSensorOpts(lk, field) {
  const dids = [lk.src_did, lk.tgt_did].filter(Boolean);
  const sensors = dids.flatMap(did => (_pwDevMap[did]?.sensors || []).filter(s => s.stype === 'snmp'));
  const cur = lk[field] || '';
  const opts = [['', '— None —'], ...sensors.map(s => [`${s.device_id}/${s.sensor_id}`, s.name])];
  return opts.map(([v, l]) => `<option value="${v}"${v===cur?' selected':''}>${escXml(l)}</option>`).join('');
}
function setPwLinkSensors(lkId) {
  const lk = pwLinks.find(l => l.id === lkId);
  if (!lk) return;
  lk.sensor_in  = document.getElementById('pw-lk-si')?.value || '';
  lk.sensor_out = document.getElementById('pw-lk-so')?.value || '';
  _pwSave('pw_links', pwLinks);
  // Re-color immediately with current threshold state
  const lineEl = document.querySelector(`[data-pwlid="${CSS.escape(lkId)}"] .link-main`);
  if (lineEl) _pwApplyLinkEl(lineEl, lk, _pwDevMap[lk.src_did], _pwDevMap[lk.tgt_did]);
  toast('Sensors saved');
}

// ═══════════════════════════ SEARCH / FILTER ═══════════════════════════
function filterNodes(query) {
  const q = (query || '').trim().toLowerCase();
  nodes.forEach(n => {
    const el = document.getElementById('node-' + n.id);
    if (!el) return;
    const match = !q
      || n.name.toLowerCase().includes(q)
      || (n.properties?.ip || '').toLowerCase().includes(q)
      || (n.type || '').toLowerCase().includes(q)
      || Object.values(n.properties || {}).some(v => String(v).toLowerCase().includes(q));
    el.classList.toggle('node-dimmed', !match);
  });
}
document.getElementById('search-input')?.addEventListener('input', e => filterNodes(e.target.value));

// ═══════════════════════════ DUPLICATE ═══════════════════════════
async function duplicateNode(id) {
  const src = nodeMap[id]; if (!src) return;
  try {
    const r = await api('POST', '/api/nodes', {
      name: src.name + ' (copy)', type: src.type,
      x: src.x + 40, y: src.y + 40,
      properties: { ...src.properties }, page_id: currentPageId
    });
    pushAction(
      async () => { await api('DELETE', `/api/nodes/${r.id}`); await loadData(); },
      async () => { await api('POST', '/api/nodes', { name:src.name+' (copy)', type:src.type, x:src.x+40, y:src.y+40, properties:{...src.properties}, page_id:currentPageId }); await loadData(); }
    );
    await loadData();
    toast('Device duplicated');
  } catch (e) { toast('⚠ ' + e.message); }
}

// ═══════════════════════════ UNDO / REDO ═══════════════════════════
function pushAction(undoFn, redoFn) {
  undoStack.push({ undo: undoFn, redo: redoFn });
  if (undoStack.length > 50) undoStack.shift();
  redoStack.length = 0;
}
async function doUndo() {
  const action = undoStack.pop();
  if (!action) { toast('Nothing to undo'); return; }
  try { await action.undo(); if (action.redo) redoStack.push(action); }
  catch (e) { toast('⚠ Undo failed: ' + e.message); }
}
async function doRedo() {
  const action = redoStack.pop();
  if (!action) { toast('Nothing to redo'); return; }
  try { await action.redo(); undoStack.push(action); }
  catch (e) { toast('⚠ Redo failed: ' + e.message); }
}

// ═══════════════════════════ EXPORT / IMPORT ═══════════════════════════
function exportJSON() {
  const data = JSON.stringify({ nodes, links, groups }, null, 2);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([data], { type: 'application/json' }));
  a.download = 'topology.json';
  a.click();
  toast('Topology exported');
}

function _exportBuildClone(scale) {
  if (!nodes.length && !groups.length) return null;
  let x0=Infinity, y0=Infinity, x1=-Infinity, y1=-Infinity;
  nodes.forEach(n => { const s=nsize(n.type,n); x0=Math.min(x0,n.x); y0=Math.min(y0,n.y); x1=Math.max(x1,n.x+s.w); y1=Math.max(y1,n.y+s.h); });
  groups.forEach(g => { x0=Math.min(x0,g.x); y0=Math.min(y0,g.y); x1=Math.max(x1,g.x+g.w); y1=Math.max(y1,g.y+g.h); });
  const PAD=40, vw=x1-x0+PAD*2, vh=y1-y0+PAD*2;
  const clone = document.getElementById('topo-svg').cloneNode(true);
  clone.setAttribute('xmlns','http://www.w3.org/2000/svg');
  clone.setAttribute('viewBox',`${x0-PAD} ${y0-PAD} ${vw} ${vh}`);
  clone.setAttribute('width', vw*scale);
  clone.setAttribute('height', vh*scale);
  const vport = clone.querySelector('#viewport');
  if (vport) vport.setAttribute('transform','');
  const bg = document.createElementNS('http://www.w3.org/2000/svg','rect');
  bg.setAttribute('x',x0-PAD); bg.setAttribute('y',y0-PAD);
  bg.setAttribute('width',vw); bg.setAttribute('height',vh);
  bg.setAttribute('fill','#070f17');
  clone.insertBefore(bg, clone.firstChild);
  const cssText = Array.from(document.styleSheets).flatMap(s => { try { return Array.from(s.cssRules).map(r=>r.cssText); } catch { return []; } }).join('\n');
  const styleEl = document.createElementNS('http://www.w3.org/2000/svg','style');
  styleEl.textContent = cssText;
  clone.insertBefore(styleEl, clone.firstChild);
  return { clone, vw, vh };
}

function exportSVG() {
  const res = _exportBuildClone(1);
  if (!res) { toast('Nothing to export'); return; }
  const xml = new XMLSerializer().serializeToString(res.clone);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([xml],{type:'image/svg+xml'}));
  a.download = 'topology.svg'; a.click();
  toast('SVG exported');
}

async function _inlineFontsForExport() {
  // Inline self-hosted fonts into the exported SVG/PNG so labels render
  // correctly when the file is opened outside the app. Fetched from /fonts/
  // — no external CDN dependency, works fully offline.
  const FONTS = [
    { family: 'Orbitron',        weight: 400, file: 'orbitron-v35-latin-regular.woff2'        },
    { family: 'Orbitron',        weight: 700, file: 'orbitron-v35-latin-700.woff2'            },
    { family: 'Orbitron',        weight: 900, file: 'orbitron-v35-latin-900.woff2'            },
    { family: 'Share Tech Mono', weight: 400, file: 'share-tech-mono-v16-latin-regular.woff2' },
  ];
  const parts = [];
  for (const f of FONTS) {
    try {
      const blob = await fetch('/fonts/' + f.file).then(r => r.blob());
      const b64 = await new Promise(res => {
        const rd = new FileReader();
        rd.onload = () => res(rd.result);
        rd.readAsDataURL(blob);
      });
      parts.push(
        `@font-face{font-family:'${f.family}';font-style:normal;` +
        `font-weight:${f.weight};src:url(${b64}) format('woff2');}`
      );
    } catch {}
  }
  return parts.join('\n');
}

async function exportPNG() {
  // SCALE=2 → 4× pixel area of the SVG viewport (crisp quality without
  // risking browser canvas size limits that SCALE=4 hits on large topologies)
  const SCALE = 2;
  const res = _exportBuildClone(SCALE);
  if (!res) { toast('Nothing to export'); return; }
  toast('Preparing PNG…');

  // Try to inline fonts (times out in 4 s if offline — PNG still renders
  // with system font fallbacks in that case)
  const fontCSS = await _inlineFontsForExport();
  if (fontCSS) {
    const styleEl = res.clone.querySelector('style');
    if (styleEl) styleEl.textContent = fontCSS + '\n' + styleEl.textContent;
  }

  const xml = new XMLSerializer().serializeToString(res.clone);
  const url = URL.createObjectURL(new Blob([xml], { type: 'image/svg+xml' }));

  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    try {
      const canvas = document.createElement('canvas');
      canvas.width  = Math.round(res.vw * SCALE);
      canvas.height = Math.round(res.vh * SCALE);
      const ctx = canvas.getContext('2d');
      if (!ctx) { URL.revokeObjectURL(url); toast('PNG export failed'); return; }
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, 0, 0);
      URL.revokeObjectURL(url);
      canvas.toBlob(b => {
        if (!b) { toast('PNG export failed'); return; }
        const a = document.createElement('a');
        a.href = URL.createObjectURL(b);
        a.download = 'topology.png';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
        toast('PNG exported');
      }, 'image/png');
    } catch (_e) {
      URL.revokeObjectURL(url);
      console.error('PNG export error:', _e);
      toast('PNG export failed');
    }
  };
  img.onerror = (e) => {
    URL.revokeObjectURL(url);
    console.error('PNG export: SVG failed to load as image', e);
    toast('PNG export failed');
  };
  img.src = url;
}

function toggleExportMenu(e) {
  e.stopPropagation();
  const m = document.getElementById('export-menu');
  m.style.display = m.style.display === 'none' ? 'block' : 'none';
}
function closeExportMenu() {
  const m = document.getElementById('export-menu');
  if (m) m.style.display = 'none';
}
document.addEventListener('click', () => closeExportMenu());

async function importJSON(file) {
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    if (!Array.isArray(data.nodes) || !Array.isArray(data.links)) { toast('⚠ Invalid topology file'); return; }
    _confirm(`Import <b>${data.nodes.length}</b> devices and <b>${data.links.length}</b> links?<br><br>This will replace the current topology.`, async () => {
      try {
        for (const n of nodes) await api('DELETE', `/api/nodes/${n.id}`).catch(()=>{});
        const idMap = {};
        for (const n of data.nodes) {
          const r = await api('POST', '/api/nodes', { name:n.name, type:n.type, x:n.x, y:n.y, properties:n.properties, page_id:currentPageId });
          idMap[n.id] = r.id;
        }
        for (const l of data.links) {
          const src = idMap[l.source_id], tgt = idMap[l.target_id];
          if (src && tgt) await api('POST', '/api/links', { source_id:src, target_id:tgt, label:l.label, link_type:l.link_type, page_id:currentPageId });
        }
        if (Array.isArray(data.groups)) {
          for (const g of groups) await api('DELETE', `/api/groups/${g.id}`).catch(()=>{});
          for (const g of data.groups) await api('POST', '/api/groups', { name:g.name, color:g.color, x:g.x, y:g.y, w:g.w, h:g.h, page_id:currentPageId });
        }
        undoStack.length = 0; redoStack.length = 0;
        await loadData();
        fitToView();
        toast('Topology imported');
      } catch (e) { toast('⚠ Import failed: ' + e.message); }
    }, 'Yes, Import');
  } catch (e) { toast('⚠ Import failed: ' + e.message); }
}
document.getElementById('import-file')?.addEventListener('change', e => {
  const file = e.target.files?.[0];
  if (file) { importJSON(file); e.target.value = ''; }
});

document.getElementById('pw-layout-import-file')?.addEventListener('change', e => {
  const file = e.target.files?.[0];
  if (file) { importPwLayout(file); e.target.value = ''; }
});

function exportPwLayout() {
  const data = JSON.stringify({ pwOverrides, pwGroupOverrides, pwLinks }, null, 2);
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([data], { type: 'application/json' }));
  a.download = 'pw_layout.json';
  a.click();
  toast('Layout exported');
}

async function importPwLayout(file) {
  try {
    const data = JSON.parse(await file.text());
    if (typeof data.pwOverrides !== 'object' || typeof data.pwGroupOverrides !== 'object' || !Array.isArray(data.pwLinks)) {
      toast('⚠ Invalid layout file'); return;
    }
    const nNodes  = Object.keys(data.pwOverrides).length;
    const nGroups = Object.keys(data.pwGroupOverrides).length;
    const nLinks  = data.pwLinks.length;
    _confirm(
      `Import layout?<br><br><span style="font-family:Share Tech Mono,monospace;font-size:11px;color:rgba(0,212,255,0.7)">${nNodes} node overrides · ${nGroups} groups · ${nLinks} links</span><br><br>This will replace the current PingWatch layout.`,
      () => {
        pwOverrides      = data.pwOverrides;
        pwGroupOverrides = data.pwGroupOverrides;
        pwLinks          = data.pwLinks;
        _pwSave('pw_node_overrides',  pwOverrides);
        _pwSave('pw_group_overrides', pwGroupOverrides);
        _pwSave('pw_links',           pwLinks);
        renderPingWatchCanvas();
        fitToView();
        toast('Layout imported');
      },
      'Yes, Import'
    );
  } catch (e) { toast('⚠ Import failed: ' + e.message); }
}

// ═══════════════════════════ MULTI-SELECT ═══════════════════════════
function showMultiPanel() {
  document.getElementById('panel-title').textContent = 'MULTI-SELECT';
  document.getElementById('panel-icon').textContent = '◈';

  if (isPingWatchPage) {
    // Gather selected PW devices
    const selNodes = [...multiSelect].map(nid => nodeMap[nid]).filter(n => n && n._pwDid);
    const selDevs  = selNodes.map(n => pwDevices.find(d => d.device_id === n._pwDid)).filter(Boolean);

    const upCount   = selDevs.filter(d => d.status === 'up').length;
    const downCount = selDevs.filter(d => d.status === 'down').length;
    const unkCount  = selDevs.length - upCount - downCount;

    // Per-device summary rows (capped at 12 to avoid overflow)
    const devRows = selDevs.slice(0, 12).map(d => {
      const c = pwStatusColor(d.status);
      const dot = `<span style="color:${c};font-size:9px">●</span>`;
      return `<div style="display:flex;align-items:center;gap:5px;padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.04)">
        ${dot}
        <span style="font-family:'Share Tech Mono',monospace;font-size:9px;color:rgba(255,255,255,0.75);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escXml(d.name)}</span>
        <span style="font-family:'Share Tech Mono',monospace;font-size:8px;color:rgba(255,255,255,0.35)">${escXml(d.host)}</span>
      </div>`;
    }).join('');
    const moreRow = selDevs.length > 12
      ? `<div style="font-size:9px;color:rgba(255,255,255,0.3);font-family:'Share Tech Mono',monospace;padding-top:3px">+${selDevs.length - 12} more…</div>`
      : '';

    const _typeOpts = [
      ['','— keep current —'],
      ['switch','Switch'],['bb-switch','Backbone Switch'],['firewall','Firewall'],
      ['wan-switch','WAN Switch'],['server','Server'],['pc','PC / Workstation'],
      ['laptop','Laptop'],['ap','WiFi Access Point'],['connector','Cato Connector'],
      ['remote-pc','Remote PC'],['cloud','Cloud / Internet'],
      ['router','Router / Gateway'],['vm','Virtual Machine'],['appliance','Network Appliance'],
    ['storage','Storage / NAS'],['phone','IP Phone / VoIP'],['camera','IP Camera / CCTV'],
    ['printer','Printer / MFP'],['load-balancer','Load Balancer'],['hypervisor','Hypervisor / ESXi'],
    ['ups','UPS / PDU'],['container','Container Host'],['ipmi','IPMI / BMC'],
    ].map(([v,l])=>`<option value="${v}">${l}</option>`).join('');

    document.getElementById('panel-body').innerHTML = `
      <div class="panel-section">
        <div class="panel-section-title">SELECTION — ${selDevs.length} DEVICES</div>
        <div style="display:flex;gap:10px;margin-bottom:8px">
          <span style="font-size:9px;font-family:'Share Tech Mono',monospace;color:#00ff9d">▲ ${upCount} UP</span>
          ${downCount ? `<span style="font-size:9px;font-family:'Share Tech Mono',monospace;color:#ff3333">▼ ${downCount} DOWN</span>` : ''}
          ${unkCount  ? `<span style="font-size:9px;font-family:'Share Tech Mono',monospace;color:#888">? ${unkCount}</span>` : ''}
        </div>
        <div style="max-height:160px;overflow-y:auto;">${devRows}${moreRow}</div>
      </div>
      <div class="panel-section" style="margin-top:10px">
        <div class="panel-section-title">BULK SETTINGS</div>
        <div class="field-group">
          <div class="field-label">DEVICE ICON</div>
          <select id="multi-icon-sel"
            style="background:#0d1a2e;color:#e2e8f0;border:1px solid rgba(0,212,255,0.3);border-radius:4px;padding:4px 6px;font-family:'Share Tech Mono',monospace;font-size:10px;width:100%;cursor:pointer"
            onchange="setBulkPwNodeType(this.value)">${_typeOpts}</select>
        </div>
        <div class="field-group" style="margin-top:8px">
          <div class="field-label">COLOR OVERRIDE</div>
          <div style="display:flex;align-items:center;gap:8px;">
            <input type="color" id="multi-color-pick" value="#00d4ff"
                   onchange="setBulkPwNodeColor(this.value)"
                   style="width:36px;height:24px;cursor:pointer;border:none;background:none;padding:0"/>
            <button class="btn" style="font-size:10px;padding:2px 8px" onclick="resetBulkPwNodeColor()">Reset all</button>
          </div>
        </div>
      </div>
    `;
    document.getElementById('panel-actions').innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;width:100%;">
        <button class="btn" style="width:100%;border-color:var(--gold);color:var(--gold)" onclick="openBulkLinkModal()">⟷ LINK ALL TO… (${multiSelect.size})</button>
        <button class="btn btn-primary" style="width:100%" onclick="multiSelect.clear();renderNodes();deselect()">CLEAR SELECTION</button>
      </div>
    `;
  } else {
    document.getElementById('panel-body').innerHTML = `
      <div class="panel-section">
        <div class="panel-section-title">SELECTION</div>
        <div class="info-row"><span class="info-label">DEVICES</span><span class="info-value">${multiSelect.size}</span></div>
      </div>
    `;
    document.getElementById('panel-actions').innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;width:100%;">
        <button class="btn" style="width:100%;border-color:var(--danger);color:var(--danger)" onclick="deleteSelectedNodes()">✕ DELETE SELECTED (${multiSelect.size})</button>
        <button class="btn btn-primary" style="width:100%" onclick="multiSelect.clear();renderNodes();deselect()">CLEAR SELECTION</button>
      </div>
    `;
  }
}

function setBulkPwNodeType(newType) {
  if (!newType) return;  // "— keep current —" selected
  const selNodes = [...multiSelect].map(nid => nodeMap[nid]).filter(n => n && n._pwDid);
  for (const node of selNodes) {
    const sid = String(node._pwDid);
    pwOverrides[sid] = { ...(pwOverrides[sid] || {}), node_type: newType };
    node.type = newType;
  }
  _pwSave('pw_node_overrides', pwOverrides);
  renderPingWatchCanvas();
  showMultiPanel();
}

function setBulkPwNodeColor(color) {
  const selNodes = [...multiSelect].map(nid => nodeMap[nid]).filter(n => n && n._pwDid);
  for (const node of selNodes) {
    const sid = String(node._pwDid);
    pwOverrides[sid] = { ...(pwOverrides[sid] || {}), color };
  }
  _pwSave('pw_node_overrides', pwOverrides);
  renderPingWatchCanvas();
}

function resetBulkPwNodeColor() {
  const selNodes = [...multiSelect].map(nid => nodeMap[nid]).filter(n => n && n._pwDid);
  for (const node of selNodes) {
    const sid = String(node._pwDid);
    if (pwOverrides[sid]) delete pwOverrides[sid].color;
  }
  _pwSave('pw_node_overrides', pwOverrides);
  renderPingWatchCanvas();
  showMultiPanel();
}

async function deleteSelectedNodes() {
  if (multiSelect.size === 0) return;
  _confirm(`Delete <b>${multiSelect.size}</b> selected device(s) and all their links?`, async () => {
    const ids = [...multiSelect];
    const snapNodes = ids.map(id => nodeMap[id]).filter(Boolean)
      .map(n => ({ ...n, properties: { ...n.properties } }));
    const snapLinks = links.filter(l => ids.includes(l.source_id) || ids.includes(l.target_id));
    multiSelect.clear();
    for (const id of ids) await api('DELETE', `/api/nodes/${id}`).catch(()=>{});
    pushAction(
      async () => {
        const idMap = {};
        for (const n of snapNodes) {
          const r = await api('POST', '/api/nodes', { name:n.name, type:n.type, x:n.x, y:n.y, properties:n.properties, page_id:currentPageId });
          idMap[n.id] = r.id;
        }
        for (const l of snapLinks) {
          const src = idMap[l.source_id] ?? l.source_id;
          const tgt = idMap[l.target_id] ?? l.target_id;
          await api('POST', '/api/links', { source_id:src, target_id:tgt, label:l.label, link_type:l.link_type, page_id:currentPageId }).catch(()=>{});
        }
        await loadData();
      }, null
    );
    selectedEl = null;
    await loadData();
    deselect();
    toast(`${ids.length} device(s) deleted`);
  });
}

// ═══════════════════════════ CONTEXT DRAW LINK ═══════════════════════════
function ctxDrawLinkFrom(nodeId) {
  const node = nodeMap[nodeId]; if (!node) return;
  const cn = nodeCenter(node);
  linkDraw = { srcNode: node };
  svg.style.cursor = 'crosshair';
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.id = 'link-draw-temp';
  line.setAttribute('stroke', 'rgba(0,212,255,0.7)');
  line.setAttribute('stroke-width', '2');
  line.setAttribute('stroke-dasharray', '6,4');
  line.setAttribute('x1', cn.x); line.setAttribute('y1', cn.y);
  line.setAttribute('x2', cn.x); line.setAttribute('y2', cn.y);
  document.getElementById('links-layer').appendChild(line);
}



// ═══════════════════════════ NODE COLOR ═══════════════════════════
function getHue(hex) {
  const r = parseInt(hex.slice(1,3),16)/255;
  const g = parseInt(hex.slice(3,5),16)/255;
  const b = parseInt(hex.slice(5,7),16)/255;
  const max = Math.max(r,g,b), min = Math.min(r,g,b);
  if (max === min) return 0;
  let h;
  if (max === r)      h = 60 * ((g - b) / (max - min)) % 360;
  else if (max === g) h = 60 * ((b - r) / (max - min)) + 120;
  else                h = 60 * ((r - g) / (max - min)) + 240;
  return h < 0 ? h + 360 : h;
}
// Node color: grayscale→sepia (hue≈37°)→hue-rotate to target

// ═══════════════════════════ DASHBOARD BG CANVAS ═══════════════════════════
function initDashBg() {
  const canvas = document.getElementById('dash-bg-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let pts = [], scanY = 0;

  // ── Performance: throttle to 15 fps via setTimeout+RAF (not busy-RAF) ────────
  const DB_FPS = 15, DB_MS = 1000 / DB_FPS;
  let _rafId = null;

  function resize() {
    canvas.width  = canvas.offsetWidth  || 300;
    canvas.height = canvas.offsetHeight || 600;
  }

  function spawn() {
    // 16 particles (was 28) — pair checks: 120 vs 378 per frame
    pts = Array.from({ length: 16 }, () => ({
      x:  Math.random() * canvas.width,
      y:  Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.25,
      vy: (Math.random() - 0.5) * 0.25,
      r:  Math.random() * 1.4 + 0.5,
      ph: Math.random() * Math.PI * 2,
    }));
  }

  function frame() {
    // Stop-and-restart: don't re-schedule when paused (saves empty RAF/s)
    // Also skip when dashboard panel canvas is not visible (node/link panel showing)
    if (_bgPaused || !_ntmVisible || !canvas.offsetParent) { _rafId = null; return; }
    _rafId = setTimeout(() => requestAnimationFrame(frame), DB_MS);

    const W = canvas.width, H = canvas.height;
    const t = Date.now() * 0.001;
    ctx.clearRect(0, 0, W, H);

    // Move particles
    pts.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
      if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
    });

    // Connections — single batched path (1 stroke call instead of N)
    const MD2 = 90 * 90;
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(0,212,255,0.12)';
    ctx.lineWidth = 0.6;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
        if (dx*dx + dy*dy < MD2) {
          ctx.moveTo(pts[i].x, pts[i].y);
          ctx.lineTo(pts[j].x, pts[j].y);
        }
      }
    }
    ctx.stroke();

    // Particles
    pts.forEach(p => {
      const glow = 0.45 + 0.4 * Math.sin(t * 1.6 + p.ph);
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(0,212,255,${glow.toFixed(2)})`;
      ctx.fill();
    });

    // Scan bar — simple fillRect, no gradient allocation
    scanY = (scanY + 0.4) % H;
    ctx.fillStyle = 'rgba(0,212,255,0.04)';
    ctx.fillRect(0, scanY - 18, W, 22);
    ctx.fillStyle = 'rgba(0,212,255,0.10)';
    ctx.fillRect(0, scanY, W, 1);
  }

  function start() { if (!_rafId) _rafId = requestAnimationFrame(frame); }

  resize(); spawn(); start();
  _resumeDashBg = start;
  new ResizeObserver(() => { resize(); spawn(); }).observe(canvas.parentElement);
}

// ═══════════════════════════ DASHBOARD PANEL ═══════════════════════════
function showDashboardPanel() {
  document.getElementById('panel-title').textContent = 'NETWORK STATUS';
  document.getElementById('panel-icon').textContent = '◈';

  const nodeTypeCounts = {};
  nodes.forEach(n => { nodeTypeCounts[n.type] = (nodeTypeCounts[n.type] || 0) + 1; });
  const topTypes = Object.entries(nodeTypeCounts).sort((a,b) => b[1]-a[1]).slice(0,5);
  const maxType = topTypes[0]?.[1] || 1;

  const linkTypeCounts = {};
  links.forEach(l => { linkTypeCounts[l.link_type] = (linkTypeCounts[l.link_type] || 0) + 1; });
  const topLinks = Object.entries(linkTypeCounts).sort((a,b) => b[1]-a[1]);
  const maxLink = topLinks[0]?.[1] || 1;

  const LCOLORS = { trunk:'#00ff9d', access:'#00d4ff', ha:'#ff3366', internet:'#ffd700', wifi:'#a855f7', tunnel:'#a855f7' };

  document.getElementById('panel-body').innerHTML = `
    <div style="display:flex;justify-content:center;margin:10px 0 6px;">
      <svg viewBox="0 0 100 100" width="88" height="88" style="overflow:visible">
        <circle cx="50" cy="50" r="44" fill="none" stroke="rgba(0,212,255,0.07)" stroke-width="1"/>
        <circle cx="50" cy="50" r="30" fill="none" stroke="rgba(0,212,255,0.05)" stroke-width="1"/>
        <circle cx="50" cy="50" r="16" fill="none" stroke="rgba(0,212,255,0.09)" stroke-width="1"/>
        <circle cx="50" cy="50" r="2.5" fill="rgba(0,212,255,0.5)"/>
        <g style="transform-origin:50px 50px;animation:radarSpin 3s linear infinite">
          <line x1="50" y1="50" x2="50" y2="6" stroke="rgba(0,212,255,0.55)" stroke-width="1.5"/>
          <circle cx="50" cy="6" r="2" fill="rgba(0,212,255,0.9)"/>
        </g>
        <g style="transform-origin:50px 50px;animation:radarSpin 3s linear infinite;animation-delay:-1s">
          <circle cx="50" cy="6" r="1" fill="rgba(0,212,255,0.25)"/>
        </g>
        ${nodes.slice(0,8).map((n,i) => {
          const a = (i / Math.max(nodes.length,1)) * Math.PI * 2;
          const dist = 20 + (i % 3) * 8;
          const px = (50 + Math.cos(a) * dist).toFixed(1);
          const py = (50 + Math.sin(a) * dist).toFixed(1);
          return `<circle cx="${px}" cy="${py}" r="2" fill="rgba(0,212,255,0.35)"/>`;
        }).join('')}
      </svg>
    </div>
    <div class="dash-stat-grid">
      <div class="dash-stat-card">
        <div class="dash-stat-val" style="color:var(--accent)">${nodes.length}</div>
        <div class="dash-stat-label">DEVICES</div>
      </div>
      <div class="dash-stat-card">
        <div class="dash-stat-val" style="color:var(--accent2)">${links.length}</div>
        <div class="dash-stat-label">LINKS</div>
      </div>
      <div class="dash-stat-card">
        <div class="dash-stat-val" style="color:var(--gold)">${groups.length}</div>
        <div class="dash-stat-label">GROUPS</div>
      </div>
    </div>
    ${topTypes.length ? `
    <div class="dash-section">
      <div class="dash-section-title">DEVICE TYPES</div>
      ${topTypes.map(([t,c]) => `
        <div class="dash-bar-row">
          <span class="dash-bar-label">${t}</span>
          <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${Math.round(c/maxType*100)}%;background:var(--accent)"></div></div>
          <span class="dash-bar-count">${c}</span>
        </div>`).join('')}
    </div>` : ''}
    ${topLinks.length ? `
    <div class="dash-section">
      <div class="dash-section-title">LINK TYPES</div>
      ${topLinks.map(([t,c]) => `
        <div class="dash-bar-row">
          <span class="dash-bar-label">${t}</span>
          <div class="dash-bar-track"><div class="dash-bar-fill" style="width:${Math.round(c/maxLink*100)}%;background:${LCOLORS[t]||'var(--accent)'}"></div></div>
          <span class="dash-bar-count">${c}</span>
        </div>`).join('')}
    </div>` : ''}
    ${!nodes.length ? '<div style="color:rgba(255,255,255,0.18);font-family:Share Tech Mono,monospace;font-size:10px;text-align:center;padding:16px 0;letter-spacing:1px;">RIGHT-CLICK TO ADD DEVICES</div>' : ''}
  `;
  document.getElementById('panel-actions').innerHTML = '';
}

// ═══════════════════════════ HEADER STATS ═══════════════════════════
let _lastHeaderCounts = '';
function updateHeaderStats() {
  const c = `${nodes.length}|${links.length}|${groups.length}`;
  if (c === _lastHeaderCounts) return;  // nothing changed — skip DOM write
  _lastHeaderCounts = c;
  const el = document.getElementById('header-stats');
  if (!el) return;
  el.innerHTML = `
    <span class="h-stat-pill" style="color:var(--accent);border-color:rgba(0,212,255,0.25)">&#9673; ${nodes.length}</span>
    <span class="h-stat-pill" style="color:var(--accent2);border-color:rgba(0,255,157,0.25)">&#8596; ${links.length}</span>
    ${groups.length ? `<span class="h-stat-pill" style="color:var(--gold);border-color:rgba(255,215,0,0.25)">&#9703; ${groups.length}</span>` : ''}
  `;
}

// ═══════════════════════════ MAIN CANVAS BACKGROUND ═══════════════════════════
function initMainBg() {
  const canvas = document.getElementById('main-bg-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  // ── Performance: throttle to 15 fps via setTimeout+RAF (not busy-RAF) ────────
  const BG_FPS = 15, BG_MS = 1000 / BG_FPS;
  let _bgRafId = null;

  let pts = [], rings = [], streams = [], scanY = 0;
  const CHARS = '01アイウエオカキクケコサシスセソタチツテトナニヌネノ';

  // ── Offscreen hex grid cache ──────────────────────────────────────────────
  let hexCache = null, hexCacheW = 0, hexCacheH = 0;
  function buildHexCache(W, H) {
    if (hexCacheW === W && hexCacheH === H) return; // already up to date
    hexCacheW = W; hexCacheH = H;
    hexCache = new OffscreenCanvas(W, H);
    const hx = hexCache.getContext('2d');
    const R = 38;
    hx.strokeStyle = 'rgba(0,212,255,1)'; // will be tinted via globalAlpha
    hx.lineWidth = 0.5;
    const rw = R * Math.sqrt(3), rh = R * 1.5;
    const cols2 = Math.ceil(W / rw) + 2, rows2 = Math.ceil(H / rh) + 2;
    for (let row = -1; row < rows2; row++) {
      for (let col = -1; col < cols2; col++) {
        const ox = col * rw + (row % 2 === 0 ? 0 : rw / 2);
        const oy = row * rh;
        hx.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = Math.PI / 180 * (60 * i - 30);
          const px = ox + R * Math.cos(a), py = oy + R * Math.sin(a);
          i === 0 ? hx.moveTo(px, py) : hx.lineTo(px, py);
        }
        hx.closePath(); hx.stroke();
      }
    }
  }

  function drawHexGrid(W, H, t) {
    buildHexCache(W, H);
    if (!hexCache) return;
    const pulse = 0.025 + 0.01 * Math.sin(t * 0.4);
    ctx.globalAlpha = pulse / 1; // hexCache stroked at alpha=1; scale to pulse
    ctx.drawImage(hexCache, 0, 0);
    ctx.globalAlpha = 1;
  }

  function resize() {
    canvas.width  = canvas.offsetWidth  || window.innerWidth;
    canvas.height = canvas.offsetHeight || window.innerHeight;
    hexCacheW = hexCacheH = 0; // invalidate hex cache on resize
    spawnParticles();
    spawnStreams();
  }

  function spawnParticles() {
    // Reduced from 35 → 20 particles (pair checks: 595 → 190 per frame)
    pts = Array.from({ length: 20 }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.2,
      vy: (Math.random() - 0.5) * 0.2,
      r: Math.random() * 1.3 + 0.4,
      ph: Math.random() * Math.PI * 2,
      col: Math.random() < 0.12 ? 'g' : Math.random() < 0.15 ? 'p' : 'c',
    }));
  }

  function spawnStreams() {
    // Cap at 10 streams — reduces fillText calls by ~65% vs previous 28
    const cols = Math.min(Math.floor(canvas.width / 36), 10);
    streams = Array.from({ length: cols }, (_, i) => ({
      x: i * (canvas.width / cols) + (canvas.width / cols) / 2,
      y: Math.random() * canvas.height,
      speed: 0.6 + Math.random() * 1.2,
      chars: Array.from({ length: 10 }, () => CHARS[Math.floor(Math.random() * CHARS.length)]),
      opacity: 0.018 + Math.random() * 0.022,
      len: 4 + Math.floor(Math.random() * 5),
      tick: 0,
    }));
  }

  // Spawn a ring pulse at a random position
  function spawnRing() {
    rings.push({
      x: 80 + Math.random() * (canvas.width - 160),
      y: 80 + Math.random() * (canvas.height - 160),
      r: 0, maxR: 60 + Math.random() * 90,
      col: Math.random() < 0.2 ? 'rgba(0,255,157,' : Math.random() < 0.2 ? 'rgba(168,85,247,' : 'rgba(0,212,255,',
    });
  }

  // Draw sci-fi corner HUD brackets
  function drawCorners(W, H) {
    const sz = 28, th = 1.5;
    ctx.lineWidth = th;
    [
      [0, 0,  1,  1],
      [W, 0, -1,  1],
      [0, H,  1, -1],
      [W, H, -1, -1],
    ].forEach(([cx, cy, dx, dy]) => {
      ctx.strokeStyle = 'rgba(0,212,255,0.55)';
      ctx.beginPath(); ctx.moveTo(cx + dx*sz, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy*sz); ctx.stroke();
      // inner tick
      ctx.strokeStyle = 'rgba(0,212,255,0.25)';
      ctx.beginPath(); ctx.moveTo(cx + dx*8, cy + dy*3); ctx.lineTo(cx + dx*3, cy + dy*3); ctx.lineTo(cx + dx*3, cy + dy*8); ctx.stroke();
    });
    // center crosshair
    const mx = W / 2, my = H / 2, cs = 14;
    ctx.strokeStyle = 'rgba(0,212,255,0.1)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(mx - cs, my); ctx.lineTo(mx + cs, my); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(mx, my - cs); ctx.lineTo(mx, my + cs); ctx.stroke();
    ctx.beginPath(); ctx.arc(mx, my, cs * 0.6, 0, Math.PI * 2); ctx.stroke();
  }

  let lastRing = 0;
  function frame() {
    // Stop-and-restart: don't re-schedule when paused (saves empty RAF/s)
    if (_bgPaused || !_ntmVisible) { _bgRafId = null; return; }
    _bgRafId = setTimeout(() => requestAnimationFrame(frame), BG_MS);

    const W = canvas.width, H = canvas.height;
    const t = Date.now() * 0.001;

    ctx.fillStyle = 'rgba(4,8,20,0.62)';
    ctx.fillRect(0, 0, W, H);

    // Hex grid (cached offscreen canvas, just drawImage + globalAlpha pulse)
    drawHexGrid(W, H, t);

    // Data streams
    ctx.font = '10px Share Tech Mono';
    streams.forEach(s => {
      s.y += s.speed;
      s.tick++;
      if (s.y > H + s.len * 14) { s.y = -s.len * 14; }
      if (s.tick % 8 === 0) s.chars[Math.floor(Math.random() * s.chars.length)] = CHARS[Math.floor(Math.random() * CHARS.length)];
      for (let i = 0; i < s.len; i++) {
        const fy = s.y - i * 14;
        if (fy < -14 || fy > H + 14) continue;
        const fade = (1 - i / s.len) * s.opacity;
        ctx.fillStyle = i === 0 ? `rgba(180,255,255,${(s.opacity * 3).toFixed(3)})` : `rgba(0,212,255,${fade.toFixed(3)})`;
        ctx.fillText(s.chars[i % s.chars.length], s.x - 5, fy);
      }
    });

    // Particles movement
    pts.forEach(p => { p.x += p.vx; p.y += p.vy; if (p.x < 0) p.x = W; if (p.x > W) p.x = 0; if (p.y < 0) p.y = H; if (p.y > H) p.y = 0; });

    // Connections — single batched path (1 stroke call instead of N)
    const MD2 = 110 * 110;
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(0,212,255,0.08)';
    ctx.lineWidth = 0.6;
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
        if (dx*dx + dy*dy < MD2) {
          ctx.moveTo(pts[i].x, pts[i].y);
          ctx.lineTo(pts[j].x, pts[j].y);
        }
      }
    }
    ctx.stroke();

    pts.forEach(p => {
      const pulse = 0.35 + 0.5 * Math.sin(t * 1.5 + p.ph);
      const a = pulse.toFixed(2);
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.col === 'g' ? `rgba(0,255,157,${a})` : p.col === 'p' ? `rgba(168,85,247,${a})` : `rgba(0,212,255,${a})`;
      ctx.fill();
    });

    // Ring pulses
    if (t - lastRing > 2.8 + Math.random() * 3) { spawnRing(); lastRing = t; }
    rings.forEach(r => { r.r += 1.1; });
    rings.forEach(r => {
      const progress = r.r / r.maxR;
      const alpha = (1 - progress) * 0.22;
      if (alpha <= 0) return;
      ctx.beginPath(); ctx.arc(r.x, r.y, r.r, 0, Math.PI * 2);
      ctx.strokeStyle = r.col + alpha.toFixed(3) + ')';
      ctx.lineWidth = 1 + (1 - progress);
      ctx.stroke();
    });
    for (let i = rings.length - 1; i >= 0; i--) { if (rings[i].r >= rings[i].maxR) rings.splice(i, 1); }

    // Scan line — simple fillRect, no gradient allocation per frame
    scanY = (scanY + 0.55) % H;
    ctx.fillStyle = 'rgba(0,212,255,0.03)';
    ctx.fillRect(0, scanY - 35, W, 39);
    ctx.fillStyle = 'rgba(0,212,255,0.07)';
    ctx.fillRect(0, scanY, W, 1);

    // Corner HUD
    drawCorners(W, H);
  }

  function startMainBg() { if (!_bgRafId) _bgRafId = requestAnimationFrame(frame); }

  resize();
  startMainBg();
  _resumeMainBg = startMainBg;
  window.addEventListener('resize', resize);
}

// ═══════════════════════════ CANVAS CONSTELLATION ═══════════════════════════
function renderBackground() {
  const layer = document.getElementById('bg-layer');
  if (!layer) return;
  let seed = 42;
  const rand = () => { seed = (seed * 1664525 + 1013904223) & 0xffffffff; return (seed >>> 0) / 4294967296; };

  const stars = Array.from({length: 55}, () => ({
    x: rand() * 3000 - 300,
    y: rand() * 2200 - 300,
    r: rand() * 1.4 + 0.7,
    op: rand() * 0.11 + 0.05,
    big: rand() > 0.82
  }));

  let out = '';
  // Connecting lines
  for (let i = 0; i < stars.length; i++) {
    for (let j = i + 1; j < stars.length; j++) {
      const dx = stars[i].x - stars[j].x, dy = stars[i].y - stars[j].y;
      if (Math.sqrt(dx*dx + dy*dy) < 240 && rand() > 0.55) {
        out += `<line x1="${stars[i].x.toFixed(0)}" y1="${stars[i].y.toFixed(0)}" x2="${stars[j].x.toFixed(0)}" y2="${stars[j].y.toFixed(0)}" stroke="rgba(0,212,255,0.035)" stroke-width="0.6"/>`;
      }
    }
  }
  // Stars
  stars.forEach(s => {
    if (s.big) out += `<circle cx="${s.x.toFixed(0)}" cy="${s.y.toFixed(0)}" r="${(s.r*2.2).toFixed(1)}" fill="rgba(0,212,255,${(s.op*0.5).toFixed(3)})" filter="url(#glow-blue)"/>`;
    out += `<circle cx="${s.x.toFixed(0)}" cy="${s.y.toFixed(0)}" r="${s.r.toFixed(1)}" fill="rgba(0,212,255,${s.op.toFixed(3)})"/>`;
  });

  layer.innerHTML = out;
}


// ═══════════════════════════ BOOT ═══════════════════════════
initMainBg();
initDashBg();
renderBackground();
loadPages().then(async () => {
  const saved = sessionStorage.getItem('ntm_active_tab');
  const pageIds = pages.map(p => String(p.id));
  if (saved && saved !== 'pw' && pageIds.includes(saved)) {
    await switchPage(parseInt(saved));
  } else {
    await switchToPingWatchPage();
  }
}).then(() => {
  const wrap = document.getElementById('canvas-wrap');
  if (wrap.clientWidth > 0) {
    fitToView();
  } else {
    const ro = new ResizeObserver((_, observer) => {
      if (wrap.clientWidth > 0) {
        observer.disconnect();
        fitToView();
      }
    });
    ro.observe(wrap);
  }
});
// ── Fullscreen helpers ────────────────────────────────────────────────────────
function _ntmUpdateFsBtn(btn) {
  const isFs = !!document.fullscreenElement;
  btn.textContent = isFs ? '✕ EXIT FULL' : '⛶ FULL SCREEN';
}
function _ntmToggleFs() {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
  else document.exitFullscreen();
}
document.addEventListener('fullscreenchange', () => {
  const isFs = !!document.fullscreenElement;
  document.body.classList.toggle('fs-active', isFs);
  const btn = document.getElementById('page-fs-btn');
  if (btn) _ntmUpdateFsBtn(btn);
  // Collapse side panel on enter, restore on exit
  const panel = document.getElementById('side-panel');
  if (panel) {
    if (isFs) {
      panel._ntmWasCollapsed = panel.classList.contains('collapsed');
      panel.classList.add('collapsed');
    } else {
      if (!panel._ntmWasCollapsed) panel.classList.remove('collapsed');
    }
  }
});

// Reload pages when PingWatch parent signals tab switch
window.addEventListener('message', e => {
  if (e.data && e.data.type === 'pw_reload_pages') {
    loadPages().then(() => {
      if (isPingWatchPage) switchToPingWatchPage();
      else if (!pages.find(p => p.id === currentPageId)) switchToPingWatchPage();
      else renderPageBar();
    });
  }
});

// Show/hide Info Box editor based on selected type
document.getElementById('node-type')?.addEventListener('change', (e) => {
  const t = e.target.value;
  setInfoEditorVisible(t);

  if (t === 'info-box') {
    const hasAny = (document.getElementById('info-lines')?.children?.length || 0) > 0;
    if (!hasAny) loadInfoLines([], { blankTemplate: true }); // ✅ blank rows, no sample text
  }
});
