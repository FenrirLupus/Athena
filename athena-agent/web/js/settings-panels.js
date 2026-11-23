// Settings as two panels: a bullet index on the left, the settings themselves on the right.
//
// The index is built from the blocks that already exist, so a section added to the markup appears here without this
// file knowing its name. Cards sort alphabetically and the arrow flips the order; each block collapses so a long
// page becomes a list of headings.
(function () {
  var order = 1;   // 1 = A–Z, -1 = Z–A


  function titleOf(block) {
    var h = block.querySelector(".block-head h2, h2, h3");
    return h ? h.textContent.trim() : (block.id || "section");
  }

  function build() {
    var body = document.getElementById("page-body");
    if (!body || body.dataset.panelled) return;
    // the Agents section lives on its own page now: it is removed from the page, not merely left out of the index,
    // or it stays a direct child of the body and renders in the left column over the card list
    var allBlocks = Array.prototype.slice.call(body.querySelectorAll(".block"));
    var blocks = allBlocks.filter(function (block) { return block.id !== "agents"; });
    allBlocks.filter(function (block) { return block.id === "agents"; })
      .forEach(function (block) { block.remove(); });
    if (!blocks.length) return;
    body.dataset.panelled = "1";
    // page-specs falls through to the home spec on this page, which caps the band at 1180px and centres it:
    // the panel never reached the window edge because of these two classes
    body.classList.remove("page-col", "home-grid");

    var left = el("aside", "set-left");
    var right = el("div", "set-right");
    // anything already in the body that is not a block (the warnings line) belongs at the top of the right panel,
    // otherwise it takes the first grid cell and the two panels swap places
    var strays = Array.prototype.slice.call(body.children).filter(function (node) {
      return !node.classList.contains("block");
    });

    var head = el("div", "set-left-head");
    head.appendChild(el("span", "set-left-title", "Settings"));
    var sort = el("button", "set-sort", "A–Z");
    sort.type = "button";
    sort.setAttribute("aria-label", "sort alphabetically");
    head.appendChild(sort);
    left.appendChild(head);

    // the page's own explanation belongs at the top of the settings, where it is read
    var intro = document.querySelector("#page-header .page-heading p, #page-header p");
    if (intro && intro.textContent.trim()) {
      var note = el("p", "set-intro muted small", intro.textContent.trim());
      right.insertBefore(note, right.firstChild);
    }

    var list = el("ul", "set-list");
    list.setAttribute("role", "tablist");
    list.setAttribute("aria-label", "settings sections");
    left.appendChild(list);
    body.appendChild(left);
    body.appendChild(right);
    strays.forEach(function (node) { right.insertBefore(node, right.firstChild); });

    var cards = blocks.map(function (block) {
      var id = block.id || titleOf(block).toLowerCase().replace(/\s+/g, "-");
      block.id = id;

      // the block collapses to its heading
      var chevron = el("span", "set-chevron", "▾");
      var headNode = block.querySelector(".block-head") || block;
      headNode.insertBefore(chevron, headNode.firstChild);
      headNode.classList.add("set-head");
      headNode.setAttribute("role", "button");
      headNode.tabIndex = 0;
      var toggle = function () { block.classList.toggle("set-collapsed"); };
      headNode.addEventListener("click", toggle);
      headNode.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });

      right.appendChild(block);

      var li = el("li", "set-item");
      var card = el("button", "set-card");
      card.type = "button";
      card.dataset.target = id;
      card.setAttribute("role", "tab");
      card.setAttribute("aria-controls", id);
      card.setAttribute("aria-selected", "false");
      card.appendChild(el("span", "set-dot", "•"));
      card.appendChild(el("span", "set-card-title", titleOf(block)));
      var noteText = ((block.querySelector(".block-note") || {}).textContent || "").trim();
      var note = el("span", "set-card-note", noteText);
      note.title = noteText;          // the clamp hides the tail; hovering shows all of it
      card.appendChild(note);
      card.addEventListener("click", function () { select(id); });
      li.appendChild(card);
      list.appendChild(li);
      return { li: li, title: titleOf(block) };
    });

    // the left panel is a selector, not an index: the right panel shows one category's settings
    function select(id) {
      blocks.forEach(function (block) {
        block.hidden = block.id !== id;
        if (block.id === id) block.classList.remove("set-collapsed");
      });
      Array.prototype.forEach.call(list.querySelectorAll(".set-card"), function (card) {
        var on = card.dataset.target === id;
        card.classList.toggle("active", on);
        card.setAttribute("aria-selected", on ? "true" : "false");
      });
      right.scrollTop = 0;
    }

    function sortCards() {
      cards.slice()
        .sort(function (a, b) { return order * a.title.localeCompare(b.title, undefined, { sensitivity: "base" }); })
        .forEach(function (c) { list.appendChild(c.li); });
      sort.textContent = order === 1 ? "A–Z" : "Z–A";
    }
    sort.addEventListener("click", function () { order = -order; sortCards(); });
    sortCards();
    select(cards.slice().sort(function (a, b) { return a.title.localeCompare(b.title); })[0].li
             .querySelector(".set-card").dataset.target);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
