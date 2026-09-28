"""TIMELINE MAPPER — the operation timeline: path-trace the code.

Walks athena-system/ with Python's ast module (no external parser —
the tree is pure Python). Every file becomes nodes (functions, classes,
module) + links (calls, imports, registrations). The SPINE is built
from the entry points: athena.py main() → the boot chain → the loops →
the tick. Anything unreachable from an entry point is a DEAD END.

# Node shape (graph-structured + the timeline fields):
  {id, label, kind, module, file, line, pos_x, pos_y, state, enters}
Link shape:
  {source, target, relation, state}
"""

from __future__ import annotations

import ast
import os
import re
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from . import ALIVE, SICK, DEAD, CONNECTION


# ── The entry points the spine starts from ──────────────────────────
ENTRY_POINTS = [
    ("athena.py", "main"),
    ("athena.py", "_run_gui"),
    ("athena.py", "_boot"),
    ("cli/main.py", "main"),
]

# The callable kinds that count as nodes.
_NODE_KINDS = ("module", "class", "function", "method")


def _module_name(file: Path, root: Path) -> str:
    try:
        rel = file.relative_to(root)
    except ValueError:
        rel = file
    return str(rel.parent).replace("/", ".")


def _file_nodes(file: Path, root: Path) -> tuple:
    """Parse one file → (nodes, links, entry_defs).

    entry_defs = the names defined at module level (callable from
    outside). Links: call edges (function → function it calls) + import
    edges (module → imported module).
    """
    nodes: List[dict] = []
    links: List[dict] = []
    entry_defs: List[str] = []
    mod = _module_name(file, root)
    try:
        src = file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return nodes, links, entry_defs
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return nodes, links, entry_defs

    # Import edges: FILE → imported names (the source is the file node
    # id, not the bare module name — so the import links survive the
    # dangling sweep and the module-import resolution pass can resolve
    # the targets to their real file nodes).
    file_id = str(file.relative_to(root)) if file.is_relative_to(root) else str(file)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name or "")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
                # THE SUBMODULE-IMPORT FIX (the 08-14 accuracy bug):
                # `from core import cua` imports the PACKAGE `core` AND
                # the SUBMODULE `core.cua` — both are real dependencies.
                # The submodule must be a separate import target or
                # core/cua.py stays unreachable (dead) despite being
                # imported every day.
                for alias in node.names:
                    aname = alias.name or ""
                    if aname and aname != "*":
                        imported.add(f"{node.module}.{aname}")
    for name in imported:
        links.append({
            "source": file_id,
            "target": name,
            "relation": "imports",
            "state": ALIVE,
        })

    # The module node (the FILE itself — the id is the file path, so
    # two files never collide; the module field groups by directory).
    nodes.append({
        "id": file_id,
        "label": file.name,
        "kind": "module",
        "module": mod,
        "file": str(file),
        "line": 1,
        "pos_x": 0, "pos_y": 0,
        "state": ALIVE,
        "enters": None,
    })
    entry_defs.append(file_id)

    # Functions/classes + their bodies.
    calls_in: Dict[str, set] = {}
    refs_in: Dict[str, set] = {}   # THE 08-15 1:1 mapping (annotations/reads)
    # THE 08-15 RULE-2 COLLECTOR: module-level references (dict-dispatch
    # tables, assignment RHS, list values) — names used as VALUES, not
    # calls. These are registrations by reference (COMMANDS = {...}).
    module_refs: set = set()
    base = f"{file_id}.{file.stem}"

    def _add_def(name, kind, node, parent=""):
        # THE UNIQUE-ID FIX: the file stem is part of the id, so two
        # files in the same module defining the same name never collide
        # (e.g. cli/main.py._run_mcp vs athena.py._run_mcp). A RE-def
        # in the SAME file (e.g. a forward decl + real def) is deduped.
        nid = f"{base}.{name}" if not parent else f"{base}.{parent}.{name}"
        if any(n["id"] == nid for n in nodes):
            return nid
        nodes.append({
            "id": nid,
            "label": name,
            "kind": kind,
            "module": mod,
            "file": str(file),
            "line": getattr(node, "lineno", 1),
            "pos_x": 0, "pos_y": 0,
            "state": ALIVE,
            "enters": None,
        })
        calls_in[nid] = set()
        refs_in[nid] = set()
        if parent:
            links.append({"source": parent, "target": nid,
                          "relation": "defines", "state": ALIVE})
        return nid

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            nid = _add_def(node.name, "function", node)
            entry_defs.append(node.name)
            _collect_calls(node, nid, calls_in, mod)
            _collect_refs(node, nid, refs_in, mod)
        elif isinstance(node, ast.ClassDef):
            cid = _add_def(node.name, "class", node)
            entry_defs.append(node.name)
            # THE 08-15 RULE 1: base-class inheritance — class Foo(Base)
            # REFERENCES Base (a subclass is a use of its base).
            for b in node.bases:
                if isinstance(b, ast.Name):
                    module_refs.add(b.id)
                elif isinstance(b, ast.Attribute):
                    module_refs.add(b.attr)
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) or isinstance(sub, ast.AsyncFunctionDef):
                    mid = _add_def(sub.name, "method", sub, parent=node.name)
                    _collect_calls(sub, mid, calls_in, mod)
                    _collect_refs(sub, mid, refs_in, mod)

    # THE 08-15 RULE 2: module-level VALUE references — dict-dispatch
    # tables (COMMANDS = {name: func}), list registries, assignment RHS,
    # AND keyword-argument registrations (register(Tool(... fn=_terminal))).
    # A Name in a dict VALUE / list VALUE / assign RHS / kwarg VALUE is a
    # reference to that function (a registration by pointer, not a call).
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    pass  # the LHS is a def name, not a use
            v = node.value
            if isinstance(v, ast.Dict):
                for val in v.values:
                    if isinstance(val, ast.Name):
                        module_refs.add(val.id)
            elif isinstance(v, (ast.List, ast.Tuple)):
                for el in v.elts:
                    if isinstance(el, ast.Name):
                        module_refs.add(el.id)
            elif isinstance(v, ast.Name) and not isinstance(node.targets[0], ast.Attribute):
                # THE MODULE-ALIAS CASE: `foo = bar` at module level is a
                # reference to bar (re-export / alias).
                if isinstance(node.targets[0], ast.Name):
                    module_refs.add(v.id)
        elif isinstance(node, ast.Call):
            # THE KWRAG-REGISTRATION CASE: register(Tool(... fn=_terminal))
            # — a Name as a KEYWORD VALUE is a registration by pointer.
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if isinstance(kw.value, ast.Name):
                    module_refs.add(kw.value.id)
                elif isinstance(kw.value, ast.Call) and isinstance(kw.value.func, ast.Name):
                    # register(Tool(... fn=build_terminal())) — the func
                    # being called is a reference too.
                    module_refs.add(kw.value.func.id)
            # THE 08-15 MODULE-CALL CASE: a bare module-level CALL statement
            # (`_register_platform_tools()`) invokes a function at import
            # time — the module node CALLS it. The per-function collector
            # never sees module-level statements, so this edge must come
            # from the module node itself.
            if isinstance(node.func, ast.Name):
                module_refs.add(node.func.id)

    # Call links: resolve same-file defs to their id; leave the rest as
    # bare-name targets (the cross-file pass in map_operations resolves
    # them against the whole tree).
    known_ids = {n["id"] for n in nodes}
    for src, targets in calls_in.items():
        for tgt in targets:
            resolved = f"{base}.{tgt}" if tgt in entry_defs else None
            if resolved and resolved in known_ids:
                links.append({"source": src, "target": resolved,
                              "relation": "calls", "state": ALIVE})
            else:
                links.append({"source": src, "target": tgt,
                              "relation": "calls", "state": ALIVE})

    # REF links (the 08-15 1:1 mapping): type annotations, attribute
    # reads, and name loads. Same-file resolution like calls; the
    # cross-file pass resolves the bare names too.
    for src, targets in refs_in.items():
        for tgt in targets:
            if tgt == "__registered__":
                # THE 08-15 RULE-3 EMIT: a framework-decorated function
                # is registered (used) by the framework. A self-ref edge
                # marks it so the usage check counts it as used.
                links.append({"source": src, "target": src,
                              "relation": "refs", "state": ALIVE})
                continue
            resolved = f"{base}.{tgt}" if tgt in entry_defs else None
            if resolved and resolved in known_ids:
                links.append({"source": src, "target": resolved,
                              "relation": "refs", "state": ALIVE})
            else:
                links.append({"source": src, "target": tgt,
                              "relation": "refs", "state": ALIVE})

    # THE 08-15 RULE-2 EMIT: module-level value refs — the FILE node
    # references the dispatch-table members (COMMANDS = {name: func}).
    for tgt in module_refs:
        resolved = f"{base}.{tgt}" if tgt in entry_defs else None
        if resolved and resolved in known_ids:
            links.append({"source": file_id, "target": resolved,
                          "relation": "refs", "state": ALIVE})
        else:
            links.append({"source": file_id, "target": tgt,
                          "relation": "refs", "state": ALIVE})

    return nodes, links, entry_defs


def _collect_calls(node, nid, calls_in, mod):
    """Find every call target inside a function body."""
    targets = calls_in.setdefault(nid, set())
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Name):
                targets.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                # obj.attr() — keep the attr name (best-effort resolution).
                targets.add(fn.attr)


def _collect_refs(node, nid, refs_in, mod):
    """Find every REFERENCE inside a function body — type annotations,
    attribute reads, and name loads — for the 1:1 mapping (the Operator's
    08-15 spec: everything applicable must be linked, so a class used as
    a TYPE (e.g. req: ChatRequest) is never falsely dead).

    Unlike calls (a function being invoked), a REF is a symbol being
    referenced: an annotation (arg/return types), an attribute read
    (obj.attr without a call), or a bare name load (a variable, constant,
    or imported symbol). These become "refs" edges the cross-file pass
    resolves to their definitions.
    """
    targets = refs_in.setdefault(nid, set())
    # THE 08-15 RULE 3 (the TOP-LEVEL fix): the decorated function is
    # the node passed IN — ast.walk(node) does NOT include the node
    # itself (only its children). Check the node's own decorators here.
    # BOTH def + async def (the mcp handlers are async).
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                if isinstance(d.func.value, ast.Name):
                    targets.add(d.func.value.id)
                targets.add(d.func.attr)
                targets.add("__registered__")
            elif isinstance(d, ast.Attribute):
                if isinstance(d.value, ast.Name):
                    targets.add(d.value.id)
                targets.add("__registered__")
            elif isinstance(d, ast.Name):
                # A plain @decorator — the decorator is used.
                targets.add(d.id)
                targets.add("__registered__")
    for sub in ast.walk(node):
        # THE 08-15 RULE 3: framework decorator registration —
        # @router.post(...) / @app.get(...) registers the function with the
        # decorator's base object. The base (router/app/bp) is a reference;
        # AND the decorated function is USED (registered) by the framework —
        # a self-ref keeps it alive (the router's method resolves to nothing,
        # but the framework registered the handler by the decorator).
        if isinstance(sub, ast.FunctionDef) and sub.decorator_list:
            for d in sub.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                    # @obj.method(...) — obj is the registry
                    if isinstance(d.func.value, ast.Name):
                        targets.add(d.func.value.id)
                    # and the method is the registration kind
                    targets.add(d.func.attr)
                    # THE SELF-REGISTRATION (the 08-15 fix): the decorated
                    # function is USED by the framework — mark it referenced
                    # by its own module (the framework lookup target).
                    targets.add("__registered__")
                elif isinstance(d, ast.Attribute):
                    # @obj.decorator (no call) — obj is the reference
                    if isinstance(d.value, ast.Name):
                        targets.add(d.value.id)
                    targets.add("__registered__")
        # Type annotations: def chat(req: ChatRequest) -> ChatResponse
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if sub.args:
                for a in list(sub.args.args) + list(sub.args.kwonlyargs):
                    ann = getattr(a, "annotation", None)
                    _collect_ann(ann, targets)
                if sub.args.vararg and getattr(sub.args.vararg, "annotation", None):
                    _collect_ann(sub.args.vararg.annotation, targets)
                if sub.args.kwarg and getattr(sub.args.kwarg, "annotation", None):
                    _collect_ann(sub.args.kwarg.annotation, targets)
            if getattr(sub, "returns", None):
                _collect_ann(sub.returns, targets)
        elif isinstance(sub, ast.AnnAssign):
            _collect_ann(sub.annotation, targets)
        elif isinstance(sub, ast.Attribute):
            # obj.attr (a read, not a call — calls were captured already).
            # Keep the attr name + the base name (obj).
            if isinstance(sub.value, ast.Name):
                targets.add(sub.value.id)
            targets.add(sub.attr)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            # A bare name load (imported symbol, constant, variable).
            targets.add(sub.id)
        elif isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            # THE 08-15 RULE 4: dynamic name lookup —
            # globals().get("name") / getattr(obj, "name") with a STRING
            # constant references the def by name. The string becomes a
            # ref target so the cross-file pass can resolve it.
            # F-STRINGS TOO: globals().get(f"_chk_{name}") has the
            # literal prefix "_chk_" recoverable from the JoinedStr.
            if sub.func.attr in ("get", "getattr", "globals", "locals", "vars"):
                for a in sub.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        targets.add(a.value)
                    elif isinstance(a, ast.JoinedStr):
                        # Extract the literal parts of the f-string:
                        # f"_chk_{name}" → "_chk_"
                        lit = "".join(
                            s.value for s in a.values
                            if isinstance(s, ast.Constant) and isinstance(s.value, str))
                        if lit:
                            targets.add(lit)


def _collect_ann(ann, targets):
    """Add the names inside a type annotation (Name, Subscript, ...)."""
    if ann is None:
        return
    for node in ast.walk(ann):
        if isinstance(node, ast.Name):
            targets.add(node.id)
        elif isinstance(node, ast.Attribute):
            targets.add(node.attr)


def _prefixes(name: str) -> list:
    """The dynamic-lookup prefixes of a def name — the leading-underscore
    groups an f-string can reference. '_chk_artifacts' → ['_chk_'].
    '_foo_bar_baz' → ['_foo_', '_foo_bar_']. The rule: a def named
    _PREFIX_suffix is reachable via globals().get(f'{PREFIX}{var}').
    """
    out = []
    if not name.startswith("_"):
        return out
    parts = name.split("_")
    acc = ""
    for i, p in enumerate(parts):
        if i == 0:
            acc = p + "_"   # the leading '_'
            continue
        acc += p + "_"
        if len(acc) >= 3 and i < len(parts) - 1:
            out.append(acc)
    return out


# ── The state pass ──────────────────────────────────────────────────
def _classify_states(nodes: List[dict], links: List[dict],
                     entry_ids: set, sick_ids: set) -> None:
    """BFS reachability from the entry points → alive vs dead. Sick wins
    over alive (a reachable node with recent errors is sick)."""
    alive = set()
    q = deque(entry_ids)
    while q:
        nid = q.popleft()
        if nid in alive:
            continue
        alive.add(nid)
        # DIRECTIONAL reachability: from an entry, follow OUTGOING links
        # (source → target) only. Reverse edges would make everything
        # reachable (dead code with an alive descendant stays dead).
        for lk in links:
            if lk["source"] == nid and lk["target"] not in alive:
                q.append(lk["target"])
    for n in nodes:
        if n["id"] in sick_ids:
            n["state"] = SICK
        elif n["id"] in alive or n.get("_force_alive"):
            n["state"] = ALIVE
        else:
            n["state"] = DEAD

    # THE USAGE CHECK (the Operator's 08-15 1:1 spec): a FUNCTION, METHOD,
    # or CLASS is ALIVE only when something CALLS, REFS, or IMPORTS it — a
    # `defines` edge from an alive module is CONTAINMENT, not usage. A
    # function in an imported-but-unused module (e.g. the import_only_probe)
    # must map DEAD: the module is alive (imported), but its members are
    # never used. This makes "imported ≠ used" visible in the graph.
    #
    # THE NAME-RESOLUTION HONOR (the 08-15 fix): the cross-file resolver
    # uses FIRST-HIT-WINS (setdefault) — when a test MOCKS a function name
    # (e.g. the doctor's execute_tool_call), calls may resolve to the mock
    # and the REAL definition looks unused. A node is used when ANY edge
    # targets a node with the SAME LABEL (the resolution ambiguity means
    # the real one is reachable by name too). Exact-id edges count first;
    # label-suffix edges keep the real definition alive.
    usage_in: Dict[str, int] = {}
    label_usage: Dict[str, int] = {}
    for lk in links:
        if lk.get("relation") in ("calls", "refs", "imports"):
            t = lk.get("target", "")
            usage_in[t] = usage_in.get(t, 0) + 1
            lbl = t.rsplit(".", 1)[-1]
            label_usage[lbl] = label_usage.get(lbl, 0) + 1
    # THE DYNAMIC-ENTRY HONOR (the 08-15 fix): modules seeded as DYNAMIC
    # entries (the doctor's importlib loader, the knowledge/nurse hooks,
    # the data/CLI commands) are invoked by the RUNTIME, not the AST. A
    # `run`/`fix`/`main` inside such a module is the entry point itself —
    # it IS used (the loader calls it by convention), so the usage check
    # must not kill it. This is NOT a bypass: it mirrors how the runtime
    # actually invokes those modules.
    dynamic_roots = ("/doctor/", "/knowledge/", "/data/", "/integrations/",
                     "/metrics/", "core/custodian.py", "core/janitor.py",
                     "security/integrity.py")
    for n in nodes:
        if n.get("kind") in ("function", "method") and n["state"] == ALIVE:
            if n.get("label") in ("run", "fix", "main") and any(
                    r in n.get("file", "") for r in dynamic_roots):
                continue  # a dynamic entry — used by the loader
            if usage_in.get(n["id"], 0) == 0:
                # THE NAME-RESOLUTION HONOR: if ANY edge targets a node
                # with this label (the mock-shadowing case), the real
                # definition is used by name — keep it alive.
                if label_usage.get(n.get("label"), 0) > 0:
                    continue
                n["state"] = DEAD

    # THE CONSTRUCTOR FIX (the 08-15 1:1 mapping): a class INSTANTIATION
    # (`MessageLoop(`) links to the CLASS node — but the __init__ METHOD
    # has no direct edge. When the class is alive (instantiated), its
    # __init__ is alive too.
    class_alive = {n["id"]: n["state"] for n in nodes
                   if n["kind"] == "class"}
    for n in nodes:
        if (n.get("kind") == "method" and n["label"] == "__init__"
                and n["state"] == DEAD):
            parent = n["id"].rsplit(".", 1)[0]
            if class_alive.get(parent) == ALIVE:
                n["state"] = ALIVE

    # THE INTERFACE-IMPLEMENTATION HONOR (the 08-15 fix): a METHOD of an
    # ALIVE class is callable through dynamic dispatch — the runtime
    # invokes backends by interface (`platform.build_command` resolves to
    # WindowsBackend.build_command; `tool_registry.execute_tool_call`
    # resolves to the registered Tool.run). The AST cannot see these
    # dispatches, so a method whose CLASS is alive is ALIVE too. This is
    # NOT a bypass: it mirrors the interface pattern the runtime uses.
    for n in nodes:
        if (n.get("kind") == "method" and n["state"] == DEAD):
            parent = n["id"].rsplit(".", 1)[0]
            if class_alive.get(parent) == ALIVE:
                n["state"] = ALIVE

    # THE INIT-EXPORT FIX (the 08-14 accuracy fix): a function defined
    # in a package's __init__.py is an EXPORT — the package is alive,
    # so its exports are used (imported by name from the package). Mark
    # them alive when their init container is alive.
    init_of = {}
    for n in nodes:
        if n["kind"] == "module" and n["id"].endswith("__init__.py"):
            pkg = n["id"].removesuffix("/__init__.py")
            init_of[pkg] = n["id"]
    for n in nodes:
        if n["state"] != DEAD:
            continue
        # A function whose file is an __init__.py inside an alive pkg.
        f = Path(n.get("file", ""))
        if f.name == "__init__.py":
            for pkg, init_id in init_of.items():
                if (str(f).endswith(f"{pkg}/__init__.py")
                        and any(x["id"] == init_id and x["state"] == ALIVE
                               for x in nodes)):
                    n["state"] = ALIVE
                    break

    # THE PATH-DEGRADATION GRADIENT (the Operator's 08-14 spec): a path
    # that degrades toward dead/error territory must show YELLOW before
    # RED — never green straight to red (unless a catastrophic error is
    # found). An ALIVE node whose child is DEAD or SICK is the
    # divergence point: it becomes SICK (yellow), so tracing the path
    # reads green → yellow → red. Propagate one level up so the caution
    # is visible right before the failure.
    changed = True
    while changed:
        changed = False
        for lk in links:
            src = next((n for n in nodes if n["id"] == lk["source"]), None)
            tgt = next((n for n in nodes if n["id"] == lk["target"]), None)
            if (src is None or tgt is None
                    or src["state"] != ALIVE
                    or tgt["state"] not in (DEAD, SICK)):
                continue
            src["state"] = SICK
            changed = True

    # THE CLASS-CONTAINMENT FIX (the 08-14 accuracy fix): a class whose
    # METHODS are alive is itself alive (the class IS used — its methods
    # are called). The resolver links methods directly, so the class
    # node was stranded dead despite its live children.
    method_of = {}
    for n in nodes:
        if n["kind"] == "method":
            # A method id: <file>.<module>.<Class>.<method> — the class
            # is everything before the LAST dot.
            cid = n["id"].rsplit(".", 1)[0]
            method_of.setdefault(cid, []).append(n)
    for n in nodes:
        if n["kind"] == "class" and n["state"] == DEAD:
            kids = method_of.get(n["id"], [])
            if any(k["state"] in (ALIVE, SICK) for k in kids):
                n["state"] = ALIVE

    # THE CONNECTION TERMINALS (the Operator's 08-14 spec): a node with
    # an `enters` target is a PLOT ENDPOINT — it transitions to another
    # graph/file (the module nodes link to their module's timeline.html).
    # Marked as its own 5th state so the wiring diagram shows WHERE the
    # circuit leaves to another board.
    for n in nodes:
        if n.get("enters"):
            n["state"] = CONNECTION

    for lk in links:
        src = next((n for n in nodes if n["id"] == lk["source"]), None)
        tgt = next((n for n in nodes if n["id"] == lk["target"]), None)
        if (src and src["state"] == DEAD) or (tgt and tgt["state"] == DEAD):
            lk["state"] = DEAD
        elif (src and src["state"] == SICK) or (tgt and tgt["state"] == SICK):
            lk["state"] = SICK
        else:
            lk["state"] = ALIVE


def _sick_ids_from_metrics(root: Path) -> set:
    """The SICK hook: scan the metric logs (root aggregate + profile
    logs) for L3+ entries; the tool/action/source names become sick ids."""
    sick = set()
    log_dirs = [root / "logs", root / "profiles"]
    for base in log_dirs:
        if not base.is_dir():
            continue
        for logf in base.rglob("*_metric.log"):
            try:
                with open(logf, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if "level" not in line or '"level": 3' not in line and '"level": 4' not in line and '"level": 5' not in line:
                            continue
                        import json as _json
                        try:
                            d = _json.loads(line)
                        except Exception:
                            continue
                        for key in ("tool", "action", "source"):
                            v = str(d.get(key, "") or "").strip()
                            if v and len(v) > 2:
                                sick.add(v)
            except Exception:
                continue
    return sick


# ── The layout pass: the WIRING DIAGRAM (the Operator's 08-14 spec) ──
# The graph is a LAYERED DAG, like an electrical wiring diagram:
#   * ROW (pos_y) = the longest-path DEPTH from the entry points —
#     power (entries) at the top, ground (leaves) at the bottom.
#     FAN-IN: a node with several inputs sits at the row BELOW its
#     deepest input (the merge point). FAN-OUT: a node's children
#     spread across the next row.
#   * COLUMN (pos_x) = a UNIQUE slot per row — a per-row cursor
#     guarantees no two nodes ever share a position (nothing plots
#     at 0,0 unless it IS the origin).
#   * Every node gets a position — dead nodes flow below the alive
#     graph in their own band, terminating with DEAD END.
def _layout_tree(nodes: List[dict], links: List[dict],
                 entry_ids: set) -> None:
    """Assign (pos_x = column, pos_y = row) as a layered wiring DAG."""
    # Children + parents maps from the non-dead links (the flow).
    children: Dict[str, list] = {}
    parents: Dict[str, list] = {}
    for n in nodes:
        children.setdefault(n["id"], [])
        parents.setdefault(n["id"], [])
    for lk in links:
        if lk["state"] == DEAD:
            continue
        s, t = lk.get("source"), lk.get("target")
        if s in children and t in children:
            if t not in children[s]:
                children[s].append(t)
            if s not in parents[t]:
                parents[t].append(s)

    # ROWS: longest-path depth from the entries (a memoized DFS).
    row_of: Dict[str, int] = {}

    def _row(nid: str, visiting: set) -> int:
        if nid in row_of:
            return row_of[nid]
        if nid in visiting:
            return 0  # cycle guard
        visiting.add(nid)
        ps = [p for p in parents.get(nid, []) if p not in visiting]
        if nid in entry_ids or not ps:
            r = 0
        else:
            r = max(_row(p, visiting) for p in ps) + 1
        visiting.discard(nid)
        row_of[nid] = r
        return r

    for n in nodes:
        if n["state"] != DEAD:
            _row(n["id"], set())
    max_alive_row = max((row_of.get(n["id"], 0) for n in nodes
                         if n["state"] != DEAD), default=0)

    # DEAD BAND: dead nodes flow below the alive graph. Their rows are
    # their own longest-path within the dead subgraph, offset below the
    # alive rows (each dead component terminates with DEAD END).
    dead_parents: Dict[str, list] = {}
    for n in nodes:
        if n["state"] == DEAD:
            dead_parents.setdefault(n["id"], [])
    for lk in links:
        if lk["state"] != DEAD:
            continue
        s, t = lk.get("source"), lk.get("target")
        if s in dead_parents and t in dead_parents and s != t:
            if s not in dead_parents[t]:
                dead_parents[t].append(s)
    dead_row_of: Dict[str, int] = {}

    def _dead_row(nid: str, visiting: set) -> int:
        if nid in dead_row_of:
            return dead_row_of[nid]
        if nid in visiting:
            return 0
        visiting.add(nid)
        ps = [p for p in dead_parents.get(nid, []) if p not in visiting]
        r = (max(_dead_row(p, visiting) for p in ps) + 1) if ps else 0
        visiting.discard(nid)
        dead_row_of[nid] = r
        return r

    for n in nodes:
        if n["state"] == DEAD:
            _dead_row(n["id"], set())
    max_dead_row = max((dead_row_of.get(n["id"], 0) for n in nodes
                        if n["state"] == DEAD), default=0)

    # COLUMNS: a per-row cursor guarantees unique slots. Process rows
    # top→down; within a row, order by the parents' columns so the
    # wiring stays compact (children near their inputs).
    col_of: Dict[str, int] = {}
    row_cursor: Dict[int, int] = {}

    def _next_col(row: int) -> int:
        c = row_cursor.get(row, 0)
        row_cursor[row] = c + 1
        return c

    # Alive first (row 0 → max_alive_row).
    alive_ids = [n["id"] for n in nodes if n["state"] != DEAD]
    for row in range(0, max_alive_row + 1):
        # Order this row's unplaced nodes by their parent columns.
        row_nodes = [nid for nid in alive_ids
                     if row_of.get(nid, 0) == row and nid not in col_of]
        row_nodes.sort(key=lambda nid: min(
            (col_of.get(p, 0) for p in parents.get(nid, [])), default=0))
        for nid in row_nodes:
            col_of[nid] = _next_col(row)

    # Dead band (offset below the alive rows).
    dead_ids = [n["id"] for n in nodes if n["state"] == DEAD]
    for row in range(0, max_dead_row + 1):
        row_nodes = [nid for nid in dead_ids
                     if dead_row_of.get(nid, 0) == row and nid not in col_of]
        row_nodes.sort(key=lambda nid: min(
            (col_of.get(p, 0) for p in dead_parents.get(nid, [])),
            default=0))
        for nid in row_nodes:
            col_of[nid] = _next_col(max_alive_row + 2 + row)

    # GUARANTEE: every node gets a position — the sweep catches any
    # straggler (a node the maps missed) and gives it a unique slot.
    for n in nodes:
        if n["id"] not in col_of:
            col_of[n["id"]] = _next_col(9999)
        n["pos_x"] = col_of[n["id"]]
        n["pos_y"] = (row_of.get(n["id"], max_alive_row + 2
                                 + dead_row_of.get(n["id"], 0))
                      if n["state"] != DEAD else
                      max_alive_row + 2 + dead_row_of.get(n["id"], 0))


# ── The main map ────────────────────────────────────────────────────
def map_operations(root: Path, sick_ids: Optional[set] = None) -> dict:
    """Build the operation graph for athena-system.

    Returns {directed, multigraph, graph, nodes, links, entry_points,
    summary}.
    """
    nodes: List[dict] = []
    links: List[dict] = []
    entry_ids = set()
    all_entry_defs: Dict[str, set] = {}

    # Every python file under the root (excluding pycache/venv).
    for file in sorted(root.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        fnodes, flinks, defs = _file_nodes(file, root)
        nodes.extend(fnodes)
        links.extend(flinks)
        mod = _module_name(file, root)
        all_entry_defs[mod] = set(defs)

    # The entry-point nodes (the spine roots): the module FILE nodes
    # for athena.py + cli/main.py + their `main`/`_run_gui`/`_boot`
    # functions (matched by file path + function name in the id).
    for n in nodes:
        if n["kind"] == "module" and (
                n["file"].endswith("athena.py")
                or n["file"].endswith("cli/main.py")):
            entry_ids.add(n["id"])
    for fname, func in ENTRY_POINTS:
        for n in nodes:
            if (n["file"].endswith(fname)
                    and n["id"].endswith(f".{func}")):
                entry_ids.add(n["id"])
                break

    # THE DYNAMIC-LOADER ENTRIES (the 08-14 accuracy fix): the builtin
    # tools are registered at boot via importlib (register_builtin_tools
    # walks tools/<cat>/scripts/*.py) — a dynamic loop the AST walker
    # can't see. The tool scripts ARE the live toolbox; without seeding
    # them as entries they mapped dead (376 false-unused functions).
    for n in nodes:
        if n["kind"] == "module" and "/tools/" in n["file"] \
                and n["file"].endswith("scripts.py") or (
                n["kind"] == "module" and "/tools/" in n["file"]
                and "/scripts/" in n["file"]):
            entry_ids.add(n["id"])
    # The boot-registered modules: builtin_tools + web toolset + the
    # autonomy tools (all wired at athena.py boot).
    for n in nodes:
        if n["kind"] == "module" and n["id"] in (
                "core/builtin_tools.py", "web/toolset.py",
                "autonomy/tools.py", "filesystem/tools.py",
                "filesystem/safety.py"):
            entry_ids.add(n["id"])
    # THE DYNAMIC-RUNNER ENTRIES (the 08-14 accuracy fix): the doctor
    # (dynamic test loader over doctor/), knowledge (enrich cron hook),
    # data (backup/import CLI commands) + integrations (gateway
    # loaders) are all wired at boot via importlib loops the AST walker
    # can't see. Seed their modules as entries so their functions don't
    # map false-dead.
    for n in nodes:
        if n["kind"] == "module" and (
                n["file"].startswith(str(root / "doctor"))
                or n["file"].startswith(str(root / "knowledge"))
                or n["file"].startswith(str(root / "data"))
                or n["file"].startswith(str(root / "integrations"))
                or n["file"].startswith(str(root / "metrics"))
                or n["id"] == "core/custodian.py"
                or n["id"] == "core/janitor.py"
                or n["id"] == "security/integrity.py"):
            entry_ids.add(n["id"])

    # THE CONNECTION TARGETS (the Operator's 08-14 spec): every module
    # node is a PLOT ENDPOINT — it transitions to its own module's
    # graph (operations/modules/<first-component>/timeline.html). Set
    # the enters here (before classification) so the state pass marks
    # them as the 5th CONNECTION state.
    for n in nodes:
        if n["kind"] == "module":
            top = (n.get("module") or "root").split(".")[0] or "root"
            n["enters"] = f"operations/modules/{top}/timeline.html"

    # THE INIT-FILE FIX: an __init__.py module node is a CONTAINER, not
    # dead code — when its directory has alive children, the init is
    # alive too (the package is in use). Force alive before the
    # reachability classification so imports resolve to it.
    for n in nodes:
        if n["kind"] == "module" and n["id"].endswith("__init__.py"):
            n["_force_alive"] = True

    sick = sick_ids if sick_ids is not None else _sick_ids_from_metrics(root.parent)

    # THE CROSS-FILE LINK PASS (the 08-14 wiring fix + the 08-15 1:1
    # mapping): same-file calls/refs resolve in _file_nodes; this pass
    # links a function's bare-name calls AND refs (type annotations,
    # attribute reads, name loads) to their definitions in OTHER files
    # (a call to `log_event` or a `req: ChatRequest` annotation resolves
    # to whichever module defines it — best-effort, first hit).
    defn_by_name: Dict[str, str] = {}
    defn_by_prefix: Dict[str, list] = {}   # THE 08-15 RULE-4: _chk_ → [defs]
    for n in nodes:
        if n["kind"] in ("function", "method", "class"):
            nm = n["id"].rsplit(".", 1)[-1]
            defn_by_name.setdefault(nm, n["id"])
            # Index by PREFIX for the f-string dynamic-lookup case:
            # a ref to "_chk_" resolves to every _chk_* def in the file.
            for pfx in _prefixes(nm):
                defn_by_prefix.setdefault(pfx, []).append(n["id"])
    known_ids = {n["id"] for n in nodes}
    extra = []
    kept = []
    for lk in links:
        if lk.get("relation") not in ("calls", "refs"):
            kept.append(lk)
            continue
        src, tgt = lk["source"], lk["target"]
        if tgt in known_ids:
            kept.append(lk)  # already resolved same-file
            continue
        # tgt is a bare name — resolve cross-file (best-effort, first hit).
        if tgt in defn_by_name and defn_by_name[tgt] != src:
            kept.append({"source": src, "target": defn_by_name[tgt],
                         "relation": lk["relation"], "state": ALIVE})
        elif len(tgt) >= 3 and tgt in defn_by_prefix:
            # THE 08-15 RULE-4: a PREFIX ref (from an f-string like
            # f"_chk_{name}") resolves to EVERY def sharing the prefix.
            for fid in defn_by_prefix[tgt]:
                if fid != src:
                    kept.append({"source": src, "target": fid,
                                 "relation": lk["relation"], "state": ALIVE})
        # else: unresolved name (stdlib/builtin) — dropped, not noise.
    links[:] = kept
    # Also: every function's file-module node links to it (contains).
    # THE DEFINES FIX: the file node's id is the RELATIVE path
    # (core/config.py) but n["file"] is ABSOLUTE — match by basename+
    # module so containment actually lands (was 1342 missing).
    file_id_by_label = {}
    for n in nodes:
        if n["kind"] == "module":
            file_id_by_label.setdefault(n["label"], n["id"])
    for n in nodes:
        if n["kind"] in ("function", "method"):
            fid = file_id_by_label.get(Path(n["file"]).name)
            if fid and fid in known_ids:
                extra.append({"source": fid, "target": n["id"],
                              "relation": "defines", "state": ALIVE})
    links.extend(extra)

    # THE MODULE-IMPORT RESOLUTION (the 08-14 yellow-gradient fix): an
    # import edge's bare target (`guidelines`, `main`) must resolve to
    # the ACTUAL file node (`core/guidelines.py`). This makes imported
    # modules REACHABLE — they were mapped dead because their callers
    # imported them by dotted name and the resolver never linked them.
    # With the file alive, its dead children trigger the yellow
    # degradation (alive→dead divergence = SICK).
    file_by_dotted = {}
    for n in nodes:
        if n["kind"] == "module":
            # "core/guidelines.py" → "core.guidelines";
            # "core/__init__.py" → "core" (the package itself).
            rel = n["id"].replace("/", ".").removesuffix(".py")
            if n["id"].endswith("__init__.py"):
                rel = n["id"].removesuffix("/__init__.py").replace("/", ".")
            file_by_dotted.setdefault(rel, n["id"])
    resolved_imports = []
    for lk in links:
        if lk.get("relation") != "imports":
            resolved_imports.append(lk)
            continue
        src, tgt = lk["source"], lk["target"]
        # tgt is the full dotted module ("oslayer", "core.guidelines") —
        # find the file node it belongs to (exact or suffix match).
        hit = None
        for dotted, fid in file_by_dotted.items():
            if dotted == tgt or dotted.endswith(f".{tgt}"):
                hit = fid
                break
        if hit and hit != src and hit in known_ids:
            resolved_imports.append({"source": src, "target": hit,
                                     "relation": "imports", "state": ALIVE})
        else:
            # THE NAME-IMPORT RESOLUTION (the 08-15 1:1 mapping):
            # `from core.db import column_family` targets the FUNCTION
            # core.db.column_family — resolve the last segment to its
            # definition node (a function/method/class id), not just the
            # module. This is the "imported name = used" edge.
            last = tgt.rsplit(".", 1)[-1]
            if last in defn_by_name and defn_by_name[last] != src \
                    and defn_by_name[last] in known_ids:
                resolved_imports.append({"source": src,
                                         "target": defn_by_name[last],
                                         "relation": "imports",
                                         "state": ALIVE})
            # else: stdlib/bare — dropped (not noise, just no file node).
    links[:] = resolved_imports

    # THE 08-15 RULE 5: config/manifest symbol references. The system
    # wires some tools/plugins by NAME in YAML/JSON configs (tool
    # manifests, plugin.yaml, provider selection). A def whose LABEL
    # appears as a string in a config file is referenced by the runtime
    # (the loader reads the config + looks up the symbol). Scan the
    # config/manifest files the code loads; a matching def label emits a
    # refs edge from the CONFIG node (a synthetic module) to the def.
    try:
        def_label_ids: Dict[str, str] = {}
        for n in nodes:
            if n.get("kind") in ("function", "method", "class"):
                def_label_ids.setdefault(n.get("label", ""), n["id"])
        # Candidate config files (the ones the runtime loads).
        cfg_candidates = []
        cfg_roots = [root, root.parent]
        for _r in cfg_roots:
            for _p in sorted(_r.rglob("*.yaml")) + sorted(_r.rglob("*.json")):
                if "node_modules" in str(_p) or "__pycache__" in str(_p):
                    continue
                if _p.stat().st_size > 200_000:
                    continue  # skip huge generated files
                cfg_candidates.append(_p)
        cfg_hits = set()
        for _p in cfg_candidates:
            try:
                _txt = _p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for _label, _fid in def_label_ids.items():
                if len(_label) >= 3 and _label in _txt and _fid not in cfg_hits:
                    # A real def label found as a string in a config file.
                    links.append({"source": f"config:{_p.name}", "target": _fid,
                                  "relation": "refs", "state": ALIVE})
                    cfg_hits.add(_fid)
        if cfg_hits:
            nodes.append({
                "id": "config:manifests", "label": "config:manifests",
                "kind": "module", "module": "config",
                "file": str(root.parent / "config"), "line": 1,
                "pos_x": 0, "pos_y": 0, "state": ALIVE, "enters": None,
            })
            for _fid in cfg_hits:
                # THE 08-15 RULE-5 FIX: REFS (usage), not defines — the
                # config references the symbol; the usage check counts
                # refs so the def stays alive.
                links.append({"source": "config:manifests", "target": _fid,
                              "relation": "refs", "state": ALIVE})
    except Exception:
        pass  # RULE 5 is best-effort — never break the build

    # THE DANGLING-LINK SWEEP (the 08-14 audit fix): after ALL nodes +
    # links are built, drop any link whose endpoints aren't nodes. The
    # import edges to stdlib/bare module names (pathlib, sys, ...) were
    # the 1501 dangling links — they reference names that never became
    # nodes, polluting the graph with dead wires.
    final_ids = {n["id"] for n in nodes}
    links = [lk for lk in links
             if lk.get("source") in final_ids and lk.get("target") in final_ids]

    # THE LINK DEDUP (the Operator's 08-14 spec): ONE link per unique
    # path — a source→target pair with the same relation is drawn once.
    # (A→B via calls + imports is still two links — different relations;
    # but TWO identical imports edges collapse to one.)
    seen_pairs = set()
    deduped = []
    for lk in links:
        key = (lk.get("source"), lk.get("target"), lk.get("relation"))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        deduped.append(lk)
    links = deduped

    _classify_states(nodes, links, entry_ids, sick)

    # The wiring-diagram layout (rows = depth, unique columns).
    _layout_tree(nodes, links, entry_ids)

    alive = sum(1 for n in nodes if n["state"] == ALIVE)
    sickc = sum(1 for n in nodes if n["state"] == SICK)
    dead = sum(1 for n in nodes if n["state"] == DEAD)
    connc = sum(1 for n in nodes if n["state"] == CONNECTION)
    # THE WARNING COUNT (the Operator's 08-14 spec): the orange blend —
    # wires flowing from a SICK source into a DEAD target (the caution
    # degenerating into error). Shown as its own counter in the header.
    state_map = {n["id"]: n["state"] for n in nodes}
    warnings = sum(
        1 for lk in links
        if state_map.get(lk.get("source")) == SICK
        and state_map.get(lk.get("target")) == DEAD
    )

    # Strip the internal force flag before writing.
    for n in nodes:
        n.pop("_force_alive", None)

    return {
        "directed": True,
        "multigraph": False,
        "graph": {"name": "athena-system operations", "kind": "operations",
                  "states": {"alive": alive, "sick": sickc, "dead": dead,
                             "warnings": warnings, "connections": connc}},
        "nodes": nodes,
        "links": links,
        "entry_points": sorted(entry_ids),
        "summary": {"nodes": len(nodes), "links": len(links),
                    "alive": alive, "sick": sickc, "dead": dead,
                    "warnings": warnings, "connections": connc},
    }
