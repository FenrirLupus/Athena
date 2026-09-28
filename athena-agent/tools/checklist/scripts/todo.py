"""Built-in todo tool — a flat task list (one script = one tool).

An EXPANSION of the checklist family (tools/checklist/scripts/).
Registers ONLY the `todo` tool. A flat list of tasks — add, list,
toggle done, clear — for quick everyday tracking (the checklist is
the SEQUENCED planner; todo is the flat list).
"""

import json
from pathlib import Path


def _todo_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "todo.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(path: Path) -> list[dict]:
    items = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
    return items


def _save(path: Path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _add(args: dict, timeout: float = 10.0) -> str:
    path = _todo_path(args.get("profile", ""))
    items = _load(path)
    item = {"id": str(len(items) + 1), "text": str(args.get("task", "")),
            "done": False}
    items.append(item)
    _save(path, items)
    return json.dumps({"ok": True, "item": item}, ensure_ascii=False)


def _list(args: dict, timeout: float = 10.0) -> str:
    path = _todo_path(args.get("profile", ""))
    items = _load(path)
    return json.dumps({"items": items}, ensure_ascii=False)


def _toggle(args: dict, timeout: float = 10.0) -> str:
    path = _todo_path(args.get("profile", ""))
    items = _load(path)
    idx = int(args.get("index", 0) or 0) - 1
    if not (0 <= idx < len(items)):
        return f"error: no todo item at index {args.get('index')}"
    items[idx]["done"] = not items[idx]["done"]
    _save(path, items)
    return json.dumps({"ok": True, "item": items[idx]}, ensure_ascii=False)


def _clear(args: dict, timeout: float = 10.0) -> str:
    path = _todo_path(args.get("profile", ""))
    items = _load(path)
    # Remove completed items.
    remaining = [i for i in items if not i.get("done")]
    _save(path, remaining)
    return json.dumps({"ok": True, "removed": len(items) - len(remaining)},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props, req in (
        ("todo_add", "Add a task to the todo list.", _add,
         {"task": {"type": "string"}, "profile": {"type": "string"}}, ["task"]),
        ("todo_list", "List the todo items.", _list,
         {"profile": {"type": "string"}}, []),
        ("todo_toggle", "Toggle a task's done state (1-based index).", _toggle,
         {"index": {"type": "integer"}, "profile": {"type": "string"}}, ["index"]),
        ("todo_clear", "Remove completed tasks.", _clear,
         {"profile": {"type": "string"}}, []),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    return ["todo_add", "todo_list", "todo_toggle", "todo_clear"]
