// ── IMPORT / EXPORT ───────────────────────────────────────────────────────────
async function _exportFile(apiPath, defaultName, btnEl, btnLabel, headers){
  if(btnEl){btnEl.disabled=true;btnEl.textContent='Preparing…';}
  try{
    const r=await fetch(apiPath, headers ? {headers} : undefined);
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
  const pass=await _promptPassphrase('Encrypt Backup Bundle',
    'Enter a passphrase to encrypt the bundle (recommended — it carries the encryption key, TLS certs and config). Leave blank to use the server\'s configured backup passphrase, or to export unencrypted if none is set.<br><br>Store the passphrase in your vault: it is required to restore and is never saved inside the bundle.',
    {allowBlank:true, okLabel:'Export'});
  if(pass===null) return; // cancelled
  const d=new Date();
  const stamp=d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
  const headers=pass?{'X-Bundle-Passphrase':pass}:undefined;
  await _exportFile('/api/db/export/bundle', `pingwatch-bundle-${stamp}.zip`, btn, '⬇ Export Full Bundle', headers);
}

// Lightweight passphrase modal. Resolves to the entered string, '' for an
// allowed-blank submit, or null when cancelled.
function _promptPassphrase(title, messageHtml, opts){
  opts=opts||{};
  return new Promise(resolve=>{
    const ov=document.createElement('div');
    ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:10000;display:flex;align-items:center;justify-content:center;';
    ov.innerHTML=`<div style="background:var(--card,#1e2533);border:1px solid var(--border,#2a3448);border-radius:10px;padding:26px 30px;max-width:440px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
      <div style="font-size:15px;font-weight:600;margin-bottom:10px;color:var(--text,#e0e6f0);">${title}</div>
      <div style="font-size:13px;color:var(--text2,#8899aa);margin-bottom:16px;line-height:1.5">${messageHtml||''}</div>
      <input type="password" id="_pp_input" autocomplete="new-password" placeholder="Passphrase" style="width:100%;box-sizing:border-box;padding:9px 12px;border-radius:6px;border:1px solid var(--border,#2a3448);background:var(--bg2,#11161f);color:var(--text,#e0e6f0);font-size:13px;margin-bottom:18px"/>
      <div style="display:flex;gap:10px;justify-content:flex-end;">
        <button id="_pp_cancel" style="padding:8px 20px;border-radius:6px;border:1px solid var(--border,#2a3448);background:transparent;color:var(--text2,#8899aa);cursor:pointer;font-size:13px;">Cancel</button>
        <button id="_pp_ok" style="padding:8px 20px;border-radius:6px;border:none;background:var(--accent,#2f81f7);color:#fff;cursor:pointer;font-weight:600;font-size:13px;">${opts.okLabel||'OK'}</button>
      </div>
    </div>`;
    document.body.appendChild(ov);
    const inp=ov.querySelector('#_pp_input');
    const close=v=>{ try{document.body.removeChild(ov);}catch(_){ } resolve(v); };
    inp.focus();
    const submit=()=>{ const v=inp.value; if(!v && !opts.allowBlank){ inp.style.borderColor='var(--down,#ff4444)'; return; } close(v); };
    ov.querySelector('#_pp_cancel').onclick=()=>close(null);
    ov.querySelector('#_pp_ok').onclick=submit;
    inp.addEventListener('keydown',e=>{ if(e.key==='Enter') submit(); if(e.key==='Escape') close(null); });
  });
}

function _importConfirm(filename, onConfirm){
  const ov=document.createElement('div');
  ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
  ov.innerHTML=`<div style="background:var(--card,#1e2533);border:1px solid var(--border,#2a3448);border-radius:10px;padding:28px 32px;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.5);">
    <div style="font-size:15px;font-weight:600;margin-bottom:10px;color:var(--text,#e0e6f0);">Restore from Backup</div>
    <div style="font-size:13px;color:var(--text2,#8899aa);margin-bottom:20px;">Import <b style="color:var(--text,#e0e6f0)">${filename}</b>?<br><br>This will <span style="color:var(--down,#ff4444);font-weight:600;">replace the imported database(s)</span> and restart the server.<br><span style="color:var(--text2,#8899aa);font-size:12px">Accepts: Main DB, Logs DB, or full bundle (.zip / encrypted .pwbk).</span></div>
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

async function _doImport(file, passphrase){
  const statusEl=document.getElementById('db-import-status');
  if(statusEl){statusEl.style.color='var(--text3)';statusEl.textContent='Uploading…';}
  try{
    // Send the raw file bytes directly — avoids base64 encoding the entire
    // file in memory (which previously required ~3× the file size in RAM).
    const headers={'Content-Type':'application/octet-stream'};
    if(passphrase) headers['X-Bundle-Passphrase']=passphrase;
    const resp=await fetch('/api/db/import',{method:'POST',headers,body:file});
    const r=await resp.json().catch(()=> resp.ok?{ok:true}:null);
    // Encrypted bundle — server asks for a passphrase; prompt and retry.
    if(resp.status===400 && r && r.need_passphrase){
      const pass=await _promptPassphrase('Encrypted Backup',
        (r.error||'This bundle is encrypted.')+'<br>Enter the passphrase used when it was created.',
        {okLabel:'Restore'});
      if(pass===null){ if(statusEl){statusEl.style.color='var(--text3)';statusEl.textContent='Import cancelled';} return; }
      return _doImport(file, pass);
    }
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
}

async function importDb(){
  const inp=document.createElement('input');
  inp.type='file'; inp.accept='.db,.sqlite,.zip,.pwbk';
  inp.style.cssText='position:fixed;top:-9999px;left:-9999px;opacity:0;';
  document.body.appendChild(inp);
  const _cleanup=()=>{ try{document.body.removeChild(inp);}catch(_){} };
  inp.addEventListener('cancel', _cleanup);
  inp.onchange=()=>{
    _cleanup();
    const file=inp.files[0]; if(!file) return;
    _importConfirm(file.name, ()=>_doImport(file, ''));
  };
  inp.click();
}
