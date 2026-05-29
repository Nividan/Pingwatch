/* PingWatch — Live Map (NOC console + Site drill-in).
 * Loaded by /livemap.html in an iframe under index.html → #liveMapView.
 *
 * State flow
 * ──────────
 *   /api/livemap/sites          → sidebar rows + mosaic
 *   /api/livemap/noc/summary    → hero stats + widgets
 *   /api/livemap/sites/:n/tree  → drill-in tier tree
 *
 * URL hash routes:
 *   #/noc           NOC Overview (default)
 *   #/site/<name>   Drill-in for a specific site
 *
 * SSE updates arrive via postMessage from the parent app.js
 * (extends the existing _sseBatch pattern to fan out to this iframe).
 */
(function() {
'use strict';

// ─── Tiny helpers ────────────────────────────────────────────
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function $(id) { return document.getElementById(id); }
function ce(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
async function api(method, url, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(method + ' ' + url + ' ' + r.status);
  return r.json();
}
function debounce(fn, ms) {
  let t;
  return function() {
    const ctx = this, args = arguments;
    clearTimeout(t);
    t = setTimeout(function() { fn.apply(ctx, args); }, ms);
  };
}
function timeAgo(ts) {
  if (!ts) return '';
  const sec = Math.floor(Date.now() / 1000 - ts);
  if (sec < 60)    return sec + 's';
  if (sec < 3600)  return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h';
  return Math.floor(sec / 86400) + 'd';
}
function kindAbbrev(kind) {
  switch ((kind || '').toLowerCase()) {
    case 'lab':      return 'LAB';
    case 'dc':       return 'DC';
    case 'hq':       return 'HQ';
    case 'pop':      return 'PoP';
    case 'edge':     return 'EDG';
    case 'office':   return 'OFC';
    case 'internet': return 'INT';
    default:         return 'LAB';
  }
}
function worstStatus(s) {
  if (s.down) return 'down';
  if (s.warn) return 'warn';
  if (s.up || s.devices === 0) return 'up';
  return 'unknown';
}

// ─── Global state ────────────────────────────────────────────
const LM = {
  sites:         [],
  sitesByName:   {},
  summary:       null,
  treeCache:     {},        // {siteName: treePayload}
  currentRoute:  { view: 'noc', site: null },
  ssePending:    [],
  sseTimer:      null,
  liveTickTimer: null,      // periodic time-ago refresh
};

// Inline SVG icons (Heroicons-ish), kept tiny + cyan-tinted
const ICONS = {
  isp:    '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>',
  wan:    '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 17l4-10 4 6 4-4 4 8"/><circle cx="4" cy="17" r="1.5" fill="currentColor"/><circle cx="20" cy="19" r="1.5" fill="currentColor"/></svg>',
  fw:     '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/></svg>',
  core:   '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="7" width="18" height="10" rx="1"/><circle cx="7"  cy="12" r="0.9" fill="currentColor"/><circle cx="11" cy="12" r="0.9" fill="currentColor"/><circle cx="15" cy="12" r="0.9" fill="currentColor"/><circle cx="19" cy="12" r="0.9" fill="currentColor"/><path d="M3 11h18"/></svg>',
  sw:     '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="9" width="18" height="6" rx="1"/><circle cx="7" cy="12" r="0.7" fill="currentColor"/><circle cx="11" cy="12" r="0.7" fill="currentColor"/><circle cx="15" cy="12" r="0.7" fill="currentColor"/><circle cx="19" cy="12" r="0.7" fill="currentColor"/></svg>',
  ap:     '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 11a10 10 0 0 1 14 0"/><path d="M8 14a6 6 0 0 1 8 0"/><path d="M10.5 17a3 3 0 0 1 3 0"/><circle cx="12" cy="19.5" r="0.9" fill="currentColor"/></svg>',
  hyp:    '<svg class="cluster-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><circle cx="6.5" cy="7" r="0.6" fill="currentColor"/><circle cx="6.5" cy="17" r="0.6" fill="currentColor"/></svg>',
  chassis:'<svg class="cluster-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="1"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>',
  vm:     '<svg class="cluster-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="16" height="14" rx="1"/><path d="M9 21h6M12 18v3"/></svg>',
  ipmi:   '<svg class="cluster-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
  cloud:  '<svg class="cluster-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 18a4 4 0 0 1 0-8 5 5 0 0 1 9.5-1A4 4 0 0 1 17 18H7z"/></svg>',
};

// ─── Sidebar / scope bar rendering ──────────────────────────
function renderScopeBar() {
  const total = LM.sites.length;
  const focus = LM.currentRoute.view === 'site' ? LM.currentRoute.site : 'NOC';
  $('scope-name').textContent = total + ' SITES · ' + (focus || 'NOC');

  // Pills: up/warn/down/devices (totals)
  let up = 0, warn = 0, down = 0, devs = 0;
  for (const s of LM.sites) {
    up += s.up; warn += s.warn; down += s.down; devs += s.devices;
  }
  $('scope-counts').innerHTML =
    '<span class="pill up"><span class="pill-dot" style="background:var(--up)"></span>' + up + ' UP</span>' +
    '<span class="pill warn"><span class="pill-dot" style="background:var(--warn)"></span>' + warn + ' WARN</span>' +
    '<span class="pill down"><span class="pill-dot" style="background:var(--down)"></span>' + down + ' DOWN</span>' +
    '<span class="pill dev"><span class="pill-dot" style="background:var(--accent)"></span>' + devs + ' DEVICES</span>';
}

function _siteRow(site) {
  const status = worstStatus(site);
  const kind = (site.kind || 'lab').toLowerCase();
  const abbrev = kindAbbrev(kind);
  const selClass = (LM.currentRoute.view === 'site' && LM.currentRoute.site === site.name) ? ' sel' : '';
  const alerts = site.alerts ? '<span class="sr-alerts">' + site.alerts + '</span>' : '';
  const display = site.display_name || site.name;
  return '<div class="site-row' + selClass + '" data-site="' + esc(site.name) + '">' +
           '<span class="sr-dot ' + status + '"></span>' +
           '<span class="sr-kind ' + esc(kind) + '">' + esc(abbrev) + '</span>' +
           '<span class="sr-name">' + esc(display) + '</span>' +
           '<span class="sr-meta">' +
              '<span class="sr-count">' + site.devices + '</span>' +
              alerts +
           '</span>' +
         '</div>';
}

function _viewRow() {
  const sel = LM.currentRoute.view === 'noc' ? ' sel' : '';
  return '<div class="site-row' + sel + '" data-view="noc">' +
           '<span class="sr-dot up"></span>' +
           '<span class="sr-kind noc">NOC</span>' +
           '<span class="sr-name">Overview</span>' +
           '<span class="sr-meta"><span class="sr-count">all</span></span>' +
         '</div>';
}

function renderSidebar() {
  const q = ($('lm-search').value || '').trim().toLowerCase();
  const all = LM.sites.filter(function(s) {
    return !q || s.name.toLowerCase().indexOf(q) >= 0 ||
                 (s.display_name || '').toLowerCase().indexOf(q) >= 0;
  });
  const pinned = all.filter(function(s) { return s.pinned; });
  const rest   = all.filter(function(s) { return !s.pinned; });

  $('lm-list-view').innerHTML   = _viewRow();
  $('lm-list-pinned').innerHTML = pinned.length
    ? pinned.map(function(s) { return _siteRow(s); }).join('')
    : '<div class="lm-empty" style="padding:6px;text-align:left">— none</div>';
  $('lm-list-all').innerHTML    = rest.length
    ? rest.map(function(s) { return _siteRow(s); }).join('')
    : '<div class="lm-empty" style="padding:6px;text-align:left">— no sites yet</div>';

  $('lm-search-count').textContent = String(LM.sites.length);
}

// Sidebar event delegation — runs once. Click navigates; right-click opens a
// small context menu for pin/unpin + edit (Edit Site itself still uses the
// shared forms-site.js modal exposed on window.openSiteModal).
function bindSidebar() {
  const sb = $('lm-sidebar');
  sb.addEventListener('click', function(e) {
    const row = e.target.closest('.site-row');
    if (!row) return;
    if (row.getAttribute('data-view') === 'noc') {
      navigate('noc');
    } else {
      const name = row.getAttribute('data-site');
      if (name) navigate('site', name);
    }
  });
  sb.addEventListener('contextmenu', function(e) {
    const row = e.target.closest('.site-row[data-site]');
    if (!row) return;
    const name = row.getAttribute('data-site');
    if (!name) return;
    e.preventDefault();
    _siteRowMenu(e, name);
  });
  $('lm-search').addEventListener('input', debounce(renderSidebar, 120));
}

// ─── Sidebar context menu ───────────────────────────────────
function _lmMenuClose() {
  document.querySelectorAll('.lm-menu').forEach(function(m) { m.remove(); });
}

function _lmMenuOpen(evt, items) {
  _lmMenuClose();
  const m = ce('div', 'lm-menu');
  items.forEach(function(it) {
    if (it.separator) { m.appendChild(ce('div', 'lm-menu-sep')); return; }
    const row = ce('div', 'lm-menu-item' + (it.disabled ? ' disabled' : ''));
    row.textContent = it.label;
    if (!it.disabled) {
      row.addEventListener('click', function() {
        _lmMenuClose();
        try { it.onClick(); } catch (_) {}
      });
    }
    m.appendChild(row);
  });
  document.body.appendChild(m);
  // Position, clamped to viewport
  const vw = window.innerWidth, vh = window.innerHeight;
  const r = m.getBoundingClientRect();
  let x = evt.clientX, y = evt.clientY;
  if (x + r.width  > vw - 4) x = vw - r.width  - 4;
  if (y + r.height > vh - 4) y = vh - r.height - 4;
  m.style.left = x + 'px';
  m.style.top  = y + 'px';
  // Dismiss on outside click / Escape
  const dismiss = function(ev) {
    if (!m.contains(ev.target)) {
      document.removeEventListener('mousedown', dismiss, true);
      document.removeEventListener('keydown', onKey, true);
      _lmMenuClose();
    }
  };
  const onKey = function(ev) {
    if (ev.key === 'Escape') {
      document.removeEventListener('mousedown', dismiss, true);
      document.removeEventListener('keydown', onKey, true);
      _lmMenuClose();
    }
  };
  setTimeout(function() {
    document.addEventListener('mousedown', dismiss, true);
    document.addEventListener('keydown', onKey, true);
  }, 0);
}

function _siteRowMenu(evt, name) {
  const site = LM.sitesByName[name] || {};
  const pinned = !!site.pinned;
  _lmMenuOpen(evt, [
    {
      label: pinned ? '★  Unpin from top' : '☆  Pin to top',
      onClick: function() { _setSitePinned(name, pinned ? 0 : 1); }
    },
    { separator: true },
    {
      label: '⚙  Edit Site…',
      onClick: function() {
        if (typeof window.openSiteModal === 'function') {
          window.openSiteModal('edit', name);
        } else if (typeof window._lmOpenSiteModal === 'function') {
          window._lmOpenSiteModal('edit', name);
        }
      }
    }
  ]);
}

async function _setSitePinned(name, value) {
  try {
    const r = await fetch('/api/sites/meta/' + encodeURIComponent(name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ pinned: value })
    });
    if (!r.ok) {
      const j = await r.json().catch(function(){return{};});
      console.warn('[livemap] pin toggle failed:', j.error || r.statusText);
      return;
    }
    // Refresh sites so the row jumps between ALL SITES and PINNED.
    await refreshAll();
  } catch (e) {
    console.warn('[livemap] pin toggle error:', e);
  }
}

// ─── NOC overview rendering ─────────────────────────────────
function _heroStat(opts) {
  const cls   = opts.cls || '';
  const value = String(opts.value);
  const bar   = opts.bar
    ? '<div class="hero-bar">' +
        '<i class="up"   style="flex:' + opts.bar.up   + '"></i>' +
        '<i class="warn" style="flex:' + opts.bar.warn + '"></i>' +
        '<i class="down" style="flex:' + opts.bar.down + '"></i>' +
      '</div>'
    : '';
  const legend = opts.legend
    ? '<div class="hero-legend">' + opts.legend.map(function(L) {
        return '<span><i class="ld" style="background:' + L.color + '"></i>' + esc(L.label) + ' <b>' + esc(String(L.val)) + '</b></span>';
      }).join('') + '</div>'
    : '';
  return '<div class="hero-stat ' + cls + '">' +
           '<div class="hero-label">' + esc(opts.label) + '</div>' +
           '<div class="hero-value ' + (opts.valueCls || '') + '">' + esc(value) + '</div>' +
           '<div class="hero-sub">' + esc(opts.sub || '') + '</div>' +
           bar +
           legend +
         '</div>';
}

function _mosaicCellSpans(devCount) {
  // sqrt scaling: BSLAB with ~80 → ~6×3 cells; edge sites with 4 → 1×1.
  // Clamp to keep grid readable.
  if (!devCount || devCount < 1) return { col: 1, row: 1 };
  const root = Math.sqrt(devCount);
  let col = Math.max(1, Math.min(6, Math.round(root / 1.6)));
  let row = Math.max(1, Math.min(3, Math.round(root / 2.5)));
  return { col: col, row: row };
}

function renderNOC() {
  const main = $('lm-main');
  const s = LM.summary;
  if (!s) {
    main.innerHTML = '<div class="lm-empty">Loading NOC summary…</div>';
    return;
  }
  // Hero stats
  const heroSites = _heroStat({
    cls: 'up', label: 'SITES', value: s.sites.total, valueCls: 'up',
    sub: s.sites.up + ' up · ' + s.sites.warn + ' warn · ' + s.sites.down + ' down',
    bar: { up: s.sites.up, warn: s.sites.warn, down: s.sites.down },
    legend: [
      { color: 'var(--up)',   label: 'UP',   val: s.sites.up   },
      { color: 'var(--warn)', label: 'WARN', val: s.sites.warn },
      { color: 'var(--down)', label: 'DOWN', val: s.sites.down },
    ]
  });
  const heroDevices = _heroStat({
    cls: 'dev', label: 'DEVICES', value: s.devices.total, valueCls: 'dev',
    sub: 'across ' + s.sites.total + ' sites',
    bar: { up: s.devices.up, warn: s.devices.warn, down: s.devices.down },
    legend: [
      { color: 'var(--up)',   label: 'UP',   val: s.devices.up   },
      { color: 'var(--warn)', label: 'WARN', val: s.devices.warn },
      { color: 'var(--down)', label: 'DOWN', val: s.devices.down },
    ]
  });
  const heroAlerts = _heroStat({
    cls: 'down', label: 'ACTIVE ALERTS', value: s.alerts.active, valueCls: 'down',
    sub: s.alerts.down + ' down · ' + s.alerts.warn + ' warn · ' + s.alerts.ack + ' ack',
  });
  const upPct = (s.uptime_24h * 100);
  const heroUptime = _heroStat({
    cls: 'gold', label: 'UPTIME · 24H',
    value: upPct.toFixed(2) + '%', valueCls: 'gold',
    sub: s.flaps_24h + ' flaps · ' + s.incidents_24h + ' incidents',
  });

  // Mosaic — site cells with sqrt-sized spans.
  // Exclude internet/pinned sites — they have their own OFF-SITE widget so
  // including them here would double-display the same info.
  const cells = LM.sites.filter(function(site) {
    return (site.kind || '').toLowerCase() !== 'internet';
  }).map(function(site) {
    const status = worstStatus(site);
    const span = _mosaicCellSpans(site.devices);
    const kind = (site.kind || 'lab').toLowerCase();
    const abbrev = kindAbbrev(kind);
    const alerts = site.alerts ? '<span class="mc-alerts">●' + site.alerts + '</span>' : '';
    return '<div class="mosaic-cell ' + status + '" ' +
                  'style="grid-column: span ' + span.col + '; grid-row: span ' + span.row + '" ' +
                  'data-site="' + esc(site.name) + '">' +
             '<div class="mc-row">' +
               '<span class="mc-dot" style="background:var(--' + (status === 'up' ? 'up' : (status === 'warn' ? 'warn' : status === 'down' ? 'down' : 'dim')) + ')"></span>' +
               '<span class="mc-name">' + esc(site.display_name || site.name) + '</span>' +
             '</div>' +
             '<div class="mc-meta">' +
               '<span>' + esc(abbrev) + '</span>' +
               '<span>' + site.devices + '</span>' +
               alerts +
             '</div>' +
           '</div>';
  }).join('');

  // OFF-Site internet widget
  const offsiteRows = (s.off_site || []).map(function(o) {
    const cls = o.status === 'up' ? 'up' : (o.status === 'warn' ? 'warn' : (o.status === 'down' ? 'down' : 'unknown'));
    const ms = (o.latency_ms == null)
      ? '<span class="ni-ms timeout">— timeout</span>'
      : '<span class="ni-ms">' + o.latency_ms + 'ms</span>';
    return '<div class="ni-row">' +
             '<span class="ni-dot ' + cls + '"></span>' +
             '<span class="ni-name">' + esc(o.name) + '</span>' +
             '<span class="ni-ip">' + esc(o.host) + '</span>' +
             ms +
           '</div>';
  }).join('') || '<div class="lm-empty">No internet checks configured</div>';

  // Sites by type
  const kindRows = Object.keys(s.by_kind || {}).sort().map(function(k) {
    const bk = s.by_kind[k];
    const total = bk.total || 1;
    return '<div class="bk-row">' +
             '<span class="bk-tag ' + esc(k) + '">' + esc(kindAbbrev(k)) + '</span>' +
             '<span class="bk-name">' + esc(k.toUpperCase()) + '</span>' +
             '<span class="bk-bar">' +
               '<span class="bk-fill up"   style="flex:' + bk.up   + '"></span>' +
               '<span class="bk-fill warn" style="flex:' + bk.warn + '"></span>' +
               '<span class="bk-fill down" style="flex:' + bk.down + '"></span>' +
             '</span>' +
             '<span class="bk-count">' + (bk.up + bk.warn) + '/' + total + '</span>' +
           '</div>';
  }).join('') || '<div class="lm-empty">No sites yet</div>';

  // Top problem sites
  const probRows = (s.top_problems || []).map(function(p, i) {
    const status = worstStatus(p);
    const stats =
      (p.down ? '<span class="pdown">●' + p.down + '↓</span>' : '') +
      (p.warn ? '<span class="pwarn">●' + p.warn + '⚠</span>' : '');
    return '<div class="prob-row ' + status + '" data-site="' + esc(p.name) + '">' +
             '<span class="prob-rank">' + (i + 1) + '</span>' +
             '<span class="prob-kind ' + esc((p.kind || 'lab').toLowerCase()) + '">' + esc(kindAbbrev(p.kind)) + '</span>' +
             '<span class="prob-name">' + esc(p.name) + '</span>' +
             '<span class="prob-stat">' + stats + '</span>' +
             '<span class="prob-alerts">' + (p.alerts || 0) + '</span>' +
           '</div>';
  }).join('') || '<div class="lm-empty">No problems — all sites healthy</div>';

  // Recent alerts feed
  const feedRows = (s.recent_alerts || []).map(function(a) {
    const sev = a.severity || (a.direction === 'down' ? 'down' : 'warn');
    return '<div class="feed-row ' + sev + '" data-site="' + esc(a.site || '') + '">' +
             '<span class="feed-st ' + sev + '">' + esc(sev.toUpperCase()) + '</span>' +
             '<span class="feed-ago">' + esc(timeAgo(a.ts)) + '</span>' +
             '<span class="feed-name">' + esc(a.dname || a.host) + '</span>' +
             '<span class="feed-loc">' + esc((a.site || '—') + (a.sname ? ' › ' + a.sname : '')) + '</span>' +
           '</div>';
  }).join('') || '<div class="lm-empty">No recent alerts</div>';

  main.innerHTML =
    '<div class="noc">' +
      '<div class="noc-hero">' + heroSites + heroDevices + heroAlerts + heroUptime + '</div>' +
      '<div class="noc-mid">' +
        '<div class="noc-mosaic">' +
          '<div class="noc-block-head"><span class="nbh-label">SITE HEALTH MOSAIC</span>' +
            '<span class="nbh-sub">cells sized by device count · colored by worst status</span>' +
          '</div>' +
          '<div class="mosaic">' + cells + '</div>' +
        '</div>' +
        '<div class="noc-side">' +
          '<div class="noc-internet">' +
            '<div class="noc-block-head"><span class="nbh-label">OFF-SITE · INTERNET</span>' +
              '<span class="nbh-sub">pinned reachability checks</span>' +
            '</div>' +
            '<div class="ni-rows">' + offsiteRows + '</div>' +
          '</div>' +
          '<div class="noc-bykind">' +
            '<div class="noc-block-head"><span class="nbh-label">SITES BY TYPE</span>' +
              '<span class="nbh-sub">' + Object.keys(s.by_kind || {}).length + ' categories</span>' +
            '</div>' +
            '<div class="bk-rows">' + kindRows + '</div>' +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="noc-bot">' +
        '<div class="noc-problems">' +
          '<div class="noc-block-head"><span class="nbh-label">TOP PROBLEM SITES</span>' +
            '<span class="nbh-sub">ranked by active alerts</span>' +
          '</div>' +
          '<div class="prob-rows">' + probRows + '</div>' +
        '</div>' +
        '<div class="noc-feed">' +
          '<div class="noc-block-head"><span class="nbh-label">RECENT ALERTS</span>' +
            '<span class="nbh-sub">live · all sites</span>' +
            '<span class="feed-live"><span class="feed-live-dot"></span>LIVE</span>' +
          '</div>' +
          '<div class="feed-rows">' + feedRows + '</div>' +
        '</div>' +
      '</div>' +
    '</div>';

  // Mosaic cell + problem-row click → drill in
  main.querySelectorAll('.mosaic-cell').forEach(function(el) {
    el.addEventListener('click', function() {
      const name = el.getAttribute('data-site');
      if (name) navigate('site', name);
    });
  });
  main.querySelectorAll('.prob-row').forEach(function(el) {
    el.addEventListener('click', function() {
      const name = el.getAttribute('data-site');
      if (name) navigate('site', name);
    });
  });
  main.querySelectorAll('.feed-row').forEach(function(el) {
    el.addEventListener('click', function() {
      const name = el.getAttribute('data-site');
      if (name) navigate('site', name);
    });
  });
}

// ─── Site detail (M1b) ──────────────────────────────────────
function _devCard(d, opts) {
  opts = opts || {};
  const status = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));
  const icon = opts.icon || ICONS.sw;
  const role = opts.role ? '<span class="dev-role">' + esc(opts.role) + '</span>' : '';
  // Parent linkage attributes drive the SVG connection layer in _drawConnections().
  const parents = JSON.stringify(d.parent_device_ids || []);
  // Per-parent port wiring — {pid: {lport, rport}}. Serialised on the card so
  // the tooltip can show "Gi0/1 ↔ Gi0/24" next to each mapping line.
  const parentPorts = JSON.stringify(d.parent_device_ports || {});
  const tier = opts.tier || '';
  return '<div class="dev ' + status + '" data-did="' + esc(d.did) + '"' +
           ' data-parent-ids=\'' + parents.replace(/'/g, '&#39;') + '\'' +
           ' data-parent-ports=\'' + parentPorts.replace(/'/g, '&#39;') + '\'' +
           (tier ? ' data-tier="' + esc(tier) + '"' : '') + '>' +
           '<div class="dev-row">' + icon +
             '<span class="dev-name">' + esc(d.name) + '</span>' +
             role +
           '</div>' +
           '<div class="dev-ip">' + esc(d.host) + '</div>' +
         '</div>';
}

function _clusterCard(c, opts) {
  opts = opts || {};
  const status = c.status === 'up' ? 'up' : (c.status === 'warn' ? 'warn' : (c.status === 'down' ? 'down' : 'unknown'));
  const icon = opts.icon || ICONS.hyp;
  // Optional chip in the cluster head — used to mark IPMI cards as OOB
  // (out-of-band management plane) without a separate tier-tag header.
  // Style is keyed via the chip class so other tiers can opt into it later.
  const chip = opts.chip
    ? '<span class="cluster-chip ' + esc(opts.chip.cls || '') + '">' + esc(opts.chip.label) + '</span>'
    : '';
  // Mini status grid: one LED per child device. CSS handles wrapping via
  // grid-template-columns: repeat(auto-fill, 10px) — no per-cluster column
  // count needed, so a 1-device cluster shows one LED instead of stretching
  // to fill 4 columns.
  const cells = c.cells.map(function(cell) {
    return '<div class="d-' + (cell.status || 'unknown') + '" title="' + esc(cell.name) + '"></div>';
  }).join('');
  const cardCls = 'cluster' + (opts.tier ? ' tier-' + opts.tier : '') + ' ' + status;
  // Resolved parents (union across cluster members) + member dids for the
  // connection layer to map child→cluster when a parent is itself inside a cluster.
  const parents = JSON.stringify(c.parent_device_ids || []);
  const memberDids = JSON.stringify((c.cells || []).map(function(x) { return x.did; }));
  // Per-cell parent detail for the hover tooltip — lets a line know exactly
  // which cells flow through it (not just "this cluster has these parents").
  // `pp` carries per-parent port info for this cell so the tooltip can render
  // "Gi0/1 ↔ Gi0/24" alongside the from→to mapping.
  const cellsDetail = JSON.stringify((c.cells || []).map(function(x) {
    return { did: x.did, name: x.name, p: x.parent_device_ids || [],
             pp: x.parent_device_ports || {} };
  }));
  const mixedAttr = c.mixed_parents ? ' data-mixed-parents="1"' : '';
  return '<div class="' + cardCls + '" data-cluster="' + esc(c.name) + '"' +
           ' data-parent-ids=\'' + parents.replace(/'/g, '&#39;') + '\'' +
           ' data-cells=\'' + memberDids.replace(/'/g, '&#39;') + '\'' +
           ' data-cells-detail=\'' + cellsDetail.replace(/'/g, '&#39;') + '\'' +
           (opts.tier ? ' data-tier="' + esc(opts.tier) + '"' : '') +
           mixedAttr + '>' +
           '<div class="cluster-head">' + icon +
             '<span class="cluster-title">' + esc(c.name) + '</span>' +
             chip +
             '<span class="cluster-count">' + c.count + '</span>' +
           '</div>' +
           '<div class="cluster-grid">' + cells + '</div>' +
           '<div class="cluster-foot">' +
             '<span class="cf-up">●' + c.up + '</span>' +
             '<span class="cf-warn">●' + c.warn + '</span>' +
             '<span class="cf-down">●' + c.down + '</span>' +
             '<span class="cluster-expand">▸ EXPAND</span>' +
           '</div>' +
         '</div>';
}

function renderSite(name) {
  const main = $('lm-main');
  main.innerHTML = '<div class="lm-empty">Loading ' + esc(name) + '…</div>';
  fetchSiteTree(name).then(function(tree) {
    _renderSiteTree(name, tree);
  }).catch(function(e) {
    main.innerHTML = '<div class="lm-empty">Failed to load site: ' + esc(e.message || e) + '</div>';
  });
}

// (Removed) The OFF-Site context band that used to appear above every site
// drill-in. Pinned internet reachability is already a first-class entry in
// the sidebar; surfacing it again on every site page was redundant noise.

function _renderSiteTree(name, tree) {
  const main = $('lm-main');
  const site = LM.sitesByName[name] || {};
  const kind = (site.kind || '').toLowerCase();

  // Internet-kind sites are conceptually a flat list of reachability checks,
  // not a multi-tier infrastructure. Render them as a clean grid of device
  // cards so the user doesn't see a "HYPERVISORS" label with one weird
  // cluster of 3 internet probes.
  if (kind === 'internet') {
    _renderInternetSite(name, tree);
    return;
  }

  const isps  = tree.isp           || [];
  const wans  = tree.wan_switches  || [];
  const fws   = tree.firewalls     || [];
  const cores = tree.core_switches || [];
  const sws   = tree.switches      || [];
  const aps   = tree.access_points || [];
  const chs   = tree.chassis       || [];
  const hyps  = tree.hypervisors   || [];
  const vms   = tree.vm_clusters   || [];
  const ipmi  = tree.ipmi          || [];

  function tierRow(cls, label, items, opts) {
    if (!items.length && !opts.alwaysShow) return '';
    const tagCls   = opts.tagCls || cls;
    const rowAlign = opts.center ? ' center' : ' spread';
    const trailing = opts.trailing || '';
    return '<div class="sd-tier">' +
             '<span class="tier-tag ' + tagCls + '">' + esc(label) + '</span>' +
             '<div class="sd-tier-row' + rowAlign + '">' +
               items.map(opts.render).join('') +
               trailing +
             '</div>' +
           '</div>';
  }

  function _ispRow(d) {
    return _devCard(d, { icon: ICONS.isp, role: 'ISP', tier: 'isp' });
  }
  function _wanRow(d) {
    return _devCard(d, { icon: ICONS.wan, tier: 'wan_switch' });
  }
  function _fwRow(d) {
    return _devCard(d, { icon: ICONS.fw, role: 'PRIMARY', tier: 'firewall' });
  }
  function _coreRow(d) {
    return _devCard(d, { icon: ICONS.core, tier: 'core_switch' });
  }
  function _swRow(d) {
    return _devCard(d, { icon: ICONS.sw, tier: 'switch' });
  }
  function _apRow(d) {
    return _devCard(d, { icon: ICONS.ap, tier: 'ap' });
  }
  function _chsRow(c) {
    return _clusterCard(c, { icon: ICONS.chassis, tier: 'chassis' });
  }
  function _hypRow(c) {
    return _clusterCard(c, { icon: ICONS.hyp, tier: 'hypervisor' });
  }
  function _vmRow(c) {
    return _clusterCard(c, { icon: ICONS.vm, tier: 'vm' });
  }
  function _ipmiRow(c) {
    // IPMI cluster cards live in the hypervisor row alongside the in-band
    // clusters. The purple OOB chip on each card carries the "out-of-band
    // management" signal that the old standalone tier-tag header carried.
    return _clusterCard(c, {
      icon: ICONS.ipmi,
      tier: 'ipmi',
      chip: { label: 'OOB', cls: 'oob' },
    });
  }

  // No more dedicated IPMI column — cards trail the hypervisor row as
  // regular flex items, each carrying its own OOB chip.
  const ipmiTrailing = ipmi.length ? ipmi.map(_ipmiRow).join('') : '';

  main.innerHTML =
    '<div class="sd-wrap">' +
      '<div class="site">' +
        '<div class="site-corners"><span></span><span></span><span></span><span></span></div>' +
        '<div class="site-tab">' +
          '<span class="site-tab-k">SITE</span>' +
          '<span class="site-tab-n">' + esc(name) + '</span>' +
          '<span class="site-tab-s">› SELECTED · MAIN INFRASTRUCTURE</span>' +
        '</div>' +
        '<div class="sd-canvas">' +
          tierRow('isp',  'ISP',              isps,  { render: _ispRow,  center: true }) +
          tierRow('wan',  'WAN SWITCH',       wans,  { render: _wanRow,  center: true }) +
          tierRow('fw',   'FIREWALL',         fws,   { render: _fwRow,   center: true }) +
          tierRow('core', 'CORE SWITCH',      cores, { render: _coreRow, center: false }) +
          tierRow('sw',   'ACCESS SWITCHES',  sws,   { render: _swRow,   center: false }) +
          tierRow('ap',   'ACCESS POINTS',    aps,   { render: _apRow,   center: false }) +
          tierRow('chs',  'CHASSIS',          chs,   { render: _chsRow }) +
          tierRow('hyp',  'HYPERVISORS',      hyps,  { render: _hypRow, trailing: ipmiTrailing }) +
          tierRow('vm',   'VM CLUSTERS',      vms,   { render: _vmRow }) +
        '</div>' +
      '</div>' +
      (tree.other && tree.other.length
        ? '<div class="sd-other"><div class="sd-other-h">OTHER · UNCLASSIFIED DEVICES (' + tree.other.length + ')</div>' +
          '<div class="sd-other-grid">' +
            tree.other.map(function(d) {
              const st = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));
              const _parents = JSON.stringify(d.parent_device_ids || []);
              const _pports  = JSON.stringify(d.parent_device_ports || {});
              return '<div class="dev ' + st + '" data-did="' + esc(d.did) + '"' +
                       ' data-parent-ids=\'' + _parents.replace(/'/g, '&#39;') + '\'' +
                       ' data-parent-ports=\'' + _pports.replace(/'/g, '&#39;') + '\'' +
                       ' data-tier="other">' +
                       '<div class="dev-row">' + ICONS.sw + '<span class="dev-name">' + esc(d.name) + '</span></div>' +
                       '<div class="dev-ip">' + esc(d.host) + '</div>' +
                     '</div>';
            }).join('') +
          '</div></div>'
        : '');

  // Cluster click → side panel
  // Listener is bound once in boot() now; binding here would accumulate
  // a new handler on every site render.

  // Draw parent connection lines once the layout settles. Wrapped in rAF so
  // we read getBoundingClientRect after the browser has laid the cards out.
  const canvas = main.querySelector('.sd-canvas');
  if (canvas) {
    requestAnimationFrame(function() { _drawConnections(canvas); });
    // Redraw on resize — connection coords drift when the panel reflows.
    _bindCanvasResize(canvas);
    // Canvas-level hover tracking: aggregates overlapping hits via
    // elementsFromPoint so a shared trunk shows every line passing through.
    _bindCanvasTooltip(canvas);
  }
}

// ─── SVG connection lines ──────────────────────────────────────────
// Color rules keyed by the CHILD tier (since each line ends at the child).
// Top-of-topology (ISP / WAN / Firewall) is yellow/gold — signals "this is
// the carrier-facing edge". Below the firewall the LAN paints cyan / lime.
const _CONN_STYLES = {
  'isp':         { color: 'var(--gold)',    dashed: false }, // (root)
  'wan_switch':  { color: 'var(--gold)',    dashed: false }, // → ISP (yellow)
  'firewall':    { color: 'var(--gold)',    dashed: false }, // → WAN/ISP (yellow)
  'core_switch': { color: 'var(--accent2)', dashed: false }, // → FW (cyan)
  'switch':      { color: 'var(--accent2)', dashed: false }, // → CORE/FW (cyan)
  'ap':          { color: 'var(--accent2)', dashed: false }, // → SW (cyan)
  'chassis':     { color: 'var(--accent2)', dashed: false }, // → SW (cyan)
  'hypervisor':  { color: 'var(--up)',      dashed: false }, // → SW/CHS (lime)
  'vm':          { color: 'var(--accent2)', dashed: false }, // → HYP (cyan)
  'ipmi':        { color: 'var(--purple)',  dashed: true  }, // → SW (purple dashed)
  'other':       { color: 'var(--up)',      dashed: false },
};

// Vertical tier index — mirrors site_tree._SWEEP_ORDER. A link whose endpoints
// are ≥2 apart "skips" a tier; such links are candidates for side-channel
// routing so they don't run straight through the intervening row's cards.
// IPMI shares the hypervisor row's index since it renders trailing in it.
const _TIER_INDEX = {
  isp: 0, wan_switch: 1, firewall: 2, core_switch: 3, switch: 4, ap: 5,
  chassis: 6, hypervisor: 7, ipmi: 7, vm: 8, other: 9,
};

function _cardLabel(el) {
  if (!el) return '';
  const t = el.querySelector('.dev-name') || el.querySelector('.cluster-title');
  if (t && t.textContent) return t.textContent.trim();
  return el.getAttribute('data-cluster') || el.getAttribute('data-did') || '';
}

function _drawConnections(canvasEl) {
  if (!canvasEl) return;
  const oldSvg = canvasEl.querySelector('.conn-svg');
  if (oldSvg) oldSvg.remove();

  // Build did → DOM element index. Device cards are direct hits; cluster
  // cards register their member dids so child clusters can resolve a parent
  // that lives INSIDE another cluster (e.g. VM cluster → ESXi inside HYP cluster).
  // We also index cluster cards by group name so "group:<name>" parent refs
  // resolve to the matching cluster card (e.g. a VM whose parent is the
  // entire ESXi cluster, not individual hosts).
  const didEl = new Map();
  const groupEl = new Map();
  // didName resolves a parent ref to its true display name — the actual
  // device name (even when it lives INSIDE a cluster card) or, for group
  // refs, the group name itself. Used by the tooltip so a mapping shows
  // "esxi-12a → ESXi-luffy" rather than "esxi-12a → ESXI-Standalone".
  const didName = new Map();
  canvasEl.querySelectorAll('.dev[data-did]').forEach(function(el) {
    const did = el.getAttribute('data-did');
    didEl.set(did, el);
    const nameEl = el.querySelector('.dev-name');
    if (nameEl) didName.set(did, (nameEl.textContent || '').trim() || did);
  });
  canvasEl.querySelectorAll('.cluster').forEach(function(el) {
    const gname = el.getAttribute('data-cluster');
    if (gname) groupEl.set(gname, el);
    let cells;
    try { cells = JSON.parse(el.getAttribute('data-cells') || '[]'); }
    catch { cells = []; }
    cells.forEach(function(did) {
      if (!didEl.has(did)) didEl.set(did, el);
    });
    // Cell-level name lookup — pulled from data-cells-detail so we get the
    // real device name regardless of whether the device has its own card.
    let cellsDetail;
    try { cellsDetail = JSON.parse(el.getAttribute('data-cells-detail') || '[]'); }
    catch { cellsDetail = []; }
    cellsDetail.forEach(function(cell) {
      if (cell && cell.did && cell.name && !didName.has(cell.did)) {
        didName.set(cell.did, cell.name);
      }
    });
  });

  function _resolveParent(ref) {
    if (typeof ref !== 'string' || !ref) return null;
    if (ref.indexOf('group:') === 0) return groupEl.get(ref.slice(6)) || null;
    return didEl.get(ref) || null;
  }
  function _refDisplayName(ref) {
    if (typeof ref !== 'string' || !ref) return '';
    if (ref.indexOf('group:') === 0) return ref.slice(6);
    return didName.get(ref) || ref;
  }

  // Pass 1 — build parent → children map with per-cell mapping detail.
  // parentMap key: parent DOM element.
  // value: [{ childEl, tier, mappings: [{from, pid}, ...] }]
  // Same (parent, child) pair coalesces — duplicates from per-cell iteration are merged.
  const parentMap = new Map();
  function _push(parentEl, childEl, tier, mapping) {
    let arr = parentMap.get(parentEl);
    if (!arr) { arr = []; parentMap.set(parentEl, arr); }
    let entry = arr.find(function(e) { return e.childEl === childEl; });
    if (!entry) {
      entry = { childEl: childEl, tier: tier, mappings: [] };
      arr.push(entry);
    }
    entry.mappings.push(mapping);
  }

  canvasEl.querySelectorAll('[data-parent-ids]').forEach(function(child) {
    const tier = child.getAttribute('data-tier') || 'other';
    const isCluster = child.classList.contains('cluster');

    // A port map entry is canonically a list of {lport, rport} pairs (LACP
    // support — multiple physical links between the same device pair). The
    // pre-LACP single-dict shape is tolerated for forward compatibility with
    // stale caches. Returns [] when nothing's set so the caller can fall back
    // to one bare mapping per parent.
    function _portPairList(v) {
      if (Array.isArray(v)) return v.filter(function(p) { return p && typeof p === 'object'; });
      if (v && typeof v === 'object') return [v];
      return [];
    }

    if (isCluster) {
      // Per-cell granularity: tooltip can show "esxi-01 → SW-A, esxi-02 → SW-B"
      let cellsDetail;
      try { cellsDetail = JSON.parse(child.getAttribute('data-cells-detail') || '[]'); }
      catch { cellsDetail = []; }
      cellsDetail.forEach(function(cell) {
        const pp = (cell.pp && typeof cell.pp === 'object') ? cell.pp : {};
        (cell.p || []).forEach(function(pid) {
          const parentEl = _resolveParent(pid);
          if (!parentEl || parentEl === child) return;
          const pairs = _portPairList(pp[pid]);
          if (!pairs.length) {
            _push(parentEl, child, tier,
                  { from: cell.name, pid: pid, lport: '', rport: '' });
          } else {
            pairs.forEach(function(wp) {
              _push(parentEl, child, tier,
                    { from: cell.name, pid: pid,
                      lport: wp.lport || '', rport: wp.rport || '' });
            });
          }
        });
      });
    } else {
      // Device card: card itself is the only "cell".
      let parents;
      try { parents = JSON.parse(child.getAttribute('data-parent-ids') || '[]'); }
      catch { return; }
      let parentPorts;
      try { parentPorts = JSON.parse(child.getAttribute('data-parent-ports') || '{}'); }
      catch { parentPorts = {}; }
      const fromName = _cardLabel(child);
      parents.forEach(function(pid) {
        const parentEl = _resolveParent(pid);
        if (!parentEl || parentEl === child) return;
        const pairs = _portPairList(parentPorts[pid]);
        if (!pairs.length) {
          _push(parentEl, child, tier,
                { from: fromName, pid: pid, lport: '', rport: '' });
        } else {
          pairs.forEach(function(wp) {
            _push(parentEl, child, tier,
                  { from: fromName, pid: pid,
                    lport: wp.lport || '', rport: wp.rport || '' });
          });
        }
      });
    }
  });

  // Pass 2 — draw orthogonal lines with a SHARED trunk per parent.
  //
  // Layout polish:
  //   • Fan-out: parent entry points spread across the parent's bottom edge
  //     instead of stacking at center — no more knot at parent_X.
  //   • Child sort: children draw L→R by their card X, so the fan-out slots
  //     map monotonically (no crossings between trunk segments).
  //   • 4px grid: trunk Y + entry X snap so parallel trunks align cleanly
  //     when multiple parents share a row.
  //   • Long-haul dim: lines whose horizontal travel exceeds 30% of the
  //     canvas drop to opacity 0.4 + slower dash animation. Fixes the IPMI
  //     dashed line dominating the hypervisor row visually.
  //   • Trunk underlay: a fader, slightly thicker stroke at trunk Y unifies
  //     multi-child connections into a readable "backbone".
  //   • Endpoint notches: small dots where a line meets a card edge so it
  //     reads as "plugged in" rather than abutting.
  const cRect = canvasEl.getBoundingClientRect();
  const svgNs = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNs, 'svg');
  svg.setAttribute('class', 'conn-svg');
  svg.setAttribute('width', cRect.width);
  svg.setAttribute('height', cRect.height);

  const LONG_HAUL_PX = cRect.width * 0.30;
  function _snap4(v) { return Math.round(v / 4) * 4; }

  // Shared helpers used by both same-row and below-row routes.
  function _appendHit(d, parentLabel, childEl, mappings) {
    const hit = document.createElementNS(svgNs, 'path');
    hit.setAttribute('d', d);
    hit.setAttribute('stroke', 'transparent');
    hit.setAttribute('stroke-width', '12');
    hit.setAttribute('fill', 'none');
    hit.setAttribute('class', 'conn-hit');
    hit.dataset.parentName = parentLabel;
    hit.dataset.childName = _cardLabel(childEl);
    hit.dataset.mappings = JSON.stringify(mappings.map(function(m) {
      return { from: m.from, to: _refDisplayName(m.pid),
               lport: m.lport || '', rport: m.rport || '' };
    }));
    svg.appendChild(hit);
  }
  function _appendLine(d, style, longHaul) {
    const path = document.createElementNS(svgNs, 'path');
    path.setAttribute('d', d);
    path.setAttribute('stroke', style.color);
    path.setAttribute('stroke-width', '1.5');
    path.setAttribute('fill', 'none');
    path.setAttribute('vector-effect', 'non-scaling-stroke');
    path.setAttribute('class',
      'conn-line' +
      (style.dashed ? ' dashed' : '') +
      (longHaul ? ' long-haul' : ''));
    if (style.dashed) path.setAttribute('stroke-dasharray', '5 5');
    svg.appendChild(path);
  }
  function _appendNotch(x, y, color, longHaul) {
    const dot = document.createElementNS(svgNs, 'circle');
    dot.setAttribute('cx', x);
    dot.setAttribute('cy', y);
    dot.setAttribute('r', '2');
    dot.setAttribute('fill', color);
    dot.setAttribute('class', 'conn-notch' + (longHaul ? ' long-haul' : ''));
    svg.appendChild(dot);
  }

  // Group a child entry's mappings into one line per physical link.
  //
  // Primary key: (from, lport, rport) — distinguishes both LACP / dual-NIC
  // pairs on a single device AND distinct devices inside a cluster card
  // (e.g. 4 ESXi cells each cabling to the same switch → 4 visible lines).
  //
  // Cap: when the primary key would draw more than MAX_PORT_LINES (a 27-cell
  // VM cluster all blank-uplinking to one parent), collapse to (lport, rport)
  // so the canvas doesn't dissolve into a stack of parallel hairlines. The
  // tooltip still shows every individual mapping via elementsFromPoint.
  const MAX_PORT_LINES = 12;
  function _portGroups(mappings) {
    if (!mappings || !mappings.length) return [[]];
    const fine = new Map();
    mappings.forEach(function(m) {
      const key = (m.from || '') + '|' + (m.lport || '') + '|' + (m.rport || '');
      if (!fine.has(key)) fine.set(key, []);
      fine.get(key).push(m);
    });
    if (fine.size <= MAX_PORT_LINES) return Array.from(fine.values());
    // Too noisy — fall back to one line per distinct port pair only.
    const coarse = new Map();
    mappings.forEach(function(m) {
      const key = (m.lport || '') + '|' + (m.rport || '');
      if (!coarse.has(key)) coarse.set(key, []);
      coarse.get(key).push(m);
    });
    return Array.from(coarse.values());
  }

  // Card-rect index (canvas-relative) for skip-tier cross-detection. A skip
  // link only diverts to a side-channel when its straight vertical would
  // actually pass through some OTHER card — clean drops stay straight.
  const cardRects = [];
  canvasEl.querySelectorAll('.dev, .cluster').forEach(function(el) {
    const r = el.getBoundingClientRect();
    cardRects.push({ el: el,
      left: r.left - cRect.left, right: r.right - cRect.left,
      top:  r.top  - cRect.top,  bottom: r.bottom - cRect.top });
  });
  function _verticalCrossesCard(x, yTop, yBot, childEl, parentEl) {
    for (let i = 0; i < cardRects.length; i++) {
      const cr = cardRects[i];
      if (cr.el === childEl || cr.el === parentEl) continue;
      if (x > cr.left + 2 && x < cr.right - 2 &&
          yBot > cr.top + 2 && yTop < cr.bottom - 2) return true;
    }
    return false;
  }

  // Lane bookkeeping (greedy interval colouring), shared across all parents:
  //   placedTrunks — horizontal trunk buses, separated by Y lane within a band
  //                  (same parent-row) so parallel trunks never stack.
  //   sideLanes    — skip-tier verticals in a left/right gutter, separated so
  //                  multiple long-haul links don't overlap in the margin.
  const placedTrunks = [];
  const sideLanes = [];

  parentMap.forEach(function(children, parentEl) {
    const pRect = parentEl.getBoundingClientRect();
    const pCenterX = pRect.left + pRect.width / 2 - cRect.left;
    const py = pRect.bottom - cRect.top;
    const parentLabel = _cardLabel(parentEl);

    // Sort by child X — left children get left fan slots, right children get
    // right ones, so trunk segments don't cross each other.
    const sortedAll = children.slice().sort(function(a, b) {
      const aR = a.childEl.getBoundingClientRect();
      const bR = b.childEl.getBoundingClientRect();
      return (aR.left + aR.width / 2) - (bR.left + bR.width / 2);
    });

    // Split children by whether they share a row with the parent (vertical
    // overlap) — those route SIDE-to-SIDE through the card edges, not down
    // to a trunk Y. Same-row connections look natural for sibling switches
    // (e.g. an EX uplinks to a N5K beside it in the SWITCHES row).
    const sameRow = [];
    const below   = [];
    sortedAll.forEach(function(c) {
      const r = c.childEl.getBoundingClientRect();
      // Cards are "in the same row" if their Y ranges overlap by more than
      // a few px (defensive against minor sub-pixel layout drift).
      const overlap = Math.min(r.bottom, pRect.bottom) - Math.max(r.top, pRect.top);
      if (overlap > 4) sameRow.push(c);
      else             below.push(c);
    });

    // ── Same-row routing (side-to-side through card edges) ──
    sameRow.forEach(function(c) {
      const r = c.childEl.getBoundingClientRect();
      const style = _CONN_STYLES[c.tier] || _CONN_STYLES.other;
      const parentIsLeft = (pRect.left + pRect.right) < (r.left + r.right);
      const pSideX = _snap4((parentIsLeft ? pRect.right : pRect.left) - cRect.left);
      const cSideX = _snap4((parentIsLeft ? r.left      : r.right    ) - cRect.left);
      const pMidY  = _snap4((pRect.top + pRect.bottom) / 2 - cRect.top);
      const cMidY  = _snap4((r.top      + r.bottom)      / 2 - cRect.top);
      const midX   = _snap4((pSideX + cSideX) / 2);
      const longHaul = Math.abs(cSideX - pSideX) > LONG_HAUL_PX;

      // One line per distinct (lport, rport) — parallel along the side edges
      // so LACP / dual-NIC bundles read as separate wires. Step fits inside
      // 50 % of the shorter card's height so multiple ports never overflow.
      const groups = _portGroups(c.mappings);
      const N = groups.length;
      const cardH = Math.min(pRect.bottom - pRect.top, r.bottom - r.top);
      const portStep = N > 1 ? Math.min(8, (cardH * 0.5) / (N - 1)) : 0;
      const startOff = -((N - 1) * portStep) / 2;

      groups.forEach(function(grp, k) {
        const dy = startOff + k * portStep;
        const pY = pMidY + dy;
        const cY = cMidY + dy;
        const d = (Math.abs(pY - cY) < 4)
          ? 'M ' + pSideX + ' ' + pY + ' H ' + cSideX
          : 'M ' + pSideX + ' ' + pY +
            ' H ' + midX +
            ' V ' + cY +
            ' H ' + cSideX;
        _appendHit(d, parentLabel, c.childEl, grp);
        _appendLine(d, style, longHaul);
        _appendNotch(pSideX, pY, style.color, longHaul);
        _appendNotch(cSideX, cY, style.color, longHaul);
      });
    });

    // ── Below-row routing (laned trunk + skip-tier side-channels) ──
    if (!below.length) return;

    // Adjacent-tier children share a fanned trunk just below the parent.
    // Skip-tier children (≥2 tiers down) may need a gutter to avoid running
    // straight through the intervening row's cards.
    const parentTier = parentEl.getAttribute('data-tier') || 'other';
    const pIdx = _TIER_INDEX[parentTier] != null ? _TIER_INDEX[parentTier] : 99;
    const trunkKids = [], skipKids = [];
    below.forEach(function(c) {
      const cIdx = _TIER_INDEX[c.tier] != null ? _TIER_INDEX[c.tier] : 99;
      (Math.abs(cIdx - pIdx) >= 2 ? skipKids : trunkKids).push(c);
    });

    // ---- adjacent-tier children: fan-out + a Y-laned shared trunk ----
    if (trunkKids.length) {
      let nearestChildTop = Infinity;
      trunkKids.forEach(function(c) {
        const top = c.childEl.getBoundingClientRect().top - cRect.top;
        if (top < nearestChildTop) nearestChildTop = top;
      });
      const gap = nearestChildTop - py;

      const N = trunkKids.length;

      // Child centers along X — computed up front so the fan-out can align
      // entry points to actual child positions when they're widely spread.
      const xs = trunkKids.map(function(c) {
        const r = c.childEl.getBoundingClientRect();
        return _snap4(r.left + r.width / 2 - cRect.left);
      });

      // Parent bottom-edge bounds (slight inset so verticals don't kiss corners).
      const pBotLeft  = _snap4(pRect.left  - cRect.left + 6);
      const pBotRight = _snap4(pRect.right - cRect.left - 6);
      const pBotWidth = Math.max(0, pBotRight - pBotLeft);

      // When children span much wider than the parent (e.g. BladeCenter feeding
      // 5 hypervisors that fill the canvas), cramming N entries into a 40-px
      // fan under the parent forces N long horizontals across the trunk band.
      // Instead, align each entry X with its child X (clamped to the parent
      // bottom edge) so each link becomes a near-vertical drop. When children
      // sit tight under the parent, keep the small symmetric fan as before.
      const childSpan = N > 1 ? (Math.max.apply(null, xs) - Math.min.apply(null, xs)) : 0;
      const wideFan   = N > 1 && childSpan > pBotWidth * 0.6;

      let fanStart = pCenterX, fanStep = 0;
      if (N > 1 && !wideFan) {
        const fanWidth = Math.min(pRect.width * 0.6, 80, 8 * (N - 1) + 8);
        fanStart = pCenterX - fanWidth / 2;
        fanStep  = fanWidth / (N - 1);
      }
      trunkKids.forEach(function(c, i) {
        if (N === 1) c._entryX = pCenterX;
        else if (wideFan) c._entryX = _snap4(Math.max(pBotLeft, Math.min(pBotRight, xs[i])));
        else c._entryX = _snap4(fanStart + i * fanStep);
      });
      const entryXs = trunkKids.map(function(c) { return c._entryX; });
      const trunkLeft  = Math.min(Math.min.apply(null, xs), Math.min.apply(null, entryXs));
      const trunkRight = Math.max(Math.max.apply(null, xs), Math.max.apply(null, entryXs));

      // Lane: lowest Y-slot in this parent-row band whose X-span doesn't
      // overlap an already-placed trunk — so sibling trunks never stack on the
      // same pixel row. Band groups parents at a near-identical bottom edge.
      const band = Math.round(py / 6);
      let lane = 0;
      while (placedTrunks.some(function(t) {
        return t.band === band && t.lane === lane &&
               !(trunkRight < t.left - 6 || trunkLeft > t.right + 6);
      })) lane++;
      placedTrunks.push({ band: band, lane: lane, left: trunkLeft, right: trunkRight });
      let trunkY = _snap4(py + Math.max(8, Math.min(22, gap / 2)) + lane * 8);
      const maxY = _snap4(nearestChildTop - 6);
      if (trunkY > maxY) trunkY = maxY;

      // ── Visual-density gates ─────────────────────────────────────────
      // When a parent has many children OR they're spread across most of the
      // canvas, the unifying "trunk bus" becomes noise rather than help, and
      // N children all sharing one trunkY collapses into one thick bar. Two
      // complementary moves:
      //   • busSuppressed  → drop the underlay; it's not unifying anything.
      //   • childLaneStep  → give each child its own Y row in the trunk band
      //                       so N horizontals read as N readable lanes.
      const trunkSpan   = trunkRight - trunkLeft;
      const wideSpread  = trunkSpan > cRect.width * 0.5;
      const busSuppressed = N > 2 || wideSpread;
      let childLaneStep = (N > 2) ? 4 : 0;
      // Don't push lanes past the available vertical band (py → nearestChildTop).
      // Shrink the step if the full spread wouldn't fit.
      const availableBand = (nearestChildTop - 6) - (py + 8);
      if (childLaneStep > 0 && childLaneStep * (N - 1) > availableBand) {
        childLaneStep = Math.max(0, availableBand / (N - 1));
      }
      const childYStart = -((N - 1) * childLaneStep) / 2;

      // Trunk underlay
      if (!busSuppressed && N > 1 && trunkSpan > 4) {
        const tierCounts = {};
        trunkKids.forEach(function(c) { tierCounts[c.tier] = (tierCounts[c.tier] || 0) + 1; });
        const dominantTier = Object.keys(tierCounts).sort(function(a, b) {
          return tierCounts[b] - tierCounts[a];
        })[0];
        const trunkStyle = _CONN_STYLES[dominantTier] || _CONN_STYLES.other;
        const bus = document.createElementNS(svgNs, 'path');
        bus.setAttribute('d', 'M ' + trunkLeft + ' ' + trunkY + ' H ' + trunkRight);
        bus.setAttribute('stroke', trunkStyle.color);
        bus.setAttribute('stroke-width', '2.5');
        bus.setAttribute('fill', 'none');
        bus.setAttribute('vector-effect', 'non-scaling-stroke');
        bus.setAttribute('class', 'conn-trunk-bus');
        svg.appendChild(bus);
      }

      trunkKids.forEach(function(c, ki) {
        const r = c.childEl.getBoundingClientRect();
        const cx = _snap4(r.left + r.width / 2 - cRect.left);
        const cy = r.top - cRect.top;
        const entryX = c._entryX;
        const style = _CONN_STYLES[c.tier] || _CONN_STYLES.other;
        const longHaul = Math.abs(cx - entryX) > LONG_HAUL_PX;

        // This child's own Y row inside the trunk band — separates the N
        // horizontals so they don't pile up at a single trunkY.
        const childBaseTrunkY = trunkY + childYStart + ki * childLaneStep;

        // One line per distinct port pair — verticals spread along the parent
        // bottom + child top so each "port" is plainly visible; trunk Y also
        // staggers by a few pixels so the horizontal segments don't merge.
        const groups = _portGroups(c.mappings);
        const Np = groups.length;
        const maxSpread = Math.min(pRect.width, r.width) * 0.4;
        const portStep = Np > 1 ? Math.min(8, maxSpread / (Np - 1)) : 0;
        const startOff = -((Np - 1) * portStep) / 2;
        const yStagger = Np > 1 ? 3 : 0;
        const yStart   = -((Np - 1) * yStagger) / 2;

        groups.forEach(function(grp, k) {
          const dx = startOff + k * portStep;
          const dy = yStart   + k * yStagger;
          const cxK     = cx + dx;
          const entryXK = entryX + dx;
          const trunkYK = childBaseTrunkY + dy;
          const d = 'M ' + cxK + ' ' + cy +
                    ' V ' + trunkYK +
                    ' H ' + entryXK +
                    ' V ' + py;
          _appendHit(d, parentLabel, c.childEl, grp);
          _appendLine(d, style, longHaul);
          _appendNotch(cxK,     cy, style.color, longHaul);
          _appendNotch(entryXK, py, style.color, longHaul);
        });
      });
    }

    // ---- skip-tier children: straight drop when clear, gutter when blocked ----
    skipKids.forEach(function(c) {
      const r = c.childEl.getBoundingClientRect();
      const cx = _snap4(r.left + r.width / 2 - cRect.left);
      const cy = r.top - cRect.top;
      const pY = _snap4(py);
      const style = _CONN_STYLES[c.tier] || _CONN_STYLES.other;

      // Distinct port pairs render as parallel skip lines on both routes.
      const groups = _portGroups(c.mappings);
      const N = groups.length;
      const maxSpread = Math.min(pRect.width, r.width) * 0.4;
      const portStep = N > 1 ? Math.min(8, maxSpread / (N - 1)) : 0;
      const startOff = -((N - 1) * portStep) / 2;

      // Reordering usually parks the child under its parent, so a straight
      // vertical clears every intervening card — keep it straight when so.
      if (!_verticalCrossesCard(cx, pY, cy, c.childEl, parentEl)) {
        groups.forEach(function(grp, k) {
          const dx = startOff + k * portStep;
          const cxK = cx + dx;
          const pX  = _snap4(pCenterX + dx);
          const d = 'M ' + cxK + ' ' + cy + ' V ' + pY +
                    (Math.abs(cxK - pX) > 2 ? ' H ' + pX : '');
          const longHaul = Math.abs(cxK - pX) > LONG_HAUL_PX;
          _appendHit(d, parentLabel, c.childEl, grp);
          _appendLine(d, style, longHaul);
          _appendNotch(cxK, cy, style.color, longHaul);
          _appendNotch(pX,  pY, style.color, longHaul);
        });
        return;
      }
      // Blocked → run up a side gutter on the nearer canvas edge, with its own
      // vertical lane so multiple skip links don't overlap in the margin.
      const leftSide = ((cx + pCenterX) / 2) < (cRect.width / 2);
      const top = Math.min(pY, cy), bot = Math.max(pY, cy);
      let glane = 0;
      while (sideLanes.some(function(s) {
        return s.side === leftSide && s.lane === glane &&
               !(bot < s.top - 6 || top > s.bottom + 6);
      })) glane++;
      sideLanes.push({ side: leftSide, lane: glane, top: top, bottom: bot });
      // Left gutter hugs the canvas edge (x<16), staying left of the
      // absolutely-positioned tier-tag labels so it never crosses them.
      const gutterX = _snap4(leftSide ? (8 + glane * 9)
                                      : (cRect.width - 12 - glane * 9));
      // Port sub-spread inside the gutter lane (tighter — 3px steps).
      const gPortStep = N > 1 ? 3 : 0;
      const gStart    = -((N - 1) * gPortStep) / 2;
      groups.forEach(function(grp, k) {
        const dx = gStart + k * gPortStep;
        const cxK = cx + dx;
        const gx  = gutterX + dx;
        const pX  = _snap4(pCenterX + dx);
        const d = 'M ' + cxK + ' ' + cy +
                  ' V ' + _snap4(cy - 6) +
                  ' H ' + gx +
                  ' V ' + pY +
                  ' H ' + pX;
        _appendHit(d, parentLabel, c.childEl, grp);
        _appendLine(d, style, true);   // long-haul styling (dimmed, slower dash)
        _appendNotch(cxK, cy, style.color, true);
        _appendNotch(pX,  pY, style.color, true);
      });
    });
  });

  // Insert at start so cards stack on top of the lines.
  canvasEl.insertBefore(svg, canvasEl.firstChild);
}

// ─── Connection-line tooltip ──────────────────────────────────────
// Single canvas-level listener. On mousemove we query elementsFromPoint
// to find EVERY .conn-hit at the cursor — so a trunk shared by two lines
// shows both sets of mappings, not just the topmost.
function _connTooltipEl() {
  let tip = document.getElementById('lm-conn-tooltip');
  if (!tip) {
    tip = document.createElement('div');
    tip.id = 'lm-conn-tooltip';
    tip.className = 'lm-conn-tooltip';
    document.body.appendChild(tip);
  }
  return tip;
}
function _hideConnTooltip() {
  const tip = document.getElementById('lm-conn-tooltip');
  if (tip) tip.style.display = 'none';
}
function _bindCanvasTooltip(canvasEl) {
  if (canvasEl._tooltipBound) return;
  canvasEl._tooltipBound = true;
  canvasEl.addEventListener('mousemove', _onCanvasConnMove);
  canvasEl.addEventListener('mouseleave', _hideConnTooltip);
}
function _onCanvasConnMove(e) {
  const stack = document.elementsFromPoint(e.clientX, e.clientY);
  const hits = stack.filter(function(el) {
    return el && el.classList && el.classList.contains('conn-hit');
  });
  if (!hits.length) { _hideConnTooltip(); return; }

  // Aggregate by (childName, parentName) so two distinct links sharing a
  // trunk show as two sections, while N overlapping segments of the same
  // (child, parent) pair collapse into one. Each mapping carries the real
  // target name (device or group) — not the cluster card the line happens
  // to terminate at — so the tooltip reflects what's actually configured.
  const groups = new Map();
  hits.forEach(function(h) {
    const childName  = h.dataset.childName  || '';
    const parentName = h.dataset.parentName || '';
    const key = childName + ' → ' + parentName;
    let mappings;
    try { mappings = JSON.parse(h.dataset.mappings || '[]'); }
    catch { mappings = []; }
    if (!mappings.length) mappings = [{ from: childName, to: parentName }];
    // Backward compatibility — earlier renders embedded plain strings.
    mappings = mappings.map(function(m) {
      if (typeof m === 'string') return { from: m, to: parentName, lport: '', rport: '' };
      return { from:  (m && m.from)  || '',
               to:    (m && m.to)    || parentName,
               lport: (m && m.lport) || '',
               rport: (m && m.rport) || '' };
    });
    if (!groups.has(key)) {
      groups.set(key, { childName: childName, parentName: parentName, items: new Map() });
    }
    const g = groups.get(key);
    mappings.forEach(function(m) {
      // Include port pair in the dedup key so two distinct port wirings on
      // the same (child, parent) pair (e.g. dual-NIC) don't collapse.
      const itemKey = m.from + ' → ' + m.to + ' | ' + m.lport + '↔' + m.rport;
      if (!g.items.has(itemKey)) g.items.set(itemKey, m);
    });
  });

  const sections = Array.from(groups.values()).map(function(g) {
    return {
      childName: g.childName,
      parentName: g.parentName,
      items: Array.from(g.items.values()),
    };
  });

  // Render the port pair next to a mapping when at least one side is set.
  // "Gi0/1 ↔ Gi0/24" when both, "Gi0/1 ↔ ?" when only the local side is known.
  function _portSuffix(m) {
    if (!m.lport && !m.rport) return '';
    const l = m.lport || '?', r = m.rport || '?';
    return ' <span class="lm-tt-ports">(' + esc(l) + ' ↔ ' + esc(r) + ')</span>';
  }

  let html = '';
  if (sections.length === 1) {
    const s = sections[0];
    html =
      '<div class="lm-tt-h">' + esc(s.childName) + ' ↔ ' + esc(s.parentName) +
        ' <span class="lm-tt-c">' + s.items.length + '</span></div>' +
      '<div class="lm-tt-l">' +
        s.items.map(function(m) {
          return '<div>' + esc(m.from) + ' → ' + esc(m.to) + _portSuffix(m) + '</div>';
        }).join('') +
      '</div>';
  } else {
    const total = sections.reduce(function(acc, s) { return acc + s.items.length; }, 0);
    html =
      '<div class="lm-tt-h">' + sections.length + ' OVERLAPPING LINKS' +
        ' <span class="lm-tt-c">' + total + '</span></div>';
    sections.forEach(function(s, i) {
      if (i > 0) html += '<div class="lm-tt-divider"></div>';
      html +=
        '<div class="lm-tt-sub">' + esc(s.childName) + ' ↔ ' + esc(s.parentName) +
          ' <span class="lm-tt-c">' + s.items.length + '</span></div>' +
        '<div class="lm-tt-l">' +
          s.items.map(function(m) {
            return '<div>' + esc(m.from) + ' → ' + esc(m.to) + _portSuffix(m) + '</div>';
          }).join('') +
        '</div>';
    });
  }

  const tip = _connTooltipEl();
  tip.innerHTML = html;
  tip.style.display = 'block';

  // Position below-right of cursor; flip if clipped against the viewport.
  const pad = 14;
  let x = e.clientX + pad, y = e.clientY + pad;
  const w = tip.offsetWidth || 200, h = tip.offsetHeight || 50;
  if (x + w > window.innerWidth)  x = e.clientX - w - pad;
  if (y + h > window.innerHeight) y = e.clientY - h - pad;
  tip.style.left = x + 'px';
  tip.style.top  = y + 'px';
}

let _canvasResizeObs = null;
function _bindCanvasResize(canvas) {
  // Re-draw on canvas reflow (sidebar collapse, window resize).
  if (_canvasResizeObs) _canvasResizeObs.disconnect();
  if (typeof ResizeObserver === 'undefined') return;
  let raf = 0;
  _canvasResizeObs = new ResizeObserver(function() {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(function() { _drawConnections(canvas); });
  });
  _canvasResizeObs.observe(canvas);
}

function _renderInternetSite(name, tree) {
  const main = $('lm-main');
  // Collect every device in the site (flattened across whatever tiers
  // the heuristic put them in).
  const all = [].concat(
    tree.isp           || [],
    tree.wan_switches  || [],
    tree.firewalls     || [],
    tree.core_switches || [],
    tree.switches      || [],
    tree.other         || [],
    [].concat.apply([], (tree.hypervisors || []).map(function(c) { return c.cells; })),
    [].concat.apply([], (tree.vm_clusters || []).map(function(c) { return c.cells; })),
    [].concat.apply([], (tree.ipmi        || []).map(function(c) { return c.cells; }))
  );
  // Pull latency from the noc summary's off_site block when names match.
  const offsite = (LM.summary && LM.summary.off_site) || [];
  const latencyByDid = {};
  offsite.forEach(function(o) { if (o.did) latencyByDid[o.did] = o.latency_ms; });

  const cards = all.map(function(d) {
    const st = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));
    const ms = latencyByDid[d.did];
    const lat = (ms == null)
      ? '<span class="latency down">— timeout</span>'
      : '<span class="latency">' + ms + 'ms</span>';
    return '<div class="sd-offsite-card ' + st + '" data-did="' + esc(d.did) + '">' +
             '<div class="sd-oc-row">' + ICONS.cloud +
               '<span class="sd-oc-name">' + esc(d.name) + '</span>' +
             '</div>' +
             '<div class="sd-oc-host">' + esc(d.host) + lat + '</div>' +
           '</div>';
  }).join('');

  main.innerHTML =
    '<div class="sd-wrap">' +
      '<div class="site">' +
        '<div class="site-corners"><span></span><span></span><span></span><span></span></div>' +
        '<div class="site-tab">' +
          '<span class="site-tab-k">SITE</span>' +
          '<span class="site-tab-n">' + esc(name) + '</span>' +
          '<span class="site-tab-s">› INTERNET REACHABILITY (PINNED)</span>' +
        '</div>' +
        '<div class="sd-canvas">' +
          '<div class="sd-internet-grid">' + (cards || '<div class="lm-empty">No reachability checks configured</div>') + '</div>' +
        '</div>' +
      '</div>' +
    '</div>';
  // Listener is bound once in boot() — see note in _renderSiteTree.
}

function _siteCanvasClick(e) {
  const cluster = e.target.closest('.cluster');
  const dev     = e.target.closest('.dev');
  if (cluster) {
    const cname = cluster.getAttribute('data-cluster');
    openClusterPanel(cname);
    return;
  }
  if (dev) {
    const did = dev.getAttribute('data-did');
    openDevicePanel(did);
    return;
  }
}

// ─── Side panel ─────────────────────────────────────────────
function openSidePanel(title, bodyHtml) {
  const sp = $('lm-sidepanel');
  $('lm-sp-title').textContent = title;
  $('lm-sp-body').innerHTML = bodyHtml;
  sp.classList.add('open');
  sp.setAttribute('aria-hidden', 'false');
}
function closeSidePanel() {
  const sp = $('lm-sidepanel');
  sp.classList.remove('open');
  sp.setAttribute('aria-hidden', 'true');
}

// Build {did → device card} and {clusterName → cluster card} lookups for a tree.
// Devices include cluster members so any cell can be resolved by its did.
function _buildTreeIndex(tree) {
  const devs = {};
  const clusters = {};
  const flatDevs = [].concat(
    tree.isp || [], tree.wan_switches || [], tree.firewalls || [],
    tree.core_switches || [], tree.switches || [], tree.other || []
  );
  flatDevs.forEach(function(d) { devs[d.did] = d; });
  const clusterLists = [
    {tier: 'chassis',    list: tree.chassis     || []},
    {tier: 'hypervisor', list: tree.hypervisors || []},
    {tier: 'vm',         list: tree.vm_clusters || []},
    {tier: 'ipmi',       list: tree.ipmi        || []},
  ];
  clusterLists.forEach(function(grp) {
    grp.list.forEach(function(c) {
      clusters[c.name] = {tier: grp.tier, cluster: c};
      (c.cells || []).forEach(function(cell) {
        devs[cell.did] = {did: cell.did, name: cell.name, host: cell.host,
                          status: cell.status, _tier: grp.tier, _clusterName: c.name};
      });
    });
  });
  return {devs: devs, clusters: clusters};
}

// Resolve a parent_device_id entry ("group:<name>" or "<did>") to a render-ready ref.
function _resolveParentRef(pid, idx) {
  if (typeof pid === 'string' && pid.indexOf('group:') === 0) {
    const name = pid.slice(6);
    const ci = idx.clusters[name];
    if (ci) return {kind: 'cluster', tier: ci.tier, name: name,
                    status: ci.cluster.status, count: ci.cluster.count};
    return {kind: 'missing', tier: 'other', name: name + ' (missing group)', status: 'unknown'};
  }
  const d = idx.devs[pid];
  if (d) return {kind: 'device', tier: d._tier || 'device', did: d.did,
                 name: d.name, host: d.host, status: d.status};
  return {kind: 'missing', tier: 'other', name: '(off-site / missing device)', status: 'unknown'};
}

// Build an HTML row for a connection (upstream parent or downstream child).
function _connRowHtml(ref) {
  const st = ref.status === 'up' ? 'up' : (ref.status === 'warn' ? 'warn'
           : (ref.status === 'down' ? 'down' : 'unknown'));
  const sub = ref.kind === 'cluster'
            ? (ref.count != null ? esc(String(ref.count) + ' devices') : 'cluster')
            : esc(ref.host || '');
  const dataAttr = ref.kind === 'cluster' ? ' data-cluster="' + esc(ref.name) + '"'
                  : (ref.did ? ' data-did="' + esc(ref.did) + '"' : '');
  return '<div class="lm-sp-row ' + st + '"' + dataAttr + '>' +
           '<span class="sr-dot ' + st + '"></span>' +
           '<span class="lm-sp-name">' + esc(ref.name) + '</span>' +
           '<span class="lm-sp-host">' + sub + '</span>' +
         '</div>';
}

function openClusterPanel(cname) {
  // Look up the current site's tree from cache and find the cluster cells
  const siteName = LM.currentRoute.site;
  const tree = LM.treeCache[siteName];
  if (!tree) return;
  const all = [].concat(tree.chassis || [], tree.hypervisors || [],
                        tree.vm_clusters || [], tree.ipmi || []);
  const c = all.find(function(x) { return x.name === cname; });
  if (!c) return;

  const idx = _buildTreeIndex(tree);

  // Upstream — parents of this cluster (group-level + any per-cell parents
  // members may have. Dedup by ref key.)
  const upstreamSeen = {};
  const upstream = [];
  (c.parent_device_ids || []).forEach(function(pid) {
    if (upstreamSeen[pid]) return;
    upstreamSeen[pid] = true;
    upstream.push(_resolveParentRef(pid, idx));
  });

  // Downstream — anything that points to this cluster (group:<name>) OR to
  // an individual member did. Dedup by ref identity.
  const byParent = tree.by_parent || {};
  const downstreamRaw = [].concat(byParent['group:' + cname] || []);
  (c.cells || []).forEach(function(cell) {
    (byParent[cell.did] || []).forEach(function(r) { downstreamRaw.push(r); });
  });
  const downSeen = {};
  const downstream = [];
  downstreamRaw.forEach(function(r) {
    const key = r.kind + ':' + (r.did || r.name);
    if (downSeen[key]) return;
    downSeen[key] = true;
    if (r.kind === 'device') {
      const d = idx.devs[r.did];
      if (d) downstream.push({kind: 'device', tier: r.tier, did: r.did,
                              name: d.name, host: d.host, status: d.status});
    } else {
      const ci = idx.clusters[r.name];
      if (ci) downstream.push({kind: 'cluster', tier: r.tier, name: r.name,
                               status: ci.cluster.status, count: ci.cluster.count});
    }
  });

  // Device rows — name + IP
  const rows = (c.cells || []).map(function(cell) {
    const st = cell.status === 'up' ? 'up' : (cell.status === 'warn' ? 'warn'
             : (cell.status === 'down' ? 'down' : 'unknown'));
    return '<div class="lm-sp-row ' + st + '" data-did="' + esc(cell.did) + '">' +
             '<span class="sr-dot ' + st + '"></span>' +
             '<span class="lm-sp-name">' + esc(cell.name) + '</span>' +
             '<span class="lm-sp-host">' + esc(cell.host || '') + '</span>' +
           '</div>';
  }).join('');

  const header =
    '<div class="lm-sp-row up" style="border-left-color:var(--accent);background:rgba(0,212,255,0.06)">' +
      '<span class="lm-sp-name"><b>' + c.count + ' devices</b> · ' +
        '<span style="color:var(--up)">' + c.up + ' up</span> · ' +
        '<span style="color:var(--warn)">' + c.warn + ' warn</span> · ' +
        '<span style="color:var(--down)">' + c.down + ' down</span>' +
      '</span>' +
    '</div>';

  const upBlock = upstream.length
    ? '<div class="lm-sp-section">UPSTREAM · ' + upstream.length + '</div>' +
      upstream.map(_connRowHtml).join('')
    : '<div class="lm-sp-section">UPSTREAM</div>' +
      '<div class="lm-sp-empty">No parent links</div>';

  const downBlock = downstream.length
    ? '<div class="lm-sp-section">DOWNSTREAM · ' + downstream.length + '</div>' +
      downstream.map(_connRowHtml).join('')
    : '';

  const devBlock = '<div class="lm-sp-section">DEVICES · ' + c.count + '</div>' + rows;

  openSidePanel(cname.toUpperCase(), header + upBlock + downBlock + devBlock);
}

// Monotonic token so a fast click-through (panel A → panel B before A's
// /api/devices fetch resolves) doesn't let the older response overwrite the
// newer panel's sensor section.
let _devPanelToken = 0;

function _sensorStatusKey(s) {
  // Match the badge logic in the Devices view: explicit down beats threshold
  // warn/crit; missing data → unknown.
  if (s.alerts_muted) return 'muted';
  if (!s.running)     return 'paused';
  if (s.alive === false) return 'down';
  if (s.alive === true) {
    if (s.threshold_state === 'crit') return 'down';
    if (s.threshold_state === 'warn') return 'warn';
    return 'up';
  }
  return 'unknown';
}

function _sensorIssueText(s) {
  // Pick the most diagnostic single line for the row. last_detail covers
  // probe errors ("timeout", "connection refused", "200 OK"); threshold lines
  // describe the breach when the probe itself succeeded.
  const detail = (s.last_detail || '').trim();
  if (s.alive === false) return detail || 'probe failed';
  if (s.threshold_state === 'crit' || s.threshold_state === 'warn') {
    const lbl = s.threshold_state.toUpperCase();
    if (s.last_value != null && s.snmp_unit) return lbl + ' · ' + s.last_value + ' ' + s.snmp_unit;
    if (s.last_ms != null) return lbl + ' · ' + s.last_ms + 'ms';
    return lbl + (detail ? ' · ' + detail : '');
  }
  return detail;
}

function _sensorRowHtml(s) {
  const st = _sensorStatusKey(s);
  // Map non-status keys to a colour-equivalent for the dot CSS.
  const dotCls = (st === 'paused' || st === 'muted' || st === 'unknown') ? 'unknown' : st;
  const stype = (s.stype || '').toUpperCase();
  const issue = _sensorIssueText(s);
  return '<div class="lm-sp-srow ' + dotCls + '">' +
           '<span class="sr-dot ' + dotCls + '"></span>' +
           '<span class="lm-sp-srow-main">' +
             '<span class="lm-sp-srow-name">' + esc(s.name || s.sensor_id || '?') + '</span>' +
             (stype ? '<span class="lm-sp-srow-type">' + esc(stype) + '</span>' : '') +
           '</span>' +
           (issue ? '<span class="lm-sp-srow-issue">' + esc(issue) + '</span>' : '') +
         '</div>';
}

function _renderSensorBlock(devDict, showAll) {
  const sensors = Array.isArray(devDict && devDict.sensors) ? devDict.sensors : [];
  if (!sensors.length) {
    return '<div class="lm-sp-section">SENSORS</div>' +
           '<div class="lm-sp-empty">No sensors configured</div>';
  }
  // Failing = down (alive false) OR threshold warn/crit. Paused/muted sensors
  // aren't "issues" — they're explicitly suppressed and would just noise the
  // failing list.
  const failing = sensors.filter(function(s) {
    if (s.alerts_muted || !s.running) return false;
    if (s.alive === false) return true;
    if (s.threshold_state === 'crit' || s.threshold_state === 'warn') return true;
    return false;
  });
  const otherCount = sensors.length - failing.length;
  const list = showAll ? sensors : failing;
  // Sort: down first, then warn, then everything else by name.
  const rank = { down: 0, warn: 1, up: 2, unknown: 3, paused: 4, muted: 5 };
  list.sort(function(a, b) {
    const ra = rank[_sensorStatusKey(a)] ?? 9;
    const rb = rank[_sensorStatusKey(b)] ?? 9;
    if (ra !== rb) return ra - rb;
    return (a.name || '').localeCompare(b.name || '');
  });
  const header = failing.length
    ? '<div class="lm-sp-section warn">FAILING SENSORS · ' + failing.length + '</div>'
    : '<div class="lm-sp-section">SENSORS · ALL HEALTHY</div>';
  const rows = list.length
    ? list.map(_sensorRowHtml).join('')
    : '<div class="lm-sp-empty">No failing sensors</div>';
  // Toggle: only useful when there are other sensors beyond what's already shown.
  let toggle = '';
  if (otherCount > 0) {
    toggle = showAll
      ? '<button class="lm-sp-toggle" data-show="failing">Show failing only</button>'
      : '<button class="lm-sp-toggle" data-show="all">Show all (' + sensors.length + ')</button>';
  }
  return header + rows + toggle;
}

function openDevicePanel(did) {
  // Try to find this device in the current tree first
  const siteName = LM.currentRoute.site;
  const tree = LM.treeCache[siteName];
  if (!tree) return;
  const idx = _buildTreeIndex(tree);
  const d = idx.devs[did];
  if (!d) return;
  const st = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));

  // Resolve parent refs from the device card / cell when available
  const devCard = [].concat(
    tree.isp || [], tree.wan_switches || [], tree.firewalls || [],
    tree.core_switches || [], tree.switches || [], tree.other || []
  ).find(function(x) { return x.did === did; });
  let parentIds = (devCard && devCard.parent_device_ids) || [];
  if (!parentIds.length) {
    // Cell-level parent ids live inside cluster.cells
    const clusterLists = [tree.chassis || [], tree.hypervisors || [],
                          tree.vm_clusters || [], tree.ipmi || []];
    for (let i = 0; i < clusterLists.length && !parentIds.length; i++) {
      for (let j = 0; j < clusterLists[i].length && !parentIds.length; j++) {
        const cell = (clusterLists[i][j].cells || []).find(function(x) { return x.did === did; });
        if (cell) parentIds = cell.parent_device_ids || [];
      }
    }
  }
  const upstream = parentIds.map(function(pid) { return _resolveParentRef(pid, idx); });

  // Downstream — anyone pointing at this did
  const byParent = tree.by_parent || {};
  const downRefs = byParent[did] || [];
  const downstream = downRefs.map(function(r) {
    if (r.kind === 'device') {
      const dd = idx.devs[r.did];
      return dd ? {kind: 'device', tier: r.tier, did: r.did,
                   name: dd.name, host: dd.host, status: dd.status} : null;
    }
    const ci = idx.clusters[r.name];
    return ci ? {kind: 'cluster', tier: r.tier, name: r.name,
                 status: ci.cluster.status, count: ci.cluster.count} : null;
  }).filter(Boolean);

  const header =
    '<div class="lm-sp-row ' + st + '">' +
      '<span class="sr-dot ' + st + '"></span>' +
      '<span class="lm-sp-name">' + esc(d.name || '') + '</span>' +
      '<span class="lm-sp-host">' + esc(d.host || '') + '</span>' +
    '</div>' +
    '<div style="margin:6px 0 10px;font-family:\'JetBrains Mono\',monospace;font-size:10px;color:rgba(255,255,255,0.5);letter-spacing:1px">' +
      'Status: <span style="color:var(--' + (st === 'unknown' ? 'dim' : st) + ')">' + st.toUpperCase() + '</span><br/>' +
      ((devCard && devCard.group) ? 'Group: ' + esc(devCard.group) + '<br/>' : '') +
      ((devCard && devCard.alerts) ? 'Active alerts: <span style="color:var(--down)">' + devCard.alerts + '</span><br/>' : '') +
    '</div>';

  const upBlock = upstream.length
    ? '<div class="lm-sp-section">UPSTREAM · ' + upstream.length + '</div>' +
      upstream.map(_connRowHtml).join('')
    : '';
  const downBlock = downstream.length
    ? '<div class="lm-sp-section">DOWNSTREAM · ' + downstream.length + '</div>' +
      downstream.map(_connRowHtml).join('')
    : '';
  // Sensor section is fetched async so the panel opens instantly. Show a
  // skeleton row while in-flight; replace with the real list when the
  // /api/devices/{did} response lands (and the user hasn't moved on).
  const sensorSkeleton =
    '<div id="lm-sp-sensors">' +
      '<div class="lm-sp-section">SENSORS</div>' +
      '<div class="lm-sp-empty">Loading…</div>' +
    '</div>';

  openSidePanel(d.name ? d.name.toUpperCase() : 'DEVICE',
                header + upBlock + downBlock + sensorSkeleton);

  // Race guard: each open bumps the token; only the latest open is allowed
  // to mutate the DOM. Prevents a slow fetch on device A from blowing away
  // panel B's sensor list when the user click-throughs quickly.
  const myToken = ++_devPanelToken;
  _devPanelDid    = did;
  _devPanelShowAll = false;

  api('GET', '/api/devices/' + encodeURIComponent(did)).then(function(devDict) {
    if (myToken !== _devPanelToken) return;
    const slot = document.getElementById('lm-sp-sensors');
    if (!slot) return;
    _devPanelLastDict = devDict;
    slot.innerHTML = _renderSensorBlock(devDict, _devPanelShowAll);
  }).catch(function() {
    if (myToken !== _devPanelToken) return;
    const slot = document.getElementById('lm-sp-sensors');
    if (slot) slot.innerHTML =
      '<div class="lm-sp-section">SENSORS</div>' +
      '<div class="lm-sp-empty">Failed to load sensors</div>';
  });
}

// Panel-scoped state that drives the show-all toggle. Kept module-scoped (not
// closure-scoped) so the panel-body click handler can read it without rebinding
// per open.
let _devPanelDid      = null;
let _devPanelLastDict = null;
let _devPanelShowAll  = false;

// ─── Routing ────────────────────────────────────────────────
function parseHash() {
  const h = (window.location.hash || '').replace(/^#/, '');
  if (h.indexOf('/site/') === 0) {
    return { view: 'site', site: decodeURIComponent(h.slice(6)) };
  }
  return { view: 'noc', site: null };
}
function navigate(view, site) {
  if (view === 'site' && site) {
    window.location.hash = '/site/' + encodeURIComponent(site);
  } else {
    window.location.hash = '/noc';
  }
}
function handleRoute() {
  LM.currentRoute = parseHash();
  renderSidebar();
  renderScopeBar();
  closeSidePanel();
  if (LM.currentRoute.view === 'site') {
    renderSite(LM.currentRoute.site);
  } else {
    renderNOC();
  }
}
window.addEventListener('hashchange', handleRoute);

// ─── Data loading ───────────────────────────────────────────
async function fetchSitesAndSummary() {
  try {
    const [sitesPayload, summary] = await Promise.all([
      api('GET', '/api/livemap/sites'),
      api('GET', '/api/livemap/noc/summary'),
    ]);
    LM.sites = sitesPayload.sites || [];
    LM.sitesByName = {};
    LM.sites.forEach(function(s) { LM.sitesByName[s.name] = s; });
    LM.summary = summary;
  } catch (e) {
    console.warn('[livemap] failed to fetch sites/summary:', e);
  }
}

async function fetchSiteTree(name) {
  if (LM.treeCache[name]) return LM.treeCache[name];
  const tree = await api('GET', '/api/livemap/sites/' + encodeURIComponent(name) + '/tree');
  LM.treeCache[name] = tree;
  return tree;
}

async function refreshAll() {
  await fetchSitesAndSummary();
  // Invalidate site tree cache (status may have changed)
  LM.treeCache = {};
  LM._lastHash = _payloadHash();
  handleRoute();
}

// ─── SSE / postMessage from parent ──────────────────────────
// We re-render via innerHTML — cheap to write but visibly flashes if we do it
// too often. Bump the debounce to 2s and skip re-renders when the data hash
// hasn't actually changed (most SSE batches are no-ops for the NOC view).
function _payloadHash() {
  // Cheap fingerprint: sites' (name, status, devices, alerts) + summary totals.
  if (!LM.summary) return '';
  const sitePart = LM.sites.map(function(s) {
    return s.name + ':' + s.up + '/' + s.warn + '/' + s.down + '/' + s.alerts;
  }).join('|');
  const sum = LM.summary;
  const sumPart = [
    sum.sites.up,    sum.sites.warn,    sum.sites.down,
    sum.devices.up,  sum.devices.warn,  sum.devices.down,
    sum.alerts.active, sum.flaps_24h,
    (sum.recent_alerts || []).length,
    (sum.recent_alerts && sum.recent_alerts[0] && sum.recent_alerts[0].ts) || 0,
  ].join(',');
  return sitePart + '#' + sumPart;
}
function _flushSseBatch() {
  LM.ssePending = [];
  fetchSitesAndSummary().then(function() {
    const h = _payloadHash();
    if (h === LM._lastHash) return;       // nothing meaningful changed
    LM._lastHash = h;
    if (LM.currentRoute.view === 'noc') {
      renderNOC();
    } else {
      LM.treeCache = {};
      renderSite(LM.currentRoute.site);
    }
    renderSidebar();
    renderScopeBar();
  });
}
function _scheduleFlush() {
  if (LM.sseTimer) return;
  LM.sseTimer = setTimeout(function() {
    LM.sseTimer = null;
    _flushSseBatch();
  }, 2000);   // was 250 — coarser cadence eliminates visible flicker
}

window.addEventListener('message', function(e) {
  if (e.origin !== window.location.origin) return;
  const data = e.data || {};
  if (data.type === 'theme') {
    if (data.value === 'light') document.documentElement.setAttribute('data-theme', 'light');
    else document.documentElement.removeAttribute('data-theme');
    return;
  }
  if (data.type === 'ntm_update' || data.type === 'lm_update') {
    LM.ssePending.push(data);
    _scheduleFlush();
    return;
  }
  if (data.type === 'lm_refresh') {
    refreshAll();
    return;
  }
});

// Side-panel close + row drill-down binding (delegated)
document.addEventListener('click', function(e) {
  if (e.target && e.target.id === 'lm-sp-close') { closeSidePanel(); return; }
  // Sensor section show-all / show-failing toggle — re-render in place using
  // the cached dict so we don't refetch.
  const toggle = e.target.closest && e.target.closest('.lm-sp-toggle');
  if (toggle) {
    _devPanelShowAll = toggle.getAttribute('data-show') === 'all';
    const slot = document.getElementById('lm-sp-sensors');
    if (slot && _devPanelLastDict) {
      slot.innerHTML = _renderSensorBlock(_devPanelLastDict, _devPanelShowAll);
    }
    return;
  }
  const row = e.target.closest && e.target.closest('.lm-sp-body .lm-sp-row');
  if (!row) return;
  const cname = row.getAttribute('data-cluster');
  if (cname) { openClusterPanel(cname); return; }
  const did = row.getAttribute('data-did');
  if (did) { openDevicePanel(did); }
});

// ─── Boot ───────────────────────────────────────────────────
function _startLiveTick() {
  if (LM.liveTickTimer) return;
  LM.liveTickTimer = setInterval(function() {
    if (LM.currentRoute.view !== 'noc' || !LM.summary) return;
    document.querySelectorAll('.feed-row').forEach(function(row, i) {
      const a = (LM.summary.recent_alerts || [])[i];
      if (!a) return;
      const ago = row.querySelector('.feed-ago');
      if (ago) ago.textContent = timeAgo(a.ts);
    });
  }, 5000);
}
function _stopLiveTick() {
  if (LM.liveTickTimer) { clearInterval(LM.liveTickTimer); LM.liveTickTimer = null; }
}

async function boot() {
  bindSidebar();
  // Bind the canvas click handler once on the stable #lm-main element.
  // Site renders re-set main.innerHTML which removes children but leaves
  // the listener on the element itself; binding here keeps the count at 1
  // regardless of how many times the user navigates between sites.
  $('lm-main').addEventListener('click', _siteCanvasClick);
  if (!window.location.hash) window.location.hash = '/noc';
  await fetchSitesAndSummary();
  handleRoute();
  // Refresh time-ago text in the feed periodically (cheap), but pause
  // when the iframe is hidden behind another tab so we don't burn CPU.
  _startLiveTick();
  document.addEventListener('visibilitychange', function() {
    if (document.hidden) _stopLiveTick(); else _startLiveTick();
  });
}

// Expose hooks for forms-site.js
window._lmRefresh = refreshAll;
window._lmGetSites = function() { return LM.sites.slice(); };
window._lmGetSite  = function(n) { return LM.sitesByName[n]; };

document.addEventListener('DOMContentLoaded', boot);
})();
