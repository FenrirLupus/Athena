"""Built-in planner tool — add an event/task (one script = one tool).

The sibling of calendar.py inside tools/calendar/scripts/. This script
registers ONLY the `calendar_add` tool. One folder, multiple tools.

The planner stores both EVENTS (a scheduled happening) and TASKS (a
to-do item) keyed by YYYY-MM-DD — following the same format across the
daily/weekly/monthly/yearly planner.
"""

import json
from datetime import datetime
from pathlib import Path


def _cal_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "calendar.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _add_event(args: dict, timeout: float = 10.0) -> str:
    path = _cal_path(args.get("profile", ""))
    n = 0
    if path.exists():
        n = sum(1 for _ in path.read_text(encoding="utf-8", errors="replace")
                .splitlines() if _.strip())
    d = args.get("date", "")
    # YYYY-MM-DD (the Operator's planner format) — accept fuzzy too.
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        from datetime import date, timedelta
        low = (d or "").strip().lower()
        if low == "today":
            d = date.today().isoformat()
        elif low == "tomorrow":
            d = (date.today() + timedelta(days=1)).isoformat()
        elif low == "yesterday":
            d = (date.today() - timedelta(days=1)).isoformat()
        else:
            return "error: date must be YYYY-MM-DD (or today/tomorrow/yesterday)"
    event = {
        "id": str(n + 1),
        "date": d,
        "kind": args.get("kind", "event"),
        "time": args.get("time", ""),
        "title": args.get("title", ""),
        "detail": args.get("detail", ""),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return json.dumps({"ok": True, "event": event}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="calendar_add",
        description="Add an event or task to the planner (YYYY-MM-DD). "
                    "kind: event|task.",
        parameters={
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                "kind": {"type": "string", "enum": ["event", "task"],
                         "description": "Event or task"},
                "time": {"type": "string", "description": "Optional time (HH:MM)"},
                "title": {"type": "string", "description": "The event/task title"},
                "detail": {"type": "string", "description": "Optional detail"},
                "profile": {"type": "string", "description": "Profile name"},
            },
            "required": ["date", "title"],
        },
        fn=_add_event,
    ))
    return ["calendar_add"]
