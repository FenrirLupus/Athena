// Theme: the attribute is set before the first stylesheet applies, so there is no flash of the wrong theme.
// The choice lives in localStorage only — no cookie, no server round trip, nothing to keep in sync.
(function () {
  var stored = null;
  try { stored = localStorage.getItem("athena.theme"); } catch (e) { /* private mode */ }
  var theme = stored === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;

  function apply(next) {
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("athena.theme", next); } catch (e) { /* ignore */ }
    var button = document.getElementById("theme-toggle");
    if (button) button.setAttribute("aria-checked", next === "light" ? "true" : "false");
  }

  window.AthenaTheme = {
    get: function () { return document.documentElement.dataset.theme || "dark"; },
    toggle: function () { apply(window.AthenaTheme.get() === "light" ? "dark" : "light"); },
    apply: apply
  };

  document.addEventListener("DOMContentLoaded", function () {
    var button = document.getElementById("theme-toggle");
    if (button) {
      button.setAttribute("aria-checked", window.AthenaTheme.get() === "light" ? "true" : "false");
      button.addEventListener("click", window.AthenaTheme.toggle);
    }
  });
})();
