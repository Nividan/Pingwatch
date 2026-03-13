// ── App state ────────────────────────────────────────────────────
const S={devices:{},sensors:{},logs:{},charts:{},devTraps:{},role:'viewer'};
let sse;

// ── Clock ────────────────────────────────────────────────────────
setInterval(()=>document.getElementById('clk').textContent=new Date().toLocaleTimeString('en-GB'),500);

// ── SSE ──────────────────────────────────────────────────────────
function connectSSE(){
  if(sse)sse.close();
  sse=new EventSource('/events');
  sse.onopen=()=>document.getElementById('cbn').style.display='none';
  sse.addEventListener('sensor',e=>{
    const d=JSON.parse(e.data);
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
    const d=JSON.parse(e.data);
    if(S.devices[d.did])S.devices[d.did].status=d.status;
    updateCardStatus(d.did,d.status);
    updateSbDevDot(d.did,d.status);
    updatePills();
    if(typeof _dwOnDeviceUpdate==='function') _dwOnDeviceUpdate();
  });
  sse.addEventListener('log',e=>{
    const d=JSON.parse(e.data);
    pushLog(d.did,d.sid,d.msg,d.type);
  });
  sse.addEventListener('flap',e=>{
    const d=JSON.parse(e.data);
    pushFlap(d);
  });
  sse.addEventListener('flap_down',e=>{
    const d=JSON.parse(e.data); d._direction='down'; pushFlap(d);
    if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();
  });
  sse.addEventListener('flap_recovered',e=>{
    const d=JSON.parse(e.data); d._direction='recovered'; pushFlap(d);
    if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();
  });
  sse.addEventListener('threshold_warning',e=>{
    const d=JSON.parse(e.data); pushThresholdEvent(d,'warn');
  });
  sse.addEventListener('threshold_critical',e=>{
    const d=JSON.parse(e.data); pushThresholdEvent(d,'crit');
  });
  sse.addEventListener('snmp_trap',e=>{
    const d=JSON.parse(e.data); d._direction='trap'; pushFlap(d);
    pushDevTrap(d);
  });
  sse.onerror=()=>{
    document.getElementById('cbn').style.display='block';
    setTimeout(connectSSE,3000);
  };
}
// connectSSE() is called by onAuthenticated() after login check

// ── Toast ────────────────────────────────────────────────────────
function toast(msg,type='info'){
  const el=document.getElementById('toast'),d=document.createElement('div');
  d.className=`ti ${type}`;d.textContent=msg;el.appendChild(d);
  setTimeout(()=>d.remove(),3200);
}

// ── Auth ─────────────────────────────────────────────────────────
function showLogin(msg){
  document.getElementById('login-screen').style.display='flex';
  document.getElementById('login-err').style.display = msg ? 'block' : 'none';
  document.getElementById('login-err').textContent = msg || '';
  document.getElementById('login-btn').disabled=false;
  setTimeout(()=>document.getElementById('login-user')?.focus(),80);
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
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:user,password:pass})});
    const d=await r.json();
    if(!r.ok||d.error){showLogin(d.error||'Login failed.');btn.textContent='Sign In';return;}
    hideLogin();
    onAuthenticated(d.username);
  }catch(e){showLogin('Server error. Try again.');btn.textContent='Sign In';}
}
async function doLogout(){
  await fetch('/api/logout',{method:'POST'});
  document.getElementById('tb-user').style.display='none';
  document.getElementById('btnLogout').style.display='none';
  document.getElementById('btnSettings').style.display='none';
  document.getElementById('btnAdd').style.display='none';
  document.getElementById('btnAddGroup').style.display='none';
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
}
function applyRbac(){
  const op    = S.role==='operator'||S.role==='admin';
  const admin = S.role==='admin';
  document.querySelectorAll('.rbac-op').forEach(el=>el.style.display=op?'':'none');
  document.querySelectorAll('.rbac-admin').forEach(el=>el.style.display=admin?'':'none');
  // Add buttons are Devices-tab-only — re-apply after rbac
  const _devTab = activeMainTab==='devices';
  document.getElementById('btnAdd').style.display      = (op&&_devTab)?'':'none';
  document.getElementById('btnAddGroup').style.display = (op&&_devTab)?'':'none';
}
async function checkAuth(){
  // Hide UI controls until we confirm auth
  document.getElementById('btnAdd').style.display='none';
  document.getElementById('btnAddGroup').style.display='none';
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
  let u=0,d=0,w=0,i=0;
  Object.values(S.devices).forEach(v=>{
    const s=v.status;
    if(s==='up')u++;else if(s==='down')d++;else if(s==='warn')w++;else i++;
  });
  document.getElementById('pu').textContent=u;
  document.getElementById('pd').textContent=d;
  document.getElementById('pw').textContent=w;
  document.getElementById('pi').textContent=i;
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
    const dispName=isTrap?(d.dname||d.src_ip||'Unknown'):(esc(d.dname||''));
    const dispSub =isTrap?('◎ '+(d.trap_oid?d.trap_oid.split('.').slice(-4).join('.'):'trap')):(ico+' '+esc(d.sname||''));
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
  const _canWrite=S.role==='operator'||S.role==='admin';
  document.getElementById('btnAdd').style.display      =(tab==='devices'&&_canWrite)?'':'none';
  document.getElementById('btnAddGroup').style.display =(tab==='devices'&&_canWrite)?'':'none';
  document.getElementById('tabDashboard').classList.toggle('active',tab==='dashboard');
  document.getElementById('tabDevices').classList.toggle('active',tab==='devices');
  document.getElementById('tabEvents').classList.toggle('active',tab==='events');
  document.getElementById('tabMap').classList.toggle('active',tab==='map');
  const dashboardView=document.getElementById('dashboardView');
  const eventsView   =document.getElementById('eventsView');
  const mapView      =document.getElementById('mapView');
  const emptyMain    =document.getElementById('emptyMain');
  const dpanels      =document.getElementById('dpanels');
  dashboardView.style.display='none';
  eventsView.style.display   ='none';
  mapView.style.display      ='none';
  if(tab==='dashboard'){
    dashboardView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    if(typeof stopMap==='function') stopMap();
    if(typeof _dwRenderAll==='function') _dwRenderAll();
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
    else if(mf&&mf.contentWindow) mf.contentWindow.postMessage({type:'pw_reload_pages'},'*');
    mf?.contentWindow?.postMessage({type:'ntm_resume'},'*');
    if(typeof startMap==='function') startMap();
  } else {
    const hasDevices=Object.keys(S.devices).length>0;
    emptyMain.style.display=hasDevices?'none':'flex';
    dpanels.style.display=hasDevices?'':'none';
    if(typeof stopMap==='function') stopMap();
    document.getElementById('map-frame')?.contentWindow?.postMessage({type:'ntm_pause'},'*');
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
  if(data.devices.length){
    document.getElementById('emptyMain').style.display='none';
    if(activeMainTab==='devices') document.getElementById('dpanels').style.display='';
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
  }
}
// App bootstrap — check session before doing anything
checkAuth();