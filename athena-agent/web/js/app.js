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
    wireComposer();
    Live.start();
  }

  document.addEventListener('DOMContentLoaded', boot);
  return { boot, showError };
})();
