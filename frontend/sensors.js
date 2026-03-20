// �?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�? SENSOR TILES �?�?�?�?�?�?�?�?�?�?�?�?
function sIco(t){return t==='ping'?'◉':t==='tcp'?'⇌':t==='snmp'?'◎':t==='dns'?'⬡':t==='tls'?'T':t==='http_keyword'?'K':t==='banner'?'B':'◈'}
function msC(ms,s){if(ms===null)return'b';const hw=s?.warn_ms>0,hc=s?.crit_ms>0;if(hw||hc){const w=hw?s.warn_ms:99999,c=hc?s.crit_ms:99999;if(ms>=c)return'b';if(ms>=w)return'w';return'g';}if(ms<(window._lGood||100))return'g';if(ms<(window._lWarn||300))return'w';return'b'}
function fmtTs(ts){try{return new Date(ts).toLocaleTimeString('en-GB');}catch(e){return ts;}}

function tileHTML(s){
  const st=s.alive===true?'up':s.alive===false?'down':'';
  const isSnmp=s.stype==='snmp';
  const isDns  =s.stype==='dns';
  const isTls  =s.stype==='tls';
  const isBanner=s.stype==='banner';
  const rawVal = (isSnmp||isDns) ? (s.last_value||s.last_detail||'—')
               : isTls ? (s.last_value!=null?s.last_value+'d':null) : null;
  const vt = (isSnmp||isDns)
    ? (s.alive===false?'FAIL':(rawVal.length>14?rawVal.slice(0,14)+'…':rawVal))
    : isTls ? (s.alive===false?'FAIL':(rawVal||'—'))
    : (s.last_ms!==null&&s.last_ms!==undefined?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—'));
  const vc=s.alive===false?'b':((isSnmp||isDns||isTls)?(s.alive===true?'g':'m'):(s.last_ms!==null?msC(s.last_ms,s):'m'));
  const tgt=s.stype==='http'?(s.url||s.host):s.stype==='tcp'?`${s.host}:${s.port}`:s.stype==='snmp'?`${s.host} OID:${(s.snmp_oid||'').split('.').slice(-3).join('.')}`:s.stype==='dns'?`${s.dns_query||s.host} (${s.dns_record_type||'A'})`:s.host;
  const isMuted=s.alerts_muted||S.devices[s.device_id]?.alerts_muted;
  const hist=(s.history||[]).slice(-40);
  const ub=Array(40).fill(0).map((_,i)=>{
    const idx=i-(40-hist.length);
    if(idx<0)return'<div class="ub-s"></div>';
    const v=hist[idx];
    if(v===null)return`<div class="ub-s" style="background:var(--down)"></div>`;
    const _mc=msC(v,s);const c=(isSnmp||isDns||isTls)?'var(--up)':(_mc==='g'?'var(--up)':_mc==='w'?'var(--warn)':'var(--down)');
    return`<div class="ub-s" style="background:${c}"></div>`;
  }).join('');
  return`
  <div class="stl-hd">
    <div class="stl-tbdg ${s.stype}">${sIco(s.stype)} ${s.stype.toUpperCase().replace('_',' ')}</div>
    <div class="stl-nm">${esc(s.name)}</div>
    <span class="stl-muted" id="sm-muted-${s.device_id}_${s.sensor_id}" title="Alerts muted" style="${isMuted?'':'display:none'}">🔕</span>
    <button class="stl-hist" onclick="event.stopPropagation();openDetail('${s.device_id}','${s.sensor_id}','history')" title="History">&#9201;</button>
    <div class="stl-sdot ${st}"></div>
  </div>
  <div class="stl-body">
    <div class="stl-val ${vc}" id="stv-${s.device_id}_${s.sensor_id}">${vt}</div>
    <div class="stl-det" title="${esc(s.last_detail||'')}">
      <span id="std-${s.device_id}_${s.sensor_id}">${esc(s.last_detail||tgt)}</span>
    </div>
    <div class="ub" id="ub-${s.device_id}_${s.sensor_id}">${ub}</div>
  </div>
  <div class="stl-spark"><canvas class="spk" height="28"></canvas></div>
  <div class="stl-stats">
    ${(isSnmp||isTls)?`
    <div class="stl-stat"><div class="stl-sv" id="sa-${s.device_id}_${s.sensor_id}">—</div><div class="stl-sk">Avg</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sm-${s.device_id}_${s.sensor_id}">${s.loss_pct!==undefined?s.loss_pct+'%':'—'}</div><div class="stl-sk">Loss</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sl-${s.device_id}_${s.sensor_id}">${s.total||0}</div><div class="stl-sk">Sent</div></div>`:`
    <div class="stl-stat"><div class="stl-sv" id="sa-${s.device_id}_${s.sensor_id}">${s.avg_ms?s.avg_ms+'ms':'—'}</div><div class="stl-sk">Avg</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sm-${s.device_id}_${s.sensor_id}">${s.min_ms?s.min_ms+'ms':'—'}</div><div class="stl-sk">Min</div></div>
    <div class="stl-stat"><div class="stl-sv" id="sl-${s.device_id}_${s.sensor_id}">${s.loss_pct!==undefined?s.loss_pct+'%':'—'}</div><div class="stl-sk">Loss</div></div>`}
  </div>`;
}

function renderTile(did,s){
  const grid=document.getElementById(`sg-${did}`);
  if(!grid)return;
  const key=`${did}/${s.sensor_id}`;
  const old=document.getElementById(`t-${key.replace('/','_')}`);
  if(old)old.remove();
  const t=document.createElement('div');
  const _thr=s.threshold_state&&s.threshold_state!=='ok'&&s.alive!==false?' thr-'+s.threshold_state:'';
  t.className=`stl ${s.alive===true?'up':s.alive===false?'down':''}${_thr}`;
  t.id=`t-${key.replace('/','_')}`;
  t.onclick=()=>openDetail(did,s.sensor_id);
  t.innerHTML=tileHTML(s);
  grid.appendChild(t);
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
  const _newThr=s.threshold_state&&s.threshold_state!=='ok'&&s.alive!==false?' thr-'+s.threshold_state:'';
  tile.className=`stl ${s.alive===true?'up':s.alive===false?'down':''}${_newThr}`;
  const dot=tile.querySelector('.stl-sdot');
  if(dot)dot.className=`stl-sdot ${s.alive===true?'up':s.alive===false?'down':''}`;
  const isSnmp=s.stype==='snmp';
  const isDns2  =s.stype==='dns';
  const isTls2  =s.stype==='tls';
  const isBanner2=s.stype==='banner';
  const rawVal2 = (isSnmp||isDns2)?(s.last_value||s.last_detail||'—')
               : isTls2?(s.last_value!=null?s.last_value+'d':null):null;
  const vt = (isSnmp||isDns2)
    ? (s.alive===false?'FAIL':(rawVal2.length>14?rawVal2.slice(0,14)+'…':rawVal2))
    : isTls2 ? (s.alive===false?'FAIL':(rawVal2||'—'))
    : (s.last_ms!==null&&s.last_ms!==undefined?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—'));
  const vc=s.alive===false?'b':((isSnmp||isDns2||isTls2)?(s.alive===true?'g':'m'):(s.last_ms!==null?msC(s.last_ms,s):'m'));
  const vel=document.getElementById(`stv-${sk}`);
  if(vel){vel.textContent=vt;vel.className=`stl-val ${vc}`;}
  const mutedBadge=document.getElementById(`sm-muted-${sk}`);
  if(mutedBadge){const isMuted2=s.alerts_muted||S.devices[s.device_id]?.alerts_muted;mutedBadge.style.display=isMuted2?'':'none';}
  const del=document.getElementById(`std-${sk}`);
  if(del)del.textContent=s.last_detail||'';
  const ael=document.getElementById(`sa-${sk}`),mel=document.getElementById(`sm-${sk}`),lel=document.getElementById(`sl-${sk}`);
  if(isSnmp||isTls2){
    if(ael)ael.textContent='—';
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
    sqs.forEach((sq,i)=>{
      const idx=i-(40-hist.length);
      if(idx<0){sq.style.background='var(--bg4)';return;}
      const v=hist[idx];
      if(v===null){sq.style.background='var(--down)';return;}
      const _mc2=msC(v,s);sq.style.background=(isSnmp||isDns2||isTls2)?'var(--up)':(_mc2==='g'?'var(--up)':_mc2==='w'?'var(--warn)':'var(--down)');
    });
  }
  drawSpk(key,s.history||[]);
  updateDetailWin(s.device_id,s.sensor_id,s);
}

function setupCharts(dev){
  setupChartsByDid(dev.device_id);
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
  const g=ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(46,204,113,.22)');g.addColorStop(1,'rgba(46,204,113,0)');
  ctx.beginPath();ctx.moveTo(pts[0].x,H);
  pts.forEach(p=>ctx.lineTo(p.x,p.y));
  ctx.lineTo(pts[pts.length-1].x,H);ctx.closePath();ctx.fillStyle=g;ctx.fill();
  ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.strokeStyle='#2ecc71';ctx.lineWidth=1.5;ctx.stroke();
}

// ── Device status recalc ─────────────────────────────────────────
function recalcDevStatus(did){
  const sensors=Object.values(S.sensors).filter(s=>s.device_id===did);
  const alives=sensors.map(s=>s.alive);
  let st='unknown';
  if(alives.some(a=>a===false))st='down';
  else if(alives.every(a=>a===true))st='up';
  else if(alives.some(a=>a===true))st='warn';
  if(S.devices[did])S.devices[did].status=st;
  updateDpHeader(did,st);
  updateSbDevDot(did,st);
}
function updateDpHeader(did,st){
  updateCardStatus(did,st);
  // update device window bar if open
  const bar=document.getElementById(`dwbar-${did}`);
  if(bar) bar.className=`dw-bar ${st}`;
}

// ── Sidebar ──────────────────────────────────────────────────────
function renderSidebar(){
  const tree=document.getElementById('devTree');
  const devs=Object.values(S.devices);
  if(!devs.length){
    tree.innerHTML=`<div style="padding:18px 14px;color:var(--text3);font-size:11px;text-align:center;line-height:1.8">No devices yet.<br>Click <strong>＋ Add Device</strong>.</div>`;
    return;
  }
  const _devCol=new Set(JSON.parse(localStorage.getItem('pw-tree-collapsed')||'[]'));
  const _grpCol=new Set(JSON.parse(localStorage.getItem('pw-sb-grp-collapsed')||'[]'));
  // Build group map
  const grpMap={};
  devs.forEach(dev=>{
    const g=dev.group||'Default Group';
    if(!grpMap[g]) grpMap[g]=[];
    grpMap[g].push(dev);
  });
  // Order groups to match main panel order
  const panelOrder=[...document.querySelectorAll('.grp-grid')].map(g=>g.dataset.group).filter(Boolean);
  const allGrps=Object.keys(grpMap);
  const finalGrps=[...new Set([...panelOrder.filter(g=>grpMap[g]),...allGrps])];

  function devNodeHTML(dev){
    const col=_devCol.has(dev.device_id);
    return`
    <div class="dev-node" id="sbn-${dev.device_id}">
      <div class="dev-row" onclick="scrollToDev('${dev.device_id}')">
        <div class="dev-arr ${col?'':'open'}" title="${col?'Expand':'Collapse'}"
             onclick="event.stopPropagation();toggleDevTree('${dev.device_id}')">▶</div>
        <div class="dev-sdot ${dev.status||'unknown'}" id="sbdd-${dev.device_id}"></div>
        <div class="dev-info">
          <div class="dev-name">${esc(dev.name)}</div>
          <div class="dev-host">${esc(dev.host)}</div>
        </div>
        <div class="dev-cnt">${dev.sensors.length}</div>
      </div>
      <div class="snr-list${col?' collapsed':''}" id="sbsl-${dev.device_id}">
        ${dev.sensors.map(s=>`
          <div class="snr-row" id="sbsr-${dev.device_id}_${s.sensor_id}"
               onclick="openDetail('${dev.device_id}','${s.sensor_id}')">
            <div class="s-ico ${s.stype}">${sIco(s.stype)}</div>
            <div class="s-snm">${esc(s.name)}</div>
            <div class="s-sdot ${s.alive===true?'up':s.alive===false?'down':''}"
                 id="sbsd-${dev.device_id}_${s.sensor_id}"></div>
          </div>`).join('')}
      </div>
    </div>`;
  }

  if(finalGrps.length<=1 && finalGrps[0]==='Default Group'){
    // Single default group — no group header, flat list
    tree.innerHTML=(grpMap['Default Group']||[]).map(devNodeHTML).join('');
  } else {
    tree.innerHTML=finalGrps.map(grp=>{
      const gdevs=grpMap[grp]||[];
      const gid=grpId(grp);
      const grpCol=_grpCol.has(grp);
      return`
      <div class="sb-grp" id="sbg-${gid}">
        <div class="sb-grp-hdr" data-grp="${esc(grp)}" onclick="toggleSbGrp(this.dataset.grp)">
          <div class="sb-grp-arr ${grpCol?'':'open'}">▶</div>
          <div class="sb-grp-name">${esc(grp)}</div>
          <div class="sb-grp-cnt">${gdevs.length}</div>
        </div>
        <div class="sb-grp-body${grpCol?' collapsed':''}" id="sbgb-${gid}">
          ${gdevs.map(devNodeHTML).join('')}
        </div>
      </div>`;
    }).join('');
  }
}

function toggleSbGrp(grp){
  const gid=grpId(grp);
  const body=document.getElementById('sbgb-'+gid);
  const arr=document.querySelector('#sbg-'+gid+' .sb-grp-arr');
  if(!body) return;
  const nowCol=body.classList.toggle('collapsed');
  if(arr) arr.classList.toggle('open',!nowCol);
  const set=new Set(JSON.parse(localStorage.getItem('pw-sb-grp-collapsed')||'[]'));
  if(nowCol) set.add(grp); else set.delete(grp);
  localStorage.setItem('pw-sb-grp-collapsed',JSON.stringify([...set]));
}

function toggleDevTree(did){
  const list=document.getElementById(`sbsl-${did}`);
  const arr=document.querySelector(`#sbn-${did} .dev-arr`);
  if(!list) return;
  const nowCollapsed=list.classList.toggle('collapsed');
  if(arr){arr.classList.toggle('open',!nowCollapsed);arr.title=nowCollapsed?'Expand':'Collapse';}
  const set=new Set(JSON.parse(localStorage.getItem('pw-tree-collapsed')||'[]'));
  if(nowCollapsed) set.add(did); else set.delete(did);
  localStorage.setItem('pw-tree-collapsed',JSON.stringify([...set]));
}
function updateSbDevDot(did,st){const d=document.getElementById(`sbdd-${did}`);if(d)d.className=`dev-sdot ${st}`;}
function updateSbSensorDot(s){const d=document.getElementById(`sbsd-${s.device_id}_${s.sensor_id}`);if(d)d.className=`s-sdot ${s.alive===true?'up':s.alive===false?'down':''}`;}
function scrollToDev(did){openDevWin(did);}

// ── Device actions ───────────────────────────────────────────────
async function startDev(did){await api('POST',`/api/device/${did}/start`);toast('Monitoring started','ok')}
async function stopDev(did) {await api('POST',`/api/device/${did}/stop`); toast('Monitoring stopped','info')}
async function delDev(did){
  if(!confirm('Delete device and all its sensors?'))return;
  await api('DELETE',`/api/device/${did}`);
  document.getElementById(`dp-${did}`)?.remove();
  closeM('dwo');
  delete S.devices[did];
  Object.keys(S.sensors).filter(k=>k.startsWith(did+'/')).forEach(k=>{delete S.sensors[k];delete S.charts[k];delete S.logs[k];});
  pruneEmptyGroups();
  refreshGroupCounts();
  renderSidebar();updatePills();
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
      <div class="scan-spin">&#8635; Scanning 15 services&hellip;</div>
    </div>
    <div class="mfoot" style="justify-content:space-between">
      <button class="btn-s" onclick="closeM('mdscan')">Close</button>
      <button class="btn-p" id="btn-add-scanned" style="display:none"
              onclick="addScannedSensors()">Add Selected</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  try{
    const r=await api('POST',`/api/device/${did}/scan`,{});
    _scanServices=r.services||[];
    _renderScanResults(did,host,_scanServices);
  }catch(e){
    const b=document.getElementById('scan-body');
    if(b)b.innerHTML=`<div class="scan-spin" style="color:var(--down)">Scan failed: ${esc(String(e))}</div>`;
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
  // Keep S.devices in sync so reopening device window doesn't re-render the deleted tile
  if(S.devices[did]) S.devices[did].sensors=(S.devices[did].sensors||[]).filter(s=>s.sensor_id!==sid);
  recalcDevStatus(did);updatePills();
  const previewEl=document.getElementById(`dcsnr-${did}`);
  if(previewEl) previewEl.innerHTML=sSnrPreview(did);
  const cntEl=document.querySelector(`#sbn-${did} .dev-cnt`);
  if(cntEl) cntEl.textContent=Object.values(S.sensors).filter(s=>s.device_id===did).length;
  toast('Sensor removed','info');
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
function openDetail(did,sid,initialTab){
  const key=`${did}/${sid}`;
  const s=S.sensors[key];if(!s)return;
  const tgt=s.stype==='http'?(s.url||s.host):s.stype==='tcp'?`${s.host}:${s.port}`:s.stype==='snmp'?`${s.host}:${s.port||161} · ${s.snmp_community} · OID ${s.snmp_oid}`:s.stype==='dns'?`${s.dns_query||s.host} · ${s.dns_record_type||'A'}${s.dns_server?' via '+s.dns_server:''}`:s.host;
  closeM('dm');
  const o=document.createElement('div');
  o.className='dmo';o.id='dm';
  o.onclick=e=>{if(e.target===o)closeM('dm')};
  o.innerHTML=`
  <div class="dmbox">
    <div class="dm-hd">
      <div class="dm-tbdg ${s.stype}">${sIco(s.stype)} ${s.stype.toUpperCase()}</div>
      <div class="dm-ttl">${esc(s.name)}</div>
      <div class="dm-tgt">${esc(tgt)}</div>
      <div style="display:flex;gap:6px;margin-left:auto">
        <button class="dp-btn s" onclick="startDev('${did}')">▶</button>
        <button class="dp-btn" onclick="openEditSensor('${did}','${sid}')">✎ Edit</button>
        <button class="dp-btn d" onclick="delSensor('${did}','${sid}');closeM('dm')">✕ Remove</button>
        <button class="mclose" onclick="closeM('dm')">✕</button>
      </div>
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
          <span>${s.stype==='snmp'?'Poll history':s.stype==='tls'?'Check history':'Latency'} — last ${s.history?.length||0} samples</span>
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
          <button class="dm-hist-pill" data-m="525600" onclick="dmHistPick('${did}','${sid}',525600)">1y</button>
        </div>
        <span id="dm-hist-stats-${did}-${sid}" class="dm-hist-stats"></span>
      </div>
      <div class="dm-kpi-bar" id="kpi-${did}-${sid}">
        <div class="dm-kpi-item" id="kpi-avail-${did}-${sid}">Avail<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-avg-${did}-${sid}">Avg ms<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-min-${did}-${sid}">Min ms<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-max-${did}-${sid}">Max ms<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-loss-${did}-${sid}">Loss %<br><span>—</span></div>
        <div class="dm-kpi-item" id="kpi-jitter-${did}-${sid}">Jitter<br><span>—</span></div>
      </div>
      <div class="dm-metric-toggles">
        <label><input type="checkbox" id="tog-avg-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> Avg</label>
        <label><input type="checkbox" id="tog-band-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> Min/Max</label>
        <label><input type="checkbox" id="tog-loss-${did}-${sid}" checked onchange="dmHistRedraw('${did}','${sid}')"> Loss%</label>
        <label><input type="checkbox" id="tog-jitter-${did}-${sid}" onchange="dmHistRedraw('${did}','${sid}')"> Jitter</label>
        <button class="dm-ar-btn" id="ar-${did}-${sid}" onclick="dmToggleAutoRefresh('${did}','${sid}')">Auto</button>
      </div>
      <div style="position:relative">
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
  if (statsEl) statsEl.textContent = 'Loading…';
  const dynamicLimit = Math.min(10000, Math.max(500, Math.round(minutes * 60 / 10)));
  const [hr, sr] = await Promise.all([
    fetch(`/api/device/${did}/sensor/${sid}/history?minutes=${minutes}&limit=${dynamicLimit}`)
      .then(r => r.json()).catch(() => ({})),
    fetch(`/api/device/${did}/sensor/${sid}/summary?minutes=${minutes}`)
      .then(r => r.json()).catch(() => ({})),
  ]);
  const samples = hr.samples || [];
  const summary = sr.summary || [];
  _histCache[`${did}/${sid}`] = { samples, summary, minutes };
  _buildKpiBar(summary, did, sid);
  _setupHistTooltip(canvas, summary, did, sid, minutes);
  // canvas may have been re-looked-up inside _setupHistTooltip; re-fetch by id to be safe
  const c = document.getElementById(`dm-hist-canvas-${did}-${sid}`) || canvas;
  _drawHistCanvas(c, statsEl, did, sid, summary, samples, minutes);
  if (sumEl) _buildSummaryTable(sumEl, summary, minutes);
}

function dmHistRedraw(did, sid) {
  const cache = _histCache[`${did}/${sid}`];
  if (!cache) return;
  const canvas  = document.getElementById(`dm-hist-canvas-${did}-${sid}`);
  const statsEl = document.getElementById(`dm-hist-stats-${did}-${sid}`);
  if (canvas) _drawHistCanvas(canvas, statsEl, did, sid, cache.summary, cache.samples, cache.minutes);
}

function _buildKpiBar(summary, did, sid) {
  if (!summary.length) return;
  let totalOk = 0, totalFail = 0, wsum = 0, wcnt = 0;
  let minMs = Infinity, maxMs = -Infinity, lossSum = 0, jitterSum = 0;
  for (const r of summary) {
    totalOk   += r.ok;
    totalFail += r.fail;
    if (r.avg_ms != null) { wsum += r.avg_ms * r.ok; wcnt += r.ok; }
    if (r.min_ms != null) minMs = Math.min(minMs, r.min_ms);
    if (r.max_ms != null) maxMs = Math.max(maxMs, r.max_ms);
    lossSum   += r.loss_pct   || 0;
    jitterSum += r.jitter_ms  || 0;
  }
  const total = totalOk + totalFail;
  const avail = total > 0 ? (totalOk / total * 100) : 100;
  const avg   = wcnt > 0 ? Math.round(wsum / wcnt * 10) / 10 : null;
  const _set = (id, val) => {
    const el = document.getElementById(id);
    if (el) { const sp = el.querySelector('span'); if (sp) sp.textContent = val; }
  };
  _set(`kpi-avail-${did}-${sid}`,  avail.toFixed(1) + '%');
  _set(`kpi-avg-${did}-${sid}`,    avg != null ? avg + 'ms' : '—');
  _set(`kpi-min-${did}-${sid}`,    minMs !== Infinity  ? minMs + 'ms' : '—');
  _set(`kpi-max-${did}-${sid}`,    maxMs !== -Infinity ? maxMs + 'ms' : '—');
  const avgLoss = lossSum / summary.length;
  _set(`kpi-loss-${did}-${sid}`,   avgLoss.toFixed(1) + '%');
  _set(`kpi-jitter-${did}-${sid}`, (jitterSum / summary.length).toFixed(1) + 'ms');
  // Color-variant classes
  const _kpiColor = (id, cls) => {
    const el = document.getElementById(id);
    if (el) el.className = 'dm-kpi-item' + (cls ? ' ' + cls : '');
  };
  _kpiColor(`kpi-avail-${did}-${sid}`, avail < 80 ? 'dm-kpi-crit' : avail < 95 ? 'dm-kpi-warn' : avail === 100 ? 'dm-kpi-good' : '');
  _kpiColor(`kpi-loss-${did}-${sid}`,  avgLoss >= 20 ? 'dm-kpi-crit' : avgLoss >= 5 ? 'dm-kpi-warn' : avgLoss === 0 ? 'dm-kpi-good' : '');
  _kpiColor(`kpi-jitter-${did}-${sid}`, 'dm-kpi-info');
}

function _setupHistTooltip(canvas, summary, did, sid, minutes) {
  const tip = document.getElementById(`tip-${did}-${sid}`);
  if (!tip || !canvas) return;
  if (canvas._tipAC) canvas._tipAC.abort();
  const ac = new AbortController();
  canvas._tipAC = ac;
  const { signal } = ac;
  const LEFT = 52, RIGHT = 48, BOT = 28, TOP = 12;
  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const mx = (e.clientX - rect.left) * scaleX;
    const plotW = canvas.width - LEFT - RIGHT;
    if (mx < LEFT || mx > canvas.width - RIGHT) { tip.style.display = 'none'; return; }
    const hoverTs = (Date.now() / 1000 - minutes * 60) + (mx - LEFT) / plotW * (minutes * 60);
    let nearest = null, bestDist = Infinity;
    for (const r of summary) {
      const d = Math.abs(r.ts + 1800 - hoverTs);
      if (d < bestDist) { bestDist = d; nearest = r; }
    }
    if (!nearest) { tip.classList.remove('tip-visible'); return; }
    const d = new Date(nearest.ts * 1000);
    const lbl = d.toLocaleDateString([], {month:'short',day:'numeric'}) + ' ' +
                d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    const isDown = nearest.ok === 0 && nearest.fail > 0;
    const statusColor = isDown ? 'var(--down)' : 'var(--up)';
    const statusText  = isDown ? '● DOWN' : '● UP';
    const lossColor   = (nearest.loss_pct || 0) > 5 ? 'var(--warn)' : 'var(--text)';
    tip.innerHTML =
      `<div style="font-size:.82rem;font-weight:600;color:var(--text);margin-bottom:7px;` +
      `padding-bottom:6px;border-bottom:1px solid rgba(255,255,255,.1)">${lbl}</div>` +
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
      `<div style="margin-top:7px;padding-top:5px;border-top:1px solid rgba(255,255,255,.1);` +
      `font-size:.76rem;color:${statusColor}">${statusText}</div>`;
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
    cctx.strokeStyle = 'rgba(255,255,255,.22)';
    cctx.lineWidth = 1;
    cctx.setLineDash([3, 3]);
    cctx.beginPath(); cctx.moveTo(mx, TOP); cctx.lineTo(mx, canvas.height - BOT); cctx.stroke();
    cctx.setLineDash([]);
    // Highlight circle at nearest avg point
    if (nearest.avg_ms != null) {
      const cache = _histCache[`${did}/${sid}`];
      if (cache) {
        const { summary: _s, samples: _sp } = cache;
        const _sMaxMs = _s.reduce((m, r) => Math.max(m, r.max_ms || 0), 0);
        const _msV = _sp.filter(p => p.ok && p.ms != null).map(p => p.ms);
        const _rawMax = Math.max(_sMaxMs, _msV.length ? Math.max(..._msV) : 0);
        const _sen2 = S.sensors[`${did}/${sid}`];
        const _maxY = Math.max((_sen2?.crit_ms || 0) * 1.1, _rawMax * 1.2, 10);
        const _plotH = canvas.height - BOT - TOP;
        const _pyOf = ms => (canvas.height - BOT) - (ms / _maxY) * _plotH;
        const _px = LEFT + (nearest.ts + 1800 - (Date.now() / 1000 - minutes * 60)) / (minutes * 60) * (canvas.width - LEFT - RIGHT);
        const _py = _pyOf(nearest.avg_ms);
        cctx.beginPath(); cctx.arc(_px, _py, 4.5, 0, Math.PI * 2);
        cctx.fillStyle = '#3b9eff'; cctx.fill();
        cctx.strokeStyle = 'rgba(255,255,255,.85)'; cctx.lineWidth = 1.5; cctx.stroke();
      }
    }
  }, { signal });
  canvas.addEventListener('mouseleave', () => {
    tip.classList.remove('tip-visible');
    dmHistRedraw(did, sid);
  }, { signal });
}

function _drawHistCanvas(canvas, statsEl, did, sid, summary, samples, minutes) {
  if (!canvas) return;
  canvas.width = canvas.offsetWidth || 660;
  const W = canvas.width, H = canvas.height || 320;
  const LEFT = 52, RIGHT = 48, BOT = 28, TOP = 12;
  const plotW = W - LEFT - RIGHT;
  const plotH = H - BOT - TOP;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
  bgGrad.addColorStop(0, '#0d1520');
  bgGrad.addColorStop(1, '#060c16');
  ctx.fillStyle = bgGrad; ctx.fillRect(0, 0, W, H);

  if (!samples.length && !summary.length) {
    ctx.fillStyle = '#8b949e'; ctx.font = '13px Inter,sans-serif'; ctx.textAlign = 'center';
    ctx.fillText('No data for this period', W / 2, H / 2);
    if (statsEl) statsEl.textContent = 'No data';
    return;
  }

  // Read toggle states
  const togAvg    = document.getElementById(`tog-avg-${did}-${sid}`)?.checked ?? true;
  const togBand   = document.getElementById(`tog-band-${did}-${sid}`)?.checked ?? true;
  const togLoss   = document.getElementById(`tog-loss-${did}-${sid}`)?.checked ?? true;
  const togJitter = document.getElementById(`tog-jitter-${did}-${sid}`)?.checked ?? false;

  const windowStart = Date.now() / 1000 - minutes * 60;
  const tsRange = minutes * 60;
  const windowEnd = windowStart + tsRange;
  const xOf = ts => LEFT + (ts - windowStart) / tsRange * plotW;

  // Y scales
  const msVals = samples.filter(p => p.ok && p.ms != null).map(p => p.ms);
  const summaryMaxMs = summary.reduce((m, r) => Math.max(m, r.max_ms || 0), 0);
  const rawMax = Math.max(summaryMaxMs, msVals.length ? Math.max(...msVals) : 0);
  const _sen = S.sensors[`${did}/${sid}`];
  const maxY = Math.max((_sen?.crit_ms || 0) * 1.1, rawMax * 1.2, 10);
  const yOf   = ms  => (H - BOT) - (ms / maxY) * plotH;
  const yLoss = pct => (H - BOT) - (pct / 100) * plotH;

  // ── 1. Downtime spans ─────────────────────────────────────────
  ctx.fillStyle = 'rgba(248,81,73,.12)';
  for (const r of summary) {
    if (r.ok === 0 && r.fail > 0) {
      const x1 = Math.max(LEFT, xOf(r.ts));
      const x2 = Math.min(W - RIGHT, xOf(r.ts + 3600));
      if (x2 > x1) {
        ctx.fillRect(x1, TOP, x2 - x1, plotH);
        // Thin top accent line
        ctx.strokeStyle = 'rgba(248,81,73,.4)';
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x1, TOP + 1); ctx.lineTo(x2, TOP + 1); ctx.stroke();
      }
    }
  }

  // ── 2. Min/Max band ───────────────────────────────────────────
  if (togBand) {
    const bandPts = summary.filter(r => r.min_ms != null && r.max_ms != null);
    if (bandPts.length > 1) {
      ctx.beginPath();
      bandPts.forEach((r, i) => {
        const x = xOf(r.ts + 1800);
        if (i === 0) ctx.moveTo(x, yOf(r.max_ms));
        else ctx.lineTo(x, yOf(r.max_ms));
      });
      for (let i = bandPts.length - 1; i >= 0; i--)
        ctx.lineTo(xOf(bandPts[i].ts + 1800), yOf(bandPts[i].min_ms));
      ctx.closePath();
      ctx.fillStyle = 'rgba(47,129,247,.10)';
      ctx.fill();
    }
  }

  // ── 3. Loss% bars ─────────────────────────────────────────────
  if (togLoss && summary.length) {
    const barW = Math.max(2, plotW / summary.length * 0.65);
    for (const r of summary) {
      if ((r.loss_pct || 0) === 0) continue;
      const x = xOf(r.ts + 1800) - barW / 2;
      const yTop = yLoss(r.loss_pct);
      ctx.fillStyle = 'rgba(240,165,0,.45)';
      ctx.shadowColor = 'rgba(240,165,0,.4)';
      ctx.shadowBlur = 5;
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

  // ── 4. Jitter line ────────────────────────────────────────────
  if (togJitter) {
    const jPts = summary.filter(r => (r.jitter_ms || 0) > 0);
    if (jPts.length > 1) {
      ctx.beginPath();
      jPts.forEach((r, i) => {
        const x = xOf(r.ts + 1800), y = yOf(r.jitter_ms);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = 'rgba(188,130,255,.7)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // ── 5. Avg latency line + gradient fill ───────────────────────
  if (togAvg) {
    const pts = samples.filter(p => p.ok && p.ms != null).map(p => ({ x: xOf(p.ts), y: yOf(p.ms) }));
    if (pts.length > 1) {
      const g = ctx.createLinearGradient(0, TOP, 0, H - BOT);
      g.addColorStop(0,    'rgba(60,140,255,.38)');
      g.addColorStop(0.55, 'rgba(47,129,247,.12)');
      g.addColorStop(1,    'rgba(47,129,247,.02)');
      ctx.beginPath();
      ctx.moveTo(pts[0].x, H - BOT);
      // bezier fill path (follows smooth curve)
      ctx.lineTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length - 1; i++) {
        const cpx = (pts[i].x + pts[i+1].x) / 2;
        const cpy = (pts[i].y + pts[i+1].y) / 2;
        ctx.quadraticCurveTo(pts[i].x, pts[i].y, cpx, cpy);
      }
      ctx.lineTo(pts[pts.length-1].x, pts[pts.length-1].y);
      ctx.lineTo(pts[pts.length - 1].x, H - BOT);
      ctx.closePath();
      ctx.fillStyle = g; ctx.fill();
      // Smooth bezier line
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      for (let i = 1; i < pts.length - 1; i++) {
        const cpx = (pts[i].x + pts[i+1].x) / 2;
        const cpy = (pts[i].y + pts[i+1].y) / 2;
        ctx.quadraticCurveTo(pts[i].x, pts[i].y, cpx, cpy);
      }
      ctx.lineTo(pts[pts.length-1].x, pts[pts.length-1].y);
      ctx.strokeStyle = '#3b9eff'; ctx.lineWidth = 2; ctx.stroke();
    }
  }

  // ── 6. Threshold lines ────────────────────────────────────────
  ctx.font = '10px Inter,sans-serif';
  if (_sen?.warn_ms > 0 && _sen.warn_ms < maxY) {
    const wy = yOf(_sen.warn_ms);
    ctx.strokeStyle = 'rgba(240,165,0,.5)'; ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(LEFT, wy); ctx.lineTo(W - RIGHT, wy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(240,165,0,.8)'; ctx.textAlign = 'left';
    ctx.fillText('warn ' + _sen.warn_ms + 'ms', LEFT + 4, wy - 3);
  }
  if (_sen?.crit_ms > 0 && _sen.crit_ms < maxY) {
    const cy = yOf(_sen.crit_ms);
    ctx.strokeStyle = 'rgba(248,81,73,.5)'; ctx.lineWidth = 1;
    ctx.setLineDash([5, 4]);
    ctx.beginPath(); ctx.moveTo(LEFT, cy); ctx.lineTo(W - RIGHT, cy); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(248,81,73,.8)'; ctx.textAlign = 'left';
    ctx.fillText('crit ' + _sen.crit_ms + 'ms', LEFT + 4, cy - 3);
  }

  // ── 7. Failed ticks ───────────────────────────────────────────
  ctx.strokeStyle = 'rgba(248,81,73,.6)'; ctx.lineWidth = 1.5;
  samples.filter(p => !p.ok).forEach(p => {
    const x = xOf(p.ts);
    ctx.beginPath(); ctx.moveTo(x, H - BOT); ctx.lineTo(x, H - BOT - 10); ctx.stroke();
  });

  // ── 8. Y-axis gridlines + labels ─────────────────────────────
  ctx.lineWidth = 1;
  ctx.font = '10px Inter,sans-serif';
  [0.25, 0.5, 0.75, 1].forEach(f => {
    const y = (H - BOT) - f * plotH;
    const msLbl = Math.round(maxY * f);
    ctx.strokeStyle = 'rgba(255,255,255,.04)';
    ctx.beginPath(); ctx.moveTo(LEFT, y); ctx.lineTo(W - RIGHT, y); ctx.stroke();
    ctx.fillStyle = 'rgba(139,148,158,.75)'; ctx.textAlign = 'right';
    ctx.fillText(msLbl >= 1000 ? (msLbl / 1000).toFixed(1) + 's' : msLbl + 'ms', LEFT - 4, y + 3);
    if (togLoss) {
      ctx.fillStyle = 'rgba(240,165,0,.55)'; ctx.textAlign = 'left';
      ctx.fillText(Math.round(100 * f) + '%', W - RIGHT + 4, y + 3);
    }
  });
  ctx.fillStyle = 'rgba(139,148,158,.4)'; ctx.textAlign = 'right';
  ctx.fillText('0', LEFT - 4, H - BOT + 3);
  ctx.strokeStyle = 'rgba(255,255,255,.1)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(LEFT, TOP); ctx.lineTo(LEFT, H - BOT); ctx.stroke();
  if (togLoss) {
    ctx.strokeStyle = 'rgba(240,165,0,.18)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(W - RIGHT, TOP); ctx.lineTo(W - RIGHT, H - BOT); ctx.stroke();
  }

  // ── 9. Time labels (X-axis) ───────────────────────────────────
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
    ctx.strokeStyle = 'rgba(255,255,255,.04)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, TOP); ctx.lineTo(x, H - BOT); ctx.stroke();
    const d = new Date(ts * 1000);
    const lbl = _gInt < 86400
      ? d.toLocaleDateString([], {month:'short',day:'numeric'}) + ' ' + d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})
      : d.toLocaleDateString([], {month:'short',day:'numeric'});
    ctx.fillStyle = 'rgba(139,148,158,.65)'; ctx.font = '9px Inter,sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(lbl, x, H - 4);
  }

  // ── Stats bar ─────────────────────────────────────────────────
  if (statsEl) {
    const total = samples.length, okCt = samples.filter(p => p.ok).length;
    const upPct = total ? Math.round(okCt / total * 1000) / 10 : 0;
    const avgMs = msVals.length ? Math.round(msVals.reduce((a, b) => a + b, 0) / msVals.length) : null;
    const minMs = msVals.length ? Math.round(Math.min(...msVals)) : null;
    statsEl.textContent =
      `${total} probes · ${upPct}% up · avg ${avgMs != null ? avgMs + 'ms' : '—'} · min ${minMs != null ? minMs + 'ms' : '—'} · max ${Math.round(rawMax)}ms`;
  }
}

function _buildSummaryTable(sumEl, summary, minutes) {
  if (!sumEl) return;
  if (!summary.length) { sumEl.innerHTML = ''; return; }
  let _bSec;
  if      (minutes <= 5760)   _bSec = 3600;
  else if (minutes <= 64800)  _bSec = 86400;
  else if (minutes <= 288000) _bSec = 7 * 86400;
  else                        _bSec = 30 * 86400;
  const _tzOff = new Date().getTimezoneOffset() * 60;
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
    const rowCls = lossPct > 20 ? 'hrow-crit' : lossPct > 5 ? 'hrow-warn' : '';
    return `<tr class="${rowCls}">
      <td>${lbl}</td>
      <td style="color:var(--up)">${_b.ok}↑</td>
      <td style="color:${_b.fail?'var(--down)':'var(--text3)'}">${_b.fail}↓</td>
      <td style="color:${upPct<100?'var(--warn)':'var(--text2)'}">${upPct}%</td>
      <td>${avg!=null?avg+'ms':'—'}</td>
      <td>${minMs!=null?minMs+'ms':'—'}</td>
      <td>${maxMs!=null?maxMs+'ms':'—'}</td>
      <td style="color:${lossPct>5?'var(--warn)':'var(--text2)'}">${loss}%</td>
      <td style="color:rgba(188,130,255,.85)">${jitter}ms</td>
    </tr>`;
  }).join('');
  sumEl.innerHTML = `<table class="dm-hist-tbl">
    <thead><tr><th>Time</th><th>Up</th><th>Down</th><th>Avail</th><th>Avg</th><th>Min</th><th>Max</th><th>Loss</th><th>Jitter</th></tr></thead>
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
    if (btn) { btn.textContent = 'Auto'; btn.classList.remove('active'); }
  } else {
    S._arTimers[key] = setInterval(() => dmHistReload(did, sid), 30000);
    if (btn) { btn.textContent = 'Auto ●'; btn.classList.add('active'); }
  }
}

function _dmStopAR(did, sid) {
  if (!S._arTimers) return;
  const key = `${did}/${sid}`;
  if (S._arTimers[key]) { clearInterval(S._arTimers[key]); delete S._arTimers[key]; }
}

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
  if(k==='last'){
    if(isSnmp||isDns) return s.alive===false?'FAIL':(s.last_value||s.last_detail||'—');
    if(s.stype==='tls') return s.alive===false?'FAIL':(s.last_value!=null?s.last_value+'d':'—');
    return s.last_ms!==null&&s.last_ms!==undefined?`${s.last_ms}ms`:(s.alive===false?'DOWN':'—');
  }
  if(k==='avg') return (s.stype==='snmp'||s.stype==='tls')?'—':(s.avg_ms?`${s.avg_ms}ms`:'—');
  if(k==='min') return (s.stype==='snmp'||s.stype==='tls')?'—':(s.min_ms?`${s.min_ms}ms`:'—');
  if(k==='max') return (s.stype==='snmp'||s.stype==='tls')?'—':(s.max_ms?`${s.max_ms}ms`:'—');
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
  ctx.strokeStyle='rgba(255,255,255,0.04)';ctx.lineWidth=1;ctx.setLineDash([3,4]);
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
      ctx.strokeStyle='rgba(231,76,60,.7)';ctx.lineWidth=1.5;
      ctx.beginPath();ctx.moveTo(cx-5,cy-5);ctx.lineTo(cx+5,cy+5);ctx.stroke();
      ctx.beginPath();ctx.moveTo(cx+5,cy-5);ctx.lineTo(cx-5,cy+5);ctx.stroke();
    }
  });
  if(pts.length<2)return;
  const g=ctx.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'rgba(46,204,113,.18)');g.addColorStop(1,'rgba(46,204,113,0)');
  ctx.beginPath();ctx.moveTo(pts[0].x,H);pts.forEach(p=>ctx.lineTo(p.x,p.y));
  ctx.lineTo(pts[pts.length-1].x,H);ctx.closePath();ctx.fillStyle=g;ctx.fill();
  ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.strokeStyle='rgba(46,204,113,.2)';ctx.lineWidth=6;ctx.stroke();
  ctx.beginPath();pts.forEach((p,i)=>i===0?ctx.moveTo(p.x,p.y):ctx.lineTo(p.x,p.y));
  ctx.strokeStyle='#2ecc71';ctx.lineWidth=2;ctx.stroke();
  const lp=pts[pts.length-1];
  ctx.beginPath();ctx.arc(lp.x,lp.y,4,0,Math.PI*2);ctx.fillStyle='#2ecc71';ctx.fill();
}