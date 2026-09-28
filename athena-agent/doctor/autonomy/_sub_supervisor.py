"""Supervisor test — the Operator's server-as-parent architecture.

The server (parent) supervises profile runtimes: each non-default
profile runs as its OWN child process (child runtime), with a
loopback door for the parent's gateway routing, a heartbeat for
liveness, and auto-restart on crash. The default profile is embedded
(the admin, always on).
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    from core.supervisor import (RUNTIMES_STATE, HEARTBEAT_DIR,
                                 HEARTBEAT_TTL_S, HEARTBEAT_INTERVAL_S,
                                 _heartbeat_alive, _touch_heartbeat,
                                 start_runtime, stop_runtime, restart_runtime,
                                 list_runtimes, check_heartbeats, supervise)
    from core.loopback_door import door_port, child_alive
    import core.supervisor as sup
    import core.loopback_door as door

    checks = []

    # 1. The state file lives in operations/ (the Operator's state home).
    checks.append({
        "name": "runtimes state in operations/",
        "status": "ok" if RUNTIMES_STATE.parent.name == "operations"
        else "fail",
        "detail": str(RUNTIMES_STATE),
    })

    # 1b. Near-realtime pacing (the Operator's spec): 10s write, 30s TTL —
    #     write 3× faster than the TTL (5s–60s applicable range).
    checks.append({
        "name": "heartbeat near-realtime pacing (10s/30s)",
        "status": "ok" if 5 <= HEARTBEAT_INTERVAL_S <= 60
        and HEARTBEAT_TTL_S >= 3 * HEARTBEAT_INTERVAL_S - 1
        else "fail",
        "detail": f"write={HEARTBEAT_INTERVAL_S}s ttl={HEARTBEAT_TTL_S}s",
    })

    # 2. The loopback door gives a stable per-profile port (84xx).
    p1, p2 = door_port("probe-a"), door_port("probe-b")
    checks.append({
        "name": "door ports stable + distinct",
        "status": "ok" if p1 != p2 and 8400 <= p1 < 8500
        and 8400 <= p2 < 8500 else "fail",
        "detail": f"{p1} / {p2}",
    })

    # 3. Heartbeat lifecycle: touch → alive; aged → dead.
    orig_dir = sup.HEARTBEAT_DIR
    with tempfile.TemporaryDirectory() as td:
        sup.HEARTBEAT_DIR = Path(td)
        _touch_heartbeat("probe-x")
        alive_now = _heartbeat_alive("probe-x")
        checks.append({
            "name": "heartbeat alive right after touch",
            "status": "ok" if alive_now else "fail",
            "detail": str(alive_now),
        })

        # 4. The supervisor sweep: a stale heartbeat → dead.
        state = sup._load_state()
        state["runtimes"]["probe-stale"] = {
            "pid": None, "started_at": 0, "status": "running", "restarts": 0}
        sup._save_state(state)
        (sup.HEARTBEAT_DIR / "probe-stale.heartbeat").write_text(
            '{"at": 0, "profile": "probe-stale"}', encoding="utf-8")
        dead = check_heartbeats()["dead"]
        checks.append({
            "name": "supervisor detects stale heartbeat as dead",
            "status": "ok" if "probe-stale" in dead else "fail",
            "detail": f"dead={dead}",
        })
        state = sup._load_state()
        state["runtimes"].pop("probe-stale", None)
        sup._save_state(state)
    sup.HEARTBEAT_DIR = orig_dir

    # 5. The default profile is the EMBEDDED admin (always on, no child).
    from intelligence.profiles import current_profile
    checks.append({
        "name": "default profile = embedded admin",
        "status": "ok" if current_profile().name == ".default" else "fail",
        "detail": "server owns the default runtime natively",
    })
    return checks
