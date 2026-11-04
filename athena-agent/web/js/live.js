/* live.js — FUNCTION only: fetch, fill, wire.
 *
 * One file serves three pages, and each page declares which it is with <body data-page="…">. The
 * functions below are therefore all guarded by the presence of their host element: Home fills the channel
 * table and the resource cards, Chat fills the rail, the thread and the composer, Settings fills the
 * config key/values and the models table. Nothing here creates layout — repeated markup is a
 * <template> in the document, cloned and filled.
 */
const Live = (() => {
  const POLL_MS = 5000;
  const timers = [];
  const $ = (id) => document.getElementById(id);

  let resourcesGeneration = 0;
  let lastTypingText = '';
  const state = { page: 'home', channels: [], channel: null, personas: {}, accents: {}, tab: 'thread',
                  sending: false, signature: '', awaiting: [], attachments: [],
    threadMessages: [], threadLoaded: false, historyOldest: null, hasMore: false, historyBusy: false,
                  channelState: 'idle' };

  async function getJSON(url) {
    const resp = await fetch(url, { headers: { accept: 'application/json' } });
    if (!resp.ok) throw new Error(`${url} → HTTP ${resp.status}`);
    return resp.json();
  }

  async function postJSON(url, body) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    return data;
  }

  /* ---------- helpers ---------- */
  function fill(obj) {
    document.querySelectorAll('[data-fill]').forEach(node => {
      const key = node.dataset.fill;
      if (!(key in obj)) return;
      const value = obj[key];
      node.textContent = Array.isArray(value) ? value.join('; ') : String(value ?? '—');
    });
  }

  function fillPath(source, path) {
    return path.split('.').reduce((acc, key) => (acc && acc[key] !== undefined ? acc[key] : undefined), source);
  }

  function fillConfig(root) {
    document.querySelectorAll('[data-cfg]').forEach(node => {
      const path = node.dataset.cfg;
      // /config nests the values under `config` and `paths`; a page names the field it wants, so try
      // the path as written, then under each envelope — one lookup, three chances, no page-specific code
      let value = fillPath(root, path);
      if (value === undefined) value = fillPath(root.config || {}, path);
      if (value === undefined) value = fillPath(root.paths || {}, path);
      node.textContent = value === undefined || value === null || value === ''
        ? '—' : (typeof value === 'object' ? JSON.stringify(value) : String(value));
    });
  }

  function cloneInto(templateId, host, items, write) {
    const tpl = $(templateId);
    if (!tpl || !host) return;
    items.forEach(item => {
      const node = tpl.content.cloneNode(true);
      write(node, item);
      host.append(node);
    });
  }

  const setCell = (node, cell, value) => {
    const target = node.querySelector(`[data-cell="${cell}"]`);
    if (target) target.textContent = value ?? '—';
  };

  /* The typing indicator: a wave of ASCII dots, and the name of the agent that is thinking.
   *
   * It is driven by the channel's state, so it stops the moment the turn ends — an indicator that keeps
   * waving after the answer arrived is worse than none. Frames move the gap left to right, which reads
   * as a wave in a monospace font; a spinner would say "loading", this says "Default is thinking". */
  const WAVE_FRAMES = ['.  ', '.. ', '...', ' ..', '  .', ' ..', '...', '.. '];
  let waveTimer = null;
  let waveIndex = 0;

  function setTyping(channelState, queueDepth, awaiting, inboundFrom) {
    const out = $('typing');          // left: this agent is working
    const inn = $('typing-in');       // right: this agent is waiting on a peer
    const working = channelState === 'working';
    const active = working || channelState === 'queued';
    const peers = (awaiting || []).slice(0, 2);

    if (out) {
      out.hidden = !active;
      if (active) {
        const label = $('typing-label');
        if (label && label.textContent !== lastTypingText) {
          const name = state.channel || 'agent';
          if (out.dataset.channel && out.dataset.channel !== name) label.textContent = '';
          label.textContent = name.charAt(0).toUpperCase() + name.slice(1);
          label.style.setProperty('--agent', state.accents[state.channel] || '');
          out.dataset.channel = name;
          lastTypingText = label.textContent;
        }
        const suffix = out.querySelector('.typing-suffix');
        const sentence = channelState === 'queued' ? 'is queued' : 'is thinking';
        // re-writing the same words every 5 s made a screen reader announce "is thinking" for the whole turn
        if (suffix && suffix.textContent !== sentence) suffix.textContent = sentence;
      } else {
        // a hidden line keeps no name: the label and suffix are cleared so no agent is named while nothing runs
        const label = $('typing-label');
        if (label && label.textContent !== '') label.textContent = '';
        const suffix = out.querySelector('.typing-suffix');
        if (suffix) suffix.textContent = '';
        out.querySelectorAll('[data-wave]').forEach(wave => { wave.textContent = ''; });
        const queueNote = $('typing-queued');
        if (queueNote) {
          const waiting = Number(queueDepth || 0);
          queueNote.hidden = !waiting || !working;
          queueNote.textContent = waiting ? `· ${waiting} more waiting` : '';
        }
      }
    }

    if (inn) {
      const title = (name) => name.charAt(0).toUpperCase() + name.slice(1);
      const inbound = inboundFrom || "";
      // two kinds of arrival, and they read differently:
      //   someone sent this agent a message      → "<Peer> is responding …"   (an inbound connection)
      //   this agent asked someone and is waiting → "waiting on <Peer> …"      (an answer in flight)
      const show = Boolean(inbound) || peers.length > 0;
      inn.hidden = !show;
      if (!show) {
        const peer = $('typing-peer');
        if (peer) peer.textContent = '';
        inn.querySelectorAll('[data-wave]').forEach(wave => { wave.textContent = ''; });
      }
      if (show) {
        const peer = $('typing-peer');
        if (peer) {
          if (inbound) {
            peer.textContent = `${title(inbound)} is responding`;
            peer.style.setProperty('--agent', state.accents[inbound] || '');
          } else {
            peer.textContent = `waiting on ${title(peers[0])}`
              + (peers.length > 1 ? ` +${peers.length - 1}` : '');
            peer.style.setProperty('--agent', state.accents[peers[0]] || '');
          }
        }
      }
    }

    // one ticker for both lines, started only while something is visible and stopped when nothing is
    const anyVisible = (out && !out.hidden) || (inn && !inn.hidden);
    if (anyVisible && !waveTimer) {
      document.querySelectorAll('[data-wave]').forEach(wave => {
        wave.textContent = WAVE_FRAMES[waveIndex % WAVE_FRAMES.length];
      });
      waveTimer = setInterval(() => {
        waveIndex += 1;
        const frame = WAVE_FRAMES[waveIndex % WAVE_FRAMES.length];
        document.querySelectorAll('[data-wave]').forEach(wave => {
          if (!wave.closest('[hidden]')) wave.textContent = frame;
        });
      }, 160);
    } else if (!anyVisible && waveTimer) {
      clearInterval(waveTimer);
      waveTimer = null;
    }
  }

  const initials = (name) => (name || '?').trim().slice(0, 1).toUpperCase();
  const clock = (ts) => {
    if (!ts) return '';
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };
  const bytes = (n) => (n ? `${(n / 1e9).toFixed(2)} GB` : '—');

  /* ---------- header (every page) ---------- */
  async function refreshHealth() {
    try {
      const h = await getJSON('/health');
      fill({ provider: h.provider, model: h.model, window: h.window, version: h.version,
             status: h.status, warnings: (h.warnings || []).length ? h.warnings : 'none',
             version_line: `Athena ${h.version} · ${h.provider} · ${h.model}` });
    } catch {
      fill({ provider: 'offline', status: 'unreachable', version_line: 'Athena · offline' });
    }
  }

  /* ---------- channels: Home table + Chat rail ---------- */
  async function refreshLanes() {
    const table = $('channels-body');
    const rail = $('channel-list');
    if (!table && !rail) return;

    let data;
    try {
      data = await getJSON('/agents');
    } catch (err) {
      const message = `multiplexer unreachable: ${err.message}`;
      if (table) {
        table.replaceChildren();
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 8;
        td.className = 'error';
        td.textContent = message;
        tr.append(td);
        table.append(tr);
      }
      if (rail) {
        rail.replaceChildren();
        const li = document.createElement('li');
        li.className = 'error small';
        li.textContent = message;
        rail.append(li);
      }
      return;
    }

    state.channels = (data.channels || []).map(l => l.channel);
    state.personas = Object.fromEntries((data.agents || []).map(a => [a.channel, a.persona || '']));
    state.accents = Object.fromEntries((data.agents || []).map(a => [a.channel, a.accent || '']));
    if (state.page === 'chat' && (!state.channel || !state.channels.includes(state.channel))) {
      state.channel = state.channels[0] || 'default';
    }

    /* Home: one row per channel, with a link into Chat */
    if (table) {
      table.replaceChildren();
      if (!state.channels.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 8;
        td.className = 'muted';
        td.textContent = 'No channel has been used yet. Open Chat to start one.';
        tr.append(td);
        table.append(tr);
      } else {
        cloneInto('tpl-channel-row', table, data.channels || [], (node, l) => {
          setCell(node, 'channel', l.channel);
          setCell(node, 'turns', l.turns ?? 0);
          setCell(node, 'queue', l.queue_depth ?? 0);
          setCell(node, 'messages', l.session_messages ?? 0);
          setCell(node, 'last_status', l.last_status ?? '—');
          setCell(node, 'last_seconds', l.last_seconds ?? '—');
          const dot = node.querySelector('[data-cell="state"]');
          if (dot) {
            // `busy` is a boolean about the worker; `state` is the parked/waking/sleeping machine. Printing busy
            // showed a parked agent as idle while the rail beside it said sleeping.
            const stateName = l.state || (l.busy ? 'working' : 'idle');
            dot.textContent = stateName;
            dot.className = `dot ${stateName}`;
          }
          const link = node.querySelector('[data-cell="open"]');
          if (link) link.href = `html/chat.html?channel=${encodeURIComponent(l.channel)}`;
        });
      }
    }

    /* Chat: the rail */
    if (rail) {
      rail.replaceChildren();
      if (!state.channels.length) {
        const li = document.createElement('li');
        li.className = 'rail-empty muted small';
        li.textContent = 'no channels yet';
        rail.append(li);
      } else {
        cloneInto('tpl-rail-item', rail, data.channels || [], (node, channel) => {
          const button = node.querySelector('.rail-item');
          button.dataset.channel = channel.channel;
          setCell(node, 'channel', channel.channel);
          if (channel.accent) {
            button.dataset.agentColour = '1';
            button.style.setProperty('--agent', channel.accent);
          }
          button.classList.toggle('active', channel.channel === state.channel);
          const badge = node.querySelector('[data-cell="badge"], .rail-badge');
          // `channelState`, not `state`: a local named `state` shadows the module's state object in this
          // block, so reading `state.channel` above threw a TDZ ReferenceError and the rail silently emptied
          const channelState = channel.state || (channel.busy ? 'working' : 'idle');

          // the dot carries the state: green idle · blue working · orange queued · red error · yellow stale
          const dot = node.querySelector('[data-cell="state-dot"]');
          if (dot) {
            dot.dataset.state = wakingHeld() ? 'waking' : channelState;
            dot.title = channelState;
            dot.style.setProperty('--agent', channel.accent || '');
          }

          // the row reads: dot · name · status — the status is always present, so an idle agent says so
          // instead of leaving a gap the reader has to interpret
          const status = node.querySelector('[data-cell="status"]');
          if (status) {
            const backlog = Number(channel.queue_depth || 0);
            const shown = wakingHeld() && channelState !== 'sleeping' ? 'waking' : channelState;
            status.textContent = backlog && (shown === 'working' || shown === 'queued')
              ? `${shown} +${backlog}` : shown;
            status.dataset.state = shown;
          }
          button.addEventListener('click', () => selectChannel(channel.channel));
        });
      }
      const count = $('channel-count');
      if (count) count.textContent = String(state.channels.length);
      const title = $('channel-title');
      if (title) title.textContent = state.channel || 'default';
      const found = (data.channels || []).find(l => l.channel === state.channel);
      state.awaiting = (found && found.awaiting) || [];
      state.channelState = (found && found.state) || 'idle';
      const dot = $('channel-state');
      if (dot && found) {
        const channelState = found.state || (found.busy ? 'working' : 'idle');
        const shown = wakingHeld() && channelState !== 'sleeping' ? 'waking' : channelState;
        dot.textContent = shown;
        dot.className = `dot ${shown}`;
        dot.dataset.state = shown;
        setTyping(channelState, found.queue_depth, found.awaiting || [], found.inbound_from || "");
        const power = $('power');
        if (power) {
          power.textContent = channelState === 'sleeping' ? 'wake' : 'sleep';
          power.dataset.state = channelState;
          power.disabled = channelState === 'working' || channelState === 'queued';
          power.title = channelState === 'sleeping'
            ? 'wake and verify this channel' : 'park this channel until woken';
        }
        const input = $('chat-text');
        const send = $('chat-send');
        const asleep = channelState === 'sleeping';
        if (input) {
          // never disabled: a message is the wake signal, so the way to wake this channel is to use it
          input.placeholder = asleep
            ? 'This channel is asleep — sending a message wakes it.'
            : 'Message the channel…  (Enter to send, Shift+Enter for a new line)';
        }
        if (send) send.disabled = state.sending;
      } else if (!found) {
        setTyping('idle', 0, [], "");
      }
    }

    const tools = $('tools-line');
    if (tools) tools.textContent = `tools: ${(data.tools || []).join(', ') || '—'}`;
  }

  function selectChannel(name) {
    if (!name || name === state.channel) return;   // the clear below threw away every page scrolled into view
    state.threadLoaded = false;
    state.threadMessages = [];
    state.historyOldest = null;
    state.hasMore = false;
    state.signature = '';
    state.channel = name;
    state.signature = '';
    // the indicators describe the channel you were reading: on switch they must clear AT ONCE, not on the
    // next poll — otherwise the previous channel's "Scout is thinking" sits under default's messages
    setTyping('idle', 0, [], '');
    refreshLanes();
    document.querySelectorAll('.rail-item').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.channel === name);
    });
    const title = $('channel-title');
    if (title) title.textContent = name;
    refreshThread(true);
    refreshResources();
    refreshFooterCtx();
  }

  /* ---------- chat: the thread ---------- */
  /* ---------- thread: the whole conversation, bucketed by day ---------- */
  // `LATEST.jsonl` is only what the model is *sent*. The rest of the thread lives in compression snapshots and
  // is served by /history, so the pane can be read back to the beginning: one day-header per date, newest at
  // the bottom, and scrolling up asks for the next older page instead of loading everything at once.
  const msgKey = (m) => `${m.ts || ''}|${m.role || ''}|${(m.content || '').slice(0, 120)}`;

  function dayLabel(date) {
    if (!date || date === 'unknown') return 'undated';
    const d = new Date(`${date}T00:00:00`);
    return Number.isNaN(d.getTime())
      ? date
      : d.toLocaleDateString([], { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' });
  }

  function paintMessage(node, msg) {
    const role = msg.role || 'user';
    const direction = msg.direction || (role === 'assistant' ? 'outbound' : 'inbound');
    node.querySelector('.msg').classList.add(role, direction);

    // who spoke: this agent (outbound), the operator, or a peer agent that mentioned us
    const displayName = (channel) => state.personas[channel] || channel;
    const speaker = direction === 'outbound' ? state.channel : (msg.author || '');
    const colour = speaker ? (state.accents[speaker] || '') : '';
    if (colour) node.querySelector('.msg-body').style.setProperty('--agent', colour);
    const who = direction === 'outbound'
      ? displayName(state.channel || 'agent')
      : (msg.author ? `@${displayName(msg.author)}`
                    : (role === 'tool' ? `tool · ${msg.tool_name || 'result'}` : 'you'));
    setCell(node, 'avatar', initials(who.replace('@', '')));
    setCell(node, 'author', who);
    if (msg.author) {
      const authorNode = node.querySelector('[data-cell="author"]');
      if (authorNode) authorNode.dataset.peer = '1';      // inbound, but not the operator
    }
    setCell(node, 'time', clock(msg.ts));
    const body = msg.content || '';
    setCell(node, 'text', body);
    // a tool-call row is outbound with no prose: the call is the tag, the answer is the next message.
    if (!body) node.querySelector('.msg-text')?.remove();
    if (Array.isArray(msg.tool_calls) && msg.tool_calls.length) {
      const tag = node.querySelector('[data-cell="tag"]');
      if (tag) { tag.hidden = false; tag.textContent = `called ${msg.tool_calls.join(', ')}`; }
    }
    // archived rows are dimmed: they are readable history, not context the agent still holds
    if (msg.live === false) node.querySelector('.msg')?.classList.add('archived');
  }

  function renderThreadDays({ scroll = 'bottom' } = {}) {
    const thread = $('thread');
    if (!thread) return;
    const scroller = $('thread-scroll') || thread;
    const before = { top: scroller.scrollTop, height: scroller.scrollHeight };

    thread.replaceChildren();
    const msgs = state.threadMessages || [];
    if (!msgs.length) {
      const li = document.createElement('li');
      li.className = 'thread-empty muted';
      li.textContent = 'No messages yet. Say something below — the same turn the CLI runs.';
      thread.append(li);
      return;
    }

    const days = [];
    for (const m of msgs) {
      const date = m.date || String(m.ts || '').slice(0, 10) || 'unknown';
      if (!days.length || days[days.length - 1].date !== date) days.push({ date, messages: [] });
      days[days.length - 1].messages.push(m);
    }

    let dividerPlaced = false;
    const anyArchived = msgs.some(m => m.live === false);
    for (const day of days) {
      const head = document.createElement('li');
      head.className = 'day-head';
      head.dataset.date = day.date;
      const label = document.createElement('span');
      label.className = 'day-label';
      label.textContent = dayLabel(day.date);
      const count = document.createElement('span');
      count.className = 'day-count';
      count.textContent = `${day.messages.length} message${day.messages.length === 1 ? '' : 's'}`;
      head.append(label, count);
      thread.append(head);

      for (const msg of day.messages) {
        if (!dividerPlaced && msg.live && anyArchived) {
          const div = document.createElement('li');
          div.className = 'ctx-divider';
          div.textContent = 'context window starts here — everything above is archived history';
          thread.append(div);
          dividerPlaced = true;
        }
        cloneInto('tpl-message', thread, [msg], paintMessage);
      }
    }

    if (scroll === 'bottom') {
      scroller.scrollTop = scroller.scrollHeight;
    } else if (scroll === 'keep') {
      // prepending above the viewport must not move what the reader is looking at
      scroller.scrollTop = before.top + (scroller.scrollHeight - before.height);
    }
  }

  async function loadHistoryPage({ before = null, prepend = false, scroll = 'bottom' } = {}) {
    const params = new URLSearchParams({ days: String(prepend ? 1 : 3) });
    if (before) params.set('before', before);
    const data = await getJSON(`/agents/${encodeURIComponent(state.channel)}/history?${params}`);
    const incoming = [];
    for (const day of data.days || []) {
      for (const m of day.messages || []) incoming.push({ ...m, date: day.date });
    }
    const merged = prepend ? [...incoming, ...(state.threadMessages || [])] : incoming;
    const seen = new Set();
    state.threadMessages = merged
      .filter(m => { const k = msgKey(m); if (seen.has(k)) return false; seen.add(k); return true; })
      .sort((a, b) => String(a.ts || '').localeCompare(String(b.ts || '')));
    state.historyOldest = data.oldest_loaded || state.historyOldest;
    state.hasMore = Boolean(data.has_more);
    state.historyTotal = data.total_messages;
    renderThreadDays({ scroll });
    const note = $('thread-note');
    if (note) {
      const loaded = state.threadMessages.length;
      note.textContent = `${loaded} of ${data.total_messages} in this page` +
            (state.sessionMessages ? ` · ${state.sessionMessages} in the live window` : '')
        + (state.hasMore ? ' — scroll up for earlier days' : ' in this channel');
    }
  }

  async function refreshThread(forceScroll = false) {
    if (!state.channel) return;
    if (!state.threadLoaded) {
      try {
        await loadHistoryPage({ scroll: 'bottom' });
        state.threadLoaded = true;
      } catch (err) {
        const thread = $('thread');
        if (thread) {
          thread.replaceChildren();
          const li = document.createElement('li');
          li.className = 'error';
          li.textContent = `cannot load the thread: ${err.message}`;
          thread.append(li);
        }
      }
      return;
    }

    let data;
    try {
      data = await getJSON(`/agents/${encodeURIComponent(state.channel)}/messages?limit=200`);
    } catch { return; }

    // the live window is authoritative for what is in context, so replace those rows wholesale and keep the
    // archived days as loaded (they cannot change underneath: they are snapshots)
    const live = (data.messages || []).map(m => ({ ...m, live: true,
                                                   date: String(m.ts || '').slice(0, 10) }));
    const archived = (state.threadMessages || []).filter(m => m.live === false);
    const merged = [...archived, ...live];
    const seen = new Set();
    const next = merged
      .filter(m => { const k = msgKey(m); if (seen.has(k)) return false; seen.add(k); return true; })
      .sort((a, b) => String(a.ts || '').localeCompare(String(b.ts || '')));
    const signature = next.map(m => `${m.role}:${(m.content || '').length}:${m.ts}`).join('|');
    if (signature === state.signature && !forceScroll) return;
    state.signature = signature;
    state.threadMessages = next;

    const scroller = $('thread-scroll');
    const atBottom = !scroller || (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight) < 120;
    renderThreadDays({ scroll: forceScroll || atBottom ? 'bottom' : 'keep' });
  }

  /* ---------- resources: Home cards + Chat tab ---------- */
  async function refreshFooterCtx() {
    // Settings and New-agent showed a permanent "—": the figure was filled only inside refreshResources, which
    // returns early when the page has no resources panel. One lane is asked for, not all of them.
    if ($('resources-cards')) return;
    let lane = state.channel || (state.channels && state.channels[0]);
    if (!lane) {
      // Settings and New-agent never list the lanes, so the figure has nothing to measure until one is named.
      // Asking /agents for the roster is cheap and does not instantiate a channel.
      try {
        const payload = await getJSON('/agents');
        const rows = payload.agents || payload.channels || [];
        const names = rows.map(r => r.channel || r.name).filter(Boolean);
        if (names.length) { state.channels = names; lane = names[0]; }
      } catch (_) { /* the footer stays honest about not knowing */ }
    }
    if (!lane) { fill({ ctx_summary: 'no agent yet' }); return; }
    try {
      const r = await getJSON(`/agents/${encodeURIComponent(lane)}/resources`);
      const used = r.prompt_tokens_est ?? 0;
      const total = r.window || 32768;
      const pct = total ? (used / total) * 100 : 0;
      fill({ ctx_summary: `${pct.toFixed(1)}% · ${used.toLocaleString()} / ${total.toLocaleString()} tok` });
    } catch (_) { fill({ ctx_summary: '—' }); }
  }

  async function refreshResources() {
    const host = $('resources-cards');
    if (!host) return;

    const channel = state.page === 'chat' ? state.channel : (state.channels[0] || null);
    if (!channel) {
      if (!state.channels.length) {
        try { state.channels = ((await getJSON('/agents')).channels || []).map(l => l.channel); } catch { return; }
      }
      if (!state.channels.length) {
        host.replaceChildren();
        const p = document.createElement('p');
        p.className = 'muted';
        p.textContent = 'no agent has been used yet';
        host.append(p);
        fill({ ctx_summary: 'no agent yet' });
        return;
      }
    }

    // Home asks for ONE lane, not all of them: `/resources` instantiates a resident channel and worker thread, so
    // polling every profile from the overview page defeated the resident/on-disk distinction that page displays.
    const channels = state.page === 'chat' && state.channel ? [state.channel]
                   : state.page === 'home' ? state.channels.slice(0, 1)
                   : state.channels;
    const BLOCKS = ['system', 'assistant', 'user', 'summary', 'history', 'memory', 'output'];
    // one generation per run: two overlapping runs (a rail click plus the 10 s poll) both appended, so the panel
    // could show another agent's spend under the selected agent
    const gen = ++resourcesGeneration;
    host.replaceChildren();

    for (const name of channels) {
      let r;
      try { r = await getJSON(`/agents/${encodeURIComponent(name)}/resources`); } catch { continue; }
      if (gen !== resourcesGeneration) return;

      cloneInto('tpl-res-card', host, [r], (node, data) => {
        setCell(node, 'channel', data.channel);
        // `null` means "this channel has no turn in memory yet" — the route returns it after every restart. It is
        // NOT zero: `/metrics` still holds the history, so printing 0 claimed nothing was ever evicted or spent.
        setCell(node, 'turns', data.turns ?? '—');
        setCell(node, 'history_turns', data.history_turns ?? '—');
        setCell(node, 'dropped_turns', data.dropped_turns ?? '—');
        setCell(node, 'session_messages', data.session_messages ?? '—');
        const budget = data.budget || null;
        // the API sends `turns`; `turns_used` was never a field, so the panel always printed "calls ?/100"
        setCell(node, 'cost', budget
          ? `calls ${budget.turns ?? '?'}/${budget.max_turns ?? '?'} · cost $${(budget.cost_usd ?? 0).toFixed(4)}`
          : 'no turn yet this run · see metrics');

        // the footer asks "how full is the window right now", which is the one number that explains a
        // session that remembers less than it did yesterday
        if (name === (state.page === 'chat' ? state.channel : channels[0])) {
          const used = data.prompt_tokens_est ?? 0;
          const total = data.window || 32768;
          const pct = total ? (used / total) * 100 : 0;
          fill({ ctx_summary: `${pct.toFixed(1)}% · ${used.toLocaleString()} / ${total.toLocaleString()} tok` });
        }

        const windowTokens = data.window || 32768;
        cloneInto('tpl-bar', node.querySelector('[data-bars]'),
          Object.entries(data.allocated || {}).filter(([k]) => BLOCKS.includes(k)),
          (barNode, [key, tokens]) => {
            setCell(barNode, 'name', key);
            setCell(barNode, 'value', tokens);
            const fillBar = barNode.querySelector('[data-cell="fill"]');
            if (fillBar) fillBar.style.width = `${Math.min(100, (tokens / windowTokens) * 100).toFixed(1)}%`;
          });
      });
    }
  }

  /* ---------- settings ---------- */
  async function refreshSettings() {
    if (state.page !== 'settings') return;
    let data;
    try {
      data = await getJSON('/config');
    } catch (err) {
      const line = $('warnings-line');
      if (line) { line.className = 'error small'; line.textContent = `cannot read /config: ${err.message}`; }
      return;
    }

    fillConfig(data);
    const body = $('models-body');
    if (body) {
      body.replaceChildren();
      const models = data.installed_models || [];
      if (!models.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        td.className = 'muted';
        td.textContent = 'no models downloaded';
        tr.append(td);
        body.append(tr);
      } else {
        cloneInto('tpl-model-row', body, models, (node, m) => {
          setCell(node, 'name', m.name);
          setCell(node, 'format', m.format || '—');
          setCell(node, 'quant', m.quant || '—');
          setCell(node, 'size', bytes(m.bytes));
          setCell(node, 'repo', m.repo || '—');
        });
      }
    }

    // the agents block: who exists, what they answer with, and a way in
    const agentBody = $('cfg-agents');
    if (agentBody) {
      agentBody.replaceChildren();
      try {
        const list = (await getJSON('/agents')).agents || [];
        if (!list.length) {
          const tr = document.createElement('tr');
          const td = document.createElement('td');
          td.colSpan = 5;
          td.className = 'muted';
          td.textContent = 'no agents yet';
          tr.append(td);
          agentBody.append(tr);
        } else {
          list.forEach(agent => {
            const tr = document.createElement('tr');
            const cells = [agent.channel, agent.persona || '—',
                           agent.provider || 'global', agent.model || 'global'];
            cells.forEach((text, index) => {
              const td = document.createElement('td');
              if (index === 0) td.className = 'strong';
              td.textContent = text;
              tr.append(td);
            });
            const td = document.createElement('td');
            const link = document.createElement('a');
            link.className = 'row-link';
            link.href = `chat.html?channel=${encodeURIComponent(agent.channel)}`;
            link.textContent = 'chat →';
            td.append(link);
            tr.append(td);
            agentBody.append(tr);
          });
        }
      } catch { /* the warnings line already reports an unreachable server */ }
    }

    const warnings = $('warnings-line');
    if (warnings) {
      const list = data.warnings || [];
      warnings.className = list.length ? 'error small' : 'muted small';
      warnings.textContent = list.length ? `config warnings: ${list.join('; ')}` : 'no config warnings';
    }
  }

  /* ---------- chat: sleep / wake ---------- */
  // a transition the reader cannot read is not shown: waking usually finishes in ~200 ms, so the paint is
  // held at a legible minimum while the server's own state is still corrected underneath
  const WAKING_HOLD_MS = 900;
  let wakingUntil = 0;

  function markWaking() {
    wakingUntil = Date.now() + WAKING_HOLD_MS;
    markWakingPaint();
    setTimeout(() => { wakingUntil = 0; refreshLanes(); }, WAKING_HOLD_MS + 40);
  }

  function wakingHeld() {
    return Date.now() < wakingUntil;
  }

  function markWakingPaint() {
    // optimistic: waking can finish between two polls, and a state the reader never sees is the same as
    // no state at all — so it is painted locally and corrected by the next refresh
    state.channelState = 'waking';
    const status = document.querySelector('.rail-item.active .rail-status');
    if (status) { status.textContent = 'waking'; status.dataset.state = 'waking'; }
    const dot = document.querySelector('.rail-item.active .rail-dot');
    if (dot) dot.dataset.state = 'waking';
    const header = $('channel-state');
    if (header) { header.textContent = 'waking'; header.className = 'dot waking'; }
  }

  async function togglePower() {
    const button = $('power');
    const meta = $('chat-meta');
    const sleeping = state.channelState === 'sleeping';
    button.disabled = true;
    if (sleeping) markWaking();
    if (meta) meta.textContent = sleeping ? 'waking…' : 'parking…';
    try {
      const path = sleeping ? 'wake' : 'sleep';
      const result = await postJSON(`/agents/${encodeURIComponent(state.channel)}/${path}`, {});
      if (sleeping) {
        const checks = result.checks || {};
        const summary = Object.entries(checks)
          .map(([name, item]) => `${name} ${item.ok ? 'ok' : 'FAILED'}`).join(' · ');
        // the wake is a verification, so say what it verified — a bare "awake" would hide a failure
        if (meta) meta.textContent = result.awake
          ? `awake · ${summary}`
          : `wake failed · ${result.detail || summary}`;
      } else if (meta) {
        meta.textContent = 'asleep — no work will be accepted until it is woken';
      }
      await refreshLanes();
      await refreshThread(true);
    } catch (err) {
      if (meta) meta.textContent = `${sleeping ? 'wake' : 'sleep'} failed: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  }

  /* ---------- chat: attachments ---------- */
  function renderAttachments() {
    const list = $('attach-list');
    const button = $('chat-attach');
    if (!list) return;
    list.replaceChildren();
    state.attachments.forEach((file, index) => {
      const li = document.createElement('li');
      li.className = 'attach-chip';
      const name = document.createElement('span');
      name.textContent = `${file.name} · ${Math.max(1, Math.round(file.bytes / 1024))} KB`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.title = 'remove';
      remove.addEventListener('click', () => {
        state.attachments.splice(index, 1);
        renderAttachments();
      });
      li.append(name, remove);
      list.append(li);
    });
    if (button) button.classList.toggle('has-files', state.attachments.length > 0);
  }

  function attachError(message) {
    const list = $('attach-list');
    if (!list) return;
    const li = document.createElement('li');
    li.className = 'attach-error';
    li.textContent = message;
    list.append(li);
  }

  async function uploadFiles(files) {
    const problems = [];
    for (const file of files) {
      try {
        const resp = await fetch(`/agents/${encodeURIComponent(state.channel)}/attach`, {
          method: 'POST',
          headers: { 'X-Filename': encodeURIComponent(file.name),
                     'Content-Type': file.type || 'application/octet-stream' },
          body: file,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
        state.attachments.push({ name: data.name || file.name, path: data.path, bytes: data.bytes });
      } catch (err) {
        problems.push(`${file.name}: ${err.message}`);
      }
    }
    renderAttachments();                 // clears the list, so errors must be written AFTER it or they vanish
    problems.forEach(attachError);
  }

  function wireAttach() {
    const input = $('chat-file');
    const button = $('chat-attach');
    if (!input || !button) return;
    button.addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
      const files = Array.from(input.files || []);
      input.value = '';
      if (files.length) await uploadFiles(files);
    });
    renderAttachments();
  }

  /* ---------- chat: sending ---------- */
  async function sendTurn() {
    const input = $('chat-text');
    const meta = $('chat-meta');
    const button = $('chat-send');
    const typed = (input?.value || '').trim();
    const attached = state.attachments.slice();
    if ((!typed && !attached.length) || state.sending) return;

    // the model needs the paths, not the chips: an attachment is only useful if the turn names it
    const manifest = attached.length
      ? "\n\n[attached files — open one with read_file(path)]\n"
        + attached.map(f => `- ${f.name} → ${f.path} (${f.bytes} bytes)`).join("\n")
      : "";
    const text = (typed || "(see the attached files)") + manifest;

    state.sending = true;
    button.disabled = true;
    input.value = '';
    state.attachments = [];
    renderAttachments();
    const waking = state.channelState === 'sleeping';
    if (waking) markWaking();
    if (meta) meta.textContent = waking
      ? `waking ${state.channel} to take this message…`
      : `sent · waiting on ${state.channel} (a local model answers ~1 token/s)`;
    setTyping('working', 0, state.awaiting || [], '');   // show it now; the poll confirms a moment later
    const scroller = $('thread-scroll');
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
    await refreshThread(true);

    try {
      const result = await postJSON(`/agents/${encodeURIComponent(state.channel)}/turn`, { text });
      if (meta) {
        meta.textContent = `${result.status} · ${result.calls ?? '?'} call(s) · ` +
          `${result.tool_rounds ?? 0} tool round(s) · ${result.seconds ?? '?'} s`;
      }
      await refreshThread(true);
      await refreshLanes();
      await refreshResources();
      await refreshFooterCtx();
    } catch (err) {
      if (meta) meta.textContent = `failed: ${err.message}`;
      input.value = text;
    } finally {
      state.sending = false;
      button.disabled = false;
      input.focus();
    }
  }

  async function createLane(name) {
    const clean = (name || '').trim().replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 32);
    if (!clean) return;
    // creating a lane is a POST to /agents; the server validates the label and mints the id
    let problem = '';
    try {
      const resp = await fetch('/agents', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: clean }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        problem = body.detail || `could not create ${clean} (${resp.status})`;
      }
    } catch (err) {
      problem = `could not create ${clean}: ${err}`;
    }
    if (problem) {
      state.lastError = problem;
      if (typeof toast === 'function') toast(problem); else console.warn(problem);
      await refreshLanes();
      return;
    }
    state.channel = clean;
    state.signature = '';
    await refreshLanes();
    await refreshThread(true);
    await refreshResources();
    await refreshFooterCtx();
  }

  /* ---------- boot ---------- */
  function start() {
    state.page = document.body.dataset.page || 'home';

    if (state.page === 'chat') {
      $('power')?.addEventListener('click', togglePower);
      const requested = new URLSearchParams(location.search).get('channel');
      if (requested) state.channel = requested;
      $('chat-send')?.addEventListener('click', (event) => { event.preventDefault(); sendTurn(); });
      $('composer')?.addEventListener('submit', (event) => { event.preventDefault(); sendTurn(); });
      $('channel-new')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = $('channel-new-name');
        await createLane(input?.value || '');
        if (input) input.value = '';
      });
    }

    wireAttach();
    refreshHealth();
    refreshLanes().then(() => {
      if (state.page === 'chat') { refreshThread(true); }
      refreshResources();
      refreshFooterCtx();
    });
    if (state.page === 'settings') refreshSettings();

    const scroller = $('thread-scroll');
    if (scroller) {
      scroller.addEventListener('scroll', () => {
        if (scroller.scrollTop > 80 || !state.hasMore || state.historyBusy) return;
        state.historyBusy = true;
        loadHistoryPage({ before: state.historyOldest, prepend: true, scroll: 'keep' })
          .catch(() => {})
          .finally(() => { state.historyBusy = false; });
      });
    }
    timers.push(setInterval(refreshHealth, POLL_MS));
    timers.push(setInterval(refreshLanes, POLL_MS));
    if (state.page === 'chat') timers.push(setInterval(() => refreshThread(), POLL_MS));
    timers.push(setInterval(refreshResources, POLL_MS * 2));
    if (state.page === 'settings') timers.push(setInterval(refreshSettings, POLL_MS * 4));
  }

  return { start, selectChannel, sendTurn, state, POLL_MS };
})();
