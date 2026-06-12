// ───────────────────────────────────────────────────────────────
// PingWatch — Probes page (distributed remote agents, v1.3)
//
// S.probes      : {probe_id → probe view dict} (from /api/probes)
// S._siteProbes : {site name → probe_id}       (from /api/sites/meta)
//
// Also exports the helpers devices.js / sensors.js use for the
// "via probe" pill and stale-grey rendering:
//   _effectiveProbeFor(dev, sensor) → probe_id | ''
//   _probeIsStale(probe_id)         → true when its agent is disconnected
// ───────────────────────────────────────────────────────────────

async function _probesRefreshCache(){
  try{
    const r=await api('GET','/api/probes');
    S.probes={};
    (r.probes||[]).forEach(p=>{ S.probes[p.probe_id]=p; });
  }catch(_){}
  try{
    const r=await api('GET','/api/sites/meta');
    S._siteProbes={};
    (r.sites||[]).forEach(s=>{ if(s.probe_id) S._siteProbes[s.name]=s.probe_id; });
  }catch(_){}
}

// ── Cascade resolver (client mirror of core/probe_assign.py) ─────
function _effectiveProbeFor(dev, sensor){
  let pid = sensor && sensor.probe_id ? sensor.probe_id : '';
  if(pid) return pid==='central' ? '' : pid;
  pid = dev && dev.probe_id ? dev.probe_id : '';
  if(pid) return pid==='central' ? '' : pid;
  const site = dev ? (dev.site||'') : '';
  pid = site ? (S._siteProbes[site]||'') : '';
  return pid==='central' ? '' : pid;
}

function _probeIsStale(pid){
  if(!pid) return false;
  const p=S.probes[pid];
  return !p || !p.connected;          // unknown/never-enrolled = stale too
}

function _probeName(pid){
  const p=S.probes[pid];
  return p ? (p.name||pid) : pid;
}

// Small "via ‹probe›" pill for device cards / sensor detail.
function _viaProbePill(pid){
  if(!pid) return '';
  const stale=_probeIsStale(pid);
  return `<span class="via-probe-pill${stale?' via-probe-stale':''}" `+
         `title="Measured by remote probe ${esc(_probeName(pid))}`+
         `${stale?' — probe offline, data is stale':''}">`+
         `📡 ${esc(_probeName(pid))}${stale?' ⚠':''}</span>`;
}

// Re-render device panels when a probe's connectivity flips so stale
// styling appears/disappears without a manual refresh.
function _refreshStaleBadges(){
  if(typeof renderDp!=='function') return;
  Object.values(S.devices).forEach(dev=>{
    const pid=_effectiveProbeFor(dev,null);
    let hit = !!pid;
    if(!hit){
      hit=(dev.sensors||[]).some(s=>_effectiveProbeFor(dev,s));
    }
    if(hit){ try{ renderDp(dev); }catch(_){} }
  });
}

// ── Page ─────────────────────────────────────────────────────────
let _probesTimer=null;

function _probesInit(){
  _probesRender();
  _probesLoad();
  if(_probesTimer) clearInterval(_probesTimer);
  // last-seen relative times + connected dots drift — refresh every 15s
  _probesTimer=setInterval(()=>{
    if(activeMainTab!=='probes'){ clearInterval(_probesTimer); _probesTimer=null; return; }
    _probesLoad();
  },15000);
}

function _probesRender(){
  const v=document.getElementById('probesView');
  if(!v) return;
  v.innerHTML=
    `<div class="pagehead">`+
      `<div class="pagehead-l">`+
        `<h1>Probes</h1>`+
        `<div class="sub" id="pbSub">Remote agents that measure sensors from inside other networks.</div>`+
      `</div>`+
      `<div class="pagehead-r">`+
        `<button class="btn primary rbac-admin" onclick="_pbOpenAdd()">+ Add Probe</button>`+
        `<button class="btn" onclick="_probesLoad()">Refresh</button>`+
      `</div>`+
    `</div>`+
    `<div id="pb-list" class="pb-list"></div>`;
}

async function _probesLoad(){
  await _probesRefreshCache();
  const list=document.getElementById('pb-list');
  if(!list) return;
  const probes=Object.values(S.probes).sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  if(!probes.length){
    list.innerHTML=
      `<div class="pb-empty">`+
        `<div class="pb-empty-icon">📡</div>`+
        `<h3>No remote probes yet</h3>`+
        `<p>A probe is a lightweight agent you install in a branch office, DR site, or customer LAN. `+
        `Sensors assigned to it are measured from there and report back here — only outbound HTTPS is needed.</p>`+
        `<button class="btn primary rbac-admin" onclick="_pbOpenAdd()">+ Add your first probe</button>`+
      `</div>`;
    applyRbac();
    return;
  }
  list.innerHTML=probes.map(p=>_pbRow(p)).join('');
  applyRbac();
}

function _pbAgo(ts){
  if(!ts) return 'never';
  const s=Math.max(0,Math.floor(Date.now()/1000-ts));
  if(s<60)    return s+'s ago';
  if(s<3600)  return Math.floor(s/60)+'m ago';
  if(s<86400) return Math.floor(s/3600)+'h ago';
  return Math.floor(s/86400)+'d ago';
}

function _pbRow(p){
  const dotCls = p.status==='revoked' ? 'pb-dot-revoked'
               : p.connected          ? 'pb-dot-up'
               : p.status==='pending' ? 'pb-dot-pending'
               : 'pb-dot-down';
  const stateLbl = p.status==='revoked' ? 'Revoked'
                 : p.connected          ? 'Connected'
                 : p.status==='pending' ? 'Awaiting enrollment'
                 : 'Disconnected';
  const srvVer=(window._pwVersion||'');
  const verBadge=(p.agent_version && srvVer && p.agent_version!==srvVer)
    ? `<span class="pb-chip pb-chip-warn" title="Agent ${esc(p.agent_version)} ≠ server ${esc(srvVer)} — download a fresh package">update available</span>`
    : '';
  const skewBadge=(Math.abs(p.clock_skew_s||0)>30)
    ? `<span class="pb-chip pb-chip-warn" title="Agent clock differs from server by ${Math.round(p.clock_skew_s)}s — check NTP at the branch">clock skew ${Math.round(p.clock_skew_s)}s</span>`
    : '';
  const caps=p.capabilities||{};
  const capChips=['snmpget','paramiko','pyvmomi'].map(c=>
    `<span class="pb-chip ${caps[c]?'pb-chip-ok':'pb-chip-off'}" title="${esc(c)} ${caps[c]?'available':'not installed'} on the probe host">${esc(c)}</span>`
  ).join('');
  const sites=Object.entries(S._siteProbes).filter(([,pid])=>pid===p.probe_id).map(([n])=>n);
  const enrollNote=(p.status==='pending'&&p.enroll_pending)
    ? `<span class="pb-chip" title="One-time enrollment token armed — install the package on the branch host">token armed</span>`
    : '';
  const pidq=esc(JSON.stringify(p.probe_id));
  return `<div class="pb-card" id="pb-card-${esc(p.probe_id)}">`+
    `<div class="pb-card-head">`+
      `<span class="pb-dot ${dotCls}"></span>`+
      `<span class="pb-name">${esc(p.name||p.probe_id)}</span>`+
      `<span class="pb-state">${stateLbl}</span>`+
      verBadge+skewBadge+enrollNote+
      `<span class="pb-spacer"></span>`+
      `<button class="btn-s rbac-admin" onclick='_pbDownload(${pidq})' title="Download the pre-configured agent zip (issues a fresh one-time token)">⬇ Package</button>`+
      `<button class="btn-s rbac-admin" onclick='_pbReenroll(${pidq})' title="Revoke the agent credential and arm a new one-time enrollment token">↻ Re-enroll</button>`+
      `<button class="btn-s rbac-admin" onclick='_pbRevoke(${pidq})' title="Revoke the agent credential (record kept)">⛔ Revoke</button>`+
      `<button class="btn-s bulk-danger rbac-admin" onclick='_pbDelete(${pidq})'>✕ Delete</button>`+
    `</div>`+
    (p.description?`<div class="pb-desc">${esc(p.description)}</div>`:'')+
    `<div class="pb-meta">`+
      `<span title="Last successful checkin">Last seen: <b>${_pbAgo(p.last_seen)}</b></span>`+
      `<span title="Sensors whose effective probe is this one">Sensors: <b>${p.sensor_count||0}</b></span>`+
      (sites.length?`<span title="Sites bound to this probe">Sites: <b>${esc(sites.join(', '))}</b></span>`:'')+
      (p.agent_version?`<span>Agent: <b>v${esc(p.agent_version)}</b></span>`:'')+
      (p.os_info?`<span>OS: <b>${esc(p.os_info)}</b></span>`:'')+
      (p.last_checkin_ip?`<span>IP: <b>${esc(p.last_checkin_ip)}</b></span>`:'')+
      ((p.spool_depth||0)>0?`<span class="pb-warn-text" title="Results buffered on the agent — backfills when connected">Spool: <b>${p.spool_depth}</b></span>`:'')+
      ((p.pending_tasks||0)>0?`<span title="Queued/running scans on this probe">Tasks: <b>${p.pending_tasks}</b></span>`:'')+
    `</div>`+
    `<div class="pb-caps">${capChips}</div>`+
  `</div>`;
}

function _probesOnStatus(_d){
  if(activeMainTab==='probes') _probesLoad();
}

// ── Add probe ────────────────────────────────────────────────────
function _pbOpenAdd(){
  closeM('pb-add');
  const o=document.createElement('div');
  o.className='mo'; o.id='pb-add';
  _overlayClose(o,()=>closeM('pb-add'));
  o.innerHTML=
    `<div class="mbox" style="max-width:480px">`+
      `<div class="mhd"><span>Add Probe</span><button class="mx" onclick="closeM('pb-add')">✕</button></div>`+
      `<div class="mbdy">`+
        `<div class="fr"><label class="fl">Name</label>`+
          `<input type="text" id="pb-add-name" placeholder="e.g. branch-tlv, customer-acme" maxlength="64"></div>`+
        `<div class="fr"><label class="fl">Description (optional)</label>`+
          `<input type="text" id="pb-add-desc" placeholder="Where does this probe live?" maxlength="256"></div>`+
        `<p class="pb-hint">You'll get a one-time enrollment token (valid 7 days) and a pre-configured agent package to install on the branch host. Only outbound HTTPS to this server is required.</p>`+
      `</div>`+
      `<div class="mft">`+
        `<button class="btn" onclick="closeM('pb-add')">Cancel</button>`+
        `<button class="btn primary" onclick="_pbSubmitAdd()">Create Probe</button>`+
      `</div>`+
    `</div>`;
  document.body.appendChild(o);
  const inp=document.getElementById('pb-add-name');
  inp.focus();
  inp.onkeydown=e=>{ if(e.key==='Enter') _pbSubmitAdd(); };
}

async function _pbSubmitAdd(){
  const name=(document.getElementById('pb-add-name')?.value||'').trim();
  const desc=(document.getElementById('pb-add-desc')?.value||'').trim();
  if(!name){ toast('Name required','err'); return; }
  let r;
  try{ r=await api('POST','/api/probes',{name,description:desc}); }
  catch(e){ toast(e.message||'Failed to create probe','err'); return; }
  if(!r||!r.probe_id){ toast((r&&r.error)||'Failed to create probe','err'); return; }
  closeM('pb-add');
  _probesLoad();
  _pbShowToken(r.probe_id, name, r.enrollment_token);
}

function _pbShowToken(pid, name, token){
  closeM('pb-token');
  const o=document.createElement('div');
  o.className='mo'; o.id='pb-token';
  _overlayClose(o,()=>closeM('pb-token'));
  o.innerHTML=
    `<div class="mbox" style="max-width:560px">`+
      `<div class="mhd"><span>Probe created: ${esc(name)}</span><button class="mx" onclick="closeM('pb-token')">✕</button></div>`+
      `<div class="mbdy">`+
        `<p>The easiest path: <b>download the package</b> — it ships with this token already in <code>config.json</code>.</p>`+
        `<div class="pb-token-box" id="pb-token-val">${esc(token)}</div>`+
        `<p class="pb-hint">Shown once. One-time use, expires in 7 days, bound to this probe. `+
        `Downloading the package later issues a fresh token automatically.</p>`+
      `</div>`+
      `<div class="mft">`+
        `<button class="btn" onclick="_pbCopyToken()">Copy token</button>`+
        `<button class="btn primary" onclick='_pbDownload(${esc(JSON.stringify(pid))});closeM("pb-token")'>⬇ Download package</button>`+
      `</div>`+
    `</div>`;
  document.body.appendChild(o);
}

function _pbCopyToken(){
  const t=document.getElementById('pb-token-val')?.textContent||'';
  navigator.clipboard?.writeText(t).then(()=>toast('Token copied','ok'),
                                         ()=>toast('Copy failed','err'));
}

// ── Row actions ──────────────────────────────────────────────────
function _pbDownload(pid){
  // Plain navigation → browser save dialog; the endpoint re-arms a fresh
  // one-time token inside the zip on every download.
  window.location.href=`/api/probes/${encodeURIComponent(pid)}/package`;
  setTimeout(_probesLoad, 1500);
}

async function _pbReenroll(pid){
  _pwConfirm(`Re-enroll probe "${_probeName(pid)}"? The current agent credential is revoked and a new one-time token is issued.`, async ()=>{
    let r;
    try{ r=await api('POST',`/api/probes/${encodeURIComponent(pid)}/reenroll`); }
    catch(e){ toast(e.message||'Re-enroll failed','err'); return; }
    if(!r||!r.ok){ toast((r&&r.error)||'Re-enroll failed','err'); return; }
    _probesLoad();
    _pbShowToken(pid, _probeName(pid), r.enrollment_token);
  }, 'Re-enroll');
}

async function _pbRevoke(pid){
  _pwConfirm(`Revoke probe "${_probeName(pid)}"? Its agent loses access immediately; assigned sensors go stale until you re-enroll or reassign.`, async ()=>{
    try{
      const r=await api('POST',`/api/probes/${encodeURIComponent(pid)}/revoke`);
      if(!r||!r.ok){ toast((r&&r.error)||'Revoke failed','err'); return; }
      toast('Probe revoked','ok');
    }catch(e){ toast(e.message||'Revoke failed','err'); return; }
    _probesLoad();
  }, 'Revoke');
}

async function _pbDelete(pid){
  // First attempt without a target: a 409 returns the assignment counts.
  let counts=null;
  try{
    const r=await api('DELETE',`/api/probes/${encodeURIComponent(pid)}`);
    if(r&&r.ok){ toast('Probe deleted','ok'); _probesLoad(); return; }
  }catch(e){
    try{ counts=JSON.parse(e.message).assignments; }catch(_){ /* fall through */ }
  }
  _pbDeleteDialog(pid, counts);
}

function _pbDeleteDialog(pid, counts){
  closeM('pb-del');
  const others=Object.values(S.probes).filter(p=>p.probe_id!==pid);
  const c=counts||{devices:'?',sensors:'?',sites:'?'};
  const o=document.createElement('div');
  o.className='mo'; o.id='pb-del';
  _overlayClose(o,()=>closeM('pb-del'));
  o.innerHTML=
    `<div class="mbox" style="max-width:520px">`+
      `<div class="mhd"><span>Delete probe: ${esc(_probeName(pid))}</span><button class="mx" onclick="closeM('pb-del')">✕</button></div>`+
      `<div class="mbdy">`+
        `<p>This probe still has assignments: <b>${c.devices}</b> device(s), <b>${c.sensors}</b> sensor override(s), <b>${c.sites}</b> site binding(s).</p>`+
        `<p>Where should they go? (Nothing is ever auto-moved — central usually can't reach branch targets.)</p>`+
        `<div class="fr"><label class="pb-radio"><input type="radio" name="pb-del-target" value="central" checked> Reassign to <b>Central</b> (explicit pin — sensors will be probed from this server)</label></div>`+
        (others.length?`<div class="fr"><label class="pb-radio"><input type="radio" name="pb-del-target" value="__other"> Reassign to another probe: `+
          `<select id="pb-del-other">${others.map(p=>`<option value="${esc(p.probe_id)}">${esc(p.name)}</option>`).join('')}</select></label></div>`:'')+
      `</div>`+
      `<div class="mft">`+
        `<button class="btn" onclick="closeM('pb-del')">Cancel</button>`+
        `<button class="btn bulk-danger" onclick='_pbDeleteApply(${esc(JSON.stringify(pid))})'>Delete &amp; Reassign</button>`+
      `</div>`+
    `</div>`;
  document.body.appendChild(o);
}

async function _pbDeleteApply(pid){
  const sel=document.querySelector('input[name="pb-del-target"]:checked');
  let target=sel?sel.value:'central';
  if(target==='__other') target=document.getElementById('pb-del-other')?.value||'central';
  try{
    const r=await api('DELETE',
      `/api/probes/${encodeURIComponent(pid)}?reassign_to=${encodeURIComponent(target)}`);
    if(!r||!r.ok){ toast((r&&r.error)||'Delete failed','err'); return; }
  }catch(e){ toast(e.message||'Delete failed','err'); return; }
  closeM('pb-del');
  toast('Probe deleted','ok');
  await _probesRefreshCache();
  _probesLoad();
  if(typeof _refreshDevices==='function') _refreshDevices();
}

// ── Probe dropdown for device / sensor / site forms ──────────────
// inheritLabel describes what '' falls back to at that level.
function _probeSelectHtml(id, current, inheritLabel){
  const probes=Object.values(S.probes)
    .filter(p=>p.status!=='revoked')
    .sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  const cur=current||'';
  let opts=`<option value=""${cur===''?' selected':''}>${esc(inheritLabel)}</option>`;
  opts+=`<option value="central"${cur==='central'?' selected':''}>Central (this server)</option>`;
  probes.forEach(p=>{
    opts+=`<option value="${esc(p.probe_id)}"${cur===p.probe_id?' selected':''}>📡 ${esc(p.name)}</option>`;
  });
  // Keep an unknown/stale current value visible instead of silently moving it
  if(cur && cur!=='central' && !S.probes[cur]){
    opts+=`<option value="${esc(cur)}" selected>${esc(cur)} (missing)</option>`;
  }
  return `<select id="${esc(id)}">${opts}</select>`;
}
