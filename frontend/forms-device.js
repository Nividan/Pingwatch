// ── ADD DEVICE MODAL ─────────────────────────────────────────────────────
function openAddDevice(){
  closeM('mad');
  const o=document.createElement('div');o.className='mo';o.id='mad';
  _overlayClose(o, ()=>closeM('mad'));
  o.innerHTML=`
  <div class="mbox">
    <div class="mhd"><div class="mttl">Add Device</div><button class="mclose" onclick="closeM('mad')">✕</button></div>
    <div class="mbdy">
      <div class="fr"><label class="fl">Device Name</label>
        <input type="text" id="ad-n" placeholder="Router, Server, DNS…" autocomplete="off"/></div>
      <div class="fgrid">
        <div class="fr"><label class="fl">Host / IP Address</label>
          <input type="text" id="ad-h" placeholder="192.168.1.1" autocomplete="off"/></div>
        <div class="fr"><label class="fl">Group</label>
          <input type="text" id="ad-g" placeholder="Default Group" autocomplete="off"/></div>
      </div>
      <div class="fr"><label class="fl">Webhook URL <span style="color:var(--text3);font-weight:400">(optional — POST on status change)</span></label>
        <input type="text" id="ad-wh" placeholder="https://hooks.slack.com/…" autocomplete="off"/></div>
      <details class="dev-creds" style="margin-top:10px">
        <summary style="cursor:pointer;color:var(--text2);font-size:13px;font-weight:500;user-select:none">Default Credentials <span style="color:var(--text3);font-weight:400">(optional — pre-fills new sensors)</span></summary>
        <div style="margin-top:8px;display:flex;flex-direction:column;gap:8px">
          <div class="fgrid">
            <div class="fr"><label class="fl">SNMP Community</label>
              <input type="text" id="ad-snmp-comm" placeholder="public" autocomplete="off"/></div>
            <div class="fr"><label class="fl">SNMP Version</label>
              <select id="ad-snmp-ver">
                <option value="">— any —</option>
                <option value="2c">v2c</option>
                <option value="1">v1</option>
                <option value="3">v3 (community)</option>
              </select></div>
          </div>
          <div class="fgrid">
            <div class="fr"><label class="fl">VMware Username</label>
              <input type="text" id="ad-vmw-user" placeholder="administrator@vsphere.local" autocomplete="off"/></div>
            <div class="fr"><label class="fl">VMware Password</label>
              <input type="password" id="ad-vmw-pass" placeholder="" autocomplete="new-password"/></div>
          </div>
        </div>
      </details>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mad')">Cancel</button>
      <button class="btn-p" onclick="submitAddDevice()">Add Device</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('ad-n')?.focus(),50);
  ['ad-n','ad-h'].forEach(id=>document.getElementById(id)?.addEventListener('keydown',e=>{if(e.key==='Enter')submitAddDevice()}));
}

async function submitAddDevice(){
  const name=(document.getElementById('ad-n')?.value||'').trim();
  const host=(document.getElementById('ad-h')?.value||'').trim().replace(/^https?:\/\//,'').split('/')[0].toLowerCase();
  const group=(document.getElementById('ad-g')?.value||'Default Group').trim();
  const webhook_url=(document.getElementById('ad-wh')?.value||'').trim();
  const snmp_community_default=(document.getElementById('ad-snmp-comm')?.value||'').trim();
  const snmp_version_default=document.getElementById('ad-snmp-ver')?.value||'';
  const vmware_user_default=(document.getElementById('ad-vmw-user')?.value||'').trim();
  const vmware_password_default=document.getElementById('ad-vmw-pass')?.value||'';
  if(!name||!host){toast('Name and host are required','err');return;}
  const btn=document.querySelector('#mad .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Adding...';}
  const payload={name,host,group,webhook_url};
  if(snmp_community_default) payload.snmp_community_default=snmp_community_default;
  if(snmp_version_default) payload.snmp_version_default=snmp_version_default;
  if(vmware_user_default) payload.vmware_user_default=vmware_user_default;
  if(vmware_password_default) payload.vmware_password_default=vmware_password_default;
  let r;
  try{
    r=await api('POST','/api/device',payload);
  }catch(e){
    toast('Failed to add device','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Add Device';}
  }
  if(!r.did){toast('Failed to add device','err');return;}
  closeM('mad');
  const devR=await fetch(`/api/device/${r.did}`);
  const dev=await devR.json();
  S.devices[r.did]=dev;
  document.getElementById('emptyMain').style.display='none';
  if(activeMainTab==='devices'){
    document.getElementById('dpanels').style.display='';
    document.getElementById('devActBar').style.display='';
  }
  renderDp(dev);updatePills();
  refreshGroupCounts();
  toast(`Added: ${name}`,'ok');
  openScanModal(r.did);
}

// ── EDIT DEVICE ──────────────────────────────────────────────────────────

let _edSecIps = [];   // secondary IPs being edited

function openEditDevice(did){
  const dev = S.devices[did];
  if(!dev) return;
  closeM('dwo');
  closeM('med');
  _edSecIps = [...(dev.secondary_ips || [])];
  const _edGroups = [...new Set(Object.values(S.devices).map(d=>d.group).filter(Boolean))].sort();
  const _edGroupItems = _edGroups.map(g =>
    `<div class="grp-dd-item${g===(dev.group||'Default Group')?' cur':''}" data-g="${esc(g.toLowerCase())}" onmousedown="event.preventDefault();_edgPick('${esc(g)}')">${esc(g)}</div>`
  ).join('');
  const o = document.createElement('div'); o.className='mo'; o.id='med';
  _overlayClose(o, ()=>closeM('med'));
  o.innerHTML = `
  <div class="mbox">
    <div class="mhd">
      <div class="mttl">Edit Device</div>
      <button class="mclose" onclick="closeM('med')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr">
        <label class="fl">Device Name</label>
        <input type="text" id="ed-n" value="${esc(dev.name)}" autocomplete="off"/>
      </div>
      <div class="fgrid">
        <div class="fr">
          <label class="fl">Host / IP Address</label>
          <input type="text" id="ed-h" value="${esc(dev.host)}" autocomplete="off"/>
        </div>
        <div class="fr">
          <label class="fl">Group</label>
          <div style="position:relative">
            <input type="text" id="ed-g" value="${esc(dev.group||'Default Group')}" autocomplete="off"
                   style="padding-right:28px"
                   onfocus="_edgShow()" oninput="_edgFilter(this.value)"/>
            <button class="grp-dd-arrow" tabindex="-1" onmousedown="event.preventDefault();_edgToggle()">▾</button>
            <div id="ed-g-dd" class="grp-dd" style="display:none">${_edGroupItems}</div>
          </div>
        </div>
      </div>
      <details class="dev-creds" style="margin-top:10px"${_edSecIps.length?' open':''}>
        <summary style="cursor:pointer;color:var(--text2);font-size:13px;font-weight:500;user-select:none">Secondary IPs <span style="color:var(--text3);font-weight:400">(${_edSecIps.length})</span></summary>
        <div style="margin-top:8px">
          <div id="ed-sip-list" style="max-height:160px;overflow-y:auto;margin-bottom:6px"></div>
          <div style="display:flex;gap:6px">
            <input type="text" id="ed-sip-input" placeholder="e.g. 10.0.0.5" autocomplete="off"
                   style="flex:1" onkeydown="if(event.key==='Enter'){event.preventDefault();_edSipAdd()}"/>
            <button class="btn-s" type="button" onclick="_edSipAdd()" style="white-space:nowrap">+ Add</button>
          </div>
        </div>
      </details>
      <div class="fr">
        <label class="fl">Webhook URL <span style="color:var(--text3);font-weight:400">(optional)</span></label>
        <input type="text" id="ed-wh" value="${esc(dev.webhook_url||'')}" placeholder="https://hooks.slack.com/…" autocomplete="off"/>
      </div>
      <div class="fr" style="margin-top:4px">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
          <input type="checkbox" id="ed-am" ${dev.alerts_muted?'checked':''}>
          <span class="fl" style="margin:0">🔕 Mute all alerts for this device</span>
        </label>
        <div style="font-size:11px;color:var(--text3);margin-top:3px;margin-left:24px">Silences DOWN / recovery / threshold alerts for every sensor in this device.</div>
      </div>
      <div class="alrt-section" style="margin-top:10px">
        <div class="alrt-section-hdr">Alert Profile</div>
        <div id="ed-profile-body" style="font-size:12px;color:var(--text3)">Loading\u2026</div>
      </div>
      <details class="dev-creds" style="margin-top:10px"${(dev.snmp_community_default||dev.vmware_user_default||dev.has_vmware_password_default)?' open':''}>
        <summary style="cursor:pointer;color:var(--text2);font-size:13px;font-weight:500;user-select:none">Default Credentials <span style="color:var(--text3);font-weight:400">(pre-fills new sensors)</span></summary>
        <div style="margin-top:8px;display:flex;flex-direction:column;gap:8px">
          <div class="fgrid">
            <div class="fr"><label class="fl">SNMP Community</label>
              <input type="text" id="ed-snmp-comm" value="${esc(dev.snmp_community_default||'')}" placeholder="public" autocomplete="off"/></div>
            <div class="fr"><label class="fl">SNMP Version</label>
              <select id="ed-snmp-ver">
                <option value="" ${!dev.snmp_version_default?'selected':''}>— any —</option>
                <option value="2c" ${dev.snmp_version_default==='2c'?'selected':''}>v2c</option>
                <option value="1"  ${dev.snmp_version_default==='1'?'selected':''}>v1</option>
                <option value="3"  ${dev.snmp_version_default==='3'?'selected':''}>v3 (community)</option>
              </select></div>
          </div>
          <div class="fgrid">
            <div class="fr"><label class="fl">VMware Username</label>
              <input type="text" id="ed-vmw-user" value="${esc(dev.vmware_user_default||'')}" placeholder="administrator@vsphere.local" autocomplete="off"/></div>
            <div class="fr"><label class="fl">VMware Password</label>
              <input type="password" id="ed-vmw-pass" placeholder="${dev.has_vmware_password_default?'(unchanged)':''}" autocomplete="new-password"/></div>
          </div>
        </div>
      </details>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('med')">Cancel</button>
      <button class="btn-p" onclick="submitEditDevice('${did}')">Save Changes</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ed-n')?.focus(), 50);
  ['ed-n','ed-h','ed-g'].forEach(id =>
    document.getElementById(id)?.addEventListener('keydown', e => {
      if(e.key === 'Enter') submitEditDevice(did);
    })
  );
  document.getElementById('ed-g')?.addEventListener('blur', () => setTimeout(_edgHide, 150));
  _edSipRender();
  _loadDeviceProfileSection(did);
}

async function _loadDeviceProfileSection(did) {
  const body = document.getElementById('ed-profile-body');
  if (!body) return;
  try {
    const dev = S.devices[did];
    const r   = await api('GET', '/api/alert/profiles');
    const all = r.profiles || [];
    const devProf   = all.find(p => p.scope_type === 'device' && p.scope_value === did);
    const groupProf = dev && all.find(p => p.scope_type === 'group' && p.scope_value === (dev.group || 'Default Group'));
    const globalProf = all.find(p => p.scope_type === 'global');
    const inherited  = groupProf || globalProf;

    if (devProf) {
      body.innerHTML = `
        <div style="margin-bottom:10px">
          <span class="alrt-override-badge">Override</span>
          <span style="margin-left:8px;color:var(--text)">${esc(devProf.name)}</span>
          <span style="margin-left:6px;color:var(--text3);font-size:11px">(${devProf.stages.length} stage${devProf.stages.length === 1 ? '' : 's'})</span>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn-s" onclick="_editProfileFromDevice(${devProf.id})">Edit profile\u2026</button>
          <button class="btn-s" onclick="_resetDeviceProfile('${esc(did)}', ${devProf.id})">Reset to inherited</button>
        </div>`;
    } else {
      const inheritedFrom = inherited
        ? `${inherited.scope_type === 'group' ? 'Group' : 'Global'} profile <strong style="color:var(--text)">${esc(inherited.name)}</strong>`
        : `(none \u2014 no profile resolved)`;
      body.innerHTML = `
        <div style="margin-bottom:10px">
          <span class="alrt-inherit-badge">Inherited</span>
          <span style="margin-left:8px">${inheritedFrom}</span>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn-s" onclick="_overrideDeviceProfile('${esc(did)}')">Override at device level</button>
          ${inherited ? `<button class="btn-s" onclick="_editProfileFromDevice(${inherited.id})">View inherited profile</button>` : ''}
        </div>`;
    }
  } catch (e) {
    if (body) body.innerHTML = `<span style="color:var(--down)">Failed to load profile</span>`;
  }
}

function _editProfileFromDevice(profileId) {
  closeM('med');
  if (typeof openProfileEditor === 'function') openProfileEditor(profileId);
  else toast('Open the Alerting page to edit profiles', 'err');
}

function _overrideDeviceProfile(did) {
  closeM('med');
  if (typeof openProfileEditor === 'function')
    openProfileEditor(null, { scope_type: 'device', scope_value: did });
  else toast('Open the Alerting page to create profiles', 'err');
}

async function _resetDeviceProfile(did, profileId) {
  if (!confirm('Delete the device-scoped alert profile?\nThis device will fall back to the group or global profile.')) return;
  try {
    await api('DELETE', '/api/alert/profile/' + profileId);
    toast('Device profile reset', 'ok');
    _loadDeviceProfileSection(did);
  } catch (e) {
    toast('Reset failed: ' + (e.message || e), 'err');
  }
}

function _edgShow(){
  const dd=document.getElementById('ed-g-dd');
  if(!dd) return;
  dd.style.display='';
  _edgFilter('');
}
function _edgToggle(){
  const dd=document.getElementById('ed-g-dd');
  if(dd && dd.style.display!=='none') _edgHide();
  else _edgShow();
}
function _edgHide(){
  const dd=document.getElementById('ed-g-dd');
  if(dd) dd.style.display='none';
}
function _edgFilter(v){
  const dd=document.getElementById('ed-g-dd');
  if(!dd) return;
  const q=v.trim().toLowerCase();
  let any=false;
  dd.querySelectorAll('.grp-dd-item').forEach(el=>{
    const show=!q||el.dataset.g.includes(q);
    el.style.display=show?'':'none';
    if(show) any=true;
  });
  dd.style.display=any?'':'none';
}
function _edgPick(g){
  const inp=document.getElementById('ed-g');
  if(inp) inp.value=g;
  _edgHide();
}

async function submitEditDevice(did){
  const name  = (document.getElementById('ed-n')?.value || '').trim();
  const host  = (document.getElementById('ed-h')?.value || '').trim().replace(/^https?:\/\//,'').split('/')[0].toLowerCase();
  const group = (document.getElementById('ed-g')?.value || 'Default Group').trim();
  const webhook_url  = (document.getElementById('ed-wh')?.value || '').trim();
  const alerts_muted = document.getElementById('ed-am')?.checked || false;
  const snmp_community_default = (document.getElementById('ed-snmp-comm')?.value || '').trim();
  const snmp_version_default   = document.getElementById('ed-snmp-ver')?.value || '';
  const vmware_user_default    = (document.getElementById('ed-vmw-user')?.value || '').trim();
  const vmware_password_default = document.getElementById('ed-vmw-pass')?.value || '';
  if(!name || !host){ toast('Name and host are required','err'); return; }
  const btn=document.querySelector('#med .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  const payload = {name, host, group, webhook_url, alerts_muted,
    snmp_community_default, snmp_version_default, vmware_user_default,
    secondary_ips: _edSecIps};
  if(vmware_password_default) payload.vmware_password_default = vmware_password_default;
  let r;
  try{
    r = await api('PATCH', `/api/device/${did}`, payload);
  }catch(e){
    toast('Failed to save changes','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save Changes';}
  }
  if(!r || r.error){ toast('Failed to save changes','err'); return; }
  closeM('med');
  const dev = S.devices[did];
  if(dev){ dev.name = name; dev.host = host; dev.group = group; dev.webhook_url = webhook_url; dev.alerts_muted = alerts_muted; dev.snmp_community_default = snmp_community_default; dev.snmp_version_default = snmp_version_default; dev.vmware_user_default = vmware_user_default; dev.secondary_ips = _edSecIps; if(vmware_password_default) dev.has_vmware_password_default = true; renderDp(dev); }
  pruneEmptyGroups();
  updatePills();
  refreshGroupCounts();
  toast(`Saved: ${name}`, 'ok');
}

// ── Secondary IPs helpers ─────────────────────────────────────────────
function _edSipRender(){
  const el = document.getElementById('ed-sip-list');
  if(!el) return;
  if(!_edSecIps.length){ el.innerHTML = '<div style="font-size:11px;color:var(--text3);padding:4px 0">No secondary IPs</div>'; return; }
  el.innerHTML = _edSecIps.map((ip,i) =>
    `<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--border)">
      <span style="flex:1;font-size:12px;font-family:monospace">${esc(ip)}</span>
      <button class="btn-s" style="padding:1px 6px;font-size:11px;color:var(--down)" onclick="_edSipRemove(${i})">✕</button>
    </div>`
  ).join('');
  // update count in summary
  const summary = document.querySelector('#med details.dev-creds:first-of-type summary span');
  if(summary) summary.textContent = `(${_edSecIps.length})`;
}
function _edSipAdd(){
  const inp = document.getElementById('ed-sip-input');
  if(!inp) return;
  const ip = inp.value.trim().toLowerCase();
  if(!ip){ toast('Enter an IP address','err'); return; }
  if(!/^[\w.\-:]+$/.test(ip)){ toast('Invalid IP format','err'); return; }
  const primary = (document.getElementById('ed-h')?.value || '').trim().toLowerCase();
  if(ip === primary){ toast('Already the primary host','err'); return; }
  if(_edSecIps.includes(ip)){ toast('Already in the list','err'); return; }
  if(_edSecIps.length >= 50){ toast('Maximum 50 secondary IPs','err'); return; }
  _edSecIps.push(ip);
  inp.value = '';
  _edSipRender();
}
function _edSipRemove(idx){
  _edSecIps.splice(idx, 1);
  _edSipRender();
}
