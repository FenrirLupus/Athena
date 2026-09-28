"""Delegation test — the queen-bee model (Athena > profiles > peers)."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from autonomy import kanban, scheduler

    checks = []
    # isolate kanban db
    import autonomy.kanban as kb
    original = kb._db_path if hasattr(kb, "_db_path") else None
    with tempfile.TemporaryDirectory() as td:
        try:
            if hasattr(kb, "_DB_PATH"):
                kb._DB_PATH = Path(td) / "kb.db"
            # Queen (default/athena) delegates → top priority.
            # DYNAMIC assignees: any profile names work (the test must
            # not depend on specific profiles existing).
            from intelligence.profiles import list_profiles
            profiles = list_profiles()
            named = [p for p in profiles if not p.is_default]
            target_a = named[0].name if named else "default"
            target_b = named[1].name if len(named) > 1 else "default"
            q = kanban.delegate("Queen task", target_a, created_by="default")
            checks.append({
                "name": "queen delegate top priority",
                "status": "ok" if q.get("priority") == 10 else "fail",
                "detail": f"priority={q.get('priority')} → {target_a}",
            })
            # Peer asks → lower priority.
            p = kanban.delegate("Peer help", target_b,
                                created_by=target_a, priority=5)
            checks.append({
                "name": "peer request lower priority",
                "status": "ok" if p.get("priority") == 5 else "fail",
                "detail": f"priority={p.get('priority')}",
            })
            # The scheduler hint tells the agent WHO asked.
            hint = scheduler._delegation_hint(q)
            checks.append({
                "name": "queen hint surfaced",
                "status": "ok" if "Athena (the administrator)" in hint else "fail",
                "detail": hint,
            })
            hint = scheduler._delegation_hint(p)
            checks.append({
                "name": "peer hint surfaced",
                "status": "ok" if "fellow agent" in hint else "fail",
                "detail": hint,
            })
            # Queue ordering: the queen task comes first for target_a.
            work = kanban.open_work_for(target_a)
            checks.append({
                "name": "queen work first in queue",
                "status": "ok" if work and work[0]["id"] == q["id"] else "fail",
                "detail": f"first={work[0]['title'] if work else 'none'}",
            })
            kanban.delete_task(q["id"]); kanban.delete_task(p["id"])
        finally:
            if original is not None:
                kb._DB_PATH = original
    return checks
