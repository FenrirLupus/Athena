"""One timestamp format for everything that records a moment.

Metrics rows, ledger lines, session messages and registry entries are read by tools that compare them as strings,
so the shape has to be identical everywhere: UTC, seconds resolution, trailing `Z`.
"""
from __future__ import annotations

import time

__all__ = ["stamp", "stamp_from", "FORMAT"]

FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def stamp_from(epoch: float | int) -> str:
    """Format an epoch as the project's timestamp."""
    return time.strftime(FORMAT, time.gmtime(float(epoch)))


def stamp() -> str:
    """Format now as the project's timestamp."""
    return time.strftime(FORMAT, time.gmtime())
