// ── Shared helpers (loaded first — used by all other forms-*.js files) ────

/** Live Map tier dropdown — single source of truth for both Add Group and
 *  Edit Group modals. Mirrored on the backend in monitoring/site_tree.py
 *  _VALID_TIERS. When adding a new tier here, add the matching key + regex
 *  in the backend too. */
const _LM_TIER_OPTIONS = [
  {value: '',            label: '— Auto-detect from name —'},
  {value: 'isp',         label: 'ISP'},
  {value: 'wan_switch',  label: 'WAN Switch'},
  {value: 'firewall',    label: 'Firewall'},
  {value: 'core_switch', label: 'Core Switch'},
  {value: 'switch',      label: 'Access Switch'},
  {value: 'ap',          label: 'Access Point'},
  {value: 'chassis',     label: 'Chassis / Enclosure'},
  {value: 'hypervisor',  label: 'Hypervisor / Server'},
  {value: 'vm',          label: 'VM'},
  {value: 'ipmi',        label: 'IPMI / OOB'},
  {value: 'other',       label: 'Other'},
];
/** Build option HTML for the tier dropdown. `selected` is the currently
 *  chosen value (or '' for the empty option). `emptyLabel` overrides the
 *  first option's label — per-group uses "— Auto-detect from name —" (the
 *  default), per-device uses "— None —" (override falls through to group/regex).
 */
function _lmTierOptionsHtml(selected, emptyLabel) {
  const sel = String(selected == null ? '' : selected);
  return _LM_TIER_OPTIONS.map(function(o) {
    const lbl = (o.value === '' && emptyLabel) ? emptyLabel : o.label;
    return '<option value="' + o.value + '"' +
           (o.value === sel ? ' selected' : '') +
           '>' + lbl + '</option>';
  }).join('');
}

/* ── SNMP dropdown option lists — single source of truth ───────────────
   Used by Add/Edit Device + Sensor modals. Backend whitelist mirrors are
   in routes/devices.py (_V3_LEVELS / _V3_AUTH / _V3_PRIV) and probes.py.
   When changing any list here, update the matching backend whitelist. */
const _SNMP_VERSIONS = [
  {value: '2c', label: 'v2c'},
  {value: '1',  label: 'v1'},
  {value: '3',  label: 'v3'},
];
const _SNMP_V3_LEVELS = [
  {value: 'noAuthNoPriv', label: 'noAuthNoPriv'},
  {value: 'authNoPriv',   label: 'authNoPriv'},
  {value: 'authPriv',     label: 'authPriv'},
];
const _SNMP_V3_AUTH_PROTOS = [
  {value: 'SHA',     label: 'SHA'},
  {value: 'MD5',     label: 'MD5'},
  {value: 'SHA-224', label: 'SHA-224'},
  {value: 'SHA-256', label: 'SHA-256'},
  {value: 'SHA-384', label: 'SHA-384'},
  {value: 'SHA-512', label: 'SHA-512'},
];
const _SNMP_V3_PRIV_PROTOS = [
  {value: 'AES',     label: 'AES'},
  {value: 'DES',     label: 'DES'},
  {value: 'AES-192', label: 'AES-192'},
  {value: 'AES-256', label: 'AES-256'},
];

/** Generic option-list HTML builder. Falls back to the first entry as
 *  default when `selected` is empty / null. */
function _optHtml(list, selected) {
  const sel = selected == null ? '' : String(selected);
  // First entry is the default when no value is set.
  const effective = sel || (list[0] && list[0].value) || '';
  return list.map(function(o) {
    return '<option value="' + o.value + '"' +
           (o.value === effective ? ' selected' : '') +
           '>' + o.label + '</option>';
  }).join('');
}
function _snmpVerOptionsHtml(selected)      { return _optHtml(_SNMP_VERSIONS,        selected); }
function _snmpV3LevelOptionsHtml(selected)  { return _optHtml(_SNMP_V3_LEVELS,       selected); }
function _snmpV3AuthOptionsHtml(selected)   { return _optHtml(_SNMP_V3_AUTH_PROTOS,  selected); }
function _snmpV3PrivOptionsHtml(selected)   { return _optHtml(_SNMP_V3_PRIV_PROTOS,  selected); }

function closeM(id){document.getElementById(id)?.remove();}
/** Attach backdrop-click-to-close that ignores mousedown-inside drags. */
function _overlayClose(o, closeFn) {
  let _mdown = false;
  o.addEventListener('mousedown', e => { _mdown = (e.target === o); });
  o.addEventListener('click',     e => { if (e.target === o && _mdown) closeFn(); });
}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

/** Safe localStorage JSON reader — returns fallback on parse error or missing key. */
function _lsGet(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback; }
  catch { return fallback; }
}
/** Safe localStorage JSON writer — silently ignores quota/private mode errors. */
function _lsSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* quota/private mode */ }
}

/** Map a backend status string to its CSS class.
 *  Accepts: 'up' | 'down' | 'warn' | 'unknown' | undefined → returns the matching class. */
function statusClass(status) {
  return ({up:'up', down:'down', warn:'warn', unknown:''})[status] || '';
}

/** Latency value → color class: 'g' (good), 'w' (warn), 'b' (bad/down).
 *  Uses sensor-specific warn_ms/crit_ms if set, then per-type defaults,
 *  then the global window._lGood / window._lWarn breakpoints. */
function msColor(ms, sensor) {
  if (ms === null || ms === undefined) return 'b';
  const td = window._snrTypeDefaults?.[sensor?.stype] || {};
  const w = sensor?.warn_ms > 0 ? sensor.warn_ms : (td.warn_ms > 0 ? td.warn_ms : 0);
  const c = sensor?.crit_ms > 0 ? sensor.crit_ms : (td.crit_ms > 0 ? td.crit_ms : 0);
  // Inverted-threshold metrics: lower value = worse (VMware datastore free-GB, TLS cert days-to-expiry)
  const inverted = (sensor?.stype === 'vmware' && typeof sensor?.vmware_metric === 'string' && sensor.vmware_metric.startsWith('dstore_'))
                || (sensor?.stype === 'tls');
  if (w > 0 || c > 0) {
    if (inverted) {
      if (c > 0 && ms <= c) return 'b';
      if (w > 0 && ms <= w) return 'w';
      return 'g';
    }
    if (c > 0 && ms >= c) return 'b';
    if (w > 0 && ms >= w) return 'w';
    return 'g';
  }
  if (ms < (window._lGood || 100)) return 'g';
  if (ms < (window._lWarn || 300)) return 'w';
  return 'b';
}

window.addEventListener('resize',()=>{
  Object.keys(S.charts).forEach(k=>{
    const info=S.charts[k];if(info)drawSpk(k,S.sensors[k]?.history||[]);
  });
});

/* ── Site combobox ────────────────────────────────────────────────────────
   A real click-to-pick dropdown of ALL sites that still allows typing a new
   site name (sites are free-text). Replaces the native <datalist> used by the
   subnet / device / group editors — datalists filter-as-you-type and don't
   show every option reliably. Drop in via siteComboHtml(id, current, ph): the
   inner <input> keeps the given id, so existing `getElementById(id).value`
   read paths are unchanged. Sites come from /api/sites (the full union the
   sidebar uses), cached for 60s. Self-wires through document-level delegation
   — no per-form init call. */
(function(){
  let _open = null;          // currently open .site-combo element (or null)
  let _sites = null;         // cached site list
  let _sitesAt = 0;

  async function _ensureSites(){
    if (_sites && (Date.now() - _sitesAt) < 60000) return _sites;
    try {
      const r = await fetch('/api/sites', { credentials: 'same-origin' });
      if (r.ok) { _sites = ((await r.json()).sites || []).filter(Boolean); _sitesAt = Date.now(); }
    } catch (_) { /* keep stale/empty */ }
    return _sites || [];
  }
  // Let callers drop the cache after creating a new site so it shows up at once.
  window._siteComboInvalidate = function(){ _sites = null; };

  const _inp   = c => c.querySelector('.site-combo-input');
  const _panel = c => c.querySelector('.site-combo-panel');

  function _render(c){
    const inp = _inp(c), panel = _panel(c);
    const q = (inp.value || '').trim().toLowerCase();
    const all = _sites || [];
    const matches = q ? all.filter(s => s.toLowerCase().includes(q)) : all;
    let html = '<div class="site-combo-item site-combo-clear" data-val="">— No site —</div>';
    html += matches.map(s =>
      `<div class="site-combo-item" data-val="${esc(s)}">${esc(s)}</div>`).join('');
    // Offer to use a brand-new value when the typed text isn't an exact match.
    if (q && !all.some(s => s.toLowerCase() === q)) {
      const v = inp.value.trim();
      html += `<div class="site-combo-item site-combo-new" data-val="${esc(v)}">➕ Use “${esc(v)}”</div>`;
    }
    panel.innerHTML = html;
  }

  function _position(c){
    const r = _inp(c).getBoundingClientRect(), panel = _panel(c);
    panel.style.left  = r.left + 'px';
    panel.style.width = r.width + 'px';
    const below = window.innerHeight - r.bottom;
    const ph = Math.min(panel.scrollHeight, 240);
    if (below < ph + 8 && r.top > below) {           // flip up when cramped
      panel.style.top = ''; panel.style.bottom = (window.innerHeight - r.top + 4) + 'px';
    } else {
      panel.style.bottom = ''; panel.style.top = (r.bottom + 4) + 'px';
    }
  }

  async function _openCombo(c){
    if (_open && _open !== c) _closeCombo(_open);
    _open = c;
    await _ensureSites();
    if (_open !== c || !document.contains(c)) return;   // raced/closed
    _render(c);
    _panel(c).hidden = false;
    c.classList.add('open');
    _position(c);
  }
  function _closeCombo(c){
    if (!c) return;
    _panel(c).hidden = true;
    c.classList.remove('open');
    if (_open === c) _open = null;
  }

  function _reposition(){
    if (_open && !document.contains(_open)) { _open = null; return; }
    if (_open) _position(_open);
  }
  window.addEventListener('scroll', _reposition, true);
  window.addEventListener('resize', _reposition);

  document.addEventListener('focusin', e => {
    const t = e.target;
    if (t.classList && t.classList.contains('site-combo-input'))
      _openCombo(t.closest('.site-combo'));
  });
  document.addEventListener('input', e => {
    const t = e.target;
    if (t.classList && t.classList.contains('site-combo-input')) {
      const c = t.closest('.site-combo');
      if (c.classList.contains('open')) _render(c); else _openCombo(c);
    }
  });
  document.addEventListener('mousedown', e => {
    const arrow = e.target.closest && e.target.closest('.site-combo-arrow');
    if (arrow) {                       // toggle without stealing focus first
      e.preventDefault();
      const c = arrow.closest('.site-combo');
      if (c.classList.contains('open')) _closeCombo(c);
      else { _inp(c).focus(); _openCombo(c); }
      return;
    }
    const item = e.target.closest && e.target.closest('.site-combo-item');
    if (item) {
      e.preventDefault();              // keep focus, don't blur-close mid-pick
      const c = item.closest('.site-combo'), inp = _inp(c);
      inp.value = item.dataset.val || '';
      inp.dispatchEvent(new Event('change', { bubbles: true }));
      _closeCombo(c);
      return;
    }
    if (_open && !(e.target.closest && e.target.closest('.site-combo'))) _closeCombo(_open);
  });
  document.addEventListener('keydown', e => {
    if (!_open) return;
    const panel = _panel(_open);
    if (e.key === 'Escape') { _closeCombo(_open); return; }
    const items = [...panel.querySelectorAll('.site-combo-item')];
    if (!items.length) return;
    let i = items.findIndex(x => x.classList.contains('active'));
    if (e.key === 'ArrowDown')      { e.preventDefault(); i = Math.min(items.length - 1, i + 1); }
    else if (e.key === 'ArrowUp')   { e.preventDefault(); i = Math.max(0, i - 1); }
    else if (e.key === 'Enter')     { if (i >= 0) { e.preventDefault(); items[i].dispatchEvent(new MouseEvent('mousedown', {bubbles:true})); } return; }
    else return;
    items.forEach(x => x.classList.remove('active'));
    items[i].classList.add('active');
    items[i].scrollIntoView({ block: 'nearest' });
  });

  window.siteComboHtml = function(id, current, placeholder){
    return '<div class="site-combo" data-site-combo>'
      + '<input type="text" id="' + esc(id) + '" class="site-combo-input" autocomplete="off" '
      + 'maxlength="60" value="' + esc(current || '') + '" placeholder="'
      + esc(placeholder || 'e.g. NYC, DC1, HQ') + '"/>'
      + '<button type="button" class="site-combo-arrow" tabindex="-1" aria-label="Show sites">'
      + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
      + 'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>'
      + '</button><div class="site-combo-panel" hidden></div></div>';
  };
})();
