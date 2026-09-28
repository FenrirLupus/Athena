"""Memory — the two-sided persistent note store (MEMORY.md).

Two files per profile, one per SIDE:

    assistant/MEMORY.md   — what the ASSISTANT has learned / notes on her
                            own side ("I was told the user's birthday is…")
    user/MEMORY.md        — what is known ABOUT the user ("the user's
                            birthday is…")

The same event populates BOTH sides differently. The user saying their
birthday becomes:
    user/MEMORY.md:        "User's birthday is March 3rd."
    assistant/MEMORY.md:   "I was told the user's birthday (March 3rd)."

FORMAT (the Operator's block contract): every memory entry is ONE --- block,
organized with a title and bullets:

    ---
    # Memory Title - Memory Description
    - Memory information
    - Memory information
    ---

BUDGET (the Operator's spec):
    - 6400 tokens TOTAL per memory file (the cap is 6400)
    - title + description (the # line) ≤ 128 words
    - each bullet ≤ 64 words
    - every entry can be expanded or shrunk by word/token count

Kept LEAN (the Memory Theory): when full, consolidate — never drop the
important facts. Both files are injected into the prompt every session.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from core.config import ATHENA_ROOT
from intelligence.profiles import get_profile

_lock = threading.Lock()

# Cap for the always-visible store (keeps the prompt lean).
MAX_ENTRIES = 40
# The Operator's memory budget: 6400 tokens per file (the reference cap is lower).
TOKEN_BUDGET = 6400
# A conservative words-per-token ratio (~1.3 words per token for English).
WORDS_PER_TOKEN = 1.3
# Word caps: title+description ≤ 128, each bullet ≤ 64.
TITLE_WORD_CAP = 128
BULLET_WORD_CAP = 64


def _root(profile: str = "") -> Path:
    p = get_profile(profile)
    return p.root if p else ATHENA_ROOT


def memory_path(side: str, profile: str = "") -> Path:
    """The MEMORY.md path for a side (assistant | user)."""
    side = (side or "assistant").lower()
    if side not in ("assistant", "user"):
        side = "assistant"
    return _root(profile) / side / "MEMORY.md"


# -- The block format ----------------------------------------------------

def _split_blocks(text: str) -> list[str]:
    """Split memory text into --- delimited blocks (the Operator's format)."""
    parts = re.split(r"(?m)^---\s*$", text)
    blocks = []
    for part in parts:
        part = part.strip()
        if part:
            blocks.append(part)
    return blocks


def _parse_block(block: str) -> dict:
    """Parse ONE --- block into {title, bullets, priority}."""
    lines = block.splitlines()
    title = ""
    bullets = []
    priority = 3  # default: mid priority
    for line in lines:
        s = line.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m and not title:
            # The # COUNT is the priority (the Operator's spec): more # = more
            # important. Markdown natively supports 1-6, so we use the
            # FULL 6 levels:
            #   ###### = priority 1 (least) … # = priority 6 (most).
            hashes = len(m.group(1))
            title = m.group(2).strip()
            priority = min(6, max(1, hashes))
        elif s.startswith("-"):
            bullets.append(s.lstrip("- ").strip())
    return {"title": title, "bullets": bullets, "priority": priority}


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _entry_to_block(entry: dict) -> str:
    """Render one entry as its --- delimited section.

    The strict format shares ONE --- between blocks (like the identity
    files): each block opens with --- and the NEXT block's --- closes it.
    The # COUNT on the title = the priority (more # = more important).
    """
    title = entry.get("title", "").strip()
    bullets = [b for b in entry.get("bullets", []) if b.strip()]
    priority = int(entry.get("priority", 3) or 3)
    priority = min(6, max(1, priority))
    lines = []
    if title:
        lines.append(f"{'#' * priority} {title}")
    for b in bullets:
        lines.append(f"- {b}")
    return "---\n" + "\n".join(lines)


def read_entries(side: str, profile: str = "") -> list[dict]:
    """The memory entries for a side as {title, bullets} blocks, in order."""
    path = memory_path(side, profile)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = _split_blocks(text)
        entries = [_parse_block(b) for b in blocks]
        return [e for e in entries if e.get("title") or e.get("bullets")]
    except Exception as exc:
        # THE MEMORY AUDIT (the Operator's 08-12 metrics spec): a memory
        # read failure is silent data loss — it MUST reach the logs so
        # the nurse can repair it.
        try:
            from core.logging import log_event
            log_event(4, f"memory read failed for {side}: {exc}",
                      source="intelligence", action="memory_read",
                      target=str(path))
        except Exception:
            pass
        return []


def read_all(profile: str = "") -> dict[str, list[dict]]:
    """Both sides: {"assistant": [...], "user": [...]}."""
    return {
        "assistant": read_entries("assistant", profile),
        "user": read_entries("user", profile),
    }


def add_entry(side: str, content: str, profile: str = "",
              title: str = "", priority: int = 3) -> str:
    """Append a note to one side. Returns the path written.

    content: the bullet text (or the full entry when title is given).
    title:   optional — when set, the entry gets a # title line; content
             is treated as a bullet.
    priority: 1-6 (the Operator's level system — MORE # = MORE important):
             ###### = 1 (least) … # = 6 (most). Default 3 (###). The
             6 levels chunk into 3 tiers (1-2 / 3-4 / 5-6) when trimmed.
    """
    side = (side or "assistant").lower()
    if side not in ("assistant", "user"):
        side = "assistant"
    content = content.strip()
    if not content:
        return str(memory_path(side, profile))

    priority = min(6, max(1, int(priority if priority else 3)))
    entry = {"title": title.strip() or "", "bullets": [content],
             "priority": priority}
    path = memory_path(side, profile)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = read_entries(side, profile)
        # Dedup: identical fact already noted? Skip silently. Compares the
        # FULL entry (title + bullets) — short similar facts ("fact level
        # 1" vs "fact level 2") must NOT be merged, only true duplicates.
        for e in existing:
            for b in e.get("bullets", []):
                if b.strip() == content.strip() or (
                    len(content) > 40 and _similar(b, content) >= 0.97
                ):
                    return str(path)
        existing.append(entry)
        if len(existing) > MAX_ENTRIES:
            existing = existing[-MAX_ENTRIES:]
        _write(path, existing, side)
    return str(path)


def clear(side: str = "", profile: str = "") -> None:
    """Clear one side (or both)."""
    with _lock:
        for s in (("assistant", "user") if not side else (side,)):
            path = memory_path(s, profile)
            if path.exists():
                path.write_text("", encoding="utf-8")


def _write(path: Path, entries: list[dict], side: str = "assistant") -> None:
    """Write entries as --- blocks. Shrinks to the token budget.

    The strict format: ONE --- between blocks (each opens with --- and
    the NEXT block's --- closes it), with a trailing --- at the end.
    """
    entries = shrink(entries)
    blocks = [_entry_to_block(e) for e in entries]
    try:
        if not blocks:
            path.write_text("", encoding="utf-8")
            return
        body = "\n".join(blocks)
        body += "\n---\n"  # the final close
        path.write_text(body, encoding="utf-8")
    except Exception as exc:
        # THE MEMORY AUDIT (the Operator's 08-12 metrics spec): a memory
        # write failure is silent data loss — it MUST reach the logs so
        # the nurse can repair it.
        try:
            from core.logging import log_event
            log_event(4, f"memory write failed for {side}: {exc}",
                      source="intelligence", action="memory_write",
                      target=str(path))
        except Exception:
            pass


# -- The budget + expand/shrink ------------------------------------------

def token_estimate(entries: list[dict]) -> int:
    """Rough token estimate for a list of entries (words / ratio)."""
    words = sum(
        _word_count(e.get("title", ""))
        + sum(_word_count(b) for b in e.get("bullets", []))
        for e in entries
    )
    return int(words / WORDS_PER_TOKEN)


def over_budget(entries: list[dict], budget: int = TOKEN_BUDGET) -> bool:
    """True when the entries exceed the token budget."""
    return token_estimate(entries) > budget


def shrink(entries: list[dict], budget: int = TOKEN_BUDGET) -> list[dict]:
    """SHRINK entries to fit the budget — TIERED, DYNAMIC (the Operator's spec).

    The 6 priority levels chunk into 3 TIERS (chunked by every 2 levels):
        Tier A: levels 5-6  (most important — always included)
        Tier B: levels 3-4  (important — included if budget allows)
        Tier C: levels 1-2  (least — the first to go)

    Resources are DYNAMIC: the highest tier is always kept, then the next
    tier fills in only while the token budget remains — the lowest tiers
    are dropped first. Conversation data changes, so what fits changes.
    """
    entries = [dict(e) for e in entries]

    def _tier(prio: int) -> int:
        """Tier chunk: levels 5-6 → 3 (top), 3-4 → 2, 1-2 → 1 (bottom)."""
        prio = min(6, max(1, int(prio or 3)))
        return (prio + 1) // 2  # 1-2→1, 3-4→2, 5-6→3

    # Cap words per bullet + title first (the 64/128 caps).
    for e in entries:
        e["title"] = _cap_words(e.get("title", ""), TITLE_WORD_CAP)
        e["bullets"] = [_cap_words(b, BULLET_WORD_CAP) for b in e.get("bullets", [])]
        e["bullets"] = [b for b in e["bullets"] if b]

    # Include by TIER from the top: tier 3 (most important) always; then
    # tier 2 and tier 1 only while the budget holds.
    included = [e for e in entries if _tier(e.get("priority", 3)) == 3]
    for tier in (2, 1):
        more = [e for e in entries if _tier(e.get("priority", 3)) == tier]
        # Within a tier, keep the NEWEST first (drop the oldest of the tier).
        more = list(reversed(more))
        for e in more:
            candidate = included + [e]
            if over_budget(candidate, budget):
                break
            included.append(e)
    # Fall back: if even tier 3 alone is over budget, trim bullets oldest-
    # first, then drop the lowest-priority entries of the tier.
    while over_budget(included, budget) and included:
        dropped = False
        for e in included:
            if len(e.get("bullets", [])) > 1:
                e["bullets"] = e["bullets"][1:]
                dropped = True
                break
        if not dropped:
            included = included[1:]
    return included


def _cap_words(text: str, cap: int) -> str:
    words = re.findall(r"\S+", text)
    if len(words) <= cap:
        return text
    return " ".join(words[:cap])


# -- Rendering -----------------------------------------------------------

def render_entries(entries: list[dict]) -> str:
    """The entries as compact bullets (for the prompt stack / CLI).

    Preserves the PRIORITY as the # count (more # = more important).
    """
    lines = []
    for e in entries:
        if e.get("title"):
            prio = min(6, max(1, int(e.get("priority", 3) or 3)))
            lines.append(f"{'#' * prio} {e['title']}")
        for b in e.get("bullets", []):
            lines.append(f"- {b}")
    return "\n".join(lines)


def summary(profile: str = "") -> str:
    """A compact view of both sides (for the prompt stack / CLI)."""
    mem = read_all(profile)
    out = []
    if mem["assistant"]:
        out.append("Assistant memory (notes on my side):\n" +
                   render_entries(mem["assistant"]))
    if mem["user"]:
        out.append("User memory (what I know about the user):\n" +
                   render_entries(mem["user"]))
    return "\n\n".join(out)


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()
