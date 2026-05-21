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

// Tier Y constants (from the design reference, topology-variations.jsx)
const SD = {
  Y_FW:  56,    // firewall row top
  Y_SW:  168,
  Y_HYP: 286,
  Y_VM:  416,
  H_FW:  60,
  H_SW:  56,
  H_HYP: 116,
  H_VM:  100,
  ML:    80,    // left margin (after tier-tag column)
  MR:    80,    // right margin reserved for IPMI pillar
};

// Inline SVG icons (Heroicons-ish), kept tiny + cyan-tinted
const ICONS = {
  fw:     '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/></svg>',
  sw:     '<svg class="dev-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="9" width="18" height="6" rx="1"/><circle cx="7" cy="12" r="0.7" fill="currentColor"/><circle cx="11" cy="12" r="0.7" fill="currentColor"/><circle cx="15" cy="12" r="0.7" fill="currentColor"/><circle cx="19" cy="12" r="0.7" fill="currentColor"/></svg>',
  hyp:    '<svg class="cluster-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><circle cx="6.5" cy="7" r="0.6" fill="currentColor"/><circle cx="6.5" cy="17" r="0.6" fill="currentColor"/></svg>',
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

function _siteRow(site, opts) {
  opts = opts || {};
  const status = worstStatus(site);
  const kind = (site.kind || 'lab').toLowerCase();
  const abbrev = kindAbbrev(kind);
  const selClass = (LM.currentRoute.view === 'site' && LM.currentRoute.site === site.name) ? ' sel' : '';
  const alerts = site.alerts ? '<span class="sr-alerts">' + site.alerts + '</span>' : '';
  const display = site.display_name || site.name;
  const editBtn = opts.editable
    ? '<button class="sr-edit" title="Edit site" data-site="' + esc(site.name) + '">⚙</button>'
    : '';
  return '<div class="site-row' + selClass + '" data-site="' + esc(site.name) + '">' +
           '<span class="sr-dot ' + status + '"></span>' +
           '<span class="sr-kind ' + esc(kind) + '">' + esc(abbrev) + '</span>' +
           '<span class="sr-name">' + esc(display) + '</span>' +
           '<span class="sr-meta">' +
              '<span class="sr-count">' + site.devices + '</span>' +
              alerts +
           '</span>' +
           editBtn +
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
    ? pinned.map(function(s) { return _siteRow(s, { editable: true }); }).join('')
    : '<div class="lm-empty" style="padding:6px;text-align:left">— none</div>';
  $('lm-list-all').innerHTML    = rest.length
    ? rest.map(function(s) { return _siteRow(s, { editable: true }); }).join('')
    : '<div class="lm-empty" style="padding:6px;text-align:left">— no sites yet</div>';

  $('lm-search-count').textContent = String(LM.sites.length);
}

// Sidebar event delegation — runs once
function bindSidebar() {
  const sb = $('lm-sidebar');
  sb.addEventListener('click', function(e) {
    // Edit-cog button takes priority
    const ed = e.target.closest('.sr-edit');
    if (ed) {
      e.stopPropagation();
      const name = ed.getAttribute('data-site');
      window._lmOpenSiteModal && window._lmOpenSiteModal('edit', name);
      return;
    }
    const row = e.target.closest('.site-row');
    if (!row) return;
    if (row.getAttribute('data-view') === 'noc') {
      navigate('noc');
    } else {
      const name = row.getAttribute('data-site');
      if (name) navigate('site', name);
    }
  });
  $('lm-search').addEventListener('input', debounce(renderSidebar, 120));
  $('lm-add-site').addEventListener('click', function() {
    window._lmOpenSiteModal && window._lmOpenSiteModal('add');
  });
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

  // Mosaic — site cells with sqrt-sized spans
  const cells = LM.sites.map(function(site) {
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
  return '<div class="dev ' + status + '" data-did="' + esc(d.did) + '" ' +
              'style="left:' + opts.x + 'px;top:' + opts.y + 'px;width:' + opts.w + 'px">' +
           '<div class="dev-row">' + icon +
             '<span class="dev-name">' + esc(d.name) + '</span>' +
             role +
           '</div>' +
           '<div class="dev-ip">' + esc(d.host) + '</div>' +
         '</div>';
}

function _clusterCard(c, opts) {
  const status = c.status === 'up' ? 'up' : (c.status === 'warn' ? 'warn' : (c.status === 'down' ? 'down' : 'unknown'));
  const icon = opts.icon || ICONS.hyp;
  // Mini status grid: one cell per child device. Auto-fit columns.
  const cols = Math.max(4, Math.min(10, Math.ceil(Math.sqrt(c.cells.length))));
  const cells = c.cells.map(function(cell) {
    return '<div class="d-' + (cell.status || 'unknown') + '" title="' + esc(cell.name) + '"></div>';
  }).join('');
  return '<div class="cluster ' + status + '" data-cluster="' + esc(c.name) + '" ' +
              'style="left:' + opts.x + 'px;top:' + opts.y + 'px;width:' + opts.w + 'px;height:' + opts.h + 'px">' +
           '<div class="cluster-head">' + icon +
             '<span class="cluster-title">' + esc(c.name) + '</span>' +
             '<span class="cluster-count">' + c.count + '</span>' +
           '</div>' +
           '<div class="cluster-grid" style="grid-template-columns:repeat(' + cols + ',1fr)">' + cells + '</div>' +
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

function _offsiteBand(s) {
  // Render the OFF-Site band at the top of every site-detail view.
  if (!s || !s.off_site || !s.off_site.length) {
    return '';
  }
  const cards = s.off_site.map(function(o) {
    const cls = o.status === 'up' ? 'up' : (o.status === 'warn' ? 'warn' : (o.status === 'down' ? 'down' : 'unknown'));
    const ms = (o.latency_ms == null)
      ? '<span class="latency down">— timeout</span>'
      : '<span class="latency">' + o.latency_ms + 'ms</span>';
    return '<div class="sd-offsite-card ' + cls + '">' +
             '<div class="sd-oc-row">' + ICONS.cloud +
               '<span class="sd-oc-name">' + esc(o.name) + '</span>' +
             '</div>' +
             '<div class="sd-oc-host">' + esc(o.host) + ms + '</div>' +
           '</div>';
  }).join('');
  return '<div class="sd-offsite">' +
           '<div class="sd-offsite-head">' +
             '<span class="sd-offsite-k">SITE</span>' +
             '<span class="sd-offsite-n">OFF-Site</span>' +
             '<span class="sd-offsite-s">› INTERNET REACHABILITY (PINNED)</span>' +
           '</div>' +
           '<div class="sd-offsite-row">' + cards + '</div>' +
         '</div>';
}

function _renderSiteTree(name, tree) {
  const main = $('lm-main');
  // Compose the wrap. The .site container is a flex:1 region; tier devices
  // are positioned absolutely inside it relative to its bounding box.
  main.innerHTML =
    '<div class="sd-wrap">' +
      _offsiteBand(LM.summary) +
      '<div class="site">' +
        '<div class="site-corners"><span></span><span></span><span></span><span></span></div>' +
        '<div class="site-tab">' +
          '<span class="site-tab-k">SITE</span>' +
          '<span class="site-tab-n">' + esc(name) + '</span>' +
          '<span class="site-tab-s">› SELECTED · MAIN INFRASTRUCTURE</span>' +
        '</div>' +
        '<svg class="conn-svg"></svg>' +
        '<div class="site-canvas" style="position:absolute;inset:0"></div>' +
      '</div>' +
      (tree.other && tree.other.length
        ? '<div class="sd-other"><div class="sd-other-h">OTHER · UNCLASSIFIED DEVICES</div>' +
          '<div class="sd-other-grid">' +
            tree.other.map(function(d) {
              const st = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));
              return '<div class="dev ' + st + '" data-did="' + esc(d.did) + '" style="position:static">' +
                       '<div class="dev-row">' + ICONS.sw + '<span class="dev-name">' + esc(d.name) + '</span></div>' +
                       '<div class="dev-ip">' + esc(d.host) + '</div>' +
                     '</div>';
            }).join('') +
          '</div></div>'
        : '');

  // Tier tags
  const canvas = main.querySelector('.site-canvas');
  const fws  = tree.firewalls   || [];
  const sws  = tree.switches    || [];
  const hyps = tree.hypervisors || [];
  const vms  = tree.vm_clusters || [];
  const ipmi = tree.ipmi        || [];

  // Add tier-tag labels at left margin
  function tag(cls, label, y) {
    const t = ce('div', 'tier-tag ' + cls, esc(label));
    t.style.left = '16px';
    t.style.top  = y + 'px';
    canvas.appendChild(t);
  }
  if (fws.length)  tag('fw',   'FIREWALL',    SD.Y_FW  - 18);
  if (sws.length)  tag('sw',   'SWITCHES',    SD.Y_SW  - 18);
  if (hyps.length) tag('hyp',  'HYPERVISORS', SD.Y_HYP - 18);
  if (vms.length)  tag('vm',   'VM CLUSTERS', SD.Y_VM  - 18);
  if (ipmi.length) {
    const t = ce('div', 'tier-tag ipmi', 'IPMI · OOB');
    t.style.right = '14px';
    t.style.top   = (SD.Y_HYP - 18) + 'px';
    canvas.appendChild(t);
  }

  // Lay out firewall row (centered)
  const canvasRect = function() { return canvas.getBoundingClientRect(); };
  // We need to wait until layout has happened. Use requestAnimationFrame.
  requestAnimationFrame(function() {
    const rect = canvas.getBoundingClientRect();
    const W = rect.width;
    if (W < 200) {
      // Layout not ready yet, retry once
      requestAnimationFrame(function() { _layoutTiers(canvas, name, tree); });
    } else {
      _layoutTiers(canvas, name, tree);
    }
  });

  // Cluster click → side panel
  main.addEventListener('click', _siteCanvasClick);
}

function _layoutTiers(canvas, name, tree) {
  // Wipe any device/cluster cards from a previous render
  canvas.querySelectorAll('.dev, .cluster').forEach(function(n) { n.remove(); });
  const rect = canvas.getBoundingClientRect();
  const W = rect.width;
  const fws  = tree.firewalls   || [];
  const sws  = tree.switches    || [];
  const hyps = tree.hypervisors || [];
  const vms  = tree.vm_clusters || [];
  const ipmi = tree.ipmi        || [];

  const usableW = W - SD.ML - SD.MR;        // body width between tier-tag and IPMI pillar
  const cx      = SD.ML + usableW / 2;

  const cardsHtml = [];

  // Firewall(s): single row centered. If multiple, distribute.
  const fwW = Math.min(260, Math.max(180, usableW * 0.42));
  if (fws.length === 1) {
    cardsHtml.push(_devCard(fws[0], { x: Math.round(cx - fwW / 2), y: SD.Y_FW, w: fwW, icon: ICONS.fw, role: 'PRIMARY' }));
  } else {
    fws.forEach(function(d, i) {
      const slot = usableW / fws.length;
      cardsHtml.push(_devCard(d, {
        x: Math.round(SD.ML + slot * i + (slot - fwW) / 2),
        y: SD.Y_FW, w: fwW, icon: ICONS.fw,
        role: i === 0 ? 'PRIMARY' : 'SECONDARY'
      }));
    });
  }

  // Switches: spread evenly across the usable width
  const swW = Math.min(220, Math.max(160, usableW * 0.32));
  sws.forEach(function(d, i) {
    const slot = sws.length ? usableW / sws.length : usableW;
    const x = SD.ML + slot * i + (slot - swW) / 2;
    cardsHtml.push(_devCard(d, { x: Math.round(x), y: SD.Y_SW, w: swW, icon: ICONS.sw }));
  });

  // Hypervisor clusters: evenly across usable width
  const hypW = Math.min(190, Math.max(150, usableW / Math.max(1, hyps.length) - 12));
  hyps.forEach(function(c, i) {
    const slot = hyps.length ? usableW / hyps.length : usableW;
    const x = SD.ML + slot * i + (slot - hypW) / 2;
    cardsHtml.push(_clusterCard(c, { x: Math.round(x), y: SD.Y_HYP, w: hypW, h: SD.H_HYP, icon: ICONS.hyp }));
  });

  // VM clusters: align under hypervisors when count matches; else distribute
  const vmW = Math.min(190, Math.max(150, usableW / Math.max(1, vms.length) - 12));
  vms.forEach(function(c, i) {
    const slot = vms.length ? usableW / vms.length : usableW;
    const x = SD.ML + slot * i + (slot - vmW) / 2;
    cardsHtml.push(_clusterCard(c, { x: Math.round(x), y: SD.Y_VM, w: vmW, h: SD.H_VM, icon: ICONS.vm }));
  });

  // IPMI pillar: vertical card on the right that spans hyp + vm rows
  if (ipmi.length) {
    const pillarH = (SD.Y_VM + SD.H_VM) - SD.Y_HYP;
    const pillarW = 110;
    // Stack IPMI clusters vertically inside the pillar slot
    ipmi.forEach(function(c, i) {
      const slotH = pillarH / ipmi.length;
      cardsHtml.push(_clusterCard(c, {
        x: Math.round(W - SD.MR + 6),
        y: SD.Y_HYP + slotH * i,
        w: pillarW,
        h: Math.max(80, slotH - 8),
        icon: ICONS.ipmi,
      }));
    });
  }

  // Append all cards in one operation (so a DocumentFragment isn't needed)
  const wrap = document.createElement('div');
  wrap.innerHTML = cardsHtml.join('');
  while (wrap.firstChild) canvas.appendChild(wrap.firstChild);

  // Build SVG connection paths
  _drawConnections(canvas, { fws: fws.length, sws: sws.length, hyps: hyps.length, vms: vms.length, ipmi: ipmi.length, W: W });
}

function _drawConnections(canvas, ctx) {
  const svg = canvas.parentElement.querySelector('.conn-svg');
  if (!svg) return;
  svg.setAttribute('viewBox', '0 0 ' + ctx.W + ' 600');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.innerHTML = '';

  const cx = SD.ML + (ctx.W - SD.ML - SD.MR) / 2;
  const fwBottom = SD.Y_FW + SD.H_FW;
  const swTop    = SD.Y_SW;
  const swBottom = SD.Y_SW + SD.H_SW;
  const hypTop   = SD.Y_HYP;
  const hypBottom= SD.Y_HYP + SD.H_HYP;
  const vmTop    = SD.Y_VM;

  function path(d, attrs) {
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', d);
    for (const k in attrs) p.setAttribute(k, attrs[k]);
    svg.appendChild(p);
  }

  // Uplink from top into FortiGate (gold, animated dashed)
  if (ctx.fws) {
    path('M ' + cx + ' 0 L ' + cx + ' ' + SD.Y_FW,
         { stroke: 'rgba(255,215,0,0.65)', 'stroke-width': '1.5',
           'stroke-dasharray': '4 6', class: 'flow' });
  }

  // FortiGate → switches
  if (ctx.fws && ctx.sws) {
    const slot = (ctx.W - SD.ML - SD.MR) / ctx.sws;
    for (let i = 0; i < ctx.sws; i++) {
      const sx = SD.ML + slot * i + slot / 2;
      const my = (fwBottom + swTop) / 2;
      const d = 'M ' + cx + ' ' + fwBottom +
                ' L ' + cx + ' ' + my +
                ' L ' + sx + ' ' + my +
                ' L ' + sx + ' ' + swTop;
      const attrs = {
        stroke: 'rgba(0,212,255,0.6)', 'stroke-width': '1.5',
        'stroke-dasharray': '6 4'
      };
      if (i === 0) attrs.class = 'flow';
      path(d, attrs);
    }
  }

  // Switches → hypervisors (split evenly: each switch feeds an equal slice)
  if (ctx.sws && ctx.hyps) {
    const swSlot = (ctx.W - SD.ML - SD.MR) / ctx.sws;
    const hypSlot = (ctx.W - SD.ML - SD.MR) / ctx.hyps;
    const perSwitch = Math.ceil(ctx.hyps / ctx.sws);
    for (let i = 0; i < ctx.hyps; i++) {
      const hx = SD.ML + hypSlot * i + hypSlot / 2;
      const swIdx = Math.min(ctx.sws - 1, Math.floor(i / perSwitch));
      const sx = SD.ML + swSlot * swIdx + swSlot / 2;
      const my = (swBottom + hypTop) / 2;
      const d = 'M ' + sx + ' ' + swBottom +
                ' L ' + sx + ' ' + my +
                ' L ' + hx + ' ' + my +
                ' L ' + hx + ' ' + hypTop;
      const attrs = {
        stroke: 'rgba(0,255,157,0.55)', 'stroke-width': '1.4',
        'stroke-dasharray': '4 4'
      };
      if (i < 2) attrs.class = 'flow';
      path(d, attrs);
    }
  }

  // Hypervisors → VM clusters (vertical)
  if (ctx.hyps && ctx.vms) {
    const hypSlot = (ctx.W - SD.ML - SD.MR) / ctx.hyps;
    const vmSlot  = (ctx.W - SD.ML - SD.MR) / ctx.vms;
    for (let i = 0; i < Math.max(ctx.hyps, ctx.vms); i++) {
      const hx = SD.ML + hypSlot * Math.min(i, ctx.hyps - 1) + hypSlot / 2;
      const vx = SD.ML + vmSlot  * Math.min(i, ctx.vms - 1)  + vmSlot  / 2;
      const my = (hypBottom + vmTop) / 2;
      const d = 'M ' + hx + ' ' + hypBottom +
                ' L ' + hx + ' ' + my +
                ' L ' + vx + ' ' + my +
                ' L ' + vx + ' ' + vmTop;
      path(d, { stroke: 'rgba(0,212,255,0.5)', 'stroke-width': '1.3',
                'stroke-dasharray': '3 5' });
    }
  }

  // IPMI: dashed purple from the right-most switch
  if (ctx.sws && ctx.ipmi) {
    const swSlot = (ctx.W - SD.ML - SD.MR) / ctx.sws;
    const sx = SD.ML + swSlot * (ctx.sws - 1) + swSlot / 2;
    const ix = ctx.W - SD.MR + 6 + 55;
    const my = (swBottom + hypTop) / 2;
    path('M ' + sx + ' ' + swBottom +
         ' L ' + sx + ' ' + my +
         ' L ' + ix + ' ' + my +
         ' L ' + ix + ' ' + hypTop,
         { stroke: 'rgba(168,85,247,0.55)', 'stroke-width': '1.3',
           'stroke-dasharray': '2 6' });
  }
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

function openClusterPanel(cname) {
  // Look up the current site's tree from cache and find the cluster cells
  const siteName = LM.currentRoute.site;
  const tree = LM.treeCache[siteName];
  if (!tree) return;
  const all = [].concat(tree.hypervisors || [], tree.vm_clusters || [], tree.ipmi || []);
  const c = all.find(function(x) { return x.name === cname; });
  if (!c) return;
  const rows = c.cells.map(function(cell) {
    const st = cell.status === 'up' ? 'up' : (cell.status === 'warn' ? 'warn' : (cell.status === 'down' ? 'down' : 'unknown'));
    return '<div class="lm-sp-row ' + st + '" data-did="' + esc(cell.did) + '">' +
             '<span class="sr-dot ' + st + '"></span>' +
             '<span class="lm-sp-name">' + esc(cell.name) + '</span>' +
           '</div>';
  }).join('');
  openSidePanel(cname.toUpperCase(),
    '<div class="lm-sp-row up" style="border-left-color:var(--accent);background:rgba(0,212,255,0.06)">' +
      '<span class="lm-sp-name"><b>' + c.count + ' devices</b> · ' +
        '<span style="color:var(--up)">' + c.up + ' up</span> · ' +
        '<span style="color:var(--warn)">' + c.warn + ' warn</span> · ' +
        '<span style="color:var(--down)">' + c.down + ' down</span>' +
      '</span>' +
    '</div>' + rows
  );
}

function openDevicePanel(did) {
  // Try to find this device in the current tree first
  const siteName = LM.currentRoute.site;
  const tree = LM.treeCache[siteName];
  if (!tree) return;
  const allDevs = [].concat(
    tree.firewalls || [],
    tree.switches  || [],
    tree.other     || [],
    [].concat.apply([], (tree.hypervisors || []).map(function(c) { return c.cells; })),
    [].concat.apply([], (tree.vm_clusters || []).map(function(c) { return c.cells; })),
    [].concat.apply([], (tree.ipmi        || []).map(function(c) { return c.cells; }))
  );
  const d = allDevs.find(function(x) { return x.did === did; });
  if (!d) return;
  const st = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));
  openSidePanel(d.name ? d.name.toUpperCase() : 'DEVICE',
    '<div class="lm-sp-row ' + st + '">' +
      '<span class="sr-dot ' + st + '"></span>' +
      '<span class="lm-sp-name">' + esc(d.name || '') + '</span>' +
      '<span class="lm-sp-host">' + esc(d.host || '') + '</span>' +
    '</div>' +
    '<div style="margin-top:10px;font-family:\'Share Tech Mono\',monospace;font-size:10px;color:rgba(255,255,255,0.5);letter-spacing:1px">' +
      'Status: <span style="color:var(--' + (st === 'unknown' ? 'dim' : st) + ')">' + st.toUpperCase() + '</span><br/>' +
      (d.group ? 'Group: ' + esc(d.group) + '<br/>' : '') +
      (d.alerts ? 'Active alerts: <span style="color:var(--down)">' + d.alerts + '</span><br/>' : '') +
    '</div>'
  );
}

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
  handleRoute();
}

// ─── SSE / postMessage from parent ──────────────────────────
function _flushSseBatch() {
  // Coalesce: any update → refetch summary + sites in the background
  // (debounced flushes happen at 250ms via _scheduleFlush).
  LM.ssePending = [];
  fetchSitesAndSummary().then(function() {
    // Re-render current view in place
    if (LM.currentRoute.view === 'noc') {
      renderNOC();
    } else {
      // Bust the per-site tree cache and re-render
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
  }, 250);
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

// Side-panel close binding (delegated)
document.addEventListener('click', function(e) {
  if (e.target && e.target.id === 'lm-sp-close') closeSidePanel();
});

// ─── Boot ───────────────────────────────────────────────────
async function boot() {
  bindSidebar();
  if (!window.location.hash) window.location.hash = '/noc';
  await fetchSitesAndSummary();
  handleRoute();
  // Refresh time-ago text in the feed periodically (cheap)
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

// Expose hooks for forms-site.js
window._lmRefresh = refreshAll;
window._lmGetSites = function() { return LM.sites.slice(); };
window._lmGetSite  = function(n) { return LM.sitesByName[n]; };

document.addEventListener('DOMContentLoaded', boot);
})();
