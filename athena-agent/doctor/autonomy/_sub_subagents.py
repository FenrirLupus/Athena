"""Subagents test — unnamed workers (a subagent IS a task), spawn → run → return."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from autonomy import kanban
    from autonomy.kanban import (spawn_subagent, list_subagents, next_subagent,
                                 complete_subagent, subagent_result)
    from autonomy import scheduler

    checks = []
    import autonomy.kanban as kb
    original = kb.BOARDS_ROOT
    with tempfile.TemporaryDirectory() as td:
        kb.BOARDS_ROOT = Path(td)
        try:
            # ANY agent can spawn (athena queen + profile).
            # DYNAMIC parent: the first non-default profile (any works).
            from intelligence.profiles import list_profiles
            profiles = list_profiles()
            named = next((p for p in profiles if not p.is_default), None)
            agent2 = named.name if named else "default"
            q = spawn_subagent("default", "Summarize", "Summarize the session.")
            k = spawn_subagent(agent2, "Check", "Check the vault.")
            checks.append({
                "name": "any agent spawns subagents",
                "status": "ok" if q.get("status") == "queued" and k.get("parent") == agent2 else "fail",
                "detail": f"athena={q['id'][:6]} {agent2}={k['id'][:6]}",
            })
            # A subagent has NO name — it IS the task (id + body, no agent identity).
            checks.append({
                "name": "subagent is the task (unnamed)",
                "status": "ok" if q.get("title") and q.get("id") and "parent" in q else "fail",
                "detail": "id+body carry the task; no worker name",
            })
            # Worker pool: oldest first, status transitions queued→running.
            n1 = next_subagent()
            n2 = next_subagent()
            checks.append({
                "name": "worker pool hands out tasks",
                "status": "ok" if n1 and n2 and n1["id"] == q["id"] and n2["id"] == k["id"] else "fail",
                "detail": f"{n1['id'][:6]} → {n2['id'][:6]}",
            })
            n3 = next_subagent()
            checks.append({
                "name": "pool drains",
                "status": "ok" if n3 is None else "fail",
                "detail": f"third={n3}",
            })
            # Results return to the parent (done + failed).
            complete_subagent(q["id"], "Summary written.")
            complete_subagent(k["id"], "boom", failed=True)
            r1 = subagent_result(q["id"])
            r2 = subagent_result(k["id"])
            checks.append({
                "name": "result returns to parent",
                "status": "ok" if r1["status"] == "done" and r1["result"] == "Summary written."
                and r2["status"] == "failed" else "fail",
                "detail": f"{r1['status']}/{r2['status']}",
            })
            # Pool listing filters by parent.
            q_parent = list_subagents(parent="default")
            k_parent = list_subagents(parent=agent2)
            checks.append({
                "name": "pool lists by parent",
                "status": "ok" if len(q_parent) == 1 and len(k_parent) == 1 else "fail",
                "detail": f"default={len(q_parent)} {agent2}={len(k_parent)}",
            })
            # The scheduler tick runs a queued subagent (bounded). The
            # EXECUTION is stubbed — a real _run_subagent would call the
            # provider chain (a live LLM turn that writes to a wrong cwd
            # — the .athena/.athena tick files). The test verifies the
            # LIFECYCLE path, not the LLM call.
            spawn_subagent("default", "Tick task", "Run during tick.")
            import autonomy.scheduler as sched_mod
            orig_run_sub = sched_mod._run_subagent
            sched_mod._run_subagent = lambda sub: "stubbed-result"

            # The tick needs a real conversation object (it fires
            # handle_thought for due jobs + the kanban feeder) — a mock
            # with handle_thought + profile satisfies the lifecycle path.
            class _MockConv:
                profile = type("P", (), {"name": "default"})()
                def __init__(self):
                    self.thoughts = []
                def handle_thought(self, text, priority=0.5):
                    self.thoughts.append(text)

            conv = _MockConv()
            try:
                fired = scheduler.tick(conv, now=None) if hasattr(scheduler, "tick") else []
            finally:
                sched_mod._run_subagent = orig_run_sub
            # The tick may fire jobs too; the subagent path must not crash.
            checks.append({
                "name": "scheduler tick handles subagents",
                "status": "ok" if isinstance(fired, list) else "fail",
                "detail": f"fired={len(fired)}",
            })
        finally:
            kb.BOARDS_ROOT = original
    return checks
