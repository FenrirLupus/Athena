"""Built-in timer tool — a countdown timer (one script = one tool).

Part of the clock family inside tools/clock/scripts/. Registers ONLY
the `timer` tool. A plain JSONL store under the profile's runtime dir —
set a countdown, check remaining, clear.
"""

import json
import time
from pathlib import Path


def _timer_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "timer.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(path: Path) -> dict:
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    return json.loads(line)
                except Exception:
                    continue
    return {}


def _set(args: dict, timeout: float = 10.0) -> str:
    path = _timer_path(args.get("profile", ""))
    seconds = max(int(args.get("seconds", 0) or 0), 0)
    label = args.get("label", "timer")
    state = {
        "label": label,
        "seconds": seconds,
        "started_at": time.time(),
        "ends_at": time.time() + seconds,
        "status": "running" if seconds else "cleared",
    }
    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    return json.dumps({"ok": True, "timer": state}, ensure_ascii=False)


def _check(args: dict, timeout: float = 10.0) -> str:
    path = _timer_path(args.get("profile", ""))
    state = _load(path)
    if not state or state.get("status") != "running":
        return json.dumps({"status": "no running timer"}, ensure_ascii=False)
    remaining = max(float(state.get("ends_at", 0)) - time.time(), 0)
    if remaining <= 0:
        state["status"] = "done"
        path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
        return json.dumps({"status": "done",
                           "label": state.get("label", "")}, ensure_ascii=False)
    return json.dumps({"status": "running", "remaining_s": round(remaining, 1),
                       "label": state.get("label", "")}, ensure_ascii=False)


def _clear(args: dict, timeout: float = 10.0) -> str:
    path = _timer_path(args.get("profile", ""))
    if path.exists():
        path.unlink()
    return json.dumps({"ok": True, "status": "cleared"}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props in (
        ("timer", "Set a countdown timer (seconds).", _set,
         {"seconds": {"type": "integer", "description": "Countdown seconds"},
          "label": {"type": "string", "description": "Optional label"}}),
        ("timer_check", "Check the timer's remaining time.", _check,
         {"profile": {"type": "string"}}),
        ("timer_clear", "Clear the timer.", _clear,
         {"profile": {"type": "string"}}),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object",
                        "properties": props,
                        "required": ["seconds"] if name == "timer" else []},
            fn=fn,
        ))
    return ["timer", "timer_check", "timer_clear"]
