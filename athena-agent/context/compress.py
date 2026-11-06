"""Compression — turn the evicted span into a digest for SUMMARY.md.

Two digests exist. `context/digest.py` writes the real one: the turn's own provider, its own system prompt, a
markdown summary with tables and ASCII where structure helps. This module is the **fallback** — mechanical,
deterministic, always available — used when the model call fails, returns a stub, or `budget.digest` is set to
`mechanical`. A compression must never fail because a summary could not be written: the snapshot and the vault
copy are the durable record.

`SUMMARY.md` is **replaced** on every compression (the previous one is archived as
`summary_<YYYY-MM-DD>_<HH-MM-SS>.md`), so it describes the window that just left and never grows into a log of
summaries.

What a digest must preserve, whenever it is produced (documentation/architecture/07 §3a):
  * anchors — decisions, file paths, parameter values, open threads
  * not narrative — how something was reached, the order of the conversation
"""
from __future__ import annotations

from core import trace

MAX_LINE_CHARS = 140
MAX_TURNS_PER_SECTION = 40


def mechanical_digest(evicted: list[dict]) -> str:
    """A compact, lossless-in-shape summary of the turns leaving the window.

    One span per compression, not one line per turn: the entry says how much left the window, the exit says how
    much of it the digest could keep and where the text goes. `Session.compress` owns the write to `SUMMARY.md`,
    so the span names that destination rather than writing here.
    """
    if not evicted:
        trace.event("context.compress.skipped", level="debug", messages=0, reason="nothing was evicted")
        return ""
    counts: dict[str, int] = {}
    for msg in evicted:
        role = msg.get("role") or "?"
        counts[role] = counts.get(role, 0) + 1

    shown = evicted[-MAX_TURNS_PER_SECTION:]
    chars_in = sum(len(str(msg.get("content") or "")) for msg in evicted)
    with trace.span("context.compress", level="info", mode="mechanical", messages=len(evicted),
                    chars_in=chars_in, kept=len(shown), cap_turns=MAX_TURNS_PER_SECTION) as sp:
        lines = [f"_Mechanical digest of {len(evicted)} turns "
                 f"({', '.join(f'{k}: {v}' for k, v in sorted(counts.items()))})._", ""]

        for msg in shown:
            role = (msg.get("role") or "?").strip()
            content = " ".join((msg.get("content") or "").split())
            if not content and msg.get("tool_calls"):
                names = ", ".join(tc.get("name", "?") for tc in msg["tool_calls"])
                content = f"[called {names}]"
            if len(content) > MAX_LINE_CHARS:
                content = content[:MAX_LINE_CHARS] + "…"
            lines.append(f"- **{role}**: {content}")

        if len(evicted) > len(shown):
            lines.append(f"- _…and {len(evicted) - len(shown)} earlier turns (see the snapshot)_")

        lines += ["", "_Mechanical digest — the model-written one (context/digest.py) was unavailable for this compression._"]
        text = "\n".join(lines)
        result = dict(messages=len(evicted), kept=len(shown), elided=len(evicted) - len(shown),
                      summary_chars=len(text), dest="SUMMARY.md")
        if len(evicted) > len(shown):
            result["reason"] = "only the newest turns are rendered; the rest live in the snapshot"
        sp.ok(**result)
        return text
