// ── Alerting (Rules in Settings, History in Events tab) ──────────────

let _alertRules        = [];
let _alertEditingId    = null;   // null = new rule
let _alertEvtFilter    = 'all';
let _alertEvtOffset    = 0;
const _ALERT_EVT_LIMIT = 100;
let _alertMaintWindows = [];


// ═══════════════════════════════════════════════════════════════
// RULES sub-tab
// ═══════════════════════════════════════════════════════════════

async function _alertingLoadRules() {
  const list = document.getElementById('alrt-list');
  if (!list) return;
  list.innerHTML = '<div class="alrt-loading">Loading…</div>';
  applyRbac();
  try {
    const r = await fetch('/api/alert/rules');
    if (r.status === 401) { showLogin('Session expired'); return; }
    const d = await r.json();
    _alertRules = d.rules || [];
    _alertingRenderRules(_alertRules);
  } catch (e) {
    list.innerHTML = `<div class="alrt-err">Failed to load rules: ${esc(String(e))}</div>`;
  }
}

function _alertingRenderRules(rules) {
  const wrap = document.getElementById('alrt-list');
  if (!wrap) return;
  if (!rules.length) {
    wrap.innerHTML = `<div class="alrt-empty">No rules yet. Click <strong>＋ New Rule</strong> to get started.</div>`;
    return;
  }
  wrap.innerHTML = rules.map(r => _alertRuleCard(r)).join('');
  applyRbac();
}

function _alertRuleCard(r) {
  const sevCls = r.severity === 'critical' ? 'alrt-sev-crit'
               : r.severity === 'info'     ? 'alrt-sev-info' : 'alrt-sev-warn';
  const dot = r.enabled
    ? '<span class="alrt-dot-on" title="Enabled">●</span>'
    : '<span class="alrt-dot-off" title="Disabled">○</span>';
  const condCount = (r.conditions || []).length;
  const actCount  = (r.actions    || []).length;
  const condSummary = condCount === 0
    ? '<span style="color:var(--text3)">Matches all events</span>'
    : `${condCount} condition${condCount > 1 ? 's' : ''} (${esc(r.condition_logic)})`;
  const actSummary = actCount === 0
    ? '<span style="color:var(--down)">No actions</span>'
    : `${actCount} action${actCount > 1 ? 's' : ''}`;
  const safeName = esc(r.name).replace(/'/g, '&#39;');
  return `
    <div class="alrt-card">
      <div class="alrt-card-left">
        <div class="alrt-card-top">
          ${dot}
          <span class="alrt-name">${esc(r.name)}</span>
          <span class="alrt-sev-badge ${sevCls}">${esc(r.severity)}</span>
        </div>
        <div class="alrt-card-info">
          <span class="alrt-info-pill">⚙ ${condSummary}</span>
          <span class="alrt-info-pill">↪ ${actSummary}</span>
          <span class="alrt-info-pill" style="color:var(--text3)">cooldown ${r.cooldown_s}s</span>
        </div>
      </div>
      <div class="alrt-card-btns">
        <button class="btn-sm rbac-op"    onclick="_alertingToggle(${r.id})">${r.enabled ? 'Disable' : 'Enable'}</button>
        <button class="btn-sm rbac-admin" onclick="_alertingOpenEditor(${r.id})">Edit</button>
        <button class="btn-sm rbac-admin" onclick="_alertingTest(${r.id},'${safeName}')">Test</button>
        <button class="btn-sm rbac-admin alrt-del-btn" onclick="_alertingDelete(${r.id},'${safeName}')">Delete</button>
      </div>
    </div>`;
}

async function _alertingToggle(id) {
  const d = await api('POST', `/api/alert/rule/${id}/toggle`);
  if (d.error) { toast(d.error, 'err'); return; }
  _alertingLoadRules();
}

async function _alertingTest(id, name) {
  const d = await api('POST', `/api/alert/rule/${id}/test`);
  if (d.error) { toast(d.error, 'err'); return; }
  toast(d.msg || `Test dispatched for "${name}"`, 'info');
}

async function _alertingDelete(id, name) {
  if (!confirm(`Delete rule "${name}"?\nThis cannot be undone.`)) return;
  const d = await api('DELETE', `/api/alert/rule/${id}`);
  if (d.error) { toast(d.error, 'err'); return; }
  toast(`Rule "${name}" deleted`, 'info');
  _alertingLoadRules();
}


// ═══════════════════════════════════════════════════════════════
// ALERT EVENTS sub-tabs (Active + History)
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
    <div id="alrt-evt-list-${panelId}"><div class="alrt-loading">Loading…</div></div>
    <div id="alrt-evt-pager-${panelId}" class="alrt-pager"></div>`;

  await _alertingFetchEvents(state, panelId);
}

async function _alertingFetchEvents(state, panelId) {
  const listId = `alrt-evt-list-${panelId}`;
  const list   = document.getElementById(listId);
  if (!list) return;
  const qs  = `state=${state}&limit=${_ALERT_EVT_LIMIT}&offset=${_alertEvtOffset}`;
  try {
    const r = await fetch(`/api/alert/events?${qs}`);
    if (r.status === 401) { showLogin('Session expired'); return; }
    const d = await r.json();
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
    // Pager
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
               : e.severity === 'info'     ? 'alrt-sev-info' : 'alrt-sev-warn';
  return `
    <div class="alrt-evt-row">
      <div class="alrt-evt-left">
        <div class="alrt-evt-top">
          <span class="alrt-state-badge ${stateCls}">${e.state}</span>
          <span class="alrt-sev-badge ${sevCls}">${esc(e.severity)}</span>
          <span class="alrt-evt-rule">${esc(e.rule_name)}</span>
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
  const d = await api('POST', `/api/alert/event/${id}/ack`);
  if (!d.ok && d.error) { toast(d.error, 'err'); return; }
  toast('Alert acknowledged', 'ok');
  _alertingLoadEvents(_alertEvtFilter, true);
  fetch('/api/alert/events/active').then(r=>r.json()).then(d=>{
    _alertEvtBadgeCount = d.count || 0;
    if (typeof _updateEvtBadge === 'function') _updateEvtBadge();
  }).catch(()=>{});
}

async function _alertResolve(id) {
  const d = await api('POST', `/api/alert/event/${id}/resolve`);
  if (!d.ok && d.error) { toast(d.error, 'err'); return; }
  toast('Alert resolved', 'ok');
  _alertingLoadEvents(_alertEvtFilter, true);
  fetch('/api/alert/events/active').then(r=>r.json()).then(d=>{
    _alertEvtBadgeCount = d.count || 0;
    if (typeof _updateEvtBadge === 'function') _updateEvtBadge();
  }).catch(()=>{});
}


// ═══════════════════════════════════════════════════════════════
// MAINTENANCE WINDOWS sub-tab
// ═══════════════════════════════════════════════════════════════

async function _alertingLoadMaint() {
  const list = document.getElementById('alrt-maint-list');
  if (!list) return;
  list.innerHTML = '<div class="alrt-loading">Loading…</div>';
  applyRbac();
  try {
    const r = await fetch('/api/alert/windows');
    if (r.status === 401) { showLogin('Session expired'); return; }
    const d = await r.json();
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
  const d = await api('DELETE', `/api/alert/window/${id}`);
  if (d.error) { toast(d.error, 'err'); return; }
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
        <button class="mclose" onclick="closeM('alrt-maint-modal')">✕</button>
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
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  const isNew  = id === null;
  const method = isNew ? 'POST'  : 'PATCH';
  const path   = isNew ? '/api/alert/window' : `/api/alert/window/${id}`;
  const d = await api(method, path, payload);

  if (btn) { btn.disabled = false; btn.textContent = 'Save Window'; }

  if (d.error) { toast(d.error, 'err'); return; }
  toast(isNew ? `Window "${name}" created` : `Window "${name}" updated`, 'ok');
  closeM('alrt-maint-modal');
  _alertingLoadMaint();
}


// ═══════════════════════════════════════════════════════════════
// RULE EDITOR modal
// ═══════════════════════════════════════════════════════════════

function _alertingOpenEditor(id) {
  closeM('alrt-editor-modal');
  const rule = (id !== null) ? (_alertRules.find(r => r.id === id) || null) : null;
  _alertEditingId = rule ? rule.id : null;

  const name    = rule?.name            || '';
  const enabled = rule ? rule.enabled   : true;
  const sev     = rule?.severity        || 'warning';
  const logic   = rule?.condition_logic || 'AND';
  const cool    = rule?.cooldown_s      ?? 300;

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'alrt-editor-modal';
  _overlayClose(o, () => closeM('alrt-editor-modal'));

  o.innerHTML = `
    <div class="mbox alrt-editor-box">
      <div class="mhd">
        <div class="mttl">${rule ? '✏ Edit Rule' : '＋ New Rule'}</div>
        <button class="mclose" onclick="closeM('alrt-editor-modal')">✕</button>
      </div>
      <div class="mbdy alrt-editor-body">
        <div class="fr">
          <label class="fl">Name</label>
          <input type="text" id="ae-name" value="${esc(name)}"
            placeholder="e.g. Ping DOWN → ops email" autocomplete="off" maxlength="200"/>
        </div>
        <div class="alrt-row3">
          <div class="fr">
            <label class="fl">Severity</label>
            <select id="ae-sev">
              <option value="info"     ${sev==='info'    ?'selected':''}>info</option>
              <option value="warning"  ${sev==='warning' ?'selected':''}>warning</option>
              <option value="critical" ${sev==='critical'?'selected':''}>critical</option>
            </select>
          </div>
          <div class="fr">
            <label class="fl">Cooldown (seconds)</label>
            <input type="number" id="ae-cool" value="${cool}" min="0" step="60"/>
          </div>
          <div class="fr" style="align-self:flex-end;padding-bottom:14px">
            <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
              <input type="checkbox" id="ae-enabled" ${enabled?'checked':''}/>
              <span style="font-size:12px;color:var(--text2)">Enabled</span>
            </label>
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">
            <span>Conditions</span>
            <span style="color:var(--text3);font-size:11px;margin-left:8px">Match</span>
            <select id="ae-logic" class="ae-logic-sel">
              <option value="AND" ${logic==='AND'?'selected':''}>ALL</option>
              <option value="OR"  ${logic==='OR' ?'selected':''}>ANY</option>
            </select>
            <span style="color:var(--text3);font-size:11px;margin-left:4px">of the conditions</span>
            <button class="btn-sm" style="margin-left:auto" onclick="_alertingAddCondition(null)">＋ Add</button>
          </div>
          <div id="ae-cond-list" class="alrt-cond-list"></div>
          <div id="ae-cond-empty" class="alrt-hint"
               style="${(rule?.conditions||[]).length ? 'display:none' : ''}">
            No conditions — matches every sensor event.
          </div>
        </div>

        <div class="alrt-section">
          <div class="alrt-section-hdr">
            <span>Actions</span>
            <div style="margin-left:auto;display:flex;gap:6px">
              <button class="btn-sm" onclick="_alertingAddAction('email')">＋ Email</button>
              <button class="btn-sm" onclick="_alertingAddAction('webhook')">＋ Webhook</button>
              <button class="btn-sm" onclick="_alertingAddAction('syslog')">＋ Syslog</button>
            </div>
          </div>
          <div id="ae-act-list" class="alrt-act-list"></div>
          <div id="ae-act-empty" class="alrt-hint"
               style="${(rule?.actions||[]).length ? 'display:none' : ''}">
            No actions — rule will match but send no notifications.
          </div>
        </div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('alrt-editor-modal')">Cancel</button>
        <button class="btn-p" id="ae-save-btn" onclick="_alertingSave()">Save Rule</button>
      </div>
    </div>`;

  document.body.appendChild(o);
  (rule?.conditions || []).forEach(c => _alertingAddCondition(c));
  (rule?.actions    || []).forEach(a => _alertingAddAction(a.atype, a));
  setTimeout(() => document.getElementById('ae-name')?.focus(), 60);
}

// ── Condition rows ────────────────────────────────────────────────────
const _AE_FIELDS = [
  {v:'event_type',      l:'Event Type'},
  {v:'sensor_type',     l:'Sensor Type'},
  {v:'device_group',    l:'Device Group'},
  {v:'threshold_state', l:'Threshold State'},
  {v:'direction',       l:'Direction'},
  {v:'loss_pct',        l:'Packet Loss %'},
  {v:'severity',        l:'Severity'},
];
const _AE_OPS = [
  {v:'eq',       l:'= equals'},
  {v:'ne',       l:'≠ not equals'},
  {v:'contains', l:'contains'},
  {v:'in',       l:'in list'},
  {v:'gt',       l:'> greater than'},
  {v:'gte',      l:'≥ at least'},
  {v:'lt',       l:'< less than'},
  {v:'lte',      l:'≤ at most'},
];
// Known enum values for fields that have a fixed set of options
const _AE_FIELD_VALUES = {
  event_type:      ['down','recovered','threshold_warning','threshold_critical'],
  severity:        ['critical','warning','info'],
  direction:       ['down','recovered','threshold'],
  threshold_state: ['warn','crit'],
};

function _aeValueHtml(field, val) {
  const opts = _AE_FIELD_VALUES[field];
  if (opts) {
    const safeVal = opts.includes(val) ? val : opts[0];
    return `<select class="ae-cond-val">${opts.map(o =>
      `<option value="${o}"${safeVal===o?' selected':''}>${o}</option>`).join('')}</select>`;
  }
  return `<input type="text" class="ae-cond-val" value="${esc(val)}"
    placeholder="value" autocomplete="off" spellcheck="false"/>`;
}

function _aeFieldChanged(selectEl) {
  const row   = selectEl.closest('.alrt-cond-row');
  const valEl = row?.querySelector('.ae-cond-val');
  if (!valEl) return;
  const newHtml = _aeValueHtml(selectEl.value, '');
  valEl.outerHTML = newHtml;
}

function _alertingAddCondition(cond) {
  const list  = document.getElementById('ae-cond-list');
  const empty = document.getElementById('ae-cond-empty');
  if (!list) return;
  if (empty) empty.style.display = 'none';
  const field = cond?.field || 'event_type';
  const op    = cond?.op    || 'eq';
  const val   = cond?.value ?? '';
  const fieldOpts = _AE_FIELDS.map(f =>
    `<option value="${f.v}" ${field===f.v?'selected':''}>${esc(f.l)}</option>`).join('');
  const opOpts = _AE_OPS.map(o =>
    `<option value="${o.v}" ${op===o.v?'selected':''}>${esc(o.l)}</option>`).join('');
  const row = document.createElement('div');
  row.className = 'alrt-cond-row';
  row.innerHTML = `
    <select class="ae-cond-field" onchange="_aeFieldChanged(this)">${fieldOpts}</select>
    <select class="ae-cond-op">${opOpts}</select>
    ${_aeValueHtml(field, val)}
    <button class="alrt-rm-btn"
      onclick="this.closest('.alrt-cond-row').remove();
               _alertingCheckEmpty('ae-cond-list','ae-cond-empty')"
      title="Remove">✕</button>`;
  list.appendChild(row);
}

// ── Action blocks ─────────────────────────────────────────────────────
function _alertingAddAction(atype, action) {
  const list  = document.getElementById('ae-act-list');
  const empty = document.getElementById('ae-act-empty');
  if (!list) return;
  if (empty) empty.style.display = 'none';
  const cfg = action?.config || {};
  const blk = document.createElement('div');
  blk.className = 'alrt-act-block';
  blk.dataset.atype = atype;

  const rmBtn = `<button class="alrt-rm-btn"
    onclick="this.closest('.alrt-act-block').remove();
             _alertingCheckEmpty('ae-act-list','ae-act-empty')"
    title="Remove">✕</button>`;

  if (atype === 'email') {
    blk.innerHTML = `
      <div class="alrt-act-hdr"><span class="alrt-act-label">📧 Email</span>${rmBtn}</div>
      <div class="fr">
        <label class="fl">To <span style="color:var(--text3);font-size:10px">(comma-separated)</span></label>
        <input type="text" class="ae-act-to" value="${esc(cfg.to||'')}"
          placeholder="ops@example.com, alerts@corp.com" autocomplete="off"/>
      </div>
      <div class="fr">
        <label class="fl">Subject <span style="color:var(--text3);font-size:10px">(supports {dname}, {sname}, {severity}, {event_type})</span></label>
        <input type="text" class="ae-act-subj" value="${esc(cfg.subject||'')}"
          placeholder="[{severity}] {dname}/{sname} — {event_type}" autocomplete="off"/>
      </div>
      <div class="fr">
        <label class="fl">Body <span style="color:var(--text3);font-size:10px">(empty = auto-generated)</span></label>
        <textarea class="ae-act-body" rows="2" placeholder="Leave empty for default…">${esc(cfg.body||'')}</textarea>
      </div>`;
  } else if (atype === 'webhook') {
    blk.innerHTML = `
      <div class="alrt-act-hdr"><span class="alrt-act-label">🔗 Webhook</span>${rmBtn}</div>
      <div class="fr">
        <label class="fl">URL</label>
        <input type="text" class="ae-act-url" value="${esc(cfg.url||'')}"
          placeholder="https://hooks.example.com/..." autocomplete="off" spellcheck="false"/>
      </div>
      <div class="fr">
        <label class="fl">Body template <span style="color:var(--text3);font-size:10px">(JSON with {placeholders} — empty = full ctx dict)</span></label>
        <textarea class="ae-act-wbody" rows="2"
          placeholder='{"text":"[{severity}] {dname}/{sname} is {event_type}"}'>${esc(cfg.body||'')}</textarea>
      </div>`;
  } else if (atype === 'syslog') {
    blk.innerHTML = `
      <div class="alrt-act-hdr"><span class="alrt-act-label">📡 Syslog</span>${rmBtn}</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap">
        <div class="fr" style="flex:2;min-width:180px">
          <label class="fl">Host <span style="color:var(--text3);font-size:10px">(empty = use global syslog settings)</span></label>
          <input type="text" class="ae-act-shost" value="${esc(cfg.host||'')}"
            placeholder="syslog.corp.com" autocomplete="off" spellcheck="false"/>
        </div>
        <div class="fr" style="flex:1;min-width:80px">
          <label class="fl">Port</label>
          <input type="number" class="ae-act-sport" value="${cfg.port||514}" min="1" max="65535"/>
        </div>
        <div class="fr" style="flex:1;min-width:80px">
          <label class="fl">Protocol</label>
          <select class="ae-act-sproto">
            <option value="udp" ${(cfg.proto||'udp')==='udp'?'selected':''}>UDP</option>
            <option value="tcp" ${(cfg.proto||'')==='tcp'?'selected':''}>TCP</option>
          </select>
        </div>
      </div>`;
  }
  list.appendChild(blk);
}

function _alertingCheckEmpty(listId, emptyId) {
  const list  = document.getElementById(listId);
  const empty = document.getElementById(emptyId);
  if (empty) empty.style.display = (list && list.children.length === 0) ? '' : 'none';
}

// ── Save rule ─────────────────────────────────────────────────────────
async function _alertingSave() {
  const name    = (document.getElementById('ae-name')?.value || '').trim();
  const enabled = document.getElementById('ae-enabled')?.checked ?? true;
  const sev     = document.getElementById('ae-sev')?.value   || 'warning';
  const logic   = document.getElementById('ae-logic')?.value || 'AND';
  const cool    = parseInt(document.getElementById('ae-cool')?.value || '300', 10);

  const conditions = [];
  document.querySelectorAll('#ae-cond-list .alrt-cond-row').forEach(row => {
    conditions.push({
      field: row.querySelector('.ae-cond-field')?.value || 'event_type',
      op:    row.querySelector('.ae-cond-op')?.value    || 'eq',
      value: row.querySelector('.ae-cond-val')?.value   || '',
    });
  });

  const actions = [];
  document.querySelectorAll('#ae-act-list .alrt-act-block').forEach(blk => {
    const atype = blk.dataset.atype;
    let cfg = {};
    if (atype === 'email') {
      cfg = {
        to:      (blk.querySelector('.ae-act-to')?.value   || '').trim(),
        subject: (blk.querySelector('.ae-act-subj')?.value || '').trim(),
        body:    (blk.querySelector('.ae-act-body')?.value || '').trim(),
      };
    } else if (atype === 'webhook') {
      cfg = {
        url:  (blk.querySelector('.ae-act-url')?.value   || '').trim(),
        body: (blk.querySelector('.ae-act-wbody')?.value || '').trim(),
      };
    } else if (atype === 'syslog') {
      cfg = {
        host:  (blk.querySelector('.ae-act-shost')?.value  || '').trim(),
        port:  parseInt(blk.querySelector('.ae-act-sport')?.value || '514', 10),
        proto: blk.querySelector('.ae-act-sproto')?.value || 'udp',
      };
    }
    actions.push({ atype, config: cfg });
  });

  const payload = {
    name, enabled, severity: sev,
    condition_logic: logic, cooldown_s: isNaN(cool) ? 300 : cool,
    conditions, actions,
  };

  const btn = document.getElementById('ae-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  const isNew  = _alertEditingId === null;
  const method = isNew ? 'POST'  : 'PATCH';
  const path   = isNew ? '/api/alert/rule' : `/api/alert/rule/${_alertEditingId}`;
  const d = await api(method, path, payload);

  if (btn) { btn.disabled = false; btn.textContent = 'Save Rule'; }

  if (d.error) { toast(d.error, 'err'); return; }
  toast(isNew ? `Rule "${name}" created` : `Rule "${name}" updated`, 'ok');
  closeM('alrt-editor-modal');
  _alertingLoadRules();
}
