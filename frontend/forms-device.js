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
        <div class="fr"><label class="fl">Site <span style="color:var(--text3);font-weight:400;font-size:11px">(optional)</span></label>
          ${siteComboHtml('ad-site', '', 'HQ, DR-Site-2…')}</div>
      </div>
      <div class="fr"><label class="fl">Group</label>
        <input type="text" id="ad-g" placeholder="Default Group" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Measured from <span style="color:var(--text3);font-weight:400;font-size:11px">(remote probe — sensors inherit unless overridden)</span></label>
        ${typeof _probeSelectHtml==='function' ? _probeSelectHtml('ad-probe','', 'Inherit from site / Central') : '<select id="ad-probe"><option value="">Central</option></select>'}</div>
      <div class="fr"><label class="fl">Topology Role <span style="color:var(--text3);font-weight:400;font-size:11px">(optional — anchors auto-links on the NTM Live map)</span></label>
        <select id="ad-role">${_lmTierOptionsHtml('', '— None —')}</select></div>
      <details class="dev-creds" style="margin-top:10px">
        <summary style="cursor:pointer;color:var(--text2);font-size:13px;font-weight:500;user-select:none">Default Credentials <span style="color:var(--text3);font-weight:400">(optional — pre-fills new sensors)</span></summary>
        <div style="margin-top:8px;display:flex;flex-direction:column;gap:8px">
          <div class="fgrid">
            <div class="fr" id="ad-snmp-comm-row"><label class="fl">SNMP Community</label>
              <input type="text" id="ad-snmp-comm" placeholder="public" autocomplete="off"/></div>
            <div class="fr"><label class="fl">SNMP Version</label>
              <select id="ad-snmp-ver" onchange="_adSnmpVerChange()">${_snmpVerOptionsHtml('2c')}</select></div>
          </div>
          <div id="ad-v3-block" style="display:none;border-left:2px solid var(--accent);padding-left:10px;margin-left:2px;flex-direction:column;gap:8px">
            <div class="fgrid">
              <div class="fr"><label class="fl">v3 Username</label>
                <input type="text" id="ad-v3-user" placeholder="snmpuser" autocomplete="off"/></div>
              <div class="fr"><label class="fl">Security Level</label>
                <select id="ad-v3-level" onchange="_adV3LevelChange()">${_snmpV3LevelOptionsHtml('noAuthNoPriv')}</select></div>
            </div>
            <div class="fgrid" id="ad-v3-auth-row" style="display:none">
              <div class="fr"><label class="fl">Auth Protocol</label>
                <select id="ad-v3-auth-proto">${_snmpV3AuthOptionsHtml('SHA')}</select></div>
              <div class="fr"><label class="fl">Auth Passphrase</label>
                <input type="password" id="ad-v3-auth-pass" placeholder="min 8 chars" autocomplete="new-password"/></div>
            </div>
            <div class="fgrid" id="ad-v3-priv-row" style="display:none">
              <div class="fr"><label class="fl">Privacy Protocol</label>
                <select id="ad-v3-priv-proto">${_snmpV3PrivOptionsHtml('AES')}</select></div>
              <div class="fr"><label class="fl">Privacy Passphrase</label>
                <input type="password" id="ad-v3-priv-pass" placeholder="min 8 chars" autocomplete="new-password"/></div>
            </div>
            <div class="fr"><label class="fl">Context (optional)</label>
              <input type="text" id="ad-v3-ctx" placeholder="" autocomplete="off"/></div>
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

// Populate a <datalist> with sites from /api/sites. Used by the add/edit
// device modals' site autocomplete inputs. Caches the response on window for
// 60s so reopening the modal doesn't re-fetch. Silently falls back to local
// sites (extracted from S.devices) on fetch failure.
async function _populateSiteDatalist(dlId){
  const dl = document.getElementById(dlId);
  if (!dl) return;
  let sites = window._pwSitesCache;
  const fresh = sites && (Date.now() - sites._t < 60000);
  if (!fresh) {
    try {
      const r = await api('GET', '/api/sites');
      sites = r.sites || [];
      sites._t = Date.now();
      window._pwSitesCache = sites;
    } catch (_) {
      // Fallback: derive from in-memory devices
      sites = [...new Set(Object.values(S.devices || {}).map(d => d.site).filter(Boolean))].sort();
    }
  }
  dl.innerHTML = sites.map(s => `<option value="${esc(s)}"></option>`).join('');
}

async function submitAddDevice(){
  const name=(document.getElementById('ad-n')?.value||'').trim();
  const host=(document.getElementById('ad-h')?.value||'').trim().replace(/^https?:\/\//,'').split('/')[0].toLowerCase();
  const group=(document.getElementById('ad-g')?.value||'Default Group').trim();
  const site =(document.getElementById('ad-site')?.value||'').trim();
  const snmp_community_default=(document.getElementById('ad-snmp-comm')?.value||'').trim();
  const snmp_version_default=document.getElementById('ad-snmp-ver')?.value||'';
  const vmware_user_default=(document.getElementById('ad-vmw-user')?.value||'').trim();
  const vmware_password_default=document.getElementById('ad-vmw-pass')?.value||'';
  if(!name||!host){toast('Name and host are required','err');return;}
  const btn=document.querySelector('#mad .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Adding...';}
  const payload={name,host,group};
  if(site) payload.site = site;
  const _adProbe=document.getElementById('ad-probe')?.value||'';
  if(_adProbe) payload.probe_id=_adProbe;
  if(snmp_community_default) payload.snmp_community_default=snmp_community_default;
  if(snmp_version_default) payload.snmp_version_default=snmp_version_default;
  if(vmware_user_default) payload.vmware_user_default=vmware_user_default;
  if(vmware_password_default) payload.vmware_password_default=vmware_password_default;
  // SNMPv3 device defaults (send only when v3 is selected)
  if(snmp_version_default === '3'){
    payload.snmp_v3_user_default       = (document.getElementById('ad-v3-user')?.value || '').trim();
    payload.snmp_v3_level_default      = document.getElementById('ad-v3-level')?.value || 'noAuthNoPriv';
    payload.snmp_v3_auth_proto_default = document.getElementById('ad-v3-auth-proto')?.value || '';
    payload.snmp_v3_priv_proto_default = document.getElementById('ad-v3-priv-proto')?.value || '';
    payload.snmp_v3_context_default    = (document.getElementById('ad-v3-ctx')?.value || '').trim();
    const _ap = document.getElementById('ad-v3-auth-pass')?.value || '';
    const _pp = document.getElementById('ad-v3-priv-pass')?.value || '';
    if(_ap) payload.snmp_v3_auth_pass_default = _ap;
    if(_pp) payload.snmp_v3_priv_pass_default = _pp;
  }
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
  if(site) window._pwSitesCache = null;  // invalidate so next opens see new site
  // Topology role — applied AFTER device creation because role storage lives on
  // ip_allocations.kind, which is populated by ipam_sync_device_add (enqueued).
  // A brief race is possible (write enqueued while role PUT runs) but the role
  // UPDATE is keyed by (subnet, ip) so it'll either match the row we just
  // inserted or the row our sync inserts moments later — both are correct.
  const role = document.getElementById('ad-role')?.value || '';
  if(role){
    try{ await api('PUT', `/api/device/${r.did}/role`, { role }); }
    catch(err){
      const msg = (err && err.message) ? err.message : 'unknown error';
      toast(`Device added, role tag failed: ${msg}`,'err');
    }
    window._pwRolesCache = null;  // invalidate map auto-link cache
  }
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
// Parent links being edited (Live Map). One entry per chip: {pid, lport, rport}.
// Duplicates by pid are allowed and expected — LACP / multi-link aggregations
// connect the same device pair over multiple physical interfaces, each with
// its own local↔remote port pair. Group refs (pid starting "group:") never
// carry port info but still live in this array as bare entries.
let _edParentLinks = [];
let _edParentDid = null;  // did of the device currently open in Edit Device
let _edParentTierFilter = null;  // inferred tier for the device being edited (null = no filter)

function openEditDevice(did){
  const dev = S.devices[did];
  if(!dev) return;
  closeM('dwo');
  closeM('med');
  _edSecIps = [...(dev.secondary_ips || [])];
  // Flatten the server's {pid, list[]} ports shape into one entry per chip.
  // A pid with no port entries gets a single bare chip; a pid with N port
  // pairs gets N chips. Group refs (no port info) always get one bare chip.
  _edParentLinks = [];
  const _srcIds = Array.isArray(dev.parent_device_ids) ? dev.parent_device_ids : [];
  const _srcPorts = (dev.parent_device_ports && typeof dev.parent_device_ports === 'object')
    ? dev.parent_device_ports : {};
  _srcIds.forEach(pid => {
    if (typeof pid !== 'string' || !pid) return;
    const isGroup = pid.indexOf('group:') === 0;
    const raw = _srcPorts[pid];
    // Server canonical shape is a list, but tolerate the pre-LACP single-dict
    // shape too in case a stale cache slipped through.
    const pairs = Array.isArray(raw) ? raw : (raw && typeof raw === 'object' ? [raw] : []);
    if (isGroup || pairs.length === 0) {
      _edParentLinks.push({ pid, lport: '', rport: '' });
    } else {
      pairs.forEach(p => _edParentLinks.push({
        pid, lport: String(p.lport || ''), rport: String(p.rport || '')
      }));
    }
  });
  _edParentDid = did;
  _edParentTierFilter = _edInferTierForFilter(dev);
  const _edGroups = [...new Set(Object.values(S.devices).map(d=>d.group).filter(Boolean))].sort();
  const _edGroupItems = _edGroups.map(g =>
    `<div class="grp-dd-item${g===(dev.group||'Default Group')?' cur':''}" data-g="${esc(g.toLowerCase())}" onmousedown="event.preventDefault()" onclick="_edgPick(this.textContent)">${esc(g)}</div>`
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
      <div class="ed-tabs">
        <button type="button" class="ed-tab itab itab-active" data-tab="general"     onclick="_edSwitchTab('general')">General</button>
        <button type="button" class="ed-tab itab"             data-tab="networking"  onclick="_edSwitchTab('networking')">Networking <span class="ed-tab-cnt" id="ed-tab-net-count">${_edSecIps.length?'('+_edSecIps.length+')':''}</span></button>
        <button type="button" class="ed-tab itab"             data-tab="credentials" onclick="_edSwitchTab('credentials')">Credentials</button>
        <button type="button" class="ed-tab itab"             data-tab="licenses"    onclick="_edSwitchTab('licenses')">Licenses <span class="ed-tab-cnt" id="ed-tab-lic-count"></span></button>
      </div>

      <!-- Tab: General -->
      <div class="ed-tab-pane" id="ed-tab-general">
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
            <label class="fl">Site <span style="color:var(--text3);font-weight:400;font-size:11px">(optional)</span></label>
            ${siteComboHtml('ed-site', dev.site||'', 'HQ, DR-Site-2…')}
          </div>
        </div>
        <div class="fgrid">
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
          <div class="fr">
            <label class="fl">Topology Role <span style="color:var(--text3);font-weight:400;font-size:11px">(optional)</span></label>
            <select id="ed-role" data-orig="">${_lmTierOptionsHtml('', '— None —')}</select>
          </div>
        </div>
        <div class="fr">
          <label class="fl">Measured from <span style="color:var(--text3);font-weight:400;font-size:11px">(remote probe — sensors inherit unless overridden)</span></label>
          ${typeof _probeSelectHtml==='function' ? _probeSelectHtml('ed-probe', dev.probe_id||'', 'Inherit from site / Central') : '<select id="ed-probe"><option value="">Central</option></select>'}
        </div>
        <div class="fr" style="margin-top:8px">
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
      </div>

      <!-- Tab: Networking -->
      <div class="ed-tab-pane" id="ed-tab-networking" style="display:none">
        <div class="ed-pane-hdr">Secondary IP Addresses</div>
        <div class="ed-pane-sub">Additional IPs the device responds on. Used for IPAM allocation matching and topology role assignment.</div>
        <div id="ed-sip-list" style="max-height:220px;overflow-y:auto;margin-bottom:8px"></div>
        <div style="display:flex;gap:6px">
          <input type="text" id="ed-sip-input" placeholder="e.g. 10.0.0.5" autocomplete="off"
                 style="flex:1" onkeydown="if(event.key==='Enter'){event.preventDefault();_edSipAdd()}"/>
          <button class="btn-s" type="button" onclick="_edSipAdd()" style="white-space:nowrap">+ Add</button>
        </div>

        <div class="ed-pane-hdr" style="margin-top:18px">Parent Devices <span style="color:var(--text3);font-weight:400;font-size:12px">(Live Map)</span></div>
        <div class="ed-pane-sub">
          Devices this hangs off (e.g. a hypervisor's TOR switches, a VM's hypervisors).
          Drives connection lines on the Live Map drill-in. Leave empty to inherit the
          group default. Multi-select supports dual-NIC / dual-homed devices.
        </div>
        <div id="ed-parents-chips" class="pw-chip-input"></div>
        <div style="display:flex;gap:6px;align-items:center">
          <input type="text" id="ed-parents-input" list="ed-parents-dl"
                 placeholder="Type to search devices…" autocomplete="off"
                 style="flex:1"/>
          <datalist id="ed-parents-dl"></datalist>
          <label style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text2);white-space:nowrap;cursor:pointer;user-select:none">
            <input type="checkbox" id="ed-parents-allTiers"/> All tiers
          </label>
        </div>
        <div class="fh" id="ed-parents-hint" style="margin-top:4px"></div>
      </div>

      <!-- Tab: Credentials -->
      <div class="ed-tab-pane" id="ed-tab-credentials" style="display:none">
        <div class="ed-pane-hdr">Default Credentials <span style="color:var(--text3);font-weight:400;font-size:12px">(pre-fills new sensors)</span></div>
        <div style="display:flex;flex-direction:column;gap:10px">
          <div class="fgrid">
            <div class="fr" id="ed-snmp-comm-row" style="${dev.snmp_version_default==='3'?'display:none':''};"><label class="fl">SNMP Community</label>
              <input type="text" id="ed-snmp-comm" value="${esc(dev.snmp_community_default||'')}" placeholder="public" autocomplete="off"/></div>
            <div class="fr"><label class="fl">SNMP Version</label>
              <select id="ed-snmp-ver" onchange="_edSnmpVerChange()">${_snmpVerOptionsHtml(dev.snmp_version_default || '2c')}</select></div>
          </div>
          <div id="ed-v3-block" style="${dev.snmp_version_default==='3'?'':'display:none;'}border-left:2px solid var(--accent);padding-left:10px;margin-left:2px;display:flex;flex-direction:column;gap:8px">
            <div class="fgrid">
              <div class="fr"><label class="fl">v3 Username</label>
                <input type="text" id="ed-v3-user" value="${esc(dev.snmp_v3_user_default||'')}" placeholder="snmpuser" autocomplete="off"/></div>
              <div class="fr"><label class="fl">Security Level</label>
                <select id="ed-v3-level" onchange="_edV3LevelChange()">${_snmpV3LevelOptionsHtml(dev.snmp_v3_level_default || 'noAuthNoPriv')}</select></div>
            </div>
            <div class="fgrid" id="ed-v3-auth-row" style="${(dev.snmp_v3_level_default==='authNoPriv'||dev.snmp_v3_level_default==='authPriv')?'':'display:none'}">
              <div class="fr"><label class="fl">Auth Protocol</label>
                <select id="ed-v3-auth-proto">${_snmpV3AuthOptionsHtml(dev.snmp_v3_auth_proto_default || 'SHA')}</select></div>
              <div class="fr"><label class="fl">Auth Passphrase</label>
                <input type="password" id="ed-v3-auth-pass" placeholder="${dev.has_snmp_v3_auth_pass_default?'(unchanged)':'min 8 chars'}" autocomplete="new-password"/></div>
            </div>
            <div class="fgrid" id="ed-v3-priv-row" style="${dev.snmp_v3_level_default==='authPriv'?'':'display:none'}">
              <div class="fr"><label class="fl">Privacy Protocol</label>
                <select id="ed-v3-priv-proto">${_snmpV3PrivOptionsHtml(dev.snmp_v3_priv_proto_default || 'AES')}</select></div>
              <div class="fr"><label class="fl">Privacy Passphrase</label>
                <input type="password" id="ed-v3-priv-pass" placeholder="${dev.has_snmp_v3_priv_pass_default?'(unchanged)':'min 8 chars'}" autocomplete="new-password"/></div>
            </div>
            <div class="fr"><label class="fl">Context (optional)</label>
              <input type="text" id="ed-v3-ctx" value="${esc(dev.snmp_v3_context_default||'')}" placeholder="" autocomplete="off"/></div>
          </div>
          <div class="fgrid">
            <div class="fr"><label class="fl">VMware Username</label>
              <input type="text" id="ed-vmw-user" value="${esc(dev.vmware_user_default||'')}" placeholder="administrator@vsphere.local" autocomplete="off"/></div>
            <div class="fr"><label class="fl">VMware Password</label>
              <input type="password" id="ed-vmw-pass" placeholder="${dev.has_vmware_password_default?'(unchanged)':''}" autocomplete="new-password"/></div>
          </div>
        </div>
      </div>

      <!-- Tab: Licenses -->
      <div class="ed-tab-pane" id="ed-tab-licenses" style="display:none">
        <div class="ed-pane-hdr">Licenses &amp; Subscriptions <span id="ed-lic-count" style="color:var(--text3);font-weight:400;font-size:12px"></span></div>
        <div class="ed-pane-sub">Track expiry dates for device licenses (FortiCare, SmartNet, etc.). Warn/crit days trigger alerts before expiry.</div>
        <div id="ed-lic-list" style="max-height:260px;overflow-y:auto;margin-bottom:10px"></div>
        <div style="border:1px solid var(--border);border-radius:6px;padding:10px;display:flex;flex-direction:column;gap:8px">
          <div class="fgrid">
            <div class="fr" style="margin:0"><input type="text" id="ed-lic-name" placeholder="License name (e.g. FortiCare)" autocomplete="off"/></div>
            <div class="fr" style="margin:0"><input type="date" id="ed-lic-date" autocomplete="off" class="lic-date-inp"/></div>
          </div>
          <div class="fgrid">
            <div class="fr" style="margin:0"><input type="text" id="ed-lic-note" placeholder="Note (optional)" autocomplete="off"/></div>
            <div class="fr" style="margin:0;display:flex;gap:6px;align-items:center">
              <input type="number" id="ed-lic-warn" value="30" min="0" max="365" style="width:60px" title="Warn days before expiry"/>
              <span style="font-size:10px;color:var(--text3)">warn days</span>
              <input type="number" id="ed-lic-crit" value="0" min="0" max="365" style="width:60px" title="Crit days before expiry"/>
              <span style="font-size:10px;color:var(--text3)">crit days</span>
            </div>
          </div>
          <button class="btn-s" type="button" onclick="_edLicAdd('${did}')" style="align-self:flex-start">+ Add License</button>
        </div>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('med')">Cancel</button>
      <button class="btn-p" onclick="submitEditDevice('${did}')">Save Changes</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ed-n')?.focus(), 50);
  ['ed-n','ed-h','ed-g','ed-site'].forEach(id =>
    document.getElementById(id)?.addEventListener('keydown', e => {
      if(e.key === 'Enter') submitEditDevice(did);
    })
  );
  document.getElementById('ed-g')?.addEventListener('blur', () => setTimeout(_edgHide, 150));
  _edSipRender();
  _edParentInit(did);
  _edLicLoad(did);
  _loadDeviceProfileSection(did);
  _loadDeviceRole(did);
}

// ── Parent Devices (Live Map) helpers ─────────────────────────────────
// Tier filter mirrors monitoring/site_tree.py: VM → hypervisor parents,
// hypervisor → switch, switch → firewall+switch, IPMI → switch,
// firewall → firewall (root). Falls back to "anything" when we can't tell.
const _ED_PARENT_TIER_RULES = [
  {tier: 'ipmi',        parents: ['switch','core_switch'],                                     rx: /\b(ipmi|idrac|ilo|drac|oob|bmc|cimc)\b/i},
  {tier: 'isp',         parents: [],                                                           rx: /\b(isp|isp\d|isp[-_](?:gw|router|link|modem|cpe)|wan[-_]link|fiber[-_]?isp|cable[-_]?isp|starlink|carrier[-_]?(?:cpe|demarc))\b/i},
  {tier: 'wan_switch',  parents: ['isp'],                                                      rx: /\b(wan[-_]?(?:sw|switch|router|gw)|edge[-_]?(?:router|sw|switch)|isp[-_]?sw|border[-_]?(?:sw|router))\b/i},
  {tier: 'firewall',    parents: ['wan_switch','isp','firewall'],                              rx: /\b(fortigate|fortinet|palo[\s\-]?alto|sonicwall|checkpoint|firewall|fw\d|asa\d|edgewall|pfsense|opnsense|untangle|fw-)\b/i},
  {tier: 'core_switch', parents: ['firewall','wan_switch','core_switch'],                      rx: /\b(core[-_]?(?:sw|switch|router)|core\d|aggregation|agg[-_]?(?:sw|switch)|backbone[-_]?(?:sw|switch)|l3[-_]?(?:sw|switch)|spine[-_]?(?:sw|switch)|spine\d|n[79]k|nexus[-_]?[79]\d{3}|asr\d|cat(?:alyst)?[-_]?[69]\d{3})\b/i},
  {tier: 'switch',      parents: ['core_switch','firewall','switch'],                          rx: /\b(switch|sw\d|sw-|tor-|ex[-\s]?\d+|n5k|catalyst|nexus|junos|mikrotik|aruba|cisco-sw|l2|access[-_]?(?:sw|switch)|router|rtr-)\b/i},
  {tier: 'chassis',     parents: ['switch','core_switch'],                                     rx: /\b(bladecenter|chassis|enclosure|c[-\s]?class|c7000|c3000|ucs[-\s]?\d|ucs-fi|m1000e|oa\d|onboard[-\s]?admin)\b/i},
  {tier: 'vm',          parents: ['hypervisor'],                                               rx: /\b(vm-|-vm\b|vms?\b|cluster-vm|guest|tenant)\b/i},
  {tier: 'hypervisor',  parents: ['chassis','switch','core_switch'],                           rx: /\b(esxi?|hyperv|kvm|proxmox|vmware|xenserver|blade|esx-|hypervisor|host\d)\b/i},
];

function _edInferTierForFilter(dev) {
  const blob = `${dev.name || ''} ${dev.host || ''} ${dev.group || ''}`;
  for (const rule of _ED_PARENT_TIER_RULES) {
    if (rule.rx.test(blob)) return rule;
  }
  return {tier: 'hypervisor', parents: ['switch']};  // safe default
}

// Suffix used in the datalist + commit parser to mark a group entry.
// Datalist values must be plain strings; embedding a sentinel lets us
// distinguish a group like "Hypervisors" from a device named "Hypervisors".
const _GRP_SFX = '  ·  group';

function _edRenderParentChips() {
  const wrap = document.getElementById('ed-parents-chips');
  if (!wrap) return;
  if (!_edParentLinks.length) {
    const groupName = (document.getElementById('ed-g')?.value || '').trim();
    wrap.innerHTML = `<span class="pw-chip-empty">None — falls back to group default${groupName ? ` (${esc(groupName)})` : ''}</span>`;
    return;
  }
  wrap.innerHTML = _edParentLinks.map((link, i) => {
    const pid = link.pid;
    if (typeof pid === 'string' && pid.indexOf('group:') === 0) {
      const gname = pid.slice(6);
      // Group refs don't carry port info — wiring is per-device only.
      return `<span class="pw-chip pw-chip-group">
        <span class="pw-chip-badge">GROUP</span>${esc(gname)}
        <button class="pw-chip-x" onclick="_edRemoveParent(${i})" title="Remove">&times;</button>
      </span>`;
    }
    const d = S.devices[pid];
    const label = d ? (d.name || pid) : `(missing: ${pid})`;
    const lport = esc(link.lport || '');
    const rport = esc(link.rport || '');
    // Inline port boxes: local (this device's port) ↔ remote (parent's port).
    // Either field can be left blank; both-blank pairs are pruned on save.
    return `<span class="pw-chip pw-chip-link">
      <span class="pw-chip-label">${esc(label)}</span>
      <input type="text" class="pw-chip-port" placeholder="local" value="${lport}"
             maxlength="32" title="Local port on this device"
             oninput="_edSetParentLinkPort(${i},'lport',this.value)"/>
      <span class="pw-chip-port-sep">&harr;</span>
      <input type="text" class="pw-chip-port" placeholder="remote" value="${rport}"
             maxlength="32" title="Remote port on the parent device"
             oninput="_edSetParentLinkPort(${i},'rport',this.value)"/>
      <button class="pw-chip-x" onclick="_edRemoveParent(${i})" title="Remove">&times;</button>
    </span>`;
  }).join('');
}

function _edRemoveParent(idx) {
  if (idx < 0 || idx >= _edParentLinks.length) return;
  _edParentLinks.splice(idx, 1);
  _edRenderParentChips();
}

function _edSetParentLinkPort(idx, key, val) {
  if (idx < 0 || idx >= _edParentLinks.length) return;
  if (key !== 'lport' && key !== 'rport') return;
  _edParentLinks[idx][key] = String(val || '').slice(0, 32);
}

function _edPopulateParentDatalist() {
  const dl = document.getElementById('ed-parents-dl');
  if (!dl) return;
  const allTiers = document.getElementById('ed-parents-allTiers')?.checked;
  const allowed = allTiers ? null : (_edParentTierFilter ? new Set(_edParentTierFilter.parents) : null);

  function _devTier(d) {
    const blob = `${d.name || ''} ${d.host || ''} ${d.group || ''}`;
    for (const rule of _ED_PARENT_TIER_RULES) {
      if (rule.rx.test(blob)) return rule.tier;
    }
    return 'hypervisor';  // unclassified fallback
  }

  const candidates = Object.values(S.devices || {})
    .filter(d => d.device_id !== _edParentDid)
    .filter(d => allowed ? allowed.has(_devTier(d)) : true)
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''));

  // Group candidates: aggregate per (group name → tier of majority + count).
  // Allow a group if at least one member matches the tier filter.
  const groupAgg = new Map();
  Object.values(S.devices || {}).forEach(d => {
    const g = (d.group || 'Default Group').trim();
    if (!groupAgg.has(g)) groupAgg.set(g, { count: 0, tiers: new Set() });
    const a = groupAgg.get(g);
    a.count += 1;
    a.tiers.add(_devTier(d));
  });
  const groupRows = [...groupAgg.entries()]
    .filter(([g, a]) => a.count >= 2)  // singleton groups: just pick the device
    .filter(([g, a]) => !allowed || [...a.tiers].some(t => allowed.has(t)))
    .sort((a, b) => a[0].localeCompare(b[0]));

  const devOpts = candidates.map(d =>
    `<option value="${esc(d.name || d.device_id)}"></option>`);
  const grpOpts = groupRows.map(([g, a]) =>
    `<option value="${esc(g + _GRP_SFX + ' (' + a.count + ')')}"></option>`);
  dl.innerHTML = [...devOpts, ...grpOpts].join('');

  const hint = document.getElementById('ed-parents-hint');
  if (hint) {
    const grpLabel = groupRows.length ? ` · ${groupRows.length} group(s)` : '';
    if (allTiers || !_edParentTierFilter) {
      hint.textContent = `Showing all ${candidates.length} device(s)${grpLabel}.`;
    } else {
      hint.textContent = `Filtered to ${_edParentTierFilter.parents.join(' / ')} tier(s) — ${candidates.length} device(s)${grpLabel}. Tick "All tiers" to widen.`;
    }
  }
}

function _edCommitParentFromInput(input) {
  const raw = (input.value || '').trim();
  if (!raw) return;
  if (_edParentLinks.length >= 16) {
    toast('Max 16 parent links', 'err');
    return;
  }
  // Helper: a group ref appears at most once (no port info to differentiate);
  // device refs may repeat — duplicate chips represent LACP / multi-NIC links.
  const _hasGroupRef = (ref) => _edParentLinks.some(l => l.pid === ref);

  // Group entries from the datalist look like "Hypervisors  ·  group (10)".
  // Detect via the embedded sentinel and strip the suffix to recover the
  // raw group name.
  const grpSfxIdx = raw.indexOf(_GRP_SFX);
  if (grpSfxIdx > 0) {
    const gname = raw.slice(0, grpSfxIdx);
    const exists = Object.values(S.devices || {}).some(
      d => (d.group || 'Default Group') === gname
    );
    if (!exists) { toast(`Group "${gname}" not found`, 'err'); return; }
    const ref = 'group:' + gname;
    if (_hasGroupRef(ref)) { toast('Group already added', 'err'); return; }
    _edParentLinks.push({ pid: ref, lport: '', rport: '' });
    input.value = '';
    _edRenderParentChips();
    return;
  }
  // Try as a device name (case-insensitive exact match).
  const lc = raw.toLowerCase();
  let match = Object.values(S.devices || {}).find(
    d => d.device_id !== _edParentDid && (d.name || '').toLowerCase() === lc
  );
  if (!match && S.devices[raw] && raw !== _edParentDid) match = S.devices[raw];
  if (match) {
    // Duplicates allowed — each chip is a distinct physical link (LACP).
    _edParentLinks.push({ pid: match.device_id, lport: '', rport: '' });
    input.value = '';
    _edRenderParentChips();
    return;
  }
  // Fall back: try as a bare group name (user typed without the suffix).
  const groupSet = new Set(Object.values(S.devices || {}).map(
    d => (d.group || 'Default Group')
  ));
  if (groupSet.has(raw)) {
    const ref = 'group:' + raw;
    if (_hasGroupRef(ref)) { toast('Group already added', 'err'); return; }
    _edParentLinks.push({ pid: ref, lport: '', rport: '' });
    input.value = '';
    _edRenderParentChips();
    return;
  }
  toast(`No device or group named "${raw}"`, 'err');
}

function _edParentInit(did) {
  _edRenderParentChips();
  _edPopulateParentDatalist();
  const input = document.getElementById('ed-parents-input');
  const allBox = document.getElementById('ed-parents-allTiers');
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        _edCommitParentFromInput(input);
      } else if (e.key === 'Backspace' && !input.value && _edParentLinks.length) {
        _edParentLinks.pop();
        _edRenderParentChips();
      }
    });
    input.addEventListener('change', () => _edCommitParentFromInput(input));
  }
  if (allBox) {
    allBox.addEventListener('change', () => _edPopulateParentDatalist());
  }
}

// Tab switcher for the Edit Device modal. Hides every .ed-tab-pane in #med
// and clears .itab-active on every .ed-tab, then shows the chosen pane and
// marks its button active. Stateless — re-clicking the same tab is a no-op.
function _edSwitchTab(name){
  document.querySelectorAll('#med .ed-tab-pane').forEach(el => el.style.display = 'none');
  document.querySelectorAll('#med .ed-tab').forEach(el => el.classList.remove('itab-active'));
  const pane = document.getElementById('ed-tab-' + name);
  if (pane) pane.style.display = '';
  const btn = document.querySelector(`#med .ed-tab[data-tab="${name}"]`);
  if (btn) btn.classList.add('itab-active');
}

// Fetch the current topology role for the device and populate the dropdown.
// Caches the roles map on window for 60s so consecutive opens skip the GET.
// On failure, falls back to '' (None). data-orig records the loaded value so
// submitEditDevice can detect a change and PUT only when needed.
async function _loadDeviceRole(did){
  const sel = document.getElementById('ed-role');
  if (!sel) return;
  let cache = window._pwRolesCache;
  const fresh = cache && (Date.now() - cache._t < 60000);
  if (!fresh){
    try {
      const r = await api('GET', '/api/topology/roles');
      cache = r.roles || {};
      cache._t = Date.now();
      window._pwRolesCache = cache;
    } catch(_) {
      cache = {};
    }
  }
  const role = (cache && cache[did]) || '';
  sel.value = role;
  sel.dataset.orig = role;
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

// v0.9.7: SNMPv3 block toggles on the Edit Device form.  Show the v3 auth
// fields only when version=3; hide community when v3 is selected.
function _edSnmpVerChange(){
  const ver = document.getElementById('ed-snmp-ver')?.value || '';
  const blk = document.getElementById('ed-v3-block');
  const comm = document.getElementById('ed-snmp-comm-row');
  if(blk) blk.style.display = (ver === '3') ? 'flex' : 'none';
  if(comm) comm.style.display = (ver === '3') ? 'none' : '';
  if(ver === '3') _edV3LevelChange();
}
function _edV3LevelChange(){
  const lvl = document.getElementById('ed-v3-level')?.value || 'noAuthNoPriv';
  const ar  = document.getElementById('ed-v3-auth-row');
  const pr  = document.getElementById('ed-v3-priv-row');
  if(ar) ar.style.display = (lvl === 'authNoPriv' || lvl === 'authPriv') ? '' : 'none';
  if(pr) pr.style.display = (lvl === 'authPriv') ? '' : 'none';
}
// Same toggles on the Add Device form (mirrored IDs with "ad-" prefix).
function _adSnmpVerChange(){
  const ver = document.getElementById('ad-snmp-ver')?.value || '';
  const blk = document.getElementById('ad-v3-block');
  const comm = document.getElementById('ad-snmp-comm-row');
  if(blk) blk.style.display = (ver === '3') ? 'flex' : 'none';
  if(comm) comm.style.display = (ver === '3') ? 'none' : '';
  if(ver === '3') _adV3LevelChange();
}
function _adV3LevelChange(){
  const lvl = document.getElementById('ad-v3-level')?.value || 'noAuthNoPriv';
  const ar  = document.getElementById('ad-v3-auth-row');
  const pr  = document.getElementById('ad-v3-priv-row');
  if(ar) ar.style.display = (lvl === 'authNoPriv' || lvl === 'authPriv') ? '' : 'none';
  if(pr) pr.style.display = (lvl === 'authPriv') ? '' : 'none';
}

async function submitEditDevice(did){
  const name  = (document.getElementById('ed-n')?.value || '').trim();
  const host  = (document.getElementById('ed-h')?.value || '').trim().replace(/^https?:\/\//,'').split('/')[0].toLowerCase();
  const group = (document.getElementById('ed-g')?.value || 'Default Group').trim();
  const site  = (document.getElementById('ed-site')?.value || '').trim();
  const alerts_muted = document.getElementById('ed-am')?.checked || false;
  const snmp_community_default = (document.getElementById('ed-snmp-comm')?.value || '').trim();
  const snmp_version_default   = document.getElementById('ed-snmp-ver')?.value || '';
  const vmware_user_default    = (document.getElementById('ed-vmw-user')?.value || '').trim();
  const vmware_password_default = document.getElementById('ed-vmw-pass')?.value || '';
  if(!name || !host){ toast('Name and host are required','err'); return; }
  const btn=document.querySelector('#med .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  // Serialize chip list → server shape. parent_device_ids is the deduped list
  // of unique pids; parent_device_ports groups port pairs by pid (multiple
  // pairs per pid = LACP / multi-link).
  const _serializedIds = [];
  const _serializedPorts = {};
  const _seenPids = new Set();
  (Array.isArray(_edParentLinks) ? _edParentLinks : []).forEach(link => {
    if (!link || typeof link.pid !== 'string' || !link.pid) return;
    if (!_seenPids.has(link.pid)) {
      _seenPids.add(link.pid);
      _serializedIds.push(link.pid);
    }
    const lp = (link.lport || '').trim();
    const rp = (link.rport || '').trim();
    if (!lp && !rp) return;  // bare chip — no wiring info to persist
    if (link.pid.indexOf('group:') === 0) return;  // group refs never carry ports
    if (!_serializedPorts[link.pid]) _serializedPorts[link.pid] = [];
    _serializedPorts[link.pid].push({ lport: lp, rport: rp });
  });
  const payload = {name, host, group, site, alerts_muted,
    snmp_community_default, snmp_version_default, vmware_user_default,
    secondary_ips: _edSecIps,
    parent_device_ids: _serializedIds,
    parent_device_ports: _serializedPorts};
  { const _edp=document.getElementById('ed-probe');
    if(_edp && _edp.value !== (S.devices[did]?.probe_id||'')) payload.probe_id=_edp.value; }
  if(vmware_password_default) payload.vmware_password_default = vmware_password_default;
  // SNMPv3 device defaults — emit only when the section is visible (version=3).
  if(snmp_version_default === '3'){
    payload.snmp_v3_user_default       = (document.getElementById('ed-v3-user')?.value || '').trim();
    payload.snmp_v3_level_default      = document.getElementById('ed-v3-level')?.value || 'noAuthNoPriv';
    payload.snmp_v3_auth_proto_default = document.getElementById('ed-v3-auth-proto')?.value || '';
    payload.snmp_v3_priv_proto_default = document.getElementById('ed-v3-priv-proto')?.value || '';
    payload.snmp_v3_context_default    = (document.getElementById('ed-v3-ctx')?.value || '').trim();
    const _ap = document.getElementById('ed-v3-auth-pass')?.value || '';
    const _pp = document.getElementById('ed-v3-priv-pass')?.value || '';
    if(_ap) payload.snmp_v3_auth_pass_default = _ap;
    if(_pp) payload.snmp_v3_priv_pass_default = _pp;
  }
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
  if(site !== (S.devices[did]?.site || '')) window._pwSitesCache = null;
  // Topology role — separate PUT only when changed. role is stored on
  // ip_allocations.kind, not on the device row, so it doesn't ride along
  // with the device PATCH.
  const roleSel = document.getElementById('ed-role');
  if (roleSel) {
    const newRole = roleSel.value || '';
    const oldRole = roleSel.dataset.orig || '';
    if (newRole !== oldRole) {
      try {
        await api('PUT', `/api/device/${did}/role`, { role: newRole });
        window._pwRolesCache = null;  // invalidate map cache
      } catch(err) {
        // Common causes: server not restarted after pull (404 on endpoint),
        // host isn't a plain IP (no IPAM allocation to tag), or no IPAM
        // subnet covers the host IP. Surface the actual error so the user
        // can diagnose without opening DevTools.
        const msg = (err && err.message) ? err.message : 'unknown error';
        toast(`Saved, but role tag failed: ${msg}`, 'err');
      }
    }
  }
  closeM('med');
  const dev = S.devices[did];
  if(dev){ dev.name = name; dev.host = host; dev.group = group; dev.site = site; dev.alerts_muted = alerts_muted; dev.snmp_community_default = snmp_community_default; dev.snmp_version_default = snmp_version_default; dev.vmware_user_default = vmware_user_default; dev.secondary_ips = _edSecIps; dev.parent_device_ids = [..._serializedIds]; dev.parent_device_ports = JSON.parse(JSON.stringify(_serializedPorts)); if(vmware_password_default) dev.has_vmware_password_default = true; renderDp(dev); }
  // Live Map runs in iframe — postMessage triggers a tree refresh so the
  // connection lines redraw without a full page reload.
  const _lf = document.getElementById('livemap-frame');
  _lf?.contentWindow?.postMessage({ type: 'lm_refresh' }, window.location.origin);
  updatePills();
  refreshGroupCounts();
  toast(`Saved: ${name}`, 'ok');
}

// ── Secondary IPs helpers ─────────────────────────────────────────────
function _edSipRender(){
  const el = document.getElementById('ed-sip-list');
  if(!el) return;
  if(!_edSecIps.length){
    el.innerHTML = '<div style="font-size:11px;color:var(--text3);padding:4px 0">No secondary IPs</div>';
  } else {
    el.innerHTML = _edSecIps.map((ip,i) =>
      `<div style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--border)">
        <span style="flex:1;font-size:12px;font-family:monospace">${esc(ip)}</span>
        <button class="btn-s" style="padding:1px 6px;font-size:11px;color:var(--down)" onclick="_edSipRemove(${i})">✕</button>
      </div>`
    ).join('');
  }
  // Update Networking tab counter chip (was the <details> summary in the pre-tab layout)
  const cnt = document.getElementById('ed-tab-net-count');
  if(cnt) cnt.textContent = _edSecIps.length ? `(${_edSecIps.length})` : '';
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

// ── License helpers ─────────────────────────────────────────────────
let _edLicenses = [];
let _edLicEditing = null;  // id of license currently being edited inline, or null

function _edLicStatusBadge(lic){
  const today = new Date(); today.setHours(0,0,0,0);
  const exp = new Date(lic.expiry_date + 'T00:00:00');
  const days = Math.ceil((exp - today) / 86400000);
  const crit = lic.crit_days || 0;
  const warn = lic.warn_days || 30;
  if(days <= crit) return `<span style="color:var(--down);font-size:10px;font-weight:600">${days < 0 ? 'Expired '+(-days)+'d ago' : 'Expired'}</span>`;
  if(days <= warn) return `<span style="color:var(--warn);font-size:10px;font-weight:600">Expiring (${days}d)</span>`;
  return `<span style="color:var(--up);font-size:10px;font-weight:600">Valid (${days}d)</span>`;
}

async function _edLicLoad(did){
  _edLicEditing = null;  // clear any stale edit state from previous modal opens
  try{
    const r = await api('GET', `/api/device/${did}/licenses`);
    _edLicenses = (r && r.licenses) || [];
  }catch(e){ _edLicenses = []; }
  _edLicRender(did);
}

function _edLicRender(did){
  const el = document.getElementById('ed-lic-list');
  const cnt = document.getElementById('ed-lic-count');
  if(cnt) cnt.textContent = _edLicenses.length ? `(${_edLicenses.length})` : '';
  const tabCnt = document.getElementById('ed-tab-lic-count');
  if(tabCnt) tabCnt.textContent = _edLicenses.length ? `(${_edLicenses.length})` : '';
  if(!el) return;
  if(!_edLicenses.length){
    el.innerHTML = '<div style="font-size:11px;color:var(--text3);padding:4px 0">No licenses</div>';
    return;
  }
  el.innerHTML = _edLicenses.map(lic => {
    if(_edLicEditing === lic.id){
      // Inline edit mode — name + note are read-only display; date/warn/crit are editable
      return `<div style="display:flex;flex-direction:column;gap:6px;padding:8px 6px;margin:4px 0;border:1px solid var(--accent);border-radius:6px;background:var(--bg3)">
        <div style="font-size:12px;font-weight:500;color:var(--text)">
          ${esc(lic.license_name)}${lic.note ? `<span style="font-size:10px;color:var(--text3);font-weight:400"> — ${esc(lic.note)}</span>` : ''}
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <input type="date" id="ed-lic-edit-date-${lic.id}" value="${esc(lic.expiry_date)}" class="lic-date-inp" style="font-size:12px"/>
          <span style="font-size:10px;color:var(--text3)">warn</span>
          <input type="number" id="ed-lic-edit-warn-${lic.id}" value="${lic.warn_days}" min="0" max="365" style="width:52px;font-size:11px"/>
          <span style="font-size:10px;color:var(--text3)">crit</span>
          <input type="number" id="ed-lic-edit-crit-${lic.id}" value="${lic.crit_days}" min="0" max="365" style="width:52px;font-size:11px"/>
          <div style="margin-left:auto;display:flex;gap:4px">
            <button class="btn-s" style="padding:1px 8px;font-size:11px" onclick="_edLicEditCancel('${esc(did)}')">Cancel</button>
            <button class="btn-p" style="padding:1px 8px;font-size:11px" onclick="_edLicEditSave(${lic.id},'${esc(did)}')">Save</button>
          </div>
        </div>
      </div>`;
    }
    // View mode
    return `<div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid var(--border)">
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500;color:var(--text)">${esc(lic.license_name)}</div>
        <div style="font-size:10px;color:var(--text3);display:flex;gap:8px;align-items:center;margin-top:2px">
          <span style="font-family:monospace">${esc(lic.expiry_date)}</span>
          ${_edLicStatusBadge(lic)}
          ${lic.note ? `<span title="${esc(lic.note)}">📝</span>` : ''}
          <span style="opacity:0.5">w:${lic.warn_days}d c:${lic.crit_days}d</span>
        </div>
      </div>
      <button class="btn-s" style="padding:1px 6px;font-size:11px" title="Edit expiry / warn / crit days" onclick="_edLicEdit(${lic.id},'${esc(did)}')">✎</button>
      <button class="btn-s" style="padding:1px 6px;font-size:11px;color:var(--down)" title="Delete license" onclick="_edLicDel(${lic.id},'${esc(did)}')">✕</button>
    </div>`;
  }).join('');
}

async function _edLicAdd(did){
  const name = document.getElementById('ed-lic-name')?.value.trim();
  const date = document.getElementById('ed-lic-date')?.value.trim();
  if(!name || !date){ toast('Name and date are required','err'); return; }
  const note = document.getElementById('ed-lic-note')?.value.trim() || '';
  const warn = parseInt(document.getElementById('ed-lic-warn')?.value) || 30;
  const crit = parseInt(document.getElementById('ed-lic-crit')?.value) || 0;
  try{
    const r = await api('POST', `/api/device/${did}/licenses`, {
      license_name: name, expiry_date: date, note, warn_days: warn, crit_days: crit
    });
    if(r && r.licenses) _edLicenses = r.licenses;
    _edLicRender(did);
    document.getElementById('ed-lic-name').value = '';
    document.getElementById('ed-lic-date').value = '';
    document.getElementById('ed-lic-note').value = '';
    toast('License added','ok');
  }catch(e){ toast('Failed to add license','err'); }
}

async function _edLicDel(licId, did){
  try{
    await api('DELETE', `/api/license/${licId}`);
    _edLicenses = _edLicenses.filter(l => l.id !== licId);
    if(_edLicEditing === licId) _edLicEditing = null;
    _edLicRender(did);
    toast('License removed','ok');
  }catch(e){ toast('Failed to delete license','err'); }
}

function _edLicEdit(licId, did){
  _edLicEditing = licId;
  _edLicRender(did);
  // Auto-focus the date field
  setTimeout(() => document.getElementById('ed-lic-edit-date-'+licId)?.focus(), 30);
}

function _edLicEditCancel(did){
  _edLicEditing = null;
  _edLicRender(did);
}

async function _edLicEditSave(licId, did){
  const lic = _edLicenses.find(l => l.id === licId);
  if(!lic){ _edLicEditing = null; _edLicRender(did); return; }
  const date = (document.getElementById('ed-lic-edit-date-'+licId)?.value || '').trim();
  if(!date){ toast('Date is required','err'); return; }
  const warn = parseInt(document.getElementById('ed-lic-edit-warn-'+licId)?.value);
  const crit = parseInt(document.getElementById('ed-lic-edit-crit-'+licId)?.value);
  const warnDays = isNaN(warn) ? 30 : Math.max(0, Math.min(365, warn));
  const critDays = isNaN(crit) ? 0  : Math.max(0, Math.min(365, crit));
  try{
    await api('PATCH', `/api/license/${licId}`, {
      license_name: lic.license_name,   // unchanged — required by API
      expiry_date:  date,
      note:         lic.note || '',     // unchanged — preserved on round-trip
      warn_days:    warnDays,
      crit_days:    critDays,
    });
    // Update in-memory copy and exit edit mode
    lic.expiry_date = date;
    lic.warn_days   = warnDays;
    lic.crit_days   = critDays;
    _edLicEditing = null;
    _edLicRender(did);
    toast('License updated','ok');
  }catch(e){ toast('Failed to update license','err'); }
}
