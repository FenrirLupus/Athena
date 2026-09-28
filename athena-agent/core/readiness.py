"""Readiness — the lifecycle state tracker (readiness).

the Operator's spec: readiness is part of the LIFECYCLE. Every component
(server, child runtime, profile) reports WHERE it is in its lifecycle:

    STARTING → READY → SHUTTING_DOWN → STOPPED

- A child is not "up" just because its PID exists — it is READY only
  after the loop + door + heartbeat are live (the supervisor's liveness
  heartbeat ≠ readiness).
- The gateway claims ready only when its components are ready.
- During shutdown, readiness flips to SHUTTING_DOWN so the supervisor
  does NOT try to restart a component that is stopping intentionally.
"""
from __future__ import annotations

import threading
import time

STARTING = "starting"
READY = "ready"
SHUTTING_DOWN = "shutting_down"
STOPPED = "stopped"

_lock = threading.Lock()
_state: dict[str, dict] = {}


def set_state(component: str, state: str, detail: str = "") -> None:
    """Mark a component's lifecycle state."""
    with _lock:
        _state[component] = {
            "state": state,
            "detail": detail,
            "at": time.time(),
        }


def get_state(component: str) -> dict:
    with _lock:
        return dict(_state.get(component, {"state": STOPPED,
                                           "detail": "unknown",
                                           "at": 0.0}))


def is_ready(component: str) -> bool:
    return get_state(component)["state"] == READY


def is_shutting_down(component: str) -> bool:
    return get_state(component)["state"] == SHUTTING_DOWN




def status() -> dict:
    with _lock:
        return {c: dict(v) for c, v in _state.items()}
