// ── Footer (the Operator's spec: Version, Server, Runtime, Token usage) ──
// THE OFFLINE STATE (the Operator's 08-12 spec): when the server stops,
// the footer shows Server: Offline / Runtime: Offline IMMEDIATELY. After
// 5s of no response the frosted disconnect overlay appears. It RETRIES
// every 10s (max 6 attempts = 60s total) — then FORCE-RELOADS the page
// (the 08-15 shutdown overlay spec: after all 6 retries the page
// reloads, which re-fetches the server — a fresh boot or a 404).
let _offlineSince = null;   // ms timestamp when the server went dark
let _disconnectShown = false;
let _retryCount = 0;        // reconnect attempts made (max 6)
let _retryTimer = null;
const _MAX_RETRIES = 6;     // THE 08-15 SHUTDOWN LADDER: 6 tries × 10s
const _RETRY_MS = 10000;    // = 60 seconds total, then a forced reload

function _showDisconnectError() {
  // THE FORCE-RELOAD (the 08-15 spec): after 6 failed retries, the page
  // RELOADS itself — the server is either back (fresh boot → healthy
  // page) or still down (the reload lands on a 404/refused page). The
  // Reload button is gone — the reload is automatic.
  const title = $('disconnect-title');
  const sub = $('disconnect-sub');
  const status = $('disconnect-status');
  const reload = $('disconnect-reload');
  const barWrap = $('disconnect-bar-wrap');
  if (title) title.textContent = 'Connection Lost — Reloading…';
  if (sub) sub.textContent = 'Server: Offline · Runtime: Offline · Error 404';
  if (status) status.textContent = '6 attempts failed (60s) — reloading the page now.';
  if (barWrap) barWrap.classList.add('hidden');
  if (reload) reload.classList.add('hidden');
  if (_retryTimer) { clearInterval(_retryTimer); _retryTimer = null; }
  // THE AUTO-RELOAD (the 08-15 spec): a short beat so the status text
  // renders, then the page reloads.
  setTimeout(() => { location.reload(); }, 800);
}

function _beginDisconnectRetries() {
  // THE RECONNECT LADDER (the 08-15 spec): one health probe every 10s,
  // max 6 attempts. Success clears the overlay; 6 failures → reload.
  const status = $('disconnect-status');
  const barWrap = $('disconnect-bar-wrap');
  const bar = $('disconnect-bar');
  if (barWrap) barWrap.classList.remove('hidden');
  const tryOnce = async () => {
    if (_retryCount >= _MAX_RETRIES) { _showDisconnectError(); return; }
    _retryCount++;
    if (status) status.textContent = 'Reconnecting… (attempt ' + _retryCount + '/' + _MAX_RETRIES + ')';
    if (bar) bar.style.width = Math.round(_retryCount / _MAX_RETRIES * 100) + '%';
    try {
      const r = await fetch('/health', {cache: 'no-store'});
      if (r.ok) {
        setOffline(false);   // recovered — clears everything
        return;
      }
    } catch (e) { /* still dark */ }
    if (_retryCount >= _MAX_RETRIES) { _showDisconnectError(); }
  };
  tryOnce();
  _retryTimer = setInterval(tryOnce, _RETRY_MS);
}

function setOffline(offline) {
  if (!offline) {
    // The server is back — clear the offline state + overlay + retries.
    _offlineSince = null;
    _disconnectShown = false;
    _retryCount = 0;
    if (_retryTimer) { clearInterval(_retryTimer); _retryTimer = null; }
    const ov = $('disconnect-overlay');
    if (ov) ov.classList.add('hidden');
    const reload = $('disconnect-reload');
    if (reload) reload.classList.add('hidden');
    const barWrap = $('disconnect-bar-wrap');
    if (barWrap) barWrap.classList.add('hidden');
    return;
  }
  const now = Date.now();
  if (_offlineSince === null) _offlineSince = now;
  // Status flips to Offline the moment the fetch fails.
  $('foot-server').textContent = 'Offline';
  $('foot-runtime').textContent = 'Offline';
  // After 5 seconds of darkness, the frosted disconnect overlay appears
  // + the reconnect ladder starts.
  if (!_disconnectShown && now - _offlineSince >= 5000) {
    _disconnectShown = true;
    const ov = $('disconnect-overlay');
    if (ov) ov.classList.remove('hidden');
    _beginDisconnectRetries();
  }
}

// The Reload button: a fresh page load (the server is either back or
// the page must be re-fetched to confirm the outage).
document.addEventListener('DOMContentLoaded', () => {
  const reload = $('disconnect-reload');
  if (reload) reload.addEventListener('click', () => location.reload());
});

async function loadFooter() {
  try {
    // The version — the single source (core.config.VERSION) via /version.
    try {
      const vr = await fetch('/version');
      const vd = await vr.json();
      $('foot-version').textContent = vd.version || '—';
    } catch (e) { $('foot-version').textContent = '—'; }
    const res = await fetch('/health');
    if (!res.ok) { setOffline(true); return; }
    const d = await res.json();
    setOffline(false);
    // Server + Runtime status.
    const srv = d.server || {};
    const runtime = (srv.running !== undefined)
      ? (srv.running ? 'running' : 'idle')
      : (d.ok ? 'online' : 'degraded');
    $('foot-server').textContent = d.ok ? 'online' : 'degraded';
    $('foot-runtime').textContent = runtime;
    // The LOADED COUNTS (the Operator's 08-12 footer scheme): exactly
    // what loaded at startup — plugins, tools, skills — in 3-DIGIT
    // format (000, 001, 010, 100) for the quick-numbers read.
    const loaded = d.loaded || {};
    const pad3 = n => String(n == null ? 0 : n).padStart(3, '0');
    $('foot-plugins').textContent = pad3(loaded.plugins);
    $('foot-tools').textContent = pad3(loaded.tools);
    $('foot-skills').textContent = pad3(loaded.skills);
    // Token usage: current / available + the progress bar. The Operator's
    // spec: the actual % shows next to EACH amount.
    const tk = d.tokens || {used: 0, available: 0, percent: 0};
    const fmt = n => n >= 1e6 ? (n/1e6).toFixed(2) + 'M'
               : n >= 1e3 ? (n/1e3).toFixed(1) + 'k' : String(n);
    const pct = Math.min(999, tk.percent || 0);
    $('foot-tokens-used').textContent = fmt(tk.used);
    $('foot-tokens-pct').textContent = '(' + pct.toFixed(1) + '%)';
    $('foot-tokens-avail').textContent = fmt(tk.available);
    $('foot-tokens-avail-pct').textContent = '(' + (100 - pct).toFixed(1) + '%)';
    $('foot-tokens-bar').style.width = Math.min(100, pct) + '%';
  } catch (e) { setOffline(true); }
}

// ── THE TERMINAL (the Operator's spec): the raw shell — command input with
//    HISTORY (up-arrow recall). Output streams below. ────────────────
var TERM_HISTORY = [];
var TERM_HIST_IDX = -1;
var TERM_PENDING = null;
// THE LIVE LOG REFRESH (the Operator's 08-12 spec): the Terminal and
// Console poll every 3s while their panel is OPEN so new log entries
// appear dynamically (the "terminal doesn't update" complaint). The
// first load appends everything; later polls append only NEW lines.
var TERM_LAST_COUNT = 0;
var CONSOLE_LAST_COUNT = 0;

function openTerminal() {
  const p = $('term-panel');
  p.classList.remove('hidden');
  const out = $('term-output');
  if (!out.childNodes.length) {
    const boot = document.createElement('div');
    boot.className = 'term-line term-dim';
    boot.textContent = 'Athena Terminal — the raw shell. Type a system command (Enter to run, ↑/↓ for history).';
    out.appendChild(boot);
    // THE METRIC LOG FEED (the Operator's 08-12 spec): the Terminal is
    // the DEVELOPER TERMINAL — it shows the ROOT AGGREGATE (scope=all),
    // the appended version of ALL profiles' logs in ONE stream. The
    // Console (separate button) shows only the current profile.
    TERM_LAST_COUNT = 0;
    loadTerminalLog(out, /*appendOnly=*/false);
  }
  // THE LIVE TICK: while the terminal is open, poll for new log lines.
  if (!p._termTimer) {
    p._termTimer = setInterval(() => {
      if (p.classList.contains('hidden')) return;
      loadTerminalLog($('term-output'), /*appendOnly=*/true);
    }, 3000);
  }
  $('term-input').focus();
  refreshConsoleButton();
}

async function loadTerminalLog(out, appendOnly) {
  try {
    const res = await fetch('/logs?scope=all');
    const d = await res.json();
    const text = d.log || '';
    if (!text) return;
    const lines = text.split('\n').filter(Boolean);
    const start = appendOnly ? TERM_LAST_COUNT : Math.max(0, lines.length - 200);
    TERM_LAST_COUNT = lines.length;
    for (let i = start; i < lines.length; i++) {
      const raw = lines[i];
      let e;
      try { e = JSON.parse(raw); } catch (err) { continue; }
      const line = document.createElement('div');
      line.className = 'term-line term-log';
      const when = (e.time || '').slice(0, 19);
      const src = e.source || e.tool || e.status || 'event';
      const code = e.code ? ' · ' + e.code : '';
      const reason = e.reason ? ' (' + e.reason + ')' : '';
      line.textContent = '[' + when + '] L' + (e.level || 3) + ' ' + src +
                         code + reason + ' — ' + (e.result || e.message || '');
      out.appendChild(line);
    }
    out.scrollTop = out.scrollHeight;
  } catch (e) { /* server mid-boot — the terminal still works */ }
}
function closeOverlay(id) {
  const el = $(id);
  if (el) el.classList.add('hidden');
  if (id === 'term-panel') TERM_PENDING = null;
}
function termPrint(text, cls) {
  const out = $('term-output');
  const line = document.createElement('div');
  line.className = 'term-line' + (cls ? ' ' + cls : '');
  line.textContent = text;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}
async function runTerminal(cmd) {
  termPrint('$ ' + cmd, 'term-cmd');
  try {
    const res = await fetch('/terminal', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({cmd}),
    });
    const d = await res.json();
    if (!d.ok) { termPrint('blocked/error: ' + (d.error || 'unknown'), 'term-err'); return; }
    if (d.stdout) termPrint(d.stdout.replace(/\n$/, ''));
    if (d.stderr) termPrint(d.stderr.replace(/\n$/, ''), 'term-err');
    if (!d.stdout && !d.stderr) termPrint('(exit ' + d.exit + ')', 'term-dim');
  } catch (e) { termPrint('error: ' + e.message, 'term-err'); }
}
document.addEventListener('DOMContentLoaded', () => {
  const btn = $('foot-terminal-btn');
  if (btn) btn.addEventListener('click', openTerminal);
  const input = $('term-input');
  if (input) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && input.value.trim()) {
        const cmd = input.value.trim();
        TERM_HISTORY.push(cmd);
        TERM_HIST_IDX = TERM_HISTORY.length;
        input.value = '';
        runTerminal(cmd);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (TERM_HIST_IDX > 0) {
          TERM_HIST_IDX--;
          input.value = TERM_HISTORY[TERM_HIST_IDX] || '';
        }
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (TERM_HIST_IDX < TERM_HISTORY.length) {
          TERM_HIST_IDX++;
          input.value = TERM_HISTORY[TERM_HIST_IDX] || '';
        }
      }
    });
  }
  document.querySelectorAll('.overlay-close').forEach(b => {
    b.addEventListener('click', () => closeOverlay(b.dataset.close));
  });
  const cbtn = $('foot-console-btn');
  if (cbtn) cbtn.addEventListener('click', openConsole);
});

// ── THE CONSOLE (the Operator's spec): the profile-scoped operator
//    view — SAME STYLE as the Terminal (the raw log look), but only the
//    CURRENT profile's entries, newest first. Live-refreshed. ─
async function openConsole() {
  const p = $('console-panel');
  p.classList.remove('hidden');
  const list = $('console-list');
  if (!list._consoleLoaded) {
    list._consoleLoaded = true;
    list.innerHTML = '<div class="console-loading">loading…</div>';
    CONSOLE_LAST_COUNT = 0;
    await loadConsoleLog(list, /*appendOnly=*/false);
  }
  // THE LIVE TICK: poll the current profile's console while open.
  if (!p._consoleTimer) {
    p._consoleTimer = setInterval(() => {
      if (p.classList.contains('hidden')) return;
      loadConsoleLog($('console-list'), /*appendOnly=*/true);
    }, 3000);
  }
  refreshConsoleButton();
}

async function loadConsoleLog(list, appendOnly) {
  try {
    const res = await fetch('/console?limit=500');
    const d = await res.json();
    const entries = d.entries || [];
    if (!entries.length) {
      if (!appendOnly) list.innerHTML = '<div class="console-loading">no events yet</div>';
      return;
    }
    if (!appendOnly) list.innerHTML = '';
    // APPEND ONLY NEW entries (the console is newest-first; track by
    // count so a refresh doesn't re-render the whole panel).
    const start = appendOnly ? CONSOLE_LAST_COUNT : 0;
    CONSOLE_LAST_COUNT = entries.length;
    const newEntries = appendOnly ? entries.slice(0, Math.max(0, entries.length - start)) : entries;
    const LEVEL_CLS = {1: 'lvl1', 2: 'lvl2', 3: 'lvl3', 4: 'lvl4', 5: 'lvl5'};
    // Render NEWEST → OLDEST at the top (same as the terminal feed
    // format — the raw log line, one per row).
    for (const e of newEntries) {
      const row = document.createElement('div');
      row.className = 'term-line term-log console-as-term';
      const when = (e.time || '').slice(0, 19);
      const src = e.source || e.tool || e.status || 'event';
      const code = e.code ? ' · ' + e.code : '';
      const reason = e.reason ? ' (' + e.reason + ')' : '';
      row.textContent = '[' + when + '] L' + (e.level || 3) + ' ' + src +
                        code + reason + ' — ' + (e.result || e.message || '');
      list.appendChild(row);
    }
    list.scrollTop = list.scrollHeight;
  } catch (e) {
    if (!appendOnly) list.innerHTML = '<div class="console-loading">error: ' + e.message + '</div>';
  }
}
function refreshConsoleButton() {
  const cbtn = $('foot-console-btn');
  if (cbtn && !$('console-panel').classList.contains('hidden')) {
    cbtn.textContent = 'Console (active)';
  } else if (cbtn) {
    cbtn.textContent = 'Console';
  }
}

