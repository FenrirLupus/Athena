// ── Sessions (per ACTIVE profile — auto-switches across the site) ────
// THE 08-17 PROFILE FIX: this file must NOT declare its own ACTIVE_PROFILE
// (it shadowed the shared one with 'default', so the sessions PAGE always
// fetched the .default profile's sessions regardless of the selected agent).
// It now reads currentProfile() → the shared, persisted, per-profile choice.
// REALTIME GUARD: the fingerprint of the last rendered session list — a
// 3s poll that finds the same state skips the rebuild (no visible churn).
let SESSIONS_FINGERPRINT = null;

async function loadSessions() {
  const prof = currentProfile();
  const res = await fetch('/sessions?profile=' + encodeURIComponent(prof));
  const data = await res.json();
  const list = data.sessions || [];
  const fp = JSON.stringify(list) + '|' +
             JSON.stringify(data.activity || []);
  if (fp === SESSIONS_FINGERPRINT) return;  // same state — leave the DOM
  SESSIONS_FINGERPRINT = fp;
  $('sessions-profile').textContent = 'profile: ' + (data.profile || prof);
  const labels = data.labels || {};   // {UUID: Label} — the user's side
  $('sessions-list').innerHTML = '';
  // ACTIVITY (the Operator's spec): mark each session active or stale + its
  // ENTRY COUNT (the count the mass-delete thresholds use).
  const activity = {};
  for (const a of (data.activity || [])) activity[a.session_id] = a;
  for (const sid of (data.sessions || []).slice().reverse()) {
    const row = document.createElement('div');
    row.className = 'session';
    const act = activity[sid] || {};
    const mark = act.stale ? ' · stale' : ' · active';
    const count = act.messages != null ? ' · ' + act.messages + ' entries' : '';
    // The row shows the LABEL (when set — the user's side), the UUID
    // (the system's side), the entry count + a Rename and Delete button.
    const label = labels[sid] || '';
    row.innerHTML =
      '<span class="session-label"></span>' +
      '<span class="session-id"></span>' +
      '<span class="session-act"></span>' +
      '<span class="session-count"></span>' +
      '<button class="session-ren" title="Rename this session">✏️</button>' +
      '<button class="session-del" title="Delete this session">🗑</button>';
    row.querySelector('.session-label').textContent = label ? (label + ' — ') : '';
    row.querySelector('.session-label').style.fontWeight = label ? '600' : 'normal';
    row.querySelector('.session-id').textContent = sid;
    row.querySelector('.session-act').textContent = mark;
    row.querySelector('.session-count').textContent = count;
    if (act.stale) row.classList.add('stale');
    // RENAME (the Operator's 08-12 spec): store {UUID: label} — the
    // system still addresses the session by UUID; the user sees the label.
    row.querySelector('.session-ren').onclick = async (e) => {
      e.stopPropagation();
      const cur = labels[sid] || '';
      const label = prompt('Session label (empty = clear):', cur);
      if (label === null) return;
      await fetch('/sessions/' + encodeURIComponent(sid) + '/label', {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({label: label, profile: prof}),
      });
      loadSessions();
      loadSessionDropdown();
    };
    row.querySelector('.session-del').onclick = async (e) => {
      e.stopPropagation();
      if (!confirm('Delete session ' + sid + '? This removes the .db file.')) return;
      await fetch('/sessions/' + encodeURIComponent(sid) +
                  '?profile=' + encodeURIComponent(prof),
                  {method: 'DELETE'});
      loadSessions();
      loadSessionDropdown();
    };
    row.onclick = async () => {
      const r = await fetch('/sessions/' + sid + '?profile=' + encodeURIComponent(prof));
      const d = await r.json();
      $('session-view').textContent = JSON.stringify(d.messages || d, null, 2);
    };
    $('sessions-list').appendChild(row);
  }
}

// Create a NEW session (points the chat at it) + refresh the dropdown.
$('sessions-new').addEventListener('click', async () => {
  const prof = currentProfile();
  try {
    const r = await fetch('/sessions/new?profile=' + encodeURIComponent(prof),
                          {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      loadSessions();
      loadSessionDropdown();
      loadChatHistory();
    }
  } catch (e) { /* ignore */ }
});
$('sessions-refresh').addEventListener('click', () => {
  loadSessions();
  loadSessionDropdown();
});

// ── MASS DELETE by entry count (the Operator's 08-11 spec) ────────────────
// Two modes (radio): "at least N" deletes sessions with >= N entries;
// "at most N" deletes sessions with <= N entries. Never the current one.
$('sessions-bulk-delete').addEventListener('click', async () => {
  const prof = currentProfile();
  const n = parseInt($('bulk-count').value, 10);
  if (!n || n < 1) {
    $('sessions-bulk-status').textContent = 'enter a positive count';
    return;
  }
  const atLeast = $('bulk-min').checked;
  const label = (atLeast ? 'at least ' : 'at most ') + n + ' entries';
  if (!confirm('Delete ALL sessions with ' + label + '? This removes their .db files. The current session is kept.')) return;
  const st = $('sessions-bulk-status');
  st.textContent = 'deleting…';
  try {
    const body = atLeast ? {profile: prof, min_entries: n} : {profile: prof, max_entries: n};
    const r = await fetch('/sessions/delete-by-count', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.ok) {
      st.textContent = 'deleted ' + (d.deleted_count || 0) + ' session(s) with ' + label;
    } else {
      st.textContent = 'failed: ' + (d.detail || 'unknown');
    }
  } catch (e) {
    st.textContent = 'error: ' + e.message;
  }
  loadSessions();
  loadSessionDropdown();
});
