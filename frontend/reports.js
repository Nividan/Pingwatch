/* ── reports.js — Reports tab UI ─────────────────────────────────────
   Sub-tabs: Templates / Schedules / History
   CRUD modals reuse the global .mo/.mbox modal pattern from forms-utils.
   Preview opens in a new tab; Run Now + Test Send hit POST /api/reports/*.
*/

let _rptTab = 'templates';   // 'templates' | 'schedules' | 'history'
let _rptBooted = false;

/* ── Custom-kind section catalogue ─────────────────────────────────
   Picker data. Each group lists its sections; items may have an `opt`
   block that renders a <select> inline with the checkbox for a per-
   section knob (Top N, days-ahead, etc). Section IDs must match the
   `{% if "id" in meta.sections %}` guards in reports/templates/custom.html.
*/
const _RPT_SECTIONS = [
  {label:'Availability & Uptime', items:[
    {id:'overall_uptime',     label:'Overall uptime headline'},
    {id:'availability_trend', label:'Availability trend chart'},
    {id:'per_device_uptime',  label:'Per-device uptime table'},
    {id:'top_worst_devices',  label:'Worst-performing devices',
      opt:{key:'top_worst_n', def:5, values:[[5,'Top 5'],[10,'Top 10'],[20,'Top 20']]}},
  ]},
  {label:'Incidents', items:[
    {id:'incident_summary',   label:'Incident summary + severity donut'},
    {id:'major_incidents',    label:'Major outages (clustered)',
      opt:{key:'major_min_devices', def:10, values:[[5,'≥ 5 devices'],[10,'≥ 10 devices'],[20,'≥ 20 devices']]}},
    {id:'incident_timeline',  label:'Incident timeline chart'},
    {id:'top_noisy_sensors',  label:'Noisiest sensors',
      opt:{key:'top_noisy_n', def:5, values:[[5,'Top 5'],[10,'Top 10'],[20,'Top 20']]}},
    {id:'incident_log',       label:'Incident log (outages)',
      opt:{key:'show_individual_events', def:false, type:'bool', sublabel:'+ raw events'}},
    {id:'sensor_config_issues', label:'Sensor configuration issues'},
    {id:'maint_windows',      label:'Maintenance windows'},
  ]},
  {label:'Performance', items:[
    {id:'latency_percentiles', label:'Latency percentiles (p50/p95/p99)',
      opt:{key:'latency_top_n', def:100, values:[[25,'Top 25'],[50,'Top 50'],[100,'Top 100']]}},
    {id:'snmp_traps',         label:'Top SNMP trap types',
      opt:{key:'top_traps_n', def:10, values:[[10,'Top 10'],[25,'Top 25'],[50,'Top 50']]}},
  ]},
  {label:'Inventory & Estate', items:[
    {id:'estate_overview',    label:'Estate overview (devices, users)'},
    {id:'device_inventory',   label:'Full device inventory'},
    {id:'ipam',               label:'IPAM subnet utilisation'},
  ]},
  {label:'Compliance & Security', items:[
    {id:'tls_expiring',       label:'TLS certificates expiring',
      opt:{key:'tls_days_ahead', def:90, values:[[30,'≤ 30 days'],[60,'≤ 60 days'],[90,'≤ 90 days']]}},
    {id:'licenses',           label:'Device license tracking'},
    {id:'backup_coverage',    label:'Config backup coverage'},
    {id:'audit_log',          label:'Recent admin activity',
      opt:{key:'audit_limit', def:50, values:[[25,'Last 25'],[50,'Last 50'],[100,'Last 100']]}},
  ]},
  {label:'Health', items:[
    {id:'device_health',      label:'Device health scores',
      opt:{key:'health_top_n', def:25, values:[[10,'Top 10'],[25,'Top 25'],[0,'All devices']]}},
  ]},
];

/* ── Presets that mirror the three fixed kinds ───────────────────
   Each preset lists the section IDs to tick. Option defaults come from
   _RPT_SECTIONS[].opt.def, so presets only need to declare the section
   set — knobs fall back to sensible defaults. */
const _RPT_PRESETS = {
  exec: ['overall_uptime','availability_trend','incident_summary',
         'top_worst_devices','major_incidents','top_noisy_sensors',
         'incident_timeline','maint_windows','device_health'],
  tech: ['overall_uptime','availability_trend','per_device_uptime',
         'latency_percentiles','snmp_traps','tls_expiring',
         'major_incidents','incident_log','sensor_config_issues','maint_windows'],
  inv:  ['estate_overview','backup_coverage','ipam','licenses','tls_expiring',
         'device_inventory','audit_log'],
};

function _rptBuildSectionsHtml(cfg){
  const picked  = new Set(cfg.sections && cfg.sections.length ? cfg.sections : _RPT_PRESETS.exec);
  const optsCfg = cfg.options || {};
  const groups  = _RPT_SECTIONS.map(g => {
    const items = g.items.map(it => {
      const checked = picked.has(it.id) ? 'checked' : '';
      let optHtml = '';
      if(it.opt && it.opt.type === 'bool'){
        const cur = (optsCfg[it.opt.key] === undefined) ? !!it.opt.def : !!optsCfg[it.opt.key];
        optHtml = `<label class="rpt-sec-bopt" onclick="event.stopPropagation()">
          <input type="checkbox" data-bopt="${esc(it.opt.key)}" ${cur?'checked':''}>
          <span>${esc(it.opt.sublabel || '')}</span>
        </label>`;
      } else if(it.opt){
        const cur = optsCfg[it.opt.key] ?? it.opt.def;
        const opts = it.opt.values.map(([v,label])=>`<option value="${v}" ${Number(cur)===v?'selected':''}>${esc(label)}</option>`).join('');
        optHtml = `<select class="rpt-sec-opt" data-opt="${esc(it.opt.key)}" onclick="event.stopPropagation()">${opts}</select>`;
      }
      return `<label class="chk-item">
        <input type="checkbox" data-sec="${esc(it.id)}" ${checked}>
        <span class="chk-lbl">${esc(it.label)}</span>
        ${optHtml}
      </label>`;
    }).join('');
    return `<div class="rpt-sec-group">
      <div class="rpt-sec-ghd">${esc(g.label)}</div>
      <div class="rpt-sec-grid">${items}</div>
    </div>`;
  }).join('');
  return `<div class="rpt-sections">${groups}</div>
    <div class="rpt-sec-actions">
      <button type="button" class="btn-s" onclick="_rptSecPreset('all')">Select all</button>
      <button type="button" class="btn-s" onclick="_rptSecPreset('none')">Clear</button>
      <button type="button" class="btn-s" onclick="_rptSecPreset('exec')">Executive preset</button>
      <button type="button" class="btn-s" onclick="_rptSecPreset('tech')">Technical preset</button>
      <button type="button" class="btn-s" onclick="_rptSecPreset('inv')">Inventory preset</button>
    </div>`;
}

function _rptSecPreset(name){
  const wrap = document.getElementById('_rt_sections_wrap');
  if(!wrap) return;
  let pick;
  if(name === 'all')       pick = _RPT_SECTIONS.flatMap(g => g.items.map(i => i.id));
  else if(name === 'none') pick = [];
  else                     pick = _RPT_PRESETS[name] || [];
  const set = new Set(pick);
  wrap.querySelectorAll('input[type=checkbox][data-sec]').forEach(cb => {
    cb.checked = set.has(cb.dataset.sec);
  });
}

function _rptToggleCustom(){
  const sel  = document.getElementById('_rt_kind');
  const wrap = document.getElementById('_rt_sections_wrap');
  const box  = document.querySelector('#rptTplModal .mbox');
  if(!sel || !wrap) return;
  const on = (sel.value === 'custom');
  wrap.style.display = on ? '' : 'none';
  if(box) box.style.maxWidth = on ? '780px' : '620px';
}

/* ── Modal helpers (replace native confirm/alert and show progress) ── */

function _rptConfirm(opts){
  // opts: {title, message, confirmLabel, danger, onConfirm}
  return new Promise(resolve=>{
    closeM('rptConfirmModal');
    const o = document.createElement('div');
    o.className = 'mo'; o.id = 'rptConfirmModal';
    _overlayClose(o, ()=>{ closeM('rptConfirmModal'); resolve(false); });
    const danger = !!opts.danger;
    o.innerHTML = `
      <div class="mbox" style="max-width:440px">
        <div class="mhd"><span>${esc(opts.title||'Confirm')}</span></div>
        <div class="mbdy">
          <div style="font-size:13px;color:var(--text);line-height:1.5">${esc(opts.message||'')}</div>
        </div>
        <div class="mft">
          <button class="btn-s" id="_rcm_no">Cancel</button>
          <button class="btn-p" id="_rcm_yes" style="${danger?'background:var(--down);border-color:var(--down)':''}">${esc(opts.confirmLabel||'OK')}</button>
        </div>
      </div>`;
    document.body.appendChild(o);
    const done = v => { closeM('rptConfirmModal'); resolve(v); };
    document.getElementById('_rcm_no').onclick  = ()=>done(false);
    document.getElementById('_rcm_yes').onclick = ()=>done(true);
    setTimeout(()=>document.getElementById('_rcm_yes')?.focus(), 50);
  });
}

function _rptNotify(opts){
  // opts: {title, message, kind: 'info'|'success'|'error'}
  closeM('rptNotifyModal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'rptNotifyModal';
  _overlayClose(o, ()=>closeM('rptNotifyModal'));
  const kind = opts.kind || 'info';
  const ico = kind==='success' ? '✓' : kind==='error' ? '✕' : 'ℹ';
  const color = kind==='success' ? 'var(--up)' : kind==='error' ? 'var(--down)' : 'var(--accent)';
  o.innerHTML = `
    <div class="mbox" style="max-width:440px">
      <div class="mhd"><span>${esc(opts.title||'Notice')}</span></div>
      <div class="mbdy">
        <div style="display:flex;gap:14px;align-items:flex-start">
          <div style="font-size:24px;color:${color};flex:none;line-height:1">${ico}</div>
          <div style="font-size:13px;color:var(--text);line-height:1.5">${esc(opts.message||'')}</div>
        </div>
      </div>
      <div class="mft">
        ${opts.secondary ? `<button class="btn-s" id="_rn_sec">${esc(opts.secondary.label)}</button>` : ''}
        <button class="btn-p" id="_rn_ok">OK</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  document.getElementById('_rn_ok').onclick = ()=>closeM('rptNotifyModal');
  if(opts.secondary){
    document.getElementById('_rn_sec').onclick = ()=>{
      closeM('rptNotifyModal');
      try{ opts.secondary.onClick && opts.secondary.onClick(); }catch(_){}
    };
  }
  setTimeout(()=>document.getElementById('_rn_ok')?.focus(), 50);
}

let _rptProgressInterval = null;
function _rptShowProgress(title, message){
  closeM('rptProgressModal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'rptProgressModal';
  // No overlay close — user must wait
  o.innerHTML = `
    <div class="mbox" style="max-width:420px">
      <div class="mhd"><span>${esc(title||'Working…')}</span></div>
      <div class="mbdy">
        <div style="display:flex;gap:16px;align-items:center">
          <div class="rpt-spinner" aria-hidden="true"></div>
          <div style="flex:1">
            <div id="_rpt_prog_msg" style="font-size:13px;color:var(--text);line-height:1.5">${esc(message||'Please wait.')}</div>
            <div id="_rpt_prog_elapsed" style="font-size:11px;color:var(--text3);margin-top:6px;font-family:'JetBrains Mono',monospace">0s</div>
          </div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(o);
  const started = Date.now();
  _rptProgressInterval = setInterval(()=>{
    const el = document.getElementById('_rpt_prog_elapsed');
    if(!el){ clearInterval(_rptProgressInterval); return; }
    const s = Math.floor((Date.now()-started)/1000);
    el.textContent = s < 60 ? `${s}s` : `${Math.floor(s/60)}m ${s%60}s`;
  }, 500);
}
function _rptHideProgress(){
  if(_rptProgressInterval){ clearInterval(_rptProgressInterval); _rptProgressInterval = null; }
  closeM('rptProgressModal');
}
function _rptSetProgressMessage(msg){
  const el = document.getElementById('_rpt_prog_msg');
  if(el) el.textContent = msg;
}

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
    const r = await api('GET', '/api/reports/templates');
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
            <button class="rpt-btn" onclick="_rptEditTemplate(null,'inventory')">+ Inventory &amp; Compliance</button>
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
      try{ const r = await api('GET', '/api/reports/template/'+tid); t = r.template || t; }catch(_){}
    }
    const cfg = t.config_json || {};
    const periodIsCustom = (cfg.period || '').startsWith('custom:');
    let customStart = '', customEnd = '';
    if(periodIsCustom){
      const parts = cfg.period.split(':');
      if(parts.length === 3){
        const toIso = s => { try { return new Date(Number(s)*1000).toISOString().slice(0,16); } catch(_){ return ''; } };
        customStart = toIso(parts[1]);
        customEnd   = toIso(parts[2]);
      }
    }
    const o = document.createElement('div');
    o.className = 'mo'; o.id = 'rptTplModal';
    _overlayClose(o, ()=>closeM('rptTplModal'));
    const _maxW = (t.kind === 'custom') ? '780px' : '620px';
    o.innerHTML = `
      <div class="mbox" style="max-width:${_maxW};max-height:90vh;display:flex;flex-direction:column">
        <div class="mhd"><span>${tid?'Edit':'New'} Report Template</span></div>
        <div class="mbdy" style="overflow-y:auto;flex:1">
          <div class="fr">
            <label class="fl">Name</label>
            <input type="text" id="_rt_name" value="${esc(t.name)}" placeholder="e.g. Monthly Exec Summary">
          </div>
          <div class="fr">
            <label class="fl">Kind</label>
            <select id="_rt_kind" onchange="_rptToggleCustom()">
              <option value="executive" ${t.kind==='executive'?'selected':''}>Executive Summary (high-level)</option>
              <option value="technical" ${t.kind==='technical'?'selected':''}>Technical / Operations (detailed)</option>
              <option value="inventory" ${t.kind==='inventory'?'selected':''}>Inventory &amp; Compliance (devices, backups, IPAM, licenses)</option>
              <option value="custom"    ${t.kind==='custom'?'selected':''}>Custom — pick your own sections</option>
            </select>
          </div>
          <div class="fr" id="_rt_sections_wrap" style="${t.kind==='custom'?'':'display:none'}">
            <label class="fl">Report sections</label>
            <div class="fh">Pick the blocks to include. Per-section drop-downs tune size / depth. Use a preset as a starting point and tweak.</div>
            ${_rptBuildSectionsHtml(cfg)}
          </div>
          <div class="fr">
            <label class="fl">Description (optional)</label>
            <input type="text" id="_rt_desc" value="${esc(t.description||'')}">
          </div>
          <div class="fr">
            <label class="fl">Default Period</label>
            <select id="_rt_period" onchange="_rptTogglePeriod()">
              <option value="last_7d"       ${cfg.period==='last_7d'?'selected':''}>Last 7 days</option>
              <option value="last_30d"      ${cfg.period==='last_30d'?'selected':''}>Last 30 days</option>
              <option value="last_90d"      ${cfg.period==='last_90d'?'selected':''}>Last 90 days</option>
              <option value="last_month"    ${(cfg.period==='last_month'||!cfg.period)?'selected':''}>Last calendar month</option>
              <option value="last_quarter"  ${cfg.period==='last_quarter'?'selected':''}>Last quarter</option>
              <option value="last_year"     ${cfg.period==='last_year'?'selected':''}>Last 365 days</option>
              <option value="month_to_date" ${cfg.period==='month_to_date'?'selected':''}>Month to date</option>
              <option value="custom"        ${periodIsCustom?'selected':''}>Custom range…</option>
            </select>
          </div>
          <div class="fr" id="_rt_custom_wrap" style="${periodIsCustom?'':'display:none'}">
            <label class="fl">Custom range</label>
            <div class="fgrid">
              <div>
                <div class="fh" style="margin-bottom:3px">Start</div>
                <input type="datetime-local" id="_rt_custom_start" value="${esc(customStart)}">
              </div>
              <div>
                <div class="fh" style="margin-bottom:3px">End</div>
                <input type="datetime-local" id="_rt_custom_end" value="${esc(customEnd)}">
              </div>
            </div>
            <div class="fh">Times use your browser's local timezone. Server converts to Unix epoch on save.</div>
          </div>
          <div class="fr">
            <label class="fl">Incident severity filter</label>
            <select id="_rt_severity">
              <option value="all"  ${(!cfg.severity_min || cfg.severity_min==='all')?'selected':''}>All incidents (default)</option>
              <option value="warn" ${cfg.severity_min==='warn'?'selected':''}>Warning and above</option>
              <option value="crit" ${cfg.severity_min==='crit'?'selected':''}>Critical / Down only</option>
            </select>
            <div class="fh">Trim out lower-severity noise. Executive reports usually want "Critical only"; technical/ops reports usually want "All".</div>
          </div>
          <div class="fr">
            <label class="fl">Cover title</label>
            <input type="text" id="_rt_title" value="${esc(cfg.title||'')}" placeholder="defaults to kind">
          </div>
          <div class="fr">
            <label class="fl">Subtitle (optional)</label>
            <input type="text" id="_rt_subtitle" value="${esc(cfg.subtitle||'')}">
          </div>
          <div class="fr" style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="_rt_csv" ${cfg.include_csv?'checked':''} style="width:auto">
            <label for="_rt_csv" style="color:var(--text);font-size:13px;cursor:pointer">Include CSV sidecar (attaches an Excel-friendly .csv alongside the PDF)</label>
          </div>
          <div class="fr">
            <label class="fl">PDF compliance level</label>
            <select id="_rt_pdfa">
              <option value=""          ${!cfg.pdfa_mode?'selected':''}>Standard PDF (default)</option>
              <option value="pdf/a-1b"  ${cfg.pdfa_mode==='pdf/a-1b'?'selected':''}>PDF/A-1b — long-term archival</option>
              <option value="pdf/a-2b"  ${cfg.pdfa_mode==='pdf/a-2b'?'selected':''}>PDF/A-2b — modern archival</option>
              <option value="pdf/a-3b"  ${cfg.pdfa_mode==='pdf/a-3b'?'selected':''}>PDF/A-3b — archival + embedded data</option>
            </select>
            <div class="fh">Pick an archival level if your compliance policy requires it. Adds ~15% to file size. Needs WeasyPrint ≥ 62 on the server — falls back to standard PDF otherwise.</div>
          </div>
        </div>
        <div class="mft">
          <button class="btn-s" onclick="closeM('rptTplModal')">Cancel</button>
          <button class="btn-p" onclick="_rptSaveTemplate('${esc(tid||'')}')">${tid?'Save':'Create'}</button>
        </div>
      </div>`;
    document.body.appendChild(o);
  })();
}

function _rptTogglePeriod(){
  const sel = document.getElementById('_rt_period');
  const wrap = document.getElementById('_rt_custom_wrap');
  if(!sel || !wrap) return;
  wrap.style.display = (sel.value === 'custom') ? '' : 'none';
}

async function _rptSaveTemplate(tid){
  let period = document.getElementById('_rt_period').value;
  if(period === 'custom'){
    const sRaw = document.getElementById('_rt_custom_start')?.value || '';
    const eRaw = document.getElementById('_rt_custom_end')?.value   || '';
    if(!sRaw || !eRaw){
      _rptNotify({title:'Custom range incomplete', message:'Pick both a start and end date/time.', kind:'error'});
      return;
    }
    const sTs = Math.floor(new Date(sRaw).getTime() / 1000);
    const eTs = Math.floor(new Date(eRaw).getTime() / 1000);
    if(!sTs || !eTs || eTs <= sTs){
      _rptNotify({title:'Invalid range', message:'End must be after start.', kind:'error'});
      return;
    }
    period = `custom:${sTs}:${eTs}`;
  }
  const payload = {
    name:        document.getElementById('_rt_name').value.trim(),
    kind:        document.getElementById('_rt_kind').value,
    description: document.getElementById('_rt_desc').value.trim(),
    config_json: {
      period:       period,
      title:        document.getElementById('_rt_title').value.trim(),
      subtitle:     document.getElementById('_rt_subtitle').value.trim(),
      severity_min: document.getElementById('_rt_severity')?.value || 'all',
      include_csv:  !!document.getElementById('_rt_csv')?.checked,
      pdfa_mode:    document.getElementById('_rt_pdfa')?.value || '',
    },
  };
  if(payload.kind === 'custom'){
    const wrap = document.getElementById('_rt_sections_wrap');
    const sections = wrap
      ? Array.from(wrap.querySelectorAll('input[type=checkbox][data-sec]:checked')).map(cb => cb.dataset.sec)
      : [];
    if(!sections.length){
      _rptNotify({title:'No sections picked', message:'Custom reports need at least one section ticked. Pick a preset or check individual blocks.', kind:'error'});
      return;
    }
    const options = {};
    if(wrap){
      wrap.querySelectorAll('select.rpt-sec-opt[data-opt]').forEach(sel => {
        const n = parseInt(sel.value, 10);
        if(!isNaN(n)) options[sel.dataset.opt] = n;
      });
      wrap.querySelectorAll('input[type=checkbox][data-bopt]').forEach(cb => {
        options[cb.dataset.bopt] = !!cb.checked;
      });
    }
    payload.config_json.sections = sections;
    payload.config_json.options  = options;
  }
  if(!payload.name){ _rptNotify({title:'Missing field', message:'Name is required.', kind:'error'}); return; }
  try{
    if(tid){
      await api('PATCH', '/api/reports/template/'+tid, payload);
    }else{
      await api('POST',  '/api/reports/template',      payload);
    }
    closeM('rptTplModal');
    _rptRenderTemplates();
  }catch(e){
    _rptNotify({title:'Save failed', message:(e.message||String(e)), kind:'error'});
  }
}

async function _rptDeleteTemplate(tid, name){
  const ok = await _rptConfirm({
    title:   'Delete template?',
    message: `Template "${name}" and all its schedules will be permanently removed. This cannot be undone.`,
    confirmLabel: 'Delete',
    danger: true,
  });
  if(!ok) return;
  try{
    await api('DELETE', '/api/reports/template/'+tid);
    _rptRenderTemplates();
  }catch(e){ _rptNotify({title:'Delete failed', message:(e.message||String(e)), kind:'error'}); }
}

function _rptPreview(tid){
  // Open a blank tab first (sync — avoids popup blocker), then navigate to POST-backed endpoint via a form
  const w = window.open('about:blank', '_blank');
  if(!w){ _rptNotify({title:'Popup blocked', message:'Allow popups for this site to preview reports in a new tab.', kind:'error'}); return; }
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
  const ok = await _rptConfirm({
    title:   'Run report now?',
    message: 'The report will render with current data and be saved to the server. You can download the PDF from the History tab when it is ready.',
    confirmLabel: 'Run Now',
  });
  if(!ok) return;
  _rptShowProgress('Generating report…', 'Collecting data, rendering charts, and producing the PDF. Large reports with many devices can take a minute or two.');
  try{
    const r = await api('POST', '/api/reports/run', {template_id: tid});
    _rptHideProgress();
    const kb = Math.round((r.pdf_bytes||0)/1024);
    const hasFile = !!(r.pdf_path && r.pdf_bytes);
    _rptNotify({
      title:   hasFile ? 'Report ready' : 'Report rendered',
      message: hasFile
        ? `PDF saved (${kb} KB). Open the History tab to download it.`
        : `Rendered ${kb} KB but the server could not save it to disk (check file permissions on backup/reports/). The entry still appears in History.`,
      kind:    hasFile ? 'success' : 'error',
      secondary: { label: 'Go to History', onClick: ()=>{ _rptSwitch('history'); } },
    });
    if(_rptTab==='history') _rptRenderHistory();
  }catch(e){
    _rptHideProgress();
    _rptNotify({title:'Run failed', message:(e.message||String(e)), kind:'error'});
  }
}

function _rptTestSend(tid){
  closeM('rptTestModal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'rptTestModal';
  _overlayClose(o, ()=>closeM('rptTestModal'));
  o.innerHTML = `
    <div class="mbox" style="max-width:440px">
      <div class="mhd"><span>Send Test Report</span></div>
      <div class="mbdy">
        <div class="fr">
          <label class="fl">Recipient email address</label>
          <input type="email" id="_rt_test_to" placeholder="you@example.com" autocomplete="email">
          <div class="fh">The report will render with current data and be sent via the SMTP server configured in Settings → Email.</div>
        </div>
        <div id="_rt_test_status" style="font-size:12px;color:var(--text3);min-height:16px"></div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('rptTestModal')">Cancel</button>
        <button class="btn-p" id="_rt_test_send" onclick="_rptDoTestSend('${esc(tid)}')">Send Test</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  setTimeout(()=>{ const i=document.getElementById('_rt_test_to'); if(i) i.focus(); }, 50);
}

async function _rptDoTestSend(tid){
  const to = (document.getElementById('_rt_test_to')?.value || '').trim();
  const status = document.getElementById('_rt_test_status');
  const btn = document.getElementById('_rt_test_send');
  if(!to){ status.textContent = 'Please enter an email address.'; status.style.color='var(--down)'; return; }
  if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to)){
    status.textContent = 'That does not look like a valid email address.'; status.style.color='var(--down)'; return;
  }
  btn.disabled = true; status.style.color='var(--text3)';
  status.textContent = 'Rendering and sending…';
  try{
    const r = await api('POST', '/api/reports/test-send', {template_id: tid, to});
    status.style.color = 'var(--up)';
    status.textContent = 'Sent — check your inbox ('+Math.round((r.bytes||0)/1024)+' KB attached).';
    setTimeout(()=>closeM('rptTestModal'), 1400);
  }catch(e){
    status.style.color = 'var(--down)';
    status.textContent = 'Failed: '+(e.message||e);
    btn.disabled = false;
  }
}

/* ── Schedules ─────────────────────────────────────────────────── */

async function _rptRenderSchedules(){
  const body = document.getElementById('rptBody');
  body.innerHTML = `<div class="muted" style="padding:20px">Loading…</div>`;
  try{
    const [a,b] = await Promise.all([api('GET', '/api/reports/schedules'), api('GET', '/api/reports/templates')]);
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
      try{ const r = await api('GET', '/api/reports/schedule/'+sid); s = r.schedule || s; }catch(_){}
    }
    let tpls = [], grps = [];
    try{
      const [a,g] = await Promise.all([api('GET', '/api/reports/templates'), api('GET', '/api/groups').catch(_=>({groups:[]}))]);
      tpls = a.templates || [];
      grps = g.groups || [];
    }catch(_){}
    const rcptList = (s.recipient_emails||[]).join(', ');
    const o = document.createElement('div');
    o.className = 'mo'; o.id = 'rptSchModal';
    _overlayClose(o, ()=>closeM('rptSchModal'));
    o.innerHTML = `
      <div class="mbox" style="max-width:620px">
        <div class="mhd"><span>${sid?'Edit':'New'} Schedule</span></div>
        <div class="mbdy">
          <div class="fr">
            <label class="fl">Name</label>
            <input type="text" id="_rs_name" value="${esc(s.name)}" placeholder="e.g. Monthly to Managers">
          </div>
          <div class="fr">
            <label class="fl">Template</label>
            <select id="_rs_tpl">
              ${tpls.map(t=>`<option value="${esc(t.id)}" ${t.id===s.template_id?'selected':''}>${esc(t.name)} · ${esc(t.kind)}</option>`).join('')}
            </select>
          </div>
          <div class="fgrid">
            <div class="fr">
              <label class="fl">Frequency</label>
              <select id="_rs_freq" onchange="_rptToggleFreq()">
                <option value="daily"     ${s.freq==='daily'?'selected':''}>Daily</option>
                <option value="weekly"    ${s.freq==='weekly'?'selected':''}>Weekly</option>
                <option value="monthly"   ${(s.freq==='monthly'||!s.freq)?'selected':''}>Monthly</option>
                <option value="quarterly" ${s.freq==='quarterly'?'selected':''}>Quarterly</option>
              </select>
            </div>
            <div class="fr">
              <label class="fl">Run at (HH:MM, server TZ)</label>
              <input type="text" id="_rs_time" value="${esc(s.time_str||'03:00')}">
            </div>
          </div>
          <div class="fgrid">
            <div class="fr" id="_rs_dow_wrap">
              <label class="fl">Days of week (1=Mon … 7=Sun)</label>
              <input type="text" id="_rs_dow" value="${esc(s.day_of_week||'1')}" placeholder="1,2,3,4,5">
            </div>
            <div class="fr" id="_rs_dom_wrap">
              <label class="fl">Day of month</label>
              <input type="number" min="1" max="28" id="_rs_dom" value="${s.day_of_month||1}">
            </div>
          </div>
          <div class="fr">
            <label class="fl">Reporting period</label>
            <select id="_rs_period">
              <option value="last_7d"       ${s.period==='last_7d'?'selected':''}>Last 7 days</option>
              <option value="last_30d"      ${s.period==='last_30d'?'selected':''}>Last 30 days</option>
              <option value="last_90d"      ${s.period==='last_90d'?'selected':''}>Last 90 days</option>
              <option value="last_month"    ${(s.period==='last_month'||!s.period)?'selected':''}>Last calendar month</option>
              <option value="last_quarter"  ${s.period==='last_quarter'?'selected':''}>Last quarter</option>
              <option value="last_year"     ${s.period==='last_year'?'selected':''}>Last 365 days</option>
              <option value="month_to_date" ${s.period==='month_to_date'?'selected':''}>Month to date</option>
            </select>
          </div>
          <div class="fr">
            <label class="fl">Recipient user group (optional)</label>
            <select id="_rs_grp">
              <option value="0">— none —</option>
              ${grps.map(g=>`<option value="${g.id}" ${g.id===s.recipient_group?'selected':''}>${esc(g.name)}</option>`).join('')}
            </select>
          </div>
          <div class="fr">
            <label class="fl">Extra email recipients (comma-separated)</label>
            <input type="text" id="_rs_rcpt" value="${esc(rcptList)}" placeholder="alice@example.com, bob@example.com">
          </div>
          <div class="fr">
            <label class="fl">Email subject</label>
            <input type="text" id="_rs_subj" value="${esc(s.subject_tpl||'[{company}] {title} — {period}')}">
            <div class="fh">Placeholders: {company}, {period}, {title}, {crit}, {warn}, {uptime}, {pdf_kb}</div>
          </div>
          <div class="fr">
            <label class="fl">Email body</label>
            <textarea id="_rs_body" style="height:80px">${esc(s.body_tpl||'')}</textarea>
            <div class="fh">Plain text. Placeholders OK. Leave blank for auto-generated body.</div>
          </div>
          <div class="fr" style="display:flex;align-items:center;gap:8px">
            <input type="checkbox" id="_rs_en" ${s.enabled?'checked':''} style="width:auto">
            <label for="_rs_en" style="color:var(--text);font-size:13px;cursor:pointer">Enabled</label>
          </div>
        </div>
        <div class="mft">
          <button class="btn-s" onclick="closeM('rptSchModal')">Cancel</button>
          <button class="btn-p" onclick="_rptSaveSchedule('${esc(sid||'')}')">${sid?'Save':'Create'}</button>
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
  if(!payload.name){      _rptNotify({title:'Missing field', message:'Name is required.',       kind:'error'}); return; }
  if(!payload.template_id){_rptNotify({title:'Missing field', message:'Pick a template first.', kind:'error'}); return; }
  try{
    if(sid) await api('PATCH', '/api/reports/schedule/'+sid, payload);
    else    await api('POST',  '/api/reports/schedule',      payload);
    closeM('rptSchModal');
    _rptRenderSchedules();
  }catch(e){ _rptNotify({title:'Save failed', message:(e.message||String(e)), kind:'error'}); }
}

async function _rptDeleteSchedule(sid, name){
  const ok = await _rptConfirm({
    title:'Delete schedule?',
    message:`Schedule "${name}" will stop firing. Existing history rows are kept.`,
    confirmLabel:'Delete', danger:true,
  });
  if(!ok) return;
  try{ await api('DELETE', '/api/reports/schedule/'+sid); _rptRenderSchedules(); }
  catch(e){ _rptNotify({title:'Delete failed', message:(e.message||String(e)), kind:'error'}); }
}

async function _rptRunSchedule(sid, name){
  const ok = await _rptConfirm({
    title:'Run schedule now?',
    message:`Schedule "${name}" will render its report and email all configured recipients immediately.`,
    confirmLabel:'Run & Send',
  });
  if(!ok) return;
  _rptShowProgress('Running schedule…', 'Rendering report and delivering to recipients.');
  try{
    const r = await api('POST', '/api/reports/schedule/'+sid+'/run');
    _rptHideProgress();
    _rptNotify({
      title: r.ok ? 'Schedule sent' : 'Saved but not sent',
      message: r.ok
        ? `Emailed ${r.sent||0} recipient(s). An entry has been added to History.`
        : `Report was generated but not delivered: ${r.error||'no recipients configured'}.`,
      kind: r.ok ? 'success' : 'error',
      secondary: { label:'Go to History', onClick: ()=>_rptSwitch('history') },
    });
  }catch(e){
    _rptHideProgress();
    _rptNotify({title:'Run failed', message:(e.message||String(e)), kind:'error'});
  }
}

/* ── History ───────────────────────────────────────────────────── */

async function _rptRenderHistory(){
  const body = document.getElementById('rptBody');
  body.innerHTML = `<div class="muted" style="padding:20px">Loading…</div>`;
  try{
    const r = await api('GET', '/api/reports/history');
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
      const kb  = Math.round((h.pdf_bytes||0)/1024);
      const ckb = Math.round((h.csv_bytes||0)/1024);
      const statusPill = _rptStatusPill(h.delivery_status);
      const ridTitle = h.pdf_sha256 ? `SHA-256: ${h.pdf_sha256}` : '';
      const ridBadge = h.report_id
        ? `<span class="rpt-fingerprint" title="${esc(ridTitle)}">${esc(h.report_id)}</span>`
        : '';
      return `
        <tr>
          <td style="width:28px;text-align:center">
            <input type="checkbox" class="rpt-hist-cb" data-hid="${esc(h.id)}" onchange="_rptHistSelChanged()">
          </td>
          <td class="muted small">${_fmtDate(h.generated_at)}</td>
          <td>
            <strong>${esc(h.template_name||'(deleted)')}</strong>
            ${ridBadge ? `<div style="margin-top:2px">${ridBadge}</div>` : ''}
          </td>
          <td>${esc(h.kind||'')}</td>
          <td>${_fmtDate(h.period_start)} → ${_fmtDate(h.period_end)}</td>
          <td>${statusPill}</td>
          <td class="muted small">${kb} KB · ${(h.render_ms||0)} ms${ckb?`<br>+${ckb} KB CSV`:''}</td>
          <td style="text-align:right;white-space:nowrap">
            ${(h.pdf_path && h.pdf_bytes)?`<a class="rpt-btn-sm" href="/api/reports/history/${esc(h.id)}/download" target="_blank" title="Download PDF">⬇ PDF</a>`:'<span class="muted small">no file</span>'}
            ${(h.csv_path && h.csv_bytes)?`<a class="rpt-btn-sm" href="/api/reports/history/${esc(h.id)}/csv" target="_blank" title="Download CSV sidecar">⬇ CSV</a>`:''}
            <button class="rpt-btn-sm rpt-btn-danger" onclick="_rptDeleteHistory('${esc(h.id)}')">🗑</button>
          </td>
        </tr>`;
    }).join('');
    body.innerHTML = `
      <div id="rptHistActions" class="rpt-hist-actions" style="display:none">
        <span class="muted small" id="rptHistSelCount">0 selected</span>
        <button class="rpt-btn rpt-btn-danger" onclick="_rptDeleteHistoryBulk()">Delete selected</button>
        <button class="rpt-btn-sm" onclick="_rptHistClearSel()">Clear</button>
      </div>
      <table class="rpt-table">
        <thead>
          <tr>
            <th style="width:28px;text-align:center">
              <input type="checkbox" id="rptHistSelAll" onchange="_rptHistSelAll(this.checked)" title="Select all">
            </th>
            <th>Generated</th><th>Template</th><th>Kind</th><th>Period</th><th>Delivery</th><th>Size</th><th></th>
          </tr>
        </thead>
        <tbody>${trs}</tbody>
      </table>`;
  }catch(e){
    body.innerHTML = `<div class="error" style="padding:20px">Failed to load history</div>`;
  }
}


/* ── History: multi-select helpers ─────────────────────────────── */

function _rptHistSelAll(checked){
  document.querySelectorAll('.rpt-hist-cb').forEach(cb=>{ cb.checked = !!checked; });
  _rptHistSelChanged();
}

function _rptHistClearSel(){
  const all = document.getElementById('rptHistSelAll');
  if(all) all.checked = false;
  document.querySelectorAll('.rpt-hist-cb').forEach(cb=>{ cb.checked = false; });
  _rptHistSelChanged();
}

function _rptHistSelChanged(){
  const cbs = document.querySelectorAll('.rpt-hist-cb');
  const checked = Array.from(cbs).filter(cb=>cb.checked);
  const n = checked.length;
  const bar = document.getElementById('rptHistActions');
  const cnt = document.getElementById('rptHistSelCount');
  if(bar) bar.style.display = n ? 'flex' : 'none';
  if(cnt) cnt.textContent = `${n} selected`;
  // Sync the header "select all" tri-state indicator
  const all = document.getElementById('rptHistSelAll');
  if(all){
    all.checked       = n > 0 && n === cbs.length;
    all.indeterminate = n > 0 && n < cbs.length;
  }
}

async function _rptDeleteHistoryBulk(){
  const ids = Array.from(document.querySelectorAll('.rpt-hist-cb'))
    .filter(cb=>cb.checked)
    .map(cb=>cb.getAttribute('data-hid'))
    .filter(Boolean);
  if(!ids.length) return;
  const ok = await _rptConfirm({
    title: `Delete ${ids.length} report${ids.length===1?'':'s'}?`,
    message: 'Removes the history entries and deletes their PDF/CSV files from the server. This cannot be undone.',
    confirmLabel: `Delete ${ids.length}`,
    danger: true,
  });
  if(!ok) return;
  try{
    const r = await api('POST', '/api/reports/history/bulk-delete', { ids });
    _rptNotify({
      title: 'Reports deleted',
      message: `Deleted ${r.deleted||0}${r.missing?` · ${r.missing} already gone`:''}.`,
      kind: 'success',
    });
    _rptRenderHistory();
  }catch(e){
    _rptNotify({title:'Bulk delete failed', message:(e.message||String(e)), kind:'error'});
  }
}

function _rptStatusPill(s){
  if(s==='sent') return '<span class="pill up">sent</span>';
  if(s==='failed') return '<span class="pill crit">failed</span>';
  if(s==='skipped') return '<span class="pill warn">skipped</span>';
  if(s==='local_only') return '<span class="pill accent">local</span>';
  if(s==='render_only') return '<span class="pill warn" title="Rendered but could not save to disk">render only</span>';
  if(s==='pending') return '<span class="pill muted">pending</span>';
  return `<span class="muted small">${esc(s||'')}</span>`;
}

async function _rptDeleteHistory(hid){
  const ok = await _rptConfirm({
    title:'Delete report?',
    message:'This removes the history entry and deletes the PDF file from the server.',
    confirmLabel:'Delete', danger:true,
  });
  if(!ok) return;
  try{ await api('DELETE', '/api/reports/history/'+hid); _rptRenderHistory(); }
  catch(e){ _rptNotify({title:'Delete failed', message:(e.message||String(e)), kind:'error'}); }
}

function _fmtDate(ts){
  if(!ts) return '';
  try{
    const d = new Date(Number(ts)*1000);
    return d.toLocaleString(undefined, {year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit'});
  }catch(_){ return ''; }
}
