// The agent list reads: avatar, profile name, nickname, status — alphabetically, with the built-in default first.
//
// The rail is rebuilt on every poll, so the order and the labels are re-applied after each render rather than set
// once. Personas are fetched once per lane and cached: a rename shows up on the next page load, not every refresh.
(function () {
  var personas = Object.create(null);     // channel -> {name, nickname}

  // the left label is the profile (the directory), the right one is the agent's name inside it
  function profileName(channel) { return (channel || "").trim(); }
  function agentName(channel) {
    var p = personas[channel] || {};
    return (p.name || p.nickname || "").trim();
  }

  async function loadPersonas() {
    try {
      var list = (await (await fetch("/agents", { headers: { accept: "application/json" } })).json()).agents || [];
      await Promise.all(list.map(async function (a) {
        var ch = a.channel || a.name;
        if (!ch || personas[ch]) return;
        try {
          var prof = await (await fetch("/agents/" + encodeURIComponent(ch) + "/profile",
                                        { headers: { accept: "application/json" } })).json();
          var persona = prof.persona || {};
          personas[ch] = { name: persona.name || ch, nickname: persona.nickname || "" };
        } catch (e) { personas[ch] = { name: ch, nickname: "" }; }
      }));
    } catch (e) { }
  }

  var observer = null, busy = false;

  function restyle() {
    if (busy) return;                       // moving nodes fires mutations: re-entering here would livelock
    var list = document.getElementById("channel-list");
    if (!list) return;
    var items = [].slice.call(list.querySelectorAll(".rail-item[data-channel]"));
    if (!items.length) return;
    busy = true;

    // default is the built-in profile: always the first row, whatever the alphabet says
    var ordered = items.slice().sort(function (a, b) {
      var an = a.dataset.channel, bn = b.dataset.channel;
      if (an === "default") return -1;
      if (bn === "default") return 1;
      return profileName(an).localeCompare(profileName(bn), undefined, { sensitivity: "base" });
    });
    // move only when the order is wrong; appending identical nodes is what fed the observer
    var current = [].slice.call(list.children);
    var desired = ordered.map(function (i) { return i.closest("li") || i; });
    if (desired.some(function (node, idx) { return current[idx] !== node; })) {
      if (observer) observer.disconnect();
      desired.forEach(function (node) { list.appendChild(node); });
      if (observer) observer.observe(list, { childList: true });
    }

    ordered.forEach(function (item) {

      var nameEl = item.querySelector(".rail-name");
      var who = profileName(item.dataset.channel);
      if (nameEl && nameEl.textContent !== who) nameEl.textContent = who;

      var nick = agentName(item.dataset.channel);
      // the status is shown in full; the names truncate first, since the status is the one word that must read
      var statusEl = item.querySelector(".rail-status");
      if (statusEl) {
        var full = (statusEl.textContent || "").trim();
        if (statusEl.dataset.full && statusEl.dataset.full !== full) {
          statusEl.textContent = statusEl.dataset.full;     // undo an earlier abbreviation if one is present
        }
        statusEl.dataset.full = full;
        statusEl.title = full;
      }

      var nickEl = item.querySelector(".rail-nick");
      if (!nickEl) {
        nickEl = document.createElement("span");
        nickEl.className = "rail-nick";
        var anchor = nameEl || item.querySelector(".rail-status");
        if (anchor && anchor.parentNode === item) item.insertBefore(nickEl, anchor.nextSibling);
        else item.appendChild(nickEl);
      }
      if (nickEl.textContent !== nick) nickEl.textContent = nick;
      nickEl.hidden = !nick;
    });
    busy = false;
  }

  async function start() {
    await loadPersonas();
    var list = document.getElementById("channel-list");
    if (list) {
      observer = new MutationObserver(function () { restyle(); });
      observer.observe(list, { childList: true });
    }
    restyle();
    setInterval(restyle, 2500);
  }

  document.addEventListener("DOMContentLoaded", start);
  if (document.readyState !== "loading") start();
})();
