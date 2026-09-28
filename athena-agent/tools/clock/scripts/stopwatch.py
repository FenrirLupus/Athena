"""Built-in stopwatch tool — elapsed time (one script = one tool).

Part of the clock family inside tools/clock/scripts/. Registers ONLY
the `stopwatch` tool. A plain JSONL store — start, stop, lap, reset.
"""

import json
import time
from pathlib import Path


def _sw_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "stopwatch.jsonl"
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


def _start(args: dict, timeout: float = 10.0) -> str:
    path = _sw_path(args.get("profile", ""))
    state = {"status": "running", "started_at": time.time(),
             "elapsed": 0.0, "laps": []}
    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    return json.dumps({"ok": True, "stopwatch": state}, ensure_ascii=False)


def _check(args: dict, timeout: float = 10.0) -> str:
    path = _sw_path(args.get("profile", ""))
    state = _load(path)
    if not state or state.get("status") != "running":
        return json.dumps({"status": "stopped"}, ensure_ascii=False)
    elapsed = float(state.get("elapsed", 0)) + (time.time() - float(state.get("started_at", time.time())))
    return json.dumps({"status": "running", "elapsed_s": round(elapsed, 2),
                       "laps": state.get("laps", [])}, ensure_ascii=False)


def _lap(args: dict, timeout: float = 10.0) -> str:
    path = _sw_path(args.get("profile", ""))
    state = _load(path)
    if not state or state.get("status") != "running":
        return json.dumps({"error": "no running stopwatch"}, ensure_ascii=False)
    elapsed = float(state.get("elapsed", 0)) + (time.time() - float(state.get("started_at", time.time())))
    laps = state.get("laps", [])
    prev = laps[-1] if laps else 0.0
    laps.append(round(elapsed - prev, 2))
    state["laps"] = laps
    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    return json.dumps({"lap": laps[-1], "laps": laps}, ensure_ascii=False)


def _stop(args: dict, timeout: float = 10.0) -> str:
    path = _sw_path(args.get("profile", ""))
    state = _load(path)
    if not state or state.get("status") != "running":
        return json.dumps({"error": "no running stopwatch"}, ensure_ascii=False)
    elapsed = float(state.get("elapsed", 0)) + (time.time() - float(state.get("started_at", time.time())))
    state["status"] = "stopped"
    state["elapsed"] = round(elapsed, 2)
    state["started_at"] = 0
    path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    return json.dumps({"status": "stopped", "elapsed_s": state["elapsed"],
                       "laps": state.get("laps", [])}, ensure_ascii=False)


def _reset(args: dict, timeout: float = 10.0) -> str:
    path = _sw_path(args.get("profile", ""))
    if path.exists():
        path.unlink()
    return json.dumps({"ok": True, "status": "reset"}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn in (
        ("stopwatch", "Start the stopwatch.", _start),
        ("stopwatch_check", "Check elapsed time.", _check),
        ("stopwatch_lap", "Record a lap.", _lap),
        ("stopwatch_stop", "Stop and report elapsed.", _stop),
        ("stopwatch_reset", "Reset the stopwatch.", _reset),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object",
                        "properties": {"profile": {"type": "string"}},
                        "required": []},
            fn=fn,
        ))
    return ["stopwatch", "stopwatch_check", "stopwatch_lap",
            "stopwatch_stop", "stopwatch_reset"]
