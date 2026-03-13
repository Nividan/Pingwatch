// ── IMPORT / EXPORT ───────────────────────────────────────────────────────────
async function exportDb(){
  const btn=document.querySelector('[onclick="exportDb()"]');
  if(btn){btn.disabled=true;btn.textContent='Preparing…';}
  try{
    const r=await fetch('/api/db/export');
    if(!r.ok){toast('Export failed: '+r.status,'err');return;}
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const a=document.createElement('a');
    a.href=url;
    const d=new Date();
    const stamp=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    a.download=`pingwatch-backup-${stamp}.db`;
    a.click();
    URL.revokeObjectURL(url);
    toast('Backup downloaded','ok');
  }catch(e){
    toast('Export error: '+e.message,'err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='⬇ Download Backup';}
  }
}

function _importConfirm(filename, onConfirm){
  const ov=document.createElement('div');
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML=`<div style="background:var(--card,#1e2533);border:1px solid var(--border,#2a3448);border-radius:10px;padding:28px 32px;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:15px;font-weight:600;margin-bottom:10px;color:var(--text,#e0e6f0);">Restore from Backup</div>
    <div style="font-size:13px;color:var(--text2,#8899aa);margin-bottom:20px;">Import <b style="color:var(--text,#e0e6f0)">${filename}</b>?<br><br>This will <span style="color:var(--down,#ff4444);font-weight:600;">REPLACE ALL current data</span> and restart the server.</div>
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
  inp.type='file'; inp.accept='.db';
  inp.style.cssText='position:fixed;top:-9999px;left:-9999px;opacity:0;';
  document.body.appendChild(inp);
  const _cleanup=()=>{ try{document.body.removeChild(inp);}catch(_){} };
  inp.addEventListener('cancel', _cleanup);
  inp.onchange=()=>{
    _cleanup();
    const file=inp.files[0]; if(!file) return;
    _importConfirm(file.name, async()=>{
      const statusEl=document.getElementById('db-import-status');
      if(statusEl){statusEl.style.color='var(--text3)';statusEl.textContent='Reading file…';}
      try{
        const buf=await file.arrayBuffer();
        const bytes=new Uint8Array(buf);
        let binary='';
        for(let i=0;i<bytes.length;i+=8192) binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
        const b64=btoa(binary);
        if(statusEl) statusEl.textContent='Uploading…';
        const r=await api('POST','/api/db/import',{data:b64});
        if(r&&r.ok){
          if(statusEl){statusEl.style.color='var(--up)';statusEl.textContent=r.msg||'Imported — restarting…';}
          toast(r.msg||'Imported — server restarting…','ok');
          setTimeout(()=>location.reload(),4000);
        }else{
          if(statusEl){statusEl.style.color='var(--down)';statusEl.textContent=(r&&r.error)||'Import failed';}
          toast((r&&r.error)||'Import failed','err');
        }
      }catch(e){
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
