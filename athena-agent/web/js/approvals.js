/* approvals.js — the pause, made visible.
 *
 * One manager now answers every question the architecture puts to a person: a path outside an agent's scope, an
 * install the agent staged, a system package it asked for. So this file no longer knows what an approval *is* —
 * it renders whatever `/permissions` returns and sends back exactly one of the actions the ask offered.
 *
 * That matters for safety: the buttons come from `ask.actions`, so a card can never offer "uninstall" for something
 * that only asked to be installed, and it can never answer with an action the manager did not name.
 *
 * It polls rather than streams, deliberately: a permission is rare, and one request every three seconds costs
 * nothing next to a websocket that has to stay alive on a screen nobody is watching. A hidden tab does not poll.
 */
(() => {
  const POLL_MS = 3000;
  const ENDPOINT = "/permissions";

  let busy = false;
  let shown = null;
  let previousFocus = null;          // the ask currently on screen

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  function close() {
    const veil = document.querySelector(".apv-veil");
    if (veil) veil.remove();
    shown = null;
    if (previousFocus && document.contains(previousFocus)) {
      try { previousFocus.focus(); } catch (_) { /* the element may be gone */ }
    }
    previousFocus = null;
  }

  function trap(event) {
    if (event.key !== "Tab") return;
    const focusable = [...document.querySelectorAll(".apv-veil button:not([disabled])")];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function say(message) {
    if (typeof toast === "function") { try { toast(message); return; } catch (_) { /* fall through */ } }
    // neither a global `toast()` nor a `#toasts` element exists on the current pages, so every outcome — allowed,
    // denied, installed, "allowed but it failed" — was silently discarded. The host is created on demand, and the
    // node carries inline styles so it is readable before the redesign's stylesheet lands.
    let host = document.getElementById("toasts");
    if (!host) {
      host = el("div", "toasts");
      host.id = "toasts";
      host.style.cssText = "position:fixed;right:16px;bottom:16px;display:flex;flex-direction:column;gap:8px;" +
                           "z-index:60;pointer-events:none";
      document.body.appendChild(host);
    }
    const node = el("div", "toast", message);
    node.style.cssText = "background:#161a20;color:#e6e8eb;border:1px solid #2a3038;border-left:3px solid #7c8cff;" +
                         "padding:8px 12px;border-radius:6px;font:13px/1.4 ui-monospace,monospace;" +
                         "box-shadow:0 6px 20px rgba(0,0,0,.45);max-width:min(60ch,80vw)";
    host.appendChild(node);
    setTimeout(() => node.remove(), 4000);
  }

  async function answer(ask, action, scope) {
    if (busy) return;
    busy = true;
    const buttons = document.querySelectorAll(".apv-veil button");
    buttons.forEach((b) => { b.disabled = true; });
    try {
      const res = await fetch(`${ENDPOINT}/${encodeURIComponent(ask.id)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ decision: action, scope: scope || "once" }),
        // a deny fired from `beforeunload` races the navigation, and a plain fetch is cancelled with the page —
        // the agent then waited out the full timeout believing the user was unavailable
        keepalive: true,
      });
      if (res.status === 404 || res.status === 409) {
        // answered elsewhere, expired, or no longer pending — nothing was granted, and the card is dead
        close();
      } else if (!res.ok) {
        buttons.forEach((b) => { b.disabled = false; });
        say(`could not answer: ${res.status}`);
      } else {
        const body = await res.json().catch(() => ({}));
        close();
        if (body.result && body.result.ok === false) {
          say(`allowed, but it failed: ${body.result.error || "see /security"}`);
        } else if (body.decision === "installed") {
          say(`installed ${body.name || body.summary}`);
        } else if (body.decision === "uninstalled") {
          say(`uninstalled ${body.name || body.summary}`);
        } else if (body.decision === "allow") {
          say(`allowed${scope && scope !== "once" ? ` (${scope})` : ""}`);
        } else if (body.decision === "deny") {
          say("denied");
        }
      }
    } catch (_) {
      buttons.forEach((b) => { b.disabled = false; });
    } finally {
      busy = false;
      poll();
    }
  }

  const LABELS = {
    allow: "Allow", deny: "Deny", install: "Install", repair: "Repair",
    uninstall: "Uninstall", cancel: "Cancel",
  };
  const CLASSES = {
    install: "apv-perm", repair: "apv-once", uninstall: "apv-session",
    cancel: "apv-deny", deny: "apv-deny", allow: "apv-once",
  };

  function render(ask) {
    close();
    shown = ask;

    const veil = el("div", "apv-veil");
    veil.setAttribute("role", "dialog");
    veil.setAttribute("aria-modal", "true");
    veil.setAttribute("aria-label", `${ask.agent || "an agent"} is asking for permission`);

    const card = el("div", "apv-card");
    card.dataset.id = ask.id || "";
    card.dataset.kind = ask.kind || "";

    const head = el("div", "apv-head");
    head.appendChild(el("span", "apv-dot"));
    head.appendChild(el("span", "apv-kind", ask.kind));
    head.appendChild(el("span", "apv-agent", ask.agent || "system"));
    if (ask.blocking && ask.expires_in != null) {
      const clock = el("span", "apv-clock", `${Math.round(ask.expires_in)}s`);
      // the deadline, not a number painted once: the clock is re-read on every poll so it counts down
      clock.dataset.deadline = String(Date.now() + Number(ask.expires_in) * 1000);
      head.appendChild(clock);
    }
    card.appendChild(head);

    // the ask says what it is; this only lays it out
    card.appendChild(el("div", "apv-title", ask.summary || "(no summary)"));

    // who is asking, and why — stated, not left to be inferred from the wording
    const fields = el("dl", "apv-fields");
    const field = (label, value) => {
      if (value === undefined || value === null || value === "") return;
      fields.appendChild(el("dt", "apv-field-key", label || ""));
      const node = el("dd", "apv-field-value", String(value));
      if (!label) node.classList.add("apv-field-wide");
      fields.appendChild(node);
    };
    field("agent", ask.agent || "unknown");
    field("reason", ask.reason || "no reason given");
    field("state", ask.state);
    for (const line of (ask.detail || [])) field("", line);
    if (ask.packages && ask.packages.length) field("packages", ask.packages.join(", "));
    if (ask.targets && ask.targets.length && ask.kind !== "path") field("target", ask.targets.join(", "));
    card.appendChild(fields);

    if (ask.warning) card.appendChild(el("p", "apv-warning", ask.warning));

    const actions = el("div", "apv-actions");
    const available = (ask.actions && ask.actions.length) ? ask.actions : ["allow", "deny"];

    // a path ask has three ways to say yes, because "once" and "always this folder" are different decisions;
    // an install has one action and no scope at all
    if (available.includes("allow")) {
      for (const scope of ["once", "session", "permanent"]) {
        const label = scope === "permanent" ? "Always" : (scope === "session" ? "Session" : "Once");
        const button = el("button", `apv-btn apv-${scope === "permanent" ? "perm" : scope}`, label);
        button.onclick = () => answer(ask, "allow", scope);
        actions.appendChild(button);
      }
    }
    for (const action of available) {
      if (action === "allow" || action === "deny") continue;
      const button = el("button", `apv-btn ${CLASSES[action] || "apv-once"}`, LABELS[action] || action);
      button.onclick = () => answer(ask, action, "once");
      actions.appendChild(button);
    }
    if (available.includes("deny")) {
      const deny = el("button", "apv-btn apv-deny", "Deny");
      deny.onclick = () => answer(ask, "deny", "once");
      actions.appendChild(deny);
    }

    // what you may do, in words, before the buttons that do it
    const options = available.includes("allow")
      ? ["allow once", "allow for this session", "always", "deny"]
      : available.map((action) => (LABELS[action] || action).toLowerCase());
    card.appendChild(el("p", "apv-options", `options: ${options.join(" · ")}`));
    card.appendChild(actions);

    veil.appendChild(card);
    previousFocus = document.activeElement;
    document.body.appendChild(veil);
    // role="dialog" aria-modal="true" was a claim, not a behaviour: focus stayed on <body> and Tab walked the page
    // behind the modal, so a decision that blocks an agent's turn could be missed entirely
    const firstButton = card.querySelector("button:not([disabled])");
    if (firstButton) firstButton.focus();
    veil.addEventListener("keydown", trap);
  }

  function tickClock() {
    const clock = document.querySelector(".apv-clock");
    if (!clock || !clock.dataset.deadline) return;
    const left = Number(clock.dataset.deadline) - Date.now();
    if (left <= 0) {
      clock.textContent = "expired";
      if (shown) { const dead = shown; close(); say(`the ask expired unanswered — ${dead.kind || "request"} denied`); }
      return;
    }
    clock.textContent = `${Math.ceil(left / 1000)}s`;
  }

  async function poll() {
    if (document.hidden || busy) return;
    tickClock();
    if (shown) return;
    try {
      const res = await fetch(ENDPOINT, { headers: { accept: "application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      const pending = (data && data.pending) || [];
      // a blocking ask first: an agent is waiting on it
      const ordered = pending.slice().sort((a, b) => Number(!!b.blocking) - Number(!!a.blocking));
      if (ordered.length) render(ordered[0]);
    } catch (_) { /* a dropped poll is not an error worth showing */ }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !shown) return;
    // Escape is a refusal for a blocking ask and a cancel for a queued one
    answer(shown, shown.actions && shown.actions.includes("deny") ? "deny" : "cancel", "once");
  });

  // A permission prompt blocks a turn, so an unanswered one must not outlive the page. An *install request* is the
  // opposite: it is durable on disk and should still be waiting after a reload — cancelling it on unload threw away
  // the agent's work because someone refreshed a tab.
  window.addEventListener("beforeunload", () => {
    if (shown && shown.blocking) answer(shown, "deny", "once");
  });

  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  poll();
})();
