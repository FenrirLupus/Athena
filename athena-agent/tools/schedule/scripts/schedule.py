"""Built-in schedule tool — list reminders (one script = one tool).

The sibling set inside tools/schedule/scripts/: schedule.py registers
the `schedule` (list) tool; schedule_set.py registers the set tool.
One folder, multiple tools (the Operator's 08-12 spec).
"""

import json
from pathlib import Path


def _sched_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "schedule.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _list_schedule(args: dict, timeout: float = 10.0) -> str:
    path = _sched_path(args.get("profile", ""))
    items = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
    return json.dumps({"schedule": items}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="schedule",
        description="List scheduled reminders.",
        parameters={
            "type": "object",
            "properties": {
                "profile": {"type": "string", "description": "Profile name"},
            },
            "required": [],
        },
        fn=_list_schedule,
    ))
    return ["schedule"]
