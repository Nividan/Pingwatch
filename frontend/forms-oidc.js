// ── OpenID Connect Settings ────────────────────────────────────────
// Lives in Settings → Integrations → OIDC sub-tab.

function _oidcCollectForm() {
  const body = {
    oidc_enabled:             document.getElementById('oidc-enabled')?.checked ? 1 : 0,
    oidc_issuer_url:          (document.getElementById('oidc-issuer-url')?.value || '').trim(),
    oidc_client_id:           (document.getElementById('oidc-client-id')?.value || '').trim(),
    oidc_redirect_uri:        (document.getElementById('oidc-redirect-uri')?.value || '').trim(),
    oidc_scopes:              (document.getElementById('oidc-scopes')?.value || 'openid profile email groups').trim(),
    oidc_claim_username:      (document.getElementById('oidc-claim-username')?.value || 'preferred_username').trim(),
    oidc_claim_email:         (document.getElementById('oidc-claim-email')?.value || 'email').trim(),
    oidc_claim_display_name:  (document.getElementById('oidc-claim-display-name')?.value || 'name').trim(),
    oidc_claim_groups:        (document.getElementById('oidc-claim-groups')?.value || 'groups').trim(),
    oidc_auto_provision:      document.getElementById('oidc-auto-provision')?.checked ? 1 : 0,
    oidc_allow_unmapped:      document.getElementById('oidc-allow-unmapped')?.checked ? 1 : 0,
    oidc_default_role:        document.getElementById('oidc-default-role')?.value || 'viewer',
    oidc_display_name:        (document.getElementById('oidc-display-name')?.value || 'Single Sign-On').trim(),
  };
  const secret = document.getElementById('oidc-client-secret')?.value || '';
  if (secret) body.oidc_client_secret = secret;  // omit → keep existing
  return body;
}

function _oidcShowResult(elId, ok, msg) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (ok === null) { el.innerHTML = '<span style="color:var(--text3)">' + esc(msg || '') + '</span>'; return; }
  el.innerHTML = ok
    ? `<span style="color:var(--up)">✔ ${esc(msg)}</span>`
    : `<span style="color:var(--down)">✘ ${esc(msg)}</span>`;
}

function _oidcRenderDiscovery(s) {
  const el = document.getElementById('oidc-discovery-panel');
  const tsEl = document.getElementById('oidc-discovery-ts');
  if (!el) return;
  if (!s.discovery_issuer) {
    el.textContent = 'Not fetched yet — click Auto-discover.';
    if (tsEl) tsEl.textContent = '';
    return;
  }
  const rows = [
    ['issuer',              s.discovery_issuer],
    ['authorization',       s.discovery_auth_endpoint],
    ['token',               s.discovery_token_endpoint],
    ['userinfo',            s.discovery_userinfo_endpoint],
    ['jwks_uri',            s.discovery_jwks_uri],
    ['end_session',         s.discovery_end_session_endpoint],
  ].filter(r => r[1]);
  el.innerHTML = rows.map(([k, v]) =>
    `<div><span style="color:var(--text3)">${esc(k)}:</span> ${esc(v)}</div>`
  ).join('');
  if (tsEl && s.oidc_discovery_fetched_ts) {
    const dt = new Date(s.oidc_discovery_fetched_ts * 1000);
    tsEl.textContent = `· fetched ${dt.toLocaleString()}`;
  }
}

async function _loadOidcPanel() {
  let s;
  try {
    s = await api('GET', '/api/oidc/settings');
  } catch(e) {
    toast('Failed to load OIDC settings', 'err');
    return;
  }
  if (s.error) { toast(s.error, 'err'); return; }
  const set    = (id, val) => { const el = document.getElementById(id); if (el) el.value = String(val ?? ''); };
  const setChk = (id, val) => { const el = document.getElementById(id); if (el) el.checked = !!val; };

  setChk('oidc-enabled',             s.oidc_enabled);
  set('oidc-issuer-url',             s.oidc_issuer_url || '');
  set('oidc-client-id',              s.oidc_client_id || '');
  set('oidc-redirect-uri',           s.oidc_redirect_uri || (window.location.origin + '/api/oidc/callback'));
  set('oidc-scopes',                 s.oidc_scopes || 'openid profile email groups');
  set('oidc-claim-username',         s.oidc_claim_username || 'preferred_username');
  set('oidc-claim-email',            s.oidc_claim_email || 'email');
  set('oidc-claim-display-name',     s.oidc_claim_display_name || 'name');
  set('oidc-claim-groups',           s.oidc_claim_groups || 'groups');
  setChk('oidc-auto-provision',      s.oidc_auto_provision ?? 1);
  setChk('oidc-allow-unmapped',      s.oidc_allow_unmapped ?? 1);
  set('oidc-default-role',           s.oidc_default_role || 'viewer');
  set('oidc-display-name',           s.oidc_display_name || 'Single Sign-On');

  const secretEl = document.getElementById('oidc-client-secret');
  if (secretEl) secretEl.placeholder = s.oidc_client_secret_set
    ? '●●●●●●●● (set — leave blank to keep)'
    : 'client secret';

  _oidcRenderDiscovery(s);
}

async function saveOidcSettings() {
  const body = _oidcCollectForm();
  let r;
  try {
    r = await api('PATCH', '/api/oidc/settings', body);
  } catch(e) {
    toast('Failed to save OIDC settings', 'err');
    return;
  }
  if (r.error) { toast(r.error, 'err'); return; }
  toast('OIDC settings saved', 'ok');
  _loadIntegrationsStatus();
}

async function refreshOidcDiscovery() {
  // Save first so the backend has the latest issuer_url
  const issuer = (document.getElementById('oidc-issuer-url')?.value || '').trim();
  if (!issuer) { toast('Enter an issuer URL first', 'err'); return; }
  try {
    await api('PATCH', '/api/oidc/settings', {oidc_issuer_url: issuer});
  } catch(e) { /* ignore, try to refresh anyway */ }
  const btn = event?.target;
  if (btn) { btn.disabled = true; btn.textContent = 'Fetching…'; }
  let r;
  try {
    r = await api('POST', '/api/oidc/discovery/refresh', {});
  } catch(e) {
    toast('Discovery fetch failed', 'err');
    return;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻ Auto-discover'; }
  }
  if (r.error) { toast(r.error, 'err'); return; }
  toast('Discovery fetched — panel refreshed', 'ok');
  _loadOidcPanel();
}

async function testOidcConfig() {
  _oidcShowResult('oidc-test-result', null, 'Testing…');
  let r;
  try {
    r = await api('POST', '/api/oidc/test', {});
  } catch(e) {
    _oidcShowResult('oidc-test-result', false, 'Request failed');
    return;
  }
  if (r.error) { _oidcShowResult('oidc-test-result', false, r.error); return; }
  _oidcShowResult('oidc-test-result', r.ok, r.message || (r.ok ? 'OK' : 'Failed'));
}
