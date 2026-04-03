// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? GROUP GRID �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?

function grpId(g){ return 'grp-'+btoa(unescape(encodeURIComponent(g))).replace(/[^a-z0-9]/gi,''); }
function gridId(g){ return 'gg-'+btoa(unescape(encodeURIComponent(g))).replace(/[^a-z0-9]/gi,''); }
function cntId(g){  return 'gc-'+btoa(unescape(encodeURIComponent(g))).replace(/[^a-z0-9]/gi,''); }

/** Safe localStorage JSON reader — returns fallback on parse error or missing key. */
function _lsGet(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback; }
  catch { return fallback; }
}
function _lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota/private mode */ }
}

function ensureGroupSection(group){
  const id=grpId(group);
  if(document.getElementById(id)) return;
  const gid=gridId(group), gcid=cntId(group);

  const wrap=document.createElement('div');
  wrap.className='grp-wrap'; wrap.id=id;

  // Header
  const hdr=document.createElement('div');
  hdr.className='grp-hdr';

  const _grpCol=new Set(_lsGet('pw-grp-collapsed', []));
  const isCol=_grpCol.has(group);

  const dragH=document.createElement('div');
  dragH.className='grp-drag-handle'; dragH.textContent='⠿'; dragH.title='Drag to reorder groups';
  dragH.addEventListener('mousedown',()=>{ _grpDragOK=true; });

  const line1=document.createElement('div');
  line1.className='grp-line'; line1.style.cssText='max-width:40px;flex:0 0 40px';

  const arr=document.createElement('div');
  arr.className='grp-arr'+(isCol?'':' open');
  arr.title=isCol?'Expand':'Collapse';
  arr.textContent='▶';
  arr.addEventListener('click',function(){ toggleGroup(group); });

  const label=document.createElement('div');
  label.className='grp-label';
  label.title='Double-click to rename';
  label.textContent=group;
  label.addEventListener('dblclick',function(){ renameGroup(label, group); });

  const cnt=document.createElement('div');
  cnt.className='grp-count'; cnt.id=gcid; cnt.textContent='0';

  const line2=document.createElement('div');
  line2.className='grp-line';

  hdr.appendChild(dragH); hdr.appendChild(line1); hdr.appendChild(arr); hdr.appendChild(label);
  hdr.appendChild(cnt);   hdr.appendChild(line2);

  // Grid
  const grid=document.createElement('div');
  grid.className='grp-grid'+(isCol?' collapsed':''); grid.id=gid; grid.dataset.group=group;
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
  applyGrpDrag(wrap);
  document.getElementById('dpanels').appendChild(wrap);
}

function refreshGroupCounts(){
  document.querySelectorAll('.grp-grid').forEach(grid=>{
    const group=grid.dataset.group;
    const cnt=document.getElementById(cntId(group));
    if(cnt) cnt.textContent=grid.querySelectorAll('.dc:not(.dc-add)').length;
  });
}

function toggleGroup(group){
  const grid=document.getElementById(gridId(group));
  const arr=document.querySelector('#'+grpId(group)+' .grp-arr');
  if(!grid) return;
  const nowCol=grid.classList.toggle('collapsed');
  if(arr){arr.classList.toggle('open',!nowCol);arr.title=nowCol?'Expand':'Collapse';}
  const set=new Set(_lsGet('pw-grp-collapsed', []));
  if(nowCol) set.add(group); else set.delete(group);
  _lsSet('pw-grp-collapsed', [...set]);
}

function pruneEmptyGroups(){
  document.querySelectorAll('.grp-wrap').forEach(w=>{
    const grid=w.querySelector('.grp-grid');
    const n=grid?grid.querySelectorAll('.dc:not(.dc-add)').length:0;
    if(n===0) w.remove();
  });
}

function renderDp(dev){
  document.getElementById('emptyMain').style.display='none';
  if(activeMainTab==='devices') document.getElementById('dpanels').style.display='';
  const old=document.getElementById('dp-'+dev.device_id);
  if(old) old.remove();
  const group=dev.group||'Default Group';
  ensureGroupSection(group);
  const grid=document.getElementById(gridId(group));
  const addBtn=grid.querySelector('.dc-add');
  const el=document.createElement('div');
  el.innerHTML=cardHTML(dev);
  const card=el.firstElementChild;
  grid.insertBefore(card,addBtn);
  applyDrag(card);
  dev.sensors.forEach(s=>{ S.sensors[dev.device_id+'/'+s.sensor_id]=s; });
  refreshGroupCounts();
  applyRbac();
  // Re-apply search filter so new/updated cards are filtered correctly
  const _srch=document.getElementById('devSearch');
  if(_srch&&_srch.value) _applyDevFilter(_srch.value);
}

function sSnrPreview(did){
  // return up to 3 sensor preview rows for the card, respecting saved drag order
  const snrs=Object.values(S.sensors).filter(s=>s.device_id===did);
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
    const worst=vms.find(s=>s.alive===false)||vms.find(s=>s.threshold_state&&s.threshold_state!=='ok')||vms[0];
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
    return `<div class="dc-snr">
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
  return `
  <div class="dc ${st}" id="dp-${dev.device_id}" onclick="openDevWin('${dev.device_id}')">
    <div class="dc-bar ${st}" id="dcbar-${dev.device_id}"></div>
    <div class="dc-drag-handle" title="Drag to reorder">⠿</div>
    <div class="dc-body">
      <div>
        <div class="dc-name">${esc(dev.name)}</div>
        <div class="dc-host">${esc(dev.host)}</div>
      </div>
      <div class="dc-status ${st}" id="dcst-${dev.device_id}">
        <div class="dc-sdot ${st==='up'?'up':''}"></div>${lbl}
      </div>
      <div class="dc-sensors" id="dcsnr-${dev.device_id}">
        ${sSnrPreview(dev.device_id)}
      </div>
    </div>
  </div>`;
}

function updateCardStatus(did,st){
  const lbl={up:'Up',down:'Down',warn:'Warning',unknown:'Unknown'}[st]||st;
  const card=document.getElementById(`dp-${did}`);
  if(card){
    card.className=`dc ${st}`;
    const bar=document.getElementById(`dcbar-${did}`);
    if(bar)bar.className=`dc-bar ${st}`;
    const badge=document.getElementById(`dcst-${did}`);
    if(badge){badge.className=`dc-status ${st}`;badge.innerHTML=`<div class="dc-sdot ${st==='up'?'up':''}"></div>${lbl}`;}
  }
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
    } else if(isSnmp){
      v=full.alive===false?'FAIL':(full.last_value||'—').slice(0,10);
    } else {
      v=full.last_ms!=null?`${full.last_ms}ms`:(full.alive===false?'DOWN':'—');
    }
    const c = full.alive===false?'b':((isSnmp||isVmware)?(full.alive===true?'g':'m'):(full.last_ms!=null?msC(full.last_ms,full):'m'));
    vEl.textContent=v; vEl.className=`dc-snr-val ${c}`;
  }
  if(dEl) dEl.className=`dc-snr-dot ${s.alive===true?'up':s.alive===false?'down':''}`;
}

// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? DRAG AND DROP �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?
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
  if((dev.group||'Default Group')!==group){
    dev.group=group;
    api('PATCH','/api/device/'+did,{group});
    renderSidebar();
  }
  refreshGroupCounts();
  pruneEmptyGroups();
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
  const rect=target.getBoundingClientRect();
  const dpanels=document.getElementById('dpanels');
  if(e.clientY<rect.top+rect.height/2){
    dpanels.insertBefore(_dragGrpEl,target);
  } else {
    dpanels.insertBefore(_dragGrpEl,target.nextSibling);
  }
}

function onGrpDrop(e){
  if(!_dragGrpEl) return;
  e.preventDefault(); e.stopPropagation();
}

function saveGroupOrder(){
  const order=[...document.querySelectorAll('.grp-wrap')].map(w=>{
    const g=w.querySelector('.grp-grid');
    return g?g.dataset.group:null;
  }).filter(Boolean);
  _lsSet('pw-grp-order', order);
}

function restoreGroupOrder(){
  const order=_lsGet('pw-grp-order', []);
  if(!order.length) return;
  const dpanels=document.getElementById('dpanels');
  order.forEach(grp=>{
    const wrap=document.getElementById(grpId(grp));
    if(wrap) dpanels.appendChild(wrap);
  });
}
// ─────────────────────────────────────────────────────────────────

function getDragAfter(grid,x,y){
  const cards=[...grid.querySelectorAll('.dc:not(.dc-add):not(.dc-dragging):not(.dc-drop-indicator)')];
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
  // Update all devices in this group
  Object.values(S.devices).filter(d=>(d.group||'Default Group')===oldName).forEach(d=>{
    d.group=newName;
    api('PATCH','/api/device/'+d.device_id,{group:newName});
  });
  // Patch the DOM without re-rendering
  const wrap=document.getElementById(grpId(oldName));
  if(wrap){
    const grid=wrap.querySelector('.grp-grid');
    if(grid) grid.dataset.group=newName;
    wrap.id=grpId(newName);
    labelEl.textContent=newName;
    // Update the click handler on the add-device card
    const addCard=wrap.querySelector('.dc-add');
    if(addCard){
      addCard.replaceWith(addCard.cloneNode(true)); // remove old listener
      const fresh=wrap.querySelector('.dc-add');
      fresh.addEventListener('click',function(){ openAddDeviceGroup(newName); });
    }
    // Update the label's own listener
    labelEl.replaceWith(labelEl.cloneNode(true));
    const newLabel=wrap.querySelector('.grp-label');
    newLabel.addEventListener('dblclick',function(){ renameGroup(newLabel, newName); });
    cntId_refresh(wrap, newName);
  }
  toast('Group renamed to "'+newName+'"','ok');
}

function cntId_refresh(wrap, group){
  const oldCnt=wrap.querySelector('.grp-count');
  if(oldCnt){ oldCnt.id=cntId(group); }
}

// ── Add Device pre-filled with a group ───────────────────────────
function openAddDeviceGroup(group){
  openAddDevice();
  setTimeout(()=>{const f=document.getElementById('ad-g');if(f)f.value=group;},40);
}

// ── Add Group modal ──────────────────────────────────────────────
function openAddGroup(){
  closeM('mag');
  const o=document.createElement('div');
  o.className='mo';o.id='mag';
  _overlayClose(o, ()=>closeM('mag'));
  o.innerHTML=`
  <div class="mbox" style="min-width:360px;max-width:420px">
    <div class="mhd">
      <div class="mttl">Add Group</div>
      <button class="mclose" onclick="closeM('mag')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr">
        <label class="fl">Group Name</label>
        <input type="text" id="ag-n" placeholder="e.g. Production, Office, Lab…" autocomplete="off"/>
        <div class="fh">A new empty group section will appear on the dashboard.</div>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mag')">Cancel</button>
      <button class="btn-p" onclick="submitAddGroup()">Create Group</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>{
    const inp=document.getElementById('ag-n');
    if(inp){
      inp.focus();
      inp.addEventListener('keydown',e=>{ if(e.key==='Enter') submitAddGroup(); });
    }
  },40);
}

function submitAddGroup(){
  const name=(document.getElementById('ag-n')?.value||'').trim();
  if(!name){ toast('Group name is required','err'); return; }
  // Check if already exists
  const exists=document.getElementById(grpId(name));
  if(exists){ toast('Group already exists','err'); return; }
  // Create the section (empty)
  ensureGroupSection(name);
  // Make dpanels visible if it wasn't
  document.getElementById('emptyMain').style.display='none';
  if(activeMainTab==='devices'){
    document.getElementById('dpanels').style.display='';
    document.getElementById('devActBar').style.display='';
  }
  closeM('mag');
  toast('Group "'+name+'" created','ok');
  // Scroll the new group into view
  setTimeout(()=>{
    const wrap=document.getElementById(grpId(name));
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
  for(const did in S.devices){
    counts.all++;
    const st=(S.devices[did].status||'unknown').toLowerCase();
    if(counts[st]!==undefined) counts[st]++;
  }
  document.querySelectorAll('.dev-status-pill').forEach(p=>{
    const ct=p.querySelector('.pill-ct');
    if(ct) ct.textContent=counts[p.dataset.st]??0;
  });
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
      const sensorMatch=S._devSensors[did]&&[...S._devSensors[did]]
        .some(k=>S.sensors[k]&&S.sensors[k].name.toLowerCase().includes(q));
      if(!nameMatch&&!sensorMatch) return;
    }
    _filteredDids.push(did);
  });
  _devPage=0;
  _renderPage();
}

function _renderPage(){
  const start=_devPage*_devPageSize;
  const visible=new Set(_filteredDids.slice(start,start+_devPageSize));
  const allDids=new Set(_filteredDids);
  // Show/hide individual cards
  document.querySelectorAll('.dc:not(.dc-add)').forEach(card=>{
    const did=card.id.replace('dp-','');
    card.style.display=visible.has(did)?'':'none';
  });
  // Hide groups with no visible cards on this page; hide groups not in filter
  document.querySelectorAll('.grp-wrap').forEach(wrap=>{
    const grid=wrap.querySelector('.grp-grid');
    if(!grid){wrap.style.display='';return;}
    const hasVisible=[...grid.querySelectorAll('.dc:not(.dc-add)')]
      .some(c=>c.style.display!=='none');
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
});

