// ── SAML 2.0 Settings ───────────────────────────────────────────────
// Lives in Settings → Integrations → SAML 2.0 sub-tab.
// _loadSamlPanel() populates the fields; saveSamlSettings() saves them.

function _samlMetaSrcToggle() {
  const src = document.querySelector('input[name="saml-meta-src"]:checked')?.value || 'url';
  const urlEl  = document.getElementById('saml-meta-url');
  const xmlEl  = document.getElementById('saml-meta-xml');
  const fileEl = document.getElementById('saml-meta-file-wrap');
  if (urlEl)  urlEl.style.display  = (src === 'url')  ? '' : 'none';
  if (xmlEl)  xmlEl.style.display  = (src === 'xml')  ? '' : 'none';
  if (fileEl) fileEl.style.display = (src === 'file') ? '' : 'none';
}

async function _samlLoadFileToTextarea(fileInput) {
  const f = fileInput?.files?.[0];
  if (!f) return;
  try {
    const text = await f.text();
    const ta = document.getElementById('saml-meta-xml');
    if (ta) ta.value = text;
    // Auto-flip to Paste XML so the user can review before import
    const pasteRadio = document.querySelector('input[name="saml-meta-src"][value="xml"]');
    if (pasteRadio) { pasteRadio.checked = true; _samlMetaSrcToggle(); }
    _samlShowResult('saml-meta-result', null, `Loaded ${f.name} (${f.size} bytes) — review and click Import`);
  } catch(e) {
    _samlShowResult('saml-meta-result', false, 'Could not read file: ' + (e.message || e));
  }
}

function _samlCollectForm() {
  return {
    saml_enabled:                    document.getElementById('saml-enabled')?.checked ? 1 : 0,
    saml_sp_entity_id:               (document.getElementById('saml-sp-entity-id')?.value || '').trim(),
    saml_sp_acs_url:                 (document.getElementById('saml-sp-acs-url')?.value || '').trim(),
    saml_idp_entity_id:              (document.getElementById('saml-idp-entity-id')?.value || '').trim(),
    saml_idp_sso_url:                (document.getElementById('saml-idp-sso-url')?.value || '').trim(),
    saml_sign_authn_requests:        document.getElementById('saml-sign-authn-requests')?.checked ? 1 : 0,
    saml_want_assertions_signed:     document.getElementById('saml-want-assertions-signed')?.checked ? 1 : 0,
    saml_attr_username:              (document.getElementById('saml-attr-username')?.value || 'NameID').trim(),
    saml_attr_email:                 (document.getElementById('saml-attr-email')?.value || 'mail').trim(),
    saml_attr_display_name:          (document.getElementById('saml-attr-display-name')?.value || 'displayName').trim(),
    saml_attr_groups:                (document.getElementById('saml-attr-groups')?.value || 'memberOf').trim(),
    saml_auto_provision:             document.getElementById('saml-auto-provision')?.checked ? 1 : 0,
    saml_allow_unmapped:             document.getElementById('saml-allow-unmapped')?.checked ? 1 : 0,
    saml_default_role:               document.getElementById('saml-default-role')?.value || 'viewer',
    saml_display_name:               (document.getElementById('saml-display-name')?.value || 'Single Sign-On').trim(),
  };
}

function _samlShowResult(elId, ok, msg) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (ok === null) { el.innerHTML = '<span style="color:var(--text3)">' + esc(msg || '') + '</span>'; return; }
  el.innerHTML = ok
    ? `<span style="color:var(--up)">✔ ${esc(msg)}</span>`
    : `<span style="color:var(--down)">✘ ${esc(msg)}</span>`;
}

function _samlRenderCertInfo(elId, info, placeholder) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!info || !info.present) {
    el.textContent = placeholder;
    el.style.color = 'var(--text3)';
    return;
  }
  if (info.error) {
    el.textContent = '⚠ ' + info.error;
    el.style.color = 'var(--down)';
    return;
  }
  const days = info.days_left;
  const col = (days <= 0) ? 'var(--down)'
           : (days < 30)  ? 'var(--warn)'
           :               'var(--up)';
  const subj = info.subject || '';
  const na   = info.not_after || '';
  el.innerHTML = `<div style="color:var(--text)">${esc(subj)}</div>` +
                 `<div style="color:var(--text3);margin-top:4px;font-size:11px">Expires ${esc(na)} · <span style="color:${col}">${days} days left</span></div>`;
}

async function _loadSamlPanel() {
  let s;
  try {
    s = await api('GET', '/api/saml/settings');
  } catch(e) {
    toast('Failed to load SAML settings', 'err');
    return;
  }
  if (s.error) { toast(s.error, 'err'); return; }
  const set    = (id, val) => { const el = document.getElementById(id); if (el) el.value = String(val ?? ''); };
  const setChk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };

  setChk('saml-enabled',                    s.saml_enabled);
  set('saml-sp-entity-id',                  s.saml_sp_entity_id || '');
  set('saml-sp-acs-url',                    s.saml_sp_acs_url || (window.location.origin + '/api/saml/acs'));
  set('saml-idp-entity-id',                 s.saml_idp_entity_id || '');
  set('saml-idp-sso-url',                   s.saml_idp_sso_url || '');
  setChk('saml-sign-authn-requests',        s.saml_sign_authn_requests ?? 1);
  setChk('saml-want-assertions-signed',     s.saml_want_assertions_signed ?? 1);
  set('saml-attr-username',                 s.saml_attr_username || 'NameID');
  set('saml-attr-email',                    s.saml_attr_email || 'mail');
  set('saml-attr-display-name',             s.saml_attr_display_name || 'displayName');
  set('saml-attr-groups',                   s.saml_attr_groups || 'memberOf');
  setChk('saml-auto-provision',             s.saml_auto_provision ?? 1);
  setChk('saml-allow-unmapped',             s.saml_allow_unmapped ?? 1);
  set('saml-default-role',                  s.saml_default_role || 'viewer');
  set('saml-display-name',                  s.saml_display_name || 'Single Sign-On');
  set('saml-meta-url',                      s.saml_metadata_url || '');

  _samlRenderCertInfo('saml-sp-cert-info',  s.saml_sp_cert_info, 'No SP cert — click Generate to create one.');
  _samlRenderCertInfo('saml-idp-cert-info', s.saml_idp_cert_info, 'No IdP cert — import IdP metadata.');
  _samlMetaSrcToggle();
}

async function saveSamlSettings() {
  const body = _samlCollectForm();
  let r;
  try {
    r = await api('PATCH', '/api/saml/settings', body);
  } catch(e) {
    toast('Failed to save SAML settings', 'err');
    return;
  }
  if (r.error) { toast(r.error, 'err'); return; }
  toast('SAML settings saved', 'ok');
  _loadIntegrationsStatus();
}

async function generateSamlSpCert() {
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  let r;
  try {
    r = await api('POST', '/api/saml/sp_cert/generate', {});
  } catch(e) {
    toast('Cert generation failed', 'err');
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⚙ Generate SP signing cert'; }
  }
  if (r.error) { toast(r.error, 'err'); return; }
  _samlRenderCertInfo('saml-sp-cert-info', r.cert_info,
                      'No SP cert — click Generate to create one.');
  toast('SP signing cert generated — download + upload new SP metadata to your IdP', 'ok');
}

function downloadSamlSpMetadata() {
  window.open('/api/saml/metadata', '_blank');
}

async function importSamlMetadata() {
  const src = document.querySelector('input[name="saml-meta-src"]:checked')?.value || 'url';
  const body = { source: src };
  if (src === 'url') {
    body.url = (document.getElementById('saml-meta-url')?.value || '').trim();
    if (!body.url) { _samlShowResult('saml-meta-result', false, 'Enter a metadata URL'); return; }
  } else {
    body.xml = document.getElementById('saml-meta-xml')?.value || '';
    if (!body.xml.trim()) { _samlShowResult('saml-meta-result', false, 'Paste metadata XML'); return; }
  }
  _samlShowResult('saml-meta-result', null, 'Importing…');
  let r;
  try {
    r = await api('POST', '/api/saml/metadata/import', body);
  } catch(e) {
    _samlShowResult('saml-meta-result', false, e.message || 'Request failed');
    return;
  }
  if (r.error) { _samlShowResult('saml-meta-result', false, r.error); return; }
  _samlShowResult('saml-meta-result', true, 'Imported — entity_id + SSO URL + IdP cert populated');
  // Refresh the panel to show populated fields
  _loadSamlPanel();
}

async function testSamlConfig() {
  _samlShowResult('saml-test-result', null, 'Testing…');
  let r;
  try {
    r = await api('POST', '/api/saml/test', {});
  } catch(e) {
    _samlShowResult('saml-test-result', false, 'Request failed');
    return;
  }
  if (r.error) { _samlShowResult('saml-test-result', false, r.error); return; }
  _samlShowResult('saml-test-result', r.ok, r.message || (r.ok ? 'OK' : 'Failed'));
}
