// ── Shared helpers (loaded first — used by all other forms-*.js files) ────
function closeM(id){document.getElementById(id)?.remove();}
/** Attach backdrop-click-to-close that ignores mousedown-inside drags. */
function _overlayClose(o, closeFn) {
  let _mdown = false;
  o.addEventListener('mousedown', e => { _mdown = (e.target === o); });
  o.addEventListener('click',     e => { if (e.target === o && _mdown) closeFn(); });
}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

window.addEventListener('resize',()=>{
  Object.keys(S.charts).forEach(k=>{
    const info=S.charts[k];if(info)drawSpk(k,S.sensors[k]?.history||[]);
  });
});
