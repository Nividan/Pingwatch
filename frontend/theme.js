// ── Theme (Dark ⇄ Light) ─────────────────────────────────────────
// Single source of truth for theme switching.
//
// The sync-apply bootstrap runs from an inline <script> in <head>
// (see index.html) to prevent FOUC. This module provides the
// long-lived API used after the page is interactive.
//
// Storage model (hybrid):
//   • localStorage['pw_theme'] — instant, per-browser
//   • users.theme_preference    — authoritative, synced via /api/me
//
// Flow:
//   1. Bootstrap reads localStorage → sets <html data-theme>.
//   2. On login / /api/me, app.js calls setTheme(server_value) to
//      reconcile (server wins, then overwrites localStorage).
//   3. User clicks "Theme" menu → toggleTheme() → setTheme(other).
//      setTheme fires PATCH /api/me/theme in background + posts
//      message to the map iframe + dispatches 'themechange'.

(function (w) {
  'use strict';

  const STORAGE_KEY  = 'pw_theme';
  const VALID        = ['dark', 'light'];
  const DEFAULT      = 'dark';

  function _read() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return VALID.includes(v) ? v : DEFAULT;
    } catch (_) { return DEFAULT; }
  }

  function _write(v) {
    try { localStorage.setItem(STORAGE_KEY, v); } catch (_) { /* quota/private */ }
  }

  /** Current theme, always one of VALID. */
  function getTheme() {
    const attr = document.documentElement.getAttribute('data-theme');
    return VALID.includes(attr) ? attr : _read();
  }

  /** Apply a theme. Performs side-effects (storage, iframe, event, server). */
  function setTheme(next, opts) {
    opts = opts || {};
    if (!VALID.includes(next)) next = DEFAULT;

    const prev = getTheme();
    document.documentElement.setAttribute('data-theme', next);
    _write(next);

    // Notify the topology-map iframe (same origin, optional listener)
    try {
      const f = document.getElementById('map-frame');
      if (f && f.contentWindow) {
        f.contentWindow.postMessage({ type: 'theme', value: next }, '*');
      }
    } catch (_) {}

    // Broadcast for canvas drawers that cache colors
    try {
      w.dispatchEvent(new CustomEvent('themechange', { detail: next }));
    } catch (_) {}

    // Update any dynamic UI that the CSS can't reach
    _updateMenuLabel(next);

    // Background sync to server — skip when we're just mirroring the
    // server's own value (opts.sync === false) to avoid echo.
    if (prev !== next && opts.sync !== false) {
      try {
        fetch('/api/me/theme', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ theme: next }),
          credentials: 'same-origin',
        }).catch(() => { /* ignore — localStorage still wins */ });
      } catch (_) {}
    }
  }

  /** Flip to the other theme. */
  function toggleTheme() {
    setTheme(getTheme() === 'dark' ? 'light' : 'dark');
  }

  /** Read a CSS custom property from :root (for canvas code). */
  function getCssVar(name) {
    try {
      return getComputedStyle(document.documentElement)
              .getPropertyValue(name).trim();
    } catch (_) { return ''; }
  }

  /**
   * Read a CSS custom property and parse it as an RGB tuple [r,g,b].
   * Accepts #rgb / #rrggbb / rgb(...) / rgba(...). Returns null on failure.
   * Intended for canvas code that needs `rgba(${rgb.join(',')},${alpha})`.
   */
  function getCssRgb(name) {
    const raw = getCssVar(name);
    if (!raw) return null;
    const s = raw.trim();
    // #rgb / #rrggbb
    if (s.charAt(0) === '#') {
      let h = s.slice(1);
      if (h.length === 3) h = h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
      if (h.length !== 6) return null;
      const n = parseInt(h, 16);
      if (isNaN(n)) return null;
      return [(n>>16)&255, (n>>8)&255, n&255];
    }
    // rgb(...) / rgba(...)
    const m = s.match(/rgba?\s*\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i);
    if (m) return [+m[1], +m[2], +m[3]];
    return null;
  }

  /** Refresh the Theme button label + icon based on current state. */
  function _updateMenuLabel(theme) {
    const btn = document.getElementById('usrThemeBtn');
    if (!btn) return;
    // Label shows the theme you'll switch TO, with an icon for the target.
    if (theme === 'dark') {
      btn.textContent = '☀ Switch to Light';
      btn.setAttribute('aria-label', 'Switch to light theme');
    } else {
      btn.textContent = '🌙 Switch to Dark';
      btn.setAttribute('aria-label', 'Switch to dark theme');
    }
  }

  // Re-sync the label whenever the menu opens (in case some other tab changed it)
  document.addEventListener('DOMContentLoaded', () => _updateMenuLabel(getTheme()));

  // Public API
  w.getTheme    = getTheme;
  w.setTheme    = setTheme;
  w.toggleTheme = toggleTheme;
  w.getCssVar   = getCssVar;
  w.getCssRgb   = getCssRgb;
})(window);
