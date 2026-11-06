"""One timestamp format for everything that records a moment.

Metrics rows, ledger lines, session messages and registry entries are read by tools that compare them as strings,
so the shape has to be identical everywhere: UTC, seconds resolution, trailing `Z`.
"""
from __future__ import annotations

import time

from core import trace

__all__ = ["stamp", "stamp_from", "FORMAT"]

FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def stamp_from(epoch: float | int) -> str:
    """Format an epoch as the project's timestamp."""
    try:
        return time.strftime(FORMAT, time.gmtime(float(epoch)))
    except Exception as exc:                       # noqa: BLE001 — traced and re-raised, never swallowed
        # the caller keeps the exception; the trail gets the *shape* of what could not be read as a moment. The
        # value itself is a raw timestamp with no decision attached to it, so only a bounded prefix of it travels.
        trace.failure("timefmt.epoch.invalid", exc, value=_shape(epoch))
        raise


def _shape(value) -> str:
    """Type and a bounded prefix of a value that would not format — enough to identify it, never all of it."""
    try:
        text = repr(value)
    except Exception:                              # noqa: BLE001 — a broken __repr__ must not replace the real error
        text = "<unrepresentable>"
    return f"{type(value).__name__}: {text[:40]}"


def stamp() -> str:
    """Format now as the project's timestamp."""
    return time.strftime(FORMAT, time.gmtime())
