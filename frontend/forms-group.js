// ── forms-group.js ────────────────────────────────────────────────
// Edit Group modal: rename + alert profile + reserved space for future
// per-group config. Opened from the gear icon on the group header.
// The double-click rename on the group label is preserved as a shortcut.

async function openEditGroup(groupName) {
  closeM('meg');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'meg';
  _overlayClose(o, () => closeM('meg'));
  o.innerHTML = `
    <div class="mbox meg-mbox" style="min-width:560px;max-width:640px">
      <div class="mhd">
        <div class="mttl">Edit Group — ${esc(groupName)}</div>
        <button class="mclose" onclick="closeM('meg')">&#x2715;</button>
      </div>
      <div class="mbdy meg-body">
        <div class="meg-row2">
          <div class="meg-sec">
            <div class="meg-h">Group Name</div>
            <input type="text" id="eg-name" value="${esc(groupName)}" autocomplete="off"/>
          </div>
          <div class="meg-sec">
            <div class="meg-h">Site</div>
            <input type="text" id="eg-site" list="eg-site-dl"
                   placeholder="(loading…)" autocomplete="off"/>
            <datalist id="eg-site-dl"></datalist>
            <div class="fh" id="eg-site-hint">Applies to every device in this group.</div>
          </div>
        </div>

        <div class="meg-row2">
          <div class="meg-sec">
            <div class="meg-h">Tier (Live Map)</div>
            <select id="eg-tier">${_lmTierOptionsHtml('')}</select>
            <div class="fh">Forces every device into this Live Map tier. Auto = name-pattern inference.</div>
          </div>
          <div class="meg-sec">
            <div class="meg-h">Mute alerts</div>
            <label class="meg-mute">
              <input type="checkbox" id="eg-muted"/>
              <span>🔕 Suppress for this group</span>
            </label>
            <div class="fh">Silences alerts &amp; flap events; probes still run.</div>
          </div>
        </div>

        <div class="meg-sec">
          <div class="meg-h">Parent Devices (Live Map)</div>
          <div id="eg-parents-chips" class="pw-chip-input"></div>
          <input type="text" id="eg-parents-input" list="eg-parents-dl"
                 placeholder="Type to search — Enter or comma to add" autocomplete="off"/>
          <datalist id="eg-parents-dl"></datalist>
          <div class="fh">Default parents for every device in this group (drives Live Map connection lines). Per-device parents override this.</div>
        </div>

        <div class="meg-sec">
          <div class="meg-h">Alert Profile</div>
          <div id="eg-profile-body" style="font-size:12px;color:var(--text3)">Loading\u2026</div>
        </div>

      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('meg')">Cancel</button>
        <button class="btn-p" onclick="saveEditGroup('${esc(groupName).replace(/'/g, "\\'")}')">Save</button>
      </div>
    </div>`;
  document.body.appendChild(o);

  // Async-load the resolved alert profile + override status
  _loadGroupProfileSection(groupName);
  // Async-load the current mute state (default: unchecked until it lands)
  _loadGroupMuteState(groupName);
  // Site picker: prefill from the unique sites currently used by devices in
  // this group. Populate the autocomplete datalist from /api/sites.
  _loadGroupSiteState(groupName);
  // Live Map tier override: pull the current setting and select the option.
  _loadGroupTierState(groupName);
  // Live Map parent devices override: chip multi-select.
  _loadGroupParentsState(groupName);
}

// ── Group Parent Devices chip input ────────────────────────────────
// Module-level state for the open Edit Group modal. Stored as a list of
// device IDs (not names), serialized as JSON into pw_group_parents.
let _egParentIds = [];

// Same sentinel as Edit Device — the datalist offers "Foo  ·  group (N)"
// entries so the user can pick a whole group as a parent without typing a
// device id. Stored ref form: "group:<name>".
const _EG_GRP_SFX = '  ·  group';

function _egRenderParentChips() {
  const wrap = document.getElementById('eg-parents-chips');
  if (!wrap) return;
  if (!_egParentIds.length) {
    wrap.innerHTML = '<span class="pw-chip-empty">None — devices inherit per-device parents only</span>';
    return;
  }
  wrap.innerHTML = _egParentIds.map((ref, i) => {
    if (typeof ref === 'string' && ref.indexOf('group:') === 0) {
      const gname = ref.slice(6);
      return `<span class="pw-chip pw-chip-group" data-i="${i}">
        <span class="pw-chip-badge">GROUP</span>${esc(gname)}
        <button class="pw-chip-x" onclick="_egRemoveParent(${i})" title="Remove">&times;</button>
      </span>`;
    }
    const d = S.devices[ref];
    const label = d ? (d.name || ref) : `(missing: ${ref})`;
    return `<span class="pw-chip" data-i="${i}">
      ${esc(label)}
      <button class="pw-chip-x" onclick="_egRemoveParent(${i})" title="Remove">&times;</button>
    </span>`;
  }).join('');
}

function _egRemoveParent(idx) {
  _egParentIds.splice(idx, 1);
  _egRenderParentChips();
}

function _egAddParent(ref) {
  if (!ref || _egParentIds.includes(ref)) return;
  if (_egParentIds.length >= 8) {
    toast('Max 8 parent devices', 'err');
    return;
  }
  _egParentIds.push(ref);
  _egRenderParentChips();
}

function _egPopulateParentDatalist() {
  const dl = document.getElementById('eg-parents-dl');
  if (!dl) return;
  const devOpts = Object.values(S.devices || {})
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    .map(d => `<option value="${esc(d.name || d.device_id)}"></option>`);
  // Group entries — aggregate by group name + member count. Drop singletons
  // (a 1-device "group" adds nothing over picking the device directly).
  const groupCount = new Map();
  Object.values(S.devices || {}).forEach(d => {
    const g = (d.group || 'Default Group').trim();
    groupCount.set(g, (groupCount.get(g) || 0) + 1);
  });
  const grpOpts = [...groupCount.entries()]
    .filter(([, n]) => n >= 2)
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([g, n]) => `<option value="${esc(g + _EG_GRP_SFX + ' (' + n + ')')}"></option>`);
  dl.innerHTML = [...devOpts, ...grpOpts].join('');
}

function _egCommitParentFromInput(input) {
  const raw = (input.value || '').trim();
  if (!raw) return;
  // Group entry — strip the sentinel suffix and store as "group:<name>".
  const grpSfxIdx = raw.indexOf(_EG_GRP_SFX);
  if (grpSfxIdx > 0) {
    const gname = raw.slice(0, grpSfxIdx);
    const exists = Object.values(S.devices || {}).some(
      d => (d.group || 'Default Group') === gname
    );
    if (!exists) { toast(`Group "${gname}" not found`, 'err'); return; }
    _egAddParent('group:' + gname);
    input.value = '';
    return;
  }
  // Device by name (case-insensitive) or by id.
  const lc = raw.toLowerCase();
  let match = Object.values(S.devices || {}).find(
    d => (d.name || '').toLowerCase() === lc
  );
  if (!match && S.devices[raw]) match = S.devices[raw];
  if (match) {
    _egAddParent(match.device_id);
    input.value = '';
    return;
  }
  // Bare group name (typed without the suffix).
  const groupSet = new Set(Object.values(S.devices || {}).map(
    d => (d.group || 'Default Group')
  ));
  if (groupSet.has(raw)) {
    _egAddParent('group:' + raw);
    input.value = '';
    return;
  }
  toast(`No device or group named "${raw}"`, 'err');
}

async function _loadGroupParentsState(groupName) {
  _egParentIds = [];
  _egPopulateParentDatalist();
  try {
    const r = await api('GET', '/api/settings/pw_group_parents').catch(() => null);
    const map = (r && r.value && typeof r.value === 'object') ? r.value : {};
    const cur = Array.isArray(map[groupName]) ? map[groupName] : [];
    _egParentIds = cur.filter(p => typeof p === 'string' && p);
  } catch { /* default: empty */ }
  _egRenderParentChips();
  // Wire up input — commit on Enter or comma.
  const input = document.getElementById('eg-parents-input');
  if (input) {
    input.dataset.initial = JSON.stringify(_egParentIds);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ',') {
        e.preventDefault();
        _egCommitParentFromInput(input);
      } else if (e.key === 'Backspace' && !input.value && _egParentIds.length) {
        _egParentIds.pop();
        _egRenderParentChips();
      }
    });
    input.addEventListener('change', () => _egCommitParentFromInput(input));
  }
}

async function _loadGroupTierState(groupName) {
  const sel = document.getElementById('eg-tier');
  if (!sel) return;
  try {
    const r = await api('GET', '/api/settings/pw_group_tiers');
    const map = (r && r.value) || {};
    const cur = map[groupName] || '';
    sel.value = cur;
    sel.dataset.initial = cur;
  } catch {
    sel.dataset.initial = '';
  }
}

async function _loadGroupSiteState(groupName) {
  const input = document.getElementById('eg-site');
  const hint  = document.getElementById('eg-site-hint');
  if (!input) return;
  // Distinct sites across devices in this group (currently in memory)
  const sitesInGroup = [...new Set(
    Object.values(S.devices || {})
      .filter(d => (d.group || 'Default Group') === groupName)
      .map(d => (d.site || ''))
  )];
  let initial = '';
  if (sitesInGroup.length === 1) {
    initial = sitesInGroup[0];
  } else if (sitesInGroup.length > 1) {
    // Mixed — leave blank, surface the spread in the hint so the user knows
    // what they're about to overwrite.
    if (hint) {
      const labeled = sitesInGroup.map(s => s || '(Unsited)').sort().join(', ');
      hint.innerHTML = `<span style="color:var(--warn)">Currently mixed: ${esc(labeled)}.</span>
        Saving will move every device to the value above.`;
    }
  }
  input.value = initial;
  input.dataset.initial = initial;
  input.placeholder = 'HQ, DR-Site-2…';
  // Populate the datalist from /api/sites (UNION of IPAM + devices).
  if (typeof _populateSiteDatalist === 'function') {
    _populateSiteDatalist('eg-site-dl');
  }
}

async function _loadGroupMuteState(groupName){
  const box = document.getElementById('eg-muted');
  if (!box) return;
  try {
    const r = await api('GET', '/api/device-group/' + encodeURIComponent(groupName) + '/mute');
    box.checked = !!(r && r.muted);
    box.dataset.initial = box.checked ? '1' : '0';
  } catch {
    // Silent — default unchecked is fine
  }
}

async function _loadGroupProfileSection(groupName) {
  const body = document.getElementById('eg-profile-body');
  if (!body) return;
  try {
    const r = await api('GET', '/api/alert/profiles');
    const all = r.profiles || [];
    const groupProf = all.find(p => p.scope_type === 'group' && p.scope_value === groupName);
    const globalProf = all.find(p => p.scope_type === 'global');

    let html = '';
    if (groupProf) {
      html += `<div style="margin-bottom:10px">
        <span class="alrt-override-badge">Override</span>
        <span style="margin-left:8px;color:var(--text)">${esc(groupProf.name)}</span>
        <span style="margin-left:6px;color:var(--text3);font-size:11px">
          (${groupProf.stages.length} stage${groupProf.stages.length === 1 ? '' : 's'})
        </span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn-s" onclick="editProfileFromGroup(${groupProf.id})">Edit profile\u2026</button>
        <button class="btn-s" onclick="resetGroupProfile('${esc(groupName).replace(/'/g, "\\'")}', ${groupProf.id})">Reset to inherited</button>
      </div>`;
    } else {
      const inheritedFrom = globalProf
        ? `Global profile <strong style="color:var(--text)">${esc(globalProf.name)}</strong>`
        : `(none \u2014 no profile resolved)`;
      html += `<div style="margin-bottom:10px">
        <span class="alrt-inherit-badge">Inherited</span>
        <span style="margin-left:8px">${inheritedFrom}</span>
      </div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn-s" onclick="overrideGroupProfile('${esc(groupName).replace(/'/g, "\\'")}')">Override at group level</button>
        ${globalProf ? `<button class="btn-s" onclick="editProfileFromGroup(${globalProf.id})">View global profile</button>` : ''}
      </div>`;
    }
    body.innerHTML = html;
  } catch (e) {
    body.innerHTML = `<span style="color:var(--down)">Failed to load profile: ${esc(e.message || e)}</span>`;
  }
}

async function saveEditGroup(oldName) {
  const newName = (document.getElementById('eg-name')?.value || '').trim();
  if (!newName) { toast('Group name cannot be empty', 'err'); return; }

  // Live Map tier override — write BEFORE rename so the map entry can be
  // migrated if the group is being renamed in the same save.
  const tierSel = document.getElementById('eg-tier');
  let _tierWrote = false;
  if (tierSel && tierSel.dataset.initial !== undefined) {
    const wantTier = (tierSel.value || '').trim();
    const hadTier  = tierSel.dataset.initial || '';
    if (wantTier !== hadTier || newName !== oldName) {
      try {
        const cur = await api('GET', '/api/settings/pw_group_tiers').catch(() => null);
        const map = (cur && cur.value && typeof cur.value === 'object') ? { ...cur.value } : {};
        // Remove the old name's entry first (handles both rename and clear).
        delete map[oldName];
        if (wantTier) map[newName] = wantTier;
        await api('PATCH', '/api/settings/pw_group_tiers', { value: map });
        // Live Map runs in an iframe — postMessage triggers a refresh so the
        // drill-in re-buckets devices with the new override applied.
        const _lf = document.getElementById('livemap-frame');
        _lf?.contentWindow?.postMessage(
          { type: 'lm_refresh' },
          window.location.origin
        );
        _tierWrote = true;
      } catch (e) {
        toast('Tier save failed: ' + (e.message || e), 'err');
        return;
      }
    }
  }

  // Live Map parent-devices override — same write-before-rename pattern.
  const parentsInput = document.getElementById('eg-parents-input');
  if (parentsInput && parentsInput.dataset.initial !== undefined) {
    const wantParents = Array.isArray(_egParentIds) ? _egParentIds.slice() : [];
    const hadParents  = JSON.parse(parentsInput.dataset.initial || '[]');
    const same = wantParents.length === hadParents.length &&
                 wantParents.every((p, i) => p === hadParents[i]);
    if (!same || newName !== oldName) {
      try {
        const cur = await api('GET', '/api/settings/pw_group_parents').catch(() => null);
        const map = (cur && cur.value && typeof cur.value === 'object') ? { ...cur.value } : {};
        delete map[oldName];
        if (wantParents.length) map[newName] = wantParents;
        await api('PATCH', '/api/settings/pw_group_parents', { value: map });
        const _lf = document.getElementById('livemap-frame');
        _lf?.contentWindow?.postMessage(
          { type: 'lm_refresh' },
          window.location.origin
        );
      } catch (e) {
        toast('Parent save failed: ' + (e.message || e), 'err');
        return;
      }
    }
  }

  // Site change — applies to every device in the group. We do this BEFORE
  // the rename so we can key the bulk-move on the pre-rename group name
  // (group_id-less API; we filter client-side by group name then PATCH).
  const siteInput = document.getElementById('eg-site');
  if (siteInput && siteInput.dataset.initial !== undefined) {
    const wantSite = (siteInput.value || '').trim();
    const hadSite  = siteInput.dataset.initial;
    // Treat the field as dirty when either the value changed OR the group's
    // devices currently have mixed sites (initial was blank-as-placeholder
    // for "mixed", but we want a blank submit to clear them all).
    const devs = Object.values(S.devices).filter(
      d => (d.group || 'Default Group') === oldName
    );
    const distinctSites = [...new Set(devs.map(d => d.site || ''))];
    const wasMixed = distinctSites.length > 1;
    if (wantSite.length > 80) {
      toast('Site name too long (max 80)', 'err'); return;
    }
    if (wantSite !== hadSite || wasMixed) {
      const dids = devs.map(d => d.device_id);
      if (dids.length) {
        try {
          const r = await api('POST', '/api/devices/bulk',
            { device_ids: dids, action: 'move', site: wantSite });
          if (!r || !r.ok) { toast('Site update failed', 'err'); return; }
          dids.forEach(d => { const dv = S.devices[d]; if (dv) dv.site = wantSite; });
          window._pwSitesCache = null;  // refresh autocomplete next open
          // Re-render moved devices so their .grp-wrap parents update.
          dids.forEach(d => { const dv = S.devices[d]; if (dv) renderDp(dv); });
        } catch (e) {
          toast('Site update failed: ' + (e.message || e), 'err');
          return;
        }
      }
    }
  }

  // Persist mute-state change first so it applies regardless of rename outcome.
  const muteBox = document.getElementById('eg-muted');
  if (muteBox && muteBox.dataset.initial !== undefined) {
    const want = muteBox.checked;
    const had  = muteBox.dataset.initial === '1';
    if (want !== had) {
      // Key on the pre-rename name; if renaming too, we re-post below.
      try {
        await api('POST',
          '/api/device-group/' + encodeURIComponent(oldName) + '/mute',
          { muted: want });
        if (typeof _setGroupMutedLocal === 'function') _setGroupMutedLocal(oldName, want);
      } catch (e) {
        toast('Mute save failed: ' + (e.message || e), 'err');
        return;
      }
    }
  }

  if (newName === oldName) {
    toast('Saved', 'ok');
    closeM('meg');
    return;
  }

  // Reuse the rename pipeline (PATCH every device + DOM patch)
  try {
    const devs = Object.values(S.devices).filter(
      d => (d.group || 'Default Group') === oldName
    );
    for (const d of devs) {
      d.group = newName;
      await api('PATCH', '/api/device/' + d.device_id, { group: newName });
    }

    // If the group was muted, re-apply the mute under the new name (the
    // mute list keys on the name string, which just changed).
    if (muteBox && muteBox.checked) {
      try {
        await api('POST',
          '/api/device-group/' + encodeURIComponent(oldName) + '/mute',
          { muted: false });
        await api('POST',
          '/api/device-group/' + encodeURIComponent(newName) + '/mute',
          { muted: true });
        if (typeof _setGroupMutedLocal === 'function') {
          _setGroupMutedLocal(oldName, false);
          _setGroupMutedLocal(newName, true);
        }
      } catch { /* non-fatal; admin can re-toggle */ }
    }
    // DOM patch (mirrors devices.js renameGroup)
    const wrap = document.getElementById(grpId(oldName));
    if (wrap) {
      const grid = wrap.querySelector('.grp-grid');
      if (grid) grid.dataset.group = newName;
      wrap.id = grpId(newName);
      const labelEl = wrap.querySelector('.grp-label');
      if (labelEl) {
        labelEl.textContent = newName;
        labelEl.replaceWith(labelEl.cloneNode(true));
        const newLabel = wrap.querySelector('.grp-label');
        newLabel.addEventListener('dblclick', () => renameGroup(newLabel, newName));
      }
      const addCard = wrap.querySelector('.dc-add');
      if (addCard) {
        addCard.replaceWith(addCard.cloneNode(true));
        const fresh = wrap.querySelector('.dc-add');
        fresh.addEventListener('click', () => openAddDeviceGroup(newName));
      }
      // Also rebind the gear button so it carries the new name
      const gear = wrap.querySelector('.grp-edit-btn');
      if (gear) {
        gear.replaceWith(gear.cloneNode(true));
        const freshGear = wrap.querySelector('.grp-edit-btn');
        freshGear.addEventListener('click', e => {
          e.stopPropagation();
          openEditGroup(newName);
        });
      }
    }
    toast('Group renamed to "' + newName + '"', 'ok');
    closeM('meg');
  } catch (e) {
    toast('Rename failed: ' + (e.message || e), 'err');
  }
}

// ── Profile actions wired into the alerting.js editor ─────────────

function editProfileFromGroup(profileId) {
  closeM('meg');
  if (typeof openProfileEditor === 'function') {
    openProfileEditor(profileId);
  } else {
    toast('Open the Alerting page to edit profiles', 'err');
  }
}

function overrideGroupProfile(groupName) {
  closeM('meg');
  if (typeof openProfileEditor === 'function') {
    // Create-new mode pre-scoped to this group
    openProfileEditor(null, { scope_type: 'group', scope_value: groupName });
  } else {
    toast('Open the Alerting page to create profiles', 'err');
  }
}

async function resetGroupProfile(groupName, profileId) {
  if (!confirm(`Delete the group-scoped alert profile for "${groupName}"?\n` +
               `Devices in this group will fall back to the global profile.`)) return;
  try {
    await api('DELETE', '/api/alert/profile/' + profileId);
    toast('Group profile reset', 'ok');
    _loadGroupProfileSection(groupName);
  } catch (e) {
    toast('Reset failed: ' + (e.message || e), 'err');
  }
}
