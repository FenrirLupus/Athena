"""Mentions — one agent addressing another with @name.

An agent asks another agent a question by writing `@scout: what do you know about X?`. The server turns
that into a **queued turn on the target**, and the target's answer comes back as a queued turn on the
asker. Three turns, no nested call:

    turn 1  A's reply contains @B: <question>      → a job is queued on B, authored by A
    turn 2  B answers, in its own context          → its reply is queued back to A
    turn 3  A receives it as a message from B      → and decides what to do

Never a synchronous A→B call while B might call A: that is a deadlock, and the enqueue is what makes the
hive safe to have loops in. The cost is that the asker is not *blocked* waiting — the answer arrives as its
own turn, which is also how a human colleague works.

Why `@` and not a tool: a tool call is an action the model performs inside one turn; a mention is
*addressing*, and the answer has to survive into another agent's context. Keeping it in the transcript text
means the exchange is readable in both channels, and it works for a model that cannot call tools at all.
"""
from __future__ import annotations

import re

# An address at a word boundary, either bare (`@scout`) or leading a question (`@scout: …`).
MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,31})")

MAX_MENTIONS = 2          # per reply; more than this is a broadcast, which is not what a mention means
MAX_HOPS = 3              # ask → answer → receive; a fourth hop risks an echo chamber


def find(text: str, known: list[str] | None = None, exclude: str = "") -> list[str]:
    """Mentioned agent names, in order, de-duplicated, capped — and never the speaker itself."""
    seen, out = set(), []
    for match in MENTION_RE.finditer(text or ""):
        name = match.group(1).strip()
        if not name or name == exclude or name in seen:
            continue
        if known is not None and name not in known:
            continue                      # @somefile or @someone-unknown is not an address
        seen.add(name)
        out.append(name)
        if len(out) >= MAX_MENTIONS:
            break
    return out


def strip(text: str, names: list[str]) -> str:
    """The reply with its addresses removed — what the channel itself says, minus the routing."""
    out = text or ""
    for name in names:
        out = re.sub(rf"(?<![\w@])@{re.escape(name)}\b:?\s*", "", out)
    return out.strip()


def question_for(text: str, target: str) -> str:
    """The part of a reply addressed to `target`, or the whole reply when it is not marked up."""
    pattern = re.compile(rf"(?<![\w@])@{re.escape(target)}\b:?\s*(.+?)(?=(?:@[A-Za-z0-9][A-Za-z0-9_-]*)|$)", re.S)
    match = pattern.search(text or "")
    return (match.group(1).strip() if match else (text or "").strip())
