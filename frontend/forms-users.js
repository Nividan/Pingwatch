// ── USER MANAGEMENT ───────────────────────────────────────────────────────────

function _openChangePwModal(){
  closeM('m-cpw');
  const o=document.createElement('div'); o.className='mo'; o.id='m-cpw';
  o.innerHTML=`
    <div class="mw" style="max-width:380px">
      <div class="mhdr"><span>🔑 Change Password</span><button class="mclose" onclick="closeM('m-cpw')">✕</button></div>
      <div class="mbdy" style="padding:20px 22px">
        <div class="fr"><label class="fl">Current Password</label>
          <input type="password" id="cpw-cur" placeholder="current password" autocomplete="current-password"/></div>
        <div class="fr"><label class="fl">New Password</label>
          <input type="password" id="cpw-new" placeholder="min 8 characters" autocomplete="new-password"/></div>
        <div class="fr"><label class="fl">Confirm New Password</label>
          <input type="password" id="cpw-con" placeholder="confirm" autocomplete="new-password"/></div>
        <div style="display:flex;gap:8px;margin-top:18px;justify-content:flex-end">
          <button class="btn-s" onclick="closeM('m-cpw')">Cancel</button>
          <button class="btn-p" id="cpw-btn" onclick="_submitChangePw()">Update Password</button>
        </div>
      </div>
    </div>`;
  _overlayClose(o,()=>closeM('m-cpw'));
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('cpw-cur')?.focus(),50);
}

async function _submitChangePw(){
  const cur=document.getElementById('cpw-cur')?.value||'';
  const np =document.getElementById('cpw-new')?.value||'';
  const np2=document.getElementById('cpw-con')?.value||'';
  const btn=document.getElementById('cpw-btn');
  if(!cur||!np){toast('All password fields are required','err');return;}
  if(np!==np2){toast('Passwords do not match','err');return;}
  if(np.length<8){toast('Password must be at least 8 characters','err');return;}
  if(btn){btn.disabled=true;btn.textContent='Updating…';}
  try{
    const r=await api('PATCH','/api/me/password',{current_password:cur,password:np});
    if(r.error){toast(r.error,'err');return;}
    toast('Password updated','ok');
    closeM('m-cpw');
  }catch(e){
    toast('Failed to update password','err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Update Password';}
  }
}

async function changeOwnPassword(){
  const btn=document.getElementById('btnChgPw');
  const cur=document.getElementById('st-cpw')?.value||'';
  const np =document.getElementById('st-npw')?.value||'';
  const np2=document.getElementById('st-npw2')?.value||'';
  if(!cur||!np){toast('All password fields are required','err');return;}
  if(np!==np2){toast('Passwords do not match','err');return;}
  if(np.length<8){toast('Password must be at least 8 characters','err');return;}
  btn.disabled=true;btn.textContent='Updating...';
  let r;
  try{
    r=await api('PATCH','/api/me/password',{current_password:cur,password:np});
  }catch(e){
    toast('Failed to update password','err');
    return;
  }finally{
    btn.disabled=false;btn.textContent='Update Password';
  }
  if(r.error){toast(r.error,'err');return;}
  document.getElementById('st-cpw').value='';
  document.getElementById('st-npw').value='';
  document.getElementById('st-npw2').value='';
  toast('Password updated','ok');
}

async function reloadUserTable(){
  const ur=await api('GET','/api/users');
  const wrap=document.getElementById('userTableWrap');
  if(wrap) wrap.innerHTML=renderUserTable(ur.users||[]);
}

function openAddUser(){
  closeM('mau');
  const o=document.createElement('div'); o.className='mo'; o.id='mau';
  _overlayClose(o,()=>closeM('mau'));
  o.innerHTML=`
  <div class="mbox">
    <div class="mhd">
      <div class="mttl">Add User</div>
      <button class="mclose" onclick="closeM('mau')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">Authentication</label>
        <select id="au-type" onchange="_auTypeChange()">
          <option value="local">Local — password stored in PingWatch</option>
          <option value="ldap">Domain — authenticated via LDAP / AD</option>
        </select>
      </div>
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="au-u" autocomplete="off" placeholder="username"/></div>
      <div id="au-pw-fields">
        <div class="fr"><label class="fl">Password</label>
          <input type="password" id="au-p" placeholder="password"/></div>
        <div class="fr"><label class="fl">Confirm Password</label>
          <input type="password" id="au-p2" placeholder="confirm password"/></div>
      </div>
      <div id="au-domain-field" style="display:none">
        <div class="fr"><label class="fl">Domain</label>
          <input type="text" id="au-domain" placeholder="EXAMPLE (optional)" autocomplete="off"/></div>
        <div class="fh">Password will be verified against your LDAP / AD server at login.</div>
      </div>
      <div class="fr"><label class="fl">Role</label>
        <select id="au-r">
          <option value="viewer">Viewer — read-only dashboard access</option>
          <option value="operator">Operator — manage devices &amp; sensors</option>
          <option value="admin" selected>Admin — full access incl. users &amp; settings</option>
        </select>
      </div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mau')">Cancel</button>
      <button class="btn-p" onclick="submitAddUser()">Create User</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('au-u')?.focus(),50);
}

function _auTypeChange(){
  const isLdap=document.getElementById('au-type')?.value==='ldap';
  const pwFields=document.getElementById('au-pw-fields');
  const domainField=document.getElementById('au-domain-field');
  if(pwFields) pwFields.style.display=isLdap?'none':'';
  if(domainField) domainField.style.display=isLdap?'':'none';
}

async function submitAddUser(){
  const username=(document.getElementById('au-u')?.value||'').trim();
  const auth_type=document.getElementById('au-type')?.value||'local';
  const role=document.getElementById('au-r')?.value||'admin';
  if(!username){toast('Username is required','err');return;}
  let body={username,role,auth_type};
  if(auth_type==='ldap'){
    body.domain=(document.getElementById('au-domain')?.value||'').trim();
  }else{
    const pw=document.getElementById('au-p')?.value||'';
    const pw2=document.getElementById('au-p2')?.value||'';
    if(!pw){toast('Password is required','err');return;}
    if(pw!==pw2){toast('Passwords do not match','err');return;}
    body.password=pw;
  }
  const btn=document.querySelector('#mau .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Creating...';}
  let r;
  try{
    r=await api('POST','/api/users',body);
  }catch(e){
    toast('Failed to create user','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Create User';}
  }
  if(r.error){toast(r.error,'err');return;}
  closeM('mau');
  await reloadUserTable();
  toast(`User "${username}" created`,'ok');
}

let _resetPwTarget='';
function openResetPw(username){
  _resetPwTarget=username;
  closeM('mrpw');
  const o=document.createElement('div'); o.className='mo'; o.id='mrpw';
  _overlayClose(o,()=>closeM('mrpw'));
  o.innerHTML=`
  <div class="mbox">
    <div class="mhd">
      <div class="mttl">Reset Password — ${esc(username)}</div>
      <button class="mclose" onclick="closeM('mrpw')">✕</button>
    </div>
    <div class="mbdy">
      <div class="fr"><label class="fl">New Password</label>
        <input type="password" id="rp-p" placeholder="new password"/></div>
      <div class="fr"><label class="fl">Confirm Password</label>
        <input type="password" id="rp-p2" placeholder="confirm password"/></div>
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('mrpw')">Cancel</button>
      <button class="btn-p" onclick="submitResetPw()">Set Password</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('rp-p')?.focus(),50);
}

async function submitResetPw(){
  const pw=document.getElementById('rp-p')?.value||'';
  const pw2=document.getElementById('rp-p2')?.value||'';
  if(!pw){toast('Password is required','err');return;}
  if(pw!==pw2){toast('Passwords do not match','err');return;}
  const btn=document.querySelector('#mrpw .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Setting...';}
  let r;
  try{
    r=await api('PATCH',`/api/users/${encodeURIComponent(_resetPwTarget)}/password`,{password:pw});
  }catch(e){
    toast('Failed to set password','err');
    return;
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Set Password';}
  }
  if(r.error){toast(r.error,'err');return;}
  closeM('mrpw');
  toast(`Password updated for "${_resetPwTarget}"`,'ok');
}

async function deleteUser(username){
  if(!confirm(`Delete user "${username}"? This cannot be undone.`))return;
  const r=await api('DELETE',`/api/users/${encodeURIComponent(username)}`);
  if(r.error){toast(r.error,'err');return;}
  await reloadUserTable();
  toast(`User "${username}" deleted`,'ok');
}
