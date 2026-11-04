/* app.js — FUNCTION only: boot, tabs, composer behaviour.
 *
 * Each page is its own document with its own structure; there is no router and no renderer here.
 *   - the page navbar (tabs) switches which .panel is visible — the buttons are in the HTML
 *   - the composer grows with its content, sends on Enter, breaks the line on Shift+Enter
 *   - Live owns every request
 */
const App = (() => {
  /* A failed fetch or a thrown callback used to leave a panel looking merely empty. Any error now
   * surfaces as a bar at the top of the page, because "the rail is blank" and "the rail broke" must not
   * look identical. */
  function showError(message) {
    let bar = document.getElementById('js-error');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'js-error';
      bar.className = 'js-error';
      document.body.prepend(bar);
    }
    bar.textContent = message;
  }

  function watchErrors() {
    window.addEventListener('error', (event) => {
      showError(`script error: ${event.message}`);
    });
    window.addEventListener('unhandledrejection', (event) => {
      const reason = event.reason;
      showError(`request failed: ${(reason && reason.message) || reason}`);
    });
  }

  function markNav() {
    const page = document.body.dataset.page || 'home';
    document.querySelectorAll('.nav-link').forEach(link => {
      link.classList.toggle('active', link.dataset.page === page);
    });
  }

  function wireTabs() {
    const tabs = document.querySelectorAll('.tab[data-tab]');
    if (!tabs.length) return;
    // association on load, not only after a click: the strip was announced as a tab list with no selected tab
    tabs.forEach(button => {
      const name = button.dataset.tab;
      const panel = document.getElementById(`panel-${name}`);
      if (panel) {
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-labelledby', `tab-${name}`);
        if (!panel.id) panel.id = `panel-${name}`;
      }
      button.id = `tab-${name}`;
      button.setAttribute('aria-controls', `panel-${name}`);
      button.setAttribute('aria-selected', panel && panel.classList.contains('active') ? 'true' : 'false');
      button.setAttribute('tabindex', button.classList.contains('active') ? '0' : '-1');
    });
    // arrow keys move between tabs, which is what a tablist promises
    document.querySelectorAll('.page-nav[role="tablist"]').forEach(list => {
      list.addEventListener('keydown', event => {
        const keys = { ArrowRight: 1, ArrowLeft: -1, Home: 'first', End: 'last' };
        if (!(event.key in keys)) return;
        const items = [...list.querySelectorAll('[role="tab"]')];
        const at = items.indexOf(document.activeElement);
        if (at < 0) return;
        event.preventDefault();
        const next = keys[event.key] === 'first' ? items[0]
                   : keys[event.key] === 'last' ? items[items.length - 1]
                   : items[(at + keys[event.key] + items.length) % items.length];
        next.focus();
        next.click();
      });
    });
    tabs.forEach(button => {
      button.addEventListener('click', () => {
        const name = button.dataset.tab;
        tabs.forEach(other => {
          other.classList.toggle('active', other === button);
          other.setAttribute('aria-selected', other === button ? 'true' : 'false');
          other.setAttribute('tabindex', other === button ? '0' : '-1');
        });
        document.querySelectorAll('.panel').forEach(panel => {
          panel.classList.toggle('active', panel.id === `panel-${name}`);
        });
        Live.state.tab = name;
      });
    });
  }

  function wireComposer() {
    const input = document.getElementById('chat-text');
    if (!input) return;

    const grow = () => {
      input.style.height = 'auto';
      input.style.height = `${Math.min(180, input.scrollHeight)}px`;
    };
    input.addEventListener('input', grow);
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        document.getElementById('chat-send')?.click();
      }
    });
    grow();
  }

  function boot() {
    watchErrors();
    markNav();
    wireTabs();
    wireComposer();
    Live.start();
  }

  document.addEventListener('DOMContentLoaded', boot);
  return { boot, showError };
})();
