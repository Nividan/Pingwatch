// ── BULK DEVICE IMPORT MODAL ──────────────────────────────────────────────
// Step 1: format picker + upload (text or XLSX file)
// Step 1.5: SolarWinds column-mapper (only when format=solarwinds)
// Step 2: review/preview with mode selector + per-row toggle
// Step 3: summary

const _imp = {
  format: 'json',          // json|csv|prtg|zabbix|solarwinds
  text:   '',              // text payload (json/csv/prtg/zabbix)
  b64:    '',              // base64 payload (xlsx)
  filename: '',
  // SolarWinds inspection result
  swHeaders: [],
  swSample:  [],
  swDetectedFmt: 'csv',
  columnMap: {},           // { sourceHeader: targetField | "(skip)" }
  // Parse result
  devices: [],             // canonical from /parse, mutated for review (selected flag)
  errors:  [],
  mappingReport: {},
  orphans: [],
  orphanCount: 0,
  // Review state
  mode: 'add_update',      // add_only|add_update|replace
  selected: new Set(),     // indexes of devices to apply
  defaultGroup: 'Imported',
  // Apply result
  applyResult: null,
  step: 1,
};

function openBulkImport(){
  closeM('mimp');
  Object.assign(_imp, {
    format: 'json', text: '', b64: '', filename: '',
    swHeaders: [], swSample: [], swDetectedFmt: 'csv', columnMap: {},
    devices: [], errors: [], mappingReport: {}, orphans: [], orphanCount: 0,
    mode: 'add_update', selected: new Set(), defaultGroup: 'Imported',
    applyResult: null, step: 1,
  });
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'mimp';
  _overlayClose(o, ()=>closeM('mimp'));
  o.innerHTML = `
    <div class="mbox mbox-discover">
      <div class="mhd">
        <div class="mttl">📥 Import Devices from File</div>
        <button class="mclose" onclick="closeM('mimp')">✕</button>
      </div>
      <div class="mbdy" id="imp-bdy"></div>
      <div class="mft" id="imp-ft"></div>
    </div>`;
  document.body.appendChild(o);
  _impRenderUpload();
}

// ── Step 1: format picker + upload ─────────────────────────────────
function _impRenderUpload(){
  _imp.step = 1;
  const bdy = document.getElementById('imp-bdy');
  const ft  = document.getElementById('imp-ft');
  if(!bdy||!ft) return;

  const fmts = [
    {k:'json',       label:'PingWatch JSON',  hint:'Native format — round-trip safe'},
    {k:'csv',        label:'CSV',             hint:'name, host, group, sensors columns'},
    {k:'prtg',       label:'PRTG XML',        hint:'PRTG config / device template export'},
    {k:'solarwinds', label:'SolarWinds',      hint:'SWQL CSV / XLSX export — choose columns next'},
    {k:'zabbix',     label:'Zabbix XML',      hint:'<zabbix_export><hosts> template export'},
  ];
  const tabs = fmts.map(f => `
    <button class="imp-tab ${_imp.format===f.k?'sel':''}" onclick="_impPickFormat('${f.k}')">
      ${esc(f.label)}
    </button>`).join('');
  const hint = fmts.find(f=>f.k===_imp.format)?.hint || '';

  const isXlsxCapable = _imp.format === 'solarwinds';
  const accept = isXlsxCapable ? '.csv,.xlsx,.xlsm,.txt' :
                 _imp.format === 'json' ? '.json,.txt' :
                 _imp.format === 'csv'  ? '.csv,.txt'  :
                 '.xml,.txt';

  bdy.innerHTML = `
    <div class="imp-tabs">${tabs}</div>
    <div class="imp-hint">${esc(hint)}</div>
    <div class="fr">
      <label class="fl">Upload file</label>
      <input type="file" id="imp-file" accept="${accept}" onchange="_impFileChosen(event)"/>
      ${_imp.filename ? `<div class="imp-info-line">Selected: <strong>${esc(_imp.filename)}</strong></div>` : ''}
    </div>
    <div class="fr">
      <label class="fl">…or paste contents (text formats only)</label>
      <textarea id="imp-text" rows="8" placeholder="${isXlsxCapable ? 'XLSX requires file upload (binary). Paste CSV here or upload .xlsx above.' : 'Paste file contents here'}" oninput="_impTextChanged(this.value)">${esc(_imp.text)}</textarea>
    </div>
  `;
  const canNext = !!(_imp.text || _imp.b64);
  const nextLabel = _imp.format === 'solarwinds' ? 'Inspect ▶' : 'Parse ▶';
  ft.innerHTML = `
    <button class="btn-s" onclick="closeM('mimp')">Cancel</button>
    <button class="btn-p" id="imp-next-btn" ${canNext?'':'disabled'} onclick="_impNextFromUpload()">${nextLabel}</button>
  `;
}

function _impPickFormat(k){
  _imp.format = k;
  _imp.text = ''; _imp.b64 = ''; _imp.filename = '';
  _imp.columnMap = {}; _imp.swHeaders = []; _imp.swSample = [];
  _impRenderUpload();
}

function _impTextChanged(v){
  _imp.text = v || '';
  _imp.b64 = '';   // text wins over file
  _imp.filename = '';
  const btn = document.getElementById('imp-next-btn');
  if(btn) btn.disabled = !(_imp.text || _imp.b64);
}

function _impFileChosen(ev){
  const f = ev.target.files && ev.target.files[0];
  if(!f) return;
  _imp.filename = f.name;
  const isXlsx = /\.(xlsx|xlsm)$/i.test(f.name);
  const reader = new FileReader();
  if(isXlsx){
    reader.onload = e => {
      const buf = new Uint8Array(e.target.result);
      // Base64-encode in chunks to avoid call-stack overflow on large files.
      let bin = '';
      const CHUNK = 0x8000;
      for(let i=0;i<buf.length;i+=CHUNK){
        bin += String.fromCharCode.apply(null, buf.subarray(i, i+CHUNK));
      }
      _imp.b64 = btoa(bin);
      _imp.text = '';
      const ta = document.getElementById('imp-text');
      if(ta){ ta.value = ''; ta.disabled = true; ta.placeholder = 'Binary file loaded — preview disabled'; }
      const btn = document.getElementById('imp-next-btn');
      if(btn) btn.disabled = false;
      _impRefreshFilenameLine();
    };
    reader.readAsArrayBuffer(f);
  } else {
    reader.onload = e => {
      _imp.text = e.target.result || '';
      _imp.b64 = '';
      const ta = document.getElementById('imp-text');
      if(ta){ ta.value = _imp.text; }
      const btn = document.getElementById('imp-next-btn');
      if(btn) btn.disabled = !_imp.text;
      _impRefreshFilenameLine();
    };
    reader.readAsText(f);
  }
}

function _impRefreshFilenameLine(){
  // Re-render to update the filename display + textarea state.
  // Avoid full re-render to keep the text contents (already loaded into _imp.text).
  const bdy = document.getElementById('imp-bdy');
  if(!bdy) return;
  const lines = bdy.querySelectorAll('.imp-info-line');
  lines.forEach(l => l.remove());
  if(_imp.filename){
    const fileInput = document.getElementById('imp-file');
    if(fileInput){
      const div = document.createElement('div');
      div.className = 'imp-info-line';
      div.innerHTML = `Selected: <strong>${esc(_imp.filename)}</strong>`;
      fileInput.insertAdjacentElement('afterend', div);
    }
  }
}

async function _impNextFromUpload(){
  const btn = document.getElementById('imp-next-btn');
  if(btn){ btn.disabled = true; btn.textContent = 'Working…'; }
  if(_imp.format === 'solarwinds'){
    await _impRunSwInspect();
  } else {
    await _impRunParse();
  }
}

// ── Step 1.5: SolarWinds column-mapper ─────────────────────────────
async function _impRunSwInspect(){
  const body = { filename: _imp.filename || 'export.csv' };
  if(_imp.text) body.text = _imp.text;
  else if(_imp.b64) body.b64 = _imp.b64;
  let r;
  try{
    r = await api('POST', '/api/import/sw/inspect', body);
  }catch(e){
    alert('Inspect failed: ' + (e?.message || e));
    _impRenderUpload();
    return;
  }
  if(!r || r.error){
    alert('Inspect failed: ' + (r && r.error || 'unknown'));
    _impRenderUpload();
    return;
  }
  _imp.swHeaders     = r.headers || [];
  _imp.swSample      = r.sample_rows || [];
  _imp.swDetectedFmt = r.detected_format || 'csv';
  // Pre-populate column map heuristically (case-insensitive header match).
  _imp.columnMap = {};
  const TARGET_HINTS = {
    'name':'name','caption':'name','nodename':'name','displayname':'name','hostname':'name',
    'host':'host','ip':'host','ipaddress':'host','address':'host','ip_address':'host',
    'group':'group','location':'group','department':'group','site':'group',
    'community':'snmp_community_default','snmp_community':'snmp_community_default',
    'nodeid':'external_id','id':'external_id',
  };
  _imp.swHeaders.forEach(h => {
    const norm = (h||'').toLowerCase().replace(/[ \-_.]/g,'');
    const t = TARGET_HINTS[norm];
    if(t) _imp.columnMap[h] = t;
  });
  _impRenderColumnMapper();
}

function _impRenderColumnMapper(){
  _imp.step = 1.5;
  const bdy = document.getElementById('imp-bdy');
  const ft  = document.getElementById('imp-ft');
  if(!bdy||!ft) return;
  const TARGETS = [
    ['(skip)',                 'Skip this column'],
    ['name',                   'Device name'],
    ['host',                   'Host / IP address'],
    ['group',                  'Device group'],
    ['external_id',            'External ID (unique key)'],
    ['snmp_community_default', 'Default SNMP community'],
    ['snmp_version_default',   'Default SNMP version'],
    ['webhook_url',            'Webhook URL'],
  ];
  const rowsHtml = _imp.swHeaders.map((h,i) => {
    const cur = _imp.columnMap[h] || '(skip)';
    const opts = TARGETS.map(([k,lbl]) =>
      `<option value="${esc(k)}" ${k===cur?'selected':''}>${esc(lbl)}</option>`
    ).join('');
    const sample = _imp.swSample.slice(0,3).map(r =>
      `<span class="imp-cm-sample">${esc(String(r[i]==null?'':r[i]).slice(0,40))}</span>`
    ).join('');
    return `
      <tr>
        <td><strong>${esc(h)}</strong><div class="imp-cm-samples">${sample}</div></td>
        <td>
          <select onchange="_impSetColMap('${esc(h).replace(/'/g,'&#39;')}', this.value)">${opts}</select>
        </td>
      </tr>`;
  }).join('');
  bdy.innerHTML = `
    <div class="imp-step-hd">Step 2 of 3 — Map your columns to PingWatch fields</div>
    <div class="imp-cm-wrap">
      <table class="imp-cm-tbl">
        <thead><tr><th>Source column (sample values)</th><th>Maps to</th></tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
    <div class="imp-info-line">Required: at least one column mapped to <strong>name</strong> and one to <strong>host</strong>.</div>
  `;
  ft.innerHTML = `
    <button class="btn-s" onclick="_impRenderUpload()">◀ Back</button>
    <button class="btn-p" onclick="_impRunParse()">Preview ▶</button>
  `;
}

function _impSetColMap(header, target){
  if(target === '(skip)') delete _imp.columnMap[header];
  else _imp.columnMap[header] = target;
}

// ── Step 2: parse + render review ──────────────────────────────────
async function _impRunParse(){
  const body = { format: _imp.format, filename: _imp.filename };
  if(_imp.text) body.text = _imp.text;
  else if(_imp.b64) body.b64 = _imp.b64;
  if(_imp.format === 'solarwinds') body.column_map = _imp.columnMap;
  let r;
  try{
    r = await api('POST', '/api/import/parse', body);
  }catch(e){
    alert('Parse failed: ' + (e?.message || e));
    if(_imp.format === 'solarwinds') _impRenderColumnMapper(); else _impRenderUpload();
    return;
  }
  if(!r || r.error){
    alert('Parse failed: ' + (r && r.error || 'unknown'));
    if(_imp.format === 'solarwinds') _impRenderColumnMapper(); else _impRenderUpload();
    return;
  }
  _imp.devices       = r.devices || [];
  _imp.errors        = r.errors || [];
  _imp.mappingReport = r.mapping_report || {};
  _imp.orphans       = r.orphans || [];
  _imp.orphanCount   = r.orphan_count || 0;
  _imp.selected = new Set(_imp.devices.map((_,i)=>i));   // all selected by default
  _impRenderReview();
}

function _impRenderReview(){
  _imp.step = 2;
  const bdy = document.getElementById('imp-bdy');
  const ft  = document.getElementById('imp-ft');
  if(!bdy||!ft) return;

  const total   = _imp.devices.length;
  const newCt   = _imp.devices.filter(d=>d.match_status==='new').length;
  const updCt   = _imp.devices.filter(d=>d.match_status==='update').length;
  const errCt   = _imp.errors.length;
  const mp      = _imp.mappingReport || {};
  const skipped = mp.sensors_skipped || [];

  const skippedHtml = skipped.length ? `
    <div class="imp-skip-box">
      <div class="imp-skip-hd">Sensor mapping report:
        <strong>${mp.sensors_mapped||0} of ${mp.sensors_total||0}</strong> sensors mapped</div>
      ${skipped.map(s => `
        <div class="imp-skip-row">
          <span class="imp-skip-count">${s.count}×</span>
          <span class="imp-skip-type">${esc(s.source_type||'?')}</span>
          <span class="imp-skip-reason">${esc(s.reason||'')}</span>
        </div>`).join('')}
    </div>` : '';

  const rowsHtml = _imp.devices.map((d, i) => {
    const sel = _imp.selected.has(i) ? 'checked' : '';
    const sensors = (d.sensors||[]).map(s => esc(s.stype||'?')).join(', ') || '—';
    const status = d.match_status === 'update' ? `
      <span class="imp-chip imp-chip-update" title="Will update existing device${
        d.match_diff && Object.keys(d.match_diff).length
          ? ': ' + Object.entries(d.match_diff).map(([k,v])=>k+(Array.isArray(v)?': '+v[0]+' → '+v[1]:'')).join('; ')
          : ''
      }">UPDATE${d.match_diff && Object.keys(d.match_diff).length ? ': '+Object.keys(d.match_diff).slice(0,2).join(',') : ''}</span>` :
      `<span class="imp-chip imp-chip-new">NEW</span>`;
    return `
      <tr>
        <td><input type="checkbox" ${sel} onchange="_impToggle(${i}, this.checked)"/></td>
        <td>${esc(d.name||'')}</td>
        <td>${esc(d.host||'')}</td>
        <td>${esc(d.group||'')}</td>
        <td class="imp-sensors-cell">${sensors}</td>
        <td>${status}</td>
      </tr>`;
  }).join('');

  const errorsHtml = errCt ? `
    <div class="imp-err-box">
      <div class="imp-err-hd">${errCt} parse error${errCt===1?'':'s'} — these rows skipped:</div>
      ${_imp.errors.slice(0,10).map(e =>
        `<div class="imp-err-row">row ${e.row}: ${esc(e.reason||'')}</div>`).join('')}
      ${errCt > 10 ? `<div class="imp-err-row">… and ${errCt-10} more</div>` : ''}
    </div>` : '';

  const orphHtml = (_imp.mode === 'replace' && _imp.orphanCount) ? `
    <div class="imp-orph-box">
      <div class="imp-orph-hd">⚠ ${_imp.orphanCount} existing device${_imp.orphanCount===1?'':'s'} not in this file —
        will be <strong>deleted</strong> in replace mode:</div>
      ${_imp.orphans.slice(0,10).map(o =>
        `<div class="imp-orph-row">${esc(o.name||'')} (${esc(o.host||'')})</div>`).join('')}
      ${_imp.orphanCount > 10 ? `<div class="imp-orph-row">… and ${_imp.orphanCount-10} more</div>` : ''}
    </div>` : '';

  bdy.innerHTML = `
    <div class="imp-step-hd">Preview — ${total} device${total===1?'':'s'} parsed
      <span class="imp-counts">(${newCt} new, ${updCt} update${updCt===1?'':'s'}, ${errCt} error${errCt===1?'':'s'})</span>
    </div>
    ${skippedHtml}
    ${errorsHtml}
    <details class="imp-help">
      <summary>❓ How matching &amp; modes work</summary>
      <div class="imp-help-body">
        <p><strong>Match order</strong> — for each row, PingWatch looks for an existing device by
          <code>external_id</code> first (exact), then by <code>host</code> (case-insensitive).
          The row is tagged <span class="imp-chip imp-chip-new">NEW</span> if no match,
          <span class="imp-chip imp-chip-update">UPDATE</span> if matched. Device <em>name</em> is never
          used for matching — duplicate names are allowed.</p>
        <p><strong>Mode behavior on matched rows:</strong></p>
        <ul>
          <li><strong>Add only</strong> — skip matched rows (reported as <em>already monitored</em>). New devices created.</li>
          <li><strong>Add + update</strong> <em>(default)</em> — update matched devices in place:
            name/group/defaults overwritten (empty file values leave existing field alone);
            sensors merged by name (new ones added, matching ones updated, manually-added sensors preserved — never deleted).</li>
          <li><strong>Replace</strong> — same as Add + update, PLUS any existing device <em>not</em> in this file is deleted (confirmation required).</li>
        </ul>
        <p><strong>Tip</strong> — set an <code>external_id</code> in your file (e.g. <code>"my-cmdb:db01"</code>) so re-imports survive hostname or IP changes.</p>
      </div>
    </details>
    <div class="imp-mode-row">
      <span>Mode:</span>
      <label class="cb-row"><input type="radio" name="imp-mode" value="add_only" ${_imp.mode==='add_only'?'checked':''} onchange="_impSetMode('add_only')"/> Add only</label>
      <label class="cb-row"><input type="radio" name="imp-mode" value="add_update" ${_imp.mode==='add_update'?'checked':''} onchange="_impSetMode('add_update')"/> Add + update</label>
      <label class="cb-row"><input type="radio" name="imp-mode" value="replace" ${_imp.mode==='replace'?'checked':''} onchange="_impSetMode('replace')"/> Replace (delete orphans)</label>
    </div>
    <div class="fr" style="margin-top:8px">
      <label class="fl">Default group (used when row has no group)</label>
      <input type="text" id="imp-default-group" value="${esc(_imp.defaultGroup)}" oninput="_imp.defaultGroup=this.value"/>
    </div>
    ${orphHtml}
    <div class="imp-tbl-wrap">
      <table class="imp-tbl">
        <thead>
          <tr>
            <th><input type="checkbox" ${_imp.selected.size===_imp.devices.length?'checked':''} onchange="_impSelectAll(this.checked)"/></th>
            <th>Name</th><th>Host</th><th>Group</th><th>Sensors</th><th>Status</th>
          </tr>
        </thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    </div>
  `;
  const sel = _imp.selected.size;
  const applyLabel = _imp.mode === 'replace'
    ? `Apply (${sel} devices, may delete ${_imp.orphanCount}) ▶`
    : `Apply (${sel} device${sel===1?'':'s'}) ▶`;
  ft.innerHTML = `
    <button class="btn-s" onclick="closeM('mimp')">Cancel</button>
    <button class="btn-s" onclick="${_imp.format==='solarwinds'?'_impRenderColumnMapper()':'_impRenderUpload()'}">◀ Back</button>
    <button class="btn-p" id="imp-apply-btn" ${sel?'':'disabled'} onclick="_impApply()">${applyLabel}</button>
  `;
}

function _impToggle(i, on){
  if(on) _imp.selected.add(i); else _imp.selected.delete(i);
  const btn = document.getElementById('imp-apply-btn');
  if(btn){
    btn.disabled = !_imp.selected.size;
    const sel = _imp.selected.size;
    btn.textContent = _imp.mode === 'replace'
      ? `Apply (${sel} devices, may delete ${_imp.orphanCount}) ▶`
      : `Apply (${sel} device${sel===1?'':'s'}) ▶`;
  }
}
function _impSelectAll(on){
  _imp.selected = on
    ? new Set(_imp.devices.map((_,i)=>i))
    : new Set();
  _impRenderReview();
}
function _impSetMode(m){
  _imp.mode = m;
  _impRenderReview();
}

// ── Step 3: apply ──────────────────────────────────────────────────
async function _impApply(){
  if(!_imp.selected.size) return;
  if(_imp.mode === 'replace' && _imp.orphanCount){
    const ok = confirm(`This will DELETE ${_imp.orphanCount} existing device(s) not in the file. Continue?`);
    if(!ok) return;
  }
  const btn = document.getElementById('imp-apply-btn');
  if(btn){ btn.disabled = true; btn.textContent = 'Applying…'; }

  // Strip preview-only fields the backend doesn't need.
  const items = Array.from(_imp.selected).sort((a,b)=>a-b).map(i => {
    const d = _imp.devices[i];
    const out = {
      external_id: d.external_id,
      name: d.name, host: d.host, group: d.group || _imp.defaultGroup,
      sensors: d.sensors || [],
    };
    ['snmp_community_default','snmp_version_default','webhook_url'].forEach(k => {
      if(d[k]) out[k] = d[k];
    });
    return out;
  });

  let r;
  try{
    r = await api('POST', '/api/import/apply', {
      devices: items,
      mode:    _imp.mode,
      format:  _imp.format,
      default_group: _imp.defaultGroup,
    });
  }catch(e){
    alert('Apply failed: ' + (e?.message || e));
    if(btn){ btn.disabled = false; btn.textContent = 'Apply ▶'; }
    return;
  }
  if(!r || r.error){
    alert('Apply failed: ' + (r && r.error || 'unknown'));
    if(btn){ btn.disabled = false; btn.textContent = 'Apply ▶'; }
    return;
  }
  _imp.applyResult = r;
  _impRenderSummary();
  // Refresh device list in the background.
  if(typeof loadDevices === 'function') setTimeout(loadDevices, 500);
}

function _impRenderSummary(){
  _imp.step = 3;
  const bdy = document.getElementById('imp-bdy');
  const ft  = document.getElementById('imp-ft');
  const r   = _imp.applyResult || {};
  const created = r.created || [];
  const updated = r.updated || [];
  const deleted = r.deleted || [];
  const skipped = r.skipped || [];
  const errors  = r.errors  || [];

  const block = (title, items, color) => items.length ? `
    <div class="imp-sum-block">
      <div class="imp-sum-hd" style="color:${color}">${esc(title)} (${items.length})</div>
      <div class="imp-sum-body">
        ${items.slice(0,15).map(it =>
          `<div>${esc(it.name||it.host||it.error||JSON.stringify(it))}${
            it.host ? ` <span class="imp-sum-host">(${esc(it.host)})</span>` : ''}</div>`
        ).join('')}
        ${items.length > 15 ? `<div>… and ${items.length-15} more</div>` : ''}
      </div>
    </div>` : '';

  bdy.innerHTML = `
    <div class="imp-step-hd">Import complete</div>
    <div class="imp-sum-row">
      <div class="imp-sum-pill imp-sum-pill-ok">Created: <strong>${created.length}</strong></div>
      <div class="imp-sum-pill imp-sum-pill-warn">Updated: <strong>${updated.length}</strong></div>
      <div class="imp-sum-pill imp-sum-pill-del">Deleted: <strong>${deleted.length}</strong></div>
      <div class="imp-sum-pill imp-sum-pill-skip">Skipped: <strong>${skipped.length}</strong></div>
      <div class="imp-sum-pill imp-sum-pill-err">Errors: <strong>${errors.length}</strong></div>
    </div>
    ${block('Created', created, 'var(--up)')}
    ${block('Updated', updated, 'var(--warn)')}
    ${block('Deleted', deleted, 'var(--down)')}
    ${block('Skipped', skipped, 'var(--text2)')}
    ${block('Errors',  errors,  'var(--down)')}
  `;
  ft.innerHTML = `
    <button class="btn-p" onclick="closeM('mimp')">Done</button>
  `;
}
