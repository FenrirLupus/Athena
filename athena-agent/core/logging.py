"""Logging helper — every subsystem logs through this one door.

The 5-level severity model (the Operator's spec):
    1 GOOD      — everything working, session/start markers
    2 NOTICE    — minor events, routine changes, gate fires
    3 WARNING   — unexpected but recoverable; attention may be needed
    4 ERROR     — a function failed; recovery required
    5 CRITICAL  — severe failure; system compromised or can't continue

Modules call log_event(...) instead of importing metrics.logger directly.
The helper guarantees: correct level clamping, source tagging, and it
NEVER raises (logging must never break the code it instruments).
"""
from __future__ import annotations


def log_event(level: int, result: str, *, source: str = "runtime",
              tool: str = "", action: str = "", target: str = "",
              profile: str = "") -> None:
    """Log one metric entry at the given severity. Never raises.

    source should be the subsystem: server | cli | gui | runtime | nurse |
    curator | providers | autonomy | intelligence | data | security |
    context | filesystem | metrics | db | config.
    """
    try:
        from metrics.logger import log
        log(int(level), result, source=source, tool=tool or source,
            action=action, target=target, profile=profile)
    except Exception:
        pass  # logging must never break the instrumented code
