"""Built-in schedule_set tool — set a reminder (one script = one tool).

The sibling of schedule.py inside tools/schedule/scripts/. Registers
ONLY the `schedule_set` tool. One folder, multiple tools.
"""

import json
from pathlib import Path


def _sched_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "schedule.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _set_schedule(args: dict, timeout: float = 10.0) -> str:
    path = _sched_path(args.get("profile", ""))
    n = 0
    if path.exists():
        n = sum(1 for _ in path.read_text(encoding="utf-8", errors="replace").splitlines() if _.strip())
    item = {
        "id": str(n + 1),
        "time": args.get("time", ""),
        "task": args.get("task", ""),
        "repeat": args.get("repeat", ""),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return json.dumps({"ok": True, "item": item}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="schedule_set",
        description="Set a scheduled reminder.",
        parameters={
            "type": "object",
            "properties": {
                "time": {"type": "string", "description": "When (e.g. 09:00 or 2026-08-13T09:00)"},
                "task": {"type": "string", "description": "The reminder"},
                "repeat": {"type": "string", "description": "Repeat (daily|weekly|none)"},
                "profile": {"type": "string", "description": "Profile name"},
            },
            "required": ["time", "task"],
        },
        fn=_set_schedule,
    ))
    return ["schedule_set"]
