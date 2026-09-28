"""Turn finalizer — the clean end-of-turn step (the turn-finalizer pattern).

the Operator's spec: the finalizer sanitizes the reply and marks the turn
closed — guaranteeing the vault never stores junk. It runs at the END
of every turn, after the reply is produced:

  1. strip control characters / zero-width junk from the reply
  2. trim runaway whitespace
  3. strip common AI-isms (the "as an AI" boilerplate) — reply_cleanup
  4. mark the turn closed in the session-state flow machine

The model's reply enters the record CLEAN; the system's complexity stays
in the backend. This is the hands-off-button philosophy: simple in,
clean out.
"""
from __future__ import annotations

import re

# Control chars that corrupt stored records (except \n \t \r).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Runaway whitespace: 3+ blank lines collapse to 1; trailing space trimmed.
_WS_RE = re.compile(r"\n{3,}")
_TRAIL_RE = re.compile(r"[ \t]+$", re.M)
# Common AI-isms to strip (the reply_cleanup layer).
_AI_ISMS = (
    "as an ai", "as a language model", "as an ai language model",
    "i don't have feelings", "i don't have personal",
    "i'm just an ai", "i am just an ai",
)


def sanitize_reply(reply: str) -> str:
    """Clean a reply for the archive: no control chars, no AI-isms."""
    if not reply:
        return reply
    text = _CTRL_RE.sub("", reply)
    text = _WS_RE.sub("\n\n", text)
    text = _TRAIL_RE.sub("", text)
    # Strip leading AI-ism boilerplate lines (keep the real content).
    lines = text.split("\n")
    out = []
    for line in lines:
        low = line.strip().lower()
        if any(ism in low for ism in _AI_ISMS) and len(line.strip()) < 120:
            continue  # drop the boilerplate line
        out.append(line)
    text = "\n".join(out).strip()
    return text


def finalize_turn(session_id: str, reply: str) -> str:
    """The end-of-turn step: sanitize + mark closed.

    Returns the CLEAN reply (what gets stored).
    """
    clean = sanitize_reply(reply)
    try:
        from core.session_state import get_state
        get_state(session_id).finish_turn()
    except Exception as exc:
        _log(3, f"finalize turn state failed: {exc}",
             source="turn_finalizer")
    return clean


def _log(level: int, msg: str, source: str = "turn_finalizer") -> None:
    """The finalizer is operational — state failures are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass
