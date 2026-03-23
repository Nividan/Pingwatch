// ── LDAP / Active Directory Settings ───────────────────────────────────────

async function openLdapSettings() {
  closeM('mldap');
  // Fetch current settings
  let s;
  try {
    s = await api('GET', '/api/ldap/settings');
  } catch(e) {
    toast('Failed to load LDAP settings', 'err');
    return;
  }
  if (s.error) { toast(s.error, 'err'); return; }

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'mldap';
  _overlayClose(o, () => closeM('mldap'));
  o.innerHTML = _ldapRenderModal(s);
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ldap-server')?.focus(), 50);
}

function _ldapRenderModal(s) {
  const checked      = s.ldap_enabled ? 'checked' : '';
  const dbgChecked   = s.ldap_debug   ? 'checked' : '';
  const sslVal  = s.ldap_ssl ?? 0;
  const passPlaceholder = s.ldap_bind_pass_set ? '●●●●●●●● (set — leave blank to keep)' : 'bind password';
  return `
  <div class="mbox" style="max-width:560px;width:96vw">
    <div class="mhd">
      <div class="mttl">🔐 LDAP / Active Directory Settings</div>
      <button class="mclose" onclick="closeM('mldap')">✕</button>
    </div>
    <div class="mbdy" style="display:flex;flex-direction:column;gap:10px">

      <div class="fr" style="align-items:center;gap:10px">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:13px;color:var(--text)">
          <input type="checkbox" id="ldap-enabled" ${checked} style="width:15px;height:15px;cursor:pointer"/>
          Enable LDAP / Active Directory Authentication
        </label>
      </div>

      <div class="fh" style="margin:0">
        Domain users must be added in the Users list. Their passwords are verified against this server at login.
      </div>

      <div class="fgrid" style="margin-top:4px">
        <div class="fr"><label class="fl">LDAP Server</label>
          <input type="text" id="ldap-server" value="${esc(s.ldap_server||'')}"
            placeholder="dc.example.com or 192.168.1.10" autocomplete="off"/></div>
        <div class="fr"><label class="fl">Port</label>
          <input type="number" id="ldap-port" value="${s.ldap_port||389}"
            min="1" max="65535" style="max-width:100px"/></div>
      </div>

      <div class="fr"><label class="fl">Security</label>
        <select id="ldap-ssl" style="max-width:240px" onchange="_ldapSslChange()">
          <option value="0" ${sslVal===0?'selected':''}>None — plain LDAP (port 389)</option>
          <option value="1" ${sslVal===1?'selected':''}>LDAPS — TLS from start (port 636)</option>
          <option value="2" ${sslVal===2?'selected':''}>StartTLS — upgrade connection (port 389)</option>
        </select>
      </div>

      <div class="fr"><label class="fl">Base DN</label>
        <input type="text" id="ldap-base-dn" value="${esc(s.ldap_base_dn||'')}"
          placeholder="DC=example,DC=com" autocomplete="off"/></div>

      <div class="fr"><label class="fl">Bind DN</label>
        <input type="text" id="ldap-bind-dn" value="${esc(s.ldap_bind_dn||'')}"
          placeholder="CN=svc-pingwatch,OU=Service Accounts,DC=example,DC=com" autocomplete="off"/></div>

      <div class="fr"><label class="fl">Bind Password</label>
        <input type="password" id="ldap-bind-pass" placeholder="${passPlaceholder}" autocomplete="new-password"/></div>

      <div class="fr"><label class="fl">User Search Filter</label>
        <input type="text" id="ldap-user-filter" value="${esc(s.ldap_user_filter||'(sAMAccountName={username})')}"
          placeholder="(sAMAccountName={username})" autocomplete="off"/>
      </div>
      <div class="fh" style="margin-top:-4px">Use <code style="font-family:monospace;color:var(--accent)">{username}</code> as the placeholder for the login name.</div>

      <div class="fgrid">
        <div class="fr"><label class="fl">NetBIOS Domain</label>
          <input type="text" id="ldap-domain" value="${esc(s.ldap_domain||'')}"
            placeholder="EXAMPLE (optional)" autocomplete="off"/></div>
        <div class="fr"><label class="fl">Timeout (s)</label>
          <input type="number" id="ldap-timeout" value="${s.ldap_timeout||10}"
            min="1" max="120" style="max-width:80px"/></div>
      </div>

      <div style="display:flex;gap:8px;margin-top:6px;align-items:center;flex-wrap:wrap">
        <button class="btn-s" style="font-size:12px" onclick="testLdapConnection()">▶ Test Connection</button>
        <button class="btn-s" style="font-size:12px" onclick="openLdapTestAuth()">▶ Test User Auth</button>
        <div id="ldap-test-result" style="font-size:12px;flex:1"></div>
      </div>

      <div style="border-top:1px solid var(--border);margin-top:4px;padding-top:10px">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-size:12px;color:var(--text2)">
          <input type="checkbox" id="ldap-debug" ${dbgChecked} style="width:14px;height:14px;cursor:pointer"/>
          Enable debug logging — logs TCP, BIND, and search steps for each authentication attempt
        </label>
      </div>

    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mldap')">Cancel</button>
      <button class="btn-p" onclick="submitLdapSettings()">Save LDAP Settings</button>
    </div>
  </div>`;
}

function _ldapSslChange() {
  const ssl = parseInt(document.getElementById('ldap-ssl')?.value || '0');
  const portEl = document.getElementById('ldap-port');
  if (!portEl) return;
  if (ssl === 1 && portEl.value === '389') portEl.value = '636';
  if (ssl !== 1 && portEl.value === '636') portEl.value = '389';
}

function _ldapCollectForm() {
  return {
    ldap_enabled:     document.getElementById('ldap-enabled')?.checked ? 1 : 0,
    ldap_debug:       document.getElementById('ldap-debug')?.checked   ? 1 : 0,
    ldap_server:      (document.getElementById('ldap-server')?.value || '').trim(),
    ldap_port:        parseInt(document.getElementById('ldap-port')?.value || '389'),
    ldap_ssl:         parseInt(document.getElementById('ldap-ssl')?.value || '0'),
    ldap_base_dn:     (document.getElementById('ldap-base-dn')?.value || '').trim(),
    ldap_bind_dn:     (document.getElementById('ldap-bind-dn')?.value || '').trim(),
    ldap_bind_pass:   document.getElementById('ldap-bind-pass')?.value || '',
    ldap_user_filter: (document.getElementById('ldap-user-filter')?.value || '').trim(),
    ldap_domain:      (document.getElementById('ldap-domain')?.value || '').trim(),
    ldap_timeout:     parseInt(document.getElementById('ldap-timeout')?.value || '10'),
  };
}

function _ldapShowResult(ok, msg) {
  const el = document.getElementById('ldap-test-result');
  if (!el) return;
  el.innerHTML = ok
    ? `<span style="color:var(--up)">✔ ${esc(msg)}</span>`
    : `<span style="color:var(--down)">✘ ${esc(msg)}</span>`;
}

async function testLdapConnection() {
  const body = _ldapCollectForm();
  _ldapShowResult(null, '');
  const el = document.getElementById('ldap-test-result');
  if (el) el.innerHTML = '<span style="color:var(--text3)">Testing…</span>';
  let r;
  try {
    r = await api('POST', '/api/ldap/test_connection', body);
  } catch(e) {
    _ldapShowResult(false, 'Request failed'); return;
  }
  _ldapShowResult(r.ok, r.message || (r.ok ? 'Success' : 'Failed'));
}

// Mini sub-dialog for testing a specific user's credentials
function openLdapTestAuth() {
  closeM('mldap-ta');
  const o = document.createElement('div'); o.className = 'mo'; o.id = 'mldap-ta';
  _overlayClose(o, () => closeM('mldap-ta'));
  o.innerHTML = `
  <div class="mbox" style="max-width:380px">
    <div class="mhd">
      <div class="mttl">Test User Authentication</div>
      <button class="mclose" onclick="closeM('mldap-ta')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="ldap-ta-user" placeholder="jsmith" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Password</label>
        <input type="password" id="ldap-ta-pass" placeholder="AD password" autocomplete="new-password"/></div>
      <div id="ldap-ta-result" style="font-size:12px;margin-top:6px;min-height:18px"></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mldap-ta')">Close</button>
      <button class="btn-p" onclick="submitLdapTestAuth()">Test</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ldap-ta-user')?.focus(), 50);
}

async function submitLdapTestAuth() {
  const username = (document.getElementById('ldap-ta-user')?.value || '').trim();
  const password = document.getElementById('ldap-ta-pass')?.value || '';
  const resEl = document.getElementById('ldap-ta-result');
  if (!username || !password) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">Username and password required</span>';
    return;
  }
  const btn = document.querySelector('#mldap-ta .btn-p');
  if (btn) { btn.disabled = true; btn.textContent = 'Testing…'; }
  if (resEl) resEl.innerHTML = '<span style="color:var(--text3)">Testing…</span>';
  let r;
  try {
    r = await api('POST', '/api/ldap/test_auth', {username, password});
  } catch(e) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">✘ Request failed</span>';
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Test'; }
  }
  if (resEl) {
    resEl.innerHTML = r.ok
      ? `<span style="color:var(--up)">✔ ${esc(r.message||'Authentication successful')}</span>`
      : `<span style="color:var(--down)">✘ ${esc(r.message||'Authentication failed')}</span>`;
  }
  // Mirror result to main modal if still open
  _ldapShowResult(r.ok, r.message || (r.ok ? 'Auth successful' : 'Auth failed'));
}

async function submitLdapSettings() {
  const body = _ldapCollectForm();
  const btn = document.querySelector('#mldap .btn-p');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  let r;
  try {
    r = await api('PATCH', '/api/ldap/settings', body);
  } catch(e) {
    toast('Failed to save LDAP settings', 'err');
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save LDAP Settings'; }
  }
  if (r.error) { toast(r.error, 'err'); return; }
  closeM('mldap');
  toast('LDAP settings saved', 'ok');
}
