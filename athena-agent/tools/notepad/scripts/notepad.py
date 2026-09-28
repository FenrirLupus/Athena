"""Built-in notepad tool — operator notes (one script = one tool).

Notes are saved NATIVELY to the profile's WORKSPACE directory (the
Operator's 08-12 spec): notepad files live in workspace/notes/ as plain
.markdown/.txt files — the same place the agent's work files live.
"""

import datetime
import re
from pathlib import Path


def _notes_dir(profile: str = "") -> Path:
    from intelligence.profiles import get_profile
    p = get_profile(profile) if profile else get_profile("")
    ws = p.workspace_dir
    nd = ws / "notes"
    nd.mkdir(parents=True, exist_ok=True)
    return nd


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:48] or "note"


def _write(args: dict, timeout: float = 10.0) -> str:
    nd = _notes_dir(args.get("profile", ""))
    title = str(args.get("title", "")).strip() or "untitled"
    body = str(args.get("body", "")).strip()
    fname = _slug(title) + ".md"
    path = nd / fname
    now = datetime.datetime.now().isoformat(timespec="seconds")
    content = f"# {title}\n\n{body}\n\n---\n\n_updated: {now}_\n"
    path.write_text(content, encoding="utf-8")
    return f"saved {path} ({len(body)} chars)"


def _list(args: dict, timeout: float = 10.0) -> str:
    nd = _notes_dir(args.get("profile", ""))
    files = sorted(p.name for p in nd.glob("*.md")) + \
            sorted(p.name for p in nd.glob("*.txt"))
    return "\n".join(files) if files else "(no notes yet)"


def _read(args: dict, timeout: float = 10.0) -> str:
    nd = _notes_dir(args.get("profile", ""))
    name = str(args.get("name", "")).strip()
    for p in (*nd.glob("*.md"), *nd.glob("*.txt")):
        if p.name == name or p.stem == name:
            return p.read_text(encoding="utf-8", errors="replace")
    return f"error: note not found: {name}"


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props, req in (
        ("note_write", "Write a note to the workspace notes/ dir.", _write,
         {"title": {"type": "string"}, "body": {"type": "string"},
          "profile": {"type": "string"}}, ["title", "body"]),
        ("note_list", "List notes in the workspace notes/ dir.", _list,
         {"profile": {"type": "string"}}, []),
        ("note_read", "Read a note by name.", _read,
         {"name": {"type": "string"}, "profile": {"type": "string"}}, ["name"]),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    return ["note_write", "note_list", "note_read"]
