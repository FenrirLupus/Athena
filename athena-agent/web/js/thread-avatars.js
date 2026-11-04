// The conversation panel shows who is speaking with their avatar, not just their initial.
//
// The thread template already carries a .msg-avatar cell; this fills it with the agent's stored picture when the
// message is the agent's own, and leaves the person's initials alone otherwise. The thread is rebuilt as messages
// arrive, so an observer decorates new rows as they appear.
(function () {
  var lane = new URLSearchParams(location.search).get("channel") || "default";
  var agentNames = null;      // the names an agent's own messages can carry
  var applied = false;

  function namesFor(payload) {
    var persona = (payload || {}).persona || {};
    var set = [lane, payload && payload.channel, persona.name, persona.first_name,
               persona.name ? String(persona.name).split(" ")[0] : null];
    return set.filter(Boolean).map(function (v) { return String(v).trim().toLowerCase(); });
  }

  var byAuthor = Object.create(null);   // author name as printed -> that agent's lane

  async function loadNames() {
    try {
      var res = await fetch("/agents/" + encodeURIComponent(lane) + "/profile",
                            { headers: { accept: "application/json" } });
      agentNames = namesFor(await res.json());
    } catch (e) { agentNames = [lane.toLowerCase()]; }

    // every lane names itself, and messages from another agent carry "@name"
    try {
      var list = (await (await fetch("/agents", { headers: { accept: "application/json" } })).json()).agents || [];
      list.forEach(function (a) {
        var ch = a.channel || a.name;
        if (!ch) return;
        byAuthor[String(ch).toLowerCase()] = ch;
        byAuthor["@" + String(ch).toLowerCase()] = ch;
      });
      byAuthor["@" + lane.toLowerCase()] = lane;
    } catch (e) { }
  }

  function decorate() {
    if (!agentNames) return;
    var cells = document.querySelectorAll(".msg .msg-avatar:not([data-avatared])");
    Array.prototype.forEach.call(cells, function (cell) {
      var msg = cell.closest(".msg");
      var author = msg && msg.querySelector(".msg-author");
      var who = author ? author.textContent.trim().toLowerCase() : "";
      cell.dataset.avatared = "1";
      if (!author || !who) return;
      // whose message is this? another agent's name wins; the lane's own persona names fall back to the lane
      var owner = byAuthor[who] || (agentNames.indexOf(who) >= 0 ? lane : null);
      if (!owner) return;                       // a person, or an unknown speaker: initials stay
      var img = new Image();
      img.className = "msg-avatar-img";
      img.alt = "";
      img.onload = function () {
        cell.textContent = "";
        cell.appendChild(img);
        cell.classList.add("has-avatar");
      };
      img.onerror = function () { img.remove(); };
      img.src = "/agents/" + encodeURIComponent(owner) + "/avatar";
    });
    applied = true;
  }

  async function start() {
    await loadNames();
    var thread = document.getElementById("thread") || document.body;
    new MutationObserver(decorate).observe(thread, { childList: true, subtree: true });
    decorate();
    setInterval(decorate, 2000);
  }

  document.addEventListener("DOMContentLoaded", start);
  if (document.readyState !== "loading") start();
})();
