// ── Settings modal (General, Alerts, Database, Audit, Sensors, Networking) ─

function _fmtTtl(s){
  s=parseInt(s)||86400;
  if(s<60)  return s+'s';
  if(s<3600)return Math.round(s/60)+'m';
  const h=s/3600;
  return (h===Math.floor(h)?h:Math.round(h*10)/10)+'h';
}

function _renderCertSection(tr){
  const c=tr.cert||{};
  let infoHtml;
  if(tr.csr_pending){
    infoHtml=`<div style="padding:10px 12px;background:rgba(240,165,0,.1);border:1px solid rgba(240,165,0,.3);border-radius:6px;font-size:12px;color:var(--warn)">
      <strong>CSR Pending</strong> — A Certificate Signing Request has been generated and the private key is stored. Upload the signed certificate from your CA to complete the installation.
    </div>`;
  } else if(!c.subject){
    infoHtml='<div style="font-size:12px;color:var(--text3)">No certificate loaded. Enable HTTPS and save — a self-signed certificate will be generated automatically on the next startup.</div>';
  } else {
    const daysLeft=c.days_left??0;
    const badgeColor=daysLeft<0?'var(--err)':daysLeft<=30?'var(--warn)':'var(--ok)';
    const badgeTxt=daysLeft<0?'EXPIRED':(daysLeft<=30?`⚠ ${daysLeft}d left`:`✓ ${daysLeft}d`);
    const srcLabel={'generated':'Auto-generated (self-signed)','imported':'Imported from certs/ folder','uploaded':'Manually uploaded','db':'Loaded from database'}[c.source]||c.source||'—';
    infoHtml=`<div style="display:grid;grid-template-columns:130px 1fr;gap:5px 10px;font-size:12px">
      <span style="color:var(--text3)">Subject</span><span>${esc(c.subject||'—')}</span>
      <span style="color:var(--text3)">Issuer</span><span>${esc(c.issuer||'—')}${c.self_signed?' <span style="color:var(--text3)">(self-signed)</span>':''}</span>
      <span style="color:var(--text3)">Expires</span><span>${esc(c.not_after||'—')} <span style="color:${badgeColor};font-weight:600">${badgeTxt}</span></span>
      <span style="color:var(--text3)">Source</span><span>${esc(srcLabel)}</span>
    </div>`;
  }
  const btnsHtml=tr.csr_pending?`
    <button class="btn-p" style="font-size:12px" onclick="openInstallSigned()">Install Signed Certificate</button>
    <button class="btn-s" onclick="openGenerateCSR()">Regenerate CSR</button>
  `:`
    <button class="btn-s" onclick="openUploadCert()">Upload Certificate</button>
    <button class="btn-s" onclick="openGenerateCSR()">Generate CSR</button>
    <button class="btn-s" id="btn-gen-cert" onclick="generateNewCert()">Generate Self-Signed</button>
  `;
  return `<div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:10px">Certificate</div>
    ${infoHtml}
    <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">${btnsHtml}</div>`;
}

async function _refreshCertSection(){
  const sec=document.getElementById('net-cert-section');
  if(!sec) return;
  const tr=await api('GET','/api/tls');
  window._tlsSettings={...window._tlsSettings,...tr};
  sec.innerHTML=_renderCertSection(tr);
}

async function openSettings(){
  _stopLogLive();
  closeM('mset');
  const [sr, ur, tr] = await Promise.all([
    api('GET','/api/settings'),
    api('GET','/api/users'),
    api('GET','/api/tls'),
  ]);
  window._tlsSettings = {...tr, org_name: sr.org_name||''};
  const o=document.createElement('div'); o.className='mo'; o.id='mset';
  _overlayClose(o, ()=>{_stopLogLive();closeM('mset');});
  // Pre-compute backup tab values so the template stays single-level
  const _bkFreq = sr.backup_sched_freq || 'daily';
  const _bkDaysActive = (_bkFreq === 'weekly') ? '' : 'none';
  const _bkDaysSaved = String(sr.backup_sched_days || '1,2,3,4,5,6,7').split(',').map(d => d.trim());
  const _bkDayLabels = [['1','Mon'],['2','Tue'],['3','Wed'],['4','Thu'],['5','Fri'],['6','Sat'],['7','Sun']];
  const _bkDaysHtml = _bkDayLabels.map(([v,l]) =>
    '<label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--text2);cursor:pointer">' +
    '<input type="checkbox" id="st-bk-d' + v + '" value="' + v + '"' + (_bkDaysSaved.includes(v) ? ' checked' : '') + '> ' + l + '</label>'
  ).join('');
  // Port Scanner section pre-compute
  const _SCAN_PORT_DEFS = [
    {key:'ping',  label:'Ping'},        {key:'21',    label:'FTP 21'},
    {key:'22',    label:'SSH 22'},      {key:'25',    label:'SMTP 25'},
    {key:'53',    label:'DNS 53'},      {key:'80',    label:'HTTP 80'},
    {key:'443',   label:'HTTPS 443'},   {key:'3389',  label:'RDP 3389'},
    {key:'3306',  label:'MySQL 3306'},  {key:'5432',  label:'PgSQL 5432'},
    {key:'6379',  label:'Redis 6379'},  {key:'27017', label:'MongoDB 27017'},
    {key:'389',   label:'LDAP 389'},    {key:'8080',  label:'HTTP-Alt 8080'},
    {key:'8443',  label:'HTTPS-Alt 8443'},
  ];
  const _scanActive = new Set(
    String(sr.scan_ports || 'ping,21,22,25,53,80,443,3389,3306,5432,6379,27017,389,8080,8443')
      .split(',').map(s => s.trim()).filter(Boolean)
  );
  const _scanDefKeys  = new Set(_SCAN_PORT_DEFS.map(d => d.key));
  const _scanCustom   = [..._scanActive].filter(k => !_scanDefKeys.has(k)).join(', ');
  const _scanPortsHtml = _SCAN_PORT_DEFS.map(({key, label}) =>
    `<label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);cursor:pointer">` +
    `<input type="checkbox" class="st-scan-port" value="${key}"${_scanActive.has(key) ? ' checked' : ''}> ${label}</label>`
  ).join('');
  const _dbkFreq = sr.db_backup_freq || 'daily';
  const _dbkDaysActive = (_dbkFreq === 'weekly') ? '' : 'none';
  const _dbkDaysSaved = String(sr.db_backup_days || '1,2,3,4,5,6,7').split(',').map(d => d.trim());
  const _dbkDaysHtml = _bkDayLabels.map(([v,l]) =>
    '<label style="display:flex;align-items:center;gap:5px;font-size:12px;color:var(--text2);cursor:pointer">' +
    '<input type="checkbox" id="st-dbk-d' + v + '" value="' + v + '"' + (_dbkDaysSaved.includes(v) ? ' checked' : '') + '> ' + l + '</label>'
  ).join('');
  o.innerHTML=`
  <div class="mbox" style="width:1020px;max-width:96vw;height:85vh;display:flex;flex-direction:column">
    <div class="mhd">
      <div class="mttl">⚙ Settings</div>
      <button class="mclose" onclick="_stopLogLive();closeM('mset')">✕</button>
    </div>
    <div class="stab-layout">
    <nav class="stab-sidebar">
      <button class="stab-nav active" id="stab-btn-general" onclick="switchSettingsTab('general')">⚙️ General</button>
      <button class="stab-nav" id="stab-btn-users" onclick="switchSettingsTab('users')">👤 Users</button>
      <button class="stab-nav" id="stab-btn-groups" onclick="switchSettingsTab('groups')">👥 Groups</button>
      <button class="stab-nav" id="stab-btn-integrations" onclick="switchSettingsTab('integrations')">🔗 Integrations</button>
      <button class="stab-nav" id="stab-btn-database" onclick="switchSettingsTab('database')">🗄️ Database</button>
      <button class="stab-nav" id="stab-btn-logs" onclick="switchSettingsTab('logs')">📜 Logs</button>
      <button class="stab-nav" id="stab-btn-sensors" onclick="switchSettingsTab('sensors')">📡 Sensors</button>
      <button class="stab-nav" id="stab-btn-networking" onclick="switchSettingsTab('networking')">🌐 Networking</button>
      <button class="stab-nav" id="stab-btn-backup" onclick="switchSettingsTab('backup')">💾 Config Backup</button>
      <button class="stab-nav" id="stab-btn-alert-rules" onclick="switchSettingsTab('alert-rules')">🚨 Alert Rules</button>
    </nav>
    <div class="stab-content">
    <div class="mbdy stab-fade" id="stab-general" style="overflow-y:auto;flex:1">
      <div class="fr">
        <label class="fl">Session Timeout (seconds)</label>
        <input type="number" id="st-ttl" value="${sr.session_ttl||86400}" min="60" style="max-width:180px"/>
        <div id="st-ttl-hint" style="font-size:11px;color:var(--text3);margin-top:5px">Current: ${_fmtTtl(sr.session_ttl||86400)} — takes effect on next login</div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Data Retention</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">Raw Samples (days)</label>
            <input type="number" id="st-ret-raw" value="${sr.retention_raw_days||7}" min="1" max="365" style="max-width:100px"/>
            <div style="font-size:11px;color:var(--text3);margin-top:3px">Full-resolution probe data (default: 7)</div></div>
          <div class="fr"><label class="fl">5-Min Aggregates (days)</label>
            <input type="number" id="st-ret-5m" value="${sr.retention_5m_days||90}" min="7" max="1825" style="max-width:100px"/>
            <div style="font-size:11px;color:var(--text3);margin-top:3px">5-minute rollups (default: 90)</div></div>
          <div class="fr"><label class="fl">Hourly Aggregates (days)</label>
            <input type="number" id="st-ret-1h" value="${sr.retention_1h_days||1095}" min="30" max="3650" style="max-width:120px"/>
            <div style="font-size:11px;color:var(--text3);margin-top:3px">Hourly rollups for long-term history (default: 1095 / 3 years)</div></div>
        </div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <label class="fl">Probe Workers</label>
        <input type="number" id="st-mw" value="${sr.max_workers_executor||''}" min="4" max="512" placeholder="Auto" style="max-width:100px"/>
        <div style="font-size:11px;color:var(--text3);margin-top:5px">Leave blank for auto-scaling (currently: ${sr.max_workers_executor_effective||64} workers). Set 4–512 to override.</div>
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
      <div class="fr" style="margin-top:16px">
        <div class="fl" style="margin-bottom:10px">Server Info</div>
        <div class="st-info-grid">
          <span class="st-info-key">Port</span><span class="st-info-val">${sr.port}</span>
          <span class="st-info-key">Address</span><span class="st-info-val">${sr.bind}</span>
          <span class="st-info-key">Database</span><span class="st-info-val">${esc(sr.db_path||'')}</span>
        </div>
      </div>
      <div class="fr" style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Server Controls</div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn-p" style="font-size:12px;padding:7px 16px" onclick="serverRestart()">&#x21BA; Restart Server</button>
          <button class="btn-danger" style="font-size:12px;padding:7px 16px" onclick="serverShutdown()">&#x23FB; Shutdown Server</button>
        </div>
        <div class="fh" style="margin-top:8px">Restart applies pending settings changes. Shutdown stops the server process entirely.</div>
      </div>
    </div>
    <div class="mbdy stab-fade" id="stab-users" style="display:none;padding-top:8px;overflow-y:auto;flex:1">
      <div id="userTableWrap">${renderUserTable(ur.users||[])}</div>
      <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn-p" style="font-size:12px;padding:7px 14px" onclick="openAddUser()">＋ Add User</button>
        <button class="btn-s" style="font-size:12px;padding:7px 14px" onclick="openLdapSettings()">🔐 LDAP Settings</button>
      </div>
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
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
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:10px">Login Security</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">Max login attempts</label>
            <input type="number" id="st-fail-max" value="${sr.login_fail_max||5}" min="1" max="100" style="max-width:100px"/>
            <div class="fh">Attempts before lockout</div></div>
          <div class="fr"><label class="fl">Lockout window (s)</label>
            <input type="number" id="st-fail-win" value="${sr.login_fail_window||60}" min="10" max="3600" style="max-width:100px"/>
            <div class="fh">Window to count failed attempts</div></div>
        </div>
      </div>
    </div>
    <div class="mbdy stab-fade" id="stab-groups" style="display:none;overflow-y:auto;flex:1">
      <div class="alrt-panel-hdr" style="margin-bottom:10px">
        <span style="color:var(--text3);font-size:12px">Manage alert recipient groups. Assign users to groups and use groups in alert rule email actions.</span>
        <button class="btn-p rbac-admin" style="font-size:12px;padding:5px 12px" onclick="_groupsOpenEditor(null)">＋ New Group</button>
      </div>
      <div id="group-list"><div class="alrt-loading">Loading…</div></div>
    </div>
    <div class="mbdy stab-fade" id="stab-integrations" style="display:none;overflow-y:auto;flex:1">
      <!-- Sub-tab bar -->
      <div style="display:flex;gap:6px;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border)">
        <button id="itab-smtp" class="itab itab-active" onclick="switchIntegTab('smtp')">📧 SMTP <span id="ibadge-smtp" style="font-size:13px"></span></button>
        <button id="itab-syslog" class="itab" onclick="switchIntegTab('syslog')">📤 Syslog <span id="ibadge-syslog" style="font-size:13px"></span></button>
      </div>

      <!-- ── SMTP sub-panel ── -->
      <div id="ipanel-smtp">
        <div id="smtp-status-bar"></div>
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
            <input type="text" id="st-smtp-from" value="${sr.smtp_from||''}" placeholder="pingwatch@yourdomain.com"/></div>
          <div class="fr"><label class="fl">To</label>
            <input type="text" id="st-smtp-to"   value="${sr.smtp_to||''}"   placeholder="alerts@yourdomain.com"/></div>
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
          Emails are sent on sensor DOWN and RECOVERED events.
        </div>
      </div>

      <!-- ── Syslog sub-panel ── -->
      <div id="ipanel-syslog" style="display:none">
        <div id="syslog-status-bar"></div>
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:16px">Alert Event Forwarding</div>
        <div class="fr" style="margin-top:0">
          <label class="fl">Server IP / Hostname</label>
          <input type="text" id="st-sl-host" value="${esc(sr.syslog_host||'')}" placeholder="192.168.1.100 or syslog.example.com" style="max-width:280px"/>
          <div class="fh">Syslog or SIEM server address</div>
        </div>
        <div class="fr" style="margin-top:14px">
          <label class="fl">Port</label>
          <input type="number" id="st-sl-port" value="${sr.syslog_port||514}" min="1" max="65535" style="max-width:120px"/>
          <div class="fh">Default: 514</div>
        </div>
        <div class="fr" style="margin-top:14px">
          <label class="fl">Protocol</label>
          <select id="st-sl-proto" style="max-width:120px">
            <option value="udp" ${(sr.syslog_proto||'udp')==='udp'?'selected':''}>UDP</option>
            <option value="tcp" ${(sr.syslog_proto||'udp')==='tcp'?'selected':''}>TCP</option>
          </select>
          <div class="fh">UDP is standard for syslog; use TCP for reliable delivery</div>
        </div>
        <div class="fr" style="margin-top:14px">
          <label class="fl">Minimum Severity</label>
          <select id="st-sl-minsev" style="max-width:160px">
            <option value="critical" ${(sr.syslog_min_severity||'warning')==='critical'?'selected':''}>Critical only</option>
            <option value="warning"  ${(sr.syslog_min_severity||'warning')==='warning'?'selected':''}>Warning and above</option>
            <option value="info"     ${(sr.syslog_min_severity||'warning')==='info'?'selected':''}>All events</option>
          </select>
          <div class="fh">Events below this severity are not forwarded</div>
        </div>
        <div style="margin-top:16px;padding:10px 12px;background:var(--bg3);border-radius:6px;font-size:12px;color:var(--text3);line-height:1.5">
          Messages are sent in <strong style="color:var(--text2)">RFC 5424</strong> format with facility LOCAL0.
          Forwarding is non-blocking — syslog errors will not affect monitoring.
        </div>

        <!-- Application Log Forwarding -->
        <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
          <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:14px">Application Log Forwarding</div>
          <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
            <div style="flex:1">
              <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Forward Application Logs</div>
              <div class="fh" style="margin:0">Send PingWatch application log entries to the syslog server (facility LOCAL1)</div>
            </div>
            <label class="toggle" style="flex-shrink:0"><input type="checkbox" id="st-sl-applogs" ${sr.syslog_app_logs?'checked':''}><span class="tsl"></span></label>
          </div>
          <div class="fr" style="margin-top:14px">
            <label class="fl">Minimum Level</label>
            <select id="st-sl-loglevel" style="max-width:140px">
              <option value="debug"   ${(sr.syslog_app_log_level||'info')==='debug'  ?'selected':''}>DEBUG</option>
              <option value="info"    ${(sr.syslog_app_log_level||'info')==='info'   ?'selected':''}>INFO</option>
              <option value="warning" ${(sr.syslog_app_log_level||'info')==='warning'?'selected':''}>WARNING</option>
              <option value="error"   ${(sr.syslog_app_log_level||'info')==='error'  ?'selected':''}>ERROR</option>
            </select>
            <div class="fh">Only log entries at or above this level are forwarded</div>
          </div>
          <div class="fr" style="margin-top:14px">
            <label class="fl">Log Sources</label>
            <div style="display:flex;gap:20px;flex-wrap:wrap">
              <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);cursor:pointer">
                <input type="checkbox" id="st-sl-src-app" ${(sr.syslog_app_log_sources||[]).includes('app')?'checked':''}> Application</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);cursor:pointer">
                <input type="checkbox" id="st-sl-src-audit" ${(sr.syslog_app_log_sources||[]).includes('audit')?'checked':''}> Audit</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text2);cursor:pointer">
                <input type="checkbox" id="st-sl-src-backup" ${(sr.syslog_app_log_sources||[]).includes('backup')?'checked':''}> Backup</label>
            </div>
            <div class="fh">Requires syslog forwarding to be enabled and configured above</div>
          </div>
        </div>
      </div>
    </div>
    <div class="mft" id="stab-footer-general">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSettings()">Save Settings</button>
    </div>
    <div class="mft" id="stab-footer-users" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSecuritySettings()">Save Security</button>
    </div>
    <div class="mft" id="stab-footer-groups" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
    </div>
    <div class="mft" id="stab-footer-integrations" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button id="integ-btn-test" class="btn-s" onclick="testSmtp()" style="display:none">Send Test Email</button>
      <button id="integ-btn-test-syslog" class="btn-s" onclick="testSyslog()" style="display:none">Send Test Message</button>
      <button id="integ-btn-save" class="btn-p" onclick="_saveIntegrations()">Save</button>
    </div>
    <div class="mbdy stab-fade" id="stab-database" style="display:none;overflow-y:auto;flex:1">

      <!-- Database Backend -->
      <div id="db-backend-section" style="border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:10px">Database Backend</div>
        <div id="db-backend-info" style="font-size:12px;color:var(--text3)">Loading...</div>
      </div>

      <!-- Main DB -->
      <div style="border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:10px">Main Database</div>
        <div id="db-stats-main" style="font-size:12px;color:var(--text3);margin-bottom:10px">Loading…</div>
        <button class="btn-p" style="font-size:12px;padding:6px 14px" onclick="exportDb()">&#8681; Download Main DB</button>
        <span style="font-size:11px;color:var(--text3);margin-left:8px">Config, devices, sensors, users, settings</span>
      </div>

      <!-- Logs DB -->
      <div style="border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:10px">Sensor Logs Database</div>
        <div id="db-stats-logs" style="font-size:12px;color:var(--text3);margin-bottom:10px">Loading…</div>
        <button class="btn-s" style="font-size:12px;padding:6px 14px" onclick="exportLogsDb()">&#8681; Download Logs DB</button>
        <span style="font-size:11px;color:var(--text3);margin-left:8px">Sensor samples, flap log, SNMP traps, errors</span>
      </div>

      <!-- Bundle Export + Import -->
      <div style="border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:10px">Export / Import</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <button class="btn-s" style="font-size:12px;padding:6px 14px" onclick="exportBundle()">&#8681; Export Full Bundle (ZIP)</button>
          <button class="btn-s" style="font-size:12px;padding:6px 14px" onclick="importDb()">&#8679; Import DB / Bundle</button>
          <span id="db-import-status" style="font-size:12px;color:var(--text3)"></span>
        </div>
        <div class="fh" style="margin-top:8px">Bundle ZIP contains both DBs. Import accepts Main DB, Logs DB, or a bundle ZIP.<br><span style="color:var(--down)">Warning: import replaces the uploaded DB and restarts the server.</span></div>
      </div>

      <div style="margin-top:4px;padding-top:16px;border-top:1px solid var(--border)">
        <div onclick="_toggleDbBackup()" style="display:flex;align-items:center;justify-content:space-between;cursor:pointer;user-select:none">
          <div style="font-size:12px;font-weight:600;color:var(--text2)">Scheduled Database Backup</div>
          <span id="dbk-chevron" style="font-size:10px;color:var(--text3);transition:transform .2s;transform:rotate(-90deg)">&#9660;</span>
        </div>
        <div id="dbk-collapse" style="display:none;margin-top:16px">
        <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
          <div style="flex:1">
            <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Enable Scheduled Backup</div>
            <div class="fh" style="margin:0">Automatically backup the database on a schedule — saved to backup/database/</div>
          </div>
          <label class="toggle" style="flex-shrink:0"><input type="checkbox" id="st-dbk-enabled" ${sr.db_backup_enabled?'checked':''}><span class="tsl"></span></label>
        </div>
        <div class="fr" style="margin-top:14px">
          <label class="fl">Frequency</label>
          <select id="st-dbk-freq" style="max-width:160px" onchange="_dbkFreqChange()">
            <option value="daily" ${_dbkFreq==='daily'?'selected':''}>Daily</option>
            <option value="weekly" ${_dbkFreq==='weekly'?'selected':''}>Weekly</option>
          </select>
        </div>
        <div class="fr" style="margin-top:14px;display:${_dbkDaysActive}" id="st-dbk-days-row">
          <label class="fl">Days</label>
          <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px">${_dbkDaysHtml}</div>
        </div>
        <div class="fr" style="margin-top:14px">
          <label class="fl">Backup Time</label>
          <input type="time" id="st-dbk-time" value="${sr.db_backup_time||'03:00'}" style="max-width:140px"/>
          <div class="fh">Server local time (24h)</div>
        </div>
        <div style="margin-top:18px;padding-top:16px;border-top:1px solid var(--border)">
          <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
            <div style="flex:1">
              <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Backups to Keep</div>
              <div class="fh" style="margin:0">Oldest backup files are deleted when limit is exceeded</div>
            </div>
            <input type="number" id="st-dbk-keep" min="1" max="50" value="${sr.db_backup_keep!=null?sr.db_backup_keep:7}" style="width:70px;flex-shrink:0;text-align:center"/>
          </div>
        </div>
        <div style="margin-top:14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn-s" style="font-size:12px;padding:7px 14px" onclick="runDbBackupNow()">&#x25B6; Run Backup Now</button>
          <span id="dbk-run-result" style="font-size:12px;color:var(--text3)"></span>
        </div>
        <div id="dbk-last-info" style="margin-top:6px;font-size:11px;color:var(--text3)">${sr.db_backup_last_ts?`Last backup: ${esc(sr.db_backup_last_ts)} \u2014 ${esc(sr.db_backup_last_result)}`:''}</div>
        </div><!-- /dbk-collapse -->
      </div>
    </div>
    <div class="mft" id="stab-footer-database" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveDbBackupSettings()">Save DB Backup</button>
    </div>
    <div class="mbdy stab-fade" id="stab-logs" style="display:none;padding:0;overflow-y:auto;flex:1">
      <div style="padding:10px 14px 6px;border-bottom:1px solid var(--border)">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
          <input type="checkbox" id="st-debug-mode" ${sr.debug_mode?'checked':''}/>
          <span style="font-size:12px;font-weight:600;color:var(--text2)">Debug Mode</span>
        </label>
        <div class="fh" style="margin-top:4px">Enable verbose debug logging. When off, only INFO and above is written to log files.</div>
      </div>
      <div class="log-subtab-bar">
        <button class="log-stab active" id="lstab-btn-app"     onclick="_switchLogTab('app')">Application</button>
        <button class="log-stab"        id="lstab-btn-sensors" onclick="_switchLogTab('sensors')">Sensors</button>
        <button class="log-stab"        id="lstab-btn-audit"   onclick="_switchLogTab('audit')">Audit</button>
        <button class="log-stab"        id="lstab-btn-backup"  onclick="_switchLogTab('backup')">Backup</button>
        <div style="margin-left:auto;display:flex;gap:4px;align-items:center">
          <button class="btn-s log-live-btn" id="logLiveBtn" onclick="_toggleLogLive()" style="font-size:11px;padding:4px 10px">\u25cb Live</button>
          <button class="btn-s" onclick="_loadLogTab()" style="font-size:11px;padding:4px 10px">\u21bb Refresh</button>
        </div>
      </div>
      <div class="log-filter-bar">
        <select id="logFTime" onchange="_onLogFilterChange()">
          <option value="all" selected>All time</option>
          <option value="5m">Last 5 min</option>
          <option value="15m">Last 15 min</option>
          <option value="1h">Last 1 hour</option>
          <option value="24h">Last 24 hours</option>
        </select>
        <select id="logFLevel" onchange="_onLogFilterChange()">
          <option value="">All Levels</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
        <input id="logFSearch" type="search" placeholder="Search logs\u2026 (level:error device:FortiGate)" oninput="_onLogFilterChange()" class="log-search">
        <button class="log-clear-btn" onclick="_clearLogFilters()" title="Clear all filters">\u2715</button>
        <div class="log-export-group">
          <button class="log-export-btn" onclick="_exportLogCsv()" title="Export as CSV">\u2b07 CSV</button>
          <button class="log-export-btn" onclick="_exportLogJson()" title="Export as JSON">\u2b07 JSON</button>
        </div>
      </div>
      <div id="log-body" class="log-viewer"><span style="color:var(--text3)">Loading\u2026</span></div>
    </div>
    <div class="mft" id="stab-footer-logs" style="display:none">
      <span id="log-footer-label" style="font-size:11px;color:var(--text3)">Loading\u2026</span>
    </div>
    <div class="mbdy stab-fade" id="stab-sensors" style="display:none;overflow-y:auto;flex:1">
      <div style="padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid var(--border)">
        <div class="fl" style="margin-bottom:6px">Global Defaults</div>
        <div class="fh" style="margin-bottom:10px">Fallback values applied to all new sensors — override per type below.</div>
        <div class="fgrid">
          <div class="fr"><label class="fl">Interval (s)</label>
            <input type="number" id="st-snr-iv" value="${sr.snr_interval||5}" min="1" max="300" style="max-width:100px"/></div>
          <div class="fr"><label class="fl">Timeout (s)</label>
            <input type="number" id="st-snr-tmo" value="${sr.snr_timeout||4}" min="1" max="60" style="max-width:100px"/></div>
        </div>
      </div>
      <div id="sdrTabBody"><div style="color:var(--text3);font-size:12px;padding:8px">Loading…</div></div>
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="fl" style="margin-bottom:4px">Port Scanner</div>
        <div class="fh" style="margin-bottom:10px">Choose which ports are probed when you click "Scan" on a device. Custom ports use a TCP probe.</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:6px;margin-bottom:10px">
          ${_scanPortsHtml}
        </div>
        <div class="fr" style="margin-bottom:6px">
          <label class="fl">Custom ports <span class="fh" style="font-weight:400">(comma-separated, e.g. 9200, 8888)</span></label>
          <input type="text" id="st-scan-custom" value="${_scanCustom}" placeholder="9200, 8888, …" style="width:100%"/>
        </div>
        <div style="display:flex;justify-content:flex-end">
          <button class="btn-s" onclick="_scanPortsReset()">Reset to Defaults</button>
        </div>
      </div>
    </div>
    <div class="mft" id="stab-footer-sensors" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSensorTypeDefaults()">Save Sensor Defaults</button>
    </div>
    <div class="mbdy stab-fade" id="stab-networking" style="display:none;overflow-y:auto;flex:1">
      <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:12px">Server Ports</div>
      <div class="fr">
        <label class="fl">HTTP Port</label>
        <input type="number" id="st-http-port" value="${sr.http_port||7070}" min="1" max="65535" style="max-width:120px"/>
        <div class="fh">Port the web interface listens on (HTTP). When HTTPS is enabled this port can optionally redirect to HTTPS.</div>
      </div>
      <div class="fr" style="margin-top:12px">
        <label class="fl">SNMP Trap Port</label>
        <input type="number" id="st-snmp-port" value="${sr.snmp_port||162}" min="1" max="65535" style="max-width:120px"/>
        <div class="fh">UDP port for SNMP trap reception. Falls back to 1162 then 2162 if binding fails.</div>
      </div>

      <div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border)">
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:12px">HTTPS / TLS</div>
        <div class="fr">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
            <input type="checkbox" id="st-tls-enabled" ${tr.tls_enabled?'checked':''}>
            <span class="fl" style="margin:0">Enable HTTPS</span>
          </label>
          <div class="fh">Restart required to take effect. Self-signed certificates will show a browser warning — install a CA-signed certificate for production use.</div>
        </div>
        <div class="fr" style="margin-top:12px">
          <label class="fl">HTTPS Port</label>
          <input type="number" id="st-tls-port" value="${tr.tls_port||8443}" min="1" max="65535" style="max-width:120px"/>
          <div class="fh">Default: 8443. Port 443 requires admin/root privileges.</div>
        </div>
        <div class="fr" style="margin-top:12px">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
            <input type="checkbox" id="st-http-redirect" ${tr.http_redirect?'checked':''}>
            <span class="fl" style="margin:0">Redirect HTTP → HTTPS</span>
          </label>
          <div class="fh">When enabled, a redirect server runs on the HTTP port and sends browsers to HTTPS automatically.</div>
        </div>

        <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)" id="net-cert-section">
          ${_renderCertSection(tr)}
        </div>
      </div>

      <div style="margin-top:16px;padding:10px;background:var(--bg3);border-radius:6px;font-size:12px;color:var(--warn)">
        Port changes and HTTPS toggle require a server restart to take effect.
      </div>
    </div>
    <div class="mft" id="stab-footer-networking" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveNetworkingSettings()">Save Networking</button>
    </div>
    <div class="mbdy stab-fade" id="stab-backup" style="display:none;overflow-y:auto;flex:1">
      <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:16px">Global Backup Schedule</div>
      <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
        <div style="flex:1">
          <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Enable Scheduled Backups</div>
          <div class="fh" style="margin:0">Run config backups automatically at the specified time</div>
        </div>
        <label class="toggle" style="flex-shrink:0"><input type="checkbox" id="st-bk-enabled" ${sr.backup_sched_enabled?'checked':''}><span class="tsl"></span></label>
      </div>
      <div class="fr" style="margin-top:14px" id="st-bk-freq-row">
        <label class="fl">Frequency</label>
        <select id="st-bk-freq" style="max-width:160px" onchange="_bkFreqChange()">
          <option value="daily" ${_bkFreq==='daily'?'selected':''}>Daily</option>
          <option value="weekly" ${_bkFreq==='weekly'?'selected':''}>Weekly</option>
        </select>
      </div>
      <div class="fr" style="margin-top:14px;display:${_bkDaysActive}" id="st-bk-days-row">
        <label class="fl">Days</label>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px">${_bkDaysHtml}</div>
      </div>
      <div class="fr" style="margin-top:14px">
        <label class="fl">Backup Time</label>
        <input type="time" id="st-bk-time" value="${sr.backup_sched_time||'02:00'}" style="max-width:140px"/>
        <div class="fh">Server local time (24h)</div>
      </div>
      <div style="margin-top:18px;padding-top:16px;border-top:1px solid var(--border)">
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:16px">Retention</div>
        <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
          <div style="flex:1">
            <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Configs to Keep per Device</div>
            <div class="fh" style="margin:0">Oldest config file and DB entry are deleted when the limit is exceeded</div>
          </div>
          <input type="number" id="st-bk-keep" min="1" max="50" value="${sr.backup_keep!=null?sr.backup_keep:3}" style="width:70px;flex-shrink:0;text-align:center"/>
        </div>
      </div>
      <div style="margin-top:16px;padding:10px 12px;background:var(--bg3);border-radius:6px;font-size:12px;color:var(--text3);line-height:1.5">
        Enable individual devices via <strong style="color:var(--text2)">Device Config Backup → Configure</strong> using the "Add to Scheduled Backup" toggle.
      </div>
    </div>
    <div class="mft" id="stab-footer-backup" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveBackupScheduleSettings()">Save Config Backup</button>
    </div>
    <div class="mbdy stab-fade" id="stab-alert-rules" style="display:none;overflow-y:auto;flex:1">
      <div class="alrt-panel-hdr">
        <span style="color:var(--text3);font-size:12px">Rules are evaluated in order for every sensor event.</span>
        <button class="btn-p rbac-admin" onclick="_alertingOpenEditor(null)">＋ New Rule</button>
      </div>
      <div id="alrt-list"><div class="alrt-loading">Loading…</div></div>
      <div style="margin:16px 0 8px;padding-top:16px;border-top:1px solid var(--border)">
        <div class="alrt-panel-hdr">
          <div>
            <div style="font-size:13px;font-weight:600;color:var(--text2)">🛠 Maintenance Windows</div>
            <div style="font-size:12px;color:var(--text3);margin-top:2px">Suppress notifications during scheduled maintenance. Rules still evaluate.</div>
          </div>
          <button class="btn-p rbac-admin" onclick="_alertMaintOpen(null)">＋ New Window</button>
        </div>
        <div id="alrt-maint-list"><div class="alrt-loading">Loading…</div></div>
      </div>
    </div>
    <div class="mft" id="stab-footer-alert-rules" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
    </div>
    </div><!-- /stab-content -->
    </div><!-- /stab-layout -->
  </div>`;
  document.body.appendChild(o);
}

let _stabSwitching = false;
function switchSettingsTab(tab){
  if (_stabSwitching) return;
  const tabs = ['general','users','groups','integrations','database','logs','sensors','networking','backup','alert-rules'];

  // Find currently visible tab
  let cur = null;
  tabs.forEach(t => { if (document.getElementById(`stab-${t}`).style.display !== 'none') cur = t; });
  if (cur === tab) return;

  // Update tab buttons immediately (feels responsive)
  tabs.forEach(t => document.getElementById(`stab-btn-${t}`).classList.toggle('active', t === tab));

  const curEl  = cur ? document.getElementById(`stab-${cur}`) : null;
  const nextEl = document.getElementById(`stab-${tab}`);
  const mbox   = nextEl.closest('.mbox');

  _stabSwitching = true;
  if (curEl) {
    // Phase 1: fade out current tab
    curEl.classList.add('stab-out');
    setTimeout(() => {
      // Phase 2: swap content
      curEl.style.display = 'none';
      curEl.classList.remove('stab-out');
      if (cur) document.getElementById(`stab-footer-${cur}`).style.display = 'none';

      nextEl.style.display = '';
      nextEl.classList.add('stab-out');
      document.getElementById(`stab-footer-${tab}`).style.display = '';

      // Fade in
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          nextEl.classList.remove('stab-out');
          setTimeout(() => {
            _stabSwitching = false;
            if (tab === 'logs')         _loadLogTab();
            if (tab === 'sensors')      loadSensorsDefaultsTab();
            if (tab === 'backup')       _loadBackupScheduleSettings();
            if (tab === 'database')     _loadDbBackupSettings();
            if (tab === 'alert-rules')  { _alertingLoadRules(); _alertingLoadMaint(); }
            if (tab === 'groups')       _groupsLoad();
            if (tab === 'integrations') _loadIntegrationsStatus();
          }, 220);
        });
      });
    }, 200);
  } else {
    nextEl.style.display = '';
    document.getElementById(`stab-footer-${tab}`).style.display = '';
    _stabSwitching = false;
    if (tab === 'logs')        _loadLogTab();
    if (tab === 'sensors')     loadSensorsDefaultsTab();
    if (tab === 'backup')      _loadBackupScheduleSettings();
    if (tab === 'database')    _loadDbBackupSettings();
    if (tab === 'alert-rules') _alertingLoadRules();
    if (tab === 'maint')        _alertingLoadMaint();
    if (tab === 'groups')       _groupsLoad();
    if (tab === 'integrations') _loadIntegrationsStatus();
  }
}

async function saveNetworkingSettings(){
  const httpPort=parseInt(document.getElementById('st-http-port')?.value);
  const snmpPort=parseInt(document.getElementById('st-snmp-port')?.value);
  const tlsEnabled=document.getElementById('st-tls-enabled')?.checked||false;
  const tlsPort=parseInt(document.getElementById('st-tls-port')?.value)||8443;
  const httpRedirect=document.getElementById('st-http-redirect')?.checked||false;
  if(!httpPort||httpPort<1||httpPort>65535){toast('HTTP port must be 1–65535','err');return;}
  if(!snmpPort||snmpPort<1||snmpPort>65535){toast('SNMP port must be 1–65535','err');return;}
  if(!tlsPort||tlsPort<1||tlsPort>65535){toast('HTTPS port must be 1–65535','err');return;}
  const btn=document.querySelector('#stab-footer-networking .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  let r1,r2;
  try{
    [r1,r2]=await Promise.all([
      api('PATCH','/api/settings',{http_port:httpPort,snmp_port:snmpPort}),
      api('PATCH','/api/tls',{tls_enabled:tlsEnabled,tls_port:tlsPort,http_redirect:httpRedirect}),
    ]);
  }catch(e){
    toast('Failed to save networking settings','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save Networking';}
  }
  if(!r1.ok||!r2.ok){toast('Failed to save networking settings','err');return;}
  toast('Saved — restart the server for changes to take effect','ok');
}

// ── Upload Certificate (tabbed modal: PEM paste / file upload / PFX) ────────

let _ucTab='pem';
function _switchUcTab(tab){
  _ucTab=tab;
  ['pem','file','pfx'].forEach(t=>{
    document.getElementById(`uc-tab-${t}`)?.classList.toggle('active',t===tab);
    const p=document.getElementById(`uc-pane-${t}`);
    if(p) p.style.display=t===tab?'':'none';
  });
  const errEl=document.getElementById('uc-err');
  if(errEl) errEl.style.display='none';
}

function openUploadCert(){
  closeM('muc');
  _ucTab='pem';
  const o=document.createElement('div');o.className='mo';o.id='muc';
  _overlayClose(o,()=>closeM('muc'));
  o.innerHTML=`
  <div class="mbox" style="width:580px;max-width:96vw">
    <div class="mhd"><div class="mttl">Upload Certificate</div><button class="mclose" onclick="closeM('muc')">✕</button></div>
    <div class="mbdy">
      <div class="uc-tabs">
        <button class="uc-tab active" id="uc-tab-pem" onclick="_switchUcTab('pem')">Paste PEM</button>
        <button class="uc-tab" id="uc-tab-file" onclick="_switchUcTab('file')">Upload Files</button>
        <button class="uc-tab" id="uc-tab-pfx" onclick="_switchUcTab('pfx')">PFX / PKCS#12</button>
      </div>

      <!-- PEM paste pane -->
      <div id="uc-pane-pem">
        <div class="fr">
          <label class="fl">Certificate (PEM)</label>
          <textarea id="uc-cert" rows="6" style="font-family:monospace;font-size:11px;resize:vertical" placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"></textarea>
        </div>
        <div class="fr" style="margin-top:10px">
          <label class="fl">Private Key (PEM)</label>
          <textarea id="uc-key" rows="6" style="font-family:monospace;font-size:11px;resize:vertical" placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;...&#10;-----END RSA PRIVATE KEY-----"></textarea>
        </div>
      </div>

      <!-- File upload pane -->
      <div id="uc-pane-file" style="display:none">
        <div class="fr">
          <label class="fl">Certificate File</label>
          <div class="fh" style="margin-bottom:6px">.cer, .crt, .pem — DER or PEM encoded</div>
          <input type="file" id="uc-f-cert" accept=".cer,.crt,.pem,.der"/>
          <div id="uc-f-cert-name" class="uc-file-label"></div>
        </div>
        <div class="fr" style="margin-top:10px">
          <label class="fl">Private Key File</label>
          <div class="fh" style="margin-bottom:6px">.key or .pem — PEM encoded</div>
          <input type="file" id="uc-f-key" accept=".key,.pem"/>
          <div id="uc-f-key-name" class="uc-file-label"></div>
        </div>
      </div>

      <!-- PFX pane -->
      <div id="uc-pane-pfx" style="display:none">
        <div class="fr">
          <label class="fl">PFX / P12 File</label>
          <div class="fh" style="margin-bottom:6px">PKCS#12 bundle containing certificate + private key</div>
          <input type="file" id="uc-f-pfx" accept=".pfx,.p12"/>
          <div id="uc-f-pfx-name" class="uc-file-label"></div>
        </div>
        <div class="fr" style="margin-top:10px">
          <label class="fl">Password <span style="color:var(--text3);font-weight:400">(leave empty if none)</span></label>
          <input type="password" id="uc-pfx-pw" placeholder="" autocomplete="new-password"/>
        </div>
      </div>

      <div id="uc-err" style="display:none;margin-top:10px;padding:8px;background:var(--bg3);border-radius:4px;font-size:12px;color:var(--err)"></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('muc')">Cancel</button>
      <button class="btn-p" id="btn-uc-save" onclick="submitUploadCert()">Validate &amp; Save</button>
    </div>
  </div>`;
  document.body.appendChild(o);
}

function _readFileAsText(fileEl){
  return new Promise((res,rej)=>{
    const f=fileEl?.files?.[0];
    if(!f){rej('No file selected');return;}
    const r=new FileReader();
    r.onload=()=>res(r.result);
    r.onerror=()=>rej('Failed to read file');
    r.readAsText(f);
  });
}
function _readFileAsB64(fileEl){
  return new Promise((res,rej)=>{
    const f=fileEl?.files?.[0];
    if(!f){rej('No file selected');return;}
    const r=new FileReader();
    r.onload=()=>res(r.result.split(',')[1]||'');
    r.onerror=()=>rej('Failed to read file');
    r.readAsDataURL(f);
  });
}

async function submitUploadCert(){
  const errEl=document.getElementById('uc-err');
  const btn=document.getElementById('btn-uc-save');
  const showErr=msg=>{errEl.textContent=msg;errEl.style.display='';};
  btn.disabled=true;btn.textContent='Validating...';
  errEl.style.display='none';

  try{
    let r;
    if(_ucTab==='pem'){
      const cert_pem=(document.getElementById('uc-cert')?.value||'').trim();
      const key_pem =(document.getElementById('uc-key')?.value||'').trim();
      if(!cert_pem||!key_pem){showErr('Both certificate and private key are required.');btn.disabled=false;btn.textContent='Validate & Save';return;}
      r=await api('POST','/api/tls/upload',{cert_pem,key_pem});
    } else if(_ucTab==='file'){
      const certEl=document.getElementById('uc-f-cert');
      const keyEl =document.getElementById('uc-f-key');
      if(!certEl?.files?.length||!keyEl?.files?.length){showErr('Both certificate and key files are required.');btn.disabled=false;btn.textContent='Validate & Save';return;}
      // Read cert as base64 (may be DER binary), key as text (always PEM)
      const [cert_b64, key_pem] = await Promise.all([_readFileAsB64(certEl), _readFileAsText(keyEl)]);
      // Send both: cert_b64 for DER support, key_pem as text
      r=await api('POST','/api/tls/upload',{cert_b64, key_pem:key_pem.trim()});
    } else {
      const pfxEl=document.getElementById('uc-f-pfx');
      if(!pfxEl?.files?.length){showErr('Select a PFX/P12 file.');btn.disabled=false;btn.textContent='Validate & Save';return;}
      const pfx_b64=await _readFileAsB64(pfxEl);
      const password=document.getElementById('uc-pfx-pw')?.value||'';
      r=await api('POST','/api/tls/upload-pfx',{pfx_b64,password});
    }
    if(r.error){showErr(r.error);btn.disabled=false;btn.textContent='Validate & Save';return;}
    closeM('muc');
    toast('Certificate uploaded — restart the server to apply','ok');
  }catch(e){
    showErr('Request failed — check server connectivity.');
    btn.disabled=false;btn.textContent='Validate & Save';
  }
}

function generateNewCert(){
  closeM('mgc');
  const o=document.createElement('div');o.className='mo';o.id='mgc';
  _overlayClose(o,()=>closeM('mgc'));
  // Pre-fill with saved tls_cn or machine hostname (loaded from /api/tls)
  const _tr=window._tlsSettings||{};
  const _defaultCn=(_tr.cert&&_tr.cert.subject)||'';
  o.innerHTML=`
  <div class="mbox" style="width:480px;max-width:96vw">
    <div class="mhd"><div class="mttl">Generate Self-Signed Certificate</div><button class="mclose" onclick="closeM('mgc')">✕</button></div>
    <div class="mbdy">
      <div class="fr">
        <label class="fl">Common Name (CN)</label>
        <input type="text" id="gc-cn" value="${esc(_defaultCn)}" placeholder="e.g. pingwatch.local or 192.168.1.10" autocomplete="off"/>
        <div class="fh">The hostname or IP address browsers will connect to. Shown as the certificate name.</div>
      </div>
      <div class="fr" style="margin-top:12px">
        <label class="fl">Organization (O)</label>
        <input type="text" id="gc-org" value="${esc(_tr.org_name||'')}" placeholder="e.g. My Company" autocomplete="off"/>
        <div class="fh">Optional. Shown in the certificate details.</div>
      </div>
      <div class="fr" style="margin-top:12px">
        <label class="fl">Additional SANs</label>
        <textarea id="gc-sans" rows="3" placeholder="One per line — DNS name or IP address&#10;e.g. pingwatch.local&#10;e.g. 192.168.1.10" autocomplete="off" style="resize:vertical;font-family:monospace;font-size:12px"></textarea>
        <div class="fh">Optional. Extra Subject Alternative Names added to the certificate. The CN, localhost, and 127.0.0.1 are always included.</div>
      </div>
      <div style="margin-top:12px;padding:8px;background:var(--bg3);border-radius:4px;font-size:12px;color:var(--text3)">
        A new RSA-2048 self-signed certificate valid for 825 days will be generated and saved. Restart the server to apply it.
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mgc')">Cancel</button>
      <button class="btn-p" id="btn-gc-submit" onclick="submitGenerateCert()">Generate</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('gc-cn')?.focus(),50);
}

async function submitGenerateCert(){
  const hostname=(document.getElementById('gc-cn')?.value||'').trim();
  const org_name=(document.getElementById('gc-org')?.value||'').trim();
  const extra_sans=(document.getElementById('gc-sans')?.value||'')
    .split('\n').map(s=>s.trim()).filter(Boolean);
  if(!hostname){toast('Common Name is required','err');return;}
  const btn=document.getElementById('btn-gc-submit');
  if(btn){btn.disabled=true;btn.textContent='Generating...';}
  let r;
  try{
    r=await api('POST','/api/tls/generate',{hostname,org_name,extra_sans});
  }catch(e){
    toast('Certificate generation failed','err');
    if(btn){btn.disabled=false;btn.textContent='Generate';}
    return;
  }
  if(btn){btn.disabled=false;btn.textContent='Generate';}
  if(r.error){toast(r.error,'err');return;}
  closeM('mgc');
  toast('New self-signed certificate generated — restart the server to apply','ok');
}

// ── Generate CSR modal ──────────────────────────────────────────────────────

function openGenerateCSR(){
  closeM('mcsr');
  const _tr=window._tlsSettings||{};
  const _defaultCn=(_tr.cert&&_tr.cert.subject)||'';
  const o=document.createElement('div');o.className='mo';o.id='mcsr';
  _overlayClose(o,()=>closeM('mcsr'));
  o.innerHTML=`
  <div class="mbox" style="width:520px;max-width:96vw">
    <div class="mhd"><div class="mttl">Generate Certificate Signing Request</div><button class="mclose" onclick="closeM('mcsr')">✕</button></div>
    <div class="mbdy" id="csr-form">
      <div class="fr">
        <label class="fl">Common Name (CN)</label>
        <input type="text" id="csr-cn" value="${esc(_defaultCn)}" placeholder="e.g. pingwatch.example.com" autocomplete="off"/>
        <div class="fh">The hostname your CA will issue the certificate for.</div>
      </div>
      <div class="fgrid" style="margin-top:10px">
        <div class="fr">
          <label class="fl">Organization (O)</label>
          <input type="text" id="csr-org" value="${esc(_tr.org_name||'')}" placeholder="e.g. My Company" autocomplete="off"/>
        </div>
        <div class="fr">
          <label class="fl">Key Size</label>
          <select id="csr-ks">
            <option value="2048" selected>RSA 2048</option>
            <option value="4096">RSA 4096</option>
          </select>
        </div>
      </div>
      <div class="fr" style="margin-top:10px">
        <label class="fl">Subject Alternative Names</label>
        <textarea id="csr-sans" rows="3" placeholder="One per line — DNS name or IP address" autocomplete="off" style="resize:vertical;font-family:monospace;font-size:12px"></textarea>
        <div class="fh">Optional. The CN is always included automatically.</div>
      </div>
      <div style="margin-top:12px;padding:8px;background:var(--bg3);border-radius:4px;font-size:12px;color:var(--text3)">
        A new private key will be generated and stored securely. After your CA signs the CSR, upload the signed certificate using <strong>Upload Certificate</strong>.
      </div>
    </div>
    <div class="mbdy" id="csr-result" style="display:none">
      <div style="font-size:12px;font-weight:600;color:var(--ok);margin-bottom:10px">CSR generated successfully</div>
      <div class="fr">
        <label class="fl">CSR (PEM) — send this to your Certificate Authority</label>
        <textarea id="csr-out" rows="12" readonly style="font-family:monospace;font-size:11px;resize:vertical;background:var(--bg3);cursor:text"></textarea>
      </div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <button class="btn-s" onclick="_copyCSR()">Copy to Clipboard</button>
        <button class="btn-s" onclick="_downloadCSR()">Download .csr</button>
      </div>
      <div style="margin-top:12px;padding:8px;background:var(--bg3);border-radius:4px;font-size:12px;color:var(--text3)">
        The private key has been saved. When your CA returns the signed certificate, use <strong>Upload Certificate</strong> to install it — the key will be matched automatically.
      </div>
    </div>
    <div class="mft" id="csr-footer-form">
      <button class="btn-s" onclick="closeM('mcsr')">Cancel</button>
      <button class="btn-p" id="btn-csr-submit" onclick="submitGenerateCSR()">Generate CSR</button>
    </div>
    <div class="mft" id="csr-footer-done" style="display:none">
      <button class="btn-p" onclick="closeM('mcsr')">Done</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('csr-cn')?.focus(),50);
}

async function submitGenerateCSR(){
  const hostname=(document.getElementById('csr-cn')?.value||'').trim();
  const org_name=(document.getElementById('csr-org')?.value||'').trim();
  const key_size=parseInt(document.getElementById('csr-ks')?.value||'2048');
  const extra_sans=(document.getElementById('csr-sans')?.value||'')
    .split('\n').map(s=>s.trim()).filter(Boolean);
  if(!hostname){toast('Common Name is required','err');return;}
  const btn=document.getElementById('btn-csr-submit');
  if(btn){btn.disabled=true;btn.textContent='Generating...';}
  let r;
  try{
    r=await api('POST','/api/tls/csr',{hostname,org_name,key_size,extra_sans});
  }catch(e){
    toast('CSR generation failed','err');
    if(btn){btn.disabled=false;btn.textContent='Generate CSR';}
    return;
  }
  if(btn){btn.disabled=false;btn.textContent='Generate CSR';}
  if(r.error){toast(r.error,'err');return;}
  // Refresh cert section in networking tab so CSR-pending state shows immediately
  _refreshCertSection();
  // Show result pane
  document.getElementById('csr-form').style.display='none';
  document.getElementById('csr-footer-form').style.display='none';
  document.getElementById('csr-result').style.display='';
  document.getElementById('csr-footer-done').style.display='';
  document.getElementById('csr-out').value=r.csr_pem||'';
}

function _copyCSR(){
  const ta=document.getElementById('csr-out');
  if(!ta) return;
  navigator.clipboard.writeText(ta.value).then(()=>toast('CSR copied to clipboard','ok')).catch(()=>{
    ta.select();document.execCommand('copy');toast('CSR copied','ok');
  });
}

function _downloadCSR(){
  const pem=document.getElementById('csr-out')?.value||'';
  if(!pem) return;
  const blob=new Blob([pem],{type:'application/pkcs10'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='pingwatch.csr';
  a.click();
  URL.revokeObjectURL(a.href);
}

// ── Install Signed Certificate modal ────────────────────────────────────────
// Used after a CSR has been generated — accepts only the signed cert (cert only
// or cert + chain). The private key is already stored from CSR generation.

let _isTab='pem';
function _switchIsTab(tab){
  _isTab=tab;
  ['pem','file'].forEach(t=>{
    document.getElementById(`is-tab-${t}`)?.classList.toggle('active',t===tab);
    const p=document.getElementById(`is-pane-${t}`);
    if(p) p.style.display=t===tab?'':'none';
  });
  const errEl=document.getElementById('is-err');
  if(errEl) errEl.style.display='none';
}

function openInstallSigned(){
  closeM('mis');
  _isTab='pem';
  const o=document.createElement('div');o.className='mo';o.id='mis';
  _overlayClose(o,()=>closeM('mis'));
  o.innerHTML=`
  <div class="mbox" style="width:560px;max-width:96vw">
    <div class="mhd"><div class="mttl">Install Signed Certificate</div><button class="mclose" onclick="closeM('mis')">✕</button></div>
    <div class="mbdy">
      <div style="padding:8px 12px;background:rgba(35,209,139,.08);border:1px solid rgba(35,209,139,.2);border-radius:6px;font-size:12px;color:var(--text2);margin-bottom:14px">
        The private key from your CSR is already stored. Paste or upload the signed certificate (and optionally the CA chain) below.
      </div>
      <div class="uc-tabs">
        <button class="uc-tab active" id="is-tab-pem" onclick="_switchIsTab('pem')">Paste PEM</button>
        <button class="uc-tab" id="is-tab-file" onclick="_switchIsTab('file')">Upload File</button>
      </div>

      <!-- Paste pane -->
      <div id="is-pane-pem">
        <div class="fr">
          <label class="fl">Certificate (PEM)</label>
          <div class="fh" style="margin-bottom:6px">Paste the signed certificate. You may include intermediate CA certificates below it (full chain).</div>
          <textarea id="is-cert" rows="10" style="font-family:monospace;font-size:11px;resize:vertical" placeholder="-----BEGIN CERTIFICATE-----&#10;(your signed certificate)&#10;-----END CERTIFICATE-----&#10;&#10;-----BEGIN CERTIFICATE-----&#10;(intermediate CA — optional)&#10;-----END CERTIFICATE-----"></textarea>
        </div>
      </div>

      <!-- File pane -->
      <div id="is-pane-file" style="display:none">
        <div class="fr">
          <label class="fl">Certificate File</label>
          <div class="fh" style="margin-bottom:6px">.cer, .crt, .pem — DER or PEM. PEM files may contain the full chain.</div>
          <input type="file" id="is-f-cert" accept=".cer,.crt,.pem,.der"/>
          <div id="is-f-cert-name" class="uc-file-label"></div>
        </div>
      </div>

      <div id="is-err" style="display:none;margin-top:10px;padding:8px;background:var(--bg3);border-radius:4px;font-size:12px;color:var(--err)"></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mis')">Cancel</button>
      <button class="btn-p" id="btn-is-save" onclick="submitInstallSigned()">Install Certificate</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('is-cert')?.focus(),50);
}

async function submitInstallSigned(){
  const errEl=document.getElementById('is-err');
  const btn=document.getElementById('btn-is-save');
  const showErr=msg=>{errEl.textContent=msg;errEl.style.display='';};
  btn.disabled=true;btn.textContent='Validating...';
  errEl.style.display='none';
  try{
    let r;
    if(_isTab==='pem'){
      const cert_pem=(document.getElementById('is-cert')?.value||'').trim();
      if(!cert_pem){showErr('Certificate is required.');btn.disabled=false;btn.textContent='Install Certificate';return;}
      r=await api('POST','/api/tls/install-signed',{cert_pem});
    } else {
      const certEl=document.getElementById('is-f-cert');
      if(!certEl?.files?.length){showErr('Select a certificate file.');btn.disabled=false;btn.textContent='Install Certificate';return;}
      const cert_b64=await _readFileAsB64(certEl);
      r=await api('POST','/api/tls/install-signed',{cert_b64});
    }
    if(r.error){showErr(r.error);btn.disabled=false;btn.textContent='Install Certificate';return;}
    closeM('mis');
    _refreshCertSection();
    toast('Certificate installed — restart the server to apply','ok');
  }catch(e){
    showErr('Request failed — check server connectivity.');
    btn.disabled=false;btn.textContent='Install Certificate';
  }
}

let _activeLogTab = 'app';
let _logFilter = { timeRange: 'all', level: '', search: '' };
let _logLiveMode = false;
let _logLiveTimer = null;
let _logLastTs = '';
let _logData = [];
let _logSearchDebounce = null;

function _switchLogTab(key) {
  _activeLogTab = key;
  ['app','sensors','audit','backup'].forEach(k => {
    document.getElementById(`lstab-btn-${k}`)?.classList.toggle('active', k === key);
  });
  _logLastTs = '';
  _logData = [];
  _loadLogTab();
}

function _parseLogSearch(raw) {
  const result = { level: '', search: '' };
  if (!raw) return result;
  const parts = raw.trim().split(/\s+/);
  const textParts = [];
  for (const part of parts) {
    const lower = part.toLowerCase();
    if (lower.startsWith('level:')) {
      const val = part.substring(6).toUpperCase();
      if (['DEBUG','INFO','WARNING','ERROR','CRITICAL'].includes(val)) result.level = val;
    } else if (lower.startsWith('device:')) {
      textParts.push(part.substring(7));
    } else {
      textParts.push(part);
    }
  }
  result.search = textParts.join(' ');
  return result;
}

function _onLogFilterChange() {
  _logFilter.timeRange = document.getElementById('logFTime')?.value || 'all';
  _logFilter.level     = document.getElementById('logFLevel')?.value || '';
  _logFilter.search    = document.getElementById('logFSearch')?.value || '';
  _logLastTs = '';
  clearTimeout(_logSearchDebounce);
  _logSearchDebounce = setTimeout(_loadLogTab, 300);
}

function _clearLogFilters() {
  _logFilter = { timeRange: 'all', level: '', search: '' };
  _logLastTs = '';
  ['logFTime','logFLevel','logFSearch'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.tagName === 'SELECT') el.value = (id === 'logFTime') ? 'all' : '';
    else el.value = '';
  });
  _loadLogTab();
}

function _colorLog(text, searchTerm) {
  if (!text) return '<span style="color:var(--text3)">(empty)</span>';
  const e = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(INFO|WARNING|WARN|ERROR|CRITICAL|DEBUG)\s+(.*)/;
  const hl = (searchTerm && searchTerm.trim())
    ? (s => {
        const escaped = e(s);
        const q = searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return escaped.replace(new RegExp(`(${q})`, 'gi'), '<mark class="ll-hl">$1</mark>');
      })
    : e;
  return text.split('\n').map(line => {
    if (!line) return '<div class="ll-row ll-empty"></div>';
    const m = line.match(RE);
    if (m) {
      const [,ts,lvl,msg] = m;
      return `<div class="ll-row"><span class="ll-pre"><span class="ll-ts">${e(ts)}</span><span class="ll-${lvl.toLowerCase()}">${e(lvl)}</span></span><span class="ll-msg">${hl(msg)}</span></div>`;
    }
    return `<div class="ll-row ll-cont"><span class="ll-msg">${hl(line)}</span></div>`;
  }).join('');
}

async function _loadLogTab() {
  const el  = document.getElementById('log-body');
  const lbl = document.getElementById('log-footer-label');
  if (!el) return;

  const parsed = _parseLogSearch(_logFilter.search);
  const params = new URLSearchParams();
  const level = parsed.level || _logFilter.level;
  if (level) params.set('level', level);

  if (_logLiveMode && _logLastTs) {
    params.set('after', _logLastTs);
  } else if (_logFilter.timeRange !== 'all') {
    const offsets = { '5m': 5*60, '15m': 15*60, '1h': 3600, '24h': 86400 };
    const sec = offsets[_logFilter.timeRange];
    if (sec) {
      const d = new Date(Date.now() - sec * 1000);
      const after = d.getFullYear() + '-' +
        String(d.getMonth()+1).padStart(2,'0') + '-' +
        String(d.getDate()).padStart(2,'0') + ' ' +
        String(d.getHours()).padStart(2,'0') + ':' +
        String(d.getMinutes()).padStart(2,'0') + ':' +
        String(d.getSeconds()).padStart(2,'0');
      params.set('after', after);
    }
  }
  if (parsed.search) params.set('search', parsed.search);
  const qs = params.toString();
  const url = `/api/logs/${_activeLogTab}` + (qs ? '?' + qs : '');

  try {
    const r = await fetch(url);
    if (!r.ok) { el.textContent = 'Access denied'; return; }
    const d = await r.json();
    const searchTerm = parsed.search || '';

    if (_logLiveMode && _logLastTs) {
      if (d.lines) el.innerHTML += _colorLog(d.lines, searchTerm);
      // no new lines → keep existing content
    } else {
      el.innerHTML = _colorLog(d.lines || '(empty)', searchTerm);
      _logData = (d.lines || '').split('\n').filter(l => l);
    }

    if (d.lines) {
      const lines = d.lines.split('\n');
      for (let i = lines.length - 1; i >= 0; i--) {
        const tm = lines[i].match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
        if (tm) { _logLastTs = tm[1]; break; }
      }
    }

    if (_logLiveMode) el.scrollTop = el.scrollHeight;

    const names = { app:'pingwatch.log', sensors:'pingwatchsensors.log',
                    audit:'pingwatchaudit.log', backup:'pingwatchbackup.log' };
    if (lbl) {
      const showing = d.shown != null ? d.shown.toLocaleString() : '?';
      const total   = d.total != null ? d.total.toLocaleString() : '?';
      lbl.textContent = `Showing ${showing} / ${total} logs \u00b7 ${names[_activeLogTab] || ''}`;
    }
  } catch(e) {
    el.textContent = `Failed to load: ${String(e)}`;
  }
}

function _toggleLogLive() {
  _logLiveMode = !_logLiveMode;
  const btn = document.getElementById('logLiveBtn');
  if (btn) {
    btn.classList.toggle('log-live-on', _logLiveMode);
    btn.textContent = _logLiveMode ? '\uD83D\uDFE2 Live' : '\u25cb Live';
  }
  if (_logLiveMode) {
    _logLastTs = '';
    _loadLogTab();
    _logLiveTimer = setInterval(() => {
      if (!document.getElementById('log-body')) { _stopLogLive(); return; }
      _loadLogTab();
    }, 3000);
  } else {
    _stopLogLive();
  }
}

function _stopLogLive() {
  _logLiveMode = false;
  if (_logLiveTimer) { clearInterval(_logLiveTimer); _logLiveTimer = null; }
  const btn = document.getElementById('logLiveBtn');
  if (btn) { btn.classList.remove('log-live-on'); btn.textContent = '\u25cb Live';}
}

function _exportLogCsv() {
  if (!_logData.length) { toast('No log data to export','warn'); return; }
  const RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(INFO|WARNING|WARN|ERROR|CRITICAL|DEBUG)\s+(.*)/;
  const header = 'Timestamp,Level,Message\n';
  const rows = _logData.map(line => {
    const m = line.match(RE);
    if (m) return [m[1], m[2], m[3]].map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',');
    return '"","","' + String(line).replace(/"/g,'""') + '"';
  });
  _logDownload(`pingwatch-${_activeLogTab}.csv`, header + rows.join('\n'), 'text/csv');
}

function _exportLogJson() {
  if (!_logData.length) { toast('No log data to export','warn'); return; }
  const RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(INFO|WARNING|WARN|ERROR|CRITICAL|DEBUG)\s+(.*)/;
  const out = _logData.map(line => {
    const m = line.match(RE);
    if (m) return { timestamp: m[1], level: m[2], message: m[3] };
    return { timestamp: '', level: '', message: line };
  });
  _logDownload(`pingwatch-${_activeLogTab}.json`, JSON.stringify(out, null, 2), 'application/json');
}

function _logDownload(filename, content, mime) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([content], { type: mime }));
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// ── Per-type sensor defaults tab ──────────────────────────────────────────

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

function _sdrToggle(btn){
  const row = btn.closest('tr');
  const extraRow = row.nextElementSibling;
  if(!extraRow) return;
  const open = extraRow.style.display === '';
  extraRow.style.display = open ? 'none' : '';
  btn.classList.toggle('open', !open);
  btn.textContent = open ? '▾' : '▴';
}

async function loadSensorsDefaultsTab(){
  const el = document.getElementById('sdrTabBody');
  if(!el) return;
  const {devices} = await api('GET','/api/devices');
  const typeCounts = {};
  for(const dev of devices)
    for(const s of (dev.sensors||[]))
      typeCounts[s.stype] = (typeCounts[s.stype]||0) + 1;
  const types = Object.keys(typeCounts).sort();
  if(!types.length){ el.innerHTML='<div style="color:var(--text3);font-size:12px;padding:8px">No sensors found.</div>'; return; }
  const td = window._snrTypeDefaults || {};
  const rows = types.map(t => {
    const m   = _SDR_META[t] || {ico:'?', label:t, desc:''};
    const d   = td[t] || {};
    const cnt = typeCounts[t];
    const iv  = d.interval      != null ? d.interval      : (window._snrDef?.interval||5);
    const to  = d.timeout       != null ? d.timeout       : (window._snrDef?.timeout||4);
    const wm  = d.warn_ms  != null ? d.warn_ms  : (_SDR_WARN_DEF[t] || '');
    const cm  = d.crit_ms  != null ? d.crit_ms  : (_SDR_CRIT_DEF[t] || '');
    const warnUnit = t==='tls'?'days':t==='snmp'?'val':'ms';
    const extra = _sdrExtraFields(t, d);
    return `<tr class="sdr-card sdr-row" data-type="${t}">
      <td><div class="sdr-type-cell"><span class="sdr-icon" title="${m.desc}">${m.ico}</span><span class="sdr-lbl">${m.label}</span></div></td>
      <td style="text-align:center"><span class="sdr-cnt">${cnt}</span></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_interval" value="${iv}" min="1" max="300"/><span class="sdr-unit">s</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_timeout" value="${to}" min="1" max="60"/><span class="sdr-unit">s</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_warn_ms" value="${wm}" min="1" placeholder="—"/><span class="sdr-unit">${warnUnit}</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_crit_ms" value="${cm}" min="1" placeholder="—"/><span class="sdr-unit">${warnUnit}</span></div></td>
      <td style="text-align:center">${extra ? `<button class="sdr-expand-btn" onclick="_sdrToggle(this)" title="Type-specific settings">▾</button>` : ''}</td>
    </tr>
    ${extra ? `<tr class="sdr-extra-row" data-for="${t}" style="display:none"><td colspan="7"><div class="sdr-extra">${extra}</div></td></tr>` : ''}`;
  }).join('');
  el.innerHTML = `<table class="sdr-tbl">
    <thead><tr>
      <th>Type</th><th>#</th>
      <th>Interval</th><th>Timeout</th>
      <th>Warn</th><th>Crit</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function _scanPortsReset(){
  document.querySelectorAll('.st-scan-port').forEach(cb => { cb.checked = true; });
  const el = document.getElementById('st-scan-custom');
  if(el) el.value = '';
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
  const snrIv  = parseInt(document.getElementById('st-snr-iv')?.value);
  const snrTmo = parseInt(document.getElementById('st-snr-tmo')?.value);
  const globalDefaults = {};
  if(snrIv  >= 1) globalDefaults.snr_interval     = snrIv;
  if(snrTmo >= 1) globalDefaults.snr_timeout       = snrTmo;
  // Collect scan_ports from checkboxes + custom input
  const scanChecked = [...document.querySelectorAll('.st-scan-port:checked')].map(cb => cb.value);
  const scanCustomRaw = (document.getElementById('st-scan-custom')?.value || '').trim();
  const scanCustomPorts = scanCustomRaw ? scanCustomRaw.split(',').map(s => s.trim()).filter(Boolean) : [];
  const scanPorts = [...scanChecked, ...scanCustomPorts].join(',');

  const r = await api('PATCH', '/api/settings', {snr_type_defaults: result, ...globalDefaults, scan_ports: scanPorts});
  if(!r.ok){ toast('Save failed','err'); return; }
  window._snrTypeDefaults = result;
  window._snrDef = window._snrDef || {};
  if(globalDefaults.snr_interval)     window._snrDef.interval     = globalDefaults.snr_interval;
  if(globalDefaults.snr_timeout)      window._snrDef.timeout      = globalDefaults.snr_timeout;
  toast('Sensor defaults saved','ok');
}

function renderUserTable(users){
  if(!users||!users.length) return '<div style="color:var(--text3);font-size:12px;padding:8px 0">No users found.</div>';
  const rows=users.map(u=>{
    const isLdap=u.auth_type==='ldap';
    const badge=isLdap
      ?`<span class="usr-badge-ldap">🌐 Domain</span>`
      :`<span class="usr-badge-local">🔑 Local</span>`;
    const resetBtn=isLdap?'':`<button onclick="openResetPw('${esc(u.username)}')">🔑 Reset Pw</button>`;
    const uq=encodeURIComponent(u.username);
    return `
    <tr>
      <td><strong>${esc(u.username)}</strong></td>
      <td style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(u.full_name||'')}">${esc(u.full_name||'—')}</td>
      <td style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(u.email||'')}">${esc(u.email||'—')}</td>
      <td>${esc(u.group_name||'—')}</td>
      <td><span style="color:var(--text2)">${esc(u.role)}</span></td>
      <td>${badge}</td>
      <td><div class="usr-act">
        <button onclick="_openUserProfileModal('${esc(u.username)}')">✏ Edit</button>
        ${resetBtn}
        <button class="del" onclick="deleteUser('${esc(u.username)}')">🗑 Delete</button>
      </div></td>
    </tr>`;
  }).join('');
  return `<table class="usr-table">
    <thead><tr><th>Username</th><th>Full Name</th><th>Email</th><th>Group</th><th>Role</th><th>Auth</th><th>Actions</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function saveSettings(){
  const ttl=parseInt(document.getElementById('st-ttl')?.value);
  if(!ttl||ttl<60){toast('Session timeout must be at least 60 seconds','err');return;}
  const btn=[...document.querySelectorAll('[onclick="saveSettings()"]')].find(el=>el.offsetParent!==null);
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  const body={session_ttl:ttl};
  // Data rollup retention tiers (v0.8.0)
  const retRaw=parseInt(document.getElementById('st-ret-raw')?.value);
  const ret5m =parseInt(document.getElementById('st-ret-5m')?.value);
  const ret1h =parseInt(document.getElementById('st-ret-1h')?.value);
  if(retRaw>=1)  body.retention_raw_days=retRaw;
  if(ret5m>=7)   body.retention_5m_days=ret5m;
  if(ret1h>=30)  body.retention_1h_days=ret1h;
  const mwRaw=document.getElementById('st-mw')?.value?.trim();
  const mw=mwRaw ? parseInt(mwRaw) : 0;
  body.max_workers_executor = (mw>=4) ? mw : 0;  // 0 = auto
  const flapDisp=parseInt(document.getElementById('st-flap-disp')?.value);
  const flapDb  =parseInt(document.getElementById('st-flap-db')?.value);
  const trapDb  =parseInt(document.getElementById('st-trap-db')?.value);
  if(flapDisp>=5)  body.max_flaps_display=flapDisp;
  if(flapDb>=50)   body.max_flap_entries=flapDb;
  if(trapDb>=50)   body.max_trap_entries=trapDb;
  body.org_name=(document.getElementById('st-orgname')?.value||'').trim();
  const lGood=parseInt(document.getElementById('st-lgood')?.value);
  const lWarn=parseInt(document.getElementById('st-lwarn')?.value);
  if(lGood>=1) body.latency_good_ms=lGood;
  if(lWarn>=1) body.latency_warn_ms=lWarn;
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
  body.debug_mode=document.getElementById('st-debug-mode')?.checked?1:0;
  let r;
  try{
    r=await api('PATCH','/api/settings',body);
  }catch(e){
    toast('Failed to save settings','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save Settings';}
  }
  if(!r.ok){toast('Failed to save settings','err');return;}
  if(body.session_ttl){
    const hint=document.getElementById('st-ttl-hint');
    if(hint) hint.textContent=`Current: ${_fmtTtl(body.session_ttl)} — takes effect on next login`;
    if(typeof _sessionTtl!=='undefined') _sessionTtl=body.session_ttl;
  }
  if(body.max_flaps_display) MAX_FLAPS=body.max_flaps_display;
  if(body.latency_good_ms)   window._lGood=body.latency_good_ms;
  if(body.latency_warn_ms)   window._lWarn=body.latency_warn_ms;
  if('org_name' in body){
    window._snrDef=window._snrDef||{};
    const el=document.getElementById('tbVer');
    if(el) el.textContent=body.org_name||'Network Monitor v3';
    document.title='PingWatch \u2014 '+(body.org_name||'Network Monitor');
  }
  toast('Settings saved','ok');
}

async function saveSecuritySettings(){
  const failMax = parseInt(document.getElementById('st-fail-max')?.value);
  const failWin = parseInt(document.getElementById('st-fail-win')?.value);
  const body = {};
  if(failMax >= 1)  body.login_fail_max    = failMax;
  if(failWin >= 10) body.login_fail_window = failWin;
  const r = await api('PATCH', '/api/settings', body);
  if(!r.ok){ toast('Failed to save security settings','err'); return; }
  toast('Security settings saved','ok');
}

function _bkFreqChange(){
  const freq = document.getElementById('st-bk-freq')?.value;
  const daysRow = document.getElementById('st-bk-days-row');
  if(daysRow) daysRow.style.display = freq === 'weekly' ? '' : 'none';
}

async function _loadBackupScheduleSettings(){
  const r = await api('GET', '/api/settings');
  const en = document.getElementById('st-bk-enabled');
  const freq = document.getElementById('st-bk-freq');
  const time = document.getElementById('st-bk-time');
  const keep = document.getElementById('st-bk-keep');
  if(en)   en.checked = !!r.backup_sched_enabled;
  if(freq) freq.value = r.backup_sched_freq || 'daily';
  if(time) time.value = r.backup_sched_time || '02:00';
  if(keep) keep.value = r.backup_keep != null ? r.backup_keep : 3;
  // Populate day checkboxes
  const days = String(r.backup_sched_days || '1,2,3,4,5,6,7').split(',').map(d => d.trim());
  for(let i=1; i<=7; i++){
    const cb = document.getElementById(`st-bk-d${i}`);
    if(cb) cb.checked = days.includes(String(i));
  }
  _bkFreqChange();
}

async function saveBackupScheduleSettings(){
  const enabled = document.getElementById('st-bk-enabled')?.checked ? 1 : 0;
  const freq    = document.getElementById('st-bk-freq')?.value || 'daily';
  const time    = document.getElementById('st-bk-time')?.value || '02:00';
  const keep    = parseInt(document.getElementById('st-bk-keep')?.value) || 3;
  const days = [];
  for(let i=1; i<=7; i++){
    if(document.getElementById(`st-bk-d${i}`)?.checked) days.push(i);
  }
  if(freq === 'weekly' && !days.length){
    toast('Select at least one day for weekly schedule','err'); return;
  }
  const btn = document.querySelector('#stab-footer-backup .btn-p');
  if(btn){ btn.disabled=true; btn.textContent='Saving...'; }
  try {
    const r = await api('PATCH', '/api/settings', {
      backup_sched_enabled: enabled,
      backup_sched_freq:    freq,
      backup_sched_time:    time,
      backup_sched_days:    days.length ? days.join(',') : '1,2,3,4,5,6,7',
      backup_keep:          keep,
    });
    if(!r?.ok){ toast('Failed to save backup settings','err'); return; }
    toast('Backup schedule settings saved','ok');
  } catch(e) {
    toast('Failed to save backup settings','err');
  } finally {
    if(btn){ btn.disabled=false; btn.textContent='Save Config Backup'; }
  }
}

function _toggleDbBackup(){
  const body = document.getElementById('dbk-collapse');
  const chevron = document.getElementById('dbk-chevron');
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  chevron.style.transform = open ? 'rotate(-90deg)' : 'rotate(0deg)';
}

function _dbkFreqChange(){
  const freq = document.getElementById('st-dbk-freq')?.value;
  const daysRow = document.getElementById('st-dbk-days-row');
  if(daysRow) daysRow.style.display = freq === 'weekly' ? '' : 'none';
}

async function _loadDbStats(){
  const mainEl = document.getElementById('db-stats-main');
  const logsEl = document.getElementById('db-stats-logs');
  if(!mainEl && !logsEl) return;
  try {
    const s = await api('GET', '/api/db/stats');
    const fmtSize = b => b >= 1048576 ? (b/1048576).toFixed(1)+' MB' : b >= 1024 ? (b/1024).toFixed(1)+' KB' : b+' B';
    const fmtN   = n => n.toLocaleString();
    if(mainEl) mainEl.innerHTML =
      `<span style="color:var(--text2)">${esc(s.main.path)}</span> &nbsp;|&nbsp; <span style="color:var(--text)">${fmtSize(s.main.size)}</span>`;
    if(logsEl) logsEl.innerHTML =
      `<span style="color:var(--text2)">${esc(s.logs.path)}</span> &nbsp;|&nbsp; <span style="color:var(--text)">${fmtSize(s.logs.size)}</span><br>` +
      `<span style="color:var(--text3)">Samples: ${fmtN(s.logs.samples)} &nbsp; Flaps: ${fmtN(s.logs.flaps)} &nbsp; Traps: ${fmtN(s.logs.traps)} &nbsp; Errors: ${fmtN(s.logs.errors)}</span>`;
  } catch(e) {
    if(mainEl) mainEl.textContent = 'Could not load DB info';
    if(logsEl) logsEl.textContent = '';
  }
}

async function _loadDbBackendInfo(){
  const el = document.getElementById('db-backend-info');
  if(!el) return;
  try {
    const d = await api('GET', '/api/settings/db');
    if(d.backend === 'postgresql'){
      el.innerHTML =
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--up)"></span>' +
        '<span style="color:var(--text);font-weight:600">PostgreSQL</span></div>' +
        '<span style="color:var(--text3)">Host: ' + esc(d.pg_host) + ':' + d.pg_port +
        ' &nbsp;|&nbsp; Database: ' + esc(d.pg_database) + ' &nbsp;|&nbsp; User: ' + esc(d.pg_user) + '</span>';
    } else {
      const fmtSize = b => b >= 1048576 ? (b/1048576).toFixed(1)+' MB' : b >= 1024 ? (b/1024).toFixed(1)+' KB' : b+' B';
      el.innerHTML =
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">' +
        '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent)"></span>' +
        '<span style="color:var(--text);font-weight:600">SQLite</span></div>' +
        '<span style="color:var(--text3)">Main: ' + fmtSize(d.db_size||0) + ' &nbsp;|&nbsp; Logs: ' + fmtSize(d.logs_db_size||0) + '</span>' +
        '<div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border)">' +
        '<div style="font-size:12px;font-weight:500;color:var(--text2);margin-bottom:8px">Migrate to PostgreSQL</div>' +
        '<div style="font-size:11px;color:var(--text3);margin-bottom:10px">Copy all data to a PostgreSQL server and switch the backend.</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">' +
        '<div><label style="font-size:11px;color:var(--text3)">Host</label><input type="text" id="mig-pg-host" value="localhost" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:4px;color:var(--text);font-size:12px"></div>' +
        '<div><label style="font-size:11px;color:var(--text3)">Port</label><input type="number" id="mig-pg-port" value="5432" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:4px;color:var(--text);font-size:12px"></div>' +
        '</div>' +
        '<div style="margin-bottom:8px"><label style="font-size:11px;color:var(--text3)">Database</label><input type="text" id="mig-pg-db" value="pingwatch" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:4px;color:var(--text);font-size:12px"></div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">' +
        '<div><label style="font-size:11px;color:var(--text3)">User</label><input type="text" id="mig-pg-user" value="pingwatch" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:4px;color:var(--text);font-size:12px"></div>' +
        '<div><label style="font-size:11px;color:var(--text3)">Password</label><input type="password" id="mig-pg-pass" style="width:100%;padding:6px 8px;background:var(--bg);border:1px solid var(--border2);border-radius:4px;color:var(--text);font-size:12px"></div>' +
        '</div>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
        '<button class="btn-s" style="font-size:12px;padding:6px 14px" onclick="_migTestConn()">Test Connection</button>' +
        '<button class="btn-p" style="font-size:12px;padding:6px 14px" id="mig-btn" onclick="_migRun()" disabled>Migrate Now</button>' +
        '<span id="mig-status" style="font-size:12px;color:var(--text3)"></span>' +
        '</div>' +
        '</div>';
    }
  } catch(e) {
    el.textContent = 'Could not load backend info';
  }
}

async function _migTestConn(){
  const st = document.getElementById('mig-status');
  st.textContent = 'Testing...';
  st.style.color = 'var(--text3)';
  try {
    const r = await api('POST', '/api/settings/db/test', {
      host: document.getElementById('mig-pg-host')?.value || 'localhost',
      port: parseInt(document.getElementById('mig-pg-port')?.value) || 5432,
      database: document.getElementById('mig-pg-db')?.value || 'pingwatch',
      user: document.getElementById('mig-pg-user')?.value || 'pingwatch',
      password: document.getElementById('mig-pg-pass')?.value || '',
    });
    if(r.ok){
      st.innerHTML = '<span style="color:var(--up)">&#10003; Connected</span>';
      const btn = document.getElementById('mig-btn');
      if(btn) btn.disabled = false;
    } else {
      st.innerHTML = '<span style="color:var(--down)">&#10007; ' + esc(r.error) + '</span>';
    }
  } catch(e) {
    st.innerHTML = '<span style="color:var(--down)">' + esc(e.message) + '</span>';
  }
}

async function _migRun(){
  if(!confirm('This will copy all SQLite data to PostgreSQL and switch the backend.\nThe server will need to restart after migration.\n\nContinue?')) return;
  const btn = document.getElementById('mig-btn');
  const st  = document.getElementById('mig-status');
  btn.disabled = true;
  st.textContent = 'Migrating...';
  st.style.color = 'var(--warn)';
  try {
    const r = await api('POST', '/api/settings/db/migrate', {
      host: document.getElementById('mig-pg-host')?.value || 'localhost',
      port: parseInt(document.getElementById('mig-pg-port')?.value) || 5432,
      database: document.getElementById('mig-pg-db')?.value || 'pingwatch',
      user: document.getElementById('mig-pg-user')?.value || 'pingwatch',
      password: document.getElementById('mig-pg-pass')?.value || '',
    });
    if(r.ok){
      st.innerHTML = '<span style="color:var(--up)">&#10003; Migration complete! Server restart required.</span>';
      if(r.restart_required) {
        if(confirm('Migration successful! Restart the server now?')) {
          await api('POST', '/api/server/restart');
          st.textContent = 'Server restarting...';
        }
      }
    } else {
      st.innerHTML = '<span style="color:var(--down)">&#10007; ' + esc(r.error) + '</span>';
      btn.disabled = false;
    }
  } catch(e) {
    st.innerHTML = '<span style="color:var(--down)">' + esc(e.message) + '</span>';
    btn.disabled = false;
  }
}

async function _loadDbBackupSettings(){
  _loadDbStats();
  _loadDbBackendInfo();
  const r = await api('GET', '/api/settings');
  const en   = document.getElementById('st-dbk-enabled');
  const freq = document.getElementById('st-dbk-freq');
  const time = document.getElementById('st-dbk-time');
  const keep = document.getElementById('st-dbk-keep');
  if(en)   en.checked = !!r.db_backup_enabled;
  if(freq) freq.value = r.db_backup_freq || 'daily';
  if(time) time.value = r.db_backup_time || '03:00';
  if(keep) keep.value = r.db_backup_keep != null ? r.db_backup_keep : 7;
  const days = String(r.db_backup_days || '1,2,3,4,5,6,7').split(',').map(d => d.trim());
  for(let i=1; i<=7; i++){
    const cb = document.getElementById(`st-dbk-d${i}`);
    if(cb) cb.checked = days.includes(String(i));
  }
  _dbkFreqChange();
  const lastInfo = document.getElementById('dbk-last-info');
  if(lastInfo) lastInfo.textContent = r.db_backup_last_ts
    ? `Last backup: ${r.db_backup_last_ts} \u2014 ${r.db_backup_last_result}` : '';
}

async function saveDbBackupSettings(){
  const enabled = document.getElementById('st-dbk-enabled')?.checked ? 1 : 0;
  const freq    = document.getElementById('st-dbk-freq')?.value || 'daily';
  const time    = document.getElementById('st-dbk-time')?.value || '03:00';
  const keep    = parseInt(document.getElementById('st-dbk-keep')?.value) || 7;
  const days = [];
  for(let i=1; i<=7; i++){
    if(document.getElementById(`st-dbk-d${i}`)?.checked) days.push(i);
  }
  if(freq === 'weekly' && !days.length){
    toast('Select at least one day for weekly schedule','err'); return;
  }
  const btn = document.querySelector('#stab-footer-database .btn-p');
  if(btn){ btn.disabled=true; btn.textContent='Saving...'; }
  try {
    const r = await api('PATCH', '/api/settings', {
      db_backup_enabled: enabled,
      db_backup_freq:    freq,
      db_backup_time:    time,
      db_backup_days:    days.length ? days.join(',') : '1,2,3,4,5,6,7',
      db_backup_keep:    keep,
    });
    if(!r?.ok){ toast('Failed to save DB backup settings','err'); return; }
    toast('Database backup settings saved','ok');
  } catch(e) {
    toast('Failed to save DB backup settings','err');
  } finally {
    if(btn){ btn.disabled=false; btn.textContent='Save DB Backup'; }
  }
}

async function runDbBackupNow(){
  const btn = document.querySelector('[onclick="runDbBackupNow()"]');
  const res = document.getElementById('dbk-run-result');
  if(btn){ btn.disabled=true; btn.textContent='Running...'; }
  if(res) res.textContent = '';
  try {
    const r = await api('POST', '/api/db/backup/run', {});
    if(res) res.innerHTML = r.ok
      ? `<span style="color:var(--up)">\u2714 ${esc(r.msg||'Backup complete')}</span>`
      : `<span style="color:var(--down)">\u2718 ${esc(r.msg||'Backup failed')}</span>`;
    if(r.ok) _loadDbBackupSettings();
  } catch(e) {
    if(res) res.innerHTML = `<span style="color:var(--down)">\u2718 Request failed</span>`;
  } finally {
    if(btn){ btn.disabled=false; btn.textContent='\u25B6 Run Backup Now'; }
  }
}

// ── Integrations tab helpers ─────────────────────────────────────────────

function _timeAgo(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 5)     return 'just now';
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function _renderIntegStatus(id, status) {
  const el = document.getElementById(`${id}-status-bar`);
  if (!el) return;
  const icons   = {ok:'🟢', error:'⚠️', unconfigured:'🔴', configured:'🟡'};
  const labels  = {ok:'Connected', error:'Misconfigured', unconfigured:'Not configured', configured:'Configured'};
  const icon    = icons[status.state]  || '🔴';
  const label   = labels[status.state] || status.state;
  const lastOk  = status.last_ok_ts ? _timeAgo(status.last_ok_ts) : 'Never';
  const noun    = id === 'smtp' ? 'email' : 'syslog';
  const errHtml = (status.state === 'error' && status.last_err_msg)
    ? `<div style="font-size:11px;color:var(--down);margin-top:3px">${esc(status.last_err_msg)}</div>` : '';
  el.innerHTML = `<div style="display:flex;align-items:flex-start;gap:10px;padding:9px 12px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;margin-bottom:14px">
    <span style="font-size:16px;line-height:1.3">${icon}</span>
    <div>
      <span style="font-size:12px;font-weight:600;color:var(--text2)">${label}</span>
      <span style="font-size:11px;color:var(--text3);margin-left:10px">Last ${noun} sent: ${lastOk}</span>
      ${errHtml}
    </div>
  </div>`;
  const badge = document.getElementById(`ibadge-${id}`);
  if (badge) badge.textContent = ' ' + icon;
}

function switchIntegTab(name) {
  ['smtp', 'syslog'].forEach(t => {
    document.getElementById(`itab-${t}`)?.classList.toggle('itab-active', t === name);
    const p = document.getElementById(`ipanel-${t}`);
    if (p) p.style.display = t === name ? '' : 'none';
  });
  // Swap footer action buttons
  const testSmtpBtn   = document.getElementById('integ-btn-test');
  const testSyslogBtn = document.getElementById('integ-btn-test-syslog');
  if (testSmtpBtn)   testSmtpBtn.style.display   = name === 'smtp'   ? '' : 'none';
  if (testSyslogBtn) testSyslogBtn.style.display  = name === 'syslog' ? '' : 'none';
}

async function _loadIntegrationsStatus() {
  try {
    const r = await api('GET', '/api/settings');
    if (r.smtp_status)   _renderIntegStatus('smtp',   r.smtp_status);
    if (r.syslog_status) _renderIntegStatus('syslog', r.syslog_status);
  } catch(e) { /* non-critical */ }
  // Show correct footer buttons for the currently visible sub-tab
  const activeSubTab = document.getElementById('ipanel-smtp')?.style.display !== 'none' ? 'smtp' : 'syslog';
  switchIntegTab(activeSubTab);
}

async function _saveIntegrations() {
  const btn = document.getElementById('integ-btn-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    const activeSubTab = document.getElementById('ipanel-smtp')?.style.display !== 'none' ? 'smtp' : 'syslog';
    if (activeSubTab === 'smtp') {
      await saveSettings();
    } else {
      await saveSyslogSettings();
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
}

async function saveSyslogSettings(){
  const host     = (document.getElementById('st-sl-host')?.value   || '').trim();
  const port     = parseInt(document.getElementById('st-sl-port')?.value) || 514;
  const proto    = document.getElementById('st-sl-proto')?.value   || 'udp';
  const minSev   = document.getElementById('st-sl-minsev')?.value  || 'warning';
  const appLogs  = document.getElementById('st-sl-applogs')?.checked ? 1 : 0;
  const logLevel = document.getElementById('st-sl-loglevel')?.value || 'info';
  const logSources = ['app','audit','backup'].filter(s => document.getElementById(`st-sl-src-${s}`)?.checked);
  if(!host){ toast('Enter a syslog server address','err'); return; }
  const btn = document.getElementById('integ-btn-save');
  if(btn){ btn.disabled=true; btn.textContent='Saving...'; }
  try {
    const r = await api('PATCH', '/api/settings', {
      syslog_host:            host,
      syslog_port:            port,
      syslog_proto:           proto,
      syslog_min_severity:    minSev,
      syslog_app_logs:        appLogs,
      syslog_app_log_level:   logLevel,
      syslog_app_log_sources: logSources,
    });
    if(!r?.ok){ toast('Failed to save syslog settings','err'); return; }
    toast('Syslog settings saved','ok');
    _loadIntegrationsStatus();
  } catch(e) {
    toast('Failed to save syslog settings','err');
  } finally {
    if(btn){ btn.disabled=false; btn.textContent='Save'; }
  }
}

async function testSyslog(){
  const btn = document.getElementById('integ-btn-test-syslog');
  if(btn){ btn.disabled=true; btn.textContent='Sending...'; }
  try {
    const r = await api('POST', '/api/settings/syslog_test', {});
    toast(r?.ok ? r.msg || 'Test message sent' : `Failed: ${r?.msg||'Unknown error'}`,
          r?.ok ? 'ok' : 'err');
    setTimeout(_loadIntegrationsStatus, 500);
  } catch(e) {
    toast('Syslog test failed','err');
  } finally {
    if(btn){ btn.disabled=false; btn.textContent='Send Test Message'; }
  }
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
  setTimeout(_loadIntegrationsStatus, 500);
}

async function serverRestart(){
  if(!confirm('Restart the server now?\n\nThe dashboard will reconnect automatically.')) return;
  const btn=document.querySelector('[onclick="serverRestart()"]');
  if(btn){btn.disabled=true;btn.textContent='Restarting…';}
  try{
    await api('POST','/api/server/restart',{});
    toast('Server restarting — reconnecting…','ok');
    closeM('mset');
    // Poll until the server is back up
    setTimeout(async()=>{
      for(let i=0;i<30;i++){
        await new Promise(r=>setTimeout(r,2000));
        try{
          const r=await fetch('/api/server_info',{credentials:'include'});
          if(r.ok){ toast('Server is back online','ok'); location.reload(); return; }
        }catch(_){}
      }
      toast('Server may still be restarting — refresh manually','warn');
    },2000);
  }catch(e){
    toast('Restart failed: '+e,'error');
    if(btn){btn.disabled=false;btn.textContent='↺ Restart Server';}
  }
}

async function serverShutdown(){
  if(!confirm('Shut down the server?\n\nYou will need to restart it manually from the command line.')) return;
  const btn=document.querySelector('[onclick="serverShutdown()"]');
  if(btn){btn.disabled=true;btn.textContent='Shutting down…';}
  try{
    await api('POST','/api/server/shutdown',{});
    toast('Server is shutting down…','warn');
    closeM('mset');
  }catch(e){
    // A network error here just means the server already stopped — that's fine
    toast('Server shut down','warn');
    closeM('mset');
  }
}

// ── GROUP MANAGEMENT ──────────────────────────────────────────────

let _groupsCache = null;

async function _groupsLoad(){
  const wrap = document.getElementById('group-list');
  if(!wrap) return;
  try{
    const r = await api('GET','/api/user/groups');
    _groupsCache = r.groups || [];
    wrap.innerHTML = _groupsRender(_groupsCache);
  }catch(e){
    wrap.innerHTML='<div style="color:var(--err);font-size:12px">Failed to load groups.</div>';
  }
}

function _groupsRender(groups){
  if(!groups.length) return '<div style="color:var(--text3);font-size:12px;padding:8px 0">No groups yet. Create one to use as alert email recipients.</div>';
  const rows=groups.map(g=>`
    <tr>
      <td><strong>${esc(g.name)}</strong></td>
      <td style="color:var(--text3)">${esc(g.description||'')}</td>
      <td style="text-align:center">${g.member_count}</td>
      <td><div class="usr-act">
        <button onclick="_groupsOpenEditor(${g.id})">✏ Edit</button>
        <button class="del" onclick="_groupsDelete(${g.id},'${esc(g.name)}')">🗑 Delete</button>
      </div></td>
    </tr>`).join('');
  return `<table class="usr-table">
    <thead><tr><th>Name</th><th>Description</th><th style="text-align:center">Members</th><th>Actions</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function _groupsOpenEditor(id){
  // Fetch users to build member list
  let users=[], group=null;
  try{
    const ur=await api('GET','/api/users');
    users=ur.users||[];
  }catch(_){}
  if(id){
    group=(_groupsCache||[]).find(g=>g.id===id)||null;
  }
  const memberUsernames=new Set(users.filter(u=>u.group_id===id).map(u=>u.username));
  const memberList=users.map(u=>`
    <label style="display:flex;align-items:center;gap:6px;padding:3px 0;cursor:pointer">
      <input type="checkbox" data-uname="${esc(u.username)}" ${memberUsernames.has(u.username)?'checked':''}/>
      <span>${esc(u.username)}</span>
      <span style="color:var(--text3);font-size:11px">${esc(u.role)}</span>
    </label>`).join('');

  closeM('m-grp-ed');
  const o=document.createElement('div'); o.className='mo'; o.id='m-grp-ed';
  _overlayClose(o,()=>closeM('m-grp-ed'));
  o.innerHTML=`
  <div class="mbox" style="max-width:420px">
    <div class="mhd">
      <div class="mttl">${id?'Edit Group':'New Group'}</div>
      <button class="mclose" onclick="closeM('m-grp-ed')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">Name</label>
        <input type="text" id="grp-name" value="${esc(group?.name||'')}" placeholder="NOC Team" maxlength="100" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Description</label>
        <input type="text" id="grp-desc" value="${esc(group?.description||'')}" placeholder="Optional description" maxlength="500" autocomplete="off"/></div>
      <div class="fr"><label class="fl" style="margin-bottom:6px">Members</label>
        <div style="max-height:180px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px">
          ${memberList||'<span style="color:var(--text3);font-size:12px">No users found.</span>'}
        </div>
        <div class="fh">A user can belong to only one group. Changing group here removes them from their previous group.</div>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('m-grp-ed')">Cancel</button>
      <button class="btn-p" onclick="_groupsSave(${id||'null'})">Save</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('grp-name')?.focus(),50);
}

async function _groupsSave(id){
  const name=(document.getElementById('grp-name')?.value||'').trim();
  const desc=(document.getElementById('grp-desc')?.value||'').trim();
  if(!name){toast('Group name is required','err');return;}
  const btn=document.querySelector('#m-grp-ed .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  try{
    let r;
    if(id){
      r=await api('PATCH',`/api/user/group/${id}`,{name,description:desc});
    }else{
      r=await api('POST','/api/user/group',{name,description:desc});
      id=r.id;
    }
    if(r.error){toast(r.error,'err');return;}
    // Save members
    if(id){
      const checks=document.querySelectorAll('#m-grp-ed [data-uname]');
      const usernames=Array.from(checks).filter(c=>c.checked).map(c=>c.dataset.uname);
      await api('PUT',`/api/user/group/${id}/members`,{usernames});
    }
    _groupsCache=r.groups||_groupsCache;
    const wrap=document.getElementById('group-list');
    if(wrap) wrap.innerHTML=_groupsRender(_groupsCache||[]);
    // Refresh user table too (group assignments changed)
    const uw=document.getElementById('userTableWrap');
    if(uw){
      const ur=await api('GET','/api/users');
      uw.innerHTML=renderUserTable(ur.users||[]);
    }
    if(typeof _aeInvalidateGroups==='function') _aeInvalidateGroups();
    closeM('m-grp-ed');
    toast(id?`Group "${name}" saved`:`Group "${name}" created`,'ok');
  }catch(e){
    toast('Failed to save group','err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save';}
  }
}

async function _groupsDelete(id, name){
  if(!confirm(`Delete group "${name}"?\n\nMembers will be unassigned from this group. Alert rules using this group will stop sending emails to it.`)) return;
  const r=await api('DELETE',`/api/user/group/${id}`);
  if(r.error){toast(r.error,'err');return;}
  _groupsCache=r.groups||[];
  const wrap=document.getElementById('group-list');
  if(wrap) wrap.innerHTML=_groupsRender(_groupsCache);
  // Refresh user table
  const uw=document.getElementById('userTableWrap');
  if(uw){
    const ur=await api('GET','/api/users');
    uw.innerHTML=renderUserTable(ur.users||[]);
  }
  if(typeof _aeInvalidateGroups==='function') _aeInvalidateGroups();
  toast(`Group "${name}" deleted`,'ok');
}

// ── USER PROFILE MODAL (admin path, opens from Users tab) ─────────

async function _openUserProfileModal(username){
  // Fetch current data from /api/users (admin) or /api/me (self)
  let userData=null;
  let isMe=false;
  let callerRole='viewer';
  try{
    const me=await api('GET','/api/me');
    isMe=(me.username===username);
    callerRole=me.role||'viewer';
  }catch(_){}

  try{
    const ur=await api('GET','/api/users');
    userData=(ur.users||[]).find(u=>u.username===username)||null;
  }catch(_){}

  const groups=await (async()=>{
    try{ const r=await api('GET','/api/user/groups'); return r.groups||[]; }catch(_){return [];}
  })();

  const isAdmin=(callerRole==='admin');
  const fullName=userData?.full_name||'';
  const email=userData?.email||'';
  const groupId=userData?.group_id??'';
  const role=userData?.role||'viewer';

  const groupOpts=['<option value="">— No group —</option>',
    ...groups.map(g=>`<option value="${g.id}" ${g.id===userData?.group_id?'selected':''}>${esc(g.name)}</option>`)
  ].join('');
  const roleOpts=['viewer','operator','admin'].map(r=>
    `<option value="${r}" ${r===role?'selected':''}>${r}</option>`).join('');

  closeM('m-uprof');
  const o=document.createElement('div'); o.className='mo'; o.id='m-uprof';
  _overlayClose(o,()=>closeM('m-uprof'));
  o.innerHTML=`
  <div class="mbox" style="max-width:400px">
    <div class="mhd">
      <div class="mttl">Edit Profile — ${esc(username)}</div>
      <button class="mclose" onclick="closeM('m-uprof')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">Full Name</label>
        <input type="text" id="uprof-name" value="${esc(fullName)}" placeholder="Jane Doe" maxlength="200" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Email</label>
        <input type="email" id="uprof-email" value="${esc(email)}" placeholder="jane@corp.com" maxlength="200" autocomplete="off"/></div>
      ${isAdmin?`
      <div class="fr"><label class="fl">Group</label>
        <select id="uprof-group">${groupOpts}</select></div>
      <div class="fr"><label class="fl">Role</label>
        <select id="uprof-role">${roleOpts}</select></div>`:''}
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('m-uprof')">Cancel</button>
      <button class="btn-p" onclick="_submitUserProfile('${esc(username)}',${isAdmin})">Save</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('uprof-name')?.focus(),50);
}

async function _submitUserProfile(username, isAdmin){
  const full_name=(document.getElementById('uprof-name')?.value||'').trim();
  const email=(document.getElementById('uprof-email')?.value||'').trim();
  const btn=document.querySelector('#m-uprof .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  try{
    let body={full_name,email};
    if(isAdmin){
      const gv=document.getElementById('uprof-group')?.value;
      body.group_id = gv===''?null:parseInt(gv);
      body.role=(document.getElementById('uprof-role')?.value||'');
    }
    const r=await api('PATCH',`/api/users/${encodeURIComponent(username)}/profile`,body);
    if(r.error){toast(r.error,'err');return;}
    closeM('m-uprof');
    // Refresh user table
    const uw=document.getElementById('userTableWrap');
    if(uw){
      const ur=await api('GET','/api/users');
      uw.innerHTML=renderUserTable(ur.users||[]);
    }
    toast('Profile saved','ok');
  }catch(e){
    toast('Failed to save profile','err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save';}
  }
}
