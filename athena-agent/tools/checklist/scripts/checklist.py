"""Built-in checklist tool — operator + agent planning (one tool).

A checklist is a SEQUENCED task list: the agent (and the operator)
use it to plan/diagnose/implement SAFELY, taking items sequentially
one at a time. A plain JSONL store under the profile's runtime dir.
"""

import json
from pathlib import Path


def _list_path(profile: str = "") -> Path:
    from core.config import ATHENA_ROOT
    p = ATHENA_ROOT / "profiles" / (profile or ".default") / "runtime" / "checklists.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load(path: Path) -> list[dict]:
    lists = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                try:
                    lists.append(json.loads(line))
                except Exception:
                    continue
    return lists


def _save(path: Path, lists: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for lst in lists:
            f.write(json.dumps(lst, ensure_ascii=False) + "\n")


def _new(args: dict, timeout: float = 10.0) -> str:
    path = _list_path(args.get("profile", ""))
    lists = _load(path)
    n = len(lists) + 1
    lst = {"id": str(n), "name": str(args.get("name", f"checklist-{n}")),
           "items": [], "created": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
    lists.append(lst)
    _save(path, lists)
    return json.dumps({"ok": True, "checklist": lst}, ensure_ascii=False)


def _add_item(args: dict, timeout: float = 10.0) -> str:
    path = _list_path(args.get("profile", ""))
    lists = _load(path)
    lst = next((l for l in lists if l.get("name") == args.get("name")), None)
    if not lst:
        return f"error: checklist not found: {args.get('name')}"
    lst["items"].append({"text": str(args.get("item", "")), "done": False})
    _save(path, lists)
    return json.dumps({"ok": True, "items": lst["items"]}, ensure_ascii=False)


def _toggle(args: dict, timeout: float = 10.0) -> str:
    path = _list_path(args.get("profile", ""))
    lists = _load(path)
    lst = next((l for l in lists if l.get("name") == args.get("name")), None)
    if not lst:
        return f"error: checklist not found: {args.get('name')}"
    idx = int(args.get("index", 0) or 0) - 1
    if 0 <= idx < len(lst["items"]):
        lst["items"][idx]["done"] = not lst["items"][idx]["done"]
    _save(path, lists)
    return json.dumps({"ok": True, "items": lst["items"]}, ensure_ascii=False)


def _show(args: dict, timeout: float = 10.0) -> str:
    path = _list_path(args.get("profile", ""))
    lists = _load(path)
    if args.get("name"):
        lst = next((l for l in lists if l.get("name") == args.get("name")), None)
        return json.dumps(lst, ensure_ascii=False) if lst else "error: not found"
    return json.dumps({"checklists": [
        {"name": l.get("name"), "total": len(l.get("items", [])),
         "done": sum(1 for i in l.get("items", []) if i.get("done"))}
        for l in lists]}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props, req in (
        ("checklist_new", "Create a new checklist.", _new,
         {"name": {"type": "string"}, "profile": {"type": "string"}}, ["name"]),
        ("checklist_add", "Add an item to a checklist.", _add_item,
         {"name": {"type": "string"}, "item": {"type": "string"},
          "profile": {"type": "string"}}, ["name", "item"]),
        ("checklist_toggle", "Toggle an item's done state (1-based index).", _toggle,
         {"name": {"type": "string"}, "index": {"type": "integer"},
          "profile": {"type": "string"}}, ["name", "index"]),
        ("checklist_show", "Show a checklist (or all) with progress.", _show,
         {"name": {"type": "string"}, "profile": {"type": "string"}}, []),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    return ["checklist_new", "checklist_add", "checklist_toggle", "checklist_show"]
