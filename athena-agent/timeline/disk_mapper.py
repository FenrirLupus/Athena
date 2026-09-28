"""TIMELINE DISK MAPPER — the filesystem as a timeline.

Maps a directory tree (.athena/ or a profile's root) as nodes (dirs,
files) + links (parent→child "contains"). The lifecycle order is the
spine: the boot's ensure_all creates the layout top-down, so position
is the creation/ownership order — the wipe keep-list + the shared-home
symlinks are the roots that everything hangs from.

Same states: alive (present + referenced), dead (present but not in
the keep/known set — trim candidates), sick (present but erroring:
stale pycache, leftover temp dirs, orphaned files).
"""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import List

from . import ALIVE, SICK, DEAD

# Known-good / referenced roots (the wipe keep-list + the shared homes).
KNOWN_GOOD = {
    "athena-system", "authentication.json", ".secret", ".venv", ".wiki",
    "logs", "plugins", "profiles", "skills", "tools", "workflows",
    "graphs",
}
# Things that are suspicious (sick candidates).
SICK_MARKERS = ("__pycache__", ".pyc", "-wal", "-shm", "tmp_", "deleted")


def map_disk(root: Path, label: str = "") -> dict:
    """Build the disk timeline for a root directory."""
    root = Path(root)
    nodes: List[dict] = []
    links: List[dict] = []

    # The root itself.
    nodes.append({
        "id": str(root), "label": root.name or str(root),
        "kind": "dir", "module": "", "file": str(root), "line": 0,
        "pos_x": 0, "pos_y": 0, "state": ALIVE, "enters": None,
    })

    # THE __pycache__ PRUNE (the 08-15 fix): Python bytecode caches
    # (especially under .venv/site-packages) are normal — not health
    # signals. os.walk with dirnames pruning skips them ENTIRELY (their
    # children never walk) — no 2400+ fake "sick" nodes from dependency
    # caches.
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune: never descend into __pycache__ dirs.
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and d != ".git"]
        rel = str(Path(dirpath).relative_to(root))
        if "/.git" in "/" + rel or rel.startswith(".git"):
            continue
        if Path(dirpath).name == "__pycache__":
            continue
        # The dir node itself (except the root — added separately).
        if dirpath != str(root):
            nodes.append({
                "id": dirpath, "label": Path(dirpath).name,
                "kind": "dir", "module": "", "file": dirpath, "line": 0,
                "pos_x": 0, "pos_y": 0, "state": ALIVE, "enters": None,
            })
            links.append({"source": str(Path(dirpath).parent),
                          "target": dirpath,
                          "relation": "contains", "state": ALIVE})
        for fname in sorted(filenames):
            fpath = str(Path(dirpath) / fname)
            nodes.append({
                "id": fpath, "label": fname, "kind": "file",
                "module": "", "file": fpath, "line": 0,
                "pos_x": 0, "pos_y": 0, "state": ALIVE, "enters": None,
            })
            links.append({"source": dirpath, "target": fpath,
                          "relation": "contains", "state": ALIVE})

    # State pass: the known-good roots are alive; suspicious markers are
    # sick; everything else present is alive (it exists because the
    # layout created it) — DEAD is reserved for roots NOT in the known
    # set (trim candidates) at the TOP level.
    for n in nodes:
        rel = str(Path(n["file"]).relative_to(root))
        top = rel.split("/")[0] if rel != "." else root.name
        # The remaining SICK markers (stale .pyc files, -wal/-shm SQLite
        # journals, tmp_ leftovers, deleted markers) still flag — only
        # __pycache__ DIRS were skipped above.
        if any(m in n["file"] for m in SICK_MARKERS):
            n["state"] = SICK
        elif rel != "." and top in KNOWN_GOOD:
            n["state"] = ALIVE
        elif rel == ".":
            n["state"] = ALIVE
        else:
            n["state"] = DEAD
    for lk in links:
        src = next((n for n in nodes if n["id"] == lk["source"]), None)
        tgt = next((n for n in nodes if n["id"] == lk["target"]), None)
        if (src and src["state"] == DEAD) or (tgt and tgt["state"] == DEAD):
            lk["state"] = DEAD
        elif (src and src["state"] == SICK) or (tgt and tgt["state"] == SICK):
            lk["state"] = SICK
        else:
            lk["state"] = ALIVE

    # Positions: BFS depth from the root.
    q = deque([(str(root), 0)])
    seen = set()
    pos = 0
    while q:
        nid, depth = q.popleft()
        if nid in seen:
            continue
        seen.add(nid)
        for n in nodes:
            if n["id"] == nid:
                n["pos_x"] = pos
                n["pos_y"] = depth * 50
                pos += 1
                break
        for lk in links:
            if lk["source"] == nid and lk["target"] not in seen:
                q.append((lk["target"], depth + 1))

    alive = sum(1 for n in nodes if n["state"] == ALIVE)
    sickc = sum(1 for n in nodes if n["state"] == SICK)
    dead = sum(1 for n in nodes if n["state"] == DEAD)

    return {
        "directed": True,
        "multigraph": False,
        "graph": {"name": label or f"disk timeline: {root}",
                  "kind": "disk",
                  "states": {"alive": alive, "sick": sickc, "dead": dead}},
        "nodes": nodes,
        "links": links,
        "entry_points": [str(root)],
        "summary": {"nodes": len(nodes), "links": len(links),
                    "alive": alive, "sick": sickc, "dead": dead},
    }
