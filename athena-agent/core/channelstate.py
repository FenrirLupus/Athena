"""The agent state machine — explicit states, checked transitions, and a record on disk.

"State" is explicit: a value from a fixed set, persisted in the lane's record and moved only through the
transitions this module allows, so an operator question about where an agent is has a recorded answer.

    available   on disk, not in memory (it will start on its first message)
    idle        resident, nothing pending
    queued      resident, work waiting, nothing running
    working     a turn is running right now
    error       resident, the last turn failed
    sleeping    deliberately parked: it takes no work until woken
    waking      coming back: the wake checks are running right now
    removed     the profile is gone

Transitions are declared once, here, so an impossible one fails loudly instead of producing a state that
only looks wrong on a screenshot. The state is written to `profiles/<channel>/state.json` on every change:

**A state file is only as true as the process that wrote it.** `working` recovered from disk after a crash
means "a process that is no longer running was working" — so the state is reset to `idle` on load, with the
old value recorded. That reset is the whole point of persisting: the UI can say "it died mid-turn" instead of
showing a channel that has been `working` since Tuesday.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from core.jsonio import read_json
from core import durable

FILENAME = "state.json"

AVAILABLE = "available"
IDLE = "idle"
QUEUED = "queued"
WORKING = "working"
ERROR = "error"
SLEEPING = "sleeping"
WAKING = "waking"
REMOVED = "removed"

ALL = (AVAILABLE, IDLE, QUEUED, WORKING, ERROR, SLEEPING, WAKING, REMOVED)

# What may follow what. `removed` is terminal.
TRANSITIONS: dict[str, set[str]] = {
    AVAILABLE: {IDLE, WORKING, SLEEPING, REMOVED},
    IDLE: {QUEUED, WORKING, ERROR, SLEEPING, REMOVED},
    QUEUED: {WORKING, IDLE, ERROR, REMOVED},
    WORKING: {IDLE, QUEUED, ERROR, REMOVED},
    ERROR: {QUEUED, WORKING, IDLE, SLEEPING, REMOVED},
    # waking is its own state, not a flicker between two lines of code: the wake runs three checks, and a
    # transition that does work should be observable (and persisted through a crash mid-check)
    SLEEPING: {WAKING, REMOVED},
    WAKING: {IDLE, ERROR, REMOVED},
    REMOVED: set(),
}

# states that belong to a live process; finding one on disk means the writer is gone
    # `waking` is in flight too: a check interrupted by a crash leaves a record that describes no live process
_IN_FLIGHT = (WORKING, QUEUED, WAKING)


def can_move(before: str, after: str) -> bool:
    return after in TRANSITIONS.get(before, set())


def path(profile_root: Path) -> Path:
    return Path(profile_root) / FILENAME


def read(profile_root: Path) -> dict:
    """The last recorded state, or {} when there is none (a profile that has never run)."""
    data = read_json(path(profile_root), {})
    return data if isinstance(data, dict) else {}


def write(profile_root: Path, state: str, **extra) -> dict:
    """Record a state atomically. Never raises for a missing directory — the caller is mid-transition."""
    record = {"state": state, "since": time.time(), "pid": os.getpid(), **extra}
    target = path(profile_root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        durable.write_state(target, json.dumps(record, indent=2))
    except OSError:
        pass
    return record


def recover(profile_root: Path) -> tuple[str, str]:
    """(state to adopt, state recovered from). A stale in-flight state becomes `idle`, not `working`."""
    record = read(profile_root)
    previous = str(record.get("state") or "")
    if previous in _IN_FLIGHT:
        write(profile_root, IDLE, recovered_from=previous)
        return IDLE, previous
    if previous in (IDLE, ERROR, SLEEPING):
        return previous, ""
    return AVAILABLE, previous
