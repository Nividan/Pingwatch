// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? ADD DEVICE MODAL �?�?�?�?�?�?�?�?�?�?�?�?
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

// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? EDIT DEVICE �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?

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
  // Update local state and re-render (no extra round-trip needed)
  const dev = S.devices[did];
  if(dev){ dev.name = name; dev.host = host; dev.group = group; dev.webhook_url = webhook_url; dev.alerts_muted = alerts_muted; renderDp(dev); }
  renderSidebar();
  updatePills();
  refreshGroupCounts();
  toast(`Saved: ${name}`, 'ok');
}



const PORT_CHIPS = [['HTTP','80'],['HTTPS','443'],['SSH','22'],['FTP','21'],['DNS','53'],
  ['RDP','3389'],['SMTP','25'],['MySQL','3306'],['PgSQL','5432'],['Redis','6379'],
  ['Mongo','27017'],['LDAP','389']];

// Render the shared sensor fields HTML (used by both Add and Edit)
function sensorFormHTML(dev, s=null) {
  const defHost = s?.host || dev?.host || '';
  const curType = s?.stype || 'ping';
  return `
  <div class="fr">
    <label class="fl">Sensor Type</label>
    <div class="stp">
      <div class="stpo ${curType==='ping'?'sel':''}" data-t="ping" onclick="selType('ping')">
        <div class="stpo-ico">◉</div><div class="stpo-nm">PING</div><div class="stpo-ds">ICMP echo</div>
      </div>
      <div class="stpo ${curType==='tcp'?'sel':''}" data-t="tcp" onclick="selType('tcp')">
        <div class="stpo-ico">⇌</div><div class="stpo-nm">TCP PORT</div><div class="stpo-ds">Port check</div>
      </div>
      <div class="stpo ${curType==='http'?'sel':''}" data-t="http" onclick="selType('http')">
        <div class="stpo-ico">◈</div><div class="stpo-nm">HTTP/S</div><div class="stpo-ds">Web response</div>
      </div>
      <div class="stpo ${curType==='snmp'?'sel':''}" data-t="snmp" onclick="selType('snmp')">
        <div class="stpo-ico">◎</div><div class="stpo-nm">SNMP</div><div class="stpo-ds">OID polling</div>
      </div>
      <div class="stpo ${curType==='dns'?'sel':''}" data-t="dns" onclick="selType('dns')">
        <div class="stpo-ico">⬡</div><div class="stpo-nm">DNS</div><div class="stpo-ds">Record lookup</div>
      </div>
      <div class="stpo ${curType==='tls'?'sel':''}" data-t="tls" onclick="selType('tls')">
        <div class="stpo-ico">T</div><div class="stpo-nm">TLS</div><div class="stpo-ds">Cert expiry</div>
      </div>
      <div class="stpo ${curType==='http_keyword'?'sel':''}" data-t="http_keyword" onclick="selType('http_keyword')">
        <div class="stpo-ico">K</div><div class="stpo-nm">HTTP KW</div><div class="stpo-ds">Keyword check</div>
      </div>
      <div class="stpo ${curType==='banner'?'sel':''}" data-t="banner" onclick="selType('banner')">
        <div class="stpo-ico">B</div><div class="stpo-nm">BANNER</div><div class="stpo-ds">TCP banner</div>
      </div>
    </div>
    <input type="hidden" id="as-t" value="${curType}"/>
  </div>
  <div class="fr"><label class="fl">Sensor Name</label>
    <input type="text" id="as-n" value="${esc(s?.name||'')}" placeholder="Ping, HTTPS health, sysDescr…" autocomplete="off"/></div>
  <!-- PING -->
  <div class="fg ${curType==='ping'?'vis':''}" id="fg-ping">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host / IP</label>
        <input type="text" id="as-ph" value="${esc(defHost)}" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Timeout (s)</label>
        <input type="number" id="as-pto" value="${s?.timeout||4}" min="1" max="30"/></div>
    </div>
  </div>
  <!-- TCP -->
  <div class="fg ${curType==='tcp'?'vis':''}" id="fg-tcp">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host / IP</label>
        <input type="text" id="as-th" value="${esc(defHost)}" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Port Number</label>
        <input type="number" id="as-tp" value="${s?.port||''}" placeholder="80" min="1" max="65535"/></div>
    </div>
    <div class="fr"><label class="fl">Common Ports</label>
      <div class="port-chips">
        ${PORT_CHIPS.map(([n,p])=>`<div class="pc" onclick="document.getElementById('as-tp').value='${p}'">${n} ${p}</div>`).join('')}
      </div>
    </div>
  </div>
  <!-- HTTP -->
  <div class="fg ${curType==='http'?'vis':''}" id="fg-http">
    <div class="fr"><label class="fl">URL</label>
      <input type="text" id="as-hu" value="${esc(s?.url||'')}" placeholder="https://example.com/health" autocomplete="off"/>
      <div class="fh">Include http:// or https://</div>
    </div>
    <div class="fr" style="margin-top:4px">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;color:var(--text2)">
        <input type="checkbox" id="as-vssl" ${s?.verify_ssl===false?'':'checked'} style="width:auto;cursor:pointer"/>
        Verify SSL certificate
      </label>
      <div class="fh">Uncheck to ignore self-signed / expired certs</div>
    </div>
    <div class="fr" style="margin-top:4px">
      <label class="fl">Expected Status Code <span style="color:var(--text3);font-weight:400">(0 = any 2xx–3xx)</span></label>
      <input type="number" id="as-xstatus" value="${s?.http_expected_status||0}" min="0" max="599" style="max-width:120px"/>
    </div>
  </div>
  <!-- SNMP -->
  <div class="fg ${curType==='snmp'?'vis':''}" id="fg-snmp">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host / IP</label>
        <input type="text" id="as-sh" value="${esc(defHost)}" autocomplete="off"/></div>
      <div class="fr"><label class="fl">UDP Port</label>
        <input type="number" id="as-sp" value="${s?.port||161}" min="1" max="65535"/></div>
    </div>
    <div class="fgrid">
      <div class="fr"><label class="fl">Community String</label>
        <input type="text" id="as-sc" value="${esc(s?.snmp_community||'public')}" placeholder="public" autocomplete="off"/></div>
      <div class="fr"><label class="fl">SNMP Version</label>
        <select id="as-sv">
          <option value="2c" ${(s?.snmp_version||'2c')==='2c'?'selected':''}>v2c</option>
          <option value="1"  ${s?.snmp_version==='1'?'selected':''}>v1</option>
          <option value="3"  ${s?.snmp_version==='3'?'selected':''}>v3 (community)</option>
        </select>
      </div>
    </div>
    <div class="fr" style="margin-top:6px"><label class="fl">Common OIDs</label>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <select id="as-oid-vendor" style="max-width:210px" onchange="snmpVendorChange()">
          <option value="">— Vendor —</option>
        </select>
        <select id="as-oid-pick" style="flex:1;min-width:180px" onchange="snmpOidPick()" disabled>
          <option value="">— Select OID —</option>
        </select>
      </div>
      <div class="fh" id="as-oid-unit" style="min-height:14px"></div>
    </div>
    <div class="fr" style="margin-top:2px">
      <div style="display:flex;gap:8px;align-items:center">
        <button class="dp-btn" type="button" onclick="discoverInterfaces()" id="as-disc-btn">⊕ Discover Interfaces</button>
        <span id="as-iface-status" style="font-size:11px;color:var(--text3)"></span>
      </div>
      <div id="as-iface-list" style="display:none;margin-top:8px"></div>
    </div>
    <div class="fr"><label class="fl">OID</label>
      <input type="text" id="as-oid" value="${esc(s?.snmp_oid||'1.3.6.1.2.1.1.1.0')}" placeholder="1.3.6.1.2.1.1.1.0" autocomplete="off"/>
      <div class="fh" id="as-oid-unit2" style="min-height:14px">Type or paste an OID, choose from picker above, or use Discover Interfaces.</div>
    </div>
  </div>
  <!-- DNS -->
  <div class="fg ${curType==='dns'?'vis':''}" id="fg-dns">
    <div class="fgrid">
      <div class="fr"><label class="fl">Query (hostname)</label>
        <input type="text" id="as-dq" value="${esc(s?.dns_query||defHost)}" placeholder="example.com" autocomplete="off"/>
        <div class="fh">The hostname or domain to resolve</div>
      </div>
      <div class="fr"><label class="fl">Record Type</label>
        <select id="as-drt">
          ${['A','AAAA','CNAME','MX','NS','TXT','PTR'].map(r=>`<option value="${r}" ${(s?.dns_record_type||'A')===r?'selected':''}>${r}</option>`).join('')}
        </select>
      </div>
    </div>
    <div class="fgrid">
      <div class="fr"><label class="fl">DNS Server (optional)</label>
        <input type="text" id="as-ds" value="${esc(s?.dns_server||'')}" placeholder="8.8.8.8 (leave blank for system)" autocomplete="off"/>
        <div class="fh">Leave blank to use system resolver</div>
      </div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-dp" value="${s?.port||53}" min="1" max="65535"/>
      </div>
    </div>
  </div>
  <!-- TLS -->
  <div class="fg ${curType==='tls'?'vis':''}" id="fg-tls">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host</label>
        <input type="text" id="as-tlsh" value="${esc(s?.host||defHost)}" placeholder="example.com" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-tlsp" value="${s?.port||443}" min="1" max="65535"/></div>
    </div>
  </div>
  <!-- HTTP KEYWORD -->
  <div class="fg ${curType==='http_keyword'?'vis':''}" id="fg-http_keyword">
    <div class="fr"><label class="fl">URL</label>
      <input type="text" id="as-kwu" value="${esc(s?.url||'')}" placeholder="https://example.com" autocomplete="off"/>
      <div class="fh">Include http:// or https://</div>
    </div>
    <div class="fr"><label class="fl">Keyword</label>
      <input type="text" id="as-kww" value="${esc(s?.keyword||'')}" placeholder="Expected text in response body" autocomplete="off"/></div>
    <div class="fr" style="margin-top:4px">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;color:var(--text2)">
        <input type="checkbox" id="as-kwssl" ${s?.verify_ssl===false?'':'checked'} style="width:auto;cursor:pointer"/>
        Verify SSL certificate
      </label>
    </div>
    <div class="fr" style="margin-top:4px">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;color:var(--text2)">
        <input type="checkbox" id="as-kwcase" ${s?.keyword_case?'checked':''} style="width:auto;cursor:pointer"/>
        Case-sensitive match
      </label>
    </div>
  </div>
  <!-- BANNER -->
  <div class="fg ${curType==='banner'?'vis':''}" id="fg-banner">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host</label>
        <input type="text" id="as-bnh" value="${esc(s?.host||defHost)}" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-bnp" value="${s?.port||21}" min="1" max="65535"/></div>
    </div>
    <div class="fr"><label class="fl">Banner Regex <span style="color:var(--text3);font-weight:400">(optional)</span></label>
      <input type="text" id="as-bnr" value="${esc(s?.banner_regex||'')}" placeholder="Leave blank — any banner = UP" autocomplete="off"/></div>
  </div>
  <!-- Alert Thresholds & Debounce -->
  <details style="margin-top:8px">
    <summary style="cursor:pointer;font-size:12px;color:var(--text2);padding:4px 0;user-select:none">
      &#9658; Alert Thresholds &amp; Debounce
    </summary>
    <div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
      <div class="fgrid">
        <div class="fr"><label class="fl">Fail After (probes)</label>
          <input type="number" id="as-fa" value="${s?.fail_after||(window._snrDef?.fail_after||1)}" min="1" max="60" style="max-width:100px"/>
          <div class="fh">Consecutive failures before DOWN alert</div>
        </div>
        <div class="fr"><label class="fl">Recover After (probes)</label>
          <input type="number" id="as-ra" value="${s?.recover_after||(window._snrDef?.recover_after||1)}" min="1" max="60" style="max-width:100px"/>
          <div class="fh">Consecutive successes before RECOVERED</div>
        </div>
      </div>
      <div class="fgrid">
        <div class="fr"><label class="fl">${curType==='tls'?'Warn Days (cert expiry)':curType==='snmp'?'Warn Value':'Warn Latency (ms)'}</label>
          <input type="number" id="as-wms" value="${s?.warn_ms||(window._snrTypeDefaults?.[curType]?.warn_ms||_SDR_WARN_DEF[curType]||'')}" placeholder="${curType==='snmp'||curType==='tls'?'e.g. 100':'e.g. 200'}" min="1" style="max-width:100px"/>
        </div>
        <div class="fr"><label class="fl">${curType==='tls'?'Crit Days (cert expiry)':curType==='snmp'?'Crit Value':'Crit Latency (ms)'}</label>
          <input type="number" id="as-cms" value="${s?.crit_ms||(window._snrTypeDefaults?.[curType]?.crit_ms||_SDR_CRIT_DEF[curType]||'')}" placeholder="${curType==='snmp'||curType==='tls'?'e.g. 50':'e.g. 500'}" min="1" style="max-width:100px"/>
        </div>
      </div>
      <div class="fgrid">
        <div class="fr"><label class="fl">Warn Loss %</label>
          <input type="number" id="as-lwp" value="${s?.loss_warn_pct||0}" min="0" max="100" style="max-width:100px"/>
        </div>
        <div class="fr"><label class="fl">Crit Loss %</label>
          <input type="number" id="as-lcp" value="${s?.loss_crit_pct||0}" min="0" max="100" style="max-width:100px"/>
        </div>
      </div>
      <div class="fr" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
          <input type="checkbox" id="as-am" ${s?.alerts_muted?'checked':''}>
          <span class="fl" style="margin:0">🔕 Mute alerts for this sensor</span>
        </label>
        <div style="font-size:11px;color:var(--text3);margin-top:3px;margin-left:24px">Probing continues — no DOWN / recovery / threshold events are fired.</div>
      </div>
    </div>
  </details>
  <div class="fgrid" style="margin-top:4px">
    <div class="fr"><label class="fl">Interval (s)</label>
      <input type="number" id="as-iv" value="${s?.interval||(window._snrDef?.interval||5)}" min="1" max="300"/></div>
    <div class="fr"><label class="fl">Timeout (s)</label>
      <input type="number" id="as-tmo" value="${s?.timeout||(window._snrDef?.timeout||4)}" min="1" max="60"/></div>
  </div>`;
}

// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? ADD SENSOR MODAL �?�?�?�?�?�?�?�?�?�?�?�?
function openAddSensor(did){
  const dev=S.devices[did];if(!dev)return;
  window._ifaceDid=did;
  window._snrAddMode=true;
  closeM('mas');
  const o=document.createElement('div');o.className='mo';o.id='mas';
  o.onclick=e=>{if(e.target===o)closeM('mas')};
  o.innerHTML=`
  <div class="mbox" style="max-width:560px">
    <div class="mhd">
      <div class="mttl">Add Sensor — <span style="color:var(--text2)">${esc(dev.name)}</span></div>
      <button class="mclose" onclick="closeM('mas')">✕</button>
    </div>
    <div class="mbdy" style="max-height:70vh;overflow-y:auto">
      ${sensorFormHTML(dev)}
      <div class="fr" style="margin-top:4px"><label class="fl">Start Immediately</label>
        <select id="as-si"><option value="1">Yes — start now</option><option value="0">No — manual</option></select>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mas')">Cancel</button>
      <button class="btn-p" onclick="submitAddSensor('${did}')">Add Sensor</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>{
    document.getElementById('as-n')?.focus();
    const initType = document.getElementById('as-t')?.value || 'ping';
    _applyTypeDefaults(initType);
    if(initType==='snmp') _snmpLoadVendors();
  },50);
}

//�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? EDIT SENSOR MODAL �?�?�?�?�?�?�?�?�?�?�?�?
function openEditSensor(did, sid){
  const key=`${did}/${sid}`;
  const s=S.sensors[key];
  if(!s)return;
  const dev=S.devices[did];
  window._ifaceDid=did;
  window._snrAddMode=false;
  closeM('mes');
  const o=document.createElement('div');o.className='mo';o.id='mes';
  o.onclick=e=>{if(e.target===o)closeM('mes')};
  o.innerHTML=`
  <div class="mbox" style="max-width:560px">
    <div class="mhd">
      <div class="mttl">Edit Sensor — <span style="color:var(--text2)">${esc(s.name)}</span></div>
      <button class="mclose" onclick="closeM('mes')">✕</button>
    </div>
    <div class="mbdy" style="max-height:70vh;overflow-y:auto">
      ${sensorFormHTML(dev, s)}
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mes')">Cancel</button>
      <button class="btn-p" onclick="submitEditSensor('${did}','${sid}')">Save Changes</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>{
    document.getElementById('as-n')?.focus();
    if(document.getElementById('as-t')?.value==='snmp') _snmpLoadVendors();
  },50);
}

async function submitEditSensor(did, sid){
  const payload = collectSensorForm(did);
  if(!payload) return;
  const btn=document.querySelector('#mes .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  const r = await api('PATCH', `/api/device/${did}/sensor/${sid}`, payload);
  if(btn){btn.disabled=false;btn.textContent='Save Changes';}
  if(r.status !== 'updated'){toast('Failed to update sensor','err');return;}
  closeM('mes');
  closeM('dm');
  // Re-fetch and update tile
  const devR = await fetch(`/api/device/${did}`);
  if(!devR.ok){ toast('Sensor updated','ok'); return; }
  const dev  = await devR.json();
  S.devices[did] = dev;
  const ns = dev.sensors.find(s=>s.sensor_id===sid);
  if(ns){
    S.sensors[`${did}/${sid}`] = ns;
    renderTile(did, ns);
    setupCharts(dev);
    // Update sidebar name
    const nm=document.querySelector(`#sbsr-${did}_${sid} .s-snm`);
    if(nm)nm.textContent=ns.name;
  }
  toast('Sensor updated','ok');
}

function selType(t){
  document.getElementById('as-t').value=t;
  document.querySelectorAll('.stpo').forEach(o=>o.classList.toggle('sel',o.dataset.t===t));
  ['ping','tcp','http','snmp','dns','tls','http_keyword','banner'].forEach(x=>document.getElementById(`fg-${x}`)?.classList.toggle('vis',x===t));
  if(t==='snmp') _snmpLoadVendors();
  if(window._snrAddMode) _applyTypeDefaults(t);
}

function _applyTypeDefaults(t){
  const d = window._snrTypeDefaults?.[t] || {};
  const _sv = (id,v) => { if(v==null) return; const e=document.getElementById(id); if(e) e.value=v; };
  const _sc = (id,v) => { if(v==null) return; const e=document.getElementById(id); if(e) e.checked=!!v; };
  _sv('as-iv',  d.interval);
  _sv('as-tmo', d.timeout);
  _sv('as-fa',  d.fail_after);
  _sv('as-ra',  d.recover_after);
  _sv('as-wms', d.warn_ms  ?? _SDR_WARN_DEF[t]);
  _sv('as-cms', d.crit_ms  ?? _SDR_CRIT_DEF[t]);
  if(t==='tcp')          _sv('as-tp',    d.port);
  if(t==='snmp')       { _sv('as-sp',    d.port); _sv('as-sc', d.community); if(d.version) document.getElementById('as-sv').value=d.version; }
  if(t==='dns')        { _sv('as-dp',    d.port); if(d.record_type) document.getElementById('as-drt').value=d.record_type; _sv('as-ds', d.dns_server); }
  if(t==='tls')          _sv('as-tlsp',  d.port);
  if(t==='banner')       _sv('as-bnp',   d.port);
  if(t==='http')       { _sc('as-vssl',  d.verify_ssl); _sv('as-xstatus', d.http_expected_status); }
  if(t==='http_keyword'){ _sc('as-kwssl',d.verify_ssl); _sc('as-kwcase',  d.keyword_case); }
}

// ── SNMP OID catalog picker ───────────────────────────────────────
let _snmpCatalog=null;

async function _snmpLoadVendors(){
  const vsel=document.getElementById('as-oid-vendor');
  if(!vsel||vsel.options.length>1) return;   // already populated this instance
  if(_snmpCatalog){
    // catalog already fetched — populate immediately from cache
    _snmpCatalog.forEach(v=>{
      const o=document.createElement('option');
      o.value=v.vendor; o.textContent=v.vendor;
      vsel.appendChild(o);
    });
    return;
  }
  try{
    const r=await fetch('/api/snmp/catalog');
    const d=await r.json();
    _snmpCatalog=d.catalog||[];
    _snmpCatalog.forEach(v=>{
      const o=document.createElement('option');
      o.value=v.vendor; o.textContent=v.vendor;
      vsel.appendChild(o);
    });
  }catch(e){}
}

function snmpVendorChange(){
  const vendor=document.getElementById('as-oid-vendor')?.value;
  const psel=document.getElementById('as-oid-pick');
  const unitEl=document.getElementById('as-oid-unit');
  if(!psel) return;
  psel.innerHTML='<option value="">— Select OID —</option>';
  psel.disabled=!vendor;
  if(unitEl) unitEl.textContent='';
  if(!vendor||!_snmpCatalog) return;
  const entry=_snmpCatalog.find(v=>v.vendor===vendor);
  if(!entry) return;
  entry.oids.forEach(o=>{
    const opt=document.createElement('option');
    opt.value=o.oid;
    opt.textContent=o.label+(o.unit?' ('+o.unit+')':'');
    opt.dataset.unit=o.unit||'';
    psel.appendChild(opt);
  });
}

function snmpOidPick(){
  const psel=document.getElementById('as-oid-pick');
  const oidEl=document.getElementById('as-oid');
  const unitEl=document.getElementById('as-oid-unit');
  if(!psel||!oidEl) return;
  const sel=psel.options[psel.selectedIndex];
  if(sel&&sel.value){
    oidEl.value=sel.value;
    if(unitEl) unitEl.textContent=sel.dataset.unit?'Unit: '+sel.dataset.unit:'';
  }
}

// ── SNMP Interface Discovery ──────────────────────────────────────
async function discoverInterfaces(){
  const host      = document.getElementById('as-sh')?.value.trim();
  const community = document.getElementById('as-sc')?.value.trim()||'public';
  const port      = parseInt(document.getElementById('as-sp')?.value)||161;
  const version   = document.getElementById('as-sv')?.value||'2c';
  const btn       = document.getElementById('as-disc-btn');
  const statusEl  = document.getElementById('as-iface-status');
  const listEl    = document.getElementById('as-iface-list');
  if(!host){ toast('Enter a Host / IP first','err'); return; }
  if(btn){ btn.disabled=true; btn.textContent='Discovering…'; }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='Querying device…'; }
  if(listEl){ listEl.style.display='none'; listEl.innerHTML=''; }
  const r=await api('POST','/api/snmp/interfaces',{host,community,port,version});
  if(btn){ btn.disabled=false; btn.textContent='⊕ Discover Interfaces'; }
  if(r.error){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent=r.error; }
    return;
  }
  const ifaces=r.interfaces||[];
  if(!ifaces.length){
    if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='No interfaces returned.'; }
    return;
  }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent=`${ifaces.length} interface${ifaces.length!==1?'s':''} discovered`; }

  // ── Build the interface table ──────────────────────────────────
  const METRICS=[
    {v:'status',     l:'Oper Status',        oid:'1.3.6.1.2.1.2.2.1.8.',    u:'1=up 2=down'},
    {v:'in_oct',     l:'In Traffic',         oid:'1.3.6.1.2.1.2.2.1.10.',   u:'bytes (32-bit)'},
    {v:'out_oct',    l:'Out Traffic',        oid:'1.3.6.1.2.1.2.2.1.16.',   u:'bytes (32-bit)'},
    {v:'in_hc',      l:'In Traffic (64-bit)',oid:'1.3.6.1.2.1.31.1.1.1.6.', u:'bytes (64-bit)'},
    {v:'out_hc',     l:'Out Traffic (64-bit)',oid:'1.3.6.1.2.1.31.1.1.1.10.',u:'bytes (64-bit)'},
    {v:'in_err',     l:'In Errors',          oid:'1.3.6.1.2.1.2.2.1.14.',   u:'errors'},
    {v:'out_err',    l:'Out Errors',         oid:'1.3.6.1.2.1.2.2.1.20.',   u:'errors'},
    {v:'in_disc',    l:'In Discards',        oid:'1.3.6.1.2.1.2.2.1.13.',   u:'packets'},
    {v:'out_disc',   l:'Out Discards',       oid:'1.3.6.1.2.1.2.2.1.19.',   u:'packets'},
    {v:'speed',      l:'Link Speed',         oid:'1.3.6.1.2.1.2.2.1.5.',    u:'bits/sec'},
    {v:'admin_st',   l:'Admin Status',       oid:'1.3.6.1.2.1.2.2.1.7.',    u:'1=up 2=down'},
  ];

  // Store metrics for pickIfaceOid access
  window._ifaceMetrics = METRICS;

  let html='<div style="border:1px solid var(--border);border-radius:6px;overflow:hidden">';
  html+='<div style="overflow-x:auto;max-height:220px;overflow-y:auto">';
  html+='<table style="width:100%;border-collapse:collapse;font-size:11px">';
  html+='<thead><tr style="background:var(--bg2);color:var(--text2);position:sticky;top:0">';
  html+='<th style="padding:5px 8px;text-align:center"><input type="checkbox" id="as-iface-all" title="Select all" onchange="toggleAllIfaces(this)"/></th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Idx</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Name</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Description / Alias</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Status</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Speed</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Monitor metric</th>';
  html+='</tr></thead><tbody>';

  ifaces.forEach((iface,i)=>{
    const stClr=iface.status==='up'?'var(--up)':'var(--down)';
    const displayName=esc(iface.name||iface.descr);
    const displayDescr=esc(iface.alias||iface.descr);
    const rowBg=i%2?'background:var(--bg2)':'';
    const opts=METRICS.map(m=>`<option value="${m.v}">${m.l}</option>`).join('');
    html+=`<tr style="border-top:1px solid var(--border);${rowBg}">`;
    html+=`<td style="padding:4px 8px;text-align:center"><input type="checkbox" class="as-iface-cb" data-idx="${iface.index}" data-name="${esc(iface.name||iface.descr)}" onchange="updateIfaceSelCount()"/></td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3)">${iface.index}</td>`;
    html+=`<td style="padding:4px 8px;font-weight:500;white-space:nowrap">${displayName}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text2)">${displayDescr}</td>`;
    html+=`<td style="padding:4px 8px;color:${stClr};white-space:nowrap">${iface.status}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);white-space:nowrap">${esc(iface.speed)}</td>`;
    html+=`<td style="padding:4px 8px">
      <select class="as-iface-metric" data-idx="${iface.index}"
              style="font-size:11px;padding:2px 4px;max-width:140px">
        <option value="">— metric —</option>${opts}
      </select>
    </td>`;
    html+='</tr>';
  });

  html+='</tbody></table></div>';
  html+='<div style="padding:8px 10px;background:var(--bg2);border-top:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<button class="btn-p" style="font-size:11px;padding:5px 14px" onclick="addSelectedIfaceSensors()">Add Selected as Sensors</button>';
  html+='<span id="as-iface-sel-count" style="font-size:11px;color:var(--text3)">0 selected</span>';
  html+='</div></div>';
  listEl.innerHTML=html;
  listEl.style.display='';
}

function toggleAllIfaces(cb){
  document.querySelectorAll('.as-iface-cb').forEach(c=>c.checked=cb.checked);
  updateIfaceSelCount();
}

function updateIfaceSelCount(){
  const cbs=[...document.querySelectorAll('.as-iface-cb')];
  const n=cbs.filter(c=>c.checked).length;
  const el=document.getElementById('as-iface-sel-count');
  if(el) el.textContent=n?`${n} of ${cbs.length} selected`:'0 selected';
  const all=document.getElementById('as-iface-all');
  if(all){all.indeterminate=(n>0&&n<cbs.length);all.checked=(cbs.length>0&&n===cbs.length);}
}

async function addSelectedIfaceSensors(){
  const did=window._ifaceDid;
  if(!did){toast('Device context lost — reopen the sensor form','err');return;}
  const checked=[...document.querySelectorAll('.as-iface-cb:checked')];
  if(!checked.length){toast('Select at least one interface','err');return;}
  const host=document.getElementById('as-sh')?.value.trim()||S.devices[did]?.host||'';
  const community=document.getElementById('as-sc')?.value.trim()||'public';
  const port=parseInt(document.getElementById('as-sp')?.value)||161;
  const version=document.getElementById('as-sv')?.value||'2c';
  const iv=parseInt(document.getElementById('as-iv')?.value)||5;
  const tmo=parseInt(document.getElementById('as-tmo')?.value)||4;
  const fail_after=Math.max(1,parseInt(document.getElementById('as-fa')?.value)||1);
  const recover_after=Math.max(1,parseInt(document.getElementById('as-ra')?.value)||1);
  const warn_ms=parseInt(document.getElementById('as-wms')?.value)||null;
  const crit_ms=parseInt(document.getElementById('as-cms')?.value)||null;
  const start=document.getElementById('as-si')?.value==='1';
  const rows=[];let noMetric=0;
  checked.forEach(cb=>{
    const idx=cb.dataset.idx;
    const name=cb.dataset.name||('IF'+idx);
    const sel=document.querySelector(`.as-iface-metric[data-idx="${idx}"]`);
    if(!sel||!sel.value){noMetric++;return;}
    const metric=(window._ifaceMetrics||[]).find(m=>m.v===sel.value);
    if(metric) rows.push({idx:parseInt(idx),name,metric});
  });
  if(noMetric) toast(`${noMetric} row${noMetric>1?'s':''} skipped — no metric chosen`,'info');
  if(!rows.length){toast('Choose a metric for each checked interface','err');return;}
  const btn=document.querySelector('[onclick="addSelectedIfaceSensors()"]');
  if(btn){btn.disabled=true;btn.textContent=`Adding ${rows.length}…`;}
  let added=0,failed=0;
  const addedSids=[];
  for(const row of rows){
    const r=await api('POST',`/api/device/${did}/sensor`,{
      name:row.name+' '+row.metric.l, type:'snmp', host, port,
      snmp_community:community, snmp_oid:row.metric.oid+row.idx, snmp_version:version,
      interval:iv, timeout:tmo, verify_ssl:true, url:null,
      dns_query:'',dns_record_type:'A',dns_server:'',http_expected_status:0,
      fail_after,recover_after,warn_ms,crit_ms,
      loss_warn_pct:0,loss_crit_pct:0,keyword:'',keyword_case:false,banner_regex:''
    });
    if(r?.sid){
      if(start) await api('POST',`/api/device/${did}/sensor/${r.sid}/start`);
      addedSids.push(r.sid);added++;
    }else{failed++;}
  }
  if(btn){btn.disabled=false;btn.textContent='Add Selected as Sensors';}
  if(added){
    try{
      const devR=await fetch(`/api/device/${did}`);
      if(devR.ok){
        const dev=await devR.json();
        S.devices[did]=dev;
        const newSensors=(dev.sensors||[]).filter(s=>addedSids.includes(s.sensor_id));
        newSensors.forEach(ns=>{
          S.sensors[`${did}/${ns.sensor_id}`]=ns;
          S.logs[`${did}/${ns.sensor_id}`]=[];
          if(document.getElementById('dwo')&&document.getElementById(`sg-${did}`)){
            renderTile(did,ns);setupCharts(dev);
          }
          const sbsl=document.getElementById(`sbsl-${did}`);
          if(sbsl){
            const r2=document.createElement('div');
            r2.className='snr-row';r2.id=`sbsr-${did}_${ns.sensor_id}`;
            r2.onclick=()=>openDetail(did,ns.sensor_id);
            r2.innerHTML=`<div class="s-ico ${ns.stype}">${sIco(ns.stype)}</div><div class="s-snm">${esc(ns.name)}</div><div class="s-sdot unknown" id="sbsd-${did}_${ns.sensor_id}"></div>`;
            sbsl.appendChild(r2);
          }
        });
        const cnt=document.querySelector(`#sbn-${did} .dev-cnt`);
        if(cnt) cnt.textContent=dev.sensors.length;
        const previewEl=document.getElementById(`dcsnr-${did}`);
        if(previewEl) previewEl.innerHTML=sSnrPreview(did);
      }
    }catch(e){/* non-critical — sensor was added */}
    toast(`Added ${added} sensor${added>1?'s':''}${failed?`, ${failed} failed`:''}`, 'ok');
    closeM('mas');
  }else{
    toast('Failed to add sensors','err');
  }
}

function collectSensorForm(did){
  const type=document.getElementById('as-t')?.value||'ping';
  const name=(document.getElementById('as-n')?.value||'').trim();
  const iv  =parseInt(document.getElementById('as-iv')?.value)||5;
  const tmo =parseInt(document.getElementById('as-tmo')?.value)||4;
  let host=null,port=null,url=null,verify_ssl=true,
      snmp_community='public',snmp_oid='1.3.6.1.2.1.1.1.0',snmp_version='2c',
      dns_query='',dns_record_type='A',dns_server='',http_expected_status=0,
      keyword='',keyword_case=false,banner_regex='';
  if(type==='ping'){
    host=document.getElementById('as-ph')?.value.trim()||S.devices[did]?.host;
  } else if(type==='tcp'){
    host=document.getElementById('as-th')?.value.trim()||S.devices[did]?.host;
    port=parseInt(document.getElementById('as-tp')?.value);
    if(!port){toast('Port number required','err');return null;}
  } else if(type==='http'){
    url=document.getElementById('as-hu')?.value.trim();
    if(!url){toast('URL required','err');return null;}
    host=S.devices[did]?.host;
    verify_ssl=document.getElementById('as-vssl')?.checked!==false;
    http_expected_status=parseInt(document.getElementById('as-xstatus')?.value)||0;
  } else if(type==='snmp'){
    host=document.getElementById('as-sh')?.value.trim()||S.devices[did]?.host;
    port=parseInt(document.getElementById('as-sp')?.value)||161;
    snmp_community=document.getElementById('as-sc')?.value.trim()||'public';
    snmp_oid=document.getElementById('as-oid')?.value.trim()||'1.3.6.1.2.1.1.1.0';
    snmp_version=document.getElementById('as-sv')?.value||'2c';
  } else if(type==='dns'){
    dns_query=document.getElementById('as-dq')?.value.trim()||S.devices[did]?.host||'';
    if(!dns_query){toast('Query hostname required','err');return null;}
    dns_record_type=document.getElementById('as-drt')?.value||'A';
    dns_server=document.getElementById('as-ds')?.value.trim()||'';
    port=parseInt(document.getElementById('as-dp')?.value)||53;
    host=dns_server||S.devices[did]?.host||'';
  } else if(type==='tls'){
    host=(document.getElementById('as-tlsh')?.value.trim()||S.devices[did]?.host||'')
         .replace(/^https?:\/\//i,'').split('/')[0];
    port=parseInt(document.getElementById('as-tlsp')?.value)||443;
    if(!host){toast('Host required','err');return null;}
  } else if(type==='http_keyword'){
    url=document.getElementById('as-kwu')?.value.trim();
    if(!url){toast('URL required','err');return null;}
    keyword=document.getElementById('as-kww')?.value.trim()||'';
    if(!keyword){toast('Keyword required','err');return null;}
    verify_ssl=document.getElementById('as-kwssl')?.checked!==false;
    keyword_case=document.getElementById('as-kwcase')?.checked||false;
    host=S.devices[did]?.host;
  } else if(type==='banner'){
    host=document.getElementById('as-bnh')?.value.trim()||S.devices[did]?.host;
    port=parseInt(document.getElementById('as-bnp')?.value)||21;
    banner_regex=document.getElementById('as-bnr')?.value.trim()||'';
    if(!host){toast('Host required','err');return null;}
  }
  const fail_after   =Math.max(1,parseInt(document.getElementById('as-fa')?.value)||1);
  const recover_after=Math.max(1,parseInt(document.getElementById('as-ra')?.value)||1);
  const warn_ms      =parseInt(document.getElementById('as-wms')?.value)||null;
  const crit_ms      =parseInt(document.getElementById('as-cms')?.value)||null;
  const loss_warn_pct=parseInt(document.getElementById('as-lwp')?.value)||0;
  const loss_crit_pct=parseInt(document.getElementById('as-lcp')?.value)||0;
  const alerts_muted =document.getElementById('as-am')?.checked||false;
  if(!name){toast('Sensor name required','err');return null;}
  return {type,name,host,port,url,interval:iv,timeout:tmo,
          verify_ssl,snmp_community,snmp_oid,snmp_version,
          dns_query,dns_record_type,dns_server,http_expected_status,
          fail_after,recover_after,warn_ms,crit_ms,loss_warn_pct,loss_crit_pct,
          keyword,keyword_case,banner_regex,alerts_muted};
}

async function submitAddSensor(did){
  const payload=collectSensorForm(did);
  if(!payload)return;
  const start=document.getElementById('as-si')?.value==='1';
  const btn=document.querySelector('#mas .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Adding...';}
  try{
    await addSensorDirect(did, payload.name, payload.type,
      payload.host, payload.port, payload.url,
      payload.interval, payload.timeout, start,
      payload.verify_ssl, payload.snmp_community,
      payload.snmp_oid, payload.snmp_version,
      payload.dns_query, payload.dns_record_type, payload.dns_server,
      payload.http_expected_status,
      payload.fail_after, payload.recover_after,
      payload.warn_ms, payload.crit_ms, payload.loss_warn_pct, payload.loss_crit_pct,
      payload.keyword, payload.keyword_case, payload.banner_regex);
    closeM('mas');
  }catch(e){
    toast('Failed to add sensor: '+e.message,'err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Add Sensor';}
  }
}

async function addSensorDirect(did,name,type,host,port,url,interval,timeout,startNow=true,
  verify_ssl=true,snmp_community='public',snmp_oid='1.3.6.1.2.1.1.1.0',snmp_version='2c',
  dns_query='',dns_record_type='A',dns_server='',http_expected_status=0,
  fail_after=1,recover_after=1,warn_ms=null,crit_ms=null,loss_warn_pct=0,loss_crit_pct=0,
  keyword='',keyword_case=false,banner_regex=''){
  const r=await api('POST',`/api/device/${did}/sensor`,{name,type,host,port,url,interval,timeout,
    verify_ssl,snmp_community,snmp_oid,snmp_version,dns_query,dns_record_type,dns_server,http_expected_status,
    fail_after,recover_after,warn_ms,crit_ms,loss_warn_pct,loss_crit_pct,
    keyword,keyword_case,banner_regex});
  if(!r||!r.sid){toast(r?.error||'Failed to add sensor','err');return;}
  if(startNow)await api('POST',`/api/device/${did}/sensor/${r.sid}/start`);
  // Refresh state and UI
  try{
    const devR=await fetch(`/api/device/${did}`);
    if(!devR.ok)throw new Error(`HTTP ${devR.status}`);
    const dev=await devR.json();
    S.devices[did]=dev;
    const ns=(dev.sensors||[]).find(s=>s.sensor_id===r.sid);
    if(ns){
      S.sensors[`${did}/${r.sid}`]=ns;
      S.logs[`${did}/${r.sid}`]=[];
      if(document.getElementById('dwo') && document.getElementById(`sg-${did}`)){
        renderTile(did,ns);
        setupCharts(dev);
      }
    }
    const sbsl=document.getElementById(`sbsl-${did}`);
    if(sbsl&&ns){
      const row=document.createElement('div');
      row.className='snr-row';row.id=`sbsr-${did}_${r.sid}`;
      row.onclick=()=>openDetail(did,r.sid);
      row.innerHTML=`<div class="s-ico ${ns.stype}">${sIco(ns.stype)}</div><div class="s-snm">${esc(ns.name)}</div><div class="s-sdot unknown" id="sbsd-${did}_${r.sid}"></div>`;
      sbsl.appendChild(row);
      const cnt=document.querySelector(`#sbn-${did} .dev-cnt`);
      if(cnt)cnt.textContent=dev.sensors.length;
    }
    const previewEl=document.getElementById(`dcsnr-${did}`);
    if(previewEl) previewEl.innerHTML=sSnrPreview(did);
  }catch(e){
    // Sensor was added — UI refresh failed but that's OK
  }
  toast(`Sensor "${name}" added`,'ok');
}

// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? SETTINGS �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?

async function openSettings(){
  closeM('mset');
  const [sr, ur] = await Promise.all([
    api('GET','/api/settings'),
    api('GET','/api/users'),
  ]);
  const o=document.createElement('div'); o.className='mo'; o.id='mset';
  o.onclick=e=>{if(e.target===o)closeM('mset');};
  o.innerHTML=`
  <div class="mbox" style="width:640px;max-width:96vw">
    <div class="mhd">
      <div class="mttl">⚙ Settings</div>
      <button class="mclose" onclick="closeM('mset')">✕</button>
    </div>
    <div class="dw-tabs" style="padding:0 4px">
      <button class="dw-tab active" id="stab-btn-general" onclick="switchSettingsTab('general')">General</button>
      <button class="dw-tab" id="stab-btn-users" onclick="switchSettingsTab('users')">Users</button>
      <button class="dw-tab" id="stab-btn-alerts" onclick="switchSettingsTab('alerts')">Alerts</button>
      <button class="dw-tab" id="stab-btn-database" onclick="switchSettingsTab('database')">Database</button>
      <button class="dw-tab" id="stab-btn-audit" onclick="switchSettingsTab('audit')">Audit</button>
      <button class="dw-tab" id="stab-btn-sensors" onclick="switchSettingsTab('sensors')">Sensors</button>
      <button class="dw-tab" id="stab-btn-networking" onclick="switchSettingsTab('networking')">Networking</button>
    </div>
    <div class="mbdy" id="stab-general" style="max-height:65vh;overflow-y:auto">
      <div class="fr">
        <label class="fl">Session Timeout (seconds)</label>
        <input type="number" id="st-ttl" value="${sr.session_ttl||86400}" min="60" style="max-width:180px"/>
        <div style="font-size:11px;color:var(--text3);margin-top:5px">Current: ${Math.round((sr.session_ttl||86400)/3600*10)/10}h — takes effect on next login</div>
      </div>
      <div class="fr" style="margin-top:12px">
        <label class="fl">Sample Retention (days)</label>
        <input type="number" id="st-ret" value="${sr.retention_days||365}" min="1" max="365" style="max-width:120px"/>
        <div style="font-size:11px;color:var(--text3);margin-top:5px">How long to keep latency history samples (default: 365 days)</div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">New Sensor Defaults</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">Interval (s)</label>
            <input type="number" id="st-snr-iv" value="${sr.snr_interval||5}" min="1" max="300" style="max-width:100px"/></div>
          <div class="fr"><label class="fl">Timeout (s)</label>
            <input type="number" id="st-snr-tmo" value="${sr.snr_timeout||4}" min="1" max="60" style="max-width:100px"/></div>
        </div>
        <div class="fgrid" style="margin-top:6px">
          <div class="fr"><label class="fl">Fail After (probes)</label>
            <input type="number" id="st-snr-fa" value="${sr.snr_fail_after||1}" min="1" max="60" style="max-width:100px"/></div>
          <div class="fr"><label class="fl">Recover After (probes)</label>
            <input type="number" id="st-snr-ra" value="${sr.snr_recover_after||1}" min="1" max="60" style="max-width:100px"/></div>
        </div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Event &amp; History Limits</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">Events shown</label>
            <input type="number" id="st-flap-disp" value="${sr.max_flaps_display||20}" min="5" max="200" style="max-width:100px"/>
            <div class="fh">Max events shown in Events tab</div></div>
          <div class="fr"><label class="fl">Events in DB</label>
            <input type="number" id="st-flap-db" value="${sr.max_flap_entries||500}" min="50" max="10000" style="max-width:100px"/>
            <div class="fh">Max flap entries kept in database</div></div>
        </div>
        <div class="fr" style="margin-top:6px"><label class="fl">SNMP Traps in DB</label>
          <input type="number" id="st-trap-db" value="${sr.max_trap_entries||500}" min="50" max="10000" style="max-width:100px"/>
          <div class="fh">Max SNMP trap entries kept in database</div></div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Security</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">Max login attempts</label>
            <input type="number" id="st-fail-max" value="${sr.login_fail_max||5}" min="1" max="100" style="max-width:100px"/>
            <div class="fh">Attempts before lockout</div></div>
          <div class="fr"><label class="fl">Lockout window (s)</label>
            <input type="number" id="st-fail-win" value="${sr.login_fail_window||60}" min="10" max="3600" style="max-width:100px"/>
            <div class="fh">Window to count failed attempts</div></div>
        </div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Appearance</div>
        <div class="fr"><label class="fl">Organisation Name</label>
          <input type="text" id="st-orgname" value="${esc(sr.org_name||'')}" placeholder="Network Monitor" style="max-width:260px"/>
          <div class="fh">Shown in the top bar and browser tab title</div></div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Latency Colour Thresholds</div>
        <div class="fgrid">
          <div class="fr"><label class="fl" style="color:var(--up)">Good (green) &lt; (ms)</label>
            <input type="number" id="st-lgood" value="${sr.latency_good_ms||100}" min="1" max="10000" style="max-width:100px"/></div>
          <div class="fr"><label class="fl" style="color:var(--warn)">Warn (yellow) &lt; (ms)</label>
            <input type="number" id="st-lwarn" value="${sr.latency_warn_ms||300}" min="1" max="10000" style="max-width:100px"/></div>
        </div>
        <div class="fh">Sensor tiles and sparklines use these breakpoints to colour-code latency</div>
      </div>
      <div class="fr" style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:12px">Change Password</div>
        <div class="fr"><label class="fl">Current Password</label>
          <input type="password" id="st-cpw" placeholder="current password"/></div>
        <div class="fgrid">
          <div class="fr"><label class="fl">New Password</label>
            <input type="password" id="st-npw" placeholder="min 8 characters"/></div>
          <div class="fr"><label class="fl">Confirm</label>
            <input type="password" id="st-npw2" placeholder="confirm"/></div>
        </div>
        <button class="btn-p" id="btnChgPw" style="margin-top:10px;font-size:12px;padding:7px 14px"
                onclick="changeOwnPassword()">Update Password</button>
      </div>
      <div class="fr" style="margin-top:16px">
        <div class="fl" style="margin-bottom:10px">Server Info</div>
        <div class="st-info-grid">
          <span class="st-info-key">Port</span><span class="st-info-val">${sr.port}</span>
          <span class="st-info-key">Address</span><span class="st-info-val">${sr.bind}</span>
          <span class="st-info-key">Database</span><span class="st-info-val">${esc(sr.db_path||'')}</span>
        </div>
      </div>
    </div>
    <div class="mbdy" id="stab-users" style="display:none;padding-top:8px">
      <div id="userTableWrap">${renderUserTable(ur.users||[])}</div>
      <div style="margin-top:14px">
        <button class="btn-p" style="font-size:12px;padding:7px 14px" onclick="openAddUser()">＋ Add User</button>
      </div>
    </div>
    <div class="mbdy" id="stab-alerts" style="display:none">
      <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:12px">SMTP Email Alerts</div>
      <div class="fgrid">
        <div class="fr"><label class="fl">SMTP Host</label>
          <input type="text" id="st-smtp-host" value="${sr.smtp_host||''}" placeholder="smtp.gmail.com"/></div>
        <div class="fr"><label class="fl">Port</label>
          <input type="number" id="st-smtp-port" value="${sr.smtp_port||587}" style="max-width:100px"/></div>
      </div>
      <div class="fr" style="margin-top:8px"><label class="fl">Security</label>
        <select id="st-smtp-tls" style="max-width:180px">
          <option value="starttls" ${sr.smtp_tls==='starttls'?'selected':''}>STARTTLS (port 587)</option>
          <option value="ssl"      ${sr.smtp_tls==='ssl'     ?'selected':''}>SSL/TLS  (port 465)</option>
          <option value="none"     ${sr.smtp_tls==='none'    ?'selected':''}>None     (port 25)</option>
        </select>
      </div>
      <div class="fgrid" style="margin-top:8px">
        <div class="fr"><label class="fl">Username</label>
          <input type="text"     id="st-smtp-user" value="${sr.smtp_user||''}" placeholder="user@gmail.com"/></div>
        <div class="fr"><label class="fl">Password</label>
          <input type="password" id="st-smtp-pass" placeholder="${sr.smtp_pass_set?'\u25cf\u25cf\u25cf\u25cf\u25cf (set \u2014 leave blank to keep)':'enter password'}"/></div>
      </div>
      <div class="fgrid" style="margin-top:8px">
        <div class="fr"><label class="fl">From</label>
          <input type="email" id="st-smtp-from" value="${sr.smtp_from||''}" placeholder="pingwatch@yourdomain.com"/></div>
        <div class="fr"><label class="fl">To</label>
          <input type="email" id="st-smtp-to"   value="${sr.smtp_to||''}"   placeholder="alerts@yourdomain.com"/></div>
      </div>
      <div class="fr" style="margin-top:8px"><label class="fl">Down Alert Delay (seconds)</label>
        <input type="number" id="st-smtp-delay" value="${sr.smtp_down_delay??10}" min="0" max="3600" style="max-width:100px"/>
        <div class="fh">Wait this many seconds before sending a DOWN email — if sensor recovers in time, no email is sent. Set to 0 to alert immediately.</div>
      </div>
      <div style="margin-top:14px;display:flex;gap:8px;align-items:center">
        <button class="btn-p" style="font-size:12px;padding:7px 14px" onclick="testSmtp()">Send Test Email</button>
        <span id="smtp-test-result" style="font-size:12px;color:var(--text3)"></span>
      </div>
      <div style="margin-top:12px;font-size:11px;color:var(--text3)">
        Emails are sent on sensor DOWN and RECOVERED events (after fail_after / recover_after debounce).
      </div>
    </div>
    <div class="mft" id="stab-footer-general">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSettings()">Save Settings</button>
    </div>
    <div class="mft" id="stab-footer-users" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
    </div>
    <div class="mft" id="stab-footer-alerts" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSettings()">Save Settings</button>
    </div>
    <div class="mbdy" id="stab-database" style="display:none">
      <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:12px">Backup &amp; Restore</div>
      <div class="fr">
        <label class="fl">Export Database</label>
        <button class="btn-p" style="font-size:12px;padding:7px 16px" onclick="exportDb()">&#8681; Download Backup</button>
        <div class="fh">Downloads a complete snapshot of all data (devices, sensors, history, network topology, settings, users).</div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <label class="fl">Import Database</label>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn-s" style="font-size:12px;padding:7px 16px" onclick="importDb()">&#8679; Restore from Backup</button>
          <span id="db-import-status" style="font-size:12px;color:var(--text3)"></span>
        </div>
        <div class="fh" style="color:var(--down);margin-top:6px">Warning: this replaces ALL current data and restarts the server.</div>
      </div>
    </div>
    <div class="mft" id="stab-footer-database" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
    </div>
    <div class="mbdy" id="stab-audit" style="display:none;padding:0">
      <div id="auditLogBody" style="max-height:65vh;overflow-y:auto">
        <div style="color:var(--text3);font-size:12px;padding:16px">Loading…</div>
      </div>
    </div>
    <div class="mft" id="stab-footer-audit" style="display:none">
      <span style="font-size:11px;color:var(--text3)">Last 200 entries · admin only</span>
    </div>
    <div class="mbdy" id="stab-sensors" style="display:none;max-height:65vh;overflow-y:auto">
      <div id="sdrTabBody"><div style="color:var(--text3);font-size:12px;padding:8px">Loading…</div></div>
    </div>
    <div class="mft" id="stab-footer-sensors" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSensorTypeDefaults()">Save Sensor Defaults</button>
    </div>
    <div class="mbdy" id="stab-networking" style="display:none">
      <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:12px">Server Ports</div>
      <div class="fr">
        <label class="fl">HTTP Port</label>
        <input type="number" id="st-http-port" value="${sr.http_port||7070}" min="1" max="65535" style="max-width:120px"/>
        <div class="fh">Port the web interface listens on</div>
      </div>
      <div class="fr" style="margin-top:12px">
        <label class="fl">SNMP Trap Port</label>
        <input type="number" id="st-snmp-port" value="${sr.snmp_port||162}" min="1" max="65535" style="max-width:120px"/>
        <div class="fh">UDP port for SNMP trap reception. Falls back to 1162 then 2162 if binding fails.</div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:6px">HTTPS</div>
        <div style="font-size:12px;color:var(--text3)">HTTPS is not natively supported. Use a reverse proxy (Nginx, Caddy, HAProxy) to terminate TLS in front of PingWatch.</div>
      </div>
      <div style="margin-top:16px;padding:10px;background:var(--bg3);border-radius:6px;font-size:12px;color:var(--warn)">
        Port changes require a server restart to take effect.
      </div>
    </div>
    <div class="mft" id="stab-footer-networking" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveNetworkingSettings()">Save Networking</button>
    </div>
  </div>`;
  document.body.appendChild(o);
}

function switchSettingsTab(tab){
  ['general','users','alerts','database','audit','sensors','networking'].forEach(t=>{
    document.getElementById(`stab-${t}`).style.display = t===tab ? '' : 'none';
    document.getElementById(`stab-btn-${t}`).classList.toggle('active', t===tab);
    document.getElementById(`stab-footer-${t}`).style.display = t===tab ? '' : 'none';
  });
  if(tab==='audit')   loadAuditLog();
  if(tab==='sensors') loadSensorsDefaultsTab();
}

async function saveNetworkingSettings(){
  const httpPort=parseInt(document.getElementById('st-http-port')?.value);
  const snmpPort=parseInt(document.getElementById('st-snmp-port')?.value);
  if(!httpPort||httpPort<1||httpPort>65535){toast('HTTP port must be 1–65535','err');return;}
  if(!snmpPort||snmpPort<1||snmpPort>65535){toast('SNMP port must be 1–65535','err');return;}
  const btn=document.querySelector('#stab-footer-networking .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  const r=await api('PATCH','/api/settings',{http_port:httpPort,snmp_port:snmpPort});
  if(btn){btn.disabled=false;btn.textContent='Save Networking';}
  if(!r.ok){toast('Failed to save networking settings','err');return;}
  toast('Saved — restart the server for port changes to take effect','ok');
}

async function loadAuditLog(){
  const el=document.getElementById('auditLogBody');
  if(!el) return;
  const r=await fetch('/api/audit');
  if(!r.ok){ el.innerHTML='<div style="color:var(--down);padding:12px">Access denied</div>'; return; }
  const {entries}=await r.json();
  if(!entries.length){ el.innerHTML='<div style="color:var(--text3);font-size:12px;padding:12px">No audit entries yet.</div>'; return; }
  el.innerHTML=`<table class="audit-tbl"><thead><tr>
    <th>Time</th><th>User</th><th>IP</th><th>Action</th><th>Target</th><th>Detail</th>
  </tr></thead><tbody>${entries.map(e=>`<tr>
    <td class="audit-ts">${new Date(e.ts*1000).toLocaleString()}</td>
    <td class="audit-actor">${e.actor}</td>
    <td class="audit-ip">${e.ip}</td>
    <td class="audit-action ${_auditCls(e.action)}">${e.action}</td>
    <td class="audit-target">${e.target||'—'}</td>
    <td class="audit-detail">${e.detail||''}</td>
  </tr>`).join('')}</tbody></table>`;
}

function _auditCls(a){
  if(a.includes('delete')||a==='db_import') return 'aud-danger';
  if(a.includes('fail')||a.includes('pass')) return 'aud-warn';
  if(a==='login_ok'||a.includes('create')) return 'aud-ok';
  return '';
}

// ── Per-type sensor defaults tab ──────────────────────────────────────────

// Suggested warn/crit defaults per sensor type (used when no saved value exists)
const _SDR_WARN_DEF = {ping:200,  tcp:300,  http:500,  snmp:1000, dns:200,  tls:500,  http_keyword:500,  banner:300};
const _SDR_CRIT_DEF = {ping:500,  tcp:1000, http:1500, snmp:3000, dns:500,  tls:2000, http_keyword:1500, banner:1000};

const _SDR_META = {
  ping:         {ico:'📡', label:'Ping',         desc:'ICMP round-trip latency & loss'},
  tcp:          {ico:'🔌', label:'TCP Port',     desc:'TCP connection reachability'},
  http:         {ico:'🌐', label:'HTTP/S',       desc:'HTTP/HTTPS status & latency'},
  snmp:         {ico:'📊', label:'SNMP',         desc:'SNMP OID polling'},
  dns:          {ico:'🔍', label:'DNS',          desc:'DNS record resolution'},
  tls:          {ico:'🔒', label:'TLS',          desc:'TLS/SSL certificate expiry'},
  http_keyword: {ico:'🏷', label:'HTTP Keyword', desc:'HTTP response body search'},
  banner:       {ico:'📋', label:'Banner',       desc:'TCP banner / regex match'},
};

function _sdrExtraFields(type, d){
  const v  = (k,def) => d?.[k] != null ? d[k] : def;
  const chk= (k,def) => (d?.[k] != null ? d[k] : def) ? 'checked' : '';
  switch(type){
    case 'tcp':
      return `<div class="fr"><label class="fl">Default Port</label>
        <input type="number" id="sdr_tcp_port" value="${v('port','')}" min="1" max="65535" style="max-width:100px"/></div>`;
    case 'http':
      return `<div class="fr"><label class="fl">Verify SSL</label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px">
          <input type="checkbox" id="sdr_http_verify_ssl" ${chk('verify_ssl',true)}/> Verify SSL certificate</label></div>
        <div class="fr"><label class="fl">Expected Status (0 = any 2xx)</label>
        <input type="number" id="sdr_http_expected_status" value="${v('http_expected_status',0)}" min="0" max="599" style="max-width:100px"/></div>`;
    case 'snmp':
      return `<div class="fr"><label class="fl">Community</label>
        <input type="text" id="sdr_snmp_community" value="${esc(v('community','public'))}" style="max-width:160px"/></div>
        <div class="fgrid"><div class="fr"><label class="fl">Version</label>
          <select id="sdr_snmp_version">
            <option value="2c" ${v('version','2c')==='2c'?'selected':''}>v2c</option>
            <option value="1"  ${v('version','2c')==='1' ?'selected':''}>v1</option>
          </select></div>
        <div class="fr"><label class="fl">Port</label>
          <input type="number" id="sdr_snmp_port" value="${v('port',161)}" min="1" max="65535" style="max-width:100px"/></div></div>`;
    case 'dns':
      return `<div class="fgrid">
        <div class="fr"><label class="fl">Record Type</label>
          <select id="sdr_dns_record_type">${['A','AAAA','CNAME','MX','NS','TXT','PTR'].map(rt=>
            `<option value="${rt}" ${v('record_type','A')===rt?'selected':''}>${rt}</option>`).join('')}</select></div>
        <div class="fr"><label class="fl">Port</label>
          <input type="number" id="sdr_dns_port" value="${v('port',53)}" min="1" max="65535" style="max-width:100px"/></div></div>
        <div class="fr"><label class="fl">DNS Server (blank = system)</label>
          <input type="text" id="sdr_dns_server" value="${esc(v('dns_server',''))}" placeholder="8.8.8.8" style="max-width:180px"/></div>`;
    case 'tls':
      return `<div class="fr"><label class="fl">Port</label>
        <input type="number" id="sdr_tls_port" value="${v('port',443)}" min="1" max="65535" style="max-width:100px"/></div>`;
    case 'http_keyword':
      return `<div class="fr"><label class="fl">Verify SSL</label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px">
          <input type="checkbox" id="sdr_http_keyword_verify_ssl" ${chk('verify_ssl',true)}/> Verify SSL certificate</label></div>
        <div class="fr"><label class="fl">Case Sensitive</label>
        <label style="display:flex;align-items:center;gap:6px;margin-top:4px">
          <input type="checkbox" id="sdr_http_keyword_keyword_case" ${chk('keyword_case',false)}/> Case-sensitive keyword match</label></div>`;
    case 'banner':
      return `<div class="fr"><label class="fl">Default Port</label>
        <input type="number" id="sdr_banner_port" value="${v('port',21)}" min="1" max="65535" style="max-width:100px"/></div>`;
    default: return '';
  }
}

async function loadSensorsDefaultsTab(){
  const el = document.getElementById('sdrTabBody');
  if(!el) return;
  const {devices} = await api('GET','/api/devices');
  // Count sensors per type
  const typeCounts = {};
  for(const dev of devices)
    for(const s of (dev.sensors||[]))
      typeCounts[s.stype] = (typeCounts[s.stype]||0) + 1;
  const types = Object.keys(typeCounts).sort();
  if(!types.length){ el.innerHTML='<div style="color:var(--text3);font-size:12px;padding:8px">No sensors found.</div>'; return; }
  const td = window._snrTypeDefaults || {};
  el.innerHTML = types.map(t => {
    const m   = _SDR_META[t] || {ico:'?', label:t, desc:''};
    const d   = td[t] || {};
    const cnt = typeCounts[t];
    const iv  = d.interval      != null ? d.interval      : (window._snrDef?.interval||5);
    const to  = d.timeout       != null ? d.timeout       : (window._snrDef?.timeout||4);
    const fa  = d.fail_after    != null ? d.fail_after    : (window._snrDef?.fail_after||1);
    const ra  = d.recover_after != null ? d.recover_after : (window._snrDef?.recover_after||1);
    const wm  = d.warn_ms  != null ? d.warn_ms  : (_SDR_WARN_DEF[t] || '');
    const cm  = d.crit_ms  != null ? d.crit_ms  : (_SDR_CRIT_DEF[t] || '');
    const extra = _sdrExtraFields(t, d);
    return `<div class="sdr-card" data-type="${t}">
      <div class="sdr-card-hd">
        <span class="sdr-icon">${m.ico}</span>
        <div class="sdr-card-title">
          <span class="sdr-lbl">${m.label}</span>
          <span class="sdr-desc">${m.desc}</span>
        </div>
        <span class="sdr-cnt">${cnt} sensor${cnt>1?'s':''}</span>
      </div>
      <div class="sdr-fields">
        <div class="sdr-field">
          <label>Interval</label>
          <div class="sdr-input-row">
            <input type="number" id="sdr_${t}_interval" value="${iv}" min="1" max="300"/>
            <span class="sdr-unit">s</span>
          </div>
        </div>
        <div class="sdr-field">
          <label>Timeout</label>
          <div class="sdr-input-row">
            <input type="number" id="sdr_${t}_timeout" value="${to}" min="1" max="60"/>
            <span class="sdr-unit">s</span>
          </div>
        </div>
        <div class="sdr-field">
          <label>Fail After</label>
          <div class="sdr-input-row">
            <input type="number" id="sdr_${t}_fail_after" value="${fa}" min="1" max="60"/>
            <span class="sdr-unit">×</span>
          </div>
        </div>
        <div class="sdr-field">
          <label>Recover After</label>
          <div class="sdr-input-row">
            <input type="number" id="sdr_${t}_recover_after" value="${ra}" min="1" max="60"/>
            <span class="sdr-unit">×</span>
          </div>
        </div>
        <div class="sdr-field">
          <label>${t==='tls'?'Warn Days':t==='snmp'?'Warn Val':'Warn (ms)'}</label>
          <div class="sdr-input-row">
            <input type="number" id="sdr_${t}_warn_ms" value="${wm}" min="1" placeholder="—"/>
            <span class="sdr-unit">${t==='snmp'||t==='tls'?'val':'ms'}</span>
          </div>
        </div>
        <div class="sdr-field">
          <label>${t==='tls'?'Crit Days':t==='snmp'?'Crit Val':'Crit (ms)'}</label>
          <div class="sdr-input-row">
            <input type="number" id="sdr_${t}_crit_ms" value="${cm}" min="1" placeholder="—"/>
            <span class="sdr-unit">${t==='snmp'||t==='tls'?'val':'ms'}</span>
          </div>
        </div>
      </div>
      ${extra ? `<div class="sdr-extra">${extra}</div>` : ''}
    </div>`;
  }).join('');
}

async function saveSensorTypeDefaults(){
  const sections = document.querySelectorAll('.sdr-card');
  const result = {};
  sections.forEach(el => {
    const t = el.dataset.type;
    const _n = id => { const e=document.getElementById(id); return e ? +e.value : null; };
    const _v = id => { const e=document.getElementById(id); return e ? e.value  : null; };
    const _b = id => { const e=document.getElementById(id); return e ? e.checked: null; };
    const d = {};
    const iv=_n(`sdr_${t}_interval`);      if(iv  !=null&&iv  >0) d.interval     =iv;
    const to=_n(`sdr_${t}_timeout`);       if(to  !=null&&to  >0) d.timeout      =to;
    const fa=_n(`sdr_${t}_fail_after`);    if(fa  !=null&&fa  >0) d.fail_after   =fa;
    const ra=_n(`sdr_${t}_recover_after`); if(ra  !=null&&ra  >0) d.recover_after=ra;
    const wm=_n(`sdr_${t}_warn_ms`);       if(wm  !=null&&wm  >0) d.warn_ms      =wm;
    const cm=_n(`sdr_${t}_crit_ms`);       if(cm  !=null&&cm  >0) d.crit_ms      =cm;
    if(t==='tcp')         { const p=_n('sdr_tcp_port');                   if(p>0)    d.port=p; }
    if(t==='snmp')        { const p=_n('sdr_snmp_port');                  if(p>0)    d.port=p;
                            const c=_v('sdr_snmp_community');             if(c!=null) d.community=c;
                            const sv=_v('sdr_snmp_version');              if(sv)      d.version=sv; }
    if(t==='dns')         { const p=_n('sdr_dns_port');                   if(p>0)    d.port=p;
                            const rt=_v('sdr_dns_record_type');           if(rt)      d.record_type=rt;
                            const ds=_v('sdr_dns_server');                if(ds!=null) d.dns_server=ds; }
    if(t==='tls')         { const p=_n('sdr_tls_port');                   if(p>0)    d.port=p; }
    if(t==='banner')      { const p=_n('sdr_banner_port');                if(p>0)    d.port=p; }
    if(t==='http')        { const vs=_b('sdr_http_verify_ssl');           if(vs!=null) d.verify_ssl=vs;
                            const xs=_n('sdr_http_expected_status');      if(xs!=null) d.http_expected_status=xs; }
    if(t==='http_keyword'){ const vs=_b('sdr_http_keyword_verify_ssl');   if(vs!=null) d.verify_ssl=vs;
                            const kc=_b('sdr_http_keyword_keyword_case'); if(kc!=null) d.keyword_case=kc; }
    result[t] = d;
  });
  const r = await api('PATCH', '/api/settings', {snr_type_defaults: result});
  if(!r.ok){ toast('Save failed','err'); return; }
  window._snrTypeDefaults = result;
  toast('Sensor defaults saved','ok');
}

function renderUserTable(users){
  if(!users||!users.length) return '<div style="color:var(--text3);font-size:12px;padding:8px 0">No users found.</div>';
  const rows=users.map(u=>`
    <tr>
      <td><strong>${esc(u.username)}</strong></td>
      <td><span style="color:var(--text2)">${esc(u.role)}</span></td>
      <td><div class="usr-act">
        <button onclick="openResetPw('${esc(u.username)}')">🔑 Reset Password</button>
        <button class="del" onclick="deleteUser('${esc(u.username)}')">🗑 Delete</button>
      </div></td>
    </tr>`).join('');
  return `<table class="usr-table">
    <thead><tr><th>Username</th><th>Role</th><th>Actions</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function saveSettings(){
  const ttl=parseInt(document.getElementById('st-ttl')?.value);
  const ret=parseInt(document.getElementById('st-ret')?.value);
  if(!ttl||ttl<60){toast('Session timeout must be at least 60 seconds','err');return;}
  const btn=[...document.querySelectorAll('[onclick="saveSettings()"]')].find(el=>el.offsetParent!==null);
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  const body={session_ttl:ttl};
  if(ret&&ret>=1)body.retention_days=ret;
  // Group A — sensor defaults
  const snrIv =parseInt(document.getElementById('st-snr-iv')?.value);
  const snrTmo=parseInt(document.getElementById('st-snr-tmo')?.value);
  const snrFa =parseInt(document.getElementById('st-snr-fa')?.value);
  const snrRa =parseInt(document.getElementById('st-snr-ra')?.value);
  if(snrIv>=1)  body.snr_interval=snrIv;
  if(snrTmo>=1) body.snr_timeout=snrTmo;
  if(snrFa>=1)  body.snr_fail_after=snrFa;
  if(snrRa>=1)  body.snr_recover_after=snrRa;
  // Group B — event & history limits
  const flapDisp=parseInt(document.getElementById('st-flap-disp')?.value);
  const flapDb  =parseInt(document.getElementById('st-flap-db')?.value);
  const trapDb  =parseInt(document.getElementById('st-trap-db')?.value);
  if(flapDisp>=5)  body.max_flaps_display=flapDisp;
  if(flapDb>=50)   body.max_flap_entries=flapDb;
  if(trapDb>=50)   body.max_trap_entries=trapDb;
  // Group C — security
  const failMax=parseInt(document.getElementById('st-fail-max')?.value);
  const failWin=parseInt(document.getElementById('st-fail-win')?.value);
  if(failMax>=1)  body.login_fail_max=failMax;
  if(failWin>=10) body.login_fail_window=failWin;
  // Group D — branding
  body.org_name=(document.getElementById('st-orgname')?.value||'').trim();
  // Group E — latency colour thresholds
  const lGood=parseInt(document.getElementById('st-lgood')?.value);
  const lWarn=parseInt(document.getElementById('st-lwarn')?.value);
  if(lGood>=1) body.latency_good_ms=lGood;
  if(lWarn>=1) body.latency_warn_ms=lWarn;
  // SMTP
  const smtp={
    smtp_host:       document.getElementById('st-smtp-host')?.value.trim()||'',
    smtp_port:       parseInt(document.getElementById('st-smtp-port')?.value)||587,
    smtp_tls:        document.getElementById('st-smtp-tls')?.value||'starttls',
    smtp_user:       document.getElementById('st-smtp-user')?.value.trim()||'',
    smtp_from:       document.getElementById('st-smtp-from')?.value.trim()||'',
    smtp_to:         document.getElementById('st-smtp-to')?.value.trim()||'',
    smtp_down_delay: parseInt(document.getElementById('st-smtp-delay')?.value)??10,
  };
  const pw=document.getElementById('st-smtp-pass')?.value||'';
  if(pw) smtp.smtp_pass=pw;
  Object.assign(body,smtp);
  const r=await api('PATCH','/api/settings',body);
  if(btn){btn.disabled=false;btn.textContent='Save Settings';}
  if(!r.ok){toast('Failed to save settings','err');return;}
  // Apply globals immediately so changes take effect without page reload
  if(body.max_flaps_display) MAX_FLAPS=body.max_flaps_display;
  if(body.latency_good_ms)   window._lGood=body.latency_good_ms;
  if(body.latency_warn_ms)   window._lWarn=body.latency_warn_ms;
  if('org_name' in body){
    window._snrDef=window._snrDef||{};
    const el=document.getElementById('tbVer');
    if(el) el.textContent=body.org_name||'Network Monitor v3';
    document.title='PingWatch \u2014 '+(body.org_name||'Network Monitor');
  }
  window._snrDef=window._snrDef||{};
  if(body.snr_interval)     window._snrDef.interval=body.snr_interval;
  if(body.snr_timeout)      window._snrDef.timeout=body.snr_timeout;
  if(body.snr_fail_after)   window._snrDef.fail_after=body.snr_fail_after;
  if(body.snr_recover_after)window._snrDef.recover_after=body.snr_recover_after;
  toast('Settings saved','ok');
}

async function testSmtp(){
  const btn=document.querySelector('[onclick="testSmtp()"]');
  const res=document.getElementById('smtp-test-result');
  if(btn){btn.disabled=true;btn.textContent='Testing...';}
  if(res) res.textContent='';
  const cfg={
    smtp_host: document.getElementById('st-smtp-host')?.value.trim(),
    smtp_port: parseInt(document.getElementById('st-smtp-port')?.value)||587,
    smtp_tls:  document.getElementById('st-smtp-tls')?.value,
    smtp_user: document.getElementById('st-smtp-user')?.value.trim(),
    smtp_pass: document.getElementById('st-smtp-pass')?.value,
    smtp_from: document.getElementById('st-smtp-from')?.value.trim(),
    smtp_to:   document.getElementById('st-smtp-to')?.value.trim(),
  };
  const r=await api('POST','/api/settings/smtp_test',cfg);
  if(btn){btn.disabled=false;btn.textContent='Send Test Email';}
  if(res) res.style.color=r.ok?'var(--up)':'var(--down)';
  if(res) res.textContent=r.msg||'Unknown error';
}

async function exportDb(){
  const btn=document.querySelector('[onclick="exportDb()"]');
  if(btn){btn.disabled=true;btn.textContent='Preparing…';}
  try{
    const r=await fetch('/api/db/export');
    if(!r.ok){toast('Export failed: '+r.status,'err');return;}
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    const d=new Date();
    const stamp=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    a.download=`pingwatch-backup-${stamp}.db`;
    a.click();
    URL.revokeObjectURL(url);
    toast('Backup downloaded','ok');
  }catch(e){
    toast('Export error: '+e.message,'err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='⬇ Download Backup';}
  }
}

function _importConfirm(filename, onConfirm){
  const ov=document.createElement('div');
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML=`<div style="background:var(--card,#1e2533);border:1px solid var(--border,#2a3448);border-radius:10px;padding:28px 32px;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:15px;font-weight:600;margin-bottom:10px;color:var(--text,#e0e6f0);">Restore from Backup</div>
    <div style="font-size:13px;color:var(--text2,#8899aa);margin-bottom:20px;">Import <b style="color:var(--text,#e0e6f0)">${filename}</b>?<br><br>This will <span style="color:var(--down,#ff4444);font-weight:600;">REPLACE ALL current data</span> and restart the server.</div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button id="_imp_cancel" style="padding:8px 20px;border-radius:6px;border:1px solid var(--border,#2a3448);background:transparent;color:var(--text2,#8899aa);cursor:pointer;font-size:13px;">Cancel</button>
      <button id="_imp_ok" style="padding:8px 20px;border-radius:6px;border:none;background:var(--down,#ff4444);color:#fff;cursor:pointer;font-weight:600;font-size:13px;">Yes, Import</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  const close=()=>document.body.removeChild(ov);
  ov.querySelector('#_imp_cancel').onclick=close;
  ov.querySelector('#_imp_ok').onclick=()=>{ close(); onConfirm(); };
}

async function importDb(){
  const inp=document.createElement('input');
  inp.type='file'; inp.accept='.db';
  inp.style.cssText='position:fixed;top:-9999px;left:-9999px;opacity:0;';
  document.body.appendChild(inp);
  const _cleanup=()=>{ try{document.body.removeChild(inp);}catch(_){} };
  inp.addEventListener('cancel', _cleanup);
  inp.onchange=()=>{
    _cleanup();
    const file=inp.files[0]; if(!file) return;
    _importConfirm(file.name, async()=>{
      const statusEl=document.getElementById('db-import-status');
      if(statusEl){statusEl.style.color='var(--text3)';statusEl.textContent='Reading file…';}
      try{
        const buf=await file.arrayBuffer();
        const bytes=new Uint8Array(buf);
        let binary='';
        for(let i=0;i<bytes.length;i+=8192) binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
        const b64=btoa(binary);
        if(statusEl) statusEl.textContent='Uploading…';
        const r=await api('POST','/api/db/import',{data:b64});
        if(r&&r.ok){
          if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent=r.msg||'Imported — restarting…';}
          toast(r.msg||'Imported — server restarting…','ok');
          setTimeout(()=>location.reload(),4000);
        }else{
          if(statusEl){statusEl.style.color='var(--down)';statusEl.textContent=(r&&r.error)||'Import failed';}
          toast((r&&r.error)||'Import failed','err');
        }
      }catch(e){
        const isNetErr=!e.name||e.name==='TypeError'||e.name==='NetworkError';
        if(isNetErr){
          if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent='Imported — restarting…';}
          toast('Imported — server restarting…','ok');
          setTimeout(()=>location.reload(),4000);
        }else{
          if(statusEl){statusEl.style.color='var(--down)';statusEl.textContent='Import error: '+e.message;}
          toast('Import error: '+e.message,'err');
        }
      }
    });
  };
  inp.click();
}

async function changeOwnPassword(){
  const btn=document.getElementById('btnChgPw');
  const cur=document.getElementById('st-cpw')?.value||'';
  const np =document.getElementById('st-npw')?.value||'';
  const np2=document.getElementById('st-npw2')?.value||'';
  if(!cur||!np){toast('All password fields are required','err');return;}
  if(np!==np2){toast('Passwords do not match','err');return;}
  if(np.length<8){toast('Password must be at least 8 characters','err');return;}
  btn.disabled=true;btn.textContent='Updating...';
  const r=await api('PATCH','/api/me/password',{current_password:cur,password:np});
  btn.disabled=false;btn.textContent='Update Password';
  if(r.error){toast(r.error,'err');return;}
  document.getElementById('st-cpw').value='';
  document.getElementById('st-npw').value='';
  document.getElementById('st-npw2').value='';
  toast('Password updated','ok');
}

async function reloadUserTable(){
  const ur=await api('GET','/api/users');
  const wrap=document.getElementById('userTableWrap');
  if(wrap) wrap.innerHTML=renderUserTable(ur.users||[]);
}

function openAddUser(){
  closeM('mau');
  const o=document.createElement('div'); o.className='mo'; o.id='mau';
  o.onclick=e=>{if(e.target===o)closeM('mau');};
  o.innerHTML=`
  <div class="mbox">
    <div class="mhd">
      <div class="mttl">Add User</div>
      <button class="mclose" onclick="closeM('mau')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="au-u" autocomplete="off" placeholder="username"/></div>
      <div class="fr"><label class="fl">Password</label>
        <input type="password" id="au-p" placeholder="password"/></div>
      <div class="fr"><label class="fl">Confirm Password</label>
        <input type="password" id="au-p2" placeholder="confirm password"/></div>
      <div class="fr"><label class="fl">Role</label>
        <select id="au-r">
          <option value="viewer">Viewer — read-only dashboard access</option>
          <option value="operator">Operator — manage devices &amp; sensors</option>
          <option value="admin" selected>Admin — full access incl. users &amp; settings</option>
        </select>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mau')">Cancel</button>
      <button class="btn-p" onclick="submitAddUser()">Create User</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('au-u')?.focus(),50);
}

async function submitAddUser(){
  const username=(document.getElementById('au-u')?.value||'').trim();
  const pw=document.getElementById('au-p')?.value||'';
  const pw2=document.getElementById('au-p2')?.value||'';
  const role=document.getElementById('au-r')?.value||'admin';
  if(!username||!pw){toast('Username and password are required','err');return;}
  if(pw!==pw2){toast('Passwords do not match','err');return;}
  const btn=document.querySelector('#mau .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Creating...';}
  const r=await api('POST','/api/users',{username,password:pw,role});
  if(btn){btn.disabled=false;btn.textContent='Create User';}
  if(r.error){toast(r.error,'err');return;}
  closeM('mau');
  await reloadUserTable();
  toast(`User "${username}" created`,'ok');
}

let _resetPwTarget='';
function openResetPw(username){
  _resetPwTarget=username;
  closeM('mrpw');
  const o=document.createElement('div'); o.className='mo'; o.id='mrpw';
  o.onclick=e=>{if(e.target===o)closeM('mrpw');};
  o.innerHTML=`
  <div class="mbox">
    <div class="mhd">
      <div class="mttl">Reset Password — ${esc(username)}</div>
      <button class="mclose" onclick="closeM('mrpw')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">New Password</label>
        <input type="password" id="rp-p" placeholder="new password"/></div>
      <div class="fr"><label class="fl">Confirm Password</label>
        <input type="password" id="rp-p2" placeholder="confirm password"/></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mrpw')">Cancel</button>
      <button class="btn-p" onclick="submitResetPw()">Set Password</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('rp-p')?.focus(),50);
}

async function submitResetPw(){
  const pw=document.getElementById('rp-p')?.value||'';
  const pw2=document.getElementById('rp-p2')?.value||'';
  if(!pw){toast('Password is required','err');return;}
  if(pw!==pw2){toast('Passwords do not match','err');return;}
  const btn=document.querySelector('#mrpw .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Setting...';}
  const r=await api('PATCH',`/api/users/${encodeURIComponent(_resetPwTarget)}/password`,{password:pw});
  if(btn){btn.disabled=false;btn.textContent='Set Password';}
  if(r.error){toast(r.error,'err');return;}
  closeM('mrpw');
  toast(`Password updated for "${_resetPwTarget}"`,'ok');
}

async function deleteUser(username){
  if(!confirm(`Delete user "${username}"? This cannot be undone.`))return;
  const r=await api('DELETE',`/api/users/${encodeURIComponent(username)}`);
  if(r.error){toast(r.error,'err');return;}
  await reloadUserTable();
  toast(`User "${username}" deleted`,'ok');
}

// ── Helpers ──────────────────────────────────────────────────────
function closeM(id){document.getElementById(id)?.remove();}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

window.addEventListener('resize',()=>{
  Object.keys(S.charts).forEach(k=>{
    const info=S.charts[k];if(info)drawSpk(k,S.sensors[k]?.history||[]);
  });
});
