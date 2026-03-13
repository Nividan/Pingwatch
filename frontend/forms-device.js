// ── ADD DEVICE MODAL ─────────────────────────────────────────────────────
function openAddDevice(){
  closeM('mad');
  const o=document.createElement('div');o.className='mo';o.id='mad';
  o.onclick=e=>{if(e.target===o)closeM('mad')};
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
      <div class="fr">
        <label style="display:flex;align-items:center;gap:7px;cursor:pointer;font-size:12px;color:var(--text2)">
          <input type="checkbox" id="ad-ap" checked style="width:auto;cursor:pointer"/> Auto-add Ping sensor
        </label>
      </div>
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
  const ap=document.getElementById('ad-ap')?.checked;
  const webhook_url=(document.getElementById('ad-wh')?.value||'').trim();
  if(!name||!host){toast('Name and host are required','err');return;}
  const btn=document.querySelector('#mad .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Adding...';}
  const r=await api('POST','/api/device',{name,host,group,webhook_url});
  if(btn){btn.disabled=false;btn.textContent='Add Device';}
  if(!r.did){toast('Failed to add device','err');return;}
  closeM('mad');
  const devR=await fetch(`/api/device/${r.did}`);
  const dev=await devR.json();
  S.devices[r.did]=dev;
  document.getElementById('emptyMain').style.display='none';
  if(activeMainTab==='devices') document.getElementById('dpanels').style.display='';
  renderDp(dev);renderSidebar();updatePills();
  refreshGroupCounts();
  toast(`Added: ${name}`,'ok');
  if(ap)await addSensorDirect(r.did,`Ping ${host}`,'ping',host,null,null,5,4,true);
}

// ── EDIT DEVICE ──────────────────────────────────────────────────────────

function openEditDevice(did){
  const dev = S.devices[did];
  if(!dev) return;
  closeM('dwo');
  closeM('med');
  const o = document.createElement('div'); o.className='mo'; o.id='med';
  o.onclick = e => { if(e.target===o) closeM('med'); };
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
          <input type="text" id="ed-g" value="${esc(dev.group||'Default Group')}" autocomplete="off"/>
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
  if(!name || !host){ toast('Name and host are required','err'); return; }
  const btn=document.querySelector('#med .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  const r = await api('PATCH', `/api/device/${did}`, {name, host, group, webhook_url, alerts_muted});
  if(btn){btn.disabled=false;btn.textContent='Save Changes';}
  if(!r || r.error){ toast('Failed to save changes','err'); return; }
  closeM('med');
  const dev = S.devices[did];
  if(dev){ dev.name = name; dev.host = host; dev.group = group; dev.webhook_url = webhook_url; dev.alerts_muted = alerts_muted; renderDp(dev); }
  renderSidebar();
  updatePills();
  refreshGroupCounts();
  toast(`Saved: ${name}`, 'ok');
}
