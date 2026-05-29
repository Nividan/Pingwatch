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
