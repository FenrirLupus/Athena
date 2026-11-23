// The Call page: one agent, one call, a state machine inside the lane's `working` state.
//
// Phases: idle → waking → listening ⇄ deciding ⇄ speaking → ended. Each phase maps to `working` on the lane, so the
// rail keeps showing one status; the phase is the finer grain that fires the pipeline. The audio pipeline itself is
// not installed yet (no torch in this venv), so `listening` captures and meters the microphone and says so plainly
// rather than pretending to transcribe.
(function () {
  var rail, list, stage, portrait, nameEl, subEl, sayEl, note, transcript, wave, startBtn, endBtn, muteBtn;
  var phase = "idle", agent = null, stream = null, audio = null, analyser = null, timer = null;
  // the voice gate: a band, not a floor. Below `open` is silence; above `close` is clipping.
  var GATE_KEY = "athena.call.gate";
  // Sliders are 0-100 on a dBFS scale: 0 = -100 dBFS, 100 = 0 dBFS (the whole range mic input can carry).
  // The gate is a hysteretic noise gate, not a band: it opens at `open` and holds until the level falls to `close`.
  var DB_FLOOR = -100;
  var gate = { open: 70, close: 60, isOpen: false, levelDb: DB_FLOOR, denoise: "rnnoise", chunkWords: 0 };  // -30 and -40 dBFS

  function toDb(peak01) {
    if (!peak01 || peak01 <= 0) return DB_FLOOR;
    return Math.max(DB_FLOOR, 20 * Math.log10(peak01));
  }
  function sliderToDb(value) { return DB_FLOOR + (Number(value) / 100) * -DB_FLOOR; }   // 0..100 → -60..0 dBFS
  function dbLabel(value) { return sliderToDb(value).toFixed(0) + " dBFS"; }

  function loadGate() {
    try {
      var saved = JSON.parse(localStorage.getItem(GATE_KEY) || "null");
      if (saved && typeof saved.open === "number" && typeof saved.close === "number") gate = Object.assign(gate, saved);
      if (typeof saved.denoise === "string") gate.denoise = saved.denoise;
      if (saved.chunkWords) gate.chunkWords = Number(saved.chunkWords);
      // only two modes exist now; anything else (an old 4-word grouping) becomes per word
      if ([0, 1, 999].indexOf(gate.chunkWords) < 0) gate.chunkWords = 0;   // phrase mode by default
      else if (typeof saved.denoise === "boolean") gate.denoise = saved.denoise ? "rnnoise" : "off";
      if (gate.close >= gate.open) gate.close = Math.max(0, gate.open - 10);   // close must be the quieter value
    } catch (e) { }
  }
  function saveGate() {
    try {
      localStorage.setItem(GATE_KEY, JSON.stringify({ open: gate.open, close: gate.close, denoise: gate.denoise, chunkWords: gate.chunkWords }));
    } catch (e) { }
  }
  // open when loud enough; close only when it drops under the close threshold. `close` must be quieter than `open`.
  function gateStep(levelDb, wasOpen) {
    var openAt = sliderToDb(gate.open), closeAt = sliderToDb(Math.min(gate.close, gate.open - 1));
    if (wasOpen) return levelDb > closeAt;      // stays open through the hold band
    return levelDb >= openAt;
  }


  function setPhase(next) {
    phase = next;
    stage.dataset.phase = next;
    startBtn.disabled = next !== "idle";
    endBtn.disabled = next === "idle" || next === "ended";
    muteBtn.disabled = next === "idle" || next === "ended";
    if (subEl && agent) {
      var label = { idle: "not in a call", waking: "waking", listening: "listening",
                    deciding: "thinking", speaking: "speaking", ended: "call ended" }[next] || next;
      subEl.textContent = agent + " · " + label;
    }
  }

  function line(kind, text) {
    var row = el("div", "call-line call-" + kind);
    row.appendChild(el("span", "call-line-kind", kind === "you" ? "you" : kind === "agent" ? "agent" : kind));
    row.appendChild(el("span", "call-line-text", text));
    transcript.appendChild(row);
    transcript.scrollTop = transcript.scrollHeight;
  }

  async function pick(channel, card) {
    agent = channel;
    Array.prototype.forEach.call(list.querySelectorAll(".call-card"), function (c) {
      c.classList.toggle("active", c === card);
    });
    nameEl.textContent = channel;
    setPhase(phase === "idle" ? "idle" : phase);
    try {
      var prof = await (await fetch("/agents/" + encodeURIComponent(channel) + "/profile",
                                    { headers: { accept: "application/json" } })).json();
      var persona = prof.persona || {};
      portrait.textContent = (persona.name || channel).slice(0, 2).toUpperCase();
      portrait.style.background = persona.accent || "var(--panel-2)";
      nameEl.textContent = persona.name || channel;
      subEl.textContent = channel + " · not in a call";
    } catch (e) {
      portrait.textContent = channel.slice(0, 2).toUpperCase();
    }
    // the portrait is the agent's avatar when it has one
    var img = new Image();
    img.onload = function () {
      portrait.textContent = "";
      portrait.style.backgroundImage = "url(/agents/" + encodeURIComponent(channel) + "/avatar)";
      portrait.style.backgroundSize = "cover";
      portrait.style.backgroundPosition = "center";
    };
    img.src = "/agents/" + encodeURIComponent(channel) + "/avatar";
    note.textContent = "";
  }

  // ---- capture: PCM in, one wav per utterance out -------------------------------------------------------------
  var capture = { proc: null, buffer: [], since: 0, speaking: false, tail: 0, rate: 48000 };

  function encodeWav(chunks, rate) {
    var length = chunks.reduce(function (n, c) { return n + c.length; }, 0);
    var buffer = new ArrayBuffer(44 + length * 2);
    var view = new DataView(buffer);
    function str(offset, text) {
      for (var i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
    }
    str(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); str(8, "WAVE");
    str(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); str(36, "data"); view.setUint32(40, length * 2, true);
    var offset = 44;
    chunks.forEach(function (chunk) {
      for (var i = 0; i < chunk.length; i++) {
        var sample = Math.max(-1, Math.min(1, chunk[i]));
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
      }
    });
    return new Blob([view], { type: "audio/wav" });
  }

  async function transcribeUtterance(chunks) {
    if (!agent || !chunks.length) return;
    resetMetrics();
    metrics.t0 = performance.now();
    setPhase("deciding");
    sayEl.textContent = "transcribing…";
    try {
      var blob = encodeWav(chunks, capture.rate);
      var res = await fetch("/agents/" + encodeURIComponent(agent) + "/transcribe", {
        method: "POST", headers: { "content-type": "audio/wav" }, body: blob,
      });
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
      metrics.t1 = performance.now();
      var text = (data.text || "").trim();
      if (text) {
        line("you", text);
        sayEl.textContent = text;
        await askAgent(text);
      } else {
        sayEl.textContent = "(nothing recognised)";
      }
    } catch (e) {
      line("system", "transcription failed: " + (e.message || e));
      sayEl.textContent = "transcription failed";
    } finally {
      if (phase === "deciding") setPhase("listening");
    }
  }

  // the agent's half: the committed utterance goes to the lane's turn, and the answer comes back here
  async function askAgent(text) {
    if (!agent || !text) return;
    setPhase("deciding");
    sayEl.textContent = "thinking…";
    var body = JSON.stringify({ text: text, mode: "call" });   /* a spoken turn: budgeted prompt */
    async function send() {
      return fetch("/agents/" + encodeURIComponent(agent) + "/turn", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: body,
      });
    }
    try {
      var res = await send();
      if (res.status === 409) {                       // the lane is asleep: a call wakes it, once
        await fetch("/agents/" + encodeURIComponent(agent) + "/wake", { method: "POST" });
        res = await send();
      }
      var data = await res.json().catch(function () { return {}; });
      if (!res.ok) throw new Error(data.detail || ("HTTP " + res.status));
      metrics.t3 = performance.now();
      var reply = String(data.reply || data.text || data.summary || "").trim();
      if (reply) {
        line("agent", reply);
        sayEl.textContent = reply;
        setPhase("speaking");
        // the voice is wired here when the TTS worker is up; text is what works today
        if (window.AthenaVoice && window.AthenaVoice.speak) {
          try {
            await window.AthenaVoice.speak(agent, reply);
          } catch (e) {
            line("system", "voice failed: " + (e.message || e));
          }
        }
        setTimeout(function () { if (phase === "speaking") setPhase("listening"); }, 600);
      } else {
        line("system", "the agent returned no text (status: " + (data.status || "unknown") + ")");
        setPhase("listening");
      }
    } catch (e) {
      line("system", "the agent could not answer: " + (e.message || e));
      setPhase("listening");
    }
  }

  // ---- the agent's voice: fetch the wav through the server proxy and play it --------------------------------
  var player = { ctx: null, source: null };

  // ---- metrics: the stages of one exchange, shown in the page's own footer -------------------------------
  var metrics = { t0: 0, t1: 0, t3: 0, t4: 0, t6: 0, chunks: 0, synthMs: 0, audioSec: 0, words: 0 };

  function resetMetrics() {
    metrics = { t0: 0, t1: 0, t3: 0, t4: 0, t6: 0, chunks: 0, synthMs: 0, audioSec: 0, words: 0 };
  }

  function paintMetrics() {
    var slot = document.querySelector("#page-footer .footer-text");
    if (!slot) return;
    var stt = metrics.t1 && metrics.t0 ? Math.round(metrics.t1 - metrics.t0) : null;
    var agent = metrics.t3 && metrics.t1 ? Math.round(metrics.t3 - metrics.t1) : null;
    var tts = metrics.t4 && metrics.t3 ? Math.round(metrics.t4 - metrics.t3) : null;
    var first = metrics.t6 && metrics.t0 ? Math.round(metrics.t6 - metrics.t0) : null;
    var rtf = metrics.audioSec > 0 ? (metrics.synthMs / 1000 / metrics.audioSec) : null;
    var bits = [];
    if (stt !== null) bits.push("stt " + stt + " ms");
    if (agent !== null) bits.push("agent " + agent + " ms");
    if (tts !== null) bits.push("tts " + tts + " ms");
    if (first !== null) bits.push("first audio " + (first / 1000).toFixed(2) + " s");
    if (metrics.chunks) bits.push(metrics.chunks + " chunk" + (metrics.chunks === 1 ? "" : "s"));
    if (rtf !== null) bits.push("rtf " + rtf.toFixed(2));
    if (metrics.words) bits.push(metrics.words + " words");
    slot.textContent = bits.length ? bits.join(" · ") : "call metrics appear here after your first exchange";
  }

  function voiceCtx() {
    if (!player.ctx) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      player.ctx = new Ctx();
    }
    return player.ctx;
  }

  function stopVoice() {
    if (player.source) {
      try { player.source.stop(); } catch (e) { }
      player.source = null;
    }
  }

  function sentences(text) {
    var parts = (String(text).match(/[^.!?\n]+[.!?]*/g) || [String(text)])
      .map(function (part) { return part.trim(); })
      .filter(Boolean);
    // a fragment like "Yes —" has too few tokens to align with the voice conditioning, so short pieces are joined
    // onto the next one instead of being synthesised alone
    var merged = [];
    parts.forEach(function (part) {
      if (merged.length && (part.length < 24 || merged[merged.length - 1].length < 24)) {
        merged[merged.length - 1] = (merged[merged.length - 1] + " " + part).trim();
      } else {
        merged.push(part);
      }
    });
    // and no chunk may exceed what XTTS can align in one pass (a long passage mismatches text and audio frames)
    var capped = [];
    merged.forEach(function (part) {
      var rest = part;
      while (rest.length > 140) {
        var cut = rest.lastIndexOf(" ", 140);
        if (cut < 60) cut = 140;
        capped.push(rest.slice(0, cut).trim());
        rest = rest.slice(cut).trim();
      }
      if (rest) capped.push(rest);
    });
    return capped;
  }

  // Phrase-based chunking: 5-10 words preferred, 3 minimum, 20 maximum, cut at sentence then comma boundaries.
  // Per-word requests were rejected: each one pays model invocation overhead and carries no linguistic context,
  // which is what made speech sound blocky and flat.
  // Chunking for speech, with one rule above all: every chunk is a sentence start to the model, and a sentence start
  // carries emphasis ("hello" becomes "H-ello"). So chunks are built from whole sentences and merged until they are
  // substantial; sub-sentence splitting happens only when a single sentence is long enough to need it.
  function speechChunks(text) {
    var clean = String(text || "").replace(/\s+/g, " ").trim();
    if (!clean) return [];

    var MIN_WORDS = 8;      // shorter than this and the emphasis is audible
    var MAX_WORDS = 45;     // long enough that splitting is rare, short enough to stay responsive

    var sentences = (clean.match(/[^.!?]+[.!?]+["')\]]?|[^.!?]+$/g) || [clean])
      .map(function (part) { return part.trim(); })
      .filter(Boolean);

    // merge neighbouring sentences until each chunk carries enough words to sound like speech, not a fragment
    var merged = [];
    sentences.forEach(function (sentence) {
      var last = merged.length - 1;
      var lastWords = last >= 0 ? merged[last].split(" ").length : 0;
      var nextWords = sentence.split(" ").length;
      if (last >= 0 && (lastWords < MIN_WORDS || nextWords < MIN_WORDS) && lastWords + nextWords <= MAX_WORDS) {
        merged[last] = merged[last] + " " + sentence;
      } else {
        merged.push(sentence);
      }
    });

    // only a genuinely long sentence is cut, and then at a comma near the middle so the break sounds like breathing
    var out = [];
    merged.forEach(function (chunk) {
      var words = chunk.split(" ");
      while (words.length > MAX_WORDS) {
        var head = words.slice(0, MAX_WORDS).join(" ");
        var comma = head.lastIndexOf(",");
        var take = comma > Math.floor(MAX_WORDS / 3) ? head.slice(0, comma + 1).split(" ").length : MAX_WORDS;
        out.push(words.slice(0, take).join(" "));
        words = words.slice(take);
      }
      if (words.length) out.push(words.join(" "));
    });

    if (out.length > 1 && out[out.length - 1].split(" ").length < 4) {
      out[out.length - 2] += " " + out.pop();
    }
    return out;
  }

  async function fetchVoice(lane, text) {
    var started = performance.now();
    var res = await fetch("/agents/" + encodeURIComponent(lane) + "/speak", {
      method: "POST",
      headers: { "content-type": "application/json", accept: "audio/wav" },
      // 999 tells the worker "this is already one clip, do not split it again"
      body: JSON.stringify({ text: text, chunk_words: 999 }),
    });
    if (!res.ok) {
      var detail = await res.json().catch(function () { return {}; });
      throw new Error(detail.detail || ("HTTP " + res.status));
    }
    var buf = await res.arrayBuffer();
    metrics.synthMs += performance.now() - started;
    metrics.words += String(text).split(/\s+/).filter(Boolean).length;
    return buf;
  }

  // the pause a phrase should be followed by, in seconds: punctuation is the speaker's own timing cue
  function pauseAfter(phrase) {
    var end = String(phrase).trim().slice(-1);
    if (end === "." ) return 0.26;
    if (end === "?" || end === "!") return 0.22;
    if (end === "," || end === ";" || end === ":") return 0.13;
    return 0.06;
  }

  function trimSilence(audio, ctx) {
    var data = audio.getChannelData(0);
    var peak = 0;
    for (var i = 0; i < data.length; i++) { var v = Math.abs(data[i]); if (v > peak) peak = v; }
    if (peak < 0.01) return audio;
    var threshold = peak * 0.08;
    var start = 0, end = data.length - 1;
    while (start < data.length && Math.abs(data[start]) < threshold) start++;
    while (end > start && Math.abs(data[end]) < threshold) end--;
    var pad = Math.floor(audio.sampleRate * 0.015);
    start = Math.max(0, start - pad);
    end = data.length;                                           // keep the tail: the pause after a phrase is speech
    if (end - start < 32 || (start === 0 && end === data.length)) return audio;
    var out = ctx.createBuffer(audio.numberOfChannels, end - start, audio.sampleRate);
    for (var c = 0; c < audio.numberOfChannels; c++) {
      out.getChannelData(c).set(audio.getChannelData(c).subarray(start, end));
    }
    return out;
  }

  window.AthenaVoice = {
    // called from the Call button, which is the user gesture the autoplay policy needs
    unlock: function () { try { voiceCtx().resume(); } catch (e) { } },

    speak: async function (lane, text) {
      if (!text) return;
      stopVoice();
      var parts = speechChunks(text);
      if (!parts.length) return;
      var ctx = voiceCtx();
      if (ctx.state === "suspended") { try { await ctx.resume(); } catch (e) { } }

      // Every clip is fetched immediately, then placed on the audio clock back to back with a 30 ms overlap.
      // Awaiting each clip's playback instead cost a scheduling delay per word, which is what made speech blocky.
      // Bounded queue: one chunk is being spoken while exactly one more is in flight. Firing every chunk at once
      // queued work faster than the voice could synthesise it, which is how audio drifts behind the conversation.
      var cursor = 0;                       // where the next clip starts, in context time
      var lastEnd = null;
      var ahead = fetchVoice(lane, parts[0]);
      for (var index = 0; index < parts.length; index++) {
        var raw;
        try { raw = await ahead; } catch (e) { line("system", "voice failed: " + (e.message || e)); continue; }
        if (index + 1 < parts.length) ahead = fetchVoice(lane, parts[index + 1]);
        var audio = trimSilence(await ctx.decodeAudioData(raw), ctx);
        if (!metrics.t4) metrics.t4 = performance.now();
        metrics.chunks += 1;
        metrics.audioSec += audio.duration;
        var startAt = Math.max(ctx.currentTime + 0.02, cursor);
        var source = ctx.createBufferSource();
        source.buffer = audio;
        source.connect(ctx.destination);
        source.start(startAt);
        if (!metrics.t6) metrics.t6 = performance.now();
        player.source = source;
        paintMetrics();
        cursor = startAt + audio.duration + pauseAfter(parts[index]);   // the pause belongs to the punctuation
        lastEnd = { at: cursor, source: source };
      }
      if (lastEnd) {
        await new Promise(function (resolve) {
          lastEnd.source.onended = function () { player.source = null; resolve(); };
        });
      }
    },
    stop: stopVoice,
  };

  function draw(level) {
    var ctx = wave.getContext("2d");
    var w = wave.width, h = wave.height;
    ctx.clearRect(0, 0, w, h);
    var bars = 72, mid = h / 2;
    for (var i = 0; i < bars; i++) {
      var v = level * (0.35 + 0.65 * Math.abs(Math.sin(i * 0.7 + Date.now() / 240)));
      var bar = Math.max(2, v * mid * 1.6);
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#7dd3fc";
      ctx.fillRect(i * (w / bars) + 1, mid - bar, (w / bars) - 2, bar * 2);
    }
  }

  async function prepare() {
    // a call opens its own engines: whisper for the user, the agent's cloned voice for the reply
    note.textContent = "loading whisper and the voice…";
    try {
      var res = await fetch("/call/prepare?channel=" + encodeURIComponent(agent), { method: "POST" });
      var data = await res.json();
      var asr = data.asr || {}, tts = data.tts || {};
      var bits = [];
      bits.push("whisper: " + (asr.state || "?") + (asr.health && asr.health.device ? " · " + asr.health.device : ""));
      bits.push("voice: " + (tts.state || "?") + (tts.error ? " · " + tts.error : ""));
      note.textContent = bits.join("  |  ");
      line("system", "engines: " + bits.join(", "));
      return data;
    } catch (e) {
      note.textContent = "could not prepare the call: " + (e.message || e);
      return null;
    }
  }

  var starting = false;

  async function begin() {
    if (starting) return;                                  // one call at a time, however often the button is hit
    if (phase !== "idle") return;
    if (!agent) { note.textContent = "choose an agent first"; return; }
    starting = true;
    setPhase("waking");
    await prepare();
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,           // the agent must not hear itself
          noiseSuppression: gate.denoise !== "off",   // the browser suppressor (WebRTC NS, RNNoise-derived)
          autoGainControl: false,           // gain changes would move the dBFS gate under the user's feet
        },
      });
    } catch (e) {
      setPhase("ended");
      starting = false;
      note.textContent = "microphone refused: " + (e.message || e);
      return;
    }
    audio = new AudioContext();
    capture.rate = audio.sampleRate;
    var source = audio.createMediaStreamSource(stream);
    // a high-pass under the gate: rumble and fan noise below speech, which the browser suppressor leaves alone
    var highpass = audio.createBiquadFilter();
    highpass.type = "highpass";
    highpass.frequency.value = gate.denoise === "rnnoise" ? 85 : 20;
    highpass.Q.value = 0.7;
    source.connect(highpass);
    analyser = audio.createAnalyser();
    analyser.fftSize = 1024;
    highpass.connect(analyser);

    // a processor on the same stream: blocks arrive here, the gate decides which of them belong to an utterance
    capture.proc = audio.createScriptProcessor(4096, 1, 1);
    highpass.connect(capture.proc);
    capture.proc.connect(audio.destination);
    capture.proc.onaudioprocess = function (event) {
      var input = event.inputBuffer.getChannelData(0);
      var copy = new Float32Array(input.length);
      copy.set(input);
      capture.buffer.push(copy);
      // never hold more than 30 seconds of audio in memory
      var total = capture.buffer.reduce(function (n, c) { return n + c.length; }, 0);
      while (total > capture.rate * 30 && capture.buffer.length) total -= capture.buffer.shift().length;
    };
    var data = new Uint8Array(analyser.fftSize);
    setPhase("listening");
    line("system", "call opened with " + agent);
    note.textContent = "microphone live · speech-to-text is not installed yet (whisper-small needs a torch venv), so this "
                     + "call meters audio and runs the phase machine without transcribing";
    timer = setInterval(function () {
      analyser.getByteTimeDomainData(data);
      var peak = 0;
      for (var i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128) / 128);
      draw(peak);
      // the frame is only captured when it is inside the band; outside it, nothing is sent and the state stays put
      var levelDb = toDb(peak);
      gate.levelDb = levelDb;
      var open = gateStep(levelDb, gate.isOpen);
      if (open !== gate.isOpen) {
        gate.isOpen = open;
        var badge = document.getElementById("gate-state");
        if (badge) {
          badge.dataset.open = open ? "true" : "false";
          badge.textContent = open
            ? "gate: open · capturing (" + levelDb.toFixed(0) + " dBFS)"
            : "gate: closed · waiting for " + sliderToDb(gate.open).toFixed(0) + " dBFS (now "
              + levelDb.toFixed(0) + ")";
        }
      }
      if (open) {
        capture.speaking = true;
        capture.tail = 0;
        if (phase === "listening") setPhase("deciding");
      } else if (capture.speaking) {
        capture.tail += 1;
        if (capture.tail >= 5) {          // ~500 ms of silence after speech: the utterance is complete
          capture.speaking = false;
          var chunks = capture.buffer.slice();
          capture.buffer = [];
          transcribeUtterance(chunks);
        }
      }
    }, 100);
  }

  function finish(reason) {
    if (phase === "idle" || phase === "ended") return;     // ending twice must not restart anything
    stopVoice();
    if (timer) { clearInterval(timer); timer = null; }
    if (capture.proc) { try { capture.proc.disconnect(); } catch (e) { } capture.proc = null; }
    capture.buffer = []; capture.speaking = false; capture.tail = 0;
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    if (audio) { audio.close().catch(function () { }); audio = null; }
    draw(0);
    setPhase("ended");
    line("system", "call ended" + (reason ? ": " + reason : ""));
    setTimeout(function () { setPhase("idle"); starting = false; }, 1200);
  }

  function toggleMute() {
    if (!stream) return;
    var track = stream.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    muteBtn.textContent = track.enabled ? "Mute" : "Unmute";
    line("system", track.enabled ? "microphone unmuted" : "microphone muted");
  }

  async function start() {
    rail = document.getElementById("call-rail");
    list = document.getElementById("call-list");
    stage = document.getElementById("call-stage");
    portrait = document.getElementById("call-portrait");
    nameEl = document.getElementById("call-name");
    subEl = document.getElementById("call-sub");
    sayEl = document.getElementById("call-say");
    note = document.getElementById("call-note");
    transcript = document.getElementById("call-transcript");
    wave = document.getElementById("call-wave");
    startBtn = document.getElementById("call-start");
    endBtn = document.getElementById("call-end");
    muteBtn = document.getElementById("call-mute");
    if (!list) return;
    setPhase("idle");
    loadGate();
    var openEl = document.getElementById("gate-open"), closeEl = document.getElementById("gate-close");
    var openVal = document.getElementById("gate-open-value"), closeVal = document.getElementById("gate-close-value");
    var settingsToggle = document.getElementById("call-settings-toggle");
    var settingsBody = document.getElementById("call-settings-body");
    function syncGate() {
      if (openEl) openEl.value = String(gate.open);
      if (closeEl) closeEl.value = String(gate.close);
      var denoiseBox = document.getElementById("voice-denoise");
      if (denoiseBox) denoiseBox.value = gate.denoise;
      var chunkBox = document.getElementById("voice-chunk");
      if (chunkBox) chunkBox.value = String(gate.chunkWords);
      if (openVal) openVal.textContent = dbLabel(gate.open);
      if (closeVal) closeVal.textContent = dbLabel(gate.close);
      var help = document.getElementById("gate-help");
      if (help && gate.close >= gate.open) {
        help.innerHTML = "<b>close</b> must be quieter than <b>open</b> — it is the level the gate falls back to. "
                       + "With close above open the gate closes the moment it opens.";
      }
    }
        gate.chunkWords = 0;
    var denoiseEl = document.getElementById("voice-denoise");
    if (denoiseEl) {
      denoiseEl.value = gate.denoise;
      denoiseEl.addEventListener("change", function () { gate.denoise = denoiseEl.value; saveGate(); });
    }
    if (openEl) openEl.addEventListener("input", function () {
      gate.open = Number(openEl.value); syncGate(); saveGate();
    });
    if (closeEl) closeEl.addEventListener("input", function () {
      gate.close = Number(closeEl.value); syncGate(); saveGate();
    });
    if (settingsToggle) settingsToggle.addEventListener("click", function () {
      var nowHidden = settingsBody.hidden = !settingsBody.hidden;
      settingsToggle.setAttribute("aria-expanded", nowHidden ? "false" : "true");
      settingsToggle.classList.toggle("collapsed", nowHidden);
    });
    syncGate();
    paintMetrics();
    if (settingsToggle) settingsToggle.classList.toggle("collapsed", settingsBody.hidden);
    window.AthenaCallGate = { get: function () { return Object.assign({}, gate); },
                              set: function (o, c) { gate.open = o; gate.close = c; syncGate(); saveGate(); } };
    startBtn.addEventListener("click", function () {
      if (window.AthenaVoice && window.AthenaVoice.unlock) window.AthenaVoice.unlock();
      begin();
    });
    endBtn.addEventListener("click", function () { finish("ended by you"); });
    muteBtn.addEventListener("click", toggleMute);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden && phase !== "idle" && phase !== "ended") finish("tab hidden");
    });

    try {
      var data = await (await fetch("/agents", { headers: { accept: "application/json" } })).json();
      var agents = (data.agents || []).slice().sort(function (a, b) {
        if (a.channel === "default") return -1;
        if (b.channel === "default") return 1;
        return String(a.channel).localeCompare(String(b.channel));
      });
      agents.forEach(function (a) {
        var li = el("li");
        var card = el("button", "call-card");
        card.type = "button";
        var dot = el("span", "call-dot");
        dot.dataset.state = a.state || "idle";
        var copy = el("span", "call-copy");
        copy.appendChild(el("span", "call-card-name", a.channel));
        copy.appendChild(el("span", "call-card-meta", (a.state || "idle") + " · " + (a.model || "inherits")));
        card.appendChild(dot);
        card.appendChild(copy);
        card.addEventListener("click", function () { pick(a.channel, card); });
        li.appendChild(card);
        list.appendChild(li);
      });
      if (!agents.length) list.appendChild(el("li", "muted small", "no agents"));
    } catch (e) {
      list.appendChild(el("li", "muted small", "could not read agents: " + (e.message || e)));
    }
  }

  document.addEventListener("DOMContentLoaded", start);
  if (document.readyState !== "loading") start();
})();
