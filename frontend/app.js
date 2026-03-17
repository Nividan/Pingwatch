// ── App state ────────────────────────────────────────────────────
const S={devices:{},sensors:{},logs:{},charts:{},devTraps:{},role:'viewer'};
let sse;
let _sseFirstConnect = true;  // false after first successful open → reconnects trigger resync
let _reconnectTimer  = null;  // guard: only one pending reconnect at a time

// ── Clock ────────────────────────────────────────────────────────
setInterval(()=>document.getElementById('clk').textContent=new Date().toLocaleTimeString('en-GB'),500);

// ── SSE helpers ──────────────────────────────────────────────────
function _parseSSE(e){
  try{ return JSON.parse(e.data); }
  catch(err){ console.warn('SSE parse error',err,e.data); return null; }
}

// ── SSE ──────────────────────────────────────────────────────────
function connectSSE(){
  if(sse)sse.close();
  sse=new EventSource('/events');
  sse.onopen=()=>{
    document.getElementById('cbn').style.display='none';
    if(_sseFirstConnect){ _sseFirstConnect=false; return; }
    // Reconnect after drop — re-sync state and refresh widgets
    _sseResync();
  };
  sse.addEventListener('sensor',e=>{
    const d=_parseSSE(e); if(!d) return;
    const _key=`${d.device_id}/${d.sensor_id}`;
    if(!S.sensors[_key]) return;  // sensor was deleted — ignore stale probe event
    S.sensors[_key]=d;
    updateTile(d);
    updateCardSensor(d);
    recalcDevStatus(d.device_id);
    updatePills();
    updateSbSensorDot(d);
    if(typeof _dwOnSensorUpdate==='function') _dwOnSensorUpdate(d.device_id, d.sensor_id);
  });
  sse.addEventListener('device_status',e=>{
    const d=_parseSSE(e); if(!d) return;
    if(S.devices[d.did])S.devices[d.did].status=d.status;
    updateCardStatus(d.did,d.status);
    updateSbDevDot(d.did,d.status);
    updatePills();
    if(typeof _dwOnDeviceUpdate==='function') _dwOnDeviceUpdate();
  });
  sse.addEventListener('log',e=>{
    const d=_parseSSE(e); if(!d) return;
    pushLog(d.did,d.sid,d.msg,d.type);
  });
  sse.addEventListener('flap',e=>{
    const d=_parseSSE(e); if(!d) return;
    pushFlap(d);
  });
  sse.addEventListener('flap_down',e=>{
    const d=_parseSSE(e); if(!d) return; d._direction='down'; pushFlap(d);
    if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();
  });
  sse.addEventListener('flap_recovered',e=>{
    const d=_parseSSE(e); if(!d) return; d._direction='recovered'; pushFlap(d);
    if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();
  });
  sse.addEventListener('threshold_warning',e=>{
    const d=_parseSSE(e); if(!d) return; pushThresholdEvent(d,'warn');
  });
  sse.addEventListener('threshold_critical',e=>{
    const d=_parseSSE(e); if(!d) return; pushThresholdEvent(d,'crit');
  });
  sse.addEventListener('snmp_trap',e=>{
    const d=_parseSSE(e); if(!d) return; d._direction='trap'; pushFlap(d);
    pushDevTrap(d);
  });
  sse.addEventListener('backup_complete',e=>{
    const d=_parseSSE(e); if(!d) return;
    if(typeof _bkOnBackupComplete==='function') _bkOnBackupComplete(d);
  });
  sse.onerror=()=>{
    document.getElementById('cbn').style.display='block';
    // Guard: onerror can fire multiple times (browser retries) before the timer fires.
    // Only schedule one reconnect attempt at a time to avoid a reconnect storm.
    if(!_reconnectTimer)
      _reconnectTimer=setTimeout(()=>{ _reconnectTimer=null; connectSSE(); },3000);
  };
}
// ── Re-sync after SSE reconnect ──────────────────────────────────
// Generation counter: each new _sseResync() call increments this so that any
// in-flight retry chain from a previous call will self-cancel if superseded.
let _resyncGen = 0;
async function _sseResync(retryCount = 0, gen = ++_resyncGen) {
  // Re-fetch full device/sensor state so S stays consistent after server restarts
  // Retries up to 5× (every 3 s) if the server just restarted and has no devices yet
  if (gen !== _resyncGen) return;  // a newer call took over — abort this chain
  try {
    const r = await fetch('/api/devices');
    if (!r.ok) {
      if (retryCount < 5) setTimeout(() => _sseResync(retryCount + 1, gen), 3000);
      return;
    }
    const data = await r.json();
    if (!data.devices.length && retryCount < 5) {
      setTimeout(() => _sseResync(retryCount + 1, gen), 3000);
      return;
    }
    data.devices.forEach(dev => {
      S.devices[dev.device_id] = dev;
      dev.sensors.forEach(s => {
        S.sensors[`${dev.device_id}/${s.sensor_id}`] = s;
      });
    });
    updatePills();
  } catch {
    if (retryCount < 5) setTimeout(() => _sseResync(retryCount + 1, gen), 3000);
    return;
  }
  // Refresh dashboard widgets in-place without full DOM rebuild
  if (activeMainTab === 'dashboard' && typeof _dwLoad === 'function' && typeof _DW_REG !== 'undefined') {
    _dwLoad().forEach(w => { const r = _DW_REG[w.type]; if (r) r.refresh(w.id, w.cfg); });
  }
  // Refresh backups table if that tab is open
  if (activeMainTab === 'backups' && typeof _bkInit === 'function') {
    _bkInited = false;
    _bkInit();
  }
}

// ── Resync when user returns to tab after being away ─────────────
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  // If SSE dropped while the tab was hidden, reconnect (resync fires via onopen)
  if (!sse || sse.readyState === EventSource.CLOSED) {
    _sseFirstConnect = false;  // treat as reconnect so resync runs on open
    connectSSE();
  } else {
    // SSE still open but data may be stale — full state refresh immediately
    _sseResync();
  }
});

// connectSSE() is called by onAuthenticated() after login check

// ── Toast ────────────────────────────────────────────────────────
function toast(msg,type='info'){
  const el=document.getElementById('toast'),d=document.createElement('div');
  d.className=`ti ${type}`;d.textContent=msg;el.appendChild(d);
  setTimeout(()=>d.remove(),3200);
}

// ── Auth ─────────────────────────────────────────────────────────
function showLogin(msg, keepInput){
  document.getElementById('login-screen').style.display='flex';
  const errEl=document.getElementById('login-err');
  errEl.style.display = msg ? 'block' : 'none';
  errEl.textContent = msg || '';
  errEl.classList.toggle('info', !!keepInput);
  if(!keepInput){
    document.getElementById('login-btn').disabled=false;
    setTimeout(()=>document.getElementById('login-user')?.focus(),80);
  }
}
function hideLogin(){
  document.getElementById('login-screen').style.display='none';
}
async function submitLogin(){
  const user=(document.getElementById('login-user').value||'').trim();
  const pass=document.getElementById('login-pass').value||'';
  if(!user||!pass){showLogin('Please enter username and password.');return;}
  const btn=document.getElementById('login-btn');
  btn.disabled=true; btn.textContent='Signing in…';
  // Show a hint if the server is taking long (e.g. loading a large DB after import)
  const slowHint=setTimeout(()=>showLogin('Server is starting up — please wait…',true),6000);
  try{
    const ctrl=new AbortController();
    const tmo=setTimeout(()=>ctrl.abort(),90000); // 90 s max (large DB can take ~40 s to load)
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user,password:pass}),signal:ctrl.signal});
    clearTimeout(tmo);
    const d=await r.json();
    if(!r.ok||d.error){showLogin(d.error||'Login failed.');btn.textContent='Sign In';return;}
    hideLogin();
    onAuthenticated(d.username);
  }catch(e){
    const msg=e.name==='AbortError'?'Server is taking too long — it may still be loading. Try again.':'Server error. Try again.';
    showLogin(msg);btn.textContent='Sign In';
  }finally{clearTimeout(slowHint);}
}
async function doLogout(){
  await fetch('/api/logout',{method:'POST'});
  document.getElementById('tb-user').style.display='none';
  document.getElementById('btnLogout').style.display='none';
  document.getElementById('btnSettings').style.display='none';
  document.getElementById('devActBar').style.display='none';
  showLogin();
}
function onAuthenticated(username){
  const u=document.getElementById('tb-user');
  u.textContent='👤 '+username; u.style.display='';
  document.getElementById('btnLogout').style.display='';
  document.getElementById('btnSettings').style.display='';
  applyRbac();
  loadAll();
  connectSSE();
  // Refresh health bar sparkline every 5 min (clear old interval to prevent duplicates on re-login)
  if (_hbSparkInterval) clearInterval(_hbSparkInterval);
  _hbSparkInterval = setInterval(()=>{ _hbSparkLoaded=false; _hbDrawSpark(); }, 300000);
}
function applyRbac(){
  const op    = S.role==='operator'||S.role==='admin';
  const admin = S.role==='admin';
  document.querySelectorAll('.rbac-op').forEach(el=>el.style.display=op?'':'none');
  document.querySelectorAll('.rbac-admin').forEach(el=>el.style.display=admin?'':'none');
}
async function checkAuth(){
  // Hide UI controls until we confirm auth
  document.getElementById('btnSettings').style.display='none';
  document.getElementById('btnLogout').style.display='none';
  try{
    const r=await fetch('/api/me');
    if(r.ok){const d=await r.json(); S.role=d.role||'viewer'; onAuthenticated(d.username);}
    else{showLogin();}
  }catch(e){showLogin();}
}
// Enter key on login inputs
['login-user','login-pass'].forEach(id=>{
  document.getElementById(id)?.addEventListener('keydown',e=>{if(e.key==='Enter')submitLogin();});
});

// ── API ──────────────────────────────────────────────────────────
async function api(method,path,body=null){
  const o={method,headers:{'Content-Type':'application/json'}};
  if(body)o.body=JSON.stringify(body);
  const r=await fetch(path,o);
  if(r.status===401){showLogin('Session expired. Please sign in again.');return {};}
  return r.json();
}

// ── Pills ────────────────────────────────────────────────────────
function updatePills(){
  _hbUpdate();
}

// ── Global Network Health Bar ─────────────────────────────────────
let _hbSparkLoaded = false;
let _hbSparkInterval = null;
function _hbUpdate() {
  const devs = Object.values(S.devices);
  if (!devs.length) return;
  const bar = document.getElementById('healthBar');
  if (!bar) return;
  bar.style.display = '';
  const up  = devs.filter(d => d.status === 'up').length;
  const dn  = devs.filter(d => d.status === 'down').length;
  const wn  = devs.filter(d => d.status === 'warn').length;
  const tot = devs.length;
  const pct = Math.round(up / tot * 100);
  const cls = pct >= 90 ? 'healthy' : pct >= 70 ? 'degraded' : 'critical';
  const lbl = pct >= 90 ? 'Healthy' : pct >= 70 ? 'Degraded' : 'Critical';
  const fill = document.getElementById('hb-bar-fill');
  if (fill) { fill.style.width = pct + '%'; fill.className = 'hb-fill-' + cls; }
  const pctEl = document.getElementById('hb-pct');
  if (pctEl) { pctEl.textContent = pct + '%'; pctEl.className = 'hb-pct-' + cls; }
  const lblEl = document.getElementById('hb-label');
  if (lblEl) { lblEl.textContent = lbl; lblEl.className = 'hb-lbl-' + cls; }
  const upEl = document.getElementById('hb-up');
  const dnEl = document.getElementById('hb-dn');
  const wnEl = document.getElementById('hb-wn');
  if (upEl) upEl.textContent = up + ' Up';
  if (dnEl) { dnEl.textContent = dn + ' Down'; dnEl.style.display = dn ? '' : 'none'; }
  if (wnEl) { wnEl.textContent = wn + ' Warn'; wnEl.style.display = wn ? '' : 'none'; }
  if (!_hbSparkLoaded) { _hbSparkLoaded = true; _hbDrawSpark(); }
}
async function _hbDrawSpark() {
  const canvas = document.getElementById('hb-spark');
  if (!canvas) return;
  try {
    const r = await fetch('/api/availability?minutes=1440');
    const d = await r.json();
    const pts = d.availability || [];
    if (!pts.length) return;
    // Use rAF to ensure the canvas is laid out before reading offsetWidth
    await new Promise(res => requestAnimationFrame(res));
    canvas.width = canvas.offsetWidth || 120;
    const W = canvas.width, H = canvas.height || 20;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, W, H);
    const avg = pts.reduce((s, b) => s + b.pct, 0) / pts.length;
    const color = avg >= 90 ? '#23d18b' : avg >= 70 ? '#f0a500' : '#f85149';
    const now = Date.now() / 1000, win = 1440 * 60;
    const xOf = ts => (ts - (now - win)) / win * W;
    const yOf = pct => H - (Math.min(100, Math.max(0, pct)) / 100) * H;
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = xOf(p.ts + 1800), y = yOf(p.pct);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
    // Only show the canvas (and its label) after it has content
    canvas.style.display = '';
    const lbl = document.getElementById('hb-spark-lbl');
    if (lbl) lbl.style.display = '';
  } catch (e) { /* non-critical */ }
}

// ── Events / Flap log ────────────────────────────────────────────
const FLAPS=[];   // newest first; size controlled by MAX_FLAPS
const _FLAP_SEEN=new Set(); // dedup keys to prevent API+SSE duplicates
let MAX_FLAPS=20;
let unseenFlaps=0;

function _flapKey(d){
  // Unique key: device+sensor+timestamp+direction (covers both flaps and traps)
  return (d.did||d.src_ip||'')+'|'+(d.sid||d.trap_oid||'')+'|'+(d.ts||'')+'|'+(d._direction||d.direction||'');
}
let activeMainTab=(()=>{try{return localStorage.getItem('pw_tab')||'devices';}catch{return 'devices';}})();
// Apply correct tab button immediately — synchronous, no network request needed
document.getElementById('tab'+activeMainTab[0].toUpperCase()+activeMainTab.slice(1))?.classList.add('active');

function pushFlap(d){
  const k=_flapKey(d); if(_FLAP_SEEN.has(k)) return; _FLAP_SEEN.add(k);
  FLAPS.unshift(d);
  if(FLAPS.length>MAX_FLAPS) FLAPS.pop();
  renderFlaps();
  if(activeMainTab!=='events'){
    unseenFlaps++;
    const badge=document.getElementById('evtBadge');
    if(badge){ badge.style.display=''; badge.textContent=unseenFlaps; }
  }
  flashDownPill();
}

function pushThresholdEvent(d, level){
  const entry=Object.assign({},d,{_direction:'threshold',_thr_level:level});
  const k=_flapKey(entry); if(_FLAP_SEEN.has(k)) return; _FLAP_SEEN.add(k);
  FLAPS.unshift(entry);
  if(FLAPS.length>MAX_FLAPS)FLAPS.pop();
  renderFlaps();
  if(activeMainTab!=='events'){
    unseenFlaps++;
    const badge=document.getElementById('evtBadge');
    if(badge){badge.style.display='';badge.textContent=unseenFlaps;}
  }
}

function renderFlaps(){
  if(typeof _renderEvtView==='function'){ _renderEvtView(); return; }
  const list=document.getElementById('evtList');
  if(!list) return;
  if(!FLAPS.length){
    list.innerHTML='<div class="evt-empty">No flaps recorded yet.</div>';
    return;
  }
  list.innerHTML='';
  FLAPS.forEach(d=>{
    const row=document.createElement('div');
    row.className='evt-row';
    const [date,time]=(d.ts||'').split(' ');
    const ico={ping:'◉',tcp:'⇌',http:'◈',snmp:'◎',dns:'⬡',tls:'T',http_keyword:'K',banner:'B'}[d.stype]||'�?';
    let dotStyle='background:var(--down);box-shadow:0 0 6px rgba(248,81,73,.8)';
    let label='DOWN';
    if(d._direction==='recovered'){
      dotStyle='background:var(--up);box-shadow:0 0 6px rgba(35,209,139,.8)'; label='RECOVERED';
    } else if(d._direction==='threshold'){
      const isCrit=d._thr_level==='crit';
      dotStyle=isCrit?'background:#e74c3c':'background:#f39c12';
      label=isCrit?'CRIT':'WARN';
    } else if(d._direction==='trap'){
      dotStyle='background:#8e44ad;box-shadow:0 0 6px rgba(142,68,173,.8)'; label='TRAP';
    }
    const isTrap=d._direction==='trap';
    const dispName=isTrap?esc(d.dname||d.src_ip||'Unknown'):esc(d.dname||'');
    const dispSub =isTrap?('◎ '+esc(d.trap_oid?d.trap_oid.split('.').slice(-4).join('.'):'trap')):(ico+' '+esc(d.sname||''));
    const dispHost=isTrap?esc(d.src_ip||''):esc(d.host||'');
    const dispTime=d.ts?(typeof fmtTs==='function'?fmtTs(d.ts):d.ts.split('T')[1]||d.ts):(time||'');
    const dispDate=d.ts?(d.ts.split('T')[0]||''):(date||'');
    row.innerHTML=
      '<div class="evt-top">'+
        `<div class="evt-dot" style="${dotStyle}"></div>`+
        '<div class="evt-name">'+dispName+' · '+dispSub+
          ' <span style="font-size:12px;font-weight:700;color:var(--text2)">['+label+']</span></div>'+
        '<div class="evt-time">'+dispTime+'</div>'+
      '</div>'+
      '<div class="evt-host">'+dispHost+'</div>'+
      '<div class="evt-detail">'+esc(d.detail||'')+'</div>'+
      '<div class="evt-time" style="padding-left:16px;font-size:12px;color:var(--text2)">'+dispDate+'</div>';
    list.appendChild(row);
  });
}

function switchMainTab(tab){
  activeMainTab=tab;
  try{localStorage.setItem('pw_tab',tab);}catch(e){}
  document.getElementById('tabDashboard').classList.toggle('active',tab==='dashboard');
  document.getElementById('tabDevices').classList.toggle('active',tab==='devices');
  document.getElementById('tabEvents').classList.toggle('active',tab==='events');
  document.getElementById('tabMap').classList.toggle('active',tab==='map');
  document.getElementById('tabBackups').classList.toggle('active',tab==='backups');
  const dashboardView=document.getElementById('dashboardView');
  const eventsView   =document.getElementById('eventsView');
  const mapView      =document.getElementById('mapView');
  const backupsView  =document.getElementById('backupsView');
  const emptyMain    =document.getElementById('emptyMain');
  const dpanels      =document.getElementById('dpanels');
  dashboardView.style.display='none';
  eventsView.style.display   ='none';
  mapView.style.display      ='none';
  backupsView.style.display  ='none';
  document.getElementById('devActBar').style.display='none';
  if(tab==='dashboard'){
    dashboardView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    if(typeof stopMap==='function') stopMap();
    if(typeof _dwInit==='function') _dwInit();
  } else if(tab==='events'){
    eventsView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    unseenFlaps=0;
    const badge=document.getElementById('evtBadge');
    if(badge) badge.style.display='none';
    if(typeof stopMap==='function') stopMap();
    _refreshEvents();
  } else if(tab==='map'){
    emptyMain.style.display='none';
    dpanels.style.display='none';
    mapView.style.display='flex';
    const mf=document.getElementById('map-frame');
    if(mf&&!mf.src&&mf.dataset.src) mf.src=mf.dataset.src;
    else if(mf&&mf.contentWindow) mf.contentWindow.postMessage({type:'pw_reload_pages'},window.location.origin);
    mf?.contentWindow?.postMessage({type:'ntm_resume'},window.location.origin);
    if(typeof startMap==='function') startMap();
  } else if(tab==='backups'){
    backupsView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    if(typeof stopMap==='function') stopMap();
    if(typeof _bkInit==='function') _bkInit();
  } else {
    const hasDevices=Object.keys(S.devices).length>0;
    document.getElementById('devActBar').style.display='';
    emptyMain.style.display=hasDevices?'none':'flex';
    dpanels.style.display=hasDevices?'':'none';
    if(typeof stopMap==='function') stopMap();
    document.getElementById('map-frame')?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    _refreshDevices();
  }
}

async function _refreshDevices(){
  try{
    const data=await (await fetch('/api/devices')).json();
    data.devices.forEach(dev=>{
      S.devices[dev.device_id]=dev;
      dev.sensors.forEach(s=>{ S.sensors[`${dev.device_id}/${s.sensor_id}`]=s; });
      renderDp(dev);
    });
    updatePills();
    renderSidebar();
  }catch(e){}
}

async function _refreshEvents(){
  try{
    FLAPS.length=0; _FLAP_SEEN.clear();
    const [fd,td]=await Promise.all([
      fetch('/api/flaps').then(r=>r.json()),
      fetch('/api/traps').then(r=>r.json()),
    ]);
    (fd.flaps||[]).forEach(f=>{ f._direction=f.direction||'down'; _FLAP_SEEN.add(_flapKey(f)); FLAPS.push(f); });
    (td.traps||[]).forEach(t=>{ t._direction='trap'; _FLAP_SEEN.add(_flapKey(t)); FLAPS.push(t); });
    FLAPS.sort((a,b)=>new Date(b.ts)-new Date(a.ts));
    renderFlaps();
    if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();
    _loadTrapFilters();
  }catch(e){}
}

async function _loadTrapFilters(){
  try{
    const [vd, cd] = await Promise.all([
      fetch('/api/traps/vendors').then(r=>r.json()),
      fetch('/api/traps/categories').then(r=>r.json()),
    ]);
    const vSel = document.getElementById('evtFVendor');
    const cSel = document.getElementById('evtFCat');
    if(vSel && vd.vendors){
      vd.vendors.forEach(v=>{
        const o=document.createElement('option'); o.value=v; o.textContent=v; vSel.appendChild(o);
      });
    }
    if(cSel && cd.categories){
      cd.categories.forEach(c=>{
        const o=document.createElement('option'); o.value=c.name; o.textContent=c.label; cSel.appendChild(o);
      });
    }
  }catch(e){}
}

function toggleSidebar(){
  const collapsed=document.body.classList.toggle('sidebar-collapsed');
  localStorage.setItem('sidebarCollapsed', collapsed);
}
// Restore sidebar state across sessions
if(localStorage.getItem('sidebarCollapsed')==='true'){
  document.body.classList.add('sidebar-collapsed');
}

function flashDownPill(){
  const pill=document.querySelector('.stpill.down');
  if(!pill) return;
  pill.style.transition='none';
  pill.style.boxShadow='0 0 14px rgba(248,81,73,.8)';
  setTimeout(()=>{ pill.style.transition=''; pill.style.boxShadow=''; },600);
}

function pushDevTrap(d){
  const dev=Object.values(S.devices).find(v=>v.host===d.src_ip);
  if(!dev) return;
  const did=dev.device_id;
  if(!S.devTraps[did]) S.devTraps[did]=[];
  const oid=d.trap_oid?d.trap_oid.split('.').slice(-4).join('.'):'?';
  const ts=d.ts||new Date().toISOString();
  S.devTraps[did].unshift({ts,msg:'[TRAP] '+oid+(d.detail?' · '+d.detail:''),type:'trap'});
  if(S.devTraps[did].length>200) S.devTraps[did].pop();
  maybeUpdateDevLog(did);
}

// ── Load ─────────────────────────────────────────────────────────
async function loadAll(){
  // Restore saved tab immediately — before any rendering to prevent flash
  try{const t=localStorage.getItem('pw_tab');if(t&&t!=='devices')switchMainTab(t);}catch(e){}

  // Fetch settings early to configure globals
  try {
    const _sr = await (await fetch('/api/settings')).json();
    MAX_FLAPS = _sr.max_flaps_display || 20;
    window._lGood = _sr.latency_good_ms || 100;
    window._lWarn = _sr.latency_warn_ms || 300;
    window._snrDef = {
      interval:     _sr.snr_interval     || 5,
      timeout:      _sr.snr_timeout      || 4,
      fail_after:   _sr.snr_fail_after   || 1,
      recover_after:_sr.snr_recover_after|| 1,
    };
    window._snrTypeDefaults = _sr.snr_type_defaults || {};
    const orgName = (_sr.org_name || '').trim();
    const el=document.getElementById('tbVer');
    if(orgName){
      if(el) el.textContent=orgName;
      document.title='PingWatch \u2014 '+orgName;
    } else {
      try {
        const _vi=await (await fetch('/api/server_info')).json();
        if(el && _vi.version) el.textContent='Network Monitor v'+_vi.version;
        window._pwVersion=_vi.version||'';
      } catch(_){}
    }
  } catch(e){}

  // Load saved flap events from DB
  try {
    const fr=await fetch('/api/flaps');
    const fd=await fr.json();
    (fd.flaps||[]).forEach(f=>{ f._direction=f.direction||'down'; _FLAP_SEEN.add(_flapKey(f)); FLAPS.push(f); });
  } catch(e){}
  try {
    const tr=await fetch('/api/traps');
    const td=await tr.json();
    (td.traps||[]).forEach(t=>{ t._direction='trap'; _FLAP_SEEN.add(_flapKey(t)); FLAPS.push(t); });
  } catch(e){}
  // Sort combined list newest-first and cap size
  FLAPS.sort((a,b)=>new Date(b.ts)-new Date(a.ts));
  renderFlaps();
  if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();

  const r=await fetch('/api/devices');
  const data=await r.json();
  data.devices.forEach(dev=>{
    S.devices[dev.device_id]=dev;
    dev.sensors.forEach(s=>{
      S.sensors[`${dev.device_id}/${s.sensor_id}`]=s;
      S.logs[`${dev.device_id}/${s.sensor_id}`]=[];
    });
    renderDp(dev);
  });
  if(activeMainTab==='devices'){
    document.getElementById('devActBar').style.display='';
  }
  if(data.devices.length){
    document.getElementById('emptyMain').style.display='none';
    if(activeMainTab==='devices'){
      document.getElementById('dpanels').style.display='';
    }
  }
  renderFlaps(); // re-render events now that S.devices (groups) is populated
  updatePills();
  restoreGroupOrder();
  renderSidebar();
  // Backfill per-device trap log from historical FLAPS (devices now loaded)
  FLAPS.filter(f=>f._direction==='trap').forEach(t=>{
    const dev=Object.values(S.devices).find(v=>v.host===t.src_ip);
    if(dev){
      const did=dev.device_id;
      if(!S.devTraps[did]) S.devTraps[did]=[];
      const oid=t.trap_oid?t.trap_oid.split('.').slice(-4).join('.'):'?';
      S.devTraps[did].push({ts:t.ts,msg:'[TRAP] '+oid+(t.detail?' · '+t.detail:''),type:'trap'});
    }
  });
  Object.values(S.devTraps).forEach(arr=>arr.sort((a,b)=>new Date(b.ts)-new Date(a.ts)));
  // Ensure devices tab shows panels if it became active after loading
  if(activeMainTab==='devices' && Object.keys(S.devices).length){
    document.getElementById('dpanels').style.display='';
    document.getElementById('devActBar').style.display='';
  }
}
// App bootstrap — check session before doing anything
checkAuth();