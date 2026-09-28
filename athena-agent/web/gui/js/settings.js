// ── Settings — SCHEMA-DRIVEN config editor (the Operator's spec: EVERY
//    config.yaml setting is customizable) ─────────────────────────────
var profilesPageEl = null;

// ── THE PROFILE SETTINGS PAGE (the Operator's spec) ─────────────────────
// TOP: the changeable settings section (config.yaml → profile/identity).
// BOTTOM: the Existing Profiles list — SELECT a profile, then use the
// New / Duplicate / Delete buttons above the entries. Locked profiles
// (.default/.nurse/.janitor) are architecture-critical: no select for
// destructive ops, no edit, no duplicate-into.
var selectedProfile = null;

async function buildProfilesPage(page, cfg) {
  // THE PROFILE TAB STANDARD (the Operator's 08-15 spec): section panels
  // like every tab. Sections map to the identity FILES: Assistant
  // (ASSISTANT.md) + User (USER.md) + Profiles (the registry list).
  // The identity lives in the .md FRONTMATTER — NOT config.yaml (the
  // Operator's 08-15 correction: profile settings never save to config).
  // ── SECTION: ASSISTANT (ASSISTANT.md frontmatter) ──
  const asSec = document.createElement('div');
  asSec.className = 'settings-section-panel';
  const asHead = document.createElement('div');
  asHead.className = 'settings-section-title';
  asHead.textContent = 'Assistant';
  asSec.appendChild(asHead);
  const asDesc = document.createElement('div');
  asDesc.className = 'desc';
  asDesc.textContent = 'The agent identity — ASSISTANT.md frontmatter (saves to the file, not config.yaml)';
  asSec.appendChild(asDesc);
  page.appendChild(asSec);

  // ── SECTION: USER (USER.md frontmatter) ──
  const usSec = document.createElement('div');
  usSec.className = 'settings-section-panel';
  const usHead = document.createElement('div');
  usHead.className = 'settings-section-title';
  usHead.textContent = 'User';
  usSec.appendChild(usHead);
  const usDesc = document.createElement('div');
  usDesc.className = 'desc';
  usDesc.textContent = 'The operator identity — USER.md frontmatter (saves to the file, not config.yaml)';
  usSec.appendChild(usDesc);
  page.appendChild(usSec);

  // ── LOAD the ACTIVE profile's .md identity into the two sections ──
  try {
    const activeName = (cfg.profile && cfg.profile.active) || '';
    const profName = activeName || 'default';
    const rp = await fetch('/profiles/' + encodeURIComponent(profName));
    const gd = await rp.json();
    const id = (gd.identity || {});
    const addField = (section, key, label) => {
      const row = document.createElement('div');
      row.className = 'setting-row';
      const info = document.createElement('div');
      const lab = document.createElement('label');
      lab.textContent = label;
      info.appendChild(lab);
      row.appendChild(info);
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.value = id[key] || '';
      inp.dataset.profile = profName;
      inp.dataset.field = key;
      // THE .MD IDENTITY SAVE (the 08-15 fix): each change writes to the
      // identity file via /profiles/{name}/identity — debounced.
      let _t = null;
      inp.addEventListener('change', async () => {
        if (_t) clearTimeout(_t);
        _t = setTimeout(async () => {
          const body = {};
          body[key] = inp.value;
          try {
            await fetch('/profiles/' + encodeURIComponent(profName) + '/identity', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({identity: body}),
            });
          } catch (e) { /* best-effort */ }
        }, 400);
      });
      row.appendChild(inp);
      section.appendChild(row);
    };
    // ASSISTANT section: the agent-side fields.
    addField(asSec, 'agent_first', 'First name');
    addField(asSec, 'agent_last', 'Last name');
    addField(asSec, 'agent_nick', 'Nickname');
    addField(asSec, 'role', 'Role');
    // USER section: the operator-side fields.
    addField(usSec, 'operator_first', 'First name');
    addField(usSec, 'operator_last', 'Last name');
  } catch (e) { /* the profile may not be fetchable — sections stay empty */ }

  // ── SECTION: PROFILES (the registry) ──
  const prSec = document.createElement('div');
  prSec.className = 'settings-section-panel';
  const prHead = document.createElement('div');
  prHead.className = 'settings-section-title';
  prHead.textContent = 'Profiles';
  prSec.appendChild(prHead);
  // The action buttons ABOVE the entries (the Operator's spec).
  const actions = document.createElement('div');
  actions.className = 'profiles-actions';
  const btnNew = document.createElement('button');
  btnNew.className = 'profile-action-btn';
  btnNew.textContent = '+ New';
  btnNew.title = 'Create a brand-new profile';
  btnNew.addEventListener('click', () => promptProfileName('Create a new profile', async (name) => {
    const res = await fetch('/profiles/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name}),
    });
    const d = await res.json();
    if (!d.ok) throw new Error(d.error || 'create failed');
    return d.profile;
  }));
  const btnDup = document.createElement('button');
  btnDup.className = 'profile-action-btn';
  btnDup.textContent = '⧉ Duplicate';
  btnDup.title = 'Duplicate the SELECTED profile (exact copy to customize)';
  btnDup.addEventListener('click', async () => {
    const name = selectedProfile;
    if (!name) { alert('Select a profile first (click its card).'); return; }
    const newName = prompt('New profile name (exact copy of "' + name + '"):',
                           name.replace(/^\./, '') + '-copy');
    if (!newName) return;
    try {
      const res = await fetch('/profiles/' + encodeURIComponent(name) + '/duplicate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({new_name: newName}),
      });
      const d = await res.json();
      if (!d.ok) throw new Error(d.error || 'duplicate failed');
      await refreshProfilesList();
      const st = $('settings-save-status');
      if (st) st.textContent = 'duplicated → ' + d.profile + ' ✓';
    } catch (e) { alert('Duplicate failed: ' + e.message); }
  });
  const btnDel = document.createElement('button');
  btnDel.className = 'profile-action-btn danger';
  btnDel.textContent = '✕ Delete';
  btnDel.title = 'Delete the SELECTED profile (locked ones are protected)';
  btnDel.addEventListener('click', async () => {
    const name = selectedProfile;
    if (!name) { alert('Select a profile first (click its card).'); return; }
    if (!confirm('Delete profile "' + name + '" permanently?')) return;
    try {
      const res = await fetch('/profiles/' + encodeURIComponent(name) + '/delete', {method: 'POST'});
      const d = await res.json();
      if (!d.ok) throw new Error(d.error || 'delete failed');
      selectedProfile = null;
      await refreshProfilesList();
      const st = $('settings-save-status');
      if (st) st.textContent = 'deleted ✓';
    } catch (e) { alert('Delete failed: ' + e.message); }
  });
  actions.appendChild(btnNew);
  actions.appendChild(btnDup);
  actions.appendChild(btnDel);
  prSec.appendChild(actions);
  const hint = document.createElement('p');
  hint.className = 'settings-sub';
  hint.textContent = 'Click a profile to select it → then Duplicate or Delete. 🔒 = locked (architecture-critical — no modify/delete): .default · .nurse · .janitor';
  prSec.appendChild(hint);
  // The entries container (refreshed by New/Duplicate/Delete).
  const list = document.createElement('div');
  list.className = 'profiles-list';
  list.id = 'profiles-list-body';
  prSec.appendChild(list);
  page.appendChild(prSec);
  await renderProfileCards(list);
}

// ── The profile cards inside the list (CLICK to select — the Operator's
//    spec: select a profile, then New/Duplicate/Delete act on it) ──
async function renderProfileCards(list) {
  list.innerHTML = '';
  const renderOne = (p) => {
    const card = document.createElement('div');
    card.className = 'profile-card' + (p.locked ? ' locked' : '');
    if (selectedProfile === p.name) card.classList.add('selected');
    if (!p.locked) {
      card.classList.add('selectable');
      card.title = 'Click to select — then Duplicate or Delete';
      card.addEventListener('click', () => {
        selectedProfile = p.name;
        list.querySelectorAll('.profile-card').forEach(c => c.classList.remove('selected'));
        card.classList.add('selected');
        const st = $('settings-save-status');
        if (st) st.textContent = 'selected: ' + p.name;
      });
    }
    const head = document.createElement('div');
    head.className = 'profile-card-head';
    const nm = document.createElement('span');
    nm.className = 'profile-name';
    nm.textContent = p.name;
    head.appendChild(nm);
    if (p.is_default) {
      const tag = document.createElement('span');
      tag.className = 'profile-tag';
      tag.textContent = 'default';
      head.appendChild(tag);
    }
    if (p.locked) {
      const lock = document.createElement('span');
      lock.className = 'profile-lock';
      lock.textContent = '🔒 locked';
      lock.title = 'Architecture-critical — no modify / no delete';
      head.appendChild(lock);
    }
    card.appendChild(head);
    return card;
  };
  try {
    const rp = await fetch('/profiles');
    const pd = await rp.json();
    for (const p of (pd.profiles || [])) {
      const card = renderOne(p);
      try {
        const gp = await fetch('/profiles/' + encodeURIComponent(p.name));
        const gd = await gp.json();
        const fields = [
          ['agent_first', 'Agent first name'],
          ['agent_last', 'Agent last name'],
          ['agent_nick', 'Agent nickname'],
          ['role', 'Role'],
          ['operator_first', 'Operator first name'],
          ['operator_last', 'Operator last name'],
        ];
        const body = document.createElement('div');
        body.className = 'profile-card-body';
        for (const [key, label] of fields) {
          const row = document.createElement('div');
          row.className = 'profile-field';
          const l = document.createElement('label');
          l.textContent = label;
          row.appendChild(l);
          const inp = document.createElement('input');
          inp.type = 'text';
          inp.value = (gd.identity || {})[key] || '';
          inp.dataset.profile = p.name;
          inp.dataset.field = key;
          if (p.locked) {
            inp.disabled = true;
            inp.title = 'Locked profile — read-only';
          } else {
            inp.addEventListener('change', () => { settingsDirty = true; });
          }
          row.appendChild(inp);
          body.appendChild(row);
        }
        card.appendChild(body);
      } catch (e) { /* ignore */ }
      list.appendChild(card);
    }
  } catch (e) { /* ignore */ }
}

async function refreshProfilesList() {
  const list = document.getElementById('profiles-list-body');
  if (list) await renderProfileCards(list);
  try {
    const rp = await fetch('/profiles');
    const pd = await rp.json();
    // Keep the navbar + the Active-profile dropdown in sync.
    const sel = document.getElementById('profile-select');
    if (sel) {
      sel.innerHTML = '';
      for (const p of (pd.profiles || [])) {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name + (p.locked ? ' 🔒' : '');
        sel.appendChild(opt);
      }
      sel.value = pd.current || '';
    }
  } catch (e) { /* ignore */ }
}

// ── THE PROVIDER SETTINGS PAGE (the Operator's spec, 08-10) ─────────────
// TOP: the provider editor — selection dropdown (catalog) → auto-fills
// base URL; API key → .secret (never config); Model dropdown populated
// by probing the provider's /models. BOTTOM: the configured providers
// list — SELECT a card, then Edit (loads into the form) or Delete.
var selectedProvider = null;
var providerCatalog = [];
var providerFormState = {name: '', base_url: '', models: []};

async function buildProvidersPage(page, cfg) {
  // THE PROVIDER TAB STANDARD (the Operator's 08-15 spec): same section
  // panels as every tab. Sections map to the three STORES the provider
  // writes: Config (the selection), Secret (API key → .secret),
  // Authentication (base url + models → authentication.json).
  // ── SECTION: CONFIG (the provider selection) ──
  const cfgSec = document.createElement('div');
  cfgSec.className = 'settings-section-panel';
  const cfgHead = document.createElement('div');
  cfgHead.className = 'settings-section-title';
  cfgHead.textContent = 'Config';
  cfgSec.appendChild(cfgHead);
  // 1. Provider Selection (catalog dropdown).
  const selRow = document.createElement('div');
  selRow.className = 'setting-row';
  const selInfo = document.createElement('div');
  const selLab = document.createElement('label');
  selLab.textContent = 'Provider Selection';
  selInfo.appendChild(selLab);
  const selDesc = document.createElement('div');
  selDesc.className = 'desc';
  selDesc.textContent = 'Pick a provider to configure — its base url fills in automatically';
  selInfo.appendChild(selDesc);
  selRow.appendChild(selInfo);
  const selCtl = document.createElement('select');
  selCtl.id = 'prov-form-name';
  selCtl.innerHTML = '<option value="">— select a provider —</option>';
  try {
    const rc = await fetch('/providers/catalog');
    const cd = await rc.json();
    providerCatalog = cd.catalog || [];
    for (const p of providerCatalog) {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.name + (p.local ? ' (local)' : '');
      selCtl.appendChild(opt);
    }
  } catch (e) { /* ignore */ }
  selCtl.addEventListener('change', onProviderSelect);
  selRow.appendChild(selCtl);
  cfgSec.appendChild(selRow);
  page.appendChild(cfgSec);

  // ── SECTION: SECRET (API key → .secret, never displayed back) ──
  const keySec = document.createElement('div');
  keySec.className = 'settings-section-panel';
  const keyHead = document.createElement('div');
  keyHead.className = 'settings-section-title';
  keyHead.textContent = 'Secret';
  keySec.appendChild(keyHead);
  const keyRow = document.createElement('div');
  keyRow.className = 'setting-row';
  const keyInfo = document.createElement('div');
  const keyLab = document.createElement('label');
  keyLab.textContent = 'API Key';
  keyInfo.appendChild(keyLab);
  const keyDesc = document.createElement('div');
  keyDesc.className = 'desc';
  keyDesc.textContent = 'Stored in .secret only — never in config. Leave empty to keep an existing key.';
  keyInfo.appendChild(keyDesc);
  keyRow.appendChild(keyInfo);
  const keyCtl = document.createElement('input');
  keyCtl.id = 'prov-form-key';
  keyCtl.type = 'password';
  keyCtl.placeholder = '••••••••••';
  keyCtl.autocomplete = 'off';
  keyRow.appendChild(keyCtl);
  keySec.appendChild(keyRow);
  page.appendChild(keySec);

  // ── SECTION: AUTHENTICATION (base url + models → authentication.json) ──
  const authSec = document.createElement('div');
  authSec.className = 'settings-section-panel';
  const authHead = document.createElement('div');
  authHead.className = 'settings-section-title';
  authHead.textContent = 'Authentication';
  authSec.appendChild(authHead);
  const urlRow = document.createElement('div');
  urlRow.className = 'setting-row';
  const urlInfo = document.createElement('div');
  const urlLab = document.createElement('label');
  urlLab.textContent = 'Base Url';
  urlInfo.appendChild(urlLab);
  const urlDesc = document.createElement('div');
  urlDesc.className = 'desc';
  urlDesc.textContent = 'The endpoint used by this provider — saved to authentication.json';
  urlInfo.appendChild(urlDesc);
  urlRow.appendChild(urlInfo);
  const urlCtl = document.createElement('input');
  urlCtl.id = 'prov-form-url';
  urlCtl.type = 'text';
  urlCtl.placeholder = 'https://…/v1';
  urlRow.appendChild(urlCtl);
  authSec.appendChild(urlRow);
  page.appendChild(authSec);

  // 4. (No Model row — model selection moved to the Models tab, which is
  //    PER-PROFILE. This page owns API keys + base urls only.)

  // 5. Configuration buttons — the Operator's spec: Delete (the destroyer)
  //    + Edit · Refresh (load the selected provider / re-probe its models).
  //    THE 08-15 DEDUP: the tab's footer Save button handles saving —
  //    NO duplicate internal Save button here.
    const btnRow = document.createElement('div');
    btnRow.className = 'provider-actions';
    const leftGroup = document.createElement('div');
    leftGroup.className = 'provider-action-group';
    const btnDel = document.createElement('button');
    btnDel.className = 'profile-action-btn danger';
    btnDel.textContent = '✕ Delete';
    btnDel.title = 'Delete the SELECTED provider (config entry + .secret key)';
    btnDel.addEventListener('click', async () => {
      const name = selectedProvider;
      if (!name) { alert('Select a provider first (click its card).'); return; }
      if (!confirm('Delete provider "' + name + '" permanently? Its .secret key is removed too.')) return;
      try {
        const res = await fetch('/providers/delete', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name}),
        });
        const d = await res.json();
        if (!d.ok) throw new Error(d.detail || 'delete failed');
        selectedProvider = null;
        const st = $('prov-save-status');
        if (st) st.textContent = 'deleted ' + name + ' ✓';
        await refreshProvidersList();
      } catch (e) { alert('Delete failed: ' + e.message); }
    });
    leftGroup.appendChild(btnDel);
    btnRow.appendChild(leftGroup);
    const rightGroup = document.createElement('div');
    rightGroup.className = 'provider-action-group';
    const btnEdit = document.createElement('button');
    btnEdit.className = 'profile-action-btn';
    btnEdit.textContent = '✎ Edit';
    btnEdit.title = 'Load the SELECTED provider into the editor';
    btnEdit.addEventListener('click', () => {
      const name = selectedProvider;
      if (!name) { alert('Select a provider first (click its card).'); return; }
      loadProviderIntoForm(name);
    });
    rightGroup.appendChild(btnEdit);
    const probeBtn = document.createElement('button');
    probeBtn.className = 'profile-action-btn';
    probeBtn.id = 'prov-probe-btn';
    probeBtn.textContent = '⟳ Refresh';
    probeBtn.title = 'Re-probe this provider for available models (uses the entered key)';
    probeBtn.addEventListener('click', probeProviderModels);
    rightGroup.appendChild(probeBtn);
    const actStatus = document.createElement('span');
    actStatus.className = 'settings-save-status';
    actStatus.id = 'prov-save-status';
    rightGroup.appendChild(actStatus);
    btnRow.appendChild(rightGroup);
    page.appendChild(btnRow);

  // 6. THE STREAMING TOGGLE (the Operator's 08-12 spec): provider
  //    responses stream token-by-token (typed live in the chat) when
  //    ON; OFF returns the whole response at once. Stored in the GLOBAL
  //    config (provider.streaming).
  const strRow = document.createElement('div');
  strRow.className = 'setting-row';
  const strInfo = document.createElement('div');
  const strLab = document.createElement('label');
  strLab.textContent = 'Streaming Responses';
  strInfo.appendChild(strLab);
  const strDesc = document.createElement('div');
  strDesc.className = 'desc';
  strDesc.textContent = 'When ON, the model reply streams token-by-token and the chat types it live (theermes-style). When OFF, the full response arrives at once.';
  strInfo.appendChild(strDesc);
  strRow.appendChild(strInfo);
  const strCtl = document.createElement('select');
  strCtl.id = 'streaming-toggle';
  const strOn = document.createElement('option');
  strOn.value = 'true';
  strOn.textContent = 'True';
  const strOff = document.createElement('option');
  strOff.value = 'false';
  strOff.textContent = 'False';
  strCtl.appendChild(strOn);
  strCtl.appendChild(strOff);
  // The current value from the global config (default True).
  let streamingVal = true;
  try {
    const cv = cfg && cfg.provider && cfg.provider.streaming;
    streamingVal = cv === undefined ? true : Boolean(cv);
  } catch (e) { /* default true */ }
  strCtl.value = String(streamingVal);
  strCtl.addEventListener('change', async () => {
    const val = strCtl.value === 'true';
    try {
      const r = await fetch('/config/set', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({patch: {provider: {streaming: val}}}),
      });
      const d = await r.json();
      const status = document.getElementById('prov-save-status');
      if (status) status.textContent = d.ok ? 'streaming saved ✓' : 'save failed';
    } catch (e) { /* ignore */ }
  });
  strRow.appendChild(strCtl);
  page.appendChild(strRow);

  // ── BOTTOM: the configured providers list ──
  // ── SECTION: CONFIGURED (the provider list — THE 08-15 standard:
  //    extends downward with every provider, no internal scroll) ──
  const confSec = document.createElement('div');
  confSec.className = 'settings-section-panel';
  const confHead = document.createElement('div');
  confHead.className = 'settings-section-title';
  confHead.textContent = 'Configured';
  confSec.appendChild(confHead);
  const hint = document.createElement('p');
  hint.className = 'settings-sub';
  hint.textContent = 'Click a provider to select it → then Save / Delete / Edit act on it. ✓ key = a credential exists in .secret.';
  confSec.appendChild(hint);
  const list = document.createElement('div');
  list.className = 'profiles-list';
  list.id = 'providers-list-body';
  confSec.appendChild(list);
  page.appendChild(confSec);
  await refreshProvidersList(list);
  renderProviderSelection();
}

// ── THE MODELS PAGE (per-profile model settings — the Operator's spec,
//    08-10): Reason / Vision / Embedding, each with a PRIMARY (left) +
//    FALLBACK (right) pair = 6 settings. Saved into the ACTIVE
//    profile's own config.yaml (provider.selection); credentials stay
//    GLOBAL in authentication.json + .secret (the shared credential
//    set — every profile picks from the same providers). ──
async function buildModelsPage(page, cfg) {
  // THE 08-15 DEDUP: the wrapped section provides the Sub Header — no
  // duplicate internal h2/sub here. (The active profile is shown in the
  // navbar dropdown — no redundant badge inside the tab.)

  // Fetch the current state: the active profile's selection + the
  // provider landscape (configured providers with their models).
  let state = {profile: '', selection: {}, providers: []};
  try {
    const r = await fetch('/config/models');
    state = await r.json();
    // THE 08-15 VISIBILITY FIX: an empty providers list (the endpoint
    // failed or the boot raced) is surfaced instead of a silent blank —
    // the operator sees WHY the tab is empty.
    if (!(state.providers || []).length) {
      const warn = document.createElement('div');
      warn.className = 'desc';
      warn.style.color = 'var(--tertiary)';
      warn.textContent = 'No providers loaded yet — the boot may still be '
        + 'seeding, or /config/models returned empty. Reload the page after '
        + 'Athena finishes booting.';
      page.appendChild(warn);
    }
  } catch (e) {
    const err = document.createElement('div');
    err.className = 'desc';
    err.style.color = 'var(--tertiary)';
    err.textContent = 'Could not load models state (' + e.message + ') — '
      + 'the server may still be booting.';
    page.appendChild(err);
  }

  const types = [
    {key: 'reason', label: 'Reason'},
    {key: 'vision', label: 'Vision'},
    {key: 'embedding', label: 'Embedding'},
  ];
  for (const t of types) {
    page.appendChild(buildModelTypeSection(t, state));
  }

  // THE MODELS AUTO-SAVE (the 08-15 fix): the per-profile model selects
  // save on change (debounced). The status line shows live save feedback
  // (the initial "auto-save — edits write to …" text was removed — it's
  // unnecessary; the saves just happen).
  const st = document.createElement('span');
  st.className = 'settings-save-status';
  st.id = 'models-save-status';
  page.appendChild(st);
  let _modelsTimer = null;
  const saveModels = async () => {
    const entries = collectModelSelections();
    try {
      const res = await fetch('/config/models', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({selection: entries}),
      });
      const d = await res.json();
      st.textContent = d.ok
        ? 'saved → ' + (d.profile || 'default') + ' ✓'
        : 'save failed: ' + (d.detail || 'unknown');
    } catch (e) {
      st.textContent = 'save error: ' + e.message;
    }
  };
  const scheduleModelsSave = () => {
    st.textContent = 'editing… (saves shortly)';
    if (_modelsTimer) clearTimeout(_modelsTimer);
    _modelsTimer = setTimeout(saveModels, 600);
  };
  // Wire every model select on this page to the auto-save (the selects
  // carry dataset.type = reason/vision/embedding + dataset.side).
  page.addEventListener('change', e => {
    if (e.target.matches('select[data-type][data-side]')) {
      scheduleModelsSave();
    }
  });
  // THE SUB-FOOTER SAVE (the 08-15 fix): the wrapped section's Save
  // button fires this event → save now (not debounced).
  page.addEventListener('settings-section-save', () => { saveModels(); });
}

// The EMOTION page (the Operator's 08-11 spec): the active profile's emotion
// vector — 8 axis sliders per side (agent + operator), each -1..+1 with
// the ternary band labels, plus the active pair combination below.
async function buildEmotionPage(page, cfg) {
  // THE 08-15 DEDUP: the wrapped section provides the Sub Header — no
  // duplicate internal h2/sub here. (The active profile is shown in the
  // navbar dropdown — no redundant badge inside the tab.)
  let state = {profile: '', axes: [], wheel: {}, agent: {vector: {}}, operator: {vector: {}}, combinations: []};
  try {
    const r = await fetch('/config/emotion');
    state = await r.json();
  } catch (e) { /* keep defaults */ }
  const axes = (state.axes && state.axes.length) ? state.axes : ['joy','trust','fear','surprise','sadness','disgust','anger','anticipation'];

  const sides = [
    {key: 'agent', label: 'Agent'},
    {key: 'operator', label: 'Operator'},
  ];
  for (const s of sides) {
    const card = document.createElement('div');
    card.className = 'models-type-card';
    const head = document.createElement('div');
    head.className = 'models-type-head';
    head.textContent = s.label + ' emotional vector';
    card.appendChild(head);

    const vec = (state[s.key] && state[s.key].vector) || {};
    const cur = (state[s.key] && state[s.key].current) || 'neutral — uniform vector';
    const curLine = document.createElement('div');
    curLine.className = 'emotion-current';
    curLine.id = 'emotion-current-' + s.key;
    curLine.textContent = 'current: ' + cur;
    card.appendChild(curLine);

    // THE MOOD EDITOR (the Operator's 08-15 spec): the multi-word
    // sentence (<=64 words) describing how this side feels — stored in
    // EMOTION.md's mood field. The felt WORD is the emotion; the mood
    // is the sentence when asked.
    const moodLabel = document.createElement('label');
    moodLabel.textContent = 'Mood (<=64 words)';
    moodLabel.className = 'emotion-mood-label';
    card.appendChild(moodLabel);
    const moodBox = document.createElement('textarea');
    moodBox.className = 'emotion-mood-box';
    moodBox.id = 'emotion-mood-' + s.key;
    moodBox.rows = 2;
    moodBox.maxLength = 512;
    moodBox.placeholder = 'the mood sentence — how this side feels when asked';
    moodBox.value = (state[s.key] && state[s.key].mood) || '';
    card.appendChild(moodBox);

    for (const axis of axes) {
      const names = (state.wheel && state.wheel[axis]) || ['', '', ''];
      const row = document.createElement('div');
      row.className = 'emotion-axis-row';

      const lab = document.createElement('label');
      lab.textContent = axis;
      lab.title = names[0] + ' (-1) | ' + names[1] + ' (0) | ' + names[2] + ' (+1)';
      row.appendChild(lab);

      const input = document.createElement('input');
      input.type = 'range';
      input.min = -1; input.max = 1; input.step = 0.01;
      input.value = vec[axis] || 0;
      input.dataset.side = s.key;
      input.dataset.axis = axis;
      input.className = 'emotion-axis-slider';
      row.appendChild(input);

      const val = document.createElement('span');
      val.className = 'emotion-axis-val';
      val.id = 'emotion-val-' + s.key + '-' + axis;
      val.textContent = (vec[axis] || 0).toFixed(2);
      row.appendChild(val);

      const nameSpan = document.createElement('span');
      nameSpan.className = 'emotion-axis-name';
      nameSpan.id = 'emotion-name-' + s.key + '-' + axis;
      nameSpan.textContent = bandName(parseFloat(input.value), names);
      row.appendChild(nameSpan);

      input.addEventListener('input', () => {
        const v = parseFloat(input.value);
        val.textContent = v.toFixed(2);
        nameSpan.textContent = bandName(v, names);
      });
      card.appendChild(row);
    }
    page.appendChild(card);
  }

  // THE 08-15 CLEANUP: the combo line ("active: none — neutral") and the
  // auto-save status line are redundant — the per-side "current:" lines
  // above already show the emotion state. Save happens via the tab footer.
  window.__saveEmotions = async function () {
    try {
      for (const s of sides) {
        const vector = {};
        for (const axis of axes) {
          const el = document.querySelector('input.emotion-axis-slider[data-side="' + s.key + '"][data-axis="' + axis + '"]');
          vector[axis] = el ? parseFloat(el.value) : 0;
        }
        const moodEl = document.getElementById('emotion-mood-' + s.key);
        const mood = moodEl ? moodEl.value.trim() : '';
        const res = await fetch('/config/emotion', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({side: s.key, vector: vector, mood: mood}),
        });
        const d = await res.json();
        if (!d.ok) { return false; }
      }
      return true;
    } catch (e) {
      return false;
    }
  };
}

// The named emotion for a value in its band.
function bandName(v, names) {
  if (v <= -0.33) return names[0] || 'low';
  if (v >= 0.33) return names[2] || 'high';
  return names[1] || 'neutral';
}

// One model type: a card with TWO halves — Primary (left) + Fallback
// (right). Each half has a provider select + model select. The model
// select is populated when a provider is chosen (from that provider's
// models in the shared store).
function buildModelTypeSection(t, state) {
  const card = document.createElement('div');
  card.className = 'models-type-card';

  const head = document.createElement('div');
  head.className = 'models-type-head';
  head.textContent = t.label;
  card.appendChild(head);

  const halves = document.createElement('div');
  halves.className = 'models-halves';

  const sel = (state.selection && state.selection[t.key]) || {};
  const sides = [
    {key: 'primary', label: 'Primary', provider: sel.provider, model: sel.model},
    {key: 'fallback', label: 'Fallback', provider: sel.fallback_provider, model: sel.fallback_model},
  ];
  for (const s of sides) {
    const half = document.createElement('div');
    half.className = 'models-half';

    const lab = document.createElement('label');
    lab.textContent = s.label;
    half.appendChild(lab);

    const provSel = document.createElement('select');
    provSel.className = 'models-prov-select';
    provSel.dataset.type = t.key;
    provSel.dataset.side = s.key;
    provSel.innerHTML = '<option value="">— provider —</option>';
    for (const p of (state.providers || [])) {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.name;
      provSel.appendChild(opt);
    }
    provSel.value = s.provider || '';
    half.appendChild(provSel);

    const modelSel = document.createElement('select');
    modelSel.className = 'models-model-select';
    modelSel.dataset.type = t.key;
    modelSel.dataset.side = s.key;
    modelSel.innerHTML = '<option value="">— model —</option>';
    modelSel.disabled = !s.provider;
    half.appendChild(modelSel);

    // Populate the model list from the provider's shared models.
    provSel.addEventListener('change', () => {
      const prov = provSel.value;
      modelSel.innerHTML = '<option value="">— model —</option>';
      if (!prov) { modelSel.disabled = true; return; }
      const entry = (state.providers || []).find(p => p.name === prov);
      const models = (entry && entry.models) || [];
      for (const m of models) {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        modelSel.appendChild(opt);
      }
      modelSel.disabled = models.length === 0;
    });
    // After wiring the listener, set the current model.
    if (s.provider) {
      const entry = (state.providers || []).find(p => p.name === s.provider);
      const models = (entry && entry.models) || [];
      for (const m of models) {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        modelSel.appendChild(opt);
      }
      modelSel.value = s.model || '';
    }
    half.appendChild(modelSel);
    halves.appendChild(half);
  }
  card.appendChild(halves);
  return card;
}

// Collect all six settings from the Models page into the POST shape.
function collectModelSelections() {
  const entries = {reason: {}, vision: {}, embedding: {}};
  document.querySelectorAll('#page-Models .models-prov-select').forEach(sel => {
    const t = sel.dataset.type;
    const side = sel.dataset.side;
    const key = side === 'primary' ? 'provider' : 'fallback_provider';
    entries[t][key] = sel.value;
  });
  document.querySelectorAll('#page-Models .models-model-select').forEach(sel => {
    const t = sel.dataset.type;
    const side = sel.dataset.side;
    const key = side === 'primary' ? 'model' : 'fallback_model';
    entries[t][key] = sel.value;
  });
  return entries;
}

// ── Provider dropdown → auto-fill base url from the catalog ──
function onProviderSelect() {
  const name = $('prov-form-name').value;
  const urlCtl = $('prov-form-url');
  providerFormState.name = name;
  if (name) {
    const entry = providerCatalog.find(p => p.name === name);
    const configured = providersCache[name];
    // Prefer the configured base url (already saved); else catalog default.
    urlCtl.value = (configured && configured.base_url) || (entry ? entry.base_url : '');
    const st = $('prov-save-status');
    if (st && !configured) st.textContent = 'new provider';
    if (st && configured) st.textContent = 'editing existing — leave key empty to keep';
  } else {
    urlCtl.value = '';
  }
}

// ── Probe (Refresh): query the provider's /models and persist them.
//    Models stay in authentication.json (the SHARED store); choosing
//    which model to USE happens per-profile in the Models tab.
async function probeProviderModels() {
  const name = $('prov-form-name').value;
  const base_url = $('prov-form-url').value.trim();
  const api_key = $('prov-form-key').value.trim();
  if (!name) { alert('Select a provider first.'); return; }
  if (!base_url) { alert('Base url required before probing.'); return; }
  const btn = $('prov-probe-btn');
  const old = btn.textContent;
  btn.textContent = '⟳ probing…';
  btn.disabled = true;
  try {
    const res = await fetch('/providers/probe', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, base_url, api_key}),
    });
    const d = await res.json();
    const st = $('prov-save-status');
    if (d.ok && d.models && d.models.length) {
      providerFormState.models = d.models;
      if (st) st.textContent = d.count + ' models refreshed ✓';
      // A REFRESH on a configured provider persisted the fresh models —
      // re-render the list so the card's model list stays truthful.
      if (d.saved) await refreshProvidersList();
    } else {
      if (st) st.textContent = (d.detail || 'no models returned') + ' — check the key / base url';
    }
  } catch (e) {
    const st = $('prov-save-status');
    if (st) st.textContent = 'probe failed: ' + e.message;
  } finally {
    btn.textContent = old;
    btn.disabled = false;
  }
}

// ── Save: create/edit the provider (key → .secret, url+models → config) ──
async function saveProviderFromForm() {
  const name = $('prov-form-name').value;
  const base_url = $('prov-form-url').value.trim();
  const api_key = $('prov-form-key').value.trim();
  if (!name) { alert('Select a provider.'); return; }
  if (!base_url) { alert('Base url is required.'); return; }
  // THE 08-15 DEDUP: the footer Save calls this directly — the internal
  // button is gone; show progress on the status line instead.
  const st = $('prov-save-status');
  if (st) st.textContent = 'saving…';
  try {
    const res = await fetch('/providers/save', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, base_url, api_key}),
    });
    const d = await res.json();
    if (d.ok) {
      if (st) st.textContent = 'saved ' + name + ' · ' + d.models_discovered + ' models ✓';
      $('prov-form-key').value = '';
      selectedProvider = name;
      await refreshProvidersList();
    } else {
      if (st) st.textContent = 'save failed: ' + (d.detail || 'unknown');
    }
  } catch (e) {
    const st = $('prov-save-status');
    if (st) st.textContent = 'save error: ' + e.message;
  }
}

// ── Edit: load a configured provider into the editor form ──
function loadProviderIntoForm(name) {
  const entry = providersCache[name];
  if (!entry) return;
  const selCtl = $('prov-form-name');
  if (selCtl) selCtl.value = name;
  const urlCtl = $('prov-form-url');
  if (urlCtl) urlCtl.value = entry.base_url || '';
  $('prov-form-key').value = '';
  const st = $('prov-save-status');
  if (st) st.textContent = 'editing ' + name + ' — leave key empty to keep existing';
}

var providersCache = {};
async function refreshProvidersList(list) {
  // The list may be passed directly (during page build the container is
  // not yet in the document, so getElementById would miss it — the
  // Profile page pattern). Fall back to the document lookup for the
  // Edit/Delete buttons (after append the id resolves).
  if (!list) list = document.getElementById('providers-list-body');
  if (!list) return;
  list.innerHTML = '';
  let providers = [];
  try {
    const rp = await fetch('/providers/list');
    const pd = await rp.json();
    providers = pd.providers || [];
  } catch (e) { /* ignore */ }
  providersCache = {};
  for (const p of providers) providersCache[p.name] = p;
  for (const p of providers) {
    const card = document.createElement('div');
    card.className = 'profile-card selectable' + (selectedProvider === p.name ? ' selected' : '');
    card.addEventListener('click', () => {
      selectedProvider = p.name;
      list.querySelectorAll('.profile-card').forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      // Load into the editor so Save / Refresh act on THIS provider —
      // not whatever the form last held. (The Operator's flow: select a card,
      // then Edit / Refresh / Delete work on it directly.)
      loadProviderIntoForm(p.name);
      const st = $('prov-save-status');
      if (st) st.textContent = 'selected: ' + p.name;
    });
    const head = document.createElement('div');
    head.className = 'profile-card-head';
    const nm = document.createElement('span');
    nm.className = 'profile-name';
    nm.textContent = p.name;
    head.appendChild(nm);
    if (p.primary) {
      const tag = document.createElement('span');
      tag.className = 'profile-tag';
      tag.textContent = 'active';
      head.appendChild(tag);
    }
    if (p.has_key) {
      const keyTag = document.createElement('span');
      keyTag.className = 'profile-tag key-tag';
      keyTag.textContent = '✓ key';
      head.appendChild(keyTag);
    }
    card.appendChild(head);
    const body = document.createElement('div');
    body.className = 'profile-card-body';
    const urlF = document.createElement('div');
    urlF.className = 'profile-field';
    const urlL = document.createElement('label');
    urlL.textContent = 'Base url';
    urlF.appendChild(urlL);
    const urlV = document.createElement('input');
    urlV.type = 'text';
    urlV.readOnly = true;
    urlV.value = p.base_url || '';
    urlF.appendChild(urlV);
    body.appendChild(urlF);
    const modF = document.createElement('div');
    modF.className = 'profile-field';
    const modL = document.createElement('label');
    modL.textContent = 'Models (' + (p.models || []).length + ')';
    modF.appendChild(modL);
    const modV = document.createElement('input');
    modV.type = 'text';
    modV.readOnly = true;
    modV.value = (p.models || []).join(', ') || '— none —';
    modF.appendChild(modV);
    body.appendChild(modF);
    card.appendChild(body);
    list.appendChild(card);
  }
}

// ── Make the editor dropdown's stored selection match the configured list ──
function renderProviderSelection() {
  const selCtl = $('prov-form-name');
  if (!selCtl) return;
  const cur = providerFormState.name;
  if (cur) { try { selCtl.value = cur; } catch (e) { /* ignore */ } }
}
function promptProfileName(message, action) {
  const name = prompt(message);
  if (!name) return;
  (async () => {
    try {
      const created = await action(name);
      await refreshProfilesList();
      const st = $('settings-save-status');
      if (st) st.textContent = 'created → ' + created + ' ✓';
    } catch (e) { alert('Failed: ' + e.message); }
  })();
}


// CONFIG_SCHEMA: page → [ {path, label, type, desc} ] where path is the
// dot-path into config.yaml. type: number | bool | text | select.
// select gets {options: [...]}.
// NOTE: a FUNCTION (hoisted), not a const — loadSettings() is called
// during script parse (before this block), so a const would be in the
// temporal dead zone.
function getConfigSchema() {
  return {
  'Profile': [
    {path: 'profile.active', label: 'Active profile', type: 'text', desc: 'The identity Athena runs as', special: 'profiles'},
  ],
  'Permissions': [
    {path: 'permissions.placeholder', label: 'Permissions', type: 'text', desc: 'The 4-channel allow/deny/block store — the Permissions tab', special: 'permissions'},
  ],
  'Autonomy': [
    {path: 'autonomy.nurse_first_delay_s', label: 'Nurse first delay (s)', type: 'number', desc: 'How long after boot before the nurse checks in'},
    {path: 'autonomy.nurse_interval_s', label: 'Nurse interval (s)', type: 'number', desc: 'How often the nurse runs its housekeeping rounds'},
  ],
  'Server': [
    {path: 'server.host', label: 'Host', type: 'text', desc: 'The bind address'},
    {path: 'server.port', label: 'Port', type: 'number', desc: 'The web + MCP port'},
    {path: 'server.tick_interval_s', label: 'Tick interval (s)', type: 'number', desc: 'The housekeeping loop cadence'},
  ],
  'Thinking': [
    {path: 'thinking_budget.max_calls_per_hour', label: 'Max calls / hour', type: 'number', desc: 'The thinking budget ceiling'},
    {path: 'thinking_budget.min_priority', label: 'Min priority', type: 'number', desc: 'Only thoughts above this priority fire', step: 0.1},
    {path: 'thinking_budget.cooldown_s', label: 'Cooldown (s)', type: 'number', desc: 'Between autonomous thoughts'},
    {path: 'thinking_budget.fail_closed', label: 'Fail closed', type: 'bool', desc: 'Deny thoughts when the budget is unknown'},
  ],
  'Guardrails': [
    {path: 'tool_loop_guardrails.warnings_enabled', label: 'Warnings enabled', type: 'bool', desc: 'Loop guardrails nudge (never block)'},
    {path: 'tool_loop_guardrails.hard_stop_enabled', label: 'Hard stop enabled', type: 'bool', desc: 'Circuit breaker on repeated failures'},
    {path: 'tool_loop_guardrails.warn_after.exact_failure', label: 'Warn after exact failures', type: 'number'},
    {path: 'tool_loop_guardrails.warn_after.same_tool_failure', label: 'Warn after same-tool failures', type: 'number'},
    {path: 'tool_loop_guardrails.warn_after.idempotent_no_progress', label: 'Warn after no-progress', type: 'number'},
    {path: 'tool_loop_guardrails.hard_stop_after.exact_failure', label: 'Block after exact failures', type: 'number'},
    {path: 'tool_loop_guardrails.hard_stop_after.same_tool_failure', label: 'Halt after same-tool failures', type: 'number'},
    {path: 'tool_loop_guardrails.hard_stop_after.idempotent_no_progress', label: 'Block after no-progress', type: 'number'},
    {path: 'tool_loop_guardrails.loop_caps.max_web_searches', label: 'Max web searches / turn', type: 'number'},
    {path: 'tool_loop_guardrails.loop_caps.max_subagents', label: 'Max subagents / turn', type: 'number'},
  ],
  'Budget': [
    {path: 'budget.iteration.main_iterations', label: 'Main iterations', type: 'number'},
    {path: 'budget.iteration.main_max_tokens', label: 'Main max tokens', type: 'number'},
    {path: 'budget.iteration.subagent_iterations', label: 'Subagent iterations', type: 'number'},
    {path: 'budget.iteration.subagent_max_tokens', label: 'Subagent max tokens', type: 'number'},
    {path: 'budget.message_loop.max_iterations', label: 'Loop max iterations', type: 'number'},
    {path: 'budget.message_loop.max_tokens', label: 'Loop max tokens', type: 'number'},
    {path: 'budget.message_loop.recent_window', label: 'Recent window', type: 'number', desc: 'How many recent messages stay raw'},
  ],
  'Context': [
    {path: 'context.compression.context_window', label: 'Context window', type: 'number'},
    {path: 'context.compression.upper_threshold', label: 'Compress above', type: 'number', step: 0.05},
    {path: 'context.compression.lower_threshold', label: 'Compress down to', type: 'number', step: 0.05},
    {path: 'context.retrieval.enabled', label: 'Retrieval enabled', type: 'bool'},
    {path: 'context.retrieval.session_first', label: 'Session first', type: 'bool'},
    {path: 'context.retrieval.semantic', label: 'Semantic rerank', type: 'bool'},
    {path: 'context.retrieval.embedding_model', label: 'Embedding model', type: 'text'},
  ],
  'Theme': [
    {path: 'theme.mode', label: 'Theme', type: 'select', options: ['dark', 'light'], desc: 'The 5-color palette mode', special: 'theme'},
  ],
  'Provider': [],
  'Models': [],
  'Emotion': [],
  };
}

var settingsDirty = false;
var themePageEl = null;
var modelsPageEl = null;

function pathGet(obj, path) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function pathSet(obj, path, value) {
  const parts = path.split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (typeof cur[parts[i]] !== 'object' || cur[parts[i]] === null) cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
}

// SAVE (the 08-15 auto-save): collect every field back into the config
// and POST /config/all. No Save button — every field change saves
// automatically (debounced 600ms so a quick series of edits batches).
// THE 08-15 AUDIT CACHE: /config/all + the palette were refetched on
// EVERY tab switch + save. Cache in memory; invalidate after a save so
// the next read is fresh (but the SAME visit doesn't hammer the server).
let _cfgCache = null;
let _paletteCache = null;

async function fetchConfigCached() {
  if (_cfgCache) return _cfgCache;
  const r = await fetch('/config/all');
  const d = await r.json();
  _cfgCache = d;
  return d;
}
async function fetchPaletteCached() {
  if (_paletteCache) return _paletteCache;
  const r = await fetch('/config/theme/palette');
  const d = await r.json();
  _paletteCache = d;
  return d;
}
function invalidateConfigCache() {
  _cfgCache = null;
  _paletteCache = null;
}

async function saveAllSettings() {
  const cfg = {};
  try {
    const r = await fetch('/config/all');
    const d = await r.json();
    Object.assign(cfg, d.config || {});
  } catch (e) { /* ignore */ }
  document.querySelectorAll('#settings-pages [data-path]').forEach(ctl => {
    const path = ctl.dataset.path;
    const type = ctl.dataset.type;
    let v;
    if (type === 'bool') v = ctl.value === 'true';
    else if (type === 'number') v = ctl.value === '' ? null : Number(ctl.value);
    else v = ctl.value === '' ? null : ctl.value;
    pathSet(cfg, path, v);
  });
  // THEME PALETTES (the Operator's spec): collect the editor's LIVE values
  // and save them to config.yaml → theme.light / theme.dark. The hex
  // fields are the source of truth (sliders + hex both write them).
  let palettesSaved = true;
  if (themePageEl) {
    const palettes = {light: [], dark: []};
    ['light', 'dark'].forEach(mode => {
      const arr = palettes[mode];
      themePageEl.querySelectorAll('.theme-hex[data-mode="' + mode + '"]').forEach(hi => {
        const idx = Number(hi.dataset.idx);
        const v = hi.value.trim();
        arr[idx] = v.startsWith('#') ? v : '#' + v;
      });
      // Fill any gaps from the stored cache.
      const cached = (THEME_PALETTES && THEME_PALETTES[mode]) || [];
      for (let i = 0; i < 5; i++) {
        if (!arr[i] && cached[i]) arr[i] = cached[i];
      }
    });
    // CRITICAL: also write the live palettes INTO the cfg object — the
    // /config/all POST below would otherwise overwrite them with the
    // stale pre-edit values.
    if (!cfg.theme) cfg.theme = {};
    cfg.theme.light = palettes.light.slice();
    cfg.theme.dark = palettes.dark.slice();
    try {
      const rp = await fetch('/config/theme/palette', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({palettes}),
      });
      const dp = await rp.json();
      palettesSaved = dp.ok;
    } catch (e) { palettesSaved = false; }
  }
  // PROFILE IDENTITIES (the Operator's spec): the Existing Profiles list —
  // persist each UNLOCKED profile's edited identity fields.
  let profilesSaved = true;
  try {
    const edits = {};
    document.querySelectorAll('.profile-field input:not(:disabled)').forEach(inp => {
      const pname = inp.dataset.profile;
      if (!pname) return;
      edits[pname] = edits[pname] || {};
      edits[pname][inp.dataset.field] = inp.value;
    });
    for (const pname of Object.keys(edits)) {
      const pr = await fetch('/profiles/' + encodeURIComponent(pname) + '/identity', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({identity: edits[pname]}),
      });
      const pd2 = await pr.json();
      if (!pd2.ok) profilesSaved = false;
    }
  } catch (e) { profilesSaved = false; }
  try {
    const res = await fetch('/config/all', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({config: cfg}),
    });
    const d = await res.json();
    // THE SECTION STATUS (the 08-15 fix): write the save result to the
    // ACTIVE section's sub-footer status (the global status bar is gone).
    const activePage = document.querySelector('#settings-pages .settings-page.active');
    const st = (activePage && activePage.querySelector('.settings-sec-foot .settings-save-status'))
      || $('settings-save-status');
    if (st) st.textContent = (d.ok && palettesSaved && profilesSaved) ? 'saved ✓'
      : 'failed: ' + ((d.detail) || 'save failed');
    if (d.ok && palettesSaved && profilesSaved) settingsDirty = false;
    // THE 08-15 AUDIT: a save changes the config — invalidate the cache
    // so the next read is fresh (the same-visit reads stay cached).
    invalidateConfigCache();
    // Refresh the palette cache so the toggle uses the newly-saved colors.
    try {
      _paletteCache = null;   // force a fresh palette read
      const rq = await fetch('/config/theme/palette');
      const qd = await rq.json();
      if (qd.palettes) THEME_PALETTES = qd.palettes;
      applyPaletteCSS(THEME_PALETTES, document.body.classList.contains('dark') ? 'dark' : 'light');
    } catch (e) { /* ignore */ }
    // Theme applies immediately.
    if (cfg.theme && cfg.theme.mode) {
      document.body.classList.toggle('light', cfg.theme.mode === 'light');
    }
  } catch (e) {
    const st = $('settings-save-status');
    if (st) st.textContent = 'save error';
  }
}

// THE AUTO-SAVE (the 08-15 spec): every field change saves automatically
// (debounced — a quick series of edits batches into one POST). No Save
// Config button; the left nav selects the section, the right panel edits,
// and the config is always current.
let _saveTimer = null;
function scheduleSave() {
  settingsDirty = true;
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => { saveAllSettings(); }, 600);
}

async function loadSettings() {
  let cfg = {};
  try {
    // THE 08-15 AUDIT: use the in-memory cache — the config doesn't
    // change between visits unless a save happened (which invalidates).
    const d = await fetchConfigCached();
    cfg = (d && d.config) || {};
  } catch (e) { /* ignore */ }
  // THE ASYNC-COMPLETION TRACKER (the 08-15 fix): pages build out of
  // order; count completions and activate the first tab only when ALL
  // are done (or a clicked tab the moment ITS page lands).
  let _built = 0;
  let _pendingTab = null;
  // Build the tabs + pages from the schema.
  const tabsEl = $('settings-tabs');
  const pagesEl = $('settings-pages');
  tabsEl.innerHTML = '';
  pagesEl.innerHTML = '';
  const names = Object.keys(getConfigSchema()).sort();
  names.forEach(async (name, i) => {
    const tab = document.createElement('button');
    tab.className = 'settings-tab' + (i === 0 ? ' active' : '');
    tab.dataset.settingsPage = name;
    tab.textContent = name;
    tabsEl.appendChild(tab);
    const page = document.createElement('div');
    page.className = 'settings-page' + (i === 0 ? ' active' : '');
    page.id = 'page-' + name;
    // THE THEME PAGE (the Operator's spec): the palette editor instead of the
    // generic field rows — two subsections, 5 rounded swatches + HSL
    // sliders + hex fields per color. No light/dark dropdown (the navbar
    // toggle already does that — it would be a duplicate).
    if (name === 'Theme') {
      try {
        // THE 08-15 AUDIT: the palette read uses the cache too.
        const pd = await fetchPaletteCached();
        buildThemeEditor(page, (pd && pd.palettes) || {});
        wrapSettingsSection(page, 'Theme',
          'The 5-color light/dark palettes — the theme editor. Saved to config.yaml (theme.light / theme.dark).',
          async () => { saveAllSettings(); });
        pagesEl.appendChild(page);
        themePageEl = page;
        // Skip the generic field rows for this page.
        page.dataset.themePage = '1';
      } catch (e) { /* fall through to generic rows */ }
    }
    // THE PROFILE PAGE (the Operator's spec): the Existing Profiles list —
    // each profile editable 1:1, the locked ones (.default/.nurse/
    // .janitor) shown read-only with a lock.
    if (name === 'Profile') {
      try {
        await buildProfilesPage(page, cfg);
        wrapSettingsSection(page, 'Profile',
          'The profiles — identity files per agent. Locked profiles (.default/.nurse/.janitor) are read-only; named profiles are editable.',
          async () => { saveAllSettings(); });
        pagesEl.appendChild(page);
        profilesPageEl = page;
        page.dataset.themePage = '1';  // reuse the skip marker
      } catch (e) { /* fall through to generic rows */ }
    }
    // THE PROVIDER PAGE (the Operator's spec, 08-10): the provider editor —
    // selection dropdown, api key → .secret, base url, model dropdown
    // (auto-probed) + the configured providers list (save/edit/delete).
    if (name === 'Provider') {
      try {
        await buildProvidersPage(page, cfg);
        wrapSettingsSection(page, 'Provider',
          'The provider registry — base urls + model discovery in authentication.json, API keys in .secret (credentials never in config).',
          async () => {
            // THE 08-15 DEDUP: the footer Save calls the form saver
            // directly (the internal button was removed).
            await saveProviderFromForm();
          });
        pagesEl.appendChild(page);
        providersPageEl = page;
        page.dataset.themePage = '1';  // reuse the skip marker
      } catch (e) { /* fall through to generic rows */ }
    }
    // THE MODELS PAGE (the Operator's spec, 08-10): the per-profile model
    // settings — Reason / Vision / Embedding, each with a primary
    // (left) + fallback (right) pair. Read/written per-profile from
    // /config/models; credentials stay global.
    if (name === 'Models') {
      try {
        await buildModelsPage(page, cfg);
        // THE SUB-SCHEMA (the 08-15 fix): wrap in Header→Body→Footer;
        // the Save button triggers the models auto-save (the page has
        // its own live status line already).
        wrapSettingsSection(page, 'Models',
          'Per-profile model selection — Reason / Vision / Embedding, each with a primary + fallback. Saved to this profile\'s config.yaml (provider.selection); credentials stay global.',
          async () => {
            const saveModelsBtn = page.querySelector('#models-save-status');
            // Trigger the page's own save by firing a synthetic change.
            page.dispatchEvent(new Event('settings-section-save'));
          });
        pagesEl.appendChild(page);
        modelsPageEl = page;
        page.dataset.themePage = '1';  // reuse the skip marker
      } catch (e) { /* fall through to generic rows */ }
    }
    // THE EMOTION PAGE (the Operator's 08-11 spec): the active profile's
    // emotion vector — 8 axis sliders per side + the active combination.
    if (name === 'Emotion') {
      try {
        await buildEmotionPage(page, cfg);
        wrapSettingsSection(page, 'Emotion',
          'The Plutchik emotion vector — 8 axes per side, -1..+1 in three bands. The LLM gauges both sides every turn; adjust the snapshot here.',
          async () => {
            // THE 08-15 DEDUP: the footer Save posts the emotion vectors.
            if (window.__saveEmotions) await window.__saveEmotions();
          });
        pagesEl.appendChild(page);
        page.dataset.themePage = '1';  // reuse the skip marker
      } catch (e) { /* fall through to generic rows */ }
    }
    // THE PERMISSIONS TAB (the Operator's 08-15 spec): the 4-channel
    // allow/deny/block store — operator/agent/system name lists + the
    // global channel's flag pairs.
    if (name === 'Permissions') {
      try {
        await buildPermissionsPage(page, cfg);
        wrapSettingsSection(page, 'Permissions',
          'The 4-channel permissions store — allowed tools/skills by name (populated when allowed at session/global) + the global security level.',
          async () => {
            if (window.__savePermissions) await window.__savePermissions();
          });
        pagesEl.appendChild(page);
        page.dataset.themePage = '1';  // reuse the skip marker
      } catch (e) { /* fall through to generic rows */ }
    }
    if (!page.dataset.themePage) {
    // THE SETTINGS SECTION SCHEMA (the Operator's 08-15 spec): every
    // right-panel section is Sub Header (title + desc) → Sub Body (the
    // fields) → Sub Footer (the section's Save button).
    const sec = document.createElement('div');
    sec.className = 'settings-sec';
    const head = document.createElement('div');
    head.className = 'settings-sec-head';
    const h3 = document.createElement('h3');
    h3.textContent = name;
    head.appendChild(h3);
    const sub = document.createElement('div');
    sub.className = 'desc';
    sub.textContent = 'config.yaml · ' + name.toLowerCase() + ' section — edits auto-save, or press Save';
    head.appendChild(sub);
    sec.appendChild(head);
    const body = document.createElement('div');
    body.className = 'settings-sec-body';
    // THE SECTION GROUPING (the Operator's 08-15 spec): the config is
    // Category > Section > Setting — the tab is the Category (1st key),
    // so the panel body must show the SECTIONS (2nd key) as sub-panels,
    // each with its Settings (3rd key) beneath. Mirrors config.yaml 1:1.
    const groups = {};   // sectionKey -> [fields]
    const order = [];    // section display order
    for (const f of getConfigSchema()[name]) {
      const parts = (f.path || '').split('.');
      let secKey = 'general';
      if (parts.length >= 3) secKey = parts[1];      // budget.ITERATION.main
      else if (parts.length === 2) secKey = parts[0]; // identity.agent_name
      if (!(secKey in groups)) { groups[secKey] = []; order.push(secKey); }
      groups[secKey].push(f);
    }
    for (const secKey of order) {
      const g = document.createElement('div');
      g.className = 'settings-section-panel';
      const ghead = document.createElement('div');
      ghead.className = 'settings-section-title';
      // A human label: "iteration" → "Iteration"; "message_loop" → "Message Loop".
      ghead.textContent = secKey
        .split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
      g.appendChild(ghead);
      for (const f of groups[secKey]) {
        const row = document.createElement('div');
        row.className = 'setting-row';
        const info = document.createElement('div');
        const lab = document.createElement('label');
        lab.textContent = f.label;
        info.appendChild(lab);
        if (f.desc) {
          const d = document.createElement('div');
          d.className = 'desc';
          d.textContent = f.desc;
          info.appendChild(d);
        }
        row.appendChild(info);
        const val = pathGet(cfg, f.path);
        let ctl;
        if (f.type === 'bool') {
          ctl = document.createElement('select');
          ctl.innerHTML = '<option value="true">true</option><option value="false">false</option>';
          ctl.value = String(val === undefined ? false : val);
        } else if (f.type === 'select') {
          ctl = document.createElement('select');
          for (const o of (f.options || [])) {
            const opt = document.createElement('option');
            opt.value = o; opt.textContent = o;
            ctl.appendChild(opt);
          }
          if (val !== undefined && val !== null) ctl.value = String(val);
        } else if (f.type === 'number') {
          ctl = document.createElement('input');
          ctl.type = 'number';
          ctl.step = f.step || 1;
          ctl.value = (val === undefined || val === null) ? '' : String(val);
        } else {
          ctl = document.createElement('input');
          ctl.type = 'text';
          ctl.value = (val === undefined || val === null) ? '' : String(val);
        }
        ctl.dataset.path = f.path;
        ctl.dataset.type = f.type;
        // THE AUTO-SAVE (the 08-15 fix): every change saves (debounced).
        ctl.addEventListener('change', () => { scheduleSave(); });
        row.appendChild(ctl);
        g.appendChild(row);
      }
      body.appendChild(g);
    }
    sec.appendChild(body);
    const foot = document.createElement('div');
    foot.className = 'settings-sec-foot';
    const saveBtn = document.createElement('button');
    saveBtn.className = 'settings-save-btn';
    saveBtn.textContent = 'Save ' + name;
    saveBtn.addEventListener('click', () => { saveAllSettings(); });
    foot.appendChild(saveBtn);
    const st = document.createElement('span');
    st.className = 'settings-save-status';
    st.id = 'section-save-status-' + name;
    st.textContent = '';
    foot.appendChild(st);
    sec.appendChild(foot);
    page.appendChild(sec);
    pagesEl.appendChild(page);
    } // /if(!themePage)
    // THE ASYNC-COMPLETION (the 08-15 fix): pages build out of order;
    // when ALL are done, activate the first tab's page. Also: if the
    // user already clicked a tab whose page wasn't built yet, activate
    // this page now that it exists.
    _built++;
    if (_built >= names.length) {
      const firstTab = document.querySelector('#settings-tabs .settings-tab');
      if (firstTab) switchSettingsPage(firstTab.dataset.settingsPage);
    } else if (_pendingTab && _pendingTab === name) {
      switchSettingsPage(name);
    }
  });
  // Wire tab switching — EVENT DELEGATION on the container, so tabs
  // appended asynchronously (the page builder is async) still respond.
  $('settings-tabs').addEventListener('click', e => {
    const tab = e.target.closest('.settings-tab');
    if (tab) {
      _pendingTab = tab.dataset.settingsPage;
      switchSettingsPage(tab.dataset.settingsPage);
    }
  });
  settingsDirty = false;
}

// THE SETTINGS SECTION WRAPPER (the Operator's 08-15 spec): every
// right-panel section — generic OR special-built — is wrapped in
// Sub Header (title+desc) → Sub Body (the section's content) → Sub
// Footer (the section's Save button). Takes the already-built page,
// moves its children into the body, adds the head + foot.
function wrapSettingsSection(page, name, desc, onSave) {
  const sec = document.createElement('div');
  sec.className = 'settings-sec';
  const head = document.createElement('div');
  head.className = 'settings-sec-head';
  const h3 = document.createElement('h3');
  h3.textContent = name;
  head.appendChild(h3);
  if (desc) {
    const d = document.createElement('div');
    d.className = 'desc';
    d.textContent = desc;
    head.appendChild(d);
  }
  sec.appendChild(head);
  const body = document.createElement('div');
  body.className = 'settings-sec-body';
  while (page.firstChild) body.appendChild(page.firstChild);
  sec.appendChild(body);
  const foot = document.createElement('div');
  foot.className = 'settings-sec-foot';
  const saveBtn = document.createElement('button');
  saveBtn.className = 'settings-save-btn';
  saveBtn.textContent = 'Save ' + name;
  saveBtn.addEventListener('click', async () => {
    try {
      await onSave();
    } catch (e) { /* the save fn updates the status itself */ }
  });
  foot.appendChild(saveBtn);
  const st = document.createElement('span');
  st.className = 'settings-save-status';
  st.id = 'section-save-status-' + name;
  st.textContent = '';
  foot.appendChild(st);
  sec.appendChild(foot);
  page.appendChild(sec);
}

function switchSettingsPage(page) {
  document.querySelectorAll('#settings-tabs .settings-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.settingsPage === page);
  });
  document.querySelectorAll('#settings-pages .settings-page').forEach(p => {
    p.classList.toggle('active', p.id === 'page-' + page);
  });
}

