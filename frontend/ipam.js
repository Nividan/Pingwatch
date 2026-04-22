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
    <div class="ipam-hdr">
      <select class="ipam-sel" id="ipam-sel" onchange="_ipamOnSubnetChange(this.value)">
        <option value="">— Select a subnet —</option>
      </select>
      <button class="btn-sm btn-accent rbac-op" onclick="_ipamOpenAddSubnet()">＋ Add Subnet</button>
      <button class="btn-sm rbac-op" id="ipam-edit-btn" onclick="_ipamOpenEdit()" disabled title="Edit subnet name, auto-discovery, DNS server">⚙ Edit</button>
      <button class="btn-sm rbac-op" id="ipam-rm-btn" onclick="_ipamRemoveSubnet()" disabled style="color:var(--down)">✕ Remove</button>
      <button class="btn-sm rbac-op" id="ipam-dns-btn" onclick="_ipamRefreshDns()" style="display:none" title="Resolve DNS hostnames for all IPs in this subnet">Refresh DNS</button>
      <div style="width:1px;height:18px;background:var(--border);margin:0 4px"></div>
      <input class="ipam-search" id="ipam-search" type="search" placeholder="🔍  Search IP, name or DNS…"
             oninput="_ipamOnSearch(this.value)" autocomplete="off"/>
      <div class="ipam-pg" id="ipam-pg"></div>
    </div>
    <div id="ipam-table-wrap">
      <div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">
        Add a subnet to get started.
      </div>
    </div>`;
  applyRbac();
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
    document.getElementById('ipam-ren-btn')?.setAttribute('disabled', '');
    _ipamShowEmptyTable('Select a subnet above to view its IP addresses.');
  }
}

function _ipamRenderSubnetSelect() {
  const sel = document.getElementById('ipam-sel');
  if (!sel) return;
  const cur = _ipamSelectedId;
  sel.innerHTML = '<option value="">— Select a subnet —</option>' +
    _ipamSubnets.map(s =>
      `<option value="${s.id}" ${s.id === cur ? 'selected' : ''}>
        ${esc(s.cidr)}${s.name ? '  —  ' + esc(s.name) : ''}
       </option>`
    ).join('');
}

function _ipamShowEmptyTable(msg) {
  const wrap = document.getElementById('ipam-table-wrap');
  if (wrap) wrap.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">${msg}</div>`;
  const pg = document.getElementById('ipam-pg');
  if (pg) pg.innerHTML = '';
}

// ── Subnet selection ───────────────────────────────────────────────────────
async function _ipamOnSubnetChange(idVal) {
  _ipamCancelDnsInterval();   // cancel any in-flight DNS poll for previous subnet
  const id = parseInt(idVal);
  if (!id) {
    _ipamSelectedId = null;
    document.getElementById('ipam-rm-btn')?.setAttribute('disabled', '');
    document.getElementById('ipam-edit-btn')?.setAttribute('disabled', '');
    _ipamShowEmptyTable('Select a subnet above to view its IP addresses.');
    return;
  }
  _ipamSelectedId = id;
  _ipamSortCol = 'status_ip'; _ipamSortDir = 1;
  _ipamFilterStatus = ''; _ipamFilterLic = '';
  document.getElementById('ipam-rm-btn')?.removeAttribute('disabled');
  document.getElementById('ipam-edit-btn')?.removeAttribute('disabled');
  // Keep select in sync
  const sel = document.getElementById('ipam-sel');
  if (sel) sel.value = id;
  _ipamShowEmptyTable('Loading…');
  const r = await fetch(`/api/ipam/subnets/${id}/ips`);
  if (!r.ok) { toast('Failed to load IPs', 'err'); return; }
  const d = await r.json();
  const subnet = d.subnet;
  const allocs = d.allocations || {};   // {ip: {name, modified_by, modified_at}}

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

  const search = document.getElementById('ipam-search')?.value || '';
  _ipamPage = 0;
  _ipamApplyFilter(search);
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
  const saveBtn   = o.querySelector('#ipam-edit-save');

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
  }

  if (adCb) adCb.addEventListener('change', _syncConfirm);

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
