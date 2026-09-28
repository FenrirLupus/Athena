"""Approvals — the interactive permission store (the Operator's spec).

The CLI asks inline (input prompt). The WEB needs a shared store: when a
dangerous tool needs a prompt, the loop registers a PENDING approval,
waits for the GUI to decide, and the GUI's /approvals endpoints resolve
it. Fail-closed: no GUI decision = denied.
"""
from __future__ import annotations

import threading
import time
import uuid

_lock = threading.Lock()
_pending: dict[str, dict] = {}
_history: list[dict] = []


def request_approval(tool: str, arguments: dict, risk: str,
                     requester: str = "web", reason: str = "") -> dict:
    """Register a pending approval. Returns {id, tool, risk, status}."""
    aid = str(uuid.uuid4())
    with _lock:
        _pending[aid] = {
            "id": aid,
            "tool": tool,
            "arguments": arguments or {},
            "risk": risk,
            "reason": reason or f"risk: {risk}",
            "status": "pending",
            "created_at": time.time(),
            "requester": requester,
            "verdict": None,
            "scope": None,
        }
        return dict(_pending[aid])


def resolve_approval(aid: str, verdict: str, scope: str = "once") -> dict:
    """Decide a pending approval: allow | deny | block × once|session|global."""
    with _lock:
        entry = _pending.get(aid)
        if entry is None:
            return {"ok": False, "detail": "no such approval"}
        entry["status"] = "decided"
        entry["verdict"] = verdict
        entry["scope"] = scope
        entry["decided_at"] = time.time()
        _history.append(dict(entry))
        _pending.pop(aid, None)
        return {"ok": True, "verdict": verdict, "scope": scope}


def pending_approvals() -> list[dict]:
    with _lock:
        return [dict(v) for v in _pending.values()]


def pending_count() -> int:
    with _lock:
        return len(_pending)


def wait_for_decision(aid: str, timeout: float = 300.0) -> dict | None:
    """Block the turn until the GUI decides (or timeout → denied)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            entry = _pending.get(aid)
            if entry is None:
                # Resolved (moved to history) — find the verdict.
                for h in reversed(_history):
                    if h.get("id") == aid:
                        return h
                return {"verdict": "deny", "scope": "once"}
        time.sleep(0.5)
    # Timeout — fail closed.
    resolve_approval(aid, "deny", "once")
    return {"verdict": "deny", "scope": "once"}


def approval_history(limit: int = 50) -> list[dict]:
    with _lock:
        return list(reversed(_history[-limit:]))
