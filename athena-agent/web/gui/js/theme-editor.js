// ── Theme palette editor (the Operator's spec) ─────────────────────────────
// HSL ↔ hex helpers.
function hexToHsl(hex) {
  let h = (hex || '').replace('#', '');
  if (h.length === 3) h = h.split('').map(c => c + c).join('');
  const r = parseInt(h.slice(0, 2), 16) / 255;
  const g = parseInt(h.slice(2, 4), 16) / 255;
  const b = parseInt(h.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let hh = 0, s = 0, l = (max + min) / 2;
  const d = max - min;
  if (d) {
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === r) hh = ((g - b) / d + (g < b ? 6 : 0));
    else if (max === g) hh = (b - r) / d + 2;
    else hh = (r - g) / d + 4;
    hh *= 60;
  }
  return { h: Math.round(hh), s: Math.round(s * 100), l: Math.round(l * 100) };
}
function hslToHex(h, s, l) {
  h = ((h % 360) + 360) % 360;
  s /= 100; l /= 100;
  const f = n => {
    const k = (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1))));
  };
  const toHex = v => v.toString(16).padStart(2, '0');
  return '#' + toHex(f(0)) + toHex(f(8)) + toHex(f(4));
}
function hexIsValid(s) {
  return /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test((s || '').trim());
}

// Build the theme page: two subsections (Light/Dark), each with 5
// rounded color squares + HSL sliders + hex field per color. The MATCH
// SYSTEM THEME Yes/No checkbox (the Operator's spec) sits at the top.
function buildThemeEditor(page, palettes) {
  page.innerHTML = '';
  // THE 08-15 DEDUP: the wrapped section provides the Sub Header — no
  // duplicate internal h2/sub here.
  // ── MATCH SYSTEM THEME (the Operator's spec): Yes/No checkbox. Default
  //    YES — the theme follows the OS. OFF → the chosen mode persists.
  const msRow = document.createElement('div');
  msRow.className = 'setting-row';
  const msInfo = document.createElement('div');
  const msLab = document.createElement('label');
  msLab.textContent = 'Match System Theme';
  msInfo.appendChild(msLab);
  const msDesc = document.createElement('div');
  msDesc.className = 'desc';
  msDesc.textContent = 'Will Dynamically Switch The Theme Based On Operating System Theme Settings';
  msInfo.appendChild(msDesc);
  msRow.appendChild(msInfo);
  const msSel = document.createElement('select');
  msSel.innerHTML = '<option value="true">Yes</option><option value="false">No</option>';
  // The current match_system value (loaded async — fetch then set).
  fetch('/config/theme').then(r => r.json()).then(md => {
    msSel.value = md.match_system !== false ? 'true' : 'false';
  }).catch(() => { /* default Yes */ });
  msSel.addEventListener('change', async () => {
    await setMatchSystem(msSel.value === 'true');
  });
  msRow.appendChild(msSel);
  page.appendChild(msRow);
  ['light', 'dark'].forEach(mode => {
    // The color ROLE NAMES (the Operator's spec): 1 Primary · 2 Secondary ·
    // 3 Accent 1 · 4 Accent 2 · 5 Font.
    const COLOR_NAMES_TOP = ['Primary', 'Secondary', 'Accent 1', 'Accent 2', 'Font'];
    const sec = document.createElement('div');
    sec.className = 'theme-mode';
    const title = document.createElement('h3');
    title.textContent = mode === 'light' ? 'Light Mode Theme' : 'Dark Mode Theme';
    sec.appendChild(title);
    // 5 rows — ONE LINE PER COLOR (the Operator's spec): each row carries its
    // own preview tile, so there are NO separate top squares.
    // {preview} {name} H:{value} {H slider} S:{value} {S slider}
    // L:{value} {L slider} {hex code}
    const rows = [];
    for (let i = 0; i < 5; i++) {
      const row = document.createElement('div');
      row.className = 'theme-color-row';
      const hex = palettes[mode][i] || '#000000';
      const hsl = hexToHsl(hex);
      // The small color preview (left of everything).
      const prev = document.createElement('div');
      prev.className = 'theme-preview';
      prev.style.background = hex;
      row.appendChild(prev);
      // The color name.
      const lbl = document.createElement('span');
      lbl.className = 'theme-color-label';
      lbl.textContent = COLOR_NAMES_TOP[i];
      row.appendChild(lbl);
      // H:{value} {slider} S:{value} {slider} L:{value} {slider}.
      const hslWrap = document.createElement('div');
      hslWrap.className = 'theme-hsl';
      ['h', 's', 'l'].forEach(ch => {
        const val = document.createElement('span');
        val.className = 'theme-val';
        val.dataset.ch = ch;
        val.textContent = ch.toUpperCase() + ':' + hsl[ch];
        hslWrap.appendChild(val);
        const inp = document.createElement('input');
        inp.type = 'range';
        inp.min = ch === 'h' ? 0 : 0;
        inp.max = ch === 'h' ? 360 : 100;
        inp.value = hsl[ch];
        inp.dataset.ch = ch;
        inp.dataset.mode = mode;
        inp.dataset.idx = i;
        hslWrap.appendChild(inp);
      });
      row.appendChild(hslWrap);
      // The hex code field (the end of the line).
      const hexInp = document.createElement('input');
      hexInp.type = 'text';
      hexInp.className = 'theme-hex';
      hexInp.value = hex;
      hexInp.dataset.mode = mode;
      hexInp.dataset.idx = i;
      row.appendChild(hexInp);
      sec.appendChild(row);
      rows[i] = { hslWrap, hexInp, sq: prev };
    }
    // Wire the row: slider → HSL values + hex + LIVE CSS (the Operator's
    // spec: colors update REAL-TIME so the user sees the result before
    // saving); hex → sliders + live CSS.
    for (let i = 0; i < 5; i++) {
      const { hslWrap, hexInp, sq } = rows[i];
      // THE REALTIME PUSH (the Operator's spec): update the in-memory
      // palette + the live CSS variable for the CURRENTLY VISIBLE mode,
      // so every element using that color changes instantly.
      const livePush = (mode, idx, hex, hsl) => {
        if (!palettes[mode]) palettes[mode] = [];
        palettes[mode][idx] = hex;
        // PRESERVE THE EXACT HSL (the Operator's spec): the hex field alone
        // loses hue when saturation is 0 (grey). Store the HSL on the
        // field so a save/reload restores the REAL values, not the
        // lossy grey round-trip.
        hexInp.dataset.hsl = JSON.stringify(hsl || window.hexToHsl(hex));
        const visibleMode = document.body.classList.contains('dark') ? 'dark' : 'light';
        if (mode === visibleMode) {
          // The theme vars live on BOTH :root and body.dark/body.light —
          // the body scope overrides html, so BOTH must be set.
          const VAR = ['--primary', '--secondary', '--tertiary', '--quaternary', '--font'];
          document.documentElement.style.setProperty(VAR[idx], hex);
          document.body.style.setProperty(VAR[idx], hex);
        }
      };
      const updateFromSliders = () => {
        const v = {};
        hslWrap.querySelectorAll('input[type=range]').forEach(r => {
          v[r.dataset.ch] = Number(r.value);
        });
        const hex = hslToHex(v.h, v.s, v.l);
        hexInp.value = hex;
        sq.style.background = hex;
        hslWrap.querySelectorAll('.theme-val').forEach(el => {
          el.textContent = el.dataset.ch.toUpperCase() + ':' + v[el.dataset.ch];
        });
        livePush(mode, i, hex, v);
      };
      hslWrap.querySelectorAll('input[type=range]').forEach(r => {
        r.addEventListener('input', updateFromSliders);
      });
      hexInp.addEventListener('input', () => {
        if (hexIsValid(hexInp.value)) {
          const hx = hexInp.value.startsWith('#') ? hexInp.value : '#' + hexInp.value;
          const hsl2 = hexToHsl(hx);
          hslWrap.querySelectorAll('input[type=range]').forEach(r => {
            r.value = hsl2[r.dataset.ch];
          });
          hexInp.value = hx;
          sq.style.background = hx;
          hslWrap.querySelectorAll('.theme-val').forEach(el => {
            el.textContent = el.dataset.ch.toUpperCase() + ':' + hsl2[el.dataset.ch];
          });
          livePush(mode, i, hx, hsl2);
        }
      });
    }
    page.appendChild(sec);
  });
  // Apply the current mode's palette to the CSS variables live.
  applyPaletteCSS(palettes, document.body.classList.contains('dark') ? 'dark' : 'light');
}

function applyPaletteCSS(palettes, mode) {
  const p = palettes[mode] || [];
  if (p.length < 5) return;
  const root = document.documentElement;
  // NOTE: the theme vars live on BOTH :root AND body.dark/body.light —
  // the body scope overrides html, so BOTH must be set for a live
  // change to reach every element.
  const targets = [root, document.body];
  const VAR = ['--primary', '--secondary', '--tertiary', '--quaternary', '--font'];
  for (const t of targets) {
    for (let i = 0; i < 5; i++) t.style.setProperty(VAR[i], p[i]);
  }
}
