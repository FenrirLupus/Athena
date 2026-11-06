"""Where the Athena home lives.

The same `os.environ` expression was written into three modules, so a change to how the root is resolved had to be
made in all three or the tree disagreed with itself. The function is the single answer; callers that need a constant
bind it once at import, as they always did.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["athena_root"]


def athena_root() -> Path:
    """The Athena home: `ATHENA_ROOT` when set, otherwise `~/.athena`."""
    return Path(os.environ.get("ATHENA_ROOT") or (Path.home() / ".athena"))

