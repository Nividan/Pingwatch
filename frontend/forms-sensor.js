// ── Sensor forms: Add / Edit sensor, SNMP catalog, Interface discovery ────

const PORT_CHIPS = [['HTTP','80'],['HTTPS','443'],['SSH','22'],['FTP','21'],['DNS','53'],
  ['RDP','3389'],['SMTP','25'],['MySQL','3306'],['PgSQL','5432'],['Redis','6379'],
  ['Mongo','27017'],['LDAP','389']];

// Render the shared sensor fields HTML (used by both Add and Edit)
function sensorFormHTML(dev, s=null) {
  const defHost   = s?.host || '';
  const devHost   = dev?.host || '';
  const hostHint  = devHost ? `leave blank — uses device (${esc(devHost)})` : 'e.g. 192.168.1.1';
  // Link indicator shown when editing — tells user whether host is linked to the device
  const isLinked  = s ? (!s.host_override || !s.host || s.host === devHost) : null;
  const hostStatusHtml = (s && devHost)
    ? (isLinked
        ? `<div class="fh" style="color:#4ade80;font-size:10px;margin-top:2px">🔗 linked to device · clear field to keep linked</div>`
        : `<div class="fh" style="color:#fbbf24;font-size:10px;margin-top:2px">⚠ custom host · clear field to re-link to device</div>`)
    : '';
  // SNMP community — blank means "use device default" (same pattern as host)
  const devComm   = dev?.snmp_community_default || '';
  const commHint  = devComm ? `leave blank — uses device (${esc(devComm)})` : 'public';
  const commVal   = s?.snmp_community || '';
  const commIsCustom = commVal && devComm && commVal !== devComm;
  const commStatusHtml = devComm
    ? (commIsCustom
        ? `<div class="fh" style="color:#fbbf24;font-size:10px;margin-top:2px">⚠ custom · clear field to use device default (${esc(devComm)})</div>`
        : `<div class="fh" style="color:#4ade80;font-size:10px;margin-top:2px">🔗 linked to device · clear field to keep linked</div>`)
    : '';
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
      <div class="stpo ${curType==='vmware'?'sel':''}" data-t="vmware" onclick="selType('vmware')">
        <div class="stpo-ico">V</div><div class="stpo-nm">VMWARE</div><div class="stpo-ds">VM metrics</div>
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
        <input type="text" id="as-ph" value="${esc(defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">Timeout (s)</label>
        <input type="number" id="as-pto" value="${s?.timeout||4}" min="1" max="30"/></div>
    </div>
  </div>
  <!-- TCP -->
  <div class="fg ${curType==='tcp'?'vis':''}" id="fg-tcp">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host / IP</label>
        <input type="text" id="as-th" value="${esc(defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
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
        <input type="text" id="as-sh" value="${esc(defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">UDP Port</label>
        <input type="number" id="as-sp" value="${s?.port||161}" min="1" max="65535"/></div>
    </div>
    <div class="fgrid">
      <div class="fr"><label class="fl">Community String</label>
        <input type="text" id="as-sc" value="${esc(commVal)}" placeholder="${commHint}" autocomplete="off"/>
        ${commStatusHtml}</div>
      <div class="fr"><label class="fl">SNMP Version</label>
        <select id="as-sv">
          ${(()=>{const dv=s?.snmp_version||dev?.snmp_version_default||'2c';
            return `<option value="2c" ${dv==='2c'?'selected':''}>v2c</option>
          <option value="1"  ${dv==='1'?'selected':''}>v1</option>
          <option value="3"  ${dv==='3'?'selected':''}>v3 (community)</option>`;})()}
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
      <input type="hidden" id="as-snmp-unit" value="${esc(s?.snmp_unit||'')}"/>
      <div class="fh" id="as-oid-unit2" style="min-height:14px">${s?.snmp_unit?'<b>Unit: '+esc(s.snmp_unit)+'</b>':'Type or paste an OID, choose from picker above, or use Discover Interfaces.'}</div>
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
        <input type="text" id="as-tlsh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
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
        <input type="text" id="as-bnh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-bnp" value="${s?.port||21}" min="1" max="65535"/></div>
    </div>
    <div class="fr"><label class="fl">Banner Regex <span style="color:var(--text3);font-weight:400">(optional)</span></label>
      <input type="text" id="as-bnr" value="${esc(s?.banner_regex||'')}" placeholder="Leave blank — any banner = UP" autocomplete="off"/></div>
  </div>
  <!-- VMWARE -->
  <div class="fg ${curType==='vmware'?'vis':''}" id="fg-vmware">
    <div class="fgrid">
      <div class="fr"><label class="fl">vCenter / ESXi Host</label>
        <input type="text" id="as-vmh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        </div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-vmp" value="${s?.port||443}" min="1" max="65535"/></div>
    </div>
    <div class="fgrid">
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="as-vmu" value="${esc(s?.vmware_user||dev?.vmware_user_default||'')}" placeholder="administrator@vsphere.local" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Password</label>
        <input type="password" id="as-vmpw" value="" placeholder="${s?.has_vmware_password?'(unchanged — leave blank to keep)':dev?.has_vmware_password_default?'(uses device default — leave blank)':'vCenter password'}" autocomplete="new-password"/></div>
    </div>
    <div class="fr" style="margin-top:4px">
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;color:var(--text2)">
        <input type="checkbox" id="as-vmssl" ${s?.verify_ssl===false?'':'checked'} style="width:auto;cursor:pointer"/>
        Verify SSL certificate
      </label>
    </div>
    <div class="fr" style="margin-top:4px">
      <div style="display:flex;gap:8px;align-items:center">
        <button class="dp-btn" type="button" onclick="discoverVMs()" id="as-vm-disc-btn">⊕ Discover VMs</button>
        <span id="as-vm-status" style="font-size:11px;color:var(--text3)"></span>
      </div>
      <div id="as-vm-list" style="display:none;margin-top:8px"></div>
    </div>
    <div class="fgrid" style="margin-top:4px">
      <div class="fr"><label class="fl">VM ID</label>
        <input type="text" id="as-vmid" value="${esc(s?.vmware_vm_id||'')}" placeholder="vm-123 (from discovery)" autocomplete="off" readonly/>
        <input type="hidden" id="as-vmnm" value="${esc(s?.vmware_vm_name||'')}"/>
        <div class="fh">Managed Object ID — use Discover VMs above</div>
      </div>
      <div class="fr"><label class="fl">Metric</label>
        <select id="as-vmmet"><option value="">— select metric —</option></select>
        <input type="hidden" id="as-vmmet-v" value="${esc(s?.vmware_metric||'')}"/>
      </div>
    </div>
    <div class="fr" id="as-vm-diskpath-row" style="display:${s?.vmware_metric==='disk_used_pct'?'':'none'}">
      <label class="fl">Disk Path</label>
      <input type="text" id="as-vm-diskpath" value="${esc(s?.vmware_disk_path||'')}" placeholder="e.g. C:\\ or /" autocomplete="off"/>
      <div class="fh">Partition to monitor — leave blank for most-used disk</div>
    </div>
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
      ${(()=>{
        const _su=s?.snmp_unit||'';
        const _isStr=curType==='snmp'&&_su==='string';
        if(_isStr) return`<div class="fgrid"><div class="fr"><div class="fh" style="color:var(--text3)">String OID — no numeric threshold</div></div></div>`;
        const _vmm=curType==='vmware'?(s?.vmware_metric||''):'';
        const _wLbl=curType==='tls'?'Warn Days (cert expiry)':curType==='snmp'?(_snmpThrLabel(_su,true)||'Warn Value'):curType==='vmware'?_vmwareThrLabel(_vmm,true):'Warn Latency (ms)';
        const _cLbl=curType==='tls'?'Crit Days (cert expiry)':curType==='snmp'?(_snmpThrLabel(_su,false)||'Crit Value'):curType==='vmware'?_vmwareThrLabel(_vmm,false):'Crit Latency (ms)';
        const _vmph=curType==='vmware'?(_VM_THR_PH[_vmm]||{w:'e.g. 80',c:'e.g. 90'}):null;
        const _ph=_vmph?_vmph.w:_su==='bytes'||_su===''&&curType==='snmp'?'e.g. 50':curType==='snmp'||curType==='tls'?'e.g. 100':'e.g. 200';
        const _phc=_vmph?_vmph.c:_su==='bytes'||_su===''&&curType==='snmp'?'e.g. 200':curType==='snmp'||curType==='tls'?'e.g. 50':'e.g. 500';
        const _cur=curType==='snmp'&&s?.last_value!=null?`<div class="fh" style="margin-top:2px">Current: <strong>${esc(String(s.last_value))}</strong></div>`:'';
        const _noThr=curType==='vmware'&&(_vmm==='uptime'||_vmm==='on');
        return`<div class="fgrid">
        <div class="fr" id="as-wms-row"${_noThr?' style="display:none"':''}><label class="fl" id="as-wms-lbl">${_wLbl}</label>
          <input type="number" id="as-wms" value="${s?.warn_ms||(window._snrTypeDefaults?.[curType]?.warn_ms||_SDR_WARN_DEF[curType]||'')}" placeholder="${_ph}" min="1" style="max-width:100px"/>
        </div>
        <div class="fr" id="as-cms-row"${_noThr?' style="display:none"':''}><label class="fl" id="as-cms-lbl">${_cLbl}</label>
          <input type="number" id="as-cms" value="${s?.crit_ms||(window._snrTypeDefaults?.[curType]?.crit_ms||_SDR_CRIT_DEF[curType]||'')}" placeholder="${_phc}" min="1" style="max-width:100px"/>
          ${_cur}
        </div></div>`;
      })()}
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

// ── ADD SENSOR MODAL ─────────────────────────────────────────────────────
function openAddSensor(did){
  const dev=S.devices[did];if(!dev)return;
  window._ifaceDid=did;
  window._snrAddMode=true;
  closeM('mas');
  const o=document.createElement('div');o.className='mo';o.id='mas';
  _overlayClose(o, ()=>closeM('mas'));
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
    if(initType==='vmware') _vmwareLoadMetrics();
  },50);
}

// ── EDIT SENSOR MODAL ────────────────────────────────────────────────────
function openEditSensor(did, sid){
  const key=`${did}/${sid}`;
  const s=S.sensors[key];
  if(!s)return;
  const dev=S.devices[did];
  window._ifaceDid=did;
  window._snrAddMode=false;
  closeM('mes');
  const o=document.createElement('div');o.className='mo';o.id='mes';
  _overlayClose(o, ()=>closeM('mes'));
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
    const _et=document.getElementById('as-t')?.value;
    if(_et==='snmp') _snmpLoadVendors();
    if(_et==='vmware') _vmwareLoadMetrics();
  },50);
}

async function submitEditSensor(did, sid){
  const payload = collectSensorForm(did);
  if(!payload) return;
  const btn=document.querySelector('#mes .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  let r;
  try{
    r = await api('PATCH', `/api/device/${did}/sensor/${sid}`, payload);
  }catch(e){
    toast('Failed to update sensor','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save Changes';}
  }
  if(r.status !== 'updated'){toast('Failed to update sensor','err');return;}
  closeM('mes');
  closeM('dm');
  const devR = await fetch(`/api/device/${did}`);
  if(!devR.ok){ toast('Sensor updated','ok'); return; }
  const dev  = await devR.json();
  S.devices[did] = dev;
  const ns = dev.sensors.find(s=>s.sensor_id===sid);
  if(ns){
    S.sensors[`${did}/${sid}`] = ns;
    renderTile(did, ns);
    setupCharts(dev);
    const nm=document.querySelector(`#sbsr-${did}_${sid} .s-snm`);
    if(nm)nm.textContent=ns.name;
  }
  toast('Sensor updated','ok');
}

function selType(t){
  document.getElementById('as-t').value=t;
  document.querySelectorAll('.stpo').forEach(o=>o.classList.toggle('sel',o.dataset.t===t));
  ['ping','tcp','http','snmp','dns','tls','http_keyword','banner','vmware'].forEach(x=>document.getElementById(`fg-${x}`)?.classList.toggle('vis',x===t));
  if(t==='snmp') _snmpLoadVendors();
  if(t==='vmware') _vmwareLoadMetrics();
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
  if(t==='snmp')       { _sv('as-sp',    d.port); const _snrDev=window._ifaceDid?S.devices[window._ifaceDid]:null; if(!_snrDev?.snmp_community_default) _sv('as-sc', d.community); if(d.version&&!_snrDev?.snmp_version_default) document.getElementById('as-sv').value=d.version; }
  if(t==='dns')        { _sv('as-dp',    d.port); if(d.record_type) document.getElementById('as-drt').value=d.record_type; _sv('as-ds', d.dns_server); }
  if(t==='tls')          _sv('as-tlsp',  d.port);
  if(t==='banner')       _sv('as-bnp',   d.port);
  if(t==='http')       { _sc('as-vssl',  d.verify_ssl); _sv('as-xstatus', d.http_expected_status); }
  if(t==='http_keyword'){ _sc('as-kwssl',d.verify_ssl); _sc('as-kwcase',  d.keyword_case); }
}

// ── SNMP OID catalog picker ───────────────────────────────────────────────
let _snmpCatalog=null;

// Interface metrics — defined at module level so reverse-lookup works before discovery runs
const _IFACE_METRICS=[
  {v:'status',   l:'Oper Status',         oid:'1.3.6.1.2.1.2.2.1.8.',    u:'1=up 2=down'},
  {v:'in_oct',   l:'In Traffic',          oid:'1.3.6.1.2.1.2.2.1.10.',   u:'bytes (32-bit)'},
  {v:'out_oct',  l:'Out Traffic',         oid:'1.3.6.1.2.1.2.2.1.16.',   u:'bytes (32-bit)'},
  {v:'in_hc',    l:'In Traffic (64-bit)', oid:'1.3.6.1.2.1.31.1.1.1.6.', u:'bytes (64-bit)'},
  {v:'out_hc',   l:'Out Traffic (64-bit)',oid:'1.3.6.1.2.1.31.1.1.1.10.',u:'bytes (64-bit)'},
  {v:'in_err',   l:'In Errors',           oid:'1.3.6.1.2.1.2.2.1.14.',   u:'errors'},
  {v:'out_err',  l:'Out Errors',          oid:'1.3.6.1.2.1.2.2.1.20.',   u:'errors'},
  {v:'in_disc',  l:'In Discards',         oid:'1.3.6.1.2.1.2.2.1.13.',   u:'packets'},
  {v:'out_disc', l:'Out Discards',        oid:'1.3.6.1.2.1.2.2.1.19.',   u:'packets'},
  {v:'speed',    l:'Link Speed',          oid:'1.3.6.1.2.1.2.2.1.5.',    u:'bits/sec'},
  {v:'admin_st', l:'Admin Status',        oid:'1.3.6.1.2.1.2.2.1.7.',    u:'1=up 2=down'},
];

async function _snmpLoadVendors(){
  const vsel=document.getElementById('as-oid-vendor');
  if(!vsel||vsel.options.length>1) return;
  if(_snmpCatalog){
    _snmpCatalog.forEach(v=>{
      const o=document.createElement('option');
      o.value=v.vendor; o.textContent=v.vendor;
      vsel.appendChild(o);
    });
    _snmpTryMatchCurrentOid();
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
    _snmpTryMatchCurrentOid();
  }catch(e){}
}

function _snmpTryMatchCurrentOid(){
  if(!_snmpCatalog) return;
  const oidEl=document.getElementById('as-oid');
  if(!oidEl) return;
  const currentOid=oidEl.value.trim();
  if(!currentOid) return;
  // Exact catalog match
  for(const vendor of _snmpCatalog){
    for(const o of vendor.oids){
      if(o.oid===currentOid){
        const vsel=document.getElementById('as-oid-vendor');
        if(vsel){ vsel.value=vendor.vendor; snmpVendorChange(); }
        const psel=document.getElementById('as-oid-pick');
        if(psel){ psel.value=o.oid; }
        const unitEl=document.getElementById('as-oid-unit');
        if(unitEl) unitEl.textContent=o.unit?'Unit: '+o.unit:'';
        return;
      }
    }
  }
  // Fallback: interface-discovered OID (base + numeric index, e.g. "1.3.6.1.2.1.2.2.1.14.44")
  const ifaceMetrics=_IFACE_METRICS;
  if(ifaceMetrics){
    const m=currentOid.match(/^(.+\.)(\d+)$/);
    if(m){
      const base=m[1], idx=m[2];
      const metric=ifaceMetrics.find(mm=>mm.oid===base);
      if(metric){
        // Try to resolve index to interface name from last discovery
        const cached=(window._ifaceDiscovery||[]).find(f=>String(f.index)===idx);
        const ifaceLabel=cached?(cached.name||cached.descr||idx):idx;
        const unitEl2=document.getElementById('as-oid-unit2');
        if(unitEl2) unitEl2.innerHTML=`<b>${metric.l} (interface ${esc(ifaceLabel)})</b> · Unit: ${esc(metric.u)}`;
      }
    }
  }
}

// Normalize metric u-field to canonical snmp_unit value stored in DB
function _normSnmpUnit(u){
  if(!u) return '';
  if(u.startsWith('bytes')) return 'bytes';   // "bytes (32-bit)", "bytes (64-bit)" → "bytes"
  return u;
}

// Map snmp_unit to a human-readable threshold label prefix
function _snmpThrLabel(unit, isWarn){
  const p=isWarn?'Warn':'Crit';
  if(unit==='bytes')    return `${p} (Mbps)`;
  if(unit==='errors')   return `${p} (err/s)`;
  if(unit==='packets')  return `${p} (pkt/s)`;
  if(unit==='%')        return `${p} (%)`;
  if(unit==='count')    return `${p} (count)`;
  if(unit==='°C')       return `${p} (°C)`;
  if(unit==='MB')       return `${p} (MB)`;
  if(unit==='KB')       return `${p} (KB)`;
  if(unit==='seconds')  return `${p} (sec)`;
  if(unit==='bits/sec') return `${p} (Mbps)`;
  if(unit==='sess/sec') return `${p} (sess/s)`;
  if(unit==='conn/sec') return `${p} (conn/s)`;
  if(unit&&unit.startsWith('1=')) return `${p} (≥ val)`;
  return null;  // caller uses type-based fallback
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
  const sunitEl=document.getElementById('as-snmp-unit');
  if(!psel||!oidEl) return;
  const sel=psel.options[psel.selectedIndex];
  if(sel&&sel.value){
    oidEl.value=sel.value;
    const u=sel.dataset.unit||'';
    if(unitEl) unitEl.textContent=u?'Unit: '+u:'';
    if(sunitEl) sunitEl.value=u;
  }
}

// ── SNMP Interface Discovery ──────────────────────────────────────────────
async function discoverInterfaces(){
  const did       = window._ifaceDid;
  const host      = document.getElementById('as-sh')?.value.trim() || S.devices[did]?.host || '';
  const community = document.getElementById('as-sc')?.value.trim()||S.devices[did]?.snmp_community_default||'public';
  const port      = parseInt(document.getElementById('as-sp')?.value)||161;
  const version   = document.getElementById('as-sv')?.value||'2c';
  const btn       = document.getElementById('as-disc-btn');
  const statusEl  = document.getElementById('as-iface-status');
  const listEl    = document.getElementById('as-iface-list');
  if(!host){ toast('Enter a Host / IP first','err'); return; }
  if(btn){ btn.disabled=true; btn.textContent='Discovering…'; }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='Querying device…'; }
  if(listEl){ listEl.style.display='none'; listEl.innerHTML=''; }
  let r;
  try{
    r=await api('POST','/api/snmp/interfaces',{host,community,port,version});
  }catch(e){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent='Request failed'; }
    return;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent='⊕ Discover Interfaces'; }
  }
  if(r.error){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent=r.error; }
    return;
  }
  const ifaces=r.interfaces||[];
  window._ifaceDiscovery=ifaces;  // cache for OID reverse-lookup
  _snmpTryMatchCurrentOid();       // re-run now that interface names are available
  if(!ifaces.length){
    if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='No interfaces returned.'; }
    return;
  }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent=`${ifaces.length} interface${ifaces.length!==1?'s':''} discovered`; }

  const METRICS=_IFACE_METRICS;
  window._ifaceMetrics=METRICS;

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
              onchange="updateIfaceSelCount()"
              style="font-size:11px;padding:2px 4px;max-width:140px">
        <option value="">— metric —</option>${opts}
      </select>
    </td>`;
    html+='</tr>';
  });

  html+='</tbody></table></div>';
  html+='<div style="padding:8px 10px;background:var(--bg2);border-top:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<button class="btn-p" style="font-size:11px;padding:5px 14px" onclick="addSelectedIfaceSensors()" id="as-iface-add-btn">Add Selected as Sensors</button>';
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
  const checked=cbs.filter(c=>c.checked);
  const n=checked.length;
  const el=document.getElementById('as-iface-sel-count');
  if(el) el.textContent=n?`${n} of ${cbs.length} selected`:'0 selected';
  const all=document.getElementById('as-iface-all');
  if(all){all.indeterminate=(n>0&&n<cbs.length);all.checked=(cbs.length>0&&n===cbs.length);}
  const addBtn=document.getElementById('as-iface-add-btn');
  if(addBtn) addBtn.textContent=(n===1)?'Apply to Form':'Add Selected as Sensors';
  // When exactly 1 interface+metric is selected, sync the OID and snmp_unit fields
  const oidEl=document.getElementById('as-oid');
  if(oidEl && n===1){
    const cb=checked[0];
    const idx=parseInt(cb.dataset.idx);
    const sel=document.querySelector(`.as-iface-metric[data-idx="${cb.dataset.idx}"]`);
    const metric=(window._ifaceMetrics||[]).find(m=>m.v===sel?.value);
    if(metric && !isNaN(idx)){
      oidEl.value=metric.oid+idx;
      const ifaceName=cb.dataset.name||('interface '+idx);
      const unitEl=document.getElementById('as-oid-unit2');
      if(unitEl) unitEl.innerHTML=`<b>${esc(metric.l)} on ${esc(ifaceName)}</b> · Unit: ${esc(metric.u)}`;
      const sunitEl=document.getElementById('as-snmp-unit');
      if(sunitEl) sunitEl.value=_normSnmpUnit(metric.u);
    }
  }
}

async function addSelectedIfaceSensors(){
  const did=window._ifaceDid;
  if(!did){toast('Device context lost — reopen the sensor form','err');return;}
  const checked=[...document.querySelectorAll('.as-iface-cb:checked')];
  if(!checked.length){toast('Select at least one interface','err');return;}

  // ── Single selection: apply OID to form and let user continue editing ──
  if(checked.length===1){
    const cb=checked[0];
    const idx=cb.dataset.idx;
    const sel=document.querySelector(`.as-iface-metric[data-idx="${idx}"]`);
    if(!sel||!sel.value){toast('Choose a metric for the selected interface','err');return;}
    const metric=(window._ifaceMetrics||[]).find(m=>m.v===sel.value);
    if(!metric){toast('Unknown metric','err');return;}
    const oidEl=document.getElementById('as-oid');
    const sunitEl=document.getElementById('as-snmp-unit');
    const ifaceName=cb.dataset.name||('interface '+idx);
    if(oidEl) oidEl.value=metric.oid+idx;
    if(sunitEl) sunitEl.value=_normSnmpUnit(metric.u);
    const unitEl2=document.getElementById('as-oid-unit2');
    if(unitEl2) unitEl2.innerHTML=`<b>${esc(metric.l)} on ${esc(ifaceName)}</b> · Unit: ${esc(metric.u)}`;
    const listEl=document.getElementById('as-iface-list');
    if(listEl) listEl.style.display='none';
    const statusEl=document.getElementById('as-iface-status');
    if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent=`Applied: ${metric.l} on ${ifaceName} — save to confirm`;}
    return;
  }

  // ── Edit mode with multiple: not supported ─────────────────────────────
  if(!window._snrAddMode){toast('Select exactly one interface to apply to the form','err');return;}

  // ── Add mode with multiple: create all via API ─────────────────────────
  const host=document.getElementById('as-sh')?.value.trim()||S.devices[did]?.host||'';
  const community=document.getElementById('as-sc')?.value.trim()||'';
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
      snmp_unit:_normSnmpUnit(row.metric.u),
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
    }catch(e){}
    toast(`Added ${added} sensor${added>1?'s':''}${failed?`, ${failed} failed`:''}`, 'ok');
    closeM('mas');
  }else{
    toast('Failed to add sensors','err');
  }
}

// ── VMware VM Discovery ──────────────────────────────────────────────────

let _vmwareMetrics=null;
let _vmSelectedMemMB=0;  // memory of currently selected VM (MB), for smart threshold defaults

// Auto-fill warn/crit thresholds based on VM specs and metric type.
// Only fills if both fields are currently empty (never overwrites user input).
function _vmwareThrAutoFill(metric, memMB){
  const wi=document.getElementById('as-wms');
  const ci=document.getElementById('as-cms');
  if(!wi||!ci||wi.value||ci.value) return;
  let w=null,c=null;
  if(memMB>0){
    if(metric==='mem_consumed'){ w=Math.round(memMB*0.80); c=Math.round(memMB*0.90); }
    else if(metric==='mem_active'){ w=Math.round(memMB*0.50); c=Math.round(memMB*0.70); }
  }
  if(w!=null){ wi.value=w; ci.value=c; }
}

function _vmwareThrLabel(metric, isWarn){
  const pfx=isWarn?'Warn':'Crit';
  if(!metric) return pfx+' Value';
  const m=(_vmwareMetrics||[]).find(x=>x.v===metric);
  const u=m?.unit||'';
  if(u==='%')       return pfx+' %';
  if(u==='MB')      return pfx+' MB';
  if(u==='KB')      return pfx+' KB';
  if(u==='KBps')    return pfx+' KBps';
  if(u==='ms')      return pfx+' ms';
  if(u==='seconds') return pfx+' seconds';
  return pfx+' Value';
}

// Per-metric sensible warn/crit placeholder hints
const _VM_THR_PH = {
  cpu_usage:       {w:'e.g. 80',  c:'e.g. 90'},
  cpu_ready:       {w:'e.g. 10',  c:'e.g. 20'},
  mem_consumed_pct:{w:'e.g. 80',  c:'e.g. 90'},
  mem_active:      {w:'e.g. 4096',c:'e.g. 6144'},   // MB (4GB / 6GB)
  mem_consumed:    {w:'e.g. 12288',c:'e.g. 14336'}, // MB (12GB / 14GB)
  disk_used_pct:   {w:'e.g. 80',  c:'e.g. 90'},
  disk_read:       {w:'e.g. 500', c:'e.g. 1000'},
  disk_write:      {w:'e.g. 500', c:'e.g. 1000'},
  disk_usage:      {w:'e.g. 500', c:'e.g. 1000'},
  ds_read_lat:     {w:'e.g. 20',  c:'e.g. 50'},
  ds_write_lat:    {w:'e.g. 20',  c:'e.g. 50'},
  net_rx:          {w:'e.g. 500', c:'e.g. 1000'},
  net_tx:          {w:'e.g. 500', c:'e.g. 1000'},
  net_usage:       {w:'e.g. 500', c:'e.g. 1000'},
  uptime:          {w:'e.g. 86400',c:'e.g. 3600'},
};

function _vmwareThrUpdateLabels(){
  const sel=document.getElementById('as-vmmet');
  if(!sel) return;
  const wl=document.getElementById('as-wms-lbl');
  const cl=document.getElementById('as-cms-lbl');
  if(wl) wl.textContent=_vmwareThrLabel(sel.value,true);
  if(cl) cl.textContent=_vmwareThrLabel(sel.value,false);
  const _noThr2=sel.value==='uptime'||sel.value==='on';
  const wr=document.getElementById('as-wms-row');
  const cr=document.getElementById('as-cms-row');
  if(wr) wr.style.display=_noThr2?'none':'';
  if(cr) cr.style.display=_noThr2?'none':'';
  const dpRow=document.getElementById('as-vm-diskpath-row');
  if(dpRow) dpRow.style.display=sel.value==='disk_used_pct'?'':'none';
  // Update placeholders to metric-appropriate hints
  const ph=_VM_THR_PH[sel.value];
  const wi=document.getElementById('as-wms');
  const ci=document.getElementById('as-cms');
  if(wi) wi.placeholder=ph?ph.w:'e.g. 80';
  if(ci) ci.placeholder=ph?ph.c:'e.g. 90';
  _vmwareThrAutoFill(sel.value, _vmSelectedMemMB);
}

async function _vmwareLoadMetrics(){
  const sel=document.getElementById('as-vmmet');
  if(!sel) return;
  sel.onchange=()=>_vmwareThrUpdateLabels();
  if(sel.options.length>1){ _vmwareThrUpdateLabels(); return; }
  if(!_vmwareMetrics){
    try{
      const r=await fetch('/api/vmware/metrics');
      const d=await r.json();
      _vmwareMetrics=d.metrics||[];
    }catch(e){ return; }
  }
  _vmwareMetrics.forEach(m=>{
    const o=document.createElement('option');
    o.value=m.v;
    o.textContent=m.l+' ('+m.unit+')';
    sel.appendChild(o);
  });
  const cur=document.getElementById('as-vmmet-v')?.value;
  if(cur) sel.value=cur;
  _vmwareThrUpdateLabels();
}

async function discoverVMs(){
  const did      =window._ifaceDid;
  const host     =document.getElementById('as-vmh')?.value.trim()||S.devices[did]?.host||'';
  const username =document.getElementById('as-vmu')?.value.trim()||'';
  const password =document.getElementById('as-vmpw')?.value||'';
  const port     =parseInt(document.getElementById('as-vmp')?.value)||443;
  const vssl     =document.getElementById('as-vmssl')?.checked!==false;
  const btn      =document.getElementById('as-vm-disc-btn');
  const statusEl =document.getElementById('as-vm-status');
  const listEl   =document.getElementById('as-vm-list');
  const dev=did?S.devices[did]:null;
  if(!host){ toast('Enter a Host / IP first','err'); return; }
  if(!username){ toast('Enter a Username','err'); return; }
  if(!password && !dev?.has_vmware_password_default){ toast('Enter a Password','err'); return; }
  if(btn){ btn.disabled=true; btn.textContent='Discovering…'; }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='Connecting to vCenter…'; }
  if(listEl){ listEl.style.display='none'; listEl.innerHTML=''; }
  const payload={host,username,password,port,verify_ssl:vssl};
  if(!password && did) payload.did=did;
  let r;
  try{
    r=await api('POST','/api/vmware/vms',payload);
  }catch(e){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent='Request failed'; }
    return;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent='⊕ Discover VMs'; }
  }
  if(r.error){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent=r.error; }
    return;
  }
  const vms=r.vms||[];
  if(!vms.length){
    if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='No VMs found.'; }
    return;
  }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent=`${vms.length} VM${vms.length!==1?'s':''} discovered`; }

  // Ensure metrics are loaded
  if(!_vmwareMetrics){
    try{
      const mr=await fetch('/api/vmware/metrics');
      const md=await mr.json();
      _vmwareMetrics=md.metrics||[];
    }catch(e){}
  }
  const metricCheckboxes=
    `<label class="vm-met-item vm-met-all-item" style="border-bottom:1px solid var(--border);margin-bottom:3px;padding-bottom:6px"><input type="checkbox" class="vm-met-all-cb" onchange="vmMetSelectAll(this)"> <strong>All metrics</strong></label>`+
    (_vmwareMetrics||[]).map(m=>
      `<label class="vm-met-item"><input type="checkbox" value="${m.v}" onchange="vmMetChanged(this)"> ${esc(m.l)}</label>`
    ).join('');

  let html='<div style="border:1px solid var(--border);border-radius:6px;overflow:hidden;margin-top:4px">';
  html+='<div style="padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<input type="text" id="as-vm-search" placeholder="Search VM names…" oninput="filterVMs(this.value)" autocomplete="off" style="flex:1;font-size:12px;padding:4px 8px;background:var(--bg3);border:1px solid var(--border2);border-radius:4px;color:var(--text);outline:none"/>';
  html+='<span style="font-size:11px;color:var(--text3);white-space:nowrap;flex-shrink:0">Set for checked:</span>';
  html+=`<div class="vm-met-wrap" style="flex-shrink:0"><button class="vm-met-btn" type="button" onclick="toggleVmMetPicker(this)">— bulk metrics —</button><div class="vm-met-drop" style="display:none;right:0">${metricCheckboxes}</div></div>`;
  html+='</div>';
  html+='<div style="overflow-x:auto;overflow-y:auto;max-height:260px">';
  html+='<table style="width:100%;border-collapse:collapse;font-size:12px">';
  html+='<thead><tr style="background:var(--bg2);color:var(--text2);position:sticky;top:0;z-index:1">';
  html+='<th style="padding:5px 8px;text-align:center;white-space:nowrap"><input type="checkbox" id="as-vm-all" title="Select all visible" onchange="toggleAllVMs(this)"/></th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;min-width:160px">VM Name</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Power</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;max-width:120px">Guest OS</th>';
  html+='<th style="padding:5px 8px;text-align:center;white-space:nowrap">CPUs</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Mem</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;min-width:170px">Metrics</th>';
  html+='</tr></thead><tbody id="as-vm-tbody">';

  vms.forEach((vm,i)=>{
    const pwClr=vm.power_state==='poweredOn'?'var(--up)':'var(--down)';
    const pwTxt=vm.power_state==='poweredOn'?'on':'off';
    const memStr=vm.memory_mb>=1024?Math.round(vm.memory_mb/1024)+'GB':vm.memory_mb+'MB';
    const rowBg=i%2?'background:var(--bg2)':'';
    html+=`<tr style="border-top:1px solid var(--border);${rowBg}">`;
    html+=`<td style="padding:4px 8px;text-align:center"><input type="checkbox" class="as-vm-cb" data-vmid="${esc(vm.vm_id)}" data-name="${esc(vm.name)}" data-mem-mb="${vm.memory_mb||0}" data-num-cpu="${vm.num_cpu||0}" onchange="updateVMSelCount()"/></td>`;
    html+=`<td style="padding:4px 8px;font-weight:500;white-space:nowrap" title="${esc(vm.name)}">${esc(vm.name)}</td>`;
    html+=`<td style="padding:4px 8px;color:${pwClr};white-space:nowrap">${pwTxt}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text2);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(vm.guest_os)}">${esc(vm.guest_os)}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);text-align:center;white-space:nowrap">${vm.num_cpu}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);white-space:nowrap">${memStr}</td>`;
    html+=`<td style="padding:4px 8px;position:relative"><div class="vm-met-wrap" data-vmid="${esc(vm.vm_id)}"><button class="vm-met-btn" type="button" onclick="toggleVmMetPicker(this)">— pick metrics —</button><div class="vm-met-drop" style="display:none">${metricCheckboxes}</div></div></td>`;
    html+='</tr>';
  });

  html+='</tbody></table></div>';
  html+='<div style="padding:8px 10px;background:var(--bg2);border-top:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<button class="btn-p" style="font-size:11px;padding:5px 14px" onclick="addSelectedVMSensors()" id="as-vm-add-btn">Add Selected as Sensors</button>';
  html+='<span id="as-vm-sel-count" style="font-size:11px;color:var(--text3)">0 VMs · 0 sensors</span>';
  html+='</div></div>';
  listEl.innerHTML=html;
  listEl.style.display='';
}

function toggleAllVMs(cb){
  document.querySelectorAll('#as-vm-tbody tr:not([style*="display: none"]) .as-vm-cb').forEach(c=>c.checked=cb.checked);
  updateVMSelCount();
}

function filterVMs(q){
  const term=q.toLowerCase();
  document.querySelectorAll('#as-vm-tbody tr').forEach(tr=>{
    const name=tr.querySelector('td:nth-child(2)')?.textContent.toLowerCase()||'';
    tr.style.display=name.includes(term)?'':'none';
  });
}

function _getVmMetrics(vmid){
  // Returns array of selected metric values for a given VM row picker
  const wrap=document.querySelector(`.vm-met-wrap[data-vmid="${CSS.escape(vmid)}"]`);
  if(!wrap) return [];
  return [...wrap.querySelectorAll('.vm-met-drop input:checked')].map(c=>c.value);
}

function updateVMSelCount(){
  const visibleCbs=[...document.querySelectorAll('#as-vm-tbody tr:not([style*="display: none"]) .as-vm-cb')];
  const allCbs=[...document.querySelectorAll('.as-vm-cb')];
  const checked=allCbs.filter(c=>c.checked);
  const nVms=checked.length;
  // Count total sensors = sum of metrics per checked VM
  let nSensors=0;
  checked.forEach(cb=>{ const m=_getVmMetrics(cb.dataset.vmid); nSensors+=m.length||1; });
  const el=document.getElementById('as-vm-sel-count');
  if(el) el.textContent=nVms?`${nVms} VM${nVms>1?'s':''} · ${nSensors} sensor${nSensors!==1?'s':''}`:' 0 VMs · 0 sensors';
  const all=document.getElementById('as-vm-all');
  if(all){all.indeterminate=(nVms>0&&nVms<visibleCbs.length);all.checked=(visibleCbs.length>0&&checked.length>=visibleCbs.length);}
  const addBtn=document.getElementById('as-vm-add-btn');
  if(addBtn) addBtn.textContent=(nVms===1&&nSensors<=1)?'Apply to Form':'Add Selected as Sensors';
}

// ── Metric picker dropdown ────────────────────────────────────────
let _vmMetPickerOpen=null;
function toggleVmMetPicker(btn){
  const drop=btn.nextElementSibling;
  const isOpen=drop.style.display!=='none';
  // Close any other open picker
  if(_vmMetPickerOpen&&_vmMetPickerOpen!==drop) _vmMetPickerOpen.style.display='none';
  drop.style.display=isOpen?'none':'block';
  _vmMetPickerOpen=isOpen?null:drop;
}
// Close picker on outside click
document.addEventListener('click',e=>{
  if(_vmMetPickerOpen&&!e.target.closest('.vm-met-wrap')){
    _vmMetPickerOpen.style.display='none';
    _vmMetPickerOpen=null;
  }
});
function vmMetSelectAll(allCb){
  const drop=allCb.closest('.vm-met-drop');
  if(!drop) return;
  drop.querySelectorAll('input[value]').forEach(c=>c.checked=allCb.checked);
  vmMetChanged(allCb); // reuse label update + bulk apply logic
}
function vmMetChanged(cb){
  // Update the button label for this picker
  const drop=cb.closest('.vm-met-drop');
  const wrap=drop?.parentElement;
  if(!wrap) return;
  // Keep "All metrics" checkbox in sync
  const allCb=drop.querySelector('.vm-met-all-cb');
  if(allCb&&cb!==allCb){
    const metCbs=[...drop.querySelectorAll('input[value]')];
    const nChecked=metCbs.filter(c=>c.checked).length;
    allCb.checked=nChecked===metCbs.length;
    allCb.indeterminate=nChecked>0&&nChecked<metCbs.length;
  }
  const checked=[...drop.querySelectorAll('input[value]:checked')];
  const btn=wrap.querySelector('.vm-met-btn');
  if(btn){
    if(!checked.length) btn.textContent='— pick metrics —';
    else if(checked.length===1) btn.textContent=checked[0].parentElement.textContent.trim();
    else btn.textContent=`${checked.length} metrics`;
  }
  // If this is the bulk picker (in header, no data-vmid), apply to all checked rows
  if(!wrap.dataset.vmid){
    const checkedVmIds=[...document.querySelectorAll('.as-vm-cb:checked')].map(c=>c.dataset.vmid);
    checkedVmIds.forEach(vmid=>{
      const rowWrap=document.querySelector(`.vm-met-wrap[data-vmid="${CSS.escape(vmid)}"]`);
      if(!rowWrap) return;
      // Mirror the bulk selection to this row
      rowWrap.querySelectorAll('.vm-met-drop input').forEach(rowCb=>{
        rowCb.checked=!!drop.querySelector(`input[value="${rowCb.value}"]:checked`);
      });
      // Update row button label (count real metric checkboxes only)
      const rowChecked=[...rowWrap.querySelectorAll('.vm-met-drop input[value]:checked')];
      const rowBtn=rowWrap.querySelector('.vm-met-btn');
      if(rowBtn){
        if(!rowChecked.length) rowBtn.textContent='— pick metrics —';
        else if(rowChecked.length===1) rowBtn.textContent=rowChecked[0].parentElement.textContent.trim();
        else rowBtn.textContent=`${rowChecked.length} metrics`;
      }
      // Sync "All" checkbox in row
      const rowAllCb=rowWrap.querySelector('.vm-met-all-cb');
      const rowAllMetCbs=[...rowWrap.querySelectorAll('.vm-met-drop input[value]')];
      if(rowAllCb){rowAllCb.checked=rowChecked.length===rowAllMetCbs.length;rowAllCb.indeterminate=rowChecked.length>0&&rowChecked.length<rowAllMetCbs.length;}
    });
  }
  updateVMSelCount();
}

async function addSelectedVMSensors(){
  const did=window._ifaceDid;
  if(!did){toast('Device context lost — reopen the sensor form','err');return;}
  const checked=[...document.querySelectorAll('.as-vm-cb:checked')];
  if(!checked.length){toast('Select at least one VM','err');return;}

  // ── Single VM, single metric: apply to form ──
  const firstMetrics=_getVmMetrics(checked[0].dataset.vmid);
  if(checked.length===1&&firstMetrics.length<=1){
    const cb=checked[0];
    const vmid=cb.dataset.vmid;
    const vmname=cb.dataset.name;
    _vmSelectedMemMB=parseInt(cb.dataset.memMb)||0;
    const metric=firstMetrics[0]||'';
    if(!metric){toast('Pick at least one metric for this VM','err');return;}
    const metricDef=(_vmwareMetrics||[]).find(m=>m.v===metric);
    const oidEl=document.getElementById('as-vmid');
    if(oidEl) oidEl.value=vmid;
    const nmEl=document.getElementById('as-vmnm');
    if(nmEl) nmEl.value=vmname;
    const metSel=document.getElementById('as-vmmet');
    if(metSel) metSel.value=metric;
    const metV=document.getElementById('as-vmmet-v');
    if(metV) metV.value=metric;
    const nameEl=document.getElementById('as-n');
    if(nameEl&&(!nameEl.value||nameEl.value.startsWith('Ping,')))
      nameEl.value=`${vmname} ${metricDef?metricDef.l:metric}`;
    const listEl=document.getElementById('as-vm-list');
    if(listEl) listEl.style.display='none';
    const statusEl=document.getElementById('as-vm-status');
    if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent=`Applied: ${metricDef?metricDef.l:metric} on ${vmname} — save to confirm`;}
    return;
  }

  // ── Edit mode with multiple: not supported
  if(!window._snrAddMode){toast('Select exactly one VM (1 metric) to apply to the form','err');return;}

  // ── Add mode: create one sensor per VM × metric ──
  const host=document.getElementById('as-vmh')?.value.trim()||S.devices[did]?.host||'';
  const username=document.getElementById('as-vmu')?.value.trim()||'';
  const password=document.getElementById('as-vmpw')?.value||'';
  const port=parseInt(document.getElementById('as-vmp')?.value)||443;
  const vssl=document.getElementById('as-vmssl')?.checked!==false;
  const iv=parseInt(document.getElementById('as-iv')?.value)||60;
  const tmo=parseInt(document.getElementById('as-tmo')?.value)||30;
  const startNow=document.getElementById('as-si')?.value==='1';
  const fa=parseInt(document.getElementById('as-fa')?.value)||1;
  const ra=parseInt(document.getElementById('as-ra')?.value)||1;
  const wms=parseInt(document.getElementById('as-wms')?.value)||null;
  const cms=parseInt(document.getElementById('as-cms')?.value)||null;

  // Expand checked VMs × their selected metrics into individual sensor rows
  const rows=[];
  let noMetric=0;
  checked.forEach(cb=>{
    const vmid=cb.dataset.vmid;
    const vmname=cb.dataset.name;
    const vmMemMB=parseInt(cb.dataset.memMb)||0;
    const metrics=_getVmMetrics(vmid);
    if(!metrics.length){noMetric++;return;}
    metrics.forEach(metric=>{
      const metricDef=(_vmwareMetrics||[]).find(m=>m.v===metric);
      // Smart per-VM threshold: only if user left warn/crit blank
      let rowWms=wms, rowCms=cms;
      if(!rowWms&&!rowCms&&vmMemMB>0){
        if(metric==='mem_consumed'){ rowWms=Math.round(vmMemMB*0.80); rowCms=Math.round(vmMemMB*0.90); }
        else if(metric==='mem_active'){ rowWms=Math.round(vmMemMB*0.50); rowCms=Math.round(vmMemMB*0.70); }
      }
      rows.push({vmid,vmname,metric,metricLabel:metricDef?metricDef.l:metric,wms:rowWms,cms:rowCms});
    });
  });
  if(noMetric) toast(`${noMetric} VM${noMetric>1?'s':''} skipped — no metrics chosen`,'info');
  if(!rows.length){toast('Pick at least one metric for each checked VM','err');return;}
  const btn=document.getElementById('as-vm-add-btn');
  if(btn){btn.disabled=true;btn.textContent=`Adding ${rows.length}…`;}
  let added=0,failed=0;
  const addedSids=[];
  for(const row of rows){
    const sname=`${row.vmname} ${row.metricLabel}`;
    try{
      const r=await api('POST',`/api/device/${did}/sensor`,{
        name:sname,type:'vmware',host,port,interval:iv,timeout:tmo,
        verify_ssl:vssl,fail_after:fa,recover_after:ra,warn_ms:row.wms,crit_ms:row.cms,
        vmware_user:username,vmware_password:password,
        vmware_vm_id:row.vmid,vmware_vm_name:row.vmname,vmware_metric:row.metric
      });
      if(r&&r.sid){
        added++;addedSids.push(r.sid);
        if(startNow) await api('POST',`/api/device/${did}/sensor/${r.sid}/start`);
      }else{ failed++; }
    }catch(e){ failed++; }
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
    }catch(e){}
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
      snmp_community='public',snmp_oid='1.3.6.1.2.1.1.1.0',snmp_version='2c',snmp_unit='',
      dns_query='',dns_record_type='A',dns_server='',http_expected_status=0,
      keyword='',keyword_case=false,banner_regex='';
  const _devHost = S.devices[did]?.host || '';
  if(type==='ping'){
    host=document.getElementById('as-ph')?.value.trim()||'';
  } else if(type==='tcp'){
    host=document.getElementById('as-th')?.value.trim()||'';
    port=parseInt(document.getElementById('as-tp')?.value);
    if(!port){toast('Port number required','err');return null;}
  } else if(type==='http'){
    url=document.getElementById('as-hu')?.value.trim();
    if(!url){toast('URL required','err');return null;}
    host='';  // HTTP uses URL — host always inherited from device
    verify_ssl=document.getElementById('as-vssl')?.checked!==false;
    http_expected_status=parseInt(document.getElementById('as-xstatus')?.value)||0;
  } else if(type==='snmp'){
    host=document.getElementById('as-sh')?.value.trim()||'';
    port=parseInt(document.getElementById('as-sp')?.value)||161;
    snmp_community=document.getElementById('as-sc')?.value.trim()||'';
    snmp_oid=document.getElementById('as-oid')?.value.trim()||'1.3.6.1.2.1.1.1.0';
    snmp_version=document.getElementById('as-sv')?.value||'2c';
    snmp_unit=document.getElementById('as-snmp-unit')?.value||'';
  } else if(type==='dns'){
    dns_query=document.getElementById('as-dq')?.value.trim()||_devHost||'';
    if(!dns_query){toast('Query hostname required','err');return null;}
    dns_record_type=document.getElementById('as-drt')?.value||'A';
    dns_server=document.getElementById('as-ds')?.value.trim()||'';
    port=parseInt(document.getElementById('as-dp')?.value)||53;
    host=dns_server||_devHost||'';
  } else if(type==='tls'){
    host=(document.getElementById('as-tlsh')?.value.trim()||'')
         .replace(/^https?:\/\//i,'').split('/')[0];
    port=parseInt(document.getElementById('as-tlsp')?.value)||443;
    if(!host && !_devHost){toast('Host required','err');return null;}
  } else if(type==='http_keyword'){
    url=document.getElementById('as-kwu')?.value.trim();
    if(!url){toast('URL required','err');return null;}
    keyword=document.getElementById('as-kww')?.value.trim()||'';
    if(!keyword){toast('Keyword required','err');return null;}
    verify_ssl=document.getElementById('as-kwssl')?.checked!==false;
    keyword_case=document.getElementById('as-kwcase')?.checked||false;
    host='';  // HTTP KW uses URL — host always inherited from device
  } else if(type==='banner'){
    host=document.getElementById('as-bnh')?.value.trim()||'';
    port=parseInt(document.getElementById('as-bnp')?.value)||21;
    banner_regex=document.getElementById('as-bnr')?.value.trim()||'';
    if(!host && !_devHost){toast('Host required','err');return null;}
  } else if(type==='vmware'){
    host=document.getElementById('as-vmh')?.value.trim()||'';
    port=parseInt(document.getElementById('as-vmp')?.value)||443;
    verify_ssl=document.getElementById('as-vmssl')?.checked!==false;
  }
  const fail_after   =Math.max(1,parseInt(document.getElementById('as-fa')?.value)||1);
  const recover_after=Math.max(1,parseInt(document.getElementById('as-ra')?.value)||1);
  const warn_ms      =parseInt(document.getElementById('as-wms')?.value)||null;
  const crit_ms      =parseInt(document.getElementById('as-cms')?.value)||null;
  const loss_warn_pct=parseInt(document.getElementById('as-lwp')?.value)||0;
  const loss_crit_pct=parseInt(document.getElementById('as-lcp')?.value)||0;
  const alerts_muted =document.getElementById('as-am')?.checked||false;
  if(!name){toast('Sensor name required','err');return null;}
  const payload={type,name,host,port,url,interval:iv,timeout:tmo,
          verify_ssl,snmp_community,snmp_oid,snmp_version,snmp_unit,
          dns_query,dns_record_type,dns_server,http_expected_status,
          fail_after,recover_after,warn_ms,crit_ms,loss_warn_pct,loss_crit_pct,
          keyword,keyword_case,banner_regex,alerts_muted};
  if(type==='vmware'){
    payload.vmware_user=document.getElementById('as-vmu')?.value.trim()||'';
    payload.vmware_password=document.getElementById('as-vmpw')?.value||'';
    payload.vmware_vm_id=document.getElementById('as-vmid')?.value.trim()||'';
    payload.vmware_vm_name=document.getElementById('as-vmnm')?.value.trim()||'';
    payload.vmware_metric=document.getElementById('as-vmmet')?.value||'';
    payload.vmware_disk_path=document.getElementById('as-vm-diskpath')?.value.trim()||'';
    if(!payload.vmware_vm_id){toast('VM ID required — use Discover VMs','err');return null;}
    if(!payload.vmware_metric){toast('Select a metric','err');return null;}
  }
  return payload;
}

async function submitAddSensor(did){
  // If VMware type with discovery table open and VMs checked, delegate to addSelectedVMSensors()
  const _asType=document.getElementById('as-t')?.value||'ping';
  if(_asType==='vmware'){
    const _listEl=document.getElementById('as-vm-list');
    if(_listEl&&_listEl.style.display!=='none'){
      const _checked=[...document.querySelectorAll('.as-vm-cb:checked')];
      if(_checked.length){await addSelectedVMSensors();return;}
    }
  }
  const payload=collectSensorForm(did);
  if(!payload)return;
  const start=document.getElementById('as-si')?.value==='1';
  const btn=document.querySelector('#mas .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Adding...';}
  try{
    const r=await api('POST',`/api/device/${did}/sensor`,payload);
    if(!r||!r.sid){toast(r?.error||'Failed to add sensor','err');return;}
    if(start) await api('POST',`/api/device/${did}/sensor/${r.sid}/start`);
    try{
      const devR=await fetch(`/api/device/${did}`);
      if(devR.ok){
        const dev=await devR.json();
        S.devices[did]=dev;
        const ns=(dev.sensors||[]).find(s=>s.sensor_id===r.sid);
        if(ns){
          S.sensors[`${did}/${r.sid}`]=ns;
          S.logs[`${did}/${r.sid}`]=[];
          if(document.getElementById('dwo')&&document.getElementById(`sg-${did}`)){
            renderTile(did,ns);setupCharts(dev);
          }
        }
        const sbsl=document.getElementById(`sbsl-${did}`);
        if(sbsl&&ns){
          const row=document.createElement('div');
          row.className='snr-row';row.id=`sbsr-${did}_${r.sid}`;
          row.onclick=()=>openDetail(did,r.sid);
          row.innerHTML=`<div class="s-ico ${ns.stype}">${sIco(ns.stype)}</div><div class="s-snm">${esc(ns.name)}</div><div class="s-sdot unknown" id="sbsd-${did}_${r.sid}"></div>`;
          sbsl.appendChild(row);
        }
        const cnt=document.querySelector(`#sbn-${did} .dev-cnt`);
        if(cnt) cnt.textContent=(dev.sensors||[]).length;
        const previewEl=document.getElementById(`dcsnr-${did}`);
        if(previewEl) previewEl.innerHTML=sSnrPreview(did);
      }
    }catch(e2){}
    closeM('mas');
    toast('Sensor added','ok');
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
  }catch(e){}
  toast(`Sensor "${name}" added`,'ok');
}
