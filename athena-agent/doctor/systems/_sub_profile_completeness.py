"""Per-profile completeness test — the Operator's p1-p4 architecture.

Every profile is a full AGENT (the hive: queen .default, workers
.nurse/.janitor/..., drones = subagents). Each agent's profile root
MUST carry its own:
    agent/     ← its kanban board (the worker's queue, drones live here)
    runtime/   ← its learning (response-length learn-by-doing)
    workspace/ ← its work dir (terminal opens here; settable via config)
    sandbox/ ← its terminal sandbox home base (settable via config)

Anything the profile owns lives in the profile root; only genuinely
shared things (plugins/tools/skills, credentials, the vault) stay
central. This lock asserts the layout + the wiring (board_path,
workspace_dir, sandbox_dir) for EVERY profile present.
"""
from __future__ import annotations

REQUIRED_DIRS = ("agent", "runtime", "workspace", "sandbox", "logs", "events")


def run() -> list[dict]:
    from intelligence.profiles import list_profiles
    from pathlib import Path

    checks = []
    # Skip transient test profiles (doctor-*) — they are created and
    # removed within other tests' runs; their partial dirs are test
    # residue, not a completeness violation.
    profiles = [p for p in list_profiles()
                if not p.name.startswith("doctor-")]
    missing_any = []

    # 1. Every profile has all per-profile dirs.
    for p in profiles:
        missing = [d for d in REQUIRED_DIRS if not (p.root / d).is_dir()]
        if missing:
            missing_any.append(f"{p.name}:{','.join(missing)}")
        checks.append({
            "name": f"layout: {p.name} has all per-profile dirs",
            "status": "ok" if not missing else "fail",
            "detail": ", ".join(f"{d}" for d in REQUIRED_DIRS) if not missing
                      else f"missing {missing}",
        })

    # 2. The queen's board is the default board path (no root agent/ file).
    try:
        from autonomy.kanban import board_path, KANBAN_DB
        queen_board = board_path("")
        checks.append({
            "name": "kanban: queen board = profiles/.default/agent",
            "status": "ok" if "profiles" in str(queen_board)
            and queen_board.parent.name == "agent" else "fail",
            "detail": str(queen_board),
        })
        # No stale root-level board: the hive lives in profile roots.
        from core.config import ATHENA_ROOT
        stale = ATHENA_ROOT / "agent" / "kanban.db"
        checks.append({
            "name": "kanban: no root-level agent/kanban.db (stale)",
            "status": "ok" if not stale.exists() else "fail",
            "detail": "root agent/ gone — boards are per-profile" if not stale.exists()
                      else str(stale),
        })
    except Exception as exc:  # noqa: BLE001
        checks.append({
            "name": "kanban: board wiring resolves",
            "status": "fail",
            "detail": str(exc),
        })

    # 3. Every profile's workspace/sandbox resolve to ITS OWN dir (and
    #    the settable override works for the queen's config).
    for p in profiles:
        try:
            ws = p.workspace_dir
            sb = p.sandbox_dir
            ws_ok = str(ws).startswith(str(p.root)) and ws.name == "workspace"
            sb_ok = str(sb).startswith(str(p.root)) and sb.name == "sandbox"
            checks.append({
                "name": f"workspace: {p.name} resolves to own workspace",
                "status": "ok" if ws_ok else "fail",
                "detail": str(ws),
            })
            checks.append({
                "name": f"sandbox: {p.name} resolves to own sandbox",
                "status": "ok" if sb_ok else "fail",
                "detail": str(sb),
            })
        except Exception as exc:  # noqa: BLE001
            checks.append({
                "name": f"workspace/sandbox: {p.name} resolves",
                "status": "fail",
                "detail": str(exc),
            })

    # 4. The learning store is per-profile: default and a named profile
    #    write DIFFERENT files.
    try:
        from core.response_length import _learn_file
        default_learn = _learn_file("default")
        named_learn = _learn_file(".nurse")
        checks.append({
            "name": "runtime: learning store is per-profile",
            "status": "ok" if default_learn != named_learn
            and "runtime" in str(default_learn) else "fail",
            "detail": f"default={default_learn} nurse={named_learn}",
        })
    except Exception as exc:  # noqa: BLE001
        checks.append({
            "name": "runtime: learning store is per-profile",
            "status": "fail",
            "detail": str(exc),
        })

    return checks