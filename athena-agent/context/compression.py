"""Compression — keep the conversation lean (CONTEXT.md).

When the conversation's estimated tokens exceed `upper_threshold` of the
model's context window, the OLDER history is compressed down to a rolling
summary until usage reaches `lower_threshold`. The summary lives in the
session file (sessions.summary column); the recent window stays raw.

    compress(session_id, history) ->
        {compressed: bool, summary, kept, tokens_before, tokens_after}
"""
from __future__ import annotations

import json
from typing import Optional

from core import db as db_layer
from core.config import load_config

# The usage-baseline marker (the Operator's meter model): a JSON file per
# profile recording the vault's usage_total at the last compression.
# The /health meter reads (current - baseline) so the meter resets after
# each compression instead of accumulating all-time forever.
USAGE_BASELINE_NAME = "usage-baseline.json"


def _baseline_path(profile: str = "") -> "db_layer.Path":
    # The meter's zero-point lives in the profile's operations/ (the
    # Operator's home layout: machinery in operations/, not conversation
    # or vault data) — moved from sessions/vault/.
    from core.config import ATHENA_ROOT
    prof = profile or "default"
    if prof == "default":
        prof = ".default"
    return ATHENA_ROOT / "profiles" / prof / "operations" / USAGE_BASELINE_NAME


def vault_usage_total(profile: str = "") -> int:
    """The vault's accumulated usage_total (all-time token spend)."""
    try:
        conn = db_layer.connect_vault(profile)
        row = conn.execute(
            "SELECT COALESCE(SUM(usage_total),0) FROM entries "
            "WHERE deleted=0 AND usage_total IS NOT NULL").fetchone()
        conn.close()
        return int(row[0] or 0)
    except Exception:
        return 0


def mark_usage_baseline(profile: str = "") -> None:
    """Record the current vault usage as the meter's zero point."""
    try:
        _baseline_path(profile).write_text(
            json.dumps({"baseline": vault_usage_total(profile)}),
            encoding="utf-8")
    except Exception:
        pass


def usage_since_baseline(profile: str = "") -> int:
    """Tokens used since the last compression (the meter's value)."""
    try:
        p = _baseline_path(profile)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            base = int(data.get("baseline", 0))
        else:
            base = 0
        return max(0, vault_usage_total(profile) - base)
    except Exception:
        return vault_usage_total(profile)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (the standard heuristic)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages(messages: list) -> int:
    """Total estimated tokens of a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += estimate_tokens(str(content))
        total += 4  # role + message overhead
    return total




def build_summary_prompt(history: list) -> str:
    """Prompt the model to compress older history into a rolling summary."""
    lines = []
    for msg in history:
        role = msg.get("role", "?")
        content = str(msg.get("content", ""))
        lines.append(f"{role}: {content[:200]}")
    transcript = "\n".join(lines)
    return (
        "Compress the following conversation history into a concise rolling "
        "summary that preserves all important facts, decisions, and context. "
        "Keep it under 300 words.\n\n"
        f"{transcript}"
    )


def summary_dir(profile: str = "") -> Path:
    """The summary/ dir for a profile (the Operator's 08-11 spec): the
    compressed-context archive lives at sessions/summary/ — one .md per
    compression, per session. All lowercase.

    Resolves ATHENA_ROOT at CALL time (a fresh import) so tests that
    patch core.config.ATHENA_ROOT to a tempdir get isolated correctly —
    db_layer's module-level reference is bound at import and would
    ignore the patch.
    """
    from core.config import ATHENA_ROOT
    db_cfg = load_config(profile=profile)
    rel = str(db_cfg.get("db", {}).get("dir", "sessions"))
    root = ATHENA_ROOT / "profiles" / profile if profile and profile != "default" \
        else ATHENA_ROOT
    return root / rel / "summary"


def write_recap(session_id: str, summary: str, *, profile: str = "") -> str:
    """Write the compression summary markdown file.

    Filename (the Operator's spec): {Date}_{Time}_{UUID}_Summary.md
        Date = YYYY-MM-DD (when the compression fired)
        Time = HH-MM-SS (when the compression fired)
        UUID = the SESSION's id (each session compresses accordingly)
    Location: the profile's sessions/Summary/ dir.

    The file is the human-readable record of a compression: what the
    session was, when it compressed, and the rolling summary. Section 3
    (History) of the prompt injects this summary FIRST, then the recent
    raw window.
    """
    from datetime import datetime
    from pathlib import Path
    from core.config import ATHENA_ROOT

    now = datetime.now()
    recap_dir = summary_dir(profile)
    recap_dir.mkdir(parents=True, exist_ok=True)
    name = (f"{now.strftime('%Y-%m-%d')}_{now.strftime('%H-%M-%S')}_"
            f"{session_id}_Summary.md")
    path = recap_dir / name
    path.write_text(
        f"---\n"
        f"session_id: \"{session_id}\"\n"
        f"profile: \"{profile or 'default'}\"\n"
        f"compressed_at: \"{now.strftime('%Y-%m-%d %H:%M:%S')}\"\n"
        f"---\n"
        f"# Session Summary\n"
        f"**Session:** `{session_id}`  \n"
        f"**Compressed:** {now.strftime('%Y-%m-%d %H:%M:%S')}  \n"
        f"**Profile:** {profile or 'default'}  \n"
        f"---\n"
        f"## Summary\n\n{summary}\n"
        f"---\n",
        encoding="utf-8",
    )
    return str(path)


def latest_summary(session_id: str, profile: str = "") -> str:
    """The most recent compression summary for a session (the file in
    sessions/Summary/ whose name carries the session UUID), else the
    session column's value. Section 3 (History) uses this FIRST."""
    try:
        d = summary_dir(profile)
        if d.exists():
            hits = sorted(d.glob(f"*_{session_id}_Summary.md"))
            if hits:
                text = hits[-1].read_text(encoding="utf-8")
                # Return the SUMMARY body (after the ## Summary heading).
                if "## Summary" in text:
                    return text.split("## Summary", 1)[1].strip()
                return text.strip()
    except Exception:
        pass
    try:
        return db_layer.get_session_summary(session_id, profile=profile) or ""
    except Exception:
        return ""


def compress_history(session_id: str, history: list, *,
                     context_window: int, upper_threshold: float,
                     lower_threshold: float,
                     recent_window: int = 10,
                     providers=None,
                     system_prompt: str = "",
                     profile: str = "") -> dict:
    """Compress old history down to the lower threshold.

    Strategy (lean window + summary, CONTEXT.md):
      1. Keep the recent_window messages RAW (the immediate context).
      2. Send everything older to the model as one summarization turn.
      3. Store the summary in the session file (sessions.summary).
      4. Write the recap markdown {date}_{time}_{UUID}_summary.md
         (the human-readable record — the Operator's spec).
      5. The prompt stack then carries: summary + recent raw window.

    If summarization fails (no provider, error), it degrades gracefully:
    it keeps the recent window and drops the oldest (a truncation fallback)
    so the turn still fits.
    """
    result = {
        "compressed": False,
        "summary": "",
        "kept_raw": recent_window,
        "tokens_before": estimate_messages(history),
        "tokens_after": estimate_messages(history),
    }

    if len(history) <= recent_window:
        return result  # nothing old enough to compress

    old = history[:-recent_window]
    recent = history[-recent_window:]
    summary = ""

    if providers is not None:
        try:
            from core.message_loop import MessageLoop
            loop = MessageLoop(
                providers=providers,
                system_prompt=system_prompt or "You are a summarizer.",
                max_iterations=3,
            )
            turn = loop.run_turn(build_summary_prompt(old))
            summary = turn.reply.strip()
        except Exception as exc:
            from core.logging import log_event
            log_event(3, f"summarization failed (falling back to truncation): {exc}",
                      source="context", action="compress")
            summary = ""

    if not summary:
        # Degrade: keep the recent window + a tiny pointer of what was dropped.
        summary = f"[compressed: {len(old)} earlier messages omitted]"

    # Persist the summary in the session file (the broad layer's bridge).
    db_layer.set_session_summary(session_id, summary, profile=profile)

    # Write the recap markdown record ({date}_{time}_{UUID}_summary.md).
    try:
        write_recap(session_id, summary, profile=profile)
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"recap write failed (bonus record skipped): {exc}",
                  source="context", action="write_recap")

    result.update({
        "compressed": True,
        "summary": summary,
        "tokens_after": estimate_messages(recent) + estimate_tokens(summary),
    })
    # THE USAGE BASELINE (the Operator's meter model): record the vault's
    # usage_total at this compression. The /health meter reads
    # (current - baseline) / budget — so after compression the meter
    # resets toward 0 and climbs to 80% again, then compresses.
    try:
        mark_usage_baseline(profile)
    except Exception:
        pass
    return result


def context_status(messages: list, *, context_window: int,
                   upper_threshold: float) -> dict:
    """How full the context is, for logging/gating decisions."""
    used = estimate_messages(messages)
    window = max(1, context_window)
    return {
        "used_tokens": used,
        "window_tokens": window,
        "utilization": round(used / window, 3),
        "over_upper": used > (window * upper_threshold),
    }
