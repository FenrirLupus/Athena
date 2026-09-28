loadProfiles();
loadSessionDropdown();
loadChatHistory();
loadFooter();
// THE STARTUP GATE (the Operator's 08-12 spec): the page is BLOCKED by
// the frosted startup overlay until /ready says the boot systems are
// done. Poll every 500ms; show the CURRENT SYSTEM being initialized
// (from /ready's layers: server → mcp → runtime), a smooth progress bar,
// and a real elapsed/remaining ETA.
(function startupGate() {
  const ov = $('startup-overlay');
  const bar = $('startup-bar');
  const eta = $('startup-eta');
  const status = $('startup-status');
  const t0 = Date.now();
  const EST_TOTAL_MS = 5000;        // estimated full boot (config+profiles+loops)
  let done = false;
  let lastPct = 4;                  // start the bar visible + moving

  const LAYER_LABELS = {
    server:   {starting: 'Starting the HTTP server…',        ready: 'HTTP server up ✓'},
    mcp:      {starting: 'Mounting the MCP interface…',      ready: 'MCP mounted ✓'},
    runtime:  {starting: 'Building the runtime…',            ready: 'Runtime running ✓'},
  };
  const LAYER_ORDER = ['server', 'mcp', 'runtime'];

  function layerStatus(d) {
    // The first layer still starting = the current system; all ready = done.
    // Use the layer's DETAIL string (the gradual per-system progress —
    // "system profiles", "conversation loop", "tool registry", ...) when
    // present, else the static label.
    const layers = (d && d.layers) || {};
    for (const name of LAYER_ORDER) {
      const l = layers[name] || {};
      if (l.state === 'starting') {
        return l.detail || (LAYER_LABELS[name] || {}).starting || 'Initializing systems…';
      }
    }
    // All three ready — but boot may still be settling.
    for (const name of LAYER_ORDER) {
      const l = layers[name] || {};
      if (l.state !== 'ready') return 'Initializing systems…';
    }
    return 'All systems online ✓';
  }

  function frame(d, el) {
    // PROGRESS: drive toward the ETA curve; a ready boot jumps to 100.
    const prog = Math.min(96, Math.max(lastPct + 3, Math.round(el / EST_TOTAL_MS * 100)));
    lastPct = prog;
    if (d && d.ready) {
      lastPct = 100;
    }
    if (bar) bar.style.width = lastPct + '%';
    // STATUS: the current system being initialized.
    if (status) {
      status.textContent = d && d.ready ? 'All systems online ✓' : layerStatus(d);
    }
    // ETA: real elapsed + remaining estimate.
    if (eta) {
      const sec = Math.round(el / 1000);
      const remain = d && d.ready ? 0
        : Math.max(0, Math.ceil((EST_TOTAL_MS - el) / 1000));
      eta.textContent = d && d.ready
        ? 'Online in ' + sec + 's'
        : 'Elapsed: ' + sec + 's · Estimated total: ~' + Math.ceil(EST_TOTAL_MS / 1000) + 's';
    }
  }

  const timer = setInterval(async () => {
    const el = Date.now() - t0;
    try {
      const r = await fetch('/ready', {cache: 'no-store'});
      const d = await r.json();
      if (d && d.ready) {
        done = true;
        clearInterval(timer);
        frame(d, el);
        // brief 100% hold so the user SEES the bar complete, then clear
        setTimeout(() => { ov.classList.add('hidden'); }, 250);
        return;
      }
      frame(d, el);
    } catch (e) { frame(null, el); /* server mid-boot — keep waiting */ }
  }, 500);
  // A safety: never block forever — after 45s force-ready (the boot
  // may have finished without the flag, e.g. a cold import path).
  setTimeout(() => {
    if (!done) {
      done = true;
      clearInterval(timer);
      if (bar) bar.style.width = '100%';
      if (status) status.textContent = 'All systems online ✓';
      if (eta) eta.textContent = 'Online in ' + Math.round((Date.now() - t0) / 1000) + 's';
      setTimeout(() => { ov.classList.add('hidden'); }, 250);
    }
  }, 45000);
})();
// The footer's token meter refreshes every 30s (cheap health poll).
setInterval(loadFooter, 30000);
// THE FAST STATUS POLL (the Operator's 08-12 spec): a lightweight
// /health probe EVERY SECOND so Server/Runtime flip to Offline the
// moment Athena stops, and the disconnect overlay appears after 5s.
// The heavy loadFooter (30s) still fills the token meter; this only
// touches the offline state.
setInterval(async () => {
  try {
    const r = await fetch('/health', {cache: 'no-store'});
    if (!r.ok) { setOffline(true); return; }
    setOffline(false);
  } catch (e) { setOffline(true); }
}, 1000);
