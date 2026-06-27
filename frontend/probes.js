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
        `<button class="btn rbac-admin" onclick="_pbOpenCampaign()" title="Roll out the current agent build to supervisor-managed probes (staged, with auto-rollback)">⬆ Update fleet…</button>`+
        `<button class="btn" onclick="_probesLoad()">Refresh</button>`+
      `</div>`+
    `</div>`+
    `<div id="pb-campaigns" class="pb-campaigns"></div>`+
    `<div id="pb-list" class="pb-list"></div>`;
}

async function _probesLoad(){
  await _probesRefreshCache();
  try{ const b=await api('GET','/api/probes/build'); S._agentBuild=b.build_id||''; }catch(_){}
  _pbLoadCampaigns();
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
  const srvBuild=(S._agentBuild||'');
  // Supervisor-managed probes can take remote updates; legacy ones need a
  // one-time manual re-install first.
  const verBadge = p.supervisor
    ? ((srvBuild && p.build_id && p.build_id!==srvBuild)
        ? `<span class="pb-chip pb-chip-warn" title="Running ${esc(p.build_id||'?')} — current build is ${esc(srvBuild)}. Use “Update fleet” to roll out.">update available</span>`
        : (srvBuild && p.build_id===srvBuild
            ? `<span class="pb-chip pb-chip-ok" title="Running the current build ${esc(srvBuild)}">up to date</span>`
            : ''))
    : `<span class="pb-chip pb-chip-off" title="Predates managed updates — re-install the package once to enable remote updates">manual updates</span>`;
  const updBadge=_pbUpdStateChip(p);
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
      verBadge+updBadge+skewBadge+enrollNote+
      `<span class="pb-spacer"></span>`+
      `<button class="btn-s" onclick='_pbOpenUpdates(${pidq})' title="Update history for this probe">⟳ Updates</button>`+
      `<button class="btn-s rbac-admin" onclick='_pbPickOS(${pidq})' title="Download the pre-configured agent package (pick Windows or Linux; issues a fresh one-time token)">⬇ Package</button>`+
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

// ── Managed updates: per-probe state chip ────────────────────────
function _pbUpdStateChip(p){
  const st=p.update_state||'';
  const map={
    queued:        ['pb-chip-info','update queued'],
    downloading:   ['pb-chip-info','downloading'],
    staged:        ['pb-chip-info','staged'],
    restarting:    ['pb-chip-info','restarting'],
    verifying:     ['pb-chip-info','verifying'],
    rolled_back:   ['pb-chip-warn','rolled back'],
    failed_offline:['pb-chip-err','update failed'],
  };
  const m=map[st];
  if(!m) return '';   // '' or 'succeeded' → no transient chip (the build badge covers it)
  const inflight=['queued','downloading','staged','restarting','verifying'].includes(st);
  const tip=(st==='rolled_back'||st==='failed_offline')
    ? (p.update_error||st) : st;
  return `<span class="pb-chip ${m[0]}" title="${esc(tip)}">${inflight?'⟳ ':''}${esc(m[1])}</span>`;
}

// ── Campaigns list ───────────────────────────────────────────────
// Running campaigns stay pinned at the top (they need attention / Abort).
// Terminal ones (done / halted / aborted) collapse into a default-collapsed
// "Finished" section so they don't pile up above the probe list. The collapse
// state lives in a module var so the 15s auto-refresh doesn't reset it.
let _pbCampHistOpen=false;
async function _pbLoadCampaigns(){
  const box=document.getElementById('pb-campaigns');
  if(!box) return;
  let camps=[];
  try{ const r=await api('GET','/api/probes/campaigns'); camps=r.campaigns||[]; }catch(_){}
  const running  = camps.filter(c=>c.state==='running');
  const finished = camps.filter(c=>c.state!=='running').slice(0,20);  // newest first, capped
  let html='';
  if(running.length){
    html += `<div class="pb-camp-h">Update campaigns</div>`+running.map(_pbCampaignCard).join('');
  }
  if(finished.length){
    // Only flag a halted rollout that targeted the CURRENT agent build — a halt
    // for an older, superseded build is past history, not an actionable warning,
    // so the red badge doesn't linger forever after a successful later update.
    const halted=finished.filter(c=>c.state==='halted' && c.target_build && c.target_build===S._agentBuild).length;
    const open=_pbCampHistOpen;
    html += `<div class="pb-camp-hist">`+
      `<div class="pb-camp-hist-tog" onclick="_pbToggleCampHistory()">`+
        `<span id="pb-camp-hist-chev" style="display:inline-block;transition:transform .15s;transform:rotate(${open?90:0}deg)">▸</span> `+
        `Finished campaigns (${finished.length})`+
        (halted?` <span class="pb-chip pb-chip-err" style="margin-left:4px">${halted} halted</span>`:'')+
      `</div>`+
      `<div id="pb-camp-hist-body" style="display:${open?'flex':'none'};flex-direction:column;gap:10px;margin-top:10px">`+
        finished.map(_pbCampaignCard).join('')+
      `</div>`+
    `</div>`;
  }
  box.innerHTML = html;
}

function _pbToggleCampHistory(){
  _pbCampHistOpen=!_pbCampHistOpen;
  const body=document.getElementById('pb-camp-hist-body');
  const chev=document.getElementById('pb-camp-hist-chev');
  if(body) body.style.display=_pbCampHistOpen?'flex':'none';
  if(chev) chev.style.transform=`rotate(${_pbCampHistOpen?90:0}deg)`;
}

function _pbCampaignCard(c){
  const k=c.counts||{};
  const total=Object.values(k).reduce((a,b)=>a+(b||0),0);
  const done=k.succeeded||0;
  const settled=done+(k.rolled_back||0)+(k.failed_offline||0)+(k.expired||0);
  const pct=total?Math.round(settled/total*100):0;
  const stateCls={running:'pb-chip-info',halted:'pb-chip-err',done:'pb-chip-ok',
                  aborted:'pb-chip-off'}[c.state]||'pb-chip-off';
  const cidq=esc(JSON.stringify(c.id));
  return `<div class="pb-camp">`+
    `<div class="pb-camp-row">`+
      `<span class="pb-chip ${stateCls}">${esc(c.state)}</span>`+
      `<b>${esc(c.name||('Campaign '+c.id))}</b>`+
      `<code class="pb-camp-build" title="Target build">${esc(c.target_build||'')}</code>`+
      `<span class="pb-spacer"></span>`+
      (c.state==='running'?`<button class="btn-s bulk-danger rbac-admin" onclick='_pbAbortCampaign(${cidq})'>Abort</button>`:'')+
    `</div>`+
    `<div class="pb-camp-bar"><span style="width:${pct}%"></span></div>`+
    `<div class="pb-camp-counts">`+
      `<span class="pb-chip pb-chip-ok">✓ ${done} updated</span>`+
      ((k.dispatched||0)?`<span class="pb-chip pb-chip-info">⟳ ${k.dispatched} in progress</span>`:'')+
      ((k.queued||0)?`<span class="pb-chip">${k.queued} queued</span>`:'')+
      ((k.rolled_back||0)?`<span class="pb-chip pb-chip-warn">↩ ${k.rolled_back} rolled back</span>`:'')+
      ((k.failed_offline||0)?`<span class="pb-chip pb-chip-err">✖ ${k.failed_offline} offline</span>`:'')+
      ((k.expired||0)?`<span class="pb-chip pb-chip-off">${k.expired} expired</span>`:'')+
      `<span class="pb-camp-total">${total} probe${total===1?'':'s'}</span>`+
    `</div>`+
  `</div>`;
}

async function _pbAbortCampaign(cid){
  if(!confirm('Abort this campaign? Probes already updating will finish (auto-rollback if needed); no further updates are dispatched.')) return;
  try{ await api('POST',`/api/probes/campaigns/${cid}/abort`); toast('Campaign aborted','info'); }
  catch(e){ toast('Abort failed','err'); }
  _probesLoad();
}

// ── Launch a rollout campaign ────────────────────────────────────
function _pbOpenCampaign(){
  closeM('pb-camp');
  const build=S._agentBuild||'(unknown)';
  const enrolled=Object.values(S.probes).filter(p=>p.status==='enrolled');
  const capable=enrolled.filter(p=>p.supervisor);
  const legacy =enrolled.filter(p=>!p.supervisor);
  const rows=capable.map(p=>{
    const cur=(p.build_id===build);
    return `<label class="pb-camp-pick">`+
      `<input type="checkbox" class="pb-camp-cb" value="${esc(p.probe_id)}" ${(cur||!p.connected)?'':'checked'} ${p.connected?'':'disabled'}>`+
      `<span class="pb-dot ${p.connected?'pb-dot-up':'pb-dot-down'}"></span>`+
      `<span class="pb-camp-pn">${esc(p.name||p.probe_id)}</span>`+
      `<code class="pb-camp-pb">${esc(p.build_id||'?')}${cur?' · current':''}</code>`+
      `${p.connected?'':'<span class="pb-camp-ps">offline</span>'}`+
    `</label>`;
  }).join('') || `<div class="pb-hint">No supervisor-managed probes yet — re-install the package on a probe once to enable remote updates.</div>`;
  const o=document.createElement('div'); o.className='mo'; o.id='pb-camp';
  _overlayClose(o,()=>closeM('pb-camp'));
  o.innerHTML=
    `<div class="mbox" style="max-width:640px">`+
      `<div class="mhd"><span>Update fleet → <code>${esc(build)}</code></span><button class="mx" onclick="closeM('pb-camp')">✕</button></div>`+
      `<div class="mbdy">`+
        `<p class="pb-hint">Rolls out the current agent build to the selected probes. A canary updates first; if it can't re-checkin the update auto-rolls-back and the campaign halts before the rest are touched.</p>`+
        (legacy.length?`<p class="pb-hint pb-warn-text">${legacy.length} enrolled probe(s) can't update remotely yet (legacy install) — re-install the package on them once.</p>`:'')+
        `<div class="pb-camp-list">${rows}</div>`+
        `<div class="pb-camp-policy">`+
          `<div class="fr"><label class="fl">Canary (update first, gate on success)</label><input type="text" id="pbc-canary" value="1"></div>`+
          `<div class="fr"><label class="fl">Batch size (concurrent after canary)</label><input type="text" id="pbc-batch" value="5"></div>`+
          `<div class="fr"><label class="fl">Probation window — must re-checkin within (s)</label><input type="text" id="pbc-prob" value="120"></div>`+
          `<div class="fr"><label class="pb-camp-chk"><input type="checkbox" id="pbc-halt" checked> Halt the whole campaign if a probe rolls back or goes offline</label></div>`+
        `</div>`+
      `</div>`+
      `<div class="mft">`+
        `<button class="btn" onclick="closeM('pb-camp')">Cancel</button>`+
        `<button class="btn primary" onclick="_pbLaunchCampaign()" ${capable.length?'':'disabled'}>Launch rollout</button>`+
      `</div>`+
    `</div>`;
  document.body.appendChild(o);
}

async function _pbLaunchCampaign(){
  const ids=[...document.querySelectorAll('.pb-camp-cb:checked')].map(c=>c.value);
  if(!ids.length){ toast('Select at least one probe','err'); return; }
  const body={
    probe_ids:      ids,
    canary:         parseInt(document.getElementById('pbc-canary').value)||1,
    batch_size:     parseInt(document.getElementById('pbc-batch').value)||5,
    probation_secs: parseInt(document.getElementById('pbc-prob').value)||120,
    halt_on_fail:   document.getElementById('pbc-halt').checked,
  };
  try{
    const r=await api('POST','/api/probes/campaigns',body);
    const sk=(r.skipped&&r.skipped.length)?`, ${r.skipped.length} skipped`:'';
    toast(`Rollout started — ${r.selected} probe(s)${sk}`,'ok');
    closeM('pb-camp');
    _probesLoad();
  }catch(e){ toast(String((e&&e.message)||'Launch failed'),'err'); }
}

// ── Per-probe update history ─────────────────────────────────────
async function _pbOpenUpdates(pid){
  closeM('pb-upd');
  const o=document.createElement('div'); o.className='mo'; o.id='pb-upd';
  _overlayClose(o,()=>closeM('pb-upd'));
  o.innerHTML=`<div class="mbox" style="max-width:640px">`+
    `<div class="mhd"><span>Update history — ${esc(_probeName(pid))}</span><button class="mx" onclick="closeM('pb-upd')">✕</button></div>`+
    `<div class="mbdy" id="pb-upd-body"><div class="pb-hint">Loading…</div></div>`+
  `</div>`;
  document.body.appendChild(o);
  let ups=[];
  try{ const r=await api('GET',`/api/probes/${pid}/updates`); ups=r.updates||[]; }catch(_){}
  const body=document.getElementById('pb-upd-body');
  if(!body) return;
  if(!ups.length){ body.innerHTML=`<div class="pb-hint">No update attempts recorded yet.</div>`; return; }
  body.innerHTML=ups.map(u=>{
    const ok=u.outcome==='success';
    const cls=ok?'pb-chip-ok':(u.outcome==='rolled_back'?'pb-chip-warn':'pb-chip-err');
    const when=u.ts?new Date(u.ts*1000).toLocaleString():'';
    return `<div class="pb-upd-row">`+
      `<span class="pb-chip ${cls}">${esc(u.outcome||'?')}</span>`+
      `<span class="pb-upd-when">${esc(when)}</span>`+
      `<code class="pb-upd-build">${esc(u.from_build||'?')} → ${esc(u.to_build||u.target_build||'?')}</code>`+
      (u.reason?`<span class="pb-upd-reason">${esc(u.reason)}</span>`:'')+
      (ok?'':`<button class="btn-s" onclick="_pbViewLog(${u.id})">View log</button>`)+
    `</div>`;
  }).join('');
}

async function _pbViewLog(rid){
  let rep=null;
  try{ rep=await api('GET',`/api/probes/updates/${rid}`); }catch(_){}
  if(!rep){ toast('Log unavailable','err'); return; }
  closeM('pb-log');
  const o=document.createElement('div'); o.className='mo'; o.id='pb-log';
  _overlayClose(o,()=>closeM('pb-log'));
  o.innerHTML=`<div class="mbox" style="max-width:780px">`+
    `<div class="mhd"><span>Update log — ${esc(rep.target_build||'')}</span><button class="mx" onclick="closeM('pb-log')">✕</button></div>`+
    `<div class="mbdy"><pre class="pb-log">${esc(rep.log||'(no log captured)')}</pre></div>`+
  `</div>`;
  document.body.appendChild(o);
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
        `<p>The easiest path: <b>download the package for your platform</b> — it ships with this token already in <code>config.json</code>.</p>`+
        `<div class="pb-token-box" id="pb-token-val">${esc(token)}</div>`+
        `<p class="pb-hint">Shown once. One-time use, expires in 7 days, bound to this probe. `+
        `Downloading the package later issues a fresh token automatically.</p>`+
      `</div>`+
      `<div class="mft">`+
        `<button class="btn" onclick="_pbCopyToken()">Copy token</button>`+
        `<button class="btn primary" onclick='closeM("pb-token");_pbPickOS(${esc(JSON.stringify(pid))})'>⬇ Download package</button>`+
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
function _pbPickOS(pid){
  // Single "⬇ Package" entry point → choose the host platform, then download.
  // Keeps the card uncluttered and the OS choice in one place (not duplicated
  // across the card buttons and the post-create token modal).
  closeM('pb-os');
  const pidq=esc(JSON.stringify(pid));
  const o=document.createElement('div'); o.className='mo'; o.id='pb-os';
  _overlayClose(o,()=>closeM('pb-os'));
  o.innerHTML=`<div class="mbox" style="max-width:440px">`+
    `<div class="mhd"><span>Download agent package</span><button class="mx" onclick="closeM('pb-os')">✕</button></div>`+
    `<div class="mbdy">`+
      `<p class="pb-hint">Pick the platform for this probe's host — the package ships the matching installer and a fresh one-time token.</p>`+
      `<div style="display:flex;gap:10px;margin-top:4px">`+
        `<button class="btn primary" style="flex:1" onclick='closeM("pb-os");_pbDownload(${pidq},"windows")' title="Scheduled Task installer">⬇ Windows</button>`+
        `<button class="btn primary" style="flex:1" onclick='closeM("pb-os");_pbDownload(${pidq},"linux")' title="systemd installer">⬇ Linux</button>`+
      `</div>`+
    `</div>`+
  `</div>`;
  document.body.appendChild(o);
}

async function _pbDownload(pid, os){
  // Fetch the zip (not a tab navigation) so a server error surfaces as a toast
  // instead of dumping raw JSON onto a blank page. The endpoint re-arms a fresh
  // one-time token inside the zip on every successful download. `os`
  // (windows|linux) selects which installer set ships.
  const q=(os==='windows'||os==='linux')?`?os=${os}`:'';
  const label=os==='windows'?'Windows':os==='linux'?'Linux':'';
  try{
    const r=await fetch(`/api/probes/${encodeURIComponent(pid)}/package${q}`,
                        {headers:{'Accept':'application/zip'}});
    if(!r.ok){
      let msg='Download failed';
      try{ const j=await r.json(); if(j&&j.error) msg=j.error; }catch(_){}
      toast(msg,'err');
      return;
    }
    const blob=await r.blob();
    let fn=`pingwatch-agent${label?'-'+label.toLowerCase():''}.zip`;
    const m=/filename="?([^"]+)"?/.exec(r.headers.get('Content-Disposition')||'');
    if(m) fn=m[1];
    const a=document.createElement('a'), u=URL.createObjectURL(blob);
    a.href=u; a.download=fn; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(u);
    toast(`${label||'Agent'} package downloaded`,'ok');
    setTimeout(_probesLoad, 1500);
  }catch(e){
    toast('Download failed — network error','err');
  }
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
// omitCentral: skip the explicit 'central' pin option — used by the site
// form, where '' already resolves to central (the pin would render as a
// duplicate "Central (this server)" entry).
function _probeSelectHtml(id, current, inheritLabel, omitCentral){
  const probes=Object.values(S.probes)
    .filter(p=>p.status!=='revoked')
    .sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  let cur=current||'';
  if(omitCentral && cur==='central') cur='';   // same meaning at this level
  let opts=`<option value=""${cur===''?' selected':''}>${esc(inheritLabel)}</option>`;
  if(!omitCentral)
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
