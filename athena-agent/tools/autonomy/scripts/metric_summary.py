"""Metric summary tool — the EVENTS half of diagnosis (the Operator's
08-14 spec: timeline + metrics together = root cause, not a guess).

Pulls the recent L3+ entries (code + reason) from the metric logs —
the root aggregate + the current profile's log. What broke, when, why.
"""

import json
from pathlib import Path


def _metric_files():
    """The root aggregate + profile logs (newest first)."""
    from core.config import ATHENA_ROOT
    files = []
    root = ATHENA_ROOT / "logs"
    if root.is_dir():
        files.extend(sorted(root.glob("*_metric.log"),
                            key=lambda p: p.stat().st_mtime, reverse=True))
    prof = ATHENA_ROOT / "profiles" / ".default" / "logs"
    if prof.is_dir():
        files.extend(sorted(prof.glob("*_metric.log"),
                            key=lambda p: p.stat().st_mtime, reverse=True))
    return files


def metric_summary(*, level: int = 3, limit: int = 15,
                   profile: str = "") -> str:
    """The recent L3+ metric entries (code + reason) — what broke.

    level: minimum level (3 = error+; 4 = worse; 5 = critical).
    limit: how many entries to show (default 15).
    """
    try:
        files = _metric_files()
        if not files:
            return "no metric logs found"
        entries = []
        for f in files:
            try:
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if '"level"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if int(d.get("level", 0)) >= level:
                        entries.append(d)
            except Exception:
                continue
        entries.sort(key=lambda e: str(e.get("time", "")), reverse=True)
        if not entries:
            return (f"no metric entries at level {level}+ in the recent "
                    f"logs — the house is clean")
        lines = [f"Recent L{level}+ metric entries ({len(entries)} total, "
                 f"showing {min(limit, len(entries))}):"]
        for e in entries[:limit]:
            t = str(e.get("time", ""))[11:19]
            lvl = e.get("level", "?")
            src = e.get("source", "?")
            act = e.get("action", "")
            code = e.get("code", "")
            reason = e.get("reason", "")
            result = str(e.get("result", ""))[:110]
            tag = f" [{code}]" if code else ""
            why = f" ({reason})" if reason else ""
            lines.append(f"  {t} L{lvl} {src}{tag}{why} — {result}")
        return "\n".join(lines)
    except Exception as exc:
        return f"error: metric_summary failed: {exc}"


def _run_metric_summary(args: dict, timeout: float = 30.0) -> str:
    return metric_summary(
        level=int(args.get("level", 3) or 3),
        limit=int(args.get("limit", 15) or 15),
    )


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="metric_summary",
        description=("Recent L3+ metric entries (code + reason) from the "
                     "metric logs — what broke, when, why. The EVENTS "
                     "half of diagnosis; pair with timeline_query for "
                     "root cause."),
        parameters={
            "type": "object",
            "properties": {
                "level": {"type": "integer",
                          "description": "min level (3=error+, default 3)"},
                "limit": {"type": "integer",
                          "description": "entries to show (default 15)"},
            },
        },
        fn=_run_metric_summary,
    ))
    return ["metric_summary"]
