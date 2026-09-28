"""Response length — the 7-level word-cap system (the Operator's spec).

The response CONTENT length is gauged by HOW THE USER RESPONDS + their
QUERY. Only ONE level is selected per turn, and it bounds the CONTENT
(the visible reply). REASONING is uncapped — the model may think as long
as it needs; only what it SAYS must stay under the limit:

    Extremely Low:   16 words
    Very Low:        32 words
    Low:             64 words
    Medium:         128 words
    High:           256 words
    Very High:      512 words
    Extremely High:1024 words   (deep research, programming)

LEARN-BY-DOING: the level adjusts from ACTUAL usage. When a response
comes in far under its cap (e.g. a Low query answered in 10 words), the
system learns that query type earns a LOWER level — promoting concise
responses. The learning is stored per-side (assistant/user) so it
persists across sessions.
"""
from __future__ import annotations

import re
from pathlib import Path

LEVELS = [
    {"name": "extremely-low", "label": "Extremely Low", "words": 16},
    {"name": "very-low", "label": "Very Low", "words": 32},
    {"name": "low", "label": "Low", "words": 64},
    {"name": "medium", "label": "Medium", "words": 128},
    {"name": "high", "label": "High", "words": 256},
    {"name": "very-high", "label": "Very High", "words": 512},
    {"name": "extremely-high", "label": "Extremely High", "words": 1024},
]
_LEVEL_BY_WORDS = {lvl["words"]: lvl for lvl in LEVELS}
_DEFAULT_LEVEL = LEVELS[3]  # Medium (128) — the middle ground

# The learn-by-doing store — PER PROFILE. Each agent (profile) learns
# its own response-length habits in its own runtime/ dir:
#     profiles/.default/runtime/response_length_learn.json  (the queen)
#     profiles/<name>/runtime/response_length_learn.json    (a worker)
# Resolved at CALL time so the ACTIVE profile's learning is used, never
# a frozen import-time constant.
_RUNTIME_DIR = "runtime"
_LEARN_FILENAME = "response_length_learn.json"


def _learn_dir(profile: str = "") -> Path:
    try:
        from intelligence.profiles import get_profile, default_profile
        p = get_profile(profile) if profile else None
        if p is None:
            p = default_profile()
        return p.root / _RUNTIME_DIR
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"response-length learn dir failed: {exc}",
                  source="core", action="response_length")
        return Path.home() / ".athena" / _RUNTIME_DIR


def _learn_file(profile: str = "") -> Path:
    return _learn_dir(profile) / _LEARN_FILENAME


def _load_learning(profile: str = "") -> dict:
    try:
        import json
        path = _learn_file(profile)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"adjustments": {}, "usages": {}}


def _save_learning(data: dict, profile: str = "") -> None:
    try:
        import json
        path = _learn_file(profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def levels() -> list[dict]:
    return [dict(l) for l in LEVELS]




def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def gauge(text: str, query: str = "", profile: str = "") -> dict:
    """Pick ONE response level from the user's message + their query.

    The rule (the Operator's spec):
        - The longer/more involved the user's message, the higher the
          level they earn (a one-liner → Very Low; a detailed question →
          High/Very High).
        - The query's specificity also matters: a pointed question wants
          a tighter answer; an open-ended one allows more room.
    Returns the selected level dict. `profile` scopes the learned
    adjustment to the ACTIVE agent's own runtime learning.
    """
    base = _word_count(text)

    # A query that asks for depth / breadth nudges the level up. The
    # hints come from BOTH the message text and the query param.
    query_lower = ((query or "") + " " + text).lower()
    depth_hint = any(k in query_lower for k in (
        "explain", "detail", "describe", "how does", "why", "compare",
        "summarize the", "list all", "walk through", "in depth", "tell me about",
    ))
    research_hint = any(k in query_lower for k in (
        "research", "deep dive", "comprehensive", "full analysis",
        "architecture", "design", "implement", "write code", "program",
        "refactor", "build a", "complete system", "document",
    ))
    tight_hint = any(k in query_lower for k in (
        "brief", "short", "quick", "one word", "tl;dr", "just", "simple",
    ))

    # ANY real question earns at least Low (a bare 'hi' is Extremely Low).
    # The base scales with the user's message length.
    if base >= 200:
        idx = 6  # Extremely High
    elif base >= 120:
        idx = 5  # Very High
    elif base >= 60:
        idx = 4  # High
    elif base >= 25:
        idx = 3  # Medium
    elif base >= 3:
        idx = 2  # Low — any real sentence/question
    else:
        idx = 1  # Very Low — a greeting / single word
    if research_hint:
        # Deep research / programming: room for the full output. A single
        # hint → at least Very High (512); a genuinely comprehensive ask
        # (multiple research markers, or a long message) → Extremely High
        # (1024).
        research_strength = sum(
            1 for k in ("research", "deep dive", "comprehensive",
                        "architecture", "design", "implement", "program",
                        "refactor", "complete system", "document",
                        "everything", "all the") if k in query_lower
        )
        if research_strength >= 2 or base >= 60:
            idx = max(5, min(6, idx + 2))
            if research_strength >= 3 or base >= 120:
                idx = 6  # Extremely High — full research room
        else:
            idx = max(5, min(6, idx + 1))
    elif depth_hint:
        # A depth-seeking query deserves real room: at least Medium.
        idx = max(3, min(5, idx + 1))
    if tight_hint:
        # A tight request counteracts a DEPTH bump but never lowers the
        # base level for a real question (a tl;dr still gets Low).
        idx = max(2, idx - 1) if base >= 3 else 1
    # LEARN-BY-DOING: apply the stored adjustment for this query type.
    learned = _learned_adjustment(text, profile)
    if learned:
        idx = min(6, max(0, idx + learned))
    return LEVELS[idx]


def _query_key(text: str) -> str:
    """A stable key for a query type (first 2 significant words)."""
    words = re.findall(r"\S+", (text or "").lower())
    sig = [w for w in words if len(w) > 2][:2]
    return " ".join(sig) or "general"


def _learned_adjustment(text: str, profile: str = "") -> int:
    """The stored level adjustment for a query type (-2..+1, 0 = none)."""
    try:
        data = _load_learning(profile)
        return int(data.get("adjustments", {}).get(_query_key(text), 0))
    except Exception:
        return 0


def learn_usage(text: str, actual_words: int, profile: str = "") -> int:
    """LEARN from an actual response's length (the learn-by-doing rule).

    The adjusted level is the level that MATCHES the actual response —
    a DIRECT jump, not a one-level crawl. The smallest cap that still
    covers the actual word count becomes the adjusted level:

        response_length = 3, prediction = 64 (Low)
        → adjusted = 16 (Extremely Low — 3 words fit under 16)

    Returns the new adjustment (the level delta stored for this query
    type, so gauge() reflects it on the next turn). The delta is
    computed against the BASE (unadjusted) level so repeated learning
    stays correct.
    """
    if actual_words <= 0:
        return _learned_adjustment(text, profile)
    base_idx = _base_level_index(text, profile)
    # The target: the SMALLEST cap that covers the actual word count.
    target_idx = 0
    for i, lvl in enumerate(LEVELS):
        if actual_words <= lvl["words"]:
            target_idx = i
            break
    else:
        target_idx = len(LEVELS) - 1  # even Extremely High is exceeded
    delta = target_idx - base_idx
    key = _query_key(text)
    data = _load_learning(profile)
    cur = int(data.get("adjustments", {}).get(key, 0))
    # The full jump lands exactly on the matching level.
    new = max(-6, min(6, delta))
    if new != cur:
        data.setdefault("adjustments", {})[key] = new
        data.setdefault("usages", {})[key] = \
            int(data.get("usages", {}).get(key, 0)) + 1
        _save_learning(data, profile)
    return new


def _base_level_index(text: str, profile: str = "") -> int:
    """The level index WITHOUT the learned adjustment (the raw gauge).

    Internal: the stored adjustment is a DELTA on the base level, so the
    delta math must start from the base, not the already-adjusted level.
    """
    try:
        data = _load_learning(profile)
        adj = int(data.get("adjustments", {}).get(_query_key(text), 0))
    except Exception:
        adj = 0
    # gauge() applies the adjustment; subtract it to recover the base.
    cur_idx = LEVELS.index(gauge(text, profile=profile))
    return max(0, min(len(LEVELS) - 1, cur_idx - adj))


def cap_for(text: str, query: str = "", profile: str = "") -> int:
    """The word cap for a response to this message (the selected level)."""
    return gauge(text, query, profile)["words"]


def prompt_line() -> str:
    """The Guidelines line telling Athena the response-length system.

    The limits are CEILINGS on the CONTENT only: answer UP TO the level's
    word count — never exceed it, and never pad to reach it. REASONING
    is uncapped (think as long as needed); only what you SAY must fit.
    A complete answer is the goal; shorter than the cap is always fine.
    """
    parts = ["Response length: pick ONE level from the user's message + "
             "query before answering. The cap applies to the CONTENT you "
             "output — your internal reasoning is uncapped. The cap is an "
             "UPPER LIMIT — stay UNDER it, never pad to fill it:"]
    for lvl in LEVELS:
        parts.append(f"    {lvl['label']}: up to {lvl['words']} words")
    parts.append("    Select exactly one — answer completely but do not "
                 "exceed it, and never pad to match it.")
    return "\n".join(parts)
