// ── SUBNET DISCOVERY MODAL ──────────────────────────────────────────────
// Step 1: CIDR input + scan options
// Step 2: live progress
// Step 3: results table (multi-select)
// Step 4: per-device sensor review
// Step 5: bulk add via /api/discovery/bulk-add

const _disc = {
  scanId: null,
  pollT:  null,
  state:  null,            // last server response
  step:   1,               // 1=input, 2=scanning, 3=results, 4=sensor review
  rows:   [],              // results table data (mutable)
  selected: new Set(),     // ips selected for adding
  customNames: {},         // ip -> overridden name
  customGroups: {},        // ip -> group override (undefined = follow default _disc.group)
  sensorChecks: {},        // ip -> { key: bool }   key = `${stype}|${port||''}`
  sensorArgs: {},          // ip -> { key: { url, snmp_community, ... } }
  filter: '',
  sortKey: 'ip',
  showDups: 'all',         // 'all' | 'only' | 'hide'
  group: 'Discovered',     // persisted across step 3→4 navigation
};

function openDiscoverSubnet(){
  closeM('mdisc');
  _disc.scanId = null;
  _disc.state  = null;
  _disc.step   = 1;
  _disc.rows   = [];
  _disc.selected.clear();
  _disc.customNames = {};
  _disc.customGroups = {};
  _disc.sensorChecks = {};
  _disc.sensorArgs = {};
  _disc.filter = '';
  _disc.showDups = 'all';
  _disc.group = 'Discovered';

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'mdisc';
  _overlayClose(o, ()=>_discClose());
  o.innerHTML = `
    <div class="mbox mbox-discover">
      <div class="mhd">
        <div class="mttl">⊕ Discover Subnet</div>
        <button class="mclose" onclick="_discClose()">✕</button>
      </div>
      <div class="mbdy" id="disc-bdy"></div>
      <div class="mft" id="disc-ft"></div>
    </div>`;
  document.body.appendChild(o);
  _discRenderInput();
}

function _discClose(){
  if(_disc.pollT){ clearTimeout(_disc.pollT); _disc.pollT = null; }
  // If a scan is running, ask the server to stop it
  if(_disc.scanId && _disc.state && _disc.state.state === 'running'){
    try{ api('DELETE', `/api/discovery/scan/${_disc.scanId}`); }catch(e){}
  }
  closeM('mdisc');
}

// ── Step 1: input ──────────────────────────────────────────────
function _discRenderInput(){
  _disc.step = 1;
  const bdy = document.getElementById('disc-bdy');
  const ft  = document.getElementById('disc-ft');
  if(!bdy||!ft) return;
  bdy.innerHTML = `
    <div class="fr">
      <label class="fl">Subnet / CIDR</label>
      <input type="text" id="disc-cidr" placeholder="192.168.1.0/24" autocomplete="off" oninput="_discCidrChanged()"/>
      <div id="disc-cidr-info" class="disc-info-line">Enter a subnet in CIDR notation (e.g. 192.168.1.0/24)</div>
    </div>
    <div class="fr">
      <label class="cb-row"><input type="checkbox" id="disc-skip" checked/> Skip already-monitored IPs</label>
    </div>
    <div class="fr">
      <label class="fl">Scan mode</label>
      <div class="disc-mode-row">
        <label class="cb-row"><input type="radio" name="disc-mode" id="disc-mode-full" value="full" checked onchange="_discCidrChanged()"/> Full <span class="disc-mode-hint">(ping + DNS + ports + guess)</span></label>
        <label class="cb-row"><input type="radio" name="disc-mode" id="disc-mode-ping" value="ping" onchange="_discCidrChanged()"/> Ping only <span class="disc-mode-hint">(faster, large networks)</span></label>
      </div>
      <div class="disc-info-line" style="margin-top:4px">Full mode uses your <a href="#" onclick="_discOpenSettings();return false;">Port Scanner settings</a>.</div>
    </div>
    <div id="disc-warn-banner"></div>
  `;
  ft.innerHTML = `
    <button class="btn-s" onclick="_discClose()">Cancel</button>
    <button class="btn-p" id="disc-scan-btn" onclick="_discStartScan()" disabled>Scan ▶</button>
  `;
  setTimeout(()=>document.getElementById('disc-cidr')?.focus(), 50);
  document.getElementById('disc-cidr')?.addEventListener('keydown', e=>{
    if(e.key==='Enter') _discStartScan();
  });
}

function _discOpenSettings(){
  _discClose();
  if(typeof openSettings === 'function'){
    openSettings();
    setTimeout(()=>{
      const tab = document.querySelector('[data-stab="portscan"]') || document.querySelector('[onclick*="portscan"]');
      if(tab) tab.click();
    }, 100);
  }
}

// Pure-JS CIDR validator (mirrors backend _validate_cidr)
function _discParseCidr(cidr){
  if(!cidr) return {err:'Enter a CIDR'};
  const m = cidr.trim().match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/);
  if(!m) return {err:'Invalid CIDR format'};
  const oct = [+m[1],+m[2],+m[3],+m[4]];
  if(oct.some(v=>v<0||v>255)) return {err:'Invalid CIDR format'};
  const prefix = +m[5];
  if(prefix<0||prefix>32) return {err:'Invalid prefix'};
  const numAddrs = Math.pow(2, 32-prefix);
  if(numAddrs > 65536) return {err:'Subnet too large (max /16 = 65534 hosts)'};
  const hosts = prefix>=31 ? numAddrs : (numAddrs - 2);
  return {prefix, numAddrs, hosts};
}

function _discCidrChanged(){
  const cidr = document.getElementById('disc-cidr')?.value || '';
  const info = document.getElementById('disc-cidr-info');
  const banner = document.getElementById('disc-warn-banner');
  const btn = document.getElementById('disc-scan-btn');
  const r = _discParseCidr(cidr);
  if(r.err){
    if(info) info.textContent = cidr ? r.err : 'Enter a subnet in CIDR notation (e.g. 192.168.1.0/24)';
    if(info) info.className = 'disc-info-line' + (cidr ? ' disc-info-err' : '');
    if(banner) banner.innerHTML = '';
    if(btn) btn.disabled = true;
    return;
  }
  // Auto-switch to ping-only above 4096 hosts (user can override)
  if(r.hosts > 4096){
    const pingRadio = document.getElementById('disc-mode-ping');
    if(pingRadio && !pingRadio.checked && !pingRadio.dataset.userTouched){
      pingRadio.checked = true;
    }
  }
  const mode = document.querySelector('input[name="disc-mode"]:checked')?.value || 'full';
  const perHostEnrich = mode === 'full' ? 4 : 0.5;
  const pingPhase = Math.ceil(r.hosts / 64) * 2; // ~2s per batch of 64
  const enrichPhase = Math.ceil(r.hosts * 0.10 * perHostEnrich); // ~10% alive
  const est = pingPhase + enrichPhase;
  const estStr = est < 60 ? `${est}s` : `${Math.floor(est/60)}m ${est%60}s`;
  if(info){
    info.textContent = `${r.hosts} host${r.hosts===1?'':'s'} · ~${estStr} estimated`;
    info.className = 'disc-info-line';
  }
  if(banner){
    let b = '';
    if(r.hosts >= 16384){
      b = `<div class="disc-warn disc-warn-orange">⚠ Very large scan — expected runtime ~${estStr}. Consider scanning smaller ranges.</div>`;
    } else if(r.hosts >= 1024){
      b = `<div class="disc-warn">Large scan — may take several minutes (~${estStr}).</div>`;
    }
    if(r.hosts >= 65534){
      b = `<div class="disc-warn disc-warn-red">⛔ Maximum size scan — expected runtime ~${estStr}. Cancel any time.</div>`;
    }
    banner.innerHTML = b;
  }
  if(btn) btn.disabled = false;
}

// ── Step 2: scanning / progress ──────────────────────────────────
async function _discStartScan(){
  const cidr = document.getElementById('disc-cidr')?.value.trim() || '';
  const skip = document.getElementById('disc-skip')?.checked !== false;
  const mode = document.querySelector('input[name="disc-mode"]:checked')?.value || 'full';
  if(_discParseCidr(cidr).err){ toast('Invalid CIDR','err'); return; }
  const btn = document.getElementById('disc-scan-btn');
  if(btn){ btn.disabled = true; btn.textContent = 'Starting…'; }
  let r;
  try{
    r = await api('POST', '/api/discovery/scan', {cidr, skip_monitored:skip, mode});
  }catch(e){
    toast('Failed to start scan','err');
    if(btn){ btn.disabled = false; btn.textContent = 'Scan ▶'; }
    return;
  }
  if(!r || !r.scan_id){
    toast(r && r.error ? r.error : 'Failed to start scan','err');
    if(btn){ btn.disabled = false; btn.textContent = 'Scan ▶'; }
    return;
  }
  _disc.scanId = r.scan_id;
  _disc.step = 2;
  _discRenderProgress();
  _discPoll();
}

function _discRenderProgress(){
  const bdy = document.getElementById('disc-bdy');
  const ft  = document.getElementById('disc-ft');
  if(!bdy||!ft) return;
  const st  = _disc.state;
  const cidr = (st && st.cidr) || '...';
  const phase = (st && st.phase) || 'starting';
  const pg = (st && st.progress) || {total:0,checked:0,alive:0,enrich_total:0,enriched:0,monitored_skipped:0};
  let pct = 0;
  let pctLabel = '';
  if(phase === 'pinging' && pg.total > 0){
    pct = Math.min(100, Math.round((pg.checked/pg.total)*100));
    pctLabel = `${pg.checked} / ${pg.total}`;
  } else if(phase === 'enriching' && pg.enrich_total > 0){
    pct = Math.min(100, Math.round((pg.enriched/pg.enrich_total)*100));
    pctLabel = `${pg.enriched} / ${pg.enrich_total} hosts`;
  } else if(phase === 'analyzing'){
    pct = 99; pctLabel = 'Analyzing duplicates…';
  }
  const phaseLabel = {
    starting:'Starting…', pinging:'Pinging hosts',
    enriching:'Enriching alive hosts', analyzing:'Analyzing duplicates'
  }[phase] || phase;
  bdy.innerHTML = `
    <div class="disc-prog-wrap">
      <div class="disc-prog-title">Scanning ${esc(cidr)}…</div>
      <div class="disc-progress"><div class="disc-progress-bar" style="width:${pct}%"></div></div>
      <div class="disc-prog-stats">
        <span>Phase: <b>${esc(phaseLabel)}</b></span>
        <span>${esc(pctLabel)}</span>
      </div>
      <div class="disc-prog-stats" style="margin-top:6px">
        <span>Alive: <b style="color:var(--up)">${pg.alive}</b></span>
        <span>Skipped (monitored): <b>${pg.monitored_skipped}</b></span>
      </div>
    </div>
  `;
  ft.innerHTML = `
    <button class="btn-s" onclick="_discCancelScan()">Cancel ✕</button>
  `;
}

async function _discPoll(){
  if(!_disc.scanId) return;
  let r;
  try{
    r = await api('GET', `/api/discovery/scan/${_disc.scanId}`);
  }catch(e){
    _disc.pollT = setTimeout(_discPoll, 2000);
    return;
  }
  if(!r){ return; }
  _disc.state = r;
  if(_disc.step === 2) _discRenderProgress();
  if(r.state === 'done'){
    _disc.rows = (r.results || []).slice();
    _discInitSelectionDefaults();
    _discRenderResults();
    return;
  }
  if(r.state === 'cancelled'){
    if((r.results||[]).length){
      _disc.rows = r.results.slice();
      _discInitSelectionDefaults();
      _discRenderResults();
    } else {
      toast('Scan cancelled','info');
      _discRenderInput();
    }
    return;
  }
  if(r.state === 'error'){
    toast(r.error || 'Scan failed','err');
    _discRenderInput();
    return;
  }
  _disc.pollT = setTimeout(_discPoll, 1000);
}

async function _discCancelScan(){
  if(!_disc.scanId) return;
  try{
    await api('DELETE', `/api/discovery/scan/${_disc.scanId}`);
  }catch(e){}
}

// ── Step 3: results table ───────────────────────────────────────
function _discInitSelectionDefaults(){
  // Default selection: every row that is NOT a possible duplicate.
  _disc.selected.clear();
  for(const row of _disc.rows){
    if(!row.possible_duplicate_of){
      _disc.selected.add(row.ip);
    }
  }
}

function _discRowName(row, useHostname){
  if(_disc.customNames[row.ip] !== undefined){
    return _disc.customNames[row.ip];
  }
  if(useHostname && row.hostname){
    return row.hostname.split('.')[0];
  }
  return `Host ${row.ip}`;
}

function _discFilteredRows(){
  let rows = _disc.rows.slice();
  const f = (_disc.filter || '').toLowerCase();
  if(f){
    rows = rows.filter(r=>{
      const portStr = (r.ports||[]).map(p=>p.port).join(',');
      return (r.ip||'').toLowerCase().includes(f)
          || (r.hostname||'').toLowerCase().includes(f)
          || (r.mac||'').toLowerCase().includes(f)
          || (r.vendor||'').toLowerCase().includes(f)
          || portStr.includes(f);
    });
  }
  if(_disc.showDups === 'only'){
    rows = rows.filter(r=>!!r.possible_duplicate_of);
  } else if(_disc.showDups === 'hide'){
    rows = rows.filter(r=>!r.possible_duplicate_of);
  }
  const k = _disc.sortKey;
  rows.sort((a,b)=>{
    if(k==='ip'){
      const ai = (a.ip||'').split('.').map(n=>+n);
      const bi = (b.ip||'').split('.').map(n=>+n);
      for(let i=0;i<4;i++){ if(ai[i]!==bi[i]) return ai[i]-bi[i]; }
      return 0;
    }
    if(k==='hostname') return (a.hostname||'').localeCompare(b.hostname||'');
    if(k==='ports')    return ((b.ports||[]).length) - ((a.ports||[]).length);
    if(k==='guess')    return (a.guess||'').localeCompare(b.guess||'');
    return 0;
  });
  return rows;
}

function _discRenderResults(){
  _disc.step = 3;
  const bdy = document.getElementById('disc-bdy');
  const ft  = document.getElementById('disc-ft');
  if(!bdy||!ft) return;
  const rows = _discFilteredRows();
  const dupCount = _disc.rows.filter(r=>!!r.possible_duplicate_of).length;
  const isPing = (_disc.state && _disc.state.mode) === 'ping';
  bdy.innerHTML = `
    <div class="disc-result-toolbar">
      <div>
        <span id="disc-count"><b>${_disc.rows.length}</b> device${_disc.rows.length===1?'':'s'} found</span>
        ${dupCount ? `<span class="disc-dup-chip">⚠ ${dupCount} possible duplicate${dupCount===1?'':'s'}</span>` : ''}
      </div>
      <div class="disc-toolbar-right">
        <input type="text" class="disc-filter-inp" placeholder="Filter…" oninput="_discSetFilter(this.value)" value="${esc(_disc.filter)}"/>
        <select onchange="_disc.showDups=this.value; _discRenderResults()">
          <option value="all" ${_disc.showDups==='all'?'selected':''}>All rows</option>
          <option value="only" ${_disc.showDups==='only'?'selected':''}>Only duplicates</option>
          <option value="hide" ${_disc.showDups==='hide'?'selected':''}>Hide duplicates</option>
        </select>
      </div>
    </div>
    <div class="disc-quick-row">
      <button class="btn-s" onclick="_discSelectAll(true)">Select all visible</button>
      <button class="btn-s" onclick="_discSelectAll(false)">Clear all</button>
      ${isPing ? '' : `
        <button class="btn-s" onclick="_discSelectByPort(161)">+ SNMP open</button>
        <button class="btn-s" onclick="_discSelectByPort(80,443,8080,8443)">+ Web open</button>
      `}
    </div>
    <div class="disc-table-wrap">
      <table class="disc-table">
        <thead><tr>
          <th style="width:32px"></th>
          <th class="disc-th-sortable" onclick="_discSetSort('ip')">IP</th>
          <th class="disc-th-sortable" onclick="_discSetSort('hostname')">Hostname</th>
          <th>Group</th>
          <th>MAC / Vendor</th>
          <th class="disc-th-sortable" onclick="_discSetSort('ports')">${isPing?'':'Ports'}</th>
          <th class="disc-th-sortable" onclick="_discSetSort('guess')">${isPing?'':'Type'}</th>
          <th>Latency</th>
          <th></th>
        </tr></thead>
        <tbody id="disc-tbody">
          ${rows.map(r=>_discRowHtml(r,isPing)).join('')}
        </tbody>
      </table>
    </div>
    <datalist id="disc-groups-dl">${
      [...new Set(Object.values(S.devices).map(d=>d.group).filter(Boolean))].sort()
      .map(g=>`<option value="${esc(g)}"></option>`).join('')
    }</datalist>
    <div class="disc-foot-opts">
      <div class="fr" style="margin:0">
        <label class="fl">Group <span style="font-size:10px;color:var(--text3);font-weight:normal">(default for all)</span></label>
        <div style="position:relative">
          <input type="text" id="disc-group" value="${esc(_disc.group)}" placeholder="Discovered" autocomplete="off"
                 style="padding-right:28px"
                 onfocus="_dgShow()" oninput="_dgFilter(this.value);_discSetDefaultGroup(this.value)"/>
          <button class="grp-dd-arrow" tabindex="-1" onmousedown="event.preventDefault();_dgToggle()">▾</button>
          <div id="disc-group-dd" class="grp-dd" style="display:none">${
            [...new Set(Object.values(S.devices).map(d=>d.group).filter(Boolean))].sort()
            .map(g=>`<div class="grp-dd-item" data-g="${esc(g.toLowerCase())}" onmousedown="event.preventDefault()" onclick="_dgPick(this.textContent)">${esc(g)}</div>`).join('')
          }</div>
        </div>
      </div>
      <div class="fr" style="margin:0">
        <label class="fl">Naming</label>
        <select id="disc-naming" onchange="_discRefreshTbody()">
          <option value="hostname">Use hostname (fallback to IP)</option>
          <option value="ip">Use IP only</option>
        </select>
      </div>
    </div>
  `;
  ft.innerHTML = `
    <button class="btn-s" onclick="_discRenderInput()">◀ New scan</button>
    <button class="btn-s" onclick="_discClose()">Cancel</button>
    <button class="btn-p" id="disc-next-btn" onclick="_discRenderSensorReview()">Next: Review sensors ▶</button>
  `;
  _discUpdateNextBtn();
  document.getElementById('disc-group')?.addEventListener('blur', () => setTimeout(_dgHide, 150));
}

function _dgShow(){
  const dd = document.getElementById('disc-group-dd');
  if(!dd) return;
  dd.style.display = '';
  _dgFilter('');
}
function _dgToggle(){
  const dd = document.getElementById('disc-group-dd');
  if(dd && dd.style.display !== 'none') _dgHide();
  else _dgShow();
}
function _dgHide(){
  const dd = document.getElementById('disc-group-dd');
  if(dd) dd.style.display = 'none';
}
function _dgFilter(v){
  const dd = document.getElementById('disc-group-dd');
  if(!dd) return;
  const q = v.trim().toLowerCase();
  let any = false;
  dd.querySelectorAll('.grp-dd-item').forEach(el => {
    const show = !q || el.dataset.g.includes(q);
    el.style.display = show ? '' : 'none';
    if(show) any = true;
  });
  dd.style.display = any ? '' : 'none';
}
function _dgPick(g){
  const inp = document.getElementById('disc-group');
  if(inp) inp.value = g;
  _discSetDefaultGroup(g);
  _dgHide();
}

function _discRowHtml(r, isPing){
  const checked = _disc.selected.has(r.ip) ? 'checked' : '';
  const useHostname = (document.getElementById('disc-naming')?.value || 'hostname') === 'hostname';
  const nm = _discRowName(r, useHostname);
  const portChips = (r.ports||[]).map(p=>`<span class="disc-chip" title="${esc(p.name||'')}">${p.port}</span>`).join('');
  const macStr = r.mac ? `<span class="disc-mono">${esc(r.mac)}</span>` : '<span class="disc-muted">—</span>';
  const vendStr = r.vendor ? `<span class="disc-vendor">${esc(r.vendor)}</span>` : '';
  const dup = r.possible_duplicate_of;
  let dupChip = '';
  let dupNote = '';
  if(dup){
    dupChip = ` <span class="disc-warn-chip" title="${esc(dup.kind==='existing_device'?'Same hostname as existing device':'Same hostname as another scan result')}">⚠</span>`;
    if(dup.kind === 'existing_device'){
      dupNote = `<div class="disc-dup-note">↳ Same hostname as existing device <b>${esc(dup.name)}</b> (${esc(dup.host)}) — likely a second NIC. Add anyway as a separate device to monitor each NIC independently.</div>`;
    } else {
      dupNote = `<div class="disc-dup-note">↳ Same hostname as another scan result: <b>${esc(dup.host)}</b></div>`;
    }
  }
  const hostCell = r.hostname
    ? `<span class="disc-hn">${esc(r.hostname)}</span>${dupChip}`
    : `<span class="disc-muted">—</span>${dupChip}`;
  const ms = (r.ms != null) ? `${r.ms}ms` : '<span class="disc-muted">—</span>';
  return `
    <tr class="${dup?'disc-row-dup':''}" data-ip="${esc(r.ip)}">
      <td><input type="checkbox" class="disc-cb" ${checked} onchange="_discToggle('${esc(r.ip)}', this.checked)"/></td>
      <td><span class="disc-mono">${esc(r.ip)}</span></td>
      <td>${hostCell}${dupNote}
        <div class="disc-row-name">
          <input type="text" value="${esc(nm)}" placeholder="Device name" oninput="_discSetCustomName('${esc(r.ip)}', this.value)"/>
        </div>
      </td>
      <td>
        <input type="text" class="disc-row-grp${_disc.customGroups[r.ip]!==undefined?' disc-row-grp-custom':''}"
               list="disc-groups-dl" data-ip="${esc(r.ip)}"
               value="${esc(_disc.customGroups[r.ip]!==undefined?_disc.customGroups[r.ip]:_disc.group)}"
               placeholder="Group"
               onfocus="_discGrpFocus(this)" onblur="_discGrpBlur(this,'${esc(r.ip)}')"
               oninput="_discSetRowGroup('${esc(r.ip)}',this.value)"/>
      </td>
      <td>${macStr} ${vendStr}</td>
      <td>${isPing?'<span class="disc-muted">—</span>':portChips || '<span class="disc-muted">—</span>'}</td>
      <td>${isPing?'':`<span class="disc-guess">${esc(r.guess||'')}</span>`}</td>
      <td>${ms}</td>
      <td><button class="btn-s" style="padding:1px 6px;font-size:10px;white-space:nowrap" onclick="event.stopPropagation();_discLinkToDevice('${esc(r.ip)}')" title="Add this IP as a secondary IP of an existing device">🔗 Link</button></td>
    </tr>`;
}

function _discRefreshTbody(){
  const tb = document.getElementById('disc-tbody');
  if(!tb) return;
  const isPing = (_disc.state && _disc.state.mode) === 'ping';
  tb.innerHTML = _discFilteredRows().map(r=>_discRowHtml(r, isPing)).join('');
}

function _discSetFilter(v){
  _disc.filter = v || '';
  _discRefreshTbody();
}

function _discSetSort(k){
  _disc.sortKey = k;
  _discRefreshTbody();
}

function _discToggle(ip, on){
  if(on) _disc.selected.add(ip);
  else _disc.selected.delete(ip);
  _discUpdateNextBtn();
}

function _discSelectAll(on){
  const visible = _discFilteredRows();
  for(const r of visible){
    if(on) _disc.selected.add(r.ip);
    else _disc.selected.delete(r.ip);
  }
  _discRefreshTbody();
  _discUpdateNextBtn();
}

function _discSelectByPort(...ports){
  const set = new Set(ports);
  for(const r of _disc.rows){
    if((r.ports||[]).some(p=>set.has(p.port))){
      _disc.selected.add(r.ip);
    }
  }
  _discRefreshTbody();
  _discUpdateNextBtn();
}

function _discSetCustomName(ip, value){
  _disc.customNames[ip] = value;
}

function _discSetDefaultGroup(g){
  _disc.group = g;
  // Push new default to all per-row inputs that haven't been manually overridden
  document.querySelectorAll('.disc-row-grp[data-ip]').forEach(inp => {
    if(_disc.customGroups[inp.dataset.ip] === undefined){
      inp.value = g;
    }
  });
}

function _discGrpFocus(inp){
  inp.dataset.prev = inp.value;
  inp.value = '';
}
function _discGrpBlur(inp, ip){
  if(!inp.value){
    // Restore previous value if user clicked away without picking
    inp.value = inp.dataset.prev || _disc.group;
    // Make sure state matches
    if(inp.value === _disc.group) delete _disc.customGroups[ip];
  }
}

function _discSetRowGroup(ip, g){
  // If user types the same as the default, treat as "following default" (no override)
  if(g === _disc.group || g === ''){
    delete _disc.customGroups[ip];
    document.querySelector(`.disc-row-grp[data-ip="${ip}"]`)?.classList.remove('disc-row-grp-custom');
  } else {
    _disc.customGroups[ip] = g;
    document.querySelector(`.disc-row-grp[data-ip="${ip}"]`)?.classList.add('disc-row-grp-custom');
  }
}

function _discUpdateNextBtn(){
  const btn = document.getElementById('disc-next-btn');
  if(!btn) return;
  const n = _disc.selected.size;
  btn.disabled = (n === 0);
  btn.textContent = `Next: Review sensors (${n}) ▶`;
}

// ── Step 4: per-device sensor review ────────────────────────────
function _discRenderSensorReview(){
  if(_disc.selected.size === 0){ toast('Select at least one device','err'); return; }
  // Save group value before bdy is replaced (input leaves the DOM after this)
  const _grpEl = document.getElementById('disc-group');
  if(_grpEl) _disc.group = _grpEl.value.trim() || 'Discovered';
  _disc.step = 4;
  const bdy = document.getElementById('disc-bdy');
  const ft  = document.getElementById('disc-ft');
  if(!bdy||!ft) return;
  // Initialize sensor checks for any new selections
  const selectedRows = _disc.rows.filter(r=>_disc.selected.has(r.ip));
  for(const row of selectedRows){
    if(!_disc.sensorChecks[row.ip]){
      _disc.sensorChecks[row.ip] = {};
      _disc.sensorArgs[row.ip] = {};
      for(const sg of (row.suggested||[])){
        const key = `${sg.stype}|${sg.port||''}`;
        _disc.sensorChecks[row.ip][key] = !!sg.enabled;
        if(sg.url) _disc.sensorArgs[row.ip][key] = {url: sg.url};
      }
    }
  }
  bdy.innerHTML = `
    <div class="disc-rev-title">Review sensors — ${selectedRows.length} device${selectedRows.length===1?'':'s'} selected</div>
    <div class="disc-bulk-toggles">
      <button class="btn-s" onclick="_discBulkToggle('http', true)">Enable all HTTP</button>
      <button class="btn-s" onclick="_discBulkToggle('http', false)">Disable all HTTP</button>
      <button class="btn-s" onclick="_discBulkToggle('snmp', true)">Enable all SNMP</button>
      <button class="btn-s" onclick="_discBulkToggle('snmp', false)">Disable all SNMP</button>
      <button class="btn-s" onclick="_discBulkToggle('tls', true)">Enable all TLS</button>
      <button class="btn-s" onclick="_discBulkToggle('tcp', true)">Enable all TCP</button>
    </div>
    <div class="disc-rev-list">
      ${selectedRows.map((r,i)=>_discRevRowHtml(r, i===0)).join('')}
    </div>
  `;
  ft.innerHTML = `
    <button class="btn-s" onclick="_discRenderResults()">◀ Back</button>
    <button class="btn-s" onclick="_discClose()">Cancel</button>
    <button class="btn-p" id="disc-add-btn" onclick="_discBulkAdd()">Add devices</button>
  `;
  _discUpdateAddBtn();
}

function _discRevRowHtml(row, expanded){
  const useHostname = (document.getElementById('disc-naming')?.value || 'hostname') === 'hostname';
  const nm = _discRowName(row, useHostname);
  const sensors = (row.suggested||[]);
  const checks  = _disc.sensorChecks[row.ip] || {};
  const args    = _disc.sensorArgs[row.ip]   || {};
  const checkedCount = sensors.filter(s=>checks[`${s.stype}|${s.port||''}`]).length;
  const summary = `${checkedCount} sensor${checkedCount===1?'':'s'} selected`;
  const sensorRows = sensors.map(sg=>{
    const key = `${sg.stype}|${sg.port||''}`;
    const isChecked = !!checks[key];
    let extra = '';
    if(sg.stype === 'http'){
      const url = (args[key] && args[key].url) || sg.url || '';
      extra = `<input type="text" class="disc-sg-extra" placeholder="URL" value="${esc(url)}" oninput="_discSetArg('${esc(row.ip)}','${esc(key)}','url',this.value)"/>`;
    } else if(sg.stype === 'snmp'){
      const comm = (args[key] && args[key].snmp_community) || '';
      extra = `<input type="text" class="disc-sg-extra" placeholder="community (leave blank → fill later)" value="${esc(comm)}" oninput="_discSetArg('${esc(row.ip)}','${esc(key)}','snmp_community',this.value)"/>`;
    }
    return `
      <div class="disc-sg-row">
        <label class="cb-row">
          <input type="checkbox" ${isChecked?'checked':''} onchange="_discSetCheck('${esc(row.ip)}','${esc(key)}',this.checked)"/>
          <span class="disc-sg-name">${esc(sg.name)}</span>
        </label>
        ${extra}
      </div>`;
  }).join('');
  return `
    <details class="disc-rev-card" ${expanded?'open':''} data-ip="${esc(row.ip)}">
      <summary>
        <div class="disc-rev-head">
          <div>
            <b>${esc(nm)}</b>
            <span class="disc-muted"> (${esc(row.ip)})</span>
            ${row.guess?`<span class="disc-guess">— ${esc(row.guess)}</span>`:''}
          </div>
          <div style="display:flex;align-items:center;gap:8px">
            <div class="disc-rev-summary" id="disc-rev-sum-${esc(row.ip).replace(/\./g,'_')}">${summary}</div>
            <div style="font-size:10px;color:var(--text3)">→ ${esc((_disc.customGroups[row.ip]!==undefined?_disc.customGroups[row.ip]:_disc.group)||'Discovered')}</div>
          </div>
        </div>
      </summary>
      <div class="disc-sg-list">${sensorRows}</div>
    </details>`;
}

function _discSetCheck(ip, key, on){
  if(!_disc.sensorChecks[ip]) _disc.sensorChecks[ip] = {};
  _disc.sensorChecks[ip][key] = !!on;
  // Update summary in place
  const row = _disc.rows.find(r=>r.ip===ip);
  if(row){
    const checks = _disc.sensorChecks[ip];
    const c = (row.suggested||[]).filter(s=>checks[`${s.stype}|${s.port||''}`]).length;
    const sum = document.getElementById(`disc-rev-sum-${ip.replace(/\./g,'_')}`);
    if(sum) sum.textContent = `${c} sensor${c===1?'':'s'} selected`;
  }
  _discUpdateAddBtn();
}

function _discSetArg(ip, key, field, value){
  if(!_disc.sensorArgs[ip]) _disc.sensorArgs[ip] = {};
  if(!_disc.sensorArgs[ip][key]) _disc.sensorArgs[ip][key] = {};
  _disc.sensorArgs[ip][key][field] = value;
}


function _discBulkToggle(stype, on){
  for(const ip of _disc.selected){
    const row = _disc.rows.find(r=>r.ip===ip);
    if(!row) continue;
    for(const sg of (row.suggested||[])){
      if(sg.stype === stype){
        const key = `${sg.stype}|${sg.port||''}`;
        if(!_disc.sensorChecks[ip]) _disc.sensorChecks[ip] = {};
        _disc.sensorChecks[ip][key] = on;
      }
    }
  }
  _discRenderSensorReview();
}

function _discUpdateAddBtn(){
  const btn = document.getElementById('disc-add-btn');
  if(!btn) return;
  let nDev = 0, nSens = 0;
  for(const ip of _disc.selected){
    const row = _disc.rows.find(r=>r.ip===ip);
    if(!row) continue;
    nDev++;
    const checks = _disc.sensorChecks[ip] || {};
    for(const sg of (row.suggested||[])){
      if(checks[`${sg.stype}|${sg.port||''}`]) nSens++;
    }
  }
  btn.disabled = (nDev === 0);
  btn.textContent = `Add ${nDev} device${nDev===1?'':'s'} + ${nSens} sensor${nSens===1?'':'s'}`;
}

// ── Link discovered IP to existing device ─────────────────────
// Step 1: device picker
function _discLinkToDevice(ip){
  const devs = Object.values(S.devices).sort((a,b)=>a.name.localeCompare(b.name));
  if(!devs.length){ toast('No existing devices to link to','err'); return; }
  closeM('disc-link-m');
  const o = document.createElement('div'); o.className='mo'; o.id='disc-link-m';
  _overlayClose(o, ()=>closeM('disc-link-m'));
  const devItems = devs.map(d =>
    `<div class="grp-dd-item" style="padding:6px 10px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border)"
          onclick="_discLinkStep2('${esc(ip)}','${esc(d.device_id)}')">
      <span>${esc(d.name)}</span>
      <span style="font-size:11px;color:var(--text3);font-family:monospace">${esc(d.host)}</span>
    </div>`
  ).join('');
  o.innerHTML = `
  <div class="mbox" style="max-width:420px">
    <div class="mhd">
      <div class="mttl">Link ${esc(ip)} to device</div>
      <button class="mclose" onclick="closeM('disc-link-m')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr">
        <label class="fl">Search devices</label>
        <input type="text" id="disc-link-filter" autocomplete="off" placeholder="Filter…"
               oninput="_discLinkFilter(this.value)"/>
      </div>
      <div id="disc-link-list" style="max-height:280px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;margin-top:6px">${devItems}</div>
      <div style="font-size:11px;color:var(--text3);margin-top:8px">This IP will be added as a secondary IP of the selected device.</div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('disc-link-m')">Cancel</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('disc-link-filter')?.focus(), 50);
}
function _discLinkFilter(q){
  q = q.toLowerCase();
  const list = document.getElementById('disc-link-list');
  if(!list) return;
  for(const item of list.children){
    const text = item.textContent.toLowerCase();
    item.style.display = text.includes(q) ? '' : 'none';
  }
}

// Step 2: confirm + sensor selection
function _discLinkStep2(ip, did){
  const dev = S.devices[did];
  const devName = dev?.name || did;
  const row = _disc.rows.find(r => r.ip === ip);
  // Use discovered suggestions; fall back to a single PING if none
  const suggested = (row?.suggested || []).length > 0
    ? row.suggested
    : [{stype:'ping', name:`Ping-${ip}`, port:null}];

  const sensRows = suggested.map((sg, i) => {
    const label = `${esc(sg.name)} <span style="font-size:10px;color:var(--text3)">(${esc(sg.stype)}${sg.port?':'+sg.port:''})</span>`;
    return `<label style="display:flex;align-items:center;gap:8px;padding:5px 0;cursor:pointer;border-bottom:1px solid var(--border)">
      <input type="checkbox" id="dlsnk-${i}" checked>
      <span>${label}</span>
    </label>`;
  }).join('');

  const mbox = document.querySelector('#disc-link-m .mbox');
  if(!mbox) return;
  mbox.innerHTML = `
    <div class="mhd">
      <div class="mttl">Link ${esc(ip)} → ${esc(devName)}</div>
      <button class="mclose" onclick="closeM('disc-link-m')">✕</button>
    </div>
    <div class="mbdy">
      <div style="font-size:12px;color:var(--text2);margin-bottom:10px">
        <b>${esc(ip)}</b> will be registered as a secondary IP of <b>${esc(devName)}</b>.
      </div>
      <div class="fl" style="margin-bottom:6px">Also add sensors on ${esc(devName)}:</div>
      <div style="border:1px solid var(--border);border-radius:6px;padding:0 10px">${sensRows}</div>
      <div style="font-size:11px;color:var(--text3);margin-top:8px">Uncheck sensors you don't want to add. All sensors will use this IP as their host.</div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="_discLinkToDevice('${esc(ip)}')">← Back</button>
      <button class="btn-s" onclick="closeM('disc-link-m')">Cancel</button>
      <button class="btn-p" onclick="_discLinkConfirm('${esc(ip)}','${esc(did)}',${suggested.length})">Link</button>
    </div>`;
}

async function _discLinkConfirm(ip, did, nSuggested){
  const mbox = document.querySelector('#disc-link-m .mbox');
  const btn = mbox?.querySelector('.btn-p');
  if(btn){ btn.disabled=true; btn.textContent='Linking…'; }

  // Collect checked sensors
  const row = _disc.rows.find(r => r.ip === ip);
  const suggested = (row?.suggested || []).length > 0
    ? row.suggested
    : [{stype:'ping', name:`Ping-${ip}`, port:null}];
  const toCreate = suggested.filter((_,i)=>document.getElementById(`dlsnk-${i}`)?.checked);

  // 1. Register secondary IP
  let r;
  try{ r = await api('POST', `/api/device/${did}/secondary-ip`, {ip}); }
  catch(e){ toast('Failed to link IP','err'); if(btn){btn.disabled=false;btn.textContent='Link';} return; }
  if(!r || r.error){ toast(r?.error||'Failed to link','err'); if(btn){btn.disabled=false;btn.textContent='Link';} return; }

  // Update local secondary_ips
  const dev = S.devices[did];
  if(dev) dev.secondary_ips = r.secondary_ips || [...(dev.secondary_ips||[]), ip];

  // 2. Create selected sensors
  let sensCreated = 0, sensFailed = 0;
  for(const sg of toCreate){
    const spec = {name: `${sg.name}-${ip}`, type: sg.stype, host: ip};
    if(sg.port) spec.port = sg.port;
    if(sg.url)  spec.url  = sg.url;
    try{
      const sr = await api('POST', `/api/device/${did}/sensor`, spec);
      if(sr && sr.sid){
        await api('POST', `/api/device/${did}/sensor/${sr.sid}/start`);
        sensCreated++;
      } else { sensFailed++; }
    } catch(e){ sensFailed++; }
  }

  closeM('disc-link-m');
  // Remove from discovery results
  _disc.rows = _disc.rows.filter(r => r.ip !== ip);
  _disc.selected.delete(ip);
  _discRefreshTbody();
  _discUpdateCounts();

  const snrMsg = toCreate.length === 0 ? ''
    : sensFailed ? ` — ${sensCreated} sensor${sensCreated===1?'':'s'} added, ${sensFailed} failed`
    : ` + ${sensCreated} sensor${sensCreated===1?'':'s'}`;
  toast(`Linked ${ip} → ${dev?.name || did}${snrMsg}`, sensFailed?'err':'ok');
}
function _discUpdateCounts(){
  const countEl = document.getElementById('disc-count');
  if(countEl) countEl.innerHTML = `<b>${_disc.rows.length}</b> device${_disc.rows.length===1?'':'s'} found`;
}

// ── Step 5: bulk add ───────────────────────────────────────────
async function _discBulkAdd(){
  const btn = document.getElementById('disc-add-btn');
  const defaultGroup = (_disc.group || 'Discovered').trim() || 'Discovered';
  const useHostname = (document.getElementById('disc-naming')?.value || 'hostname') === 'hostname';
  const devices = [];
  for(const ip of _disc.selected){
    const row = _disc.rows.find(r=>r.ip===ip);
    if(!row) continue;
    const checks = _disc.sensorChecks[ip] || {};
    const args   = _disc.sensorArgs[ip]   || {};
    const sensors = [];
    for(const sg of (row.suggested||[])){
      const key = `${sg.stype}|${sg.port||''}`;
      if(!checks[key]) continue;
      const spec = {stype: sg.stype, name: sg.name};
      if(sg.port) spec.port = sg.port;
      const a = args[key] || {};
      if(sg.stype === 'http' && (a.url || sg.url)) spec.url = a.url || sg.url;
      if(sg.stype === 'snmp' && a.snmp_community) spec.snmp_community = a.snmp_community;
      sensors.push(spec);
    }
    const devGroup = (_disc.customGroups[ip] !== undefined ? _disc.customGroups[ip] : defaultGroup).trim() || defaultGroup;
    devices.push({
      name:  _discRowName(row, useHostname),
      host:  ip,
      group: devGroup,
      sensors,
    });
  }
  if(!devices.length){ toast('No devices to add','err'); return; }
  if(btn){ btn.disabled = true; btn.textContent = 'Adding…'; }
  let r;
  try{
    const cidr = (_disc.state && _disc.state.cidr) || '';
    r = await api('POST', '/api/discovery/bulk-add', {devices, cidr});
  }catch(e){
    toast('Bulk add failed','err');
    if(btn){ btn.disabled = false; _discUpdateAddBtn(); }
    return;
  }
  if(!r){
    toast('Bulk add failed','err');
    if(btn){ btn.disabled = false; _discUpdateAddBtn(); }
    return;
  }
  const ok = (r.created||[]).length;
  const ng = (r.errors||[]).length;
  toast(`Added ${ok} device${ok===1?'':'s'}${ng?` (${ng} failed)`:''}`, ok ? 'ok' : 'err');

  // Refresh local state for the freshly created devices
  for(const c of (r.created||[])){
    try{
      const dev = await (await fetch(`/api/device/${c.did}`)).json();
      if(dev && dev.device_id){
        S.devices[c.did] = dev;
        if(dev.sensors){
          for(const s of dev.sensors){
            S.sensors[`${c.did}/${s.sensor_id}`] = s;
            S.logs[`${c.did}/${s.sensor_id}`] = [];
          }
        }
        if(typeof renderDp === 'function') renderDp(dev);
      }
    }catch(e){}
  }
  if(typeof updatePills === 'function') updatePills();
  if(typeof refreshGroupCounts === 'function') refreshGroupCounts();
  const empty = document.getElementById('emptyMain');
  if(empty && ok) empty.style.display = 'none';
  const dpanels = document.getElementById('dpanels');
  if(dpanels && ok) dpanels.style.display = '';
  const devActBar = document.getElementById('devActBar');
  if(devActBar && ok) devActBar.style.display = '';
  _discClose();
}
