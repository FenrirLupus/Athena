"""Loop guardrails — per-turn runaway-loop protection (adapted).

The Operator's audit: Athena had intent guards (what a call may DO) but not
loop guards (when the AGENT is stuck in a loop). This module
tracks, per turn:

  • EXACT-FAILURE  — the same tool call (identical args) failed N times
                     → warn first, then BLOCK ("change strategy").
  • SAME-TOOL      — a tool failed N times this turn (any args) → halt.
  • NO-PROGRESS    — an idempotent (read-only) tool returned the SAME
                     result N times → warn, then BLOCK.
  • LOOP CAPS      — hard per-turn ceilings: max web searches, max
                     subagents spawned. Block BEFORE the call runs.

Tiers:
  • warn      — a nudge appended to the result; never blocks.
  • block     — the call is refused; the model gets a synthetic result
                explaining why (so it changes strategy, not retries).
  • halt      — the turn's tool phase is stopped (circuit breaker).

Counters reset every turn. This is the SAFETY layer on top of the
permission gate: permissions decide WHO, loop guardrails decide when
the AGENT is spinning.
"""
from __future__ import annotations

import hashlib
import json

# Tools that may be repeated (read-only) — the no-progress check applies.
IDEMPOTENT_TOOLS = {
    "read", "list", "tree", "find", "search", "exists", "stat",
    "vault_query", "web_search", "web_extract", "memory_list", "logs",
}
# Tools that mutate — never no-progress-checked, always tracked for failure.
MUTATING_TOOLS = {
    "write", "append", "replace", "patch", "delete", "copy", "move",
    "rename", "mkdir", "execute", "terminal", "vault_store", "memory_add",
}


def _signature(tool_name: str, args: dict | None) -> str:
    """A stable signature of the tool call (name + canonical args)."""
    try:
        blob = json.dumps(args or {}, sort_keys=True)
    except Exception:
        blob = str(args)
    return hashlib.sha256(f"{tool_name}|{blob}".encode("utf-8")).hexdigest()[:16]


def _hash_result(result: str | None) -> str:
    return hashlib.sha256(str(result or "").encode("utf-8")).hexdigest()[:16]


def classify_failure(tool_name: str, result: str | None) -> bool:
    """True when a tool result looks like a failure (the classifier)."""
    text = str(result or "").strip().lower()
    if not text:
        return False
    for marker in ("error:", "error ", "[denied", "failed:", "traceback",
                   "exception", "exit code: 1", "exit_code: 1", "refused",
                   "blocked", "timed out", "timeout"):
        if marker in text:
            return True
    return False


class LoopGuardrails:
    """Per-turn controller. Call reset_for_turn() at the start of a turn,
    before_call() before each tool call, after_call() after each."""

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        warn = cfg.get("warn_after", {}) or {}
        stop = cfg.get("hard_stop_after", {}) or {}
        caps = cfg.get("loop_caps", {}) or {}
        self.warnings_enabled = bool(cfg.get("warnings_enabled", True))
        self.hard_stop_enabled = bool(cfg.get("hard_stop_enabled", False))
        self.exact_failure_warn_after = int(warn.get("exact_failure", 2))
        self.same_tool_failure_warn_after = int(warn.get("same_tool_failure", 3))
        self.no_progress_warn_after = int(warn.get("idempotent_no_progress", 3))
        self.exact_failure_block_after = int(stop.get("exact_failure", 5))
        self.same_tool_failure_halt_after = int(stop.get("same_tool_failure", 8))
        self.no_progress_block_after = int(stop.get("idempotent_no_progress", 8))
        self.max_web_searches = int(caps.get("max_web_searches", 50))
        self.max_subagents = int(caps.get("max_subagents", 50))
        # THE 08-15 TEST-MODE FLAG (the Operator's fix): the DOCTOR's
        # self-tests run the loop with repeated identical reads/writes —
        # they legitimately trip the guardrails, but the L3 "loop guard
        # block" spam pollutes the log. In test mode the blocks still
        # return their decisions (the tests exercise the logic) but the
        # log level drops to INFO (1) — no L3 noise.
        self.test_mode = False
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact: dict[str, int] = {}
        self._same: dict[str, int] = {}
        self._no_progress: dict[str, tuple[str, int]] = {}
        self._web_searches = 0
        self._subagents = 0
        self.halt_reason = ""

    def before_call(self, tool_name: str, args: dict | None) -> dict | None:
        """Return a block decision, or None to allow. Runs BEFORE the call."""
        # LOOP CAPS — hard ceilings (block regardless of hard_stop).
        if tool_name == "web_search" and self.max_web_searches:
            if self._web_searches >= self.max_web_searches:
                return self._decision(
                    "block", "loop_web_search_cap",
                    f"Blocked web_search: this turn already made "
                    f"{self.max_web_searches} searches. Work with the results "
                    f"you have and answer.")
            self._web_searches += 1
        if tool_name == "delegate_task" and self.max_subagents:
            spawns = self._spawn_count(args)
            if self._subagents >= self.max_subagents:
                return self._decision(
                    "block", "loop_subagent_cap",
                    f"Blocked delegate_task: this turn already spawned "
                    f"{self._subagents} subagents (limit {self.max_subagents}). "
                    f"Finish with the results you have.")
            self._subagents += spawns

        if not self.hard_stop_enabled:
            return None

        sig = _signature(tool_name, args)
        exact = self._exact.get(sig, 0)
        # THE READ-ONLY EXEMPTION (the 08-14 fix): idempotent/read-only
        # tools are governed by the NO-PROGRESS check below — repeated
        # identical READS are legitimate monitoring (the doctor checking
        # the same file, the scheduler polling). The exact-FAILURE block
        # applies only to tools that should never repeat identically.
        if exact >= self.exact_failure_block_after \
                and tool_name not in IDEMPOTENT_TOOLS:
            self._log(3, f"loop guard block: {tool_name} exact-failure "
                         f"x{exact} (identical args)")
            return self._decision(
                "block", "repeated_exact_failure_block",
                f"Blocked {tool_name}: the same call failed {exact} times with "
                f"identical arguments. Stop retrying it unchanged; change "
                f"strategy or explain the blocker.")
        if tool_name in IDEMPOTENT_TOOLS:
            rec = self._no_progress.get(sig)
            if rec is not None and rec[1] >= self.no_progress_block_after:
                self._log(3, f"loop guard block: {tool_name} no-progress "
                             f"x{rec[1]}")
                return self._decision(
                    "block", "idempotent_no_progress_block",
                    f"Blocked {tool_name}: this read-only call returned the "
                    f"same result {rec[1]} times. Use the result you already "
                    f"have or try a different query.")
        return None

    def after_call(self, tool_name: str, args: dict | None, result: str | None,
                   *, failed: bool | None = None) -> dict | None:
        """Record the outcome; return a warn decision, or None."""
        if failed is None:
            failed = classify_failure(tool_name, result)
        sig = _signature(tool_name, args)

        if failed:
            self._no_progress.pop(sig, None)
            exact = self._exact.get(sig, 0) + 1
            self._exact[sig] = exact
            same = self._same.get(tool_name, 0) + 1
            self._same[tool_name] = same

            if self.hard_stop_enabled \
                    and same >= self.same_tool_failure_halt_after:
                self.halt_reason = (
                    f"Stopped {tool_name}: it failed {same} times this turn. "
                    f"Choose a different approach.")
                return self._decision("halt", "same_tool_failure_halt",
                                      self.halt_reason)
            if self.warnings_enabled \
                    and exact >= self.exact_failure_warn_after:
                return self._decision(
                    "warn", "repeated_exact_failure_warning",
                    f"{tool_name} failed {exact} times with identical "
                    f"arguments. This looks like a loop; change strategy.")
            if self.warnings_enabled \
                    and same >= self.same_tool_failure_warn_after:
                return self._decision(
                    "warn", "same_tool_failure_warning",
                    f"{tool_name} failed {same} times this turn. "
                    f"Try a different approach.")
            return None

        # Success: clear the failure counters.
        self._exact.pop(sig, None)
        self._same.pop(tool_name, None)

        if tool_name not in IDEMPOTENT_TOOLS:
            self._no_progress.pop(sig, None)
            return None

        h = _hash_result(result)
        prev = self._no_progress.get(sig)
        repeat = 1
        if prev is not None and prev[0] == h:
            repeat = prev[1] + 1
        self._no_progress[sig] = (h, repeat)
        if self.warnings_enabled and repeat >= self.no_progress_warn_after:
            return self._decision(
                "warn", "idempotent_no_progress_warning",
                f"{tool_name} returned the same result {repeat} times. Use "
                f"the result you already have or change the query.")
        return None

    def synthetic_result(self, decision: dict) -> str:
        """The clean message the model sees when a call is blocked."""
        return json.dumps({"error": decision.get("message", "blocked"),
                           "guardrail": decision.get("code", "block")},
                          ensure_ascii=False)

    def _log(self, level: int, msg: str) -> None:
        """Loop guardrails are operational — blocks/halts are logged."""
        try:
            from metrics.logger import log
            # THE 08-15 TEST-MODE: the doctor's self-tests trip the
            # guardrails on purpose — log at INFO (1) so the L3 noise
            # vanishes while the tests still exercise the blocks.
            if self.test_mode and level >= 3:
                level = 1
            log(level, msg, source="guardrails")
        except Exception:
            pass

    @staticmethod
    def _spawn_count(args: dict | None) -> int:
        try:
            tasks = (args or {}).get("tasks") or []
            return max(1, len(tasks) if isinstance(tasks, list) else 1)
        except Exception:
            return 1

    @staticmethod
    def _decision(action: str, code: str, message: str) -> dict:
        return {"action": action, "code": code, "message": message}
