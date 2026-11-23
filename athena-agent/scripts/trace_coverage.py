"""Measure trace coverage: which runtime modules emit at least one `core.trace` site.

Coverage is a count, not a feeling: a module counts once it imports `core.trace` *and* has at least one real call
site (`trace.event(`, `trace.span(`, `trace.failure(`). A file that imports the module and never calls it is not
instrumented — that is the shape of a module somebody meant to do and did not.

Sites are counted through the AST, not a regex: an example inside a docstring (`trace.event("model.request", …)` in
the module's own documentation) is not a call and must not inflate the number.

    python3 scripts/trace_coverage.py              # the table, the total and every untraced module
    python3 scripts/trace_coverage.py --untraced   # only what is missing, one path per line

Release tooling, not runtime: it reads the tree it lives in, so it is not part of the 75 % denominator.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The runtime tree, grouped the way the tracing note reports it. `__init__.py` is a package marker, not a module.
GROUPS = [
    ("core/", ["core"]),
    ("server/", ["server"]),
    ("providers/", ["providers"]),
    ("tools/", ["tools"]),
    ("tools/system/*/scripts/", ["tools/system/*/scripts"]),
    ("skills/system/curator/scripts/", ["skills/system/curator/scripts"]),
    ("scripts/", ["scripts"]),
    ("updater/", ["updater"]),
    ("models/", ["models"]),
    ("context/", ["context"]),
    ("data/", ["data"]),
    ("config/", ["config"]),
    ("cli/", ["cli"]),
]

EMITTERS = {"event", "span", "failure"}

# The measurer is release tooling: it would otherwise be counting itself, and an untraced tool would drag the
# runtime figure down for no runtime reason.
SKIP = {"scripts/trace_coverage.py"}


def module_files(patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            for file in sorted(path.glob("*.py")):
                if file.name != "__init__.py" and file.is_file() \
                        and str(file.relative_to(ROOT)) not in SKIP:
                    found.append(file)
    return found


def _imports_trace(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "core" and any(alias.name == "trace" for alias in node.names):
                return True
            if node.module == "core.trace":
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name == "core.trace" for alias in node.names):
                return True
    return False


def count_sites(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return 0
    if not _imports_trace(tree):
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "trace"
        and node.func.attr in EMITTERS
    )


def main(argv: list[str]) -> int:
    only_missing = "--untraced" in argv
    rows = []
    untraced: list[Path] = []
    total_sites = 0
    for label, patterns in GROUPS:
        files = module_files(patterns)
        sites = {f: count_sites(f) for f in files}
        live = [f for f, n in sites.items() if n]
        untraced.extend(f for f in files if not sites[f])
        total_sites += sum(sites.values())
        rows.append((label, len(live), len(files), sum(sites.values())))

    traced = sum(r[1] for r in rows)
    total = sum(r[2] for r in rows)
    if only_missing:
        for path in untraced:
            print(path.relative_to(ROOT))
        return 0 if total else 1

    print(f"{'directory':<32}{'traced':>7}{'total':>7}{'sites':>7}")
    print("-" * 53)
    for label, live, count, sites in rows:
        print(f"{label:<32}{live:>7}{count:>7}{sites:>7}")
    print("-" * 53)
    pct = (100.0 * traced / total) if total else 0.0
    print(f"{'TOTAL':<32}{traced:>7}{total:>7}{total_sites:>7}   {pct:.0f}%")
    target = total * 0.75
    print(f"75 % target: {traced:.0f}/{target:.1f} — "
          + ("reached" if traced >= target else f"{target - traced:.1f} module(s) short"))
    print(f"untraced ({len(untraced)}):")
    for path in untraced:
        print(f"  {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
