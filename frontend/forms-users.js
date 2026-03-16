// ── USER MANAGEMENT ───────────────────────────────────────────────────────────
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
      <div class="fr"><label class="fl">Username</label>
        <input type="text" id="au-u" autocomplete="off" placeholder="username"/></div>
      <div class="fr"><label class="fl">Password</label>
        <input type="password" id="au-p" placeholder="password"/></div>
      <div class="fr"><label class="fl">Confirm Password</label>
        <input type="password" id="au-p2" placeholder="confirm password"/></div>
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

async function submitAddUser(){
  const username=(document.getElementById('au-u')?.value||'').trim();
  const pw=document.getElementById('au-p')?.value||'';
  const pw2=document.getElementById('au-p2')?.value||'';
  const role=document.getElementById('au-r')?.value||'admin';
  if(!username||!pw){toast('Username and password are required','err');return;}
  if(pw!==pw2){toast('Passwords do not match','err');return;}
  const btn=document.querySelector('#mau .btn-p');
  if(btn){btn.disabled=true;btn.textContent='Creating...';}
  let r;
  try{
    r=await api('POST','/api/users',{username,password:pw,role});
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
