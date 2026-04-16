/* ── reports.js — Reports tab UI ─────────────────────────────────────
   Sub-tabs: Templates / Schedules / History
   CRUD modals reuse the global .mo/.mbox modal pattern from forms-utils.
   Preview opens in a new tab; Run Now + Test Send hit POST /api/reports/*.
*/

let _rptTab = 'templates';   // 'templates' | 'schedules' | 'history'
let _rptBooted = false;

function _rptInit(){
  const root = document.getElementById('reportsView');
  if(!root) return;
  if(!_rptBooted){
    root.innerHTML = `
      <div class="rpt-toolbar">
        <span class="rpt-title">📊 Reports</span>
        <div class="rpt-tabs">
          <button class="rpt-subtab" id="rptTabTemplates" onclick="_rptSwitch('templates')">Templates</button>
          <button class="rpt-subtab" id="rptTabSchedules" onclick="_rptSwitch('schedules')">Schedules</button>
          <button class="rpt-subtab" id="rptTabHistory"   onclick="_rptSwitch('history')">History</button>
        </div>
        <div style="flex:1"></div>
        <button class="rpt-btn rpt-btn-primary" id="rptNewBtn" onclick="_rptNew()">+ New</button>
      </div>
      <div id="rptBody" class="rpt-body"></div>
    `;
    _rptBooted = true;
  }
  _rptSwitch(_rptTab);
}

function _rptSwitch(tab){
  _rptTab = tab;
  ['Templates','Schedules','History'].forEach(k=>{
    const el = document.getElementById('rptTab'+k);
    if(el) el.classList.toggle('active', tab===k.toLowerCase());
  });
  const nb = document.getElementById('rptNewBtn');
  if(nb) nb.style.display = (tab==='history') ? 'none' : '';
  if(tab==='templates') _rptRenderTemplates();
  else if(tab==='schedules') _rptRenderSchedules();
  else _rptRenderHistory();
}

function _rptNew(){
  if(_rptTab==='templates') _rptEditTemplate(null);
  else if(_rptTab==='schedules') _rptEditSchedule(null);
}

/* ── Templates ─────────────────────────────────────────────────── */

async function _rptRenderTemplates(){
  const body = document.getElementById('rptBody');
  body.innerHTML = `<div class="muted" style="padding:20px">Loading…</div>`;
  try{
    const r = await api('/api/reports/templates');
    const tpls = r.templates || [];
    if(!tpls.length){
      body.innerHTML = `
        <div class="rpt-empty">
          <div class="rpt-empty-icon">📄</div>
          <div class="rpt-empty-title">No report templates yet</div>
          <div class="rpt-empty-hint">Templates define what goes into a report. Start with one of the presets.</div>
          <div class="rpt-empty-actions">
            <button class="rpt-btn rpt-btn-primary" onclick="_rptEditTemplate(null,'executive')">+ Executive Summary</button>
            <button class="rpt-btn" onclick="_rptEditTemplate(null,'technical')">+ Technical / Ops</button>
          </div>
        </div>`;
      return;
    }
    const rows = tpls.map(t=>`
      <tr>
        <td><strong>${esc(t.name)}</strong><div class="muted small">${esc(t.description||'')}</div></td>
        <td><span class="rpt-kind rpt-kind-${esc(t.kind)}">${esc(t.kind)}</span></td>
        <td class="muted small">${esc(t.created_by||'')}</td>
        <td class="muted small">${_fmtDate(t.updated_at)}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="rpt-btn-sm" onclick="_rptPreview('${esc(t.id)}')">👁 Preview</button>
          <button class="rpt-btn-sm" onclick="_rptRunNow('${esc(t.id)}')">▶ Run Now</button>
          <button class="rpt-btn-sm" onclick="_rptEditTemplate('${esc(t.id)}')">✎ Edit</button>
          <button class="rpt-btn-sm" onclick="_rptTestSend('${esc(t.id)}')">✉ Test</button>
          <button class="rpt-btn-sm rpt-btn-danger" onclick="_rptDeleteTemplate('${esc(t.id)}','${esc(t.name)}')">🗑</button>
        </td>
      </tr>`).join('');
    body.innerHTML = `
      <table class="rpt-table">
        <thead><tr><th>Name</th><th>Kind</th><th>Created By</th><th>Updated</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }catch(e){
    body.innerHTML = `<div class="error" style="padding:20px">Failed to load templates</div>`;
  }
}

function _rptEditTemplate(tid, presetKind){
  closeM('rptTplModal');
  (async()=>{
    let t = {name:'', kind: presetKind||'executive', description:'', config_json:{period:'last_month'}};
    if(tid){
      try{ const r = await api('/api/reports/template/'+tid); t = r.template || t; }catch(_){}
    }
    const cfg = t.config_json || {};
    const o = document.createElement('div');
    o.className = 'mo'; o.id = 'rptTplModal';
    _overlayClose(o, ()=>closeM('rptTplModal'));
    o.innerHTML = `
      <div class="mbox" style="max-width:620px">
        <div class="mhd">${tid?'Edit':'New'} Report Template</div>
        <div class="mbdy">
          <label class="lbl">Name</label>
          <input id="_rt_name" class="fi" value="${esc(t.name)}" placeholder="e.g. Monthly Exec Summary">
          <label class="lbl" style="margin-top:10px">Kind</label>
          <select id="_rt_kind" class="fi">
            <option value="executive" ${t.kind==='executive'?'selected':''}>Executive Summary (high-level)</option>
            <option value="technical" ${t.kind==='technical'?'selected':''}>Technical / Operations (detailed)</option>
            <option value="custom"    ${t.kind==='custom'?'selected':''}>Custom</option>
          </select>
          <label class="lbl" style="margin-top:10px">Description <span class="muted small">(optional)</span></label>
          <input id="_rt_desc" class="fi" value="${esc(t.description||'')}">
          <label class="lbl" style="margin-top:10px">Default Period</label>
          <select id="_rt_period" class="fi">
            <option value="last_7d"       ${cfg.period==='last_7d'?'selected':''}>Last 7 days</option>
            <option value="last_30d"      ${cfg.period==='last_30d'?'selected':''}>Last 30 days</option>
            <option value="last_90d"      ${cfg.period==='last_90d'?'selected':''}>Last 90 days</option>
            <option value="last_month"    ${(cfg.period==='last_month'||!cfg.period)?'selected':''}>Last calendar month</option>
            <option value="last_quarter"  ${cfg.period==='last_quarter'?'selected':''}>Last quarter</option>
            <option value="last_year"     ${cfg.period==='last_year'?'selected':''}>Last 365 days</option>
            <option value="month_to_date" ${cfg.period==='month_to_date'?'selected':''}>Month to date</option>
          </select>
          <label class="lbl" style="margin-top:10px">Cover title <span class="muted small">(optional, defaults to kind)</span></label>
          <input id="_rt_title" class="fi" value="${esc(cfg.title||'')}">
          <label class="lbl" style="margin-top:10px">Subtitle <span class="muted small">(optional)</span></label>
          <input id="_rt_subtitle" class="fi" value="${esc(cfg.subtitle||'')}">
        </div>
        <div class="mft">
          <button class="btn" onclick="closeM('rptTplModal')">Cancel</button>
          <button class="btn btn-primary" onclick="_rptSaveTemplate('${esc(tid||'')}')">${tid?'Save':'Create'}</button>
        </div>
      </div>`;
    document.body.appendChild(o);
  })();
}

async function _rptSaveTemplate(tid){
  const payload = {
    name:        document.getElementById('_rt_name').value.trim(),
    kind:        document.getElementById('_rt_kind').value,
    description: document.getElementById('_rt_desc').value.trim(),
    config_json: {
      period:   document.getElementById('_rt_period').value,
      title:    document.getElementById('_rt_title').value.trim(),
      subtitle: document.getElementById('_rt_subtitle').value.trim(),
    },
  };
  if(!payload.name){ alert('Name required'); return; }
  try{
    if(tid){
      await api('/api/reports/template/'+tid, {method:'PATCH', body: payload});
    }else{
      await api('/api/reports/template',      {method:'POST',  body: payload});
    }
    closeM('rptTplModal');
    _rptRenderTemplates();
  }catch(e){
    alert('Save failed: '+(e.message||e));
  }
}

async function _rptDeleteTemplate(tid, name){
  if(!confirm('Delete template "'+name+'"?\n\nAll schedules referencing it will also be deleted.')) return;
  try{
    await api('/api/reports/template/'+tid, {method:'DELETE'});
    _rptRenderTemplates();
  }catch(e){ alert('Delete failed'); }
}

function _rptPreview(tid){
  // Open a blank tab first (sync — avoids popup blocker), then navigate to POST-backed endpoint via a form
  const w = window.open('about:blank', '_blank');
  if(!w){ alert('Popup blocked — allow popups for preview'); return; }
  const f = w.document.createElement('form');
  f.method = 'POST';
  f.action = '/api/reports/preview';
  const h = w.document.createElement('input');
  h.type = 'hidden'; h.name = 'template_id'; h.value = tid;
  f.appendChild(h);
  w.document.body.appendChild(f);
  // The server expects JSON; fall back to a fetch + document.write so preview works
  // (form POST multipart to a JSON endpoint won't match)
  w.document.write('<div style="font-family:sans-serif;padding:40px;color:#555">Rendering preview…</div>');
  fetch('/api/reports/preview', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({template_id: tid}),
  }).then(r=>r.text()).then(html=>{
    w.document.open(); w.document.write(html); w.document.close();
  }).catch(_=>{
    w.document.open(); w.document.write('<pre style="color:#c0392b;padding:40px">Preview failed.</pre>'); w.document.close();
  });
}

async function _rptRunNow(tid){
  if(!confirm('Render this report now? A PDF will be saved to the server and appear in History.')) return;
  try{
    const r = await api('/api/reports/run', {method:'POST', body:{template_id: tid}});
    alert('Report generated ('+Math.round((r.pdf_bytes||0)/1024)+' KB). Check the History tab.');
    if(_rptTab==='history') _rptRenderHistory();
  }catch(e){ alert('Run failed'); }
}

async function _rptTestSend(tid){
  const to = prompt('Send a test copy to which email address?\n\n(Uses SMTP configured in Settings → Email.)');
  if(!to || !to.trim()) return;
  try{
    await api('/api/reports/test-send', {method:'POST', body:{template_id: tid, to: to.trim()}});
    alert('Test email sent to '+to.trim());
  }catch(e){ alert('Test send failed: '+(e.message||e)); }
}

/* ── Schedules ─────────────────────────────────────────────────── */

async function _rptRenderSchedules(){
  const body = document.getElementById('rptBody');
  body.innerHTML = `<div class="muted" style="padding:20px">Loading…</div>`;
  try{
    const [a,b] = await Promise.all([api('/api/reports/schedules'), api('/api/reports/templates')]);
    const schs = a.schedules || [];
    const tpls = b.templates || [];
    const tplById = {}; tpls.forEach(t=>tplById[t.id]=t);
    if(!schs.length){
      body.innerHTML = `
        <div class="rpt-empty">
          <div class="rpt-empty-icon">📅</div>
          <div class="rpt-empty-title">No schedules yet</div>
          <div class="rpt-empty-hint">A schedule pairs a template with a cadence and recipient list.</div>
          <div class="rpt-empty-actions">
            <button class="rpt-btn rpt-btn-primary" onclick="_rptEditSchedule(null)">+ New Schedule</button>
          </div>
        </div>`;
      return;
    }
    const rows = schs.map(s=>{
      const tpl = tplById[s.template_id];
      return `
        <tr>
          <td><strong>${esc(s.name)}</strong></td>
          <td>${tpl?esc(tpl.name):'<span class="muted">(template missing)</span>'}</td>
          <td>${esc(_fmtCadence(s))}</td>
          <td>${esc(s.period||'')}</td>
          <td class="muted small">${_fmtDate(s.last_run_ts) || '—'}</td>
          <td>${s.enabled ? '<span class="pill up">on</span>' : '<span class="pill muted">off</span>'}</td>
          <td style="text-align:right;white-space:nowrap">
            <button class="rpt-btn-sm" onclick="_rptRunSchedule('${esc(s.id)}','${esc(s.name)}')">▶ Run</button>
            <button class="rpt-btn-sm" onclick="_rptEditSchedule('${esc(s.id)}')">✎ Edit</button>
            <button class="rpt-btn-sm rpt-btn-danger" onclick="_rptDeleteSchedule('${esc(s.id)}','${esc(s.name)}')">🗑</button>
          </td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <table class="rpt-table">
        <thead><tr><th>Name</th><th>Template</th><th>Cadence</th><th>Period</th><th>Last run</th><th>State</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }catch(e){
    body.innerHTML = `<div class="error" style="padding:20px">Failed to load schedules</div>`;
  }
}

function _fmtCadence(s){
  const t = s.time_str || '03:00';
  if(s.freq==='daily')   return 'Daily @ '+t;
  if(s.freq==='weekly')  return 'Weekly ('+(s.day_of_week||'1')+') @ '+t;
  if(s.freq==='monthly') return 'Monthly day '+(s.day_of_month||1)+' @ '+t;
  if(s.freq==='quarterly')return 'Quarterly day '+(s.day_of_month||1)+' @ '+t;
  return s.freq;
}

function _rptEditSchedule(sid){
  closeM('rptSchModal');
  (async()=>{
    let s = {template_id:'', name:'', freq:'monthly', time_str:'03:00',
             day_of_month:1, day_of_week:'1', period:'last_month',
             recipient_group:0, recipient_emails:[], enabled:true,
             subject_tpl:'', body_tpl:''};
    if(sid){
      try{ const r = await api('/api/reports/schedule/'+sid); s = r.schedule || s; }catch(_){}
    }
    let tpls = [], grps = [];
    try{
      const [a,g] = await Promise.all([api('/api/reports/templates'), api('/api/user-groups').catch(_=>({groups:[]}))]);
      tpls = a.templates || [];
      grps = g.groups || [];
    }catch(_){}
    const rcptList = (s.recipient_emails||[]).join(', ');
    const o = document.createElement('div');
    o.className = 'mo'; o.id = 'rptSchModal';
    _overlayClose(o, ()=>closeM('rptSchModal'));
    o.innerHTML = `
      <div class="mbox" style="max-width:620px">
        <div class="mhd">${sid?'Edit':'New'} Schedule</div>
        <div class="mbdy">
          <label class="lbl">Name</label>
          <input id="_rs_name" class="fi" value="${esc(s.name)}" placeholder="e.g. Monthly to Managers">
          <label class="lbl" style="margin-top:10px">Template</label>
          <select id="_rs_tpl" class="fi">
            ${tpls.map(t=>`<option value="${esc(t.id)}" ${t.id===s.template_id?'selected':''}>${esc(t.name)} · ${esc(t.kind)}</option>`).join('')}
          </select>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
            <div>
              <label class="lbl">Frequency</label>
              <select id="_rs_freq" class="fi" onchange="_rptToggleFreq()">
                <option value="daily"     ${s.freq==='daily'?'selected':''}>Daily</option>
                <option value="weekly"    ${s.freq==='weekly'?'selected':''}>Weekly</option>
                <option value="monthly"   ${(s.freq==='monthly'||!s.freq)?'selected':''}>Monthly</option>
                <option value="quarterly" ${s.freq==='quarterly'?'selected':''}>Quarterly</option>
              </select>
            </div>
            <div>
              <label class="lbl">Run at (HH:MM, server TZ)</label>
              <input id="_rs_time" class="fi" value="${esc(s.time_str||'03:00')}">
            </div>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px">
            <div id="_rs_dow_wrap">
              <label class="lbl">Day(s) of week <span class="muted small">(1=Mon, 7=Sun, comma-sep)</span></label>
              <input id="_rs_dow" class="fi" value="${esc(s.day_of_week||'1')}">
            </div>
            <div id="_rs_dom_wrap">
              <label class="lbl">Day of month</label>
              <input id="_rs_dom" type="number" min="1" max="28" class="fi" value="${s.day_of_month||1}">
            </div>
          </div>
          <label class="lbl" style="margin-top:10px">Reporting period</label>
          <select id="_rs_period" class="fi">
            <option value="last_7d"       ${s.period==='last_7d'?'selected':''}>Last 7 days</option>
            <option value="last_30d"      ${s.period==='last_30d'?'selected':''}>Last 30 days</option>
            <option value="last_90d"      ${s.period==='last_90d'?'selected':''}>Last 90 days</option>
            <option value="last_month"    ${(s.period==='last_month'||!s.period)?'selected':''}>Last calendar month</option>
            <option value="last_quarter"  ${s.period==='last_quarter'?'selected':''}>Last quarter</option>
            <option value="last_year"     ${s.period==='last_year'?'selected':''}>Last 365 days</option>
            <option value="month_to_date" ${s.period==='month_to_date'?'selected':''}>Month to date</option>
          </select>
          <label class="lbl" style="margin-top:10px">Recipient user group <span class="muted small">(optional)</span></label>
          <select id="_rs_grp" class="fi">
            <option value="0">— none —</option>
            ${grps.map(g=>`<option value="${g.id}" ${g.id===s.recipient_group?'selected':''}>${esc(g.name)}</option>`).join('')}
          </select>
          <label class="lbl" style="margin-top:10px">Extra email recipients <span class="muted small">(comma-separated)</span></label>
          <input id="_rs_rcpt" class="fi" value="${esc(rcptList)}" placeholder="alice@example.com, bob@example.com">
          <label class="lbl" style="margin-top:10px">Email subject <span class="muted small">(placeholders: {company}, {period}, {title}, {crit}, {warn}, {uptime})</span></label>
          <input id="_rs_subj" class="fi" value="${esc(s.subject_tpl||'[{company}] {title} — {period}')}">
          <label class="lbl" style="margin-top:10px">Email body <span class="muted small">(plain text, placeholders OK)</span></label>
          <textarea id="_rs_body" class="fi" style="height:80px">${esc(s.body_tpl||'')}</textarea>
          <label class="lbl" style="margin-top:10px;display:flex;gap:8px;align-items:center">
            <input type="checkbox" id="_rs_en" ${s.enabled?'checked':''}> Enabled
          </label>
        </div>
        <div class="mft">
          <button class="btn" onclick="closeM('rptSchModal')">Cancel</button>
          <button class="btn btn-primary" onclick="_rptSaveSchedule('${esc(sid||'')}')">${sid?'Save':'Create'}</button>
        </div>
      </div>`;
    document.body.appendChild(o);
    _rptToggleFreq();
  })();
}

function _rptToggleFreq(){
  const f = document.getElementById('_rs_freq').value;
  const dow = document.getElementById('_rs_dow_wrap');
  const dom = document.getElementById('_rs_dom_wrap');
  if(f==='weekly'){ dow.style.display=''; dom.style.display='none'; }
  else if(f==='monthly' || f==='quarterly'){ dow.style.display='none'; dom.style.display=''; }
  else { dow.style.display='none'; dom.style.display='none'; }
}

async function _rptSaveSchedule(sid){
  const rcpts = document.getElementById('_rs_rcpt').value
                .split(',').map(s=>s.trim()).filter(Boolean);
  const payload = {
    template_id:       document.getElementById('_rs_tpl').value,
    name:              document.getElementById('_rs_name').value.trim(),
    freq:              document.getElementById('_rs_freq').value,
    time_str:          document.getElementById('_rs_time').value.trim() || '03:00',
    day_of_week:       document.getElementById('_rs_dow').value.trim() || '1',
    day_of_month:      Number(document.getElementById('_rs_dom').value) || 1,
    period:            document.getElementById('_rs_period').value,
    recipient_group:   Number(document.getElementById('_rs_grp').value) || 0,
    recipient_emails:  rcpts,
    subject_tpl:       document.getElementById('_rs_subj').value,
    body_tpl:          document.getElementById('_rs_body').value,
    enabled:           document.getElementById('_rs_en').checked,
  };
  if(!payload.name){ alert('Name required'); return; }
  if(!payload.template_id){ alert('Pick a template'); return; }
  try{
    if(sid) await api('/api/reports/schedule/'+sid, {method:'PATCH', body: payload});
    else    await api('/api/reports/schedule',      {method:'POST',  body: payload});
    closeM('rptSchModal');
    _rptRenderSchedules();
  }catch(e){ alert('Save failed: '+(e.message||e)); }
}

async function _rptDeleteSchedule(sid, name){
  if(!confirm('Delete schedule "'+name+'"?')) return;
  try{ await api('/api/reports/schedule/'+sid, {method:'DELETE'}); _rptRenderSchedules(); }
  catch(e){ alert('Delete failed'); }
}

async function _rptRunSchedule(sid, name){
  if(!confirm('Run schedule "'+name+'" now? (Generates PDF and emails recipients.)')) return;
  try{
    const r = await api('/api/reports/schedule/'+sid+'/run', {method:'POST'});
    alert((r.ok?'Sent to ':'Saved but not sent: ')+(r.sent||0)+' recipient(s). Check History.');
  }catch(e){ alert('Run failed'); }
}

/* ── History ───────────────────────────────────────────────────── */

async function _rptRenderHistory(){
  const body = document.getElementById('rptBody');
  body.innerHTML = `<div class="muted" style="padding:20px">Loading…</div>`;
  try{
    const r = await api('/api/reports/history');
    const rows = r.history || [];
    if(!rows.length){
      body.innerHTML = `
        <div class="rpt-empty">
          <div class="rpt-empty-icon">🗂</div>
          <div class="rpt-empty-title">No reports generated yet</div>
          <div class="rpt-empty-hint">Hit "Run Now" on a template, or wait for a schedule to fire.</div>
        </div>`;
      return;
    }
    const trs = rows.map(h=>{
      const kb = Math.round((h.pdf_bytes||0)/1024);
      const statusPill = _rptStatusPill(h.delivery_status);
      return `
        <tr>
          <td class="muted small">${_fmtDate(h.generated_at)}</td>
          <td><strong>${esc(h.template_name||'(deleted)')}</strong></td>
          <td>${esc(h.kind||'')}</td>
          <td>${_fmtDate(h.period_start)} → ${_fmtDate(h.period_end)}</td>
          <td>${statusPill}</td>
          <td class="muted small">${kb} KB · ${(h.render_ms||0)} ms</td>
          <td style="text-align:right;white-space:nowrap">
            ${h.pdf_path?`<a class="rpt-btn-sm" href="/api/reports/history/${esc(h.id)}/download" target="_blank">⬇ PDF</a>`:''}
            <button class="rpt-btn-sm rpt-btn-danger" onclick="_rptDeleteHistory('${esc(h.id)}')">🗑</button>
          </td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <table class="rpt-table">
        <thead><tr><th>Generated</th><th>Template</th><th>Kind</th><th>Period</th><th>Delivery</th><th>Size</th><th></th></tr></thead>
        <tbody>${trs}</tbody>
      </table>`;
  }catch(e){
    body.innerHTML = `<div class="error" style="padding:20px">Failed to load history</div>`;
  }
}

function _rptStatusPill(s){
  if(s==='sent') return '<span class="pill up">sent</span>';
  if(s==='failed') return '<span class="pill crit">failed</span>';
  if(s==='skipped') return '<span class="pill warn">skipped</span>';
  if(s==='local_only') return '<span class="pill accent">local</span>';
  if(s==='pending') return '<span class="pill muted">pending</span>';
  return `<span class="muted small">${esc(s||'')}</span>`;
}

async function _rptDeleteHistory(hid){
  if(!confirm('Delete this report entry and its PDF?')) return;
  try{ await api('/api/reports/history/'+hid, {method:'DELETE'}); _rptRenderHistory(); }
  catch(e){ alert('Delete failed'); }
}

function _fmtDate(ts){
  if(!ts) return '';
  try{
    const d = new Date(Number(ts)*1000);
    return d.toLocaleString(undefined, {year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  }catch(_){ return ''; }
}
