// ── LDAP / Active Directory Settings ───────────────────────────────────────
// The LDAP panel lives in Settings → Integrations → LDAP / AD sub-tab.
// _loadLdapPanel() populates the fields; saveLdapSettings() saves them.

function _ldapSslChange() {
  const ssl = parseInt(document.getElementById('ldap-ssl')?.value || '0');
  const portEl = document.getElementById('ldap-port');
  if (!portEl) return;
  if (ssl === 1 && portEl.value === '389') portEl.value = '636';
  if (ssl !== 1 && portEl.value === '636') portEl.value = '389';
}

function _ldapCollectForm() {
  return {
    ldap_enabled:         document.getElementById('ldap-enabled')?.checked ? 1 : 0,
    ldap_server:          (document.getElementById('ldap-server')?.value || '').trim(),
    ldap_port:            parseInt(document.getElementById('ldap-port')?.value || '389'),
    ldap_ssl:             parseInt(document.getElementById('ldap-ssl')?.value || '0'),
    ldap_base_dn:         (document.getElementById('ldap-base-dn')?.value || '').trim(),
    ldap_bind_dn:         (document.getElementById('ldap-bind-dn')?.value || '').trim(),
    ldap_bind_pass:       document.getElementById('ldap-bind-pass')?.value || '',
    ldap_user_filter:     (document.getElementById('ldap-user-filter')?.value || '').trim(),
    ldap_domain:          (document.getElementById('ldap-domain')?.value || '').trim(),
    ldap_timeout:         parseInt(document.getElementById('ldap-timeout')?.value || '10'),
    ldap_auto_provision:  document.getElementById('ldap-auto-provision')?.checked ? 1 : 0,
    ldap_nested_groups:   document.getElementById('ldap-nested-groups')?.checked  ? 1 : 0,
    ldap_group_base_dn:   (document.getElementById('ldap-group-base-dn')?.value || '').trim(),
    ldap_group_filter:    (document.getElementById('ldap-group-filter')?.value || '').trim(),
    ldap_sync_interval:   parseInt(document.getElementById('ldap-sync-interval')?.value || '60'),
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

async function saveLdapSettings() {
  const body = _ldapCollectForm();
  const btn = document.getElementById('integ-btn-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  let r;
  try {
    r = await api('PATCH', '/api/ldap/settings', body);
  } catch(e) {
    toast('Failed to save LDAP settings', 'err');
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
  if (r.error) { toast(r.error, 'err'); return; }
  toast('LDAP settings saved', 'ok');
}


// ── Test User Auth sub-dialog ───────────────────────────────────────────────
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
  // Mirror result to main panel
  _ldapShowResult(r.ok, r.message || (r.ok ? 'Auth successful' : 'Auth failed'));
}


// ── Test User Groups sub-dialog ─────────────────────────────────────────────
function openLdapTestUserGroups() {
  closeM('mldap-tug');
  const o = document.createElement('div'); o.className = 'mo'; o.id = 'mldap-tug';
  _overlayClose(o, () => closeM('mldap-tug'));
  o.innerHTML = `
  <div class="mbox" style="max-width:520px">
    <div class="mhd">
      <div class="mttl">Test User LDAP Groups</div>
      <button class="mclose" onclick="closeM('mldap-tug')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fh" style="margin-bottom:8px">Enter an LDAP username to see their group memberships and attributes.</div>
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="ldap-tug-user" placeholder="jsmith" autocomplete="off"/></div>
      <div style="margin-top:8px">
        <button class="btn-p" style="font-size:12px" onclick="_ldapTestUserGroupsRun()">Look Up</button>
      </div>
      <div id="ldap-tug-result" style="font-size:12px;margin-top:10px;min-height:24px"></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mldap-tug')">Close</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ldap-tug-user')?.focus(), 50);
}

async function _ldapTestUserGroupsRun() {
  const username = (document.getElementById('ldap-tug-user')?.value || '').trim();
  const resEl = document.getElementById('ldap-tug-result');
  if (!username) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">Username is required</span>';
    return;
  }
  const btn = document.querySelector('#mldap-tug .btn-p');
  if (btn) { btn.disabled = true; btn.textContent = 'Looking up…'; }
  if (resEl) resEl.innerHTML = '<span style="color:var(--text3)">Querying LDAP…</span>';
  let r;
  try {
    r = await api('POST', '/api/ldap/test_user_groups', {username});
  } catch(e) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">Request failed</span>';
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Look Up'; }
  }
  if (!r.ok) {
    if (resEl) resEl.innerHTML = `<span style="color:var(--down)">&#10008; ${esc(r.message||'Lookup failed')}</span>`;
    return;
  }
  let html = `<div style="color:var(--up);margin-bottom:6px">&#10004; Found user</div>`;
  if (r.display_name) html += `<div><strong>Display Name:</strong> ${esc(r.display_name)}</div>`;
  if (r.email)        html += `<div><strong>Email:</strong> ${esc(r.email)}</div>`;
  const groups = r.groups || [];
  if (groups.length) {
    html += `<div style="margin-top:6px"><strong>Member of ${groups.length} group(s):</strong></div>`;
    html += '<div style="max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px;margin-top:4px;font-family:monospace;font-size:11px">';
    groups.forEach(g => { html += `<div style="padding:2px 0">${esc(g)}</div>`; });
    html += '</div>';
  } else {
    html += '<div style="margin-top:6px;color:var(--text3)">Not a member of any groups.</div>';
  }
  if (resEl) resEl.innerHTML = html;
}
