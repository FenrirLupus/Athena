"""Digest — the model-written SUMMARY.md, and the mechanical fallback.

Compression has two halves: the *mechanical* one (evict, snapshot, vault, truncate) which must never depend on a
model, and the *digest* which is worth a model call because it decides what the agent remembers.

`mechanical_digest()` stays as the fallback: it is cheap, deterministic, and always available. `model_digest()`
asks the turn's own provider, with its own system prompt, to write the block the agent will read before its next
turn — anchors over narrative, markdown, and ASCII where a diagram says it better than prose.

The transcript is passed **inline** rather than having the model open the snapshot file: the same bytes, one call
instead of a tool round, and `read_file` is workspace-scoped so it cannot reach `sessions/` anyway. The snapshot's
name is included so the digest can cite its own provenance.
"""
from __future__ import annotations

import re
from core.context_window import effective_window

MAX_TRANSCRIPT_CHARS = 24_000        # the digest prompt must not itself become the context problem

DIGEST_SYSTEM_PROMPT = """\
You are Athena's archivist. You are given the transcript of the turns that have just left an agent's context
window, and you write the block that replaces its memory of them: the summary the agent reads before it answers
next.

What to preserve — anchors, not narrative:
- decisions taken, and what was rejected and why
- paths, filenames, parameter values, model names, numbers, identifiers
- facts established about the system, the user, or the task
- open threads: what was started and not finished, what is waiting on what
- errors that were hit and how they were fixed
Drop: greetings, pleasantries, ordering of the conversation, restatements of the same point.

Format:
- Markdown. Short headed sections, for example `## Decisions`, `## Facts and values`, `## Open threads`.
- A compact table for values you would otherwise list twice.
- ASCII for structure only — a small flow or tree when a relationship is clearer drawn than described, in a
  fenced block. Never decorative art, never a diagram that repeats a sentence.
- Dense beats long. If a section would be empty, omit it; never invent a fact to fill one.
- Say plainly what is uncertain, for example "the retry limit was 3 (assumed, not verified)".

Output only the summary block: no greeting, no preamble, no closing line, no mention of being an archivist.

Source: {snapshot} · {count} turns
"""


def _render(evicted: list[dict]) -> str:
    """The transcript as the model sees it: compact, role-tagged, tool calls named."""
    lines = []
    for msg in evicted:
        role = (msg.get("role") or "?").strip()
        content = " ".join((msg.get("content") or "").split())
        if msg.get("tool_calls"):
            names = ", ".join(tc.get("name", "?") for tc in msg["tool_calls"])
            content = (content + f" [called {names}]").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        # keep the end: the most recent turns are the ones the agent is most likely to need
        text = "… (earlier turns elided)\n" + text[-MAX_TRANSCRIPT_CHARS:]
    return text


def model_digest(provider, evicted: list[dict], snapshot_name: str, cfg: dict) -> str:
    """Ask the turn's provider for a digest. Returns "" when the model call fails, never raises.

    An empty return is the signal to fall back to `mechanical_digest`: a compression must never fail because a
    summary could not be written — the snapshot and the vault copy are the durable record, the digest is the
    readable one.
    """
    if not evicted:
        return ""
    comp = cfg.get("compression") or {}
    win = effective_window(cfg)
    # SUMMARY.md must be at least `target_min_pct` of the window, so that is the default ceiling. 0 = compute.
    max_tokens = int(comp.get("digest_max_tokens") or 0) \
        or int(win * float(comp.get("target_min_pct", 20.0)) / 100)
    system = DIGEST_SYSTEM_PROMPT.format(snapshot=snapshot_name, count=len(evicted))
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _render(evicted)},
    ]
    try:
        resp = provider.call(messages, tools=None, max_tokens=max_tokens, temperature=0.2)
    except Exception as exc:                                  # noqa: BLE001 — fall back, never fail
        print(f"[digest] model digest failed ({type(exc).__name__}: {exc}); using the mechanical one",
              flush=True)
        return ""
    text = strip_fences((resp.get("content") or "").strip())
    if len(text) < 40:
        # a refusal or a truncated stub is worse than the mechanical digest
        print(f"[digest] model returned {len(text)} chars; using the mechanical one", flush=True)
        return ""
    return text


def strip_fences(text: str) -> str:
    """Drop a wrapping ```markdown fence — the block goes into SUMMARY.md as-is."""
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def digest_for(provider, cfg: dict):
    """The `digest_fn` to hand to `Session.compress` — model-backed unless disabled or unavailable."""
    from context import compress as compress_mod

    mode = str((cfg.get("budget") or {}).get("digest") or "auto").lower()
    if mode in ("mechanical", "off", "false"):
        return compress_mod.mechanical_digest

    def _digest(evicted: list[dict], snapshot_name: str = "") -> str:
        text = model_digest(provider, evicted, snapshot_name, cfg) if provider is not None else ""
        return text or compress_mod.mechanical_digest(evicted)

    return _digest
