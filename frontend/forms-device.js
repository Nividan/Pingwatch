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

function openEditDevice(did){
  const dev = S.devices[did];
  if(!dev) return;
  closeM('dwo');
  closeM('med');
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
          <input type="text" id="ed-g" value="${esc(dev.group||'Default Group')}" list="ed-g-list" autocomplete="off"/>
          <datalist id="ed-g-list">${
            [...new Set(Object.values(S.devices).map(d=>d.group).filter(Boolean))].sort()
            .map(g=>`<option value="${esc(g)}"></option>`).join('')
          }</datalist>
        </div>
      </div>
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
    snmp_community_default, snmp_version_default, vmware_user_default};
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
  if(dev){ dev.name = name; dev.host = host; dev.group = group; dev.webhook_url = webhook_url; dev.alerts_muted = alerts_muted; dev.snmp_community_default = snmp_community_default; dev.snmp_version_default = snmp_version_default; dev.vmware_user_default = vmware_user_default; if(vmware_password_default) dev.has_vmware_password_default = true; renderDp(dev); }
  updatePills();
  refreshGroupCounts();
  toast(`Saved: ${name}`, 'ok');
}
