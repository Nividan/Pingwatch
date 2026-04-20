// ═══════════════════════════ LOGS VIEW ═══════════════════════════
// Top-level log viewer (admin-only). Streams: app / sensors / audit / backup.
// Professional features: live tail (3s polling), smart follow / scroll-lock,
// minimum-level filter, time range, text search, word-wrap, keyboard shortcuts,
// persisted preferences (localStorage), file size + counts in status bar.

// ── state ────────────────────────────────────────────────────────────────────
let _lvBooted    = false;
let _lvStream    = 'app';
let _lvFilter    = { timeRange: '6h', minLevel: '', search: '', customFrom: '', customTo: '' };
let _lvLive      = false;
let _lvTimer     = null;
let _lvFollow    = true;        // stick to bottom as new lines arrive
let _lvWrap      = true;
let _lvSearchRe  = '';
let _lvKeysBound = false;
let _lvLastSeenTotal = 0;       // for the "+N new" counter since page open
let _lvInitialTotal  = null;

// Fetch generation: increment on every issued fetch; drop late responses.
let _lvFetchGen  = 0;

// ── localStorage persistence (best effort) ───────────────────────────────────
function _lvPrefsLoad() {
  try {
    const raw = localStorage.getItem('pw_logs_prefs');
    if (!raw) return;
    const p = JSON.parse(raw);
    if (p.stream)   _lvStream = p.stream;
    if (p.filter)   _lvFilter = { ..._lvFilter, ...p.filter };
    if (typeof p.wrap === 'boolean')   _lvWrap = p.wrap;
    if (typeof p.follow === 'boolean') _lvFollow = p.follow;
  } catch(e) {}
}
function _lvPrefsSave() {
  try {
    localStorage.setItem('pw_logs_prefs', JSON.stringify({
      stream: _lvStream, filter: _lvFilter, wrap: _lvWrap, follow: _lvFollow,
    }));
  } catch(e) {}
}

// ── init ─────────────────────────────────────────────────────────────────────
function _logsInit() {
  if ((S.role || 'viewer') !== 'admin') {
    const root = document.getElementById('logsView');
    if (root) root.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text3);font-size:13px">Logs are admin-only. Your account has read-only access.</div>`;
    return;
  }
  const root = document.getElementById('logsView');
  if (!root) return;
  _lvPrefsLoad();
  if (!_lvBooted) {
    root.innerHTML = `
      <div class="lv-toolbar">
        <div class="lv-streams">
          <button class="lv-stream" data-stream="app"     onclick="_lvSwitchStream('app')">Application</button>
          <button class="lv-stream" data-stream="sensors" onclick="_lvSwitchStream('sensors')">Sensors</button>
          <button class="lv-stream" data-stream="audit"   onclick="_lvSwitchStream('audit')">Audit</button>
          <button class="lv-stream" data-stream="backup"  onclick="_lvSwitchStream('backup')">Backup</button>
        </div>
        <div class="lv-spacer"></div>
        <button class="btn-s lv-live" id="lvLiveBtn" onclick="_lvToggleLive()" title="Toggle live tail (l)">⊙ Live</button>
        <button class="btn-s" onclick="_lvFetch(true)" title="Refresh (r)">↻ Refresh</button>
      </div>
      <div class="lv-filters">
        <select id="lvFTime" onchange="_lvOnFilter()">
          <option value="all">All time</option>
          <option value="5m">Last 5 min</option>
          <option value="15m">Last 15 min</option>
          <option value="1h">Last 1 hour</option>
          <option value="3h">Last 3 hours</option>
          <option value="6h">Last 6 hours</option>
          <option value="12h">Last 12 hours</option>
          <option value="24h">Last 24 hours</option>
          <option value="custom">Custom range…</option>
        </select>
        <div id="lvFCustom" style="display:none;align-items:center;gap:6px;flex-wrap:wrap">
          <input type="datetime-local" id="lvFCustomFrom" onchange="_lvOnFilter()">
          <span style="font-size:11px;color:var(--text3)">to</span>
          <input type="datetime-local" id="lvFCustomTo" onchange="_lvOnFilter()">
        </div>
        <select id="lvFLevel" onchange="_lvOnFilter()" title="Minimum level to show">
          <option value="">All Levels</option>
          <option value="DEBUG">DEBUG+</option>
          <option value="INFO">INFO+</option>
          <option value="WARNING">WARNING+</option>
          <option value="ERROR">ERROR+</option>
          <option value="CRITICAL">CRITICAL only</option>
        </select>
        <input id="lvFSearch" type="search" placeholder="Search (e.g. timeout, FortiGate)… /" oninput="_lvOnFilter()" class="lv-search">
        <button class="lv-iconbtn" onclick="_lvClearFilters()" title="Clear filters (Esc)">✕</button>
        <div class="lv-spacer"></div>
        <button class="lv-iconbtn" id="lvWrapBtn" onclick="_lvToggleWrap()" title="Toggle word wrap (w)">⤶ Wrap</button>
        <button class="lv-iconbtn" onclick="_lvCopy()" title="Copy visible log lines">⎘ Copy</button>
        <button class="lv-iconbtn" onclick="_lvExport('csv')" title="Export as CSV">⬇ CSV</button>
        <button class="lv-iconbtn" onclick="_lvExport('json')" title="Export as JSON">⬇ JSON</button>
      </div>
      <div class="lv-status" id="lvStatus">—</div>
      <div class="lv-body-wrap">
        <div id="lvBody" class="lv-body"></div>
        <button class="lv-jump" id="lvJump" onclick="_lvJumpToLive()">⤓ Jump to live</button>
      </div>
    `;
    _lvBooted = true;
    _lvBindScrollFollow();
    _lvBindKeys();
  }
  // Restore UI controls from state
  const el = id => document.getElementById(id);
  el('lvFTime').value   = _lvFilter.timeRange || '6h';
  el('lvFLevel').value  = _lvFilter.minLevel || '';
  el('lvFSearch').value = _lvFilter.search  || '';
  el('lvFCustomFrom').value = _lvFilter.customFrom || '';
  el('lvFCustomTo').value   = _lvFilter.customTo   || '';
  el('lvFCustom').style.display = _lvFilter.timeRange === 'custom' ? 'flex' : 'none';
  _lvApplyWrapUI();
  _lvUpdateStreamBtns();
  _lvInitialTotal = null;
  _lvFetch(true);
}

function _logsDeactivate() {
  // Called when switching away from the Logs tab. Stop timers to save resources.
  _lvStopLive();
}

// ── stream switching ────────────────────────────────────────────────────────
function _lvSwitchStream(s) {
  if (s === _lvStream) return;
  _lvStream = s;
  _lvInitialTotal = null;
  _lvPrefsSave();
  _lvUpdateStreamBtns();
  _lvFetch(true);
}
function _lvUpdateStreamBtns() {
  document.querySelectorAll('.lv-stream').forEach(b => {
    b.classList.toggle('active', b.dataset.stream === _lvStream);
  });
}

// ── live / follow ────────────────────────────────────────────────────────────
function _lvToggleLive() {
  _lvLive = !_lvLive;
  const btn = document.getElementById('lvLiveBtn');
  if (btn) { btn.classList.toggle('on', _lvLive); btn.textContent = _lvLive ? '● Live' : '⊙ Live'; }
  if (_lvLive) {
    _lvFetch(false);
    _lvTimer = setInterval(() => {
      if (!document.getElementById('lvBody')) { _lvStopLive(); return; }
      _lvFetch(false);
    }, 3000);
  } else {
    _lvStopLive(false);
  }
}
function _lvStopLive(resetBtn = true) {
  _lvLive = false;
  if (_lvTimer) { clearInterval(_lvTimer); _lvTimer = null; }
  if (resetBtn) {
    const btn = document.getElementById('lvLiveBtn');
    if (btn) { btn.classList.remove('on'); btn.textContent = '⊙ Live'; }
  }
}

function _lvBindScrollFollow() {
  const body = document.getElementById('lvBody');
  if (!body) return;
  body.addEventListener('scroll', () => {
    const nearBottom = (body.scrollHeight - body.scrollTop - body.clientHeight) < 20;
    if (_lvFollow !== nearBottom) {
      _lvFollow = nearBottom;
      _lvPrefsSave();
      const jump = document.getElementById('lvJump');
      if (jump) jump.classList.toggle('show', !nearBottom);
    }
  });
}
function _lvJumpToLive() {
  const body = document.getElementById('lvBody');
  if (!body) return;
  body.scrollTop = body.scrollHeight;
  _lvFollow = true;
  const jump = document.getElementById('lvJump');
  if (jump) jump.classList.remove('show');
}

// ── filters ──────────────────────────────────────────────────────────────────
function _lvOnFilter() {
  _lvFilter.timeRange  = document.getElementById('lvFTime').value;
  _lvFilter.minLevel   = document.getElementById('lvFLevel').value;
  _lvFilter.search     = document.getElementById('lvFSearch').value;
  _lvFilter.customFrom = document.getElementById('lvFCustomFrom').value;
  _lvFilter.customTo   = document.getElementById('lvFCustomTo').value;
  document.getElementById('lvFCustom').style.display = _lvFilter.timeRange === 'custom' ? 'flex' : 'none';
  _lvPrefsSave();
  _lvFetch(true);
}
function _lvClearFilters() {
  _lvFilter = { timeRange: '6h', minLevel: '', search: '', customFrom: '', customTo: '' };
  document.getElementById('lvFTime').value   = '6h';
  document.getElementById('lvFLevel').value  = '';
  document.getElementById('lvFSearch').value = '';
  document.getElementById('lvFCustomFrom').value = '';
  document.getElementById('lvFCustomTo').value   = '';
  document.getElementById('lvFCustom').style.display = 'none';
  _lvPrefsSave();
  _lvFetch(true);
}

// ── wrap / display ───────────────────────────────────────────────────────────
function _lvToggleWrap() {
  _lvWrap = !_lvWrap;
  _lvPrefsSave();
  _lvApplyWrapUI();
}
function _lvApplyWrapUI() {
  const body = document.getElementById('lvBody');
  const btn  = document.getElementById('lvWrapBtn');
  if (body) body.classList.toggle('nowrap', !_lvWrap);
  if (btn)  btn.classList.toggle('active', _lvWrap);
}

// ── fetch + render ───────────────────────────────────────────────────────────
function _lvBuildQuery() {
  const q = new URLSearchParams();
  if (_lvFilter.minLevel) q.set('min_level', _lvFilter.minLevel);
  if (_lvFilter.search)   q.set('search',    _lvFilter.search);

  // Time range → after / before
  const now = new Date();
  let after = '', before = '';
  const tr = _lvFilter.timeRange;
  if (tr === 'custom') {
    after  = _lvFilter.customFrom ? _lvLocalToIso(_lvFilter.customFrom) : '';
    before = _lvFilter.customTo   ? _lvLocalToIso(_lvFilter.customTo)   : '';
  } else if (tr && tr !== 'all') {
    const mins = { '5m':5, '15m':15, '1h':60, '3h':180, '6h':360, '12h':720, '24h':1440 }[tr];
    if (mins) {
      const t = new Date(now.getTime() - mins * 60000);
      after = _lvDtToLogTs(t);
    }
  }
  if (after)  q.set('after',  after);
  if (before) q.set('before', before);
  q.set('limit', '5000');
  return q.toString();
}
function _lvDtToLogTs(d) {
  // Logs are written in local server time as "YYYY-MM-DD HH:MM:SS".
  // Our filter compares strings, so format the same way using local time.
  const p = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function _lvLocalToIso(dtLocal) {
  // datetime-local yields "YYYY-MM-DDTHH:MM" — convert to log-file format.
  return dtLocal.replace('T', ' ') + ':00';
}

async function _lvFetch(resetScroll) {
  const body = document.getElementById('lvBody');
  const status = document.getElementById('lvStatus');
  if (!body) return;
  const gen = ++_lvFetchGen;
  const q   = _lvBuildQuery();
  let r;
  try {
    r = await api('GET', `/api/logs/${_lvStream}?${q}`);
  } catch (e) {
    if (gen !== _lvFetchGen) return;
    body.innerHTML = `<div class="lv-empty" style="color:var(--err)">Failed to load logs: ${esc(e.message||e)}</div>`;
    if (status) status.textContent = '—';
    return;
  }
  if (gen !== _lvFetchGen) return;  // superseded

  if (_lvInitialTotal === null) _lvInitialTotal = r.total ?? 0;
  _lvLastSeenTotal = r.total ?? 0;
  const newSinceOpen = Math.max(0, _lvLastSeenTotal - _lvInitialTotal);

  // Render lines
  const lines = (r.lines || '').split('\n');
  _lvSearchRe = _lvFilter.search ? _lvBuildSearchRegex(_lvFilter.search) : null;
  body.innerHTML = lines.length && lines[0] !== ''
    ? lines.map(_lvRenderLine).join('')
    : `<div class="lv-empty">No log lines match the current filters.</div>`;

  // Status bar
  if (status) {
    const size = _lvFmtBytes(r.file_size || 0);
    const rot  = r.rotated_count ? ` · ${r.rotated_count} rotated` : '';
    const newMark = newSinceOpen > 0 ? ` · <span style="color:var(--accent)">+${newSinceOpen} new since open</span>` : '';
    status.innerHTML = `Showing <b>${r.shown}</b> of <b>${r.filtered}</b> filtered (<b>${r.total}</b> total) · ${esc(size)}${esc(rot)}${newMark}`;
  }

  // Scroll: follow mode → stick to bottom; explicit reset → jump to bottom
  if (resetScroll || _lvFollow) {
    body.scrollTop = body.scrollHeight;
    _lvFollow = true;
    const jump = document.getElementById('lvJump');
    if (jump) jump.classList.remove('show');
  }
}

function _lvBuildSearchRegex(q) {
  try {
    return new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  } catch (e) { return null; }
}

const _LV_LINE_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(INFO|WARNING|WARN|ERROR|CRITICAL|DEBUG)\s+(.*)$/;
function _lvRenderLine(line) {
  const m = _LV_LINE_RE.exec(line);
  if (!m) {
    // Continuation line
    const text = esc(line);
    const hl = _lvSearchRe ? text.replace(_lvSearchRe, '<mark>$1</mark>') : text;
    return `<div class="lv-line lv-cont">${hl || '&nbsp;'}</div>`;
  }
  const ts = m[1], lvl = m[2], msg = m[3];
  const cls = `lv-${lvl.toLowerCase()}`;
  const msgEsc = esc(msg);
  const msgHl  = _lvSearchRe ? msgEsc.replace(_lvSearchRe, '<mark>$1</mark>') : msgEsc;
  return `<div class="lv-line ${cls}"><span class="lv-ts">${esc(ts)}</span><span class="lv-lvl">${lvl}</span><span class="lv-msg">${msgHl}</span></div>`;
}

function _lvFmtBytes(n) {
  if (!n) return '0 B';
  const u = ['B','KB','MB','GB','TB'];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length-1) { v /= 1024; i++; }
  return `${v < 10 ? v.toFixed(2) : v.toFixed(1)} ${u[i]}`;
}

// ── copy / export ────────────────────────────────────────────────────────────
function _lvCopy() {
  const body = document.getElementById('lvBody');
  if (!body) return;
  const txt = body.innerText;
  if (!txt) { toast('Nothing to copy', 'err'); return; }
  navigator.clipboard.writeText(txt).then(
    () => toast('Copied log lines to clipboard', 'ok'),
    () => toast('Copy failed', 'err'),
  );
}

async function _lvExport(fmt) {
  const body = document.getElementById('lvBody');
  if (!body) return;
  const rows = [];
  body.querySelectorAll('.lv-line').forEach(el => {
    const ts  = el.querySelector('.lv-ts')?.textContent  || '';
    const lvl = el.querySelector('.lv-lvl')?.textContent || '';
    const msg = el.querySelector('.lv-msg')?.innerText   || el.innerText;
    rows.push({ timestamp: ts, level: lvl, message: msg });
  });
  if (!rows.length) { toast('Nothing to export', 'err'); return; }
  const stamp = new Date().toISOString().replace(/[:.]/g,'-').slice(0,19);
  let blob, name;
  if (fmt === 'csv') {
    const esc = s => /[",\n]/.test(s) ? `"${s.replace(/"/g,'""')}"` : s;
    const header = 'timestamp,level,message\n';
    const body_  = rows.map(r => [r.timestamp, r.level, r.message].map(esc).join(',')).join('\n');
    blob = new Blob([header + body_], { type: 'text/csv' });
    name = `pingwatch-${_lvStream}-${stamp}.csv`;
  } else {
    blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' });
    name = `pingwatch-${_lvStream}-${stamp}.json`;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── keyboard shortcuts ───────────────────────────────────────────────────────
function _lvBindKeys() {
  if (_lvKeysBound) return;
  _lvKeysBound = true;
  document.addEventListener('keydown', (e) => {
    // Only active when Logs tab is visible and no modal / input is focused.
    const logsView = document.getElementById('logsView');
    if (!logsView || logsView.style.display === 'none') return;
    if (document.querySelector('.mo')) return;  // modal is open
    const t = e.target;
    const isInput = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (isInput && t.id !== 'lvFSearch') return;

    if (e.key === '/') {
      if (isInput) return;
      e.preventDefault();
      document.getElementById('lvFSearch')?.focus();
    } else if (e.key === 'Escape') {
      if (t.id === 'lvFSearch') {
        _lvClearFilters();
        t.blur();
      }
    } else if ((e.key === 'l' || e.key === 'L') && !isInput) {
      e.preventDefault(); _lvToggleLive();
    } else if ((e.key === 'r' || e.key === 'R') && !isInput) {
      e.preventDefault(); _lvFetch(true);
    } else if ((e.key === 'w' || e.key === 'W') && !isInput) {
      e.preventDefault(); _lvToggleWrap();
    } else if (e.key === 'End' && !isInput) {
      e.preventDefault(); _lvJumpToLive();
    }
  });
}
