// Tail the process's own log into the terminal overlay.
//
// /console returns the records the logger emitted, with a sequence cursor, so this is the server's console shown as
// it is — not a reconstruction from other endpoints. It writes into whatever .term-log exists, and stays out of the
// way if the overlay is not on the page.
(function () {
  var cursor = 0, MAX = 400, timer, log;
  var autoScroll = true, LINES_UP_TO_STOP = 16;

  var LABEL = { warning: "WARN", caution: "CAUT", critical: "CRIT" };
  var CLASS = { warning: "term-warn", caution: "term-caution", critical: "term-critical" };

  function line(row) {
    var level = LABEL[row.level] ? row.level : "";
    var el = document.createElement("div");
    el.className = "term-line " + (level ? CLASS[level] : "term-info");
    var at = document.createElement("span");
    at.className = "term-at";
    at.textContent = new Date((row.at || 0) * 1000).toTimeString().slice(0, 8);
    el.appendChild(at);
    if (level) {
      var tag = document.createElement("span");
      tag.className = "term-lvl";
      tag.textContent = LABEL[level];
      el.appendChild(tag);
    }
    el.appendChild(document.createTextNode(row.text));
    return el;
  }

  async function tick() {
    if (document.hidden || page !== 1) return;
    log = log || document.querySelector(".term-log");
    if (!log) return;
    try {
      var data = await (await fetch("/console?since=" + cursor + "&limit=200")).json();
      (data.rows || []).forEach(function (row) { log.appendChild(line(row)); });
      cursor = data.cursor !== undefined ? data.cursor : cursor;
      while (log.children.length > MAX) log.removeChild(log.firstChild);
      if ((data.rows || []).length && autoScroll) toBottom();
    } catch (e) { /* the next tick retries */ }
  }

  // whole history, dated, 128 lines to a page; live lines only append while page 1 is showing
  var page = 1, pages = 1, pager;

  function header(date) {
    var el = document.createElement("div");
    el.className = "term-date";
    el.textContent = date;
    return el;
  }

  async function loadPage(n) {
    log = log || document.querySelector(".term-log");
    if (!log) return;
    try {
      var data = await (await fetch("/console/history?page=" + n + "&size=128")).json();
      page = data.page || 1;
      pages = data.pages || 1;
      log.replaceChildren();
      (data.groups || []).forEach(function (group) {
        log.appendChild(header(group.date));
        (group.rows || []).slice().reverse().forEach(function (row) { log.appendChild(line(row)); });
      });
      if (!(data.total || 0)) { log.appendChild(header("no console history yet")); }
      paintPager(data.total || 0);
      watchScroll();
      autoScroll = true;
      toBottom();
      cursor = Math.max.apply(null, (data.groups || []).flatMap(function (g) {
        return (g.rows || []).map(function (r) { return r.seq || 0; });
      }).concat([0]));
    } catch (e) { }
  }

  function lineHeight() {
    var sample = log && log.querySelector(".term-line");
    var height = sample ? sample.getBoundingClientRect().height : 0;
    return height > 4 ? height + 2 : 18;
  }

  function distance() {
    return log ? log.scrollHeight - log.scrollTop - log.clientHeight : 0;
  }

  function toBottom() {
    if (log) log.scrollTop = log.scrollHeight;
  }

  // reading upward by sixteen lines is a decision to stop following the tail; returning to the foot reverses it
  function watchScroll() {
    log = log || document.querySelector(".term-log");
    if (!log || log.dataset.watched) return;
    log.dataset.watched = "1";
    log.addEventListener("scroll", function () {
      autoScroll = distance() < lineHeight() * 2;
      if (distance() > lineHeight() * LINES_UP_TO_STOP) autoScroll = false;
    });
  }

  function paintPager(total) {
    log = log || document.querySelector(".term-log");
    if (!log || !log.parentNode) return;
    if (!pager) {
      pager = document.createElement("div");
      pager.className = "term-pager";
      pager.innerHTML = '<button type="button" data-p="prev" aria-label="previous page">‹</button>' +
                        '<span class="term-page-no"></span>' +
                        '<button type="button" data-p="next" aria-label="next page">›</button>' +
                        '<span class="term-page-total muted small"></span>';
      log.parentNode.insertBefore(pager, log);
      pager.addEventListener("click", function (event) {
        var button = event.target.closest("button[data-p]");
        if (!button) return;
        loadPage(page + (button.dataset.p === "next" ? 1 : -1));
      });
    }
    pager.querySelector(".term-page-no").textContent = "page " + page + " / " + pages;
    pager.querySelector(".term-page-total").textContent = total ? total.toLocaleString() + " lines" : "";
  }

  window.AthenaConsole = {
    tick: tick,
    page: loadPage,
    reset: function () { cursor = 0; loadPage(1); },
    state: function () {
      return { autoScroll: autoScroll, atBottom: distance() < lineHeight() * 2,
               distancePx: Math.round(distance()), linePx: Math.round(lineHeight()) };
    },
  };
  timer = setInterval(tick, 2000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); });
  // the overlay's markup is built when it opens, so the first paint waits for that click
  function openHandler() { setTimeout(function () { loadPage(1); }, 120); }
  var opener = document.getElementById("terminal-open");
  if (opener) opener.addEventListener("click", openHandler);
  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("#terminal-open")) openHandler();
  });
  setTimeout(function () { if (document.querySelector(".term-log")) loadPage(1); }, 400);
})();
