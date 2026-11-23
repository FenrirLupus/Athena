"""Prompt-size estimation — one implementation, because two counts that disagree make every budget a guess.

The value decides two things: what a turn reports as its prompt estimate, and which history rows `_trim_to_budget`
drops when a spoken turn must fit a smaller prompt. That second use is why the function is not a diagnostic: a
different answer here changes which rows the model sees.

The guarded form is the canonical one. It equals the unguarded version on every input that version accepts, and
returns a number instead of raising on the two it does not: `None`, and a list content part that is not a mapping.
Both are unreachable from the turn loop — the live window is the loop's own list and every stored row's content is
a string — so the difference is a defined answer where the other form raises, and no real row reaches either.
"""
from __future__ import annotations

__all__ = ["messages_tokens", "PER_MESSAGE", "CHARS_PER_TOKEN"]

PER_MESSAGE = 8          # per-message framing
CHARS_PER_TOKEN = 2.5    # calibrated to what the provider bills, not to a guess


def messages_tokens(messages) -> int:
    """Approximate the prompt size a provider will bill for: characters over 2.5, plus framing per message."""
    total = 0
    for message in messages or []:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                total += len(str(part.get("text") or "")) if isinstance(part, dict) else len(str(part))
        total += PER_MESSAGE
    return int(total / CHARS_PER_TOKEN)
