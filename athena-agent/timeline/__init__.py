"""TIMELINE — the Timeline System (the Operator's 08-14 spec).

Athena's native architecture mapper: path-traced PIPELINE graphs, not
web diagrams. Two graph types:

  * OPERATION timelines — the code/execution spine: entry points on the
    left (START), the call chain as a tree, dead code splitting off as
    labeled DEAD END branches. X = execution order, Y = depth.

  * DISK timelines — the filesystem as a timeline: what exists, where,
    in what order the layout is created (the wipe/ensure_all lifecycle).

Each graph is TWO files (the Operator's spec):
  index.json      — the condensed database: all nodes + links + positions
                    + states + cross-graph refs (the agent's TOC)
  timeline.html   — the SVG visual (top-down, infinite, filename links)

Every node/link carries one of three STATES (the Operator's 08-14 spec):
  alive  — reachable + healthy
  sick   — reachable + recently errored (the metrics hook: L3+ entries
           in the metric logs mentioning this node)
  dead   — unreachable from any entry point (trim-able)

Storage layout (the Operator's spec):
  .athena/graphs/                    — the ROOT graphs (maps athena-system:
                                       the architecture map)
  .athena/profiles/<p>/graphs/       — each PROFILE's graphs (their own
                                       disk + projects)
"""

from pathlib import Path

# The states (the Operator's 08-14 spec): the four health tiers + the
# CONNECTION terminals — a plot endpoint that TRANSITIONS to another
# graph/file (the module nodes with an enters target — the wiring
# diagram's terminal block where the wire leaves this circuit).
ALIVE = "alive"
SICK = "sick"
DEAD = "dead"
CONNECTION = "connection"
STATES = (ALIVE, SICK, DEAD, CONNECTION)


def root_graphs_dir() -> Path:
    """The ROOT graphs dir: .athena/graphs — maps athena-system (the
    architecture map, 1:1)."""
    from core.config import ATHENA_ROOT
    d = ATHENA_ROOT / "graphs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profile_graphs_dir(profile: str = "") -> Path:
    """A PROFILE's graphs dir: profiles/<p>/graphs — their own disk +
    projects. The default profile's graphs live under profiles/.default/."""
    from core.config import ATHENA_ROOT
    from core import db as db_layer
    root = db_layer._profile_root(profile)
    d = root / "graphs"
    d.mkdir(parents=True, exist_ok=True)
    return d
