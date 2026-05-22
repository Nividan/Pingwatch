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

// Sidebar event delegation — runs once. Site CRUD lives in the Devices tab
// now; the Live Map sidebar is read-only.
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
  $('lm-search').addEventListener('input', debounce(renderSidebar, 120));
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
  return '<div class="dev ' + status + '" data-did="' + esc(d.did) + '">' +
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
  // Mini status grid: one cell per child device. Auto-fit columns based on count.
  const cols = Math.max(4, Math.min(10, Math.ceil(Math.sqrt(c.cells.length))));
  const cells = c.cells.map(function(cell) {
    return '<div class="d-' + (cell.status || 'unknown') + '" title="' + esc(cell.name) + '"></div>';
  }).join('');
  return '<div class="cluster ' + status + '" data-cluster="' + esc(c.name) + '">' +
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

function _offsiteBand(s, currentSite) {
  // Render the OFF-Site band at the top of every site-detail view EXCEPT
  // when the user is drilled into the off-site itself (avoid showing the
  // same internet checks twice — once as the band and once as the main tree).
  if (!s || !s.off_site || !s.off_site.length) return '';
  const cur = (currentSite || '').toLowerCase();
  if (cur === 'off-site' || cur === 'offsite' || cur === 'internet') return '';
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

  const fws  = tree.firewalls   || [];
  const sws  = tree.switches    || [];
  const hyps = tree.hypervisors || [];
  const vms  = tree.vm_clusters || [];
  const ipmi = tree.ipmi        || [];

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

  function _fwRow(d) {
    return _devCard(d, { icon: ICONS.fw, role: 'PRIMARY' });
  }
  function _swRow(d) {
    return _devCard(d, { icon: ICONS.sw });
  }
  function _hypRow(c) {
    return _clusterCard(c, { icon: ICONS.hyp });
  }
  function _vmRow(c) {
    return _clusterCard(c, { icon: ICONS.vm });
  }
  function _ipmiRow(c) {
    return _clusterCard(c, { icon: ICONS.ipmi });
  }

  // IPMI inline at the right end of the HYPERVISORS row, with its own
  // tier-tag above the card. Sharing the row aligns IPMI vertically with
  // the hypervisor cluster cards (which is what the design intends).
  const ipmiTrailing = ipmi.length
    ? '<div class="sd-ipmi-inline">' +
        '<span class="tier-tag ipmi inline">IPMI · OOB</span>' +
        ipmi.map(_ipmiRow).join('') +
      '</div>'
    : '';

  main.innerHTML =
    '<div class="sd-wrap">' +
      _offsiteBand(LM.summary, name) +
      '<div class="site">' +
        '<div class="site-corners"><span></span><span></span><span></span><span></span></div>' +
        '<div class="site-tab">' +
          '<span class="site-tab-k">SITE</span>' +
          '<span class="site-tab-n">' + esc(name) + '</span>' +
          '<span class="site-tab-s">› SELECTED · MAIN INFRASTRUCTURE</span>' +
        '</div>' +
        '<div class="sd-canvas">' +
          tierRow('fw',  'FIREWALL',    fws,  { render: _fwRow,  center: true }) +
          tierRow('sw',  'SWITCHES',    sws,  { render: _swRow,  center: false }) +
          tierRow('hyp', 'HYPERVISORS', hyps, { render: _hypRow, trailing: ipmiTrailing }) +
          tierRow('vm',  'VM CLUSTERS', vms,  { render: _vmRow }) +
        '</div>' +
      '</div>' +
      (tree.other && tree.other.length
        ? '<div class="sd-other"><div class="sd-other-h">OTHER · UNCLASSIFIED DEVICES (' + tree.other.length + ')</div>' +
          '<div class="sd-other-grid">' +
            tree.other.map(function(d) {
              const st = d.status === 'up' ? 'up' : (d.status === 'warn' ? 'warn' : (d.status === 'down' ? 'down' : 'unknown'));
              return '<div class="dev ' + st + '" data-did="' + esc(d.did) + '">' +
                       '<div class="dev-row">' + ICONS.sw + '<span class="dev-name">' + esc(d.name) + '</span></div>' +
                       '<div class="dev-ip">' + esc(d.host) + '</div>' +
                     '</div>';
            }).join('') +
          '</div></div>'
        : '');

  // Cluster click → side panel
  // Listener is bound once in boot() now; binding here would accumulate
  // a new handler on every site render.
}

function _renderInternetSite(name, tree) {
  const main = $('lm-main');
  // Collect every device in the site (flattened across whatever tiers
  // the heuristic put them in).
  const all = [].concat(
    tree.firewalls   || [],
    tree.switches    || [],
    tree.other       || [],
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

// Side-panel close binding (delegated)
document.addEventListener('click', function(e) {
  if (e.target && e.target.id === 'lm-sp-close') closeSidePanel();
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
