/* PingWatch — Site CRUD modal (Live Map).
 * Loaded by livemap.html before livemap.js. Exposes window._lmOpenSiteModal.
 */
(function() {
'use strict';

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

function openModal(mode, name) {
  closeModal();

  // For "edit", pull the current site object from livemap state
  let existing = null;
  if (mode === 'edit' && name && typeof window._lmGetSite === 'function') {
    existing = window._lmGetSite(name) || { name: name, kind: 'lab', pinned: 0, display_name: '' };
  }

  const title = mode === 'edit' ? 'EDIT SITE' : 'ADD SITE';
  const nameVal = existing ? existing.name : '';
  const kindVal = existing ? existing.kind : 'lab';
  const pinnedVal = existing && existing.pinned ? 'checked' : '';
  const displayVal = existing ? existing.display_name : '';

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
          '<label class="check">' +
            '<input id="lm-f-pinned" type="checkbox" ' + pinnedVal + '/> Pin to top of sidebar' +
          '</label>' +
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
    const inPinned  = document.getElementById('lm-f-pinned').checked ? 1 : 0;
    const inDisplay = document.getElementById('lm-f-display').value.trim();
    if (!inName) {
      alert('Name is required'); return;
    }
    try {
      if (mode === 'edit') {
        const renameDevs = !!(document.getElementById('lm-f-rename-devs') && document.getElementById('lm-f-rename-devs').checked);
        const body = {
          kind: inKind,
          pinned: inPinned,
          display_name: inDisplay,
        };
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
        await fetch('/api/sites/meta', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'same-origin',
          body: JSON.stringify({
            name: inName, kind: inKind, pinned: inPinned, display_name: inDisplay,
          }),
        }).then(async function(r) {
          if (!r.ok) { const j = await r.json().catch(function(){return{};}); throw new Error(j.error || r.statusText); }
        });
      }
      closeModal();
      if (typeof window._lmRefresh === 'function') window._lmRefresh();
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
        '<div style="font-family:\'Share Tech Mono\',monospace;font-size:11px;line-height:1.55;color:var(--text)">' +
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
      if (typeof window._lmRefresh === 'function') window._lmRefresh();
    } catch (e) {
      alert('Failed to delete: ' + (e.message || e));
    }
  };
}

window._lmOpenSiteModal = openModal;

})();
