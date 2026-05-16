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
    <div class="mbox" style="min-width:520px;max-width:600px">
      <div class="mhd">
        <div class="mttl">Edit Group — ${esc(groupName)}</div>
        <button class="mclose" onclick="closeM('meg')">&#x2715;</button>
      </div>
      <div class="mbdy">
        <div class="alrt-section">
          <div class="alrt-section-hdr">Group Name</div>
          <div class="fr">
            <input type="text" id="eg-name" value="${esc(groupName)}" autocomplete="off"/>
            <div class="fh">Rename this group. All devices in the group will follow.</div>
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">Site</div>
          <div class="fr">
            <input type="text" id="eg-site" list="eg-site-dl"
                   placeholder="(loading…)" autocomplete="off"/>
            <datalist id="eg-site-dl"></datalist>
            <div class="fh" id="eg-site-hint">
              Assigning a site here applies to every device in this group.
              Leave empty to clear (Unsited).
            </div>
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">Device Icon (NTM Live map)</div>
          <div class="fr">
            <select id="eg-icon">
              <option value="">— Auto-detect from name / group —</option>
              <option value="switch">Switch</option>
              <option value="bb-switch">Backbone Switch</option>
              <option value="firewall">Firewall</option>
              <option value="wan-switch">WAN Switch</option>
              <option value="server">Server</option>
              <option value="pc">PC / Workstation</option>
              <option value="laptop">Laptop</option>
              <option value="ap">WiFi Access Point</option>
              <option value="connector">Cato Connector</option>
              <option value="remote-pc">Remote PC</option>
              <option value="cloud">Cloud / Internet</option>
              <option value="router">Router / Gateway</option>
              <option value="vm">Virtual Machine</option>
              <option value="appliance">Network Appliance</option>
              <option value="storage">Storage / NAS</option>
              <option value="phone">IP Phone / VoIP</option>
              <option value="camera">IP Camera / CCTV</option>
              <option value="printer">Printer / MFP</option>
              <option value="load-balancer">Load Balancer</option>
              <option value="hypervisor">Hypervisor / ESXi</option>
              <option value="ups">UPS / PDU</option>
              <option value="container">Container Host</option>
              <option value="ipmi">IPMI / BMC</option>
            </select>
            <div class="fh">
              Default icon for every device in this group on the NTM Live map.
              Per-device icon overrides (set from the NTM panel) still take
              precedence.
            </div>
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">Alert Profile</div>
          <div id="eg-profile-body" style="font-size:12px;color:var(--text3)">
            Loading\u2026
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">Alerts</div>
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
            <input type="checkbox" id="eg-muted"/>
            <span>🔕 Mute alerts for this group</span>
          </label>
          <div class="fh" style="margin-top:4px">
            Suppresses alert dispatch and flap events for every device and sensor in this
            group. Probes still run and device cards still reflect their real status.
          </div>
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
  // NTM device-icon default: pull the current setting and select the option.
  _loadGroupIconState(groupName);
}

async function _loadGroupIconState(groupName) {
  const sel = document.getElementById('eg-icon');
  if (!sel) return;
  try {
    const r = await api('GET', '/api/settings/pw_group_icons');
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
          if (typeof pruneEmptyGroups === 'function') pruneEmptyGroups();
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
