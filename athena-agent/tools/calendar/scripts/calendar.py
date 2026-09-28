"""Built-in planner tool — list events/tasks (one script = one tool).

Part of the built-in generalized tools. The calendar is a PLANNER: it
acts on a daily, weekly, monthly, or yearly basis, holding both EVENTS
and TASKS — all keyed by the YYYY-MM-DD format. The store is a plain
JSONL file under the profile's runtime dir — no external service.

This script registers the `calendar` (list/plan) tool. The sibling
script `calendar_add.py` registers the add tool. One folder, multiple
tools.
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path


def _cal_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "calendar.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(path: Path) -> list[dict]:
    events = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
    return events


def _parse_date(value: str) -> date:
    """YYYY-MM-DD (strict — the Operator's format) or a fuzzy date."""
    value = (value or "").strip()
    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass
    low = value.lower()
    if low == "today":
        return date.today()
    if low == "tomorrow":
        return date.today() + timedelta(days=1)
    if low == "yesterday":
        return date.today() - timedelta(days=1)
    if low.startswith("monday") or low.startswith("mon"):
        return date.today() + timedelta(days=(0 - date.today().weekday()) % 7)
    if low.startswith("tuesday") or low.startswith("tue"):
        return date.today() + timedelta(days=(1 - date.today().weekday()) % 7)
    if low.startswith("wednesday") or low.startswith("wed"):
        return date.today() + timedelta(days=(2 - date.today().weekday()) % 7)
    if low.startswith("thursday") or low.startswith("thu"):
        return date.today() + timedelta(days=(3 - date.today().weekday()) % 7)
    if low.startswith("friday") or low.startswith("fri"):
        return date.today() + timedelta(days=(4 - date.today().weekday()) % 7)
    if low.startswith("saturday") or low.startswith("sat"):
        return date.today() + timedelta(days=(5 - date.today().weekday()) % 7)
    if low.startswith("sunday") or low.startswith("sun"):
        return date.today() + timedelta(days=(6 - date.today().weekday()) % 7)
    # Last resort: today (unknown format).
    return date.today()


def _range_for(period: str, anchor: date) -> tuple[date, date]:
    """The (start, end) inclusive range for a planning period."""
    period = (period or "day").lower()
    if period in ("day", "daily", "today", "date"):
        return anchor, anchor
    if period in ("week", "weekly"):
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    if period in ("month", "monthly"):
        start = anchor.replace(day=1)
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, nxt - timedelta(days=1)
    if period in ("year", "yearly"):
        start = anchor.replace(month=1, day=1)
        return start, start.replace(month=12, day=31)
    return anchor, anchor


def _list_events(args: dict, timeout: float = 10.0) -> str:
    path = _cal_path(args.get("profile", ""))
    anchor = _parse_date(args.get("date", ""))
    period = args.get("period", "day")
    start, end = _range_for(period, anchor)
    events = _load(path)
    in_range = [
        e for e in events
        if start <= _parse_date(e.get("date", "")) <= end
    ]
    in_range.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
    return json.dumps({
        "period": period,
        "range": [start.isoformat(), end.isoformat()],
        "events": in_range,
    }, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="calendar",
        description="Plan: list events/tasks for a day, week, month, or year "
                    "(YYYY-MM-DD). period: day|week|month|year.",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string",
                         "description": "Anchor date YYYY-MM-DD (or today/tomorrow/weekday)"},
                "period": {"type": "string",
                           "enum": ["day", "week", "month", "year"],
                           "description": "Planning period"},
                "profile": {"type": "string", "description": "Profile name"},
            },
            "required": [],
        },
        fn=_list_events,
    ))
    return ["calendar"]
