// ── THE CALL PAGE (the Operator's spec): agents + the operator call and
//    discuss over voice. 2 callers minimum (includes the operator),
//    max 15. The grid is DISCORD-STYLE: it computes the rows/columns
//    that best fit the stage (tiles stay ~16:9), every tile SHRINKS so
//    all callers stay INSIDE the page body (no scrolling), and the
//    fill is row-major — row 1 populates first, then row 2, then row 3.
// ─────────────────────────────────────────────────────────────────────
var CALL_MIN = 2;
var CALL_MAX = 15;
var CALL_ACTIVE = false;   // the call state (Start/Stop)
var CALL_MUTED = false;    // the microphone state (Mute/Unmute)
// The ROSTER — the real caller names (the Operator's spec: profile-wise,
// prompt-chosen). The operator is always #0; 2 .. 15 total.
var CALL_ROSTER = ['Operator'];
var CALL_AGENTS = [];  // NO force-populated names — only existing profiles
var CALL_GRID_GAP = 8; // px between tiles
function CALL_COUNT() { return CALL_ROSTER.length; }

// The caller panel (16:9, HD stream scaled to fit — the avatar stands
// in for the video stream; the ratio and object-fit are the spec).
function callPanel(i) {
  const el = document.createElement('div');
  el.className = 'call-panel';
  const av = document.createElement('div');
  av.className = 'call-avatar';
  av.textContent = '👤';
  el.appendChild(av);
  const tag = document.createElement('div');
  tag.className = 'call-tag';
  if (i === 0) {
    tag.textContent = 'Operator';
    const op = document.createElement('div');
    op.className = 'call-op';
    op.textContent = 'YOU';
    el.appendChild(op);
  } else {
    tag.textContent = CALL_ROSTER[i] || 'caller';
  }
  el.appendChild(tag);
  return el;
}

// THE DISCORD-STYLE GRID MATH: choose rows/cols so the tiles come as
// close to 16:9 as possible while covering the stage — rows fill
// first (row-major), and the tiles shrink to always fit the body.
function callGridDims(n) {
  const grid = $('call-grid');
  if (!grid) return {cols: Math.min(5, Math.max(2, n)), rows: Math.ceil(n / 2)};
  const stageW = Math.max(100, grid.clientWidth);
  const stageH = Math.max(80, grid.clientHeight);
  let best = {cols: Math.min(5, Math.max(2, n)), rows: Math.ceil(n / Math.min(5, Math.max(2, n)))};
  let bestScore = -1;
  for (let rows = 1; rows <= 3; rows++) {
    for (let cols = 2; cols <= 5; cols++) {
      if (cols * rows >= n) {
        // How close the tiles are to 16:9 in this arrangement.
        const tileAR = (stageW / cols) / (stageH / rows);
        const score = -Math.abs(tileAR - (16 / 9));
        if (score > bestScore) { bestScore = score; best = {cols, rows}; }
      }
    }
  }
  return best;
}

function renderCallGrid() {
  const grid = $('call-grid');
  if (!grid) return;
  const n = CALL_COUNT();
  const {cols} = callGridDims(n);
  grid.style.setProperty('--call-cols', cols);
  grid.innerHTML = '';
  for (let i = 0; i < n; i++) {
    const p = callPanel(i);
    if (CALL_MUTED && i === 0) p.classList.add('muted');
    if (CALL_ACTIVE) p.classList.add('speaking');
    grid.appendChild(p);
  }
  // THE SHRINK-TO-FIT (the Operator's spec + Discord-style): after the DOM
  // is in, measure the stage and size every tile so ALL callers fit
  // inside the body — no overflow, no scroll.
  requestAnimationFrame(() => sizeCallTiles(grid));
  const status = $('call-status');
  if (status) {
    const state = CALL_ACTIVE ? 'in call' : 'idle';
    status.textContent = n + ' callers · ' + state +
      (CALL_MUTED ? ' · mic muted' : '');
  }
  const count = $('call-count');
  if (count) count.textContent = n + ' / ' + CALL_MAX;
  const addBtn = $('call-add');
  const rmBtn = $('call-remove');
  if (addBtn) addBtn.disabled = n >= CALL_MAX;
  if (rmBtn) rmBtn.disabled = n <= CALL_MIN;
}

// Size every tile to fit the stage: width = stage/cols, height keeps
// 16:9; if the rows overflow the stage height, scale down so the whole
// grid sits inside the body.
function sizeCallTiles(grid) {
  const tiles = grid.querySelectorAll('.call-panel');
  if (!tiles.length) return;
  const stageW = Math.max(100, grid.clientWidth);
  const stageH = Math.max(80, grid.clientHeight);
  const n = tiles.length;
  const {cols} = callGridDims(n);
  // THE ROWS THAT ACTUALLY HAVE TILES (the Operator's spec: row 1 fills
  // first, then row 2, then row 3). Empty trailing rows must NOT be in
  // the template — otherwise align-content centers the empty rows and
  // the visible tiles get pinned off-center.
  const rows = Math.ceil(n / cols);
  const gap = CALL_GRID_GAP;
  // Natural tile width from the columns; height from 16:9.
  let w = (stageW - (cols - 1) * gap) / cols;
  let h = w * (9 / 16);
  // If the rows overflow the stage, shrink so everything fits.
  const totalH = rows * h + (rows - 1) * gap;
  if (totalH > stageH) {
    const scale = (stageH - (rows - 1) * gap) / (rows * h);
    w *= scale;
    h *= scale;
  }
  tiles.forEach(t => {
    t.style.width = Math.floor(w) + 'px';
    t.style.height = Math.floor(h) + 'px';
  });
  // Make the grid center its rows (Discord-style: the whole block sits
  // centered in the stage).
  grid.style.gridTemplateRows = 'repeat(' + rows + ', ' + Math.floor(h) + 'px)';
}

// Recompute on resize so the tiles always fit the window.
if (window.addEventListener) {
  window.addEventListener('resize', () => {
    const grid = $('call-grid');
    if (grid && grid.querySelectorAll('.call-panel').length) {
      sizeCallTiles(grid);
    }
  });
}
// THE VISIBLE-ONLY RE-SIZE: the grid is hidden at boot (display:none →
// clientWidth=0), so tiles must be re-measured when the Call page
// becomes ACTIVE. index.js calls onCallShown() after switching tabs.
function onCallShown() {
  const grid = $('call-grid');
  if (grid && grid.querySelectorAll('.call-panel').length) {
    sizeCallTiles(grid);
  }
}

// ── THE HOTBAR (the Operator's spec): the button pairs ──
// The CALLABLE set (the Operator's spec): ONLY the EXISTING profiles — no
// force-populated names. The operator is always caller #0.
var CALL_AVAILABLE = [];
var __PROFILE_COUNT = 0;
async function loadCallAvailable() {
  try {
    const r = await fetch('/profiles');
    const d = await r.json();
    CALL_AVAILABLE = (d.profiles || []).map(p => p.name);
    __PROFILE_COUNT = CALL_AVAILABLE.length;
  } catch (e) {
    CALL_AVAILABLE = [];
  }
}

// ── THE CALLER PICKER (the Operator's spec): a popup list of the existing
//    profiles/agents — CLICK to add/remove, no typing. ───────────────
function openCallPicker(mode) {
  const picker = $('call-picker');
  const list = $('call-picker-list');
  const title = $('call-picker-title');
  if (!picker || !list) return;
  title.textContent = mode === 'add' ? 'Add caller — pick a profile/agent' : 'Remove caller — pick who to remove';
  list.innerHTML = '';
  const inRoom = CALL_ROSTER.slice();
  let items = [];
  if (mode === 'add') {
    items = CALL_AVAILABLE.filter(n => !inRoom.includes(n)).slice(0, 15);
  } else {
    items = inRoom.slice(1);  // never the operator
  }
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'call-picker-empty';
    empty.textContent = mode === 'add' ? 'Everyone is already in the call.' : 'Only the operator is in the call.';
    list.appendChild(empty);
  }
  for (const n of items) {
    const item = document.createElement('div');
    item.className = 'call-picker-item';
    const isProfile = __PROFILE_COUNT > 0 && CALL_AVAILABLE.indexOf(n) < __PROFILE_COUNT;
    if (isProfile) {
      const tag = document.createElement('span');
      tag.className = 'pick-tag';
      tag.textContent = 'profile';
      item.appendChild(tag);
    }
    const label = document.createElement('span');
    label.textContent = n;
    item.appendChild(label);
    item.addEventListener('click', () => {
      if (mode === 'add') CALL_ROSTER.push(n);
      else {
        const idx = CALL_ROSTER.indexOf(n);
        if (idx > 0) CALL_ROSTER.splice(idx, 1);
      }
      closeCallPicker();
      renderCallGrid();
    });
    list.appendChild(item);
  }
  picker.classList.remove('hidden');
}
function closeCallPicker() {
  const picker = $('call-picker');
  if (picker) picker.classList.add('hidden');
}

function wireCallHotbar() {
  const toggle = $('call-toggle');
  if (toggle) toggle.addEventListener('click', () => {
    CALL_ACTIVE = !CALL_ACTIVE;
    toggle.textContent = CALL_ACTIVE ? '⏹ Stop Call' : '▶ Start Call';
    toggle.classList.toggle('active', CALL_ACTIVE);
    renderCallGrid();
  });
  const mute = $('call-mute');
  if (mute) mute.addEventListener('click', () => {
    CALL_MUTED = !CALL_MUTED;
    mute.textContent = CALL_MUTED ? '🔇 Unmute Mic' : '🎙 Mute Mic';
    mute.classList.toggle('active', CALL_MUTED);
    renderCallGrid();
  });
  const add = $('call-add');
  if (add) add.addEventListener('click', () => {
    if (CALL_COUNT() >= CALL_MAX) return;
    openCallPicker('add');  // the popup list (the Operator's spec: click, no typing)
  });
  const rm = $('call-remove');
  if (rm) rm.addEventListener('click', () => {
    if (CALL_COUNT() <= CALL_MIN) return;
    openCallPicker('remove');  // the popup list (the Operator's spec: click, no typing)
  });
  const msg = $('call-msg-input');
  const send = $('call-msg-send');
  const sendMsg = () => {
    if (!msg || !msg.value.trim()) return;
    const status = $('call-status');
    if (status) status.textContent = 'operator: ' + msg.value.trim() + ' …';
    msg.value = '';
  };
  if (send) send.addEventListener('click', sendMsg);
  if (msg) msg.addEventListener('keydown', e => {
    if (e.key === 'Enter') sendMsg();
  });
  const reset = $('call-refresh');
  if (reset) reset.addEventListener('click', () => {
    CALL_ROSTER = ['Operator'];
    CALL_ACTIVE = false;
    CALL_MUTED = false;
    const toggle2 = $('call-toggle');
    if (toggle2) { toggle2.textContent = '▶ Start Call'; toggle2.classList.remove('active'); }
    const mute2 = $('call-mute');
    if (mute2) { mute2.textContent = '🎙 Mute Mic'; mute2.classList.remove('active'); }
    renderCallGrid();
  });
}
// Boot the room.
loadCallAvailable().then(() => {
  // The default room: the operator + one EXISTING profile (the Operator's
  // spec: 2 callers minimum, all real — never force-populated).
  if (CALL_ROSTER.length < CALL_MIN && CALL_AVAILABLE.length) {
    CALL_ROSTER = ['Operator', CALL_AVAILABLE[0]];
  }
  renderCallGrid();
});
renderCallGrid();
wireCallHotbar();
// The picker close button + outside-click closes it.
document.addEventListener('DOMContentLoaded', () => {
  const closeBtn = $('call-picker-close');
  if (closeBtn) closeBtn.addEventListener('click', closeCallPicker);
  document.addEventListener('click', (e) => {
    const picker = $('call-picker');
    if (picker && !picker.classList.contains('hidden') &&
        !e.target.closest('.call-picker') && !e.target.closest('#call-add') &&
        !e.target.closest('#call-remove')) {
      closeCallPicker();
    }
  });
});
