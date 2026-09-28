"""Nurse watcher — the nurse's ONLY job: watch the metric logs.

The nurse is always active, but she does nothing when the system is fine.
She watches the per-profile .log files and reacts ONLY to changed files.
Attention threshold (the Operator's spec):

    Level 1 Good      — nothing to do (ok)
    Level 2 Notice    — nothing to do (ok)
    Level 3 Warning   — nurse investigates  (fix/diagnose)
    Level 4 Error     — nurse investigates  (fix/diagnose)
    Level 5 Critical  — nurse investigates  (fix/diagnose)

Cost doctrine: the nurse makes ZERO provider calls when the logs show only
1s and 2s. She only spends (thinks/calls) when a changed log contains a
3/4/5 — that's what makes the always-on nurse affordable.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from metrics.logger import LOGS_DIR, LEVELS, parse_entries

ATTENTION_MIN = 3  # levels >= 3 get the nurse's attention

# last-seen file state: path -> (mtime, size) — the change detector.
_seen: dict[str, tuple[float, int]] = {}


def _scan_paths() -> list[Path]:
    """All metric log files across every profile subfolder."""
    if not LOGS_DIR.exists():
        return []
    return sorted(p for p in LOGS_DIR.rglob("*_metric.log") if p.is_file())


def changed_files() -> list[Path]:
    """Files whose mtime/size changed since the last scan (new activity)."""
    changed = []
    for path in _scan_paths():
        try:
            st = path.stat()
            key = str(path)
            state = (st.st_mtime, st.st_size)
            if key not in _seen or _seen[key] != state:
                _seen[key] = state
                changed.append(path)
        except OSError:
            continue
    return changed


def check_logs() -> dict:
    """The nurse's watch pass. Returns what needs attention.

    Checks only CHANGED files (the free pass). For each changed log, parses
    entries; if any entry has level >= ATTENTION_MIN, it's flagged.
    Returns: {"attention": [...], "ok": True/False, "max_level": n}
    """
    from metrics.logger import colorize_level

    result = {"attention": [], "ok": True, "max_level": 1}
    for path in changed_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        entries = parse_entries(text)
        worst = max((e["level"] for e in entries), default=1)
        if worst >= ATTENTION_MIN:
            result["ok"] = False
            result["attention"].append({
                "file": str(path),
                "max_level": worst,
                "level_name": LEVELS.get(worst, "?"),
                "level_colored": colorize_level(worst, f"L{worst}"),
                "entries": [e for e in entries if e["level"] >= ATTENTION_MIN],
            })
        if worst > result["max_level"]:
            result["max_level"] = worst
    return result


def reset_watch() -> None:
    """Clear the seen-state (fresh start)."""
    _seen.clear()
