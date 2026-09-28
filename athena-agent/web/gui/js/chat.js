// ── Chat: COMBINED HISTORY (the Operator's spec) ──────────────────────────
// THE CHASING-DOT ICON (the Operator's 08-12 spec): a small 3x3 dot grid
// in the Thinking block's header. The CENTER dot is always lit; the 8
// surrounding dots chase clockwise around the ring while the turn runs.
// 3 lines of 3 chars: ● = lit, · = dim.
const CHASE_FRAMES = [
  ['●··','·●·','···'],  // top-left
  ['·●·','·●·','···'],  // top-mid
  ['··●','·●·','···'],  // top-right
  ['···','·●●','···'],  // mid-right
  ['···','·●·','··●'],  // bottom-right
  ['···','·●·','·●·'],  // bottom-mid
  ['···','·●·','●··'],  // bottom-left
  ['···','●●·','···'],  // mid-left
];
const DOT3X3_FRAMES = CHASE_FRAMES;  // legacy alias

function scrollChatToBottom() {
  // The SCROLLABLE element is the combined history panel.
  const panel = $('chat-panels');
  if (panel) panel.scrollTop = panel.scrollHeight;
}

// THE MARKDOWN RENDERER (the Operator's 08-12 spec): message content is
// stored as RAW markdown in the session db and rendered faithfully on
// display. A small local renderer covers the common forms (headings,
// bold/italic, code, lists, links, inline code) — output is escaped so
// tool output can never inject HTML.
function mdToHtml(src) {
  if (!src) return '';
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  let out = esc(String(src));
  // code blocks first (```...```) — keep raw inside <pre><code>
  out = out.replace(/```([\s\S]*?)```/g, (m, code) =>
    '<pre class="md-pre"><code>' + code + '</code></pre>');
  // inline code
  out = out.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  // headings
  out = out.replace(/^##### (.*)$/gm, '<h5>$1</h5>')
           .replace(/^#### (.*)$/gm, '<h4>$1</h4>')
           .replace(/^### (.*)$/gm, '<h3>$1</h3>')
           .replace(/^## (.*)$/gm, '<h2>$1</h2>')
           .replace(/^# (.*)$/gm, '<h1>$1</h1>');
  // bold + italic
  out = out.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>')
           .replace(/\*([^*\n]+)\*/g, '<i>$1</i>');
  // links
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  // unordered lists
  out = out.replace(/^\s*[-*] (.*)$/gm, '<li>$1</li>')
           .replace(/(<li>[\s\S]*?<\/li>)(?![\s\S]*?<li>)/g,
             '<ul>$1</ul>');
  // line breaks + paragraphs
  out = out.replace(/\n{2,}/g, '</p><p>');
  out = out.replace(/\n/g, '<br>');
  return '<p>' + out + '</p>';
}

function addMsg(role, text) {
  // The COMBINED column: every message appends to ONE history, in the
  // order it happened — the opposite side alternates naturally.
  const col = $('chat-history');
  const m = document.createElement('div');
  m.className = 'msg ' + role;
  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = role === 'user' ? 'You' : 'Athena';
  m.appendChild(who);
  // The TEXT as its own block (the Operator's 08-12 fix): the bubble is
  // a flex column — the who-label on top, the message text below, so
  // the text wraps cleanly and never runs against the label. Rendered
  // as MARKDOWN (the 08-12 spec) — stored raw, displayed faithfully.
  const body = document.createElement('span');
  body.className = 'msg-body md';
  body.innerHTML = mdToHtml(text);
  m.appendChild(body);
  col.appendChild(m);
  scrollChatToBottom();
}

// THE THINKING FLOW (the Operator's 08-12 spec): the
// turn's calls render as a compact "Thinking" block between the message
// and the response. Each call: ⚙️ system / 🛠️ tool / 🖊️ skill + name +
// args preview + result preview ("✅ Tool N completed - result").
function startThinking() {
  // THE MERGED THINKING BLOCK (the Operator's 08-12 spec): ONE element —
  // the chasing-dots icon + "Thinking" label in a clickable header, the
  // flow rows below. No separate dots bubble: the block IS the indicator.
  // THE TURN WRAPPER (the Operator's 08-15 spec): each agent turn is a
  // COLUMN container — the thinking block on TOP, the response BELOW —
  // so the order is ALWAYS Thinking → Response (never side-by-side).
  const wrap = document.createElement('div');
  wrap.className = 'turn assistant';
  const m = document.createElement('div');
  m.className = 'msg thinking live';
  const head = document.createElement('div');
  head.className = 'thinking-head';
  // The small 3x3 chasing-dot icon (center lit, ring chases clockwise).
  const pre = document.createElement('pre');
  pre.className = 'dots3x3';
  pre.textContent = CHASE_FRAMES[0].join('\n');
  head.appendChild(pre);
  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = 'Thinking';
  head.appendChild(who);
  const chev = document.createElement('span');
  chev.className = 'thinking-chev';
  chev.textContent = '▾';
  head.appendChild(chev);
  m.appendChild(head);
  const rows = document.createElement('div');
  rows.className = 'flow-rows';
  m.appendChild(rows);
  // Click the header to collapse/expand the block (persisted after done).
  head.addEventListener('click', () => {
    const collapsed = m.classList.toggle('collapsed');
    chev.textContent = collapsed ? '▸' : '▾';
  });
  wrap.appendChild(m);
  $('chat-history').appendChild(wrap);
  scrollChatToBottom();
  // The chase animation: center dot stays lit, the ring chases clockwise.
  let i = 0;
  const timer = setInterval(() => {
    i = (i + 1) % CHASE_FRAMES.length;
    pre.textContent = CHASE_FRAMES[i].join('\n');
  }, 120);
  m._thinkingTimer = timer;
  m._flowRows = rows;
  m._turnWrap = wrap;
  return m;
}

function stopThinking(m) {
  if (!m) return;
  if (m._thinkingTimer) clearInterval(m._thinkingTimer);
  // THE EXPAND-BY-DEFAULT (the Operator's 08-15 spec): while the reply
  // types, the thinking block STAYS EXPANDED — the operator watches the
  // reasoning/tool rows fill. Only the FINAL turn end (finishTurn)
  // collapses it.
  m.classList.remove('live');
  // THE RE-OPEN MARK (the Operator's 08-15 spec): the block is CLOSED —
  // a later 'call' event after a reply opens a NEW block (the loop
  // continuing its work).
  m._closed = true;
}

function finishTurn(m) {
  // THE 08-15 SPEC: when the whole turn ends (the final response landed),
  // collapse the thinking block by default — the operator can expand it
  // to see the full reasoning + tool/skill calls. Never removed.
  // THE EMPTY-BLOCK REMOVAL (the 08-15 fix): a block with NO rows and NO
  // reasoning was created optimistically at send but the turn did no
  // visible work (a plain reply) — an empty block persisting in the
  // history is noise. Remove its whole wrapper instead of collapsing —
  // BUT only when the wrapper holds ONLY the empty block (no reply
  // bubble landed inside it — a reply means the turn DID produce
  // output, so the block removal must not delete the response).
  if (!m) return;
  const rows = m._flowRows;
  const hasContent = rows && rows.childElementCount > 0;
  const hasReason = m.querySelector('.flow-row.reason');
  const wrap = m._turnWrap;
  const hasReply = wrap && wrap.querySelector('.msg.assistant');
  if (!hasContent && !hasReason) {
    // An EMPTY block is noise. Remove the BLOCK element itself; if a
    // reply bubble is inside the wrapper it STAYS (the response is real
    // output — only the pointless empty block goes). If no reply, the
    // whole wrapper goes (nothing was produced at all).
    if (wrap && wrap.parentNode) {
      if (hasReply) {
        m.parentNode.removeChild(m);
      } else {
        wrap.parentNode.removeChild(wrap);
      }
    }
    return;
  }
  m.classList.add('collapsed');
  const chev = m.querySelector('.thinking-chev');
  if (chev) chev.textContent = '▸';
}

// THE RESPONSE LIFECYCLE lives INSIDE sendChat (startResponse/stopResponse
// are closures over the local refs — the 08-15 scope fix). The old
// module-scope _resetLiveRefs is GONE: it could not see the local
// _liveReply/_liveReplyBody/_liveReason bindings, so it silently did
// nothing — the "same response block reused" bug.

// Load the current session's chat history in PAGES (the Operator's spec:
// 100 messages at a time from the session .db). Newest page loads first
// at the bottom; "load older" fetches the previous page and prepends it.
const PAGE_SIZE = 100;
let CHAT_SESSION = '';     // the session being viewed
let _followLive = true;    // THE 08-15 FIX: auto-follow the server's
                           // current session UNTIL the operator manually
                           // picks a session — then stay on the choice
                           // (the "swaps back to newest" bug was the tick
                           // overriding a manual selection).
let CHAT_OFFSET = 0;       // how many newest messages are already shown
let CHAT_TOTAL = 0;


async function loadChatHistory() {
  // Load the NEWEST page (offset 0) for the current session.
  const sid = CHAT_SESSION || '';
  try {
    const res = await fetch('/chat/history?offset=0&limit=' + PAGE_SIZE +
                            '&session_id=' + encodeURIComponent(sid));
    const d = await res.json();
    CHAT_TOTAL = d.total || 0;
    CHAT_OFFSET = (d.messages || []).length;
    CHAT_SESSION = d.session_id || CHAT_SESSION;
    renderChatPage(d.messages || [], /*prepend=*/false);
    updateLoadMore();
  } catch (e) { /* server mid-boot */ }
}

async function loadOlderPage() {
  const sid = CHAT_SESSION || '';
  try {
    const res = await fetch('/chat/history?offset=' + CHAT_OFFSET +
                            '&limit=' + PAGE_SIZE +
                            '&session_id=' + encodeURIComponent(sid));
    const d = await res.json();
    CHAT_OFFSET += (d.messages || []).length;
    CHAT_TOTAL = d.total || 0;
    renderChatPage(d.messages || [], /*prepend=*/true);
    updateLoadMore();
  } catch (e) { /* ignore */ }
}

function renderOneMessage(msg) {
  // THE SINGLE-MESSAGE RENDERER (the Operator's 08-12 sectional rebuild):
  // one message → one DOM node (bubble + its persisted thinking block).
  // Used by BOTH the full render AND the delta-append — no duplicated
  // rendering logic, so a partial rebuild looks identical to a full one.
  // THE TURN WRAPPER (the 08-15 spec): an assistant turn with a flow is a
  // COLUMN — the thinking block on TOP, the reply BELOW — so the order
  // is ALWAYS Thinking → Response, never side-by-side (the old code put
  // the thinking block INSIDE the flex-row msg-wrap → the block sat to
  // the RIGHT of the reply).
  const isUser = msg.role === 'user';
  const hasFlow = !isUser && (msg.flow || msg.reason);
  const wrap = document.createElement('div');
  if (hasFlow) {
    wrap.className = 'turn assistant';
  } else {
    wrap.className = 'msg-wrap ' + (isUser ? 'user' : 'assistant');
  }
  // THE PERSISTED THINKING BLOCK FIRST (the 08-15 order: Thinking before
  // the Response).
  if (hasFlow) {
    const tb = document.createElement('div');
    tb.className = 'msg thinking collapsed';
    const head = document.createElement('div');
    head.className = 'thinking-head';
    const pre = document.createElement('pre');
    pre.className = 'dots3x3';
    pre.textContent = CHASE_FRAMES[0].join('\n');
    head.appendChild(pre);
    const who2 = document.createElement('span');
    who2.className = 'who';
    who2.textContent = 'Thinking';
    head.appendChild(who2);
    const chev = document.createElement('span');
    chev.className = 'thinking-chev';
    chev.textContent = '▸';
    head.appendChild(chev);
    tb.appendChild(head);
    const rows = document.createElement('div');
    rows.className = 'flow-rows';
    tb.appendChild(rows);
    tb._flowRows = rows;   // appendFlowRow targets this container
    // The reasoning chain first (dimmed), then the calls.
    if (msg.reason) {
      const r = document.createElement('div');
      r.className = 'flow-row reason';
      r.textContent = '🧠 ' + msg.reason;
      rows.appendChild(r);
    }
    for (const call of (msg.flow || [])) {
      appendFlowRow(tb, call);
    }
    // Rebind the collapse handler.
    head.addEventListener('click', () => {
      const collapsed = tb.classList.toggle('collapsed');
      chev.textContent = collapsed ? '▸' : '▾';
    });
    wrap.appendChild(tb);
  }
  const m = document.createElement('div');
  m.className = 'msg ' + (isUser ? 'user' : 'assistant');
  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = isUser ? 'You' : 'Athena';
  m.appendChild(who);
  // MARKDOWN (the Operator's 08-12 spec): content stored raw, rendered.
  const body = document.createElement('span');
  body.className = 'msg-body md';
  body.innerHTML = mdToHtml(msg.content);
  m.appendChild(body);
  wrap.appendChild(m);
  return wrap;
}

function renderChatPage(msgs, prepend) {
  // THE COMBINED HISTORY (the Operator's spec): ONE column, true order —
  // whoever started the conversation is line 1, then the opposite side
  // alternates. The server returns messages in chronological order, so
  // a single append (or prepend for older pages) preserves the order.
  const col = $('chat-history');
  const panel = $('chat-panels');
  // THE SCROLL-POSITION STITCH (the Operator's spec): when older messages
  // are prepended, the scrollbar must STAY in the same visual place —
  // record the position before, then offset by the added height.
  let prevScroll = 0;
  if (prepend && panel) {
    prevScroll = panel.scrollTop + (panel.scrollHeight - panel.clientHeight);
  }
  if (!prepend) {
    col.innerHTML = '';
  }
  const frag = document.createDocumentFragment();
  for (const msg of msgs) {
    frag.appendChild(renderOneMessage(msg));
  }
  if (prepend) {
    col.insertBefore(frag, col.firstChild);
    // Restore the visual position: the added height shifts everything;
    // set scrollTop so the same message stays under the same pixel.
    if (panel) {
      const newHeight = panel.scrollHeight - panel.clientHeight;
      panel.scrollTop = newHeight - (prevScroll);
      if (panel.scrollTop < 0) panel.scrollTop = 0;
    }
  } else {
    col.appendChild(frag);
  }
  // Scroll to the newest (bottom) when loading the first page.
  if (!prepend) {
    scrollChatToBottom();
  }
}

function updateLoadMore() {
  // THE LOAD-OLDER PILL (the Operator's spec): hoverable, centered at the
  // top of the body — the approval-popup pattern for history. It POPS
  // UP only when the user scrolls all the way UP (the history window is
  // the newest 100; older pages are stitched on top when at the top).
  const wrap = $('chat-load-more-wrap');
  if (!wrap) return;
  const panel = $('chat-panels');
  const atTop = !panel || panel.scrollTop <= 2;
  const hasOlder = CHAT_OFFSET < CHAT_TOTAL;
  wrap.style.display = (atTop && hasOlder) ? 'block' : 'none';
}

function onChatScroll() {
  // The scroll listener: the pill appears when the user reaches the
  // very top AND older messages exist; it disappears otherwise.
  updateLoadMore();
}
$('chat-panels')?.addEventListener('scroll', onChatScroll, {passive: true});
$('chat-load-more')?.addEventListener('click', loadOlderPage);

$('session-select').addEventListener('change', e => {
  const sid = e.target.value;
  if (!sid) return;
  // THE MANUAL-SESSION FIX (the 08-15 bug): the operator EXPLICITLY chose
  // this session — the live tick must NOT snap back to the newest. The
  // flag stops the auto-follow until the page reloads (or the operator
  // picks the current session again, which re-enables follow).
  CHAT_SESSION = sid;
  // Re-enable follow ONLY when the operator picked the server's current
  // (live) session — otherwise stay on the manual choice.
  try {
    fetch('/sessions/current').then(r => r.json()).then(d => {
      _followLive = !d.current || d.current === sid;
    }).catch(() => { _followLive = false; });
  } catch (e) { _followLive = false; }
  loadChatHistory();
});

// THE LIVE TICK (the Operator's 08-12 spec): the chat
// page updates instead of waiting for a manual refresh — new sessions,
// interrupts, completed turns, and history changes surface immediately.
// THE 08-15 AUDIT: the tick polled EVERY 1s even when idle (2 network
// calls per second). Now it backs off to 3s when no turn is live (the
// stream renders itself during a turn — the fast cadence is only needed
// right after activity). The tick is a SELF-RESCHEDULING loop (a named
// function + setTimeout) so the cadence is truly dynamic.
let _tickMs = 1000;

// THE CHAT TICK: runs the sync body once, then reschedules itself at
// the current cadence (1s → 3s idle).
async function chatTick() {
  const ws = document.getElementById('chat-ws');
  if (!ws || !ws.classList.contains('active')) {
    setTimeout(chatTick, 3000);
    return;
  }
  if (_chatBusy) {
    // A stream is live — it renders itself; slow to idle cadence.
    _tickMs = 3000;
    setTimeout(chatTick, _tickMs);
    return;
  }
  try {
    const res = await fetch('/sessions/current');
    const d = await res.json();
    // THE 08-15 FIX: only auto-follow when the operator has NOT manually
    // picked a session. A manual selection (the dropdown) sets
    // _followLive=false — the tick then keeps syncing the VIEWED session
    // instead of snapping back to the server's current (newest).
    if (_followLive && d.current && d.current !== CHAT_SESSION) {
      // The loop moved to a new session (e.g. a Home-page start) —
      // follow it so the chat always shows the live conversation.
      CHAT_SESSION = d.current;
      if (typeof loadSessionDropdown === 'function') loadSessionDropdown();
      loadChatHistory();
    } else {
      // THE SECTIONAL APPEND (the Operator's 08-12 release fix): when
      // the session's .db is appended, sync ONLY the new messages to the
      // website — never a full rebuild (the flashing effect). The rolling
      // window stays; older history loads only by scrolling to the top.
      const hres = await fetch('/chat/history?offset=0&limit=1&session_id=' +
                               encodeURIComponent(CHAT_SESSION || ''));
      const hd = await hres.json();
      const totalNow = hd.total || 0;
      if (totalNow > CHAT_TOTAL) {
        // The delta: how many NEW messages arrived since we last synced.
        const delta = totalNow - CHAT_TOTAL;
        if (delta > 0 && delta <= PAGE_SIZE) {
          // SECTIONAL: fetch ONLY the new messages + append them to the
          // existing DOM. No innerHTML='', no re-render of what's shown —
          // existing bubbles, thinking-block expand states, scroll, and
          // any loaded-older pages are ALL untouched (no flash).
          const dres = await fetch('/chat/history?offset=0&limit=' + delta +
                                   '&session_id=' +
                                   encodeURIComponent(CHAT_SESSION || ''));
          const dd = await dres.json();
          const newMsgs = (dd.messages || []);
          const col = $('chat-history');
          const frag = document.createDocumentFragment();
          for (const msg of newMsgs) {
            frag.appendChild(renderOneMessage(msg));
          }
          col.appendChild(frag);
          CHAT_TOTAL = totalNow;
          CHAT_OFFSET += newMsgs.length;
          scrollChatToBottom();
          updateLoadMore();
        } else if (delta > PAGE_SIZE) {
          // A big catch-up (the page was backgrounded) — one full reload
          // beats appending hundreds of nodes one-by-one.
          loadChatHistory();
        }
      }
    }
  } catch (e) { /* server mid-boot */ }
  // THE IDLE BACKOFF: ease 1s → 3s as the chat sits idle.
  if (_tickMs < 3000) _tickMs += 500;
  setTimeout(chatTick, _tickMs);
}
// THE TICK KICKOFF: the self-rescheduling loop starts at the fast
// cadence (the first seconds are the active ones).
setTimeout(chatTick, 1000);

let _chatBusy = false;   // a turn is running — the next message interrupts
let _streamCancel = false;  // THE 08-15 INTERRUPT FIX: set when a new
                            // message cuts the running turn — the OLD
                            // stream stops painting immediately (no
                            // residual delta fragments like "pro / ce / ed").
let _sendSeq = 0;        // each send gets a unique id; an older stream
                         // whose id != the current id is CANCELLED.
async function sendChat() {
  let text = $('chat-input').value.trim();
  if (!text) return;
  // THE EMOJI SHORTHAND EXPANSION (the Operator's 08-15 spec): a typed
  // ":heart:" becomes ❤️ at send time even if the picker wasn't used.
  text = text.replace(/:([a-z_]+):/gi, (m, name) => EMOJI_MAP[name.toLowerCase()] || m);
  $('chat-input').value = '';
  // Reset the composer height after send (the 08-15 fix): back to one
  // line — the auto-grow left it expanded from the multi-line message.
  const _ci = $('chat-input');
  if (_ci) { _ci.style.height = 'auto'; _ci.style.height = '42px'; }
  // Reset the md preview after send too.
  const _pre = $('chat-md-preview');
  if (_pre) { _pre.style.display = 'none'; _pre.innerHTML = ''; }
  // THE SYSTEM COMMANDS (the Operator's 08-15 spec): typed commands are
  // handled HERE — never sent to the model.
  const lower = text.toLowerCase().trim();
  if (lower === 'stop' || lower === '/stop' || lower === '!stop'
      || lower === 'interrupt' || lower === '/interrupt' || lower === '!interrupt') {
    addMsg('user', text);
    if (_chatBusy) {
      _streamCancel = true;
      try { await fetch('/chat/interrupt', {method: 'POST'}); } catch (e) { /* best-effort */ }
      addMsg('assistant', 'Interrupted — the running turn has been stopped.');
    } else {
      addMsg('assistant', 'Nothing is running — Athena is idle.');
    }
    _chatBusy = false;
    setSendButton(false);      // THE 08-16 SWAP: Stop → Send
    return;
  }
  if (lower === 'restart' || lower === '/restart' || lower === '!restart') {
    addMsg('user', text);
    addMsg('assistant', 'Restarting the runtime — this refreshes Athena\u2019s world. Moment.');
    try {
      await fetch('/system/restart', {method: 'POST'});
    } catch (e) { /* best-effort */ }
    addMsg('assistant', 'Runtime restarted — everything is fresh.');
    return;
  }
  if (lower === 'refresh' || lower === '/refresh' || lower === '!refresh') {
    addMsg('user', text);
    try {
      const r = await fetch('/system/refresh', {method: 'POST'});
      const d = await r.json();
      addMsg('assistant', d.ok ? ('Refreshed: ' + (d.detail || 'ok')) : ('refresh failed: ' + (d.detail || '')));
    } catch (e) {
      addMsg('assistant', 'refresh failed: ' + e.message);
    }
    return;
  }
  addMsg('user', text);
  // THE INTERRUPT (the Operator's 08-12 spec): sending while Athena is
  // thinking cuts the running turn FIRST — the new message then queues
  // and processes. The flag is checked by the running MessageLoop.
  if (_chatBusy) {
    _streamCancel = true;   // stop the old stream's painting NOW
    try { await fetch('/chat/interrupt', {method: 'POST'}); } catch (e) { /* best-effort */ }
  }
  _chatBusy = true;
  setSendButton(true);       // THE 08-16 SWAP: Send → Stop
  _streamCancel = false;   // the NEW stream paints normally
  _tickMs = 1000;          // THE 08-15 AUDIT: activity bumps the tick to
                           // the fast cadence (it eases back to 3s idle)
  const mySend = ++_sendSeq;  // this stream's identity
  // THE LAZY BLOCK (the 08-15 fix): NO thinking block at send — the
  // first state(1) event (the loop's working signal) creates it, so an
  // empty block never appears before the work starts. A plain reply
  // with no tools gets NO block at all.
  let thinking = null;
  // THE MERGED BLOCK (the Operator's 08-12 spec): the Thinking block IS
  // the flow block — one element, dots header + flow rows. flowBlock
  // aliases it so call/reason events append into the same block.
  let flowBlock = thinking;
  // THE STREAMING THINKING FLOW (the Operator's 08-12 spec,
  // adapted): POST /chat/stream and read the SSE body as it arrives —
  // every tool/skill call appears LIVE, then the reply. No giant block.
  try {
    const res = await fetch('/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text}),
    });
    if (!res.ok || !res.body) throw new Error('stream unavailable');
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    const streamed = new Set();   // calls already shown live (dedup)
    let _liveReply = null;        // the typing assistant bubble
    let _liveReplyBody = null;
    let _liveReason = null;       // the live reasoning chain line
    // THE RESPONSE LIFECYCLE (the Operator's 08-15 spec): the MIRROR of
    // startThinking/stopThinking — startResponse creates a FRESH reply
    // bubble (clearing the refs IN SCOPE, unlike the dead module-scope
    // _resetLiveRefs which never touched these locals — the "same
    // response block reused" bug), stopResponse finalizes it.
    function startResponse() {
      // Clear the OLD refs first — the scope fix: these are the same
      // locals the delta handler reads, so a new response NEVER appends
      // into the previous turn's bubble.
      _liveReply = null;
      _liveReplyBody = null;
      // THE LAZY CREATE (the 08-15 fix): if no block exists yet (no
      // state/call arrived first), create one so the response still
      // lands in a turn wrapper.
      if (!flowBlock) {
        flowBlock = startThinking();
        thinking = flowBlock;
      }
      stopThinking(flowBlock);
      _liveReply = document.createElement('div');
      _liveReply.className = 'msg assistant';
      const who = document.createElement('span');
      who.className = 'who';
      who.textContent = 'Athena';
      _liveReply.appendChild(who);
      const body = document.createElement('span');
      body.className = 'msg-body';
      _liveReply.appendChild(body);
      // THE TURN WRAPPER (the 08-15 spec): the response lands INSIDE
      // the CURRENT turn's column wrapper — BELOW its thinking block —
      // so the order is always Thinking → Response. Uses flowBlock (the
      // CURRENT block, reassigned on continuation) so every response
      // lands under ITS OWN block.
      const wrap = flowBlock._turnWrap || $('chat-history');
      wrap.appendChild(_liveReply);
      _liveReplyBody = body;
    }
    function stopResponse() {
      // THE 08-15 TURN END: finalize the live reply (no more deltas
      // append) + drop the refs so the next turn starts fresh.
      _liveReply = null;
      _liveReplyBody = null;
      _liveReason = null;
    }
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      // SSE events are \n\n-separated
      let idx;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = raw.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        let d;
        try { d = JSON.parse(line.slice(6)); } catch (e) { continue; }
        if (d.type === 'state') {
          // THE CONTINUATION SIGNAL (the 08-15 fix): the loop emits
          // "working, iteration N" at EVERY iteration start.
          //   • iteration 1 (no block yet) → create the block lazily —
          //     the working spinner appears exactly when work starts.
          //   • a reply is already live (a previous response landed) →
          //     the loop has CONTINUED: FINISH the previous block first
          //     (collapse/remove it properly), then open a new turn
          //     wrapper so the next response lands in its own block.
          if (mySend !== _sendSeq) break;
          if (!flowBlock) {
            flowBlock = startThinking();
            thinking = flowBlock;
          } else if ((_liveReply || _liveReplyBody) && flowBlock._closed) {
            finishTurn(flowBlock);      // close the previous turn's block
            flowBlock = startThinking();
            thinking = flowBlock;
            // THE 08-15 FIX (the scope bug): inline reset (in scope) —
            // the module-scope _resetLiveRefs never touched these locals.
            _liveReply = null;
            _liveReplyBody = null;
            _liveReason = null;
          }
        } else if (d.type === 'reason') {
          // THE 08-15 INTERRUPT FIX: an older stream (interrupted by a
          // new send) stops painting immediately.
          if (mySend !== _sendSeq) break;
          // THE RE-OPEN (the 08-15 spec): reasoning after a reply opens
          // a new turn wrapper (the loop continuing its work).
          if (!flowBlock || flowBlock._closed) {
            flowBlock = startThinking();
            thinking = flowBlock;
            _liveReason = null;   // a fresh block gets a fresh reason line
            _liveReply = null;    // and a fresh reply bubble
            _liveReplyBody = null;
          }
          // THE LIVE REASONING CHAIN (DeepSeek R1-style models): the
          // model's thinking streams FIRST as reason deltas — shown in
          // the Thinking block as a dimmed live line before the reply.
          const rows = (flowBlock && flowBlock._flowRows) ? flowBlock._flowRows : flowBlock;
          if (!_liveReason) {
            _liveReason = document.createElement('div');
            _liveReason.className = 'flow-row reason';
            _liveReason.textContent = '🧠 ' + (d.text || '');
            rows.appendChild(_liveReason);
          } else {
            _liveReason.textContent += d.text || '';
          }
          scrollChatToBottom();
        } else if (d.type === 'delta') {
          // THE 08-15 INTERRUPT FIX: stop painting if a new send cut us.
          if (mySend !== _sendSeq) break;
          // THE LIVE TYPEWRITER (the Operator's 08-12 spec,
          // adapted): each token delta appends to the assistant bubble
          // as it arrives — the reply is TYPED live, not shown at once.
          if (!_liveReply) {
            // THE RESPONSE LIFECYCLE (the 08-15 spec): a FRESH bubble per
            // response — startResponse clears the old refs IN SCOPE and
            // creates a new one (never the previous turn's bubble).
            startResponse();
          }
          _liveReplyBody.textContent += d.text || '';
          scrollChatToBottom();
        } else if (d.type === 'call') {
          // THE 08-15 INTERRUPT FIX: an older stream stops painting.
          if (mySend !== _sendSeq) break;
          // LIVE call row (the tool.started/completed stream): each
          // call appears ONE LINE AT A TIME as the server streams it —
          // tools as they fire, skills as they're invoked (skill_load),
          // results appended when they complete. No artificial pacing:
          // the stream IS the timing (interruptibility needs the turn
          // stoppable at once).
          // THE RE-OPEN (the Operator's 08-15 spec): when the loop
          // CONTINUES after a response (a new iteration — more tool
          // calls after the reply), a NEW thinking block opens so the
          // operator sees her keep working. The prior block stays
          // collapsed in the history.
          if (!flowBlock || flowBlock._closed) {
            flowBlock = startThinking();
            thinking = flowBlock;
            // THE 08-15 FIX (the scope bug): the module-scope
            // _resetLiveRefs could NOT see the local _liveReply refs —
            // inline the reset here (in scope) so the next response
            // truly starts fresh.
            _liveReply = null;
            _liveReplyBody = null;
            _liveReason = null;
          }
          let kind = d.kind || 'tool';
          let name = (d.detail || '').split(' ')[0];
          let argsPart = (d.detail || '').slice(name.length).trim();
          // A skill_load call IS a skill call (the mirror rule): it
          // displays as 🖊️ skill:<name> with the loaded skill's
          // instructions as its result.
          if (name === 'skill_load') {
            kind = 'skill';
            try {
              const parsed = JSON.parse(argsPart || '{}');
              name = parsed.name || 'skill';
              argsPart = '';
            } catch (e) { name = 'skill'; argsPart = ''; }
          }
          appendFlowRow(flowBlock, {kind, name, args: argsPart,
                                    result: d.extra || ''});
          streamed.add(name);
        } else if (d.type === 'flow') {
          // THE FINAL FLOW SUMMARY (the 08-15 FIX): the server sends the
          // FULL call list at the END of the turn — every call was
          // ALREADY streamed live during the run. A re-open here would
          // spawn an EMPTY block after the response (all calls deduped
          // by the streamed set). Only re-open when the flow carries
          // calls NOT yet shown (a genuine continuation with new work).
          const fresh = (d.flow || []).filter(c => !streamed.has(c.name));
          if (!flowBlock || flowBlock._closed) {
            if (fresh.length) {
              flowBlock = startThinking();
              thinking = flowBlock;
              // THE 08-15 FIX (the scope bug): inline reset (in scope).
              _liveReply = null;
              _liveReplyBody = null;
              _liveReason = null;
            } else {
              // Nothing new — append to the current block if it exists;
              // never spawn an empty one.
              flowBlock = flowBlock || null;
            }
          }
          for (const call of fresh) {
            appendFlowRow(flowBlock, call);
          }
        } else if (d.type === 'reply') {
          // THE 08-15 FIX: use flowBlock (the CURRENT turn's block — a
          // continuation may have spawned a new one) instead of thinking
          // (the FIRST block) so the END collapses the RIGHT block.
          stopThinking(flowBlock || thinking);
          _chatBusy = false;
          setSendButton(false);   // THE 08-16 SWAP: Stop → Send
          // If deltas already typed the reply live, the final event only
          // closes the turn (no duplicate bubble).
          if (!_liveReply) {
            // THE RESPONSE LIFECYCLE (the 08-15 spec): the fallback reply
            // (no deltas arrived — non-streaming, or the reply came at
            // once) uses startResponse — a fresh bubble in the CURRENT
            // turn's wrapper — so it lands AFTER the thinking block and
            // is never detached by finishTurn.
            startResponse();
            _liveReplyBody.textContent = d.reply || '';
          } else if (d.reply && !_liveReplyBody.textContent) {
            _liveReplyBody.textContent = d.reply;
          }
          // THE 08-15 TURN END: the final response landed — collapse the
          // thinking block (the operator can expand it; the turn wrapper
          // keeps the stack Thinking → Response). Runs AFTER the fallback
          // reply is in the wrapper so finishTurn sees the reply.
          finishTurn(flowBlock || thinking);
          // THE RESPONSE LIFECYCLE (the 08-15 spec): finalize the reply.
          stopResponse();
          // SYNC THE COUNTERS (the 08-12 sectional-append fix): the user
          // msg + this reply are already on screen (addMsg/typewriter).
          // Set CHAT_TOTAL AND CHAT_OFFSET to the server's count so the
          // 1-second tick sees delta=0 (no re-append duplicates) and
          // the Load-Older pill never appears on a fresh session
          // (OFFSET < TOTAL would be a false "there are older messages").
          try {
            const sres = await fetch('/chat/history?offset=0&limit=1&session_id=' +
                                     encodeURIComponent(d.session_id || CHAT_SESSION || ''));
            const sd = await sres.json();
            CHAT_TOTAL = sd.total || CHAT_TOTAL;
            CHAT_OFFSET = sd.total || CHAT_OFFSET;
            if (d.session_id) CHAT_SESSION = d.session_id;
          } catch (e) { /* count stays stale — the next tick recovers */ }
          updateLoadMore();
          return;
        }
      }
    }
    stopThinking(thinking);
    _chatBusy = false;
    setSendButton(false);   // THE 08-16 SWAP: Stop → Send
    _liveReply = null;
    _liveReplyBody = null;
    // Stream ended without a reply event (interrupt) — sync the counters
    // so the tick doesn't re-append what's already shown and the
    // Load-Older pill doesn't appear spuriously.
    try {
      const sres = await fetch('/chat/history?offset=0&limit=1&session_id=' +
                               encodeURIComponent(CHAT_SESSION || ''));
      const sd = await sres.json();
      CHAT_TOTAL = sd.total || CHAT_TOTAL;
      CHAT_OFFSET = sd.total || CHAT_OFFSET;
    } catch (e) { /* next tick recovers */ }
  } catch (e) {
    stopThinking(thinking);
    _chatBusy = false;
    setSendButton(false);   // THE 08-16 SWAP: Stop → Send
    _liveReply = null;
    _liveReplyBody = null;
    addMsg('error', 'network error: ' + e);
  }
}
// THE STREAMING FLOW BLOCK: created once per turn, rows appended live.
function startFlowBlock() {
  // The flow rows live INSIDE the merged Thinking block (startThinking
  // created it with the dots header + a .flow-rows container).
  return startThinking();
}
function appendFlowRow(block, call) {
  const container = (block && block._flowRows) ? block._flowRows : block;
  const row = document.createElement('div');
  row.className = 'flow-row ' + (call.kind || 'tool');
  const EM = {system: '⚙️', tool: '🛠️', skill: '🖊️'};
  const em = EM[call.kind] || '⚙️';
  let txt = em + ' ' + (call.name || 'call');
  if (call.args) {
    const a = String(call.args);
    txt += ' — ' + (a.length > 70 ? a.slice(0, 70) + '…' : a);
  }
  if (call.result) {
    const r = String(call.result);
    txt += ' → ' + (r.length > 90 ? r.slice(0, 90) + '…' : r);
  }
  row.textContent = txt;
  container.appendChild(row);
  scrollChatToBottom();
}
// THE 08-16 SEND/STOP SWAP: the send button BECOMES Stop while Athena
// is thinking/working — click = stop her (same as typing /stop).
function stopAthena() {
  if (!_chatBusy) return;
  _streamCancel = true;   // stop the old stream's painting NOW
  try { fetch('/chat/interrupt', {method: 'POST'}); } catch (e) { /* best-effort */ }
  addMsg('assistant', '⏹ Stopped — Athena is no longer working.');
  setSendButton(false);
}
function setSendButton(busy) {
  const btn = $('chat-send');
  if (!btn) return;
  if (busy) {
    btn.textContent = 'Stop';
    btn.classList.add('btn-stop');
  } else {
    btn.textContent = 'Send';
    btn.classList.remove('btn-stop');
  }
}
$('chat-send').addEventListener('click', () => {
  // THE 08-16 SEND/STOP SWAP: while Athena is thinking/working the
  // button is STOP — clicking cancels the current turn (the same as
  // typing /stop). Idle → it is SEND (the normal path).
  if (_chatBusy) {
    stopAthena();
    return;
  }
  sendChat();
});
// THE QUICK ACCESS MENU (the Operator's 08-12 placeholder): toggles the
// dropdown. Options are placeholders today; they'll populate later
// (snippets, commands, files). Clicking an option closes the menu.
$('chat-qa').addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = $('chat-qa-menu');
  menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
});
document.addEventListener('click', (e) => {
  const wrap = $('chat-qa-wrap');
  const menu = $('chat-qa-menu');
  if (menu && !wrap.contains(e.target)) menu.style.display = 'none';
});
document.querySelectorAll('.chat-qa-opt').forEach((opt) => {
  opt.addEventListener('click', () => {
    $('chat-qa-menu').style.display = 'none';
    const action = opt.dataset.qa;
    // THE QUICK MENU ACTIONS (the Operator's 08-15 spec): the Emoji
    // menu, the md preview toggle, and the system commands.
    if (action === 'emoji') {
      // Open the emoji picker unfiltered (insert at the caret).
      renderEmojiPicker('');
      const picker = $('chat-emoji-picker');
      if (picker && picker.style.display !== 'none') $('chat-input').focus();
    } else if (action === 'md-preview') {
      toggleMdPreview();
    } else if (action === 'interrupt') {
      $('chat-input').value = 'interrupt';
      sendChat();
    } else if (action === 'restart') {
      $('chat-input').value = 'restart';
      sendChat();
    } else if (action === 'refresh') {
      $('chat-input').value = 'refresh';
      sendChat();
    }
  });
});
// THE ATTACHMENTS BUTTON (the Operator's 08-12 chat spec): opens the
// file picker; each chosen file posts to /chat/attach (multipart), gets
// copied into the agent's documents/<type>/ store, and renders in the
// chat history as a 📎 attachment line.
$('chat-attach').addEventListener('click', () => { $('chat-file-input').click(); });
$('chat-file-input').addEventListener('change', async (e) => {
  const files = [...(e.target.files || [])];
  e.target.value = '';  // allow re-selecting the same file
  if (!files.length) return;
  for (const file of files) {
    addMsg('user', '[📎 attaching: ' + file.name + '…]');
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('/chat/attach', {method: 'POST', body: fd});
      const d = await res.json();
      if (d.ok) {
        addMsg('user', d.line || ('[📎 attachment: ' + file.name + ']'));
      } else {
        addMsg('error', 'attach failed: ' + (d.error || 'unknown'));
      }
    } catch (err) {
      addMsg('error', 'attach network error: ' + err);
    }
  }
});
// THE MIC PLACEHOLDER (the Operator's spec): push-to-talk comes later.
// The button is inert today — it marks where voice input will live.
$('chat-mic').addEventListener('click', () => {
  addMsg('error', 'Push to talk is coming soon.');
});
$('chat-input').addEventListener('keydown', e => {
  // Enter SENDS only when the command palette is closed — with the
  // palette open, Enter selects the highlighted command instead.
  const pal = $('cmd-palette');
  if (pal && pal.style.display !== 'none') return;
  // THE COMPOSER (the Operator's 08-15 spec): Shift+Enter inserts a
  // NEWLINE (multi-line messages); plain Enter sends. The input is a
  // textarea so newlines persist in the value.
  if (e.key === 'Enter' && e.shiftKey) {
    e.preventDefault();
    const input = $('chat-input');
    const start = input.selectionStart || 0;
    const end = input.selectionEnd || 0;
    input.value = input.value.slice(0, start) + '\n' + input.value.slice(end);
    input.selectionStart = input.selectionEnd = start + 1;
    return;
  }
  // THE 08-15 FIX: preventDefault BEFORE sendChat — otherwise the
  // textarea's default Enter action (insert a newline) runs AFTER the
  // field is cleared, leaving blank lines in the emptied box.
  if (e.key === 'Enter') {
    e.preventDefault();
    sendChat();
  }
});

// ── THE COMMAND PALETTE (the Operator's spec) ─────────────────────────────
// Typing / or \ pops a FILE-SYSTEM-TREE panel: the root shows CATEGORIES
// (folders); selecting one shows its COMMANDS; selecting a command shows
// its SUBCOMMANDS + usage syntax. A breadcrumb shows the path and each
// level can be clicked to go back up. Arrow keys move, Enter/Tab drills
// down (or selects at the leaf), Backspace goes up, Escape closes.
let CMD_TREE = [];         // [{name: category, commands: [{name, subcommands, help}]}]
let CMD_PATH = [];         // the navigation stack: [] = root (categories)
let CMD_SELECTED = -1;     // the highlighted row at the current level

async function loadCommands() {
  try {
    const res = await fetch('/commands');
    const d = await res.json();
    CMD_TREE = d.tree || [];
  } catch (e) { /* server mid-boot */ }
}
loadCommands();

// The items at the CURRENT level (what the left pane shows) — walked
// GENERICALLY at any depth (the Operator's spec: infinitely deep chains).
//   CMD_PATH = []               → the categories (folders)
//   CMD_PATH = [cat]            → the commands in that folder
//   CMD_PATH = [cat, cmd, ...]  → the next {Argument/Action} layer
function currentLevelItems() {
  if (CMD_PATH.length === 0) {
    return CMD_TREE.map(c => ({
      kind: 'category', name: c.name, children: [],
      help: 'folder: ' + c.commands.length + ' commands',
      category: c,
    }));
  }
  // Descend: [cat] → commands; deeper → children of the previous node.
  const cat = CMD_TREE.find(c => c.name === CMD_PATH[0]);
  if (!cat) return [];
  if (CMD_PATH.length === 1) {
    // The commands inside this folder.
    return cat.commands.map(cmd => ({
      kind: 'command', name: cmd.name, children: cmd.children || [],
      help: cmd.help || '',
      command: cmd,
    }));
  }
  let node = cat.commands.find(c => c.name === CMD_PATH[1]);
  if (!node) return [];
  if (CMD_PATH.length === 2) {
    // The command's first argument layer.
    return (node.children || []).map(ch => ({
      kind: 'arg', name: ch.name, children: ch.children || [],
      help: '/ ' + CMD_PATH[1] + ' ' + ch.name,
    }));
  }
  // Deeper levels: walk down the children chain.
  let kids = node.children || [];
  for (let i = 2; i < CMD_PATH.length; i++) {
    const k = (kids || []).find(x => x.name === CMD_PATH[i]);
    if (!k) return [];
    kids = k.children || [];
  }
  return kids.map(ch => ({
    kind: 'arg', name: ch.name, children: ch.children || [],
    help: '/ ' + CMD_PATH[1] + ' ' + ch.name,
  }));
}

function cmdUsage(item) {
  // The usage syntax for the CURRENT item (spaced segments: / module arg).
  if (item.kind === 'category') return item.help;
  if (item.kind === 'command') {
    if (item.help && item.help.trim()) return item.help;
    if (item.children && item.children.length) {
      return '/ ' + item.name + ' [' + item.children.map(c => c.name).join(' | ') + ']';
    }
    return '/ ' + item.name;
  }
  return item.help || item.name;
}

function renderCmdPalette() {
  const pal = $('cmd-palette');
  const list = $('cmd-list');
  const usage = $('cmd-usage');
  const crumbs = $('cmd-breadcrumb');
  if (!pal || !list || !usage) return;
  // The breadcrumb: the path so far, each level clickable to go back.
  crumbs.innerHTML = '';
  const rootCrumb = document.createElement('span');
  rootCrumb.className = 'cmd-crumb' + (CMD_PATH.length === 0 ? ' current' : '');
  rootCrumb.textContent = '/';
  rootCrumb.addEventListener('click', () => { CMD_PATH = []; CMD_SELECTED = 0; renderCmdPalette(); });
  crumbs.appendChild(rootCrumb);
  CMD_PATH.forEach((seg, i) => {
    const sep = document.createElement('span');
    sep.className = 'cmd-crumb-sep';
    sep.textContent = ' › ';
    crumbs.appendChild(sep);
    const cr = document.createElement('span');
    cr.className = 'cmd-crumb' + (i === CMD_PATH.length - 1 ? ' current' : '');
    cr.textContent = seg;
    cr.addEventListener('click', () => {
      CMD_PATH = CMD_PATH.slice(0, i + 1);
      CMD_SELECTED = 0;
      renderCmdPalette();
    });
    crumbs.appendChild(cr);
  });
  // The LEFT pane: the items at this level (the FILTERED set when the
  // user has typed a prefix — arrows keep the filter).
  list.innerHTML = '';
  const items = pal._filtered || currentLevelItems();
  items.forEach((item, i) => {
    const row = document.createElement('div');
    row.className = 'cmd-item' + (i === CMD_SELECTED ? ' selected' : '');
    row.textContent = (item.kind === 'category' ? '▸ ' : '') + item.name;
    row.dataset.idx = i;
    row.addEventListener('click', () => {
      CMD_SELECTED = i;
      enterItem(item);
    });
    list.appendChild(row);
  });
  // The RIGHT pane: the usage of the SELECTED item.
  const sel = items[CMD_SELECTED];
  usage.innerHTML = '';
  if (sel) {
    const h = document.createElement('div');
    h.className = 'cmd-usage-title';
    h.textContent = (sel.kind === 'category' ? '▸ ' : '/ ') + sel.name;
    usage.appendChild(h);
    const syn = document.createElement('div');
    syn.className = 'cmd-usage-syntax';
    syn.textContent = cmdUsage(sel);
    usage.appendChild(syn);
    if (sel.children && sel.children.length) {
      const subs = document.createElement('div');
      subs.className = 'cmd-usage-subs';
      subs.textContent = 'parts: ' + sel.children.map(c => c.name).join(' · ');
      usage.appendChild(subs);
    }
  }
  pal.style.display = items.length > 0 ? 'flex' : 'none';
}

// ENTER the selected item (the Operator's command structure):
//   {/ or \} {Module/Function} {Argument/Action} {status/flag}
// CATEGORIES are folders ONLY — navigate, never populate. A COMMAND
// populates the module segment + reveals its argument layer; an ARG
// appends its segment. EVERY segment is space-separated. When a node
// has NO further children (a LEAF), the chain is COMPLETE — the builder
// HIDES so the user types the rest themselves.
function enterItem(item) {
  const pal = $('cmd-palette');
  const input = $('chat-input');
  if (pal) pal._filtered = null;
  if (item.kind === 'category') {
    CMD_PATH = [item.name];
    CMD_SELECTED = 0;
    renderCmdPalette();
    return;
  }
  if (item.kind === 'command') {
    CMD_PATH = [item.categoryName || (CMD_PATH[0] || ''), item.name];
    input.value = '/ ' + item.name + ' ';
    const kids = item.children || [];
    if (!kids.length) {
      // Leaf command: no arguments — the chain is complete. Hide the
      // builder and let the user type the rest.
      input.focus();
      closeCmdPalette();
      return;
    }
    CMD_SELECTED = 0;
    renderCmdPalette();
    return;
  }
  // An {Argument/Action} (possibly deep): append its segment. If it has
  // children, reveal the next layer; if it's a leaf, the chain is done.
  const pathSoFar = CMD_PATH.slice(1);  // skip the category segment
  const newPath = pathSoFar.concat(item.name);
  CMD_PATH = [CMD_PATH[0], ...newPath];
  const value = '/ ' + newPath.join(' ');
  input.value = value + ' ';
  const kids = item.children || [];
  if (!kids.length) {
    // LEAF: the full chain is built — hide the builder, user types on.
    input.focus();
    closeCmdPalette();
    return;
  }
  CMD_SELECTED = 0;
  renderCmdPalette();
}

// SYNC THE PALETTE FROM THE INPUT (the Operator's spec): what the user TYPES
// drives the menu exactly like a selection does. "/Category " navigates
// into the folder; "/Command " navigates into its Argument layer; a
// partial fragment filters the current level; anything else closes it.
function syncPaletteFromInput() {
  const raw = $('chat-input').value;
  if (!raw.startsWith('/') && !raw.startsWith('\\')) {
    closeCmdPalette();
    return;
  }
  const body = raw.slice(1);
  // The input may read "/ skills doctor " — trim the leading space so
  // the first segment is "skills" (not " skills").
  const trimmed = body.replace(/^\s+/, '');
  const parts = trimmed.split(' ');
  const hasTrailing = trimmed.endsWith(' ');
  // Complete segments (each followed by a space) navigate INTO a level.
  const complete = hasTrailing ? parts.filter(Boolean) : parts.slice(0, -1).filter(Boolean);
  // The fragment the user is still typing (the last, space-less segment).
  const typing = hasTrailing ? '' : (parts[parts.length - 1] || '');
  // Navigate by the complete segments (the Operator's structure):
  //   {/} {Module/Function} {Argument/Action} {status/flag} ...
  // ANY number of segments — seg[0] may be a CATEGORY or a COMMAND.
  CMD_PATH = [];
  if (complete.length >= 1 && complete[0]) {
    const seg0 = complete[0].toLowerCase();
    // Try the segment as a CATEGORY first.
    let cat = CMD_TREE.find(c => c.name.toLowerCase() === seg0);
    if (cat) {
      CMD_PATH = [cat.name];
      if (complete.length >= 2 && complete[1]) {
        const cmd = cat.commands.find(c => c.name.toLowerCase() === complete[1].toLowerCase());
        if (cmd && (cmd.children || []).length > 0) {
          CMD_PATH = [cat.name, cmd.name];
          // Walk the remaining segments down the children chain.
          let kids = cmd.children || [];
          for (let i = 2; i < complete.length; i++) {
            const k = kids.find(x => x.name.toLowerCase() === complete[i].toLowerCase());
            if (!k || !(k.children || []).length) break;
            CMD_PATH.push(k.name);
            kids = k.children || [];
          }
        }
      }
    } else {
      // The segment is a COMMAND (module) — find which category holds it
      // and navigate into its argument layer directly.
      for (const c of CMD_TREE) {
        const cmd = (c.commands || []).find(x => x.name.toLowerCase() === seg0);
        if (cmd && (cmd.children || []).length > 0) {
          CMD_PATH = [c.name, cmd.name];
          let kids = cmd.children || [];
          for (let i = 1; i < complete.length; i++) {
            const k = kids.find(x => x.name.toLowerCase() === complete[i].toLowerCase());
            if (!k || !(k.children || []).length) break;
            CMD_PATH.push(k.name);
            kids = k.children || [];
          }
          break;
        }
      }
    }
  }
  // Filter the current level by the typing fragment.
  let items = currentLevelItems();
  if (typing) {
    const t = typing.toLowerCase();
    items = items.filter(it => it.name.toLowerCase().startsWith(t));
    // At the ROOT, also surface matching COMMANDS across categories so
    // typing "/cron" finds cron even though it lives under System.
    if (CMD_PATH.length === 0) {
      CMD_TREE.forEach(c => {
        (c.commands || []).forEach(cmd => {
          if (cmd.name.toLowerCase().startsWith(t)) {
            items.push({
              kind: 'command', name: cmd.name,
              children: cmd.children || [], help: cmd.help || '',
              command: cmd, categoryName: c.name,
            });
          }
        });
      });
    }
  }
  const pal = $('cmd-palette');
  if (pal) pal._filtered = items;
  CMD_SELECTED = 0;
  renderCmdPalette();
}

// The input listener: / or \ opens + syncs the palette; typing filters
// it; anything else closes it.
$('chat-input').addEventListener('input', syncPaletteFromInput);

function closeCmdPalette() {
  const pal = $('cmd-palette');
  if (pal) { pal.style.display = 'none'; pal._filtered = null; }
  CMD_PATH = [];
  CMD_SELECTED = -1;
}

// Keyboard: arrows move, Enter/Tab drills down / selects, Backspace goes
// up, Escape closes.
$('chat-input').addEventListener('keydown', e => {
  const pal = $('cmd-palette');
  if (!pal || pal.style.display === 'none') return;
  const items = pal._filtered || currentLevelItems();
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (items.length) {
      CMD_SELECTED = (CMD_SELECTED + 1) % items.length;
      renderCmdPalette();
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (items.length) {
      CMD_SELECTED = (CMD_SELECTED - 1 + items.length) % items.length;
      renderCmdPalette();
    }
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    const it = (pal._filtered || currentLevelItems())[CMD_SELECTED];
    if (it) enterItem(it);
  } else if (e.key === 'Backspace' && CMD_PATH.length > 0) {
    // Backspace at a non-root level goes UP one folder.
    const raw = $('chat-input').value;
    if (!raw.endsWith(' ')) {
      e.preventDefault();
      CMD_PATH = CMD_PATH.slice(0, -1);
      CMD_SELECTED = 0;
      renderCmdPalette();
    }
  } else if (e.key === 'Escape') {
    closeCmdPalette();
  }
});

// ── THE EMOJI PICKER (the Operator's 08-15 spec): typing ":" shows a
// picker filtered by the text after the colon (:hear → ❤️); picking
// (or typing a full shorthand + space/enter) replaces it with the emoji.
const EMOJI_MAP = {
  'heart': '❤️', 'love': '💖', 'smile': '😊', 'joy': '😂', 'wink': '😉',
  'kiss': '😘', 'cry': '😢', 'angry': '😡', 'sad': '😞', 'cool': '😎',
  'ok': '👌', 'thumbs': '👍', 'wave': '👋', 'fire': '🔥', 'star': '⭐',
  'check': '✅', 'x': '❌', 'think': '🤔', 'sleep': '😴', 'wolf': '🐺',
  'moon': '🌙', 'snow': '❄️', 'firefly': '✨', 'cat': '🐱', 'dog': '🐶',
};

function renderEmojiPicker(filter) {
  const picker = $('chat-emoji-picker');
  if (!picker) return;
  const keys = Object.keys(EMOJI_MAP).filter(k => !filter || k.startsWith(filter.toLowerCase()));
  if (!keys.length) { picker.style.display = 'none'; return; }
  picker.innerHTML = '';
  for (const k of keys.slice(0, 20)) {
    const it = document.createElement('button');
    it.type = 'button';
    it.className = 'chat-emoji-item';
    it.textContent = EMOJI_MAP[k] + ' ' + k;
    it.addEventListener('click', () => {
      replaceEmojiShorthand(k);
      picker.style.display = 'none';
      $('chat-input').focus();
    });
    picker.appendChild(it);
  }
  picker.style.display = 'flex';
}

function replaceEmojiShorthand(name) {
  const input = $('chat-input');
  const val = input.value;
  // Replace the LAST ":<name>" (or ":<partial>") with the emoji.
  const idx = val.lastIndexOf(':');
  if (idx !== -1) {
    input.value = val.slice(0, idx) + EMOJI_MAP[name] + val.slice(val.indexOf(' ', idx) === -1 ? val.length : val.indexOf(' ', idx));
    input.selectionStart = input.selectionEnd = input.value.length;
  }
}

$('chat-input').addEventListener('input', e => {
  // THE AUTO-GROW (the 08-15 composer): the textarea grows with its
  // content up to the max-height (Shift+Enter newlines expand it).
  const ta = e.target;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  // THE MD LIVE PREVIEW (the 08-15 spec): re-render the draft preview.
  renderMdPreview();
  const val = ta.value;
  const idx = val.lastIndexOf(':');
  if (idx !== -1 && val.indexOf(' ', idx) === -1 && idx < val.length - 1) {
    renderEmojiPicker(val.slice(idx + 1).trim());
  } else {
    const picker = $('chat-emoji-picker');
    if (picker) picker.style.display = 'none';
  }
});
$('chat-input').addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    const picker = $('chat-emoji-picker');
    if (picker && picker.style.display !== 'none') { picker.style.display = 'none'; e.preventDefault(); }
  }
});

// ── THE MARKDOWN LIVE PREVIEW (the Operator's 08-15 spec): the draft
//    renders as the operator types — a live preview strip above the
//    input row. Auto-shows when the draft contains markdown (headers,
//    code, bold, lists, links); the Quick Menu's "Toggle Markdown
//    Preview" forces it on/off.
let _mdPreviewForced = false;   // null/auto → false = auto only

function _draftHasMarkdown(text) {
  return /(^|\n)\s*(#{1,6}\s|[-*]\s|\d+\.\s|```|> )/.test(text)
      || /\*\*[^*]+\*\*/.test(text)
      || /__[^_]+__/.test(text)
      || /\[[^\]]+\]\([^)]+\)/.test(text)
      || /`[^`\n]+`/.test(text);
}

function renderMdPreview() {
  const pre = $('chat-md-preview');
  if (!pre) return;
  const text = $('chat-input').value;
  const show = _mdPreviewForced || _draftHasMarkdown(text);
  if (!show || !text.trim()) {
    pre.style.display = 'none';
    pre.innerHTML = '';
    return;
  }
  pre.style.display = 'block';
  pre.innerHTML = mdToHtml(text);
  pre.scrollTop = pre.scrollHeight;
}

function toggleMdPreview() {
  _mdPreviewForced = !_mdPreviewForced;
  renderMdPreview();
  const pre = $('chat-md-preview');
  if (pre) pre.style.display = (_mdPreviewForced || _draftHasMarkdown($('chat-input').value)) ? 'block' : 'none';
}
