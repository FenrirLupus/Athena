"""One timestamp format for everything that records a moment.

Metrics rows, ledger lines, session messages and registry entries are read by tools that compare them as strings,
so the shape has to be identical everywhere: UTC, seconds resolution, trailing `Z`.
"""
from __future__ import annotations

import calendar
import time

from core import trace

__all__ = ["stamp", "stamp_from", "parse", "FORMAT"]

FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse(stamp: str) -> float:
    """Read the project's timestamp back into an epoch (UTC), or raise `ValueError`.

    `calendar.timegm`, not `time.mktime`: the stamp is UTC, and mktime would read it as local time — the same
    shift that once moved a snapshot's collision bump by the timezone offset. A reader that silently returns
    the wrong hour is worse than one that refuses, so an unknown shape raises.
    """
    text = str(stamp or "").strip()
    try:
        return float(calendar.timegm(time.strptime(text, FORMAT)))
    except Exception as exc:                       # noqa: BLE001 — one refusal, one message
        raise ValueError(f"not a {FORMAT} timestamp: {text[:32]!r}") from exc


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
