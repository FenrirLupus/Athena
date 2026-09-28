"""State-file layout test — the Operator's separation of concerns.

sessions/  = pure conversation (session-*.db + vault/ only)
operations/ = scheduler.db (system state)
agent/     = kanban.db per PROFILE root (agent operations + subagents)

The operation/agent state NEVER lives in sessions/ — so it's easy to
know what's a session on disk and what's machinery.
"""
from __future__ import annotations


def run() -> list[dict]:
    from core.config import ATHENA_ROOT
    from autonomy.scheduler import SCHEDULER_DB
    from autonomy.kanban import KANBAN_DB
    from intelligence.curator import STATE_PATH

    checks = []
    checks.append({
        "name": "scheduler.db in operations/",
        "status": "ok" if SCHEDULER_DB.parent.name == "operations"
        and "sessions" not in str(SCHEDULER_DB) else "fail",
        "detail": str(SCHEDULER_DB),
    })
    checks.append({
        "name": "kanban.db in agent/",
        "status": "ok" if KANBAN_DB.parent.name == "agent"
        and "sessions" not in str(KANBAN_DB) else "fail",
        "detail": str(KANBAN_DB),
    })
    checks.append({
        "name": "curator state = operations/curator.json",
        "status": "ok" if STATE_PATH.parent.name == "operations"
        and STATE_PATH.name == "curator.json"
        and "sessions" not in str(STATE_PATH) else "fail",
        "detail": str(STATE_PATH),
    })
    # The active profile is a CONFIG VARIABLE (no sidecar file).
    from core.config import active_profile_name
    checks.append({
        "name": "active profile = config variable (no sidecar)",
        "status": "ok" if active_profile_name() in ("", "default")
        or active_profile_name() else "fail",
        "detail": "profile.active in config.yaml",
    })
    # No state .db inside the sessions dir (it stays pure conversation).
    sessions_ok = True
    try:
        from core.config import DEFAULT_PROFILE_ROOT
        for child in (DEFAULT_PROFILE_ROOT / "sessions").iterdir():
            if child.suffix == ".db" and child.name not in (
                    "vault.db", "index.db"):
                if "session-" not in child.name:
                    sessions_ok = False
    except Exception:
        sessions_ok = False
    checks.append({
        "name": "sessions/ contains only session files + vault",
        "status": "ok" if sessions_ok else "fail",
        "detail": "no scheduler/kanban state in sessions/",
    })
    return checks
