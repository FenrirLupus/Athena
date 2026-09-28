"""Timeline query tool — the STRUCTURE half of diagnosis (the Operator's
08-14 spec: timeline + metrics together = root cause, not a guess).

Given a node/function/error, return its neighborhood from the Timeline
System's index.json — what it calls, what calls it, its state
(alive/sick/dead), and the cross-module refs (enters: <module>).
"""

import json
from pathlib import Path


def _load_indexes():
    """Load the root operations + disk index.json (lazy)."""
    from timeline import root_graphs_dir
    out = {}
    base = root_graphs_dir()
    for sub in ("operations", "disk"):
        p = base / sub / "index.json"
        if p.exists():
            try:
                out[sub] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                out[sub] = None
    return out


def _find(query: str, indexes: dict):
    """Find nodes matching the query across the indexes."""
    q = query.strip().lower()
    hits = []
    for kind, idx in indexes.items():
        if not idx:
            continue
        for n in idx.get("nodes", []):
            nid = str(n.get("id", "")).lower()
            label = str(n.get("label", "")).lower()
            file = str(n.get("file", "")).lower()
            if q in nid or q in label or q in file or q in file.rsplit("/", 1)[-1]:
                hits.append((kind, n))
                if len(hits) >= 12:
                    return hits
    return hits


def _neighborhood(nid: str, kind: str, idx: dict) -> dict:
    """The node's direct links + state."""
    node = None
    for n in idx.get("nodes", []):
        if n.get("id") == nid:
            node = n
            break
    if node is None:
        return {"node": nid, "found": False}
    out_links = [lk for lk in idx.get("links", []) if lk.get("source") == nid]
    in_links = [lk for lk in idx.get("links", []) if lk.get("target") == nid]
    return {
        "node": nid,
        "found": True,
        "label": node.get("label"),
        "kind": node.get("kind"),
        "file": node.get("file"),
        "state": node.get("state", "unknown"),
        "module": node.get("module"),
        "enters": node.get("enters"),
        "calls": [{"target": lk.get("target"), "relation": lk.get("relation"),
                   "state": lk.get("state")} for lk in out_links[:12]],
        "called_by": [{"source": lk.get("source"), "relation": lk.get("relation"),
                       "state": lk.get("state")} for lk in in_links[:12]],
    }


def timeline_query(query: str, *, graph: str = "operations",
                   depth: int = 1) -> str:
    """Query the Timeline System graphs. Returns the node's neighborhood.

    query: a function/file/module name (fuzzy — matches id, label, or
    file path).
    graph: "operations" (default — the code timeline) or "disk".
    """
    try:
        indexes = _load_indexes()
        idx = indexes.get(graph)
        if idx is None:
            return (f"error: no {graph} graph indexed — run "
                    f"'athena timeline build' first")
        hits = _find(query, {graph: idx})
        if not hits:
            return f"no node found for '{query}' in the {graph} graph"
        lines = []
        for kind, n in hits[:6]:
            nb = _neighborhood(n.get("id"), kind, idx)
            st = nb.get("state", "?")
            mark = {"alive": "●", "sick": "▲", "dead": "✗"}.get(st, "?")
            lines.append(f"{mark} {nb.get('node')} [{st}] "
                         f"({nb.get('label')} · {nb.get('file','')})")
            for c in (nb.get("calls") or [])[:8]:
                lines.append(f"    → calls {c['target']} "
                             f"({c.get('relation')}) [{c.get('state')}]")
            for c in (nb.get("called_by") or [])[:8]:
                lines.append(f"    ← called by {c['source']} "
                             f"({c.get('relation')}) [{c.get('state')}]")
            if nb.get("enters"):
                lines.append(f"    ⧉ enters {nb['enters']}")
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as exc:
        return f"error: timeline_query failed: {exc}"


def _run_timeline_query(args: dict, timeout: float = 30.0) -> str:
    return timeline_query(
        str(args.get("query", "")),
        graph=str(args.get("graph", "operations")),
    )


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="timeline_query",
        description=("Query the Timeline System graphs — a node's "
                     "neighborhood (what it calls, what calls it), its "
                     "state (alive/sick/dead), and cross-module refs. "
                     "Use for diagnosis: find where a failing function "
                     "sits and what it touches."),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "function/file/module name"},
                "graph": {"type": "string",
                          "enum": ["operations", "disk"],
                          "description": "which graph (default operations)"},
            },
            "required": ["query"],
        },
        fn=_run_timeline_query,
    ))
    return ["timeline_query"]
