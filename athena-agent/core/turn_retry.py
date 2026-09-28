"""Turn retry state — retry the SAME provider once before falling back.

the Operator's spec: reliability without waste. A transient hiccup (a flaky
network read, a 5xx blip) should get ONE retry with a short backoff —
most such hiccups pass on the retry, so the chain's fallback is only
used for genuinely failing providers. Retry state is PER TURN: it resets
when a new user turn begins (so repeated turns don't accumulate).

Wiring: the ProviderChain._chat_model path catches transient errors and
asks this module whether to retry once. The state is bounded: at most
ONE retry per (provider, model) per turn.
"""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
# turn_id -> {(provider, model): retry_count}
_state: dict[str, dict[tuple[str, str], int]] = {}

# One retry per (provider, model) per turn; 0.8s backoff before the retry.
MAX_RETRIES = 1
BACKOFF_S = 0.8

# Transient error signals: a retry is worth it.
TRANSIENT_MARKERS = (
    "timed out", "timeout", "connection refused", "connection reset",
    "temporary failure", "5",  # 5xx server errors
    "overloaded", "rate limit", "slow down", "busy",
)


def begin_turn(turn_id: str) -> None:
    """Reset the retry state for a new turn."""
    with _lock:
        _state[turn_id] = {}


def end_turn(turn_id: str) -> None:
    """Drop the turn's retry state (bounded memory)."""
    with _lock:
        _state.pop(turn_id, None)


def is_transient(error: str) -> bool:
    """Is this error worth retrying (vs a permanent config/auth failure)?"""
    low = str(error).lower()
    return any(marker in low for marker in TRANSIENT_MARKERS)


def should_retry(turn_id: str, provider: str, model: str,
                 error: str) -> bool:
    """May we retry this (provider, model) once more this turn?"""
    if not is_transient(error):
        return False
    with _lock:
        counts = _state.setdefault(turn_id, {})
        key = (provider, model)
        if counts.get(key, 0) >= MAX_RETRIES:
            return False
        counts[key] = counts.get(key, 0) + 1
    time.sleep(BACKOFF_S)
    return True


def retry_stats() -> dict:
    """Diagnostic: current retry state per turn."""
    with _lock:
        return {"turns": len(_state),
                "retries": {tid: len(c) for tid, c in _state.items()}}
