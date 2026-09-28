// THE PERMISSIONS TAB (the Operator's 08-15 spec): the 4-channel
// permissions store — three channel sections with per-name bulleted
// lists (operator/agent/system) + the GLOBAL channel with its flag pairs
// (Type: allow/deny/block × Level: once/session/global for tools + skills).
//
// The store IS the state: a name in a channel's list = allowed at its
// recorded scope (populated when the operator allows at session/global);
// a name absent = the default allow-ONCE (prompt every time).

var permissionsState = null;

async function buildPermissionsPage(page, cfg) {
  page.innerHTML = '';
  let data = {profile: '', locked: false, store: {}, loaded_tools: [], loaded_skills: []};
  try {
    const r = await fetch('/permissions');
    data = await r.json();
  } catch (e) { /* keep defaults */ }
  permissionsState = data;

  const CH = {
    operator_channel: 'Operator',
    agent_channel: 'Agent',
    system_channel: 'System',
    global_channel: 'Global',
  };

  // ── THE THREE CHANNEL SECTIONS (per-name lists) ──
  for (const [ch, label] of Object.entries(CH)) {
    if (ch === 'global_channel') continue;
    const sec = document.createElement('div');
    sec.className = 'settings-section-panel';
    const head = document.createElement('div');
    head.className = 'settings-section-title';
    head.textContent = label + ' Channel';
    sec.appendChild(head);
    const desc = document.createElement('div');
    desc.className = 'desc';
    desc.textContent = ch === 'system_channel'
      ? 'The house\u2019s own machinery — entries default to allow-session (read-only).'
      : 'Allowed tools/skills by name — populated when allowed at session/global.';
    sec.appendChild(desc);
    // TOOLS list
    sec.appendChild(buildNameList(ch, 'tools', data));
    // SKILLS list
    sec.appendChild(buildNameList(ch, 'skills', data));
    page.appendChild(sec);
  }

  // ── THE GLOBAL CHANNEL (flags) ──
  const gsec = document.createElement('div');
  gsec.className = 'settings-section-panel';
  const ghead = document.createElement('div');
  ghead.className = 'settings-section-title';
  ghead.textContent = 'Global Channel';
  gsec.appendChild(ghead);
  const gdesc = document.createElement('div');
  gdesc.className = 'desc';
  gdesc.textContent = 'The global security level — Type (allow/deny/block) × Level (once/session/global) for tools + skills.';
  gsec.appendChild(gdesc);
  gsec.appendChild(buildGlobalFlags(data));
  page.appendChild(gsec);

  // THE SAVE (the tab footer calls this).
  window.__savePermissions = async function () {
    // The list changes already POST as they toggle — this is the
    // explicit save for the global flags.
    return true;
  };
}

// A bulleted name list for one channel/kind (tools or skills).
function buildNameList(channel, kind, data) {
  const box = document.createElement('div');
  box.className = 'permissions-namebox';

  const title = document.createElement('div');
  title.className = 'permissions-kind-title';
  title.textContent = kind === 'tools' ? 'Tools' : 'Skills';
  box.appendChild(title);

  const listed = new Set(data.store && data.store[channel] ? (data.store[channel][kind] || []) : []);
  const allNames = kind === 'tools' ? (data.loaded_tools || []) : (data.loaded_skills || []);

  if (!allNames.length && !listed.size) {
    const none = document.createElement('div');
    none.className = 'desc';
    none.textContent = '(none loaded)';
    box.appendChild(none);
    return box;
  }

  // The entries: the listed (allowed) ones first, then the unlisted ones.
  const names = [...new Set([...listed, ...allNames])];
  for (const name of names) {
    const row = document.createElement('div');
    row.className = 'permission-entry';

    const chip = document.createElement('span');
    chip.className = 'permission-chip' + (listed.has(name) ? ' allowed' : '');
    chip.textContent = name;
    row.appendChild(chip);

    const state = document.createElement('span');
    state.className = 'permission-state';
    state.textContent = listed.has(name) ? 'allowed' : 'default: prompt each time';
    row.appendChild(state);

    const toggle = document.createElement('button');
    toggle.className = 'permission-toggle' + (listed.has(name) ? ' on' : '');
    toggle.textContent = listed.has(name) ? 'Remove' : 'Allow';
    toggle.title = listed.has(name)
      ? 'Remove from the allowed list (back to prompt-each-time)'
      : 'Add to the allowed list (no re-prompt within the scope)';
    toggle.disabled = data.locked || channel === 'system_channel';
    toggle.addEventListener('click', async () => {
      const present = !listed.has(name);
      try {
        const r = await fetch('/permissions', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'entry', channel, kind, name, present}),
        });
        const d = await r.json();
        if (!d.ok) { alert('update failed: ' + (d.detail || 'unknown')); return; }
        if (present) { listed.add(name); toggle.classList.add('on'); toggle.textContent = 'Remove'; }
        else { listed.delete(name); toggle.classList.remove('on'); toggle.textContent = 'Allow'; }
        state.textContent = listed.has(name) ? 'allowed' : 'default: prompt each time';
        chip.classList.toggle('allowed', listed.has(name));
      } catch (e) { alert('update error: ' + e.message); }
    });
    row.appendChild(toggle);

    box.appendChild(row);
  }
  return box;
}

// The GLOBAL channel's flag pairs (tools + skills).
function buildGlobalFlags(data) {
  const box = document.createElement('div');
  box.className = 'permissions-global';
  const g = (data.store && data.store.global_channel) || {};

  for (const kind of ['tools', 'skills']) {
    const row = document.createElement('div');
    row.className = 'setting-row';
    const info = document.createElement('div');
    const lab = document.createElement('label');
    lab.textContent = kind === 'tools' ? 'Tools' : 'Skills';
    info.appendChild(lab);
    row.appendChild(info);

    const typeSel = document.createElement('select');
    typeSel.appendChild(document.createElement('option')).textContent = '— set —';
    for (const t of ['allow', 'deny', 'block']) {
      const opt = document.createElement('option');
      opt.value = t; opt.textContent = t;
      typeSel.appendChild(opt);
    }
    // NULL default (the CEO's 08-15 order): the global channel is
    // SKIPPED until the operator sets it here.
    typeSel.value = (g[kind] && g[kind].type) || '';
    row.appendChild(typeSel);

    const levelSel = document.createElement('select');
    for (const lv of ['once', 'session', 'global']) {
      const opt = document.createElement('option');
      opt.value = lv; opt.textContent = lv;
      levelSel.appendChild(opt);
    }
    levelSel.value = (g[kind] && g[kind].level) || 'session';
    row.appendChild(levelSel);

    const apply = document.createElement('button');
    apply.className = 'settings-save-btn';
    apply.textContent = 'Apply';
    apply.disabled = data.locked;
    apply.addEventListener('click', async () => {
      try {
        // Empty type → clear the global flags back to NULL (skipped).
        const typeVal = typeSel.value || '';
        const levelVal = levelSel.value || 'session';
        const r = await fetch('/permissions', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'global', kind,
                                type: typeVal, level: levelVal}),
        });
        const d = await r.json();
        alert(d.ok ? 'global ' + kind + ' permission updated' : 'update failed: ' + (d.detail || ''));
      } catch (e) { alert('update error: ' + e.message); }
    });
    row.appendChild(apply);

    box.appendChild(row);
  }
  return box;
}
