// ── Alerting (PRTG-style profiles + action templates + events + maint) ──

let _alertProfiles      = [];
let _alertTemplates     = [];
let _alertEditingProfId = null;   // null = new profile
let _alertEditingTplId  = null;   // null = new template
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
  const list = document.getElementById('alrt-list');
  if (!list) return;
  list.innerHTML = '<div class="alrt-loading">Loading\u2026</div>';
  applyRbac();
  try {
    const [pr, tr] = await Promise.all([
      api('GET', '/api/alert/profiles'),
      api('GET', '/api/alert/action-templates'),
    ]);
    _alertProfiles  = pr.profiles  || [];
    _alertTemplates = tr.templates || [];
    _alertingRenderProfiles();
    _alertingRenderTemplates();
  } catch (e) {
    list.innerHTML = `<div class="alrt-err">Failed to load profiles: ${esc(String(e))}</div>`;
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

async function openProfileEditor(id, scopeDefaults = null) {
  closeM('alrt-prof-modal');
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

  const tplOpts = id => {
    const opts = _alertTemplates.map(t =>
      `<option value="${t.id}" ${id === t.id ? 'selected' : ''}>${esc(t.name)} (${t.atype})</option>`
    ).join('');
    return `<option value="">— pick template —</option>${opts}`;
  };

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'alrt-prof-modal';
  _overlayClose(o, () => closeM('alrt-prof-modal'));

  o.innerHTML = `
    <div class="mbox alrt-editor-box">
      <div class="mhd">
        <div class="mttl">${prof ? '✏ Edit Alert Profile' : '＋ New Alert Profile'}</div>
        <button class="mclose" onclick="closeM('alrt-prof-modal')">&#x2715;</button>
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
              <option value="group"  ${scopeType === 'group'  ? 'selected' : ''}>Group</option>
              <option value="device" ${scopeType === 'device' ? 'selected' : ''}>Device</option>
              <option value="sensor" ${scopeType === 'sensor' ? 'selected' : ''}>Sensor</option>
            </select>
          </div>
          <div class="fr" id="ap-scope-val-row" style="${scopeType === 'global' ? 'display:none' : ''}">
            <label class="fl" id="ap-scope-val-lbl">${
              scopeType === 'group' ? 'Group name' :
              scopeType === 'device' ? 'Device ID' : 'did/sid'
            }</label>
            <input type="text" id="ap-scope-val" value="${esc(scopeValue)}" autocomplete="off"/>
          </div>
          <div class="fr" style="align-self:flex-end;padding-bottom:14px">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="ap-enabled" ${enabled ? 'checked' : ''}/>
              <span style="font-size:12px;color:var(--text2)">Enabled</span>
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
                <th>Action template</th>
              </tr>
            </thead>
            <tbody>
              ${_AP_TRIG_ORDER.map((trig, i) => {
                const s = stagesByPos[i];
                const isRecovery = trig.endsWith('_recovered');
                return `
                  <tr class="alrt-stage-row alrt-stage-${trig}" data-trig="${trig}" data-pos="${i}">
                    <td class="alrt-trig-cell">${_AP_TRIG_LABELS[trig]}</td>
                    <td>${isRecovery
                        ? '<span style="color:var(--text3);font-size:11px">—</span>'
                        : `<input type="number" class="ap-delay" value="${s?.delay_s ?? _AP_DEFAULT_DELAYS[i]}" min="0" step="10"/>`}</td>
                    <td>${isRecovery
                        ? '<span style="color:var(--text3);font-size:11px">—</span>'
                        : `<input type="number" class="ap-repeat" value="${s?.repeat_min ?? 0}" min="0" step="5"/>`}</td>
                    <td><select class="ap-action">${tplOpts(s?.action_id || 0)}</select></td>
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
        <button class="btn-s" onclick="closeM('alrt-prof-modal')">Cancel</button>
        <button class="btn-p" id="ap-save-btn" onclick="_alertingSaveProfile()">Save Profile</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  setTimeout(() => document.getElementById('ap-name')?.focus(), 60);
}

function _apScopeChange() {
  const sel  = document.getElementById('ap-scope-type')?.value;
  const row  = document.getElementById('ap-scope-val-row');
  const lbl  = document.getElementById('ap-scope-val-lbl');
  if (!row) return;
  row.style.display = sel === 'global' ? 'none' : '';
  if (lbl) lbl.textContent =
    sel === 'group'  ? 'Group name' :
    sel === 'device' ? 'Device ID'  : 'did/sid';
}

async function _alertingSaveProfile() {
  const name       = (document.getElementById('ap-name')?.value || '').trim();
  const enabled    = !!document.getElementById('ap-enabled')?.checked;
  const scopeType  = document.getElementById('ap-scope-type')?.value || 'global';
  const scopeValue = (document.getElementById('ap-scope-val')?.value || '').trim();

  if (!name) { toast('Name is required', 'err'); return; }
  if (scopeType !== 'global' && !scopeValue) {
    toast('Scope value is required for non-global scopes', 'err'); return;
  }

  // Collect stages from the table rows
  const stages = [];
  document.querySelectorAll('#alrt-prof-modal .alrt-stage-row').forEach(row => {
    const trig    = row.dataset.trig;
    const actSel  = row.querySelector('.ap-action');
    const action_id = parseInt(actSel?.value || '0');
    if (!action_id) return;   // empty stage row
    const isRecovery = trig.endsWith('_recovered');
    stages.push({
      trigger_state: trig,
      delay_s:    isRecovery ? 0 : parseInt(row.querySelector('.ap-delay')?.value  || '0'),
      repeat_min: isRecovery ? 0 : parseInt(row.querySelector('.ap-repeat')?.value || '0'),
      action_id,
    });
  });

  const payload = {
    name, enabled,
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
    closeM('alrt-prof-modal');
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
}

function _atTypeChange() {
  const t = document.getElementById('at-type')?.value || 'email';
  const pane = document.getElementById('at-cfg-pane');
  if (pane) pane.innerHTML = _atCfgHtml(t, {});
}

function _atCfgHtml(atype, cfg) {
  if (atype === 'email') {
    const users  = Array.isArray(cfg.to_users)  ? cfg.to_users.join(',')  : (cfg.to_users  || '');
    const groups = Array.isArray(cfg.to_groups) ? cfg.to_groups.join(',') : (cfg.to_groups || '');
    const emails = cfg.to_emails || cfg.to || '';
    return `
      <div class="fr"><label class="fl">Usernames (comma-separated)</label>
        <input type="text" id="at-users" value="${esc(users)}" placeholder="admin, oncall"/></div>
      <div class="fr"><label class="fl">Group ids (comma-separated)</label>
        <input type="text" id="at-groups" value="${esc(groups)}" placeholder="1,2"/></div>
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
        <textarea id="at-body" rows="4" placeholder='{"text":"{dname}/{sname} {event_type}"}'>${esc(cfg.body || '')}</textarea></div>`;
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
    const users  = (document.getElementById('at-users')?.value  || '').trim();
    const groups = (document.getElementById('at-groups')?.value || '').trim();
    const emails = (document.getElementById('at-emails')?.value || '').trim();
    if (users)  cfg.to_users  = users.split(',').map(s => s.trim()).filter(Boolean);
    if (groups) cfg.to_groups = groups.split(',').map(s => parseInt(s.trim())).filter(n => n > 0);
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
    if (typeof _alertEvtBadgeCount !== 'undefined') {
      _alertEvtBadgeCount = d.active_count || 0;
      if (typeof _updateEvtBadge === 'function') _updateEvtBadge();
    }
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
  const btns = e.state === 'active' ? `
    <button class="btn-sm rbac-op" onclick="_alertAck(${e.id})">ACK</button>
    <button class="btn-sm rbac-op" onclick="_alertResolve(${e.id})">Resolve</button>` : '';
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
      <div class="alrt-card-btns">${btns}</div>
    </div>`;
}

async function _alertAck(id) {
  try { await api('POST', `/api/alert/event/${id}/ack`); }
  catch (e) { toast(e.message, 'err'); return; }
  toast('Alert acknowledged', 'ok');
  _alertingLoadEvents(_alertEvtFilter, true);
}

async function _alertResolve(id) {
  try { await api('POST', `/api/alert/event/${id}/resolve`); }
  catch (e) { toast(e.message, 'err'); return; }
  toast('Alert resolved', 'ok');
  _alertingLoadEvents(_alertEvtFilter, true);
}

// ═══════════════════════════════════════════════════════════════
// MAINTENANCE WINDOWS sub-tab (preserved from old engine)
// ═══════════════════════════════════════════════════════════════

async function _alertingLoadMaint() {
  const list = document.getElementById('alrt-maint-list');
  if (!list) return;
  list.innerHTML = '<div class="alrt-loading">Loading\u2026</div>';
  applyRbac();
  try {
    const d = await api('GET', '/api/alert/windows');
    _alertMaintWindows = d.windows || [];
    _alertMaintRenderList(_alertMaintWindows);
  } catch (e) {
    list.innerHTML = `<div class="alrt-err">Error: ${esc(String(e))}</div>`;
  }
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
    const active  = w.start_ts <= now && w.end_ts >= now;
    const start   = new Date(w.start_ts * 1000).toLocaleString();
    const end     = new Date(w.end_ts   * 1000).toLocaleString();
    const scopeLbl = w.scope_type === 'all'    ? 'All devices'
                   : w.scope_type === 'group'  ? `Group: ${esc(w.scope_value)}`
                   : `Device: ${esc(w.scope_value)}`;
    const recurLbl = w.recurring
      ? `Recurring days ${esc(w.recur_days)} ${esc(w.recur_start)}–${esc(w.recur_end)}`
      : 'One-time';
    const safeName = esc(w.name).replace(/'/g, '&#39;');
    return `
      <div class="alrt-card ${active ? 'alrt-maint-active' : ''}">
        <div class="alrt-card-left">
          <div class="alrt-card-top">
            ${active ? '<span class="alrt-dot-on" title="Currently active">●</span>' : ''}
            <span class="alrt-name">${esc(w.name)}</span>
            ${active ? '<span class="alrt-sev-badge alrt-sev-warn">ACTIVE</span>' : ''}
          </div>
          <div class="alrt-card-info">
            <span class="alrt-info-pill">📅 ${start} → ${end}</span>
            <span class="alrt-info-pill">🎯 ${scopeLbl}</span>
            <span class="alrt-info-pill">🔄 ${recurLbl}</span>
          </div>
        </div>
        <div class="alrt-card-btns">
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
  const toLocal = ts => ts ? new Date(ts * 1000).toISOString().slice(0,16) : '';

  const name       = w?.name        || '';
  const scopeType  = w?.scope_type  || 'all';
  const scopeVal   = w?.scope_value || '';
  const startDt    = toLocal(w?.start_ts || now);
  const endDt      = toLocal(w?.end_ts   || now + 3600);
  const recurring  = w?.recurring   || false;
  const recurDays  = w?.recur_days  || '';
  const recurStart = w?.recur_start || '00:00';
  const recurEnd   = w?.recur_end   || '06:00';

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
          <label class="fl">Scope</label>
          <select id="mw-scope" onchange="_mwScopeChange()">
            <option value="all"    ${scopeType==='all'   ?'selected':''}>All devices</option>
            <option value="group"  ${scopeType==='group' ?'selected':''}>Device group</option>
            <option value="device" ${scopeType==='device'?'selected':''}>Specific device ID</option>
          </select>
        </div>
        <div class="fr" id="mw-scope-val-row" style="${scopeType==='all'?'display:none':''}">
          <label class="fl" id="mw-scope-val-lbl">${scopeType==='group'?'Group name':'Device ID'}</label>
          <input type="text" id="mw-scope-val" value="${esc(scopeVal)}" autocomplete="off"/>
        </div>
        <div style="display:flex;gap:12px;flex-wrap:wrap">
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
}

function _mwScopeChange() {
  const sel = document.getElementById('mw-scope')?.value;
  const row = document.getElementById('mw-scope-val-row');
  const lbl = document.getElementById('mw-scope-val-lbl');
  if (!row) return;
  row.style.display = sel === 'all' ? 'none' : '';
  if (lbl) lbl.textContent = sel === 'group' ? 'Group name' : 'Device ID';
}

function _mwRecurChange() {
  const checked = document.getElementById('mw-recurring')?.checked;
  const panel   = document.getElementById('mw-recur-panel');
  if (panel) panel.style.display = checked ? '' : 'none';
}

function _mwDayToggle(btn) {
  btn.classList.toggle('active');
}

async function _alertMaintSave(id) {
  const name      = (document.getElementById('mw-name')?.value || '').trim();
  const scopeType = document.getElementById('mw-scope')?.value || 'all';
  const scopeVal  = (document.getElementById('mw-scope-val')?.value || '').trim();
  const startRaw  = document.getElementById('mw-start')?.value;
  const endRaw    = document.getElementById('mw-end')?.value;
  const recurring = document.getElementById('mw-recurring')?.checked || false;
  const recurStart = document.getElementById('mw-recur-start')?.value || '';
  const recurEnd   = document.getElementById('mw-recur-end')?.value   || '';

  const activeDays = [...document.querySelectorAll('.alrt-day-btn.active')]
    .map(b => b.dataset.day).join(',');

  const startTs = startRaw ? Math.floor(new Date(startRaw).getTime() / 1000) : 0;
  const endTs   = endRaw   ? Math.floor(new Date(endRaw).getTime()   / 1000) : 0;

  const payload = {
    name, scope_type: scopeType, scope_value: scopeVal,
    start_ts: startTs, end_ts: endTs,
    recurring, recur_days: activeDays,
    recur_start: recurStart, recur_end: recurEnd,
  };

  const btn = document.getElementById('mw-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving\u2026'; }

  const isNew  = id === null;
  const method = isNew ? 'POST'  : 'PATCH';
  const path   = isNew ? '/api/alert/window' : `/api/alert/window/${id}`;
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
