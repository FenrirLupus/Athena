// The notification centre: what the architecture has been doing, under the bell.
//
// Read state and deletions live in localStorage, because they are the person's decisions about their own feed — not
// the server's. Swipe left marks read, swipe right deletes; the bell keeps a 12.5px ring with a red dot while
// anything is unread.
(function () {
  var READ = "athena.notif.read", GONE = "athena.notif.deleted";
  var bell, panel, list;

  function load(key) { try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { return []; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { } }

  function markRead(id) {
    var read = load(READ);
    if (read.indexOf(id) < 0) { read.push(id); save(READ, read); }
    var node = list.querySelector('[data-id="' + id + '"]');
    if (node) { node.classList.remove("notif-unread"); node.classList.add("notif-read"); }
    countUnread();
  }
  function remove(id) {
    var gone = load(GONE);
    if (gone.indexOf(id) < 0) { gone.push(id); save(GONE, gone); }
    var node = list.querySelector('[data-id="' + id + '"]');
    if (node) { node.classList.add("notif-gone"); setTimeout(function () { node.remove(); countUnread(); }, 190); }
  }
  function countUnread() {
    var n = list.querySelectorAll(".notif-unread").length;
    bell.dataset.unread = n ? String(n) : "";
    bell.setAttribute("aria-label", n ? "notifications, " + n + " unread" : "notifications");
  }

  var lanes = null;
  var avatarOK = Object.create(null);   // channel -> true once its avatar has loaded in this session   // the channels that actually exist; a name that is not one has no avatar to fetch
  async function loadLanes() {
    try {
      var data = await (await fetch("/agents", { headers: { accept: "application/json" } })).json();
      lanes = (data.agents || []).map(function (a) { return String(a.channel || a.name || "").toLowerCase(); })
        .filter(Boolean);
    } catch (e) { lanes = []; }
  }

  function avatar(name) {
    var box = document.createElement("span");
    box.className = "notif-avatar";
    var initials = (name || "?").replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase() || "?";
    box.textContent = initials;
    if (!name) return box;
    if (lanes && lanes.indexOf(String(name).toLowerCase()) < 0) return box;   // not an agent: initials
    if (avatarOK[name] === false) return box;                                 // known to have none
    // the agent's stored avatar; listeners are attached before the request so a cached image still clears the initials
    var real = document.createElement("img");
    real.alt = name;
    real.addEventListener("error", function () {
      real.remove();
      avatarOK[name] = false;              // cached miss: stop asking every 15 seconds
      box.textContent = initials;          // the fallback is a name, not a hole
    });
    real.addEventListener("load", function () {
      avatarOK[name] = true;
      box.replaceChildren(real);      // drop the initials text, keep the image: textContent="" removes both
    });
    real.src = "/agents/" + encodeURIComponent(name) + "/avatar";
    if (avatarOK[name]) box.textContent = "";      // already known: no flash of initials on a re-render
    box.appendChild(real);
    return box;
  }

  function replyCard(agent, text, at, id) {
    var li = el("li", "notif-row notif-reply");
    li.dataset.id = id;
    li.dataset.type = "reply";
    if (agent) li.appendChild(avatar(agent));      // a ledger event has no author to show a face for
    var body = el("div", "notif-copy");
    body.appendChild(el("span", "notif-title", (agent || "agent") + " replied" + (at ? " · " + at : "")));
    body.appendChild(el("span", "notif-msg", text || ""));
    li.appendChild(body);
    li.classList.add(load(READ).indexOf(id) >= 0 ? "notif-read" : "notif-unread");
    wireSwipe(li, id);
    return li;
  }

  function row(kind, title, detail, id) {
    var li = el("li", "notif-row notif-" + kind);
    li.dataset.id = id;
    li.dataset.type = kind;
    li.appendChild(el("span", "notif-kind", kind));
    li.appendChild(el("span", "notif-title", title));
    if (detail) li.appendChild(el("span", "notif-detail", detail));
    li.classList.add(load(READ).indexOf(id) >= 0 ? "notif-read" : "notif-unread");
    wireSwipe(li, id);
    return li;
  }

  // a swipe is a pointer drag past 40px: left marks read, right deletes
  function wireSwipe(li, id) {
    var startX = 0, dx = 0, active = false;
    li.addEventListener("pointerdown", function (event) {
      active = true; startX = event.clientX; dx = 0;
      li.classList.add("notif-swiping");
      li.setPointerCapture && li.setPointerCapture(event.pointerId);
    });
    li.addEventListener("pointermove", function (event) {
      if (!active) return;
      dx = event.clientX - startX;
      li.style.setProperty("--swipe", Math.max(-80, Math.min(80, dx)) + "px");
    });
    li.addEventListener("pointerup", function () {
      if (!active) return;
      active = false;
      li.classList.remove("notif-swiping");
      li.style.removeProperty("--swipe");
      if (dx <= -40) markRead(id);
      else if (dx >= 40) remove(id);
    });
    li.addEventListener("pointercancel", function () {
      active = false; li.classList.remove("notif-swiping"); li.style.removeProperty("--swipe");
    });
  }

  // Three outcomes, asked in order: did it finish, and how badly did it go?
  //   critical — did not finish, major issue      caution — did not finish, has an issue
  //   warning  — finished, slow or minor issue
  function classify(item) {
    if (item.pending) return "caution";
    var level = (item.level || "").toLowerCase();
    if (item.failed || level === "bad" || level === "deny" || level === "refused" || level === "critical") return "critical";
    if (item.slow || level === "warn" || level === "warning") return "warning";
    return "warning";
  }

  function label(kind) {
    return kind === "critical" ? "critical" : kind === "caution" ? "caution" : "warning";
  }

  async function loadFeed() {
    if (!list) return;
    if (lanes === null) await loadLanes();
    var read = load(READ), gone = load(GONE);
    var pending = [], events = [];
    try {
      pending = ((await (await fetch("/permissions", { headers: { accept: "application/json" } })).json()).pending) || [];
    } catch (e) { }
    try {
      events = ((await (await fetch("/security/events?limit=12", { headers: { accept: "application/json" } })).json()).rows) || [];
    } catch (e) { }

    list.replaceChildren();
    var shown = 0;
    pending.forEach(function (ask, i) {
      var id = "ask:" + (ask.id || i);
      if (gone.indexOf(id) >= 0) return;
      var kind = classify({ pending: true });
      list.appendChild(row(kind, ask.summary || ask.kind || "waiting on a person", ask.agent || "", id)); shown++;
    });
    events.slice().reverse().forEach(function (ev, i) {
      var id = "ev:" + (ev.id || ev.at_epoch || i);
      if (gone.indexOf(id) >= 0) return;
      var kind = classify({ level: ev.level, failed: ev.failed, slow: ev.slow });
      list.appendChild(row(kind, ev.kind || "event", (ev.path || ev.text || "") + (ev.ms ? " · " + ev.ms + "ms" : ""), id)); shown++;
    });
    var replies = [];
    try {
      var laneList = (await (await fetch("/agents", { headers: { accept: "application/json" } })).json()).agents || [];
      for (var i = 0; i < laneList.length; i++) {
        var name = laneList[i].channel || laneList[i].name || laneList[i].id;
        try {
          var h = await (await fetch("/agents/" + encodeURIComponent(name) + "/history?days=1",
                                     { headers: { accept: "application/json" } })).json();
          var msgs = [];
          (h.days || []).forEach(function (d) { (d.messages || []).forEach(function (m) { msgs.push(m); }); });
          if (!msgs.length) msgs = h.messages || h.rows || h.turns || [];
          msgs.forEach(function (m, j) {
            var role = String(m.role || m.from || "").toLowerCase();
            var text = m.content || m.text || m.message || "";
            if (role.indexOf("assistant") !== 0 && role.indexOf("agent") !== 0) return;
            if (!text) return;
            var stamp = m.ts ? new Date(m.ts).toTimeString().slice(0, 5) : "";
            replies.push({ agent: name, text: String(text), at: stamp, id: "rp:" + name + ":" + (m.id || j) });
          });
        } catch (e) { }
      }
    } catch (e) { }
    replies.slice(-8).reverse().forEach(function (r) {
      if (gone.indexOf(r.id) >= 0) return;
      list.appendChild(replyCard(r.agent, r.text, r.at, r.id)); shown++;
    });

    if (!shown) list.appendChild(el("li", "notif-empty muted small", "nothing yet"));
    countUnread();
  }

  function open() { panel.hidden = false; bell.setAttribute("aria-expanded", "true"); loadFeed(); }
  function close() { panel.hidden = true; bell.setAttribute("aria-expanded", "false"); }

  function wire() {
    bell = document.getElementById("bell");
    panel = document.getElementById("notif-panel");
    list = document.getElementById("notif-list");
    if (!bell || !panel) return;
    bell.addEventListener("click", function (event) { event.stopPropagation(); panel.hidden ? open() : close(); });
    var closer = document.getElementById("notif-close");
    if (closer) closer.addEventListener("click", close);

    // every visible notification, marked or cleared in one action
    var readAll = document.getElementById("notif-read-all");
    var clearAll = document.getElementById("notif-clear-all");
    if (readAll) readAll.addEventListener("click", function () {
      var ids = [].map.call(list.querySelectorAll(".notif-row"), function (n) { return n.dataset.id; });
      var seen = load(READ);
      ids.forEach(function (id) { if (seen.indexOf(id) < 0) seen.push(id); });
      save(READ, seen);
      loadFeed();
    });
    if (clearAll) clearAll.addEventListener("click", function () {
      var ids = [].map.call(list.querySelectorAll(".notif-row"), function (n) { return n.dataset.id; });
      var gone = load(GONE);
      ids.forEach(function (id) { if (gone.indexOf(id) < 0) gone.push(id); });
      save(GONE, gone);
      loadFeed();
    });
    document.addEventListener("click", function (event) {
      if (!panel.hidden && !panel.contains(event.target) && event.target !== bell) close();
    });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape" && !panel.hidden) close(); });
    setInterval(function () { if (!document.hidden) loadFeed(); }, 15000);
    loadFeed();
  }

  window.AthenaNotif = { open: open, close: close, load: loadFeed, markRead: markRead, remove: remove };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
