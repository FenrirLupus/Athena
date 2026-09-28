"""Identity — the YAML frontmatter at the top of ASSISTANT.md and USER.md.

the Operator's spec: a metadata block at the top of each identity file carries
as much identity information as possible (name_first/name_last/name_nick,
gender, sexuality, sun/moon/rising signs, birth date, home, role, …).
The name variables MATCH the schema's (name_first/name_last/name_nick).

    ---
    name_first: "Athena"
    name_nick: "Athena"
    gender: "female"
    ...
    ---

The flow names come from here FIRST, then config.yaml's identity section,
then the fallbacks (Assistant / User).
"""
from __future__ import annotations

import re
from pathlib import Path


def read_frontmatter(path: Path) -> dict:
    """Parse the YAML frontmatter block at the top of a file.

    Format (the Operator's strict --- delimiter contract):
        ---              ← OPEN
        key: value       ← YAML
        ---              ← CLOSE
        <body>
        ---              ← TRAILING (the block is delimited on both sides)

    Returns {} when there is no frontmatter or it can't be parsed (the
    identity falls back gracefully).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"identity file unreadable: {exc}", source="config",
                  action="read_frontmatter", target=str(path))
        return {}
    if not text.startswith("---"):
        return {}
    # Find the closing --- (second line that is exactly ---).
    lines = text.splitlines()
    end = None
    for i in range(1, min(len(lines), 80)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end])
    try:
        import yaml
        data = yaml.safe_load(block)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"identity frontmatter parse failed: {exc}",
                  source="config", action="read_frontmatter", target=str(path))
        return {}




def _default_base() -> Path:
    """The default profile's home (the Operator's spec: profiles/.default/)."""
    from core.config import DEFAULT_PROFILE_ROOT
    return DEFAULT_PROFILE_ROOT


def agent_identity(profile_root: Path | None = None) -> dict:
    """The agent's identity from the profile's ASSISTANT.md frontmatter.

    profile_root: the profile's root dir (profiles/<name>/); None = the
    default profile's assistant/ (profiles/.default/).
    """
    base = profile_root if profile_root is not None else _default_base()
    path = base / "assistant" / "ASSISTANT.md"
    if not path.exists():
        path = base / "ASSISTANT.md"
    return read_frontmatter(path)


def user_identity(profile_root: Path | None = None) -> dict:
    """The operator's identity from the profile's USER.md frontmatter."""
    base = profile_root if profile_root is not None else _default_base()
    path = base / "user" / "USER.md"
    if not path.exists():
        path = base / "USER.md"
    return read_frontmatter(path)


def display_name(identity: dict, fallback: str = "") -> str:
    """The best display name from an identity dict.

    Uses the SCHEMA's variable names (name_first / name_last / name_nick
    — the Operator's rule: .md files match the schema). Prefers name_nick,
    then name_first, then first+last, then the fallback.
    """
    nick = (identity.get("name_nick") or identity.get("nickname") or "").strip()
    first = (identity.get("name_first") or identity.get("first_name") or "").strip()
    last = (identity.get("name_last") or identity.get("last_name") or "").strip()
    if nick:
        return nick
    if first and last:
        return f"{first} {last}"
    if first:
        return first
    return fallback


# -- Prompt-file guardrails (the Operator's 10%-soft / 30%-max doctrine) -----

# Each prompt section has a SOFT limit of 10% of the model's context
# window. The TOTAL for all files is capped at 30% of the window (NOT
# 5 × 10% = 50%): the #-priority system decides which sections/blocks
# actually fill the budget, so tokens stay cheap and optimized.
#     Soft per section: 10% of window (3200 @ 32k)
#     Max total:        30% of window (32768 × 0.30 = 9830.4 → 9830)
# Rounding: down when the fraction < 0.5, up when ≥ 0.5.
PROMPT_SECTIONS = 5
SECTION_SOFT_FRACTION = 0.10
TOTAL_MAX_FRACTION = 0.30
_BOUNDARY = 1024
_WORDS_PER_TOKEN = 1.3


def _context_window() -> int:
    try:
        from core.config import load_config
        cfg = load_config()
        return int(cfg.get("compression", {}).get("context_window", 32000) or 32000)
    except Exception:
        return 32000


def _round_half(value: float) -> int:
    """Round down when the fraction < 0.5, up when ≥ 0.5."""
    return int(value + 0.5) if (value - int(value)) >= 0.5 else int(value)


def prompt_file_total_budget() -> int:
    """The TOTAL budget for all prompt files: 30% of the window.

    30% of the model's context window, rounded half-up/down (the Operator's
    rule): 32768 × 0.30 = 9830.4 → 9830.
    """
    return _round_half(_context_window() * TOTAL_MAX_FRACTION)


def section_token_budget() -> int:
    """The per-section SOFT limit: 10% of the model's window (3200 @ 32k).

    A section may grow to 10% before the #-priority system decides what
    actually fits within the 30% total.
    """
    return int(_context_window() * SECTION_SOFT_FRACTION)


def dynamic_budget(static_tokens: int = 0) -> int:
    """The budget for the DYNAMIC sections (Assistant/User/History).

    The 30% total is shared: STATIC sections (System, Guidelines) take
    only what they actually use, and the REST flows to the dynamic
    sections. Static content doesn't waste budget — the dynamic sections
    (which change every turn) get the remainder.
    """
    total = prompt_file_total_budget()
    if static_tokens <= 0:
        # Measure the actual static sections (System + Guidelines) so the
        # dynamic share is accurate as they grow or shrink.
        try:
            from context.prompt_builder import _environment_block, _tools_index
            from core.guidelines import GUIDELINES
            static_words = (len(_environment_block().split())
                            + len(_tools_index().split())
                            + len(GUIDELINES.split()))
            static_tokens = int(static_words / _WORDS_PER_TOKEN)
        except Exception:
            static_tokens = 0
    return max(0, total - int(static_tokens))


def identity_token_budget() -> int:
    """The identity-file budget: the dynamic share, split across the
    dynamic sections. Identity files are ONE dynamic section; they get
    their portion of what's left after the static sections.
    """
    # The dynamic sections: Assistant identity, User identity, History.
    # Split the dynamic budget evenly-ish; the #-priority system decides
    # the actual inclusion. Default: 2 identity files + history share.
    dyn = dynamic_budget()
    return max(1, int(dyn / 3))


def identity_word_budget() -> int:
    """The word budget for an identity file (word-count basis, token backup)."""
    return int(identity_token_budget() * _WORDS_PER_TOKEN)


def identity_over_budget(text: str, budget: int | None = None) -> bool:
    """True when the identity text exceeds the token budget."""
    budget = budget if budget is not None else identity_token_budget()
    words = len(re.findall(r"\S+", text))
    return int(words / _WORDS_PER_TOKEN) > budget


def priority_of_block(block: str) -> int:
    """The # priority of a --- section: the FIRST heading's # count.

    MATCHES the memory module's convention: MORE # = MORE important
    (the Operator's rule):
        #        = priority 1 (least important)
        ##       = priority 2
        ###      = priority 3
        ####     = priority 4
        #####    = priority 5
        ######   = priority 6 (MOST important)
    A section with no heading gets the default 3.
    """
    for line in block.splitlines():
        s = line.strip()
        m = re.match(r"^(#{1,6})\s+", s)
        if m:
            return min(6, max(1, len(m.group(1))))
    return 3


def assemble_priority_blocks(sections: list[str],
                             total_budget: int | None = None,
                             soft_budget: int | None = None) -> list[str]:
    """Include --- sections by PRIORITY within the budget (the Operator's rule).

    - Every section's # level is its priority (more # = more important).
    - Sections are included HIGH-priority FIRST.
    - The SOFT limit (10% of window) applies per section.
    - The TOTAL (30% of window) caps everything.
    - Higher-priority sections always win the budget; lower ones fill
      only when there is room.
    """
    total_budget = total_budget if total_budget is not None else prompt_file_total_budget()
    soft_budget = soft_budget if soft_budget is not None else section_token_budget()
    scored = [(priority_of_block(s), s) for s in sections]
    scored.sort(key=lambda pair: -pair[0])  # highest priority first
    included: list[str] = []
    used_words = 0
    for prio, section in scored:
        words = len(re.findall(r"\S+", section))
        # Section soft limit: a single section may not exceed 10%.
        if int(words / _WORDS_PER_TOKEN) > soft_budget:
            continue
        # Total limit: 30% of the window.
        if int((used_words + words) / _WORDS_PER_TOKEN) > total_budget:
            continue
        included.append(section)
        used_words += words
    # Restore the original file order (inclusion is by priority, but the
    # rendered order stays natural).
    order = {s: i for i, s in enumerate(sections)}
    included.sort(key=lambda s: order.get(s, 0))
    return included


def trim_identity_blocks(text: str, budget: int | None = None) -> str:
    """Fit identity text to the budget: DEDUPE → SIMPLIFY → TRUNCATE.

    the Operator's doctrine: fitting means dedupe/simplify/optimize FIRST —
    never blind truncation when a smarter reduction exists.

        1. DEDUPE: drop duplicate lines (same fact repeated across
           sections, e.g. the same bullet in multiple --- blocks).
        2. SIMPLIFY: collapse repeated phrases (a word repeated many
           times in a bullet becomes one mention).
        3. BLOCK-TRIM: drop the LAST --- sections (lower priority than
           the opening identity) until it fits.
        4. WORD-CUT: only when even the first block alone exceeds the
           budget (a hard fallback, never the first resort).
    """
    budget = budget if budget is not None else identity_token_budget()
    if not identity_over_budget(text, budget):
        return text
    import re as _re

    # 1. DEDUPE: remove exact-duplicate lines (preserving order).
    seen = set()
    deduped_lines = []
    for line in text.splitlines():
        key = line.strip()
        if key and key in seen:
            continue
        seen.add(key)
        deduped_lines.append(line)
    text = "\n".join(deduped_lines)
    if not identity_over_budget(text, budget):
        return text

    # 2. SIMPLIFY: collapse repeated words inside bullets ("word word word
    #    word word …" → "word").
    def _simplify_line(line: str) -> str:
        s = line.strip()
        if not s.startswith("-"):
            return line
        tokens = s.split()
        out = []
        prev = None
        for t in tokens:
            if t != prev:
                out.append(t)
            prev = t
        return "- " + " ".join(out[1:]) if out[1:] else line

    text = "\n".join(_simplify_line(l) for l in text.splitlines())
    if not identity_over_budget(text, budget):
        return text

    # 3. BLOCK-TRIM: keep the leading sections, drop the last ones.
    parts = _re.split(r"(?m)^---\s*$", text)
    sections = [p for p in parts if p.strip()]
    if sections:
        kept = []
        for section in sections:
            candidate = "---\n" + "\n---\n".join(kept + [section]) + "\n---"
            if identity_over_budget(candidate, budget) and kept:
                break
            kept.append(section)
        trimmed = "---\n" + "\n---\n".join(kept) + "\n---"
        if not identity_over_budget(trimmed, budget):
            return trimmed

    # 4. WORD-CUT: the hard fallback.
    words = _re.findall(r"\S+", text)
    budget_words = identity_word_budget()
    if len(words) > budget_words:
        return " ".join(words[:budget_words])
    return text
