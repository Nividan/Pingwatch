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
          <div class="alrt-section-hdr">Alert Profile</div>
          <div id="eg-profile-body" style="font-size:12px;color:var(--text3)">
            Loading\u2026
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
  if (newName === oldName) { closeM('meg'); return; }

  // Reuse the rename pipeline (PATCH every device + DOM patch)
  try {
    const devs = Object.values(S.devices).filter(
      d => (d.group || 'Default Group') === oldName
    );
    for (const d of devs) {
      d.group = newName;
      await api('PATCH', '/api/device/' + d.device_id, { group: newName });
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
