// Page specs: the structure each page needs, derived from what the page already contains.
//
// Built this way on purpose. Rewriting four documents by hand is how a redesign breaks a page nobody opened; deriving
// the layout from the blocks already present means a new settings section appears in the side panel without an edit
// here, and a page that already has the markup keeps working untouched.
(function () {
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function homeSpec() {
    var body = document.getElementById("page-body");
    if (!body) return;
    body.classList.add("page-col");
    body.classList.add("home-grid");
    showQuickStart(body);
  }

  // The guide only exists when there is nothing to look at: a page full of agents does not need a tutorial in front of
  // it, and a first run has nothing else to show.
  async function showQuickStart(body) {
    var existing = document.getElementById("quickstart");
    if (existing) existing.remove();
    var rows = [];
    try {
      var res = await fetch("/agents", { headers: { accept: "application/json" } });
      var payload = await res.json();
      rows = payload.agents || payload.channels || [];
    } catch (err) { return; }
    // the guide is for a box with nothing to look at: no lane, or a first lane that has never run
    var first = rows[0];
    if (rows.length && Number(first && first.turns || 0) > 0) return;

    var box = el("section", "block");
    box.id = "quickstart";
    var head = el("div", "block-head");
    head.appendChild(el("h2", null, "First run"));
    box.appendChild(head);
    var steps = el("ol", "quickstart-steps");
    [["Start an agent", "a name and a persona are all it needs", rows.length > 0],
     ["Give it a provider", "a local model or a key, stored once and never shown again", Boolean(first && first.has_key)],
     ["Say something", "the first message wakes it", false],
     ["Watch the footer", "context used, version, and the terminal", false]].forEach(function (row, i) {
      var li = el("li", "quickstart-step");
      li.dataset.qsStep = String(i + 1);
      li.dataset.done = row[2] ? "true" : "false";
      li.appendChild(el("strong", null, row[0]));
      li.appendChild(el("span", "muted small", " — " + row[1]));
      steps.appendChild(li);
    });
    box.appendChild(steps);
    var link = el("a", "pill", rows.length ? "Open the agent" : "Create the first agent");
    link.href = rows.length ? "html/chat.html" : "html/new-agent.html";
    box.appendChild(link);
    box.dataset.qsState = rows.length ? "first-run" : "empty";
  }

  function settingsSpec() {
    var root = document.getElementById("page-root");
    var body = document.getElementById("page-body");
    if (!root || !body) return;
    var blocks = Array.prototype.slice.call(body.querySelectorAll(":scope > .block"));
    if (blocks.length < 2) return;

    var nav = el("aside", "sidenav");
    nav.setAttribute("role", "tablist");
    nav.setAttribute("aria-orientation", "vertical");
    nav.setAttribute("aria-label", "Settings sections");

    var tabs = [];
    blocks.forEach(function (block, index) {
      var title = block.querySelector("h2");
      var name = (title ? title.textContent : block.id || ("section " + (index + 1))).trim();
      var tab = el("button", "pill sidenav-item" + (index === 0 ? " active" : ""), name);
      tab.type = "button";
      tab.id = "set-tab-" + (block.id || index);
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", index === 0 ? "true" : "false");
      tab.setAttribute("aria-controls", "page-body");
      tab.setAttribute("tabindex", index === 0 ? "0" : "-1");
      tab.dataset.index = String(index);
      tab.addEventListener("click", function () { select(index); });
      nav.appendChild(tab);
      tabs.push(tab);
      block.hidden = index !== 0;
    });

    // the page body is the panel: it keeps its id and gains the role, so there is no second panel to keep in step
    root.classList.add("shell");
    body.setAttribute("role", "tabpanel");
    body.setAttribute("tabindex", "0");
    body.setAttribute("aria-labelledby", tabs[0].id);
    root.insertBefore(nav, body);

    function select(index) {
      tabs.forEach(function (tab, i) {
        tab.classList.toggle("active", i === index);
        tab.setAttribute("aria-selected", i === index ? "true" : "false");
        tab.setAttribute("tabindex", i === index ? "0" : "-1");
      });
      blocks.forEach(function (block, i) { block.hidden = i !== index; });
      body.setAttribute("aria-labelledby", tabs[index].id);
    }

    nav.addEventListener("keydown", function (event) {
      var at = tabs.indexOf(document.activeElement);
      if (at < 0 || (event.key !== "ArrowDown" && event.key !== "ArrowUp")) return;
      event.preventDefault();
      var next = tabs[(at + (event.key === "ArrowDown" ? 1 : tabs.length - 1)) % tabs.length];
      next.focus(); next.click();
    });
  }

  function wizardSpec() {
    var steps = document.getElementById("wizard-steps");
    if (steps) steps.classList.add("steps");
  }

  function apply() {
    // key off the elements each spec needs, not the page's declared name: a page that has moved or been renamed
    // still gets its spec, and a page without the markup is left alone
    var page = document.body.dataset.page || "";
    var hasWizard = !!document.getElementById("wizard-steps");
    var hasBody = !!document.querySelector("#page-body .block");
    if (hasWizard) wizardSpec();
    // settings is owned by settings-panels.js; building a second index here duplicated it
  // if (hasBody && page === "settings") settingsSpec();
    else if (hasBody && page !== "new-agent") homeSpec();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
  window.AthenaPageSpecs = { apply: apply };
})();
