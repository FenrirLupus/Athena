// ── VAULT GRID (the Operator's cell-based table: X = columns, Y = rows) ───
let VAULT_COLS = [];
let VAULT_ROWS = [];
let selectedRowId = null;
// THE 08-17 WEBSITE PAGINATION (the Operator's spec): the vault loads 500
// per page, newest-first; "Load older" appends the next 500 (like the chat
// page). The AGENT's vault_query tool sees the FULL vault — only the
// website grid is paginated. VAULT_OFFSET tracks how far back we've loaded.
const VAULT_PAGE_SIZE = 500;
let VAULT_OFFSET = 0;
let VAULT_TOTAL = 0;   // the total entries (for the load-older pill)
// REALTIME GUARD: the fingerprint of the last rendered data — a 3s poll
// that finds the same state skips the re-render (scroll/menus survive).
let VAULT_FINGERPRINT = null;

// THE VAULT ERROR PANEL (the Operator's spec): centered on the page body —
// "Vault Error: {code}" + "{message}". Hidden on success.
function showVaultError(code, message) {
  const panel = $('vault-error');
  const grid = $('vault-grid');
  if (!panel) return;
  $('vault-error-code').textContent = 'Vault Error: ' + (code || 'unknown');
  $('vault-error-msg').textContent = message || 'Unknown error';
  panel.classList.remove('hidden');
  if (grid) grid.classList.add('hidden');
}
function hideVaultError() {
  const panel = $('vault-error');
  const grid = $('vault-grid');
  if (panel) panel.classList.add('hidden');
  if (grid) grid.classList.remove('hidden');
}

async function loadVaultGrid() {
  const prof = currentProfile();
  hideVaultError();
  // Reset to the newest page on a full reload.
  VAULT_OFFSET = 0;
  VAULT_ROWS = [];
  VAULT_COLS = [];
  try {
    const res = await fetch('/vault/table?profile=' + encodeURIComponent(prof) + '&limit=' + VAULT_PAGE_SIZE + '&offset=0');
    let d;
    try { d = await res.json(); } catch (e) { d = null; }
    if (!res.ok || !d) {
      const detail = d && d.error ? d.error : ('HTTP ' + res.status);
      showVaultError(res.status, detail);
      return;
    }
    $('vault-profile').textContent = 'profile: ' + (d.profile || prof) + ' · ' + (d.total || 0) + ' rows (page: ' + VAULT_PAGE_SIZE + ')';
    const cols = d.columns || [];
    const rows = d.rows || [];
    VAULT_COLS = cols;
    VAULT_ROWS = rows;
    VAULT_OFFSET = rows.length;
    VAULT_TOTAL = d.total || 0;
    renderVaultGrid();
    updateVaultLoadOlder(VAULT_TOTAL);
    VAULT_FINGERPRINT = JSON.stringify(cols) + '|' + JSON.stringify(rows.map(r => r.id));
    return d.total;
  } catch (e) {
    showVaultError('load', e && e.message ? e.message : String(e));
  }
}

// THE 08-17 LOAD-OLDER (the vault's previous page of older entries).
// Mirrors the chat pattern: fetching a higher offset returns the page
// BEFORE (older); it is PREPENDED above so the vault stays oldest→newest.
async function loadOlderVault() {
  const prof = currentProfile();
  const btn = $('vault-load-older');
  if (btn) { btn.disabled = true; btn.textContent = 'loading…'; }
  try {
    const res = await fetch('/vault/table?profile=' + encodeURIComponent(prof) +
                            '&limit=' + VAULT_PAGE_SIZE + '&offset=' + VAULT_OFFSET);
    let d;
    try { d = await res.json(); } catch (e) { d = null; }
    if (!res.ok || !d) return;
    const rows = d.rows || [];
    if (rows.length === 0) { updateVaultLoadOlder(d.total || 0, true); return; }
    const seenIds = new Set(VAULT_ROWS.map(r => r.id));
    const fresh = rows.filter(r => !seenIds.has(r.id));
    VAULT_ROWS = fresh.concat(VAULT_ROWS);   // PREPEND (older above)
    VAULT_OFFSET += rows.length;
    VAULT_TOTAL = d.total || 0;
    renderVaultGrid();
    updateVaultLoadOlder(VAULT_TOTAL);
  } catch (e) { /* ignore */ }
  finally {
    if (btn) btn.disabled = false;
  }
}

// THE 08-17 LOAD-OLDER PILL: appears at the TOP of the vault when the user
// scrolls to the top AND there are older entries (the chat history pattern).
function updateVaultLoadOlder(total, done) {
  const wrap = $('vault-load-more-wrap');
  const headerWrap = $('vault-load-more-at-top');
  if (!wrap) return;
  const loaded = VAULT_ROWS.length;
  const hasOlder = !done && loaded < (total || 0);
  // The pill only shows when scrolled to the top + older exist.
  const grid = $('vault-grid');
  const atTop = !grid || grid.scrollTop <= 2;
  wrap.style.display = (hasOlder && atTop) ? 'block' : 'none';
}

// THE 08-17 SCROLL LISTENER: the pill pops when the user reaches the top,
// exactly like the chat page's load-more pill.
$('vault-grid')?.addEventListener('scroll', () => {
  const wrap = $('vault-load-more-wrap');
  if (!wrap) return;
  const grid = $('vault-grid');
  const atTop = !grid || grid.scrollTop <= 2;
  const hasOlder = VAULT_ROWS.length < (VAULT_TOTAL || 0);
  wrap.style.display = (atTop && hasOlder) ? 'block' : 'none';
}, {passive: true});

function renderVaultGrid() {
  const head = $('vault-grid-head');
  const body = $('vault-grid-body');
  head.innerHTML = '';
  body.innerHTML = '';
  // The first header cell holds the ⋮ action column.
  const thr = document.createElement('tr');
  const actionTh = document.createElement('th');
  actionTh.textContent = '⋮';
  thr.appendChild(actionTh);
  for (const col of VAULT_COLS) {
    const th = document.createElement('th');
    th.textContent = col;
    thr.appendChild(th);
  }
  head.appendChild(thr);

  for (const row of VAULT_ROWS) {
    const tr = document.createElement('tr');
    if (row.id === selectedRowId) tr.classList.add('selected');
    tr.onclick = () => { selectRow(row.id); };
    // The ⋮ action cell — opens the per-row dropdown.
    const menuTd = document.createElement('td');
    menuTd.innerHTML = '<div class="row-menu-wrap">' +
      '<button class="row-menu-btn">⋮</button>' +
      '<div class="row-menu">' +
      '<div class="mi" data-act="edit">✏️ Edit</div>' +
      '<div class="mi" data-act="add">＋ Add Row</div>' +
      '<div class="mi" data-act="subtract">🗑 Subtract</div>' +
      '<div class="mi" data-act="copy">📋 Copy</div>' +
      '</div></div>';
    menuTd.querySelector('.row-menu-btn').onclick = (e) => {
      e.stopPropagation();
      document.querySelectorAll('.row-menu.open').forEach(m => m.classList.remove('open'));
      menuTd.querySelector('.row-menu').classList.toggle('open');
    };
    menuTd.querySelectorAll('.mi').forEach(mi => {
      mi.onclick = (e) => {
        e.stopPropagation();
        menuTd.querySelector('.row-menu').classList.remove('open');
        const act = mi.dataset.act;
        if (act === 'edit') openEditor('edit', row);
        else if (act === 'add') openEditor('add', null);
        else if (act === 'subtract') subtractRow(row.id);
        else if (act === 'copy') copyRow(row);
      };
    });
    tr.appendChild(menuTd);

    for (const col of VAULT_COLS) {
      const td = document.createElement('td');
      let v = row[col];
      if (v === null || v === undefined) v = '';
      v = String(v);
      if (col === 'id') { td.className = 'cell-id'; td.textContent = v.slice(0, 16) + '…'; }
      else if (v.length > 120) td.textContent = v.slice(0, 120) + '…';
      else td.textContent = v;
      td.title = (row[col] === null || row[col] === undefined) ? '' : String(row[col]);
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function selectRow(id) {
  selectedRowId = id;
  document.querySelectorAll('#vault-grid tbody tr').forEach(tr => tr.classList.remove('selected'));
  const rows = document.querySelectorAll('#vault-grid tbody tr');
  for (const row of rows) {
    if (row.querySelector('.cell-id') && row.querySelector('.cell-id').title.includes(id)) {
      row.classList.add('selected');
      break;
    }
  }
}

function copyRow(row) {
  const text = JSON.stringify(row, null, 2);
  navigator.clipboard.writeText(text).then(() => {
    alert('Row copied to clipboard.');
  }).catch(() => { prompt('Copy the row:', text); });
}

async function subtractRow(id) {
  if (!confirm('Subtract (soft-delete) this row? It stays recoverable in the archive.')) return;
  const prof = currentProfile();
  await fetch('/vault/row/' + encodeURIComponent(id) + '?profile=' + encodeURIComponent(prof),
              {method: 'DELETE'});
  loadVaultGrid();
}

// The inline cell editor (Edit an existing row / Add a new row).
function openEditor(mode, row) {
  const title = $('cell-editor-title');
  const fields = $('cell-editor-fields');
  fields.innerHTML = '';
  title.textContent = mode === 'edit' ? 'Edit Row' : 'Add Row';
  if (mode === 'edit' && row) {
    for (const col of VAULT_COLS) {
      if (col === 'deleted' || col === 'id') continue;
      const wrap = document.createElement('div');
      wrap.className = 'field';
      const lab = document.createElement('label');
      lab.textContent = col;
      const inp = document.createElement('textarea');
      inp.dataset.col = col;
      const v = row[col];
      inp.value = (v === null || v === undefined) ? '' : String(v);
      wrap.appendChild(lab); wrap.appendChild(inp);
      fields.appendChild(wrap);
    }
    $('cell-editor-save').onclick = () => saveEdit(row.id);
  } else {
    // Add mode: offer the core columns (the Operator's TYPE of call first).
    for (const col of ['type', 'role', 'content', 'context', 'source']) {
      const wrap = document.createElement('div');
      wrap.className = 'field';
      const lab = document.createElement('label');
      lab.textContent = col;
      const inp = document.createElement('textarea');
      inp.dataset.col = col;
      wrap.appendChild(lab); wrap.appendChild(inp);
      fields.appendChild(wrap);
    }
    $('cell-editor-save').onclick = () => saveAdd();
  }
  $('cell-editor').classList.add('open');
}
$('cell-editor-cancel').onclick = () => $('cell-editor').classList.remove('open');

async function saveEdit(id) {
  const cells = {};
  document.querySelectorAll('#cell-editor-fields textarea').forEach(t => {
    cells[t.dataset.col] = t.value;
  });
  const prof = currentProfile();
  await fetch('/vault/row/' + encodeURIComponent(id), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({profile: prof, cells}),
  });
  $('cell-editor').classList.remove('open');
  loadVaultGrid();
}

async function saveAdd() {
  const body = {profile: currentProfile()};
  document.querySelectorAll('#cell-editor-fields textarea').forEach(t => {
    body[t.dataset.col] = t.value;
  });
  await fetch('/vault/row', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  $('cell-editor').classList.remove('open');
  loadVaultGrid();
}

$('vault-add-row').onclick = () => openEditor('add', null);
$('vault-refresh').onclick = () => loadVaultGrid();
// THE 08-17 LOAD-OLDER (the next page of older vault entries).
const vaultOlderBtn = $('vault-load-older');
if (vaultOlderBtn) vaultOlderBtn.onclick = () => loadOlderVault();
// THE 08-17 IMPORT BUTTON (the Operator's vault-import spec): pick a .db,
// upload to /vault/import-db → export→JSONL→import into this profile.
const importBtn = $('vault-import-db');
const importFile = $('vault-import-file');
function currentProfileVault() {
  try { return (typeof currentProfile === 'function') ? currentProfile() : (window.currentProfile || ''); } catch (e) { return ''; }
}
if (importBtn) {
  importBtn.onclick = () => { if (importFile) importFile.click(); };
}
if (importFile) {
  importFile.onchange = async () => {
    const f = importFile.files && importFile.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    fd.append('profile', currentProfileVault() || '.default');
    const ok = confirm('Import "' + f.name + '" into the vault?\n\nCopy-first: only the matching variables (role/content/timestamp→date+time) are imported. Nothing existing is deleted or changed.');
    if (!ok) { importFile.value = ''; return; }
    try {
      const res = await fetch('/vault/import-db', { method: 'POST', body: fd });
      const d = await res.json();
      if (d && d.ok) {
        alert('Import complete:\n  ' + (d.imported || 0) + ' rows imported\n  ' + (d.skipped || 0) + ' skipped\n  profile: ' + (d.profile || '') + '\nCopy-first — original vault untouched.');
        loadVaultGrid();
      } else {
        alert('Import failed: ' + ((d && d.error) || ('HTTP ' + res.status)));
      }
    } catch (e) { alert('Import error: ' + e.message); }
    importFile.value = '';
  };
}
// Close any open row menu when clicking elsewhere.
document.addEventListener('click', (e) => {
  if (!e.target.closest('.row-menu-wrap')) {
    document.querySelectorAll('.row-menu.open').forEach(m => m.classList.remove('open'));
  }
});

