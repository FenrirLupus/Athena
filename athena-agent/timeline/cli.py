"""TIMELINE CLI — `athena timeline build|view|status`.

build — rebuild the graphs: the ROOT operation timeline (athena-system,
the architecture map) + the root disk timeline + each profile's disk +
operation timelines (their own files/projects).
view  — open a timeline.html in the default browser.
status— the graph bundles + their state counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _build_all() -> None:
    from timeline import root_graphs_dir, profile_graphs_dir
    from timeline.mapper import map_operations
    from timeline.disk_mapper import map_disk
    from timeline.render import write_bundle
    from core.config import ATHENA_ROOT

    print("Timeline build:")

    # ROOT: the athena-system OPERATION map (the architecture map).
    ops = map_operations(ATHENA_ROOT / "athena-system")
    idx = write_bundle(ops, root_graphs_dir() / "operations",
                       title="athena-system operations",
                       graph_list=[{"label": "root operations",
                                    "href": "../timeline.html"}])
    s = ops["summary"]
    print(f"  root operations → {idx.parent.name}: "
          f"{s['nodes']} nodes ({s['alive']} alive, {s['sick']} sick, "
          f"{s['dead']} dead)")

    # PER-MODULE GRAPHS (the Operator's 08-14 spec): each module gets
    # its OWN timeline.html — the endpoint .py files map to their own
    # graph. The root's module nodes hyperlink to them; the header's
    # graph-switcher swaps between them seamlessly.
    mod_dir = root_graphs_dir() / "operations" / "modules"
    mod_nodes = {}
    for n in ops["nodes"]:
        m = (n.get("module") or "root").split(".")[0]
        if not m:
            m = "root"
        mod_nodes.setdefault(m, []).append(n)
    mod_ids = {}
    for m, ns in mod_nodes.items():
        mod_ids[m] = {n["id"] for n in ns}
    # Cross-links: every module-level node links to ITS module's graph.
    # ABSOLUTE-rooted (the 08-14 404 fix): "operations/modules/x/…" —
    # the render prefixes the page depth so it works from ANY page.
    for m, ns in mod_nodes.items():
        rel = f"operations/modules/{m}/timeline.html"
        for n in ns:
            if n.get("kind") == "module":
                n["enters"] = rel
    # The graph list for the header switcher (root + every module).
    # THE ABSOLUTE-HREF FIX (the Operator's 08-14 bug): the hrefs are
    # rooted at the graphs dir (not the page's dir) — a module page's
    # switcher would otherwise resolve "modules/x" relative to ITSELF
    # (404). The render resolves them against the page's depth.
    root_graphs = root_graphs_dir()
    graph_list = [{"label": "root operations",
                   "href": "operations/timeline.html"}]
    graph_list += [{"label": f"{m} operations",
                    "href": f"operations/modules/{m}/timeline.html"}
                   for m in sorted(mod_nodes)]
    for m, ns in sorted(mod_nodes.items()):
        mlinks = [lk for lk in ops["links"]
                  if lk["source"] in mod_ids[m] or lk["target"] in mod_ids[m]]
        sub = {
            "directed": True, "multigraph": False,
            "graph": {"name": f"{m} operations", "kind": "operations",
                      "states": {}},
            "nodes": ns, "links": mlinks,
            "entry_points": ops["entry_points"],
            "summary": {"nodes": len(ns), "links": len(mlinks),
                        "alive": sum(1 for x in ns if x["state"] == "alive"),
                        "sick": sum(1 for x in ns if x["state"] == "sick"),
                        "dead": sum(1 for x in ns if x["state"] == "dead"),
                        "warnings": sum(
                            1 for lk in mlinks
                            if lk.get("state") == "sick")},
        }
        md = mod_dir / m
        write_bundle(sub, md, title=f"{m} operations", graph_list=graph_list)
        print(f"    module {m} → {len(ns)} nodes "
              f"({sub['summary']['alive']}A/{sub['summary']['sick']}S/"
              f"{sub['summary']['dead']}D)")
    # Re-write the root bundle with the FULL graph list (the switcher
    # on the root page lists root + every module).
    write_bundle(ops, root_graphs_dir() / "operations",
                 title="athena-system operations", graph_list=graph_list)

    # ROOT: the DISK map.
    disk = map_disk(ATHENA_ROOT, label="athena root disk")
    idx = write_bundle(disk, root_graphs_dir() / "disk",
                       title="athena root disk")
    s = disk["summary"]
    print(f"  root disk → {idx.parent.name}: "
          f"{s['nodes']} nodes ({s['alive']} alive, {s['sick']} sick, "
          f"{s['dead']} dead)")

    # PROFILES: each profile gets its own disk + operation graphs.
    from intelligence.profiles import list_profiles
    for p in list_profiles():
        proot = p.root
        pd = profile_graphs_dir(p.name)
        try:
            pdisk = map_disk(proot, label=f"{p.name} disk")
            write_bundle(pdisk, pd / "disk", title=f"{p.name} disk")
            ps = pdisk["summary"]
            print(f"  {p.name} disk → {ps['nodes']} nodes "
                  f"({ps['alive']} alive, {ps['sick']} sick, {ps['dead']} dead)")
        except Exception as exc:
            print(f"  {p.name} disk SKIP: {exc}")

    # The root index.json — the TOC of every graph.
    toc = {"system": "Timeline System", "root": str(root_graphs_dir()),
           "graphs": {}}
    for sub in sorted((root_graphs_dir()).iterdir()):
        if sub.is_dir() and (sub / "index.json").exists():
            try:
                d = json.loads((sub / "index.json").read_text(encoding="utf-8"))
                toc["graphs"][sub.name] = {
                    "title": d.get("title", sub.name),
                    "summary": d.get("summary", {}),
                    "index": str(sub / "index.json"),
                    "timeline": str(sub / "timeline.html"),
                }
            except Exception:
                continue
    (root_graphs_dir() / "index.json").write_text(
        json.dumps(toc, indent=2), encoding="utf-8")
    print(f"  root index → {len(toc['graphs'])} graphs indexed")


def _view(name: str = "operations") -> None:
    from timeline import root_graphs_dir
    import webbrowser
    target = root_graphs_dir() / name / "timeline.html"
    if not target.exists():
        print(f"no timeline for '{name}' (built: "
              f"{[p.name for p in root_graphs_dir().iterdir() if p.is_dir()]})")
        sys.exit(1)
    webbrowser.open(target.as_uri())
    print(f"opened {target}")


def _status() -> None:
    from timeline import root_graphs_dir
    for sub in sorted(root_graphs_dir().iterdir()):
        if sub.is_dir() and (sub / "index.json").exists():
            try:
                d = json.loads((sub / "index.json").read_text(encoding="utf-8"))
                s = d.get("summary", {})
                print(f"  {sub.name}: {d.get('title','')} — "
                      f"{s.get('nodes',0)} nodes "
                      f"({s.get('alive',0)}A/{s.get('sick',0)}S/{s.get('dead',0)}D)")
            except Exception:
                continue


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="athena timeline")
    ap.add_argument("command", choices=["build", "view", "status"])
    ap.add_argument("name", nargs="?", default="")
    args = ap.parse_args(argv)
    if args.command == "build":
        _build_all()
    elif args.command == "view":
        _view(args.name or "operations")
    elif args.command == "status":
        _status()
    return 0
