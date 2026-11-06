"""Where the Athena home lives.

The same `os.environ` expression was written into three modules, so a change to how the root is resolved had to be
made in all three or the tree disagreed with itself. The function is the single answer; callers that need a constant
bind it once at import, as they always did.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import trace

__all__ = ["athena_root"]


def athena_root() -> Path:
    """The Athena home: `ATHENA_ROOT` when set, otherwise `~/.athena`."""
    configured = os.environ.get("ATHENA_ROOT") or ""
    home = Path(configured) if configured else (Path.home() / ".athena")
    trace.event("root.resolve", level="debug", source="env" if configured else "default",
                root=str(home))
    if configured and not Path(configured).is_absolute():
        # a relative value resolves against whatever directory the process was started in, so the same
        # process can read `core/` from one tree and write `profiles/` into another
        trace.event("root.outside", level="error", rule="relative-root", value=configured,
                    resolved=str(home.resolve()))
    return home

