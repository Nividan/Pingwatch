// ── Alerting (PRTG-style profiles + action templates + events + maint) ──

let _alertProfiles      = [];
let _alertTemplates     = [];
let _alertEditingProfId = null;   // null = new profile
let _alertEditingTplId  = null;   // null = new template
let _apCurrentPicker    = null;   // picker dropdown state
let _alertEvtFilter     = 'all';
let _alertEvtOffset     = 0;
const _ALERT_EVT_LIMIT  = 100;
let _alertMaintWindows  = [];

// Constants the editor uses
const _AP_TRIG_LABELS = {
  down:              'Down',
  warning:           'Warning',
  down_recovered:    'Down → recovered',
  warning_recovered: 'Warning → recovered',
};
const _AP_TRIG_ORDER = ['down', 'down', 'down_recovered',
                        'warning', 'warning', 'warning_recovered'];
const _AP_DEFAULT_DELAYS = [60, 600, 0, 60, 600, 0];

// ═══════════════════════════════════════════════════════════════
// PROFILES + TEMPLATES — top-level loaders called from settings tab
// ═══════════════════════════════════════════════════════════════

async function _alertingLoadProfiles() {
  // Fetch + cache profiles/templates regardless of which surface is mounted.
  // Settings tab DOM was removed in v1.0 (#alrt-list no longer exists); the
  // new top-level Alerting page (#al-pg-profiles) is the primary surface.
  // Renders no-op if the target DOM is missing.
  const settingsList = document.getElementById('alrt-list');
  if (settingsList) {
    settingsList.innerHTML = '<div class="alrt-loading">Loading\u2026</div>';
  }
  applyRbac();
  try {
    const [pr, tr] = await Promise.all([
      api('GET', '/api/alert/profiles'),
      api('GET', '/api/alert/action-templates'),
    ]);
    _alertProfiles  = pr.profiles  || [];
    _alertTemplates = tr.templates || [];
    _alertingRenderProfiles();   // no-op if #alrt-list missing
    _alertingRenderTemplates();  // no-op if #alrt-tpl-list missing
    if (document.getElementById('al-pg-profiles')) {
      _alPgRenderProfiles();
      _alPgRenderChannels();
      _alPgRenderEscalation();
      _alPgRefreshSubtitle();
    }
  } catch (e) {
    if (settingsList) {
      settingsList.innerHTML = `<div class="alrt-err">Failed to load profiles: ${esc(String(e))}</div>`;
    }
  }
}

function _alertingRenderProfiles() {
  const wrap = document.getElementById('alrt-list');
  if (!wrap) return;
  if (!_alertProfiles.length) {
    wrap.innerHTML = `<div class="alrt-empty">
      No alert profiles yet. Click <strong>＋ New Profile</strong> to create one.
    </div>`;
    return;
  }
  // Order: global → group → device → sensor
  const rank = {global: 0, group: 1, device: 2, sensor: 3};
  const sorted = [..._alertProfiles].sort(
    (a, b) => (rank[a.scope_type] - rank[b.scope_type])
              || a.name.localeCompare(b.name)
  );
  wrap.innerHTML = `<div class="alrt-tree">${sorted.map(p => _alertProfileRow(p)).join('')}</div>`;
  applyRbac();
}

function _alertProfileRow(p) {
  const stageCount = (p.stages || []).length;
  const scopeLbl = p.scope_type === 'global'
    ? 'Global'
    : `${p.scope_type[0].toUpperCase()}${p.scope_type.slice(1)}: ${esc(p.scope_value || '')}`;
  const dot = p.enabled
    ? '<span class="alrt-dot-on" title="Enabled">●</span>'
    : '<span class="alrt-dot-off" title="Disabled">○</span>';
  const safeName = esc(p.name).replace(/'/g, '&#39;');
  return `
    <div class="alrt-tree-row">
      ${dot}
      <span class="alrt-tree-scope">${scopeLbl}</span>
      <span class="alrt-tree-name">${esc(p.name)}</span>
      <span style="font-size:11px;color:var(--text3)">${stageCount} stage${stageCount === 1 ? '' : 's'}</span>
      <div class="alrt-tree-btns">
        <button class="btn-sm rbac-op"    onclick="_alertingProfToggle(${p.id})">${p.enabled ? 'Disable' : 'Enable'}</button>
        <button class="btn-sm rbac-admin" onclick="openProfileEditor(${p.id})">Edit</button>
        <button class="btn-sm rbac-admin" onclick="_alertingProfTest(${p.id},'${safeName}')">Test</button>
        <button class="btn-sm rbac-admin alrt-del-btn" onclick="_alertingProfDelete(${p.id},'${safeName}')">Delete</button>
      </div>
    </div>`;
}

function _alertingRenderTemplates() {
  const wrap = document.getElementById('alrt-tpl-list');
  if (!wrap) return;
  if (!_alertTemplates.length) {
    wrap.innerHTML = `<div class="alrt-empty">
      No action templates yet. Click <strong>＋ New Action Template</strong> to create one.
    </div>`;
    return;
  }
  wrap.innerHTML = `<div class="alrt-tree">${_alertTemplates.map(t => _alertTemplateRow(t)).join('')}</div>`;
  applyRbac();
}

function _alertTemplateRow(t) {
  const cfgSummary = _tplSummary(t);
  const safeName = esc(t.name).replace(/'/g, '&#39;');
  return `
    <div class="alrt-tree-row">
      <span class="alrt-tree-scope">${t.atype}</span>
      <span class="alrt-tree-name">${esc(t.name)}</span>
      <span style="font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0">${cfgSummary}</span>
      <div class="alrt-tree-btns">
        <button class="btn-sm rbac-admin" onclick="openTemplateEditor(${t.id})">Edit</button>
        <button class="btn-sm rbac-admin alrt-del-btn" onclick="_alertingTplDelete(${t.id},'${safeName}')">Delete</button>
      </div>
    </div>`;
}

function _tplSummary(t) {
  const c = t.config || {};
  if (t.atype === 'email') {
    const parts = [];
    if (c.to_users)  parts.push((Array.isArray(c.to_users)  ? c.to_users  : [c.to_users]).join(', '));
    if (c.to_groups) parts.push((Array.isArray(c.to_groups) ? c.to_groups : [c.to_groups]).map(g => `group:${g}`).join(', '));
    if (c.to_emails || c.to) parts.push(c.to_emails || c.to);
    return esc(parts.join(' · ')) || '<i>no recipients</i>';
  }
  if (t.atype === 'webhook') return esc(c.url || '');
  if (t.atype === 'syslog')  return esc((c.host || 'default') + ':' + (c.port || 514));
  if (t.atype === 'browser') return esc(c.title || '(default title)');
  return '';
}

// ── Profile actions ────────────────────────────────────────────────

async function _alertingProfToggle(id) {
  try { await api('POST', `/api/alert/profile/${id}/toggle`); } catch (e) { toast(e.message, 'err'); return; }
  _alertingLoadProfiles();
}

async function _alertingProfTest(id, name) {
  try {
    const d = await api('POST', `/api/alert/profile/${id}/test`);
    toast(d.msg || `Test fired for "${name}"`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function _alertingProfDelete(id, name) {
  if (!confirm(`Delete alert profile "${name}"?`)) return;
  try {
    await api('DELETE', `/api/alert/profile/${id}`);
    toast(`Profile "${name}" deleted`, 'info');
    _alertingLoadProfiles();
  } catch (e) { toast(e.message, 'err'); }
}

async function _alertingTplDelete(id, name) {
  if (!confirm(`Delete action template "${name}"?\n` +
               `This will fail if any profile stage still uses it.`)) return;
  try {
    await api('DELETE', `/api/alert/action-template/${id}`);
    toast(`Template "${name}" deleted`, 'info');
    _alertingLoadProfiles();
  } catch (e) { toast(e.message, 'err'); }
}

// ═══════════════════════════════════════════════════════════════
// PROFILE EDITOR modal — PRTG-style 6-stage table
// ═══════════════════════════════════════════════════════════════

// Cleanup wrapper: close the profile modal AND any open template picker
// dropdown + its document-level click listener. Without this, closing the
// modal while the picker is open leaves #_ap_picker_dd orphaned on body
// and the capture-phase click listener registered on document.
function _apCloseProfModal() {
  document.getElementById('_ap_picker_dd')?.remove();
  try { document.removeEventListener('click', _apPickerOutside, true); } catch (_) {}
  _apCurrentPicker = null;
  closeM('alrt-prof-modal');
}

async function openProfileEditor(id, scopeDefaults = null) {
  _apCloseProfModal();
  // Make sure templates are loaded for the picker
  if (!_alertTemplates.length) {
    try {
      const tr = await api('GET', '/api/alert/action-templates');
      _alertTemplates = tr.templates || [];
    } catch (_) { /* will show empty picker */ }
  }
  // Make sure the profile is in our cache (may have been opened from elsewhere)
  let prof = id !== null ? _alertProfiles.find(p => p.id === id) : null;
  if (!prof && id !== null) {
    try {
      const r = await api('GET', `/api/alert/profile/${id}`);
      prof = r.profile;
    } catch (e) { toast(e.message, 'err'); return; }
  }
  _alertEditingProfId = prof ? prof.id : null;

  const name        = prof?.name        || '';
  const enabled     = prof ? prof.enabled : true;
  const scopeType   = prof?.scope_type  || scopeDefaults?.scope_type  || 'global';
  const scopeValue  = prof?.scope_value || scopeDefaults?.scope_value || '';
  // Exclusive flag — v1.0+. Defaults to false (additive cascade) for new
  // profiles. Migrated pre-existing profiles have exclusive=true so they
  // continue to short-circuit broader matches like before.
  const exclusive   = prof ? !!prof.exclusive : false;

  // Pre-fill stages by trigger position; if profile has fewer than 6, blanks
  const stagesByPos = _AP_TRIG_ORDER.map((trig, i) => {
    const matches = (prof?.stages || []).filter(s => s.trigger_state === trig);
    // For 'down' and 'warning' (positions 0,1 / 3,4) take by index within trigger
    if (trig === 'down') {
      const downStages = (prof?.stages || []).filter(s => s.trigger_state === 'down');
      return downStages[i] || null;
    }
    if (trig === 'warning') {
      const warnStages = (prof?.stages || []).filter(s => s.trigger_state === 'warning');
      return warnStages[i - 3] || null;
    }
    return matches[0] || null;
  });

  const tplChipPicker = (ids) => {
    const selected = Array.isArray(ids) ? ids.map(Number) : [];
    const chips = _alertTemplates
      .filter(t => selected.includes(t.id))
      .map(t =>
        `<span class="ap-chip" data-id="${t.id}">` +
        `${esc(t.name)}<span class="ap-tpl-badge ap-tpl-badge-${t.atype}">${t.atype.toUpperCase()}</span>` +
        `<button class="ap-chip-rm" onclick="_apChipRemove(this)">&#x2715;</button>` +
        `</span>`
      ).join('');
    const allAdded = selected.length >= _alertTemplates.length;
    return chips + (allAdded ? '' : `<button class="ap-add-btn" onclick="_apPickerOpen(this)">＋ Add</button>`);
  };

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'alrt-prof-modal';
  _overlayClose(o, () => _apCloseProfModal());

  o.innerHTML = `
    <div class="mbox alrt-editor-box">
      <div class="mhd">
        <div class="mttl">${prof ? '✏ Edit Alert Profile' : '＋ New Alert Profile'}</div>
        <button class="mclose" onclick="_apCloseProfModal()">&#x2715;</button>
      </div>
      <div class="mbdy alrt-editor-body">
        <div class="alrt-row3">
          <div class="fr" style="flex:2">
            <label class="fl">Name</label>
            <input type="text" id="ap-name" value="${esc(name)}"
              placeholder="e.g. Production firewalls" autocomplete="off" maxlength="200"/>
          </div>
          <div class="fr">
            <label class="fl">Scope</label>
            <select id="ap-scope-type" onchange="_apScopeChange()">
              <option value="global" ${scopeType === 'global' ? 'selected' : ''}>Global</option>
              <option value="site"   ${scopeType === 'site'   ? 'selected' : ''}>Site</option>
              <option value="group"  ${scopeType === 'group'  ? 'selected' : ''}>Group</option>
              <option value="device" ${scopeType === 'device' ? 'selected' : ''}>Device</option>
              <option value="sensor" ${scopeType === 'sensor' ? 'selected' : ''}>Sensor</option>
            </select>
          </div>
          <div class="fr" id="ap-scope-val-row" style="${scopeType === 'global' ? 'display:none' : ''}">
            <label class="fl" id="ap-scope-val-lbl">${
              scopeType === 'site'   ? 'Site name' :
              scopeType === 'group'  ? 'Group name' :
              scopeType === 'device' ? 'Device ID' : 'did/sid'
            }</label>
            <input type="text" id="ap-scope-val" value="${esc(scopeValue)}" list="ap-scope-val-dl" autocomplete="off"/>
            <datalist id="ap-scope-val-dl"></datalist>
          </div>
          <div class="fr" style="align-self:flex-end;padding-bottom:14px;display:flex;flex-direction:column;gap:4px">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="ap-enabled" ${enabled ? 'checked' : ''}/>
              <span style="font-size:12px;color:var(--text2)">Enabled</span>
            </label>
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer"
                   title="When checked, profiles at broader scopes (e.g. site or global) are SUPPRESSED when this one matches. Off = additive — every matching profile fires.">
              <input type="checkbox" id="ap-exclusive" ${exclusive ? 'checked' : ''}/>
              <span style="font-size:12px;color:var(--text2)">Exclusive</span>
            </label>
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">Escalation Stages</div>
          <table class="alrt-profile-table">
            <thead>
              <tr>
                <th>Trigger</th>
                <th>Delay (s)</th>
                <th>Repeat (min)</th>
                <th>Action templates</th>
              </tr>
            </thead>
            <tbody>
              ${_AP_TRIG_ORDER.map((trig, i) => {
                const s = stagesByPos[i];
                const isRecovery = trig.endsWith('_recovered');
                const trigClass = isRecovery ? 'alrt-trig-recovery'
                                : trig === 'down' ? 'alrt-trig-down' : 'alrt-trig-warning';
                const stageNum = i < 2 ? i + 1 : (i >= 3 && i < 5) ? i - 2 : null;
                const numBadge = stageNum != null
                  ? `<span class="alrt-stage-num">${stageNum}</span>` : '';
                const hdrRow = (i === 0 || i === 3) ? `
                  <tr class="alrt-group-hdr-row">
                    <td colspan="4"><div class="alrt-group-hdr-label alrt-group-hdr-${i === 0 ? 'down' : 'warn'}">
                      ${i === 0 ? 'Down conditions' : 'Warning conditions'}
                    </div></td>
                  </tr>` : '';
                return hdrRow + `
                  <tr class="alrt-stage-row alrt-stage-${trig}" data-trig="${trig}" data-pos="${i}">
                    <td class="alrt-trig-cell">${numBadge}<span class="${trigClass}">${_AP_TRIG_LABELS[trig]}</span></td>
                    <td>${isRecovery
                        ? '<span class="ap-dash">—</span>'
                        : `<input type="number" class="ap-delay" value="${s?.delay_s ?? _AP_DEFAULT_DELAYS[i]}" min="0" step="10"/>`}</td>
                    <td>${isRecovery
                        ? '<span class="ap-dash">—</span>'
                        : `<input type="number" class="ap-repeat" value="${s?.repeat_min ?? 0}" min="0" step="5"/>`}</td>
                    <td><div class="ap-act-picker">${tplChipPicker(s?.action_ids || [])}</div></td>
                  </tr>`;
              }).join('')}
            </tbody>
          </table>
          <div class="alrt-hint">
            Stages with no template are skipped. Add templates from the
            <strong>Action Templates</strong> section below.
          </div>
        </div>
      </div>
      <div class="mft">
        ${prof ? `<button class="btn-s alrt-del-btn" onclick="_alertingProfDelete(${prof.id},'${esc(prof.name).replace(/'/g, "&#39;")}')">Delete</button>` : ''}
        <button class="btn-s" onclick="_apCloseProfModal()">Cancel</button>
        <button class="btn-p" id="ap-save-btn" onclick="_alertingSaveProfile()">Save Profile</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ap-name')?.focus(), 60);
}

// ── Chip action-template picker ──────────────────────────────────────────────
function _apChipRemove(btn) {
  const picker = btn.closest('.ap-act-picker');
  btn.closest('.ap-chip').remove();
  // Restore add button if it was hidden
  if (!picker.querySelector('.ap-add-btn')) {
    const addBtn = document.createElement('button');
    addBtn.className = 'ap-add-btn';
    addBtn.setAttribute('onclick', '_apPickerOpen(this)');
    addBtn.textContent = '＋ Add';
    picker.appendChild(addBtn);
  }
}

function _apPickerOpen(btn) {
  document.getElementById('_ap_picker_dd')?.remove();
  document.removeEventListener('click', _apPickerOutside, true);
  _apCurrentPicker = btn.closest('.ap-act-picker');
  const selectedIds = new Set(
    Array.from(_apCurrentPicker.querySelectorAll('.ap-chip[data-id]')).map(c => +c.dataset.id)
  );
  const available = _alertTemplates.filter(t => !selectedIds.has(t.id));
  if (!available.length) return;
  const dd = document.createElement('div');
  dd.id = '_ap_picker_dd';
  dd.className = 'ap-picker-dd';
  dd.innerHTML = available.map(t =>
    `<div class="ap-picker-item" onclick="_apPickerSelect(${t.id})">` +
    `<span class="ap-picker-name">${esc(t.name)}</span>` +
    `<span class="ap-tpl-badge ap-tpl-badge-${t.atype}">${t.atype.toUpperCase()}</span>` +
    `</div>`
  ).join('');
  document.body.appendChild(dd);
  const r = btn.getBoundingClientRect();
  dd.style.left = r.left + window.scrollX + 'px';
  dd.style.top  = (r.bottom + window.scrollY + 4) + 'px';
  setTimeout(() => document.addEventListener('click', _apPickerOutside, {capture: true}), 0);
}

function _apPickerSelect(id) {
  if (!_apCurrentPicker) return;
  const t = _alertTemplates.find(x => x.id === id);
  if (!t || _apCurrentPicker.querySelector(`.ap-chip[data-id="${id}"]`)) {
    document.getElementById('_ap_picker_dd')?.remove();
    return;
  }
  const chip = document.createElement('span');
  chip.className = 'ap-chip';
  chip.dataset.id = id;
  chip.innerHTML = `${esc(t.name)}<span class="ap-tpl-badge ap-tpl-badge-${t.atype}">${t.atype.toUpperCase()}</span>` +
    `<button class="ap-chip-rm" onclick="_apChipRemove(this)">&#x2715;</button>`;
  const addBtn = _apCurrentPicker.querySelector('.ap-add-btn');
  _apCurrentPicker.insertBefore(chip, addBtn);
  // Hide add button if all templates are now selected
  const selectedCount = _apCurrentPicker.querySelectorAll('.ap-chip[data-id]').length;
  if (selectedCount >= _alertTemplates.length && addBtn) addBtn.remove();
  document.getElementById('_ap_picker_dd')?.remove();
  document.removeEventListener('click', _apPickerOutside, true);
  _apCurrentPicker = null;
}

function _apPickerOutside(e) {
  const dd = document.getElementById('_ap_picker_dd');
  if (dd && !dd.contains(e.target)) {
    dd.remove();
    document.removeEventListener('click', _apPickerOutside, true);
    _apCurrentPicker = null;
  }
}

function _apScopeChange() {
  const sel  = document.getElementById('ap-scope-type')?.value;
  const row  = document.getElementById('ap-scope-val-row');
  const lbl  = document.getElementById('ap-scope-val-lbl');
  const dl   = document.getElementById('ap-scope-val-dl');
  if (!row) return;
  row.style.display = sel === 'global' ? 'none' : '';
  if (lbl) lbl.textContent =
    sel === 'site'   ? 'Site name'  :
    sel === 'group'  ? 'Group name' :
    sel === 'device' ? 'Device ID'  : 'did/sid';
  // Populate the datalist with relevant suggestions per scope.
  if (dl) {
    if (sel === 'site') {
      _populateSiteDatalist('ap-scope-val-dl');
    } else if (sel === 'group') {
      const groups = [...new Set(Object.values(S.devices || {})
        .map(d => d.group || 'Default Group'))].sort();
      dl.innerHTML = groups.map(g => `<option value="${esc(g)}"></option>`).join('');
    } else {
      dl.innerHTML = '';
    }
  }
}

async function _alertingSaveProfile() {
  const name       = (document.getElementById('ap-name')?.value || '').trim();
  const enabled    = !!document.getElementById('ap-enabled')?.checked;
  const exclusive  = !!document.getElementById('ap-exclusive')?.checked;
  const scopeType  = document.getElementById('ap-scope-type')?.value || 'global';
  const scopeValue = (document.getElementById('ap-scope-val')?.value || '').trim();

  if (!name) { toast('Name is required', 'err'); return; }
  if (scopeType !== 'global' && !scopeValue) {
    toast('Scope value is required for non-global scopes', 'err'); return;
  }

  // Collect stages from the table rows
  const stages = [];
  document.querySelectorAll('#alrt-prof-modal .alrt-stage-row').forEach(row => {
    const trig = row.dataset.trig;
    const action_ids = Array.from(row.querySelectorAll('.ap-chip[data-id]'))
      .map(c => parseInt(c.dataset.id)).filter(n => n > 0);
    if (!action_ids.length) return;   // empty stage row — skip
    const isRecovery = trig.endsWith('_recovered');
    stages.push({
      trigger_state: trig,
      delay_s:    isRecovery ? 0 : parseInt(row.querySelector('.ap-delay')?.value  || '0'),
      repeat_min: isRecovery ? 0 : parseInt(row.querySelector('.ap-repeat')?.value || '0'),
      action_ids,
    });
  });

  const payload = {
    name, enabled, exclusive,
    scope_type: scopeType,
    scope_value: scopeType === 'global' ? '' : scopeValue,
    stages,
  };

  const btn = document.getElementById('ap-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving\u2026'; }
  const isNew  = _alertEditingProfId === null;
  const method = isNew ? 'POST' : 'PATCH';
  const path   = isNew ? '/api/alert/profile' : `/api/alert/profile/${_alertEditingProfId}`;

  try {
    await api(method, path, payload);
    toast(isNew ? `Profile "${name}" created` : `Profile "${name}" updated`, 'ok');
    _apCloseProfModal();
    _alertingLoadProfiles();
  } catch (e) {
    toast(e.message || 'Save failed', 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Profile'; }
  }
}

// ═══════════════════════════════════════════════════════════════
// ACTION TEMPLATE editor
// ═══════════════════════════════════════════════════════════════

async function openTemplateEditor(id) {
  closeM('alrt-tpl-modal');
  let tpl = id !== null ? _alertTemplates.find(t => t.id === id) : null;
  if (!tpl && id !== null) {
    try {
      const r = await api('GET', `/api/alert/action-template/${id}`);
      tpl = r.template;
    } catch (e) { toast(e.message, 'err'); return; }
  }
  _alertEditingTplId = tpl ? tpl.id : null;

  const name  = tpl?.name  || '';
  const atype = tpl?.atype || 'email';
  const cfg   = tpl?.config || {};

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'alrt-tpl-modal';
  _overlayClose(o, () => closeM('alrt-tpl-modal'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,600px)">
      <div class="mhd">
        <div class="mttl">${tpl ? '✏ Edit Action Template' : '＋ New Action Template'}</div>
        <button class="mclose" onclick="closeM('alrt-tpl-modal')">&#x2715;</button>
      </div>
      <div class="mbdy">
        <div class="fr">
          <label class="fl">Name</label>
          <input type="text" id="at-name" value="${esc(name)}"
            placeholder="e.g. Email admin" autocomplete="off" maxlength="200"/>
        </div>
        <div class="fr">
          <label class="fl">Action type</label>
          <select id="at-type" onchange="_atTypeChange()">
            <option value="email"   ${atype === 'email'   ? 'selected' : ''}>Email</option>
            <option value="webhook" ${atype === 'webhook' ? 'selected' : ''}>Webhook</option>
            <option value="syslog"  ${atype === 'syslog'  ? 'selected' : ''}>Syslog</option>
            <option value="browser" ${atype === 'browser' ? 'selected' : ''}>Browser notification</option>
          </select>
        </div>
        <div id="at-cfg-pane">${_atCfgHtml(atype, cfg)}</div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('alrt-tpl-modal')">Cancel</button>
        <button class="btn-p" id="at-save-btn" onclick="_alertingSaveTemplate()">Save Template</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('at-name')?.focus(), 60);
  if (atype === 'email') _atPopulateEmailSelects(cfg);
}

function _atTypeChange() {
  const t = document.getElementById('at-type')?.value || 'email';
  const pane = document.getElementById('at-cfg-pane');
  if (pane) { pane.innerHTML = _atCfgHtml(t, {}); if (t === 'email') _atPopulateEmailSelects({}); }
}

async function _atPopulateEmailSelects(cfg) {
  const divUsers  = document.getElementById('at-users-chk');
  const divGroups = document.getElementById('at-groups-chk');
  if (!divUsers && !divGroups) return;

  const savedUsers  = Array.isArray(cfg.to_users)  ? cfg.to_users
                    : (cfg.to_users  ? [cfg.to_users]  : []);
  const savedGroups = Array.isArray(cfg.to_groups) ? cfg.to_groups.map(String)
                    : (cfg.to_groups ? [String(cfg.to_groups)] : []);

  let users = [], groups = [];
  try {
    const [uRes, gRes] = await Promise.all([
      api('GET', '/api/users'),
      api('GET', '/api/user/groups'),
    ]);
    users  = uRes.users  || [];
    groups = gRes.groups || [];
  } catch (_) {}

  if (divUsers) {
    if (!users.length) {
      divUsers.innerHTML = '<span style="color:var(--text3);font-size:.85em">No users found</span>';
    } else {
      divUsers.innerHTML = users.map(u => {
        const chk = savedUsers.includes(u.username) ? ' checked' : '';
        return `<label class="chk-item"><input type="checkbox" value="${esc(u.username)}"${chk}> ${esc(u.username)}</label>`;
      }).join('');
    }
  }
  if (divGroups) {
    if (!groups.length) {
      divGroups.innerHTML = '<span style="color:var(--text3);font-size:.85em">No groups found</span>';
    } else {
      divGroups.innerHTML = groups.map(g => {
        const chk = savedGroups.includes(String(g.id)) ? ' checked' : '';
        return `<label class="chk-item"><input type="checkbox" value="${esc(String(g.id))}"${chk}> ${esc(g.name)}</label>`;
      }).join('');
    }
  }
}

function _atCfgHtml(atype, cfg) {
  if (atype === 'email') {
    const emails = cfg.to_emails || cfg.to || '';
    return `
      <div class="fr"><label class="fl">Users</label>
        <div id="at-users-chk" class="chk-list"><span style="color:var(--text3);font-size:.85em">Loading…</span></div></div>
      <div class="fr"><label class="fl">Groups</label>
        <div id="at-groups-chk" class="chk-list"><span style="color:var(--text3);font-size:.85em">Loading…</span></div></div>
      <div class="fr"><label class="fl">Extra emails (comma-separated)</label>
        <input type="text" id="at-emails" value="${esc(emails)}" placeholder="ops@example.com"/></div>
      <div class="fr"><label class="fl">Subject template (optional)</label>
        <input type="text" id="at-subject" value="${esc(cfg.subject || '')}"
          placeholder="[PingWatch] {dname}/{sname} — {event_type}"/></div>
      <div class="fr"><label class="fl">Body template (optional)</label>
        <textarea id="at-body" rows="3" placeholder="Leave blank for the default HTML alert">${esc(cfg.body || '')}</textarea></div>`;
  }
  if (atype === 'webhook') {
    return `
      <div class="fr"><label class="fl">URL</label>
        <input type="text" id="at-url" value="${esc(cfg.url || '')}" placeholder="https://hooks.example.com/\u2026"/></div>
      <div class="fr"><label class="fl">Method</label>
        <select id="at-method">
          <option value="POST" ${cfg.method !== 'PUT' ? 'selected' : ''}>POST</option>
          <option value="PUT"  ${cfg.method === 'PUT'  ? 'selected' : ''}>PUT</option>
        </select></div>
      <div class="fr"><label class="fl">Body template (optional, JSON or text)</label>
        <textarea id="at-body" rows="4" placeholder='{"text":"{dname}/{sname} {event_type}"}'>${esc(cfg.body || '')}</textarea></div>
      <div class="fr"><label class="fl" title="When notification batching is enabled, batch-aware receivers get one POST containing an array of alerts instead of N separate POSTs. Leave off if your receiver expects one alert per request (most Slack/Teams/generic hooks).">Batch-aware receiver</label>
        <label style="display:flex;align-items:center;gap:8px;padding-top:6px">
          <input type="checkbox" id="at-batch-aware" ${cfg.batch_aware ? 'checked' : ''}/>
          <span style="font-size:12px;color:var(--text3)">Receiver can handle batched payloads (&#123; count, alerts: [...] &#125;)</span>
        </label></div>`;
  }
  if (atype === 'syslog') {
    return `
      <div class="fr"><label class="fl">Host (blank = use global syslog server)</label>
        <input type="text" id="at-host" value="${esc(cfg.host || '')}" placeholder="syslog.example.com"/></div>
      <div class="fr"><label class="fl">Port</label>
        <input type="number" id="at-port" value="${cfg.port || 514}" min="1" max="65535"/></div>
      <div class="fr"><label class="fl">Protocol</label>
        <select id="at-proto">
          <option value="udp" ${cfg.proto !== 'tcp' ? 'selected' : ''}>UDP</option>
          <option value="tcp" ${cfg.proto === 'tcp' ? 'selected' : ''}>TCP</option>
        </select></div>`;
  }
  // browser
  return `
    <div class="fr"><label class="fl">Title template</label>
      <input type="text" id="at-title" value="${esc(cfg.title || '')}"
        placeholder="[{severity}] {dname}/{sname}"/></div>
    <div class="fr"><label class="fl">Body template</label>
      <input type="text" id="at-body" value="${esc(cfg.body || '')}"
        placeholder="{event_type}: {detail}"/></div>
    <div class="fr"><label class="fl">Sound</label>
      <select id="at-sound">
        <option value="alert"  ${cfg.sound !== 'double' && cfg.sound !== 'none' ? 'selected' : ''}>alert</option>
        <option value="double" ${cfg.sound === 'double' ? 'selected' : ''}>double</option>
        <option value="none"   ${cfg.sound === 'none'   ? 'selected' : ''}>none</option>
      </select></div>`;
}

async function _alertingSaveTemplate() {
  const name  = (document.getElementById('at-name')?.value || '').trim();
  const atype = document.getElementById('at-type')?.value || 'email';
  if (!name) { toast('Name is required', 'err'); return; }

  const cfg = {};
  if (atype === 'email') {
    const users  = Array.from(document.querySelectorAll('#at-users-chk input:checked')).map(i => i.value);
    const groups = Array.from(document.querySelectorAll('#at-groups-chk input:checked')).map(i => parseInt(i.value)).filter(n => n > 0);
    const emails = (document.getElementById('at-emails')?.value || '').trim();
    if (users.length)  cfg.to_users  = users;
    if (groups.length) cfg.to_groups = groups;
    if (emails) cfg.to_emails = emails;
    const subj  = (document.getElementById('at-subject')?.value || '').trim();
    const body  = (document.getElementById('at-body')?.value || '').trim();
    if (subj) cfg.subject = subj;
    if (body) cfg.body    = body;
  } else if (atype === 'webhook') {
    cfg.url    = (document.getElementById('at-url')?.value || '').trim();
    cfg.method = document.getElementById('at-method')?.value || 'POST';
    const body = (document.getElementById('at-body')?.value || '').trim();
    if (body) cfg.body = body;
    if (document.getElementById('at-batch-aware')?.checked) cfg.batch_aware = true;
  } else if (atype === 'syslog') {
    const host = (document.getElementById('at-host')?.value || '').trim();
    if (host) cfg.host = host;
    cfg.port  = parseInt(document.getElementById('at-port')?.value  || '514');
    cfg.proto = document.getElementById('at-proto')?.value || 'udp';
  } else {  // browser
    cfg.title = (document.getElementById('at-title')?.value || '').trim();
    cfg.body  = (document.getElementById('at-body')?.value  || '').trim();
    cfg.sound = document.getElementById('at-sound')?.value  || 'alert';
  }

  const payload = { name, atype, config: cfg };
  const btn = document.getElementById('at-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving\u2026'; }
  const isNew  = _alertEditingTplId === null;
  const method = isNew ? 'POST' : 'PATCH';
  const path   = isNew ? '/api/alert/action-template' : `/api/alert/action-template/${_alertEditingTplId}`;
  try {
    await api(method, path, payload);
    toast(isNew ? `Template "${name}" created` : `Template "${name}" updated`, 'ok');
    closeM('alrt-tpl-modal');
    _alertingLoadProfiles();
  } catch (e) {
    toast(e.message || 'Save failed', 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Template'; }
  }
}

// ═══════════════════════════════════════════════════════════════
// ALERT EVENTS sub-tab (history)
// ═══════════════════════════════════════════════════════════════

async function _alertingLoadEvents(state, reset) {
  const panelId = 'alrt-history-panel';
  const wrap    = document.getElementById(panelId);
  if (!wrap) return;

  if (reset) _alertEvtOffset = 0;

  const filterBar = `<div class="alrt-evt-filters">
    <span style="font-size:12px;color:var(--text2)">State:</span>
    ${['all','active','acknowledged','resolved','suppressed'].map(s =>
      `<button class="alrt-flt-btn ${_alertEvtFilter===s?'active':''}"
         onclick="_alertEvtSetFilter('${s}')">${s}</button>`
    ).join('')}
  </div>`;

  wrap.innerHTML = `
    <div class="alrt-panel-hdr" style="justify-content:flex-start;gap:12px">${filterBar}</div>
    <div id="alrt-evt-list-${panelId}"><div class="alrt-loading">Loading\u2026</div></div>
    <div id="alrt-evt-pager-${panelId}" class="alrt-pager"></div>`;

  await _alertingFetchEvents(state || _alertEvtFilter, panelId);
}

async function _alertingFetchEvents(state, panelId) {
  const listId = `alrt-evt-list-${panelId}`;
  const list   = document.getElementById(listId);
  if (!list) return;
  const qs = `state=${state}&limit=${_ALERT_EVT_LIMIT}&offset=${_alertEvtOffset}`;
  try {
    const d = await api('GET', `/api/alert/events?${qs}`);
    const events = d.events || [];
    if (typeof _scheduleBadgePoll === 'function') _scheduleBadgePoll();
    if (!events.length) {
      list.innerHTML = '<div class="alrt-empty">No events.</div>';
      document.getElementById(`alrt-evt-pager-${panelId}`).innerHTML = '';
      return;
    }
    list.innerHTML = events.map(e => _alertEvtRow(e)).join('');
    applyRbac();
    const pager = document.getElementById(`alrt-evt-pager-${panelId}`);
    if (pager) {
      const hasPrev = _alertEvtOffset > 0;
      const hasNext = events.length === _ALERT_EVT_LIMIT;
      pager.innerHTML = `
        <button class="btn-sm" ${hasPrev?'':'disabled'}
          onclick="_alertEvtPage('${state}','${panelId}',-1)">◀ Prev</button>
        <span style="font-size:11px;color:var(--text3)">offset ${_alertEvtOffset}</span>
        <button class="btn-sm" ${hasNext?'':'disabled'}
          onclick="_alertEvtPage('${state}','${panelId}',1)">Next ▶</button>`;
    }
  } catch (e) {
    list.innerHTML = `<div class="alrt-err">Error: ${esc(String(e))}</div>`;
  }
}

function _alertEvtPage(state, panelId, dir) {
  _alertEvtOffset = Math.max(0, _alertEvtOffset + dir * _ALERT_EVT_LIMIT);
  _alertingFetchEvents(state, panelId);
}

function _alertEvtSetFilter(state) {
  _alertEvtFilter = state;
  _alertingLoadEvents(state, true);
}

function _alertEvtRow(e) {
  const stateCls = {active:'alrt-state-active', acknowledged:'alrt-state-ack',
                    resolved:'alrt-state-res', suppressed:'alrt-state-sup'}[e.state] || '';
  const ts = e.triggered_at ? new Date(e.triggered_at * 1000).toLocaleString() : '—';
  const repeat = e.repeat_count > 1
    ? `<span class="alrt-repeat" title="Fired ${e.repeat_count} times">×${e.repeat_count}</span>` : '';
  const ackInfo = e.state === 'acknowledged' && e.ack_by
    ? `<span style="color:var(--text3);font-size:10px">ack by ${esc(e.ack_by)}</span>` : '';
  const sevCls = e.severity === 'critical' ? 'alrt-sev-crit'
               : e.severity === 'recovery' ? 'alrt-sev-recovery'
               : e.severity === 'info'     ? 'alrt-sev-info' : 'alrt-sev-warn';
  return `
    <div class="alrt-evt-row">
      <div class="alrt-evt-left">
        <div class="alrt-evt-top">
          <span class="alrt-state-badge ${stateCls}">${e.state}</span>
          <span class="alrt-sev-badge ${sevCls}">${esc(e.severity)}</span>
          <span class="alrt-evt-rule">${esc(e.profile_name || '')}</span>
          ${repeat}
        </div>
        <div class="alrt-evt-detail">
          <span class="alrt-evt-who">${esc(e.dname || e.did)} / ${esc(e.sname || e.sid)}</span>
          <span class="alrt-evt-type">${esc(e.event_type)}</span>
          <span class="alrt-evt-ts">${ts}</span>
          ${e.detail ? `<span class="alrt-evt-msg">${esc(e.detail)}</span>` : ''}
          ${ackInfo}
        </div>
      </div>
      <div class="alrt-card-btns"></div>
    </div>`;
}

async function _alertAck(id) {
  try {
    const d = await api('POST', `/api/alert/event/${id}/ack`);
    if (!d || !d.ok) {
      toast(d?.error || 'Failed to acknowledge', 'err');
      return;
    }
  } catch (e) {
    toast(e.message, 'err');
    return;
  }
  toast('Alert acknowledged', 'ok');
  _alertingLoadEvents(_alertEvtFilter, true);
  if (typeof _scheduleBadgePoll === 'function') _scheduleBadgePoll();
}

async function _alertResolve(id) {
  try {
    const d = await api('POST', `/api/alert/event/${id}/resolve`);
    if (!d || !d.ok) {
      toast(d?.error || 'Failed to resolve', 'err');
      return;
    }
  } catch (e) {
    toast(e.message, 'err');
    return;
  }
  toast('Alert resolved', 'ok');
  _alertingLoadEvents(_alertEvtFilter, true);
  if (typeof _scheduleBadgePoll === 'function') _scheduleBadgePoll();
}

// ═══════════════════════════════════════════════════════════════
// MAINTENANCE WINDOWS sub-tab (preserved from old engine)
// ═══════════════════════════════════════════════════════════════

async function _alertingLoadMaint() {
  // Used by BOTH the Settings \u2192 Alert Profiles tab (renders into
  // #alrt-maint-list) and the new top-level Alerting page (renders into
  // #al-pg-maint). Either or both containers may be present.
  const listSettings = document.getElementById('alrt-maint-list');
  const listPage     = document.getElementById('al-pg-maint');
  if (!listSettings && !listPage) return;
  if (listSettings) listSettings.innerHTML = '<div class="alrt-loading">Loading\u2026</div>';
  if (listPage)     listPage.innerHTML     = '<div class="muted" style="padding:14px">Loading\u2026</div>';
  applyRbac();
  try {
    const d = await api('GET', '/api/alert/windows');
    _alertMaintWindows = d.windows || [];
    if (listSettings) _alertMaintRenderList(_alertMaintWindows);
    if (listPage)     _alPgRenderMaintWindows(_alertMaintWindows);
  } catch (e) {
    const msg = `Error: ${esc(String(e))}`;
    if (listSettings) listSettings.innerHTML = `<div class="alrt-err">${msg}</div>`;
    if (listPage)     listPage.innerHTML     = `<div class="error" style="padding:14px">${msg}</div>`;
  }
}

// Compact maintenance-window renderer for the Alerting page's sidebar card.
// Reuses the existing _alertMaintOpen / _alertMaintDelete handlers.
function _alPgRenderMaintWindows(windows) {
  const wrap = document.getElementById('al-pg-maint');
  if (!wrap) return;
  const cntEl = document.getElementById('al-pg-mw-count');
  if (cntEl) cntEl.textContent = windows.length;
  if (!windows.length) {
    wrap.innerHTML = `<div class="muted" style="padding:14px;text-align:center;font-size:12px">
      No maintenance windows. <a class="al-pg-link rbac-admin" onclick="_alertMaintOpen(null)">Create one</a>
      to suppress notifications during scheduled work.
    </div>`;
    applyRbac();
    return;
  }
  const now = Date.now() / 1000;
  // Show enabled+active first, then enabled, then disabled (so live windows stay on top
  // and explicitly-disabled ones sink so they don't read as ambient/forgotten).
  const sorted = windows.slice().sort((a, b) => {
    const aEn = a.enabled !== false, bEn = b.enabled !== false;
    if (aEn !== bEn) return aEn ? -1 : 1;
    const aActive = aEn && a.start_ts <= now && a.end_ts >= now;
    const bActive = bEn && b.start_ts <= now && b.end_ts >= now;
    if (aActive !== bActive) return aActive ? -1 : 1;
    return (a.start_ts || 0) - (b.start_ts || 0);
  });
  wrap.innerHTML = sorted.map(w => {
    const enabled  = w.enabled !== false;
    const active   = enabled && w.start_ts <= now && w.end_ts >= now;
    const safeName = esc(w.name).replace(/'/g, '&#39;');
    const scopeLbl = w.scope_type === 'all'    ? 'All devices'
                   : w.scope_type === 'site'   ? `Site: ${esc(w.scope_value || '')}`
                   : w.scope_type === 'group'  ? `Groups: ${esc(_mwParseGroups(w.scope_value).join(', '))}`
                   : w.scope_type === 'device' ? `Device: ${esc(_mwLookupDevName(w.scope_value))}`
                   : esc(w.scope_value || '');
    const when = w.recurring
      ? `${esc(_mwShortDays(w.recur_days))} \u00b7 ${esc(w.recur_start)}\u2013${esc(w.recur_end)}`
      : `${_alPgRelTime(w.start_ts)} \u2192 ${_alPgRelTime(w.end_ts)}`;
    return `
      <div class="al-pg-mw-row ${active?'active':''} ${enabled?'':'disabled'}" onclick="_alertMaintOpen(${w.id})" title="Edit window">
        <div class="al-pg-mw-dot ${active?'on':''}"></div>
        <div class="al-pg-mw-body">
          <div class="al-pg-mw-name">
            ${esc(w.name)}
            ${active ? '<span class="al-pg-mw-pill">In effect</span>' : ''}
            ${!enabled ? '<span class="al-pg-mw-pill al-pg-mw-pill-off">Disabled</span>' : ''}
          </div>
          <div class="al-pg-mw-meta mono">${esc(scopeLbl)} \u00b7 ${when}</div>
        </div>
        <label class="al-pg-toggle rbac-admin"
               onclick="event.stopPropagation()"
               title="${enabled ? 'Disable window' : 'Enable window'}">
          <input type="checkbox" ${enabled ? 'checked' : ''} onchange="_alPgMwToggle(${w.id})"/>
          <span class="al-pg-toggle-slider"></span>
        </label>
        <button class="iconbtn rbac-admin" onclick="event.stopPropagation();_alertMaintDelete(${w.id},'${safeName}')" title="Delete">${icon('trash',12)}</button>
      </div>`;
  }).join('');
  applyRbac();
}

// Flip a single window's enabled flag. We re-PATCH the whole window payload
// because the backend's PATCH validator requires the full schema; this keeps
// us on one endpoint without inventing a partial-update path.
async function _alPgMwToggle(id) {
  const w = _alertMaintWindows.find(x => x.id === id);
  if (!w) return;
  const next = !(w.enabled !== false);  // flip
  // Optimistic local update so the row repaints immediately
  w.enabled = next;
  _alPgRenderMaintWindows(_alertMaintWindows);
  if (document.getElementById('alrt-maint-list')) {
    _alertMaintRenderList(_alertMaintWindows);
  }
  try {
    await api('PATCH', `/api/alert/window/${id}`, {
      name:        w.name,
      scope_type:  w.scope_type,
      scope_value: w.scope_value,
      start_ts:    w.start_ts,
      end_ts:      w.end_ts,
      recurring:   !!w.recurring,
      recur_days:  w.recur_days || '',
      recur_start: w.recur_start || '',
      recur_end:   w.recur_end   || '',
      enabled:     next,
    });
    toast(`Window "${w.name}" ${next ? 'enabled' : 'disabled'}`, 'ok');
  } catch (e) {
    // Roll back on failure
    w.enabled = !next;
    _alPgRenderMaintWindows(_alertMaintWindows);
    if (document.getElementById('alrt-maint-list')) {
      _alertMaintRenderList(_alertMaintWindows);
    }
    toast(e.message || 'Toggle failed', 'err');
  }
}

function _mwLookupDevName(did) {
  const d = Object.values(S.devices || {}).find(x => String(x.device_id) === String(did));
  return d ? d.name : did;
}

function _mwShortDays(daysStr) {
  // "1,3,5" \u2192 "Mon, Wed, Fri"
  const map = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  return String(daysStr || '').split(',')
    .map(s => parseInt(s.trim()))
    .filter(n => !isNaN(n) && n >= 0 && n <= 6)
    .map(n => map[n])
    .join(', ');
}

function _alertMaintRenderList(windows) {
  const wrap = document.getElementById('alrt-maint-list');
  if (!wrap) return;
  if (!windows.length) {
    wrap.innerHTML = '<div class="alrt-empty">No maintenance windows defined.</div>';
    return;
  }
  const now = Date.now() / 1000;
  wrap.innerHTML = windows.map(w => {
    const enabled = w.enabled !== false;
    const active  = enabled && w.start_ts <= now && w.end_ts >= now;
    const start   = new Date(w.start_ts * 1000).toLocaleString();
    const end     = new Date(w.end_ts   * 1000).toLocaleString();
    const _devForScope = w.scope_type === 'device'
      ? Object.values(S.devices || {}).find(d => String(d.device_id) === String(w.scope_value))
      : null;
    const scopeLbl = w.scope_type === 'all'    ? 'All devices'
                   : w.scope_type === 'site'   ? `Site: ${esc(w.scope_value || '')}`
                   : w.scope_type === 'group'  ? `Group: ${esc(_mwParseGroups(w.scope_value).join(', '))}`
                   : `Device: ${esc(_devForScope ? _devForScope.name : w.scope_value)}`;
    const recurLbl = w.recurring
      ? `Recurring days ${esc(w.recur_days)} ${esc(w.recur_start)}–${esc(w.recur_end)}`
      : 'One-time';
    const safeName = esc(w.name).replace(/'/g, '&#39;');
    return `
      <div class="alrt-card ${active ? 'alrt-maint-active' : ''} ${enabled?'':'alrt-maint-disabled'}">
        <div class="alrt-card-left">
          <div class="alrt-card-top">
            ${active ? '<span class="alrt-dot-on" title="Currently active">●</span>' : ''}
            <span class="alrt-name">${esc(w.name)}</span>
            ${active ? '<span class="alrt-sev-badge alrt-sev-effective">In effect</span>' : ''}
            ${!enabled ? '<span class="alrt-sev-badge alrt-sev-muted">Disabled</span>' : ''}
          </div>
          <div class="alrt-card-info">
            <span class="alrt-info-pill">📅 ${start} → ${end}</span>
            <span class="alrt-info-pill">🎯 ${scopeLbl}</span>
            <span class="alrt-info-pill">🔄 ${recurLbl}</span>
          </div>
        </div>
        <div class="alrt-card-btns">
          <label class="al-pg-toggle rbac-admin" title="${enabled?'Disable':'Enable'} window" style="margin-right:8px">
            <input type="checkbox" ${enabled?'checked':''} onchange="_alPgMwToggle(${w.id})"/>
            <span class="al-pg-toggle-slider"></span>
          </label>
          <button class="btn-sm rbac-admin" onclick="_alertMaintOpen(${w.id})">Edit</button>
          <button class="btn-sm rbac-admin alrt-del-btn" onclick="_alertMaintDelete(${w.id},'${safeName}')">Delete</button>
        </div>
      </div>`;
  }).join('');
  applyRbac();
}

async function _alertMaintDelete(id, name) {
  if (!confirm(`Delete maintenance window "${name}"?`)) return;
  try { await api('DELETE', `/api/alert/window/${id}`); }
  catch (e) { toast(e.message, 'err'); return; }
  toast(`Window "${name}" deleted`, 'info');
  _alertingLoadMaint();
}

function _alertMaintOpen(id) {
  closeM('alrt-maint-modal');
  const w = id !== null ? (_alertMaintWindows.find(x => x.id === id) || null) : null;

  const now     = Math.floor(Date.now() / 1000);
  // A <input type="datetime-local"> value is interpreted as LOCAL wall-clock,
  // and save re-parses it as local (new Date(str)). So render local components
  // too — using toISOString() here (UTC) drifted the window by the browser's
  // UTC offset on every open-and-resave of a one-time window.
  const toLocal = ts => {
    if (!ts) return '';
    const d = new Date(ts * 1000), p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`
         + `T${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  const name       = w?.name        || '';
  const scopeType  = w?.scope_type  || 'all';
  const scopeVal   = w?.scope_value || '';
  const startDt    = toLocal(w?.start_ts || now);
  const endDt      = toLocal(w?.end_ts   || now + 3600);
  const recurring  = w?.recurring   || false;
  const recurDays  = w?.recur_days  || '';
  const recurStart = w?.recur_start || '00:00';
  const recurEnd   = w?.recur_end   || '06:00';
  const enabled    = w ? (w.enabled !== false) : true;

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'alrt-maint-modal';
  _overlayClose(o, () => closeM('alrt-maint-modal'));

  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,540px)">
      <div class="mhd">
        <div class="mttl">${w ? '✏ Edit Window' : '＋ New Maintenance Window'}</div>
        <button class="mclose" onclick="closeM('alrt-maint-modal')">&#x2715;</button>
      </div>
      <div class="mbdy">
        <div class="fr">
          <label class="fl">Name</label>
          <input type="text" id="mw-name" value="${esc(name)}" placeholder="e.g. Weekend Maintenance" autocomplete="off" maxlength="200"/>
        </div>
        <div class="fr">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="mw-enabled" ${enabled ? 'checked' : ''}/>
            <span class="fl" style="margin:0">Enabled <span style="color:var(--text3);font-size:10px">(disabled windows never suppress alerts)</span></span>
          </label>
        </div>
        <div class="fr">
          <label class="fl">Scope</label>
          <select id="mw-scope" onchange="_mwScopeChange()">
            <option value="all"    ${scopeType==='all'   ?'selected':''}>All devices</option>
            <option value="site"   ${scopeType==='site'  ?'selected':''}>Site</option>
            <option value="group"  ${scopeType==='group' ?'selected':''}>Device group</option>
            <option value="device" ${scopeType==='device'?'selected':''}>Specific device ID</option>
          </select>
        </div>
        <div class="fr" id="mw-scope-val-row" style="${scopeType==='all'?'display:none':''}">
          <label class="fl" id="mw-scope-val-lbl">${
            scopeType==='site'  ? 'Site' :
            scopeType==='group' ? 'Device group' : 'Device'
          }</label>
          ${_mwScopeInner(scopeType, scopeVal)}
        </div>
        <div id="mw-onetime-row" style="${recurring?'display:none':'display:flex'};gap:12px;flex-wrap:wrap">
          <div class="fr" style="flex:1;min-width:160px">
            <label class="fl">Start</label>
            <input type="datetime-local" id="mw-start" value="${startDt}"/>
          </div>
          <div class="fr" style="flex:1;min-width:160px">
            <label class="fl">End</label>
            <input type="datetime-local" id="mw-end" value="${endDt}"/>
          </div>
        </div>
        <div class="fr">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
            <input type="checkbox" id="mw-recurring" ${recurring?'checked':''} onchange="_mwRecurChange()"/>
            <span class="fl" style="margin:0">Recurring (repeat on selected days)</span>
          </label>
        </div>
        <div id="mw-recur-panel" style="${recurring?'':'display:none'}">
          <div class="fr">
            <label class="fl">Days of week <span style="color:var(--text3);font-size:10px">(1=Mon … 7=Sun)</span></label>
            <div class="alrt-day-picker">
              ${[1,2,3,4,5,6,7].map(d => {
                const active = recurDays.split(',').map(x=>x.trim()).includes(String(d));
                const lbl = ['Mo','Tu','We','Th','Fr','Sa','Su'][d-1];
                return `<button type="button" class="alrt-day-btn ${active?'active':''}"
                  data-day="${d}" onclick="_mwDayToggle(this)">${lbl}</button>`;
              }).join('')}
            </div>
          </div>
          <div style="display:flex;gap:12px;flex-wrap:wrap">
            <div class="fr" style="flex:1;min-width:120px">
              <label class="fl">Start time</label>
              <input type="time" id="mw-recur-start" value="${recurStart}"/>
            </div>
            <div class="fr" style="flex:1;min-width:120px">
              <label class="fl">End time</label>
              <input type="time" id="mw-recur-end" value="${recurEnd}"/>
            </div>
          </div>
        </div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('alrt-maint-modal')">Cancel</button>
        <button class="btn-p" id="mw-save-btn" onclick="_alertMaintSave(${id ?? 'null'})">Save Window</button>
      </div>
    </div>`;

  document.body.appendChild(o);
  setTimeout(() => document.getElementById('mw-name')?.focus(), 60);
  // If the window is already site-scoped, populate the datalist now so the
  // existing value autocompletes correctly.
  if (scopeType === 'site' && typeof _populateSiteDatalist === 'function') {
    setTimeout(() => _populateSiteDatalist('mw-site-dl'), 80);
  }
}

// Parse scope_value for group windows — supports both JSON array (new) and plain string (legacy).
function _mwParseGroups(val) {
  if (!val) return [];
  try { const a = JSON.parse(val); if (Array.isArray(a)) return a; } catch (_) {}
  return [val];
}

function _mwScopeInner(scopeType, curVal) {
  if (scopeType === 'device') {
    const opts = Object.values(S.devices || {})
      .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
      .map(d => `<option value="${esc(d.device_id)}" ${String(d.device_id) === String(curVal) ? 'selected' : ''}>${esc(d.name)} — ${esc(d.host)}</option>`)
      .join('');
    return `<select id="mw-scope-val" style="width:100%">${opts || '<option value="">No devices</option>'}</select>`;
  }
  if (scopeType === 'group') {
    const groups  = [...new Set(Object.values(S.devices || {}).map(d => d.group).filter(Boolean))].sort();
    const selSet  = new Set(_mwParseGroups(curVal));
    const opts    = groups.map(g => `<option value="${esc(g)}" ${selSet.has(g) ? 'selected' : ''}>${esc(g)}</option>`).join('');
    return `<select id="mw-scope-val" multiple size="${Math.min(groups.length, 6)}" style="width:100%">${opts || '<option value="">No groups</option>'}</select>`;
  }
  if (scopeType === 'site') {
    // Site picker — datalist autocomplete from /api/sites, populated async.
    return `<input type="text" id="mw-scope-val" value="${esc(curVal)}" list="mw-site-dl" placeholder="HQ, DR-Site-2…" autocomplete="off"/>
            <datalist id="mw-site-dl"></datalist>`;
  }
  return `<input type="text" id="mw-scope-val" value="${esc(curVal)}" autocomplete="off"/>`;
}

function _mwScopeChange() {
  const sel = document.getElementById('mw-scope')?.value;
  const row = document.getElementById('mw-scope-val-row');
  const lbl = document.getElementById('mw-scope-val-lbl');
  if (!row) return;
  row.style.display = sel === 'all' ? 'none' : '';
  if (lbl) lbl.textContent =
    sel === 'site'  ? 'Site' :
    sel === 'group' ? 'Device group' : 'Device';
  // Replace the control with the appropriate dropdown for the new scope type
  const existing = document.getElementById('mw-scope-val');
  if (existing) existing.outerHTML = _mwScopeInner(sel, '');
  if (sel === 'site' && typeof _populateSiteDatalist === 'function') {
    _populateSiteDatalist('mw-site-dl');
  }
}

function _mwRecurChange() {
  const checked    = document.getElementById('mw-recurring')?.checked;
  const panel      = document.getElementById('mw-recur-panel');
  const onetimeRow = document.getElementById('mw-onetime-row');
  console.log('[mw] _mwRecurChange checked=', checked,
              'panel=', !!panel, 'onetimeRow=', !!onetimeRow);
  if (!onetimeRow) console.error('[mw] mw-onetime-row not found in DOM');
  if (panel)      panel.style.display      = checked ? '' : 'none';
  if (onetimeRow) onetimeRow.style.display = checked ? 'none' : 'flex';
}

function _mwDayToggle(btn) {
  btn.classList.toggle('active');
}

async function _alertMaintSave(id) {
  const name      = (document.getElementById('mw-name')?.value || '').trim();
  const scopeType = document.getElementById('mw-scope')?.value || 'all';
  let   scopeVal  = (document.getElementById('mw-scope-val')?.value || '').trim();
  const startRaw  = document.getElementById('mw-start')?.value;
  const endRaw    = document.getElementById('mw-end')?.value;
  const recurring = document.getElementById('mw-recurring')?.checked || false;
  const recurStart = document.getElementById('mw-recur-start')?.value || '';
  const recurEnd   = document.getElementById('mw-recur-end')?.value   || '';

  const activeDays = [...document.querySelectorAll('.alrt-day-btn.active')]
    .map(b => b.dataset.day).join(',');

  // For recurring windows the datetime pickers are hidden — auto-set a permanent range
  const nowSec = Math.floor(Date.now() / 1000);
  const startTs = recurring ? nowSec
                : (startRaw ? Math.floor(new Date(startRaw).getTime() / 1000) : 0);
  const endTs   = recurring ? nowSec + 10 * 365 * 24 * 3600          // 10 years
                : (endRaw   ? Math.floor(new Date(endRaw).getTime()   / 1000) : 0);

  // For group scope, collect all selected options and store as JSON array
  if (scopeType === 'group') {
    const sel = document.getElementById('mw-scope-val');
    scopeVal = sel ? JSON.stringify([...sel.selectedOptions].map(o => o.value)) : '[]';
  }
  const enabled = document.getElementById('mw-enabled')?.checked !== false;
  const payload = {
    name, scope_type: scopeType, scope_value: scopeVal,
    start_ts: startTs, end_ts: endTs,
    recurring, recur_days: activeDays,
    recur_start: recurStart, recur_end: recurEnd,
    enabled,
  };

  const btn = document.getElementById('mw-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving\u2026'; }

  const isNew  = id === null;
  const method = isNew ? 'POST'  : 'PATCH';
  const path   = isNew ? '/api/alert/windows' : `/api/alert/window/${id}`;
  console.log('[mw] save method=', method, 'path=', path, 'payload=', payload);
  try {
    await api(method, path, payload);
    toast(isNew ? `Window "${name}" created` : `Window "${name}" updated`, 'ok');
    closeM('alrt-maint-modal');
    _alertingLoadMaint();
  } catch (e) {
    toast(e.message || 'Save failed', 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Window'; }
  }
}

// Backwards-compat shim — old call sites may still invoke _alertingLoadRules.
// Map it to the new profiles loader so nothing breaks.
function _alertingLoadRules() { _alertingLoadProfiles(); }


// ═══════════════════════════════════════════════════════════════
// ALERTING TOP-LEVEL PAGE (#alertingView) — v1.0 redesign
// Shell + profiles + recent deliveries + channels card + escalation card
// Reuses existing CRUD endpoints under /api/alert/* so no backend changes.
// ═══════════════════════════════════════════════════════════════

let _alPgFilter = '';

async function _alertingPageInit() {
  const root = document.getElementById('alertingView');
  if (!root) return;
  root.innerHTML = `
    <div class="pagehead">
      <div class="pagehead-l">
        <h1>Alerting</h1>
        <div class="sub" id="al-pg-sub">Profiles, channels, escalation, and delivery history.</div>
      </div>
      <div class="pagehead-r">
        <button class="btn" id="al-pg-test-btn" onclick="_alPgTestAlert()">${icon('play',12)} Test Alert</button>
        <button class="btn primary rbac-admin" onclick="openProfileEditor(null)">${icon('plus',12)} New Profile</button>
      </div>
    </div>
    <div class="al-pg-layout">
      <div class="al-pg-main">
        <section class="al-pg-section">
          <div class="al-pg-sec-head">
            <div class="al-pg-sec-title">Alert Profiles <span class="al-pg-badge" id="al-pg-pf-count">0</span></div>
            <div class="search" style="max-width:260px">
              ${icon('search',13)}
              <input class="pw-input" id="al-pg-pf-filter" type="search" placeholder="Filter…" oninput="_alPgFilterChange(this.value)"/>
            </div>
          </div>
          <div id="al-pg-profiles"><div class="muted" style="padding:18px 16px">Loading…</div></div>
        </section>
        <section class="al-pg-section">
          <div class="al-pg-sec-head">
            <div class="al-pg-sec-title">Recent Deliveries <span class="muted small">last 24h</span></div>
            <button class="iconbtn" onclick="_alPgLoadDeliveries()" title="Refresh">${icon('refresh',12)}</button>
          </div>
          <div id="al-pg-deliveries"><div class="muted" style="padding:18px 16px">Loading…</div></div>
        </section>
      </div>
      <aside class="al-pg-side">
        <section class="al-pg-card">
          <div class="al-pg-card-head">
            <span>Channels <span class="al-pg-badge" id="al-pg-ch-count">0</span></span>
            <button class="iconbtn rbac-admin" onclick="openTemplateEditor(null)" title="New action template">${icon('plus',12)}</button>
          </div>
          <div id="al-pg-channels"><div class="muted" style="padding:14px">Loading…</div></div>
        </section>
        <section class="al-pg-card">
          <div class="al-pg-card-head">Escalation Policy <span class="muted small" id="al-pg-esc-sub"></span></div>
          <div id="al-pg-escalation"><div class="muted" style="padding:14px">Loading…</div></div>
        </section>
        <section class="al-pg-card">
          <div class="al-pg-card-head">
            <span>Maintenance Windows <span class="al-pg-badge" id="al-pg-mw-count">0</span></span>
            <button class="iconbtn rbac-admin" onclick="_alertMaintOpen(null)" title="New maintenance window">${icon('plus',12)}</button>
          </div>
          <div id="al-pg-maint"><div class="muted" style="padding:14px">Loading…</div></div>
        </section>
        <section class="al-pg-card">
          <div class="al-pg-card-head">
            <span>Notification Batching</span>
            <span class="muted small" id="al-pg-batch-status">—</span>
          </div>
          <div id="al-pg-batch"><div class="muted" style="padding:14px">Loading…</div></div>
        </section>
      </aside>
    </div>`;
  applyRbac();
  await Promise.all([
    _alPgLoadProfilesAndChannels(),
    _alPgLoadDeliveries(),
    _alertingLoadMaint(),
    _alPgLoadBatching(),
  ]);
}

// ── Notification Batching card ──────────────────────────────────
// Reads + writes the three alert_batch_* fields on /api/settings.
// Previously lived in Settings → Alert Profiles; moved here so the entire
// alert domain (profiles, channels, escalation, maintenance, batching) is
// configurable from one place.
async function _alPgLoadBatching() {
  const wrap = document.getElementById('al-pg-batch');
  if (!wrap) return;
  try {
    const s = await api('GET', '/api/settings');
    _alPgRenderBatching(s || {});
  } catch (e) {
    wrap.innerHTML = `<div class="error" style="padding:14px">Failed to load batching: ${esc(String(e))}</div>`;
  }
}

function _alPgRenderBatching(sr) {
  const wrap = document.getElementById('al-pg-batch');
  if (!wrap) return;
  const enabled = sr.alert_batch_enabled !== false;  // default on
  const win     = sr.alert_batch_window_s || 60;
  const max     = sr.alert_batch_max_size || 20;
  const status  = document.getElementById('al-pg-batch-status');
  if (status) status.textContent = enabled ? `on · ${win}s / ${max}` : 'off';
  wrap.innerHTML = `
    <div class="al-pg-batch-body">
      <div class="al-pg-batch-toggle-row">
        <label class="al-pg-toggle rbac-admin" title="${enabled?'Disable':'Enable'} batching">
          <input type="checkbox" id="al-pg-batch-en" ${enabled?'checked':''}/>
          <span class="al-pg-toggle-slider"></span>
        </label>
        <div class="al-pg-batch-lbl">
          <div>Enable batching</div>
          <div class="muted small">When off, alerts fire immediately as separate emails/webhooks.</div>
        </div>
      </div>
      <div class="al-pg-batch-field">
        <div class="al-pg-batch-field-head">
          <span>Batch window <span class="muted small">5–3600s</span></span>
          <input type="number" id="al-pg-batch-win" min="5" max="3600" value="${win}" class="al-pg-batch-num"/>
        </div>
        <div class="muted small">Hold the first alert this long before flushing.</div>
      </div>
      <div class="al-pg-batch-field">
        <div class="al-pg-batch-field-head">
          <span>Max batch size <span class="muted small">2–500</span></span>
          <input type="number" id="al-pg-batch-max" min="2" max="500" value="${max}" class="al-pg-batch-num"/>
        </div>
        <div class="muted small">Flush early when this many events accumulate.</div>
      </div>
      <div class="al-pg-batch-foot">
        <span class="muted small">Webhook batching is opt-in per template.</span>
        <button class="btn-sm primary rbac-admin" id="al-pg-batch-save" onclick="_alPgSaveBatching()">Save</button>
      </div>
    </div>`;
  applyRbac();
}

async function _alPgSaveBatching() {
  const enabled = document.getElementById('al-pg-batch-en')?.checked ? 1 : 0;
  const win     = parseInt(document.getElementById('al-pg-batch-win')?.value);
  const max     = parseInt(document.getElementById('al-pg-batch-max')?.value);
  if (!Number.isFinite(win) || win < 5 || win > 3600) {
    toast('Batch window must be 5–3600 seconds', 'err'); return;
  }
  if (!Number.isFinite(max) || max < 2 || max > 500) {
    toast('Max batch size must be 2–500', 'err'); return;
  }
  const btn = document.getElementById('al-pg-batch-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  try {
    const r = await api('PATCH', '/api/settings', {
      alert_batch_enabled:  enabled,
      alert_batch_window_s: win,
      alert_batch_max_size: max,
    });
    if (!r || !r.ok) { toast('Failed to save batching', 'err'); return; }
    toast('Notification batching saved', 'ok');
    // Refresh the status pill in the card head
    const status = document.getElementById('al-pg-batch-status');
    if (status) status.textContent = enabled ? `on · ${win}s / ${max}` : 'off';
  } catch (e) {
    toast(e.message || 'Failed to save batching', 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
}

async function _alPgLoadProfilesAndChannels() {
  try {
    const [pr, tr] = await Promise.all([
      api('GET', '/api/alert/profiles'),
      api('GET', '/api/alert/action-templates'),
    ]);
    _alertProfiles  = pr.profiles  || [];
    _alertTemplates = tr.templates || [];
    _alPgRenderProfiles();
    _alPgRenderChannels();
    _alPgRenderEscalation();
    _alPgRefreshSubtitle();
  } catch (e) {
    const pf = document.getElementById('al-pg-profiles');
    if (pf) pf.innerHTML = `<div class="error" style="padding:16px">Failed to load profiles: ${esc(String(e))}</div>`;
  }
}

function _alPgFilterChange(v) {
  _alPgFilter = (v || '').trim().toLowerCase();
  _alPgRenderProfiles();
}

function _alPgRefreshSubtitle() {
  const sub = document.getElementById('al-pg-sub');
  if (!sub) return;
  const total  = _alertProfiles.length;
  const active = _alertProfiles.filter(p => p.enabled).length;
  const chans  = _alertTemplates.length;
  sub.textContent = `${active} of ${total} profile${total===1?'':'s'} active · ${chans} channel${chans===1?'':'s'} configured`;
}

// ── Profile rows ────────────────────────────────────────────────────
function _alPgRenderProfiles() {
  const wrap = document.getElementById('al-pg-profiles');
  if (!wrap) return;

  const tplById = {};
  _alertTemplates.forEach(t => { tplById[t.id] = t; });

  let arr = _alertProfiles;
  if (_alPgFilter) {
    arr = arr.filter(p =>
      (p.name||'').toLowerCase().includes(_alPgFilter) ||
      (p.scope_type||'').toLowerCase().includes(_alPgFilter) ||
      (p.scope_value||'').toLowerCase().includes(_alPgFilter));
  }
  const cntEl = document.getElementById('al-pg-pf-count');
  if (cntEl) cntEl.textContent = _alertProfiles.length;

  if (!arr.length) {
    wrap.innerHTML = `<div class="muted" style="padding:18px 16px;text-align:center">
      ${_alPgFilter ? 'No profiles match the filter.' : 'No alert profiles yet — click + New Profile.'}
    </div>`;
    return;
  }

  // Stable order: global → site → group → device → sensor, then by name
  const rank = {global: 0, site: 1, group: 2, device: 3, sensor: 4};
  const sorted = arr.slice().sort((a, b) =>
    (rank[a.scope_type] - rank[b.scope_type]) || a.name.localeCompare(b.name));

  wrap.innerHTML = sorted.map(p => _alPgProfileRow(p, tplById)).join('');
  applyRbac();
}

function _alPgProfileRow(p, tplById) {
  // Build a condition summary from triggers + scope
  const triggers = [...new Set((p.stages||[]).map(s => s.trigger_state))];
  const trigStr  = triggers.length
    ? triggers.map(t => ({down:'down', warning:'warning', down_recovered:'recovered', warning_recovered:'warn-recovered'})[t] || t).join(' ∨ ')
    : '—';
  const scopeStr = p.scope_type === 'global'
    ? 'Global'
    : `${p.scope_type[0].toUpperCase()}${p.scope_type.slice(1)}: ${esc(p.scope_value || '')}`;

  // Unique channel types across all stages
  const chanTypes = new Set();
  (p.stages || []).forEach(s => {
    (s.action_ids || []).forEach(aid => {
      const t = tplById[aid];
      if (t) chanTypes.add(t.atype);
    });
  });
  const chanPills = [...chanTypes].map(at =>
    `<span class="al-pg-chan-pill" data-at="${esc(at)}">${esc(at)}</span>`).join('');

  const safeName = esc(p.name).replace(/'/g, '&#39;');
  return `
    <div class="al-pg-pf-row" data-pid="${p.id}">
      <label class="al-pg-toggle rbac-op" title="${p.enabled?'Disable':'Enable'} profile">
        <input type="checkbox" ${p.enabled?'checked':''} onchange="_alPgProfToggle(${p.id})"/>
        <span class="al-pg-toggle-slider"></span>
      </label>
      <div class="al-pg-pf-body">
        <div class="al-pg-pf-name">
          ${esc(p.name)}
          ${p.exclusive ? '<span class="al-pg-excl-pill" title="Exclusive — suppresses parent-scope profiles when matched">Exclusive</span>' : ''}
        </div>
        <div class="al-pg-pf-cond mono">
          <span class="al-pg-cond-trig">${esc(trigStr)}</span>
          <span class="al-pg-cond-sep">·</span>
          <span class="al-pg-cond-scope">${scopeStr}</span>
          <span class="al-pg-cond-sep">·</span>
          <span class="al-pg-cond-stages">${(p.stages||[]).length} stage${(p.stages||[]).length===1?'':'s'}</span>
        </div>
      </div>
      <div class="al-pg-pf-chans">${chanPills}</div>
      <div class="al-pg-pf-acts">
        <button class="iconbtn rbac-admin" onclick="openProfileEditor(${p.id})" title="Edit">${icon('edit',12)}</button>
        <button class="iconbtn rbac-admin" onclick="_alertingProfTest(${p.id},'${safeName}')" title="Test fire">${icon('play',12)}</button>
        <button class="iconbtn rbac-admin" onclick="_alertingProfDelete(${p.id},'${safeName}')" title="Delete">${icon('trash',12)}</button>
      </div>
    </div>`;
}

async function _alPgProfToggle(pid) {
  try {
    await api('POST', `/api/alert/profile/${pid}/toggle`);
    const p = _alertProfiles.find(x => x.id === pid);
    if (p) p.enabled = !p.enabled;
    _alPgRenderProfiles();
    _alPgRefreshSubtitle();
  } catch (e) {
    toast('Toggle failed: ' + (e.message || e), 'err');
    _alPgRenderProfiles();   // revert visual state on error
  }
}

// ── Channels card ───────────────────────────────────────────────────
function _alPgRenderChannels() {
  const wrap = document.getElementById('al-pg-channels');
  if (!wrap) return;
  const cntEl = document.getElementById('al-pg-ch-count');
  if (cntEl) cntEl.textContent = _alertTemplates.length;
  if (!_alertTemplates.length) {
    wrap.innerHTML = `<div class="muted" style="padding:14px;text-align:center;font-size:12px">
      No action templates yet — add one to start receiving alerts.
    </div>`;
    return;
  }
  wrap.innerHTML = _alertTemplates.map(t => {
    const ic = ({email:'mail', webhook:'ipam', syslog:'logs', browser:'bell'})[t.atype] || 'bell';
    const detail = _alPgChannelDetail(t);
    return `
      <div class="al-pg-chan-row" onclick="openTemplateEditor(${t.id})">
        <div class="al-pg-chan-ico">${icon(ic,14)}</div>
        <div class="al-pg-chan-body">
          <div class="al-pg-chan-name">${esc(t.name)}</div>
          <div class="al-pg-chan-detail mono">${esc(detail)}</div>
        </div>
        <div class="al-pg-chan-type">${esc(t.atype)}</div>
      </div>`;
  }).join('');
  applyRbac();
}

function _alPgChannelDetail(t) {
  // Mirror the existing _tplSummary() logic — earlier draft only looked at
  // c.to / c.recipients which the template editor never writes (it stores
  // to_users / to_groups / to_emails), so email rows always showed "—".
  // Syslog config stores .host only if non-empty (see save handler L693),
  // so empty host means "use the system's default syslog target".
  const c = t.config || {};
  if (t.atype === 'email') {
    const parts = [];
    if (c.to_users)  parts.push((Array.isArray(c.to_users)  ? c.to_users  : [c.to_users]).join(', '));
    if (c.to_groups) parts.push((Array.isArray(c.to_groups) ? c.to_groups : [c.to_groups]).map(g => `group:${g}`).join(', '));
    if (c.to_emails || c.to) parts.push(c.to_emails || c.to);
    return parts.join(' · ') || '(no recipients)';
  }
  if (t.atype === 'webhook') return c.url || '—';
  if (t.atype === 'syslog')  return `${c.host || '(default host)'}:${c.port || 514}${c.proto ? ' / ' + c.proto : ''}`;
  if (t.atype === 'browser') return 'In-app notifications';
  return '';
}

// ── Escalation Policy card ──────────────────────────────────────────
function _alPgRenderEscalation() {
  const wrap = document.getElementById('al-pg-escalation');
  if (!wrap) return;
  // Pick the default policy: a global profile (preferred), else the first profile
  const def = _alertProfiles.find(p => p.scope_type === 'global') || _alertProfiles[0];
  const sub = document.getElementById('al-pg-esc-sub');
  if (!def) {
    if (sub) sub.textContent = '';
    wrap.innerHTML = `<div class="muted" style="padding:14px;text-align:center;font-size:12px">
      No profile to derive policy from.
    </div>`;
    return;
  }
  if (sub) sub.textContent = esc(def.name);

  const tplById = {};
  _alertTemplates.forEach(t => { tplById[t.id] = t; });

  const stages = (def.stages || []).slice().sort((a,b) => a.sort_order - b.sort_order);
  if (!stages.length) {
    wrap.innerHTML = `<div class="muted" style="padding:14px;text-align:center;font-size:12px">
      Profile has no stages configured.
    </div>`;
    return;
  }
  wrap.innerHTML = stages.map((s, i) => {
    const delay = s.delay_s
      ? (s.delay_s < 60 ? `+${s.delay_s}s` : `+${Math.round(s.delay_s/60)} min`)
      : 'immediate';
    const channels = (s.action_ids || []).map(aid => tplById[aid]).filter(Boolean);
    const chanStr  = channels.map(t => t.name).join(' + ') || '—';
    const trigLbl  = ({down:'Down', warning:'Warning', down_recovered:'Recovered', warning_recovered:'Warn recovered'})[s.trigger_state] || s.trigger_state;
    return `
      <div class="al-pg-esc-row">
        <div class="al-pg-esc-num">${i+1}</div>
        <div class="al-pg-esc-body">
          <div class="al-pg-esc-name">${esc(chanStr)} <span class="muted small">· ${esc(trigLbl)}</span></div>
          <div class="al-pg-esc-when">After ${esc(delay)}</div>
        </div>
      </div>`;
  }).join('');
}

// ── Recent Deliveries ───────────────────────────────────────────────
async function _alPgLoadDeliveries() {
  const wrap = document.getElementById('al-pg-deliveries');
  if (!wrap) return;
  try {
    const r = await api('GET', '/api/alert/events?state=all&limit=50');
    const events = r.events || [];
    if (!events.length) {
      wrap.innerHTML = `<div class="muted" style="padding:18px 16px;text-align:center">
        No deliveries in the recent window.
      </div>`;
      return;
    }
    // Last 24h cutoff (events are time-ordered desc)
    const cutoff = Date.now()/1000 - 86400;
    const recent = events.filter(e => (e.started_at || e.created_at || 0) >= cutoff);
    const rows = (recent.length ? recent : events.slice(0, 12)).map(_alPgDeliveryRow).join('');
    wrap.innerHTML = `
      <div class="al-pg-tbl-wrap">
        <table class="al-pg-tbl">
          <thead><tr>
            <th>Time</th><th>Profile</th><th>Device</th><th>Severity</th><th>State</th><th>Notes</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } catch (e) {
    wrap.innerHTML = `<div class="error" style="padding:16px">Failed to load deliveries: ${esc(String(e))}</div>`;
  }
}

function _alPgDeliveryRow(e) {
  // Field names match the actual /api/alert/events payload (see _AE_COLS in
  // db/alert_events.py): triggered_at + dname/did. Earlier draft used
  // started_at/device_name which never existed → TIME and DEVICE rendered
  // as "—" for every row.
  const ts = e.triggered_at || 0;
  const when = ts ? _alPgRelTime(ts) : '—';
  const sev = (e.severity || e.trigger_state || 'info').toLowerCase();
  const sevCls = sev.includes('down') || sev === 'critical' ? 'crit'
              : sev.includes('warn') ? 'warn'
              : sev.includes('recover') ? 'ok'
              : 'info';
  const sevLbl = ({down:'Down', warning:'Warning', down_recovered:'Recovered', warning_recovered:'Warn recovered', critical:'Critical', info:'Info'})[sev] || sev;
  const state = (e.state || '').toLowerCase();
  const stateCls = state === 'active' ? 'crit' : state === 'ack' ? 'warn' : 'ok';
  const notes = _alPgDeliveryNotes(e, state);
  // dname is the snapshot at fire time; if a device was renamed since, did is
  // the fallback so the column stays useful for forensic lookups.
  const dev = e.dname || e.did || '—';
  return `
    <tr>
      <td class="muted small mono">${esc(when)}</td>
      <td>${esc(e.profile_name || '—')}</td>
      <td>${esc(dev)}</td>
      <td><span class="al-pg-sev ${sevCls}">${esc(sevLbl)}</span></td>
      <td><span class="al-pg-state ${stateCls}">${esc(state || '—')}</span></td>
      <td class="al-pg-notes" title="${esc(notes.tooltip)}">${notes.html}</td>
    </tr>`;
}

// Pick a useful one-line note per row. Today only "suppressed" has a structured
// reason (always maintenance windows — see monitoring/alert_profile_engine.py),
// but we synthesize light notes for ack/resolved too so the column stays useful.
function _alPgDeliveryNotes(e, state) {
  if (state === 'suppressed') {
    const r = (e.suppress_reason || '').trim();
    if (r) {
      return { html: `<span class="al-pg-note al-pg-note-mute">${esc(r)}</span>`, tooltip: r };
    }
    return { html: '<span class="al-pg-note al-pg-note-mute">Maintenance window</span>',
             tooltip: 'Suppressed by an active maintenance window' };
  }
  if (state === 'acknowledged' || state === 'ack') {
    const who = (e.ack_by || '').trim();
    const txt = who ? `Ack by ${who}` : 'Acknowledged';
    return { html: `<span class="al-pg-note">${esc(txt)}</span>`, tooltip: txt };
  }
  if (state === 'resolved' && e.resolved_at && e.triggered_at) {
    const dur = Math.max(0, e.resolved_at - e.triggered_at);
    const lbl = dur < 60   ? `${Math.round(dur)}s`
              : dur < 3600 ? `${Math.round(dur/60)}m`
              :              `${Math.round(dur/3600)}h`;
    const txt = `Recovered in ${lbl}`;
    return { html: `<span class="al-pg-note">${esc(txt)}</span>`, tooltip: txt };
  }
  if ((e.repeat_count || 1) > 1) {
    const txt = `${e.repeat_count}× repeats`;
    return { html: `<span class="al-pg-note">${esc(txt)}</span>`, tooltip: txt };
  }
  return { html: '<span class="muted">—</span>', tooltip: '' };
}

function _alPgRelTime(epochSec) {
  const diff = Date.now()/1000 - epochSec;
  if (diff < 60)    return `${Math.round(diff)}s ago`;
  if (diff < 3600)  return `${Math.round(diff/60)}m ago`;
  if (diff < 86400) return `${Math.round(diff/3600)}h ago`;
  return `${Math.round(diff/86400)}d ago`;
}

// ── Test Alert button — picks the first enabled profile to fire ─────
function _alPgTestAlert() {
  const target = _alertProfiles.find(p => p.enabled) || _alertProfiles[0];
  if (!target) { toast('No profile to test — create one first', 'warn'); return; }
  _alertingProfTest(target.id, target.name);
}
