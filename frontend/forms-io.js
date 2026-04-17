// ── IMPORT / EXPORT ───────────────────────────────────────────────────────────
async function _exportFile(apiPath, defaultName, btnEl, btnLabel){
  if(btnEl){btnEl.disabled=true;btnEl.textContent='Preparing…';}
  try{
    const r=await fetch(apiPath);
    if(!r.ok){toast('Export failed: '+r.status,'err');return;}
    const cd=r.headers.get('Content-Disposition')||'';
    const m=cd.match(/filename\*?=(?:UTF-8'')?"?([^"";]+)"?/i);
    const serverName=m?decodeURIComponent(m[1]):'';
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    a.download=serverName||defaultName;
    a.click();
    URL.revokeObjectURL(url);
    toast('Downloaded','ok');
  }catch(e){
    toast('Export error: '+e.message,'err');
  }finally{
    if(btnEl){btnEl.disabled=false;btnEl.textContent=btnLabel;}
  }
}

async function exportDb(){
  const btn=document.querySelector('[onclick="exportDb()"]');
  const d=new Date();
  const stamp=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  await _exportFile('/api/db/export', `pingwatch-main-${stamp}.db`, btn, '⬇ Download Main DB');
}

async function exportLogsDb(){
  const btn=document.querySelector('[onclick="exportLogsDb()"]');
  const d=new Date();
  const stamp=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  await _exportFile('/api/db/export/logs', `pingwatch-logs-${stamp}.db`, btn, '⬇ Download Logs DB');
}

async function exportBundle(){
  const btn=document.querySelector('[onclick="exportBundle()"]');
  const d=new Date();
  const stamp=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  await _exportFile('/api/db/export/bundle', `pingwatch-bundle-${stamp}.zip`, btn, '⬇ Export Full Bundle (ZIP)');
}

function _importConfirm(filename, onConfirm){
  const ov=document.createElement('div');
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML=`<div style="background:var(--card,#1e2533);border:1px solid var(--border,#2a3448);border-radius:10px;padding:28px 32px;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:15px;font-weight:600;margin-bottom:10px;color:var(--text,#e0e6f0);">Restore from Backup</div>
    <div style="font-size:13px;color:var(--text2,#8899aa);margin-bottom:20px;">Import <b style="color:var(--text,#e0e6f0)">${filename}</b>?<br><br>This will <span style="color:var(--down,#ff4444);font-weight:600;">replace the imported database(s)</span> and restart the server.<br><span style="color:var(--text2,#8899aa);font-size:12px">Accepts: Main DB, Logs DB, or full bundle (.zip).</span></div>
    <div style="display:flex;gap:10px;justify-content:flex-end;">
      <button id="_imp_cancel" style="padding:8px 20px;border-radius:6px;border:1px solid var(--border,#2a3448);background:transparent;color:var(--text2,#8899aa);cursor:pointer;font-size:13px;">Cancel</button>
      <button id="_imp_ok" style="padding:8px 20px;border-radius:6px;border:none;background:var(--down,#ff4444);color:#fff;cursor:pointer;font-weight:600;font-size:13px;">Yes, Import</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  const close=()=>document.body.removeChild(ov);
  ov.querySelector('#_imp_cancel').onclick=close;
  ov.querySelector('#_imp_ok').onclick=()=>{ close(); onConfirm(); };
}

async function importDb(){
  const inp=document.createElement('input');
  inp.type='file'; inp.accept='.db,.sqlite,.zip';
  inp.style.cssText='position:fixed;top:-9999px;left:-9999px;opacity:0;';
  document.body.appendChild(inp);
  const _cleanup=()=>{ try{document.body.removeChild(inp);}catch(_){} };
  inp.addEventListener('cancel', _cleanup);
  inp.onchange=()=>{
    _cleanup();
    const file=inp.files[0]; if(!file) return;
    _importConfirm(file.name, async()=>{
      const statusEl=document.getElementById('db-import-status');
      if(statusEl){statusEl.style.color='var(--text3)';statusEl.textContent='Uploading…';}
      try{
        // Send the raw file bytes directly — avoids base64 encoding the entire
        // file in memory (which previously required ~3× the file size in RAM).
        const resp=await fetch('/api/db/import',{
          method:'POST',
          headers:{'Content-Type':'application/octet-stream'},
          body:file,
        });
        const r=resp.ok||resp.status===200 ? await resp.json().catch(()=>({ok:true})) : null;
        if(r&&r.ok){
          if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent=r.msg||'Imported — restarting…';}
          toast(r.msg||'Imported — server restarting…','ok');
          setTimeout(()=>location.reload(),4000);
        }else{
          const err=(r&&r.error)||('HTTP '+resp.status);
          if(statusEl){statusEl.style.color='var(--down)';statusEl.textContent=err;}
          toast('Import failed: '+err,'err');
        }
      }catch(e){
        // A network error here most likely means the server already restarted.
        const isNetErr=!e.name||e.name==='TypeError'||e.name==='NetworkError';
        if(isNetErr){
          if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent='Imported — restarting…';}
          toast('Imported — server restarting…','ok');
          setTimeout(()=>location.reload(),4000);
        }else{
          if(statusEl){statusEl.style.color='var(--down)';statusEl.textContent='Import error: '+e.message;}
          toast('Import error: '+e.message,'err');
        }
      }
    });
  };
  inp.click();
}
