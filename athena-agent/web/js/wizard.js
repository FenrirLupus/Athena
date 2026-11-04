/* wizard.js — FUNCTION only: step machine, validation, submit.
 *
 * Four steps in one form: persona → provider → credentials → model. The structure of each step lives in
 * new-agent.html; this file shows one fieldset at a time, keeps the provider choice consistent with the
 * fields that matter (a local model needs no key, a remote one needs a URL), and posts the lot once.
 *
 * The key is never read back, never written to a URL, and never kept after submit — the input is cleared
 * so a stray screenshot or a shared screen later cannot show it.
 */
const Wizard = (() => {
  const TOTAL = 4;
  let step = 1;
  let models = [];
  let autoFilled = '';        // the model name we filled in ourselves, so it can be withdrawn again

  const $ = (id) => document.getElementById(id);
  const value = (id) => ($(id)?.value || '').trim();
  const show = (el, on) => { if (el) el.hidden = !on; };

  async function loadModels() {
    try {
      const resp = await fetch('/config', { headers: { accept: 'application/json' } });
      const data = await resp.json();
      models = data.installed_models || [];
    } catch {
      models = [];
    }
    const select = $('f-local');
    if (!select) return;
    select.replaceChildren();
    if (!models.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = '— none downloaded —';
      select.append(option);
      return;
    }
    models.forEach(model => {
      const option = document.createElement('option');
      option.value = model.name;
      option.textContent = `${model.name} · ${model.format || ''} ${model.quant || ''}`.trim();
      select.append(option);
    });
  }

  function provider() { return value('f-provider') || 'openai'; }
  const isLocal = () => provider() === 'local';

  /* fields that only make sense for a remote endpoint vs a served model */
  function syncProvider() {
    document.querySelectorAll('[data-for]').forEach(node => {
      const kind = node.dataset.for;
      if (kind === 'remote') show(node, !isLocal());
      else if (kind === 'local') show(node, isLocal());
      else if (kind === 'credentials') show(node, !isLocal());
      else if (kind === 'local-key') show(node, isLocal());
    });
    const modelField = $('f-model');
    if (isLocal()) {
      const chosen = models.find(m => m.name === ($('f-local')?.value || ''));
      if (chosen) {
        // switching to local fills the name in; switching back must take it out again, or the review
        // panel promises a remote endpoint called after the local folder
        modelField.value = chosen.name;
        autoFilled = chosen.name;
      }
    } else if (modelField && autoFilled && modelField.value.trim() === autoFilled) {
      modelField.value = '';
      autoFilled = '';
    }
    renderReview();
  }

  function renderReview() {
    const box = $('f-review');
    if (!box) return;
    const rows = [
      ['agent', value('f-channel') || '—'],
      ['name', value('f-name') || value('f-channel') || '—'],
      ['provider', provider()],
      ['endpoint', isLocal() ? `models/${value('f-local')}` : (value('f-base-url') || '—')],
      ['model', value('f-model') || value('f-local') || '—'],
      ['window', `${value('f-window') || 32768} tokens`],
      ['key', isLocal() ? 'not needed' : (value('f-api-key') ? 'will be saved to .secret' : 'reusing a saved key')],
    ];
    box.replaceChildren();
    const dl = document.createElement('dl');
    dl.className = 'kv';
    rows.forEach(([key, val]) => {
      const dt = document.createElement('dt');
      dt.textContent = key;
      const dd = document.createElement('dd');
      dd.textContent = val;
      dl.append(dt, dd);
    });
    box.append(dl);
  }

  function validate(target) {
    if (target === 1) {
      const channel = value('f-channel');
      if (!/^[A-Za-z0-9_-]{1,32}$/.test(channel)) return 'agent name: letters, digits, - or _ only';
    }
    if (target === 2) {
      if (!isLocal() && !value('f-base-url')) return 'a remote provider needs a base URL';
      if (isLocal() && !value('f-local')) return 'choose a downloaded model, or pick a remote provider';
    }
    if (target === 3 && !isLocal()) {
      // the hint says "leave empty to reuse a key already saved for this agent", so the check has to agree with it
      if (!value('f-api-key') && !savedKey) return 'an API key is required the first time (leave empty only when re-saving)';
    }
    if (target === 4) {
      if (!isLocal() && !value('f-model')) return 'the endpoint needs to know which model to call';
    }
    return '';
  }

  function paint() {
    // the current step is stated, not only coloured
    document.querySelectorAll('[data-step-button]').forEach(button => {
      const active = button.classList.contains('active');
      button.setAttribute('aria-current', active ? 'step' : 'false');
    });
    document.querySelectorAll('.step').forEach(node => {
      node.classList.toggle('active', Number(node.dataset.step) === step);
    });
    document.querySelectorAll('[data-step-button]').forEach(node => {
      node.classList.toggle('active', Number(node.dataset.stepButton) === step);
    });
    $('wz-status').textContent = `step ${step} of ${TOTAL}`;
    $('wz-back').disabled = step === 1;
    $('wz-next').textContent = step === TOTAL ? 'Create agent' : 'Next';
    $('wz-next').classList.toggle('btn-send', step === TOTAL);
    if (step === 4) renderReview();
    $('wz-message').textContent = '';
  }

  function go(target) {
    if (target > step) {
      const problem = validate(step);
      if (problem) { $('wz-message').textContent = problem; return; }
    }
    step = Math.min(TOTAL, Math.max(1, target));
    paint();
  }

  async function submit() {
    const problem = validate(1) || validate(2) || validate(4);
    if (problem) { $('wz-message').textContent = problem; return; }

    const channel = value('f-channel');
    const payload = {
      persona: {
        name: value('f-name') || channel,
        role: value('f-role'),
        tone: value('f-tone'),
        boundaries: value('f-boundaries'),
      },
      model: {
        provider: provider(),
        base_url: isLocal() ? '' : value('f-base-url'),
        name: value('f-model') || value('f-local'),
        window: Number(value('f-window') || 32768),
        max_output_tokens: Number(value('f-max-tokens') || 1024),
        temperature: Number(value('f-temperature') || 0.7),
        local: isLocal() ? value('f-local') : '',
      },
      api_key: isLocal() ? '' : value('f-api-key'),
    };

    const button = $('wz-next');
    button.disabled = true;
    $('wz-message').textContent = 'creating…';
    try {
      const resp = await fetch(`/agents/${encodeURIComponent(channel)}/profile`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      $('f-api-key').value = '';                       // the key is stored; this page keeps nothing
      $('wz-message').textContent = `created ${channel} — wrote ${(data.wrote || []).join(', ')}`;
      $('wz-next').textContent = 'Open chat';
      $('wz-next').disabled = false;      // the pre-flight disable was never undone on success, so the last step
      $('wz-next').onclick = () => { location.href = `chat.html?channel=${encodeURIComponent(channel)}`; };
                                          // stayed a dead button until a reload
    } catch (err) {
      $('wz-message').textContent = `failed: ${err.message}`;
      button.disabled = false;
    }
  }

  let savedKey = false;      // the profile already holds a key, so an empty field means "keep it"

  async function start() {
    // an existing agent's page can be re-saved without re-typing a secret it already holds
    const existing = value('f-channel');
    if (existing) {
      try {
        const res = await fetch(`/agents/${encodeURIComponent(existing)}/profile`);
        if (res.ok) { const data = await res.json(); savedKey = !!data.has_key; }
      } catch (_) { /* the validation below still refuses if there is genuinely no key */ }
    }
    $('f-provider').addEventListener('change', syncProvider);
    $('f-local').addEventListener('change', syncProvider);
    ['f-channel', 'f-name', 'f-base-url', 'f-model'].forEach(id => {
      document.getElementById(id)?.addEventListener('input', renderReview);
    });
    $('wz-back').addEventListener('click', () => go(step - 1));
    $('wz-next').addEventListener('click', () => (step === TOTAL ? submit() : go(step + 1)));
    document.querySelectorAll('[data-step-button]').forEach(button => {
      button.addEventListener('click', () => go(Number(button.dataset.stepButton)));
    });
    $('wizard').addEventListener('submit', (event) => event.preventDefault());

    loadModels().then(() => { syncProvider(); paint(); });
  }

  document.addEventListener('DOMContentLoaded', start);
  return { start };
})();
