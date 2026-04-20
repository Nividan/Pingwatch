// ── Timing constants (single source of truth) ───────────────────
const TIMINGS = Object.freeze({
  SSE_BATCH_INTERVAL:   250,    // ms — coalesce SSE events
  CLOCK_UPDATE:         1000,   // ms — header clock tick
  RECONNECT_INITIAL:    3000,   // ms — initial SSE reconnect delay
  RECONNECT_MAX:        60000,  // ms — exponential backoff cap
  SPARK_REFRESH:        300000, // ms — sparkline refresh (5 min)
  ALERT_BADGE_POLL:     60000,  // ms — alert badge poll
  IDLE_CHECK:           30000,  // ms — idle session check
});

// ── App state ────────────────────────────────────────────────────
const S={devices:{},sensors:{},logs:{},charts:{},devTraps:{},role:'viewer',_devSensors:{}};
let _loggedOut=false;  // set during intentional logout to suppress "session expired"
let sse;
let _sseFirstConnect = true;  // false after first successful open → reconnects trigger resync
let _reconnectTimer  = null;  // guard: only one pending reconnect at a time
let _reconnectDelay  = TIMINGS.RECONNECT_INITIAL; // exponential backoff: 3s → 6s → 12s → … → 60s cap

// ── SSE batching: coalesce events into 250ms windows to reduce DOM mutations ──
const _sseBatch={sensors:{},devStatuses:{},timer:null,INTERVAL:TIMINGS.SSE_BATCH_INTERVAL};
let _sseHidden=false;

function _sseFlush(){
  _sseBatch.timer=null;
  const devIds=new Set();
  // Per-sensor DOM updates
  for(const key in _sseBatch.sensors){
    const d=_sseBatch.sensors[key];
    updateTile(d); updateCardSensor(d);
    devIds.add(d.device_id);
  }
  // Per-device-status DOM updates
  for(const did in _sseBatch.devStatuses){
    const st=_sseBatch.devStatuses[did];
    updateCardStatus(did,st);
    devIds.add(did);
  }
  // Once per batch (not per event)
  devIds.forEach(did=>recalcDevStatus(did));
  if(devIds.size) updatePills();
  // Dashboard hooks (once per batch)
  if(typeof _dwOnSensorUpdate==='function'){
    for(const key in _sseBatch.sensors){
      const d=_sseBatch.sensors[key];
      _dwOnSensorUpdate(d.device_id,d.sensor_id);
    }
  }
  if(typeof _dwOnDeviceUpdate==='function'&&Object.keys(_sseBatch.devStatuses).length)
    _dwOnDeviceUpdate();
  _sseBatch.sensors={};
  _sseBatch.devStatuses={};
}
function _sseSchedule(){
  if(!_sseBatch.timer) _sseBatch.timer=setTimeout(_sseFlush,_sseBatch.INTERVAL);
}

// ── Clock ────────────────────────────────────────────────────────
setInterval(()=>document.getElementById('clk').textContent=new Date().toLocaleTimeString('en-GB'),1000);

// ── SSE refresh coalescing (prevents flood during flap storms) ───
let _refreshTimer=null;
function _scheduleRefresh(){
  if(_refreshTimer) return;
  _refreshTimer=setTimeout(()=>{
    _refreshTimer=null;
    if(typeof _refreshAlertCache==='function') _refreshAlertCache();
    _refreshFlapList();
  },3000);
}

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
    _reconnectDelay=3000;  // reset backoff on successful connect
    if(_sseFirstConnect){ _sseFirstConnect=false; return; }
    // Reconnect after drop — re-sync state and refresh widgets
    _sseResync();
  };
  sse.addEventListener('sensor',e=>{
    const d=_parseSSE(e); if(!d) return;
    const _key=`${d.device_id}/${d.sensor_id}`;
    if(!S.sensors[_key]) return;
    S.sensors[_key]=d;
    // Maintain device→sensor index
    if(!S._devSensors[d.device_id]) S._devSensors[d.device_id]=new Set();
    S._devSensors[d.device_id].add(_key);
    if(_sseHidden) return;  // skip DOM work when tab hidden
    _sseBatch.sensors[_key]=d;
    _sseSchedule();
  });
  sse.addEventListener('device_status',e=>{
    const d=_parseSSE(e); if(!d) return;
    if(S.devices[d.did]) S.devices[d.did].status=d.status;
    if(_sseHidden) return;
    _sseBatch.devStatuses[d.did]=d.status;
    _sseSchedule();
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
    _scheduleRefresh();
    _scheduleBadgePoll();
  });
  sse.addEventListener('flap_recovered',e=>{
    const d=_parseSSE(e); if(!d) return;
    resolveFlap(d,'down');
    if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();
    _scheduleRefresh();
    _scheduleBadgePoll();
  });
  sse.addEventListener('threshold_warning',e=>{
    const d=_parseSSE(e); if(!d) return; pushThresholdEvent(d,'warn');
    _scheduleRefresh();
    _scheduleBadgePoll();
  });
  sse.addEventListener('threshold_critical',e=>{
    const d=_parseSSE(e); if(!d) return; pushThresholdEvent(d,'crit');
    _scheduleRefresh();
    _scheduleBadgePoll();
  });
  sse.addEventListener('threshold_ok',e=>{
    const d=_parseSSE(e); if(!d) return;
    resolveFlap(d,'threshold');
    _scheduleRefresh();
    _scheduleBadgePoll();
  });
  sse.addEventListener('snmp_trap',e=>{
    const d=_parseSSE(e); if(!d) return; d._direction='trap'; pushFlap(d);
    pushDevTrap(d);
  });
  sse.addEventListener('backup_complete',e=>{
    const d=_parseSSE(e); if(!d) return;
    if(typeof _bkOnBackupComplete==='function') _bkOnBackupComplete(d);
  });
  sse.addEventListener('license_status',e=>{
    const d=_parseSSE(e); if(!d) return;
    if(typeof _ipamOnLicenseUpdate==='function') _ipamOnLicenseUpdate();
    if(activeMainTab==='dashboard'){
      _dwLoad().forEach(w=>{
        if(w.type==='license_overview') _dwLicenseOverviewRefresh(w.id);
      });
    }
  });
  sse.addEventListener('browser_notification',e=>{
    const d=_parseSSE(e); if(!d) return;
    _showBrowserNotif(d);
    _scheduleBadgePoll();
  });
  sse.addEventListener('log_badge',e=>{
    const d=_parseSSE(e); if(!d) return;
    _logBadgeTotal=d.total||0;
    _updateLogBadge();
  });
  sse.onerror=()=>{
    document.getElementById('cbn').style.display='block';
    // Guard: onerror can fire multiple times (browser retries) before the timer fires.
    // Only schedule one reconnect attempt at a time to avoid a reconnect storm.
    if(!_reconnectTimer){
      _reconnectTimer=setTimeout(()=>{ _reconnectTimer=null; connectSSE(); },_reconnectDelay);
      _reconnectDelay=Math.min(_reconnectDelay*2,TIMINGS.RECONNECT_MAX);
    }
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
    if (r.status === 401) {
      // Server restarted and cleared sessions — show login
      if (!_loggedOut) showLogin('Server restarted. Please sign in again.');
      return;
    }
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
        const _k=`${dev.device_id}/${s.sensor_id}`;
        S.sensors[_k] = s;
        if(!S._devSensors[dev.device_id]) S._devSensors[dev.device_id]=new Set();
        S._devSensors[dev.device_id].add(_k);
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
  if (document.visibilityState === 'visible') {
    _sseHidden = false;
    // Flush any pending batch immediately; always resync pills/badge in case
    // device_status events arrived while the tab was hidden (they update S.devices
    // but skip _sseBatch when _sseHidden=true, so no flush timer was set).
    if (_sseBatch.timer) { clearTimeout(_sseBatch.timer); _sseBatch.timer=null; _sseFlush(); }
    updatePills();
    _badgePoll();
    if (!sse || sse.readyState === EventSource.CLOSED) {
      _sseFirstConnect = false;
      connectSSE();
    } else {
      _sseResync();
    }
  } else {
    _sseHidden = true;
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
function _resetLoginForm(){
  // Scrub any mid-auth state (TOTP or RADIUS challenge prompt) so the next
  // login attempt starts fresh. Without this, the button's onclick handler
  // still points at the challenge-submit function after sign-out and posts
  // credentials to the wrong endpoint.
  const userField=document.getElementById('login-user');
  const passField=document.getElementById('login-pass');
  if(userField){ userField.disabled=false; }
  if(passField){ passField.style.display='block'; passField.value=''; }
  // Clean up TOTP elements
  const totpField=document.getElementById('login-totp');
  if(totpField) totpField.remove();
  const rememberRow=document.getElementById('login-remember-row');
  if(rememberRow) rememberRow.remove();
  // Clean up RADIUS challenge elements
  const radiusPrompt=document.getElementById('login-radius-prompt');
  if(radiusPrompt) radiusPrompt.remove();
  const radiusResp=document.getElementById('login-radius-resp');
  if(radiusResp) radiusResp.remove();
  // Restore default button + submit handler
  const btn=document.getElementById('login-btn');
  if(btn){
    btn.textContent='Sign In';
    btn.disabled=false;
    btn.onclick=submitLogin;
  }
}

function showLogin(msg, keepInput){
  document.getElementById('login-screen').style.display='flex';
  const errEl=document.getElementById('login-err');
  errEl.style.display = msg ? 'block' : 'none';
  errEl.textContent = msg || '';
  errEl.classList.toggle('info', !!keepInput);
  if(!keepInput){
    _resetLoginForm();
    setTimeout(()=>document.getElementById('login-user')?.focus(),80);
  }
  // Populate SSO buttons (async, non-blocking — login form is usable immediately)
  _renderSsoButtons();
}

// Fetch /api/settings (public fields only for unauthenticated callers) to
// discover which SSO methods are enabled, then render a button per method
// above the local username/password form. Idempotent — safe to call repeatedly.
async function _renderSsoButtons(){
  const container = document.getElementById('sso-methods');
  const divider   = document.getElementById('sso-divider');
  if(!container) return;
  container.innerHTML = '';
  container.style.display = 'none';
  if(divider) divider.style.display = 'none';
  let s;
  try {
    const r = await fetch('/api/settings/public_auth', {credentials: 'same-origin'});
    if(!r.ok) return;
    s = await r.json();
  } catch(e) { return; }
  const methods = [];
  if(s.saml_enabled) methods.push({id:'saml', label: s.saml_display_name || 'Sign in with Single Sign-On', href:'/api/saml/login'});
  if(s.oidc_enabled) methods.push({id:'oidc', label: s.oidc_display_name || 'Sign in with Single Sign-On', href:'/api/oidc/login'});
  if(!methods.length) return;
  const html = methods.map(m =>
    `<a href="${m.href}" class="sso-btn" id="sso-btn-${m.id}" style="display:flex;align-items:center;justify-content:center;gap:8px;padding:10px 14px;background:var(--bg3);border:1px solid var(--border2);border-radius:6px;color:var(--text);text-decoration:none;font-weight:500;font-size:13px;transition:background .1s" onmouseover="this.style.background='var(--bg4)'" onmouseout="this.style.background='var(--bg3)'"><span style="font-size:16px">🔑</span>${_esc(m.label)}</a>`
  ).join('');
  container.innerHTML = html;
  container.style.display = 'flex';
  if(divider) divider.style.display = 'block';
}

function _esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

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
    // 2FA gate: server says password OK but second factor required
    if(d.totp_required){
      btn.textContent='Sign In';
      _show2faPrompt(d.challenge_id, user, d.remember_hours_max||0);
      return;
    }
    // RADIUS Access-Challenge: server needs a second step (token code, push confirm, etc.)
    if(d.radius_challenge){
      btn.textContent='Sign In';
      _showRadiusChallengePrompt(d.challenge_id, user, d.prompt||'Additional verification required');
      return;
    }
    _loggedOut=false;
    S.role=d.role||'viewer';
    if(d.session_ttl)_sessionTtl=d.session_ttl;
    // Fetch full profile (incl. theme_preference) — /api/login doesn't return it
    try{
      const me=await fetch('/api/me').then(x=>x.ok?x.json():null);
      if(me&&me.theme_preference&&typeof setTheme==='function')setTheme(me.theme_preference,{sync:false});
    }catch(e){}
    hideLogin();
    try{localStorage.setItem('pw_tab','dashboard');}catch(e){}
    onAuthenticated(d.username);
  }catch(e){
    const msg=e.name==='AbortError'?'Server is taking too long — it may still be loading. Try again.':'Server error. Try again.';
    showLogin(msg);btn.textContent='Sign In';
  }finally{clearTimeout(slowHint);}
}
// ── Two-factor authentication prompt ─────────────────────────────
function _show2faPrompt(challengeId, username, rememberHoursMax){
  // Replace login form with TOTP input. Reuses login-screen container.
  const screen=document.getElementById('login-screen');
  if(!screen) return;
  const err=document.getElementById('login-err');
  if(err){err.textContent=''; err.style.display='none';}
  const userField=document.getElementById('login-user');
  const passField=document.getElementById('login-pass');
  if(userField){userField.disabled=true;}
  if(passField){passField.style.display='none';}
  let codeField=document.getElementById('login-totp');
  if(!codeField){
    codeField=document.createElement('input');
    codeField.type='text';
    codeField.id='login-totp';
    codeField.placeholder='6-digit code or recovery code';
    codeField.autocomplete='one-time-code';
    codeField.maxLength=20;
    codeField.style.cssText='width:100%;padding:10px;margin-top:8px;background:var(--surface-inset,#0e141a);color:var(--text);border:1px solid var(--border);border-radius:6px;font-family:monospace;letter-spacing:2px;text-align:center;';
    if(passField&&passField.parentNode){passField.parentNode.insertBefore(codeField, passField.nextSibling);}
  }
  codeField.style.display='block';
  codeField.value='';
  setTimeout(()=>codeField.focus(),50);

  // "Remember this device" row — duration is admin-controlled
  // (Settings → Security → totp_remember_hours). The login screen only
  // shows the configured value; users cannot change it.
  let rememberRow=document.getElementById('login-remember-row');
  if(rememberRow) rememberRow.remove();
  const maxHours=parseInt(rememberHoursMax||0,10);
  if(maxHours>0){
    rememberRow=document.createElement('div');
    rememberRow.id='login-remember-row';
    rememberRow.style.cssText='display:flex;align-items:center;gap:8px;margin-top:8px;font-size:13px;color:var(--text2)';
    const hoursTxt=maxHours===1?'1 hour':`${maxHours} hours`;
    rememberRow.innerHTML=
      `<input type="checkbox" id="login-remember-chk" style="cursor:pointer">`+
      `<label for="login-remember-chk" style="cursor:pointer">Remember this device for ${hoursTxt}</label>`;
    if(passField&&passField.parentNode){passField.parentNode.insertBefore(rememberRow, codeField.nextSibling);}
  }

  const btn=document.getElementById('login-btn');
  if(btn){btn.textContent='Verify'; btn.disabled=false;}
  // Override button click handler temporarily
  const submit=async()=>{
    const code=(codeField.value||'').trim();
    if(!code){_showLoginErr('Enter your 2FA code'); return;}
    btn.disabled=true; btn.textContent='Verifying…';
    const remember=maxHours>0&&!!document.getElementById('login-remember-chk')?.checked;
    try{
      const r=await fetch('/api/login/totp',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({challenge_id:challengeId, code, remember})});
      const d=await r.json();
      if(!r.ok||d.error){
        _showLoginErr(d.error||'Verification failed');
        btn.disabled=false; btn.textContent='Verify';
        return;
      }
      _loggedOut=false;
      S.role=d.role||'viewer';
      if(d.session_ttl)_sessionTtl=d.session_ttl;
      try{
        const me=await fetch('/api/me').then(x=>x.ok?x.json():null);
        if(me&&me.theme_preference&&typeof setTheme==='function')setTheme(me.theme_preference,{sync:false});
      }catch(e){}
      hideLogin();
      try{localStorage.setItem('pw_tab','dashboard');}catch(e){}
      onAuthenticated(d.username);
      // Reset login UI for next time
      if(userField){userField.disabled=false;}
      if(passField){passField.style.display='block';}
      if(codeField){codeField.style.display='none';}
      const rr=document.getElementById('login-remember-row');
      if(rr) rr.remove();
    }catch(e){
      _showLoginErr('Server error. Try again.');
      btn.disabled=false; btn.textContent='Verify';
    }
  };
  btn.onclick=submit;
  codeField.onkeydown=(e)=>{ if(e.key==='Enter'){e.preventDefault(); submit();} };
}
function _showLoginErr(msg){
  const err=document.getElementById('login-err');
  if(err){err.textContent=msg; err.style.display='block';}
}

// ── RADIUS Access-Challenge prompt ───────────────────────────────
// Rendered when the RADIUS server returns Access-Challenge (typically for 2FA).
// The server provides a human prompt ("Enter token code", "Approve push", etc.).
// Multi-step challenges keep re-rendering with a fresh prompt until accept/reject.
function _showRadiusChallengePrompt(challengeId, username, prompt){
  const screen=document.getElementById('login-screen');
  if(!screen) return;
  const err=document.getElementById('login-err');
  if(err){err.textContent=''; err.style.display='none';}
  const userField=document.getElementById('login-user');
  const passField=document.getElementById('login-pass');
  if(userField){userField.disabled=true;}
  if(passField){passField.style.display='none';}

  // Prompt label
  let promptEl=document.getElementById('login-radius-prompt');
  if(!promptEl){
    promptEl=document.createElement('div');
    promptEl.id='login-radius-prompt';
    promptEl.style.cssText='margin-top:8px;font-size:13px;color:var(--text2);text-align:center';
    if(passField&&passField.parentNode){passField.parentNode.insertBefore(promptEl, passField.nextSibling);}
  }
  promptEl.textContent=prompt||'Additional verification required';
  promptEl.style.display='block';

  // Response input (reused across multi-step challenges)
  let respField=document.getElementById('login-radius-resp');
  if(!respField){
    respField=document.createElement('input');
    respField.type='text';
    respField.id='login-radius-resp';
    respField.placeholder='Enter response';
    respField.autocomplete='one-time-code';
    respField.maxLength=64;
    respField.style.cssText='width:100%;padding:10px;margin-top:8px;background:var(--surface-inset,#0e141a);color:var(--text);border:1px solid var(--border);border-radius:6px;font-family:monospace;letter-spacing:2px;text-align:center;';
    if(promptEl&&promptEl.parentNode){promptEl.parentNode.insertBefore(respField, promptEl.nextSibling);}
  }
  respField.style.display='block';
  respField.value='';
  setTimeout(()=>respField.focus(),50);

  // Remove any TOTP-specific remember row if it exists (shouldn't, but be safe)
  const rr=document.getElementById('login-remember-row');
  if(rr) rr.remove();

  const btn=document.getElementById('login-btn');
  if(btn){btn.textContent='Verify'; btn.disabled=false;}
  const submit=async()=>{
    const response=(respField.value||'').trim();
    if(!response){_showLoginErr('Enter your response'); return;}
    btn.disabled=true; btn.textContent='Verifying…';
    try{
      const r=await fetch('/api/login/radius_challenge',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({challenge_id:challengeId, response})});
      const d=await r.json();
      if(!r.ok||d.error){
        _showLoginErr(d.error||'Verification failed');
        btn.disabled=false; btn.textContent='Verify';
        return;
      }
      if(d.radius_challenge){
        // Multi-step — re-render with fresh prompt and fresh challenge id
        btn.disabled=false;
        _showRadiusChallengePrompt(d.challenge_id, username, d.prompt||'Additional verification required');
        return;
      }
      _loggedOut=false;
      S.role=d.role||'viewer';
      if(d.session_ttl)_sessionTtl=d.session_ttl;
      try{
        const me=await fetch('/api/me').then(x=>x.ok?x.json():null);
        if(me&&me.theme_preference&&typeof setTheme==='function')setTheme(me.theme_preference,{sync:false});
      }catch(e){}
      hideLogin();
      try{localStorage.setItem('pw_tab','dashboard');}catch(e){}
      onAuthenticated(d.username);
      // Reset login UI for next time
      if(userField){userField.disabled=false;}
      if(passField){passField.style.display='block';}
      if(respField){respField.style.display='none';}
      if(promptEl){promptEl.style.display='none';}
    }catch(e){
      _showLoginErr('Server error. Try again.');
      btn.disabled=false; btn.textContent='Verify';
    }
  };
  btn.onclick=submit;
  respField.onkeydown=(e)=>{ if(e.key==='Enter'){e.preventDefault(); submit();} };
}

async function doLogout(){
  _loggedOut=true;
  _stopIdleCheck();
  if(sse){sse.close();sse=null;}
  if(_reconnectTimer){clearTimeout(_reconnectTimer);_reconnectTimer=null;}
  if(_hbSparkInterval){clearInterval(_hbSparkInterval);_hbSparkInterval=null;}
  if(window._badgePollInterval){clearInterval(window._badgePollInterval);window._badgePollInterval=null;}
  await fetch('/api/logout',{method:'POST'}).catch(()=>{});
  document.getElementById('usrDd').style.display='none';
  document.getElementById('devActBar').style.display='none';
  showLogin();
}
function _usrDdToggle(e){
  e.stopPropagation();
  const menu=document.getElementById('usrDdMenu');
  const btn=document.getElementById('usrDdBtn');
  const open=menu.classList.toggle('usr-dd-menu--open');
  btn.setAttribute('aria-expanded',open);
  if(open){const first=menu.querySelector('.usr-dd-item:not([disabled])');if(first)first.focus();}
}
function _usrDdClose(){
  const menu=document.getElementById('usrDdMenu');
  const btn=document.getElementById('usrDdBtn');
  if(!menu)return;
  menu.classList.remove('usr-dd-menu--open');
  if(btn)btn.setAttribute('aria-expanded','false');
}
document.addEventListener('click',_usrDdClose);
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){_usrDdClose();return;}
  const menu=document.getElementById('usrDdMenu');
  if(!menu||!menu.classList.contains('usr-dd-menu--open'))return;
  const items=[...menu.querySelectorAll('.usr-dd-item:not([disabled])')];
  const idx=items.indexOf(document.activeElement);
  if(e.key==='ArrowDown'){e.preventDefault();items[(idx+1)%items.length]?.focus();}
  if(e.key==='ArrowUp'){e.preventDefault();items[(idx-1+items.length)%items.length]?.focus();}
});

// ── About PingWatch modal ─────────────────────────────────────────────────
async function openAbout(){
  closeM('m-about');
  // Fetch server_info for live version/platform/backend/uptime
  let info = {};
  try { info = await api('GET', '/api/server_info'); } catch(_) {}
  const ver      = info.version || '—';
  const py       = info.python_version || '—';
  const plat     = (info.platform || '—').replace('Darwin', 'macOS');
  const backend  = info.db_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite';
  const uptime   = info.uptime_s != null ? _fmtUptime(info.uptime_s) : '—';
  const year     = new Date().getFullYear();

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'm-about';
  _overlayClose(o, () => closeM('m-about'));
  o.innerHTML = `
    <div class="mbox about-box" style="max-width:460px">
      <div class="mhd">
        <div class="mttl">About</div>
        <button class="mclose" onclick="closeM('m-about')">✕</button>
      </div>
      <div class="mbdy about-body">
        <div class="about-logo">
          <svg width="56" height="56" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="10" cy="10" r="8.5" stroke="currentColor" stroke-width="1" opacity=".35"/>
            <circle cx="10" cy="10" r="5"   stroke="currentColor" stroke-width="1" opacity=".55"/>
            <circle cx="10" cy="10" r="2"   fill="currentColor" opacity=".9"/>
            <line x1="1.5" y1="10" x2="5"    y2="10" stroke="currentColor" stroke-width="1.2" opacity=".7"/>
            <line x1="15"  y1="10" x2="18.5" y2="10" stroke="currentColor" stroke-width="1.2" opacity=".7"/>
            <line x1="10"  y1="1.5" x2="10" y2="5"    stroke="currentColor" stroke-width="1.2" opacity=".7"/>
            <line x1="10"  y1="15"  x2="10" y2="18.5" stroke="currentColor" stroke-width="1.2" opacity=".7"/>
          </svg>
        </div>
        <div class="about-title"><span class="tw-ping">Ping</span><span class="tw-watch">Watch</span></div>
        <div class="about-tagline">Real-Time Network Monitoring Platform</div>

        <div class="about-grid">
          <span class="about-k">Version</span>      <span class="about-v" id="about-ver">${esc(ver)}</span>
          <span class="about-k">Build</span>        <span class="about-v">Python ${esc(py)} · ${esc(backend)} · ${esc(plat)}</span>
          <span class="about-k">Uptime</span>       <span class="about-v">${esc(uptime)}</span>
        </div>

        <div class="about-sep"></div>

        <div class="about-credit">
          <span class="about-credit-emoji">🤖</span>
          Designed and built with
          <a href="https://claude.ai" target="_blank" rel="noopener noreferrer">Claude AI</a>
          as an AI-driven development experiment.
        </div>

        <div class="about-sep"></div>

        <div class="about-links">
          <a href="https://github.com/Nividan/Pingwatch" target="_blank" rel="noopener noreferrer">🐙 GitHub repository</a>
          <a href="https://github.com/Nividan/Pingwatch/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">📜 MIT License</a>
        </div>

        <div class="about-copyright">© ${year} Nividan</div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="_aboutCopyVersion()" title="Copy version + build info">⎘ Copy version</button>
        <button class="btn-p" onclick="closeM('m-about')">Close</button>
      </div>
    </div>`;
  document.body.appendChild(o);
}

function _fmtUptime(sec){
  sec = Math.max(0, Math.floor(sec||0));
  const d = Math.floor(sec/86400); sec %= 86400;
  const h = Math.floor(sec/3600);  sec %= 3600;
  const m = Math.floor(sec/60);
  const s = sec % 60;
  if(d) return `${d}d ${h}h ${m}m`;
  if(h) return `${h}h ${m}m`;
  if(m) return `${m}m ${s}s`;
  return `${s}s`;
}

function _aboutCopyVersion(){
  const el = document.getElementById('about-ver');
  // Pull the grid labels' text content for a meaningful clipboard payload
  const lines = [];
  document.querySelectorAll('#m-about .about-grid .about-k').forEach((k, i) => {
    const v = document.querySelectorAll('#m-about .about-grid .about-v')[i];
    if (v) lines.push(`${k.textContent}: ${v.textContent}`);
  });
  const txt = `PingWatch\n${lines.join('\n')}`;
  navigator.clipboard.writeText(txt).then(
    () => toast('Copied to clipboard','ok'),
    () => toast('Copy failed','err'),
  );
}
// Poll /api/ready until the server has finished db_load(). Returns quickly
// (~1 request, no splash) in the normal case where the server is already up.
// Shows a themed splash overlay when the server is mid-restart so the user
// sees a deliberate "starting up" screen instead of stuck shimmer widgets.
async function _waitForServerReady(){
  const splash = document.getElementById('pw-splash');
  const msg    = document.getElementById('pw-splash-msg');
  let shown    = false;
  let firstErrLogged = false;
  let pollCount = 0;
  let lastErr   = null;
  const T0      = Date.now();
  const MAX_MS  = 60000;
  const POLL    = 500;
  const SHOW_AFTER_MS = 300;   // don't flash splash on fast restarts
  const SLOW_AFTER_MS = 10000;

  while(Date.now() - T0 < MAX_MS){
    pollCount++;
    try{
      const r = await fetch('/api/ready', { cache:'no-store' });
      if(r.ok){
        const j = await r.json();
        if(j.ready){
          const elapsed = Date.now() - T0;
          if(shown){
            console.info(`[pw:ready] server ready after ${elapsed}ms (${pollCount} polls)`);
            splash.classList.add('fade-out');
            setTimeout(()=>{ splash.style.display='none'; splash.classList.remove('fade-out'); }, 400);
          }
          return true;
        }
      } else {
        lastErr = `HTTP ${r.status}`;
        if(!firstErrLogged){
          console.warn(`[pw:ready] /api/ready returned ${r.status} — still polling`);
          firstErrLogged = true;
        }
      }
    } catch(err){
      lastErr = (err && err.message) ? err.message : String(err);
      if(!firstErrLogged){
        console.warn('[pw:ready] /api/ready fetch failed — server probably still starting:', lastErr);
        firstErrLogged = true;
      }
    }

    // Show splash only after a short delay so brief restarts don't flash it
    if(!shown && Date.now() - T0 > SHOW_AFTER_MS){
      console.info('[pw:ready] server not yet ready — showing splash');
      if(splash) splash.style.display = 'flex';
      shown = true;
    }
    // Escalate message at 10s so the user knows we're still trying
    if(shown && msg && !msg.dataset.slow && Date.now() - T0 > SLOW_AFTER_MS){
      msg.textContent = 'Still starting up — this is taking longer than usual…';
      msg.dataset.slow = '1';
      console.warn(`[pw:ready] server taking >10s to become ready (last error: ${lastErr || 'none'})`);
    }
    await new Promise(res => setTimeout(res, POLL));
  }

  console.error(`[pw:ready] TIMEOUT after ${MAX_MS}ms — server never became ready. Last error: ${lastErr || 'none'}. Proceeding anyway; UI may be broken.`);
  if(shown && msg){
    msg.textContent = 'Server did not become ready. Check server logs.';
    // Leave the error visible briefly, then dismiss so the user can still try
    setTimeout(()=>{ if(splash) splash.style.display='none'; }, 3000);
  } else if(splash){
    splash.style.display = 'none';
  }
  return false;
}

async function onAuthenticated(username){
  // ── Clean slate: purge stale data from previous session / server restart ──
  for(const k in S.devices)  delete S.devices[k];
  for(const k in S.sensors)  delete S.sensors[k];
  for(const k in S.logs)     delete S.logs[k];
  for(const k in S.charts)   delete S.charts[k];
  for(const k in S.devTraps) delete S.devTraps[k];
  S._devSensors={};
  FLAPS.length=0; _FLAP_SEEN.clear();
  if(typeof _dwReset==='function') _dwReset();
  // Clear stale device cards & group sections from DOM
  document.querySelectorAll('#dpanels .grp-wrap').forEach(el=>el.remove());
  // Reset SSE state for clean reconnect
  _sseFirstConnect=true;
  _reconnectDelay=TIMINGS.RECONNECT_INITIAL;

  document.getElementById('tb-user').textContent=username;
  document.getElementById('usrDd').style.display='';
  const dn=document.getElementById('usr-dd-name');
  if(dn)dn.textContent=username;
  const db=document.getElementById('usr-dd-badge');
  if(db){
    const role=S.role||'';
    db.textContent=role.charAt(0).toUpperCase()+role.slice(1).toLowerCase();
    db.className='usr-dd-badge usr-dd-badge--'+role.toLowerCase();
  }
  applyRbac();
  // Gate the first data fetch on server readiness — prevents "widgets stuck
  // loading" when the user logs in between HTTP-bind and db_load() finishing.
  await _waitForServerReady();
  loadAll();
  connectSSE();
  // Refresh health bar sparkline every 5 min (clear old interval to prevent duplicates on re-login)
  if (_hbSparkInterval) clearInterval(_hbSparkInterval);
  _hbSparkInterval = setInterval(()=>{ _hbSparkLoaded=false; _hbDrawSpark(); }, TIMINGS.SPARK_REFRESH);
  // Poll badge counts (crit/warn/ack/muted); clear on re-login to prevent stacking
  _badgePoll();
  if (window._badgePollInterval) clearInterval(window._badgePollInterval);
  window._badgePollInterval = setInterval(_badgePoll, TIMINGS.ALERT_BADGE_POLL);
  _logBadgeInit();
  _lastActivity = Date.now();
  _startIdleCheck();
}

let _sessionTtl   = 86400;
let _lastActivity = Date.now();
let _idleTimer    = null;
function _onUserActivity(){ _lastActivity = Date.now(); }
function _startIdleCheck(){
  if(_idleTimer) clearInterval(_idleTimer);
  _idleTimer = setInterval(()=>{
    if((Date.now()-_lastActivity)/1000 >= _sessionTtl){
      clearInterval(_idleTimer); _idleTimer=null;
      doLogout().then(()=>showLogin('Session timed out due to inactivity.'));
    }
  },TIMINGS.IDLE_CHECK);
}
function _stopIdleCheck(){ if(_idleTimer){clearInterval(_idleTimer);_idleTimer=null;} }

// ── Status badges (crit / warn / ack / muted) ──────────────────
let _badgeCounts = {crit:0, warn:0, ack:0, muted:0};
let _badgeMutedList = [];

function _updateBadges(){
  const pairs = [
    ['badgeCrit','badgeCritCnt',_badgeCounts.crit],
    ['badgeWarn','badgeWarnCnt',_badgeCounts.warn],
    ['badgeAck','badgeAckCnt',_badgeCounts.ack],
    ['badgeMuted','badgeMutedCnt',_badgeCounts.muted],
  ];
  for(const [elId,cntId,n] of pairs){
    const el=document.getElementById(elId);
    const cnt=document.getElementById(cntId);
    if(!el) continue;
    if(cnt) cnt.textContent=n;
    el.style.display = n>0 ? 'flex' : 'none';
  }
}

function _openBadgeCrit(){
  switchMainTab('events');
  if(typeof _evtSetInnerTab==='function') _evtSetInnerTab('active');
}
function _openBadgeWarn(){
  switchMainTab('events');
  if(typeof _evtSetInnerTab==='function') _evtSetInnerTab('active');
}
function _openBadgeAck(){
  switchMainTab('events');
  if(typeof _evtSetInnerTab==='function') _evtSetInnerTab('active');
}
function _openBadgeMuted(){ _showMutedStoppedModal(); }

let _badgePollTimer = null;
async function _badgePoll(){
  try{
    const r = await fetch('/api/alert/events/active');
    if(!r.ok) return;
    const d = await r.json();
    _badgeCounts.crit  = d.crit_count  || 0;
    _badgeCounts.warn  = d.warn_count  || 0;
    _badgeCounts.ack   = d.ack_count   || 0;
    _badgeCounts.muted = d.muted_stopped_count || 0;
    _badgeMutedList    = d.muted_stopped || [];
    _updateBadges();
  } catch(_){}
}

function _scheduleBadgePoll(){
  if(_badgePollTimer) return;
  _badgePollTimer = setTimeout(()=>{
    _badgePollTimer = null;
    _badgePoll();
  }, 2000);
}

// ── Muted / stopped sensors modal ───────────────────────────────
function _showMutedStoppedModal(){
  closeM('ms-modal');
  const o = document.createElement('div');
  o.className='mo'; o.id='ms-modal';
  _overlayClose(o,()=>closeM('ms-modal'));
  const isOp = S.role==='operator'||S.role==='admin';
  let rows='';
  for(const item of _badgeMutedList){
    const reasons = item.reasons||[];
    const tags = reasons.map(r=>{
      if(r==='device_muted') return '<span class="ms-tag dev-muted">Device Muted</span>';
      if(r==='sensor_muted') return '<span class="ms-tag sen-muted">Sensor Muted</span>';
      if(r==='stopped')      return '<span class="ms-tag stopped">Stopped</span>';
      return '';
    }).join(' ');
    let acts='';
    if(isOp){
      if(reasons.includes('sensor_muted'))
        acts+=`<button class="btn-s" onclick="_msUnmuteSensor('${esc(item.did)}','${esc(item.sid)}')">Unmute</button> `;
      if(reasons.includes('device_muted'))
        acts+=`<button class="btn-s" onclick="_msUnmuteDevice('${esc(item.did)}')">Unmute Device</button> `;
      if(reasons.includes('stopped'))
        acts+=`<button class="btn-s" onclick="_msStartSensor('${esc(item.did)}','${esc(item.sid)}')">Start</button> `;
    }
    rows+=`<tr><td>${esc(item.dname)}</td><td>${esc(item.sname)}</td><td>${esc(item.stype)}</td><td>${tags}</td><td>${acts}</td></tr>`;
  }
  if(!rows) rows='<tr><td colspan="5" style="text-align:center;color:var(--text3);padding:20px">No muted or stopped sensors</td></tr>';
  o.innerHTML=`<div class="mbox" style="max-width:700px">
    <div class="mhd"><div class="mttl">Muted &amp; Stopped Sensors</div>
      <button class="mclose" onclick="closeM('ms-modal')">&#x2715;</button></div>
    <div class="mbdy" style="max-height:60vh;overflow:auto;padding:0">
      <table class="ms-tbl"><thead><tr><th>Device</th><th>Sensor</th><th>Type</th><th>Status</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
    <div class="mft"><button class="btn-s" onclick="closeM('ms-modal')">Close</button></div>
  </div>`;
  document.body.appendChild(o);
}

function _msBtnLoading(btn,label){
  if(!btn)return;
  btn.disabled=true;
  btn.textContent=label;
  btn.style.opacity='0.6';
}
async function _msUnmuteSensor(did,sid){
  const btn=event?.target; _msBtnLoading(btn,'Unmuting…');
  try{
    await api('PATCH',`/api/device/${did}/sensor/${sid}`,{alerts_muted:false});
    toast('Sensor unmuted','ok');
    await _badgePoll();
    _showMutedStoppedModal();
  }catch(_){toast('Failed to unmute sensor','err');_showMutedStoppedModal();}
}
async function _msUnmuteDevice(did){
  const btn=event?.target; _msBtnLoading(btn,'Unmuting…');
  try{
    await api('PATCH',`/api/device/${did}`,{alerts_muted:false});
    toast('Device unmuted','ok');
    if(S.devices[did]) S.devices[did].alerts_muted=false;
    await _badgePoll();
    _showMutedStoppedModal();
  }catch(_){toast('Failed to unmute device','err');_showMutedStoppedModal();}
}
async function _msStartSensor(did,sid){
  const btn=event?.target; _msBtnLoading(btn,'Starting…');
  try{
    await api('POST',`/api/device/${did}/sensor/${sid}/start`);
    toast('Sensor started','ok');
    await _badgePoll();
    _showMutedStoppedModal();
  }catch(_){toast('Failed to start sensor','err');_showMutedStoppedModal();}
}

// ── Log badge (WARNING+ entries) ────────────────────────────────
let _logBadgeTotal = 0;

function _updateLogBadge() {
  const seen = parseInt(_lsGet('logBadgeSeen') || '0', 10);
  const unseen = Math.max(0, _logBadgeTotal - seen);
  const el = document.getElementById('logBadge');
  const cnt = document.getElementById('logBadgeCnt');
  if (!el) return;
  if (S.role === 'viewer') { el.style.display = 'none'; return; }
  cnt.textContent = unseen;
  el.style.display = unseen > 0 ? '' : 'none';
}

async function _logBadgeInit() {
  try {
    const r = await fetch('/api/log-badge');
    if (!r.ok) return;
    const d = await r.json();
    _logBadgeTotal = d.total || 0;
    _updateLogBadge();
  } catch (_) {}
}

function _openLogBadge() {
  _lsSet('logBadgeSeen', String(_logBadgeTotal));
  _updateLogBadge();
  // Pre-filter the Logs tab to WARNING+ when opening from the badge
  if (typeof _lvFilter !== 'undefined') _lvFilter.minLevel = 'WARNING';
  switchMainTab('logs');
}

function _requestNotifPermission() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default')
    Notification.requestPermission();
}

function _playNotifSound(type) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const play = (freq, start, dur) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      o.frequency.value = freq;
      o.type = 'sine';
      g.gain.setValueAtTime(0.3, ctx.currentTime + start);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + dur);
      o.start(ctx.currentTime + start);
      o.stop(ctx.currentTime + start + dur + 0.05);
    };
    if (type === 'double') { play(880, 0, 0.12); play(880, 0.18, 0.12); }
    else { play(660, 0, 0.20); }  // 'alert' default
  } catch (_) {}
}

function _showBrowserNotif(d) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  const n = new Notification(d.title || 'PingWatch Alert', {
    body: d.body || '',
    icon: '/favicon.ico',
    tag:  'pingwatch-alert',
  });
  n.onclick = () => { window.focus(); n.close(); };
  if (d.sound && d.sound !== 'none') _playNotifSound(d.sound);
}
function applyRbac(){
  const op    = S.role==='operator'||S.role==='admin';
  const admin = S.role==='admin';
  document.querySelectorAll('.rbac-op').forEach(el=>el.style.display=op?'':'none');
  document.querySelectorAll('.rbac-admin').forEach(el=>el.style.display=admin?'':'none');
}
async function checkAuth(){
  // Hide UI controls until we confirm auth
  document.getElementById('usrDd').style.display='none';
  try{
    const r=await fetch('/api/me');
    if(r.ok){const d=await r.json(); S.role=d.role||'viewer'; if(d.session_ttl)_sessionTtl=d.session_ttl; if(d.theme_preference&&typeof setTheme==='function')setTheme(d.theme_preference,{sync:false}); onAuthenticated(d.username);}
    else{showLogin();}
  }catch(e){showLogin();}
}
// Enter key on login inputs
['login-user','login-pass'].forEach(id=>{
  document.getElementById(id)?.addEventListener('keydown',e=>{if(e.key==='Enter')submitLogin();});
});
['click','keydown','mousemove','touchstart'].forEach(ev=>{
  document.addEventListener(ev,_onUserActivity,{passive:true});
});

// ── API ──────────────────────────────────────────────────────────
// NOTE: map.js has its own copy of api() because it runs in an isolated iframe
// (map.html doesn't load app.js). Keep the two implementations in sync.
async function api(method,path,body=null){
  const o={method,headers:{'Content-Type':'application/json'}};
  if(body)o.body=JSON.stringify(body);
  const r=await fetch(path,o);
  if(r.status===401){if(!_loggedOut)showLogin('Session expired. Please sign in again.');return {};}
  if(!r.ok){
    const err=await r.json().catch(()=>({error:r.statusText}));
    throw new Error(err.error||r.statusText);
  }
  return r.json();
}

// ── Pills ────────────────────────────────────────────────────────
function updatePills(){
  _hbUpdate();
  if(typeof _updateStatusPills==='function') _updateStatusPills();
}

// ── Global Network Health Bar ─────────────────────────────────────
let _hbSparkLoaded   = false;
let _hbSparkInterval = null;
let _hbSparkData     = [];    // [{ts, pct}] — latest fetch
let _hbSparkEvents   = [];    // [{ts, type, label}]
let _hbSparkRange    = '24h';
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
  if (!_hbSparkLoaded) { _hbSparkLoaded = true; _hbDrawSpark(); _hbSetupSparkInteractions(); }
}

async function _hbDrawSpark() {
  const canvas = document.getElementById('hb-spark');
  if (!canvas) return;
  try {
    const r = await fetch(`/api/health/trend?range=${_hbSparkRange}`);
    if (!r.ok) return;
    const d = await r.json();
    _hbSparkData   = d.points || [];
    _hbSparkEvents = d.events || [];
    if (!_hbSparkData.length) return;
    await new Promise(res => requestAnimationFrame(res));
    canvas.width = canvas.offsetWidth || 160;
    _hbRenderSpk(canvas, false);
    canvas.style.display = '';
    const sep = document.getElementById('hb-spark-sep');
    const lbl = document.getElementById('hb-spark-lbl');
    if (sep) sep.style.display = '';
    if (lbl) lbl.style.display = '';
    _hbUpdateTrendArrow();
  } catch {}
}

// Core sparkline renderer — works for both mini (top bar) and expanded panel
function _hbRenderSpk(canvas, expanded) {
  const W   = canvas.width;
  const H   = canvas.height || (expanded ? 120 : 22);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  const pts = _hbSparkData;
  if (pts.length < 2) return;
  const rangeMin = _hbSparkRange === '1h' ? 60 : _hbSparkRange === '6h' ? 360 : 1440;
  const win  = rangeMin * 60;
  const now  = Date.now() / 1000;
  const t0   = now - win;
  const pad  = expanded ? 6 : 2;
  const xOf  = ts  => Math.max(0, Math.min(W, (ts + 900 - t0) / win * W));
  const yOf  = pct => pad + (1 - Math.min(100, Math.max(0, pct)) / 100) * (H - pad * 2);
  const coords = pts.map(p => ({ x: xOf(p.ts), y: yOf(p.pct), pct: p.pct, ts: p.ts }));

  // Vertical gradient: green (top/100%) → yellow → red (bottom/0%)
  const grad = ctx.createLinearGradient(0, 0, 0, H);
  grad.addColorStop(0,   '#23d18b');
  grad.addColorStop(0.2, '#23d18b');
  grad.addColorStop(0.5, '#f0a500');
  grad.addColorStop(1,   '#f85149');

  // Subtle fill under line
  const fillGrad = ctx.createLinearGradient(0, 0, 0, H);
  fillGrad.addColorStop(0,   'rgba(35,209,139,0.13)');
  fillGrad.addColorStop(0.5, 'rgba(240,165,0,0.06)');
  fillGrad.addColorStop(1,   'rgba(248,81,73,0.03)');
  ctx.beginPath();
  ctx.moveTo(coords[0].x, H);
  coords.forEach(c => ctx.lineTo(c.x, c.y));
  ctx.lineTo(coords[coords.length - 1].x, H);
  ctx.closePath();
  ctx.fillStyle = fillGrad;
  ctx.fill();

  // Smooth bezier line
  ctx.beginPath();
  ctx.moveTo(coords[0].x, coords[0].y);
  for (let i = 1; i < coords.length; i++) {
    const p = coords[i - 1], c = coords[i], mx = (p.x + c.x) / 2;
    ctx.bezierCurveTo(mx, p.y, mx, c.y, c.x, c.y);
  }
  ctx.strokeStyle = grad;
  ctx.lineWidth   = expanded ? 2 : 1.5;
  ctx.lineJoin    = 'round';
  ctx.stroke();

  // Event indicator dots (bottom edge)
  _hbSparkEvents.forEach(ev => {
    const x = xOf(ev.ts);
    if (x < 2 || x > W - 2) return;
    const dotY = H - (expanded ? 5 : 3);
    ctx.beginPath();
    ctx.arc(x, dotY, expanded ? 3 : 2, 0, Math.PI * 2);
    ctx.fillStyle = ev.type === 'outage' ? 'rgba(248,81,73,0.85)' : 'rgba(240,165,0,0.85)';
    ctx.fill();
  });

  // Live pulse dot on latest point
  if (coords.length) {
    const last = coords[coords.length - 1];
    const pc   = last.pct >= 95 ? '#23d18b' : last.pct >= 80 ? '#f0a500' : '#f85149';
    const pcA  = last.pct >= 95 ? 'rgba(35,209,139,0.2)' : last.pct >= 80 ? 'rgba(240,165,0,0.2)' : 'rgba(248,81,73,0.2)';
    ctx.beginPath();
    ctx.arc(last.x, last.y, expanded ? 6 : 4, 0, Math.PI * 2);
    ctx.fillStyle = pcA;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(last.x, last.y, expanded ? 2.5 : 2, 0, Math.PI * 2);
    ctx.fillStyle = pc;
    ctx.fill();
  }
}

function _hbUpdateTrendArrow() {
  const el = document.getElementById('hb-trend-arrow');
  if (!el || _hbSparkData.length < 4) { if (el) el.style.display = 'none'; return; }
  const tail  = _hbSparkData.slice(-4);
  const delta = tail[tail.length - 1].pct - tail[0].pct;
  let arrow = '→', color = 'var(--text3)';
  if (delta >= 3)  { arrow = '↑'; color = 'var(--up)'; }
  if (delta <= -3) { arrow = '↓'; color = 'var(--down)'; }
  el.textContent = arrow;
  el.style.color  = color;
  el.style.display = '';
}

function _hbSetupSparkInteractions() {
  const canvas = document.getElementById('hb-spark');
  if (!canvas || canvas._hbReady) return;
  canvas._hbReady = true;
  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const mx   = e.clientX - rect.left;
    const W    = canvas.offsetWidth || 160;
    const rangeMin = _hbSparkRange === '1h' ? 60 : _hbSparkRange === '6h' ? 360 : 1440;
    const win  = rangeMin * 60, now = Date.now() / 1000, t0 = now - win;
    let best = null, bestD = Infinity;
    _hbSparkData.forEach(p => {
      const x = (p.ts + 900 - t0) / win * W;
      const d = Math.abs(x - mx);
      if (d < bestD) { bestD = d; best = p; }
    });
    if (!best) return;
    const tot = Object.values(S.devices).length;
    const up  = Math.round(best.pct / 100 * tot);
    const dt  = new Date(best.ts * 1000);
    const t   = `${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
    // Event filter pivot: use the timestamp UNDER THE CURSOR (not the snapped
    // data point). With 15-min sample buckets, a real-world incident at 12:31
    // would otherwise miss the ±15-min window when the hover snaps to 13:00.
    // Centre the cursor on the bucket midpoint by rolling back 900s, the same
    // offset used when drawing event dots above.
    const tsHover = t0 + (mx / W) * win - 900;
    const nearby = _hbSparkEvents.filter(ev => Math.abs(ev.ts - tsHover) <= 900);
    _hbSpkTip(e.clientX, e.clientY, best.pct, up, tot - up, t, nearby);
  });
  canvas.addEventListener('mouseleave', _hbSpkTipHide);
  canvas.addEventListener('click', () => { _hbSpkTipHide(); _hbOpenExpanded(); });
}

function _hbSpkTip(cx, cy, pct, up, down, time, events) {
  let tip = document.getElementById('hb-spk-tip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'hb-spk-tip';
    tip.className = 'hb-spk-tip';
    document.body.appendChild(tip);
  }
  const col = pct >= 95 ? 'var(--up)' : pct >= 80 ? 'var(--warn)' : 'var(--down)';
  let html =
    `<div class="hb-tip-time">${time}</div>` +
    `<div class="hb-tip-pct" style="color:${col}">${Math.round(pct)}%</div>` +
    `<div class="hb-tip-row"><span style="color:var(--up)">▲</span> ${up} up</div>` +
    (down ? `<div class="hb-tip-row"><span style="color:var(--down)">▼</span> ${down} down</div>` : '');
  if (events && events.length) {
    html += '<div class="hb-tip-sep"></div>';
    // Outages first, then warnings — so a red incident never gets cropped by
    // the slice(0,3) when it shares a window with several yellow ones.
    const sorted = events.slice().sort((a, b) => {
      if (a.type === b.type) return a.ts - b.ts;
      return a.type === 'outage' ? -1 : 1;
    });
    const shown = sorted.slice(0, 3);
    shown.forEach(ev => {
      const ic = ev.type === 'outage' ? '▼' : '⚠';
      const c  = ev.type === 'outage' ? 'var(--down)' : 'var(--warn)';
      const edt = new Date(ev.ts * 1000);
      const et  = `${String(edt.getHours()).padStart(2,'0')}:${String(edt.getMinutes()).padStart(2,'0')}`;
      html += `<div class="hb-tip-ev"><span style="color:${c}">${ic}</span> <span style="color:var(--text3)">${et}</span> ${esc(ev.label)}</div>`;
    });
    if (sorted.length > 3) html += `<div class="hb-tip-ev" style="color:var(--text3)">+${sorted.length - 3} more</div>`;
  }
  tip.innerHTML = html;
  tip.style.display = '';
  const tw = tip.offsetWidth || 120;
  const th = tip.offsetHeight || 80;
  let left = cx - tw / 2, top = cy - th - 10;
  if (left < 4) left = 4;
  if (left + tw > innerWidth - 4) left = innerWidth - tw - 4;
  if (top < 4) top = cy + 12;
  tip.style.left = left + 'px';
  tip.style.top  = top  + 'px';
}
function _hbSpkTipHide() {
  const tip = document.getElementById('hb-spk-tip');
  if (tip) tip.style.display = 'none';
}

function _hbOpenExpanded() {
  document.getElementById('hb-exp-panel')?.remove();
  const ranges   = ['1h', '6h', '24h'];
  const rangeBtns = ranges.map(r =>
    `<button class="hb-exp-rbtn${r === _hbSparkRange ? ' active' : ''}" onclick="_hbExpRange('${r}')">${r}</button>`
  ).join('');
  const panel = document.createElement('div');
  panel.id    = 'hb-exp-panel';
  panel.className = 'hb-exp-panel';
  panel.innerHTML = `
    <div class="hb-exp-hdr">
      <span class="hb-exp-title">System Health Trend</span>
      <div class="hb-exp-ranges">${rangeBtns}</div>
      <button class="hb-exp-close" onclick="document.getElementById('hb-exp-panel').remove()">✕</button>
    </div>
    <canvas id="hb-exp-canvas" class="hb-exp-canvas"></canvas>
    <div class="hb-exp-footer">
      <div class="hb-exp-stats" id="hb-exp-stats"></div>
      <div class="hb-exp-evlist" id="hb-exp-evlist"></div>
    </div>`;
  document.body.appendChild(panel);
  const closeOut = e => {
    if (!panel.contains(e.target) && e.target.id !== 'hb-spark') {
      panel.remove();
      document.removeEventListener('mousedown', closeOut);
    }
  };
  setTimeout(() => document.addEventListener('mousedown', closeOut), 0);
  requestAnimationFrame(_hbRenderExpanded);
}

function _hbExpRange(range) {
  _hbSparkRange = range;
  document.querySelectorAll('.hb-exp-rbtn').forEach(b => b.classList.toggle('active', b.textContent === range));
  _hbSparkLoaded = false;
  _hbDrawSpark().then(() => _hbRenderExpanded());
}

function _hbRenderExpanded() {
  const canvas = document.getElementById('hb-exp-canvas');
  if (!canvas) return;
  canvas.width  = canvas.offsetWidth || 460;
  canvas.height = 120;
  if (!_hbSparkData.length) {
    canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  _hbRenderSpk(canvas, true);
  const stats = document.getElementById('hb-exp-stats');
  if (stats) {
    const avg  = _hbSparkData.reduce((s, p) => s + p.pct, 0) / _hbSparkData.length;
    const low  = Math.min(..._hbSparkData.map(p => p.pct));
    const lowC = low < 80 ? 'var(--down)' : low < 95 ? 'var(--warn)' : 'var(--up)';
    stats.innerHTML =
      `<span class="hb-exp-stat">Avg <strong>${Math.round(avg)}%</strong></span>` +
      `<span class="hb-exp-stat">Low <strong style="color:${lowC}">${Math.round(low)}%</strong></span>` +
      `<span class="hb-exp-stat">Incidents <strong>${_hbSparkEvents.length}</strong></span>`;
  }
  const evlist = document.getElementById('hb-exp-evlist');
  if (evlist) {
    if (!_hbSparkEvents.length) {
      evlist.innerHTML = '<span class="hb-exp-ev-ok">✓ No incidents</span>';
    } else {
      evlist.innerHTML = _hbSparkEvents.slice(-6).reverse().map(ev => {
        const dt = new Date(ev.ts * 1000);
        const t  = `${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}`;
        const ic = ev.type === 'outage' ? '↓' : '⚠';
        const c  = ev.type === 'outage' ? 'var(--down)' : 'var(--warn)';
        return `<span class="hb-exp-ev"><span style="color:${c};font-weight:700">${ic}</span> ${t} ${esc(ev.label)}</span>`;
      }).join('');
    }
  }
}

// ── Events / Flap log ────────────────────────────────────────────
const FLAPS=[];   // newest first; size controlled by MAX_FLAPS
const _FLAP_SEEN=new Set(); // dedup keys to prevent API+SSE duplicates
let MAX_FLAPS=20;

function _flapKey(d){
  // Unique key: device+sensor+timestamp+direction (covers both flaps and traps)
  return (d.did||d.src_ip||'')+'|'+(d.sid||d.trap_oid||'')+'|'+(d.ts||'')+'|'+(d._direction||d.direction||'');
}
let activeMainTab=(()=>{try{const t=localStorage.getItem('pw_tab')||'devices';return t==='alerting'?'events':t;}catch{return 'devices';}})();
// Apply correct tab button immediately — synchronous, no network request needed
document.getElementById('tab'+activeMainTab[0].toUpperCase()+activeMainTab.slice(1))?.classList.add('active');

function pushFlap(d){
  const k=_flapKey(d); if(_FLAP_SEEN.has(k)) return; _FLAP_SEEN.add(k);
  FLAPS.unshift(d);
  if(FLAPS.length>MAX_FLAPS) FLAPS.pop();
  renderFlaps();
  flashDownPill();
}

function resolveFlap(d, matchDir){
  for(let i=0;i<FLAPS.length;i++){
    const f=FLAPS[i];
    if(f.did===d.did && f.sid===d.sid
       && (f._direction===matchDir || f.direction===matchDir)
       && !f.resolved_at){
      const downMs=new Date(f.ts).getTime();
      const recMs=new Date(d.ts).getTime();
      f.resolved_at=recMs/1000;
      f.duration=Math.max(0,(recMs-downMs)/1000);
      f.ack_state='resolved';
      break;
    }
  }
  renderFlaps();
}

function pushThresholdEvent(d, level){
  const entry=Object.assign({},d,{_direction:'threshold',_thr_level:level});
  const k=_flapKey(entry); if(_FLAP_SEEN.has(k)) return; _FLAP_SEEN.add(k);
  FLAPS.unshift(entry);
  if(FLAPS.length>MAX_FLAPS)FLAPS.pop();
  renderFlaps();
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
    } else if(d._direction==='threshold_ok'){
      dotStyle='background:var(--up);box-shadow:0 0 6px rgba(35,209,139,.8)'; label='THR OK';
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

async function _refreshFlapList(){
  try{
    const fd=await fetch('/api/flaps').then(r=>r.json());
    const byKey={};
    (fd.flaps||[]).forEach(f=>{
      if(f.direction==='recovered'||f.direction==='threshold_ok') return;
      if(f.direction==='threshold_crit'){f._direction='threshold';f._thr_level='crit';}
      else if(f.direction==='threshold_warn'){f._direction='threshold';f._thr_level='warn';}
      else if(f.direction==='anomaly_warn'){f._direction='anomaly';f._thr_level='warn';}
      else f._direction=f.direction||'down';
      byKey[_flapKey(f)]=f;
    });
    for(let i=0;i<FLAPS.length;i++){
      const k=_flapKey(FLAPS[i]);
      if(byKey[k]) Object.assign(FLAPS[i],{
        id:byKey[k].id, ack_state:byKey[k].ack_state,
        resolved_at:byKey[k].resolved_at, duration:byKey[k].duration
      });
    }
    renderFlaps();
  }catch(_){}
}

function switchMainTab(tab){
  if(tab==='alerting') tab='events';
  activeMainTab=tab;
  try{localStorage.setItem('pw_tab',tab);}catch(e){}
  document.getElementById('tabDashboard').classList.toggle('active',tab==='dashboard');
  document.getElementById('tabDevices').classList.toggle('active',tab==='devices');
  document.getElementById('tabEvents').classList.toggle('active',tab==='events');
  document.getElementById('tabMap').classList.toggle('active',tab==='map');
  document.getElementById('tabBackups').classList.toggle('active',tab==='backups');
  document.getElementById('tabIpam').classList.toggle('active',tab==='ipam');
  { const _rb=document.getElementById('tabReports'); if(_rb) _rb.classList.toggle('active',tab==='reports'); }
  { const _lb=document.getElementById('tabLogs');    if(_lb) _lb.classList.toggle('active',tab==='logs');    }
  const dashboardView=document.getElementById('dashboardView');
  const eventsView   =document.getElementById('eventsView');
  const mapView      =document.getElementById('mapView');
  const backupsView  =document.getElementById('backupsView');
  const ipamView     =document.getElementById('ipamView');
  const reportsView  =document.getElementById('reportsView');
  const logsView     =document.getElementById('logsView');
  const emptyMain    =document.getElementById('emptyMain');
  const dpanels      =document.getElementById('dpanels');
  dashboardView.style.display='none';
  eventsView.style.display   ='none';
  mapView.style.display      ='none';
  backupsView.style.display  ='none';
  ipamView.style.display     ='none';
  if(reportsView) reportsView.style.display='none';
  if(logsView)    logsView.style.display   ='none';
  // Deactivate logs polling when switching away from the Logs tab
  if(tab!=='logs' && typeof _logsDeactivate==='function') _logsDeactivate();
  document.getElementById('devActBar').style.display='none';
  const _devPg=document.getElementById('devPagination');
  if(_devPg) _devPg.style.display='none';
  // Cancel any in-flight IPAM DNS poll when leaving the IPAM tab
  if(typeof _ipamCancelDnsInterval==='function') _ipamCancelDnsInterval();
  const _mf=document.getElementById('map-frame');
  // Pause/resume outer background canvas on Map tab (iframe covers it anyway)
  const _isMap = tab === 'map';
  window._bgMapActive = _isMap;
  document.getElementById('netbg').style.visibility = _isMap ? 'hidden' : '';
  if (!_isMap) window._bgResume?.();
  if(tab==='dashboard'){
    dashboardView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    if(typeof _dwInit==='function') _dwInit();
  } else if(tab==='events'){
    eventsView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    _refreshEvents();
    if(typeof _evtSubTab==='function') _evtSubTab(_evtActiveSubTab);
  } else if(tab==='map'){
    emptyMain.style.display='none';
    dpanels.style.display='none';
    mapView.style.display='flex';
    if(_mf&&!_mf.src&&_mf.dataset.src) _mf.src=_mf.dataset.src;
    else if(_mf&&_mf.contentWindow) _mf.contentWindow.postMessage({type:'pw_reload_pages'},window.location.origin);
    // Send current device statuses with resume so map can catchup missed events while paused
    _mf?.contentWindow?.postMessage({
      type:'ntm_resume',
      devices:Object.values(S.devices).map(d=>({did:d.did||d.device_id,status:d.status}))
    },window.location.origin);
  } else if(tab==='backups'){
    backupsView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    if(typeof _bkInit==='function') _bkInit();
  } else if(tab==='ipam'){
    ipamView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    if(typeof _ipamInit==='function') _ipamInit();
  } else if(tab==='reports'){
    if(reportsView) reportsView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    if(typeof _rptInit==='function') _rptInit();
  } else if(tab==='logs'){
    if(logsView) logsView.style.display='flex';
    emptyMain.style.display='none';
    dpanels.style.display='none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    if(typeof _logsInit==='function') _logsInit();
  } else {
    const hasDevices=Object.keys(S.devices).length>0;
    document.getElementById('devActBar').style.display='';
    emptyMain.style.display=hasDevices?'none':'flex';
    dpanels.style.display=hasDevices?'':'none';
    _mf?.contentWindow?.postMessage({type:'ntm_pause'},window.location.origin);
    _refreshDevices();
    if(typeof _renderPagination==='function') _renderPagination();
  }
}

async function _refreshDevices(){
  try{
    const data=await (await fetch('/api/devices')).json();
    S._devSensors={};
    data.devices.forEach(dev=>{
      S.devices[dev.device_id]=dev;
      dev.sensors.forEach(s=>{
        const _k=`${dev.device_id}/${s.sensor_id}`;
        S.sensors[_k]=s;
        if(!S._devSensors[dev.device_id]) S._devSensors[dev.device_id]=new Set();
        S._devSensors[dev.device_id].add(_k);
      });
      renderDp(dev);
    });
    updatePills();
  }catch(e){}
}

async function _refreshEvents(){
  try{
    FLAPS.length=0; _FLAP_SEEN.clear();
    const [fd,td]=await Promise.all([
      fetch('/api/flaps').then(r=>r.json()),
      fetch('/api/traps').then(r=>r.json()),
      typeof _refreshAlertCache==='function' ? _refreshAlertCache() : Promise.resolve(),
    ]);
    (fd.flaps||[]).forEach(f=>{
      if(f.direction==='recovered'||f.direction==='threshold_ok') return;
      if(f.direction==='threshold_crit'){f._direction='threshold';f._thr_level='crit';}
      else if(f.direction==='threshold_warn'){f._direction='threshold';f._thr_level='warn';}
      else if(f.direction==='anomaly_warn'){f._direction='anomaly';f._thr_level='warn';}
      else f._direction=f.direction||'down';
      const k=_flapKey(f); if(!_FLAP_SEEN.has(k)){_FLAP_SEEN.add(k);FLAPS.push(f);}
    });
    (td.traps||[]).forEach(t=>{ t._direction='trap'; const k=_flapKey(t); if(!_FLAP_SEEN.has(k)){_FLAP_SEEN.add(k);FLAPS.push(t);} });
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
      interval:      _sr.snr_interval      || 5,
      timeout:       _sr.snr_timeout       || 4,
      fail_after:    _sr.snr_fail_after    || 2,
      recover_after: _sr.snr_recover_after || 1,
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
    (fd.flaps||[]).forEach(f=>{
      if(f.direction==='recovered'||f.direction==='threshold_ok') return;
      if(f.direction==='threshold_crit'){f._direction='threshold';f._thr_level='crit';}
      else if(f.direction==='threshold_warn'){f._direction='threshold';f._thr_level='warn';}
      else if(f.direction==='anomaly_warn'){f._direction='anomaly';f._thr_level='warn';}
      else f._direction=f.direction||'down';
      const k=_flapKey(f); if(!_FLAP_SEEN.has(k)){_FLAP_SEEN.add(k);FLAPS.push(f);}
    });
  } catch(e){}
  try {
    const tr=await fetch('/api/traps');
    const td=await tr.json();
    (td.traps||[]).forEach(t=>{ t._direction='trap'; const k=_flapKey(t); if(!_FLAP_SEEN.has(k)){_FLAP_SEEN.add(k);FLAPS.push(t);} });
  } catch(e){}
  // Sort combined list newest-first and cap size
  FLAPS.sort((a,b)=>new Date(b.ts)-new Date(a.ts));
  renderFlaps();
  if(typeof _dwOnFlapEvent==='function') _dwOnFlapEvent();

  // Populate the muted-group set before rendering any group headers so
  // the 🔕 badge is painted on first pass, not after a flicker.
  if (typeof _loadMutedGroups === 'function') { try { await _loadMutedGroups(); } catch {} }

  const r=await fetch('/api/devices');
  const data=await r.json();
  S._devSensors={};
  data.devices.forEach(dev=>{
    S.devices[dev.device_id]=dev;
    dev.sensors.forEach(s=>{
      const _k=`${dev.device_id}/${s.sensor_id}`;
      S.sensors[_k]=s;
      S.logs[_k]=[];
      if(!S._devSensors[dev.device_id]) S._devSensors[dev.device_id]=new Set();
      S._devSensors[dev.device_id].add(_k);
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
  _restoreViewToggle();
  // Clear dashboard loading shimmer now that device/sensor data is ready
  if (typeof _dwClearLoading === 'function') _dwClearLoading();
  // Update group summaries for collapsed groups
  document.querySelectorAll('.grp-grid.collapsed').forEach(g=>{
    if(g.dataset.group) _updateGrpSummary(g.dataset.group);
  });
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
  // Signal NTM map iframe to re-fetch fresh data (handles server restart)
  const _mf=document.getElementById('map-frame');
  if(_mf&&_mf.contentWindow) _mf.contentWindow.postMessage({type:'pw_reload_pages'},window.location.origin);
}
// App bootstrap — check session before doing anything
checkAuth();