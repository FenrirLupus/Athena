"""Self-modification loop — the Operator's safe-change pipeline.

The nurse (repair/health) and janitor (cleanup/optimization/performance)
modify athena-system. Every self-modification follows this loop — so
Athena can heal herself AND optimize herself, and never break herself:

  1. SNAPSHOT   — zip the CURRENT athena-system (the backup of her
                  current architecture) BEFORE anything happens.
  2. DIAGNOSE   — the doctor reads what failed (nurse) / the janitor
                  scans what to clean (optimization).
  3. PLAN       — the minimal stable change, built carefully.
  4. IMPLEMENT  — apply via the todo list discipline (one change, one
                  owner, one purpose).
  5. RESTART    — restart the server/runtime to TEST the patch.
  6. VERIFY     — the doctor re-runs; green = success.
  7. ROLLBACK   — if verification FAILS, restore the snapshot (the
                  undo) and restart again.
"""
from __future__ import annotations

import time


def run_self_modification(
    *,
    agent: str,
    plan_fn,
    snapshot_version: str = "selfmod",
    restart_fn=None,
    verify_fn=None,
) -> dict:
    """Execute the full loop for one self-modification.

    plan_fn() -> dict: performs the change; returns {"ok": bool,
    "detail": str}.
    restart_fn() -> dict: restarts the server/runtime to test.
    verify_fn() -> dict: re-runs the doctor; returns {"ok": bool,
    "detail": str} (defaults to the real doctor runner).

    Returns the full loop report: {snapshot, plan, restart, verify,
    rollback, success}.
    """
    report: dict = {
        "agent": agent,
        "snapshot": None,
        "plan": None,
        "restart": None,
        "verify": None,
        "rollback": None,
        "success": False,
    }

    # 1. SNAPSHOT — before anything, the current architecture is backed up.
    try:
        from data.snapshots import snapshot
        report["snapshot"] = snapshot(version=snapshot_version)
    except Exception as exc:
        report["snapshot"] = f"failed: {exc}"
        # A failed snapshot is a HARD STOP — never modify without an undo.
        return report

    # 2+3+4. DIAGNOSE + PLAN + IMPLEMENT (the change itself).
    try:
        report["plan"] = plan_fn()
    except Exception as exc:
        report["plan"] = {"ok": False, "detail": str(exc)}

    if not (report["plan"] or {}).get("ok"):
        report["success"] = False
        # The plan failed BEFORE any code changed — nothing to roll back.
        return report

    # 5. RESTART — test the patch in a fresh runtime.
    if restart_fn is not None:
        try:
            report["restart"] = restart_fn()
        except Exception as exc:
            report["restart"] = {"ok": False, "detail": str(exc)}
    else:
        report["restart"] = {"ok": True, "detail": "no restart_fn (skipped)"}

    # 6. VERIFY — the doctor re-runs; green = success.
    if verify_fn is not None:
        try:
            report["verify"] = verify_fn()
        except Exception as exc:
            report["verify"] = {"ok": False, "detail": str(exc)}
    else:
        try:
            from doctor.run import run_all
            diag = run_all()
            report["verify"] = {
                "ok": diag["summary"].get("fail", 0) == 0,
                "detail": str(diag["summary"]),
            }
        except Exception as exc:
            report["verify"] = {"ok": False, "detail": str(exc)}

    if (report["verify"] or {}).get("ok"):
        report["success"] = True
        return report

    # 7. ROLLBACK — verification failed; restore the snapshot (the undo).
    try:
        from data.snapshots import restore, list_snapshots
        snaps = list_snapshots()
        target = snaps[0]["path"] if snaps else ""
        if target:
            report["rollback"] = restore(target, agent=agent)
            report["success"] = False
            report["verify"]["detail"] = (
                (report["verify"].get("detail") or "") +
                " → rolled back")
        else:
            report["rollback"] = {"ok": False, "detail": "no snapshot to restore"}
    except Exception as exc:
        report["rollback"] = {"ok": False, "detail": str(exc)}
    return report
