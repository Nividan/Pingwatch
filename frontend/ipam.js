// ── IP Addresses (IPAM) Tab ─────────────────────────────────────────────────
// Lightweight IP address management: define subnets (CIDR), auto-generate
// all usable host IPs, assign names to individual IPs, track who changed what.

let _ipamSubnets      = [];     // [{id, cidr, name, created_by, created_at}]
let _ipamSelectedId   = null;   // currently selected subnet id
let _ipamAllIps       = [];     // full merged list [{ip, name, modified_by, modified_at, device_id, dns_name, dns_resolved_at}]
let _ipamFiltered     = [];     // search-filtered view of _ipamAllIps
let _ipamPage         = 0;      // current page (0-based)
let _ipamShellInited  = false;  // shell HTML built once; data always refreshed on tab switch
const _IPAM_PAGE_SIZE = 200;
let _ipamGlobalCache  = null;   // flat array of all IPs across all subnets, with subnetLabel; null = stale
let _ipamLicenseMap   = {};     // did → worst license status (ok/warn/crit)
let _ipamDnsInterval  = null;   // active DNS-refresh poll interval (cleared on subnet change / nav away)

// ── Sort / filter state ─────────────────────────────────────────────────────
let _ipamSortCol      = 'status_ip'; // default: Used first, then by IP
let _ipamSortDir      = 1;           // 1 = ascending, -1 = descending
let _ipamFilterStatus = '';          // '' = all, 'used', 'free'
let _ipamFilterLic    = '';          // '' = all, 'ok', 'warn', 'crit', 'none'

// ── Init ───────────────────────────────────────────────────────────────────
async function _ipamInit() {
  // Build the toolbar/shell only once; always re-fetch data on tab switch
  if (!_ipamShellInited) {
    _ipamShellInited = true;
    _ipamRenderShell();
  }
  await Promise.all([_ipamLoadSubnets(), _ipamLoadLicenses()]);
}

function _ipamRenderShell() {
  const view = document.getElementById('ipamView');
  if (!view) return;
  view.innerHTML = `
    <div class="pagehead">
      <div class="pagehead-l">
        <h1>IP Address Management</h1>
        <div class="sub" id="ipam-sub">Subnets and per-host allocation tracking.</div>
      </div>
      <div class="pagehead-r">
        <button class="btn primary rbac-op" onclick="_ipamOpenAddSubnet()">${icon('plus',13)} Add Subnet</button>
        <button class="btn rbac-op" id="ipam-edit-btn" onclick="_ipamOpenEdit()" disabled title="Edit subnet name, auto-discovery, DNS server">${icon('edit',13)} Edit</button>
        <button class="btn ghost rbac-op" id="ipam-dns-btn" onclick="_ipamRefreshDns()" style="display:none" title="Resolve DNS hostnames for all IPs in this subnet">${icon('refresh',13)} Refresh DNS</button>
        <button class="btn danger rbac-op" id="ipam-rm-btn" onclick="_ipamRemoveSubnet()" disabled title="Delete this subnet">${icon('trash',13)} Remove</button>
      </div>
    </div>
    <div class="ipam-layout">
      <aside class="ipam-sidebar">
        <div class="ipam-sidebar-filter">
          <div class="search" style="width:100%">
            ${icon('search',14)}
            <input class="pw-input" id="ipam-subnet-filter" type="search" placeholder="Filter subnets…" oninput="_ipamFilterSidebar(this.value)" autocomplete="off"/>
          </div>
        </div>
        <div class="ipam-subnet-list" id="ipam-subnet-list">
          <div class="ipam-empty">No subnets yet.</div>
        </div>
      </aside>
      <main class="ipam-main" id="ipam-main">
        <div class="ipam-main-empty">
          <div class="ipam-main-empty-icon">${icon('ipam',32)}</div>
          <div class="ipam-main-empty-title">Select a subnet to begin</div>
          <div class="ipam-main-empty-hint">Pick a subnet from the left to see its KPIs, address heatmap, and per-IP allocations.</div>
        </div>
      </main>
    </div>`;
  applyRbac();
}

// Sidebar filter — narrow the visible subnet cards by CIDR or name match
let _ipamSidebarFilter = '';
function _ipamFilterSidebar(q) {
  _ipamSidebarFilter = (q || '').toLowerCase().trim();
  _ipamRenderSidebar();
}

// ── License status cache ───────────────────────────────────────────────────
async function _ipamLoadLicenses() {
  try {
    const r = await fetch('/api/licenses');
    if (!r.ok) return;
    const { licenses } = await r.json();
    const prio = { crit: 2, warn: 1, ok: 0 };
    const map = {};
    for (const lic of (licenses || [])) {
      const cur = map[lic.did];
      if (cur === undefined || prio[lic.last_status] > prio[cur]) {
        map[lic.did] = lic.last_status;
      }
    }
    _ipamLicenseMap = map;
  } catch {}
}

function _ipamLicBadge(did) {
  if (!did) return '<span style="color:var(--text3)">—</span>';
  const st = _ipamLicenseMap[did];
  if (st === undefined) return '<span style="color:var(--text3)">—</span>';
  if (st === 'crit') return '<span class="ipam-lic-crit">Expired</span>';
  if (st === 'warn') return '<span class="ipam-lic-warn">Expiring</span>';
  return '<span class="ipam-lic-ok">Valid</span>';
}

async function _ipamOnLicenseUpdate() {
  await _ipamLoadLicenses();
  if (_ipamSelectedId) _ipamApplyFilter(document.getElementById('ipam-search')?.value || '');
}

// ── Subnet loading ─────────────────────────────────────────────────────────
async function _ipamLoadSubnets() {
  const r = await fetch('/api/ipam/subnets');
  if (r.status === 401) { if(!_loggedOut)showLogin('Session expired'); return; }
  if (!r.ok) { toast('Failed to load subnets', 'err'); return; }
  const d = await r.json();
  _ipamSubnets = d.subnets || [];
  _ipamRenderSubnetSelect();
  // Restore previously selected subnet if still present
  if (_ipamSelectedId && _ipamSubnets.find(s => s.id === _ipamSelectedId)) {
    _ipamOnSubnetChange(_ipamSelectedId);
  } else if (_ipamSubnets.length === 1) {
    _ipamOnSubnetChange(_ipamSubnets[0].id);
  } else {
    _ipamSelectedId = null;
    document.getElementById('ipam-rm-btn')?.setAttribute('disabled', '');
    document.getElementById('ipam-edit-btn')?.setAttribute('disabled', '');
    // Main pane keeps its empty-state placeholder from the shell
  }
}

// Compatibility shim — old code paths called the select-based renderer.
// Now routes to the new sidebar renderer.
function _ipamRenderSubnetSelect() {
  _ipamRenderSidebar();
}

// Cache of per-subnet utilization stats: {subnet_id: {total, used}}
const _ipamUtilCache = {};

function _ipamRenderSidebar() {
  const list = document.getElementById('ipam-subnet-list');
  if (!list) return;

  // Refresh the top-bar subtitle with subnet count
  const sub = document.getElementById('ipam-sub');
  if (sub) {
    const n = _ipamSubnets.length;
    const conflicts = _ipamSubnets.filter(s => s._conflict).length;
    sub.textContent = `${n} subnet${n===1?'':'s'} · auto-detected from monitored devices${conflicts?` · ${conflicts} conflict${conflicts===1?'':'s'}`:''}`;
  }

  const q = _ipamSidebarFilter;
  const visible = q
    ? _ipamSubnets.filter(s => (s.cidr||'').toLowerCase().includes(q) || (s.name||'').toLowerCase().includes(q))
    : _ipamSubnets;

  if (!visible.length) {
    list.innerHTML = `<div class="ipam-empty">${q ? 'No subnets match.' : 'No subnets yet — click + Add Subnet.'}</div>`;
    return;
  }

  list.innerHTML = visible.map(s => {
    const u = _ipamUtilCache[s.id];
    const pct = u && u.total ? Math.round((u.used / u.total) * 100) : 0;
    const pctCls = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';
    const active = s.id === _ipamSelectedId ? ' active' : '';
    return `
      <div class="ipam-subnet-card${active}" onclick="_ipamOnSubnetChange(${s.id})">
        <div class="ipam-subnet-card-l">
          <div class="ipam-subnet-cidr mono">${esc(s.cidr)}</div>
          <div class="ipam-subnet-meta">${esc(s.name || '—')}</div>
        </div>
        <div class="ipam-subnet-util ${pctCls}">${pct}%</div>
      </div>`;
  }).join('');
}

function _ipamShowEmptyTable(msg) {
  const wrap = document.getElementById('ipam-table-wrap');
  if (wrap) wrap.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">${msg}</div>`;
  const pg = document.getElementById('ipam-pg');
  if (pg) pg.innerHTML = '';
}

// Render the full main pane (header + KPIs + heatmap container + table container)
// for the currently-selected subnet. Called once per subnet switch.
function _ipamRenderMain(subnet) {
  const main = document.getElementById('ipam-main');
  if (!main) return;
  const meta = subnet.name || '—';
  main.innerHTML = `
    <div class="ipam-main-head">
      <div class="ipam-main-head-l">
        <div class="ipam-main-title mono">${esc(subnet.cidr)}</div>
        <div class="ipam-main-sub">${esc(meta)}</div>
      </div>
      <div class="ipam-main-head-r">
        <button class="btn ghost sm" onclick="_ipamRefreshDns()" title="Rescan subnet (refresh DNS + allocations)">${icon('refresh',12)} Rescan</button>
        <button class="btn sm rbac-op" onclick="_ipamOpenReserve()" title="Reserve an IP">${icon('plus',12)} Reserve</button>
      </div>
    </div>
    <div class="ipam-kpis" id="ipam-kpis"></div>
    <div class="ipam-section">
      <div class="ipam-section-head">
        <div class="ipam-section-title">Address heatmap</div>
        <div class="ipam-heatmap-legend">
          <span><i class="ipam-leg free"></i>Free</span>
          <span><i class="ipam-leg used"></i>In use</span>
          <span><i class="ipam-leg gw"></i>Gateway</span>
          <span><i class="ipam-leg rsv"></i>Reserved</span>
          <span><i class="ipam-leg cfl"></i>Conflict</span>
        </div>
      </div>
      <div class="ipam-heatmap" id="ipam-heatmap"></div>
    </div>
    <div class="ipam-hdr">
      <div class="search" style="flex:1;max-width:380px">
        ${icon('search',14)}
        <input class="ipam-search pw-input" id="ipam-search" type="search" placeholder="Search IP, name or DNS…" oninput="_ipamOnSearch(this.value)" autocomplete="off"/>
      </div>
      <div class="ipam-pg" id="ipam-pg" style="margin-left:auto"></div>
    </div>
    <div id="ipam-table-wrap"></div>`;
}

// Classify each IP into one of: free | used | gw | rsv | cfl
//   gw  → name/dns matches "gateway" or is the network's .1 address
//   used → has device_id (device-linked allocation)
//   rsv → manual name but no device link
//   cfl → marked by backend (no field today — placeholder)
//   free → no allocation
function _ipamClassify(entry) {
  const name = (entry.name || '').toLowerCase();
  const dns  = (entry.dns_name || '').toLowerCase();
  if (name === 'gateway' || dns === '_gateway' || name.startsWith('gw') || dns.startsWith('gw'))
    return 'gw';
  if (entry.device_id) return 'used';
  if (entry.name)      return 'rsv';
  return 'free';
}

// Render the 5 KPI cards from the current allocations
function _ipamRenderKPIs() {
  const wrap = document.getElementById('ipam-kpis');
  if (!wrap) return;
  let used = 0, rsv = 0, gw = 0, cfl = 0;
  for (const e of _ipamAllIps) {
    const k = _ipamClassify(e);
    if (k === 'used') used++;
    else if (k === 'gw') gw++;
    else if (k === 'rsv') rsv++;
    else if (k === 'cfl') cfl++;
  }
  const total = _ipamAllIps.length;
  const filled = used + rsv + gw + cfl;
  const free = total - filled;
  const pct = total ? Math.round((filled / total) * 100) : 0;
  wrap.innerHTML = `
    <div class="ipam-kpi">
      <div class="ipam-kpi-label">Total addresses</div>
      <div class="ipam-kpi-val">${total}</div>
    </div>
    <div class="ipam-kpi">
      <div class="ipam-kpi-label">In use</div>
      <div class="ipam-kpi-val accent">${used}</div>
    </div>
    <div class="ipam-kpi">
      <div class="ipam-kpi-label">Reserved</div>
      <div class="ipam-kpi-val">${rsv + gw}</div>
    </div>
    <div class="ipam-kpi">
      <div class="ipam-kpi-label">Free</div>
      <div class="ipam-kpi-val">${free}</div>
    </div>
    <div class="ipam-kpi">
      <div class="ipam-kpi-label">Utilization</div>
      <div class="ipam-kpi-val ${pct>=90?'crit':pct>=75?'warn':''}">${pct}<span class="ipam-kpi-unit">%</span></div>
    </div>`;

  // Update cache so the sidebar utilization for this subnet refreshes too
  if (_ipamSelectedId) {
    _ipamUtilCache[_ipamSelectedId] = { total, used: filled };
    _ipamRenderSidebar();
  }
}

// Render the 16-wide square heatmap (/24 = 16×16; smaller masks shrink rows)
function _ipamRenderHeatmap() {
  const wrap = document.getElementById('ipam-heatmap');
  if (!wrap) return;
  if (!_ipamAllIps.length) { wrap.innerHTML = '<div class="ipam-empty">No addresses to display.</div>'; return; }
  const cells = _ipamAllIps.map(e => {
    const k = _ipamClassify(e);
    const tip = `${e.ip}${e.name ? ' — ' + e.name : ''}${e.dns_name ? ' (' + e.dns_name + ')' : ''}`;
    return `<button class="ipam-hm-cell ${k}" title="${esc(tip)}" onclick="_ipamFocusIp('${esc(e.ip)}')"></button>`;
  }).join('');
  wrap.innerHTML = cells;
}

function _ipamFocusIp(ip) {
  // Set the search box to this IP so the table filters to just this row
  const inp = document.getElementById('ipam-search');
  if (inp) { inp.value = ip; _ipamOnSearch(ip); }
}

function _ipamOpenReserve() {
  // Stub for now — the existing Edit-cell flow lets users assign a name to a row.
  // A dedicated Reserve modal can come later; for now, focus the first free
  // row's name cell so the user can type into it.
  const free = _ipamAllIps.find(e => !e.name && !e.device_id);
  if (free && typeof toast === 'function') {
    toast(`Tip: click any "click to assign…" cell in the table to reserve an IP (next free: ${free.ip}).`, 'info');
  }
}

// ── Subnet selection ───────────────────────────────────────────────────────
async function _ipamOnSubnetChange(idVal) {
  _ipamCancelDnsInterval();   // cancel any in-flight DNS poll for previous subnet
  const id = parseInt(idVal);
  if (!id) {
    _ipamSelectedId = null;
    document.getElementById('ipam-rm-btn')?.setAttribute('disabled', '');
    document.getElementById('ipam-edit-btn')?.setAttribute('disabled', '');
    return;
  }
  _ipamSelectedId = id;
  _ipamSortCol = 'status_ip'; _ipamSortDir = 1;
  _ipamFilterStatus = ''; _ipamFilterLic = '';
  document.getElementById('ipam-rm-btn')?.removeAttribute('disabled');
  document.getElementById('ipam-edit-btn')?.removeAttribute('disabled');

  // Refresh sidebar so the active card highlights immediately
  _ipamRenderSidebar();

  const r = await fetch(`/api/ipam/subnets/${id}/ips`);
  if (!r.ok) { toast('Failed to load IPs', 'err'); return; }
  const d = await r.json();
  const subnet = d.subnet;
  const allocs = d.allocations || {};   // {ip: {name, modified_by, modified_at}}

  // Build the full main pane now that we know the subnet
  _ipamRenderMain(subnet);

  // Show Refresh DNS button for operators when a subnet is selected
  const dnsBtn = document.getElementById('ipam-dns-btn');
  if (dnsBtn) dnsBtn.style.display = '';

  // Generate all usable IPs from CIDR, merge with allocations
  const ips = _ipamExpandCidr(subnet.cidr);
  _ipamAllIps = ips.map(ip => {
    const a = allocs[ip];
    return a
      ? { ip, name: a.name, modified_by: a.modified_by, modified_at: a.modified_at,
          device_id: a.device_id || '', dns_name: a.dns_name || '', dns_resolved_at: a.dns_resolved_at || 0 }
      : { ip, name: '', modified_by: '', modified_at: 0, device_id: '', dns_name: '', dns_resolved_at: 0 };
  });

  _ipamRenderKPIs();
  _ipamRenderHeatmap();
  _ipamPage = 0;
  _ipamApplyFilter('');
}

function _ipamIpCmp(a, b) {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < 4; i++) {
    if (pa[i] !== pb[i]) return pa[i] - pb[i];
  }
  return 0;
}

// ── CIDR expansion (pure JS, no libs) ─────────────────────────────────────
function _ipamExpandCidr(cidr) {
  const [addr, prefix] = cidr.split('/');
  const pfx   = parseInt(prefix, 10);
  const parts = addr.split('.').map(Number);
  const base  = ((parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]) >>> 0;
  const mask  = pfx === 0 ? 0 : (~0 << (32 - pfx)) >>> 0;
  const net   = (base & mask) >>> 0;
  const bcast = (net | (~mask >>> 0)) >>> 0;
  if (pfx === 32) return [_n2ip(net)];
  if (pfx === 31) return [_n2ip(net), _n2ip(bcast)];
  const ips = [];
  for (let i = net + 1; i < bcast; i++) ips.push(_n2ip(i >>> 0));
  return ips;
}
function _n2ip(n) {
  return `${(n >>> 24) & 255}.${(n >>> 16) & 255}.${(n >>> 8) & 255}.${n & 255}`;
}

// ── Sort / filter helpers ──────────────────────────────────────────────────

function _ipamSetSort(col) {
  if (_ipamSortCol === col) {
    _ipamSortDir *= -1; // toggle direction
  } else {
    _ipamSortCol = col;
    _ipamSortDir = 1;
  }
  _ipamPage = 0;
  _ipamApplyFilter(document.getElementById('ipam-search')?.value || '');
}

function _ipamSetFilterStatus(val) {
  _ipamFilterStatus = val;
  _ipamPage = 0;
  _ipamApplyFilter(document.getElementById('ipam-search')?.value || '');
}

function _ipamSetFilterLic(val) {
  _ipamFilterLic = val;
  _ipamPage = 0;
  _ipamApplyFilter(document.getElementById('ipam-search')?.value || '');
}

function _ipamLicVal(e) {
  if (!e.device_id) return 'none';
  const st = _ipamLicenseMap[e.device_id];
  return st || 'none';
}

function _ipamSortCmp(a, b) {
  const d = _ipamSortDir;
  const col = _ipamSortCol;
  let r = 0;
  switch (col) {
    case 'ip':
      r = _ipamIpCmp(a.ip, b.ip); break;
    case 'name':
      r = (a.name || '').localeCompare(b.name || ''); break;
    case 'dns':
      r = (a.dns_name || '').localeCompare(b.dns_name || ''); break;
    case 'status':
      r = (a.name ? 0 : 1) - (b.name ? 0 : 1); break;
    case 'license': {
      const p = { crit: 3, warn: 2, ok: 1, none: 0 };
      r = (p[_ipamLicVal(a)] || 0) - (p[_ipamLicVal(b)] || 0);
      break;
    }
    case 'modified_by':
      r = (a.modified_by || '').localeCompare(b.modified_by || ''); break;
    case 'modified_at':
      r = (a.modified_at || 0) - (b.modified_at || 0); break;
    case 'status_ip': // default: Used first, then by IP
      r = (a.name ? 0 : 1) - (b.name ? 0 : 1);
      if (r === 0) r = _ipamIpCmp(a.ip, b.ip);
      return r; // default sort ignores direction toggle
    default:
      r = _ipamIpCmp(a.ip, b.ip);
  }
  if (r === 0) r = _ipamIpCmp(a.ip, b.ip); // tiebreak by IP
  return r * d;
}

function _ipamSortArrow(col) {
  if (_ipamSortCol !== col) return '';
  return _ipamSortDir === 1 ? ' ▲' : ' ▼';
}

function _ipamThHtml(col, label) {
  const active = (_ipamSortCol === col) ? ' ipam-th-active' : '';
  const arrow = _ipamSortArrow(col);
  return `<th class="ipam-th-sort${active}" onclick="_ipamSetSort('${col}')">${label}${arrow}</th>`;
}

// ── Search / filter ────────────────────────────────────────────────────────
async function _ipamOnSearch(val) {
  _ipamPage = 0;
  const q = (val || '').trim();
  if (q) {
    await _ipamGlobalSearch(q);
  } else {
    // Clear global results: restore subnet view (or empty prompt)
    document.getElementById('ipam-table-wrap')?.classList.remove('ipam-global-results');
    if (_ipamSelectedId) {
      _ipamApplyFilter('');
    } else {
      _ipamShowEmptyTable('Select a subnet above to view its IP addresses.');
    }
  }
}

async function _ipamGlobalSearch(q) {
  const wrap = document.getElementById('ipam-table-wrap');
  if (!wrap) return;
  // Build global cache if stale
  if (!_ipamGlobalCache) {
    wrap.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">Searching all subnets…</div>';
    const flat = [];
    await Promise.all(_ipamSubnets.map(async sub => {
      try {
        const r = await fetch(`/api/ipam/subnets/${sub.id}/ips`);
        if (!r.ok) return;
        const d = await r.json();
        const label = sub.cidr + (sub.name ? ' — ' + sub.name : '');
        const allocs = d.allocations || {};
        _ipamExpandCidr(sub.cidr).forEach(ip => {
          const a = allocs[ip];
          flat.push(a
            ? { ip, subnetId: sub.id, subnetLabel: label,
                name: a.name, modified_by: a.modified_by, modified_at: a.modified_at,
                device_id: a.device_id || '', dns_name: a.dns_name || '' }
            : { ip, subnetId: sub.id, subnetLabel: label,
                name: '', modified_by: '', modified_at: 0, device_id: '', dns_name: '' });
        });
      } catch(_) {}
    }));
    _ipamGlobalCache = flat;
  }
  const lq = q.toLowerCase();
  const results = _ipamGlobalCache.filter(e =>
    e.ip.includes(lq) ||
    e.name.toLowerCase().includes(lq) ||
    (e.dns_name || '').toLowerCase().includes(lq) ||
    e.subnetLabel.toLowerCase().includes(lq)
  );
  _ipamRenderGlobalResults(results, q);
}

function _ipamRenderGlobalResults(results, q) {
  const wrap = document.getElementById('ipam-table-wrap');
  if (!wrap) return;
  const pg = document.getElementById('ipam-pg');
  if (!results.length) {
    wrap.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">No IPs match your search across any subnet.</div>';
    if (pg) pg.innerHTML = '';
    return;
  }
  const canEdit = S.role === 'operator' || S.role === 'admin';
  const rows = results.map(e => {
    const used = !!e.name;
    const badge = used ? `<span class="ipam-used">Used</span>` : `<span class="ipam-free">Free</span>`;
    const devBadge = e.device_id ? `<span class="ipam-dev-badge" title="Auto-populated from device">🔗</span>` : '';
    const nameText = e.name ? devBadge + esc(e.name) : '<span style="color:var(--text3)">—</span>';
    const dns = e.dns_name || '';
    const dnsDisplay = dns.length > 30 ? dns.slice(0, 28) + '…' : dns;
    return `<tr class="${used ? 'ipam-row-used' : 'ipam-row-free'}">
      <td class="ipam-ip">${esc(e.ip)}</td>
      <td style="font-size:11px;color:var(--text3)">${esc(e.subnetLabel)}</td>
      <td>${nameText}</td>
      <td class="ipam-dns" title="${esc(dns)}">${dnsDisplay ? esc(dnsDisplay) : '<span class="ipam-ts">—</span>'}</td>
      <td>${badge}</td>
      <td>${_ipamLicBadge(e.device_id)}</td>
    </tr>`;
  }).join('');
  wrap.innerHTML = `
    <table class="ipam-tbl">
      <thead>
        <tr>
          <th>IP Address</th>
          <th>Subnet</th>
          <th>Name / Description</th>
          <th>DNS</th>
          <th>Status</th>
          <th>Licenses</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;
  if (pg) pg.innerHTML = `<span style="color:var(--text3);font-size:11px">${results.length} result${results.length === 1 ? '' : 's'} across all subnets</span>`;
}

function _ipamApplyFilter(q) {
  const lq = (q || '').toLowerCase().trim();
  let arr = _ipamAllIps;

  // Text search
  if (lq) {
    arr = arr.filter(e =>
      e.ip.includes(lq) || e.name.toLowerCase().includes(lq) ||
      (e.dns_name || '').toLowerCase().includes(lq)
    );
  }
  // Status column filter
  if (_ipamFilterStatus === 'used')  arr = arr.filter(e => !!e.name);
  if (_ipamFilterStatus === 'free')  arr = arr.filter(e => !e.name);
  // License column filter
  if (_ipamFilterLic) {
    arr = arr.filter(e => _ipamLicVal(e) === _ipamFilterLic);
  }
  // Sort
  _ipamFiltered = arr.slice().sort(_ipamSortCmp);
  _ipamRenderTable();
}

// ── Table rendering ────────────────────────────────────────────────────────
function _ipamRenderTable() {
  const wrap = document.getElementById('ipam-table-wrap');
  if (!wrap) return;

  const total  = _ipamFiltered.length;
  const start  = _ipamPage * _IPAM_PAGE_SIZE;
  const end    = Math.min(start + _IPAM_PAGE_SIZE, total);
  const page   = _ipamFiltered.slice(start, end);
  const canEdit = S.role === 'operator' || S.role === 'admin';

  if (!total) {
    wrap.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">No IPs match your search.</div>';
    const pg = document.getElementById('ipam-pg');
    if (pg) pg.innerHTML = '';
    return;
  }

  const rows = page.map(e => {
    const used    = !!e.name;
    const badge   = used
      ? `<span class="ipam-used">Used</span>`
      : `<span class="ipam-free">Free</span>`;
    const dateStr = e.modified_at
      ? new Date(e.modified_at * 1000).toLocaleString()
      : '—';
    const devBadge = e.device_id
      ? `<span class="ipam-dev-badge" title="Auto-populated from device">🔗</span>`
      : '';
    const nameText = e.name
      ? devBadge + esc(e.name)
      : (canEdit ? '<span style="color:var(--text3);font-style:italic">click to assign…</span>' : '<span style="color:var(--text3)">—</span>');
    const nameCell = canEdit
      ? `<td class="ipam-name-cell" onclick="_ipamEditCell(this,'${esc(e.ip)}')">${nameText}</td>`
      : `<td>${nameText}</td>`;
    const dns = e.dns_name || '';
    const dnsDisplay = dns.length > 35 ? dns.slice(0, 33) + '…' : dns;
    const dnsCell = `<td class="ipam-dns" title="${esc(dns)}">${dnsDisplay ? esc(dnsDisplay) : '<span class="ipam-ts">—</span>'}</td>`;
    return `<tr class="${used ? 'ipam-row-used' : 'ipam-row-free'}">
      <td class="ipam-ip">${esc(e.ip)}</td>
      ${nameCell}
      ${dnsCell}
      <td>${badge}</td>
      <td>${_ipamLicBadge(e.device_id)}</td>
      <td class="ipam-ts">${esc(e.modified_by || '—')}</td>
      <td class="ipam-ts">${dateStr}</td>
    </tr>`;
  }).join('');

  // Status filter dropdown
  const stOpts = [['','All'],['used','Used'],['free','Free']];
  const stSel = stOpts.map(([v,l]) =>
    `<option value="${v}"${_ipamFilterStatus===v?' selected':''}>${l}</option>`
  ).join('');

  // License filter dropdown
  const licOpts = [['','All'],['ok','Valid'],['warn','Expiring'],['crit','Expired'],['none','None']];
  const licSel = licOpts.map(([v,l]) =>
    `<option value="${v}"${_ipamFilterLic===v?' selected':''}>${l}</option>`
  ).join('');

  wrap.innerHTML = `
    <table class="ipam-tbl">
      <thead>
        <tr>
          ${_ipamThHtml('ip', 'IP Address')}
          ${_ipamThHtml('name', 'Name / Description')}
          ${_ipamThHtml('dns', 'DNS')}
          <th class="ipam-th-filter">
            <span class="ipam-th-sort${_ipamSortCol==='status'?' ipam-th-active':''}"
                  onclick="_ipamSetSort('status')">Status${_ipamSortArrow('status')}</span>
            <select class="ipam-col-filter" onchange="_ipamSetFilterStatus(this.value)">${stSel}</select>
          </th>
          <th class="ipam-th-filter">
            <span class="ipam-th-sort${_ipamSortCol==='license'?' ipam-th-active':''}"
                  onclick="_ipamSetSort('license')">Licenses${_ipamSortArrow('license')}</span>
            <select class="ipam-col-filter" onchange="_ipamSetFilterLic(this.value)">${licSel}</select>
          </th>
          ${_ipamThHtml('modified_by', 'Modified By')}
          ${_ipamThHtml('modified_at', 'Last Modified')}
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;

  // Pagination controls
  const pg = document.getElementById('ipam-pg');
  if (pg) {
    const totalPages = Math.ceil(total / _IPAM_PAGE_SIZE);
    if (totalPages <= 1) {
      pg.innerHTML = `<span>Showing ${total} IPs</span>`;
    } else {
      pg.innerHTML = `
        <span>Showing ${start + 1}–${end} of ${total}</span>
        <button class="btn-sm" onclick="_ipamPrevPage()" ${_ipamPage === 0 ? 'disabled' : ''}>‹</button>
        <span>${_ipamPage + 1} / ${totalPages}</span>
        <button class="btn-sm" onclick="_ipamNextPage()" ${end >= total ? 'disabled' : ''}>›</button>`;
    }
  }
}

function _ipamPrevPage() {
  if (_ipamPage > 0) { _ipamPage--; _ipamRenderTable(); }
}
function _ipamNextPage() {
  const totalPages = Math.ceil(_ipamFiltered.length / _IPAM_PAGE_SIZE);
  if (_ipamPage < totalPages - 1) { _ipamPage++; _ipamRenderTable(); }
}

// ── Inline name editing ────────────────────────────────────────────────────
function _ipamEditCell(td, ip) {
  if (S.role !== 'operator' && S.role !== 'admin') return;
  const entry  = _ipamAllIps.find(e => e.ip === ip);
  const curVal = entry ? entry.name : '';
  td.innerHTML = `<input class="ipam-name-inp" id="ipam-inp-${ip.replace(/\./g,'-')}"
    value="${esc(curVal)}" placeholder="Enter name…" autocomplete="off"/>`;
  const inp = td.querySelector('input');
  if (!inp) return;
  inp.focus();
  inp.select();

  const commit = async () => {
    const newName = inp.value.trim().slice(0, 120);
    if (newName === curVal) { _ipamRenderTable(); return; }
    const r = await fetch(`/api/ipam/ips/${_ipamSelectedId}/${encodeURIComponent(ip)}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: newName}),
    });
    if (!r.ok) { toast('Failed to save', 'err'); _ipamRenderTable(); return; }
    // Update in-memory record; manual edit clears device link
    if (entry) {
      entry.name        = newName;
      entry.modified_by = S.username || '';
      entry.modified_at = Date.now() / 1000;
      entry.device_id   = '';   // user took ownership
    }
    _ipamGlobalCache = null;  // invalidate cross-subnet cache
    const search = document.getElementById('ipam-search')?.value || '';
    _ipamApplyFilter(search);
    toast(newName ? `${ip} → "${newName}"` : `${ip} cleared`, 'ok');
  };

  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); _ipamRenderTable(); }
  });
  inp.addEventListener('blur', commit);
}

// ── Add subnet modal ───────────────────────────────────────────────────────
function _ipamOpenAddSubnet() {
  closeM('ipam-add-modal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'ipam-add-modal';
  _overlayClose(o, () => closeM('ipam-add-modal'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,400px)">
      <div class="mhd">
        <div class="mttl">📋 Add Subnet</div>
        <button class="mclose" onclick="closeM('ipam-add-modal')">✕</button>
      </div>
      <div class="mbdy" style="gap:12px">
        <div class="fr">
          <label class="fl">CIDR <span style="color:var(--text3);font-size:10px">(e.g. 192.168.1.0/24)</span></label>
          <input type="text" id="ipam-add-cidr" placeholder="192.168.1.0/24"
            style="font-family:'Courier New',monospace" autocomplete="off" autocorrect="off" spellcheck="false"/>
        </div>
        <div class="fr">
          <label class="fl">Label <span style="color:var(--text3);font-size:10px">(optional)</span></label>
          <input type="text" id="ipam-add-name" placeholder="e.g. Office LAN" autocomplete="off"/>
        </div>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('ipam-add-modal')">Cancel</button>
        <button class="btn-p" id="ipam-add-save" onclick="_ipamSaveSubnet()">Add Subnet</button>
      </div>
    </div>`;
  document.body.appendChild(o);
  // Allow Enter to submit
  o.querySelector('#ipam-add-cidr').addEventListener('keydown', e => {
    if (e.key === 'Enter') _ipamSaveSubnet();
  });
  o.querySelector('#ipam-add-name').addEventListener('keydown', e => {
    if (e.key === 'Enter') _ipamSaveSubnet();
  });
  setTimeout(() => o.querySelector('#ipam-add-cidr').focus(), 50);
}

async function _ipamSaveSubnet() {
  const cidr = (document.getElementById('ipam-add-cidr')?.value || '').trim();
  const name = (document.getElementById('ipam-add-name')?.value || '').trim();
  if (!cidr) { toast('Please enter a CIDR', 'warn'); return; }

  const btn = document.getElementById('ipam-add-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }

  const r = await fetch('/api/ipam/subnets', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cidr, name}),
  });
  const d = await r.json();
  if (btn) { btn.disabled = false; btn.textContent = 'Add Subnet'; }

  if (!r.ok) {
    toast(d.error || 'Failed to add subnet', 'err');
    return;
  }
  closeM('ipam-add-modal');
  toast(`Subnet ${cidr} added`, 'ok');
  _ipamGlobalCache = null;
  await _ipamLoadSubnets();
  // Auto-select the new subnet
  if (d.id) _ipamOnSubnetChange(d.id);
}

// ── Rename subnet ──────────────────────────────────────────────────────────
// ── Edit Subnet — unified modal for all per-subnet config ───────────────
function _ipamOpenEdit() {
  if (!_ipamSelectedId) return;
  const sub = _ipamSubnets.find(s => s.id === _ipamSelectedId);
  if (!sub) return;
  closeM('ipam-edit-modal');

  const adChecked   = !!(sub.auto_discover | 0);
  const alreadyHadFirstScan = !!(sub.first_scan_approved | 0) || !!sub.last_auto_scan_ts;
  const lastScanStr = sub.last_auto_scan_ts || '—';

  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'ipam-edit-modal';
  o.dataset.adApprove = '0';
  _overlayClose(o, () => closeM('ipam-edit-modal'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,520px)">
      <div class="mhd">
        <div class="mttl">⚙ Edit Subnet</div>
        <button class="mclose" onclick="closeM('ipam-edit-modal')">✕</button>
      </div>
      <div class="mbdy" style="gap:14px">

        <div class="ipam-edit-sec">
          <div class="ipam-edit-hd">General</div>
          <div class="fr">
            <label class="fl">CIDR</label>
            <input type="text" value="${esc(sub.cidr)}" disabled
                   style="font-family:ui-monospace,Consolas,monospace;opacity:.6"/>
          </div>
          <div class="fr">
            <label class="fl">Label</label>
            <input type="text" id="ipam-edit-name" value="${esc(sub.name||'')}"
                   placeholder="e.g. Office LAN" autocomplete="off" maxlength="80"/>
          </div>
        </div>

        <div class="ipam-edit-sec">
          <div class="ipam-edit-hd">Auto-Discovery</div>
          <label class="cb-row" style="padding:4px 0">
            <input type="checkbox" id="ipam-edit-ad" ${adChecked?'checked':''}/>
            <span>Auto-discover new hosts in this subnet</span>
          </label>
          <div class="fh" style="margin-bottom:6px">
            Periodically scan this subnet. New hosts become devices with a ping sensor
            plus any services the Port Scanner detects. Global cadence + safety rails
            live in Settings → 📡 Auto-Discovery.
          </div>
          <div id="ipam-ad-confirm" class="ipam-ad-confirm" style="display:${(!adChecked || alreadyHadFirstScan)?'none':'flex'}">
            <div style="font-size:13px;font-weight:600;margin-bottom:4px">⚠ First-scan cap applies</div>
            <div class="fh">Up to the configured cap of new devices will be auto-added on the first scan.
            Hosts you later delete won't be re-added automatically.</div>
            <button class="btn-p" id="ipam-ad-confirm-btn" style="margin-top:8px;align-self:flex-start">
              Got it — Enable Auto-Discovery
            </button>
          </div>
          <div id="ipam-ad-size-warn" class="ipam-ad-confirm" style="display:none;border-color:var(--warn-border);background:var(--warn-bg)">
            <div style="font-size:13px;font-weight:600;margin-bottom:4px">⏱ Large subnet — scan may exceed deadline</div>
            <div class="fh" id="ipam-ad-size-warn-msg"></div>
          </div>
          <div class="fr">
            <label class="fl">DNS server <span class="fh" style="margin-left:6px">(optional)</span></label>
            <input type="text" id="ipam-edit-dns" value="${esc(sub.dns_server||'')}"
                   placeholder="e.g. 10.0.0.53 — empty = use system resolver"
                   autocomplete="off" maxlength="255"/>
          </div>
          <div class="fh">
            Used for reverse-DNS naming of auto-added devices in this subnet. Leave empty
            to use the server's default resolver.
          </div>
          <div class="ipam-edit-meta">
            Last auto-scan: <strong>${esc(String(lastScanStr))}</strong>
          </div>
        </div>

      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('ipam-edit-modal')">Cancel</button>
        <button class="btn-p" id="ipam-edit-save" onclick="_ipamSaveEdit()">Save</button>
      </div>
    </div>`;
  document.body.appendChild(o);

  const adCb      = o.querySelector('#ipam-edit-ad');
  const adConfirm = o.querySelector('#ipam-ad-confirm');
  const adConfBtn = o.querySelector('#ipam-ad-confirm-btn');
  const adSizeWarn = o.querySelector('#ipam-ad-size-warn');
  const adSizeMsg  = o.querySelector('#ipam-ad-size-warn-msg');
  const saveBtn   = o.querySelector('#ipam-edit-save');

  // Threshold: /20 = 4096 hosts. Default 300s deadline starts to feel tight
  // around here; /16 will almost certainly time out on real networks.
  const _SIZE_WARN_PREFIX = 20;
  const prefix = parseInt(String(sub.cidr || '').split('/')[1], 10);
  const isV4   = !String(sub.cidr || '').includes(':');
  const showSizeWarn = isV4 && Number.isFinite(prefix) && prefix <= _SIZE_WARN_PREFIX;
  if (showSizeWarn && adSizeMsg) {
    const hosts = Math.pow(2, 32 - prefix);
    adSizeMsg.textContent =
      `This subnet has ${hosts.toLocaleString()} addresses. The default scan ` +
      `deadline (5 min) may not be enough — scans that exceed it are abandoned ` +
      `and no devices are added. Consider raising auto_discover_scan_deadline_s ` +
      `in Settings → Retention, or splitting this into smaller blocks (e.g. /24s).`;
  }

  function _syncConfirm() {
    const checked = adCb.checked;
    if (!checked || alreadyHadFirstScan) {
      adConfirm.style.display = 'none';
      o.dataset.adApprove = '0';
      if (saveBtn) saveBtn.disabled = false;
    } else {
      adConfirm.style.display = 'flex';
      o.dataset.adApprove = '0';
      if (saveBtn) saveBtn.disabled = true;
    }
    if (adSizeWarn) {
      adSizeWarn.style.display = (checked && showSizeWarn) ? 'flex' : 'none';
    }
  }

  if (adCb) adCb.addEventListener('change', _syncConfirm);
  // Initial sync — show warning if AD is already on for a large subnet.
  _syncConfirm();

  if (adConfBtn) {
    adConfBtn.addEventListener('click', () => {
      o.dataset.adApprove = '1';
      adConfirm.style.display = 'none';
      if (saveBtn) saveBtn.disabled = false;
    });
  }

  // Pre-migration state: auto_discover already on but cap never approved — gate save
  if (adChecked && !alreadyHadFirstScan && saveBtn) saveBtn.disabled = true;

  const inp = o.querySelector('#ipam-edit-name');
  if (inp) {
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') _ipamSaveEdit(); });
    setTimeout(() => { inp.focus(); inp.select(); }, 50);
  }
}

async function _ipamSaveEdit() {
  if (!_ipamSelectedId) return;
  const sub = _ipamSubnets.find(s => s.id === _ipamSelectedId);
  if (!sub) return;
  const btn = document.getElementById('ipam-edit-save');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  const modal = document.getElementById('ipam-edit-modal');
  const body = {
    name:          (document.getElementById('ipam-edit-name')?.value || '').trim(),
    auto_discover: !!document.getElementById('ipam-edit-ad')?.checked ? 1 : 0,
    dns_server:    (document.getElementById('ipam-edit-dns')?.value || '').trim(),
  };
  if (modal?.dataset.adApprove === '1') body.approve_first_scan = 1;

  try {
    const r = await fetch(`/api/ipam/subnets/${_ipamSelectedId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      toast(d.error || 'Save failed', 'err');
      if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
      return;
    }
    closeM('ipam-edit-modal');
    toast('Subnet updated', 'ok');
    _ipamGlobalCache = null;
    await _ipamLoadSubnets();
  } catch {
    toast('Network error', 'err');
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
}


// ── Remove subnet ──────────────────────────────────────────────────────────
function _ipamRemoveSubnet() {
  if (!_ipamSelectedId) return;
  const sub = _ipamSubnets.find(s => s.id === _ipamSelectedId);
  if (!sub) return;
  const label = sub.cidr + (sub.name ? ` (${sub.name})` : '');

  closeM('ipam-rm-modal');
  const o = document.createElement('div');
  o.className = 'mo'; o.id = 'ipam-rm-modal';
  _overlayClose(o, () => closeM('ipam-rm-modal'));
  o.innerHTML = `
    <div class="mbox" style="width:min(95vw,380px)">
      <div class="mhd">
        <div class="mttl" style="color:var(--down)">⚠ Remove Subnet</div>
        <button class="mclose" onclick="closeM('ipam-rm-modal')">✕</button>
      </div>
      <div class="mbdy">
        <p style="margin:0;font-size:13px;color:var(--text2)">
          Remove <strong style="color:var(--text)">${esc(label)}</strong>?<br/>
          <span style="color:var(--text3);font-size:11px">All IP name assignments for this subnet will be deleted.</span>
        </p>
      </div>
      <div class="mft">
        <button class="btn-s" onclick="closeM('ipam-rm-modal')">Cancel</button>
        <button class="btn-p" style="background:var(--down)" id="ipam-rm-confirm" onclick="_ipamConfirmRemove()">Remove</button>
      </div>
    </div>`;
  document.body.appendChild(o);
}

async function _ipamConfirmRemove() {
  const btn = document.getElementById('ipam-rm-confirm');
  if (btn) { btn.disabled = true; btn.textContent = 'Removing…'; }
  const r = await fetch(`/api/ipam/subnets/${_ipamSelectedId}`, {method: 'DELETE'});
  if (!r.ok) {
    toast('Failed to remove subnet', 'err');
    if (btn) { btn.disabled = false; btn.textContent = 'Remove'; }
    return;
  }
  closeM('ipam-rm-modal');
  const sub = _ipamSubnets.find(s => s.id === _ipamSelectedId);
  toast(`Subnet ${sub?.cidr || ''} removed`, 'ok');
  _ipamSelectedId  = null;
  _ipamAllIps      = [];
  _ipamFiltered    = [];
  _ipamGlobalCache = null;
  await _ipamLoadSubnets();
}

// ── DNS Refresh ────────────────────────────────────────────────────────────

async function _ipamReloadCurrentSubnet() {
  if (!_ipamSelectedId) return;
  const r = await fetch(`/api/ipam/subnets/${_ipamSelectedId}/ips`);
  if (!r.ok) return;
  const d = await r.json();
  const allocs = d.allocations || {};
  const ips = _ipamExpandCidr(d.subnet.cidr);
  _ipamAllIps = ips.map(ip => {
    const a = allocs[ip];
    return a
      ? { ip, name: a.name, modified_by: a.modified_by, modified_at: a.modified_at,
          device_id: a.device_id || '', dns_name: a.dns_name || '', dns_resolved_at: a.dns_resolved_at || 0 }
      : { ip, name: '', modified_by: '', modified_at: 0, device_id: '', dns_name: '', dns_resolved_at: 0 };
  });
  const search = document.getElementById('ipam-search')?.value || '';
  _ipamApplyFilter(search);
}

function _ipamCancelDnsInterval() {
  if (_ipamDnsInterval) {
    clearInterval(_ipamDnsInterval);
    _ipamDnsInterval = null;
  }
}

async function _ipamRefreshDns() {
  if (!_ipamSelectedId) return;
  const btn = document.getElementById('ipam-dns-btn');
  _ipamCancelDnsInterval();   // cancel any previous in-flight poll
  if (btn) { btn.disabled = true; btn.textContent = 'Refreshing…'; }
  const resetBtn = () => { if (btn) { btn.disabled = false; btn.textContent = 'Refresh DNS'; } };
  try {
    const r = await fetch(`/api/ipam/subnets/${_ipamSelectedId}/dns/refresh`, {method: 'POST'});
    if (r.status === 409) { toast('DNS refresh already in progress', 'warn'); resetBtn(); return; }
    if (!r.ok) { toast('DNS refresh failed', 'err'); resetBtn(); return; }
    let polls = 0;
    _ipamDnsInterval = setInterval(async () => {
      polls++;
      await _ipamReloadCurrentSubnet();
      if (polls >= 20) {
        _ipamCancelDnsInterval();
        resetBtn();
      }
    }, 3000);
  } catch {
    toast('DNS refresh failed', 'err');
    resetBtn();
  }
}
