// The agent list collapses to a strip, and the control that reopens it stays in the same corner.
//
// The header carries a control rather than a count: a number is read once, a control is used every session. The
// collapsed state is remembered, because a person who narrows the list on a small window wants it narrowed next time.
(function () {
  var KEY = "athena.rail.collapsed";

  function apply(button, body, collapsed) {
    body.classList.toggle("rail-collapsed", collapsed);
    button.setAttribute("aria-expanded", collapsed ? "false" : "true");
    button.setAttribute("aria-label", collapsed ? "show agent list" : "hide agent list");
    button.title = collapsed ? "show agent list" : "hide agent list";
  }

  function wire() {
    var button = document.getElementById("rail-toggle");
    var body = document.getElementById("page-body");
    if (!button || !body) return;
    var collapsed = false;
    try { collapsed = localStorage.getItem(KEY) === "1"; } catch (e) { }
    apply(button, body, collapsed);
    button.addEventListener("click", function () {
      var now = !body.classList.contains("rail-collapsed");
      apply(button, body, now);
      try { localStorage.setItem(KEY, now ? "1" : "0"); } catch (e) { }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
  else wire();
})();
