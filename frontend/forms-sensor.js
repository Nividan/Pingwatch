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
  const isEdit = !!s;
  // Sensor-type catalogue. Tuple shape:
  //   [key, name, sub, icon, category, keywords]
  // `category` groups the sidebar; `keywords` is a free-text search index so a
  // user typing 'web', 'mail' or 'cert' surfaces the right type without having
  // to know the canonical name.
  const _types = [
    ['ping',         'Ping',     'ICMP echo',     '◉','Network',       'icmp echo reachability latency'],
    ['tcp',          'TCP Port', 'Port check',    '⇌','Network',       'port socket connect'],
    ['http',         'HTTP/S',   'Web response',  '◈','Network',       'web url status response code'],
    ['dns',          'DNS',      'Record lookup', '⬡','Network',       'resolve a aaaa mx ns ptr soa'],
    ['snmp',         'SNMP',     'OID polling',   '◎','Monitoring',    'oid polling counter mib'],
    ['tls',          'TLS',      'Cert expiry',   'T','Monitoring',    'certificate ssl x509 expiry'],
    ['banner',       'Banner',   'TCP banner',    'B','Monitoring',    'greeting handshake regex'],
    ['http_keyword', 'HTTP KW',  'Keyword check', 'K','Application',   'grep match content body'],
    ['ssh',          'SSH',      'Secure Shell',  '⇲','Auth & Access', 'login secure shell remote'],
    ['smtp',         'SMTP',     'Mail server',   '✉','Auth & Access', 'mail email server relay'],
    ['radius',       'RADIUS',   'AAA auth test', 'R','Auth & Access', 'aaa authentication authorization accounting nas freeradius nps ise fortiauthenticator login 1812'],
    ['sftp',         'SFTP',     'Secure file transfer','⇑','File Transfer','file upload download scp transfer backup'],
    ['vmware',       'VMware',   'VM metrics',    'V','Virtualization','vsphere esxi vm hypervisor'],
  ];
  // Group by category, preserving order of first appearance.
  const _catOrder = [];
  const _byCat = {};
  for (const t of _types) {
    const c = t[4] || 'Other';
    if (!_byCat[c]) { _byCat[c] = []; _catOrder.push(c); }
    _byCat[c].push(t);
  }
  const _navBtn = ([k,nm,sub,ico,cat,kw]) => {
    // data-search packs everything the filter looks at into one attribute,
    // so _sensorTypeFilter doesn't need to reach back into _types on every
    // keystroke. Lower-cased once at render time.
    const hay = `${nm} ${sub} ${cat} ${kw}`.toLowerCase();
    return `<button class="stab-nav${curType===k?' active':''}" data-t="${k}" data-search="${esc(hay)}" onclick="selType('${k}')"><span class="snav-ico">${ico}</span><span class="snav-lbl"><span>${esc(nm)}</span><span class="snav-sub">${esc(sub)}</span></span></button>`;
  };
  const _catGroups = _catOrder.map(c => `
    <div class="stab-cat-group" data-cat="${esc(c)}">
      <div class="stab-cat-hdr">${esc(c)}</div>
      ${_byCat[c].map(_navBtn).join('')}
    </div>`).join('');
  const _sidebar = isEdit ? '' : `<nav class="stab-sidebar" id="sensor-sidebar">
    <div class="stab-search-wrap">
      <input type="text" class="stab-search" id="sensor-type-search"
             placeholder="🔍 Filter types…" autocomplete="off"
             oninput="_sensorTypeFilter(this.value)"
             onkeydown="_sensorTypeKeyNav(event)">
    </div>
    <div class="stab-nav-list" id="sensor-nav-list">${_catGroups}</div>
    <div class="stab-empty" id="sensor-type-empty" style="display:none">No sensor types match</div>
  </nav>`;
  const _contentOpen = isEdit
    ? '<div style="overflow-y:auto;padding:20px;flex:1">'
    : '<div class="stab-content" style="overflow-y:auto;padding:20px">';
  return `
  <input type="hidden" id="as-t" value="${curType}"/>
  ${isEdit ? _contentOpen : `<div class="stab-layout" style="flex:1;min-height:0">${_sidebar}${_contentOpen}`}
  <div class="fr"><label class="fl">Sensor Name</label>
    <input type="text" id="as-n" value="${esc(s?.name||'')}" placeholder="Ping, HTTPS health, sysDescr…" autocomplete="off"/></div>
  <!-- PING -->
  <div class="fg ${curType==='ping'?'vis':''}" id="fg-ping">
    <div class="fr"><label class="fl">Host / IP</label>
      <input type="text" id="as-ph" value="${esc(defHost)}" placeholder="${hostHint}" autocomplete="off"/>
      ${hostStatusHtml}</div>
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
        <input type="checkbox" id="as-vssl" ${s && !s.verify_ssl ? '' : 'checked'} style="width:auto;cursor:pointer"/>
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
      <div class="fr" id="as-snmp-comm-row" style="${(s?.snmp_version||dev?.snmp_version_default||'2c')==='3'?'display:none':''}"><label class="fl">Community String</label>
        <input type="text" id="as-sc" value="${esc(commVal)}" placeholder="${commHint}" autocomplete="off"/>
        ${commStatusHtml}</div>
      <div class="fr"><label class="fl">SNMP Version</label>
        <select id="as-sv" onchange="_asSnmpVerChange()">
          ${(()=>{const dv=s?.snmp_version||dev?.snmp_version_default||'2c';
            return `<option value="2c" ${dv==='2c'?'selected':''}>v2c</option>
          <option value="1"  ${dv==='1'?'selected':''}>v1</option>
          <option value="3"  ${dv==='3'?'selected':''}>v3</option>`;})()}
        </select>
      </div>
    </div>
    ${(() => {
      // SNMPv3 block — hidden unless version=3.  Fields pre-fill from the
      // sensor override (if set) then the device default (falls back at
      // probe time anyway; the pre-fill is just UX so the user sees what
      // will actually be used).
      const v3lvl = s?.snmp_v3_level || dev?.snmp_v3_level_default || 'noAuthNoPriv';
      const v3usr = s?.snmp_v3_user  || dev?.snmp_v3_user_default  || '';
      const v3ap  = s?.snmp_v3_auth_proto || dev?.snmp_v3_auth_proto_default || 'SHA';
      const v3pp  = s?.snmp_v3_priv_proto || dev?.snmp_v3_priv_proto_default || 'AES';
      const v3ctx = s?.snmp_v3_context || dev?.snmp_v3_context_default || '';
      const hasAP = s?.has_snmp_v3_auth_pass || dev?.has_snmp_v3_auth_pass_default;
      const hasPP = s?.has_snmp_v3_priv_pass || dev?.has_snmp_v3_priv_pass_default;
      const showV3 = (s?.snmp_version || dev?.snmp_version_default || '2c') === '3';
      const showAuth = v3lvl === 'authNoPriv' || v3lvl === 'authPriv';
      const showPriv = v3lvl === 'authPriv';
      return `
      <div id="as-v3-block" style="${showV3?'':'display:none;'}border-left:2px solid var(--accent);padding-left:10px;margin:6px 0 0 2px;display:flex;flex-direction:column;gap:8px">
        <div class="fh" style="color:var(--text3);font-size:11px">SNMPv3 credentials (blank fields inherit from device default)</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">v3 Username</label>
            <input type="text" id="as-v3-user" value="${esc(v3usr)}" placeholder="snmpuser" autocomplete="off"/></div>
          <div class="fr"><label class="fl">Security Level</label>
            <select id="as-v3-level" onchange="_asV3LevelChange()">
              <option value="noAuthNoPriv" ${v3lvl==='noAuthNoPriv'?'selected':''}>noAuthNoPriv</option>
              <option value="authNoPriv"   ${v3lvl==='authNoPriv'?'selected':''}>authNoPriv</option>
              <option value="authPriv"     ${v3lvl==='authPriv'?'selected':''}>authPriv</option>
            </select></div>
        </div>
        <div class="fgrid" id="as-v3-auth-row" style="${showAuth?'':'display:none'}">
          <div class="fr"><label class="fl">Auth Protocol</label>
            <select id="as-v3-auth-proto">
              <option value="SHA"     ${v3ap==='SHA'?'selected':''}>SHA</option>
              <option value="MD5"     ${v3ap==='MD5'?'selected':''}>MD5</option>
              <option value="SHA-224" ${v3ap==='SHA-224'?'selected':''}>SHA-224</option>
              <option value="SHA-256" ${v3ap==='SHA-256'?'selected':''}>SHA-256</option>
              <option value="SHA-384" ${v3ap==='SHA-384'?'selected':''}>SHA-384</option>
              <option value="SHA-512" ${v3ap==='SHA-512'?'selected':''}>SHA-512</option>
            </select></div>
          <div class="fr"><label class="fl">Auth Passphrase</label>
            <input type="password" id="as-v3-auth-pass" placeholder="${hasAP?'(unchanged — inherits device default)':'min 8 chars'}" autocomplete="new-password"/></div>
        </div>
        <div class="fgrid" id="as-v3-priv-row" style="${showPriv?'':'display:none'}">
          <div class="fr"><label class="fl">Privacy Protocol</label>
            <select id="as-v3-priv-proto">
              <option value="AES"     ${v3pp==='AES'?'selected':''}>AES</option>
              <option value="DES"     ${v3pp==='DES'?'selected':''}>DES</option>
              <option value="AES-192" ${v3pp==='AES-192'?'selected':''}>AES-192</option>
              <option value="AES-256" ${v3pp==='AES-256'?'selected':''}>AES-256</option>
            </select></div>
          <div class="fr"><label class="fl">Privacy Passphrase</label>
            <input type="password" id="as-v3-priv-pass" placeholder="${hasPP?'(unchanged — inherits device default)':'min 8 chars'}" autocomplete="new-password"/></div>
        </div>
        <div class="fr"><label class="fl">Context (optional)</label>
          <input type="text" id="as-v3-ctx" value="${esc(v3ctx)}" autocomplete="off"/></div>
      </div>`;
    })()}
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
    <div class="fr"><label class="fl">Display as</label>
      <select id="as-snmp-display" onchange="snmpDisplayChange()" style="max-width:320px">
        <option value="">Auto-detect (recommended)</option>
        <optgroup label="Counter (rate)">
          <option value="bytes">Traffic bytes → Mbps</option>
          <option value="errors">Errors counter → err/s</option>
          <option value="packets">Packets counter → pkt/s</option>
        </optgroup>
        <optgroup label="Gauge (numeric)">
          <option value="%">Percent (0-100%)</option>
          <option value="celsius">Temperature °C</option>
          <option value="fahrenheit">Temperature °F</option>
          <option value="dbm">Signal strength dBm</option>
          <option value="count">Count / number</option>
          <option value="rpm">Fan RPM</option>
          <option value="volts">Voltage (V)</option>
          <option value="amps">Current (A)</option>
        </optgroup>
        <optgroup label="Enum (state)">
          <option value="1=up 2=down">Up / Down (1=up 2=down)</option>
          <option value="1=up 2=down 3=testing 4=unknown 5=dormant 6=notPresent 7=lowerLayerDown">ifOperStatus (full IF-MIB enum)</option>
          <option value="1=normal">Normal / other (1=normal)</option>
          <option value="__enum_custom__">Custom enum (type legend below…)</option>
        </optgroup>
        <optgroup label="Other">
          <option value="string">Text / identifier</option>
        </optgroup>
      </select>
      <div class="fh" style="min-height:14px">Controls how History and KPI tiles render.  "Auto-detect" classifies from the probe's SNMP type on first poll.</div>
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
        <input type="checkbox" id="as-kwssl" ${s && !s.verify_ssl ? '' : 'checked'} style="width:auto;cursor:pointer"/>
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
        <input type="checkbox" id="as-vmssl" ${s && !s.verify_ssl ? '' : 'checked'} style="width:auto;cursor:pointer"/>
        Verify SSL certificate
      </label>
    </div>
    <div class="fr" style="margin-top:4px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="dp-btn" type="button" onclick="discoverVMs()" id="as-vm-disc-btn">▥ Discover VMs</button>
        <button class="dp-btn" type="button" onclick="discoverHosts()" id="as-vmh-disc-btn">▦ Discover Hosts</button>
        <button class="dp-btn" type="button" onclick="discoverDatastores()" id="as-vmds-disc-btn">▤ Discover Datastores</button>
        <span id="as-vm-status" style="font-size:11px;color:var(--text3)"></span>
      </div>
      <div id="as-vm-list" style="display:none;margin-top:8px"></div>
    </div>
    <div class="fgrid" style="margin-top:4px">
      <div class="fr"><label class="fl">VM / Host / Datastore ID</label>
        <input type="text" id="as-vmid" value="${esc(s?.vmware_vm_id||'')}" placeholder="vm-123, host-28, or datastore-14 (from discovery)" autocomplete="off" readonly/>
        <input type="hidden" id="as-vmnm" value="${esc(s?.vmware_vm_name||'')}"/>
        <div class="fh">Managed Object ID — use a Discover button above</div>
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
  <!-- SMTP -->
  <div class="fg ${curType==='smtp'?'vis':''}" id="fg-smtp">
    <div class="fgrid">
      <div class="fr"><label class="fl">Mail server host</label>
        <input type="text" id="as-smh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-smp" value="${s?.port||25}" min="1" max="65535"/>
        <div class="fh" style="margin-top:4px">
          <span class="pc" style="cursor:pointer" onclick="document.getElementById('as-smp').value=25;document.getElementById('as-smtls').value='none'">25 plain</span>
          <span class="pc" style="cursor:pointer;margin-left:4px" onclick="document.getElementById('as-smp').value=587;document.getElementById('as-smtls').value='starttls'">587 STARTTLS</span>
          <span class="pc" style="cursor:pointer;margin-left:4px" onclick="document.getElementById('as-smp').value=465;document.getElementById('as-smtls').value='ssl'">465 SSL</span>
        </div>
      </div>
    </div>
    <div class="fgrid">
      <div class="fr"><label class="fl">TLS</label>
        <select id="as-smtls">
          ${['none','starttls','ssl'].map(v=>`<option value="${v}"${(s?.smtp_tls||'none')===v?' selected':''}>${v==='none'?'None (plaintext)':v==='starttls'?'STARTTLS':'SSL/TLS'}</option>`).join('')}
        </select>
      </div>
      <div class="fr"><label class="fl">Test depth</label>
        <select id="as-smlvl" onchange="_smtpLvlToggle()">
          ${[['connect','Connect only'],['ehlo','EHLO'],['starttls','STARTTLS'],['auth','AUTH (LOGIN)'],['mailfrom','MAIL FROM round-trip']]
            .map(([v,lbl])=>`<option value="${v}"${(s?.smtp_test_level||'ehlo')===v?' selected':''}>${lbl}</option>`).join('')}
        </select>
        <div class="fh">Each level runs all prior steps. MAIL FROM issues RSET — no mail is actually sent.</div>
      </div>
    </div>
    <div class="fr" style="margin-top:4px">
      <button class="dp-btn" type="button" onclick="_smtpUseSystem()">Use system SMTP</button>
      <span class="fh" style="margin-left:8px">prefills host / port / TLS / user from alert config</span>
    </div>
    <div id="as-smtp-auth-row" style="display:${['auth','mailfrom'].includes(s?.smtp_test_level||'ehlo')?'':'none'}">
      <div class="fgrid" style="margin-top:8px">
        <div class="fr"><label class="fl">Username</label>
          <input type="text" id="as-smu" value="${esc(s?.smtp_user||'')}" placeholder="user@example.com" autocomplete="off"/></div>
        <div class="fr"><label class="fl">Password</label>
          <input type="password" id="as-smpw" value="" placeholder="${s?.has_smtp_password?'(unchanged — leave blank to keep)':'SMTP password'}" autocomplete="new-password"/></div>
      </div>
    </div>
    <div id="as-smtp-mail-row" style="display:${(s?.smtp_test_level||'ehlo')==='mailfrom'?'':'none'}">
      <div class="fgrid" style="margin-top:8px">
        <div class="fr"><label class="fl">MAIL FROM (sender)</label>
          <input type="text" id="as-smfr" value="${esc(s?.smtp_from||'')}" placeholder="probe@example.com" autocomplete="off"/></div>
        <div class="fr"><label class="fl">RCPT TO (recipient)</label>
          <input type="text" id="as-smrc" value="${esc(s?.smtp_rcpt||'')}" placeholder="target@example.com" autocomplete="off"/></div>
      </div>
    </div>
  </div>
  <!-- SSH -->
  <div class="fg ${curType==='ssh'?'vis':''}" id="fg-ssh">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host / IP</label>
        <input type="text" id="as-shh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-shp" value="${s?.port||22}" min="1" max="65535"/></div>
    </div>
    <div class="fr">
      <label class="fl">Test depth</label>
      <select id="as-shlvl" onchange="_sshLvlToggle()">
        ${[['connect','Connect only (TCP)'],['banner','Banner (verify SSH + capture version)'],['auth','Auth (full login)']]
          .map(([v,lbl])=>`<option value="${v}"${(s?.ssh_test_level||'banner')===v?' selected':''}>${lbl}</option>`).join('')}
      </select>
      <div class="fh">Each level runs all prior steps. Auth closes immediately after handshake — no command is executed.</div>
    </div>
    <div id="as-ssh-auth-row" style="display:${(s?.ssh_test_level||'banner')==='auth'?'':'none'}">
      <div class="fr" style="margin-top:8px">
        <label class="fl">Auth method</label>
        <div style="display:flex;gap:16px;margin-top:4px">
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="radio" name="as-shauth" value="password" ${(s?.ssh_auth_type||'password')==='password'?'checked':''} onchange="_sshLvlToggle()"/> Password
          </label>
          <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
            <input type="radio" name="as-shauth" value="key" ${(s?.ssh_auth_type||'password')==='key'?'checked':''} onchange="_sshLvlToggle()"/> Private key
          </label>
        </div>
      </div>
      <div class="fr" style="margin-top:8px">
        <label class="fl">Username</label>
        <input type="text" id="as-shu" value="${esc(s?.ssh_user||'')}" placeholder="root / admin / monitor" autocomplete="off"/>
      </div>
      <div class="fr" id="as-ssh-pw-row" style="display:${(s?.ssh_auth_type||'password')==='password'?'':'none'};margin-top:8px">
        <label class="fl">Password</label>
        <input type="password" id="as-shpw" value="" placeholder="${s?.has_ssh_password?'(unchanged — leave blank to keep)':'SSH password'}" autocomplete="new-password"/>
      </div>
      <div class="fr" id="as-ssh-key-row" style="display:${(s?.ssh_auth_type||'password')==='key'?'':'none'};margin-top:8px">
        <label class="fl">Private key (PEM)</label>
        <textarea id="as-shkey" rows="6" placeholder="${s?.has_ssh_private_key?'(unchanged — leave blank to keep)':'-----BEGIN OPENSSH PRIVATE KEY-----\\n...\\n-----END OPENSSH PRIVATE KEY-----'}" style="font-family:Consolas,Monaco,monospace;font-size:11px;resize:vertical" autocomplete="off"></textarea>
        <div class="fh">Ed25519 / RSA / ECDSA supported. Passphrase-protected keys not supported in v1.</div>
      </div>
    </div>
  </div>
  <!-- SFTP -->
  <div class="fg ${curType==='sftp'?'vis':''}" id="fg-sftp">
    <div class="fgrid">
      <div class="fr"><label class="fl">Host / IP</label>
        <input type="text" id="as-sfh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-sfp" value="${s?.port||22}" min="1" max="65535"/></div>
    </div>
    <div class="fr">
      <label class="fl">Test depth</label>
      <select id="as-sflvl" onchange="_sftpLvlToggle()">
        ${[['open','Open SFTP subsystem (verify sftp-server is enabled)'],
            ['list','+ List directory'],
            ['stat','+ Stat specific file'],
            ['checksum','+ Download + SHA256 verify (read-only)']]
          .map(([v,lbl])=>`<option value="${v}"${(s?.sftp_test_level||'open')===v?' selected':''}>${lbl}</option>`).join('')}
      </select>
      <div class="fh">Each level runs all prior steps. Checksum is non-destructive — never writes or deletes on the remote.</div>
    </div>
    <div class="fr" style="margin-top:8px">
      <label class="fl">Auth method</label>
      <div style="display:flex;gap:16px;margin-top:4px">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="radio" name="as-sfauth" value="password" ${(s?.sftp_auth_type||'password')==='password'?'checked':''} onchange="_sftpLvlToggle()"/> Password
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="radio" name="as-sfauth" value="key" ${(s?.sftp_auth_type||'password')==='key'?'checked':''} onchange="_sftpLvlToggle()"/> Private key
        </label>
      </div>
    </div>
    <div class="fr" style="margin-top:8px">
      <label class="fl">Username</label>
      <input type="text" id="as-sfu" value="${esc(s?.sftp_user||'')}" placeholder="backup / monitor / nive" autocomplete="off"/>
    </div>
    <div class="fr" id="as-sftp-pw-row" style="display:${(s?.sftp_auth_type||'password')==='password'?'':'none'};margin-top:8px">
      <label class="fl">Password</label>
      <input type="password" id="as-sfpw" value="" placeholder="${s?.has_sftp_password?'(unchanged — leave blank to keep)':'SFTP password'}" autocomplete="new-password"/>
    </div>
    <div class="fr" id="as-sftp-key-row" style="display:${(s?.sftp_auth_type||'password')==='key'?'':'none'};margin-top:8px">
      <label class="fl">Private key (PEM)</label>
      <textarea id="as-sfkey" rows="6" placeholder="${s?.has_sftp_private_key?'(unchanged — leave blank to keep)':'-----BEGIN OPENSSH PRIVATE KEY-----'}" style="font-family:Consolas,Monaco,monospace;font-size:11px;resize:vertical" autocomplete="off"></textarea>
      <div class="fh">Ed25519 / RSA / ECDSA supported. Passphrase-protected keys not supported.</div>
    </div>
    <div class="fr" id="as-sftp-path-row" style="display:${['list','stat','checksum'].includes(s?.sftp_test_level||'open')?'':'none'};margin-top:8px">
      <label class="fl">Remote path</label>
      <input type="text" id="as-sfpath" value="${esc(s?.sftp_remote_path||'')}" placeholder="/backups  or  /backups/latest.tar.gz" autocomplete="off"/>
      <div class="fh">Directory for <b>list</b>, file for <b>stat</b> / <b>checksum</b>.</div>
    </div>
    <div class="fr" id="as-sftp-sha-row" style="display:${(s?.sftp_test_level||'open')==='checksum'?'':'none'};margin-top:8px">
      <label class="fl">Expected SHA256</label>
      <input type="text" id="as-sfsha" value="${esc(s?.sftp_expected_sha256||'')}" placeholder="a1b2c3… (64 hex chars)" autocomplete="off" style="font-family:Consolas,Monaco,monospace;font-size:11px"/>
      <div class="fh">Compute locally with <code>sha256sum</code>. Max file size: 10 MB.</div>
    </div>
  </div>
  <!-- RADIUS -->
  <div class="fg ${curType==='radius'?'vis':''}" id="fg-radius">
    <div class="fgrid">
      <div class="fr"><label class="fl">RADIUS server</label>
        <input type="text" id="as-rdh" value="${esc(s?.host||defHost)}" placeholder="${hostHint}" autocomplete="off"/>
        ${hostStatusHtml}</div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="as-rdp" value="${s?.port||1812}" min="1" max="65535"/>
        <div class="fh">1812 = RFC 2865 authentication (default).</div>
      </div>
    </div>
    <div class="fr" style="margin-top:8px">
      <label class="fl">Shared secret</label>
      <input type="password" id="as-rdsec" value="" placeholder="${s?.has_radius_secret?'(unchanged — leave blank to keep)':'RADIUS shared secret'}" autocomplete="new-password"/>
      <div class="fh">The client↔server secret — not a user password. Fernet-encrypted at rest.</div>
    </div>
    <div class="fr" style="margin-top:8px">
      <label class="fl">Test depth</label>
      <select id="as-rdlvl" onchange="_radiusLvlToggle()">
        ${[['reachable','Reachable (random user, any reply = up — no real creds needed)'],
            ['auth','+ Full auth (real username + password, expect Access-Accept)']]
          .map(([v,lbl])=>`<option value="${v}"${(s?.radius_test_level||'reachable')===v?' selected':''}>${lbl}</option>`).join('')}
      </select>
      <div class="fh">PAP only. 2FA challenges are flagged as failures (non-interactive probe).</div>
    </div>
    <div class="fr" id="as-rd-user-row" style="display:${(s?.radius_test_level||'reachable')==='auth'?'':'none'};margin-top:8px">
      <label class="fl">Username</label>
      <input type="text" id="as-rdu" value="${esc(s?.radius_username||'')}" placeholder="test user" autocomplete="off"/>
    </div>
    <div class="fr" id="as-rd-pw-row" style="display:${(s?.radius_test_level||'reachable')==='auth'?'':'none'};margin-top:8px">
      <label class="fl">Password</label>
      <input type="password" id="as-rdpw" value="" placeholder="${s?.has_radius_password?'(unchanged — leave blank to keep)':'RADIUS user password'}" autocomplete="new-password"/>
    </div>
    <div class="fr" style="margin-top:8px">
      <label class="fl">NAS-Identifier</label>
      <input type="text" id="as-rdnas" value="${esc(s?.radius_nas_id||'')}" placeholder="pingwatch" autocomplete="off"/>
      <div class="fh">Optional — some servers filter or log by this attribute. Defaults to <code>pingwatch</code>.</div>
    </div>
  </div>
  <!-- Alert Thresholds -->
  <div class="snr-section">
    <div class="snr-section-lbl">Alert Thresholds</div>
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
        const _noThr=curType==='vmware'&&['uptime','on','disk_read','disk_write','disk_usage'].includes(_vmm);
        return`<div class="fgrid">
        <div class="fr" id="as-wms-row"${_noThr?' style="display:none"':''}><label class="fl" id="as-wms-lbl">${_wLbl}</label>
          <input type="number" id="as-wms" value="${curType==='vmware'?(s?.warn_ms||''):(s?.warn_ms||(window._snrTypeDefaults?.[curType]?.warn_ms||_SDR_WARN_DEF[curType]||''))}" placeholder="${_ph}" min="1"/>
        </div>
        <div class="fr" id="as-cms-row"${_noThr?' style="display:none"':''}><label class="fl" id="as-cms-lbl">${_cLbl}</label>
          <input type="number" id="as-cms" value="${curType==='vmware'?(s?.crit_ms||''):(s?.crit_ms||(window._snrTypeDefaults?.[curType]?.crit_ms||_SDR_CRIT_DEF[curType]||''))}" placeholder="${_phc}" min="1"/>
          ${_cur}
        </div></div>`;
      })()}
      <div class="fgrid">
        <div class="fr"><label class="fl">Warn Loss %</label>
          <input type="number" id="as-lwp" value="${s?.loss_warn_pct||0}" min="0" max="100"/>
        </div>
        <div class="fr"><label class="fl">Crit Loss %</label>
          <input type="number" id="as-lcp" value="${s?.loss_crit_pct||0}" min="0" max="100"/>
        </div>
      </div>
      <div class="fr" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
          <input type="checkbox" id="as-am" ${s?.alerts_muted?'checked':''}>
          <span class="fl" style="margin:0">🔕 Mute alerts for this sensor</span>
        </label>
        <div style="font-size:11px;color:var(--text3);margin-top:3px;margin-left:24px">Probing continues — no DOWN / recovery / threshold events are fired.</div>
      </div>
      ${isEdit ? `
      <div class="fr" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
        <label class="fl" style="margin:0">📋 Alert Profile</label>
        <div id="as-profile-body" data-did="${s.device_id}" data-sid="${s.sensor_id}"
             style="font-size:12px;color:var(--text3);margin-top:6px;padding:8px;background:var(--bg2);border:1px solid var(--border);border-radius:4px">
          Loading…
        </div>
      </div>` : ''}
      ${isEdit && ['ping','tcp','http','dns','http_keyword','banner'].includes(curType) ? (() => {
        const _en = !!s?.anomaly_enabled;
        const _sn = parseInt(s?.anomaly_sensitivity||2);
        const _ms = parseInt(s?.anomaly_min_samples||50);
        const _mean = (s?.anomaly_mean_ms != null) ? Number(s.anomaly_mean_ms).toFixed(1) : null;
        const _std  = (s?.anomaly_stddev_ms != null) ? Number(s.anomaly_stddev_ms).toFixed(1) : null;
        const _cnt  = parseInt(s?.anomaly_sample_count||0);
        const _baseLine = (_mean !== null && _cnt > 0)
          ? `<div class="fh" style="margin-top:6px">Baseline: <strong>${_mean} ms ± ${_std} ms</strong> — learned from ${_cnt} samples</div>`
          : `<div class="fh" style="margin-top:6px">Baseline: <em>not yet learned</em></div>`;
        return `
        <div class="fr" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">
          <details ${_en?'open':''}>
            <summary style="cursor:pointer;user-select:none;font-weight:500;padding:6px 0">
              🧠 Anomaly Detection <span style="color:var(--text3);font-weight:400">(opt-in)</span>
            </summary>
            <div style="padding:10px;margin-top:6px;background:var(--bg2);border:1px solid var(--border);border-radius:4px">
              <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
                <input type="checkbox" id="as-anom-en" ${_en?'checked':''}>
                <span class="fl" style="margin:0">Enable learned-baseline detection</span>
              </label>
              <div class="fh" style="margin-left:24px;margin-top:3px">Fires a warning when current latency deviates significantly from the learned normal range. Static thresholds continue to work independently.</div>
              <div class="fgrid" style="margin-top:10px">
                <div class="fr"><label class="fl">Sensitivity</label>
                  <select id="as-anom-sens" style="max-width:200px">
                    <option value="1" ${_sn===1?'selected':''}>Strict — more alerts</option>
                    <option value="2" ${_sn===2?'selected':''}>Balanced (default)</option>
                    <option value="3" ${_sn===3?'selected':''}>Relaxed — quieter</option>
                  </select>
                </div>
                <div class="fr"><label class="fl">Min samples</label>
                  <input type="number" id="as-anom-min" value="${_ms}" min="5" max="10000" style="max-width:110px"/>
                  <div class="fh" style="margin-top:3px">Bootstrap guard (default 50)</div>
                </div>
              </div>
              ${_baseLine}
              ${_cnt > 0 ? `<button type="button" class="btn-s" style="margin-top:8px;font-size:12px;padding:5px 12px" onclick="_anomResetBaseline('${esc(s.device_id)}','${esc(s.sensor_id)}')">Reset baseline</button>` : ''}
            </div>
          </details>
        </div>`;
      })() : ''}
  </div>
  <!-- Probe Timing -->
  <div class="snr-section">
    <div class="snr-section-lbl">Probe Timing</div>
    <div class="fgrid">
      <div class="fr"><label class="fl">Interval (s)</label>
        <input type="number" id="as-iv" value="${s?.interval||(window._snrDef?.interval||5)}" min="1" max="300"/></div>
      <div class="fr"><label class="fl">Timeout (s)</label>
        <input type="number" id="as-tmo" value="${s?.timeout||(window._snrDef?.timeout||4)}" min="1" max="60"/></div>
    </div>
  </div>
  ${isEdit ? '' : `<!-- Start Immediately -->
  <div class="fr" style="margin-top:8px"><label class="fl">Start Immediately</label>
    <select id="as-si"><option value="1">Yes — start now</option><option value="0">No — manual</option></select>
  </div>`}
  </div>${isEdit ? '' : '</div>'}`;
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
  <div class="mbox mbox-sensor">
    <div class="mhd">
      <div class="mttl">Add Sensor — <span style="color:var(--text2)">${esc(dev.name)}</span></div>
      <button class="mclose" onclick="closeM('mas')">✕</button>
    </div>
    <div class="mbdy" style="padding:0;flex:1;min-height:0">
      ${sensorFormHTML(dev)}
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
  const _tLabels={ping:'Ping',tcp:'TCP Port',http:'HTTP/S',snmp:'SNMP',dns:'DNS',tls:'TLS',http_keyword:'HTTP KW',banner:'Banner',vmware:'VMware'};
  o.innerHTML=`
  <div class="mbox mbox-sensor-edit">
    <div class="mhd">
      <div class="mttl">Edit Sensor — <span style="color:var(--text2)">${esc(s.name)}</span>
        <span class="sensor-type-badge">${sIco(s.stype)} ${_tLabels[s.stype]||s.stype}</span>
      </div>
      <button class="mclose" onclick="closeM('mes')">✕</button>
    </div>
    <div class="mbdy" style="flex:1;min-height:0;padding:0">
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
    _loadSensorProfileSection(did, sid);
  },50);
}

// ── Alert profile section inside the sensor edit modal ────────────
async function _loadSensorProfileSection(did, sid){
  const body = document.getElementById('as-profile-body');
  if(!body) return;
  const dev = S.devices[did];
  const groupName = dev?.group || 'Default Group';
  const scopeKey = `${did}/${sid}`;
  try{
    const r = await api('GET','/api/alert/profiles');
    const all = r.profiles || [];
    const sensorProf = all.find(p => p.scope_type==='sensor' && p.scope_value===scopeKey);
    const deviceProf = all.find(p => p.scope_type==='device' && p.scope_value===did);
    const groupProf  = all.find(p => p.scope_type==='group'  && p.scope_value===groupName);
    const globalProf = all.find(p => p.scope_type==='global');

    let resolved, fromLabel;
    if(sensorProf){ resolved=sensorProf; fromLabel='this sensor (override)'; }
    else if(deviceProf){ resolved=deviceProf; fromLabel='device override'; }
    else if(groupProf){ resolved=groupProf; fromLabel=`group "${esc(groupName)}"`; }
    else if(globalProf){ resolved=globalProf; fromLabel='global default'; }
    else { resolved=null; fromLabel='— no profile resolved'; }

    let html = '';
    if(sensorProf){
      html += `<div style="margin-bottom:8px">
        <span class="alrt-override-badge">Override</span>
        <span style="margin-left:8px;color:var(--text)">${esc(sensorProf.name)}</span>
        <span style="margin-left:6px;color:var(--text3);font-size:11px">(${sensorProf.stages.length} stage${sensorProf.stages.length===1?'':'s'})</span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button type="button" class="btn-s" onclick="_sensorProfileEdit(${sensorProf.id})">Edit profile…</button>
        <button type="button" class="btn-s" onclick="_sensorProfileReset('${did}','${sid}',${sensorProf.id})">Reset to inherited</button>
      </div>`;
    } else {
      html += `<div style="margin-bottom:8px">
        <span class="alrt-inherit-badge">Inherited</span>
        <span style="margin-left:8px">from ${fromLabel}${resolved?` — <strong style="color:var(--text)">${esc(resolved.name)}</strong>`:''}</span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button type="button" class="btn-s" onclick="_sensorProfileOverride('${did}','${sid}')">Override at sensor level</button>
        ${resolved?`<button type="button" class="btn-s" onclick="_sensorProfileEdit(${resolved.id})">View ${esc(fromLabel)}</button>`:''}
      </div>`;
    }
    body.innerHTML = html;
  }catch(e){
    body.innerHTML = `<span style="color:var(--down)">Failed to load profile</span>`;
  }
}

function _sensorProfileEdit(profileId){
  closeM('mes');
  if(typeof openProfileEditor==='function') openProfileEditor(profileId);
  else toast('Open the Alerting page to edit profiles','err');
}

function _sensorProfileOverride(did, sid){
  closeM('mes');
  if(typeof openProfileEditor==='function')
    openProfileEditor(null,{scope_type:'sensor',scope_value:`${did}/${sid}`});
  else toast('Open the Alerting page to create profiles','err');
}

async function _sensorProfileReset(did, sid, profileId){
  if(!confirm('Delete the sensor-scoped alert profile?\nThis sensor will fall back to the device/group/global profile.')) return;
  try{
    await api('DELETE','/api/alert/profile/'+profileId);
    toast('Sensor profile reset','ok');
    _loadSensorProfileSection(did, sid);
  }catch(e){
    toast('Reset failed','err');
  }
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
  document.querySelectorAll('#sensor-sidebar .stab-nav').forEach(b=>b.classList.toggle('active',b.dataset.t===t));
  ['ping','tcp','http','snmp','dns','tls','http_keyword','banner','vmware','smtp','ssh','sftp','radius'].forEach(x=>document.getElementById(`fg-${x}`)?.classList.toggle('vis',x===t));
  if(t==='snmp') _snmpLoadVendors();
  if(t==='vmware') _vmwareLoadMetrics();
  if(window._snrAddMode) _applyTypeDefaults(t);
  // Update threshold labels to match selected sensor type
  const _wL=document.getElementById('as-wms-lbl'), _cL=document.getElementById('as-cms-lbl');
  if(_wL&&_cL){
    if(t==='tls'){_wL.textContent='Warn Days (cert expiry)';_cL.textContent='Crit Days (cert expiry)';}
    else if(t==='snmp'){const _su=document.getElementById('as-snmp-unit')?.value||'';_wL.textContent=_snmpThrLabel(_su,true)||'Warn Value';_cL.textContent=_snmpThrLabel(_su,false)||'Crit Value';}
    else if(t==='vmware'){const _vm=document.getElementById('as-vmmet-v')?.value||'';_wL.textContent=_vmwareThrLabel(_vm,true);_cL.textContent=_vmwareThrLabel(_vm,false);}
    else{_wL.textContent='Warn Latency (ms)';_cL.textContent='Crit Latency (ms)';}
  }
}

/* Filter the sensor-type sidebar by a free-text query. Hides any nav button
 * whose data-search packed-haystack doesn't contain the query, and collapses
 * category headers that have no remaining visible buttons. Cheap — pure DOM
 * walk, no re-render. */
function _sensorTypeFilter(q){
  const list = document.getElementById('sensor-nav-list');
  if(!list) return;
  const needle = (q || '').trim().toLowerCase();
  let totalVisible = 0;
  list.querySelectorAll('.stab-cat-group').forEach(g => {
    let groupVisible = 0;
    g.querySelectorAll('.stab-nav').forEach(b => {
      const match = !needle || (b.dataset.search || '').includes(needle);
      b.style.display = match ? '' : 'none';
      if(match) groupVisible++;
    });
    g.style.display = groupVisible ? '' : 'none';
    totalVisible += groupVisible;
  });
  const empty = document.getElementById('sensor-type-empty');
  if(empty) empty.style.display = totalVisible ? 'none' : '';
}

/* Keyboard navigation while the search input is focused.
 *  ↑ / ↓  — move between visible (filtered) types and auto-select on the way
 *  Enter  — commit the current selection (already selected by ↑/↓; Enter just
 *           blurs the search so the user can tab into the form fields)
 * Auto-selecting on arrow keys means the right-hand form panel updates live
 * as the user scrubs through types — no extra Enter press needed. */
function _sensorTypeKeyNav(e){
  if(!['ArrowUp','ArrowDown','Enter'].includes(e.key)) return;
  const visible = [...document.querySelectorAll('#sensor-nav-list .stab-nav')]
    .filter(b => b.style.display !== 'none');
  if(!visible.length) return;
  if(e.key === 'Enter'){
    e.preventDefault();
    e.target.blur();
    return;
  }
  let idx = visible.findIndex(b => b.classList.contains('active'));
  if(idx < 0) idx = 0;
  if(e.key === 'ArrowDown') idx = Math.min(idx + 1, visible.length - 1);
  if(e.key === 'ArrowUp')   idx = Math.max(idx - 1, 0);
  const t = visible[idx].dataset.t;
  if(t) selType(t);
  visible[idx].scrollIntoView({block:'nearest'});
  e.preventDefault();
}

function _applyTypeDefaults(t){
  const d = window._snrTypeDefaults?.[t] || {};
  const _sv = (id,v) => { if(v==null) return; const e=document.getElementById(id); if(e) e.value=v; };
  const _sc = (id,v) => { if(v==null) return; const e=document.getElementById(id); if(e) e.checked=!!v; };
  _sv('as-iv',  d.interval);
  _sv('as-tmo', d.timeout);
  if(t === 'vmware'){
    // Clear generic defaults — metric-specific auto-fill sets correct values
    const _wi=document.getElementById('as-wms');
    const _ci=document.getElementById('as-cms');
    if(_wi) _wi.value='';
    if(_ci) _ci.value='';
  } else {
    _sv('as-wms', d.warn_ms  ?? _SDR_WARN_DEF[t]);
    _sv('as-cms', d.crit_ms  ?? _SDR_CRIT_DEF[t]);
  }
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
  // v0.9.7: initialize the "Display as" dropdown from the sensor's saved unit.
  const savedUnit=document.getElementById('as-snmp-unit')?.value||'';
  _syncSnmpDisplay(savedUnit);
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
        _syncSnmpDisplay(o.unit||'');
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
    _syncSnmpDisplay(u);
  }
}

// v0.9.7: keep the "Display as" dropdown in sync with programmatic unit
// changes (catalog pick, discover interfaces).  If the unit doesn't match
// a predefined option, the dropdown shows "Auto-detect".
function _syncSnmpDisplay(unit) {
  const sel = document.getElementById('as-snmp-display');
  if (!sel) return;
  const opts = Array.from(sel.options).map(o => o.value);
  sel.value = opts.includes(unit) ? unit : '';
}

// v0.9.7: SNMPv3 block visibility on the Add/Edit Sensor form. Hide community when v3 is selected.
function _asSnmpVerChange(){
  const ver = document.getElementById('as-sv')?.value || '';
  const blk = document.getElementById('as-v3-block');
  const comm = document.getElementById('as-snmp-comm-row');
  if(blk) blk.style.display = (ver === '3') ? 'flex' : 'none';
  if(comm) comm.style.display = (ver === '3') ? 'none' : '';
  if(ver === '3') _asV3LevelChange();
}
function _asV3LevelChange(){
  const lvl = document.getElementById('as-v3-level')?.value || 'noAuthNoPriv';
  const ar  = document.getElementById('as-v3-auth-row');
  const pr  = document.getElementById('as-v3-priv-row');
  if(ar) ar.style.display = (lvl === 'authNoPriv' || lvl === 'authPriv') ? '' : 'none';
  if(pr) pr.style.display = (lvl === 'authPriv') ? '' : 'none';
}

function snmpDisplayChange() {
  const sel  = document.getElementById('as-snmp-display');
  const sunit = document.getElementById('as-snmp-unit');
  const hint  = document.getElementById('as-oid-unit2');
  if (!sel || !sunit) return;
  let v = sel.value;
  if (v === '__enum_custom__') {
    const legend = prompt(
      'Enter enum legend in the format "1=state1 2=state2 …"\n\nExamples:\n  1=up 2=down\n  1=normal 2=warning 3=critical',
      sunit.value || '1=ok 2=fail');
    if (legend && /\d+\s*=\s*\w+/.test(legend)) {
      v = legend.trim();
    } else {
      sel.value = sunit.value || '';
      return;
    }
  }
  sunit.value = v;
  if (hint) hint.innerHTML = v ? '<b>Unit: ' + esc(v) + '</b>' : 'Type or paste an OID, choose from picker above, or use Discover Interfaces.';
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
  const discoveryBody = {host, community, port, version, did};
  // SNMPv3 discovery — read creds directly from the visible form fields so
  // users can try v3 before saving a device default.  Blank passphrases OK;
  // backend falls back to the device default / errors with a clear message.
  if(version === '3'){
    discoveryBody.snmp_v3_user       = (document.getElementById('as-v3-user')?.value || '').trim();
    discoveryBody.snmp_v3_level      = document.getElementById('as-v3-level')?.value || 'noAuthNoPriv';
    discoveryBody.snmp_v3_auth_proto = document.getElementById('as-v3-auth-proto')?.value || '';
    discoveryBody.snmp_v3_priv_proto = document.getElementById('as-v3-priv-proto')?.value || '';
    discoveryBody.snmp_v3_context    = (document.getElementById('as-v3-ctx')?.value || '').trim();
    const _ap = document.getElementById('as-v3-auth-pass')?.value || '';
    const _pp = document.getElementById('as-v3-priv-pass')?.value || '';
    if(_ap) discoveryBody.snmp_v3_auth_pass = _ap;
    if(_pp) discoveryBody.snmp_v3_priv_pass = _pp;
  }
  let r;
  try{
    r=await api('POST','/api/snmp/interfaces', discoveryBody);
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

  const metricCheckboxes=
    `<label class="vm-met-item vm-met-all-item" style="border-bottom:1px solid var(--border);margin-bottom:2px;padding-bottom:5px"><input type="checkbox" class="iface-met-all-cb" onchange="ifaceMetSelectAll(this)"> <strong>All metrics</strong></label>`+
    METRICS.map(m=>`<label class="vm-met-item"><input type="checkbox" value="${m.v}" onchange="ifaceMetChanged(this)"> ${esc(m.l)}</label>`).join('');

  let html='<div style="border:1px solid var(--border);border-radius:6px;margin-top:4px;overflow:visible">';
  html+='<div style="padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<span style="font-size:11px;color:var(--text3);white-space:nowrap">Set for checked:</span>';
  html+=`<div class="vm-met-wrap" style="flex-shrink:0"><button class="vm-met-btn" type="button" onclick="toggleIfaceMetPicker(this)">— bulk metrics —</button><div class="vm-met-drop" style="display:none">${metricCheckboxes}</div></div>`;
  html+='</div>';
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
    const metCheckboxes=
      `<label class="vm-met-item vm-met-all-item" style="border-bottom:1px solid var(--border);margin-bottom:2px;padding-bottom:5px"><input type="checkbox" class="iface-row-met-all-cb" onchange="ifaceRowMetSelectAll(this)"> <strong>All metrics</strong></label>`+
      METRICS.map(m=>`<label class="vm-met-item"><input type="checkbox" value="${m.v}" onchange="ifaceRowMetChanged(this)"> ${esc(m.l)}</label>`).join('');
    html+=`<tr style="border-top:1px solid var(--border);${rowBg}">`;
    html+=`<td style="padding:4px 8px;text-align:center"><input type="checkbox" class="as-iface-cb" data-idx="${iface.index}" data-name="${esc(iface.name||iface.descr)}" onchange="updateIfaceSelCount()"/></td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3)">${iface.index}</td>`;
    html+=`<td style="padding:4px 8px;font-weight:500;white-space:nowrap">${displayName}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text2)">${displayDescr}</td>`;
    html+=`<td style="padding:4px 8px;color:${stClr};white-space:nowrap">${iface.status}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);white-space:nowrap">${esc(iface.speed)}</td>`;
    html+=`<td style="padding:4px 8px"><div class="vm-met-wrap" data-idx="${iface.index}"><button class="vm-met-btn" type="button" onclick="toggleIfaceRowMetPicker(this)">— pick metrics —</button><div class="vm-met-drop" style="display:none">${metCheckboxes}</div></div></td>`;
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
  let totalSensors=0;
  checked.forEach(cb=>{
    const idx=cb.dataset.idx;
    const wrap=document.querySelector(`.vm-met-wrap[data-idx="${idx}"]`);
    if(wrap){
      const metCbs=[...wrap.querySelectorAll('.vm-met-drop input[value]:checked')];
      totalSensors+=metCbs.length;
    }
  });
  const el=document.getElementById('as-iface-sel-count');
  if(el) el.textContent=n?`${n} of ${cbs.length} selected${totalSensors?` · ${totalSensors} sensor${totalSensors>1?'s':''}`:''}`:'0 selected';
  const all=document.getElementById('as-iface-all');
  if(all){all.indeterminate=(n>0&&n<cbs.length);all.checked=(cbs.length>0&&n===cbs.length);}
  const addBtn=document.getElementById('as-iface-add-btn');
  if(addBtn) addBtn.textContent=(n===1&&totalSensors===1)?'Apply to Form':'Add Selected as Sensors';
  // When exactly 1 interface+1 metric is selected, sync the OID and snmp_unit fields
  const oidEl=document.getElementById('as-oid');
  if(oidEl && n===1 && totalSensors===1){
    const cb=checked[0];
    const idx=parseInt(cb.dataset.idx);
    const wrap=document.querySelector(`.vm-met-wrap[data-idx="${idx}"]`);
    const metCbs=[...wrap.querySelectorAll('.vm-met-drop input[value]:checked')];
    if(metCbs.length===1){
      const metric=(window._ifaceMetrics||[]).find(m=>m.v===metCbs[0].value);
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
    const wrap=document.querySelector(`.vm-met-wrap[data-idx="${idx}"]`);
    const metCbs=[...wrap.querySelectorAll('.vm-met-drop input[value]:checked')];
    if(!metCbs.length){toast('Choose a metric for the selected interface','err');return;}
    if(metCbs.length>1){toast('Select exactly one metric to apply to the form','err');return;}
    const metric=(window._ifaceMetrics||[]).find(m=>m.v===metCbs[0].value);
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
  const warn_ms=parseInt(document.getElementById('as-wms')?.value)||null;
  const crit_ms=parseInt(document.getElementById('as-cms')?.value)||null;
  const start=document.getElementById('as-si')?.value==='1';
  const rows=[];let noMetric=0;
  checked.forEach(cb=>{
    const idx=cb.dataset.idx;
    const name=cb.dataset.name||('IF'+idx);
    const wrap=document.querySelector(`.vm-met-wrap[data-idx="${idx}"]`);
    const metCbs=[...wrap.querySelectorAll('.vm-met-drop input[value]:checked')];
    if(!metCbs.length){noMetric++;return;}
    metCbs.forEach(metCb=>{
      const metric=(window._ifaceMetrics||[]).find(m=>m.v===metCb.value);
      if(metric) rows.push({idx:parseInt(idx),name,metric});
    });
  });
  if(noMetric) toast(`${noMetric} interface${noMetric>1?'s':''} skipped — no metrics chosen`,'info');
  if(!rows.length){toast('Choose at least one metric for each checked interface','err');return;}
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
      warn_ms,crit_ms,
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

// ── SNMP Interface bulk metrics helpers ──────────────────────────────────

let _ifaceMetPickerOpen=null;
function _closeIfaceMetPicker(){
  if(!_ifaceMetPickerOpen) return;
  const drop=_ifaceMetPickerOpen;
  drop.style.display='none';
  if(drop._ownerWrap) drop._ownerWrap.appendChild(drop);
  _ifaceMetPickerOpen=null;
}
function toggleIfaceMetPicker(btn){
  const drop=btn.nextElementSibling;
  const isOpen=_ifaceMetPickerOpen===drop;
  if(_ifaceMetPickerOpen&&_ifaceMetPickerOpen!==drop) _closeIfaceMetPicker();
  if(isOpen){
    _closeIfaceMetPicker();
  } else {
    const wrap=btn.closest('.vm-met-wrap');
    drop._ownerWrap=wrap;
    document.body.appendChild(drop);
    const r=btn.getBoundingClientRect();
    const dropW=240;
    const left=Math.max(4, r.right-dropW);
    drop.style.position='fixed';
    drop.style.top=(r.bottom+3)+'px';
    drop.style.left=left+'px';
    drop.style.right='auto';
    drop.style.zIndex='9999';
    drop.style.display='block';
    _ifaceMetPickerOpen=drop;
  }
}
document.addEventListener('click',e=>{
  if(_ifaceMetPickerOpen&&!e.target.closest('.vm-met-wrap')&&!e.target.closest('.vm-met-drop')){
    _closeIfaceMetPicker();
  }
});
function ifaceMetSelectAll(allCb){
  const drop=allCb.closest('.vm-met-drop');
  if(!drop) return;
  drop.querySelectorAll('input[value]').forEach(c=>c.checked=allCb.checked);
  ifaceMetChanged(allCb);
}
function ifaceMetChanged(cb){
  const drop=cb.closest('.vm-met-drop');
  const wrap=drop?._ownerWrap||drop?.parentElement;
  if(!wrap) return;
  const allCb=drop.querySelector('.iface-met-all-cb');
  if(allCb&&cb!==allCb){
    const metCbs=[...drop.querySelectorAll('input[value]')];
    const nChecked=metCbs.filter(c=>c.checked).length;
    allCb.checked=nChecked===metCbs.length;
    allCb.indeterminate=nChecked>0&&nChecked<metCbs.length;
  }
  const checked=[...drop.querySelectorAll('input[value]:checked')];
  const btn=wrap.querySelector('.vm-met-btn');
  if(btn){
    if(!checked.length) btn.textContent='— bulk metrics —';
    else if(checked.length===1) btn.textContent=checked[0].parentElement.textContent.trim();
    else btn.textContent=`${checked.length} metrics`;
  }
  // If this is the bulk picker (in header, no data-idx), apply to all checked rows
  if(!wrap.dataset.idx){
    const checkedIdxs=[...document.querySelectorAll('.as-iface-cb:checked')].map(c=>c.dataset.idx);
    checkedIdxs.forEach(idx=>{
      const rowWrap=document.querySelector(`.vm-met-wrap[data-idx="${idx}"]`);
      if(!rowWrap) return;
      rowWrap.querySelectorAll('.vm-met-drop input').forEach(rowCb=>{
        rowCb.checked=!!drop.querySelector(`input[value="${rowCb.value}"]:checked`);
      });
      const rowChecked=[...rowWrap.querySelectorAll('.vm-met-drop input[value]:checked')];
      const rowBtn=rowWrap.querySelector('.vm-met-btn');
      if(rowBtn){
        if(!rowChecked.length) rowBtn.textContent='— pick metrics —';
        else if(rowChecked.length===1) rowBtn.textContent=rowChecked[0].parentElement.textContent.trim();
        else rowBtn.textContent=`${rowChecked.length} metrics`;
      }
      const rowAllCb=rowWrap.querySelector('.iface-row-met-all-cb');
      const rowAllMetCbs=[...rowWrap.querySelectorAll('.vm-met-drop input[value]')];
      if(rowAllCb){rowAllCb.checked=rowChecked.length===rowAllMetCbs.length;rowAllCb.indeterminate=rowChecked.length>0&&rowChecked.length<rowAllMetCbs.length;}
    });
    updateIfaceSelCount();
  }
}
function toggleIfaceRowMetPicker(btn){
  const drop=btn.nextElementSibling;
  const isOpen=_ifaceRowMetPickerOpen===drop;
  if(_ifaceRowMetPickerOpen&&_ifaceRowMetPickerOpen!==drop) _closeIfaceRowMetPicker();
  if(isOpen){
    _closeIfaceRowMetPicker();
  } else {
    const wrap=btn.closest('.vm-met-wrap');
    drop._ownerWrap=wrap;
    document.body.appendChild(drop);
    const r=btn.getBoundingClientRect();
    const dropW=240;
    const left=Math.max(4, r.right-dropW);
    drop.style.position='fixed';
    drop.style.top=(r.bottom+3)+'px';
    drop.style.left=left+'px';
    drop.style.right='auto';
    drop.style.zIndex='9999';
    drop.style.display='block';
    _ifaceRowMetPickerOpen=drop;
  }
}
function _closeIfaceRowMetPicker(){
  if(!_ifaceRowMetPickerOpen) return;
  const drop=_ifaceRowMetPickerOpen;
  drop.style.display='none';
  if(drop._ownerWrap) drop._ownerWrap.appendChild(drop);
  _ifaceRowMetPickerOpen=null;
}
let _ifaceRowMetPickerOpen=null;
document.addEventListener('click',e=>{
  if(_ifaceRowMetPickerOpen&&!e.target.closest('.vm-met-wrap')&&!e.target.closest('.vm-met-drop')){
    _closeIfaceRowMetPicker();
  }
});
function ifaceRowMetSelectAll(allCb){
  const drop=allCb.closest('.vm-met-drop');
  if(!drop) return;
  drop.querySelectorAll('input[value]').forEach(c=>c.checked=allCb.checked);
  ifaceRowMetChanged(allCb);
}
function ifaceRowMetChanged(cb){
  const drop=cb.closest('.vm-met-drop');
  const wrap=drop?._ownerWrap||drop?.parentElement;
  if(!wrap) return;
  const allCb=drop.querySelector('.iface-row-met-all-cb');
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
  updateIfaceSelCount();
}

// ── VMware VM Discovery ──────────────────────────────────────────────────

let _vmwareMetrics=null;
let _vmwareDatastoreMetrics=null;
let _vmSelectedMemMB=0;  // memory of currently selected VM (MB), for smart threshold defaults
let _vmSelectedCapacityGB=0;  // capacity of currently selected datastore (GB), for smart threshold defaults
let _vmDstoreMode=false;  // true when the metric dropdown is showing datastore metrics

// Fixed per-metric defaults (used when VM RAM not available or metric isn't memory-based)
const _VM_THR_DEFAULTS={
  cpu_usage:       {w:80,  c:95},
  cpu_ready:       {w:10,  c:20},
  disk_used_pct:   {w:80,  c:90},
  ds_read_lat:     {w:20,  c:50},
  ds_write_lat:    {w:20,  c:50},
  // Host metrics
  host_cpu_usage:      {w:80, c:95},
  host_cpu_ready:      {w:10, c:20},
  host_mem_usage_pct:  {w:80, c:95},
  host_ds_read_lat:    {w:20, c:50},
  host_ds_write_lat:   {w:20, c:50},
  host_disk_dev_lat:   {w:20, c:50},
  host_disk_kern_lat:  {w:10, c:30},
};

// Auto-fill warn/crit thresholds based on VM specs and metric type.
// Only fills if both fields are currently empty (never overwrites user input).
function _vmwareThrAutoFill(metric, memMB){
  const wi=document.getElementById('as-wms');
  const ci=document.getElementById('as-cms');
  if(!wi||!ci||wi.value||ci.value) return;
  let w=null,c=null;
  // Datastore free-space: defaults scale with discovered capacity
  if(metric && metric.startsWith('dstore_') && _vmSelectedCapacityGB>0){
    w=Math.round(_vmSelectedCapacityGB*0.20);
    c=Math.round(_vmSelectedCapacityGB*0.10);
  }
  // Memory metrics: compute from VM/host RAM
  else if(memMB>0){
    if(metric==='mem_consumed'||metric==='host_mem_consumed'){ w=Math.round(memMB*0.80); c=Math.round(memMB*0.90); }
    else if(metric==='mem_active'||metric==='host_mem_active'){ w=Math.round(memMB*0.50); c=Math.round(memMB*0.70); }
  }
  // All other metrics: use fixed defaults
  if(w==null){ const def=_VM_THR_DEFAULTS[metric]; if(def){w=def.w;c=def.c;} }
  if(w!=null){ wi.value=w; ci.value=c; }
}

function _vmwareThrLabel(metric, isWarn){
  const pfx=isWarn?'Warn':'Crit';
  if(!metric) return pfx+' Value';
  if(metric.startsWith('dstore_')) return pfx+' (GB free — alert below)';
  const m=_allVmwareMetrics().find(x=>x.v===metric);
  const u=m?.unit||'';
  if(u==='%')       return pfx+' %';
  if(u==='MB')      return pfx+' MB';
  if(u==='KB')      return pfx+' KB';
  if(u==='KBps')    return pfx+' KBps';
  if(u==='ms')      return pfx+' ms';
  if(u==='seconds') return pfx+' seconds';
  if(u==='watt')    return pfx+' watt';
  if(u==='GB')      return pfx+' GB';
  return pfx+' Value';
}

// Per-metric sensible warn/crit placeholder hints
const _VM_THR_PH = {
  cpu_usage:       {w:'e.g. 80',  c:'e.g. 90'},
  cpu_ready:       {w:'e.g. 10',  c:'e.g. 20'},
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
  // Host metrics
  host_cpu_usage:      {w:'e.g. 80',  c:'e.g. 95'},
  host_cpu_ready:      {w:'e.g. 10',  c:'e.g. 20'},
  host_mem_active:     {w:'e.g. 32768',c:'e.g. 49152'},
  host_mem_consumed:   {w:'e.g. 49152',c:'e.g. 57344'},
  host_mem_usage_pct:  {w:'e.g. 80',  c:'e.g. 95'},
  host_ds_read_lat:    {w:'e.g. 20',  c:'e.g. 50'},
  host_ds_write_lat:   {w:'e.g. 20',  c:'e.g. 50'},
  host_disk_dev_lat:   {w:'e.g. 20',  c:'e.g. 50'},
  host_disk_kern_lat:  {w:'e.g. 10',  c:'e.g. 30'},
};

function _vmwareThrUpdateLabels(){
  const sel=document.getElementById('as-vmmet');
  if(!sel) return;
  const wl=document.getElementById('as-wms-lbl');
  const cl=document.getElementById('as-cms-lbl');
  if(wl) wl.textContent=_vmwareThrLabel(sel.value,true);
  if(cl) cl.textContent=_vmwareThrLabel(sel.value,false);
  const _noThr2=['uptime','on','disk_read','disk_write','disk_usage',
    'host_disk_read','host_disk_write','host_disk_usage','host_net_rx','host_net_tx','host_net_usage',
    'host_power','host_uptime','host_mem_swap'].includes(sel.value);
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

let _vmwareHostMetrics=null;
async function _vmwareLoadMetrics(){
  const sel=document.getElementById('as-vmmet');
  if(!sel) return;
  // Detect mode from the saved metric when opening an existing sensor
  const cur=document.getElementById('as-vmmet-v')?.value||'';
  if(cur.startsWith('dstore_')) _vmDstoreMode=true;
  else if(cur) _vmDstoreMode=false;
  sel.onchange=()=>_vmwareThrUpdateLabels();
  // Always rebuild options when the mode might have flipped between opens
  sel.innerHTML='<option value="">— select metric —</option>';
  if(_vmDstoreMode){
    if(!_vmwareDatastoreMetrics){
      try{
        const r=await fetch('/api/vmware/datastore-metrics');
        const d=await r.json();
        _vmwareDatastoreMetrics=d.metrics||[];
      }catch(e){ return; }
    }
    const grp=document.createElement('optgroup');
    grp.label='Datastore Metrics';
    _vmwareDatastoreMetrics.forEach(m=>{const o=document.createElement('option');o.value=m.v;o.textContent=m.l;grp.appendChild(o);});
    sel.appendChild(grp);
  }else{
    if(!_vmwareMetrics||!_vmwareHostMetrics){
      try{
        const [vmR,hostR]=await Promise.all([fetch('/api/vmware/metrics'),fetch('/api/vmware/host-metrics')]);
        const vmD=await vmR.json(), hostD=await hostR.json();
        _vmwareMetrics=vmD.metrics||[];
        _vmwareHostMetrics=hostD.metrics||[];
      }catch(e){ return; }
    }
    const vmGrp=document.createElement('optgroup');
    vmGrp.label='VM Metrics';
    _vmwareMetrics.forEach(m=>{const o=document.createElement('option');o.value=m.v;o.textContent=m.l+' ('+m.unit+')';vmGrp.appendChild(o);});
    sel.appendChild(vmGrp);
    const hostGrp=document.createElement('optgroup');
    hostGrp.label='Host Metrics';
    _vmwareHostMetrics.forEach(m=>{const o=document.createElement('option');o.value=m.v;o.textContent=m.l+' ('+m.unit+')';hostGrp.appendChild(o);});
    sel.appendChild(hostGrp);
  }
  if(cur) sel.value=cur;
  _vmwareThrUpdateLabels();
}
function _allVmwareMetrics(){ return [...(_vmwareMetrics||[]),...(_vmwareHostMetrics||[]),...(_vmwareDatastoreMetrics||[])]; }

// Fetch the three VMware metric catalogues if any are missing. Callers outside
// the Add-Sensor flow (e.g. the per-VM Edit Metrics modal in sensors.js) need
// this — without it, a user who opens Edit before ever opening Add Sensor sees
// an empty catalogue and an error toast. Safe to call repeatedly: already-loaded
// arrays are kept as-is.
async function _ensureVmwareCatalogue() {
  const tasks = [];
  if (!_vmwareMetrics) tasks.push(
    fetch('/api/vmware/metrics').then(r => r.json()).then(d => { _vmwareMetrics = d.metrics || []; })
  );
  if (!_vmwareHostMetrics) tasks.push(
    fetch('/api/vmware/host-metrics').then(r => r.json()).then(d => { _vmwareHostMetrics = d.metrics || []; })
  );
  if (!_vmwareDatastoreMetrics) tasks.push(
    fetch('/api/vmware/datastore-metrics').then(r => r.json()).then(d => { _vmwareDatastoreMetrics = d.metrics || []; })
  );
  if (tasks.length) await Promise.all(tasks);
}

let _discHostMode=false;  // true when host discovery table is shown
// POST with a client-side ceiling — the global `api()` helper has no timeout,
// and vCenter discovery can legitimately take 10–120s. Without a ceiling the
// button would sit on "Connecting to vCenter…" forever if the backend hung.
// Backend discover timeout is 120s; we set 150s here so the server's clean
// ConnectionError surfaces first, then AbortController kicks in as a last
// resort. Throws an Error with `.code === 'timeout'` on abort so the caller
// can show a specific message.
async function _vmApiTimed(method, path, body, timeoutMs){
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const o = {method, headers:{'Content-Type':'application/json'}, signal: ctrl.signal};
    if (body) o.body = JSON.stringify(body);
    const r = await fetch(path, o);
    if (r.status === 401) { if (!_loggedOut) showLogin('Session expired. Please sign in again.'); return {}; }
    if (!r.ok) {
      const err = await r.json().catch(() => ({error: r.statusText}));
      const e = new Error(err.error || r.statusText);
      e.code = 'http';
      throw e;
    }
    return await r.json();
  } catch (e) {
    if (e.name === 'AbortError') {
      const te = new Error('Request timed out');
      te.code = 'timeout';
      throw te;
    }
    throw e;
  } finally {
    clearTimeout(t);
  }
}

async function discoverVMs(){
  _discHostMode=false;
  _vmDstoreMode=false;
  _vmSelectedCapacityGB=0;
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
    r=await _vmApiTimed('POST','/api/vmware/vms',payload,150000);
  }catch(e){
    if(statusEl){
      statusEl.style.color='var(--down)';
      statusEl.textContent = e.code==='timeout'
        ? 'Timed out after 150s — vCenter is slow or overloaded'
        : (e.message || 'Request failed');
    }
    return;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent='▥ Discover VMs'; }
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
  if(!_vmwareMetrics||!_vmwareHostMetrics){
    try{
      const [vmR,hostR]=await Promise.all([fetch('/api/vmware/metrics'),fetch('/api/vmware/host-metrics')]);
      const vmD=await vmR.json(), hostD=await hostR.json();
      _vmwareMetrics=vmD.metrics||[];
      _vmwareHostMetrics=hostD.metrics||[];
    }catch(e){}
  }
  const _grpNames={cpu:'CPU',mem:'Memory',disk:'Disk',datastore:'Datastore',net:'Network',sys:'System'};
  const _metByGrp={};
  (_vmwareMetrics||[]).forEach(m=>{(_metByGrp[m.group]=_metByGrp[m.group]||[]).push(m);});
  const metricCheckboxes=
    `<label class="vm-met-item vm-met-all-item" style="border-bottom:1px solid var(--border);margin-bottom:2px;padding-bottom:5px"><input type="checkbox" class="vm-met-all-cb" onchange="vmMetSelectAll(this)"> <strong>All metrics</strong></label>`+
    Object.entries(_metByGrp).map(([grp,mets])=>
      `<div class="vm-met-grp-hdr">${_grpNames[grp]||grp}</div>`+
      mets.map(m=>`<label class="vm-met-item"><input type="checkbox" value="${m.v}" onchange="vmMetChanged(this)"> ${esc(m.l)}</label>`).join('')
    ).join('');

  let html='<div style="border:1px solid var(--border);border-radius:6px;margin-top:4px;overflow:visible">';
  html+='<div style="padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<input type="text" id="as-vm-search" placeholder="Search VM names…" oninput="filterVMs(this.value)" autocomplete="off" style="flex:1;font-size:12px;padding:4px 8px;background:var(--bg3);border:1px solid var(--border2);border-radius:4px;color:var(--text);outline:none"/>';
  html+='<span style="font-size:11px;color:var(--text3);white-space:nowrap;flex-shrink:0">Set for checked:</span>';
  html+=`<div class="vm-met-wrap" style="flex-shrink:0"><button class="vm-met-btn" type="button" onclick="toggleVmMetPicker(this)">— bulk metrics —</button><div class="vm-met-drop" style="display:none">${metricCheckboxes}</div></div>`;
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

async function discoverHosts(){
  _discHostMode=true;
  _vmDstoreMode=false;
  _vmSelectedCapacityGB=0;
  const did      =window._ifaceDid;
  const host     =document.getElementById('as-vmh')?.value.trim()||S.devices[did]?.host||'';
  const username =document.getElementById('as-vmu')?.value.trim()||'';
  const password =document.getElementById('as-vmpw')?.value||'';
  const port     =parseInt(document.getElementById('as-vmp')?.value)||443;
  const vssl     =document.getElementById('as-vmssl')?.checked!==false;
  const btn      =document.getElementById('as-vmh-disc-btn');
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
    r=await _vmApiTimed('POST','/api/vmware/hosts',payload,150000);
  }catch(e){
    if(statusEl){
      statusEl.style.color='var(--down)';
      statusEl.textContent = e.code==='timeout'
        ? 'Timed out after 150s — vCenter is slow or overloaded'
        : (e.message || 'Request failed');
    }
    return;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent='▦ Discover Hosts'; }
  }
  if(r.error){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent=r.error; }
    return;
  }
  const hosts=r.hosts||[];
  if(!hosts.length){
    if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='No ESXi hosts found.'; }
    return;
  }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent=`${hosts.length} host${hosts.length!==1?'s':''} discovered`; }

  // Ensure host metrics are loaded
  if(!_vmwareHostMetrics){
    try{
      const mr=await fetch('/api/vmware/host-metrics');
      const md=await mr.json();
      _vmwareHostMetrics=md.metrics||[];
    }catch(e){}
  }
  const _grpNames={cpu:'CPU',mem:'Memory',disk:'Disk',datastore:'Datastore',net:'Network',sys:'System'};
  const _metByGrp={};
  (_vmwareHostMetrics||[]).forEach(m=>{(_metByGrp[m.group]=_metByGrp[m.group]||[]).push(m);});
  const metricCheckboxes=
    `<label class="vm-met-item vm-met-all-item" style="border-bottom:1px solid var(--border);margin-bottom:2px;padding-bottom:5px"><input type="checkbox" class="vm-met-all-cb" onchange="vmMetSelectAll(this)"> <strong>All metrics</strong></label>`+
    Object.entries(_metByGrp).map(([grp,mets])=>
      `<div class="vm-met-grp-hdr">${_grpNames[grp]||grp}</div>`+
      mets.map(m=>`<label class="vm-met-item"><input type="checkbox" value="${m.v}" onchange="vmMetChanged(this)"> ${esc(m.l)}</label>`).join('')
    ).join('');

  let html='<div style="border:1px solid var(--border);border-radius:6px;margin-top:4px;overflow:visible">';
  html+='<div style="padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<input type="text" id="as-vm-search" placeholder="Search host names…" oninput="filterVMs(this.value)" autocomplete="off" style="flex:1;font-size:12px;padding:4px 8px;background:var(--bg3);border:1px solid var(--border2);border-radius:4px;color:var(--text);outline:none"/>';
  html+='<span style="font-size:11px;color:var(--text3);white-space:nowrap;flex-shrink:0">Set for checked:</span>';
  html+=`<div class="vm-met-wrap" style="flex-shrink:0"><button class="vm-met-btn" type="button" onclick="toggleVmMetPicker(this)">— bulk metrics —</button><div class="vm-met-drop" style="display:none">${metricCheckboxes}</div></div>`;
  html+='</div>';
  html+='<div style="overflow-x:auto;overflow-y:auto;max-height:260px">';
  html+='<table style="width:100%;border-collapse:collapse;font-size:12px">';
  html+='<thead><tr style="background:var(--bg2);color:var(--text2);position:sticky;top:0;z-index:1">';
  html+='<th style="padding:5px 8px;text-align:center;white-space:nowrap"><input type="checkbox" id="as-vm-all" title="Select all visible" onchange="toggleAllVMs(this)"/></th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;min-width:160px">Host Name</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">State</th>';
  html+='<th style="padding:5px 8px;text-align:center;white-space:nowrap">Cores</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Memory</th>';
  html+='<th style="padding:5px 8px;text-align:center;white-space:nowrap">VMs</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;max-width:140px">Version</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;min-width:170px">Metrics</th>';
  html+='</tr></thead><tbody id="as-vm-tbody">';

  hosts.forEach((h,i)=>{
    const stClr=h.connection_state==='connected'?'var(--up)':'var(--down)';
    const memStr=h.memory_mb>=1024?Math.round(h.memory_mb/1024)+'GB':h.memory_mb+'MB';
    const verShort=(h.version||'').replace(/^VMware\s+/i,'');
    const rowBg=i%2?'background:var(--bg2)':'';
    html+=`<tr style="border-top:1px solid var(--border);${rowBg}">`;
    html+=`<td style="padding:4px 8px;text-align:center"><input type="checkbox" class="as-vm-cb" data-vmid="${esc(h.host_id)}" data-name="${esc(h.name)}" data-mem-mb="${h.memory_mb||0}" data-num-cpu="${h.cpu_count||0}" onchange="updateVMSelCount()"/></td>`;
    html+=`<td style="padding:4px 8px;font-weight:500;white-space:nowrap" title="${esc(h.name)}">${esc(h.name)}</td>`;
    html+=`<td style="padding:4px 8px;color:${stClr};white-space:nowrap">${esc(h.connection_state)}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);text-align:center;white-space:nowrap">${h.cpu_cores}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);white-space:nowrap">${memStr}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);text-align:center;white-space:nowrap">${h.num_vms}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text2);max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(h.version)}">${esc(verShort)}</td>`;
    html+=`<td style="padding:4px 8px;position:relative"><div class="vm-met-wrap" data-vmid="${esc(h.host_id)}"><button class="vm-met-btn" type="button" onclick="toggleVmMetPicker(this)">— pick metrics —</button><div class="vm-met-drop" style="display:none">${metricCheckboxes}</div></div></td>`;
    html+='</tr>';
  });

  html+='</tbody></table></div>';
  html+='<div style="padding:8px 10px;background:var(--bg2);border-top:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<button class="btn-p" style="font-size:11px;padding:5px 14px" onclick="addSelectedVMSensors()" id="as-vm-add-btn">Add Selected as Sensors</button>';
  html+='<span id="as-vm-sel-count" style="font-size:11px;color:var(--text3)">0 hosts · 0 sensors</span>';
  html+='</div></div>';
  listEl.innerHTML=html;
  listEl.style.display='';
}

async function discoverDatastores(){
  _discHostMode=false;
  _vmDstoreMode=true;
  const did      =window._ifaceDid;
  const host     =document.getElementById('as-vmh')?.value.trim()||S.devices[did]?.host||'';
  const username =document.getElementById('as-vmu')?.value.trim()||'';
  const password =document.getElementById('as-vmpw')?.value||'';
  const port     =parseInt(document.getElementById('as-vmp')?.value)||443;
  const vssl     =document.getElementById('as-vmssl')?.checked!==false;
  const btn      =document.getElementById('as-vmds-disc-btn');
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
    r=await _vmApiTimed('POST','/api/vmware/datastores',payload,150000);
  }catch(e){
    if(statusEl){
      statusEl.style.color='var(--down)';
      statusEl.textContent = e.code==='timeout'
        ? 'Timed out after 150s — vCenter is slow or overloaded'
        : (e.message || 'Request failed');
    }
    return;
  }finally{
    if(btn){ btn.disabled=false; btn.textContent='▤ Discover Datastores'; }
  }
  if(r.error){
    if(statusEl){ statusEl.style.color='var(--down)'; statusEl.textContent=r.error; }
    return;
  }
  const datastores=r.datastores||[];
  if(!datastores.length){
    if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent='No datastores found.'; }
    return;
  }
  if(statusEl){ statusEl.style.color='var(--text3)'; statusEl.textContent=`${datastores.length} datastore${datastores.length!==1?'s':''} discovered — click one to select`; }

  // Ensure datastore metrics are loaded (for labels)
  if(!_vmwareDatastoreMetrics){
    try{
      const mr=await fetch('/api/vmware/datastore-metrics');
      const md=await mr.json();
      _vmwareDatastoreMetrics=md.metrics||[];
    }catch(e){}
  }

  let html='<div style="border:1px solid var(--border);border-radius:6px;margin-top:4px;overflow:visible">';
  html+='<div style="padding:6px 8px;background:var(--bg2);border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center">';
  html+='<input type="text" id="as-vm-search" placeholder="Search datastore names…" oninput="filterDatastores(this.value)" autocomplete="off" style="flex:1;font-size:12px;padding:4px 8px;background:var(--bg3);border:1px solid var(--border2);border-radius:4px;color:var(--text);outline:none"/>';
  html+='</div>';
  html+='<div style="overflow-x:auto;overflow-y:auto;max-height:320px">';
  html+='<table style="width:100%;border-collapse:collapse;font-size:12px">';
  html+='<thead><tr style="background:var(--bg2);color:var(--text2);position:sticky;top:0;z-index:1">';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap;min-width:180px">Datastore</th>';
  html+='<th style="padding:5px 8px;text-align:left;white-space:nowrap">Type</th>';
  html+='<th style="padding:5px 8px;text-align:right;white-space:nowrap">Capacity</th>';
  html+='<th style="padding:5px 8px;text-align:right;white-space:nowrap">Free</th>';
  html+='<th style="padding:5px 8px;text-align:right;white-space:nowrap">Free %</th>';
  html+='<th style="padding:5px 8px;text-align:center;white-space:nowrap">State</th>';
  html+='<th style="padding:5px 8px"></th>';
  html+='</tr></thead><tbody id="as-vm-tbody">';

  datastores.forEach((d,i)=>{
    const rowBg=i%2?'background:var(--bg2)':'';
    const capStr=d.capacity_gb>=1024?(d.capacity_gb/1024).toFixed(2)+' TB':d.capacity_gb+' GB';
    const freeStr=d.free_gb>=1024?(d.free_gb/1024).toFixed(2)+' TB':d.free_gb+' GB';
    const freePctClr=d.free_pct<10?'var(--down)':d.free_pct<20?'var(--warn)':'var(--up)';
    const stClr=d.accessible?'var(--up)':'var(--down)';
    const stText=d.accessible?'accessible':'unavailable';
    html+=`<tr style="border-top:1px solid var(--border);${rowBg}" data-ds-name="${esc(d.name)}">`;
    html+=`<td style="padding:4px 8px;font-weight:500;white-space:nowrap" title="${esc(d.name)}">${esc(d.name)}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);white-space:nowrap">${esc(d.type||'')}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text3);text-align:right;white-space:nowrap">${capStr}</td>`;
    html+=`<td style="padding:4px 8px;color:var(--text2);text-align:right;white-space:nowrap">${freeStr}</td>`;
    html+=`<td style="padding:4px 8px;color:${freePctClr};text-align:right;white-space:nowrap">${d.free_pct}%</td>`;
    html+=`<td style="padding:4px 8px;color:${stClr};text-align:center;white-space:nowrap">${stText}</td>`;
    html+=`<td style="padding:4px 8px;text-align:right;white-space:nowrap"><button class="btn-p" type="button" style="font-size:11px;padding:3px 10px" onclick='selectDatastore(${JSON.stringify(d).replace(/'/g,"&#39;")})'>Select</button></td>`;
    html+='</tr>';
  });

  html+='</tbody></table></div></div>';
  listEl.innerHTML=html;
  listEl.style.display='';
}

function filterDatastores(q){
  const term=(q||'').toLowerCase();
  document.querySelectorAll('#as-vm-tbody tr').forEach(tr=>{
    const name=(tr.getAttribute('data-ds-name')||'').toLowerCase();
    tr.style.display=name.includes(term)?'':'none';
  });
}

function selectDatastore(d){
  _vmDstoreMode=true;
  _vmSelectedCapacityGB=d.capacity_gb||0;
  _vmSelectedMemMB=0;
  const oidEl=document.getElementById('as-vmid');
  if(oidEl) oidEl.value=d.ds_id||'';
  const nmEl=document.getElementById('as-vmnm');
  if(nmEl) nmEl.value=d.name||'';
  // Rebuild the metric dropdown in datastore mode, then pick the single metric
  const metV=document.getElementById('as-vmmet-v');
  if(metV) metV.value='dstore_free_gb';
  _vmwareLoadMetrics().then(()=>{
    const sel=document.getElementById('as-vmmet');
    if(sel){ sel.value='dstore_free_gb'; }
    _vmwareThrUpdateLabels();
  });
  // Prefill sensor name if blank or still the default
  const nameEl=document.getElementById('as-n');
  if(nameEl&&(!nameEl.value||nameEl.value.startsWith('Ping,'))){
    nameEl.value=`${d.name} Free Space`;
  }
  const listEl=document.getElementById('as-vm-list');
  if(listEl) listEl.style.display='none';
  const statusEl=document.getElementById('as-vm-status');
  if(statusEl){
    statusEl.style.color='var(--up)';
    const capStr=d.capacity_gb>=1024?(d.capacity_gb/1024).toFixed(2)+' TB':d.capacity_gb+' GB';
    statusEl.textContent=`Selected: ${d.name} (${capStr}) — thresholds auto-filled to 20% / 10% of capacity`;
  }
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
  const _lbl=_discHostMode?'host':'VM';
  if(el) el.textContent=nVms?`${nVms} ${_lbl}${nVms>1?'s':''} · ${nSensors} sensor${nSensors!==1?'s':''}`:` 0 ${_lbl}s · 0 sensors`;
  const all=document.getElementById('as-vm-all');
  if(all){all.indeterminate=(nVms>0&&nVms<visibleCbs.length);all.checked=(visibleCbs.length>0&&checked.length>=visibleCbs.length);}
  const addBtn=document.getElementById('as-vm-add-btn');
  if(addBtn) addBtn.textContent=(nVms===1&&nSensors<=1)?'Apply to Form':'Add Selected as Sensors';
}

// ── Metric picker dropdown ────────────────────────────────────────
// NOTE: .mbox has backdrop-filter which traps position:fixed children inside the modal.
// Fix: teleport the .vm-met-drop to document.body when opening, return it on close.
let _vmMetPickerOpen=null;
function _closeVmMetPicker(){
  if(!_vmMetPickerOpen) return;
  const drop=_vmMetPickerOpen;
  drop.style.display='none';
  // Return element to its original .vm-met-wrap
  if(drop._ownerWrap) drop._ownerWrap.appendChild(drop);
  _vmMetPickerOpen=null;
}
function toggleVmMetPicker(btn){
  const drop=btn.nextElementSibling;
  const isOpen=_vmMetPickerOpen===drop;
  // Close any other open picker
  if(_vmMetPickerOpen&&_vmMetPickerOpen!==drop) _closeVmMetPicker();
  if(isOpen){
    _closeVmMetPicker();
  } else {
    // Teleport to body so it escapes .mbox backdrop-filter containing block
    const wrap=btn.closest('.vm-met-wrap');
    drop._ownerWrap=wrap;
    document.body.appendChild(drop);
    const r=btn.getBoundingClientRect();
    const dropW=240;
    const left=Math.max(4, r.right-dropW);
    drop.style.position='fixed';
    drop.style.top=(r.bottom+3)+'px';
    drop.style.left=left+'px';
    drop.style.right='auto';
    drop.style.zIndex='9999';
    drop.style.display='block';
    _vmMetPickerOpen=drop;
  }
}
// Close picker on outside click
document.addEventListener('click',e=>{
  if(_vmMetPickerOpen&&!e.target.closest('.vm-met-wrap')&&!e.target.closest('.vm-met-drop')){
    _closeVmMetPicker();
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
  // drop may be teleported to body — use stored owner reference
  const wrap=drop?._ownerWrap||drop?.parentElement;
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
    if(!metric){toast('Pick at least one metric','err');return;}
    const metricDef=_allVmwareMetrics().find(m=>m.v===metric);
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
  const wms=parseInt(document.getElementById('as-wms')?.value)||null;
  const cms=parseInt(document.getElementById('as-cms')?.value)||null;
  const alertsMuted=document.getElementById('as-am')?.checked||false;

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
      const metricDef=_allVmwareMetrics().find(m=>m.v===metric);
      // Smart threshold: only if user left warn/crit blank
      let rowWms=wms, rowCms=cms;
      // Skip thresholds for info-only metrics
      const _INFO_ONLY=['uptime','on','disk_read','disk_write','disk_usage',
        'host_disk_read','host_disk_write','host_disk_usage','host_net_rx','host_net_tx','host_net_usage',
        'host_power','host_uptime','host_mem_swap'];
      if(_INFO_ONLY.includes(metric)){ rowWms=null; rowCms=null; }
      else if(!rowWms&&!rowCms){
        // Memory metrics: compute from VM/host RAM
        if(vmMemMB>0){
          if(metric==='mem_consumed'||metric==='host_mem_consumed'){ rowWms=Math.round(vmMemMB*0.80); rowCms=Math.round(vmMemMB*0.90); }
          else if(metric==='mem_active'||metric==='host_mem_active'){ rowWms=Math.round(vmMemMB*0.50); rowCms=Math.round(vmMemMB*0.70); }
        }
        // Other metrics: use fixed per-metric defaults
        if(!rowWms){ const def=_VM_THR_DEFAULTS[metric]; if(def){rowWms=def.w;rowCms=def.c;} }
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
        verify_ssl:vssl,warn_ms:row.wms,crit_ms:row.cms,
        alerts_muted:alertsMuted,
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
  } else if(type==='smtp'){
    host=document.getElementById('as-smh')?.value.trim()||'';
    port=parseInt(document.getElementById('as-smp')?.value)||25;
  } else if(type==='ssh'){
    host=document.getElementById('as-shh')?.value.trim()||'';
    port=parseInt(document.getElementById('as-shp')?.value)||22;
  } else if(type==='sftp'){
    host=document.getElementById('as-sfh')?.value.trim()||'';
    port=parseInt(document.getElementById('as-sfp')?.value)||22;
  }
  const warn_ms      =parseInt(document.getElementById('as-wms')?.value)||null;
  const crit_ms      =parseInt(document.getElementById('as-cms')?.value)||null;
  const loss_warn_pct=parseInt(document.getElementById('as-lwp')?.value)||0;
  const loss_crit_pct=parseInt(document.getElementById('as-lcp')?.value)||0;
  const alerts_muted =document.getElementById('as-am')?.checked||false;
  if(!name){toast('Sensor name required','err');return null;}
  const payload={type,name,host,port,url,interval:iv,timeout:tmo,
          verify_ssl,snmp_community,snmp_oid,snmp_version,snmp_unit,
          dns_query,dns_record_type,dns_server,http_expected_status,
          warn_ms,crit_ms,loss_warn_pct,loss_crit_pct,
          keyword,keyword_case,banner_regex,alerts_muted};
  // SNMPv3 per-sensor override — only send when type=snmp + version=3.  Empty
  // fields round-trip as "" so the backend inherits from the device default.
  if(type==='snmp' && snmp_version==='3'){
    payload.snmp_v3_user       = (document.getElementById('as-v3-user')?.value || '').trim();
    payload.snmp_v3_level      = document.getElementById('as-v3-level')?.value || 'noAuthNoPriv';
    payload.snmp_v3_auth_proto = document.getElementById('as-v3-auth-proto')?.value || '';
    payload.snmp_v3_priv_proto = document.getElementById('as-v3-priv-proto')?.value || '';
    payload.snmp_v3_context    = (document.getElementById('as-v3-ctx')?.value || '').trim();
    const _ap = document.getElementById('as-v3-auth-pass')?.value || '';
    const _pp = document.getElementById('as-v3-priv-pass')?.value || '';
    if(_ap) payload.snmp_v3_auth_pass = _ap;
    if(_pp) payload.snmp_v3_priv_pass = _pp;
  }
  const _anomEn=document.getElementById('as-anom-en');
  if(_anomEn){
    payload.anomaly_enabled=_anomEn.checked?1:0;
    const _sv=parseInt(document.getElementById('as-anom-sens')?.value||'2');
    payload.anomaly_sensitivity=(_sv>=1&&_sv<=3)?_sv:2;
    const _mv=parseInt(document.getElementById('as-anom-min')?.value||'50');
    payload.anomaly_min_samples=Math.max(5,Math.min(10000,isNaN(_mv)?50:_mv));
  }
  if(type==='vmware'){
    payload.vmware_user=document.getElementById('as-vmu')?.value.trim()||'';
    payload.vmware_password=document.getElementById('as-vmpw')?.value||'';
    payload.vmware_vm_id=document.getElementById('as-vmid')?.value.trim()||'';
    payload.vmware_vm_name=document.getElementById('as-vmnm')?.value.trim()||'';
    payload.vmware_metric=document.getElementById('as-vmmet')?.value||'';
    payload.vmware_disk_path=document.getElementById('as-vm-diskpath')?.value.trim()||'';
    if(['uptime','on','disk_read','disk_write','disk_usage',
        'host_disk_read','host_disk_write','host_disk_usage','host_net_rx','host_net_tx','host_net_usage',
        'host_power','host_uptime','host_mem_swap'].includes(payload.vmware_metric)){ payload.warn_ms=null; payload.crit_ms=null; }
    if(!payload.vmware_vm_id){toast('VM ID required — use Discover VMs','err');return null;}
    if(!payload.vmware_metric){toast('Select a metric','err');return null;}
  }
  if(type==='smtp'){
    payload.smtp_tls        =document.getElementById('as-smtls')?.value||'none';
    payload.smtp_test_level =document.getElementById('as-smlvl')?.value||'ehlo';
    payload.smtp_user       =document.getElementById('as-smu')?.value.trim()||'';
    payload.smtp_password   =document.getElementById('as-smpw')?.value||'';
    payload.smtp_from       =document.getElementById('as-smfr')?.value.trim()||'';
    payload.smtp_rcpt       =document.getElementById('as-smrc')?.value.trim()||'';
    const _lvl=payload.smtp_test_level;
    if((_lvl==='auth'||_lvl==='mailfrom')&&!payload.smtp_user){
      toast('AUTH level requires a username','err');return null;
    }
    if(_lvl==='mailfrom'&&(!payload.smtp_from||!payload.smtp_rcpt)){
      toast('MAIL FROM level requires From and To addresses','err');return null;
    }
  }
  if(type==='ssh'){
    payload.ssh_test_level = document.getElementById('as-shlvl')?.value||'banner';
    payload.ssh_auth_type  = document.querySelector('input[name="as-shauth"]:checked')?.value||'password';
    payload.ssh_user       = document.getElementById('as-shu')?.value.trim()||'';
    payload.ssh_password   = document.getElementById('as-shpw')?.value||'';
    payload.ssh_private_key= document.getElementById('as-shkey')?.value||'';
    if(payload.ssh_test_level==='auth' && !payload.ssh_user){
      toast('AUTH level requires a username','err');return null;
    }
  }
  if(type==='sftp'){
    payload.sftp_test_level      = document.getElementById('as-sflvl')?.value||'open';
    payload.sftp_auth_type       = document.querySelector('input[name="as-sfauth"]:checked')?.value||'password';
    payload.sftp_user            = document.getElementById('as-sfu')?.value.trim()||'';
    payload.sftp_password        = document.getElementById('as-sfpw')?.value||'';
    payload.sftp_private_key     = document.getElementById('as-sfkey')?.value||'';
    payload.sftp_remote_path     = document.getElementById('as-sfpath')?.value.trim()||'';
    payload.sftp_expected_sha256 = document.getElementById('as-sfsha')?.value.trim()||'';
    if(!payload.sftp_user){
      toast('SFTP requires a username','err');return null;
    }
    if(['list','stat','checksum'].includes(payload.sftp_test_level) && !payload.sftp_remote_path){
      toast(`SFTP ${payload.sftp_test_level} level requires a remote path`,'err');return null;
    }
    if(payload.sftp_test_level==='checksum'){
      if(!payload.sftp_expected_sha256){
        toast('Checksum level requires an expected SHA256','err');return null;
      }
      if(!/^[a-fA-F0-9]{64}$/.test(payload.sftp_expected_sha256)){
        toast('SHA256 must be 64 hex characters','err');return null;
      }
      if((payload.interval||0) < 60){
        toast('Checksum level requires interval ≥ 60s','err');return null;
      }
    }
  }
  if(type==='radius'){
    payload.host              = document.getElementById('as-rdh')?.value.trim()||payload.host;
    payload.port              = parseInt(document.getElementById('as-rdp')?.value)||1812;
    payload.radius_test_level = document.getElementById('as-rdlvl')?.value||'reachable';
    payload.radius_secret     = document.getElementById('as-rdsec')?.value||'';
    payload.radius_username   = document.getElementById('as-rdu')?.value.trim()||'';
    payload.radius_password   = document.getElementById('as-rdpw')?.value||'';
    payload.radius_nas_id     = document.getElementById('as-rdnas')?.value.trim()||'';
    // Shared secret is required on create; on edit, blank means "keep existing"
    const _isEdit = !window._snrAddMode;
    if(!payload.radius_secret && !_isEdit){
      toast('RADIUS requires a shared secret','err');return null;
    }
    if(payload.radius_test_level==='auth'){
      if(!payload.radius_username){
        toast('auth level requires a username','err');return null;
      }
      if(!payload.radius_password && !_isEdit){
        toast('auth level requires a password','err');return null;
      }
    }
  }
  return payload;
}

// Toggle visibility of auth + mailfrom rows when test level changes
function _smtpLvlToggle(){
  const lvl=document.getElementById('as-smlvl')?.value||'ehlo';
  const authRow=document.getElementById('as-smtp-auth-row');
  const mailRow=document.getElementById('as-smtp-mail-row');
  if(authRow) authRow.style.display=(lvl==='auth'||lvl==='mailfrom')?'':'none';
  if(mailRow) mailRow.style.display=(lvl==='mailfrom')?'':'none';
}

// Toggle SSH auth row (when level=auth) + password/key row (based on auth method radio)
function _sshLvlToggle(){
  const lvl=document.getElementById('as-shlvl')?.value||'banner';
  const authType=document.querySelector('input[name="as-shauth"]:checked')?.value||'password';
  const authRow=document.getElementById('as-ssh-auth-row');
  const pwRow  =document.getElementById('as-ssh-pw-row');
  const keyRow =document.getElementById('as-ssh-key-row');
  if(authRow) authRow.style.display=(lvl==='auth')?'':'none';
  if(pwRow)   pwRow.style.display  =(authType==='password')?'':'none';
  if(keyRow)  keyRow.style.display =(authType==='key')?'':'none';
}

// Toggle SFTP conditional rows + bump interval/timeout when checksum is picked.
// Path row appears at list/stat/checksum; SHA row only at checksum.
function _sftpLvlToggle(){
  const lvl=document.getElementById('as-sflvl')?.value||'open';
  const authType=document.querySelector('input[name="as-sfauth"]:checked')?.value||'password';
  const pwRow  =document.getElementById('as-sftp-pw-row');
  const keyRow =document.getElementById('as-sftp-key-row');
  const pathRow=document.getElementById('as-sftp-path-row');
  const shaRow =document.getElementById('as-sftp-sha-row');
  if(pwRow)   pwRow.style.display  =(authType==='password')?'':'none';
  if(keyRow)  keyRow.style.display =(authType==='key')?'':'none';
  if(pathRow) pathRow.style.display=(['list','stat','checksum'].includes(lvl))?'':'none';
  if(shaRow)  shaRow.style.display =(lvl==='checksum')?'':'none';
  // Checksum downloads bytes — default to 5min interval + 30s timeout to avoid
  // hammering the server. Only bump if the current value is lower than the
  // recommended floor; leave higher values alone.
  if(lvl==='checksum'){
    const iv=document.getElementById('as-iv');
    const tm=document.getElementById('as-tmo');
    if(iv && (parseInt(iv.value)||0) < 300){
      iv.value=300;
      iv.title='checksum level: min 60s, default 300s — avoids hammering the server with file downloads';
    }
    if(tm && (parseInt(tm.value)||0) < 30){ tm.value=30; }
  }
}

function _radiusLvlToggle(){
  const lvl=document.getElementById('as-rdlvl')?.value||'reachable';
  const userRow=document.getElementById('as-rd-user-row');
  const pwRow  =document.getElementById('as-rd-pw-row');
  const show=(lvl==='auth');
  if(userRow) userRow.style.display=show?'':'none';
  if(pwRow)   pwRow.style.display  =show?'':'none';
}

// Prefill SMTP fields from the system alert-SMTP config (reads /api/settings)
async function _smtpUseSystem(){
  try{
    const r=await api('GET','/api/settings');
    if(!r||!r.smtp_host){ toast('No system SMTP configured yet','info');return; }
    const h=document.getElementById('as-smh'); if(h)h.value=r.smtp_host;
    const p=document.getElementById('as-smp'); if(p&&r.smtp_port)p.value=r.smtp_port;
    const t=document.getElementById('as-smtls'); if(t&&r.smtp_tls)t.value=r.smtp_tls;
    const u=document.getElementById('as-smu'); if(u&&r.smtp_user)u.value=r.smtp_user;
    _smtpLvlToggle();
    toast('Prefilled from system SMTP — enter password to complete','info');
  }catch(e){ toast('Could not load system SMTP config','err'); }
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
  warn_ms=null,crit_ms=null,loss_warn_pct=0,loss_crit_pct=0,
  keyword='',keyword_case=false,banner_regex=''){
  const r=await api('POST',`/api/device/${did}/sensor`,{name,type,host,port,url,interval,timeout,
    verify_ssl,snmp_community,snmp_oid,snmp_version,dns_query,dns_record_type,dns_server,http_expected_status,
    warn_ms,crit_ms,loss_warn_pct,loss_crit_pct,
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

async function _anomResetBaseline(did,sid){
  if(!confirm('Reset the learned baseline for this sensor?\n\nNo anomaly alerts will fire until a new baseline is learned (bootstrap + cold-start window).')) return;
  try{
    const r=await api('POST',`/api/sensors/${did}/${sid}/anomaly/reset`,{});
    if(r&&r.error){toast(r.error,'err');return;}
    toast('Baseline reset','ok');
    const s=S.sensors[`${did}/${sid}`];
    if(s){
      s.anomaly_mean_ms=null;s.anomaly_stddev_ms=null;s.anomaly_sample_count=0;
    }
  }catch(e){toast('Reset failed','err');}
}
