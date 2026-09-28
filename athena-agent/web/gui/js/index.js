const $ = (id) => document.getElementById(id);

// ── Navbar switching (the Operator's 2-column spec) ────────────────────────
// PERSISTENCE (the Operator's 08-12 UX directive): the active tab survives a
// reload. The selection is stored in localStorage AND mirrored in the
// URL hash — a refresh keeps you on the page you were viewing.
const TABS = ['home', 'chat', 'call', 'sessions', 'vault',
              'behavior', 'usage', 'settings'];

function currentTab() {
  const hash = (location.hash || '').replace('#', '');
  if (TABS.includes(hash)) return hash;
  return localStorage.getItem('athena-tab') || 'home';
}

function activateTab(ws, { persist = true } = {}) {
  document.querySelectorAll('nav .tab').forEach((t) => {
    t.classList.toggle('active', t.dataset.ws === ws);
  });
  document.querySelectorAll('.workspace').forEach((w) => {
    w.classList.toggle('active', w.id === ws + '-ws');
  });
  if (persist) {
    localStorage.setItem('athena-tab', ws);
    // Mirror into the hash without a history entry (refresh keeps page).
    try { history.replaceState(null, '', '#' + ws); } catch (e) { /* ignore */ }
  }
  // Per-page lazy loaders (only when the page becomes visible).
  if (ws === 'sessions') loadSessions();
  if (ws === 'vault') loadVaultGrid();
  if (ws === 'behavior') loadBehavior();
  if (ws === 'usage') loadUsage();
  if (ws === 'settings') loadSettings();
  // The CALL tiles must be re-measured once the page is visible
  // (hidden at boot → clientWidth=0 → tiny tiles otherwise).
  if (ws === 'call' && typeof onCallShown === 'function') {
    requestAnimationFrame(onCallShown);
  }
}

document.querySelectorAll('nav .tab').forEach((tab) => {
  tab.addEventListener('click', () => activateTab(tab.dataset.ws));
});

// THE HOME QUICK-ACCESS CARDS (the Operator's 08-12 home spec): each
// card navigates to its section, same as clicking the nav tab.
document.querySelectorAll('.home-card').forEach((card) => {
  card.addEventListener('click', () => {
    const go = card.dataset.go;
    if (go) activateTab(go);
  });
});

// THE OPERATOR MESSAGE FIELD (the Operator's 08-12 home spec): typing a
// message on Home + Start creates a NEW session, swaps to the Chat page,
// and sends the message as the first line of that fresh conversation.
async function homeStart() {
  const input = $('home-start-input');
  const text = (input ? input.value : '').trim();
  if (!text) return;
  input.value = '';
  try {
    // 1. Create the new session (the loop points at it server-side).
    const r = await fetch('/sessions/new', {method: 'POST'});
    const d = await r.json();
    if (!d.ok) { alert('could not start: ' + (d.error || 'unknown')); return; }
    // 2. Swap to the Chat page + refresh the session dropdown.
    activateTab('chat');
    if (typeof loadSessionDropdown === 'function') await loadSessionDropdown();
    // 3. Pre-fill + send the message as the conversation opener.
    const chatInput = $('chat-input');
    if (chatInput) chatInput.value = text;
    if (typeof sendChat === 'function') sendChat();
    // Focus the chat input for the follow-up.
    if (chatInput) chatInput.focus();
  } catch (e) {
    alert('could not start conversation: ' + e);
  }
}
$('home-start-btn').addEventListener('click', homeStart);
$('home-start-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') homeStart();
});

// ── THE ACTIVE PROFILE (shared) ──────────────────────────────────────
// The single source: ACTIVE_PROFILE lives in profile.js (the dropdown
// sets it on switch). currentProfile() returns it, falling back to the
// server's reported default. This was LOST in the nav-persistence
// rewrite — sessions/vault/usage all call it, and without it they
// crashed at the first line (the 08-12 blank-grid bug).
function currentProfile() {
  // THE 08-17 PROFILE SOURCE (the Operator's spec): window.ACTIVE_PROFILE
  // (from profile.js) is preferred; if not set (e.g. a direct page load),
  // fall back to the persisted localStorage choice so the page reads the
  // right profile's data even before the dropdown script runs.
  try {
    if (window.ACTIVE_PROFILE) return window.ACTIVE_PROFILE;
  } catch (e) {}
  if (typeof ACTIVE_PROFILE !== 'undefined' && ACTIVE_PROFILE) {
    return ACTIVE_PROFILE;
  }
  try {
    const p = localStorage.getItem('athena_active_profile');
    if (p) return p;
  } catch (e) {}
  return 'default';
}

// ── BOOT: restore the last-viewed page (the Operator's UX fix) ────────────
document.addEventListener('DOMContentLoaded', () => {
  const ws = currentTab();
  activateTab(ws, { persist: false });
});

// ── SHARED helpers (the Operator's spec: index = shared code) ──
async function loadSessionDropdown() {
  try {
    const res = await fetch('/sessions/current');
    const d = await res.json();
    const sel = $('session-select');
    sel.innerHTML = '';
    const cur = d.current || '';
    CHAT_SESSION = cur;
    const prof = d.profile || 'default';
    const list = d.sessions || [];
    const labels = d.labels || {};   // {UUID: Label} — the user's side
    if (!list.includes(cur) && cur) list.unshift(cur);
    // THE 08-17 EMPTY-STATE FIX (the Operator's spec): when the profile has
    // NO sessions, the dropdown says "none" — never an empty box.
    if (list.length === 0) {
      const noneOpt = document.createElement('option');
      noneOpt.value = '';
      noneOpt.textContent = 'none';
      noneOpt.disabled = true;
      noneOpt.selected = true;
      sel.appendChild(noneOpt);
      sel.value = '';
    }
    for (const sid of list) {
      const opt = document.createElement('option');
      opt.value = sid;
      // The system sees the UUID; the user sees the LABEL when one is
      // set (the Operator's 08-12 rename spec). Fallback: the UUID.
      const label = labels[sid];
      opt.textContent = label ? (label + '  ·  ' + sid.slice(0, 8)) : sid;
      opt.title = sid;
      sel.appendChild(opt);
    }
    sel.value = cur;
    // The label is plain "Session" — the dropdown lists the selections
    // (the Operator's 2-column spec). The full UUID shows in the dropdown.
    const sl = $('session-label');
    if (sl) {
      sl.textContent = 'Session';
      sl.title = 'Profile: ' + prof + ' · Session: ' + cur;
    }
  } catch (e) { /* ignore */ }
}

// ── REALTIME REFRESH (the Operator's 08-12 UX directive) ───────────────────
// The WEBSITE view polls on a realtime cadence (3s) — it is INDEPENDENT
// of the on-disk write cadence (vault autofill, session records, enrich
// passes all write on their own schedules). The two are separated by
// design: disk writers do their thing; the page re-fetches every 3s and
// always shows the current state. Only the ACTIVE tab polls (zero waste
// on hidden pages).
const REFRESH_MS = {
  sessions: 3000,
  vault: 3000,
  usage: 3000,
  behavior: 3000,
};

let _refreshTimer = null;

function _loaderFor(ws) {
  if (ws === 'sessions' && typeof loadSessions === 'function') return loadSessions;
  if (ws === 'vault' && typeof loadVaultGrid === 'function') return loadVaultGrid;
  if (ws === 'usage' && typeof loadUsage === 'function') return loadUsage;
  if (ws === 'behavior' && typeof loadBehavior === 'function') return loadBehavior;
  return null;
}

function _scheduleRefresh() {
  if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
  const ws = currentTab();
  const loader = _loaderFor(ws);
  const ms = REFRESH_MS[ws];
  if (!loader || !ms) return;
  _refreshTimer = setInterval(() => {
    // Only refresh while this tab is STILL active (the user may have
    // switched while a tick was pending).
    if (currentTab() === ws) {
      try { loader(); } catch (e) { /* never break the page */ }
    } else {
      clearInterval(_refreshTimer);
      _refreshTimer = null;
      _scheduleRefresh();  // follow the new tab's cadence
    }
  }, ms);
}

// Hook into tab activation: every switch re-schedules the refresh.
const _origActivate = activateTab;
activateTab = (ws, opts) => {
  _origActivate(ws, opts);
  _scheduleRefresh();
};
