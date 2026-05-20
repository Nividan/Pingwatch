// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? GROUP GRID �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?

function grpId(g){ return 'grp-'+btoa(unescape(encodeURIComponent(g))).replace(/[^a-z0-9]/gi,''); }
function gridId(g){ return 'gg-'+btoa(unescape(encodeURIComponent(g))).replace(/[^a-z0-9]/gi,''); }
function cntId(g){  return 'gc-'+btoa(unescape(encodeURIComponent(g))).replace(/[^a-z0-9]/gi,''); }

// ── Site → Group → Device hierarchy helpers (v1.0+) ───────────────
// Devices have an optional `site` tag above their `group`. The visual
// bucket is keyed by the (site, group) tuple — same group name under
// different sites renders as separate buckets ("HQ → Servers" and
// "DR → Servers" are two visual sections, intentionally).
//
// We encode the tuple as `<site><group>` (unit-separator) and feed
// that into grpId/gridId/cntId. Empty site → "Unsited" bucket.
function _siteLabel(s){ return (s && String(s).trim()) || 'Unsited'; }
function _dgKey(site, group){ return `${site || ''}${group || 'Default Group'}`; }
function siteId(s){    return 'site-'+btoa(unescape(encodeURIComponent(s || '__unsited__'))).replace(/[^a-z0-9]/gi,''); }
function sitebId(s){   return 'sb-'  +btoa(unescape(encodeURIComponent(s || '__unsited__'))).replace(/[^a-z0-9]/gi,''); }
function siteCntId(s){ return 'sc-'  +btoa(unescape(encodeURIComponent(s || '__unsited__'))).replace(/[^a-z0-9]/gi,''); }

// Muted-group set — populated once at boot from /api/device-groups/muted
// and kept in sync whenever the Edit Group modal toggles the flag. Used
// to decorate group headers with a 🔕 badge.
if (!window._mutedGroups) window._mutedGroups = new Set();

async function _loadMutedGroups(){
  try {
    const r = await api('GET', '/api/device-groups/muted');
    window._mutedGroups = new Set((r && r.groups) || []);
  } catch { /* keep previous set on failure */ }
}

function _setGroupMutedLocal(group, muted){
  if (!window._mutedGroups) window._mutedGroups = new Set();
  if (muted) window._mutedGroups.add(group);
  else       window._mutedGroups.delete(group);
  _refreshGroupMuteBadge(group);
}

function _refreshGroupMuteBadge(group){
  // Same group name may appear under multiple sites — update every wrap.
  const wraps = document.querySelectorAll(`.grp-wrap[data-grp-name="${CSS.escape(group)}"]`);
  if (!wraps.length) return;
  const muted = window._mutedGroups && window._mutedGroups.has(group);
  wraps.forEach(wrap => {
    let badge = wrap.querySelector('.grp-mute-badge');
    if (muted && !badge){
      badge = document.createElement('span');
      badge.className = 'grp-mute-badge';
      badge.textContent = '🔕';
      badge.title = 'Alerts muted for this group';
      const label = wrap.querySelector('.grp-label');
      if (label && label.parentNode) label.parentNode.insertBefore(badge, label.nextSibling);
    } else if (!muted && badge){
      badge.remove();
    }
  });
}

// Tiny origin chip shown next to the device name on cards/rows when the
// device was auto-added by monitoring/auto_discovery.py. Empty string for
// manually-added or bulk-imported devices.
function _renderAutoDiscoveryChip(dev){
  const ts  = parseFloat(dev && dev.discovered_at);
  const cidr = (dev && dev.discovered_from_cidr) || '';
  if (!ts || !cidr) return '';
  let whenStr = '';
  try { whenStr = new Date(ts * 1000).toLocaleString(); } catch {}
  const tip = `Auto-discovered from ${cidr}` + (whenStr ? ` · ${whenStr}` : '');
  return ` <span class="dc-ad-chip" title="${esc(tip)}">📡</span>`;
}

// ── View mode (grid / list) ─────────────────────────────────────
let _devView = localStorage.getItem('pw-dev-view') || 'grid';

// ── Multi-select state ──────────────────────────────────────────
// _selectMode is a UI toggle (the ☑ Select button). When on, cards and
// group headers show checkboxes and clicks toggle selection rather than
// opening the device detail window.
let _selectMode = false;
let _selectedDids = new Set();
let _lastClickedDid = null;

function _setView(mode){
  _devView = mode;
  localStorage.setItem('pw-dev-view', mode);
  document.getElementById('vtGrid')?.classList.toggle('active', mode==='grid');
  document.getElementById('vtList')?.classList.toggle('active', mode==='list');
  _applyViewMode();
}

function _applyViewMode(){
  const isList = _devView==='list';
  document.querySelectorAll('.grp-grid').forEach(g => g.classList.toggle('list-view', isList));
  // Re-apply filter so visibility is correct
  _applyDevFilter(document.getElementById('devSearch')?.value||'');
}

function _restoreViewToggle(){
  if(_devView==='list'){
    document.getElementById('vtGrid')?.classList.remove('active');
    document.getElementById('vtList')?.classList.add('active');
  }
  _applyViewMode();
  _initDevCtxMenu();
}

// ══════════════════ MULTI-SELECT (bulk device ops) ══════════════════

function _toggleSelectMode(){
  _selectMode = !_selectMode;
  document.body.classList.toggle('pw-select-mode', _selectMode);
  document.getElementById('btnSelectMode')?.classList.toggle('active', _selectMode);
  if (!_selectMode){
    _selectedDids.clear();
    _lastClickedDid = null;
    _refreshAllCardSelVisuals();
    _refreshAllGroupSelVisuals();
    _updateBulkBar();
  } else {
    _updateBulkBar();
  }
}

function _exitSelectMode(){
  if (_selectMode) _toggleSelectMode();
}

function _clearSelection(){
  _selectedDids.clear();
  _lastClickedDid = null;
  _refreshAllCardSelVisuals();
  _refreshAllGroupSelVisuals();
  _updateBulkBar();
}

// Click on a card — the router between "open device" and "toggle select".
function _cardClick(ev, did){
  if (!_selectMode){
    openDevWin(did);
    return;
  }
  ev.stopPropagation();
  if (ev.shiftKey && _lastClickedDid){
    _rangeSelect(_lastClickedDid, did);
  } else {
    _toggleCardId(did);
    _lastClickedDid = did;
  }
}

// Click on the checkbox overlay (either on a card or a list row).
function _toggleCard(ev, did){
  // Ensure select mode is active — ticking a checkbox in non-select mode
  // turns it on. This is a UX nicety: users can discover the feature by
  // clicking the barely-visible checkbox area.
  if (!_selectMode){
    _selectMode = true;
    document.body.classList.add('pw-select-mode');
    document.getElementById('btnSelectMode')?.classList.add('active');
  }
  if (ev && ev.shiftKey && _lastClickedDid){
    _rangeSelect(_lastClickedDid, did);
  } else {
    _toggleCardId(did);
    _lastClickedDid = did;
  }
}

function _toggleCardId(did){
  if (_selectedDids.has(did)) _selectedDids.delete(did);
  else                        _selectedDids.add(did);
  _refreshCardSelVisual(did);
  const dev = S.devices[did];
  if (dev) _refreshGroupSelVisual(dev.group || 'Default Group');
  _updateBulkBar();
}

function _rangeSelect(fromDid, toDid){
  // DOM order is the source of truth. Walk .dc elements (both visible and
  // hidden by filter — shift-click spans through filtered items too).
  const cards = [...document.querySelectorAll('.dc:not(.dc-add)')];
  const ids   = cards.map(c => c.id.replace('dp-',''));
  const i = ids.indexOf(fromDid);
  const j = ids.indexOf(toDid);
  if (i < 0 || j < 0){
    _toggleCardId(toDid);
    return;
  }
  const [lo, hi] = i <= j ? [i, j] : [j, i];
  const groups = new Set();
  for (let k = lo; k <= hi; k++){
    _selectedDids.add(ids[k]);
    _refreshCardSelVisual(ids[k]);
    const dev = S.devices[ids[k]];
    if (dev) groups.add(dev.group || 'Default Group');
  }
  groups.forEach(g => _refreshGroupSelVisual(g));
  _updateBulkBar();
  _lastClickedDid = toDid;
}

function _toggleGroupSel(group){
  const dids = _didsInGroup(group);
  if (dids.length === 0) return;
  // Tri-state: if all are selected, unselect all; else select all (including
  // currently-unselected).
  const allSelected = dids.every(d => _selectedDids.has(d));
  dids.forEach(d => {
    if (allSelected) _selectedDids.delete(d);
    else             _selectedDids.add(d);
    _refreshCardSelVisual(d);
  });
  _refreshGroupSelVisual(group);
  _updateBulkBar();
}

function _didsInGroup(group){
  // Group-by-string on S.devices — the DOM grid may filter-hide some cards
  // but group-header select-all still selects the whole group, matching
  // file-manager semantics.
  const out = [];
  Object.values(S.devices).forEach(d => {
    const g = d.group || 'Default Group';
    if (g === group) out.push(d.device_id);
  });
  return out;
}

function _refreshCardSelVisual(did){
  const card = document.getElementById('dp-'+did);
  const row  = document.getElementById('dpl-'+did);
  const isSel = _selectedDids.has(did);
  [card, row].forEach(el => {
    if (!el) return;
    el.classList.toggle('selected', isSel);
    const cb = el.querySelector('.dc-sel-cb');
    if (cb) cb.classList.toggle('checked', isSel);
  });
}

function _refreshAllCardSelVisuals(){
  document.querySelectorAll('.dc:not(.dc-add)').forEach(c => {
    const did = c.id.replace('dp-','');
    const isSel = _selectedDids.has(did);
    c.classList.toggle('selected', isSel);
    const cb = c.querySelector('.dc-sel-cb');
    if (cb) cb.classList.toggle('checked', isSel);
  });
  document.querySelectorAll('.dc-list-row').forEach(r => {
    const did = r.id.replace('dpl-','');
    const isSel = _selectedDids.has(did);
    r.classList.toggle('selected', isSel);
    const cb = r.querySelector('.dc-sel-cb');
    if (cb) cb.classList.toggle('checked', isSel);
  });
}

function _refreshGroupSelVisual(group){
  // Selection state is computed per group name globally (across all sites
  // that have that group). Mirror onto every matching .grp-wrap.
  const wraps = document.querySelectorAll(`.grp-wrap[data-grp-name="${CSS.escape(group)}"]`);
  if (!wraps.length) return;
  const dids = _didsInGroup(group);
  const sel  = dids.filter(d => _selectedDids.has(d)).length;
  wraps.forEach(wrap => {
    const cb = wrap.querySelector('.grp-sel-cb');
    if (!cb) return;
    cb.classList.remove('checked','partial');
    _applyGroupSelClasses(cb, sel, dids.length);
  });
}

// Extracted helper so the loop above stays compact. Sets the right class
// (checked / partial / none) based on count.
function _applyGroupSelClasses(cb, sel, total){
  if (sel === 0)            { /* empty */ }
  else if (sel === total)   cb.classList.add('checked');
  else                      cb.classList.add('partial');
}

function _refreshAllGroupSelVisuals(){
  document.querySelectorAll('.grp-sel-cb').forEach(cb => {
    const g = cb.dataset.group;
    if (g) _refreshGroupSelVisual(g);
  });
}

function _updateBulkBar(){
  const bar = document.getElementById('bulkBar');
  if (!bar) return;
  const n = _selectedDids.size;
  if (!_selectMode || n === 0){
    bar.style.display = 'none';
    return;
  }
  bar.style.display = '';
  document.getElementById('bulkBarCount').textContent = `${n} selected`;
  // Count selected devices that are hidden by the current filter.
  const hidden = [..._selectedDids].filter(did => {
    const el = document.getElementById('dp-'+did) || document.getElementById('dpl-'+did);
    return el && el.style.display === 'none';
  }).length;
  const hEl = document.getElementById('bulkBarHidden');
  hEl.textContent = hidden > 0 ? `(${hidden} hidden by filter)` : '';
  // Refresh the datalist of existing groups each time the bar updates.
  const list = document.getElementById('bulkGroupList');
  if (list){
    const groups = [...new Set(Object.values(S.devices).map(d => d.group || 'Default Group'))].sort();
    list.innerHTML = groups.map(g => `<option value="${esc(g)}"></option>`).join('');
  }
  // Site datalist — fetched from /api/sites (UNION of IPAM + devices).
  // Falls back to in-memory devices list on fetch failure.
  const siteList = document.getElementById('bulkSiteList');
  if (siteList){
    if (typeof _populateSiteDatalist === 'function') {
      _populateSiteDatalist('bulkSiteList');
    } else {
      const sites = [...new Set(Object.values(S.devices).map(d => d.site).filter(Boolean))].sort();
      siteList.innerHTML = sites.map(s => `<option value="${esc(s)}"></option>`).join('');
    }
  }
}

async function _bulkApplyMove(){
  const input = document.getElementById('bulkGroupInput');
  const target = (input?.value || '').trim();
  if (!target){ toast('Enter a group name','err'); return; }
  if (target.length > 80){ toast('Group name too long (max 80)','err'); return; }
  const dids = [..._selectedDids];
  if (!dids.length) return;
  try {
    const r = await api('POST','/api/devices/bulk',{device_ids:dids, action:'move', group:target});
    if (!r || !r.ok){ toast('Bulk move failed','err'); return; }
    // Clear selection first so re-rendered cards don't redraw the tick.
    _clearSelection();
    // Optimistic update — update local state and re-parent the cards so
    // the user sees the move instantly (server SSE would also do this
    // eventually, but with a visible lag).
    dids.forEach(d => {
      const dev = S.devices[d];
      if (!dev) return;
      dev.group = target;
      renderDp(dev);
    });
    pruneEmptyGroups();
    if (r.failed > 0) toast(`${r.applied} of ${dids.length} moved (${r.failed} failed)`,'warn');
    else              toast(`${r.applied} device(s) moved to "${target}"`,'ok');
    if (input) input.value = '';
  } catch {
    toast('Network error','err');
  }
}

// Bulk move selected devices to a different site. Empty input is a valid
// value meaning "Unsited" (clears the field) — we pass site:'' to the API.
async function _bulkApplySiteMove(){
  const input = document.getElementById('bulkSiteInput');
  const target = (input?.value || '').trim();
  if (target.length > 80){ toast('Site name too long (max 80)','err'); return; }
  const dids = [..._selectedDids];
  if (!dids.length) return;
  try {
    const r = await api('POST','/api/devices/bulk',{device_ids:dids, action:'move', site:target});
    if (!r || !r.ok){ toast('Bulk site move failed','err'); return; }
    _clearSelection();
    dids.forEach(d => {
      const dev = S.devices[d];
      if (!dev) return;
      dev.site = target;
      renderDp(dev);
    });
    pruneEmptyGroups();
    window._pwSitesCache = null;  // next autocomplete fetch picks up the new site
    const lbl = target || 'Unsited';
    if (r.failed > 0) toast(`${r.applied} of ${dids.length} moved (${r.failed} failed)`,'warn');
    else              toast(`${r.applied} device(s) moved to "${lbl}"`,'ok');
    if (input) input.value = '';
  } catch {
    toast('Network error','err');
  }
}

async function _bulkAction(action){
  const dids = [..._selectedDids];
  if (!dids.length) return;
  try {
    const r = await api('POST','/api/devices/bulk',{device_ids:dids, action:action});
    if (!r || !r.ok){ toast('Bulk action failed','err'); return; }
    const verb = action==='start' ? 'resumed' : (action==='stop' ? 'paused' : action);
    if (r.failed > 0) toast(`${r.applied} of ${dids.length} ${verb} (${r.failed} failed)`,'warn');
    else              toast(`${r.applied} device(s) ${verb}`,'ok');
    _clearSelection();
  } catch {
    toast('Network error','err');
  }
}

function _bulkDeleteConfirm(){
  const n = _selectedDids.size;
  if (!n) return;
  closeM('mbulk-del');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'mbulk-del';
  _overlayClose(o, () => closeM('mbulk-del'));
  o.innerHTML = `
  <div class="mbox" style="max-width:440px">
    <div class="mhd">
      <div class="mttl">Delete ${n} device${n===1?'':'s'}?</div>
      <button class="mclose" onclick="closeM('mbulk-del')">&#x2715;</button>
    </div>
    <div class="mbdy">
      <div class="fh" style="line-height:1.5">
        This will permanently delete the selected device${n===1?'':'s'}, all their
        sensors, history, and active events.<br/><br/>
        Auto-discovered devices will be added to the suppressed-hosts list so
        they won't be re-added by the next Auto-Discovery tick.
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mbulk-del')">Cancel</button>
      <button class="btn-p" style="background:var(--down);border-color:var(--down)"
              onclick="_bulkDeleteConfirmed()">Delete ${n}</button>
    </div>
  </div>`;
  document.body.appendChild(o);
}

async function _bulkDeleteConfirmed(){
  closeM('mbulk-del');
  const dids = [..._selectedDids];
  if (!dids.length) return;
  try {
    const r = await api('POST','/api/devices/bulk',{device_ids:dids, action:'delete'});
    if (!r || !r.ok){ toast('Bulk delete failed','err'); return; }
    if (r.failed > 0) toast(`${r.applied} of ${dids.length} deleted (${r.failed} failed)`,'warn');
    else              toast(`${r.applied} device(s) deleted`,'ok');
    _clearSelection();
  } catch {
    toast('Network error','err');
  }
}

// _lsGet / _lsSet moved to forms-utils.js (canonical location)

// Build (or return) the .site-body element that should contain group wraps
// for the given site. Site headers carry a collapse arrow + label + child
// count; collapsed state persists in `pw-site-collapsed` localStorage.
function ensureSiteSection(site){
  const sid=siteId(site), sbid=sitebId(site), scid=siteCntId(site);
  const existing=document.getElementById(sid);
  if(existing) return document.getElementById(sbid);

  const wrap=document.createElement('div');
  wrap.className='site-wrap'; wrap.id=sid;
  wrap.dataset.site = site || '';

  const _siteCol=new Set(_lsGet('pw-site-collapsed', []));
  const isCol=_siteCol.has(site || '');

  const hdr=document.createElement('div');
  hdr.className='site-hdr';
  hdr.addEventListener('click', function(){ toggleSite(site); });

  const arr=document.createElement('div');
  arr.className='site-arr'+(isCol?'':' open');
  arr.textContent='▶';

  const label=document.createElement('div');
  label.className='site-label';
  label.textContent=_siteLabel(site);

  const cnt=document.createElement('div');
  cnt.className='site-count'; cnt.id=scid; cnt.textContent='';

  hdr.appendChild(arr); hdr.appendChild(label); hdr.appendChild(cnt);

  const body=document.createElement('div');
  body.className='site-body'+(isCol?' collapsed':'');
  body.id=sbid;

  wrap.appendChild(hdr); wrap.appendChild(body);
  document.getElementById('dpanels').appendChild(wrap);
  return body;
}

function toggleSite(site){
  const body=document.getElementById(sitebId(site));
  const arr=document.querySelector('#'+siteId(site)+' .site-arr');
  if(!body) return;
  const nowCol=body.classList.toggle('collapsed');
  if(arr){ arr.classList.toggle('open',!nowCol); }
  const set=new Set(_lsGet('pw-site-collapsed', []));
  if(nowCol) set.add(site || ''); else set.delete(site || '');
  _lsSet('pw-site-collapsed', [...set]);
}

function refreshSiteCounts(){
  document.querySelectorAll('.site-wrap').forEach(sw=>{
    const cnt=sw.querySelector('.site-count');
    if(!cnt) return;
    const n=sw.querySelectorAll('.dc:not(.dc-add)').length;
    const groups=sw.querySelectorAll('.grp-wrap').length;
    cnt.textContent = `${groups} group${groups===1?'':'s'} · ${n} device${n===1?'':'s'}`;
  });
}

function ensureGroupSection(group, site){
  // Composite key: same group name under different sites → distinct buckets.
  // site defaults to '' (Unsited) so any pre-existing single-arg callers keep
  // working — they all land under the Unsited site.
  if (site === undefined) site = '';
  const key=_dgKey(site, group);
  const id=grpId(key);
  if(document.getElementById(id)) return;
  const gid=gridId(key), gcid=cntId(key);

  const wrap=document.createElement('div');
  wrap.className='grp-wrap'; wrap.id=id;

  // Header
  const hdr=document.createElement('div');
  hdr.className='grp-hdr';

  const _grpCol=new Set(_lsGet('pw-grp-collapsed', []));
  const isCol=_grpCol.has(key);

  const dragH=document.createElement('div');
  dragH.className='grp-drag-handle'; dragH.textContent='⠿'; dragH.title='Drag to reorder groups';
  dragH.addEventListener('mousedown',()=>{ _grpDragOK=true; });

  const grpCb=document.createElement('div');
  grpCb.className='grp-sel-cb';
  grpCb.title='Select all in this group';
  grpCb.dataset.group=group;
  grpCb.addEventListener('click',function(e){
    e.stopPropagation();
    _toggleGroupSel(group);
  });

  const line1=document.createElement('div');
  line1.className='grp-line'; line1.style.cssText='max-width:40px;flex:0 0 40px';

  const arr=document.createElement('div');
  arr.className='grp-arr'+(isCol?'':' open');
  arr.title=isCol?'Expand':'Collapse';
  arr.textContent='▶';
  arr.addEventListener('click',function(){ toggleGroup(group, site || ''); });

  const label=document.createElement('div');
  label.className='grp-label';
  label.textContent=group;

  // 🔕 badge when group is in the muted set (populated by _loadMutedGroups)
  let muteBadge=null;
  if (window._mutedGroups && window._mutedGroups.has(group)){
    muteBadge=document.createElement('span');
    muteBadge.className='grp-mute-badge';
    muteBadge.textContent='🔕';
    muteBadge.title='Alerts muted for this group';
  }

  const editBtn=document.createElement('button');
  editBtn.className='grp-edit-btn rbac-operator';
  editBtn.title='Edit group settings';
  editBtn.innerHTML='⚙️';
  editBtn.addEventListener('click',function(e){
    e.stopPropagation();
    if (typeof openEditGroup === 'function') openEditGroup(group);
  });

  const cnt=document.createElement('div');
  cnt.className='grp-count'; cnt.id=gcid; cnt.textContent='0 total';

  const line2=document.createElement('div');
  line2.className='grp-line';

  const summary=document.createElement('span');
  summary.className='grp-summary'; summary.id='gsum-'+gridId(key).replace('gg-','');

  // Order matches the design: drag · checkbox · arrow · label · gear · spacer · pills · count
  // (summary status pills come BEFORE the total count to read "●6 ●1 ●1   8 total")
  hdr.appendChild(dragH); hdr.appendChild(grpCb); hdr.appendChild(line1); hdr.appendChild(arr); hdr.appendChild(label);
  if (muteBadge) hdr.appendChild(muteBadge);
  hdr.appendChild(editBtn); hdr.appendChild(summary); hdr.appendChild(cnt); hdr.appendChild(line2);

  // Grid
  const grid=document.createElement('div');
  grid.className='grp-grid'+(isCol?' collapsed':'')+((_devView==='list')?' list-view':'');
  grid.id=gid;
  grid.dataset.group=group;
  grid.dataset.site=site || '';
  grid.dataset.dgkey=key;
  grid.addEventListener('dragover',onDragOver);
  grid.addEventListener('drop',onDrop);
  grid.addEventListener('dragleave',onDragLeave);

  // Add-device card inside grid
  const addCard=document.createElement('div');
  addCard.className='dc dc-add';
  addCard.innerHTML='<div class="dc-add-ico">&#xFF0B;</div><div>Add Device</div>';
  addCard.addEventListener('click',function(){ openAddDeviceGroup(group); });
  grid.appendChild(addCard);

  wrap.appendChild(hdr);
  wrap.appendChild(grid);
  wrap.dataset.grpName=group;
  wrap.dataset.site=site || '';
  wrap.dataset.dgkey=key;
  applyGrpDrag(wrap);
  ensureSiteSection(site || '').appendChild(wrap);

  // The muted-groups set may be stale (e.g. auto-discovery just muted this
  // brand-new group server-side after the last _loadMutedGroups() fetch).
  // Refresh asynchronously so the badge paints correctly without blocking render.
  _loadMutedGroups().then(() => _refreshGroupMuteBadge(group));
}

function refreshGroupCounts(){
  document.querySelectorAll('.grp-grid').forEach(grid=>{
    const key=grid.dataset.dgkey || grid.dataset.group;
    const cnt=document.getElementById(cntId(key));
    if(cnt){
      const n=grid.querySelectorAll('.dc:not(.dc-add)').length;
      cnt.textContent=`${n} total`;
    }
  });
  refreshSiteCounts();
}

function toggleGroup(group, site){
  // Tolerate single-arg legacy callers: try to find the first .grp-wrap with
  // this group name (we may have multiple — one per site) and toggle that.
  let key;
  if (site === undefined) {
    const w = document.querySelector(`.grp-wrap[data-grp-name="${CSS.escape(group)}"]`);
    if (!w) return;
    key = w.dataset.dgkey;
    site = w.dataset.site || '';
  } else {
    key = _dgKey(site, group);
  }
  const grid=document.getElementById(gridId(key));
  const arr=document.querySelector('#'+grpId(key)+' .grp-arr');
  if(!grid) return;
  const nowCol=grid.classList.toggle('collapsed');
  if(arr){arr.classList.toggle('open',!nowCol);arr.title=nowCol?'Expand':'Collapse';}
  const set=new Set(_lsGet('pw-grp-collapsed', []));
  if(nowCol) set.add(key); else set.delete(key);
  _lsSet('pw-grp-collapsed', [...set]);
  _updateGrpSummary(group, site);
}

function _collapseAllGroups(){
  // Iterate by composite key (dgkey) so groups with the same name under
  // different sites collapse independently — each is its own bucket.
  const keys=[...document.querySelectorAll('.grp-grid')].map(el=>el.dataset.dgkey).filter(Boolean);
  const set=new Set(keys);
  keys.forEach(key=>{
    const grid=document.getElementById(gridId(key));
    const arr=document.querySelector('#'+grpId(key)+' .grp-arr');
    if(!grid) return;
    grid.classList.add('collapsed');
    if(arr){arr.classList.remove('open');arr.title='Expand';}
    _updateGrpSummary(grid.dataset.group, grid.dataset.site || '');
  });
  _lsSet('pw-grp-collapsed',[...set]);
}

function _expandAllGroups(){
  const keys=[...document.querySelectorAll('.grp-grid')].map(el=>el.dataset.dgkey).filter(Boolean);
  keys.forEach(key=>{
    const grid=document.getElementById(gridId(key));
    const arr=document.querySelector('#'+grpId(key)+' .grp-arr');
    if(!grid) return;
    grid.classList.remove('collapsed');
    if(arr){arr.classList.add('open');arr.title='Collapse';}
    _updateGrpSummary(grid.dataset.group, grid.dataset.site || '');
  });
  _lsSet('pw-grp-collapsed',[]);
}

function _updateGrpSummary(group, site){
  // Legacy callers that pass only group: find any matching wrap and
  // recurse into the per-site variant. Cheap and only used in one path.
  if (site === undefined) {
    document.querySelectorAll(`.grp-wrap[data-grp-name="${CSS.escape(group)}"]`).forEach(w=>{
      _updateGrpSummary(group, w.dataset.site || '');
    });
    return;
  }
  const key=_dgKey(site, group);
  const gid=gridId(key).replace('gg-','');
  const el=document.getElementById('gsum-'+gid);
  if(!el) return;
  const grid=document.getElementById(gridId(key));
  if(!grid){ el.innerHTML=''; return; }
  // Count devices by status in this group (always show, regardless of collapsed state)
  const counts={up:0,down:0,warn:0};
  grid.querySelectorAll('.dc:not(.dc-add)').forEach(card=>{
    const did=card.id.replace('dp-','');
    const dev=S.devices[did];
    if(dev){const st=dev.status||'unknown';if(counts[st]!==undefined)counts[st]++;}
  });
  const parts=[];
  if(counts.up)   parts.push(`<span class="grp-sum-pill up"><span class="grp-sum-dot"></span>${counts.up}</span>`);
  if(counts.warn) parts.push(`<span class="grp-sum-pill warn"><span class="grp-sum-dot"></span>${counts.warn}</span>`);
  if(counts.down) parts.push(`<span class="grp-sum-pill down"><span class="grp-sum-dot"></span>${counts.down}</span>`);
  el.innerHTML=parts.join('');
}

function _devSnrSummaryHtml(did){
  const dev=S.devices[did];
  const _keys=S._devSensors?.[did]||new Set();
  const snrs=[..._keys].map(k=>S.sensors[k]).filter(Boolean);
  if(!snrs.length) return '';
  let ok=0,warn=0,down=0;
  snrs.forEach(s=>{
    if(s.alerts_muted||dev?.alerts_muted){if(s.alive===true)ok++;return;}
    if(s.alive===false) down++;
    else if(s.threshold_state&&s.threshold_state!=='ok') warn++;
    else if(s.alive===true) ok++;
  });
  let h='';
  if(ok)   h+=`<span class="dls-chip up"><span class="dls-dot"></span>${ok}</span>`;
  if(warn) h+=`<span class="dls-chip warn"><span class="dls-dot"></span>${warn}</span>`;
  if(down) h+=`<span class="dls-chip down"><span class="dls-dot"></span>${down}</span>`;
  return h?`<div class="dlr-summary" id="dlr-sum-${did}">${h}</div>`:'';
}

// ── DEVICES CONTEXT MENU ─────────────────────────────────────────────────
let _dcm=null, _ctxGrp=null;

function _showDcm(x,y){
  if(!_dcm) return;
  _dcm.style.display='block';
  const mw=_dcm.offsetWidth||185,mh=_dcm.offsetHeight||160;
  _dcm.style.left=(x+mw>innerWidth?x-mw:x)+'px';
  _dcm.style.top =(y+mh>innerHeight?y-mh:y)+'px';
}

function _hideDcm(){ if(_dcm) _dcm.style.display='none'; }

function _initDevCtxMenu(){
  if(_dcm) return; // already initialized
  _dcm=document.getElementById('dev-ctx-menu');
  if(!_dcm) return;
  document.addEventListener('click',_hideDcm);
  document.addEventListener('keydown',e=>{ if(e.key==='Escape') _hideDcm(); });
  const panels=document.getElementById('dpanels');
  if(!panels) return;
  panels.addEventListener('contextmenu',e=>{
    e.preventDefault();
    const card=e.target.closest('.dc:not(.dc-add)');
    const row =e.target.closest('.dc-list-row');
    const grpHdr=e.target.closest('.grp-hdr');
    const raw =(card?.id||row?.id||'');
    const did =raw.replace(/^dp-|^dpl-/,'') || null;
    if(did&&S.devices[did]){
      const dev=S.devices[did];
      const muted=dev.alerts_muted;
      _dcm.innerHTML=`
        <div class="dci dci-accent" onclick="_hideDcm();openDevWin('${did}')">📊 Open Details</div>
        <div class="dci" onclick="_hideDcm();openEditDevice('${did}')">⚙️ Edit Device</div>
        <div class="dci-sep"></div>
        <div class="dci ${muted?'dci-green':'dci-warn'}" onclick="_hideDcm();_toggleMuteDevice('${did}')">
          ${muted?'🔔 Unmute Alerts':'🔕 Mute Alerts'}
        </div>
        <div class="dci-sep"></div>
        <div class="dci dci-danger rbac-op" onclick="_hideDcm();delDev('${did}')">🗑️ Delete Device</div>`;
    } else if(grpHdr){
      _ctxGrp=grpHdr.closest('.grp-wrap')?.dataset.grpName||'';
      const _isDefault = _ctxGrp === 'Default Group';
      _dcm.innerHTML=`
        <div class="dci dci-accent rbac-op" onclick="_hideDcm();if(typeof openEditGroup==='function')openEditGroup(_ctxGrp)">⚙️ Edit Group</div>
        <div class="dci-sep"></div>
        <div class="dci rbac-op" onclick="_hideDcm();openAddDeviceGroup(_ctxGrp)">🖥️ Add Device</div>
        ${_isDefault ? '' : `
        <div class="dci-sep"></div>
        <div class="dci dci-danger rbac-op" onclick="_hideDcm();_deleteGroup(${JSON.stringify(_ctxGrp)})">🗑️ Delete Group</div>`}`;
    } else {
      _dcm.innerHTML=`
        <div class="dci dci-accent rbac-op" onclick="_hideDcm();openAddDevice()">🖥️ Add Device</div>
        <div class="dci rbac-op" onclick="_hideDcm();openAddGroup()">👥 Add Group</div>`;
    }
    _showDcm(e.clientX+2,e.clientY+2);
  });
}

// Delete a group by moving all its devices to "Default Group".
// Groups in PingWatch are just a label field on each device, so deletion
// is implemented as a bulk re-label — the group name disappears once no
// device carries it.
async function _deleteGroup(gname){
  if(!gname || gname==='Default Group'){
    toast('Cannot delete the Default Group','err');
    return;
  }
  const members = Object.values(S.devices).filter(d => (d.group||'Default Group') === gname);
  const n = members.length;
  const msg = n === 0
    ? `Delete group "${gname}"?`
    : `Delete group "${gname}"?\n\nThe ${n} device${n===1?'':'s'} inside will be moved to "Default Group".`;
  if(!confirm(msg)) return;
  try{
    if(n > 0){
      await Promise.all(members.map(d =>
        api('PATCH', `/api/device/${d.device_id}`, { group: 'Default Group' })
      ));
    }
    toast(`Group "${gname}" deleted`,'ok');
    if(typeof _refreshDevices==='function') _refreshDevices();
  }catch(e){
    toast('Failed to delete group: '+(e.message||e),'err');
  }
}

async function _toggleMuteDevice(did){
  const dev=S.devices[did];
  if(!dev) return;
  const newMuted=!dev.alerts_muted;
  try{
    await api('PATCH',`/api/device/${did}`,{
      name:dev.name,host:dev.host,
      group:dev.group||'Default Group',
      webhook_url:dev.webhook_url||'',
      alerts_muted:newMuted,
      snmp_community_default:dev.snmp_community_default||'',
      snmp_version_default:dev.snmp_version_default||'',
      vmware_user_default:dev.vmware_user_default||''
    });
    dev.alerts_muted=newMuted;
    toast(newMuted?`🔕 Alerts muted: ${dev.name}`:`🔔 Alerts unmuted: ${dev.name}`,'ok');
  }catch(e){
    toast('Failed to update alert setting','err');
  }
}

function pruneEmptyGroups(){
  document.querySelectorAll('.grp-wrap').forEach(w=>{
    const grid=w.querySelector('.grp-grid');
    const n=grid?grid.querySelectorAll('.dc:not(.dc-add)').length:0;
    if(n===0) w.remove();
  });
  // Then prune site wrappers that have no groups left in their body.
  document.querySelectorAll('.site-wrap').forEach(sw=>{
    const body=sw.querySelector('.site-body');
    if(body && body.querySelectorAll('.grp-wrap').length===0) sw.remove();
  });
  refreshSiteCounts();
}

function renderDp(dev){
  document.getElementById('emptyMain').style.display='none';
  if(activeMainTab==='devices') document.getElementById('dpanels').style.display='';
  const old=document.getElementById('dp-'+dev.device_id);
  const oldLr=document.getElementById('dpl-'+dev.device_id);
  const group=dev.group||'Default Group';
  const site=dev.site||'';
  ensureGroupSection(group, site);
  const grid=document.getElementById(gridId(_dgKey(site, group)));
  const addBtn=grid.querySelector('.dc-add');
  // If the device is staying in its current group, replace cards in place to
  // preserve position. If the group changed, fall back to remove + append at end.
  const sameGroup = old && old.parentNode === grid;
  // Card (grid view)
  const el=document.createElement('div');
  el.innerHTML=cardHTML(dev);
  const card=el.firstElementChild;
  if(sameGroup) old.replaceWith(card);
  else { if(old) old.remove(); grid.insertBefore(card,addBtn); }
  applyDrag(card);
  // List row (list view)
  const lr=document.createElement('div');
  lr.innerHTML=listRowHTML(dev);
  const row=lr.firstElementChild;
  if(sameGroup && oldLr && oldLr.parentNode === grid) oldLr.replaceWith(row);
  else { if(oldLr) oldLr.remove(); grid.insertBefore(row,addBtn); }
  dev.sensors.forEach(s=>{ S.sensors[dev.device_id+'/'+s.sensor_id]=s; });
  refreshGroupCounts();
  applyRbac();
  // Re-apply search filter so new/updated cards are filtered correctly
  const _srch=document.getElementById('devSearch');
  if(_srch&&_srch.value) _applyDevFilter(_srch.value);
}

function sSnrPreview(did){
  // return up to 3 sensor preview rows for the card, respecting saved drag order
  const _keys=S._devSensors?.[did]||new Set();
  const snrs=[..._keys].map(k=>S.sensors[k]).filter(Boolean);
  if(!snrs.length) return '<div class="dc-more" style="padding:6px 0">No sensors yet</div>';
  const _ord=_lsGet(`pw_snr_order_${did}`,[]);
  if(_ord.length){
    snrs.sort((a,b)=>{
      const ai=_ord.indexOf(a.sensor_id),bi=_ord.indexOf(b.sensor_id);
      return (ai<0?9999:ai)-(bi<0?9999:bi);
    });
  }
  const isSnmp=s=>s.stype==='snmp'||s.stype==='dns';
  const snrVal=s=>{
    if(s.stype==='vmware'){
      if(s.last_value==null) return '—';
      const v=parseFloat(s.last_value);
      if(isNaN(v)) return (s.last_value+'').slice(0,10);
      const u=_VM_UNITS[s.vmware_metric]||'';
      return _fmtVmVal(v,u);
    }
    if(s.stype==='snmp') return (typeof _snmpTileValue === 'function') ? _snmpTileValue(s) : (s.alive===false?'FAIL':(s.last_value||'—').slice(0,10));
    if(isSnmp(s)) return s.alive===false?'FAIL':(s.last_value||'—').slice(0,10);
    return s.last_ms!=null?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—');
  };
  const vc=s=>{
    if(s.alive===false)return'b';
    if(s.stype==='vmware')return s.alive===true?'g':'m';
    if(isSnmp(s))return s.alive===true?'g':'m';
    return s.last_ms!=null?msC(s.last_ms,s):'m';
  };
  // Group VMware sensors by vmware_vm_id into synthetic preview rows
  const vmGroups={};
  const nonVm=[];
  snrs.forEach(s=>{
    if(s.stype==='vmware'&&s.vmware_vm_id){ (vmGroups[s.vmware_vm_id]=vmGroups[s.vmware_vm_id]||[]).push(s); }
    else nonVm.push(s);
  });
  // Build preview items: non-VM sensors first, then one row per VM group
  const previewItems=[];
  nonVm.forEach(s=>previewItems.push({type:'snr',s}));
  Object.entries(vmGroups).forEach(([vmid,vms])=>{
    const _unmuted=vms.filter(s=>!s.alerts_muted);
    const worst=_unmuted.find(s=>s.alive===false)||_unmuted.find(s=>s.threshold_state&&s.threshold_state!=='ok')||_unmuted[0]||vms[0];
    previewItems.push({type:'vmgrp',vmid,vms,worst});
  });
  const shown=previewItems.slice(0,3);
  let html=shown.map(item=>{
    if(item.type==='vmgrp'){
      const {vmid,vms,worst}=item;
      const st=worst.alive===false?'down':worst.alive===true?'up':'';
      const nm=vms[0]?.name?.replace(/ \S+$/,'')??vmid; // strip last word (metric label)
      const _isH=!!(vms[0]?.vmware_metric&&vms[0].vmware_metric.startsWith('host_'));
      return `<div class="dc-snr">
        <div class="dc-snr-ico vmware">${_isH?'H':'V'}</div>
        <div class="dc-snr-nm">${esc(nm)} <span style="color:var(--text3);font-size:10px">${vms.length}m</span></div>
        <div class="dc-snr-val ${worst.alive===false?'b':worst.alive===true?'g':'m'}" id="csv-${worst.device_id}_${worst.sensor_id}">${snrVal(worst)}</div>
        <div class="dc-snr-dot ${st}" id="csd-${worst.device_id}_${worst.sensor_id}"></div>
      </div>`;
    }
    const s=item.s;
    return `<div class="dc-snr snr-t-${s.stype}">
      <div class="dc-snr-ico ${s.stype}">${sIco(s.stype)}</div>
      <div class="dc-snr-nm">${esc(s.name)}</div>
      <div class="dc-snr-val ${vc(s)}" id="csv-${s.device_id}_${s.sensor_id}">${snrVal(s)}</div>
      <div class="dc-snr-dot ${s.alive===true?'up':s.alive===false?'down':''}" id="csd-${s.device_id}_${s.sensor_id}"></div>
    </div>`;
  }).join('');
  const total=nonVm.length+Object.keys(vmGroups).length;
  if(total>3) html+=`<div class="dc-more">+${total-3} more</div>`;
  return html;
}

function cardHTML(dev){
  const st=dev.status||'unknown';
  const lbl={up:'Up',down:'Down',warn:'Warning',unknown:'Unknown'}[st]||st;
  const adChip = _renderAutoDiscoveryChip(dev);
  const selCls = _selectedDids.has(dev.device_id) ? ' selected' : '';
  const cbCls  = _selectedDids.has(dev.device_id) ? 'dc-sel-cb checked' : 'dc-sel-cb';
  const tilesHtml = _devCardTiles(dev.device_id);
  const sensorCount = (S._devSensors?.[dev.device_id]?.size) || 0;
  // Vendor chip — show the device's group as a tag (proxy for vendor since
  // PingWatch doesn't track a vendor field on devices)
  const grpChip = dev.group ? `<div class="dcard-vendor">${esc(dev.group)}</div>` : '';
  return `
  <div class="dc dcard ${st}${selCls}" id="dp-${dev.device_id}" onclick="_cardClick(event,'${dev.device_id}')">
    <div class="${cbCls}" onclick="event.stopPropagation();_toggleCard(event,'${dev.device_id}')"></div>
    <div class="dc-drag-handle" title="Drag to reorder">⠿</div>
    <div class="dcard-head">
      <div class="dc-bar dcard-stripe ${st}" id="dcbar-${dev.device_id}"></div>
      <div class="dcard-name-wrap">
        <div class="dcard-name">${esc(dev.name)}${adChip}</div>
        <div class="dcard-meta"><span>${esc(dev.host||'')}</span></div>
      </div>
      ${grpChip}
    </div>
    <div class="dcard-tiles" id="dcsnr-${dev.device_id}">${tilesHtml}</div>
    <div class="dcard-foot">
      <span class="dc-status ${st}" id="dcst-${dev.device_id}">
        <div class="dc-sdot ${st==='up'?'up':''}"></div>${lbl}
      </span>
      <span class="seen">${sensorCount} sensor${sensorCount===1?'':'s'}</span>
    </div>
  </div>`;
}

// Build the .dcard-tiles content — up to 4 sensors as .stile tiles, respecting
// the user's saved sensor-drag order. Preserves csv-/csd- ids so the existing
// updateCardSensor() hook keeps targeting the right elements.
function _devCardTiles(did){
  const keys = S._devSensors?.[did] || new Set();
  const snrs = [...keys].map(k => S.sensors[k]).filter(Boolean);
  if (!snrs.length) {
    return '<div class="dcard-empty" style="padding:16px;text-align:center;color:var(--text3);font-size:11px;grid-column:1/-1">No sensors yet</div>';
  }
  const ord = _lsGet(`pw_snr_order_${did}`, []);
  if (ord.length) {
    snrs.sort((a, b) => {
      const ai = ord.indexOf(a.sensor_id), bi = ord.indexOf(b.sensor_id);
      return (ai < 0 ? 9999 : ai) - (bi < 0 ? 9999 : bi);
    });
  }
  return snrs.slice(0, 4).map(_stileHTML).join('');
}

function _stileHTML(s){
  const isSnmp = s.stype === 'snmp' || s.stype === 'dns';
  const isVmware = s.stype === 'vmware';
  let st = 'up';
  if (s.alive === false) st = 'down';
  else if (isVmware || isSnmp) st = s.alive === true ? 'up' : 'pause';
  else if (s.last_ms != null) {
    const c = typeof msC === 'function' ? msC(s.last_ms, s) : 'g';
    st = c === 'b' ? 'down' : c === 'w' ? 'warn' : c === 'g' ? 'up' : 'pause';
  } else { st = 'pause'; }
  let val = '—', unit = '';
  if (isVmware) {
    if (s.last_value != null) {
      const v = parseFloat(s.last_value);
      val = isNaN(v) ? (s.last_value+'').slice(0,8)
                     : _fmtVmVal(v, _VM_UNITS[s.vmware_metric]||'');
    }
  } else if (s.stype === 'snmp' && typeof _snmpTileValue === 'function') {
    val = _snmpTileValue(s);
  } else if (isSnmp) {
    val = s.alive === false ? 'FAIL' : (s.last_value || '—').slice(0,8);
  } else if (s.last_ms != null) {
    val = String(s.last_ms) + ' ms';   // unit baked into the string so updateCardSensor stays simple
  } else if (s.alive === false) {
    val = 'DOWN';
  }
  const sparkColor = st === 'down' ? 'var(--down)'
                    : st === 'warn' ? 'var(--warn)'
                    : st === 'pause' ? 'var(--pause)'
                    : 'var(--up)';
  let sparkHtml = '<div class="stile-spark empty"></div>';
  if (typeof pwSparkline === 'function' && Array.isArray(s.history) && s.history.length > 1) {
    const series = s.history.slice(-20).map(v => {
      const n = typeof v === 'number' ? v : parseFloat(v);
      return isNaN(n) ? 0 : n;
    });
    try {
      sparkHtml = `<div class="stile-spark">${pwSparkline(series, { color: sparkColor, h: 18, w: 100, fill: true })}</div>`;
    } catch (_) { /* keep empty placeholder */ }
  }
  return `<div class="stile ${st}">
    <div class="stile-head">
      <span class="dot ${st}" id="csd-${s.device_id}_${s.sensor_id}"></span>
      <span>${esc(s.name)}</span>
    </div>
    <div class="stile-val" id="csv-${s.device_id}_${s.sensor_id}">${esc(val)}</div>
    ${sparkHtml}
  </div>`;
}


function listRowHTML(dev){
  const st=dev.device_id ? (dev.status||'unknown') : 'unknown';
  const _keys=S._devSensors?.[dev.device_id]||new Set();
  const snrs=[..._keys].map(k=>S.sensors[k]).filter(Boolean);
  // Apply saved sensor drag order (same as sSnrPreview)
  const _ord=_lsGet(`pw_snr_order_${dev.device_id}`,[]);
  if(_ord.length){
    snrs.sort((a,b)=>{
      const ai=_ord.indexOf(a.sensor_id),bi=_ord.indexOf(b.sensor_id);
      return (ai<0?9999:ai)-(bi<0?9999:bi);
    });
  }
  const isSnmp=s=>s.stype==='snmp'||s.stype==='dns';
  const snrVal=s=>{
    if(s.stype==='vmware'){
      if(s.last_value==null) return '\u2014';
      const v=parseFloat(s.last_value);
      if(isNaN(v)) return (s.last_value+'').slice(0,10);
      return _fmtVmVal(v,_VM_UNITS[s.vmware_metric]||'');
    }
    if(s.stype==='snmp') return (typeof _snmpTileValue === 'function') ? _snmpTileValue(s) : (s.alive===false?'FAIL':(s.last_value||'\u2014').slice(0,10));
    if(isSnmp(s)) return s.alive===false?'FAIL':(s.last_value||'\u2014').slice(0,10);
    return s.last_ms!=null?`${s.last_ms}ms`:(s.alive===false?'DOWN':'\u2014');
  };
  const vc=s=>{
    if(s.alive===false)return'b';
    if(s.stype==='vmware'||isSnmp(s))return s.alive===true?'g':'m';
    return s.last_ms!=null?msC(s.last_ms,s):'m';
  };
  let snrHtml='';
  snrs.slice(0,5).forEach(s=>{
    snrHtml+=`<span class="dlr-snr snr-t-${s.stype}">
      <span class="dc-snr-ico ${s.stype}">${sIco(s.stype)}</span>
      <span class="dc-snr-nm">${esc(s.name)}</span>
      <span class="dc-snr-val ${vc(s)}" id="lsv-${s.device_id}_${s.sensor_id}">${snrVal(s)}</span>
    </span>`;
  });
  if(snrs.length>5) snrHtml+=`<span class="dlr-more">+${snrs.length-5}</span>`;
  const selCls = _selectedDids.has(dev.device_id) ? ' selected' : '';
  const cbCls  = _selectedDids.has(dev.device_id) ? 'dc-sel-cb checked' : 'dc-sel-cb';
  return `<div class="dc-list-row ${st}${selCls}" id="dpl-${dev.device_id}" onclick="_cardClick(event,'${dev.device_id}')">
    <div class="${cbCls}" onclick="event.stopPropagation();_toggleCard(event,'${dev.device_id}')"></div>
    <div class="dlr-dot"></div>
    <div class="dlr-name">${esc(dev.name)}</div>
    <div class="dlr-host">${esc(dev.host)}</div>
    <div class="dlr-sensors">${snrHtml}</div>
    ${_devSnrSummaryHtml(dev.device_id)}
  </div>`;
}

function updateCardStatus(did,st){
  const lbl={up:'Up',down:'Down',warn:'Warning',unknown:'Unknown'}[st]||st;
  const card=document.getElementById(`dp-${did}`);
  if(card){
    // Preserve selected + dcard classes when swapping status
    const wasSel = card.classList.contains('selected');
    card.className=`dc dcard ${st}${wasSel?' selected':''}`;
    const bar=document.getElementById(`dcbar-${did}`);
    if(bar)bar.className=`dc-bar dcard-stripe ${st}`;
    const badge=document.getElementById(`dcst-${did}`);
    if(badge){badge.className=`dc-status ${st}`;badge.innerHTML=`<div class="dc-sdot ${st==='up'?'up':''}"></div>${lbl}`;}
  }
  // Also update list row
  const lr=document.getElementById(`dpl-${did}`);
  if(lr) lr.className=`dc-list-row ${st}`;
  // Refresh summary badge
  const sumEl=document.getElementById(`dlr-sum-${did}`);
  if(sumEl){ const h=_devSnrSummaryHtml(did); if(h) sumEl.outerHTML=h; }
  // Refresh the group header status-pill summary (live)
  const dev=S.devices[did];
  if(dev?.group && typeof _updateGrpSummary==='function') _updateGrpSummary(dev.group);
}

function updateCardSensor(s){
  const full = S.sensors[`${s.device_id}/${s.sensor_id}`] || s; // ← use full object
  const vEl = document.getElementById(`csv-${s.device_id}_${s.sensor_id}`);
  const dEl = document.getElementById(`csd-${s.device_id}_${s.sensor_id}`);
  if(vEl){
    const isSnmp = full.stype==='snmp'||full.stype==='dns';
    const isVmware = full.stype==='vmware';
    let v;
    if(isVmware){
      if(full.last_value==null) v='—';
      else { const nv=parseFloat(full.last_value); v=isNaN(nv)?(full.last_value+'').slice(0,10):_fmtVmVal(nv,_VM_UNITS[full.vmware_metric]||''); }
    } else if(full.stype==='snmp' && typeof _snmpTileValue === 'function'){
      v=_snmpTileValue(full);
    } else if(isSnmp){
      v=full.alive===false?'FAIL':(full.last_value||'—').slice(0,10);
    } else {
      v=full.last_ms!=null?`${full.last_ms} ms`:(full.alive===false?'DOWN':'—');
    }
    vEl.textContent=v;
    // Detect new .stile-val vs legacy .dc-snr-val and update classes appropriately
    if (vEl.classList.contains('stile-val')) {
      let stStr = 'up';
      if (full.alive === false) stStr = 'down';
      else if (isVmware || isSnmp) stStr = full.alive === true ? 'up' : 'pause';
      else if (full.last_ms != null) {
        const cc = typeof msC === 'function' ? msC(full.last_ms, full) : 'g';
        stStr = cc === 'b' ? 'down' : cc === 'w' ? 'warn' : cc === 'g' ? 'up' : 'pause';
      } else { stStr = 'pause'; }
      const tile = vEl.closest('.stile');
      if (tile) tile.className = `stile ${stStr}`;
      if (dEl) dEl.className = `dot ${stStr}`;
    } else {
      const c = full.alive===false?'b':((isSnmp||isVmware)?(full.alive===true?'g':'m'):(full.last_ms!=null?msC(full.last_ms,full):'m'));
      vEl.className=`dc-snr-val ${c}`;
      if (dEl) dEl.className = `dc-snr-dot ${s.alive===true?'up':s.alive===false?'down':''}`;
    }
  }
  // Also update list row sensor value
  const lsv=document.getElementById(`lsv-${s.device_id}_${s.sensor_id}`);
  if(lsv){
    const isSnmp2=full.stype==='snmp'||full.stype==='dns';
    const isVm2=full.stype==='vmware';
    let v2;
    if(isVm2){ if(full.last_value==null) v2='\u2014'; else { const nv=parseFloat(full.last_value); v2=isNaN(nv)?(full.last_value+'').slice(0,10):_fmtVmVal(nv,_VM_UNITS[full.vmware_metric]||''); } }
    else if(full.stype==='snmp' && typeof _snmpTileValue === 'function'){ v2=_snmpTileValue(full); }
    else if(isSnmp2){ v2=full.alive===false?'FAIL':(full.last_value||'\u2014').slice(0,10); }
    else { v2=full.last_ms!=null?`${full.last_ms}ms`:(full.alive===false?'DOWN':'\u2014'); }
    const c2=full.alive===false?'b':((isSnmp2||isVm2)?(full.alive===true?'g':'m'):(full.last_ms!=null?msC(full.last_ms,full):'m'));
    lsv.textContent=v2; lsv.className=`dc-snr-val ${c2}`;
  }
  // Refresh summary badge for this device
  const sumEl2=document.getElementById(`dlr-sum-${s.device_id}`);
  if(sumEl2){ const h=_devSnrSummaryHtml(s.device_id); if(h) sumEl2.outerHTML=h; }
}

//�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? DRAG AND DROP �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?
let dragDid=null, dragEl=null, dropIndicator=null;
let _dragGrp=null, _dragGrpEl=null, _grpDragOK=false, _grpDragging=false;
// Reset drag-from-handle flag on any mouseup (handles aborted drags)
document.addEventListener('mouseup',()=>{ _grpDragOK=false; });
// Edge-scroll helper: called from onGrpDragOver to scroll #dpanels
// when the cursor is near the top or bottom of the container.
function _grpEdgeScroll(clientY){
  const dp=document.getElementById('dpanels');
  if(!dp) return;
  const r=dp.getBoundingClientRect();
  const zone=80; // px from edge that triggers scrolling
  const speed=12;
  if(clientY<r.top+zone)     dp.scrollTop-=speed;
  else if(clientY>r.bottom-zone) dp.scrollTop+=speed;
}
// Also forward mouse-wheel events directly to #dpanels while a group is being
// dragged — the HTML5 drag API suppresses default browser scroll so we do it
// ourselves.  passive:true means we never block the event; we just piggyback.
document.addEventListener('wheel', e=>{
  if(!_grpDragging) return;
  const dp=document.getElementById('dpanels');
  if(dp) dp.scrollTop += e.deltaY;
},{passive:true});

function applyDrag(card){
  card.setAttribute('draggable','true');
  card.addEventListener('dragstart',onDragStart);
  card.addEventListener('dragend',onDragEnd);
}

function onDragStart(e){
  if(e.target.tagName==='BUTTON'||e.target.closest('button')){e.preventDefault();return;}
  dragDid=e.currentTarget.id.replace('dp-','');
  dragEl=e.currentTarget;
  setTimeout(()=>dragEl&&dragEl.classList.add('dc-dragging'),0);
  e.dataTransfer.effectAllowed='move';
  e.dataTransfer.setData('text/plain',dragDid);
}

function onDragEnd(){
  if(dragEl) dragEl.classList.remove('dc-dragging');
  if(dropIndicator){dropIndicator.remove();dropIndicator=null;}
  document.querySelectorAll('.grp-grid-over').forEach(g=>g.classList.remove('grp-grid-over'));
  dragDid=null; dragEl=null;
}

function onDragLeave(e){
  const grid=e.currentTarget;
  if(!grid.contains(e.relatedTarget)){
    grid.classList.remove('grp-grid-over');
    if(dropIndicator&&dropIndicator.parentNode===grid){dropIndicator.remove();dropIndicator=null;}
  }
}

function onDragOver(e){
  if(_dragGrpEl) return; // group drag in progress — ignore card-grid events
  e.preventDefault();
  e.dataTransfer.dropEffect='move';
  const grid=e.currentTarget;
  grid.classList.add('grp-grid-over');
  if(!dropIndicator){
    dropIndicator=document.createElement('div');
    dropIndicator.className='dc dc-drop-indicator';
    dropIndicator.style.minHeight='170px';
  }
  const after=getDragAfter(grid,e.clientX,e.clientY);
  const addBtn=grid.querySelector('.dc-add');
  if(after) grid.insertBefore(dropIndicator,after);
  else       grid.insertBefore(dropIndicator,addBtn);
}

function onDrop(e){
  if(_dragGrpEl) return; // group drag handled by onGrpDrop
  e.preventDefault();
  const grid=e.currentTarget;
  const group=grid.dataset.group;
  const site =grid.dataset.site || '';
  grid.classList.remove('grp-grid-over');
  if(dropIndicator){
    // insert real card where indicator is
    if(dragEl) grid.insertBefore(dragEl,dropIndicator);
    dropIndicator.remove(); dropIndicator=null;
  }
  if(!dragEl||!dragDid) return;
  dragEl.classList.remove('dc-dragging');
  const did=dragDid;
  const dev=S.devices[did]; if(!dev) return;
  const groupChanged = (dev.group||'Default Group')!==group;
  const siteChanged  = (dev.site||'')!==site;
  if (groupChanged || siteChanged) {
    const patch = {};
    if (groupChanged) { dev.group = group; patch.group = group; }
    if (siteChanged)  { dev.site  = site;  patch.site  = site;  }
    api('PATCH','/api/device/'+did, patch);
    if (siteChanged) window._pwSitesCache = null;
  }
  refreshGroupCounts();
  pruneEmptyGroups();
  refreshSiteCounts();
}

// ── Group drag-to-reorder ─────────────────────────────────────────
function applyGrpDrag(wrap){
  wrap.setAttribute('draggable','true');
  wrap.addEventListener('dragstart',onGrpDragStart);
  wrap.addEventListener('dragend',onGrpDragEnd);
  wrap.addEventListener('dragover',onGrpDragOver);
  wrap.addEventListener('drop',onGrpDrop);
}

function onGrpDragStart(e){
  if(e.target.closest('.dc')) return; // let card drag proceed normally
  if(!_grpDragOK){ e.preventDefault(); return; }
  _grpDragOK=false;
  _grpDragging=true;
  e.stopPropagation();
  _dragGrpEl=e.currentTarget;
  _dragGrp=_dragGrpEl.querySelector('.grp-grid')?.dataset.group||null;
  setTimeout(()=>_dragGrpEl&&_dragGrpEl.classList.add('grp-dragging'),0);
  e.dataTransfer.effectAllowed='move';
  e.dataTransfer.setData('text/plain','grp:'+(_dragGrp||''));
}

function onGrpDragEnd(){
  _grpDragging=false;
  if(_dragGrpEl){ _dragGrpEl.classList.remove('grp-dragging'); saveGroupOrder(); }
  _dragGrpEl=null; _dragGrp=null;
}

function onGrpDragOver(e){
  if(!_dragGrpEl) return;
  e.preventDefault(); e.stopPropagation();
  e.dataTransfer.dropEffect='move';
  _grpEdgeScroll(e.clientY); // scroll container when near its edges
  const target=e.currentTarget;
  if(target===_dragGrpEl) return;
  // Group reorder is constrained to within a single site for v1.
  // Cross-site moves require explicit Edit Device / bulk-move flows.
  if(target.parentNode !== _dragGrpEl.parentNode) return;
  const parent=target.parentNode;  // .site-body
  const rect=target.getBoundingClientRect();
  if(e.clientY<rect.top+rect.height/2){
    parent.insertBefore(_dragGrpEl,target);
  } else {
    parent.insertBefore(_dragGrpEl,target.nextSibling);
  }
}

function onGrpDrop(e){
  if(!_dragGrpEl) return;
  e.preventDefault(); e.stopPropagation();
}

function saveGroupOrder(){
  // Order key is the composite (site,group) — same group name across sites
  // can be reordered independently within each site's body.
  const order=[...document.querySelectorAll('.grp-wrap')].map(w=>{
    const g=w.querySelector('.grp-grid');
    return g?(g.dataset.dgkey || g.dataset.group):null;
  }).filter(Boolean);
  _lsSet('pw-grp-order', order);
}

function restoreGroupOrder(){
  const dpanels=document.getElementById('dpanels');
  if(!dpanels) return;
  const order=_lsGet('pw-grp-order', []);
  if (!order.length) return;
  // Reorder groups within each site body — the site wrapper is the parent
  // now, not #dpanels. Group keys are composite (sitegroup) so the
  // saved key uniquely identifies which site's wrap to move.
  const knownIds=new Set(order.map(k => grpId(k)));
  const beforeWraps=[...dpanels.querySelectorAll('.grp-wrap')];
  order.forEach(key=>{
    const wrap=document.getElementById(grpId(key));
    if(!wrap) return;
    const parent=wrap.parentNode; // .site-body
    if(parent) parent.appendChild(wrap);
  });
  // Newcomers stay in their pre-existing parent order.
  beforeWraps.forEach(w => {
    if(!knownIds.has(w.id) && w.parentNode) w.parentNode.appendChild(w);
  });
}
// ─────────────────────────────────────────────────────────────────

function getDragAfter(grid,x,y){
  const cards=[...grid.querySelectorAll('.dc:not(.dc-add):not(.dc-dragging):not(.dc-drop-indicator)')];
  const rowCards=cards.filter(c=>{
    const r=c.getBoundingClientRect();
    return y>=r.top && y<=r.bottom;
  });
  if(rowCards.length){
    for(const c of rowCards){
      const r=c.getBoundingClientRect();
      if(x<r.left+r.width/2) return c;
    }
    const last=rowCards[rowCards.length-1];
    return cards[cards.indexOf(last)+1]||null;
  }
  for(const c of cards){
    const r=c.getBoundingClientRect();
    if(y<r.top+r.height/2) return c;
  }
  return null;
}

// ── Rename group ─────────────────────────────────────────────────
function renameGroup(labelEl, oldName){
  const newName=prompt('Rename group:',oldName);
  if(!newName||newName===oldName) return;
  // Update all devices that carry this group name (across any sites).
  Object.values(S.devices).filter(d=>(d.group||'Default Group')===oldName).forEach(d=>{
    d.group=newName;
    api('PATCH','/api/device/'+d.device_id,{group:newName});
  });
  // Patch the DOM without re-rendering — there may be one wrap per site
  // that previously contained this group name.
  document.querySelectorAll(`.grp-wrap[data-grp-name="${CSS.escape(oldName)}"]`).forEach(wrap => {
    const site = wrap.dataset.site || '';
    const newKey = _dgKey(site, newName);
    const grid = wrap.querySelector('.grp-grid');
    if (grid) {
      grid.dataset.group = newName;
      grid.dataset.dgkey = newKey;
      grid.id = gridId(newKey);
    }
    wrap.dataset.grpName = newName;
    wrap.dataset.dgkey   = newKey;
    wrap.id = grpId(newKey);
    // Update the click handler on the add-device card
    const addCard = wrap.querySelector('.dc-add');
    if (addCard) {
      addCard.replaceWith(addCard.cloneNode(true));
      const fresh = wrap.querySelector('.dc-add');
      fresh.addEventListener('click', function(){ openAddDeviceGroup(newName); });
    }
    // Update the label text
    const lbl = wrap.querySelector('.grp-label');
    if (lbl) { lbl.replaceWith(lbl.cloneNode(true)); wrap.querySelector('.grp-label').textContent = newName; }
    // Rebind gear button so it carries the new group name
    const gear = wrap.querySelector('.grp-edit-btn');
    if (gear) {
      gear.replaceWith(gear.cloneNode(true));
      const freshGear = wrap.querySelector('.grp-edit-btn');
      freshGear.addEventListener('click', function(e){
        e.stopPropagation();
        if (typeof openEditGroup === 'function') openEditGroup(newName);
      });
    }
    cntId_refresh(wrap, newKey);
  });
  toast('Group renamed to "'+newName+'"','ok');
}

function cntId_refresh(wrap, key){
  const oldCnt=wrap.querySelector('.grp-count');
  if(oldCnt){ oldCnt.id=cntId(key); }
}

// ── Add Device pre-filled with a group ───────────────────────────
function openAddDeviceGroup(group){
  openAddDevice();
  setTimeout(()=>{const f=document.getElementById('ad-g');if(f)f.value=group;},40);
}

// ── Add Group modal ──────────────────────────────────────────────
// Mirrors the Edit Group modal's field set (Site, Device Icon, Mute) so a
// user can fully configure a group at creation time instead of having to
// reopen it for editing. Alert Profile is omitted — it's an inheritance
// viewer that needs an existing group to scope against.
function openAddGroup(){
  closeM('mag');
  const o=document.createElement('div');
  o.className='mo';o.id='mag';
  _overlayClose(o, ()=>closeM('mag'));
  o.innerHTML=`
  <div class="mbox" style="min-width:520px;max-width:600px">
    <div class="mhd">
      <div class="mttl">Add Group</div>
      <button class="mclose" onclick="closeM('mag')">✕</button>
    </div>
    <div class="mbdy">
      <div class="alrt-section">
        <div class="alrt-section-hdr">Group Name</div>
        <div class="fr">
          <input type="text" id="ag-n" placeholder="e.g. Production, Office, Lab…" autocomplete="off"/>
          <div class="fh">A new empty group section will appear on the dashboard.</div>
        </div>
      </div>

      <div class="alrt-section">
        <div class="alrt-section-hdr">Site</div>
        <div class="fr">
          <input type="text" id="ag-site" list="ag-site-dl" placeholder="HQ, DR-Site-2…" autocomplete="off"/>
          <datalist id="ag-site-dl"></datalist>
          <div class="fh">
            Where the empty group section will live in the sidebar. Leave blank for Unsited.
            Future devices added to this group will not auto-inherit this — set per-device on Add Device.
          </div>
        </div>
      </div>

      <div class="alrt-section">
        <div class="alrt-section-hdr">Device Icon (NTM Live map)</div>
        <div class="fr">
          <select id="ag-icon">
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
            Per-device icon overrides (set from the NTM panel) still take precedence.
          </div>
        </div>
      </div>

      <div class="alrt-section">
        <div class="alrt-section-hdr">Alerts</div>
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none">
          <input type="checkbox" id="ag-muted"/>
          <span>🔕 Mute alerts for this group</span>
        </label>
        <div class="fh" style="margin-top:4px">
          Suppresses alert dispatch and flap events for every device and sensor in this group.
          Probes still run and device cards still reflect their real status.
        </div>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mag')">Cancel</button>
      <button class="btn-p" onclick="submitAddGroup()">Create Group</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  // Populate the Site datalist from /api/sites (UNION of IPAM + devices)
  if (typeof _populateSiteDatalist === 'function') _populateSiteDatalist('ag-site-dl');
  setTimeout(()=>{
    const inp=document.getElementById('ag-n');
    if(inp){
      inp.focus();
      inp.addEventListener('keydown',e=>{ if(e.key==='Enter') submitAddGroup(); });
    }
  },40);
}

async function submitAddGroup(){
  const name = (document.getElementById('ag-n')?.value || '').trim();
  if(!name){ toast('Group name is required','err'); return; }
  const site  = (document.getElementById('ag-site')?.value || '').trim().slice(0, 80);
  const icon  = (document.getElementById('ag-icon')?.value || '').trim();
  const muted = !!document.getElementById('ag-muted')?.checked;

  const exists = document.getElementById(grpId(_dgKey(site, name)));
  if(exists){ toast('Group already exists','err'); return; }

  ensureGroupSection(name, site);
  // Persist the new group at the END of the saved order. Without this, the
  // group is unsaved and the next restoreGroupOrder() pass would push every
  // saved group past it (appendChild reflow), making the new group jump to
  // the top of the device list — which is the opposite of what users expect.
  saveGroupOrder();
  // Make dpanels visible if it wasn't
  document.getElementById('emptyMain').style.display='none';
  if(activeMainTab==='devices'){
    document.getElementById('dpanels').style.display='';
    document.getElementById('devActBar').style.display='';
  }

  // Persist the group-level Device Icon default (if set). Same payload shape
  // the Edit Group modal uses (pw_group_icons settings key + postMessage to
  // the NTM iframe so it re-renders without a page reload).
  if (icon) {
    try {
      const cur = await api('GET', '/api/settings/pw_group_icons').catch(() => null);
      const map = (cur && cur.value && typeof cur.value === 'object') ? { ...cur.value } : {};
      map[name] = icon;
      await api('PATCH', '/api/settings/pw_group_icons', { value: map });
      const _mf = document.getElementById('map-frame');
      _mf?.contentWindow?.postMessage({ type: 'pw_group_icons', value: map }, window.location.origin);
    } catch (e) {
      toast('Icon save failed: ' + (e.message || e), 'err');
      // Still proceed — the group exists; the user can re-set the icon later
    }
  }

  // Persist the mute state if requested. The mute API keys on the group name
  // (no group_id), so we just POST with the freshly chosen name.
  if (muted) {
    try {
      await api('POST', '/api/device-group/' + encodeURIComponent(name) + '/mute', { muted: true });
      if (typeof _setGroupMutedLocal === 'function') _setGroupMutedLocal(name, true);
    } catch (e) {
      toast('Mute save failed: ' + (e.message || e), 'err');
    }
  }

  closeM('mag');
  toast('Group "'+name+'" created','ok');
  // Scroll the new group into view (at the chosen site, or Unsited bucket)
  setTimeout(()=>{
    const wrap=document.getElementById(grpId(_dgKey(site, name)));
    if(wrap) wrap.scrollIntoView({behavior:'smooth',block:'start'});
  },80);
}
// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? DEVICE WINDOW �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?
function openDevWin(did){
  const dev=S.devices[did]; if(!dev)return;
  closeM('dwo');
  const st=dev.status||'unknown';
  const o=document.createElement('div');
  o.className='dwo';o.id='dwo';
  o.onclick=e=>{if(e.target===o)closeM('dwo')};
  o.innerHTML=`
  <div class="dwbox">
    <div class="dw-hd">
      <div class="dw-bar ${st}" id="dwbar-${did}"></div>
      <div class="dw-inf">
        <div class="dw-nm">${esc(dev.name)}</div>
        <div class="dw-hs">${esc(dev.host)} <span style="color:var(--text3);margin-left:8px">${esc(dev.group||'')}</span></div>
      </div>
      <div class="dw-acts">
        <button class="dp-btn s" onclick="startDev('${did}')">▶ Start all</button>
        <button class="dp-btn"   onclick="stopDev('${did}')">■ Stop all</button>
        <button class="dp-btn"   onclick="openAddSensor('${did}')">＋ Sensor</button>
        <button class="dp-btn rbac-op" onclick="openScanModal('${did}')">⊕ Scan</button>
        <button class="dp-btn"   onclick="openEditDevice('${did}')">✎ Edit</button>
        <button class="dp-btn d" onclick="closeM('dwo');delDev('${did}')">✕ Delete</button>
        <button class="mclose"   onclick="closeM('dwo')">✕</button>
      </div>
    </div>
    <div class="dw-tabs">
      <button class="dw-tab active" id="dwtab-sensors-${did}" onclick="dwSwitchTab('${did}','sensors')">⊞ Sensors</button>
      <button class="dw-tab"        id="dwtab-log-${did}"     onclick="dwSwitchTab('${did}','log')">▸ Event Log</button>
    </div>
    <div class="dw-body" id="dwbody-sensors-${did}">
      <div class="sg" id="sg-${did}"></div>
      <div class="add-snr" onclick="openAddSensor('${did}')">＋ Add sensor — Ping · TCP Port · HTTP/S · SNMP</div>
    </div>
    <div class="dw-body" id="dwbody-log-${did}" style="display:none;flex-direction:column">
      <div class="dw-log-toolbar">
        <span class="dw-log-info" id="dwlog-info-${did}">Failure events for this device</span>
        <button class="dp-btn" onclick="dwClearLog('${did}')">✕ Clear</button>
      </div>
      <div class="dw-log-body" id="dwlog-${did}"></div>
    </div>
    <div class="dw-ft">
      <button class="btn-s" onclick="closeM('dwo')">Close</button>
    </div>
  </div>`;
  document.body.appendChild(o);

  // ── Show skeleton tiles immediately (avoids flash of stale cache) ──
  const _sg = document.getElementById('sg-'+did);
  const _skelN = (dev.sensors||[]).length || 2;
  if(_sg) _sg.innerHTML = Array.from({length:_skelN},()=>'<div class="stl stl-skel"></div>').join('');

  // ── Fetch fresh device data + logs in parallel ────────────────────
  const _devFetch  = fetch('/api/device/'+did).then(r=>r.json()).catch(()=>null);
  const _logsFetch = fetch('/api/device/'+did+'/logs').then(r=>r.json()).catch(()=>({logs:[]}));

  _devFetch.then(freshDev=>{
    if(!document.getElementById('dwo')) return; // panel closed while loading
    if(_sg) _sg.innerHTML=''; // clear skeletons
    _initSensorGrid(did);
    if(freshDev){
      S.devices[did]=freshDev;
      (freshDev.sensors||[]).forEach(s=>{
        S.sensors[`${did}/${s.sensor_id}`]=s;
        renderTile(did,s);
      });
    } else {
      // fetch failed — render from cache silently
      (S.devices[did]?.sensors||[]).forEach(s=>renderTile(did,s));
    }
    _applySensorOrder(did);
    _vmApplySavedOrders(did);
    setupChartsByDid(did);
  });

  _logsFetch.then(data=>{
    if(!document.getElementById('dwo')) return;
    (data.logs||[]).forEach(e=>{
      const key=did+'/'+e.sid;
      if(!S.logs[key]) S.logs[key]=[];
      const exists=S.logs[key].some(x=>x.ts===e.ts&&x.msg===e.msg);
      if(!exists) S.logs[key].push(e);
    });
    Object.keys(S.logs).filter(k=>k.startsWith(did+'/')).forEach(k=>{
      S.logs[k].sort((a,b)=>new Date(b.ts)-new Date(a.ts));
      if(S.logs[k].length>200) S.logs[k]=S.logs[k].slice(0,200);
    });
    renderDevLog(did);
  });
}

function dwSwitchTab(did, tab){
  ['sensors','log'].forEach(t=>{
    const body=document.getElementById('dwbody-'+t+'-'+did);
    const btn=document.getElementById('dwtab-'+t+'-'+did);
    if(body) body.style.display = t===tab ? (t==='log'?'flex':'') : 'none';
    if(btn)  btn.classList.toggle('active', t===tab);
  });
  if(tab==='log') renderDevLog(did);
}

function renderDevLog(did){
  const el=document.getElementById('dwlog-'+did);
  if(!el) return;
  el.innerHTML='';
  // Gather only failure logs from all sensors of this device, sorted newest-first
  const allLogs=[];
  const dev=S.devices[did]; if(!dev) return;
  dev.sensors.forEach(s=>{
    const key=did+'/'+s.sensor_id;
    (S.logs[key]||[]).filter(e=>e.type==='err').forEach(e=>allLogs.push({...e, sname:s.name, stype:s.stype}));
  });
  (S.devTraps&&S.devTraps[did]||[]).forEach(e=>allLogs.push({...e,sname:'',stype:'trap'}));
  allLogs.sort((a,b)=>new Date(b.ts)-new Date(a.ts));
  if(!allLogs.length){
    el.innerHTML='<div class="dw-log-empty">No failures recorded yet. All sensors are healthy.</div>';
    return;
  }
  const ico={ping:'◉',tcp:'⇌',http:'◈',snmp:'◎',dns:'⬡',tls:'T',http_keyword:'K',banner:'B',trap:'⚡'};
  allLogs.slice(0,500).forEach(e=>{
    const row=document.createElement('div');
    row.className='dw-ll '+e.type;
    row.innerHTML=
      '<span class="dw-ll-ts">['+fmtTs(e.ts)+']</span> '+
      '<span class="dw-ll-snr">'+ico[e.stype||'ping']+(e.sname?' '+esc(e.sname):'')+'</span> '+
      '<span class="dw-ll-msg">'+esc(e.msg)+'</span>';
    el.appendChild(row);
  });
  const info=document.getElementById('dwlog-info-'+did);
  if(info){
    const failCnt=allLogs.filter(e=>e.type==='err').length;
    const trapCnt=allLogs.filter(e=>e.type==='trap').length;
    let summary='';
    if(failCnt) summary+=failCnt+' failure'+(failCnt!==1?'s':'');
    if(trapCnt) summary+=(summary?' · ':'')+trapCnt+' trap'+(trapCnt!==1?'s':'');
    info.textContent=(summary||'No events')+' across '+dev.sensors.length+' sensor'+(dev.sensors.length!==1?'s':'');
  }
}

async function dwClearLog(did){
  const dev=S.devices[did]; if(!dev) return;
  const btn=document.querySelector(`#dwbody-log-${did} .dp-btn`);
  if(btn){btn.disabled=true;btn.textContent='Clearing...';}
  await api('DELETE',`/api/device/${did}/logs`);
  if(btn){btn.disabled=false;btn.textContent='✕ Clear';}
  dev.sensors.forEach(s=>{ S.logs[did+'/'+s.sensor_id]=[]; });
  if(S.devTraps) S.devTraps[did]=[];
  // Remove this device's traps from the global FLAPS array and re-render Events tab
  const devHost=dev.host;
  for(let i=FLAPS.length-1;i>=0;i--){
    if(FLAPS[i]._direction==='trap'&&FLAPS[i].src_ip===devHost) FLAPS.splice(i,1);
  }
  renderFlaps();
  renderDevLog(did);
}

// Live-update the log tab when new entries arrive (if window is open on log tab)
function maybeUpdateDevLog(did){
  const logBody=document.getElementById('dwbody-log-'+did);
  if(logBody && logBody.style.display!=='none') renderDevLog(did);
}

function setupChartsByDid(did){
  setTimeout(()=>{
    const dev=S.devices[did];if(!dev)return;
    dev.sensors.forEach(s=>{
      const key=`${did}/${s.sensor_id}`;
      const t=document.getElementById(`t-${key.replace('/','_')}`);
      if(!t)return;
      const c=t.querySelector('canvas.spk');
      if(c){S.charts[key]={canvas:c,ctx:c.getContext('2d')};if(s.history&&s.history.length>1)drawSpk(key,s.history);}
    });
  },50);
}

// ── Status filter + pagination state ─────────────────────────────
let _activeStatusFilter='all';
let _devPage=0;
let _devPageSize=parseInt(localStorage.getItem('pw_page_size')||'50');
let _filteredDids=[];

// ── Status filter pills ───────────────────────────────────────────
function _setStatusFilter(st){
  _activeStatusFilter=st;
  document.querySelectorAll('.dev-status-pill').forEach(p=>
    p.classList.toggle('active', p.dataset.st===st));
  _applyDevFilter(document.getElementById('devSearch')?.value||'');
}

function _updateStatusPills(){
  const counts={all:0,up:0,down:0,warn:0,pause:0};
  const groups=new Set();
  for(const did in S.devices){
    counts.all++;
    const st=(S.devices[did].status||'unknown').toLowerCase();
    if(counts[st]!==undefined) counts[st]++;
    const g=S.devices[did].group||'Default Group';
    groups.add(g);
  }
  document.querySelectorAll('.dev-status-pill').forEach(p=>{
    const ct=p.querySelector('.pill-ct');
    if(ct) ct.textContent=counts[p.dataset.st]??0;
  });
  // Page-head sub line — shows live device + group count
  const sub=document.getElementById('devSub');
  if(sub){
    const g=groups.size;
    sub.textContent=`${counts.all} device${counts.all!==1?'s':''} across ${g} group${g!==1?'s':''}`;
  }
}

// ── Device search / filter ────────────────────────────────────────
function _applyDevFilter(query){
  const q=(query||'').trim().toLowerCase();
  const sf=_activeStatusFilter;
  // Build ordered list of matching device IDs (preserving DOM order)
  _filteredDids=[];
  document.querySelectorAll('.dc:not(.dc-add)').forEach(card=>{
    const did=card.id.replace('dp-','');
    if(!S.devices[did]) return;
    const dev=S.devices[did];
    const stMatch=sf==='all'||(dev.status||'unknown').toLowerCase()===sf;
    if(!stMatch) return;
    if(q){
      const nameMatch=dev.name.toLowerCase().includes(q);
      const hostMatch=(dev.host||'').toLowerCase().includes(q);
      const secIpMatch=(dev.secondary_ips||[]).some(ip=>ip.toLowerCase().includes(q));
      const sensorMatch=S._devSensors[did]&&[...S._devSensors[did]]
        .some(k=>S.sensors[k]&&S.sensors[k].name.toLowerCase().includes(q));
      if(!nameMatch&&!hostMatch&&!secIpMatch&&!sensorMatch) return;
    }
    _filteredDids.push(did);
  });
  _devPage=0;
  _renderPage();
  // Bulk-bar "hidden by filter" counter depends on card visibility.
  if (_selectMode) _updateBulkBar();
}

function _renderPage(){
  // Pagination only applies to list view — grid view shows every filtered
  // device. Without this guard, switching from a paginated list view back
  // to grid silently caps the grid at the list's per-page size, because
  // the slice below runs regardless of which view is visible. The
  // pagination CONTROLS were already gated by view in _renderPagination(),
  // but the underlying slice was not — that asymmetry was the bug.
  const isList = _devView === 'list';
  const start  = isList ? _devPage * _devPageSize : 0;
  const slice  = isList ? _filteredDids.slice(start, start + _devPageSize) : _filteredDids;
  const visible=new Set(slice);
  const allDids=new Set(_filteredDids);
  // Show/hide individual cards and list rows
  document.querySelectorAll('.dc:not(.dc-add)').forEach(card=>{
    const did=card.id.replace('dp-','');
    card.style.display=visible.has(did)?'':'none';
  });
  document.querySelectorAll('.dc-list-row').forEach(row=>{
    const did=row.id.replace('dpl-','');
    row.style.display=visible.has(did)?'':'none';
  });
  // Hide groups with no visible devices on this page
  document.querySelectorAll('.grp-wrap').forEach(wrap=>{
    const grid=wrap.querySelector('.grp-grid');
    if(!grid){wrap.style.display='';return;}
    const hasVisible=[...grid.querySelectorAll('.dc:not(.dc-add)')]
      .some(c=>visible.has(c.id.replace('dp-','')));
    wrap.style.display=hasVisible?'':'none';
  });
  // No-results message
  let noRes=document.getElementById('devNoResults');
  const anyVisible=visible.size>0;
  if(!anyVisible){
    if(!noRes){
      noRes=document.createElement('div');
      noRes.id='devNoResults';
      noRes.className='dev-no-results';
      const dp=document.getElementById('dpanels');
      if(dp) dp.parentNode.insertBefore(noRes,dp.nextSibling);
    }
    const q=document.getElementById('devSearch')?.value||'';
    noRes.textContent=q||_activeStatusFilter!=='all'
      ?'No devices match the current filter.'
      :'No devices yet.';
    noRes.style.display='';
  } else if(noRes){
    noRes.style.display='none';
  }
  _renderPagination();
}

function _renderPagination(){
  const pg=document.getElementById('devPagination');
  if(!pg) return;
  if(_devView!=='list'||activeMainTab!=='devices'){pg.style.display='none';return;}
  const total=_filteredDids.length;
  const pages=Math.ceil(total/_devPageSize)||1;
  if(total<=_devPageSize){pg.style.display='none';return;}
  pg.style.display='flex';
  const start=_devPage*_devPageSize+1;
  const end=Math.min(start+_devPageSize-1,total);
  pg.innerHTML=`
    <button class="dev-pg-btn" onclick="_devGoPage(${_devPage-1})" ${_devPage===0?'disabled':''}>‹ Prev</button>
    <span class="dev-pg-info">${start}–${end} of ${total} devices</span>
    <button class="dev-pg-btn" onclick="_devGoPage(${_devPage+1})" ${_devPage>=pages-1?'disabled':''}>Next ›</button>
    <select class="dev-pg-size" onchange="_devSetPageSize(+this.value)" title="Devices per page">
      ${[25,50,100].map(n=>`<option value="${n}"${n===_devPageSize?' selected':''}>${n}/page</option>`).join('')}
    </select>`;
}

function _devGoPage(p){
  const pages=Math.ceil(_filteredDids.length/_devPageSize)||1;
  _devPage=Math.max(0,Math.min(p,pages-1));
  _renderPage();
}

function _devSetPageSize(n){
  _devPageSize=n;
  localStorage.setItem('pw_page_size',n);
  _devPage=0;
  _renderPage();
}

// Ctrl+F / Cmd+F focuses the device search when the devices tab is active
document.addEventListener('keydown', e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='f'&&activeMainTab==='devices'){
    const inp=document.getElementById('devSearch');
    if(inp){ e.preventDefault(); inp.focus(); inp.select(); }
  }
  // Ctrl+A (or Cmd+A) — select all visible devices on the Devices tab.
  // Only fires when not typing into an input/textarea/search box. Pressing
  // it turns select mode on automatically if it wasn't already.
  const isInput = e.target && (e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.isContentEditable);
  if((e.ctrlKey||e.metaKey)&&e.key==='a'&&activeMainTab==='devices'&&!isInput){
    e.preventDefault();
    if (!_selectMode){
      _selectMode = true;
      document.body.classList.add('pw-select-mode');
      document.getElementById('btnSelectMode')?.classList.add('active');
    }
    // Select every currently-visible card (respects status-pill + search filter).
    document.querySelectorAll('.dc:not(.dc-add)').forEach(c => {
      if (c.style.display === 'none') return;
      const did = c.id.replace('dp-','');
      _selectedDids.add(did);
    });
    _refreshAllCardSelVisuals();
    _refreshAllGroupSelVisuals();
    _updateBulkBar();
  }
  // Esc — exit select mode. Only on Devices tab and only when no modal is
  // open (modals have their own Esc close handlers that we shouldn't steal).
  if(e.key==='Escape'&&activeMainTab==='devices'&&!isInput&&_selectMode){
    const anyModal = document.querySelector('.mo');
    if (!anyModal) _exitSelectMode();
  }
});

