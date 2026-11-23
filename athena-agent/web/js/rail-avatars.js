// The agent list shows each agent's own avatar, and keeps showing it when the list re-renders.
//
// The rail is rebuilt on every poll, so a one-shot decoration disappears at the next refresh — which reads as
// "sometimes there, sometimes not". Two things fix that: a cache of which agents have an avatar (so the picture is
// applied the moment a row appears, with no round trip), and an observer on the list that decorates each new row.
(function () {
  var hasAvatar = Object.create(null);   // channel -> true once its avatar has loaded

  function url(name) { return "/agents/" + encodeURIComponent(name) + "/avatar"; }

  function paint(dot, name) {
    dot.style.backgroundImage = "url(" + url(name) + ")";
    dot.style.backgroundSize = "cover";
    dot.style.backgroundPosition = "center";
    dot.textContent = "";
    dot.classList.add("has-avatar");
  }

  function decorate() {
    var items = document.querySelectorAll(".rail-item[data-channel]");
    Array.prototype.forEach.call(items, function (item) {
      var dot = item.querySelector(".rail-dot");
      if (!dot) return;
      var name = item.dataset.channel;
      if (!name) return;
      if (hasAvatar[name]) { paint(dot, name); return; }
      if (dot.dataset.probed === name) return;
      dot.dataset.probed = name;
      var probe = new Image();
      probe.onload = function () {
        hasAvatar[name] = true;
        var fresh = document.querySelector('.rail-item[data-channel="' + name + '"] .rail-dot');
        if (fresh) paint(fresh, name);
      };
      probe.src = url(name);
    });
  }

  function watch() {
    var list = document.getElementById("channel-list");
    if (!list) { setTimeout(watch, 300); return; }
    new MutationObserver(decorate).observe(list, { childList: true, subtree: true });
    decorate();
  }

  document.addEventListener("DOMContentLoaded", watch);
  if (document.readyState !== "loading") watch();
  setInterval(decorate, 2000);   // a safety net for rows added without a mutation (recycled nodes)
})();
