// The agent's accent is the site's accent while you are looking at that agent.
//
// Read from the profile that is in view: the ?channel= of the chat page, or the lane the agents page is showing.
// --accent and --accent-line are set on the root element, so every rule that already uses them follows without
// a single selector being rewritten.
(function () {
  var applied = null;

  var KEY = "athena.lane";

  function stored() {
    try { return localStorage.getItem(KEY) || ""; } catch (e) { return ""; }
  }
  function remember(lane) {
    try { localStorage.setItem(KEY, lane); } catch (e) { }
  }

  // the agent you last chose wins everywhere: the rail on chat, the tab on agents, then the URL, then the
  // remembered choice — so picking kali on chat keeps kali's accent on the settings page
  function currentLane() {
    var active = document.querySelector(".rail-item.active, .page-tab.active, .rail-item[aria-current='true']");
    if (active && active.dataset.channel) { remember(active.dataset.channel); return active.dataset.channel; }
    var fromUrl = new URLSearchParams(location.search).get("channel");
    if (fromUrl) { remember(fromUrl); return fromUrl; }
    return stored() || "default";
  }

  function withAlpha(colour, alpha) {
    var value = String(colour || "").trim();
    var rgba = /rgba?\(([^)]+)\)/.exec(value);
    if (rgba) {
      var parts = rgba[1].split(",").map(function (v) { return v.trim(); });
      return "rgba(" + parts[0] + ", " + parts[1] + ", " + parts[2] + ", " + alpha + ")";
    }
    var hex = /^#([0-9a-f]{6})$/i.exec(value);
    if (hex) {
      var n = parseInt(hex[1], 16);
      return "rgba(" + ((n >> 16) & 255) + ", " + ((n >> 8) & 255) + ", " + (n & 255) + ", " + alpha + ")";
    }
    return value;
  }

  async function apply() {
    var lane = currentLane();
    if (lane === applied) return;
    try {
      var prof = await (await fetch("/agents/" + encodeURIComponent(lane) + "/profile",
                                    { headers: { accept: "application/json" } })).json();
      var accent = ((prof.persona || {}).accent || "").trim();
      if (!accent) return;
      var root = document.documentElement;
      root.style.setProperty("--accent", accent);
      root.style.setProperty("--accent-line", withAlpha(accent, 0.45));
      root.dataset.accentLane = lane;
      applied = lane;
    } catch (e) { }
  }

  setInterval(apply, 3000);
  document.addEventListener("click", function (event) {
    var pick = event.target.closest && event.target.closest(".rail-item[data-channel], .page-tab[data-channel]");
    if (pick && pick.dataset.channel) { remember(pick.dataset.channel); applied = null; }
    setTimeout(apply, 400);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
})();
