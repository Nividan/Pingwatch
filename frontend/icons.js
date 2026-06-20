// ───────────────────────────────────────────────────────────────
// PingWatch — Icon library (lucide-style strokes, vanilla JS)
// Ported from the design prototype's shell.jsx. Single export:
//   icon(name, size=16, attrs={})  → SVG string
// Usage:
//   el.innerHTML = icon('dashboard', 18);
//   `<button>${icon('refresh', 14)} Refresh</button>`
// ───────────────────────────────────────────────────────────────
(function (global) {
  const PATHS = {
    dashboard:     '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    devices:       '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><circle cx="7" cy="7" r=".7" fill="currentColor"/><circle cx="7" cy="17" r=".7" fill="currentColor"/>',
    events:        '<path d="M13 2 L4 14 H11 L10 22 L20 9 H13 Z"/>',
    map:           '<polyline points="3 6 9 4 15 6 21 4 21 18 15 20 9 18 3 20 3 6"/><line x1="9" y1="4" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="20"/>',
    livemap:       '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><line x1="12" y1="3" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="21"/><line x1="3" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="21" y2="12"/>',
    ipam:          '<path d="M4 5 H20 M4 12 H20 M4 19 H20"/><circle cx="7" cy="5" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="17" cy="19" r="1.2" fill="currentColor"/>',
    alerts:        '<path d="M6 8 a6 6 0 0 1 12 0 c0 5 2 7 2 7 H4 s2-2 2-7"/><path d="M10 19 a2 2 0 0 0 4 0"/>',
    reports:       '<rect x="4" y="3" width="16" height="18" rx="1.5"/><line x1="8" y1="9" x2="16" y2="9"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/>',
    backups:       '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6 V12 a8 3 0 0 0 16 0 V6"/><path d="M4 12 V18 a8 3 0 0 0 16 0 V12"/>',
    probes:        '<line x1="12" y1="13" x2="12" y2="21"/><line x1="8" y1="21" x2="16" y2="21"/><circle cx="12" cy="11" r="2" fill="currentColor"/><path d="M7.5 6.5 a6.4 6.4 0 0 1 9 0"/><path d="M5 4 a10 10 0 0 1 14 0"/>',
    logs:          '<path d="M14 3 H7 a2 2 0 0 0-2 2 v14 a2 2 0 0 0 2 2 h10 a2 2 0 0 0 2-2 V8 Z"/><polyline points="14 3 14 8 19 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="13" y2="17"/>',
    settings:      '<circle cx="12" cy="12" r="3"/><path d="M19.4 15 a1.7 1.7 0 0 0 .3 1.8 l.1.1 a2 2 0 1 1-2.8 2.8 l-.1-.1 a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5 V21 a2 2 0 1 1-4 0 v-.1 a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3 l-.1.1 a2 2 0 1 1-2.8-2.8 l.1-.1 a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1 H3 a2 2 0 1 1 0-4 h.1 a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8 l-.1-.1 a2 2 0 1 1 2.8-2.8 l.1.1 a1.7 1.7 0 0 0 1.8.3 H9 a1.7 1.7 0 0 0 1-1.5 V3 a2 2 0 1 1 4 0 v.1 a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3 l.1-.1 a2 2 0 1 1 2.8 2.8 l-.1.1 a1.7 1.7 0 0 0-.3 1.8 V9 a1.7 1.7 0 0 0 1.5 1 H21 a2 2 0 1 1 0 4 h-.1 a1.7 1.7 0 0 0-1.5 1 z"/>',
    search:        '<circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="21" y2="21"/>',
    bell:          '<path d="M6 8 a6 6 0 0 1 12 0 c0 5 2 7 2 7 H4 s2-2 2-7"/><path d="M10 19 a2 2 0 0 0 4 0"/>',
    sun:           '<circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/><line x1="4.5" y1="4.5" x2="6.5" y2="6.5"/><line x1="17.5" y1="17.5" x2="19.5" y2="19.5"/><line x1="4.5" y1="19.5" x2="6.5" y2="17.5"/><line x1="17.5" y1="6.5" x2="19.5" y2="4.5"/>',
    moon:          '<path d="M21 12.8 A8 8 0 1 1 11.2 3 a6.2 6.2 0 0 0 9.8 9.8 z"/>',
    chevron_down:  '<polyline points="6 9 12 15 18 9"/>',
    chevron_right: '<polyline points="9 6 15 12 9 18"/>',
    chevron_left:  '<polyline points="15 6 9 12 15 18"/>',
    chevron_up:    '<polyline points="6 15 12 9 18 15"/>',
    plus:          '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    refresh:       '<polyline points="20 4 20 10 14 10"/><polyline points="4 20 4 14 10 14"/><path d="M5 14 a8 8 0 0 1 12.7-3.7 L20 14 M4 10 L6.3 13.7 A8 8 0 0 0 19 17"/>',
    download:      '<path d="M12 3 V15"/><polyline points="7 10 12 15 17 10"/><line x1="4" y1="20" x2="20" y2="20"/>',
    upload:        '<path d="M12 21 V9"/><polyline points="7 14 12 9 17 14"/><line x1="4" y1="4" x2="20" y2="4"/>',
    filter:        '<polygon points="3 4 21 4 14 13 14 20 10 18 10 13 3 4"/>',
    more:          '<circle cx="5" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="19" cy="12" r="1.2" fill="currentColor"/>',
    grid:          '<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
    list:          '<line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/><line x1="8" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r=".8" fill="currentColor"/><circle cx="4" cy="12" r=".8" fill="currentColor"/><circle cx="4" cy="18" r=".8" fill="currentColor"/>',
    check:         '<polyline points="5 12 10 17 19 7"/>',
    x:             '<line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/>',
    arrow_up:      '<line x1="12" y1="19" x2="12" y2="5"/><polyline points="6 11 12 5 18 11"/>',
    arrow_down:    '<line x1="12" y1="5" x2="12" y2="19"/><polyline points="6 13 12 19 18 13"/>',
    expand:        '<polyline points="4 14 4 20 10 20"/><polyline points="20 10 20 4 14 4"/><line x1="4" y1="20" x2="10" y2="14"/><line x1="14" y1="10" x2="20" y2="4"/>',
    pause:         '<rect x="6" y="5" width="4" height="14"/><rect x="14" y="5" width="4" height="14"/>',
    play:          '<polygon points="6 4 20 12 6 20 6 4"/>',
    flag:          '<line x1="5" y1="3" x2="5" y2="21"/><path d="M5 4 H17 L15 8 L17 12 H5"/>',
    cpu:           '<rect x="6" y="6" width="12" height="12" rx="1"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="2" x2="9" y2="6"/><line x1="15" y1="2" x2="15" y2="6"/><line x1="9" y1="18" x2="9" y2="22"/><line x1="15" y1="18" x2="15" y2="22"/><line x1="18" y1="9" x2="22" y2="9"/><line x1="18" y1="15" x2="22" y2="15"/><line x1="2" y1="9" x2="6" y2="9"/><line x1="2" y1="15" x2="6" y2="15"/>',
    activity:      '<polyline points="3 12 7 12 10 4 14 20 17 12 21 12"/>',
    zoom:          '<circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="21" y2="21"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/>',
    user:          '<circle cx="12" cy="8" r="4"/><path d="M4 21 a8 8 0 0 1 16 0"/>',
    lock:          '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11 V7 a4 4 0 0 1 8 0 V11"/>',
    shield:        '<path d="M12 3 L4 6 V12 a9 9 0 0 0 8 9 a9 9 0 0 0 8-9 V6 Z"/>',
    log_out:       '<path d="M15 17 L20 12 L15 7"/><line x1="20" y1="12" x2="9" y2="12"/><path d="M9 21 H5 a2 2 0 0 1-2-2 V5 a2 2 0 0 1 2-2 h4"/>',
    info:          '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12" y2="8.01" stroke-width="2.4"/>',
    eye:           '<path d="M2 12 s4-7 10-7 10 7 10 7 -4 7-10 7 S2 12 2 12 z"/><circle cx="12" cy="12" r="3"/>',
    edit:          '<path d="M12 20 H21"/><path d="M16.5 3.5 a2.1 2.1 0 0 1 3 3 L7 19 L3 20 L4 16 Z"/>',
    mail:          '<rect x="3" y="5" width="18" height="14" rx="2"/><polyline points="3 7 12 13 21 7"/>',
    trash:         '<polyline points="3 6 21 6"/><path d="M19 6 V20 a2 2 0 0 1-2 2 H7 a2 2 0 0 1-2-2 V6"/><path d="M8 6 V4 a2 2 0 0 1 2-2 h4 a2 2 0 0 1 2 2 V6"/>',
  };

  /**
   * Return an SVG icon as an HTML string.
   * @param {string} name  Icon key (see PATHS).
   * @param {number} [size=16]  Width/height in px.
   * @param {object} [attrs]  Extra attrs on the <svg>, e.g. {class: 'foo'}.
   * @returns {string}
   */
  function icon(name, size, attrs) {
    var p = PATHS[name];
    if (!p) return '';
    size = size || 16;
    var extra = '';
    if (attrs) {
      for (var k in attrs) if (Object.prototype.hasOwnProperty.call(attrs, k)) {
        extra += ' ' + k + '="' + String(attrs[k]).replace(/"/g, '&quot;') + '"';
      }
    }
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"' + extra + '>' + p + '</svg>';
  }

  /**
   * PingWatch radar mark (used by brand wordmark). Sweep animates via CSS.
   * @param {number} [size=28]
   * @returns {string}
   */
  function brandMark(size) {
    size = size || 28;
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 20 20" fill="none" aria-hidden="true">'
      + '<defs><radialGradient id="bm-glow" cx=".5" cy=".5" r=".5">'
      +   '<stop offset="0" stop-color="currentColor" stop-opacity=".6"/>'
      +   '<stop offset="1" stop-color="currentColor" stop-opacity="0"/>'
      + '</radialGradient></defs>'
      + '<circle cx="10" cy="10" r="9" fill="url(#bm-glow)" opacity=".4"/>'
      + '<circle cx="10" cy="10" r="8.5" stroke="currentColor" stroke-width="1" opacity=".30"/>'
      + '<circle cx="10" cy="10" r="5"   stroke="currentColor" stroke-width="1" opacity=".55"/>'
      + '<circle cx="10" cy="10" r="2"   fill="currentColor"/>'
      + '<g class="radar-sweep" opacity=".75">'
      +   '<path d="M10 10 L18.5 10 A 8.5 8.5 0 0 1 14.5 17 Z" fill="currentColor" opacity=".18"/>'
      +   '<line x1="10" y1="10" x2="18.5" y2="10" stroke="currentColor" stroke-width="1.2"/>'
      + '</g>'
      + '<line x1="1.5" y1="10" x2="4"    y2="10" stroke="currentColor" stroke-width="1.2" opacity=".4"/>'
      + '<line x1="10"  y1="1.5" x2="10" y2="4"   stroke="currentColor" stroke-width="1.2" opacity=".4"/>'
      + '<line x1="10"  y1="16"  x2="10" y2="18.5" stroke="currentColor" stroke-width="1.2" opacity=".4"/>'
      + '</svg>';
  }

  global.icon = icon;
  global.brandMark = brandMark;
  global.PW_ICON_NAMES = Object.keys(PATHS);

  // ── Shell init — populate rail + topbar icons after DOM is ready ──
  // Called once on DOMContentLoaded; safe to call again (idempotent).
  function _pwShellInit() {
    var rail = [
      ['tabDashboard','dashboard'], ['tabDevices','devices'], ['tabEvents','events'],
      ['tabAlerting','alerts'],
      ['tabLiveMap','livemap'],      ['tabMap','map'],          ['tabBackups','backups'],
      ['tabIpam','ipam'],            ['tabReports','reports'],  ['tabProbes','probes'],
      ['tabLogs','logs'],
      ['railSettings','settings'],
    ];
    for (var i = 0; i < rail.length; i++) {
      var el = document.getElementById(rail[i][0]);
      if (!el) continue;
      var ic = el.querySelector('.rail-ico');
      if (ic) ic.innerHTML = icon(rail[i][1], 18);
    }
    // Topbar — search hint, theme toggle, bell
    var ci = document.getElementById('tbCmdIco');
    if (ci) ci.innerHTML = icon('search', 14);
    _pwUpdateThemeBtn();
    var tb = document.getElementById('tbBellBtn');
    if (tb && !tb.firstChild) tb.innerHTML = icon('bell', 16);
    // User menu items — populate icons via data-icon attribute
    var items = document.querySelectorAll('.usr-dd-item[data-icon]');
    for (var j = 0; j < items.length; j++) {
      var slot = items[j].querySelector('.usr-dd-ico');
      if (slot && !slot.firstChild) slot.innerHTML = icon(items[j].dataset.icon, 14);
    }
  }

  /** Sync the topbar theme button glyph to the current data-theme. */
  function _pwUpdateThemeBtn() {
    var btn = document.getElementById('tbThemeBtn');
    if (!btn) return;
    var t = document.documentElement.getAttribute('data-theme');
    btn.innerHTML = icon(t === 'light' ? 'moon' : 'sun', 16);
    btn.setAttribute('aria-label', t === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
  }

  // React to theme changes (theme.js dispatches 'themechange')
  global.addEventListener('themechange', _pwUpdateThemeBtn);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _pwShellInit);
  } else {
    _pwShellInit();
  }
  global._pwShellInit = _pwShellInit;
  global._pwUpdateThemeBtn = _pwUpdateThemeBtn;
})(window);
