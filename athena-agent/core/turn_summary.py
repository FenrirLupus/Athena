"""Turn summary — a concise recap after each turn (the turn summary).

the Operator's spec: after each turn the system records WHAT HAPPENED — a
one-line digest of the exchange, so the session becomes searchable by
summary (not just raw text). The summary is stored on the session row
(rolling) + included in the assistant vault entry's context field only
when the enrichment pass hasn't filled it.

Pure local generation — no provider call (simple + efficient).
"""
from __future__ import annotations

from core import db as db_layer


def build_summary(content: str, reply: str, *, tool_names: list | None = None,
                  skills: list | None = None) -> str:
    """A one-line recap of a turn: what was asked, what happened."""
    user_part = (content or "").strip().replace("\n", " ")[:120]
    reply_part = (reply or "").strip().replace("\n", " ")[:120]
    parts = [f"U: {user_part}" if user_part else "",
             f"A: {reply_part}" if reply_part else ""]
    if tool_names:
        parts.append(f"tools: {', '.join(str(t) for t in tool_names[:6])}")
    if skills:
        parts.append(f"skills: {', '.join(str(s) for s in skills[:4])}")
    line = " | ".join(p for p in parts if p)
    return line[:400]


def summarize_turn(session_id: str, content: str, reply: str, *,
                   tool_names: list | None = None,
                   skills: list | None = None,
                   profile: str = "") -> str:
    """Build + persist the turn's recap (rolling summary on the session)."""
    line = build_summary(content, reply, tool_names=tool_names,
                         skills=skills)
    try:
        # Rolling: keep the last 5 turn summaries (newest appended).
        prior = db_layer.get_session_summary(session_id, profile=profile)
        lines = [l for l in (prior or "").split("\n") if l.strip()]
        lines = lines[-4:]
        lines.append(line)
        db_layer.set_session_summary(session_id, "\n".join(lines),
                                     profile=profile)
    except Exception as exc:
        _log(3, f"turn summary write failed: {exc}", source="turn_summary")
    return line


def _log(level: int, msg: str, source: str = "turn_summary") -> None:
    """The turn-summary writer is operational — failures are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass
