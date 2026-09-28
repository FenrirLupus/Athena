"""The Server Loop — Athena's forever heart.

Runs 24/7. Free by default: the tick is pure state checking, zero provider
calls. Every edge is a gate. Provider calls happen only when a gate says a
scenario applies.

The loop never *decides to think* by itself — it *justifies* thinking
against the thinking budget, and the budget says no by default (fail-closed).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import load_config


@dataclass
class ThinkingBudget:
    """The valve on autonomous provider spend."""

    max_calls_per_hour: int = 10
    min_priority: float = 0.5
    cooldown_s: float = 60.0
    fail_closed: bool = True

    calls_this_hour: int = 0
    hour_started: float = field(default_factory=time.time)
    last_call_at: float = 0.0

    def allow(self, priority: float = 0.0) -> bool:
        now = time.time()
        # Reset the hourly counter when the hour rolls over.
        if now - self.hour_started >= 3600:
            self.calls_this_hour = 0
            self.hour_started = now
        # Fail closed: with no budget configured, thinking is never allowed.
        if self.fail_closed and self.max_calls_per_hour <= 0:
            return False
        if self.calls_this_hour >= self.max_calls_per_hour:
            return False
        if priority < self.min_priority:
            return False
        if now - self.last_call_at < self.cooldown_s:
            return False
        return True

    def spend(self) -> None:
        self.calls_this_hour += 1
        self.last_call_at = time.time()


class ServerLoop:
    """The forever loop: tick -> check gates -> fire runtime or sleep."""

    def __init__(self, runtime=None, config: Optional[dict] = None):
        cfg = config or load_config()
        self.tick_interval = float(cfg.get("server", {}).get("tick_interval_s", 60))
        budget_cfg = cfg.get("thinking_budget", {})
        self.budget = ThinkingBudget(**budget_cfg)
        self.runtime = runtime  # the Message Loop / runtime, set by the owner
        self.running = False
        self.ticks = 0
        self._last_scheduled = []
        # THE MAINTENANCE GATE (the Operator's 08-14 doctrine): provider
        # calls for upkeep (the nurse) are OFFSET — the FIRST call waits
        # 1h after boot, then at most once every 2h. The provider is for
        # conversation + tools/skills/workflows, never boot maintenance.
        self._boot_ts = time.time()
        self._last_nurse_ts = 0.0
        self._maintenance_first_s = float(
            cfg.get("autonomy", {}).get("nurse_first_delay_s", 3600))
        self._maintenance_interval_s = float(
            cfg.get("autonomy", {}).get("nurse_interval_s", 7200))
        # The standing services (doctor-weekly, curator-daily, daily-restart)
        # are guaranteed on every boot — idempotent, never duplicated.
        try:
            from autonomy.scheduler import ensure_services
            self.services_started = ensure_services()
        except Exception:
            self.services_started = []

    def check_gates(self) -> list:
        """Evaluate the gates. Returns the list of fires to trigger."""
        fires = []
        if self.runtime is not None:
            # Gate: a message is waiting (the runtime exposes pending events).
            if self.runtime.has_pending():
                fires.append(("message", None))
            # Gate: something changed / thinking budget allows autonomous thought.
            if self.runtime.has_signal() and self.budget.allow(
                priority=self.runtime.signal_priority()
            ):
                fires.append(("think", None))
        return fires

    def tick_once(self) -> list:
        """One tick. Returns what it fired (empty = slept, zero cost)."""
        self.ticks += 1
        # SUPERVISOR: the always-on parent sweeps EVERY tick — dead
        # profile runtimes get auto-restarted within ~a minute (the Operator's
        # near-realtime crash recovery; the child heartbeats every 10s).
        try:
            from core.supervisor import supervise
            result = supervise(recover=True)
            if result.get("restarted"):
                from metrics.logger import log
                log(2, f"supervisor recovered: {result['restarted']}",
                    profile="default", source="server")
        except Exception:
            pass  # supervision must never break the loop
        # AUTONOMY: the scheduler feeds due jobs into the think queue.
        try:
            from autonomy.scheduler import tick as scheduler_tick
            fired = scheduler_tick(self.runtime)
            if fired:
                self._last_scheduled = fired
        except Exception as exc:
            from metrics.logger import log
            log(4, f"scheduler tick failed: {exc}", source="server")
        # THE DYNAMIC-COST PASS (the Operator's 08-12 spec): the parent
        # manages its children's states each tick — idle workers
        # hibernate, long-hibernated workers sleep (the queen is exempt).
        try:
            from core.supervisor import manage_states
            cost = manage_states()
            if cost.get("hibernated") or cost.get("slept"):
                from metrics.logger import log
                log(2, f"dynamic cost: hibernated {cost['hibernated']}, "
                       f"slept {cost['slept']}",
                    profile="default", source="server")
        except Exception:
            pass  # state management must never break the loop
        fires = self.check_gates()

        # THE NURSE'S WATCH: check changed metric logs (free when healthy).
        # Only levels 3/4/5 get attention — and only then does it cost.
        # THE MAINTENANCE GATE (the Operator's 08-14 doctrine): the nurse
        # is MAINTENANCE — provider calls for upkeep are OFFSET so a fresh
        # boot never spams the provider. The first nurse call waits
        # MAINTENANCE_FIRST_DELAY (1h) after boot; then at most once every
        # MAINTENANCE_INTERVAL (2h). The provider is for conversation +
        # tools/skills/workflows — never boot-time maintenance.
        try:
            from metrics.nurse_watch import check_logs
            watch = check_logs()
            _now = time.time()
            _first_ok = (_now - getattr(self, "_boot_ts", _now)
                         >= self._maintenance_first_s)
            _interval_ok = (_now - getattr(self, "_last_nurse_ts", 0)
                            >= self._maintenance_interval_s)
            if (not watch["ok"] and self.runtime is not None
                    and _first_ok and _interval_ok):
                # Attention needed — the DOCTOR's call lands in the
                # NURSE's own session (System side: "hey look, there are
                # issues"), then the nurse (as the Assistant) is asked to
                # diagnose + repair — never the caller's session.
                summary = "; ".join(
                    f"{a['file'].rsplit('/', 1)[-1]} L{a['max_level']}"
                    for a in watch["attention"][:5]
                )
                try:
                    from doctor.nurse import nurse_talk
                    nurse_talk(
                        f"[doctor] log attention needed: {summary} — "
                        f"investigate levels 3/4/5",
                        side="user")
                    self._last_nurse_ts = time.time()
                except Exception:
                    pass
                # The attention is RECORDED in the nurse's own session
                # (nurse_talk above) AND queued as a kanban task for the
                # nurse agent (her queue — the scheduler's nurse path
                # runs consult on it). Firing a thought on THIS runtime
                # would persist a duplicate message into the DEFAULT
                # profile's session — the cross-profile bleed the Operator
                # caught. The nurse owns the attention; this runtime
                # only records the call.
                try:
                    from autonomy.kanban import add_task
                    from doctor.nurse import NURSE_AGENT
                    add_task(
                        f"Log attention needed (L3/4/5): {summary}",
                        body="Investigate the flagged levels and repair.",
                        assignee=NURSE_AGENT)
                except Exception:
                    pass
                fires.append(("think", None))
        except Exception:
            pass  # the watch must never break the loop
        return fires

    def run_forever(self, stop_event=None) -> None:
        """Run until stopped. The 24/7 loop.

        owns_session: when True (standalone server), the loop closes the
        metric/event session in its finally. When False (running beside
        the CLI, which owns the session), it does NOT close — so a
        session maps to exactly ONE metric log + ONE event log.
        """
        from metrics.logger import log, close_session

        self.running = True
        profile = getattr(getattr(self.runtime, "profile", None), "name", "default")
        log(1, "server session started", profile=profile, source="server")
        try:
            while self.running:
                if stop_event is not None and stop_event.is_set():
                    break
                # THE HIBERNATE PAUSE (the Operator's 08-12 dynamic-cost
                # spec): when the parent parks this child (state =
                # hibernate), the tick is PAUSED — no work, no provider
                # calls, minimal resource use. The heartbeat continues
                # (the child stays alive + liveness-visible); the parent
                # wakes it by flipping the state to wake.
                try:
                    from core.supervisor import runtime_status
                    st = runtime_status(profile)
                    if st.get("state") == "hibernate":
                        time.sleep(2.0)
                        continue
                except Exception:
                    pass
                fires = self.tick_once()
                for kind, _payload in fires:
                    if self.runtime is not None:
                        self.runtime.fire(kind)
                        log(2, f"fired gate: {kind}", profile=profile, source="server")
                time.sleep(self.tick_interval)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            log(4, f"server loop crashed: {exc}", profile=profile, source="server")
        finally:
            # Only the owner writes the server-end entry. In CLI mode the
            # CLI writes it (owns_session=False) to avoid duplicates.
            if getattr(self, "owns_session", True):
                log(1, "server session ended", profile=profile, source="server")
                close_session(profile)
            self.running = False

    def stop(self) -> None:
        self.running = False
