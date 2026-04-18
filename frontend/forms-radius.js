// ── RADIUS Settings ────────────────────────────────────────────────
// The RADIUS panel lives in Settings → Integrations → RADIUS sub-tab.
// Mirrors forms-ldap.js in shape. Supports PAP auth + Access-Challenge
// for server-side 2FA (FortiAuthenticator, NPS, FreeRADIUS, RSA).

function _radiusCollectForm() {
  return {
    radius_enabled:        document.getElementById('radius-enabled')?.checked ? 1 : 0,
    radius_server:         (document.getElementById('radius-server')?.value || '').trim(),
    radius_port:           parseInt(document.getElementById('radius-port')?.value || '1812'),
    radius_secret:         document.getElementById('radius-secret')?.value || '',
    radius_server2:        (document.getElementById('radius-server2')?.value || '').trim(),
    radius_port2:          parseInt(document.getElementById('radius-port2')?.value || '1812'),
    radius_secret2:        document.getElementById('radius-secret2')?.value || '',
    radius_timeout:        parseInt(document.getElementById('radius-timeout')?.value || '5'),
    radius_retries:        parseInt(document.getElementById('radius-retries')?.value || '3'),
    radius_nas_identifier: (document.getElementById('radius-nas-identifier')?.value || 'pingwatch').trim(),
    radius_realm_prefix:   (document.getElementById('radius-realm-prefix')?.value || ''),
    radius_realm_suffix:   (document.getElementById('radius-realm-suffix')?.value || ''),
    radius_auto_provision: document.getElementById('radius-auto-provision')?.checked ? 1 : 0,
    radius_default_role:   document.getElementById('radius-default-role')?.value || 'viewer',
    radius_debug:          document.getElementById('radius-debug')?.checked ? 1 : 0,
  };
}

function _radiusShowResult(ok, msg) {
  const el = document.getElementById('radius-test-result');
  if (!el) return;
  if (ok === null) { el.innerHTML = ''; return; }
  el.innerHTML = ok
    ? `<span style="color:var(--up)">&#10004; ${esc(msg)}</span>`
    : `<span style="color:var(--down)">&#10008; ${esc(msg)}</span>`;
}

async function testRadiusConnection() {
  const body = _radiusCollectForm();
  const el = document.getElementById('radius-test-result');
  if (el) el.innerHTML = '<span style="color:var(--text3)">Testing…</span>';
  let r;
  try {
    r = await api('POST', '/api/radius/test_connection', body);
  } catch(e) {
    _radiusShowResult(false, 'Request failed'); return;
  }
  _radiusShowResult(r.ok, r.message || (r.ok ? 'Success' : 'Failed'));
}

async function saveRadiusSettings() {
  const body = _radiusCollectForm();
  const btn = document.getElementById('integ-btn-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  let r;
  try {
    r = await api('PATCH', '/api/radius/settings', body);
  } catch(e) {
    toast('Failed to save RADIUS settings', 'err');
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
  if (r.error) { toast(r.error, 'err'); return; }
  toast('RADIUS settings saved', 'ok');
  // Clear secret fields so they don't linger in the DOM
  const s1 = document.getElementById('radius-secret');  if (s1) s1.value = '';
  const s2 = document.getElementById('radius-secret2'); if (s2) s2.value = '';
  // Refresh the panel so the 'set' sentinels update
  if (typeof _loadRadiusPanel === 'function') _loadRadiusPanel();
}

async function _loadRadiusPanel() {
  let r;
  try {
    r = await api('GET', '/api/radius/settings');
  } catch(e) {
    return;
  }
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.value = val; };
  const setChk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };
  setChk('radius-enabled',        r.radius_enabled);
  set('radius-server',            r.radius_server || '');
  set('radius-port',              r.radius_port || 1812);
  set('radius-server2',           r.radius_server2 || '');
  set('radius-port2',             r.radius_port2 || 1812);
  set('radius-timeout',           r.radius_timeout || 5);
  set('radius-retries',           r.radius_retries || 3);
  set('radius-nas-identifier',    r.radius_nas_identifier || 'pingwatch');
  set('radius-realm-prefix',      r.radius_realm_prefix || '');
  set('radius-realm-suffix',      r.radius_realm_suffix || '');
  setChk('radius-auto-provision', r.radius_auto_provision);
  set('radius-default-role',      r.radius_default_role || 'viewer');
  setChk('radius-debug',          r.radius_debug);

  const s1 = document.getElementById('radius-secret');
  if (s1) s1.placeholder = r.radius_secret_set ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (leave blank to keep)' : '';
  const s2 = document.getElementById('radius-secret2');
  if (s2) s2.placeholder = r.radius_secret2_set ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022 (leave blank to keep)' : '';

  _loadRadiusMappings();
}

async function _loadRadiusMappings() {
  const el = document.getElementById('radius-mappings-body');
  if (!el) return;
  let r;
  try {
    r = await api('GET', '/api/radius/attribute_mappings');
  } catch(e) {
    el.innerHTML = '<div style="color:var(--down);font-size:12px">Failed to load mappings</div>';
    return;
  }
  const mappings = r.mappings || [];
  const available = r.available_groups || [];
  let html = '';
  if (!mappings.length && !available.length) {
    html = '<div style="color:var(--text3);font-size:12px;padding:8px 0">No groups yet. Create a group in Users → Groups first, then assign RADIUS attribute mappings here.</div>';
    el.innerHTML = html;
    return;
  }
  html += '<div style="display:grid;grid-template-columns:minmax(140px,1fr) minmax(160px,1.5fr) minmax(140px,1.5fr) auto auto;gap:6px 10px;align-items:center;font-size:12px">';
  html += '<div style="font-weight:600;color:var(--text2);font-size:11px;text-transform:uppercase;letter-spacing:.5px">Group</div>';
  html += '<div style="font-weight:600;color:var(--text2);font-size:11px;text-transform:uppercase;letter-spacing:.5px">Attribute Name</div>';
  html += '<div style="font-weight:600;color:var(--text2);font-size:11px;text-transform:uppercase;letter-spacing:.5px">Attribute Value</div>';
  html += '<div style="font-weight:600;color:var(--text2);font-size:11px;text-transform:uppercase;letter-spacing:.5px">Role</div>';
  html += '<div></div>';
  mappings.forEach(m => {
    html += _radiusMappingRow(m.id, m.name, m.radius_attribute || '', m.radius_value || '', m.default_role || 'viewer', true);
  });
  available.forEach(g => {
    html += _radiusMappingRow(g.id, g.name, '', '', g.default_role || 'viewer', false);
  });
  html += '</div>';
  el.innerHTML = html;
}

function _radiusMappingRow(id, name, attr, value, role, mapped) {
  const attrId = `radius-map-attr-${id}`;
  const valId  = `radius-map-val-${id}`;
  return `
    <div style="color:var(--text);padding:4px 0">${esc(name)}</div>
    <input type="text" id="${attrId}" value="${esc(attr)}" placeholder="Filter-Id" style="font-family:monospace;font-size:11px"/>
    <input type="text" id="${valId}" value="${esc(value)}" placeholder="admins" style="font-family:monospace;font-size:11px"/>
    <div style="color:var(--text3);font-size:11px">${esc(role)}</div>
    <button class="btn-s" style="font-size:11px;padding:3px 10px" onclick="_saveRadiusMapping(${id})">${mapped ? 'Update' : 'Set'}</button>
  `;
}

async function _saveRadiusMapping(groupId) {
  const attr  = (document.getElementById(`radius-map-attr-${groupId}`)?.value || '').trim();
  const value = (document.getElementById(`radius-map-val-${groupId}`)?.value || '').trim();
  if (attr && !value) { toast('Value is required when attribute is set', 'err'); return; }
  if (!attr && value) { toast('Attribute is required when value is set', 'err'); return; }
  let r;
  try {
    r = await api('POST', '/api/radius/attribute_mappings',
                  {group_id: groupId, attribute: attr, value: value});
  } catch(e) {
    toast('Save failed', 'err'); return;
  }
  if (r.error) { toast(r.error, 'err'); return; }
  toast(attr ? 'Mapping saved' : 'Mapping cleared', 'ok');
  _loadRadiusMappings();
}

// ── Test User Auth sub-dialog ──────────────────────────────────────
function openRadiusTestAuth() {
  closeM('mradius-ta');
  const o = document.createElement('div'); o.className = 'mo'; o.id = 'mradius-ta';
  _overlayClose(o, () => closeM('mradius-ta'));
  o.innerHTML = `
  <div class="mbox" style="max-width:520px">
    <div class="mhd">
      <div class="mttl">Test RADIUS Authentication</div>
      <button class="mclose" onclick="closeM('mradius-ta')">&#10006;</button>
    </div>
    <div class="mbdy" id="mradius-ta-body">
      <div class="fh" style="margin-bottom:8px">Runs a full authentication against the configured RADIUS server. Successful replies show returned attributes so you can map them to groups.</div>
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="radius-ta-user" placeholder="jsmith" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Password</label>
        <input type="password" id="radius-ta-pass" placeholder="password" autocomplete="new-password"/></div>
      <div id="radius-ta-result" style="font-size:12px;margin-top:10px;min-height:18px"></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mradius-ta')">Close</button>
      <button class="btn-p" onclick="submitRadiusTestAuth()">Test</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('radius-ta-user')?.focus(), 50);
}

async function submitRadiusTestAuth() {
  const username = (document.getElementById('radius-ta-user')?.value || '').trim();
  const password = document.getElementById('radius-ta-pass')?.value || '';
  const resEl = document.getElementById('radius-ta-result');
  if (!username || !password) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">Username and password required</span>';
    return;
  }
  const btn = document.querySelector('#mradius-ta .btn-p');
  if (btn) { btn.disabled = true; btn.textContent = 'Testing…'; }
  if (resEl) resEl.innerHTML = '<span style="color:var(--text3)">Testing…</span>';
  let r;
  try {
    r = await api('POST', '/api/radius/test_auth', {username, password});
  } catch(e) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">&#10008; Request failed</span>';
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Test'; }
  }
  _renderRadiusTestAuthResult(r, resEl);
}

function _renderRadiusTestAuthResult(r, resEl) {
  if (!resEl) return;
  if (r.ok) {
    let html = `<div style="color:var(--up);margin-bottom:8px">&#10004; ${esc(r.message || 'Authentication succeeded')}</div>`;
    const attrs = r.attrs || {};
    const names = Object.keys(attrs);
    if (names.length) {
      html += '<div style="font-weight:600;color:var(--text2);margin-bottom:4px">Returned attributes:</div>';
      html += '<div style="max-height:240px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-family:monospace;font-size:11px">';
      names.forEach(n => {
        (attrs[n] || []).forEach(v => {
          html += `<div style="padding:2px 0"><span style="color:var(--accent)">${esc(n)}</span> = <span style="color:var(--text)">${esc(v)}</span></div>`;
        });
      });
      html += '</div>';
      html += '<div style="color:var(--text3);margin-top:6px;font-size:11px">Copy an attribute name + value into the mapping table above to assign RADIUS users to a group/role.</div>';
    }
    resEl.innerHTML = html;
    return;
  }
  if (r.challenge && r.challenge.id) {
    const cid = r.challenge.id;
    const prompt = r.challenge.prompt || 'Additional verification required';
    resEl.innerHTML = `
      <div style="color:var(--warn);margin-bottom:8px">&#9632; ${esc(r.message || 'Server requested challenge')}</div>
      <div style="font-size:12px;margin-bottom:4px">${esc(prompt)}</div>
      <div style="display:flex;gap:6px;align-items:center">
        <input type="text" id="radius-ta-chresp" autocomplete="off" style="flex:1" placeholder="Enter response"/>
        <button class="btn-p" style="font-size:12px" onclick="_submitRadiusTestAuthChallenge('${esc(cid)}')">Submit</button>
      </div>`;
    setTimeout(() => document.getElementById('radius-ta-chresp')?.focus(), 50);
    return;
  }
  resEl.innerHTML = `<span style="color:var(--down)">&#10008; ${esc(r.message || 'Authentication failed')}</span>`;
}

async function _submitRadiusTestAuthChallenge(challengeId) {
  const respEl = document.getElementById('radius-ta-chresp');
  const resEl  = document.getElementById('radius-ta-result');
  const response = respEl?.value || '';
  if (!response) {
    toast('Response is required', 'err');
    return;
  }
  if (resEl) resEl.innerHTML = '<span style="color:var(--text3)">Submitting…</span>';
  let r;
  try {
    r = await api('POST', '/api/radius/test_auth_challenge',
                  {challenge_id: challengeId, response});
  } catch(e) {
    if (resEl) resEl.innerHTML = '<span style="color:var(--down)">&#10008; Request failed</span>';
    return;
  }
  _renderRadiusTestAuthResult(r, resEl);
}
