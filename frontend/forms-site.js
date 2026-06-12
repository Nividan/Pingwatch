/* PingWatch — Site CRUD modal.
 *
 * Loaded by both the main app (index.html via server.py's _JS_FILES injector)
 * and the Live Map iframe (livemap.html via <script src=>). Exposes:
 *
 *   openSiteModal(mode, name)  — primary entry point used by Devices tab
 *   _lmOpenSiteModal           — legacy alias kept for the Live Map sidebar
 *
 * Refresh-after-save: calls every known callback that's wired up in the
 * current context — _refreshDevices (Devices tab), _lmRefresh (Live Map
 * iframe), and posts lm_refresh to the livemap-frame so a hidden iframe
 * picks up changes too.
 */
(function() {
'use strict';

// Inject the modal CSS exactly once. livemap.css carries these rules for the
// Live Map iframe; when this script runs in the main app the styles aren't
// loaded, so the modal would render unstyled at top-left of the page.
function _injectModalCss() {
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

  const title = mode === 'edit' ? 'EDIT SITE' : 'ADD SITE';
  const nameVal = existing ? existing.name : '';
  const kindVal = existing ? existing.kind : 'lab';
  const displayVal = existing ? existing.display_name : '';
  // Pin/unpin lives on the Live Map sidebar context menu now — not in this modal.

  const overlay = document.createElement('div');
  overlay.className = 'lm-modal-overlay';
  overlay.id = 'lm-site-modal';
  overlay.innerHTML =
    '<div class="lm-modal">' +
      '<div class="lm-modal-head">' + esc(title) + '</div>' +
      '<div class="lm-modal-body">' +
        '<div class="lm-field">' +
          '<label>Name</label>' +
          '<input id="lm-f-name" type="text" maxlength="100" placeholder="e.g. BSLAB" value="' + esc(nameVal) + '"' +
            (mode === 'edit' ? '' : '') + '/>' +
          '<span class="hint">Free-text. Devices in this group will carry this site label.</span>' +
        '</div>' +
        '<div class="lm-field">' +
          '<label>Kind</label>' +
          '<select id="lm-f-kind">' + buildOptions(kindVal) + '</select>' +
          '<span class="hint">Drives the sidebar pill colour and the Sites by Type widget.</span>' +
        '</div>' +
        '<div class="lm-field">' +
          '<label>Display name (optional)</label>' +
          '<input id="lm-f-display" type="text" maxlength="100" placeholder="(falls back to Name)" value="' + esc(displayVal) + '"/>' +
        '</div>' +
        '<div class="lm-field">' +
          '<label>Measured from (remote probe)</label>' +
          (typeof _probeSelectHtml === 'function'
            ? _probeSelectHtml('lm-f-probe', (existing && existing.probe_id) || '', 'Central (this server)')
            : '<select id="lm-f-probe"><option value="">Central</option></select>') +
          '<span class="hint">Devices in this site are probed from this agent unless they override it. IPAM scans of subnets tagged with this site also run there.</span>' +
        '</div>' +
        (mode === 'edit' && nameVal
          ? '<div class="lm-field">' +
              '<label class="check">' +
                '<input id="lm-f-rename-devs" type="checkbox"/> Also rename devices.site values' +
              '</label>' +
              '<span class="hint">Off by default — turn on if you change Name and want every device in this site to follow.</span>' +
            '</div>'
          : '') +
      '</div>' +
      '<div class="lm-modal-foot">' +
        (mode === 'edit'
          ? '<button class="lm-btn danger" id="lm-f-delete">DELETE METADATA</button>' +
            '<div style="flex:1"></div>'
          : '<div style="flex:1"></div>') +
        '<button class="lm-btn" id="lm-f-cancel">CANCEL</button>' +
        '<button class="lm-btn" id="lm-f-save" style="background:rgba(0,212,255,0.15);border-color:var(--accent)">' +
          (mode === 'edit' ? 'SAVE' : 'CREATE') +
        '</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(overlay);

  // Backdrop click closes
  overlay.addEventListener('mousedown', function(e) {
    if (e.target === overlay) closeModal();
  });
  document.getElementById('lm-f-cancel').onclick = closeModal;
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
  // Reuse the same overlay class; rendered on top of the edit modal.
  const overlay = document.createElement('div');
  overlay.className = 'lm-modal-overlay';
  overlay.id = 'lm-site-confirm';
  const hasUsage = (usage.devices > 0 || usage.subnets > 0);
  const usageLine = hasUsage
    ? '<strong>' + usage.devices + ' device' + (usage.devices === 1 ? '' : 's') + '</strong> and ' +
      '<strong>' + usage.subnets + ' subnet' + (usage.subnets === 1 ? '' : 's') + '</strong> currently use this site.'
    : 'No devices or subnets are currently assigned to this site.';
  overlay.innerHTML =
    '<div class="lm-modal" style="width:min(460px,92vw)">' +
      '<div class="lm-modal-head">DELETE SITE</div>' +
      '<div class="lm-modal-body">' +
        '<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px;line-height:1.55;color:var(--text)">' +
          'Delete <strong style="color:var(--accent)">' + esc(name) + '</strong>?<br/><br/>' +
          usageLine +
        '</div>' +
        (hasUsage
          ? '<div class="lm-field">' +
              '<label class="check">' +
                '<input id="lm-f-cascade" type="checkbox" checked/> ' +
                'Also clear this site from devices and subnets' +
              '</label>' +
              '<span class="hint">When ON, devices.site and ipam_subnets.site fields are reset to blank for any row tagged with this name. When OFF, only the metadata row (kind / pinned / display name) is removed; the site continues to appear via those references.</span>' +
            '</div>'
          : '<input id="lm-f-cascade" type="hidden" value="0"/>') +
      '</div>' +
      '<div class="lm-modal-foot">' +
        '<div style="flex:1"></div>' +
        '<button class="lm-btn" id="lm-f-cancel-del">CANCEL</button>' +
        '<button class="lm-btn danger" id="lm-f-confirm-del">DELETE</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(overlay);

  function closeConfirm() {
    const m = document.getElementById('lm-site-confirm');
    if (m) m.remove();
  }
  overlay.addEventListener('mousedown', function(e) { if (e.target === overlay) closeConfirm(); });
  document.getElementById('lm-f-cancel-del').onclick = closeConfirm;
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
