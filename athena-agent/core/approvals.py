"""Approvals — when a refusal is a question, not a verdict.

The scope guard answers yes or no. That is right for the hard lines (`.secret`, the system tree, another agent's
home: those are never negotiable) and wrong for everything else, because most out-of-scope paths are simply
*unknown to the agent*, not forbidden to the user. So the guard raises, the broker asks the person, and the turn
**waits** for the answer instead of failing or guessing.

Three ways to say yes, because "allow once" and "always allow this folder" are different decisions and conflating
them trains the user to click Allow without reading:

| scope | lives for | stored in |
|---|---|---|
| `once` | this single call | nothing |
| `session` | until the process exits | memory, per agent |
| `permanent` | until revoked | the profile's `workspaces.json` |

**A timeout is an absence, not a refusal.** After five minutes with no answer the action does not happen, and the
agent is told the user is unavailable and to resume safely — not that permission was denied. A refusal means "do not
go this way"; an absence means "carry on without this". Conflating them produces either an agent that gives up or
one that hammers the same door.

Every request carries the agent's name, the exact path, the operation, and the tool that asked — a popup that says
"an agent wants access" is a popup nobody can judge.
"""
from __future__ import annotations

import threading
import time
import uuid

from core import trace

ALLOW = "allow"
DENY = "deny"
ONCE, SESSION, PERMANENT = "once", "session", "permanent"
SCOPES = (ONCE, SESSION, PERMANENT)


class Decision:
    def __init__(self, allowed: bool, scope: str, message: str = ""):
        self.allowed = allowed
        self.scope = scope
        self.message = message

    def __repr__(self) -> str:
        return f"Decision(allowed={self.allowed}, scope={self.scope!r})"


def _refusal(exc: BaseException, **fields) -> None:
    """Trace a refusal before it is raised — the exception has no frame yet, so `where` would be `?`."""
    trace.failure("approval.refused", None, error=f"{type(exc).__name__}: {exc}", **fields)


class ApprovalBroker:
    """One broker per multiplexer: pending requests, and the grants already given."""

    def __init__(self, timeout_s: float = 300.0, on_permanent=None):
        self.timeout_s = float(timeout_s)
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._decisions: dict[str, Decision] = {}
        # {agent: [(path, flags)]} — session grants
        self._session: dict[str, list[tuple[str, frozenset]]] = {}
        self._on_permanent = on_permanent          # callable(agent, path) -> None, for the profile's store

    def _span(self) -> str:
        """The wait, in words: '300s' for minutes, '0.4s' for a test-sized timeout."""
        return f"{self.timeout_s:.0f}s" if self.timeout_s >= 10 else f"{self.timeout_s:.1f}s"

    # -- asking ------------------------------------------------------------
    def request(self, agent: str, path: str, op: str, reason: str, tool: str = "",
                flags: tuple = ()) -> Decision:
        """Ask, and block until answered or timed out. A timeout is a denial."""
        rid = uuid.uuid4().hex[:12]
        event = threading.Event()
        row = {"id": rid, "agent": agent, "path": str(path), "op": op, "tool": tool,
               "reason": reason, "flags": list(flags), "asked_at": time.time(),
               "expires_at": time.time() + self.timeout_s}
        with self._lock:
            self._pending[rid] = row
            self._events[rid] = event
        trace.event("approval.ask", channel=str(agent), id=rid, path=str(path), op=op,
                    tool=tool or "direct", timeout_s=self.timeout_s, reason=reason or "-")

        answered = event.wait(timeout=self.timeout_s)
        with self._lock:
            row = self._pending.pop(rid, row)
            decision = self._decisions.pop(rid, None)
            self._events.pop(rid, None)

        if not answered or decision is None:
            trace.failure("approval.absent", None, channel=str(agent), id=rid, path=str(path), op=op,
                          waited_s=round(self.timeout_s, 3), outcome="timeout-deny")
            # The user was not there. That is not a refusal, and the agent must not treat it as one — a refusal
            # means "do not go this way", an absence means "carry on without this". Saying which keeps the agent
            # from either giving up entirely or hammering the same door.
            return Decision(False, ONCE,
                            f"the user is not available at the moment (no answer in {self._span()}). "
                            f"Resume the task in a safe and secure manner: do not retry {op} on {path}, do not "
                            f"look for another route to it, and continue with what you can do inside your own "
                            f"scope. Mention the blocked step in your final answer so the user can grant it later")
        if decision.allowed and decision.scope in (SESSION, PERMANENT):
            self._remember(agent, str(path), decision.scope, flags)
        return decision

    def resolve(self, rid: str, decision: str = ALLOW, scope: str = ONCE) -> dict:
        """Answer a pending request. `decision` is allow|deny; `scope` is once|session|permanent."""
        decision = (decision or "").strip().lower()
        scope = (scope or ONCE).strip().lower()
        if decision not in (ALLOW, DENY):
            exc = ValueError("decision must be 'allow' or 'deny'")
            _refusal(exc, channel="approvals", id=rid, rule="bad-decision", decision=decision)
            raise exc
        if scope not in SCOPES:
            exc = ValueError(f"scope must be one of {', '.join(SCOPES)}")
            _refusal(exc, channel="approvals", id=rid, rule="bad-scope", scope=scope)
            raise exc
        if decision == DENY:
            scope = ONCE                            # a denial is never remembered
        with self._lock:
            row = self._pending.get(rid)
            event = self._events.get(rid)
            if row is None or event is None:
                exc = KeyError(rid)
                _refusal(exc, channel="approvals", id=rid, rule="not-pending", decision=decision)
                raise exc
            verdict = Decision(decision == ALLOW, scope)
            if verdict.allowed and scope == PERMANENT and self._on_permanent is not None:
                try:
                    self._on_permanent(row["agent"], row["path"])
                except Exception as exc:            # noqa: BLE001 — a failed persist must not lose the answer
                    trace.failure("approval.persist.failed", exc, channel=str(row["agent"]),
                                  path=str(row["path"]), scope=scope)
            self._decisions[rid] = verdict
        event.set()
        trace.event("approval.answer", channel=str(row["agent"]), id=rid, path=str(row["path"]),
                    op=row["op"], decision=decision, scope=scope)
        return {"id": rid, "decision": decision, "scope": scope,
                "agent": row["agent"], "path": row["path"], "op": row["op"]}

    # -- what is pending / already granted ---------------------------------
    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._pending.values()]

    def grants(self, agent: str) -> list[tuple[str, frozenset]]:
        with self._lock:
            return list(self._session.get(agent, []))

    def _remember(self, agent: str, path: str, scope: str, flags: tuple) -> None:
        """Session grants are path prefixes: allowing one file in a folder is not allowing the folder."""
        from pathlib import Path

        target = Path(path)
        prefix = target if not target.suffix else target.parent
        with self._lock:
            bucket = self._session.setdefault(agent, [])
            entry = (str(prefix), frozenset(flags or ()))
            if entry not in bucket:
                bucket.append(entry)
        trace.event("approval.grant", channel=str(agent), scope=scope, prefix=str(prefix),
                    flags=", ".join(sorted(flags or ())) or "-")

    def covers(self, agent: str, path: str, flag: str) -> bool:
        """Is this path already granted by a session approval?"""
        from pathlib import Path

        target = Path(path)
        for prefix, flags in self.grants(agent):
            if flags and flag not in flags:
                continue
            try:
                target.relative_to(prefix)
                trace.event("approval.scope", level="debug", channel=str(agent), path=str(target),
                            flag=flag, allowed=True, rule="session-grant", prefix=str(prefix))
                return True
            except ValueError:
                continue
        trace.event("approval.scope", level="debug", channel=str(agent), path=str(target),
                    flag=flag, allowed=False, rule="no-grant")
        return False

    def forget_session(self, agent: str) -> None:
        with self._lock:
            self._session.pop(agent, None)
