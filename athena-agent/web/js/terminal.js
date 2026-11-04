// The terminal: what the server is doing, in realtime, opened from the footer.
//
// Three sources, one view: the security ledger (refusals, blue and red), the turn measurements (what each model call
// cost and how long it took), and the pending questions. It is read-only — a terminal that could change the box would
// be a shell with an agent's token, which is the opposite of what this is for.
//
// Seven commands, all local to the overlay: /help /clear /filter /since /pause /resume /close. Anything else is sent
// nowhere and says so, because a prompt that silently does nothing is worse than one that refuses.
(function () {
  var POLL_MS = 2000;
  var MAX_LINES = 400;
  var COMMANDS = ["/help", "/clear", "/filter", "/since", "/pause", "/resume", "/close"];

  var state = { open: false, paused: false, cursor: 0, filter: "", lines: [] };
  var overlay, log, input, hint, clock;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function stamp() {
    var d = new Date();
    return d.toTimeString().slice(0, 8);
  }

  function line(kind, text, detail) {
    if (state.filter && (kind + " " + text + " " + (detail || "")).toLowerCase().indexOf(state.filter) < 0) return;
    var row = el("div", "term-line term-" + kind);
    row.appendChild(el("span", "term-ts", stamp()));
    row.appendChild(el("span", "term-kind", kind));
    row.appendChild(el("span", "term-text", text));
    if (detail) row.appendChild(el("span", "term-detail", detail));
    log.appendChild(row);
    state.lines.push(row);
    while (state.lines.length > MAX_LINES) state.lines.shift().remove();
    log.scrollTop = log.scrollHeight;
  }

  function severityOf(kind) {
    if (/403|401|refus|tamper|red/.test(kind)) return "bad";
    if (/warn|429|stale/.test(kind)) return "warn";
    return "info";
  }

  async function poll() {
    if (state.paused || !state.open || document.hidden) return;
    try {
      var res = await fetch("/security/events?since=" + encodeURIComponent(state.cursor), { headers: { accept: "application/json" } });
      if (!res.ok) return;
      var body = await res.json();
      (body.events || []).forEach(function (row) {
        line(severityOf(String(row.kind || "")), String(row.kind || "event"), [row.team, row.path || row.detail].filter(Boolean).join(" · "));
      });
      if (body.cursor) state.cursor = body.cursor;
    } catch (err) { /* a dropped poll is not an event */ }
  }

  function command(text) {
    var parts = text.trim().split(/\s+/);
    var verb = parts[0].toLowerCase();
    if (!verb) return;
    if (verb === "/help") {
      line("info", "commands", COMMANDS.join(" "));
    } else if (verb === "/clear") {
      log.replaceChildren(); state.lines = [];
    } else if (verb === "/filter") {
      state.filter = parts.slice(1).join(" ").toLowerCase();
      line("info", "filter", state.filter || "(cleared)");
    } else if (verb === "/since") {
      var at = Number(parts[1]);
      if (isFinite(at) && at >= 0) { state.cursor = at; line("info", "since", String(at)); }
      else line("warn", "/since needs a unix timestamp");
    } else if (verb === "/pause") {
      state.paused = true; line("info", "paused");
    } else if (verb === "/resume") {
      state.paused = false; line("info", "resumed"); poll();
    } else if (verb === "/close") {
      close();
    } else {
      line("warn", "unknown command", verb + " — try /help");
    }
  }

  function suggest() {
    var value = input.value.trim();
    if (!value.startsWith("/")) { hint.textContent = ""; return; }
    var match = COMMANDS.filter(function (c) { return c.startsWith(value); });
    hint.textContent = match.length === 1 ? match[0] : match.join("  ");
    if (match.length === 1 && value !== match[0]) hint.dataset.complete = match[0];
    else delete hint.dataset.complete;
  }

  function onKey(event) {
    if (!state.open) return;
    if (event.key === "Escape") { close(); return; }
    if (event.key === "Tab" && hint.dataset.complete) {
      event.preventDefault(); input.value = hint.dataset.complete; suggest(); return;
    }
    if (event.key === "Enter") {
      var text = input.value; input.value = ""; hint.textContent = "";
      if (!text.trim()) return;
      command(text);
    }
  }

  function build() {
    overlay = el("div", "term-veil");
    overlay.id = "terminal";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "server terminal");
    overlay.hidden = true;

    var sheet = el("div", "term-sheet");
    var head = el("div", "term-head");
    head.appendChild(el("span", "term-title", "server · realtime"));
    clock = el("span", "term-clock", "");
    head.appendChild(clock);
    var closeBtn = el("button", "term-close", "×");
    closeBtn.type = "button";
    closeBtn.addEventListener("click", close);
    head.appendChild(closeBtn);

    log = el("div", "term-log");
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");

    hint = el("div", "term-hint");
    input = el("input", "term-input");
    input.type = "text";
    input.placeholder = "/ for commands — Tab completes, Esc closes";
    input.setAttribute("aria-label", "command");
    input.addEventListener("input", suggest);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === "Tab") onKey(event);
      else if (event.key === "Escape") onKey(event);
    });

    var foot = el("div", "term-foot");
    foot.appendChild(input);
    foot.appendChild(hint);

    sheet.appendChild(head);
    sheet.appendChild(log);
    sheet.appendChild(foot);
    overlay.appendChild(sheet);
    document.body.appendChild(overlay);
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && state.open) close(); });
  }

  function open() {
    if (!overlay) build();
    state.open = true;
    overlay.hidden = false;
    line("info", "terminal opened", "read-only · " + COMMANDS.length + " commands");
    input.focus();
    poll();
  }

  function close() {
    if (!overlay) return;
    state.open = false;
    overlay.hidden = true;
  }

  function wire() {
    var button = document.getElementById("terminal-open");
    if (button) button.addEventListener("click", open);
    setInterval(function () {
      if (state.open && clock) clock.textContent = stamp();
    }, 1000);
    setInterval(poll, POLL_MS);
  }

  window.AthenaTerminal = { open: open, close: close };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
