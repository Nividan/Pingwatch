// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? SENSOR TILES �?�?�?�?�?�?�?�?�?�?�?�?
function sIco(t){return t==='ping'?'◉':t==='tcp'?'⇌':t==='snmp'?'◎':t==='dns'?'⬡':t==='tls'?'T':t==='http_keyword'?'K':t==='banner'?'B':t==='vmware'?'V':t==='smtp'?'✉':t==='ssh'?'⇲':t==='sftp'?'⇑':t==='radius'?'R':'◈'}
// msC kept as a thin alias for backward compatibility — canonical impl is msColor() in forms-utils.js
const msC = msColor;
function fmtTs(ts){try{return new Date(ts).toLocaleTimeString('en-GB');}catch(e){return ts;}}

// ── Theme-aware color cache for canvas charts ────────────────────
// Canvas can't read CSS vars directly when building rgba() strings, so we
// cache the RGB tuples and refresh on 'themechange'. Defaults match the
// dark palette so first paint (before theme.js runs) still looks right.
const _SCC = {
  accent: [47,129,247],   // --accent
  up:     [35,209,139],   // --up
  warn:   [240,165,0],    // --warn
  down:   [248,81,73],    // --down
  text:   [230,237,243],  // --text (near-white in dark, near-black in light)
  bg:     [13,17,23],     // --bg
  bg2:    [22,27,34],     // --bg2
};
function _refreshChartColors(){
  if (!window.getCssRgb) return;
  const m = { accent:'--accent', up:'--up', warn:'--warn', down:'--down',
              text:'--text', bg:'--bg', bg2:'--bg2' };
  for (const k in m) { const v = window.getCssRgb(m[k]); if (v) _SCC[k] = v; }
}
_refreshChartColors();
window.addEventListener('themechange', () => {
  _refreshChartColors();
  // Redraw every open history chart so they pick up the new palette
  try {
    for (const key in _histCache) {
      const [did, sid] = key.split('/');
      if (did && sid) dmHistRedraw(did, sid);
    }
  } catch(_) {}
});

// v0.9.7: compute the tile sub-line.  For typed SNMP sensors (enum / gauge /
// duration / text) where the main tile value already labels the reading,
// fall back to target context (host, OID tail) instead of echoing the raw
// numeric value as a useless second "1".
function _tileDetail(s, tgt) {
  if (s.stype === 'snmp') {
    const cat = _snmpCategory(s.snmp_unit, s.snmp_type);
    if (cat && cat !== 'counter_rate') {
      // last_detail for non-counter SNMP is usually just the raw value (e.g. "1")
      // — which the labeled tile value already conveys.  Show target instead.
      const d = (s.last_detail || '').trim();
      if (!d || d === String(s.last_value)) return tgt;
    }
  }
  return s.last_detail || tgt;
}

function tileHTML(s){
  const st=s.alive===true?'up':s.alive===false?'down':'';
  const isSnmp=s.stype==='snmp';
  const isDns  =s.stype==='dns';
  const isTls  =s.stype==='tls';
  const isBanner=s.stype==='banner';
  const isVmware=s.stype==='vmware';
  const rawVal = (isSnmp||isDns) ? (s.last_value||s.last_detail||'—')
               : isTls ? (s.last_value!=null?s.last_value+'d':null) : null;
  // v0.9.7: SNMP tiles format through _snmpTileValue so enum/gauge/duration
  // sensors show labeled state / unit-suffixed value instead of the raw int.
  const _snmpFmt = isSnmp ? _snmpTileValue(s) : null;
  const vt = isVmware
    ? (s.alive===false?'FAIL':(()=>{const _v=parseFloat(s.last_value);return isNaN(_v)?(s.last_value||'—').slice(0,10):_fmtVmVal(_v,_VM_UNITS[s.vmware_metric]||'');})())
    : isSnmp
      ? (_snmpFmt.length>14?_snmpFmt.slice(0,14)+'…':_snmpFmt)
      : isDns
      ? (s.alive===false?'FAIL':(rawVal.length>14?rawVal.slice(0,14)+'…':rawVal))
      : isTls ? (s.alive===false?'FAIL':(rawVal||'—'))
      : (s.last_ms!==null&&s.last_ms!==undefined?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—'));
  // For SNMP: warn (orange) if alive but value is a non-numeric string — likely wrong OID
  // Warn (orange) when SNMP is alive but value is a plain string (e.g. wrong OID → sysDescr).
  // Don't warn for formatted rates ("5.8 KB/s", "1.2 MB/s") — those end with /s.
  const _snmpStrVal = isSnmp && s.alive===true && rawVal && rawVal!=='—' && isNaN(rawVal) && !/bps$|\/s$/.test(rawVal);
  const _isCounter = isSnmp && s.last_rate != null;
  const _snmpThrColor = isSnmp && !_isCounter && s.threshold_state && s.threshold_state !== 'ok' && s.alive !== false
    ? (s.threshold_state==='crit'?'r':'w') : null;
  const _counterThrColor = _isCounter ? (s.threshold_state==='crit'?'r':s.threshold_state==='warn'?'w':'g') : null;
  const vc=s.alive===false?'b':(_isCounter?_counterThrColor:_snmpThrColor||((isSnmp||isDns||isTls||isVmware)?(_snmpStrVal?'w':(s.alive===true?'g':'m')):(s.last_ms!==null?msC(s.last_ms,s):'m')));
  const tgt=s.stype==='http'?(s.url||s.host):s.stype==='tcp'?`${s.host}:${s.port}`:s.stype==='snmp'?`${s.host} OID:${(s.snmp_oid||'').split('.').slice(-3).join('.')}`:s.stype==='dns'?`${s.dns_query||s.host} (${s.dns_record_type||'A'})`:s.stype==='vmware'?`${s.host} · ${s.vmware_vm_id}`:s.host;
  const isMuted=s.alerts_muted||S.devices[s.device_id]?.alerts_muted;
  const hist=(s.history||[]).slice(-40);
  const thrHist=(s.thr_history||[]).slice(-40);
  const ub=Array(40).fill(0).map((_,i)=>{
    const idx=i-(40-hist.length);
    if(idx<0)return'<div class="ub-s"></div>';
    const v=hist[idx];
    if(v===null)return`<div class="ub-s" style="background:var(--down)"></div>`;
    const _mc=msC(v,s);const _thr=isSnmp&&!_isCounter?thrHist[idx]||'ok':'ok';const _snmpDotC=_thr==='crit'?'var(--down)':_thr==='warn'?'var(--warn)':'var(--up)';const c=(isSnmp||isDns||isTls||isVmware)?_snmpDotC:(_mc==='g'?'var(--up)':_mc==='w'?'var(--warn)':'var(--down)');
    return`<div class="ub-s" style="background:${c}"></div>`;
  }).join('');
  return`
  <div class="stl-hd">
    <div class="stl-tbdg ${s.stype}">${sIco(s.stype)} ${s.stype.toUpperCase().replace('_',' ')}</div>
    <div class="stl-nm">${esc(s.name)}</div>
    <span class="stl-muted" id="sm-muted-${s.device_id}_${s.sensor_id}" title="Alerts muted" style="${isMuted?'':'display:none'}">🔕</span>
    <button class="stl-hist" onclick="event.stopPropagation();openDetail('${s.device_id}','${s.sensor_id}','history')" title="History">⌚</button>
    <div class="stl-sdot ${st}"></div>
  </div>
  <div class="stl-body">
    <div class="stl-val ${vc}" id="stv-${s.device_id}_${s.sensor_id}">${vt}</div>
    <div class="stl-det" title="${esc(s.last_detail||'')}">
      <span id="std-${s.device_id}_${s.sensor_id}">${esc(_tileDetail(s, tgt))}</span>
    </div>
    <div class="ub" id="ub-${s.device_id}_${s.sensor_id}">${ub}</div>
  </div>
  <div class="stl-spark"><canvas class="spk" height="38"></canvas></div>
  <div class="stl-stats">
    ${(isSnmp||isTls)?`
    <div class="stl-stat"><div class="stl-sv" id="sa-${s.device_id}_${s.sensor_id}">${_isCounter&&s.last_rate!=null?_fmtRateDisplay(s.last_rate,s.snmp_unit||'bytes'):'—'}</div><div class="stl-sk">Avg</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sm-${s.device_id}_${s.sensor_id}">${s.loss_pct!==undefined?s.loss_pct+'%':'—'}</div><div class="stl-sk">Loss</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sl-${s.device_id}_${s.sensor_id}">${s.total||0}</div><div class="stl-sk">Sent</div></div>`:`
    <div class="stl-stat"><div class="stl-sv" id="sa-${s.device_id}_${s.sensor_id}">${s.avg_ms?s.avg_ms+'ms':'—'}</div><div class="stl-sk">Avg</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sm-${s.device_id}_${s.sensor_id}">${s.min_ms?s.min_ms+'ms':'—'}</div><div class="stl-sk">Min</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sl-${s.device_id}_${s.sensor_id}">${s.loss_pct!==undefined?s.loss_pct+'%':'—'}</div><div class="stl-sk">Loss</div></div>`}
  </div>`;
}

// ── Sensor tile drag-to-reorder ───────────────────────────────────
let _snrDragEl=null, _snrDragDid=null, _snrDropInd=null;
// ── VM row drag-to-reorder ────────────────────────────────────────
let _vmRowDragEl=null, _vmRowDragDid=null, _vmRowDragVmid=null, _vmRowDropInd=null;

function _snrSaveOrder(did){
  const grid=document.getElementById(`sg-${did}`);
  if(!grid)return;
  const order=[...grid.querySelectorAll('.stl:not(.stl-drop-ind)')].map(t=>t.dataset.sid);
  _lsSet(`pw_snr_order_${did}`,order);
}

function _applySensorOrder(did){
  const order=_lsGet(`pw_snr_order_${did}`,[]);
  if(!order.length)return;
  const grid=document.getElementById(`sg-${did}`);
  if(!grid)return;
  // Append saved-order tiles first, then tiles not in order (new sensors) last
  order.forEach(sid=>{
    const el=grid.querySelector(`.stl[data-sid="${sid}"]`);
    if(el) grid.appendChild(el);
  });
  [...grid.querySelectorAll('.stl:not(.stl-drop-ind)')].filter(el=>!order.includes(el.dataset.sid))
    .forEach(el=>grid.appendChild(el));
}

function _vmSaveOrder(did,vmid){
  const sfx=_vmGrpSfx(vmid);
  const body=document.getElementById(`vgbody-${did}-${sfx}`);
  if(!body) return;
  const order=[...body.querySelectorAll('.vm-row')].map(r=>r.dataset.sid);
  _lsSet(`pw_vm_order_${did}_${sfx}`,order);
}

function _vmApplySavedOrders(did){
  const grid=document.getElementById(`sg-${did}`);
  if(!grid) return;
  grid.querySelectorAll(`[id^="vgbody-${did}-"]`).forEach(body=>{
    const sfx=body.id.slice((`vgbody-${did}-`).length);
    const order=_lsGet(`pw_vm_order_${did}_${sfx}`,[]);
    if(!order.length) return;
    order.forEach(sid=>{
      const el=body.querySelector(`.vm-row[data-sid="${sid}"]`);
      if(el) body.appendChild(el);
    });
  });
}

function _vmRowDragOver(e){
  if(!_vmRowDragEl) return;
  e.preventDefault();
  e.dataTransfer.dropEffect='move';
  if(!_vmRowDropInd){
    _vmRowDropInd=document.createElement('div');
    _vmRowDropInd.className='vm-row vm-row-drop-ind';
  }
  const body=e.currentTarget;
  const rows=[...body.querySelectorAll('.vm-row:not(.vm-row-drop-ind):not(.vm-row-dragging)')];
  let after=null;
  for(const r of rows){ const rb=r.getBoundingClientRect(); if(e.clientY<rb.top+rb.height/2){after=r;break;} }
  if(after) body.insertBefore(_vmRowDropInd,after);
  else body.appendChild(_vmRowDropInd);
}

function _vmRowDrop(e){
  if(!_vmRowDragEl) return;
  e.preventDefault();
  const body=e.currentTarget;
  if(_vmRowDropInd){ body.insertBefore(_vmRowDragEl,_vmRowDropInd); _vmRowDropInd.remove(); _vmRowDropInd=null; }
  _vmRowDragEl.classList.remove('vm-row-dragging');
  if(_vmRowDragDid&&_vmRowDragVmid) _vmSaveOrder(_vmRowDragDid,_vmRowDragVmid);
  _vmRowDragEl=null; _vmRowDragDid=null; _vmRowDragVmid=null;
}

function _vmRowDragLeave(e){
  const body=e.currentTarget;
  if(!body.contains(e.relatedTarget)&&_vmRowDropInd&&_vmRowDropInd.parentNode===body){ _vmRowDropInd.remove(); _vmRowDropInd=null; }
}

function _initSensorGrid(did){
  const grid=document.getElementById(`sg-${did}`);
  if(!grid||grid._snrDragInit)return;
  grid._snrDragInit=true;
  grid.addEventListener('dragover',_snrDragOver);
  grid.addEventListener('drop',_snrDrop);
  grid.addEventListener('dragleave',_snrDragLeave);
}

function _snrDragOver(e){
  if(!_snrDragEl)return;
  e.preventDefault();
  e.dataTransfer.dropEffect='move';
  const grid=e.currentTarget;
  grid.classList.add('sg-drag-over');
  if(!_snrDropInd){
    _snrDropInd=document.createElement('div');
    _snrDropInd.className='stl stl-drop-ind';
  }
  // Find tile after cursor (vertical layout)
  const tiles=[...grid.querySelectorAll('.stl:not(.stl-drop-ind):not(.stl-dragging)')];
  let after=null;
  for(const t of tiles){
    const r=t.getBoundingClientRect();
    if(e.clientY<r.top+r.height/2){after=t;break;}
  }
  if(after) grid.insertBefore(_snrDropInd,after);
  else       grid.appendChild(_snrDropInd);
}

function _snrDrop(e){
  if(!_snrDragEl)return;
  e.preventDefault();
  const grid=e.currentTarget;
  grid.classList.remove('sg-drag-over');
  if(_snrDropInd){
    grid.insertBefore(_snrDragEl,_snrDropInd);
    _snrDropInd.remove(); _snrDropInd=null;
  }
  _snrDragEl.classList.remove('stl-dragging');
  if(_snrDragDid) _snrSaveOrder(_snrDragDid);
  _snrDragEl=null; _snrDragDid=null;
}

function _snrDragLeave(e){
  const grid=e.currentTarget;
  if(!grid.contains(e.relatedTarget)){
    grid.classList.remove('sg-drag-over');
    if(_snrDropInd&&_snrDropInd.parentNode===grid){_snrDropInd.remove();_snrDropInd=null;}
  }
}

function renderTile(did,s){
  // ── VMware sensors go into a VM group row, not a full tile ──────
  if(s.stype==='vmware'&&s.vmware_vm_id){
    _ensureVmGrp(did,s);
    const sfx=_vmGrpSfx(s.vmware_vm_id);
    const body=document.getElementById(`vgbody-${did}-${sfx}`);
    if(!body) return;
    const key=`${did}/${s.sensor_id}`;
    const old=document.getElementById(`t-${key.replace('/','_')}`);
    const t=document.createElement('div');
    t.className=`vm-row ${s.alive===true?'up':s.alive===false?'down':''}`;
    t.id=`t-${key.replace('/','_')}`;
    t.dataset.sid=s.sensor_id;
    t.onclick=()=>openDetail(did,s.sensor_id);
    t.innerHTML=vmRowHTML(s);
    t.setAttribute('draggable','true');
    t.addEventListener('dragstart',e=>{
      if(e.target.tagName==='BUTTON'||e.target.closest('button')){e.preventDefault();return;}
      _vmRowDragEl=t; _vmRowDragDid=did; _vmRowDragVmid=s.vmware_vm_id;
      e.dataTransfer.effectAllowed='move'; e.dataTransfer.setData('text/plain',s.sensor_id);
      setTimeout(()=>t.classList.add('vm-row-dragging'),0);
    });
    t.addEventListener('dragend',()=>{
      t.classList.remove('vm-row-dragging');
      if(_vmRowDropInd){_vmRowDropInd.remove();_vmRowDropInd=null;}
      _vmRowDragEl=null; _vmRowDragDid=null; _vmRowDragVmid=null;
    });
    if(old) old.replaceWith(t); else body.appendChild(t);
    _updateVmGrpStatus(did,s.vmware_vm_id);
    const cvs=t.querySelector('canvas.spk');
    if(cvs){ cvs.width=60; S.charts[key]={canvas:cvs,ctx:cvs.getContext('2d')}; if(s.history&&s.history.length>1)drawSpk(key,s.history); }
    return;
  }
  // ── Normal tile ─────────────────────────────────────────────────
  const grid=document.getElementById(`sg-${did}`);
  if(!grid)return;
  const key=`${did}/${s.sensor_id}`;
  const old=document.getElementById(`t-${key.replace('/','_')}`);
  if(old)old.remove();
  const t=document.createElement('div');
  const _thr=s.threshold_state&&s.threshold_state!=='ok'&&s.alive!==false?' thr-'+s.threshold_state:'';
  t.className=`stl ${s.alive===true?'up':s.alive===false?'down':''}${_thr} stl-enter`;
  t.id=`t-${key.replace('/','_')}`;
  t.dataset.sid=s.sensor_id;
  t.onclick=()=>openDetail(did,s.sensor_id);
  t.style.animationDelay=Math.min(grid.children.length*40,200)+'ms';
  t.innerHTML=tileHTML(s);
  // Drag-to-reorder
  t.setAttribute('draggable','true');
  t.addEventListener('dragstart',e=>{
    if(e.target.tagName==='BUTTON'||e.target.closest('button')){e.preventDefault();return;}
    _snrDragEl=t; _snrDragDid=did;
    e.dataTransfer.effectAllowed='move';
    e.dataTransfer.setData('text/plain',s.sensor_id);
    setTimeout(()=>t.classList.add('stl-dragging'),0);
  });
  t.addEventListener('dragend',()=>{
    t.classList.remove('stl-dragging');
    if(_snrDropInd){_snrDropInd.remove();_snrDropInd=null;}
    grid.classList.remove('sg-drag-over');
    _snrDragEl=null; _snrDragDid=null;
  });
  grid.appendChild(t);
  t.addEventListener('animationend',()=>{t.classList.remove('stl-enter');t.style.animationDelay='';},{once:true});
  const cvs=t.querySelector('canvas.spk');
  if(cvs){
    S.charts[key]={canvas:cvs,ctx:cvs.getContext('2d')};
    if(s.history&&s.history.length>1)drawSpk(key,s.history);
  }
}

function updateTile(s){
  const key=`${s.device_id}/${s.sensor_id}`;
  const sk=`${s.device_id}_${s.sensor_id}`;
  const tile=document.getElementById(`t-${key.replace('/','_')}`);
  if(!tile)return;
  if(s.stype==='vmware'&&s.vmware_vm_id){
    tile.className=`vm-row ${s.alive===true?'up':s.alive===false?'down':''}`;
    const dot=tile.querySelector('.stl-sdot');
    if(dot) dot.className=`stl-sdot ${s.alive===true?'up':s.alive===false?'down':''}`;
    const vc=s.alive===false?'b':(s.threshold_state&&s.threshold_state!=='ok'?(s.threshold_state==='crit'?'r':'w'):(s.alive===true?'g':'m'));
    const _rv=s.last_value||s.last_detail||'—';
    const _rv2=parseFloat(s.last_value);
    const vt=s.alive===false?'FAIL':(!isNaN(_rv2)?_fmtVmVal(_rv2,_VM_UNITS[s.vmware_metric]||''):(_rv.length>12?_rv.slice(0,12)+'…':_rv));
    const vel=document.getElementById(`stv-${sk}`);
    if(vel){vel.textContent=vt;vel.className=`vm-row-val ${vc}`;}
    const mutedBadge=document.getElementById(`sm-muted-${sk}`);
    if(mutedBadge){const isMuted=s.alerts_muted||S.devices[s.device_id]?.alerts_muted;mutedBadge.style.display=isMuted?'':'none';}
    drawSpk(key,s.history||[]);
    _updateVmGrpStatus(s.device_id,s.vmware_vm_id);
    updateDetailWin(s.device_id,s.sensor_id,s);
    return;
  }
  const _newThr=s.threshold_state&&s.threshold_state!=='ok'&&s.alive!==false?' thr-'+s.threshold_state:'';
  tile.className=`stl ${s.alive===true?'up':s.alive===false?'down':''}${_newThr}`;
  const dot=tile.querySelector('.stl-sdot');
  if(dot)dot.className=`stl-sdot ${s.alive===true?'up':s.alive===false?'down':''}`;
  const isSnmp=s.stype==='snmp';
  const isDns2  =s.stype==='dns';
  const isTls2  =s.stype==='tls';
  const isBanner2=s.stype==='banner';
  const isVmware2=s.stype==='vmware';
  const rawVal2 = (isSnmp||isDns2)?(s.last_value||s.last_detail||'—')
               : isTls2?(s.last_value!=null?s.last_value+'d':null):null;
  // v0.9.7: SNMP live updates route through _snmpTileValue.
  const _snmpFmt2 = isSnmp ? _snmpTileValue(s) : null;
  const vt = isVmware2
    ? (s.alive===false?'FAIL':(()=>{const _v=parseFloat(s.last_value);return isNaN(_v)?(s.last_value||'—').slice(0,10):_fmtVmVal(_v,_VM_UNITS[s.vmware_metric]||'');})())
    : isSnmp
      ? (_snmpFmt2.length>14?_snmpFmt2.slice(0,14)+'…':_snmpFmt2)
      : isDns2
      ? (s.alive===false?'FAIL':(rawVal2.length>14?rawVal2.slice(0,14)+'…':rawVal2))
      : isTls2 ? (s.alive===false?'FAIL':(rawVal2||'—'))
      : (s.last_ms!==null&&s.last_ms!==undefined?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—'));
  const _snmpStrVal2 = isSnmp && s.alive===true && rawVal2 && rawVal2!=='—' && isNaN(rawVal2) && !/bps$|\/s$/.test(rawVal2);
  const _isCounter2 = isSnmp && s.last_rate != null;
  const _snmpThrColor2 = isSnmp && !_isCounter2 && s.threshold_state && s.threshold_state !== 'ok' && s.alive !== false
    ? (s.threshold_state==='crit'?'r':'w') : null;
  const _counterThrColor2 = _isCounter2 ? (s.threshold_state==='crit'?'r':s.threshold_state==='warn'?'w':'g') : null;
  const vc=s.alive===false?'b':(_isCounter2?_counterThrColor2:_snmpThrColor2||((isSnmp||isDns2||isTls2)?(_snmpStrVal2?'w':(s.alive===true?'g':'m')):(s.last_ms!==null?msC(s.last_ms,s):'m')));
  const vel=document.getElementById(`stv-${sk}`);
  if(vel){vel.textContent=vt;vel.className=`stl-val ${vc}`;}
  const mutedBadge=document.getElementById(`sm-muted-${sk}`);
  if(mutedBadge){const isMuted2=s.alerts_muted||S.devices[s.device_id]?.alerts_muted;mutedBadge.style.display=isMuted2?'':'none';}
  const del=document.getElementById(`std-${sk}`);
  if(del){
    const _tgtLive=s.stype==='http'?(s.url||s.host):s.stype==='tcp'?`${s.host}:${s.port}`:s.stype==='snmp'?`${s.host} OID:${(s.snmp_oid||'').split('.').slice(-3).join('.')}`:s.stype==='dns'?`${s.dns_query||s.host} (${s.dns_record_type||'A'})`:s.host;
    del.textContent=_tileDetail(s,_tgtLive);
  }
  const ael=document.getElementById(`sa-${sk}`),mel=document.getElementById(`sm-${sk}`),lel=document.getElementById(`sl-${sk}`);
  if(isSnmp||isTls2){
    if(ael)ael.textContent=_isCounter2&&s.last_rate!=null?_fmtRateDisplay(s.last_rate,s.snmp_unit||'bytes'):'—';
    if(mel)mel.textContent=s.loss_pct!==undefined?`${s.loss_pct}%`:'—';
    if(lel)lel.textContent=String(s.total||0);
  }else{
    if(ael)ael.textContent=s.avg_ms?`${s.avg_ms}ms`:'—';
    if(mel)mel.textContent=s.min_ms?`${s.min_ms}ms`:'—';
    if(lel)lel.textContent=s.loss_pct!==undefined?`${s.loss_pct}%`:'—';
  }
  const ubEl=document.getElementById(`ub-${sk}`);
  if(ubEl){
    const hist=(s.history||[]).slice(-40),sqs=ubEl.querySelectorAll('.ub-s');
    const thrHist2=(s.thr_history||[]).slice(-40);
    sqs.forEach((sq,i)=>{
      const idx=i-(40-hist.length);
      if(idx<0){sq.style.background='var(--bg4)';return;}
      const v=hist[idx];
      if(v===null){sq.style.background='var(--down)';return;}
      const _mc2=msC(v,s);const _thr2=isSnmp&&!_isCounter2?thrHist2[idx]||'ok':'ok';const _snmpDotC2=_thr2==='crit'?'var(--down)':_thr2==='warn'?'var(--warn)':'var(--up)';sq.style.background=(isSnmp||isDns2||isTls2)?_snmpDotC2:(_mc2==='g'?'var(--up)':_mc2==='w'?'var(--warn)':'var(--down)');
    });
  }
  drawSpk(key,s.history||[]);
  updateDetailWin(s.device_id,s.sensor_id,s);
}

function setupCharts(dev){
  setupChartsByDid(dev.device_id);
}

// ── VMware VM sensor groups ───────────────────────────────────────
function _vmGrpSfx(vmid){ return vmid.replace(/[^a-z0-9]/gi,'-'); }

function _vmNameFromSensor(s){
  // Prefer stored VM/host display name; fall back to stripping metric label from sensor name
  if(s.vmware_vm_name) return s.vmware_vm_name;
  const m=(typeof _allVmwareMetrics==='function'?_allVmwareMetrics():(_vmwareMetrics||[])).find(x=>x.v===s.vmware_metric);
  if(m&&s.name.endsWith(' '+m.l)) return s.name.slice(0,s.name.length-m.l.length-1);
  return s.name||s.vmware_vm_id;
}

// Entity type from VMware MoID prefix — authoritative.
//   vm-*        → 'vm'        (VM_METRICS)
//   host-*      → 'host'      (HOST_METRICS)
//   datastore-* → 'datastore' (DATASTORE_METRICS)
// The prefix is stable across vCenter versions and is what vmware/client.py
// uses internally to dispatch probes, so driving UI off it matches backend
// behaviour and can't produce cross-category nonsense like a datastore
// metric attached to a VM MoID.
function _vmEntityType(vmid) {
  if (!vmid) return 'vm';
  if (vmid.startsWith('host-')) return 'host';
  if (vmid.startsWith('datastore-')) return 'datastore';
  return 'vm';
}

function _metricCategory(metricKey) {
  const v = metricKey || '';
  if (v.startsWith('host_')) return 'host';
  if (v.startsWith('dstore_')) return 'datastore';
  return 'vm';
}

// Back-compat helper used by the card header — true when the MoID is a host.
function _vmGrpIsHost(did, vmid) {
  return _vmEntityType(vmid) === 'host';
}

function _ensureVmGrp(did,s){
  const vmid=s.vmware_vm_id; if(!vmid) return;
  const sfx=_vmGrpSfx(vmid);
  if(document.getElementById(`vmgrp-${did}-${sfx}`)) return;
  const grid=document.getElementById(`sg-${did}`); if(!grid) return;
  const vmName=_vmNameFromSensor(s);
  const colState=_lsGet('pw-vmgrp-col',{})||{};
  const collapsed=!!(colState[`${did}/${vmid}`]);
  const grp=document.createElement('div');
  grp.className='vm-grp stl-enter';
  grp.id=`vmgrp-${did}-${sfx}`;
  grp.dataset.vmid=vmid; grp.dataset.did=did;
  const _isHost=_vmGrpIsHost(did, vmid);
  grp.innerHTML=`
    <div class="vm-grp-hdr">
      <div class="vm-grp-arr${collapsed?'':' open'}">▶</div>
      <div class="vm-grp-badge">${_isHost?'H':'V'}</div>
      <div class="vm-grp-nm">${esc(vmName)}</div>
      <div class="vm-grp-dot" id="vgdot-${did}-${sfx}"></div>
      <div class="vm-grp-cnt" id="vgcnt-${did}-${sfx}">0 metrics</div>
      <button class="dp-btn vm-add-btn" style="font-size:11px;padding:2px 8px;margin-left:8px" title="Add or remove metrics for this VM">✎ Edit</button>
      <button class="dp-btn vm-mute-btn" style="font-size:11px;padding:2px 8px;margin-left:4px" id="vgmute-${did}-${sfx}" title="Mute alerts for all metrics">🔕 Mute</button>
      <button class="dp-btn d vm-del-btn" style="font-size:11px;padding:2px 8px;margin-left:4px" title="Remove all metrics for this VM">✕ Remove</button>
    </div>
    <div class="vm-grp-body${collapsed?' collapsed':''}" id="vgbody-${did}-${sfx}"></div>`;
  grp.querySelector('.vm-grp-hdr').addEventListener('click',e=>{
    if(e.target.closest('button')) return;
    toggleVmGrp(did,vmid);
  });
  grp.querySelector('.vm-add-btn').addEventListener('click',e=>{
    e.stopPropagation();
    openEditVmMetrics(did,vmid,vmName,_isHost);
  });
  grp.querySelector('.vm-mute-btn').addEventListener('click',e=>{
    e.stopPropagation();
    toggleVmGrpMute(did,vmid);
  });
  grp.querySelector('.vm-del-btn').addEventListener('click',e=>{
    e.stopPropagation();
    delVmGrp(did,vmid);
  });
  grid.appendChild(grp);
  grp.addEventListener('animationend',()=>grp.classList.remove('stl-enter'),{once:true});
  const _body=grp.querySelector(`#vgbody-${did}-${sfx}`);
  _body.addEventListener('dragover',_vmRowDragOver);
  _body.addEventListener('drop',_vmRowDrop);
  _body.addEventListener('dragleave',_vmRowDragLeave);
}

function _updateVmGrpStatus(did,vmid){
  const sfx=_vmGrpSfx(vmid);
  const body=document.getElementById(`vgbody-${did}-${sfx}`); if(!body) return;
  const rows=[...body.querySelectorAll('.vm-row')];
  const cntEl=document.getElementById(`vgcnt-${did}-${sfx}`);
  if(cntEl) cntEl.textContent=rows.length===1?'1 metric':`${rows.length} metrics`;
  const dotEl=document.getElementById(`vgdot-${did}-${sfx}`); if(!dotEl) return;
  const devMuted=S.devices[did]?.alerts_muted;
  const states=rows.map(r=>{
    const sn=S.sensors[`${did}/${r.dataset.sid}`];
    if(!sn) return '';
    if(sn.alerts_muted||devMuted) return sn.alive===true?'up':'';
    return sn.alive===false?'down':(sn.threshold_state&&sn.threshold_state!=='ok'?'warn':(sn.alive===true?'up':''));
  });
  dotEl.className='vm-grp-dot '+(states.includes('down')?'down':states.includes('warn')?'warn':states.includes('up')?'up':'');
  _updateVmGrpMuteBtn(did,vmid);
}

function toggleVmGrp(did,vmid){
  const sfx=_vmGrpSfx(vmid);
  const body=document.getElementById(`vgbody-${did}-${sfx}`); if(!body) return;
  const grp=document.getElementById(`vmgrp-${did}-${sfx}`); if(!grp) return;
  const collapsed=body.classList.toggle('collapsed');
  const arr=grp.querySelector('.vm-grp-arr');
  if(arr) arr.classList.toggle('open',!collapsed);
  const state=_lsGet('pw-vmgrp-col',{})||{};
  if(collapsed) state[`${did}/${vmid}`]=1; else delete state[`${did}/${vmid}`];
  _lsSet('pw-vmgrp-col',state);
}

function openAddVmMetric(did,vmid,vmName){
  openAddSensor(did);
  setTimeout(()=>{
    selType('vmware');
    const el=document.getElementById('as-vmid');
    if(el){ el.value=vmid; }
    // Pre-fill sensor name prefix so auto-name works
    window._vmGrpPrefillName=vmName||vmid;
  },80);
}


// ── ✎ Edit metrics modal (per-VM add/remove) ──────────────────────
//
// Lets the operator toggle the set of monitored metrics for a single VM
// (or host) without leaving the device card. Pre-checks every metric that
// already has a sensor on this VM; ticks/unticks generate add/remove
// pending operations applied on Save. Connection details (host, port, user,
// verify_ssl, interval, timeout) are inherited from any existing sensor on
// the same VM — we just need the password once if the user is *adding*
// new metrics.

async function openEditVmMetrics(did, vmid, vmName, isHost){
  // Lazily load the VMware metric catalogue if it hasn't been fetched yet
  // (happens when the user clicks Edit before ever opening Add Sensor).
  if (typeof _ensureVmwareCatalogue === 'function') {
    try { await _ensureVmwareCatalogue(); }
    catch (e) {
      toast('Failed to load VMware metric catalogue', 'err');
      return;
    }
  }
  const allMetrics = (typeof _allVmwareMetrics === 'function')
    ? _allVmwareMetrics() : (_vmwareMetrics || []);
  if (!allMetrics.length) {
    toast('VMware metric catalogue is empty — check /api/vmware/* endpoints', 'err');
    return;
  }

  // Existing sensors on this VM, indexed by metric key.
  const existing = Object.values(S.sensors||{}).filter(s =>
    s.device_id === did && s.stype === 'vmware' && s.vmware_vm_id === vmid
  );
  const byMetric = {};
  for (const s of existing) byMetric[s.vmware_metric] = s;

  // Entity type from the MoID prefix — vm-*, host-*, or datastore-*.
  // Keep isHost in sync for the rest of the modal (button onclick, etc.).
  const entityType = _vmEntityType(vmid);
  isHost = entityType === 'host';

  // Filter the catalogue to the entity's category — avoids offering (for
  // example) datastore metrics on a VM MoID, which would probe-fail with
  // "Datastore vm-X not found". Any metric already monitored on this VM is
  // always kept in the list so the checkbox shows its true on/off state.
  const visible = allMetrics.filter(m => {
    if (byMetric[m.v]) return true;
    return _metricCategory(m.v) === entityType;
  });

  // Group by category for the layout (cpu / mem / disk / datastore / net / sys).
  const _GROUP_LABEL = {cpu:'CPU', mem:'Memory', disk:'Disk',
                       datastore:'Datastore', net:'Network', sys:'System'};
  const groups = {};
  for (const m of visible) {
    const g = m.group || 'other';
    (groups[g] = groups[g] || []).push(m);
  }

  // Connection params from any existing sensor (all metrics share creds).
  const ref = existing[0] || {};
  const refHost = ref.host || S.devices[did]?.host || '';
  const refPort = ref.port || 443;
  const refUser = ref.vmware_user || S.devices[did]?.vmware_user_default || '';
  // If the device has a default VMware password stored, the backend will use
  // it when POST /api/device/:did/sensor comes in without a password. Detect
  // that here so we don't pester the user for a password they already saved.
  const hasDevPw = !!S.devices[did]?.has_vmware_password_default;
  // Use truthy/falsy, not `!== 0`: backend sends JSON boolean `false`, and
  // `false !== 0` is `true` under strict inequality (different types) — which
  // would silently flip verify_ssl to true on any metric added via this modal.
  const refVssl = !!ref.verify_ssl;
  const refInterval = ref.interval || 60;
  const refTimeout  = ref.timeout || 10;

  closeM('vm-edit-modal');
  const o = document.createElement('div');
  o.className = 'mo';  o.id = 'vm-edit-modal';
  _overlayClose(o, () => closeM('vm-edit-modal'));

  const groupHTML = Object.keys(groups).sort().map(g => `
    <div class="vme-grp">
      <div class="vme-grp-hd">${esc(_GROUP_LABEL[g] || g)}</div>
      <div class="vme-grp-body">
        ${groups[g].map(m => {
          const sensor = byMetric[m.v];
          const monitored = !!sensor;
          const sid = sensor ? sensor.sensor_id : '';
          return `
            <label class="vme-row">
              <input type="checkbox" data-metric="${esc(m.v)}" data-sid="${esc(sid)}"
                     ${monitored?'checked':''}/>
              <span class="vme-row-lbl">${esc(m.l)}</span>
              <span class="vme-row-unit">${esc(m.unit||'')}</span>
            </label>`;
        }).join('')}
      </div>
    </div>`).join('');

  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,640px);max-height:85vh;display:flex;flex-direction:column">
      <div class="mhd">
        <div class="mttl">✎ Edit metrics — ${esc(vmName||vmid)}</div>
        <button class="mclose" onclick="closeM('vm-edit-modal')">✕</button>
      </div>
      <div class="mbdy" style="overflow-y:auto;flex:1;gap:14px">
        <div class="fh">
          Tick metrics to monitor; untick to stop monitoring (sensor will be removed).
          Connection settings (host, user, interval) are inherited from existing sensors
          on this VM.
        </div>
        <div id="vme-grid">${groupHTML}</div>
        <div id="vme-pwd-block" style="display:none;border-top:1px solid var(--border);padding-top:12px">
          <div class="fl" style="margin-bottom:6px">vCenter / ESXi password${hasDevPw?' <span style="color:var(--text3);font-weight:400">(optional — device default will be used if blank)</span>':''}</div>
          <input type="password" id="vme-pwd" placeholder="${hasDevPw?'Leave blank to use device default':'Required to add new metrics'}" autocomplete="new-password" style="max-width:320px"/>
          <div class="fh" style="margin-top:4px">
            User: <strong>${esc(refUser||'(unset)')}</strong> · Host: <strong>${esc(refHost)}:${refPort}</strong>
          </div>
        </div>
        <div id="vme-summary" class="fh" style="font-style:italic"></div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('vm-edit-modal')">Cancel</button>
        <button class="btn-p" id="vme-save-btn" onclick="_vmEditSave('${esc(did)}','${esc(vmid)}',${isHost?1:0})">Save Changes</button>
      </div>
    </div>`;
  document.body.appendChild(o);

  // Stash connection params + original state on the modal so save can read them.
  o._vmEdit = {
    did, vmid, vmName, isHost,
    refHost, refPort, refUser, refVssl, refInterval, refTimeout,
    byMetric, hasDevPw,
  };

  // Live-update summary + show/hide password block as ticks change.
  const updateSummary = () => {
    const cbs = o.querySelectorAll('input[type=checkbox][data-metric]');
    let adds = 0, removes = 0;
    cbs.forEach(cb => {
      const wasOn = !!byMetric[cb.dataset.metric];
      if (cb.checked && !wasOn) adds++;
      else if (!cb.checked && wasOn) removes++;
    });
    const sum = document.getElementById('vme-summary');
    if (sum) {
      if (adds === 0 && removes === 0) sum.textContent = 'No pending changes.';
      else sum.textContent = `Pending: ${adds>0?`+${adds} add`:''}${adds>0&&removes>0?', ':''}${removes>0?`−${removes} remove`:''}.`;
    }
    const pwBlock = document.getElementById('vme-pwd-block');
    if (pwBlock) pwBlock.style.display = (adds > 0 && refUser) ? '' : 'none';
  };
  o.querySelectorAll('input[type=checkbox][data-metric]').forEach(cb => {
    cb.addEventListener('change', updateSummary);
  });
  updateSummary();
}

async function _vmEditSave(did, vmid, isHost){
  const o = document.getElementById('vm-edit-modal');
  if (!o || !o._vmEdit) return;
  const ctx = o._vmEdit;
  const cbs = o.querySelectorAll('input[type=checkbox][data-metric]');

  const toAdd = [], toRemove = [];
  cbs.forEach(cb => {
    const metric = cb.dataset.metric;
    const sid    = cb.dataset.sid;
    const wasOn  = !!ctx.byMetric[metric];
    if (cb.checked && !wasOn) toAdd.push(metric);
    else if (!cb.checked && wasOn && sid) toRemove.push({metric, sid});
  });

  if (toAdd.length === 0 && toRemove.length === 0) {
    closeM('vm-edit-modal');
    toast('No changes to apply', 'info');
    return;
  }

  // Need creds for adds. If the device has a default vmware password the
  // backend will fall back to it when the POST body omits `vmware_password`,
  // so a blank field is fine. Otherwise demand one.
  let pwd = '';
  if (toAdd.length > 0) {
    pwd = (document.getElementById('vme-pwd')?.value || '').trim();
    if (!pwd && !ctx.hasDevPw) {
      toast('Password required to add new metrics', 'err');
      return;
    }
  }

  const btn = document.getElementById('vme-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  let okAdd = 0, failAdd = 0, okDel = 0, failDel = 0;

  // Removals first (cheap; no creds needed).
  for (const r of toRemove) {
    try {
      await delSensor(did, r.sid);
      okDel++;
    } catch (e) { failDel++; }
  }

  // Additions: clone connection params from the reference sensor.
  for (const metric of toAdd) {
    const meta = (typeof _allVmwareMetrics==='function'?_allVmwareMetrics():(_vmwareMetrics||[]))
      .find(m => m.v === metric) || {};
    const sname = `${ctx.vmName} ${meta.l || metric}`;
    try {
      await api('POST', `/api/device/${did}/sensor`, {
        name:           sname,
        type:           'vmware',
        host:           ctx.refHost,
        port:           ctx.refPort,
        interval:       ctx.refInterval,
        timeout:        ctx.refTimeout,
        verify_ssl:     ctx.refVssl,
        vmware_user:    ctx.refUser,
        vmware_password: pwd,
        vmware_vm_id:   ctx.vmid,
        vmware_vm_name: ctx.vmName,
        vmware_metric:  metric,
      });
      okAdd++;
    } catch (e) {
      failAdd++;
    }
  }

  closeM('vm-edit-modal');

  const parts = [];
  if (okAdd)   parts.push(`+${okAdd}`);
  if (okDel)   parts.push(`−${okDel}`);
  if (failAdd) parts.push(`${failAdd} add failed`);
  if (failDel) parts.push(`${failDel} remove failed`);
  toast(`Metrics updated: ${parts.join(' · ')}`, (failAdd+failDel)===0 ? 'ok' : 'err');
}

function vmRowHTML(s){
  const sk=`${s.device_id}_${s.sensor_id}`;
  const st=s.alive===true?'up':s.alive===false?'down':'';
  const _vmRaw=s.last_value||s.last_detail||'—';
  const _vmV=parseFloat(s.last_value);
  const vt=s.alive===false?'FAIL':(!isNaN(_vmV)?_fmtVmVal(_vmV,_VM_UNITS[s.vmware_metric]||''):(_vmRaw.length>12?_vmRaw.slice(0,12)+'…':_vmRaw));
  const vc=s.alive===false?'b':(s.threshold_state&&s.threshold_state!=='ok'?(s.threshold_state==='crit'?'r':'w'):(s.alive===true?'g':'m'));
  const metricLabel=(typeof _allVmwareMetrics==='function'?_allVmwareMetrics():(_vmwareMetrics||[])).find(m=>m.v===s.vmware_metric)?.l||s.vmware_metric||s.name;
  const isMuted=s.alerts_muted||S.devices[s.device_id]?.alerts_muted;
  const hist=(s.history||[]).slice(-24);
  const ub=Array(24).fill(0).map((_,i)=>{
    const idx=i-(24-hist.length);
    if(idx<0) return'<div class="ub-s"></div>';
    const v=hist[idx];
    if(v===null) return`<div class="ub-s" style="background:var(--down)"></div>`;
    return`<div class="ub-s" style="background:var(--up)"></div>`;
  }).join('');
  return `
  <div class="stl-sdot ${st}"></div>
  <div class="vm-row-nm">${esc(metricLabel)}</div>
  <span class="stl-muted" id="sm-muted-${sk}" title="Alerts muted" style="${isMuted?'':'display:none'}">🔕</span>
  <div class="vm-row-val ${vc}" id="stv-${sk}">${vt}</div>
  <div class="ub vm-ub" id="ub-${sk}">${ub}</div>
  <canvas class="spk" height="28" style="flex:0 0 60px"></canvas>
  <button class="stl-hist" onclick="event.stopPropagation();openDetail('${s.device_id}','${s.sensor_id}','history')" title="History">⌚</button>
  <button class="vm-row-del" onclick="event.stopPropagation();delVmRow('${s.device_id}','${s.sensor_id}')" title="Remove metric">✕</button>
  <span id="std-${sk}" style="display:none">${esc(s.last_detail||'')}</span>`;
}

// ── Sparkline ────────────────────────────────────────────────────
function drawSpk(key,history){
  const info=S.charts[key];if(!info)return;
  const{canvas,ctx}=info;
  canvas.width=canvas.offsetWidth||260;
  const W=canvas.width,H=28;ctx.clearRect(0,0,W,H);
  if(!history||history.length<2)return;
  const valid=history.filter(v=>v!==null);
  const maxV=valid.length?Math.max(...valid)*1.2:200;
  const step=W/(history.length-1);
  const pts=[];
  history.forEach((v,i)=>{if(v!==null)pts.push({x:i*step,y:H-(v/maxV)*(H-3)});});
  if(pts.length<2)return;
  const _up = _SCC.up.join(',');
  const g=ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,`rgba(${_up},.22)`);g.addColorStop(1,`rgba(${_up},0)`);
  ctx.beginPath();ctx.moveTo(pts[0].x,H);
  pts.forEach(p=>ctx.lineTo(p.x,p.y));
  ctx.lineTo(pts[pts.length-1].x,H);ctx.closePath();ctx.fillStyle=g;ctx.fill();
  ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.strokeStyle=`rgb(${_up})`;ctx.lineWidth=1.5;ctx.stroke();
}

// ── Device status recalc ─────────────────────────────────────────
function recalcDevStatus(did){
  const keys=S._devSensors?.[did]||new Set();
  const devMuted=S.devices[did]?.alerts_muted;
  const active=[...keys].map(k=>S.sensors[k]).filter(s=>s&&!s.alerts_muted&&!devMuted);
  const alives=active.map(s=>s.alive);
  const thresholds=active.map(s=>s.threshold_state);
  let st='unknown';
  if(alives.some(a=>a===false))st='down';
  else if(thresholds.some(t=>t==='crit'))st='down';
  else if(thresholds.some(t=>t==='warn'))st='warn';
  else if(alives.every(a=>a===true))st='up';
  else if(alives.some(a=>a===true))st='warn';
  if(S.devices[did])S.devices[did].status=st;
  updateDpHeader(did,st);
}
function updateDpHeader(did,st){
  updateCardStatus(did,st);
  // update device window bar if open
  const bar=document.getElementById(`dwbar-${did}`);
  if(bar) bar.className=`dw-bar ${st}`;
}

// ── Device actions ───────────────────────────────────────────────
async function startDev(did){await api('POST',`/api/device/${did}/start`);toast('Monitoring started','ok')}
async function stopDev(did) {await api('POST',`/api/device/${did}/stop`); toast('Monitoring stopped','info')}
async function delDev(did){
  if(!confirm('Delete device and all its sensors?'))return;
  await api('DELETE',`/api/device/${did}`);
  document.getElementById(`dp-${did}`)?.remove();
  document.getElementById(`dpl-${did}`)?.remove();
  closeM('dwo');
  delete S.devices[did];
  Object.keys(S.sensors).filter(k=>k.startsWith(did+'/')).forEach(k=>{delete S.sensors[k];delete S.charts[k];delete S.logs[k];});
  delete S._devSensors[did];
  pruneEmptyGroups();
  refreshGroupCounts();
  updatePills();
  if(!Object.keys(S.devices).length){
    document.getElementById('emptyMain').style.display='flex';
    document.getElementById('dpanels').style.display='none';
  } else if(activeMainTab==='devices') {
    document.getElementById('dpanels').style.display='';
  }
  toast('Device removed','info');
}

// ── Device scan ──────────────────────────────────────────────────
let _scanDid=null, _scanServices=[];

async function openScanModal(did){
  _scanDid=did; _scanServices=[];
  closeM('mdscan');
  const dev=S.devices[did];
  const host=dev?.host||did;
  const o=document.createElement('div');o.className='mo';o.id='mdscan';
  o.onclick=e=>{if(e.target===o)closeM('mdscan');};
  o.innerHTML=`
  <div class="mbox" style="max-width:500px;width:95vw">
    <div class="mhd">
      <div class="mttl">Scan — <span style="color:var(--text2)">${esc(dev?.name||did)}</span>
        <span style="font-size:11px;color:var(--text3);font-weight:400"> (${esc(host)})</span></div>
      <button class="mclose" onclick="closeM('mdscan')">&#10005;</button>
    </div>
    <div id="scan-body" style="max-height:380px;overflow-y:auto;display:flex;flex-direction:column;
         gap:5px;padding:4px 0">
      <div class="scan-spin">&#8635; Scanning&hellip;</div>
    </div>
    <div class="mfoot" style="justify-content:space-between">
      <button class="btn-s" onclick="closeM('mdscan')">Close</button>
      <button class="btn-p" id="btn-add-scanned" style="display:none"
              onclick="addScannedSensors()">Add Selected</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  const ctrl=new AbortController();
  const tid=setTimeout(()=>ctrl.abort(),30000);
  try{
    const r=await fetch(`/api/device/${did}/scan`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:'{}',signal:ctrl.signal,
    });
    clearTimeout(tid);
    _scanServices=(await r.json()).services||[];
    _renderScanResults(did,host,_scanServices);
  }catch(e){
    clearTimeout(tid);
    const b=document.getElementById('scan-body');
    const msg=e.name==='AbortError'?'Scan timed out (30s)':`Scan failed: ${esc(String(e))}`;
    if(b)b.innerHTML=`<div class="scan-spin" style="color:var(--down)">${msg}</div>`;
  }
}

function _renderScanResults(did,host,services){
  const body=document.getElementById('scan-body');
  const btn=document.getElementById('btn-add-scanned');
  if(!body)return;
  if(!services.length){
    body.innerHTML=`<div class="scan-spin">No services found on ${esc(host)}</div>`;
    if(btn)btn.style.display='none';
    return;
  }
  // Build set of already-monitored stype:port keys for this device
  const existingKeys=new Set(
    Object.keys(S.sensors)
      .filter(k=>k.startsWith(did+'/'))
      .map(k=>{ const s=S.sensors[k]; return `${s.stype}:${s.port||''}`; })
  );
  body.innerHTML=services.map((svc,i)=>{
    const key=`${svc.stype}:${svc.port||''}`;
    const exists=existingKeys.has(key);
    const ms=svc.ms!=null?`${svc.ms}ms`:'';
    const portLabel=svc.port?` (${svc.port})`:'';
    const badge=exists?' <span style="color:var(--text3);font-size:10px">[exists]</span>':'';
    return `<label class="scan-row">
      <input type="checkbox" ${exists?'':'checked'} data-i="${i}">
      <span class="scan-nm">${esc(svc.name)}${portLabel}${badge}</span>
      <span class="scan-ms">${ms}</span>
      <span class="scan-detail" title="${esc(svc.detail)}">${esc(svc.detail)}</span>
    </label>`;
  }).join('');
  _updateAddBtn();
  body.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.addEventListener('change',_updateAddBtn));
  if(btn)btn.style.display='';
}

function _updateAddBtn(){
  const n=document.getElementById('scan-body')?.querySelectorAll('input:checked').length||0;
  const btn=document.getElementById('btn-add-scanned');
  if(!btn)return;
  btn.textContent=n?`Add Selected (${n})`:'Add Selected';
  btn.disabled=!n;
}

async function addScannedSensors(){
  if(!_scanDid)return;
  const did=_scanDid;
  const dev=S.devices[did];
  const host=dev?.host||'';
  const checks=document.getElementById('scan-body')?.querySelectorAll('input:checked')||[];
  const btn=document.getElementById('btn-add-scanned');
  if(btn){btn.disabled=true;btn.textContent='Adding\u2026';}
  let added=0,failed=0;
  for(const cb of checks){
    const svc=_scanServices[parseInt(cb.dataset.i)];
    if(!svc)continue;
    const isHttp=svc.stype==='http';
    const isPing=svc.stype==='ping';
    const body={
      type:svc.stype, name:svc.name,
      host:host, port:svc.port||'',
      url:isHttp?(svc.port&&svc.port!==80?`http://${host}:${svc.port}`:`http://${host}`):'',
      interval:isPing?5:30,
      timeout:isPing?4:(isHttp||svc.stype==='tls'?8:5),
      verify_ssl:false,
    };
    try{
      const r=await api('POST',`/api/device/${did}/sensor`,body);
      if(r.sid){
        await api('POST',`/api/device/${did}/sensor/${r.sid}/start`,{});
        added++;
      }else{failed++;}
    }catch{failed++;}
  }
  closeM('mdscan');
  const msg=added
    ?`Added ${added} sensor${added>1?'s':''}${failed?' ('+failed+' failed)':''}`
    :(failed?`Failed to add ${failed} sensor${failed>1?'s':''}`:'Nothing added');
  toast(msg,added?'ok':failed?'err':'info');
  if(added){
    try{
      const r=await fetch(`/api/device/${did}`);
      const updated=await r.json();
      if(updated&&updated.device_id){
        S.devices[did]=updated;
        (updated.sensors||[]).forEach(s=>{
          const key=`${did}/${s.sensor_id}`;
          S.sensors[key]=s;
          if(!S.logs[key])S.logs[key]=[];
        });
        renderDp(updated);
        const grid=document.getElementById(`sg-${did}`);
        if(grid){
          grid.innerHTML='';
          (updated.sensors||[]).forEach(s=>renderTile(did,s));
        }
      }
    }catch(e){}
  }
}

// ── Sensor delete ────────────────────────────────────────────────
async function delSensor(did,sid){
  await api('DELETE',`/api/device/${did}/sensor/${sid}`);
  const key=`${did}/${sid}`;
  document.getElementById(`t-${key.replace('/','_')}`)?.remove();
  document.getElementById(`sbsr-${did}_${sid}`)?.remove();
  delete S.sensors[key];delete S.charts[key];delete S.logs[key];
  S._devSensors?.[did]?.delete(key);
  // Keep S.devices in sync so reopening device window doesn't re-render the deleted tile
  if(S.devices[did]) S.devices[did].sensors=(S.devices[did].sensors||[]).filter(s=>s.sensor_id!==sid);
  recalcDevStatus(did);updatePills();
  const previewEl=document.getElementById(`dcsnr-${did}`);
  if(previewEl) previewEl.innerHTML=sSnrPreview(did);
  const cntEl=document.querySelector(`#sbn-${did} .dev-cnt`);
  if(cntEl) cntEl.textContent=Object.values(S.sensors).filter(s=>s.device_id===did).length;
  toast('Sensor removed','info');
}

// ── Inline confirm (window.confirm is blocked on remote HTTP) ────
// opts: {danger:true, html:false}  danger=false → primary (non-destructive); html=true → msg is trusted HTML
function _pwConfirm(msg, onYes, yesLabel='Remove', opts={}){
  const danger = opts.danger !== false;
  const body   = opts.html ? msg : esc(msg);
  const yesCls = danger ? 'btn-danger' : 'btn-p';
  const ov=document.createElement('div');
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center';
  ov.innerHTML=`<div style="background:var(--bg2);border:1px solid var(--border2);border-radius:10px;padding:22px 26px;max-width:380px;text-align:center">
    <div style="color:var(--text);margin:0 0 16px;font-size:14px;line-height:1.5">${body}</div>
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="${yesCls}" id="_pwc-yes">${esc(yesLabel)}</button>
      <button class="dp-btn" id="_pwc-no">Cancel</button>
    </div></div>`;
  document.body.appendChild(ov);
  ov.querySelector('#_pwc-no').onclick=()=>ov.remove();
  ov.querySelector('#_pwc-yes').onclick=()=>{ov.remove();onYes();};
  ov.onclick=e=>{if(e.target===ov)ov.remove();};
}

// ── VMware vm-row / vm-group delete ──────────────────────────────
async function delVmRow(did,sid){
  const s=S.sensors[`${did}/${sid}`];
  const vmid=s?.vmware_vm_id;
  await delSensor(did,sid);
  if(vmid){
    const sfx=_vmGrpSfx(vmid);
    const body=document.getElementById(`vgbody-${did}-${sfx}`);
    if(body&&body.querySelectorAll('.vm-row').length===0){
      document.getElementById(`vmgrp-${did}-${sfx}`)?.remove();
    } else {
      _updateVmGrpStatus(did,vmid);
    }
  }
}

function delVmGrp(did,vmid){
  const sfx=_vmGrpSfx(vmid);
  const body=document.getElementById(`vgbody-${did}-${sfx}`);
  if(!body) return;
  const sids=[...body.querySelectorAll('.vm-row')].map(r=>r.dataset.sid).filter(Boolean);
  const count=sids.length;
  if(!count){document.getElementById(`vmgrp-${did}-${sfx}`)?.remove();return;}
  _pwConfirm(`Remove all ${count} metric${count===1?'':'s'} for this VM?`,async()=>{
    for(const sid of sids) await delSensor(did,sid);
    document.getElementById(`vmgrp-${did}-${sfx}`)?.remove();
  });
}

async function toggleVmGrpMute(did,vmid){
  const sfx=_vmGrpSfx(vmid);
  const body=document.getElementById(`vgbody-${did}-${sfx}`);
  if(!body) return;
  const sids=[...body.querySelectorAll('.vm-row')].map(r=>r.dataset.sid).filter(Boolean);
  if(!sids.length) return;
  // Determine target state: if ANY sensor is unmuted → mute all; else unmute all
  const anyUnmuted=sids.some(sid=>{const s=S.sensors[`${did}/${sid}`];return s&&!s.alerts_muted;});
  const mute=anyUnmuted;
  const btn=document.getElementById(`vgmute-${did}-${sfx}`);
  if(btn){btn.disabled=true;btn.textContent='...';}
  let ok=0;
  for(const sid of sids){
    try{
      const r=await api('PATCH',`/api/device/${did}/sensor/${sid}`,{alerts_muted:mute});
      if(r.status==='updated'){
        const s=S.sensors[`${did}/${sid}`];
        if(s) s.alerts_muted=mute;
        const badge=document.getElementById(`sm-muted-${did}_${sid}`);
        if(badge) badge.style.display=mute?'':'none';
        ok++;
      }
    }catch(e){}
  }
  _updateVmGrpMuteBtn(did,vmid);
  if(btn) btn.disabled=false;
  toast(`${mute?'Muted':'Unmuted'} ${ok} sensor${ok===1?'':'s'}`,'ok');
}

function _updateVmGrpMuteBtn(did,vmid){
  const sfx=_vmGrpSfx(vmid);
  const body=document.getElementById(`vgbody-${did}-${sfx}`);
  const btn=document.getElementById(`vgmute-${did}-${sfx}`);
  if(!btn) return;
  if(!body){btn.textContent='🔕 Mute';return;}
  const sids=[...body.querySelectorAll('.vm-row')].map(r=>r.dataset.sid).filter(Boolean);
  const allMuted=sids.length>0&&sids.every(sid=>{const s=S.sensors[`${did}/${sid}`];return s?.alerts_muted;});
  btn.textContent=allMuted?'🔔 Unmute':'🔕 Mute';
  btn.title=allMuted?'Unmute alerts for all metrics':'Mute alerts for all metrics';
}

// ── Logs ─────────────────────────────────────────────────────────
function pushLog(did,sid,msg,type){
  const key=`${did}/${sid}`;
  if(!S.logs[key])S.logs[key]=[];
  const ts=new Date().toISOString();
  S.logs[key].unshift({ts,msg,type});
  if(S.logs[key].length>200)S.logs[key].pop();
  const el=document.getElementById(`dml-${did}-${sid}`);
  if(el){
    const d=document.createElement('div');
    d.className=`ll ${type}`;
    d.innerHTML=`<span class="ts">[${fmtTs(ts)}]</span> ${esc(msg)}`;
    el.insertBefore(d,el.firstChild);
    while(el.children.length>200)el.removeChild(el.lastChild);
  }
  maybeUpdateDevLog(did);
}

// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? DETAIL WINDOW �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?
// ── SNMP Counter rate helpers for history view ────────────────────────────
const _COUNTER_HIST_UNITS = new Set(['bytes','errors','packets']);
// v0.9.7: typed SNMP categories.  Dispatches rendering between counter-rate,
// enum state (interface up/down, UPS battery status, HA mode, …), gauge
// numeric (CPU %, temp °C, session count, …), TimeTicks duration (sysUpTime),
// and text (sysName / sysDescr).  Replaces the old binary "counter vs ms"
// split that silently rendered probe latency for every non-counter sensor.
const _GAUGE_UNITS = new Set([
  '%','percent','celsius','fahrenheit','dbm','count',
  'seconds','minutes','hours','hz','volts','amps','ratio','rpm',
]);
const _ENUM_UNIT_RE = /\d+\s*=\s*[a-z][\w-]*/i;

function _snmpCategory(snmpUnit, snmpType, snmpOid) {
  const u = (snmpUnit || '').toLowerCase().trim();
  if (_COUNTER_HIST_UNITS.has(u)) return 'counter_rate';
  if (snmpUnit && _ENUM_UNIT_RE.test(snmpUnit)) return 'enum_state';
  // v0.9.7: known-OID enum fallback for "Auto-detect" sensors (unit blank).
  if (!snmpUnit && snmpOid && _enumForOid(snmpOid)) return 'enum_state';
  if (_GAUGE_UNITS.has(u)) return 'gauge_numeric';
  if (snmpType === 'TimeTicks') return 'time_duration';
  if (snmpType === 'OCTET STRING' || u === 'string') return 'text';
  return 'gauge_numeric';   // safe default — renders value as a line, never ms
}

// Effective legend: user-set unit wins; known-OID fallback picks up
// "Auto-detect" sensors pointed at IF-MIB / UPS-MIB / etc.  Callers that
// need labeled enum state should prefer this over raw _parseEnumLegend.
function _effectiveEnumLegend(s) {
  const legend = _parseEnumLegend(s.snmp_unit);
  if (Object.keys(legend).length) return legend;
  const implicit = _enumForOid(s.snmp_oid);
  return implicit || {};
}

function _parseEnumLegend(snmpUnit) {
  const map = {};
  if (!snmpUnit) return map;
  for (const m of snmpUnit.matchAll(/(\d+)\s*=\s*([a-z][\w-]*)/gi)) {
    map[m[1]] = m[2];
  }
  return map;
}

// Well-known OID prefix → implicit enum legend.  Kicks in when the user
// added a sensor via the "Auto-detect" Display-as option (unit left blank)
// but the OID is a standard IF-MIB / ENTITY-MIB / UPS-MIB enum so we can
// confidently label the integer value.  Prefix-match so indexed rows like
// 1.3.6.1.2.1.2.2.1.8.44 resolve to ifOperStatus without needing per-index
// catalog entries.
const _KNOWN_ENUM_OIDS = [
  {prefix: '1.3.6.1.2.1.2.2.1.8.',  legend: {1:'up',2:'down',3:'testing',4:'unknown',5:'dormant',6:'notPresent',7:'lowerLayerDown'}},
  {prefix: '1.3.6.1.2.1.2.2.1.7.',  legend: {1:'up',2:'down',3:'testing'}},
  {prefix: '1.3.6.1.2.1.33.1.2.1.', legend: {1:'unknown',2:'batteryNormal',3:'batteryLow',4:'batteryDepleted'}},
];
function _enumForOid(oid) {
  if (!oid) return null;
  for (const e of _KNOWN_ENUM_OIDS) {
    if (oid.startsWith(e.prefix)) return e.legend;
  }
  return null;
}

// Shared formatter for SNMP sensor display values used by the device-tile
// sensor cards (devices.js) AND the Overview modal.  Returns the raw value
// formatted per category: enum → legend label, gauge → unit-suffixed number,
// duration → "Xd Yh", counter/text → raw string.
function _snmpTileValue(s) {
  if (s.alive === false) return 'FAIL';
  if (s.last_value == null || s.last_value === '') return '—';
  // Counter sensors: backend populates last_rate (bytes/errors/packets per sec)
  // and last_value already carries the formatted rate string ("184.4 Kbps").
  // Prefer last_rate → _fmtRateDisplay so sensors with blank snmp_unit still
  // get Kbps/Mbps suffixing instead of falling through to gauge_numeric and
  // being stripped down to a bare number.
  if (s.last_rate != null) {
    return _fmtRateDisplay(s.last_rate, s.snmp_unit || 'bytes');
  }
  const cat = _snmpCategory(s.snmp_unit, s.snmp_type, s.snmp_oid);
  // Enum-first resolution (defensive): if the sensor has a parseable legend
  // OR its OID matches a well-known IF-MIB / UPS-MIB enum, prefer labeled
  // output.  Catches the "Auto-detect" case where snmp_unit is blank but
  // the OID family tells us it's ifOperStatus / ifAdminStatus / battery
  // status.  Only skipped for counter_rate (already formatted upstream).
  if (cat !== 'counter_rate') {
    let legend = _parseEnumLegend(s.snmp_unit);
    if (!Object.keys(legend).length) {
      const implicit = _enumForOid(s.snmp_oid);
      if (implicit) legend = implicit;
    }
    if (Object.keys(legend).length) {
      const n = parseInt(s.last_value, 10);
      if (!isNaN(n) && legend[String(n)]) return legend[String(n)];
    }
  }
  if (cat === 'gauge_numeric') {
    const v = parseFloat(s.last_value);
    if (!isNaN(v)) return _fmtGaugeValue(v, s.snmp_unit);
  }
  if (cat === 'time_duration') {
    const v = parseFloat(s.last_value);
    if (!isNaN(v)) return _fmtDurationSec(v / 100);
  }
  // counter_rate sensors keep their existing formatted last_value
  // (e.g. "141.2 Kbps" from _fmt_rate on the backend).  text / unknown
  // fall through with a length cap so the tile doesn't blow out.
  return String(s.last_value).slice(0, 20);
}

function _snmpCategoryFor(did, sid) {
  const s = S.sensors[`${did}/${sid}`];
  if (!s || s.stype !== 'snmp') return null;
  return _snmpCategory(s.snmp_unit, s.snmp_type, s.snmp_oid);
}

function _fmtDurationSec(secs) {
  if (secs == null || !isFinite(secs) || secs < 0) return '—';
  const d = Math.floor(secs / 86400);
  const h = Math.floor((secs % 86400) / 3600);
  const m = Math.floor((secs % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${Math.floor(secs % 60)}s`;
}

function _fmtGaugeValue(v, snmpUnit) {
  if (v == null || !isFinite(v)) return '—';
  const u = (snmpUnit || '').toLowerCase();
  if (u === '%' || u === 'percent') return v.toFixed(v < 10 ? 1 : 0) + '%';
  if (u === 'celsius')    return v.toFixed(1) + ' °C';
  if (u === 'fahrenheit') return v.toFixed(1) + ' °F';
  if (u === 'dbm')        return v.toFixed(1) + ' dBm';
  if (u === 'hz')         return v >= 1e9 ? (v/1e9).toFixed(2)+' GHz' : v >= 1e6 ? (v/1e6).toFixed(2)+' MHz' : v >= 1e3 ? (v/1e3).toFixed(1)+' kHz' : v.toFixed(0)+' Hz';
  if (u === 'volts')      return v.toFixed(2) + ' V';
  if (u === 'amps')       return v.toFixed(2) + ' A';
  if (u === 'ratio')      return v.toFixed(3);
  if (u === 'rpm')        return Math.round(v) + ' RPM';
  if (u === 'seconds')    return _fmtDurationSec(v);
  if (u === 'minutes')    return _fmtDurationSec(v * 60);
  if (u === 'hours')      return _fmtDurationSec(v * 3600);
  // count / unknown — SI suffixes for large numbers
  if (Math.abs(v) >= 1e9) return (v/1e9).toFixed(2) + 'G';
  if (Math.abs(v) >= 1e6) return (v/1e6).toFixed(2) + 'M';
  if (Math.abs(v) >= 1e3) return (v/1e3).toFixed(1) + 'K';
  return (Math.abs(v) < 1 ? v.toFixed(3) : (Math.abs(v) < 10 ? v.toFixed(2) : v.toFixed(0)));
}

function _gaugeValSamples(samples) {
  // Normalise raw + rollup samples into {ts, ok, v, vMin, vMax, ms} with v
  // as a numeric gauge value (or null when unparseable / non-ok).  Raw rows
  // carry `value` (TEXT); rollup rows carry `avg_value` / `min_value` /
  // `max_value`.  For enum_state rendering the caller just uses v (the state
  // code); for gauge_numeric the envelope bounds drive the min/max band.
  return samples.map(p => {
    const isRollup = p.bucket_s != null;
    let v, vMin, vMax;
    if (isRollup) {
      v = p.avg_value;
      vMin = p.min_value != null ? p.min_value : v;
      vMax = p.max_value != null ? p.max_value : v;
    } else {
      const parsed = p.value != null ? parseFloat(p.value) : NaN;
      v = isNaN(parsed) ? null : parsed;
      vMin = vMax = v;
    }
    return {ts: p.ts, ok: !!p.ok, v, vMin, vMax, ms: p.ms, raw: p.value};
  });
}

function _enumTransitions(gvs) {
  // Count state-code changes across ok samples.  Returns {count, lastChangeTs}.
  let count = 0, lastCh = null, prev = null;
  for (const g of gvs) {
    if (!g.ok || g.v == null) continue;
    if (prev != null && g.v !== prev) { count++; lastCh = g.ts; }
    prev = g.v;
  }
  return {count, lastChangeTs: lastCh};
}

function _fmtGaugeYLabel(v, snmpUnit) {
  if (v == null || !isFinite(v)) return '';
  const u = (snmpUnit || '').toLowerCase();
  if (u === '%' || u === 'percent') return v.toFixed(0) + '%';
  if (u === 'celsius')    return v.toFixed(0) + '°';
  if (u === 'fahrenheit') return v.toFixed(0) + '°F';
  if (u === 'dbm')        return v.toFixed(0) + 'dB';
  if (Math.abs(v) >= 1e9) return (v/1e9).toFixed(1) + 'G';
  if (Math.abs(v) >= 1e6) return (v/1e6).toFixed(1) + 'M';
  if (Math.abs(v) >= 1e3) return (v/1e3).toFixed(1) + 'K';
  return v.toFixed(Math.abs(v) < 10 ? 1 : 0);
}

function _computeRateSamples(samples, snmpUnit) {
  if (!_COUNTER_HIST_UNITS.has(snmpUnit)) return null;
  // Each returned item carries {ts, ok, rate, min, max, ms}:
  //   rate = central value (drawn as the avg line)
  //   min/max = bucket extremes (drive the min/max envelope band)
  // At raw tier, min = max = rate (single probe).  At rollup tier, min/max
  // capture peak-preserving aggregates (v0.9.7) — a 30-second burst survives
  // at 3d / 30d / 1y views instead of being averaged away.
  const hasBuckets = samples.some(p => p.bucket_s);
  if (hasBuckets) {
    const result = [];
    for (const p of samples) {
      if (!p.ok) {
        result.push({ts: p.ts, ok: false, rate: null, min: null, max: null, ms: p.ms});
        continue;
      }
      // v0.9.7: prefer backend rate aggregates (Counter64-safe, peak-preserving).
      if (p.avg_rate != null) {
        result.push({
          ts: p.ts, ok: true,
          rate: p.avg_rate,
          min: p.min_rate != null ? p.min_rate : p.avg_rate,
          max: p.max_rate != null ? p.max_rate : p.avg_rate,
          ms:  p.ms,
        });
        continue;
      }
      // v0.9.6 fallback: bucket-endpoint derivation (smoothed avg only).
      // Applies to rollup rows that predate the v0.9.7 migration.
      if (p.first_value != null && p.last_value != null) {
        let delta = p.last_value - p.first_value;
        if (delta < 0) delta += 4294967296; // Counter32 wrap (frontend-only fallback)
        const r = delta / p.bucket_s;
        result.push({ts: p.ts, ok: true, rate: r, min: r, max: r, ms: p.ms});
        continue;
      }
      result.push({ts: p.ts, ok: true, rate: null, min: null, max: null, ms: p.ms});
    }
    return result;
  }
  // Raw tier.
  const sorted = [...samples].sort((a, b) => a.ts - b.ts);
  const result = [];
  for (let i = 0; i < sorted.length; i++) {
    const curr = sorted[i];
    if (!curr.ok) { result.push({ts: curr.ts, ok: false, rate: null, min: null, max: null, ms: curr.ms}); continue; }
    // v0.9.7: prefer backend-computed rate (correct Counter64 wrap, no prev needed).
    if (curr.rate != null) {
      result.push({ts: curr.ts, ok: true, rate: curr.rate, min: curr.rate, max: curr.rate, ms: curr.ms});
      continue;
    }
    // Fallback: client-side diff of consecutive raw counters (Counter32 only).
    if (i === 0) continue;
    const prev = sorted[i-1];
    if (curr.value == null || prev.value == null) continue;
    const elapsed = curr.ts - prev.ts;
    if (elapsed <= 0 || elapsed > 300) continue;
    let delta = parseFloat(curr.value) - parseFloat(prev.value);
    if (isNaN(delta)) continue;
    if (delta < 0) delta += 4294967296; // Counter32 wrap
    const r = delta / elapsed;
    result.push({ts: curr.ts, ok: true, rate: r, min: r, max: r, ms: curr.ms});
  }
  return result;
}

function _fmtRateDisplay(ratePerSec, snmpUnit) {
  if (snmpUnit==='bytes') {
    const bps=ratePerSec*8;
    if(bps>=1e9) return (bps/1e9).toFixed(2)+' Gbps';
    if(bps>=1e6) return (bps/1e6).toFixed(2)+' Mbps';
    if(bps>=1e3) return (bps/1e3).toFixed(1)+' Kbps';
    return bps.toFixed(0)+' bps';
  }
  if (snmpUnit==='errors')  return ratePerSec.toFixed(ratePerSec<10?2:1)+' err/s';
  if (snmpUnit==='packets') return ratePerSec.toFixed(ratePerSec<10?2:1)+' pkt/s';
  return ratePerSec.toFixed(2)+'/s';
}

function _rateToDisplayUnits(ratePerSec, snmpUnit) {
  return snmpUnit==='bytes' ? ratePerSec*8/1e6 : ratePerSec;
}

function _fmtRateYLabel(displayVal, snmpUnit) {
  if (snmpUnit==='bytes') {
    if(displayVal>=1000) return (displayVal/1000).toFixed(1)+'G';
    if(displayVal>=1)    return displayVal.toFixed(1)+'M';
    return (displayVal*1000).toFixed(0)+'K';
  }
  if (snmpUnit==='errors')  return displayVal.toFixed(1)+'e/s';
  if (snmpUnit==='packets') return displayVal.toFixed(1)+'p/s';
  return displayVal.toFixed(1);
}

function _fmtRateThrLabel(displayVal, snmpUnit) {
  if (snmpUnit==='bytes') {
    if(displayVal>=1000) return (displayVal/1000).toFixed(1)+' Gbps';
    return displayVal.toFixed(1)+' Mbps';
  }
  if (snmpUnit==='errors')  return displayVal.toFixed(1)+' err/s';
  if (snmpUnit==='packets') return displayVal.toFixed(1)+' pkt/s';
  return displayVal.toFixed(1)+'/s';
}

// ── VMware metric unit helpers ───────────────────────────────────
const _VM_UNITS={cpu_usage:'%',cpu_ready:'%',mem_active:'MB',mem_consumed:'MB',disk_read:'KBps',disk_write:'KBps',disk_usage:'KBps',disk_used_pct:'%',ds_read_lat:'ms',ds_write_lat:'ms',net_rx:'KBps',net_tx:'KBps',net_usage:'KBps',uptime:'seconds',on:'',
  host_cpu_usage:'%',host_cpu_ready:'%',host_mem_active:'MB',host_mem_consumed:'MB',host_mem_usage_pct:'%',host_mem_swap:'MB',
  host_disk_read:'KBps',host_disk_write:'KBps',host_disk_usage:'KBps',host_disk_dev_lat:'ms',host_disk_kern_lat:'ms',
  host_ds_read_lat:'ms',host_ds_write_lat:'ms',host_net_rx:'KBps',host_net_tx:'KBps',host_net_usage:'KBps',
  host_power:'watt',host_uptime:'seconds',
  dstore_free_gb:'GB'};
function _vmUnit(did,sid){const s=S.sensors[`${did}/${sid}`];return(s?.stype==='vmware')?(_VM_UNITS[s.vmware_metric]||''):null;}
function _fmtVmVal(v,u){
  if(v==null)return'—';
  switch(u){
    case'%':return v.toFixed(2)+'%';
    case'MB':return v>=1024?(v/1024).toFixed(1)+' GB':v.toFixed(0)+' MB';
    case'KB':return v>=1048576?(v/1048576).toFixed(1)+' GB':v>=1024?(v/1024).toFixed(1)+' MB':v+' KB';
    case'KBps':return v>=1024?(v/1024).toFixed(1)+' MBps':v.toFixed(1)+' KBps';
    case'ms':return v.toFixed(1)+' ms';
    case'seconds':{const d=Math.floor(v/86400),h=Math.floor((v%86400)/3600),m=Math.floor((v%3600)/60);return d>0?`${d}d ${h}h ${m}m`:h>0?`${h}h ${m}m`:`${m}m`;}
    case'watt':return v.toFixed(0)+' W';
    case'GB':return v>=1024?(v/1024).toFixed(2)+' TB':v.toFixed(1)+' GB';
    default:return String(v);
  }
}
function _vmUnitLabel(u){return u==='%'?'%':u==='MB'?'MB':u==='KB'?'MB':u==='KBps'?'KBps':u==='ms'?'ms':u==='seconds'?'time':u==='watt'?'W':u==='GB'?'GB':'';}
function _fmtVmYLabel(v,u){
  switch(u){
    case'%':return Math.round(v)+'%';
    case'MB':return v>=1024?(v/1024).toFixed(1)+'GB':Math.round(v)+'MB';
    case'KB':return v>=1048576?(v/1048576).toFixed(1)+'GB':v>=1024?(v/1024).toFixed(0)+'MB':Math.round(v)+'KB';
    case'KBps':return v>=1024?(v/1024).toFixed(1)+'MBps':Math.round(v)+'KBps';
    case'ms':return Math.round(v)+'ms';
    case'seconds':return v>=86400?(v/86400).toFixed(1)+'d':v>=3600?(v/3600).toFixed(1)+'h':Math.round(v/60)+'m';
    case'watt':return Math.round(v)+'W';
    case'GB':return v>=1024?(v/1024).toFixed(2)+'TB':Math.round(v)+'GB';
    default:return String(Math.round(v));
  }
}

function openDetail(did,sid,initialTab){
  const key=`${did}/${sid}`;
  const s=S.sensors[key];if(!s)return;
  const tgt=s.stype==='http'?(s.url||s.host):s.stype==='tcp'?`${s.host}:${s.port}`:s.stype==='snmp'?`${s.host}:${s.port||161} · ${s.snmp_community} · OID ${s.snmp_oid}`:s.stype==='dns'?`${s.dns_query||s.host} · ${s.dns_record_type||'A'}${s.dns_server?' via '+s.dns_server:''}`:s.stype==='vmware'?`${s.host}:${s.port||443} · VM ${s.vmware_vm_id} · ${s.vmware_metric}`:s.host;
  closeM('dm');
  const o=document.createElement('div');
  o.className='dmo';o.id='dm';
  o.onclick=e=>{if(e.target===o)closeM('dm')};
  o.innerHTML=`
  <div class="dmbox">
    <div class="dm-hd">
      <div class="dm-tbdg ${s.stype}">${sIco(s.stype)}<br>${s.stype.toUpperCase()}</div>
      <div class="dm-hd-info">
        <div class="dm-ttl">${esc(S.devices[did]?.name||did)}</div>
        <div class="dm-sname">${esc(s.name)}</div>
        <div class="dm-tgt">${esc(tgt)}</div>
      </div>
      <div class="dm-hd-actions">
        <button class="dp-btn s" onclick="startDev('${did}')">▶</button>
        <button class="dp-btn" onclick="openEditSensor('${did}','${sid}')">✎ Edit</button>
        <button class="dp-btn d" onclick="delSensor('${did}','${sid}');closeM('dm')">✕ Remove</button>
      </div>
      <button class="mclose" onclick="closeM('dm')">✕</button>
    </div>
    <div class="dm-tabs">
      <button class="dm-tab active" id="dm-tabn-overview-${did}-${sid}"
        onclick="dmSwitchTab('${did}','${sid}','overview')">Overview</button>
      <button class="dm-tab" id="dm-tabn-history-${did}-${sid}"
        onclick="dmSwitchTab('${did}','${sid}','history')">📈 History</button>
    </div>
    <div class="dm-body" id="dm-tab-overview-${did}-${sid}">
      <div class="dm-metrics">
        ${['last','avg','min','max','loss','sent'].map(k=>`
        <div class="dm-m">
          <span class="dm-mv" id="dmv-${did}-${sid}-${k}">${mVal(s,k)}</span>
          <span class="dm-mk">${k.toUpperCase()}</span>
        </div>`).join('')}
      </div>
      <div class="dm-chart-wrap">
        <div class="dm-cht">
          <span>${s.stype==='snmp'?'Poll history':s.stype==='tls'?'Check history':s.stype==='vmware'?'Metric history':'Latency'} — last ${s.history?.length||0} samples</span>
          <span id="dmlbl-${did}-${sid}"></span>
        </div>
        <canvas class="dmc" id="dmc-${did}-${sid}" height="110"></canvas>
      </div>
      <div class="dm-log-wrap">
        <div class="dm-lhd">
          <span>▸ Event Log</span>
          <span style="display:flex;align-items:center;gap:8px">
            <span style="font-family:'JetBrains Mono',monospace;color:var(--text3)">
              ${s.total} checks · every ${s.interval}s · timeout ${s.timeout}s
            </span>
            <button class="dp-btn" id="dml-clr-${did}-${sid}" onclick="clearSensorLog('${did}','${sid}')">✕ Clear</button>
          </span>
        </div>
        <div class="dm-lbody" id="dml-${did}-${sid}"></div>
      </div>
    </div>
    <div class="dm-body" id="dm-tab-history-${did}-${sid}" style="display:none">
      <div class="dm-hist-bar">
        <div class="dm-hist-pills">
          <button class="dm-hist-pill" data-m="60"     onclick="dmHistPick('${did}','${sid}',60)">1h</button>
          <button class="dm-hist-pill" data-m="360"    onclick="dmHistPick('${did}','${sid}',360)">6h</button>
          <button class="dm-hist-pill" data-m="720"    onclick="dmHistPick('${did}','${sid}',720)">12h</button>
          <button class="dm-hist-pill active" data-m="1440"   onclick="dmHistPick('${did}','${sid}',1440)">24h</button>
          <button class="dm-hist-pill" data-m="4320"   onclick="dmHistPick('${did}','${sid}',4320)">3d</button>
          <button class="dm-hist-pill" data-m="10080"  onclick="dmHistPick('${did}','${sid}',10080)">7d</button>
          <button class="dm-hist-pill" data-m="43200"  onclick="dmHistPick('${did}','${sid}',43200)">30d</button>
          <button class="dm-hist-pill" data-m="129600" onclick="dmHistPick('${did}','${sid}',129600)">90d</button>
          <button class="dm-hist-pill" data-m="525600"  onclick="dmHistPick('${did}','${sid}',525600)">1y</button>
          <button class="dm-hist-pill" data-m="1051200" onclick="dmHistPick('${did}','${sid}',1051200)">2y</button>
          <button class="dm-hist-pill" data-m="1576800" onclick="dmHistPick('${did}','${sid}',1576800)">3y</button>
        </div>
        <span id="dm-hist-stats-${did}-${sid}" class="dm-hist-stats"></span>
      </div>
      <div class="dm-kpi-bar" id="kpi-${did}-${sid}">
        <div class="dm-kpi-item" id="kpi-avail-${did}-${sid}">Avail<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-avg-${did}-${sid}">Avg<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-min-${did}-${sid}">Min<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-max-${did}-${sid}">Max<br><span>—</span></div>
        ${s.stype==='ping'?`<div class="dm-kpi-item" id="kpi-loss-${did}-${sid}">Loss %<br><span>—</span></div>`:''}
        <div class="dm-kpi-item" id="kpi-jitter-${did}-${sid}">Jitter<br><span>—</span></div>
      </div>
      <div class="dm-metric-toggles">
        <label><input type="checkbox" id="tog-avg-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> Avg</label>
        <label><input type="checkbox" id="tog-band-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> Min/Max</label>
        ${s.stype==='ping'?`<label><input type="checkbox" id="tog-loss-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> Loss%</label>`:''}
        <label><input type="checkbox" id="tog-jitter-${did}-${sid}" onchange="dmHistRedraw('${did}','${sid}')"> Jitter</label>
        ${s?.anomaly_enabled && ['ping','tcp','http','dns','http_keyword','banner'].includes(s.stype)
          ? `<label title="Show learned baseline band (μ ± k·σ)"><input type="checkbox" id="tog-baseline-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> 🧠 Baseline</label>` : ''}
        <button class="dm-ar-btn" id="ar-${did}-${sid}" onclick="dmToggleAutoRefresh('${did}','${sid}')">Auto-Refresh</button>
        <button class="dm-ar-btn" id="fs-${did}-${sid}" data-did="${did}" data-sid="${sid}" onclick="dmToggleFullscreen('${did}','${sid}')" title="Full screen">⤢</button>
      </div>
      <div class="dm-hist-cwrap" style="position:relative">
        <canvas id="dm-hist-canvas-${did}-${sid}" class="dm-hist-canvas" height="320"></canvas>
        <div class="dm-hist-tip" id="tip-${did}-${sid}"></div>
      </div>
      <div id="dm-hist-summary-${did}-${sid}" class="dm-hist-summary"></div>
    </div>
    <div class="dm-ft">
      <button class="btn-s" onclick="closeM('dm')">Close</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>{
    const cvs=document.getElementById(`dmc-${did}-${sid}`);
    if(cvs)drawDmChart(cvs,s.history||[]);
    const lbEl=document.getElementById(`dml-${did}-${sid}`);
    (S.logs[key]||[]).forEach(e=>{
      const d=document.createElement('div');
      d.className=`ll ${e.type}`;
      d.innerHTML=`<span class="ts">[${fmtTs(e.ts)}]</span> ${esc(e.msg)}`;
      lbEl.appendChild(d);
    });
    const lbl=document.getElementById(`dmlbl-${did}-${sid}`);
    if(lbl&&s.max_ms&&s.stype!=='snmp'&&s.stype!=='tls')lbl.textContent=`max ${s.max_ms}ms · avg ${s.avg_ms||'—'}ms`;
    if(initialTab==='history') dmSwitchTab(did,sid,'history');
  },30);
}

async function clearSensorLog(did,sid){
  const btn=document.getElementById(`dml-clr-${did}-${sid}`);
  if(btn){btn.disabled=true;btn.textContent='Clearing...';}
  await api('DELETE',`/api/device/${did}/sensor/${sid}/logs`);
  if(btn){btn.disabled=false;btn.textContent='✕ Clear';}
  const key=`${did}/${sid}`;
  S.logs[key]=[];
  const lbEl=document.getElementById(`dml-${did}-${sid}`);
  if(lbEl) lbEl.innerHTML='';
}

// ── Detail window — History tab ───────────────────────────────────────────

function dmSwitchTab(did, sid, tab) {
  ['overview','history'].forEach(t => {
    document.getElementById(`dm-tab-${t}-${did}-${sid}`).style.display = t===tab?'':'none';
    document.getElementById(`dm-tabn-${t}-${did}-${sid}`).classList.toggle('active', t===tab);
  });
  if (tab==='history') loadDmHistory(did, sid, 1440);
}

function dmHistPick(did, sid, minutes) {
  document.querySelectorAll(`#dm-tab-history-${did}-${sid} .dm-hist-pill`)
    .forEach(b => b.classList.toggle('active', +b.dataset.m === minutes));
  loadDmHistory(did, sid, minutes);
}

// Per-sensor history data cache keyed by "did/sid"
const _histCache = {};

// Shared chart renderer — callable from both the sensor detail modal and dashboard widgets
async function _renderHistoryChart(canvas, statsEl, sumEl, did, sid, minutes) {
  if (!canvas) return;
  // Only show loading fade on first load — silent refresh on ticks to avoid flash
  const _cacheKey = `${did}/${sid}`;
  const _isRefresh = !!_histCache[_cacheKey];
  const _fadeEls = [];
  if (!_isRefresh) {
    if (statsEl) statsEl.textContent = 'Loading…';
    [document.getElementById(`kpi-${did}-${sid}`), canvas.parentElement, sumEl]
      .filter(Boolean).forEach(el => { _fadeEls.push(el); el.classList.add('dm-hist-loading'); });
  }
  const _loadT0 = performance.now();
  const dynamicLimit = Math.min(10000, Math.max(500, Math.round(minutes * 60 / 10)));
  const [hr, sr] = await Promise.all([
    fetch(`/api/device/${did}/sensor/${sid}/history?minutes=${minutes}&limit=${dynamicLimit}`)
      .then(r => r.json()).catch(() => ({})),
    fetch(`/api/device/${did}/sensor/${sid}/summary?minutes=${minutes}`)
      .then(r => r.json()).catch(() => ({})),
  ]);
  const samples = hr.samples || [];
  const summary = sr.summary || [];
  const windowStart = Date.now() / 1000 - minutes * 60;
  const _senObj = S.sensors[`${did}/${sid}`];
  const _rawUnit = (_senObj?.stype==='snmp' && _senObj?.snmp_unit) ? _senObj.snmp_unit : '';
  // Fall back to 'bytes' for counter sensors created before snmp_unit was stored
  const _snmpUnit = _rawUnit || ((_senObj?.stype==='snmp' && _senObj?.last_rate != null) ? 'bytes' : '');
  const rateSamples = _computeRateSamples(samples, _snmpUnit);
  _histCache[`${did}/${sid}`] = { samples, summary, minutes, windowStart, rateSamples, snmpUnit: _snmpUnit };
  // Re-fetch elements by ID after the await: the modal may have been closed/reopened
  // while the fetch was in-flight, making the captured references stale (detached DOM).
  // Fall back to the originally-passed elements for callers that use their own IDs
  // (e.g. dashboard widgets use dw-canvas-${wid}, not dm-hist-canvas-${did}-${sid}).
  const c       = document.getElementById(`dm-hist-canvas-${did}-${sid}`) || canvas;
  const _statsEl = document.getElementById(`dm-hist-stats-${did}-${sid}`) || statsEl;
  const _sumEl   = document.getElementById(`dm-hist-summary-${did}-${sid}`) || sumEl;
  if (!c) return; // genuinely not found (modal closed mid-flight)
  _buildKpiBar(summary, samples, did, sid, rateSamples, _snmpUnit);
  _setupHistTooltip(c, summary, did, sid, minutes, rateSamples, _snmpUnit);
  _drawHistCanvas(c, _statsEl, did, sid, summary, samples, minutes, windowStart, rateSamples, _snmpUnit);
  if (_sumEl) _buildSummaryTable(_sumEl, summary, minutes, rateSamples, _snmpUnit, did, sid);
  // Ensure loading fade is visible for at least 250ms, then fade back in
  if (_fadeEls.length) {
    const _elapsed = performance.now() - _loadT0;
    if (_elapsed < 250) await new Promise(r => setTimeout(r, 250 - _elapsed));
    [document.getElementById(`kpi-${did}-${sid}`), c.parentElement,
     document.getElementById(`dm-hist-summary-${did}-${sid}`) || _sumEl
    ].forEach(el => { if (el) el.classList.remove('dm-hist-loading'); });
  }
  // If canvas.offsetWidth was 0 when _drawHistCanvas ran (layout race on first render),
  // the next animation frame will have correct dimensions — redraw from cache.
  requestAnimationFrame(() => dmHistRedraw(did, sid));
}

function dmHistRedraw(did, sid) {
  const cache = _histCache[`${did}/${sid}`];
  if (!cache) return;
  // Modal canvas (sensor detail view)
  const canvas  = document.getElementById(`dm-hist-canvas-${did}-${sid}`);
  const statsEl = document.getElementById(`dm-hist-stats-${did}-${sid}`);
  if (canvas) _drawHistCanvas(canvas, statsEl, did, sid, cache.summary, cache.samples,
    cache.minutes, cache.windowStart, cache.rateSamples, cache.snmpUnit);
  // Dashboard widget canvases for the same sensor — use a different element ID
  // (dw-canvas-${wid}) so they were skipped here before. Without this, theme
  // toggles left widgets stuck on the previous palette until the next 30s data
  // refresh fired _dwLoadSensorChart, which caused the visible "stuck dark
  // widget on light background" lag.
  try {
    if (typeof _dwLoad === 'function') {
      _dwLoad().forEach(w => {
        if (w.type !== 'sensor_chart') return;
        if (w.cfg?.did !== did || w.cfg?.sid !== sid) return;
        const wc = document.getElementById(`dw-canvas-${w.id}`);
        const ws = document.getElementById(`dw-stats-${w.id}`);
        if (wc) _drawHistCanvas(wc, ws, did, sid, cache.summary, cache.samples,
          cache.minutes, cache.windowStart, cache.rateSamples, cache.snmpUnit);
      });
    }
  } catch (_) { /* dashboard module may not be loaded yet */ }
}

function _buildKpiBar(summary, samples, did, sid, rateSamples, snmpUnit) {
  const _isPing = S.sensors[`${did}/${sid}`]?.stype === 'ping';
  const _setKpi = (id, label, val, cls) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = `${label}<br><span>${val}</span>`;
    el.className = 'dm-kpi-item' + (cls ? ' ' + cls : '');
  };
  // Availability + loss always from summary
  let totalOk=0, totalFail=0, lossSum=0, jitterSum=0;
  for (const r of summary) {
    totalOk+=r.ok; totalFail+=r.fail;
    lossSum+=r.loss_pct||0; jitterSum+=r.jitter_ms||0;
  }
  const avail    = (totalOk+totalFail)>0 ? totalOk/(totalOk+totalFail)*100 : 100;
  const avgLoss  = summary.length ? lossSum/summary.length : 0;
  const avgJitt  = summary.length ? jitterSum/summary.length : 0;
  _setKpi(`kpi-avail-${did}-${sid}`, 'Avail', avail.toFixed(1)+'%',
    avail<80?'dm-kpi-crit':avail<95?'dm-kpi-warn':avail===100?'dm-kpi-good':'');
  if (_isPing) _setKpi(`kpi-loss-${did}-${sid}`, 'Loss %', avgLoss.toFixed(1)+'%',
    avgLoss>=20?'dm-kpi-crit':avgLoss>=5?'dm-kpi-warn':avgLoss===0?'dm-kpi-good':'');

  const _isCounter = Array.isArray(rateSamples) && rateSamples.length > 0;
  if (_isCounter) {
    const _u   = snmpUnit;
    const _lbl = _u==='bytes'?'Mbps':_u==='errors'?'err/s':_u==='packets'?'pkt/s':'/s';
    const okR  = rateSamples.filter(r=>r.ok&&r.rate!=null);
    const avgR = okR.length ? okR.reduce((a,b)=>a+b.rate,0)/okR.length : null;
    // v0.9.7: use per-bucket min/max (peak-preserving at rollup tier).
    // At raw tier, r.min = r.max = r.rate so this degrades to Math.min/max of rates.
    const minR = okR.length ? Math.min(...okR.map(r => r.min != null ? r.min : r.rate)) : null;
    const maxR = okR.length ? Math.max(...okR.map(r => r.max != null ? r.max : r.rate)) : null;
    const _fr  = v => v!=null ? _fmtRateDisplay(v,_u) : '—';
    _setKpi(`kpi-avg-${did}-${sid}`, 'Avg '+_lbl, _fr(avgR));
    _setKpi(`kpi-min-${did}-${sid}`, 'Min '+_lbl, _fr(minR));
    _setKpi(`kpi-max-${did}-${sid}`, 'Max '+_lbl, _fr(maxR));
    _setKpi(`kpi-jitter-${did}-${sid}`, 'Jitter', '—', 'dm-kpi-info');
    // Push into overview dmv- stats
    const _sk2=`${did}/${sid}`;
    const _so=S.sensors[_sk2]; if(_so){_so._ov_avg=_fr(avgR);_so._ov_min=_fr(minR);_so._ov_max=_fr(maxR);}
    ['avg','min','max'].forEach((k,i)=>{const el=document.getElementById(`dmv-${did}-${sid}-${k}`);if(el)el.textContent=[_fr(avgR),_fr(minR),_fr(maxR)][i];});
    return;
  }
  // v0.9.7: typed SNMP KPI bars — enum_state / gauge_numeric / time_duration / text.
  const _snmpCat = _snmpCategoryFor(did, sid);
  if (_snmpCat && _snmpCat !== 'counter_rate') {
    const _snmpSen = S.sensors[`${did}/${sid}`];
    const gvs = _gaugeValSamples(samples);
    const okG = gvs.filter(g => g.ok && g.v != null);
    if (_snmpCat === 'enum_state') {
      const legend = _effectiveEnumLegend(_snmpSen);
      const lastG = [...okG].reverse()[0];
      const lastCode = lastG ? String(Math.round(lastG.v)) : null;
      const lastLbl  = lastCode != null ? (legend[lastCode] || ('state ' + lastCode)) : '—';
      // Primary state = legend key "1" if defined (RFC convention), else first key.
      const primaryCode = legend['1'] ? '1' : Object.keys(legend)[0] || '1';
      const primaryLbl  = legend[primaryCode] || ('state ' + primaryCode);
      const inPrimary = okG.filter(g => String(Math.round(g.v)) === primaryCode).length;
      const pct = okG.length ? (inPrimary / okG.length * 100) : 0;
      const {count: transitions, lastChangeTs} = _enumTransitions(gvs);
      const ageSec = lastChangeTs ? (Date.now()/1000 - lastChangeTs) : (gvs.length ? (Date.now()/1000 - gvs[0].ts) : null);
      const isPrimary = lastCode === primaryCode;
      _setKpi(`kpi-avg-${did}-${sid}`, 'Current', lastLbl, isPrimary ? 'dm-kpi-good' : 'dm-kpi-crit');
      _setKpi(`kpi-min-${did}-${sid}`, '% '+primaryLbl, pct.toFixed(1)+'%',
        pct<80?'dm-kpi-crit':pct<99?'dm-kpi-warn':'dm-kpi-good');
      _setKpi(`kpi-max-${did}-${sid}`, 'Transitions', String(transitions),
        transitions>10?'dm-kpi-warn':transitions>0?'dm-kpi-info':'dm-kpi-good');
      _setKpi(`kpi-jitter-${did}-${sid}`, 'Age', _fmtDurationSec(ageSec), 'dm-kpi-info');
      // Push current label into dmv- overview tiles.
      const _so = S.sensors[`${did}/${sid}`];
      if (_so) { _so._ov_avg = lastLbl; _so._ov_min = pct.toFixed(1)+'%'; _so._ov_max = String(transitions); }
      ['avg','min','max'].forEach((k,i)=>{
        const el=document.getElementById(`dmv-${did}-${sid}-${k}`);
        if(el)el.textContent=[lastLbl, pct.toFixed(1)+'%', String(transitions)][i];
      });
      return;
    }
    if (_snmpCat === 'gauge_numeric') {
      const u = _snmpSen.snmp_unit;
      const lastG = [...okG].reverse()[0];
      const avg = okG.length ? okG.reduce((a,g)=>a+g.v,0)/okG.length : null;
      // Use per-bucket min/max when present (peak-preserving at rollup tier).
      const minV = okG.length ? Math.min(...okG.map(g => g.vMin != null ? g.vMin : g.v)) : null;
      const maxV = okG.length ? Math.max(...okG.map(g => g.vMax != null ? g.vMax : g.v)) : null;
      const f = v => _fmtGaugeValue(v, u);
      _setKpi(`kpi-avg-${did}-${sid}`, 'Avg', f(avg));
      _setKpi(`kpi-min-${did}-${sid}`, 'Min', f(minV));
      _setKpi(`kpi-max-${did}-${sid}`, 'Max', f(maxV));
      _setKpi(`kpi-jitter-${did}-${sid}`, 'Last', lastG ? f(lastG.v) : '—', 'dm-kpi-info');
      const _so = S.sensors[`${did}/${sid}`];
      if (_so) { _so._ov_avg=f(avg); _so._ov_min=f(minV); _so._ov_max=f(maxV); }
      ['avg','min','max'].forEach((k,i)=>{
        const el=document.getElementById(`dmv-${did}-${sid}-${k}`);
        if(el)el.textContent=[f(avg),f(minV),f(maxV)][i];
      });
      return;
    }
    if (_snmpCat === 'time_duration') {
      // TimeTicks: value is in 1/100 seconds.  Detect reboots from value drops.
      const lastG = [...okG].reverse()[0];
      const curUptime = lastG ? _fmtDurationSec(lastG.v / 100) : '—';
      let reboots = 0, prev = null;
      for (const g of okG) { if (prev != null && g.v < prev) reboots++; prev = g.v; }
      _setKpi(`kpi-avg-${did}-${sid}`, 'Uptime', curUptime, reboots>0?'dm-kpi-warn':'dm-kpi-good');
      _setKpi(`kpi-min-${did}-${sid}`, 'Reboots', String(reboots), reboots>0?'dm-kpi-warn':'dm-kpi-good');
      _setKpi(`kpi-max-${did}-${sid}`, 'Samples', String(okG.length), 'dm-kpi-info');
      _setKpi(`kpi-jitter-${did}-${sid}`, '—', '—', 'dm-kpi-info');
      return;
    }
    if (_snmpCat === 'text') {
      // Count distinct non-null string values.
      const strSamples = samples.filter(p => p.ok && p.value != null).map(p => String(p.value));
      const lastStr = strSamples.length ? strSamples[strSamples.length-1] : '—';
      let changes = 0, prevStr = null;
      for (const s of strSamples) { if (prevStr != null && s !== prevStr) changes++; prevStr = s; }
      const disp = lastStr.length > 48 ? lastStr.slice(0,45)+'…' : lastStr;
      _setKpi(`kpi-avg-${did}-${sid}`, 'Current', disp, 'dm-kpi-info');
      _setKpi(`kpi-min-${did}-${sid}`, 'Changes', String(changes), changes>0?'dm-kpi-warn':'dm-kpi-good');
      _setKpi(`kpi-max-${did}-${sid}`, 'Samples', String(strSamples.length), 'dm-kpi-info');
      _setKpi(`kpi-jitter-${did}-${sid}`, '—', '—', 'dm-kpi-info');
      return;
    }
  }
  // VMware metric-value KPIs
  const _vmU = _vmUnit(did, sid);
  if (_vmU !== null) {
    if (!samples.length) return;
    const _lbl2 = _vmUnitLabel(_vmU);
    const vmVals = samples.filter(p => p.ok && p.ms != null).map(p => p.ms);
    const _av2 = vmVals.length ? vmVals.reduce((a,b)=>a+b,0)/vmVals.length : null;
    const _mnV = vmVals.length ? Math.min(...vmVals) : null;
    const _mxV = vmVals.length ? Math.max(...vmVals) : null;
    _setKpi(`kpi-avg-${did}-${sid}`,'Avg '+_lbl2,_fmtVmVal(_av2,_vmU));
    _setKpi(`kpi-min-${did}-${sid}`,'Min '+_lbl2,_mnV!=null?_fmtVmVal(_mnV,_vmU):'—');
    _setKpi(`kpi-max-${did}-${sid}`,'Max '+_lbl2,_mxV!=null?_fmtVmVal(_mxV,_vmU):'—');
    _setKpi(`kpi-jitter-${did}-${sid}`,'Jitter',_fmtVmVal(avgJitt,_vmU),'dm-kpi-info');
    return;
  }
  // ms-based KPIs — compute from samples (matches stats bar, reflects selected time range)
  const msVals = samples.filter(p => p.ok && p.ms != null).map(p => p.ms);
  if (!msVals.length) return;
  const avg = Math.round(msVals.reduce((a,b)=>a+b,0)/msVals.length*10)/10;
  const minMs = Math.round(Math.min(...msVals)*10)/10;
  const maxMs = Math.round(Math.max(...msVals)*10)/10;
  _setKpi(`kpi-avg-${did}-${sid}`, 'Avg ms', avg+'ms');
  _setKpi(`kpi-min-${did}-${sid}`, 'Min ms', minMs+'ms');
  _setKpi(`kpi-max-${did}-${sid}`, 'Max ms', maxMs+'ms');
  _setKpi(`kpi-jitter-${did}-${sid}`, 'Jitter', avgJitt.toFixed(1)+'ms', 'dm-kpi-info');
}

function _setupHistTooltip(canvas, summary, did, sid, minutes, rateSamples, snmpUnit) {
  const tip = document.getElementById(`tip-${did}-${sid}`);
  if (!tip || !canvas) return;
  if (canvas._tipAC) canvas._tipAC.abort();
  const ac = new AbortController();
  canvas._tipAC = ac;
  const { signal } = ac;
  const LEFT = 52, RIGHT = 48, BOT = 28, TOP = 12;
  const _isCounter = Array.isArray(rateSamples) && rateSamples.length > 0;
  const _vmU3 = _vmUnit(did, sid);
  const _isVmware3 = _vmU3 !== null;

  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const mx = (e.clientX - rect.left) * scaleX;
    const plotW = canvas.width - LEFT - RIGHT;
    if (mx < LEFT || mx > canvas.width - RIGHT) { tip.classList.remove('tip-visible'); return; }
    const _cRef = _histCache[`${did}/${sid}`];
    const _winRef = _cRef?.windowStart ?? (Date.now() / 1000 - minutes * 60);
    const hoverTs = _winRef + (mx - LEFT) / plotW * (minutes * 60);
    let nearest = null, bestDist = Infinity;
    for (const r of summary) {
      const d = Math.abs(r.ts + 1800 - hoverTs);
      if (d < bestDist) { bestDist = d; nearest = r; }
    }
    if (!nearest && !_isCounter) { tip.classList.remove('tip-visible'); return; }
    // Use actual cursor timestamp (hoverTs), not the hourly bucket boundary
    const _hd = new Date(hoverTs * 1000);
    const _datePart = _hd.toLocaleDateString([], {month:'short', day:'numeric'});
    const _timePart = minutes <= 60
      ? _hd.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false})
      : _hd.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', hour12:false});
    const lbl = _datePart + ' ' + _timePart;
    const isDown = nearest ? (nearest.ok === 0 && nearest.fail > 0) : false;
    const statusColor = isDown ? 'var(--down)' : 'var(--up)';
    const statusText  = isDown ? '● DOWN' : '● UP';
    const lossColor   = (nearest?.loss_pct || 0) > 5 ? 'var(--warn)' : 'var(--text)';
    const _hdrStyle = `font-size:.82rem;font-weight:600;color:var(--text);margin-bottom:7px;padding-bottom:6px;border-bottom:1px solid var(--border)`;
    const _valStyle = `font-size:1.1rem;font-weight:700;color:var(--accent);text-align:center;margin-bottom:8px;font-variant-numeric:tabular-nums`;
    const _ftStyle  = `margin-top:7px;padding-top:5px;border-top:1px solid var(--border);font-size:.76rem`;
    if (_isCounter) {
      tip.innerHTML =
        `<div style="${_hdrStyle}">${lbl}</div>` +
        `<div style="${_valStyle}" class="tip-exact">—</div>` +
        `<table style="border-collapse:collapse;font-size:.76rem">` +
        `<tr><td style="color:var(--text3);padding-right:18px;padding-bottom:3px">Checks</td>` +
        `<td style="color:var(--text);text-align:right">${nearest?.ok||0}↑ ${nearest?.fail||0}↓</td></tr>` +
        `<tr><td style="color:var(--text3)">Loss</td>` +
        `<td style="color:${lossColor};text-align:right">${(nearest?.loss_pct||0).toFixed(1)}%</td></tr>` +
        `</table>` +
        `<div style="${_ftStyle};color:${statusColor}">${statusText}</div>`;
    } else if (_isVmware3) {
      tip.innerHTML =
        `<div style="${_hdrStyle}">${lbl}</div>` +
        `<div style="${_valStyle}" class="tip-exact">—</div>` +
        `<table style="border-collapse:collapse;font-size:.76rem">` +
        `<tr><td style="color:var(--text3);padding-right:18px;padding-bottom:3px">Avg</td>` +
        `<td style="color:var(--text);text-align:right;font-variant-numeric:tabular-nums">` +
        `${nearest.avg_ms != null ? _fmtVmVal(nearest.avg_ms, _vmU3) : '—'}</td></tr>` +
        `<tr><td style="color:var(--text3);padding-bottom:3px">Min</td>` +
        `<td style="color:var(--text);text-align:right;font-variant-numeric:tabular-nums">` +
        `${nearest.min_ms != null ? _fmtVmVal(nearest.min_ms, _vmU3) : '—'}</td></tr>` +
        `<tr><td style="color:var(--text3);padding-bottom:3px">Max</td>` +
        `<td style="color:var(--text);text-align:right;font-variant-numeric:tabular-nums">` +
        `${nearest.max_ms != null ? _fmtVmVal(nearest.max_ms, _vmU3) : '—'}</td></tr>` +
        `<tr><td style="color:var(--text3)">Loss</td>` +
        `<td style="color:${lossColor};text-align:right">${(nearest.loss_pct || 0).toFixed(1)}%</td></tr>` +
        `</table>` +
        `<div style="${_ftStyle};color:${statusColor}">${statusText}</div>`;
    } else {
      tip.innerHTML =
        `<div style="${_hdrStyle}">${lbl}</div>` +
        `<div style="${_valStyle}" class="tip-exact">— ms</div>` +
        `<table style="border-collapse:collapse;font-size:.76rem">` +
        `<tr><td style="color:var(--text3);padding-right:18px;padding-bottom:3px">Avg</td>` +
        `<td style="color:var(--text);text-align:right;font-variant-numeric:tabular-nums">` +
        `${nearest.avg_ms != null ? nearest.avg_ms + ' ms' : '—'}</td></tr>` +
        `<tr><td style="color:var(--text3);padding-bottom:3px">Min</td>` +
        `<td style="color:var(--text);text-align:right;font-variant-numeric:tabular-nums">` +
        `${nearest.min_ms != null ? nearest.min_ms + ' ms' : '—'}</td></tr>` +
        `<tr><td style="color:var(--text3);padding-bottom:3px">Max</td>` +
        `<td style="color:var(--text);text-align:right;font-variant-numeric:tabular-nums">` +
        `${nearest.max_ms != null ? nearest.max_ms + ' ms' : '—'}</td></tr>` +
        `<tr><td style="color:var(--text3);padding-bottom:3px">Loss</td>` +
        `<td style="color:${lossColor};text-align:right">${(nearest.loss_pct || 0).toFixed(1)}%</td></tr>` +
        `<tr><td style="color:var(--text3)">Jitter</td>` +
        `<td style="color:rgba(188,130,255,.9);text-align:right">${(nearest.jitter_ms || 0).toFixed(1)} ms</td></tr>` +
        `</table>` +
        `<div style="${_ftStyle};color:${statusColor}">${statusText}</div>`;
    }
    const cssMx = e.clientX - rect.left;
    let tx = cssMx + 12, ty = e.clientY - rect.top - 70;
    if (tx + 175 > rect.width) tx = cssMx - 180;
    if (ty < 4) ty = 4;
    tip.style.left = tx + 'px';
    tip.style.top  = ty + 'px';
    tip.classList.add('tip-visible');
    // Redraw chart then overdraw crosshair + highlight point
    dmHistRedraw(did, sid);
    const cctx = canvas.getContext('2d');
    cctx.strokeStyle = `rgba(${_SCC.text.join(',')},.22)`;
    cctx.lineWidth = 1;
    cctx.setLineDash([3, 3]);
    cctx.beginPath(); cctx.moveTo(mx, TOP); cctx.lineTo(mx, canvas.height - BOT); cctx.stroke();
    cctx.setLineDash([]);
    // Dot position: use maxY from cache (set by _drawHistCanvas)
    const cache = _histCache[`${did}/${sid}`];
    if (cache) {
      const _maxY = cache.maxY ?? 10;
      const _plotH = canvas.height - BOT - TOP;
      const _yOf = v => Math.max(TOP, (canvas.height - BOT) - (Math.min(v, _maxY) / _maxY) * _plotH);
      const TARGET = 300;
      const _winStart = cache.windowStart ?? (Date.now() / 1000 - minutes * 60);
      const _bucketSec = cache.bucketSec ?? (minutes * 60) / TARGET;
      const _plotW2 = canvas.width - LEFT - RIGHT;
      const _xOf2 = ts => LEFT + (ts - _winStart) / (minutes * 60) * _plotW2;
      const _cursorBi = Math.min(TARGET - 1, Math.max(0, Math.floor((hoverTs - _winStart) / _bucketSec)));

      // Look up the drawn avg line's canvas-Y at this pixel X
      const _lineMap = cache.avgLineY || {};
      const _rpx = Math.round(mx);
      let dotCanvasY = _lineMap[_rpx] ?? _lineMap[_rpx - 1] ?? _lineMap[_rpx + 1] ?? null;

      // Convert canvas-Y back to data value for the tooltip
      let dotVal = null;
      if (dotCanvasY != null) {
        const _plotH2 = canvas.height - BOT - TOP;
        const _maxY2 = cache.maxY || 1;
        dotVal = Math.max(0, ((canvas.height - BOT) - dotCanvasY) / _plotH2 * _maxY2);
      }

      // Exact value tooltip
      const exactEl = tip.querySelector('.tip-exact');
      if (exactEl) {
        if (_isCounter) exactEl.textContent = dotVal!=null ? _fmtRateThrLabel(dotVal, snmpUnit) : '—';
        else if (_isVmware3) exactEl.textContent = dotVal!=null ? _fmtVmVal(dotVal, _vmU3) : '—';
        else exactEl.textContent = dotVal!=null ? Math.round(dotVal)+' ms' : '— ms';
      }

      // Draw dot exactly on the drawn line
      if (dotCanvasY != null) {
        cctx.beginPath(); cctx.arc(mx, dotCanvasY, 5, 0, Math.PI * 2);
        cctx.fillStyle = `rgb(${_SCC.accent.join(',')})`; cctx.fill();
        cctx.strokeStyle = `rgba(${_SCC.text.join(',')},.9)`; cctx.lineWidth = 1.5; cctx.stroke();
      }
    }
  }, { signal });
  canvas.addEventListener('mouseleave', () => {
    tip.classList.remove('tip-visible');
    dmHistRedraw(did, sid);
  }, { signal });
}

function _drawHistCanvas(canvas, statsEl, did, sid, summary, samples, minutes, windowStart, rateSamples, snmpUnit) {
  if (!canvas) return;
  const _isPing = S.sensors[`${did}/${sid}`]?.stype === 'ping';
  canvas.width = canvas.offsetWidth || 660;
  const W = canvas.width, H = canvas.height || 320;
  const LEFT = 52, RIGHT = 48, BOT = 28, TOP = 12;
  const plotW = W - LEFT - RIGHT;
  const plotH = H - BOT - TOP;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  // ── Background ────────────────────────────────────────────────
  const _txt = _SCC.text.join(',');
  const _acc = _SCC.accent.join(',');
  const _dn  = _SCC.down.join(',');
  const _wn  = _SCC.warn.join(',');
  const _upC = _SCC.up.join(',');
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, `rgb(${_SCC.bg2.join(',')})`);
  bgGrad.addColorStop(1, `rgb(${_SCC.bg.join(',')})`);
  ctx.fillStyle = bgGrad; ctx.fillRect(0, 0, W, H);

  if (!samples.length && !summary.length) {
    ctx.fillStyle = `rgba(${_txt},.6)`; ctx.font = '13px Inter,sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('No data for this period', W / 2, H / 2);
    if (statsEl) statsEl.textContent = 'No data';
    return;
  }

  // v0.9.7: typed SNMP rendering.  Counter-rate stays on the main render
  // path (preserves v0.9.6/v0.9.7 behavior).  Enum / gauge / time / text
  // sensors render via a dedicated pipeline so their chart shows the value
  // signal (not probe latency) with category-appropriate axes.
  const _rateActive = Array.isArray(rateSamples) && rateSamples.length > 0;
  const _snmpCat2 = _snmpCategoryFor(did, sid);
  if (_snmpCat2 && _snmpCat2 !== 'counter_rate' && !_rateActive) {
    _drawTypedSnmpChart(ctx, {W, H, LEFT, RIGHT, TOP, BOT, plotW, plotH,
      did, sid, samples, summary, minutes,
      windowStart: windowStart ?? (Date.now() / 1000 - minutes * 60),
      category: _snmpCat2, statsEl,
      _txt, _acc, _dn, _wn, _upC});
    return;
  }

  // Read toggle states
  const togAvg    = document.getElementById(`tog-avg-${did}-${sid}`)?.checked ?? true;
  const togBand   = document.getElementById(`tog-band-${did}-${sid}`)?.checked ?? true;
  const togLoss   = _isPing && (document.getElementById(`tog-loss-${did}-${sid}`)?.checked ?? true);
  const togJitter = document.getElementById(`tog-jitter-${did}-${sid}`)?.checked ?? false;

  // Use fixed windowStart from cache (set at fetch time) so redraws don't shift the chart
  const _ws = windowStart ?? (Date.now() / 1000 - minutes * 60);
  const tsRange = minutes * 60;
  const windowEnd = _ws + tsRange;
  const xOf = ts => LEFT + (ts - _ws) / tsRange * plotW;

  // ── Y-axis scaling ────────────────────────────────────────────────────────
  const _sen = S.sensors[`${did}/${sid}`];
  const _isCounter = Array.isArray(rateSamples) && rateSamples.length > 0;
  const _vmU2 = _vmUnit(did, sid);
  const _isVmware = _vmU2 !== null;
  let msVals, rawMax, maxY, yOf;
  if (_isCounter) {
    const okR = rateSamples.filter(r => r.ok && r.rate != null);
    // v0.9.7: include per-bucket max in scaling so peaks aren't clipped when
    // the Min/Max envelope is drawn. Falls back to r.rate when r.max is not
    // distinct from r.rate (raw tier, or pre-v0.9.7 rollup rows).
    const dispVals = okR.map(r => _rateToDisplayUnits(
      (r.max != null && r.max > r.rate) ? r.max : r.rate, snmpUnit));
    const sortedD = [...dispVals].sort((a, b) => a - b);
    const rP95 = sortedD.length ? (sortedD[Math.floor(sortedD.length * 0.95)] ?? sortedD[sortedD.length - 1]) : 0;
    maxY = Math.max(rP95 * 1.4, (_sen?.warn_ms || 0) * 1.2, 0.1);
    yOf = v => Math.max(TOP, (H - BOT) - (Math.min(v, maxY) / maxY) * plotH);
    msVals = []; rawMax = 0;
  } else {
    msVals = samples.filter(p => p.ok && p.ms != null).map(p => p.ms);
    const summaryAvgMax = summary.reduce((m, r) => Math.max(m, r.avg_ms || 0), 0);
    const sortedMs = [...msVals].sort((a, b) => a - b);
    const p95 = sortedMs.length ? (sortedMs[Math.floor(sortedMs.length * 0.95)] ?? sortedMs[sortedMs.length - 1]) : 0;
    rawMax = sortedMs.length ? sortedMs[sortedMs.length - 1] : 0;
    maxY = Math.max(Math.max(summaryAvgMax, p95) * 1.4, (_sen?.warn_ms || 0) * 1.2, 10);
    yOf = ms => Math.max(TOP, (H - BOT) - (Math.min(ms, maxY) / maxY) * plotH);
  }
  // Store maxY in cache for tooltip dot positioning
  if (_histCache[`${did}/${sid}`]) _histCache[`${did}/${sid}`].maxY = maxY;
  const yLoss = pct => (H - BOT) - (pct / 100) * plotH;

  // ── 1. Grid — horizontal lines + Y labels (drawn FIRST, behind all data) ──
  ctx.lineWidth = 1;
  ctx.font = 'bold 11px Inter,sans-serif';
  [0.2, 0.4, 0.6, 0.8, 1.0].forEach(f => {
    const y = (H - BOT) - f * plotH;
    ctx.strokeStyle = `rgba(${_txt},.08)`;
    ctx.beginPath(); ctx.moveTo(LEFT, y); ctx.lineTo(W - RIGHT, y); ctx.stroke();
    ctx.fillStyle = `rgba(${_txt},.78)`; ctx.textAlign = 'right';
    const _yLbl = _isCounter
      ? _fmtRateYLabel(maxY * f, snmpUnit)
      : _isVmware ? _fmtVmYLabel(maxY * f, _vmU2)
      : (Math.round(maxY * f) >= 1000 ? (Math.round(maxY * f) / 1000).toFixed(1) + 's' : Math.round(maxY * f) + 'ms');
    ctx.fillText(_yLbl, LEFT - 4, y + 4);
    if (togLoss && !_isCounter) {
      ctx.fillStyle = `rgba(${_wn},.9)`; ctx.textAlign = 'left';
      ctx.fillText(Math.round(100 * f) + '%', W - RIGHT + 4, y + 4);
    }
  });
  ctx.fillStyle = `rgba(${_txt},.5)`; ctx.textAlign = 'right';
  ctx.fillText('0', LEFT - 4, H - BOT + 4);
  // Y/X axis border lines
  ctx.strokeStyle = `rgba(${_txt},.12)`; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(LEFT, TOP); ctx.lineTo(LEFT, H - BOT); ctx.stroke();
  if (togLoss && !_isCounter) {
    ctx.strokeStyle = `rgba(${_wn},.2)`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(W - RIGHT, TOP); ctx.lineTo(W - RIGHT, H - BOT); ctx.stroke();
  }

  // ── 2. X-axis time grid + labels (drawn FIRST) ────────────────
  let _gInt;
  if      (tsRange <=  2*3600)   _gInt =  15*60;
  else if (tsRange <=  6*3600)   _gInt =   3600;
  else if (tsRange <= 24*3600)   _gInt =  4*3600;
  else if (tsRange <=  4*86400)  _gInt = 12*3600;
  else if (tsRange <= 14*86400)  _gInt =  86400;
  else if (tsRange <= 45*86400)  _gInt =  7*86400;
  else if (tsRange <= 200*86400) _gInt = 30*86400;
  else                           _gInt = 91*86400;
  const _tzOff = new Date().getTimezoneOffset() * 60;
  const _firstGrid = Math.ceil((_ws + _tzOff) / _gInt) * _gInt - _tzOff;
  for (let ts = _firstGrid; ts < windowEnd; ts += _gInt) {
    const x = xOf(ts);
    if (x < LEFT + 14) continue;
    ctx.strokeStyle = `rgba(${_txt},.06)`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, TOP); ctx.lineTo(x, H - BOT); ctx.stroke();
    const d = new Date(ts * 1000);
    const lbl = _gInt < 86400
      ? d.toLocaleDateString([], {month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})
      : d.toLocaleDateString([], {month:'short',day:'numeric'});
    ctx.fillStyle = `rgba(${_txt},.72)`; ctx.font = '11px Inter,sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(lbl, x, H - 3);
  }

  // ── 3. Downtime spans ─────────────────────────────────────────
  ctx.fillStyle = `rgba(${_dn},.10)`;
  for (const r of summary) {
    if (r.ok === 0 && r.fail > 0) {
      const x1 = Math.max(LEFT, xOf(r.ts));
      const x2 = Math.min(W - RIGHT, xOf(r.ts + 3600));
      if (x2 > x1) {
        ctx.fillRect(x1, TOP, x2 - x1, plotH);
        ctx.strokeStyle = `rgba(${_dn},.35)`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x1, TOP + 1); ctx.lineTo(x2, TOP + 1); ctx.stroke();
      }
    }
  }

  // ── 4. Loss% bars ─────────────────────────────────────────────
  if (togLoss && summary.length) {
    const barW = Math.max(2, plotW / summary.length * 0.6);
    for (const r of summary) {
      if ((r.loss_pct || 0) === 0) continue;
      const x = xOf(r.ts + 1800) - barW / 2;
      const yTop = yLoss(r.loss_pct);
      ctx.fillStyle = `rgba(${_wn},.4)`;
      ctx.shadowColor = `rgba(${_wn},.35)`;
      ctx.shadowBlur = 4;
      if (ctx.roundRect) {
        ctx.beginPath();
        ctx.roundRect(x, yTop, barW, (H - BOT) - yTop, [2, 2, 0, 0]);
        ctx.fill();
      } else {
        ctx.fillRect(x, yTop, barW, (H - BOT) - yTop);
      }
      ctx.shadowBlur = 0;
    }
  }

  // ── 5. Min/Max lines (secondary — 50% opacity, thinner) ───────
  if (togBand && !_isCounter) {
    const bandPts = summary.filter(r => r.min_ms != null && r.max_ms != null);
    if (bandPts.length > 1) {
      ctx.globalAlpha = 0.5;
      // Max line — pink
      ctx.beginPath();
      bandPts.forEach((r, i) => {
        const x = xOf(r.ts + 1800);
        i === 0 ? ctx.moveTo(x, yOf(r.max_ms)) : ctx.lineTo(x, yOf(r.max_ms));
      });
      ctx.strokeStyle = 'rgba(244,114,182,1)'; ctx.lineWidth = 1; ctx.stroke();
      // Min line — teal/green
      ctx.beginPath();
      bandPts.forEach((r, i) => {
        const x = xOf(r.ts + 1800);
        i === 0 ? ctx.moveTo(x, yOf(r.min_ms)) : ctx.lineTo(x, yOf(r.min_ms));
      });
      ctx.strokeStyle = `rgb(${_upC})`; ctx.lineWidth = 1; ctx.stroke();
      ctx.globalAlpha = 1.0;
    }
  }
  // v0.9.7: Min/Max envelope for counter-rate sensors.  At raw tier
  // min = max = rate so the band collapses to the avg line; at rollup tier
  // the band shows peak probe rates within each bucket (e.g. a 30-second
  // burst that would otherwise be smoothed away at 3d+ zoom).
  if (togBand && _isCounter) {
    const bandPts = rateSamples.filter(r => r.ok && r.min != null && r.max != null
                                              && (r.min !== r.max));
    if (bandPts.length > 1) {
      ctx.globalAlpha = 0.5;
      // Max — pink
      ctx.beginPath();
      bandPts.forEach((r, i) => {
        const x = xOf(r.ts), y = yOf(_rateToDisplayUnits(r.max, snmpUnit));
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.strokeStyle = 'rgba(244,114,182,1)'; ctx.lineWidth = 1; ctx.stroke();
      // Min — teal/green
      ctx.beginPath();
      bandPts.forEach((r, i) => {
        const x = xOf(r.ts), y = yOf(_rateToDisplayUnits(r.min, snmpUnit));
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      });
      ctx.strokeStyle = `rgb(${_upC})`; ctx.lineWidth = 1; ctx.stroke();
      ctx.globalAlpha = 1.0;
    }
  }

  // ── 6. Jitter line (dashed purple, 50% opacity) ───────────────
  if (togJitter && !_isCounter) {
    const jPts = summary.filter(r => (r.jitter_ms || 0) > 0);
    if (jPts.length > 1) {
      ctx.globalAlpha = 0.6;
      ctx.beginPath();
      jPts.forEach((r, i) => {
        const x = xOf(r.ts + 1800), y = yOf(r.jitter_ms);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = 'rgba(188,130,255,1)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1.0;
    }
  }

  // ── 7. Avg/Rate line — main focus ────────────────────────────
  if (togAvg) {
    // Pixel-X → data-value lookup for hover dot (filled by _drawLine)
    const _avgLineY = {};  // { pixelX: dataValue }

    const _drawLine = (pts, gapMult) => {
      if (pts.length < 2) return;
      const gapThresh = (tsRange / pts.length) * gapMult;
      ctx.lineWidth = 2;
      ctx.strokeStyle = `rgb(${_acc})`;
      ctx.shadowColor = `rgba(${_acc},.5)`;
      ctx.shadowBlur = 3;
      let segStart = 0;
      for (let i = 1; i <= pts.length; i++) {
        const isEnd = i === pts.length;
        const isGap = !isEnd && (pts[i].ts - pts[i-1].ts) > gapThresh;
        if (isEnd || isGap) {
          const seg = pts.slice(segStart, i);
          if (seg.length > 1) {
            ctx.beginPath();
            ctx.moveTo(seg[0].x, seg[0].y);
            // Sample the line for hover lookup: first point
            _avgLineY[Math.round(seg[0].x)] = seg[0].y;
            for (let j = 1; j < seg.length - 1; j++) {
              const cx = seg[j].x, cy = seg[j].y;
              const ex = (seg[j].x + seg[j+1].x) / 2, ey = (seg[j].y + seg[j+1].y) / 2;
              const sx = j === 1 ? seg[0].x : (seg[j-1].x + seg[j].x) / 2;
              const sy = j === 1 ? seg[0].y : (seg[j-1].y + seg[j].y) / 2;
              ctx.quadraticCurveTo(cx, cy, ex, ey);
              // Sample the quadratic curve at each pixel X
              const xMin = Math.floor(Math.min(sx, ex));
              const xMax = Math.ceil(Math.max(sx, ex));
              for (let px = xMin; px <= xMax; px++) {
                const xRange = ex - sx;
                if (Math.abs(xRange) < 0.5) { _avgLineY[px] = ey; continue; }
                // Approximate t from x (linear approximation is fine for per-pixel)
                const tApprox = Math.max(0, Math.min(1, (px - sx) / xRange));
                const u = 1 - tApprox;
                _avgLineY[px] = u * u * sy + 2 * u * tApprox * cy + tApprox * tApprox * ey;
              }
            }
            ctx.lineTo(seg[seg.length-1].x, seg[seg.length-1].y);
            _avgLineY[Math.round(seg[seg.length-1].x)] = seg[seg.length-1].y;
            ctx.stroke();
          }
          segStart = i;
        }
      }
      ctx.shadowBlur = 0;
    };

    const TARGET = 300;
    const _ckey = `${did}/${sid}`;
    if (_isCounter) {
      // Draw rate line from rateSamples
      const okR = rateSamples.filter(r => r.ok && r.rate != null);
      if (okR.length > 1) {
        let pts;
        if (okR.length <= TARGET) {
          pts = okR.map(r => ({ x: xOf(r.ts), y: yOf(_rateToDisplayUnits(r.rate, snmpUnit)), ts: r.ts }));
          if (_histCache[_ckey]) {
            _histCache[_ckey].sortedRateSamples = [...okR].sort((a, b) => a.ts - b.ts)
              .map(r => ({ ...r, displayRate: _rateToDisplayUnits(r.rate, snmpUnit) }));
            _histCache[_ckey].rateBuckets = null;
            _histCache[_ckey].buckets = null;
            _histCache[_ckey].bucketSec = null;
            _histCache[_ckey].sortedOkSamples = null;
          }
        } else {
          const bucketSec = tsRange / TARGET;
          const accR = Array.from({ length: TARGET }, () => ({ sum: 0, n: 0 }));
          for (const r of okR) {
            const bi = Math.min(TARGET - 1, Math.floor((r.ts - _ws) / bucketSec));
            if (bi >= 0) { accR[bi].sum += _rateToDisplayUnits(r.rate, snmpUnit); accR[bi].n++; }
          }
          if (_histCache[_ckey]) {
            _histCache[_ckey].rateBuckets = accR;
            _histCache[_ckey].buckets = null;
            _histCache[_ckey].bucketSec = bucketSec;
            _histCache[_ckey].sortedRateSamples = null;
            _histCache[_ckey].sortedOkSamples = null;
          }
          let firstBi = -1, lastBi = -1;
          for (let i = 0; i < TARGET; i++) {
            if (accR[i].n > 0) { if (firstBi < 0) firstBi = i; lastBi = i; }
          }
          pts = [];
          if (firstBi >= 0) {
            for (let i = 0; i <= lastBi; i++) {
              const ts = _ws + (i + 0.5) * bucketSec;
              const v = accR[i].n > 0 ? accR[i].sum / accR[i].n : 0;
              pts.push({ x: xOf(ts), y: yOf(v), ts });
            }
          }
        }
        _drawLine(pts, 4);
      }
    } else {
      // Draw ms line from raw samples
      // Bucket-average into at most 300 points — naturally adapts to every time frame:
      //   1h  (~120 raw)  → drawn as-is, full per-probe detail
      //   6h  (~720 raw)  → 300 buckets of ~72 s each, 2–3 samples averaged → shape preserved
      //   24h (~2880 raw) → 300 buckets of ~288 s, ~10 samples/bucket → smooth trend
      //   7d+ (≤10k raw)  → 300 buckets, 30+ samples/bucket → clean trend line
      const okSamples = samples.filter(p => p.ok && p.ms != null);
      if (okSamples.length > 1) {
        let pts;
        if (okSamples.length <= TARGET) {
          pts = okSamples.map(p => ({ x: xOf(p.ts), y: yOf(p.ms), ts: p.ts }));
          // Cache sorted raw samples for tooltip linear interpolation (computed once here)
          if (_histCache[_ckey]) {
            _histCache[_ckey].sortedOkSamples = [...okSamples].sort((a, b) => a.ts - b.ts);
            _histCache[_ckey].buckets = null;
            _histCache[_ckey].bucketSec = null;
          }
        } else {
          const bucketSec = tsRange / TARGET;
          const acc = Array.from({ length: TARGET }, () => ({ sum: 0, n: 0 }));
          for (const p of okSamples) {
            const bi = Math.min(TARGET - 1, Math.floor((p.ts - _ws) / bucketSec));
            if (bi >= 0) { acc[bi].sum += p.ms; acc[bi].n++; }
          }
          // Cache bucket data so tooltip can look up O(1) instead of recomputing O(n)
          if (_histCache[_ckey]) {
            _histCache[_ckey].buckets = acc;
            _histCache[_ckey].bucketSec = bucketSec;
            _histCache[_ckey].sortedOkSamples = null;
          }
          // Find range of buckets that have data
          let firstBi = -1, lastBi = -1;
          for (let i = 0; i < TARGET; i++) {
            if (acc[i].n > 0) { if (firstBi < 0) firstBi = i; lastBi = i; }
          }
          pts = [];
          if (firstBi >= 0) {
            for (let i = 0; i <= lastBi; i++) {
              const ts = _ws + (i + 0.5) * bucketSec;
              // Empty bucket = downtime → show 0ms baseline (covers gaps at start or middle)
              const ms = acc[i].n > 0 ? acc[i].sum / acc[i].n : 0;
              pts.push({ x: xOf(ts), y: yOf(ms), ts });
            }
          }
        }
        _drawLine(pts, 4);
      }
    }
    // Store pixel lookup for hover dot
    if (_histCache[_ckey]) _histCache[_ckey].avgLineY = _avgLineY;
  }

  // ── 8. Threshold lines ────────────────────────────────────────
  ctx.font = 'bold 11px Inter,sans-serif';
  if (_sen?.warn_ms > 0 && _sen.warn_ms <= maxY) {
    const wy = yOf(_sen.warn_ms);
    ctx.strokeStyle = `rgba(${_wn},.5)`; ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(LEFT, wy); ctx.lineTo(W - RIGHT, wy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = `rgba(${_wn},.85)`; ctx.textAlign = 'left';
    ctx.fillText(_isCounter ? 'warn '+_fmtRateThrLabel(_sen.warn_ms,snmpUnit) : _isVmware ? 'warn '+_fmtVmVal(_sen.warn_ms,_vmU2) : _sen?.stype==='tls' ? 'warn '+_sen.warn_ms+'d' : 'warn '+_sen.warn_ms+'ms', LEFT + 4, wy - 3);
  }
  if (_sen?.crit_ms > 0 && _sen.crit_ms <= maxY) {
    const cy = yOf(_sen.crit_ms);
    ctx.strokeStyle = `rgba(${_dn},.5)`; ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(LEFT, cy); ctx.lineTo(W - RIGHT, cy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = `rgba(${_dn},.85)`; ctx.textAlign = 'left';
    ctx.fillText(_isCounter ? 'crit '+_fmtRateThrLabel(_sen.crit_ms,snmpUnit) : _isVmware ? 'crit '+_fmtVmVal(_sen.crit_ms,_vmU2) : _sen?.stype==='tls' ? 'crit '+_sen.crit_ms+'d' : 'crit '+_sen.crit_ms+'ms', LEFT + 4, cy - 3);
  }

  // ── 8b. Anomaly baseline band (mean ± k·stddev) ───────────────
  // Shown only for supported latency-style sensors with a learned baseline.
  const _anomSupported = ['ping','tcp','http','dns','http_keyword','banner'];
  if (_sen?.anomaly_enabled && _anomSupported.includes(_sen.stype)
      && _sen.anomaly_mean_ms != null && _sen.anomaly_sample_count > 0
      && document.getElementById(`tog-baseline-${did}-${sid}`)?.checked !== false) {
    const _kByS = {1: 3.0, 2: 4.0, 3: 6.0};
    const _kA   = _kByS[_sen.anomaly_sensitivity || 2] || 4.0;
    const _µ    = Number(_sen.anomaly_mean_ms);
    const _σraw = Number(_sen.anomaly_stddev_ms || 0);
    const _σ    = Math.max(_σraw, 10, 0.2 * _µ);
    const _hi   = Math.min(maxY, _µ + _kA * _σ);
    const _lo   = Math.max(0, _µ - _kA * _σ);
    if (_µ >= 0 && _µ <= maxY) {
      const yHi = yOf(_hi), yLo = yOf(_lo), yMu = yOf(_µ);
      ctx.fillStyle = `rgba(${_txt},.06)`;
      ctx.fillRect(LEFT, yHi, (W - RIGHT) - LEFT, yLo - yHi);
      ctx.strokeStyle = `rgba(${_txt},.45)`; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(LEFT, yMu); ctx.lineTo(W - RIGHT, yMu); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = `rgba(${_txt},.75)`; ctx.font = '11px Inter,sans-serif'; ctx.textAlign = 'left';
      ctx.fillText(`baseline ${_µ.toFixed(1)}ms`, LEFT + 4, yMu - 3);
    }
  }

  // ── 9. Failed ticks (only for 1h — too dense at 6h+, downtime spans cover it) ──
  if (minutes <= 60) {
    ctx.strokeStyle = `rgba(${_dn},.55)`; ctx.lineWidth = 1;
    samples.filter(p => !p.ok).forEach(p => {
      const x = xOf(p.ts);
      ctx.beginPath(); ctx.moveTo(x, H - BOT); ctx.lineTo(x, H - BOT - 8); ctx.stroke();
    });
  }

  // ── Stats bar ─────────────────────────────────────────────────
  if (statsEl) {
    if (_isCounter) {
      const okR = rateSamples.filter(r => r.ok && r.rate != null);
      const total = rateSamples.length, upPct = total ? Math.round(okR.length / total * 1000) / 10 : 0;
      const avgD = okR.length ? _rateToDisplayUnits(okR.reduce((a, r) => a + r.rate, 0) / okR.length, snmpUnit) : null;
      // v0.9.7: use per-bucket min/max for peak-preserving stats at rollup tier.
      const minD = okR.length ? _rateToDisplayUnits(Math.min(...okR.map(r => r.min != null ? r.min : r.rate)), snmpUnit) : null;
      const maxD = okR.length ? _rateToDisplayUnits(Math.max(...okR.map(r => r.max != null ? r.max : r.rate)), snmpUnit) : null;
      const _f = v => v != null ? _fmtRateThrLabel(v, snmpUnit) : '—';
      statsEl.textContent = `${total} probes · ${upPct}% up · avg ${_f(avgD)} · min ${_f(minD)} · max ${_f(maxD)}`;
    } else if (_isVmware) {
      const total = samples.length, okCt = samples.filter(p => p.ok).length;
      const upPct = total ? Math.round(okCt / total * 1000) / 10 : 0;
      const avgV = msVals.length ? msVals.reduce((a,b) => a+b, 0) / msVals.length : null;
      const minV = msVals.length ? Math.min(...msVals) : null;
      statsEl.textContent =
        `${total} probes · ${upPct}% up · avg ${_fmtVmVal(avgV,_vmU2)} · min ${_fmtVmVal(minV,_vmU2)} · max ${_fmtVmVal(rawMax||null,_vmU2)}`;
    } else {
      const total = samples.length, okCt = samples.filter(p => p.ok).length;
      const upPct = total ? Math.round(okCt / total * 1000) / 10 : 0;
      const avgMs = msVals.length ? Math.round(msVals.reduce((a, b) => a + b, 0) / msVals.length) : null;
      const minMs = msVals.length ? Math.round(Math.min(...msVals)) : null;
      statsEl.textContent =
        `${total} probes · ${upPct}% up · avg ${avgMs != null ? avgMs + 'ms' : '—'} · min ${minMs != null ? minMs + 'ms' : '—'} · max ${Math.round(rawMax)}ms`;
    }
  }
}

// v0.9.7: typed SNMP chart renderer.  Handles enum_state (step chart),
// gauge_numeric (line chart with unit-aware axis), time_duration (uptime
// line with reboot markers), and text (last-value banner with change log).
function _drawTypedSnmpChart(ctx, P) {
  const {W, H, LEFT, RIGHT, TOP, BOT, plotW, plotH,
         did, sid, samples, summary, minutes, windowStart,
         category, statsEl, _txt, _acc, _dn, _wn, _upC} = P;
  const _sen = S.sensors[`${did}/${sid}`];
  const snmpUnit = _sen?.snmp_unit || '';
  const tsRange = minutes * 60;
  const windowEnd = windowStart + tsRange;
  const xOf = ts => LEFT + (ts - windowStart) / tsRange * plotW;

  const gvs = _gaugeValSamples(samples);
  const okG = gvs.filter(g => g.ok && g.v != null);

  // Text category — no chart, just the last value centered + change count.
  if (category === 'text') {
    const strSamples = samples.filter(p => p.ok && p.value != null).map(p => String(p.value));
    const lastStr = strSamples.length ? strSamples[strSamples.length - 1] : '';
    let changes = 0, prevStr = null;
    for (const s of strSamples) { if (prevStr != null && s !== prevStr) changes++; prevStr = s; }
    ctx.fillStyle = `rgba(${_txt},.72)`; ctx.textAlign = 'center';
    ctx.font = '13px Inter,sans-serif';
    ctx.fillText('LAST VALUE', W/2, TOP + 30);
    ctx.font = 'bold 18px Inter,sans-serif'; ctx.fillStyle = `rgb(${_acc})`;
    const disp = lastStr.length > 60 ? lastStr.slice(0, 57) + '…' : lastStr || '—';
    ctx.fillText(disp, W/2, TOP + 62);
    ctx.font = '12px Inter,sans-serif'; ctx.fillStyle = `rgba(${_txt},.6)`;
    ctx.fillText(`${changes} value change${changes === 1 ? '' : 's'} in window`, W/2, TOP + 92);
    if (statsEl) statsEl.textContent = `${strSamples.length} probes · ${changes} changes`;
    return;
  }

  // Y-axis scaling + formatter depending on category.
  let maxY, minY = 0, yFmt, yLabels = null;
  if (category === 'enum_state') {
    // Y-axis = state codes (integers).  Legend from snmp_unit maps codes → names.
    const legend = _sen ? _effectiveEnumLegend(_sen) : _parseEnumLegend(snmpUnit);
    const codes = Object.keys(legend).map(Number).sort((a,b) => a-b);
    if (codes.length) {
      minY = Math.max(0, codes[0] - 0.5);
      maxY = codes[codes.length-1] + 0.5;
    } else {
      const vs = okG.map(g => g.v);
      minY = vs.length ? Math.min(...vs) - 0.5 : 0;
      maxY = vs.length ? Math.max(...vs) + 0.5 : 1;
    }
    yLabels = codes.map(c => ({v: c, label: legend[String(c)] || ('state ' + c)}));
    yFmt = v => {
      const label = legend[String(Math.round(v))];
      return label || Math.round(v).toString();
    };
  } else if (category === 'time_duration') {
    const vs = okG.map(g => g.v / 100);  // TimeTicks → seconds
    minY = 0;
    maxY = vs.length ? Math.max(...vs) * 1.1 : 86400;
    yFmt = v => _fmtDurationSec(v);
  } else {
    // gauge_numeric
    const u = snmpUnit.toLowerCase();
    const vs = okG.map(g => g.v);
    if (u === '%' || u === 'percent') {
      minY = 0; maxY = 100;
    } else {
      const sorted = [...vs].sort((a,b) => a-b);
      const p95 = sorted.length ? (sorted[Math.floor(sorted.length * 0.95)] ?? sorted[sorted.length-1]) : 0;
      const minV = sorted.length ? sorted[0] : 0;
      minY = Math.min(0, minV);
      maxY = Math.max(p95 * 1.4, 1);
      if ((_sen?.warn_ms || 0) > maxY) maxY = _sen.warn_ms * 1.2;
    }
    yFmt = v => _fmtGaugeYLabel(v, snmpUnit);
  }
  const yOf = v => {
    const clamped = Math.max(minY, Math.min(maxY, v));
    return TOP + (1 - (clamped - minY) / (maxY - minY)) * plotH;
  };
  if (_histCache[`${did}/${sid}`]) {
    _histCache[`${did}/${sid}`].maxY = maxY;
    _histCache[`${did}/${sid}`].minY = minY;
  }

  // Grid + Y labels.  For enum, labels align to state codes; otherwise 20%/40%/.../100%.
  ctx.lineWidth = 1; ctx.font = 'bold 11px Inter,sans-serif';
  if (category === 'enum_state' && yLabels) {
    yLabels.forEach(({v, label}) => {
      const y = yOf(v);
      ctx.strokeStyle = `rgba(${_txt},.08)`;
      ctx.beginPath(); ctx.moveTo(LEFT, y); ctx.lineTo(W - RIGHT, y); ctx.stroke();
      ctx.fillStyle = `rgba(${_txt},.78)`; ctx.textAlign = 'right';
      ctx.fillText(label, LEFT - 4, y + 4);
    });
  } else {
    [0.2, 0.4, 0.6, 0.8, 1.0].forEach(f => {
      const y = TOP + (1 - f) * plotH;
      ctx.strokeStyle = `rgba(${_txt},.08)`;
      ctx.beginPath(); ctx.moveTo(LEFT, y); ctx.lineTo(W - RIGHT, y); ctx.stroke();
      ctx.fillStyle = `rgba(${_txt},.78)`; ctx.textAlign = 'right';
      ctx.fillText(yFmt(minY + f * (maxY - minY)), LEFT - 4, y + 4);
    });
    ctx.fillStyle = `rgba(${_txt},.5)`; ctx.textAlign = 'right';
    ctx.fillText(yFmt(minY), LEFT - 4, H - BOT + 4);
  }
  // Y-axis border
  ctx.strokeStyle = `rgba(${_txt},.12)`; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(LEFT, TOP); ctx.lineTo(LEFT, H - BOT); ctx.stroke();

  // X-axis grid lines + date labels (mirrors the main renderer).
  let _gInt;
  if      (tsRange <=  2*3600)   _gInt =  15*60;
  else if (tsRange <=  6*3600)   _gInt =   3600;
  else if (tsRange <= 24*3600)   _gInt =  4*3600;
  else if (tsRange <=  4*86400)  _gInt = 12*3600;
  else if (tsRange <= 14*86400)  _gInt =  86400;
  else if (tsRange <= 45*86400)  _gInt =  7*86400;
  else if (tsRange <= 200*86400) _gInt = 30*86400;
  else                           _gInt = 91*86400;
  const _tzOff = new Date().getTimezoneOffset() * 60;
  const _firstGrid = Math.ceil((windowStart + _tzOff) / _gInt) * _gInt - _tzOff;
  for (let ts = _firstGrid; ts < windowEnd; ts += _gInt) {
    const x = xOf(ts);
    if (x < LEFT + 14) continue;
    ctx.strokeStyle = `rgba(${_txt},.06)`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, TOP); ctx.lineTo(x, H - BOT); ctx.stroke();
    const d = new Date(ts * 1000);
    const lbl = _gInt < 86400
      ? d.toLocaleDateString([], {month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hour12:false})
      : d.toLocaleDateString([], {month:'short',day:'numeric'});
    ctx.fillStyle = `rgba(${_txt},.72)`; ctx.font = '11px Inter,sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(lbl, x, H - 3);
  }

  // Downtime spans (from summary).
  ctx.fillStyle = `rgba(${_dn},.10)`;
  for (const r of summary) {
    if (r.ok === 0 && r.fail > 0) {
      const x1 = Math.max(LEFT, xOf(r.ts));
      const x2 = Math.min(W - RIGHT, xOf(r.ts + 3600));
      if (x2 > x1) ctx.fillRect(x1, TOP, x2 - x1, plotH);
    }
  }

  // Data rendering per category.
  const sortedG = [...gvs].sort((a,b) => a.ts - b.ts);
  if (category === 'enum_state') {
    // Step chart: horizontal segment from each sample's ts to the next's ts,
    // colored by primary-state match (green) vs non-primary (red).  Draw as
    // thick strokes so brief visits are visible at zoomed-out ranges.
    const legend = _sen ? _effectiveEnumLegend(_sen) : _parseEnumLegend(snmpUnit);
    const primaryCode = legend['1'] ? '1' : Object.keys(legend)[0] || '1';
    ctx.lineWidth = 3;
    for (let i = 0; i < sortedG.length; i++) {
      const g = sortedG[i];
      if (!g.ok || g.v == null) continue;
      const isPrimary = String(Math.round(g.v)) === primaryCode;
      ctx.strokeStyle = isPrimary ? `rgb(${_upC})` : `rgb(${_dn})`;
      const x1 = xOf(g.ts);
      const nextTs = (i + 1 < sortedG.length) ? sortedG[i+1].ts : Math.min(windowEnd, g.ts + (tsRange / Math.max(sortedG.length, 1)));
      const x2 = xOf(nextTs);
      const y = yOf(g.v);
      ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x2, y); ctx.stroke();
      // Vertical transition marker
      if (i > 0) {
        const prev = sortedG.slice(0, i).reverse().find(p => p.ok && p.v != null);
        if (prev && Math.round(prev.v) !== Math.round(g.v)) {
          ctx.strokeStyle = `rgba(${_wn},.55)`; ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(x1, TOP); ctx.lineTo(x1, H - BOT); ctx.stroke();
          ctx.lineWidth = 3;
        }
      }
    }
    // Stats bar
    if (statsEl) {
      const total = gvs.length;
      const ok = okG.length;
      const inPrimary = okG.filter(g => String(Math.round(g.v)) === primaryCode).length;
      const pct = ok ? (inPrimary / ok * 100) : 0;
      const primaryLbl = legend[primaryCode] || ('state ' + primaryCode);
      const {count: transitions} = _enumTransitions(gvs);
      statsEl.textContent = `${total} probes · ${pct.toFixed(1)}% ${primaryLbl} · ${transitions} transition${transitions === 1 ? '' : 's'}`;
    }
    return;
  }

  // gauge_numeric and time_duration — line chart.
  const TARGET = 300;
  let pts;
  const valOf = g => category === 'time_duration' ? g.v / 100 : g.v;
  if (okG.length <= TARGET) {
    pts = okG.map(g => ({x: xOf(g.ts), y: yOf(valOf(g)), ts: g.ts, v: valOf(g)}));
  } else {
    const bucketSec = tsRange / TARGET;
    const acc = Array.from({length: TARGET}, () => ({sum: 0, n: 0, minV: Infinity, maxV: -Infinity}));
    for (const g of okG) {
      const bi = Math.min(TARGET-1, Math.floor((g.ts - windowStart) / bucketSec));
      if (bi >= 0) {
        const v = valOf(g);
        acc[bi].sum += v; acc[bi].n++;
        const vmn = g.vMin != null ? (category === 'time_duration' ? g.vMin/100 : g.vMin) : v;
        const vmx = g.vMax != null ? (category === 'time_duration' ? g.vMax/100 : g.vMax) : v;
        acc[bi].minV = Math.min(acc[bi].minV, vmn);
        acc[bi].maxV = Math.max(acc[bi].maxV, vmx);
      }
    }
    pts = [];
    for (let i = 0; i < TARGET; i++) {
      if (acc[i].n > 0) {
        const ts = windowStart + (i + 0.5) * bucketSec;
        pts.push({x: xOf(ts), y: yOf(acc[i].sum / acc[i].n), ts, v: acc[i].sum / acc[i].n,
                  minV: acc[i].minV, maxV: acc[i].maxV});
      }
    }
  }
  // Min/Max envelope (rollup tier only — when points carry distinct minV/maxV).
  const togBand = document.getElementById(`tog-band-${did}-${sid}`)?.checked ?? true;
  if (togBand && pts.some(p => p.minV != null && p.maxV != null && p.minV !== p.maxV)) {
    ctx.globalAlpha = 0.5; ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(244,114,182,1)'; ctx.beginPath();
    pts.forEach((p, i) => { if (p.maxV != null) (i === 0 ? ctx.moveTo : ctx.lineTo).call(ctx, p.x, yOf(p.maxV)); });
    ctx.stroke();
    ctx.strokeStyle = `rgb(${_upC})`; ctx.beginPath();
    pts.forEach((p, i) => { if (p.minV != null) (i === 0 ? ctx.moveTo : ctx.lineTo).call(ctx, p.x, yOf(p.minV)); });
    ctx.stroke();
    ctx.globalAlpha = 1.0;
  }
  // Main avg line.
  const togAvg = document.getElementById(`tog-avg-${did}-${sid}`)?.checked ?? true;
  if (togAvg && pts.length > 1) {
    ctx.strokeStyle = `rgb(${_acc})`; ctx.lineWidth = 2;
    ctx.shadowColor = `rgba(${_acc},.45)`; ctx.shadowBlur = 3;
    ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.stroke(); ctx.shadowBlur = 0;
  }
  // time_duration: mark reboots (value drops) with red vertical lines.
  if (category === 'time_duration') {
    for (let i = 1; i < sortedG.length; i++) {
      const prev = sortedG[i-1], curr = sortedG[i];
      if (prev.ok && curr.ok && curr.v < prev.v) {
        const x = xOf(curr.ts);
        ctx.strokeStyle = `rgba(${_dn},.6)`; ctx.lineWidth = 1;
        ctx.setLineDash([4,3]); ctx.beginPath();
        ctx.moveTo(x, TOP); ctx.lineTo(x, H - BOT); ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }
  // Stats bar.
  if (statsEl) {
    const total = gvs.length;
    const ok = okG.length;
    const upPct = total ? Math.round(ok / total * 1000) / 10 : 0;
    if (category === 'time_duration') {
      const last = [...okG].reverse()[0];
      let reboots = 0, prev = null;
      for (const g of okG) { if (prev != null && g.v < prev) reboots++; prev = g.v; }
      statsEl.textContent = `${total} probes · ${upPct}% up · uptime ${last ? _fmtDurationSec(last.v/100) : '—'} · ${reboots} reboot${reboots === 1 ? '' : 's'}`;
    } else {
      const avg = ok ? okG.reduce((a,g) => a + g.v, 0) / ok : null;
      const minV = ok ? Math.min(...okG.map(g => g.vMin != null ? g.vMin : g.v)) : null;
      const maxV = ok ? Math.max(...okG.map(g => g.vMax != null ? g.vMax : g.v)) : null;
      const f = v => _fmtGaugeValue(v, snmpUnit);
      statsEl.textContent = `${total} probes · ${upPct}% up · avg ${f(avg)} · min ${f(minV)} · max ${f(maxV)}`;
    }
  }
}

function _buildSummaryTable(sumEl, summary, minutes, rateSamples, snmpUnit, did, sid) {
  if (!sumEl) return;
  const _isPing = S.sensors[`${did}/${sid}`]?.stype === 'ping';
  const _isCounter = Array.isArray(rateSamples) && rateSamples.length > 0;
  let _bSec;
  if      (minutes <= 5760)   _bSec = 3600;
  else if (minutes <= 64800)  _bSec = 86400;
  else if (minutes <= 288000) _bSec = 7 * 86400;
  else                        _bSec = 30 * 86400;
  const _tzOff = new Date().getTimezoneOffset() * 60;

  // v0.9.7: typed SNMP summary tables for non-counter categories.
  const _typedCat = _snmpCategoryFor(did, sid);
  if (_typedCat && _typedCat !== 'counter_rate' && !_isCounter) {
    const _samplesCache = _histCache[`${did}/${sid}`]?.samples || [];
    const gvs = _gaugeValSamples(_samplesCache);
    if (!gvs.length) { sumEl.innerHTML = ''; return; }

    if (_typedCat === 'text') {
      // Change log — list of (ts, old→new) pairs.
      const strSamples = _samplesCache.filter(p => p.ok && p.value != null)
        .map(p => ({ts: p.ts, v: String(p.value)}));
      const changes = [];
      for (let i = 1; i < strSamples.length; i++) {
        if (strSamples[i].v !== strSamples[i-1].v) {
          changes.push({ts: strSamples[i].ts, from: strSamples[i-1].v, to: strSamples[i].v});
        }
      }
      const rows = changes.length
        ? changes.map(c => {
            const d = new Date(c.ts * 1000);
            const lbl = d.toLocaleDateString([],{month:'short',day:'numeric'}) + ' ' +
                        d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
            return `<tr>
              <td>${lbl}</td>
              <td style="color:var(--text3)">${esc(c.from).slice(0,48)}</td>
              <td style="color:var(--up)">→ ${esc(c.to).slice(0,48)}</td>
            </tr>`;
          }).join('')
        : `<tr><td colspan="3" style="color:var(--text3);text-align:center;padding:16px">No changes in window</td></tr>`;
      sumEl.innerHTML = `<table class="dm-hist-tbl">
        <thead><tr><th>Time</th><th>Previous</th><th>New</th></tr></thead>
        <tbody>${rows}</tbody></table>`;
      return;
    }

    // enum_state / gauge_numeric / time_duration → per-bucket aggregation.
    const _summarySen = S.sensors[`${did}/${sid}`];
    const legend = _typedCat === 'enum_state'
      ? (_summarySen ? _effectiveEnumLegend(_summarySen) : _parseEnumLegend(snmpUnit))
      : null;
    const primaryCode = legend && (legend['1'] ? '1' : Object.keys(legend)[0] || '1');
    const primaryLbl = legend ? (legend[primaryCode] || ('state ' + primaryCode)) : null;
    const _bk = {};
    for (const r of summary) {
      const k = Math.floor((r.ts + _tzOff) / _bSec) * _bSec - _tzOff;
      if (!_bk[k]) _bk[k] = {ts:k, ok:0, fail:0, vsum:0, vmin:Infinity, vmax:-Infinity, vcnt:0, primary:0, transitions:0, prevV:null};
      _bk[k].ok += r.ok; _bk[k].fail += r.fail;
    }
    for (const g of gvs) {
      if (!g.ok || g.v == null) continue;
      const k = Math.floor((g.ts + _tzOff) / _bSec) * _bSec - _tzOff;
      if (!_bk[k]) _bk[k] = {ts:k, ok:0, fail:0, vsum:0, vmin:Infinity, vmax:-Infinity, vcnt:0, primary:0, transitions:0, prevV:null};
      const b = _bk[k];
      const v = _typedCat === 'time_duration' ? g.v / 100 : g.v;
      b.vsum += v; b.vcnt++;
      const vmn = g.vMin != null ? (_typedCat === 'time_duration' ? g.vMin/100 : g.vMin) : v;
      const vmx = g.vMax != null ? (_typedCat === 'time_duration' ? g.vMax/100 : g.vMax) : v;
      b.vmin = Math.min(b.vmin, vmn);
      b.vmax = Math.max(b.vmax, vmx);
      if (_typedCat === 'enum_state') {
        if (String(Math.round(g.v)) === primaryCode) b.primary++;
        if (b.prevV != null && Math.round(b.prevV) !== Math.round(g.v)) b.transitions++;
        b.prevV = g.v;
      }
    }
    const fmtTs = b => {
      const d = new Date(b.ts * 1000);
      if (_bSec < 86400)  return d.toLocaleDateString([],{month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
      if (_bSec < 604800) return d.toLocaleDateString([],{month:'short',day:'numeric'});
      return d.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
    };
    if (_typedCat === 'enum_state') {
      const rows = Object.values(_bk).sort((a,b) => a.ts - b.ts).map(b => {
        const upPct = b.ok + b.fail > 0 ? Math.round(b.ok / (b.ok + b.fail) * 100) : 100;
        const pct = b.vcnt ? Math.round(b.primary / b.vcnt * 1000) / 10 : 0;
        const rowCls = pct < 80 ? 'hrow-crit' : pct < 99 ? 'hrow-warn' : '';
        return `<tr class="${rowCls}">
          <td>${fmtTs(b)}</td>
          <td style="color:var(--up)">${b.ok}↑</td>
          <td style="color:${b.fail?'var(--down)':'var(--text3)'}">${b.fail}↓</td>
          <td style="color:${upPct<100?'var(--warn)':'var(--text2)'}">${upPct}%</td>
          <td>${pct.toFixed(1)}%</td>
          <td style="color:${b.transitions?'var(--warn)':'var(--text3)'}">${b.transitions}</td>
        </tr>`;
      }).join('');
      sumEl.innerHTML = `<table class="dm-hist-tbl">
        <thead><tr><th>Time</th><th>Up</th><th>Down</th><th>Avail</th>
          <th>% ${esc(primaryLbl)}</th><th>Transitions</th></tr></thead>
        <tbody>${rows}</tbody></table>`;
      return;
    }
    // gauge_numeric / time_duration
    const f = v => (v != null && isFinite(v))
      ? (_typedCat === 'time_duration' ? _fmtDurationSec(v) : _fmtGaugeValue(v, snmpUnit))
      : '—';
    const rows = Object.values(_bk).sort((a,b) => a.ts - b.ts).map(b => {
      const upPct = b.ok + b.fail > 0 ? Math.round(b.ok / (b.ok + b.fail) * 100) : 100;
      const avg = b.vcnt ? b.vsum / b.vcnt : null;
      const rowCls = upPct < 80 ? 'hrow-crit' : upPct < 95 ? 'hrow-warn' : '';
      return `<tr class="${rowCls}">
        <td>${fmtTs(b)}</td>
        <td style="color:var(--up)">${b.ok}↑</td>
        <td style="color:${b.fail?'var(--down)':'var(--text3)'}">${b.fail}↓</td>
        <td style="color:${upPct<100?'var(--warn)':'var(--text2)'}">${upPct}%</td>
        <td>${f(avg)}</td>
        <td>${f(b.vmin === Infinity ? null : b.vmin)}</td>
        <td>${f(b.vmax === -Infinity ? null : b.vmax)}</td>
      </tr>`;
    }).join('');
    sumEl.innerHTML = `<table class="dm-hist-tbl">
      <thead><tr><th>Time</th><th>Up</th><th>Down</th><th>Avail</th>
        <th>Avg</th><th>Min</th><th>Max</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    return;
  }

  if (_isCounter) {
    // Counter mode: rate stats per time bucket
    if (!rateSamples.length && !summary.length) { sumEl.innerHTML = ''; return; }
    const _buckets = {};
    for (const r of summary) {
      const k = Math.floor((r.ts + _tzOff) / _bSec) * _bSec - _tzOff;
      if (!_buckets[k]) _buckets[k] = {ts:k, ok:0, fail:0, rsum:0, rmin:Infinity, rmax:-Infinity, rcnt:0};
      _buckets[k].ok += r.ok; _buckets[k].fail += r.fail;
    }
    for (const r of rateSamples) {
      if (!r.ok || r.rate == null) continue;
      const k = Math.floor((r.ts + _tzOff) / _bSec) * _bSec - _tzOff;
      if (!_buckets[k]) _buckets[k] = {ts:k, ok:0, fail:0, rsum:0, rmin:Infinity, rmax:-Infinity, rcnt:0};
      const d = _rateToDisplayUnits(r.rate, snmpUnit);
      _buckets[k].rsum += d; _buckets[k].rcnt++;
      // v0.9.7: use per-bucket min/max (peak-preserving at rollup tier); at
      // raw tier r.min = r.max = r.rate so this degrades to the old behavior.
      const dMin = _rateToDisplayUnits(r.min != null ? r.min : r.rate, snmpUnit);
      const dMax = _rateToDisplayUnits(r.max != null ? r.max : r.rate, snmpUnit);
      _buckets[k].rmin = Math.min(_buckets[k].rmin, dMin);
      _buckets[k].rmax = Math.max(_buckets[k].rmax, dMax);
    }
    const _u   = snmpUnit;
    const _lbl = _u==='bytes'?'Mbps':_u==='errors'?'err/s':_u==='packets'?'pkt/s':'/s';
    const _fr  = v => (v!=null&&isFinite(v)) ? _fmtRateThrLabel(v, _u) : '—';
    const rows = Object.values(_buckets).sort((a, b) => a.ts - b.ts).map(_b => {
      const d = new Date(_b.ts * 1000);
      let lbl;
      if      (_bSec < 86400)  lbl = d.toLocaleDateString([],{month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
      else if (_bSec < 604800) lbl = d.toLocaleDateString([],{month:'short',day:'numeric'});
      else                     lbl = d.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
      const upPct = _b.ok + _b.fail > 0 ? Math.round(_b.ok / (_b.ok + _b.fail) * 100) : 100;
      const avgR  = _b.rcnt > 0 ? _b.rsum / _b.rcnt : null;
      const rowCls = upPct < 80 ? 'hrow-crit' : upPct < 95 ? 'hrow-warn' : '';
      return `<tr class="${rowCls}">
        <td>${lbl}</td>
        <td style="color:var(--up)">${_b.ok}↑</td>
        <td style="color:${_b.fail?'var(--down)':'var(--text3)'}">${_b.fail}↓</td>
        <td style="color:${upPct<100?'var(--warn)':'var(--text2)'}">${upPct}%</td>
        <td>${_fr(avgR)}</td>
        <td>${_fr(_b.rmin===Infinity?null:_b.rmin)}</td>
        <td>${_fr(_b.rmax===-Infinity?null:_b.rmax)}</td>
      </tr>`;
    }).join('');
    sumEl.innerHTML = `<table class="dm-hist-tbl">
      <thead><tr><th>Time</th><th>Up</th><th>Down</th><th>Avail</th>
        <th>Avg ${_lbl}</th><th>Min ${_lbl}</th><th>Max ${_lbl}</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    return;
  }

  // VMware metric-value summary table
  const _vmU4 = _vmUnit(did, sid);
  if (_vmU4 !== null) {
    if (!summary.length) { sumEl.innerHTML = ''; return; }
    const _lbl2 = _vmUnitLabel(_vmU4);
    const _bk2 = {};
    for (const r of summary) {
      const k = Math.floor((r.ts + _tzOff) / _bSec) * _bSec - _tzOff;
      if (!_bk2[k]) _bk2[k] = {ts:k, ok:0, fail:0, wsum:0, wcnt:0, minV:Infinity, maxV:-Infinity, cnt:0};
      const b = _bk2[k];
      b.ok += r.ok; b.fail += r.fail;
      if (r.avg_ms != null) { b.wsum += r.avg_ms * r.ok; b.wcnt += r.ok; }
      if (r.min_ms != null) b.minV = Math.min(b.minV, r.min_ms);
      if (r.max_ms != null) b.maxV = Math.max(b.maxV, r.max_ms);
      b.cnt++;
    }
    const rows2 = Object.values(_bk2).sort((a, b) => a.ts - b.ts).map(b => {
      const d = new Date(b.ts * 1000);
      let lbl;
      if      (_bSec < 86400)  lbl = d.toLocaleDateString([],{month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
      else if (_bSec < 604800) lbl = d.toLocaleDateString([],{month:'short',day:'numeric'});
      else                     lbl = d.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
      const upPct = b.ok + b.fail > 0 ? Math.round(b.ok / (b.ok + b.fail) * 100) : 100;
      const avg = b.wcnt > 0 ? b.wsum / b.wcnt : null;
      const rowCls = upPct < 80 ? 'hrow-crit' : upPct < 95 ? 'hrow-warn' : '';
      return `<tr class="${rowCls}">
        <td>${lbl}</td>
        <td style="color:var(--up)">${b.ok}↑</td>
        <td style="color:${b.fail?'var(--down)':'var(--text3)'}">${b.fail}↓</td>
        <td style="color:${upPct<100?'var(--warn)':'var(--text2)'}">${upPct}%</td>
        <td>${_fmtVmVal(avg, _vmU4)}</td>
        <td>${b.minV!==Infinity?_fmtVmVal(b.minV, _vmU4):'—'}</td>
        <td>${b.maxV!==-Infinity?_fmtVmVal(b.maxV, _vmU4):'—'}</td>
      </tr>`;
    }).join('');
    sumEl.innerHTML = `<table class="dm-hist-tbl">
      <thead><tr><th>Time</th><th>Up</th><th>Down</th><th>Avail</th>
        <th>Avg ${_lbl2}</th><th>Min ${_lbl2}</th><th>Max ${_lbl2}</th></tr></thead>
      <tbody>${rows2}</tbody></table>`;
    return;
  }

  // ms-based summary table (unchanged)
  if (!summary.length) { sumEl.innerHTML = ''; return; }
  const _buckets = {};
  for (const r of summary) {
    const _bKey = Math.floor((r.ts + _tzOff) / _bSec) * _bSec - _tzOff;
    if (!_buckets[_bKey]) _buckets[_bKey] = {ts:_bKey, ok:0, fail:0, wsum:0, wcnt:0, min_ms:Infinity, max_ms:-Infinity, lsum:0, jsum:0, cnt:0};
    const _b = _buckets[_bKey];
    _b.ok   += r.ok;
    _b.fail += r.fail;
    if (r.avg_ms != null) { _b.wsum += r.avg_ms * r.ok; _b.wcnt += r.ok; }
    if (r.min_ms != null) _b.min_ms = Math.min(_b.min_ms, r.min_ms);
    if (r.max_ms != null) _b.max_ms = Math.max(_b.max_ms, r.max_ms);
    _b.lsum += r.loss_pct  || 0;
    _b.jsum += r.jitter_ms || 0;
    _b.cnt++;
  }
  const rows = Object.values(_buckets).sort((a, b) => a.ts - b.ts).map(_b => {
    const d = new Date(_b.ts * 1000);
    let lbl;
    if      (_bSec < 86400)  lbl = d.toLocaleDateString([],{month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    else if (_bSec < 604800) lbl = d.toLocaleDateString([],{month:'short',day:'numeric'});
    else                     lbl = d.toLocaleDateString([],{month:'short',day:'numeric',year:'numeric'});
    const upPct  = _b.ok + _b.fail > 0 ? Math.round(_b.ok / (_b.ok + _b.fail) * 100) : 100;
    const avg    = _b.wcnt > 0 ? Math.round(_b.wsum / _b.wcnt * 10) / 10 : null;
    const minMs  = _b.min_ms ===  Infinity ? null : _b.min_ms;
    const maxMs  = _b.max_ms === -Infinity ? null : _b.max_ms;
    const loss   = _b.cnt > 0 ? (_b.lsum / _b.cnt).toFixed(1) : '0.0';
    const jitter = _b.cnt > 0 ? (_b.jsum / _b.cnt).toFixed(1) : '0.0';
    const lossPct = parseFloat(loss);
    const rowCls = _isPing && lossPct > 20 ? 'hrow-crit' : _isPing && lossPct > 5 ? 'hrow-warn' : '';
    return `<tr class="${rowCls}">
      <td>${lbl}</td>
      <td style="color:var(--up)">${_b.ok}↑</td>
      <td style="color:${_b.fail?'var(--down)':'var(--text3)'}">${_b.fail}↓</td>
      <td style="color:${upPct<100?'var(--warn)':'var(--text2)'}">${upPct}%</td>
      <td>${avg!=null?avg+'ms':'—'}</td>
      <td>${minMs!=null?minMs+'ms':'—'}</td>
      <td>${maxMs!=null?maxMs+'ms':'—'}</td>
      ${_isPing?`<td style="color:${lossPct>5?'var(--warn)':'var(--text2)'}">${loss}%</td>`:''}
      <td style="color:rgba(188,130,255,.85)">${jitter}ms</td>
    </tr>`;
  }).join('');
  sumEl.innerHTML = `<table class="dm-hist-tbl">
    <thead><tr><th>Time</th><th>Up</th><th>Down</th><th>Avail</th><th>Avg</th><th>Min</th><th>Max</th>${_isPing?'<th>Loss</th>':''}<th>Jitter</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function dmToggleAutoRefresh(did, sid) {
  if (!S._arTimers) S._arTimers = {};
  const key = `${did}/${sid}`;
  const btn = document.getElementById(`ar-${did}-${sid}`);
  if (S._arTimers[key]) {
    clearInterval(S._arTimers[key]);
    delete S._arTimers[key];
    if (btn) { btn.textContent = 'Auto-Refresh'; btn.classList.remove('active'); }
  } else {
    S._arTimers[key] = setInterval(() => dmHistReload(did, sid), 10000);
    if (btn) { btn.textContent = 'Auto-Refresh ●'; btn.classList.add('active'); }
  }
}

function _dmStopAR(did, sid) {
  if (!S._arTimers) return;
  const key = `${did}/${sid}`;
  if (S._arTimers[key]) { clearInterval(S._arTimers[key]); delete S._arTimers[key]; }
}

function dmToggleFullscreen(did, sid) {
  const box = document.querySelector('#dm .dmbox');
  if (!box) return;
  if (!document.fullscreenElement) {
    box.requestFullscreen().catch(() => {});
  } else {
    document.exitFullscreen().catch(() => {});
  }
}

document.addEventListener('fullscreenchange', () => {
  const fsBtn = document.querySelector('.dmbox [id^="fs-"]');
  if (!fsBtn) return;
  const did = fsBtn.dataset.did, sid = fsBtn.dataset.sid;
  const canvas = document.getElementById(`dm-hist-canvas-${did}-${sid}`);
  if (document.fullscreenElement) {
    fsBtn.textContent = '⊠';
    fsBtn.title = 'Exit full screen';
    setTimeout(() => {
      if (canvas) {
        // Let flex layout determine size, then sync canvas drawing height
        const parent = canvas.parentElement;
        canvas.height = parent ? parent.clientHeight : 620;
        dmHistRedraw(did, sid);
      }
    }, 100);
  } else {
    fsBtn.textContent = '⤢';
    fsBtn.title = 'Full screen';
    if (canvas) { canvas.height = 320; dmHistRedraw(did, sid); }
  }
});

function dmHistReload(did, sid) {
  const histTab = document.getElementById(`dm-tab-history-${did}-${sid}`);
  if (!histTab) { _dmStopAR(did, sid); return; }
  const activePill = histTab.querySelector('.dm-hist-pill.active');
  const minutes = activePill ? +activePill.dataset.m : 1440;
  loadDmHistory(did, sid, minutes);
}

// Thin wrapper used by sensor detail modal (preserves existing call sites)
async function loadDmHistory(did, sid, minutes) {
  if (minutes === undefined) minutes = 1440;
  const canvas  = document.getElementById(`dm-hist-canvas-${did}-${sid}`);
  const statsEl = document.getElementById(`dm-hist-stats-${did}-${sid}`);
  const sumEl   = document.getElementById(`dm-hist-summary-${did}-${sid}`);
  await _renderHistoryChart(canvas, statsEl, sumEl, did, sid, minutes);
}

function mVal(s,k){
  const isSnmp=s.stype==='snmp';
  const isDns =s.stype==='dns';
  const isVm  =s.stype==='vmware';
  const _vu   =isVm?(_VM_UNITS[s.vmware_metric]||''):null;
  if(k==='last'){
    if(isSnmp||isDns) {
      if (s.alive === false) return 'FAIL';
      // v0.9.7: typed SNMP — show labeled enum state, formatted gauge value,
      // or duration instead of the raw response string.
      if (isSnmp) {
        const cat = _snmpCategory(s.snmp_unit, s.snmp_type, s.snmp_oid);
        if (cat === 'enum_state' && s.last_value != null) {
          const legend = _effectiveEnumLegend(s);
          const code = String(parseInt(s.last_value, 10));
          if (legend[code]) return legend[code];
        }
        if (cat === 'gauge_numeric' && s.last_value != null) {
          const v = parseFloat(s.last_value);
          if (!isNaN(v)) return _fmtGaugeValue(v, s.snmp_unit);
        }
        if (cat === 'time_duration' && s.last_value != null) {
          const v = parseFloat(s.last_value);
          if (!isNaN(v)) return _fmtDurationSec(v / 100);
        }
      }
      return s.last_value||s.last_detail||'—';
    }
    if(s.stype==='tls') return s.alive===false?'FAIL':(s.last_value!=null?s.last_value+'d':'—');
    if(isVm) return s.last_ms!=null?_fmtVmVal(s.last_ms,_vu):(s.alive===false?'DOWN':'—');
    return s.last_ms!==null&&s.last_ms!==undefined?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—');
  }
  if(k==='avg') return (isSnmp||s.stype==='tls')?(s._ov_avg||'—'):isVm?(s.avg_ms!=null?_fmtVmVal(s.avg_ms,_vu):'—'):(s.avg_ms?`${s.avg_ms}ms`:'—');
  if(k==='min') return (isSnmp||s.stype==='tls')?(s._ov_min||'—'):isVm?(s.min_ms!=null?_fmtVmVal(s.min_ms,_vu):'—'):(s.min_ms?`${s.min_ms}ms`:'—');
  if(k==='max') return (isSnmp||s.stype==='tls')?(s._ov_max||'—'):isVm?(s.max_ms!=null?_fmtVmVal(s.max_ms,_vu):'—'):(s.max_ms?`${s.max_ms}ms`:'—');
  if(k==='loss')return s.loss_pct!==undefined?`${s.loss_pct}%`:'—';
  if(k==='sent')return String(s.total||0);
}

function updateDetailWin(did,sid,s){
  ['last','avg','min','max','loss','sent'].forEach(k=>{
    const el=document.getElementById(`dmv-${did}-${sid}-${k}`);
    if(!el)return;
    el.textContent=mVal(s,k);
    if(k==='last'){const isSD=s.stype==='snmp'||s.stype==='dns'||s.stype==='tls'||s.stype==='banner';el.className=`dm-mv ${s.alive===false?'b':isSD?(s.alive===true?'g':''):(s.last_ms!==null?msC(s.last_ms,s):'')}`;}
  });
  const cvs=document.getElementById(`dmc-${did}-${sid}`);
  if(cvs)drawDmChart(cvs,s.history||[]);
  const lbl=document.getElementById(`dmlbl-${did}-${sid}`);
  if(lbl&&s.max_ms&&s.stype!=='snmp'&&s.stype!=='tls')lbl.textContent=`max ${s.max_ms}ms · avg ${s.avg_ms||'—'}ms`;
}

function drawDmChart(canvas,history){
  canvas.width=canvas.offsetWidth||620;
  const W=canvas.width,H=110,ctx=canvas.getContext('2d');
  ctx.clearRect(0,0,W,H);
  const _txt2=_SCC.text.join(','), _up2=_SCC.up.join(','), _dn2=_SCC.down.join(',');
  ctx.strokeStyle=`rgba(${_txt2},0.04)`;ctx.lineWidth=1;ctx.setLineDash([3,4]);
  [.2,.4,.6,.8].forEach(p=>{const y=H-p*(H-10);ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(W,y);ctx.stroke();});
  ctx.setLineDash([]);
  if(!history||history.length<2)return;
  const valid=history.filter(v=>v!==null);
  const maxV=valid.length?Math.max(...valid)*1.25:200;
  const step=W/(history.length-1);
  const pts=[];
  history.forEach((v,i)=>{
    if(v!==null)pts.push({x:i*step,y:H-(v/maxV)*(H-10)});
    else{
      const cx=i*step,cy=H/2;
      ctx.strokeStyle=`rgba(${_dn2},.7)`;ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(cx-5,cy-5);ctx.lineTo(cx+5,cy+5);ctx.stroke();
      ctx.beginPath();ctx.moveTo(cx+5,cy-5);ctx.lineTo(cx-5,cy+5);ctx.stroke();
    }
  });
  if(pts.length<2)return;
  const g=ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,`rgba(${_up2},.18)`);g.addColorStop(1,`rgba(${_up2},0)`);
  ctx.beginPath();ctx.moveTo(pts[0].x,H);pts.forEach(p=>ctx.lineTo(p.x,p.y));
  ctx.lineTo(pts[pts.length-1].x,H);ctx.closePath();ctx.fillStyle=g;ctx.fill();
  ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.strokeStyle=`rgba(${_up2},.2)`;ctx.lineWidth=6;ctx.stroke();
  ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.strokeStyle=`rgb(${_up2})`;ctx.lineWidth=2;ctx.stroke();
  const lp=pts[pts.length-1];
  ctx.beginPath();ctx.arc(lp.x,lp.y,4,0,Math.PI*2);ctx.fillStyle=`rgb(${_up2})`;ctx.fill();
}