// ── Behavior (the Operator's 08-11 spec): the emotional stats map ───────────
// Top: the 3-layer Emotional Octogon (outer = -1 names, middle = 0 names,
// center = +1 names) with the current value marked per axis.
// Middle: one axis bar per axis on the -1..+1 continuum.
// Bottom: the 24×24 Emotional Array table (0,0 = Neutral; 1..24 = the
// emotions in wheel order) with the live vector's cells highlighted.

async function loadBehavior() {
  try {
    const [stateRes, histRes] = await Promise.all([
      fetch('/config/emotion'),
      fetch('/config/emotion/history?limit=40'),
    ]);
    const d = await stateRes.json();
    const hist = await histRes.json();
    renderBehavior(d, hist);
  } catch (e) {
    const el = $('behavior-octogon-wrap');
    if (el) el.innerHTML = '<p class="behavior-array-neutral">Behavior unavailable: ' + e.message + '</p>';
  }
}

function renderBehavior(d, hist) {
  const axes = (d.axes && d.axes.length) ? d.axes : ['joy','trust','fear','surprise','sadness','disgust','anger','anticipation'];
  const wheel = d.wheel || {};
  const vec = (d.agent && d.agent.vector) || {};
  const current = (d.agent && d.agent.current) || 'neutral — uniform vector';

  const prof = $('behavior-profile');
  if (prof) prof.textContent = 'profile: ' + (d.profile || 'default') + ' — ' + current;

  renderPolygraph(axes, wheel, vec, (hist && hist.points) || []);
  renderBars(axes, wheel, vec);
  renderArray(d, vec);
}

// The POLYGRAPH (the Operator's 08-11 call): an X/Y time-series graph like a
// lie-detector chart. X = turns (oldest → newest), Y = the -1..+1 axis
// range. One line per emotional axis traces how that emotion changed
// across the conversation. Band guide-lines at -0.33 / +0.33.
function renderPolygraph(axes, wheel, vec, points) {
  const wrap = $('behavior-octogon-wrap');
  if (!wrap) return;
  // FULL WIDTH (the Operator's 08-12 spec): the polygraph spans the
  // whole page — the viewBox width tracks the container's real width,
  // not a fixed 900.
  const W = Math.max(600, wrap.clientWidth || 900), H = 320;
  const padL = 46, padR = 14, padT = 18, padB = 26;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const yFor = (v) => padT + plotH * (1 - (Math.max(-1, Math.min(1, v)) + 1) / 2);

  const series = points.length
    ? points
    : [{time: 'now', vector: vec}];

  // The color per axis: cycle through the theme accents.
  const axisColors = ['var(--tertiary)', 'var(--quaternary)', '#4fc3f7', '#aed581', '#f48fb1', '#ffb74d', '#b39ddb', '#4db6ac'];
  const colors = {};
  axes.forEach((a, i) => { colors[a] = axisColors[i % axisColors.length]; });

  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" class="behavior-polygraph" xmlns="http://www.w3.org/2000/svg">';

  // Y grid: the -1 / -0.33 / 0 / +0.33 / +1 guide lines.
  for (const gv of [1, 0.33, 0, -0.33, -1]) {
    const y = yFor(gv);
    svg += '<line x1="' + padL + '" y1="' + y.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + y.toFixed(1) + '" stroke="var(--line)" stroke-width="0.7" stroke-dasharray="3 3" opacity="0.7"/>';
    svg += '<text x="' + (padL - 6) + '" y="' + (y + 3).toFixed(1) + '" class="behavior-poly-y">' + (gv > 0 ? '+' : '') + gv + '</text>';
  }
  // The X axis line + the turn count.
  const n = Math.max(1, series.length);
  svg += '<text x="' + (padL + plotW / 2) + '" y="' + (H - 6) + '" class="behavior-poly-x">turns: ' + n + ' (oldest → newest)</text>';

  // The x position of turn i. When there is ONE point (no history yet),
  // the axis dots are SPREAD across the plot (the Operator's 08-12 fix:
  // all 8 axis dots visible — never stacked on one spot).
  const xFor = (i) => padL + (n === 1 ? ((i + 0.5) / Math.max(1, axes.length)) * plotW
                                      : (i / (n - 1)) * plotW);

  // One polyline per axis across the turns.
  for (let ai = 0; ai < axes.length; ai++) {
    const axis = axes[ai];
    const pts = [];
    for (let i = 0; i < n; i++) {
      const x = xFor(i);
      const v = series[i].vector ? parseFloat(series[i].vector[axis] || 0) : 0;
      pts.push(x.toFixed(1) + ',' + yFor(v).toFixed(1));
    }
    if (n > 1) {
      svg += '<polyline points="' + pts.join(' ') + '" fill="none" stroke="' + colors[axis] + '" stroke-width="1.8" opacity="0.95"/>';
    }
    // The endpoint marker + label (EVERY axis gets a dot — the fix for
    // the "only 3 dots" report: with one point the dots no longer stack).
    const lx = xFor(n - 1);
    const lv = series[n - 1].vector ? parseFloat(series[n - 1].vector[axis] || 0) : 0;
    svg += '<circle cx="' + lx.toFixed(1) + '" cy="' + yFor(lv).toFixed(1) + '" r="3.5" fill="' + colors[axis] + '"/>';
    svg += '<text x="' + lx.toFixed(1) + '" y="' + (yFor(lv) - 7).toFixed(1) + '" class="behavior-poly-dotlabel" fill="' + colors[axis] + '">' + axis + '</text>';
  }

  // The legend: axis name + its line color.
  let ly = padT + 6;
  for (const axis of axes) {
    svg += '<text x="' + (W - padR - 12) + '" y="' + ly + '" class="behavior-poly-legend" fill="' + colors[axis] + '">' + axis + '</text>';
    ly += 12;
  }

  svg += '</svg>';
  wrap.innerHTML = svg;
}

// The axis bars: each axis is a THREE-LINE block (the Operator's 08-11 spec):
//   Line 1: left label > center label > right label (band names)
//   Line 2: the fillable bar on the -1..+1 continuum
//   Line 3: empty
function renderBars(axes, wheel, vec) {
  const el = $('behavior-bars');
  if (!el) return;
  let html = '';
  for (const axis of axes) {
    const names = wheel[axis] || ['', '', ''];
    const v = parseFloat(vec[axis] || 0);
    let left, width;
    if (v >= 0) { left = 50; width = v * 50; }
    else { left = 50 + v * 50; width = -v * 50; }
    // Line 1: the three band labels spread across the track width.
    html +=
      '<div class="behavior-bar-block">' +
        '<div class="behavior-bar-labels">' +
          '<span class="behavior-bar-lbl-left">' + names[0] + '</span>' +
          '<span class="behavior-bar-lbl-center">' + names[1] + '</span>' +
          '<span class="behavior-bar-lbl-right">' + names[2] + '</span>' +
        '</div>' +
        // Line 2: the fillable bar (no side labels — the band names
        // live ABOVE the bar on line 1).
        '<div class="behavior-bar-row">' +
          '<div class="behavior-bar-track">' +
            '<div class="behavior-bar-mid" style="left:50%"></div>' +
            '<div class="behavior-bar-fill" style="left:' + left.toFixed(1) + '%;width:' + Math.max(0, width).toFixed(1) + '%"></div>' +
          '</div>' +
        '</div>' +
        // Line 3: empty spacing.
        '<div class="behavior-bar-gap"></div>' +
      '</div>';
  }
  el.innerHTML = html;
}

// The 24×24 Emotional Array TABLE: row/col 0 = Neutral; 1..24 = the
// emotions in wheel order (low → med → high per axis). The live vector's
// active pair highlights its cell.
function renderArray(d, vec) {
  const el = $('behavior-array');
  if (!el) return;
  const order = d.emotion_order || [];
  const table = d.table || [];
  const highlight = d.highlight || [];
  const hl = {};
  for (const cell of highlight) {
    hl[cell[0] + ',' + cell[1]] = true;
    hl[cell[1] + ',' + cell[0]] = true;  // symmetric
  }
  if (!table.length) {
    el.innerHTML = '<div class="behavior-array-card"><div class="behavior-array-head">Emotional Array</div>' +
      '<div class="behavior-array-neutral">table unavailable</div></div>';
    return;
  }
  const head = [];
  for (let j = 1; j <= 24; j++) {
    head.push(order[j - 1] || ('' + j));
  }
  let html = '<div class="behavior-array-card">' +
    '<div class="behavior-array-head">Emotional Table</div>' +
    '<div class="behavior-table-scroll"><table class="behavior-table">' +
    // The header: a blank corner cell for the row-label column, then the
    // 0/Neutral column, then the 24 emotion columns — matching the data
    // rows (row label + 25 cells) exactly.
    '<tr><th class="behavior-th-corner"></th><th class="behavior-th-corner">0<br>Neutral</th>' +
    head.map((h, j) => '<th>' + (j + 1) + '<br>' + h + '</th>').join('') +
    '</tr>';
  for (let i = 0; i <= 24; i++) {
    const rowName = (i === 0) ? 'Neutral' : (order[i - 1] || ('' + i));
    html += '<tr><th>' + i + '<br>' + rowName + '</th>';
    for (let j = 0; j <= 24; j++) {
      if (j === 0) { html += '<td class="behavior-td-0">' + (table[i] ? table[i][0] : '') + '</td>'; continue; }
      const key = i + ',' + j;
      const cls = hl[key] ? 'behavior-td-hl' : (i === j ? 'behavior-td-diag' : '');
      const cell = (table[i] && table[i][j]) ? table[i][j] : '';
      const rowN = (i === 0) ? 'Neutral' : (order[i - 1] || '');
      html += '<td class="' + cls + '" title="' + rowN + ' + ' + (order[j - 1] || '') + ' → ' + cell + '">' + cell + '</td>';
    }
    html += '</tr>';
  }
  html += '</table></div></div>';
  el.innerHTML = html;
}

function bandLabel(v, names) {
  if (v <= -0.33) return names[0] || 'low';
  if (v >= 0.33) return names[2] || 'high';
  return names[1] || 'neutral';
}

// Auto-refresh is handled centrally by index.js (_scheduleRefresh) —
// the active tab re-fetches on its cadence. Keeping a second interval
// here caused double-refresh (the 08-12 UX consolidation).
