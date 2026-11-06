/* One element helper for every page script.
   The same three lines were copied into seven files; a change to any of them changed one page's build behaviour and
   left the other six alone. Loaded before every page script, so `el` is simply available. */
(function () {
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  window.el = el;
  window.AthenaDom = { el: el };
})();
