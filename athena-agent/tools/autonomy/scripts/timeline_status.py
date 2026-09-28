"""Timeline status tool — the graphs overview (the Operator's 08-14
spec: the heat map at a glance). Alive/sick/dead counts per graph +
which modules are sick + the index freshness.
"""

import json
from pathlib import Path


def timeline_status() -> str:
    """The Timeline System overview — alive/sick/dead per graph."""
    try:
        from timeline import root_graphs_dir
        base = root_graphs_dir()
        lines = ["Timeline System status:"]
        found = False
        for sub in sorted(base.iterdir()):
            if not sub.is_dir():
                continue
            p = sub / "index.json"
            if not p.exists():
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            found = True
            s = d.get("summary", {})
            title = d.get("title", sub.name)
            mark = "✗" if s.get("sick", 0) else ("✓" if s.get("dead", 0) else "✓")
            lines.append(
                f"{mark} {sub.name} — {title}: {s.get('nodes',0)} nodes "
                f"({s.get('alive',0)} alive · {s.get('sick',0)} sick · "
                f"{s.get('dead',0)} dead)")

            # The sick modules (the heat).
            sick_mods = {}
            for n in d.get("nodes", []):
                if n.get("state") == "sick":
                    m = n.get("module") or "root"
                    sick_mods[m] = sick_mods.get(m, 0) + 1
            if sick_mods:
                top = sorted(sick_mods.items(), key=lambda kv: -kv[1])[:6]
                lines.append("    sick: " + ", ".join(
                    f"{m} ({c})" for m, c in top))
        if not found:
            return ("no graphs indexed — run 'athena timeline build' first")
        return "\n".join(lines)
    except Exception as exc:
        return f"error: timeline_status failed: {exc}"


def _run_timeline_status(args: dict, timeout: float = 30.0) -> str:
    return timeline_status()


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="timeline_status",
        description=("The Timeline System overview — alive/sick/dead "
                     "counts per graph + which modules are sick (the "
                     "heat map). Use to see the architecture's health "
                     "at a glance before diagnosing."),
        parameters={
            "type": "object",
            "properties": {},
        },
        fn=_run_timeline_status,
    ))
    return ["timeline_status"]
