// ── Theme (the Operator's 5-color spec) ────────────────────────────────────
// The palettes cache: loaded from /config/theme/palette at boot. The
// toggle applies the EDITED palette (not the hardcoded CSS), so
// customizations survive a light/dark switch.
var THEME_PALETTES = {light: [], dark: []};
// MATCH SYSTEM THEME (the Operator's spec): default ON. When true, the theme
// follows the OS (prefers-color-scheme); when the user toggles it OFF
// and picks a mode, that choice PERSISTS (the Theme settings checkbox).
var MATCH_SYSTEM = true;

function applyTheme(mode, persist) {
  document.body.classList.toggle('dark', mode === 'dark');
  $('theme-toggle').textContent = mode === 'dark' ? '☀ Light' : '☾ Dark';
  if (persist !== false) localStorage.setItem('athena-theme', mode);
  // Apply the saved palette (if loaded) to the CSS variables.
  if (THEME_PALETTES[mode] && THEME_PALETTES[mode].length === 5) {
    applyPaletteCSS(THEME_PALETTES, mode);
  }
}
function systemMode() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
    ? 'light' : 'dark';
}
async function loadTheme() {
  try {
    const rp = await fetch('/config/theme/palette');
    const pd = await rp.json();
    if (pd.palettes) THEME_PALETTES = pd.palettes;
  } catch (e) { /* ignore */ }
  try {
    // The initial mode comes from CONFIG (the source of truth) + the
    // match_system flag.
    const rm = await fetch('/config/theme');
    const md = await rm.json();
    MATCH_SYSTEM = md.match_system !== false;
    const mode = MATCH_SYSTEM ? systemMode() : (md.mode === 'light' ? 'light' : 'dark');
    applyTheme(mode, false);
    // When matching the system, follow OS changes live.
    if (MATCH_SYSTEM && window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: light)');
      const onChange = (e) => { if (MATCH_SYSTEM) applyTheme(e.matches ? 'light' : 'dark', false); };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  } catch (e) {
    applyTheme(localStorage.getItem('athena-theme') || systemMode());
  }
}
$('theme-toggle').addEventListener('click', async () => {
  const next = document.body.classList.contains('dark') ? 'light' : 'dark';
  applyTheme(next);
  // PERSIST the mode to config.yaml — and DISABLE match_system: the
  // user made an explicit choice, it stays that value.
  MATCH_SYSTEM = false;
  try {
    await fetch('/config/theme', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({theme: next, match_system: false}),
    });
  } catch (e) { /* ignore */ }
});
// The MATCH SYSTEM THEME switch (called by the Theme settings checkbox).
async function setMatchSystem(enabled) {
  MATCH_SYSTEM = !!enabled;
  const mode = MATCH_SYSTEM ? systemMode() : (document.body.classList.contains('dark') ? 'dark' : 'light');
  applyTheme(mode);
  try {
    await fetch('/config/theme', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({theme: mode, match_system: MATCH_SYSTEM}),
    });
  } catch (e) { /* ignore */ }
}
loadTheme();
