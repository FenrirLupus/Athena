"""Iteration budget — turns and dollars per turn, thread-safe.

Two ceilings because they fail differently: a turn count bounds looping, a dollar amount bounds
spend. Adapted from the reference shape (documentation/architecture/03-budget-and-loop.md) with
that document's traps fixed:

  * a spawned child budget is DEDUCTED from the parent, so it cannot multiply
  * refunds are capped per turn, so a no-op tool cannot spin forever
"""
from __future__ import annotations

import threading


class BudgetExhausted(RuntimeError):
    """Raised when a turn asks for a call it is not allowed to make."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class IterationBudget:
    def __init__(self, max_turns: int = 100, max_usd: float = 0.50,
                 grace_calls: int = 1, max_refunds: int = 3):
        self.max_turns = int(max_turns)
        self.max_usd = float(max_usd)
        self.grace_calls = int(grace_calls)
        self.max_refunds = int(max_refunds)
        self.turns = 0
        self.cost_usd = 0.0
        self.refunds = 0
        self.grace_used = 0
        self._lock = threading.Lock()

    # -- spending ----------------------------------------------------------
    def consume_turn(self) -> bool:
        """Take one call from the budget. Returns False when it is gone."""
        with self._lock:
            if self.turns < self.max_turns and self.cost_usd < self.max_usd:
                self.turns += 1
                return True
            if self.grace_used < self.grace_calls:
                self.grace_used += 1
                self.turns += 1
                return True
            return False

    def record_cost(self, usd: float) -> None:
        with self._lock:
            self.cost_usd += max(0.0, float(usd or 0.0))

    def refund(self) -> bool:
        """Give one call back — for a tool that did nothing. Capped, so it cannot spin."""
        with self._lock:
            if self.refunds >= self.max_refunds or self.turns <= 0:
                return False
            self.refunds += 1
            self.turns -= 1
            return True

    # -- inspection --------------------------------------------------------
    def exhausted(self) -> bool:
        with self._lock:
            over_turns = self.turns >= self.max_turns and self.grace_used >= self.grace_calls
            over_cost = self.cost_usd >= self.max_usd
            return over_turns or over_cost

    def exhausted_reason(self) -> str:
        with self._lock:
            if self.cost_usd >= self.max_usd:
                return f"exhausted_cost ({self.cost_usd:.4f}/{self.max_usd:.2f} USD)"
            if self.turns >= self.max_turns:
                return f"exhausted_turns ({self.turns}/{self.max_turns})"
            return ""

    def spawn_child(self, allocation: int = 15, usd: float | None = None) -> "IterationBudget":
        """Create a child budget, DEDUCTING its allowance from this one."""
        with self._lock:
            remaining = max(0, self.max_turns - self.turns)
            grant = min(int(allocation), remaining)
            self.turns += grant                      # the carve-out is spent, not copied
            child_usd = self.max_usd - self.cost_usd if usd is None else float(usd)
        return IterationBudget(max_turns=max(grant, 1), max_usd=max(child_usd, 0.0),
                              grace_calls=1, max_refunds=1)

    def remaining_usd(self) -> float:
        with self._lock:
            return max(0.0, float(self.max_usd) - float(self.cost_usd))

    def snapshot(self) -> dict:
        with self._lock:
            return {"turns": self.turns, "max_turns": self.max_turns,
                    "cost_usd": round(self.cost_usd, 6), "max_usd": self.max_usd,
                    "refunds": self.refunds, "grace_used": self.grace_used}


# The turn that is running, per lane. A subagent's allowance has to come out of *something*, and the only budget in
# scope when a tool calls `spawn_subagent` is the parent's — the tool context carries a scope, not a budget object.
# `run_turn` registers here for its duration; `core/subagents.spawn` carves from it.
_ACTIVE_RUNS: dict[str, "IterationBudget"] = {}
_ACTIVE_LOCK = __import__("threading").Lock()


def enter(lane: str, budget: "IterationBudget") -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_RUNS[str(lane)] = budget


def leave(lane: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_RUNS.pop(str(lane), None)


def active(lane: str) -> "IterationBudget | None":
    with _ACTIVE_LOCK:
        return _ACTIVE_RUNS.get(str(lane))

