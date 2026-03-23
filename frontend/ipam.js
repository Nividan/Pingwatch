// ── IP Addresses (IPAM) Tab ─────────────────────────────────────────────────
// Lightweight IP address management: define subnets (CIDR), auto-generate
// all usable host IPs, assign names to individual IPs, track who changed what.

let _ipamSubnets      = [];     // [{id, cidr, name, created_by, created_at}]
let _ipamSelectedId   = null;   // currently selected subnet id
let _ipamAllIps       = [];     // full merged list [{ip, name, modified_by, modified_at, device_id}]
let _ipamFiltered     = [];     // search-filtered view of _ipamAllIps
let _ipamPage         = 0;      // current page (0-based)
let _ipamShellInited  = false;  // shell HTML built once; data always refreshed on tab switch
const _IPAM_PAGE_SIZE = 200;

// ── Init ───────────────────────────────────────────────────────────────────
async function _ipamInit() {
  // Build the toolbar/shell only once; always re-fetch data on tab switch
  if (!_ipamShellInited) {
    _ipamShellInited = true;
    _ipamRenderShell();
  }
  await _ipamLoadSubnets();
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
      <button class="btn-sm rbac-op" id="ipam-rm-btn" onclick="_ipamRemoveSubnet()" disabled style="color:var(--down)">✕ Remove</button>
      <div style="width:1px;height:18px;background:var(--border);margin:0 4px"></div>
      <input class="ipam-search" id="ipam-search" type="search" placeholder="🔍  Search IP or name…"
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

// ── Subnet loading ─────────────────────────────────────────────────────────
async function _ipamLoadSubnets() {
  const r = await fetch('/api/ipam/subnets');
  if (r.status === 401) { showLogin('Session expired'); return; }
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
  const id = parseInt(idVal);
  if (!id) {
    _ipamSelectedId = null;
    document.getElementById('ipam-rm-btn')?.setAttribute('disabled', '');
    _ipamShowEmptyTable('Select a subnet above to view its IP addresses.');
    return;
  }
  _ipamSelectedId = id;
  document.getElementById('ipam-rm-btn')?.removeAttribute('disabled');
  // Keep select in sync
  const sel = document.getElementById('ipam-sel');
  if (sel) sel.value = id;
  _ipamShowEmptyTable('Loading…');
  const r = await fetch(`/api/ipam/subnets/${id}/ips`);
  if (!r.ok) { toast('Failed to load IPs', 'err'); return; }
  const d = await r.json();
  const subnet = d.subnet;
  const allocs = d.allocations || {};   // {ip: {name, modified_by, modified_at}}

  // Generate all usable IPs from CIDR, merge with allocations
  const ips = _ipamExpandCidr(subnet.cidr);
  _ipamAllIps = ips.map(ip => {
    const a = allocs[ip];
    return a
      ? { ip, name: a.name, modified_by: a.modified_by, modified_at: a.modified_at,
          device_id: a.device_id || '' }
      : { ip, name: '', modified_by: '', modified_at: 0, device_id: '' };
  });
  // Sort: Used (named) first, then Free, both groups sorted numerically by IP
  _ipamAllIps.sort((a, b) => {
    if (!!a.name !== !!b.name) return a.name ? -1 : 1;
    return _ipamIpCmp(a.ip, b.ip);
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

// ── Search / filter ────────────────────────────────────────────────────────
function _ipamOnSearch(val) {
  _ipamPage = 0;
  _ipamApplyFilter(val);
}

function _ipamApplyFilter(q) {
  const lq = (q || '').toLowerCase().trim();
  if (lq) {
    _ipamFiltered = _ipamAllIps.filter(e =>
      e.ip.includes(lq) || e.name.toLowerCase().includes(lq)
    );
  } else {
    _ipamFiltered = _ipamAllIps;
  }
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
    return `<tr class="${used ? 'ipam-row-used' : 'ipam-row-free'}">
      <td class="ipam-ip">${esc(e.ip)}</td>
      ${nameCell}
      <td>${badge}</td>
      <td class="ipam-ts">${esc(e.modified_by || '—')}</td>
      <td class="ipam-ts">${dateStr}</td>
    </tr>`;
  }).join('');

  wrap.innerHTML = `
    <table class="ipam-tbl">
      <thead>
        <tr>
          <th>IP Address</th>
          <th>Name / Description</th>
          <th>Status</th>
          <th>Modified By</th>
          <th>Last Modified</th>
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
    // Re-sort (named IPs float to top)
    const search = document.getElementById('ipam-search')?.value || '';
    _ipamAllIps.sort((a, b) => {
      if (!!a.name !== !!b.name) return a.name ? -1 : 1;
      return _ipamIpCmp(a.ip, b.ip);
    });
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
  _ipamInited = false;
  await _ipamLoadSubnets();
  // Auto-select the new subnet
  if (d.id) _ipamOnSubnetChange(d.id);
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
  _ipamSelectedId = null;
  _ipamAllIps     = [];
  _ipamFiltered   = [];
  _ipamInited     = false;
  await _ipamLoadSubnets();
}
