// ── Settings modal (General, Alerts, Database, Audit, Sensors, Networking) ─

async function openSettings(){
  closeM('mset');
  const [sr, ur, tr] = await Promise.all([
    api('GET','/api/settings'),
    api('GET','/api/users'),
    api('GET','/api/tls'),
  ]);
  window._tlsSettings = {...tr, org_name: sr.org_name||''};
  const o=document.createElement('div'); o.className='mo'; o.id='mset';
  _overlayClose(o, ()=>closeM('mset'));
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
  <div class="mbox" style="width:900px;max-width:96vw">
    <div class="mhd">
      <div class="mttl">⚙ Settings</div>
      <button class="mclose" onclick="closeM('mset')">✕</button>
    </div>
    <div class="dw-tabs" style="padding:0 4px">
      <button class="dw-tab active" id="stab-btn-general" onclick="switchSettingsTab('general')">General</button>
      <button class="dw-tab" id="stab-btn-users" onclick="switchSettingsTab('users')">Users</button>
      <button class="dw-tab" id="stab-btn-smtp" onclick="switchSettingsTab('smtp')">SMTP</button>
      <button class="dw-tab" id="stab-btn-database" onclick="switchSettingsTab('database')">Database</button>
      <button class="dw-tab" id="stab-btn-logs" onclick="switchSettingsTab('logs')">Logs</button>
      <button class="dw-tab" id="stab-btn-sensors" onclick="switchSettingsTab('sensors')">Sensors</button>
      <button class="dw-tab" id="stab-btn-networking" onclick="switchSettingsTab('networking')">Networking</button>
      <button class="dw-tab" id="stab-btn-backup" onclick="switchSettingsTab('backup')">Config Backup</button>
      <button class="dw-tab" id="stab-btn-syslog" onclick="switchSettingsTab('syslog')">Syslog</button>
      <button class="dw-tab" id="stab-btn-alert-rules" onclick="switchSettingsTab('alert-rules')">Alert Rules</button>
      <button class="dw-tab" id="stab-btn-maint"       onclick="switchSettingsTab('maint')">Maintenance</button>
    </div>
    <div class="mbdy stab-fade" id="stab-general" style="max-height:72vh;overflow-y:auto">
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
    <div class="mbdy stab-fade" id="stab-users" style="display:none;padding-top:8px">
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
    <div class="mbdy stab-fade" id="stab-smtp" style="display:none">
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
        Emails are sent on sensor DOWN and RECOVERED events (after fail_after / recover_after debounce).
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
    <div class="mft" id="stab-footer-smtp" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveSettings()">Save Settings</button>
    </div>
    <div class="mbdy stab-fade" id="stab-database" style="display:none">

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
        <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:16px">Scheduled Database Backup</div>
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
      </div>
    </div>
    <div class="mft" id="stab-footer-database" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-p" onclick="saveDbBackupSettings()">Save DB Backup</button>
    </div>
    <div class="mbdy stab-fade" id="stab-logs" style="display:none;padding:0">
      <div class="log-subtab-bar">
        <button class="log-stab active" id="lstab-btn-app"     onclick="_switchLogTab('app')">Application</button>
        <button class="log-stab"        id="lstab-btn-sensors" onclick="_switchLogTab('sensors')">Sensors</button>
        <button class="log-stab"        id="lstab-btn-audit"   onclick="_switchLogTab('audit')">Audit</button>
        <button class="log-stab"        id="lstab-btn-backup"  onclick="_switchLogTab('backup')">Backup</button>
        <button class="btn-s" onclick="_loadLogTab()" style="margin-left:auto;font-size:11px">↻ Refresh</button>
      </div>
      <div id="log-body" class="log-viewer"><span style="color:var(--text3)">Loading…</span></div>
    </div>
    <div class="mft" id="stab-footer-logs" style="display:none">
      <span id="log-footer-label" style="font-size:11px;color:var(--text3)">Last 500 lines · admin only</span>
    </div>
    <div class="mbdy stab-fade" id="stab-sensors" style="display:none;max-height:72vh;overflow-y:auto">
      <div style="padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid var(--border)">
        <div class="fl" style="margin-bottom:6px">Global Defaults</div>
        <div class="fh" style="margin-bottom:10px">Fallback values applied to all new sensors — override per type below.</div>
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
    <div class="mbdy stab-fade" id="stab-networking" style="display:none;max-height:72vh;overflow-y:auto">
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

        <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
          <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:10px">Certificate</div>
          ${(()=>{
            const c=tr.cert||{};
            if(!c.subject) return '<div style="font-size:12px;color:var(--text3)">No certificate loaded. Enable HTTPS and save — a self-signed certificate will be generated automatically on the next startup.</div>';
            const daysLeft=c.days_left??0;
            const badgeColor=daysLeft<0?'var(--err)':daysLeft<=30?'var(--warn)':'var(--ok)';
            const badgeTxt=daysLeft<0?'EXPIRED':(daysLeft<=30?`⚠ ${daysLeft}d left`:`✓ ${daysLeft}d`);
            const srcLabel={'generated':'Auto-generated (self-signed)','imported':'Imported from certs/ folder','uploaded':'Manually uploaded','db':'Loaded from database'}[c.source]||c.source||'—';
            return `<div style="display:grid;grid-template-columns:130px 1fr;gap:5px 10px;font-size:12px">
              <span style="color:var(--text3)">Subject</span><span>${esc(c.subject||'—')}</span>
              <span style="color:var(--text3)">Issuer</span><span>${esc(c.issuer||'—')}${c.self_signed?' <span style="color:var(--text3)">(self-signed)</span>':''}</span>
              <span style="color:var(--text3)">Expires</span><span>${esc(c.not_after||'—')} <span style="color:${badgeColor};font-weight:600">${badgeTxt}</span></span>
              <span style="color:var(--text3)">Source</span><span>${esc(srcLabel)}</span>
            </div>`;
          })()}
          <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
            <button class="btn-s" onclick="openUploadCert()">Upload Certificate</button>
            <button class="btn-s" id="btn-gen-cert" onclick="generateNewCert()">Generate New Self-Signed</button>
          </div>
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
    <div class="mbdy stab-fade" id="stab-backup" style="display:none;max-height:72vh;overflow-y:auto">
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
    <div class="mbdy stab-fade" id="stab-syslog" style="display:none;max-height:72vh;overflow-y:auto">
      <div style="font-size:12px;font-weight:600;color:var(--text2);margin-bottom:16px">Syslog Forwarding</div>
      <div class="fr" style="display:flex;align-items:center;justify-content:space-between;gap:12px">
        <div style="flex:1">
          <div style="font-size:11px;font-weight:500;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px">Enable Syslog Forwarding</div>
          <div class="fh" style="margin:0">Send events (device down/up, SNMP traps) to an external syslog or SIEM server in real time</div>
        </div>
        <label class="toggle" style="flex-shrink:0"><input type="checkbox" id="st-sl-enabled" ${sr.syslog_enabled?'checked':''}><span class="tsl"></span></label>
      </div>
      <div class="fr" style="margin-top:14px">
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
    </div>
    <div class="mft" id="stab-footer-syslog" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
      <button class="btn-s" onclick="testSyslog()">Send Test Message</button>
      <button class="btn-p" onclick="saveSyslogSettings()">Save Syslog</button>
    </div>
    <div class="mbdy stab-fade" id="stab-alert-rules" style="display:none;max-height:72vh;overflow-y:auto">
      <div class="alrt-panel-hdr">
        <span style="color:var(--text3);font-size:12px">Rules are evaluated in order for every sensor event.</span>
        <button class="btn-p rbac-admin" onclick="_alertingOpenEditor(null)">＋ New Rule</button>
      </div>
      <div id="alrt-list"><div class="alrt-loading">Loading…</div></div>
    </div>
    <div class="mft" id="stab-footer-alert-rules" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
    </div>
    <div class="mbdy stab-fade" id="stab-maint" style="display:none;max-height:72vh;overflow-y:auto">
      <div class="alrt-panel-hdr">
        <span style="color:var(--text3);font-size:12px">Suppress alerts during scheduled maintenance. Rules still evaluate but no notifications are sent.</span>
        <button class="btn-p rbac-admin" onclick="_alertMaintOpen(null)">＋ New Window</button>
      </div>
      <div id="alrt-maint-list"><div class="alrt-loading">Loading…</div></div>
    </div>
    <div class="mft" id="stab-footer-maint" style="display:none">
      <button class="btn-s" onclick="closeM('mset')">Close</button>
    </div>
  </div>`;
  document.body.appendChild(o);
}

let _stabSwitching = false;
function switchSettingsTab(tab){
  if (_stabSwitching) return;
  const tabs = ['general','users','smtp','database','logs','sensors','networking','backup','syslog','alert-rules','maint'];

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
    // Lock current height so the box doesn't jump
    const startH = mbox.offsetHeight;
    mbox.style.height = startH + 'px';
    mbox.classList.add('stab-anim');

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

      // Measure target height against placeholder ("Loading…") state — before content loads
      mbox.style.height = 'auto';
      const endH = mbox.offsetHeight;
      mbox.style.height = startH + 'px';

      // Animate height + fade in (double-rAF ensures browser paints opacity:0 before transitioning)
      requestAnimationFrame(() => {
        mbox.style.height = endH + 'px';
        requestAnimationFrame(() => {
          nextEl.classList.remove('stab-out');
          // Load content AFTER animation so it never races with the height transition
          setTimeout(() => {
            mbox.style.height = '';
            mbox.classList.remove('stab-anim');
            _stabSwitching = false;
            if (tab === 'logs')        _loadLogTab();
            if (tab === 'sensors')     loadSensorsDefaultsTab();
            if (tab === 'backup')      _loadBackupScheduleSettings();
            if (tab === 'database')    _loadDbBackupSettings();
            if (tab === 'alert-rules') _alertingLoadRules();
            if (tab === 'maint')       _alertingLoadMaint();
          }, 280);
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
    if (tab === 'maint')       _alertingLoadMaint();
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

function openUploadCert(){
  closeM('muc');
  const o=document.createElement('div');o.className='mo';o.id='muc';
  _overlayClose(o,()=>closeM('muc'));
  o.innerHTML=`
  <div class="mbox" style="width:560px;max-width:96vw">
    <div class="mhd"><div class="mttl">Upload Certificate</div><button class="mclose" onclick="closeM('muc')">✕</button></div>
    <div class="mbdy">
      <div class="fr">
        <label class="fl">Certificate (PEM)</label>
        <textarea id="uc-cert" rows="7" style="font-family:monospace;font-size:11px;resize:vertical" placeholder="-----BEGIN CERTIFICATE-----&#10;...&#10;-----END CERTIFICATE-----"></textarea>
      </div>
      <div class="fr" style="margin-top:12px">
        <label class="fl">Private Key (PEM)</label>
        <textarea id="uc-key" rows="7" style="font-family:monospace;font-size:11px;resize:vertical" placeholder="-----BEGIN RSA PRIVATE KEY-----&#10;...&#10;-----END RSA PRIVATE KEY-----"></textarea>
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

async function submitUploadCert(){
  const cert_pem=(document.getElementById('uc-cert')?.value||'').trim();
  const key_pem =(document.getElementById('uc-key')?.value||'').trim();
  const errEl=document.getElementById('uc-err');
  const btn=document.getElementById('btn-uc-save');
  if(!cert_pem||!key_pem){errEl.textContent='Both certificate and private key are required.';errEl.style.display='';return;}
  btn.disabled=true;btn.textContent='Validating...';
  errEl.style.display='none';
  let r;
  try{
    r=await api('POST','/api/tls/upload',{cert_pem,key_pem});
  }catch(e){
    errEl.textContent='Request failed — check server connectivity.';errEl.style.display='';
    btn.disabled=false;btn.textContent='Validate & Save';
    return;
  }
  if(r.error){
    errEl.textContent=r.error;errEl.style.display='';
    btn.disabled=false;btn.textContent='Validate & Save';
    return;
  }
  closeM('muc');
  toast('Certificate uploaded — restart the server to apply','ok');
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

let _activeLogTab = 'app';

function _switchLogTab(key) {
  _activeLogTab = key;
  ['app','sensors','audit','backup'].forEach(k => {
    document.getElementById(`lstab-btn-${k}`)?.classList.toggle('active', k === key);
  });
  _loadLogTab();
}

function _colorLog(text) {
  if (!text) return '<span style="color:var(--text3)">(empty)</span>';
  const e = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(INFO|WARNING|WARN|ERROR|CRITICAL|DEBUG)\s+(.*)/;
  return text.split('\n').map(line => {
    if (!line) return '<div class="ll-row ll-empty"></div>';
    const m = line.match(RE);
    if (m) {
      const [,ts,lvl,msg] = m;
      return `<div class="ll-row"><span class="ll-pre"><span class="ll-ts">${e(ts)}</span><span class="ll-${lvl.toLowerCase()}">${e(lvl)}</span></span><span class="ll-msg">${e(msg)}</span></div>`;
    }
    return `<div class="ll-row ll-cont"><span class="ll-msg">${e(line)}</span></div>`;
  }).join('');
}

async function _loadLogTab() {
  const el  = document.getElementById('log-body');
  const lbl = document.getElementById('log-footer-label');
  if (!el) return;
  try {
    const r = await fetch(`/api/logs/${_activeLogTab}`);
    if (!r.ok) { el.textContent = 'Access denied'; return; }
    const d = await r.json();
    el.innerHTML = _colorLog(d.lines || '(empty)');
    el.scrollTop = el.scrollHeight;
    const names = { app:'pingwatch.log', sensors:'pingwatchsensors.log',
                    audit:'pingwatchaudit.log', backup:'pingwatchbackup.log' };
    if (lbl) lbl.textContent = `Last 500 lines · admin only · ${names[_activeLogTab] || ''}`;
  } catch(e) {
    el.textContent = `Failed to load: ${String(e)}`;
  }
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
    const fa  = d.fail_after    != null ? d.fail_after    : (window._snrDef?.fail_after||1);
    const ra  = d.recover_after != null ? d.recover_after : (window._snrDef?.recover_after||1);
    const wm  = d.warn_ms  != null ? d.warn_ms  : (_SDR_WARN_DEF[t] || '');
    const cm  = d.crit_ms  != null ? d.crit_ms  : (_SDR_CRIT_DEF[t] || '');
    const warnUnit = t==='tls'?'days':t==='snmp'?'val':'ms';
    const extra = _sdrExtraFields(t, d);
    return `<tr class="sdr-card sdr-row" data-type="${t}">
      <td><div class="sdr-type-cell"><span class="sdr-icon" title="${m.desc}">${m.ico}</span><span class="sdr-lbl">${m.label}</span></div></td>
      <td style="text-align:center"><span class="sdr-cnt">${cnt}</span></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_interval" value="${iv}" min="1" max="300"/><span class="sdr-unit">s</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_timeout" value="${to}" min="1" max="60"/><span class="sdr-unit">s</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_fail_after" value="${fa}" min="1" max="60"/><span class="sdr-unit">×</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_recover_after" value="${ra}" min="1" max="60"/><span class="sdr-unit">×</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_warn_ms" value="${wm}" min="1" placeholder="—"/><span class="sdr-unit">${warnUnit}</span></div></td>
      <td><div class="sdr-num-cell"><input type="number" id="sdr_${t}_crit_ms" value="${cm}" min="1" placeholder="—"/><span class="sdr-unit">${warnUnit}</span></div></td>
      <td style="text-align:center">${extra ? `<button class="sdr-expand-btn" onclick="_sdrToggle(this)" title="Type-specific settings">▾</button>` : ''}</td>
    </tr>
    ${extra ? `<tr class="sdr-extra-row" data-for="${t}" style="display:none"><td colspan="9"><div class="sdr-extra">${extra}</div></td></tr>` : ''}`;
  }).join('');
  el.innerHTML = `<table class="sdr-tbl">
    <thead><tr>
      <th>Type</th><th>#</th>
      <th>Interval</th><th>Timeout</th><th>Fail After</th><th>Recover After</th>
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
  const snrIv  = parseInt(document.getElementById('st-snr-iv')?.value);
  const snrTmo = parseInt(document.getElementById('st-snr-tmo')?.value);
  const snrFa  = parseInt(document.getElementById('st-snr-fa')?.value);
  const snrRa  = parseInt(document.getElementById('st-snr-ra')?.value);
  const globalDefaults = {};
  if(snrIv  >= 1) globalDefaults.snr_interval     = snrIv;
  if(snrTmo >= 1) globalDefaults.snr_timeout       = snrTmo;
  if(snrFa  >= 1) globalDefaults.snr_fail_after    = snrFa;
  if(snrRa  >= 1) globalDefaults.snr_recover_after = snrRa;
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
  if(globalDefaults.snr_fail_after)   window._snrDef.fail_after   = globalDefaults.snr_fail_after;
  if(globalDefaults.snr_recover_after)window._snrDef.recover_after = globalDefaults.snr_recover_after;
  toast('Sensor defaults saved','ok');
}

function renderUserTable(users){
  if(!users||!users.length) return '<div style="color:var(--text3);font-size:12px;padding:8px 0">No users found.</div>';
  const rows=users.map(u=>{
    const isLdap=u.auth_type==='ldap';
    const badge=isLdap
      ?`<span class="usr-badge-ldap">🌐 Domain</span>`
      :`<span class="usr-badge-local">🔑 Local</span>`;
    const resetBtn=isLdap?'':`<button onclick="openResetPw('${esc(u.username)}')">🔑 Reset Password</button>`;
    return `
    <tr>
      <td><strong>${esc(u.username)}</strong></td>
      <td><span style="color:var(--text2)">${esc(u.role)}</span></td>
      <td>${badge}</td>
      <td><div class="usr-act">
        ${resetBtn}
        <button class="del" onclick="deleteUser('${esc(u.username)}')">🗑 Delete</button>
      </div></td>
    </tr>`;
  }).join('');
  return `<table class="usr-table">
    <thead><tr><th>Username</th><th>Role</th><th>Auth</th><th>Actions</th></tr></thead>
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

async function _loadDbBackupSettings(){
  _loadDbStats();
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

async function saveSyslogSettings(){
  const enabled  = document.getElementById('st-sl-enabled')?.checked ? 1 : 0;
  const host     = (document.getElementById('st-sl-host')?.value   || '').trim();
  const port     = parseInt(document.getElementById('st-sl-port')?.value) || 514;
  const proto    = document.getElementById('st-sl-proto')?.value   || 'udp';
  const minSev   = document.getElementById('st-sl-minsev')?.value  || 'warning';
  if(enabled && !host){ toast('Enter a syslog server address','err'); return; }
  const btn = document.querySelector('#stab-footer-syslog .btn-p');
  if(btn){ btn.disabled=true; btn.textContent='Saving...'; }
  try {
    const r = await api('PATCH', '/api/settings', {
      syslog_enabled:      enabled,
      syslog_host:         host,
      syslog_port:         port,
      syslog_proto:        proto,
      syslog_min_severity: minSev,
    });
    if(!r?.ok){ toast('Failed to save syslog settings','err'); return; }
    toast('Syslog settings saved','ok');
  } catch(e) {
    toast('Failed to save syslog settings','err');
  } finally {
    if(btn){ btn.disabled=false; btn.textContent='Save Syslog'; }
  }
}

async function testSyslog(){
  const btn = document.querySelector('[onclick="testSyslog()"]');
  if(btn){ btn.disabled=true; btn.textContent='Sending...'; }
  try {
    const r = await api('POST', '/api/settings/syslog_test', {});
    toast(r?.ok ? r.msg || 'Test message sent' : `Failed: ${r?.msg||'Unknown error'}`,
          r?.ok ? 'ok' : 'err');
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
