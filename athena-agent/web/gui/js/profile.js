// ── Profile dropdown (top right — auto-switch on select) ────────────
// THE 08-17 PROFILE PERSISTENCE (the Operator's spec): the active profile
// is stored in localStorage so it SURVIVES the full reload on switch —
// otherwise ACTIVE_PROFILE resets to undefined after location.reload() and
// every page falls back to reading the .default profile's data (the "the
// dropdown switched but the files are still default" bug).
const PROFILE_KEY = 'athena_active_profile';
// THE 08-17 GLOBAL PROFILE SOURCE: ACTIVE_PROFILE is an explicit GLOBAL
// (window-scoped) so every page script (vault, sessions, chat, usage)
// shares ONE value via currentProfile() — a `let` declaration would be
// script-local and undefined to the other pages.
window.ACTIVE_PROFILE = '';
try {
  window.ACTIVE_PROFILE = localStorage.getItem(PROFILE_KEY) || '';
} catch (e) { window.ACTIVE_PROFILE = ''; }

async function loadProfiles() {
  try {
    const res = await fetch('/profiles');
    const d = await res.json();
    const sel = $('profile-select');
    sel.innerHTML = '';
    for (const p of (d.profiles || [])) {
      // The API returns {name, locked, is_default} objects now.
      const name = typeof p === 'string' ? p : p.name;
      const locked = typeof p === 'string' ? false : !!p.locked;
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name + (locked ? ' 🔒' : '');
      sel.appendChild(opt);
    }
    // The server's current is the source of truth on boot (it persisted
    // the switch); sync ACTIVE_PROFILE + the dropdown to it. Falls back
    // to any locally-stored choice when the server hasn't set one yet.
    const serverCur = d.current || 'default';
    window.ACTIVE_PROFILE = serverCur !== 'default' ? serverCur : (window.ACTIVE_PROFILE || 'default');
    sel.value = window.ACTIVE_PROFILE;
    try { localStorage.setItem(PROFILE_KEY, window.ACTIVE_PROFILE); } catch (e) {}
  } catch (e) { /* server may be mid-boot */ }
}
$('profile-select').addEventListener('change', async e => {
  const target = e.target.value;
  try {
    const res = await fetch('/profiles/switch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({profile: target}),
    });
    const d = await res.json();
    if (d.ok) {
      // Persist the choice BEFORE the reload so the pages load the right
      // profile's data after location.reload().
      try { localStorage.setItem(PROFILE_KEY, target); } catch (e) {}
      window.ACTIVE_PROFILE = target;
      // THE 08-17 FULL-RELOAD (the Operator's spec): switching profiles
      // reloads the ENTIRE website so every page (vault, sessions, chat,
      // usage, settings) re-fetches for the SELECTED profile — nothing
      // stale from the previous agent survives. This guarantees the new
      // profile's identity, sessions, and vault are all loaded.
      location.reload();
    } else {
      alert('Profile switch failed: ' + ((d && d.error) || 'unknown'));
    }
  } catch (e) { /* ignore */ }
});

