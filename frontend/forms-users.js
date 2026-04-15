// ── USER MANAGEMENT ───────────────────────────────────────────────────────────

async function _openProfileModal(){
  let me={username:'',role:'viewer',full_name:'',email:'',group_id:null,group_name:''};
  try{
    const r=await api('GET','/api/me');
    Object.assign(me,r);
  }catch(_){}

  // Admins also see group + role selector — fetch current user row for group_id
  let groups=[];
  let currentGroupId=null;
  const isAdmin=(me.role==='admin');
  if(isAdmin){
    try{
      const [ur,gr]=await Promise.all([
        api('GET','/api/users'),
        api('GET','/api/user/groups'),
      ]);
      const row=(ur.users||[]).find(u=>u.username===me.username);
      if(row){currentGroupId=row.group_id??null;}
      groups=gr.groups||[];
    }catch(_){}
  }

  const roleBadge={
    admin:'background:var(--accent-bg);color:var(--accent)',
    operator:'background:#2a3a2a;color:#4caf50',
    viewer:'background:var(--bg3);color:var(--text2)',
  }[me.role]||'background:var(--bg3);color:var(--text2)';

  const groupSel=isAdmin
    ?`<div class="fr"><label class="fl">Group</label>
        <select id="myp-group">
          <option value="">— No group —</option>
          ${groups.map(g=>`<option value="${g.id}" ${g.id===currentGroupId?'selected':''}>${esc(g.name)}</option>`).join('')}
        </select></div>`
    :'';

  closeM('m-myprof');
  const o=document.createElement('div'); o.className='mo'; o.id='m-myprof';
  _overlayClose(o,()=>closeM('m-myprof'));
  o.innerHTML=`
  <div class="mbox" style="max-width:400px">
    <div class="mhd">
      <div class="mttl">👤 Edit Profile</div>
      <button class="mclose" onclick="closeM('m-myprof')">✕</button>
    </div>
    <div class="mbdy">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid var(--border)">
        <div>
          <div style="font-size:13px;font-weight:600;color:var(--text)">${esc(me.username)}</div>
          <span style="display:inline-block;font-size:11px;padding:2px 8px;border-radius:4px;margin-top:3px;${roleBadge}">${esc(me.role)}</span>
        </div>
      </div>
      <div class="fr"><label class="fl">Full Name</label>
        <input type="text" id="myp-name" value="${esc(me.full_name||'')}" placeholder="Jane Doe" maxlength="200" autocomplete="off"/></div>
      <div class="fr"><label class="fl">Email</label>
        <input type="email" id="myp-email" value="${esc(me.email||'')}" placeholder="jane@corp.com" maxlength="200" autocomplete="off"/></div>
      ${groupSel}
    </div>
    <div class="mft">
      <button class="btn-s" onclick="closeM('m-myprof')">Cancel</button>
      <button class="btn-p" id="myp-btn" onclick="_submitProfileModal('${esc(me.username)}',${isAdmin})">Save</button>
    </div>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>document.getElementById('myp-name')?.focus(),50);
}

// ── Two-Factor Authentication (TOTP) ─────────────────────────────
async function _open2faModal(){
  // Fetch current TOTP state via /api/me
  let me={username:'',totp_enabled:0};
  try{ const r=await api('GET','/api/me'); Object.assign(me,r); }catch(_){}
  closeM('m-2fa');
  const o=document.createElement('div'); o.className='mo'; o.id='m-2fa';
  _overlayClose(o,()=>closeM('m-2fa'));
  const enabled=!!me.totp_enabled;
  o.innerHTML=`
    <div class="mbox" style="max-width:480px">
      <div class="mhd">
        <div class="mttl">🔐 Two-Factor Authentication</div>
        <button class="mclose" onclick="closeM('m-2fa')">✕</button>
      </div>
      <div class="mbdy" id="tfa-body">
        ${enabled
          ? `<div style="margin-bottom:14px">2FA is <b style="color:#4caf50">ENABLED</b> on your account.</div>
             <div class="fr"><label class="fl">Current password</label>
               <input type="password" id="tfa-pass" autocomplete="current-password"/></div>
             <div class="fr"><label class="fl">Current 2FA code</label>
               <input type="text" id="tfa-code" maxlength="6" autocomplete="one-time-code"
                      style="font-family:monospace;letter-spacing:2px;text-align:center"/></div>`
          : `<div style="margin-bottom:14px">2FA is currently <b>disabled</b>. Click below to enrol.</div>`
        }
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('m-2fa')">Cancel</button>
        ${enabled
          ? `<button class="btn-p" onclick="_2faDisable()">Disable 2FA</button>`
          : `<button class="btn-p" onclick="_2faStartSetup()">Enable 2FA</button>`}
      </div>
    </div>`;
  document.body.appendChild(o);
}

async function _2faStartSetup(){
  let r;
  try{ r=await api('POST','/api/me/totp/setup',{}); }catch(e){ toast('Setup failed','err'); return; }
  if(r.error){ toast(r.error,'err'); return; }
  const body=document.getElementById('tfa-body');
  if(!body) return;
  body.innerHTML=`
    <div style="margin-bottom:12px">Add this account to your authenticator app (Google Authenticator, Authy, 1Password, etc.):</div>
    <div style="background:var(--surface-inset,#0e141a);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:10px;font-family:monospace;font-size:12px;word-break:break-all;user-select:all">${esc(r.provisioning_uri)}</div>
    <div style="margin-bottom:8px;font-size:12px;color:var(--text2)">Or enter this secret manually:</div>
    <div style="background:var(--surface-inset,#0e141a);border:1px solid var(--border);border-radius:6px;padding:10px;margin-bottom:14px;font-family:monospace;font-size:14px;letter-spacing:1px;user-select:all;text-align:center">${esc(r.secret)}</div>
    <div class="fr"><label class="fl">Enter 6-digit code from your app</label>
      <input type="text" id="tfa-verify-code" maxlength="6" autocomplete="one-time-code"
             style="font-family:monospace;letter-spacing:3px;text-align:center;font-size:18px"/></div>`;
  const ft=document.querySelector('#m-2fa .mft');
  if(ft){ ft.innerHTML=`<button class="btn-s" onclick="closeM('m-2fa')">Cancel</button>
                       <button class="btn-p" onclick="_2faVerifyEnrol()">Verify & Enable</button>`; }
  setTimeout(()=>document.getElementById('tfa-verify-code')?.focus(),50);
}

async function _2faVerifyEnrol(){
  const code=(document.getElementById('tfa-verify-code')?.value||'').trim();
  if(!code){ toast('Enter the code','err'); return; }
  let r;
  try{ r=await api('POST','/api/me/totp/verify',{code}); }catch(e){ toast('Verification failed','err'); return; }
  if(r.error){ toast(r.error,'err'); return; }
  const body=document.getElementById('tfa-body');
  if(!body) return;
  body.innerHTML=`
    <div style="margin-bottom:12px;color:#4caf50;font-weight:600">✓ 2FA enabled successfully.</div>
    <div style="margin-bottom:10px">Save these recovery codes somewhere safe. Each can be used once if you lose access to your authenticator app:</div>
    <div style="background:var(--surface-inset,#0e141a);border:1px solid var(--border);border-radius:6px;padding:14px;margin-bottom:10px;font-family:monospace;font-size:14px;line-height:1.8;letter-spacing:1px;user-select:all;column-count:2;column-gap:20px">
      ${(r.recovery_codes||[]).map(c=>esc(c)).join('<br/>')}
    </div>
    <div style="font-size:12px;color:var(--text2)">Each code works only once. Store them in a password manager.</div>`;
  const ft=document.querySelector('#m-2fa .mft');
  if(ft){ ft.innerHTML=`<button class="btn-p" onclick="closeM('m-2fa')">I've saved them</button>`; }
}

async function _2faDisable(){
  const password=document.getElementById('tfa-pass')?.value||'';
  const code=(document.getElementById('tfa-code')?.value||'').trim();
  if(!password||!code){ toast('Password and code required','err'); return; }
  let r;
  try{ r=await api('POST','/api/me/totp/disable',{password,code}); }catch(e){ toast('Disable failed','err'); return; }
  if(r.error){ toast(r.error,'err'); return; }
  toast('2FA disabled','ok');
  closeM('m-2fa');
}


async function _submitProfileModal(username, isAdmin){
  const full_name=(document.getElementById('myp-name')?.value||'').trim();
  const email=(document.getElementById('myp-email')?.value||'').trim();
  const btn=document.getElementById('myp-btn');
  if(btn){btn.disabled=true;btn.textContent='Saving...';}
  try{
    const body={full_name,email};
    if(isAdmin){
      const gv=document.getElementById('myp-group')?.value;
      body.group_id=gv===''||gv===undefined?null:parseInt(gv);
    }
    const r=await api('PATCH',`/api/users/${encodeURIComponent(username)}/profile`,body);
    if(r.error){toast(r.error,'err');return;}
    closeM('m-myprof');
    toast('Profile updated','ok');
    // Refresh user table if it's visible (Settings → Users tab open)
    const uw=document.getElementById('userTableWrap');
    if(uw){
      const ur=await api('GET','/api/users');
      uw.innerHTML=renderUserTable(ur.users||[]);
    }
  }catch(e){
    toast('Failed to update profile','err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Save';}
  }
}

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

async function openAddUser(){
  let groups=[];
  try{const gr=await api('GET','/api/user/groups');groups=gr.groups||[];}catch(_){}
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
      <div class="fr"><label class="fl">Group</label>
        <select id="au-group">
          <option value="">— No group —</option>
          ${groups.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}
        </select>
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
  const gv=document.getElementById('au-group')?.value;
  if(gv) body.group_id=parseInt(gv);
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
