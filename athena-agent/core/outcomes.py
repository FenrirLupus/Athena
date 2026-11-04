"""Outcomes — one vocabulary for how a process ended, shared by metrics and diagnostics.

Three levels, and nothing else:

  warning   finished, but took longer than expected or carried a minor issue
  caution   did not finish, and has an issue that needs attention
  critical  did not finish, and the issue is major

A record is asked two questions in order — did it finish, and how badly did it go — because that is the
distinction a person acts on. Both a slow success and a hard failure are "not good", but only one of them
means the work exists. Collapsing them into a single error flag loses exactly that.
"""
from __future__ import annotations

from typing import Iterable

LEVELS = ("warning", "caution", "critical")
RANK = {"warning": 1, "caution": 2, "critical": 3}
DEFAULT_SLOW_S = 60.0


def classify(row: dict, slow_s: float = DEFAULT_SLOW_S) -> str:
    """Return the level for one metrics row."""
    if not isinstance(row, dict):
        return "warning"
    if row.get("timed_out") or row.get("abandoned") or row.get("tool_timeout"):
        return "caution"
    failed = bool(row.get("error")) or row.get("ok") is False or row.get("failed")
    if failed:
        return "critical"
    if row.get("slow") or float(row.get("seconds") or 0.0) > slow_s:
        return "warning"
    return "warning"


def summarise(rows: Iterable[dict], slow_s: float = DEFAULT_SLOW_S) -> dict:
    """Fold rows into counts per level, with the share each one holds."""
    counts = {level: 0 for level in LEVELS}
    total = 0
    for row in rows:
        level = classify(row, slow_s)
        if level in counts:
            counts[level] += 1
            total += 1
    share = {level: (round(100.0 * n / total, 1) if total else 0.0) for level, n in counts.items()}
    worst = max(counts, key=lambda level: (RANK[level] * 1 if counts[level] else 0)) if total else ""
    return {"total": total, "counts": counts, "share_pct": share, "worst_present": worst}


def diagnostics(rows: list[dict], slow_s: float = DEFAULT_SLOW_S, tail: int = 20) -> dict:
    """What the log says now: counts per level, and the rows that need a person first."""
    summary = summarise(rows, slow_s)
    ranked = sorted(rows, key=lambda r: (-RANK.get(classify(r, slow_s), 0)))
    notable = [
        {
            "level": classify(r, slow_s),
            "channel": r.get("channel") or "",
            "seconds": r.get("seconds"),
            "error": (str(r.get("error"))[:200] if r.get("error") else ""),
            "at": r.get("at") or r.get("started_at") or "",
        }
        for r in ranked[:tail]
        if classify(r, slow_s) != "warning" or (r.get("seconds") or 0) > slow_s
    ]
    summary["notable"] = notable
    summary["slow_threshold_s"] = slow_s
    return summary
