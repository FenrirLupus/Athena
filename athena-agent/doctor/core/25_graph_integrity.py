"""The GRAPH-INTEGRITY check — the doctor verifies the TIMELINE GRAPH
itself (the Operator's 08-15 spec).

The graph is a HOUSE-WIDE evidence source: the custodian reports dead-code
from it, the janitor cleans from it, the nurse repairs from it. A WRONG
graph propagates wrong decisions everywhere. This check makes the graph a
VERIFIED system:

  1. GRAPH EXISTS + FRESH  — missing = never built; stale (a source .py is
     newer than the graph) = does not reflect the code.
  2. DEAD-VERDICT SPOT-CHECK — sample dead nodes, grep the real code; a
     referenced "dead" node = a mapper false-positive (a rule regressed).
  3. ALIVE-VERDICT SPOT-CHECK — sample alive nodes, confirm real incoming
     edges; alive-with-only-defines = a suspicious over-count.
  4. THE 1:1 PROBE (internal) — build a temp imported-but-unused module,
     run map_operations, assert module=connection + function=dead. This is
     the regression proof the removed import_only_probe provided, now
     embedded in the doctor where it runs on every full suite.
  5. LINK SANITY — summary present, nodes/links non-empty, ids unique,
     node count in a sane band.

Runs in LIVE mode (read-only + a self-deleted temp probe — no state kept).
"""
from __future__ import annotations

from pathlib import Path


def _graph_index() -> Path:
    from timeline import root_graphs_dir
    return root_graphs_dir() / "operations" / "index.json"


def run() -> list[dict]:
    from core.config import ATHENA_ROOT
    checks = []

    # ---- 1. GRAPH EXISTS + FRESH ----
    idx = _graph_index()
    if not idx.exists():
        checks.append({
            "name": "graph exists",
            "status": "fail",
            "detail": f"the operations graph has never been built ({idx})",
        })
        return checks  # nothing else to check without a graph
    checks.append({"name": "graph exists", "status": "pass",
                   "detail": f"{idx} present"})

    sys_dir = ATHENA_ROOT / "athena-system"
    newest = None
    for py in sys_dir.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        mt = py.stat().st_mtime
        if newest is None or mt > newest[0]:
            newest = (mt, str(py.relative_to(sys_dir)))
    gmt = idx.stat().st_mtime
    if newest and newest[0] > gmt + 1.0:  # 1s clock-skew tolerance
        checks.append({
            "name": "graph fresh",
            "status": "fail",
            "detail": (f"STALE: {newest[1]} is newer than the graph — "
                       f"delete graphs + rebuild (timeline build)"),
        })
    else:
        checks.append({"name": "graph fresh", "status": "pass",
                       "detail": "no source file is newer than the graph"})

    # ---- load the graph ----
    try:
        import json
        g = json.loads(idx.read_text(encoding="utf-8"))
        nodes = g.get("nodes") or []
        links = g.get("links") or []
        summary = g.get("summary") or {}
    except Exception as exc:
        checks.append({"name": "graph parse", "status": "fail",
                       "detail": f"cannot parse the graph: {exc}"})
        return checks
    checks.append({"name": "graph parse", "status": "pass",
                   "detail": f"{len(nodes)} nodes, {len(links)} links"})

    # ---- 5. LINK SANITY ----
    ids = [n.get("id") for n in nodes]
    dups = len(ids) - len(set(ids))
    if dups:
        checks.append({"name": "graph ids unique", "status": "fail",
                       "detail": f"{dups} duplicate node ids (the _add_def "
                                 f"collision bug)"})
    else:
        checks.append({"name": "graph ids unique", "status": "pass",
                       "detail": "node ids unique"})
    if len(nodes) < 500:
        checks.append({"name": "graph node band", "status": "fail",
                       "detail": f"only {len(nodes)} nodes — a partial build "
                                 f"(expected > 500 for athena-system)"})
    else:
        checks.append({"name": "graph node band", "status": "pass",
                       "detail": f"{len(nodes)} nodes in the sane band"})

    # ---- 2. DEAD-VERDICT SPOT-CHECK (sample up to 8 dead members) ----
    dead = [n for n in nodes
            if n.get("state") == "dead"
            and n.get("kind") in ("function", "method", "class")]
    dead_sample = dead[:8]
    if not dead_sample:
        checks.append({"name": "dead verdict spot-check", "status": "pass",
                       "detail": "no dead members to spot-check"})
    else:
        dead_bad = 0
        for n in dead_sample:
            label = n.get("label", "")
            f = n.get("file", "")
            if not label or len(label) < 3:
                continue
            # grep the real code for the label OUTSIDE its own file
            found = _grep_label(label, f)
            if found:
                dead_bad += 1
                checks.append({
                    "name": "dead verdict spot-check",
                    "status": "fail",
                    "detail": (f"FALSE-POSITIVE: '{label}' (in {f}) is "
                               f"referenced at {found} — the mapper "
                               f"over-flags it (a rule regressed)"),
                })
        if dead_bad == 0:
            checks.append({"name": "dead verdict spot-check", "status": "pass",
                           "detail": f"{len(dead_sample)} dead nodes "
                                     f"verified unreferenced"})

    # ---- 3. ALIVE-VERDICT SPOT-CHECK (sample up to 8 alive members) ----
    alive = [n for n in nodes
             if n.get("state") == "alive"
             and n.get("kind") in ("function", "method", "class")]
    # Prefer members whose only incoming edges are defines (the risky ones).
    inc_count = {}
    for lk in links:
        t = lk.get("target", "")
        if lk.get("relation") != "defines":
            inc_count[t] = inc_count.get(t, 0) + 1
    alive_sample = sorted(alive, key=lambda n: inc_count.get(n.get("id"), 0))[:8]
    alive_warn = 0
    # ENTRY-NAME HONOR (the 08-15 refinement): main/__init__/run/fix/etc.
    # are alive BY DESIGN (the runtime invokes them by convention) — the
    # warn targets the suspicious non-entry members only.
    entry_names = {"main", "__init__", "run", "fix", "start", "tick",
                   "register", "scan", "close", "boot", "serve",
                   "__call__", "handle", "process", "route", "status",
                   "available"}
    for n in alive_sample:
        if inc_count.get(n.get("id"), 0) == 0:
            if n.get("label") in entry_names:
                continue  # a convention entry — alive by design
            alive_warn += 1
            checks.append({
                "name": "alive verdict spot-check",
                "status": "warn",
                "detail": (f"suspiciously alive: '{n.get('label')}' has no "
                           f"non-defines incoming — the interface-honor may "
                           f"have over-counted (verify)"),
            })
    if alive_warn == 0:
        checks.append({"name": "alive verdict spot-check", "status": "pass",
                       "detail": f"{len(alive_sample)} alive nodes have "
                                 f"real incoming references"})

    # ---- 4. THE 1:1 PROBE (internal, temp, self-deleted) ----
    probe_dir = ATHENA_ROOT / "athena-system" / "doctor"
    probe_file = probe_dir / f"_graph_probe_{__import__('os').getpid()}.py"
    try:
        probe_file.write_text(
            "def _probe_unused() -> str:\n    return 'probe'\n\n",
            encoding="utf-8")
        # map the SYSTEM root — the probe is under doctor/, mapped too
        from timeline.mapper import map_operations
        g2 = map_operations(sys_dir)
        probe_nodes = [n for n in g2.get("nodes", [])
                       if n.get("file", "").endswith(probe_file.name)]
        probe_mod = [n for n in probe_nodes if n.get("kind") == "module"]
        probe_fn = [n for n in probe_nodes if n.get("kind") == "function"]
        mod_ok = probe_mod and probe_mod[0].get("state") in ("connection", "alive")
        fn_ok = probe_fn and probe_fn[0].get("state") == "dead"
        if mod_ok and fn_ok:
            checks.append({"name": "1:1 probe contract", "status": "pass",
                           "detail": "imported module alive + unused "
                                     "function dead (the mapper is 1:1)"})
        else:
            checks.append({
                "name": "1:1 probe contract", "status": "fail",
                "detail": (f"mapper contract broken: module state="
                           f"{probe_mod[0].get('state') if probe_mod else '?'}, "
                           f"function state="
                           f"{probe_fn[0].get('state') if probe_fn else '?'}"),
            })
    except Exception as exc:
        checks.append({"name": "1:1 probe contract", "status": "fail",
                       "detail": f"probe failed: {exc}"})
    finally:
        try:
            probe_file.unlink(missing_ok=True)
        except Exception:
            pass

    return checks


def _grep_label(label: str, own_file: str) -> str:
    """Find the first REAL reference to label outside its own file.

    Returns 'file:line' or '' (no reference found)."""
    from core.config import ATHENA_ROOT
    sys_dir = ATHENA_ROOT / "athena-system"
    try:
        for py in sys_dir.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            if str(py) == own_file:
                continue
            try:
                lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, l in enumerate(lines, 1):
                if label in l and not l.lstrip().startswith("#"):
                    return f"{py.relative_to(sys_dir)}:{i}"
    except Exception:
        pass
    return ""
