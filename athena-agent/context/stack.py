"""Prompt stack — assemble the six blocks, sized as shares of the window.

0.0.1 assembles four of them: system instructions, assistant identity, history, user identity.
Summary and memory are computed as budgets but rendered empty — the slots exist so the sizing is
real, and 0.0.2 fills them without changing the arithmetic.

Every limit is a percentage of model.window, rounded to nearest (documentation/architecture/07).
"""
from __future__ import annotations

import math
from pathlib import Path

from core import profile as profile_mod
from core.context_window import effective_window

CHARS_PER_TOKEN = 4.0          # the raw estimate; the *calibrated* ratio comes from what providers bill
# Measured on real turns, the truth is 1.5-2.0x this (see context/calibration.py). Everything below takes a
# factor so the budget is computed in the same units the provider charges in.


def est_tokens(text: str, factor: float = 1.0) -> int:
    """    Raw estimate times the profile's calibration factor (1.0 = uncalibrated)."""
    return int(math.ceil(len(text or "") / CHARS_PER_TOKEN * max(1.0, float(factor or 1.0))))


def message_tokens(message: dict, factor: float = 1.0) -> int:
    """Cost of one message: its text plus the per-message framing, both scaled by the factor.

    The framing (role markers, separators) is real prompt the provider bills. Scaling only the text left a
    systematic gap — 14 messages of framing is ~112 tokens that the estimate never accounted for, which showed
    up as the estimate sitting 12 % under the bill even after the factor converged.
    """
    return int((est_tokens((message or {}).get("content", "")) + 8) * max(1.0, float(factor or 1.0)))


def chars_per_token(factor: float = 1.0) -> float:
    """Inverse of the factor, for truncating a block to a token budget."""
    return CHARS_PER_TOKEN / max(1.0, float(factor or 1.0))


def allocate(window: int, cfg: dict) -> dict:
    """Token budget per block, from the configured percentages, rounded to nearest."""
    b = cfg.get("budget") or {}
    m = cfg.get("model") or {}
    pcts = {
        "system": float(b.get("system_pct", 5.0)),
        "assistant": float(b.get("assistant_pct", 7.5)),
        "user": float(b.get("user_pct", 7.5)),
        # the digest block is sized by the compression target: max is the cap it may grow to, min the floor
        # SUMMARY.md must reach. `budget.summary_pct` remains as a fallback for an older config.
        "summary": float((cfg.get("compression") or {}).get("target_max_pct")
                         or b.get("summary_pct", 20.0)),
        "history": float(b.get("history_pct", 30.0)),
        "memory": float(b.get("memory_pct", 10.0)),
    }
    # output_pct is handled separately below (it caps the reply, not a prompt block)
    out = {name: int(window * pct / 100 + 0.5) for name, pct in pcts.items()}
    out["pcts"] = pcts
    # the reply cap is the smaller of the configured hard limit and the output share
    out["output"] = min(int(m.get("max_output_tokens") or 4096),
                        int(window * float(b.get("output_pct", 10.0)) / 100 + 0.5))
    out["pcts"] = pcts
    out["window"] = window
    out["allocated"] = sum(out[k] for k in ("system", "assistant", "user", "summary",
                                            "history", "memory", "output"))
    out["free"] = window - out["allocated"]
    return out


def _fit(text: str, budget_tokens: int, what: str, factor: float = 1.0) -> str:
    """Truncate a block to its share, saying so rather than losing the tail silently."""
    if not text:
        return ""
    if est_tokens(text, factor) <= budget_tokens:
        return text
    keep = max(0, int(budget_tokens * chars_per_token(factor)))
    return text[:keep] + f"\n[{what} truncated to its budget]"


def build(instructions: str, channel_root: Path, history: list[dict], user_text: str,
          cfg: dict, summary: str = "", factor: float = 1.0) -> tuple[list[dict], dict]:
    """Return (messages, report) for one model call.

    The system block is assembled ONCE per turn and never mutated mid-turn — prompt caching is
    sacred (documentation/architecture/00-principles.md §6).

    `history` already ends with the user message: the caller stores the turn before building, and
    appending it again here would put every tool result before the last user message.
    """
    win = effective_window(cfg)
    alloc = allocate(win, cfg)

    # labelled parts, so the report can say WHICH block costs what — a total of ~890 tokens tells you to
    # optimise something; a breakdown tells you what, and every later cut is then measurable
    blocks: list[tuple[str, str]] = []
    parts = [text for _, text in blocks]        # kept as a view for the join below
    if instructions.strip():
        blocks.append(("instructions", _fit(instructions.strip(), alloc["system"], "instructions", factor)))

    if summary.strip():
        blocks.append(("summary", "# What happened before this window\n\n"
                       + _fit(summary.strip(), alloc["summary"], "summary", factor)))

    asst_body = profile_mod.read_identity(channel_root / "assistant" / "ASSISTANT.md")
    if asst_body:
        blocks.append(("assistant", "# Who I am\n\n" + _fit(asst_body, alloc["assistant"], "identity", factor)))
    asst_mood = profile_mod.read_emotion_line(channel_root / "assistant" / "EMOTION.md")
    if asst_mood:
        blocks.append(("assistant_emotion", f"# How I am right now\n\n{asst_mood}"))

    mem_rows = profile_mod.read_memory_rows(channel_root / "memories" / "MEMORY.md")
    if mem_rows:
        block = "\n".join(mem_rows)
        blocks.append(("memory", "# Memory — index of what I have learned\n\n"
                       "(these are pointers; read the body before relying on it)\n\n"
                       + _fit(block, alloc["memory"], "memory index", factor)))

    user_body = profile_mod.read_identity(channel_root / "user" / "USER.md")
    if user_body:
        blocks.append(("user", "# Who I serve\n\n" + _fit(user_body, alloc["user"], "user identity", factor)))
    user_mood = profile_mod.read_emotion_line(channel_root / "user" / "EMOTION.md")
    if user_mood:
        blocks.append(("user_emotion", f"# How they are right now\n\n{user_mood}"))

    system_text = "\n\n".join(text for _, text in blocks if text)
    block_tokens = {name: est_tokens(text, factor) for name, text in blocks if text}

    # history, newest-first until the share is spent, then restored to chronological order
    kept: list[dict] = []
    used = 0
    for msg in reversed(history):
        cost = message_tokens(msg, factor)
        if used + cost > alloc["history"]:
            break
        kept.append(msg)
        used += cost
    kept.reverse()

    # The window is cut newest-first, so it can land *between* an assistant message carrying tool_calls and the
    # tool results answering it. A provider rejects that sequence ("messages with role 'tool' must be a response to
    # a preceding message with 'tool_calls'"), and in practice it made any long tool-using turn fail at the fourth
    # round. Repair the boundary: drop leading tool rows whose call was left outside the window.
    while kept and kept[0].get("role") == "tool":
        kept.pop(0)

    messages = [{"role": "system", "content": system_text}] + kept

    report = {
        "window": win,
        "allocated": alloc,
        "system_tokens": est_tokens(system_text, factor),
        "system_tokens_raw": est_tokens(system_text),
        "history_tokens": used,
        "history_turns": len(kept),
        "dropped_turns": max(0, len(history) - len(kept)),
        "memory_rows": len(mem_rows),
        "prompt_tokens_est": est_tokens(system_text, factor) + used,
        # raw (uncalibrated) total: what the factor is re-derived from after the provider reports its count
        "est_raw": est_tokens(system_text) + sum(message_tokens(m) for m in kept),
        "calibration": round(max(1.0, float(factor or 1.0)), 4),
        # which block costs what, in tokens: the whole point of A1 in the optimization plan
        "blocks": block_tokens,
        "blocks_share_pct": {},
    }
    total = report["prompt_tokens_est"] or 1
    report["blocks_share_pct"] = {name: round(100.0 * tokens / total, 1)
                                 for name, tokens in block_tokens.items()}
    return messages, report
