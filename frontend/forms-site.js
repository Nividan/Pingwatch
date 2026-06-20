/* PingWatch — Site CRUD modal.
 *
 * Loaded by both the main app (index.html via server.py's _JS_FILES injector)
 * and the Live Map iframe (livemap.html via <script src=>). Exposes:
 *
 *   openSiteModal(mode, name)  — primary entry point used by Devices tab
 *   _lmOpenSiteModal           — legacy alias kept for the Live Map sidebar
 *
 * Markup is context-aware: inside the Live Map iframe it keeps the neon
 * .lm-modal look (rules from livemap.css, injected here as a fallback);
 * in the main app it uses the standard modal vocabulary (.mo/.mbox/.fr/
 * .btn-p…) from style.css so it matches every other dialog and follows
 * the dark/light theme.
 *
 * Refresh-after-save: calls every known callback that's wired up in the
 * current context — _refreshDevices (Devices tab), _lmRefresh (Live Map
 * iframe), and posts lm_refresh to the livemap-frame so a hidden iframe
 * picks up changes too.
 */
(function() {
'use strict';

// True when running inside the Live Map iframe (served at /livemap).
const IN_LM = location.pathname.indexOf('livemap') !== -1;

// Inject the Live-Map modal CSS exactly once (iframe only — livemap.css
// normally carries these rules; this keeps the modal styled even if that
// stylesheet ever trims them). The main app needs nothing: it uses the
// standard classes from style.css.
function _injectModalCss() {
  if (!IN_LM) return;
  if (document.getElementById('forms-site-css')) return;
  const css = '' +
    '.lm-modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.6);' +
      'backdrop-filter:blur(4px);display:flex;align-items:center;' +
      'justify-content:center;z-index:10000;font-family:"Inter",sans-serif}' +
    '.lm-modal{width:min(460px,92vw);background:#0a1118;color:#e2e8f0;' +
      'border:1px solid #00d4ff;box-shadow:0 0 30px rgba(0,212,255,0.2);' +
      'display:flex;flex-direction:column}' +
    '.lm-modal-head{padding:12px 16px;border-bottom:1px solid rgba(0,212,255,0.2);' +
      'font-family:"Inter",sans-serif;font-size:13px;font-weight:900;' +
      'letter-spacing:1px;color:#00d4ff}' +
    '.lm-modal-body{padding:14px 16px;display:flex;flex-direction:column;gap:12px}' +
    '.lm-modal-foot{padding:12px 16px;border-top:1px solid rgba(0,212,255,0.2);' +
      'display:flex;gap:8px;justify-content:flex-end;align-items:center}' +
    '.lm-modal .lm-field{display:flex;flex-direction:column;gap:4px}' +
    '.lm-modal .lm-field label{font-family:"JetBrains Mono",monospace;font-size:10px;' +
      'letter-spacing:1.5px;color:rgba(255,255,255,0.6)}' +
    '.lm-modal .lm-field input,.lm-modal .lm-field select{padding:7px 9px;' +
      'background:rgba(0,0,0,0.4);border:1px solid rgba(0,212,255,0.2);color:#e2e8f0;' +
      'font-family:"JetBrains Mono",monospace;font-size:11px;border-radius:2px;outline:none}' +
    '.lm-modal .lm-field input:focus,.lm-modal .lm-field select:focus{border-color:#00d4ff}' +
    '.lm-modal .lm-field .hint{font-family:"JetBrains Mono",monospace;font-size:9px;' +
      'color:rgba(255,255,255,0.4);letter-spacing:0.5px;line-height:1.45}' +
    '.lm-modal .lm-field .check{display:flex;align-items:center;gap:6px;' +
      'font-family:"JetBrains Mono",monospace;font-size:10px;color:rgba(255,255,255,0.85)}' +
    '.lm-modal .lm-btn{padding:6px 14px;background:rgba(0,212,255,0.05);' +
      'border:1px solid rgba(0,212,255,0.25);color:#00d4ff;' +
      'font-family:"JetBrains Mono",monospace;font-size:10px;letter-spacing:1.5px;' +
      'cursor:pointer;border-radius:2px;transition:background .12s,border-color .12s}' +
    '.lm-modal .lm-btn:hover{background:rgba(0,212,255,0.1);border-color:#00d4ff}' +
    '.lm-modal .lm-btn.danger{color:#ff3366;border-color:rgba(255,51,102,0.4)}' +
    '.lm-modal .lm-btn.danger:hover{background:rgba(255,51,102,0.08);border-color:#ff3366}';
  const style = document.createElement('style');
  style.id = 'forms-site-css';
  style.textContent = css;
  document.head.appendChild(style);
}

const KINDS = [
  { v: 'lab',      l: 'LAB · Lab / staging' },
  { v: 'dc',       l: 'DC · Data center' },
  { v: 'hq',       l: 'HQ · Headquarters' },
  { v: 'pop',      l: 'PoP · Point of Presence' },
  { v: 'edge',     l: 'EDGE · Edge / branch' },
  { v: 'office',   l: 'OFFICE · Office' },
  { v: 'internet', l: 'INTERNET · Reachability (pinned)' },
];

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Context-aware markup helpers ─────────────────────────────────
// One template, two skins. Ids and structure are identical in both
// contexts — only class names / chrome differ.

function _shellHtml(title, bodyHtml, footHtml, closeId) {
  if (IN_LM) {
    return '<div class="lm-modal">' +
        '<div class="lm-modal-head">' + esc(title.toUpperCase()) + '</div>' +
        '<div class="lm-modal-body">' + bodyHtml + '</div>' +
        '<div class="lm-modal-foot">' + footHtml + '</div>' +
      '</div>';
  }
  return '<div class="mbox" style="max-width:480px">' +
      '<div class="mhd"><div class="mttl">' + esc(title) + '</div>' +
        '<button class="mclose" id="' + esc(closeId) + '">&#10005;</button></div>' +
      '<div class="mbdy">' + bodyHtml + '</div>' +
      '<div class="mft">' + footHtml + '</div>' +
    '</div>';
}

function _fieldHtml(label, controlHtml, hint) {
  if (IN_LM) {
    return '<div class="lm-field"><label>' + label + '</label>' + controlHtml +
      (hint ? '<span class="hint">' + hint + '</span>' : '') + '</div>';
  }
  return '<div class="fr"><label class="fl">' + label + '</label>' + controlHtml +
    (hint ? '<div style="font-size:11px;color:var(--text3);line-height:1.5;margin-top:4px">' +
            hint + '</div>' : '') + '</div>';
}

function _checkHtml(inputId, text, hint, checked) {
  const box = '<input id="' + esc(inputId) + '" type="checkbox"' + (checked ? ' checked' : '') + '/> ';
  if (IN_LM) {
    return '<div class="lm-field"><label class="check">' + box + text + '</label>' +
      (hint ? '<span class="hint">' + hint + '</span>' : '') + '</div>';
  }
  return '<div class="fr"><label style="display:flex;align-items:center;gap:8px;' +
      'font-size:13px;color:var(--text2);cursor:pointer;user-select:none">' + box + text + '</label>' +
    (hint ? '<div style="font-size:11px;color:var(--text3);line-height:1.5;margin-top:4px">' +
            hint + '</div>' : '') + '</div>';
}

// kind: 'primary' | 'secondary' | 'danger'. Live Map keeps its uppercase
// mono buttons; the main app uses the standard .btn-p/.btn-s vocabulary.
function _btnHtml(id, label, kind, style) {
  if (IN_LM) {
    const cls = 'lm-btn' + (kind === 'danger' ? ' danger' : '');
    const st = kind === 'primary'
      ? 'background:rgba(0,212,255,0.15);border-color:var(--accent)' : '';
    return '<button class="' + cls + '" id="' + esc(id) + '"' +
      (st ? ' style="' + st + '"' : '') + '>' + esc(label.toUpperCase()) + '</button>';
  }
  const cls = kind === 'primary' ? 'btn-p' : (kind === 'danger' ? 'btn-s is-danger' : 'btn-s');
  return '<button class="' + cls + '" id="' + esc(id) + '"' +
    (style ? ' style="' + style + '"' : '') + '>' + esc(label) + '</button>';
}

function closeModal() {
  const m = document.getElementById('lm-site-modal');
  if (m) m.remove();
}

function buildOptions(selected) {
  return KINDS.map(function(k) {
    const sel = k.v === selected ? ' selected' : '';
    return '<option value="' + k.v + '"' + sel + '>' + esc(k.l) + '</option>';
  }).join('');
}

function _broadcastRefresh() {
  // Try every refresh hook the current context might have. Each is optional.
  try { if (typeof window._refreshDevices === 'function') window._refreshDevices(); } catch (_) {}
  try { if (typeof window._lmRefresh === 'function')      window._lmRefresh();      } catch (_) {}
  // Also poke the Live Map iframe (if loaded) so it re-fetches even when the
  // user is currently on a different tab.
  try {
    const f = document.getElementById('livemap-frame');
    if (f && f.contentWindow) {
      f.contentWindow.postMessage({ type: 'lm_refresh' }, window.location.origin);
    }
  } catch (_) {}
}

async function _fetchSiteMeta(name) {
  // Fall back to the API when running outside the Live Map (no _lmGetSite).
  try {
    const r = await fetch('/api/sites/meta', { credentials: 'same-origin' });
    if (!r.ok) return null;
    const j = await r.json();
    return (j.sites || []).find(function(s) { return s.name === name; }) || null;
  } catch (_) { return null; }
}

async function openModal(mode, name) {
  closeModal();
  _injectModalCss();

  // For "edit", pull the current site object. Prefer the in-memory Live Map
  // cache when we're loaded inside the iframe; otherwise hit the API.
  let existing = null;
  if (mode === 'edit' && name) {
    if (typeof window._lmGetSite === 'function') {
      existing = window._lmGetSite(name);
    }
    if (!existing) {
      existing = await _fetchSiteMeta(name);
    }
    if (!existing) {
      existing = { name: name, kind: 'lab', pinned: 0, display_name: '' };
    }
  }

  const title = mode === 'edit' ? 'Edit Site' : 'Add Site';
  const nameVal = existing ? existing.name : '';
  const kindVal = existing ? existing.kind : 'lab';
  const displayVal = existing ? existing.display_name : '';
  // Pin/unpin lives on the Live Map sidebar context menu now — not in this modal.

  const body =
    _fieldHtml('Name',
      '<input id="lm-f-name" type="text" maxlength="100" placeholder="e.g. BSLAB" value="' + esc(nameVal) + '"/>',
      'Free-text. Devices in this group will carry this site label.') +
    _fieldHtml('Kind',
      '<select id="lm-f-kind">' + buildOptions(kindVal) + '</select>',
      'Drives the sidebar pill colour and the Sites by Type widget.') +
    _fieldHtml('Display name (optional)',
      '<input id="lm-f-display" type="text" maxlength="100" placeholder="(falls back to Name)" value="' + esc(displayVal) + '"/>',
      '') +
    _fieldHtml('Measured from (remote probe)',
      (typeof _probeSelectHtml === 'function'
        // omitCentral: at site level '' already means central, so the
        // explicit 'central' pin would render as a duplicate option.
        ? _probeSelectHtml('lm-f-probe', (existing && existing.probe_id) || '', 'Central (this server)', true)
        : '<select id="lm-f-probe"><option value="">Central</option></select>'),
      'Devices in this site are probed from this agent unless they override it. ' +
      'IPAM scans of subnets tagged with this site also run there.') +
    (mode === 'edit' && nameVal
      ? _checkHtml('lm-f-rename-devs', 'Also rename devices&#39; site values',
          'Off by default &mdash; turn on if you change Name and want every device in this site to follow.',
          false)
      : '');

  const foot =
    (mode === 'edit'
      ? _btnHtml('lm-f-delete', 'Delete Site', 'danger', 'margin-right:auto') +
        (IN_LM ? '<div style="flex:1"></div>' : '')
      : (IN_LM ? '<div style="flex:1"></div>' : '')) +
    _btnHtml('lm-f-cancel', 'Cancel', 'secondary') +
    _btnHtml('lm-f-save', mode === 'edit' ? 'Save' : 'Create', 'primary');

  const overlay = document.createElement('div');
  overlay.className = IN_LM ? 'lm-modal-overlay' : 'mo';
  overlay.id = 'lm-site-modal';
  overlay.innerHTML = _shellHtml(title, body, foot, 'lm-f-x');

  document.body.appendChild(overlay);

  // Backdrop click closes
  overlay.addEventListener('mousedown', function(e) {
    if (e.target === overlay) closeModal();
  });
  document.getElementById('lm-f-cancel').onclick = closeModal;
  const xBtn = document.getElementById('lm-f-x');
  if (xBtn) xBtn.onclick = closeModal;
  document.getElementById('lm-f-name').focus();
  document.getElementById('lm-f-name').select();

  // Save handler
  document.getElementById('lm-f-save').onclick = async function() {
    const inName    = document.getElementById('lm-f-name').value.trim();
    const inKind    = document.getElementById('lm-f-kind').value;
    const inDisplay = document.getElementById('lm-f-display').value.trim();
    if (!inName) {
      alert('Name is required'); return;
    }
    try {
      if (mode === 'edit') {
        const renameDevs = !!(document.getElementById('lm-f-rename-devs') && document.getElementById('lm-f-rename-devs').checked);
        // pinned is intentionally omitted: the PUT handler falls back to the
        // existing value when the key is absent, so this modal never clobbers
        // a pin state that was set from the Live Map sidebar.
        const body = {
          kind: inKind,
          display_name: inDisplay,
        };
        { const _pf = document.getElementById('lm-f-probe');
          if (_pf && _pf.value !== ((existing && existing.probe_id) || '')) body.probe_id = _pf.value; }
        if (inName !== nameVal) {
          body.new_name = inName;
          body.also_rename = renameDevs;
        }
        await fetch('/api/sites/meta/' + encodeURIComponent(nameVal), {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify(body),
        }).then(async function(r) {
          if (!r.ok) { const j = await r.json().catch(function(){return{};}); throw new Error(j.error || r.statusText); }
        });
      } else {
        // New sites are created unpinned; pin from the Live Map sidebar
        // (right-click → Pin to top) after creation.
        await fetch('/api/sites/meta', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            name: inName, kind: inKind, display_name: inDisplay,
          }),
        }).then(async function(r) {
          if (!r.ok) { const j = await r.json().catch(function(){return{};}); throw new Error(j.error || r.statusText); }
        });
        // Probe binding rides a follow-up PUT (the create endpoint is
        // metadata-only); skipped when left at Central.
        const _pfNew = document.getElementById('lm-f-probe');
        if (_pfNew && _pfNew.value) {
          await fetch('/api/sites/meta/' + encodeURIComponent(inName), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ kind: inKind, probe_id: _pfNew.value }),
          });
        }
      }
      closeModal();
      _broadcastRefresh();
    } catch (e) {
      alert('Failed to save site: ' + (e.message || e));
    }
  };

  // Delete handler (edit mode only) — opens a secondary confirm modal that
  // shows usage counts (N devices, M subnets) and offers a cascade checkbox
  // (default ON, since the typical "delete this site" intent is to also
  // clear the assignment from devices and subnets).
  const delBtn = document.getElementById('lm-f-delete');
  if (delBtn) {
    delBtn.onclick = async function() {
      let usage = { devices: 0, subnets: 0 };
      try {
        const r = await fetch('/api/sites/meta/' + encodeURIComponent(nameVal) + '/usage',
                              { credentials: 'same-origin' });
        if (r.ok) usage = await r.json();
      } catch (_) {}
      _openDeleteConfirm(nameVal, usage);
    };
  }
}

function _openDeleteConfirm(name, usage) {
  _injectModalCss();
  const hasUsage = (usage.devices > 0 || usage.subnets > 0);
  const usageLine = hasUsage
    ? '<strong>' + usage.devices + ' device' + (usage.devices === 1 ? '' : 's') + '</strong> and ' +
      '<strong>' + usage.subnets + ' subnet' + (usage.subnets === 1 ? '' : 's') + '</strong> currently use this site.'
    : 'No devices or subnets are currently assigned to this site.';
  const bodyTextStyle = IN_LM
    ? 'font-family:\'JetBrains Mono\',monospace;font-size:11px;line-height:1.55;color:var(--text)'
    : 'font-size:13px;line-height:1.6;color:var(--text)';

  const body =
    '<div style="' + bodyTextStyle + '">' +
      'Delete <strong style="color:var(--accent)">' + esc(name) + '</strong>?<br/><br/>' +
      usageLine +
    '</div>' +
    (hasUsage
      ? _checkHtml('lm-f-cascade', 'Also clear this site from devices and subnets',
          'When ON, devices.site and ipam_subnets.site fields are reset to blank for any row tagged ' +
          'with this name. When OFF, only the metadata row (kind / pinned / display name) is removed; ' +
          'the site continues to appear via those references.', true)
      : '<input id="lm-f-cascade" type="hidden" value="0"/>');

  const foot =
    (IN_LM ? '<div style="flex:1"></div>' : '') +
    _btnHtml('lm-f-cancel-del', 'Cancel', 'secondary') +
    _btnHtml('lm-f-confirm-del', 'Delete', 'danger');

  // Reuse the same overlay class; rendered on top of the edit modal.
  const overlay = document.createElement('div');
  overlay.className = IN_LM ? 'lm-modal-overlay' : 'mo';
  overlay.id = 'lm-site-confirm';
  overlay.innerHTML = _shellHtml('Delete Site', body, foot, 'lm-f-x-del');
  document.body.appendChild(overlay);

  function closeConfirm() {
    const m = document.getElementById('lm-site-confirm');
    if (m) m.remove();
  }
  overlay.addEventListener('mousedown', function(e) { if (e.target === overlay) closeConfirm(); });
  document.getElementById('lm-f-cancel-del').onclick = closeConfirm;
  const xBtn = document.getElementById('lm-f-x-del');
  if (xBtn) xBtn.onclick = closeConfirm;
  document.getElementById('lm-f-confirm-del').onclick = async function() {
    const cascadeEl = document.getElementById('lm-f-cascade');
    const cascade = cascadeEl && cascadeEl.type === 'checkbox' ? cascadeEl.checked : false;
    try {
      const url = '/api/sites/meta/' + encodeURIComponent(name) + (cascade ? '?cascade=1' : '');
      const r = await fetch(url, { method: 'DELETE', credentials: 'same-origin' });
      if (!r.ok) {
        const j = await r.json().catch(function(){return{};});
        throw new Error(j.error || r.statusText);
      }
      closeConfirm();
      closeModal();
      _broadcastRefresh();
    } catch (e) {
      alert('Failed to delete: ' + (e.message || e));
    }
  };
}

// Primary entry point — used by Devices tab (and anywhere else).
window.openSiteModal   = openModal;
// Legacy alias kept for the Live Map sidebar; remove once both surfaces use
// the canonical name.
window._lmOpenSiteModal = openModal;

})();
