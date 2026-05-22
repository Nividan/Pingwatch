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
  {value: 'chassis',     label: 'Chassis / Enclosure'},
  {value: 'hypervisor',  label: 'Hypervisor / Server'},
  {value: 'vm',          label: 'VM'},
  {value: 'ipmi',        label: 'IPMI / OOB'},
  {value: 'other',       label: 'Other'},
];
/** Build option HTML for the tier dropdown. `selected` is the currently
 *  chosen value (or '' for auto-detect). */
function _lmTierOptionsHtml(selected) {
  const sel = String(selected == null ? '' : selected);
  return _LM_TIER_OPTIONS.map(function(o) {
    return '<option value="' + o.value + '"' +
           (o.value === sel ? ' selected' : '') +
           '>' + o.label + '</option>';
  }).join('');
}

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
