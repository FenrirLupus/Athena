// Page specs: the structure each page needs, derived from what the page already contains.
//
// Built this way on purpose. Rewriting four documents by hand is how a redesign breaks a page nobody opened; deriving
// the layout from the blocks already present means a new settings section appears in the side panel without an edit
// here, and a page that already has the markup keeps working untouched.
(function () {

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
    // the guide is only worth building if it reaches the page: assembling it and dropping the node leaves the
    // first run with nothing to show
    body.appendChild(box);
  }

  function apply() {
    // key off the elements each spec needs, not the page's declared name: a page that has moved or been renamed
    // still gets its spec, and a page without the markup is left alone
    var page = document.body.dataset.page || "";
    var hasBody = !!document.querySelector("#page-body .block");
    // settings and agents have blocks of their own; the first-run guide belongs to the home page alone
    if (hasBody && page === "home") homeSpec();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", apply);
  else apply();
  window.AthenaPageSpecs = { apply: apply };
})();
