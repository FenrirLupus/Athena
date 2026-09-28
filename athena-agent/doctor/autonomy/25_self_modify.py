"""Self-modification test — the nurse/janitor SNAPSHOT-FIRST loop.

The Operator's self-modification spec: BEFORE any live change, a snapshot is
taken; if the verify step fails, the change rolls back from that
snapshot. This test verifies the loop's paths at the MODULE level (no
real restarts, no real repairs).
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path


def run() -> list[dict]:
    import data.snapshots as snap

    checks = []

    # Isolate the snapshot dir so tests never touch real backups.
    # TemporaryDirectory AUTO-CLEANS on exit (mkdtemp leaked every test's
    # tempdir into /tmp — the 08-12 tempdir flood).
    import tempfile as _tmpf
    with _tmpf.TemporaryDirectory() as _td:
        orig_dir = snap.SNAPSHOT_DIR
        snap.SNAPSHOT_DIR = Path(_td) / "snapshots"
        snap.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        from doctor.nurse.self_modify import run_self_modification

        # 1. The success path: snapshot → plan → restart → verify green.
        r1 = run_self_modification(
            agent="nurse",
            plan_fn=lambda: {"ok": True, "detail": "fix"},
            restart_fn=lambda: {"ok": True, "detail": "restarted"},
            verify_fn=lambda: {"ok": True, "detail": "green"})
        checks.append({
            "name": "self-modify: success path (snapshot+plan+verify)",
            "status": "ok" if r1["success"] and r1["snapshot"]
            and r1["verify"]["ok"] else "fail",
            "detail": f"success={r1['success']} snapshot={bool(r1['snapshot'])}",
        })

        time.sleep(1.1)  # snapshot names are second-precision + immutable

        # 2. The rollback path: verify fails → restore.
        r2 = run_self_modification(
            agent="nurse",
            plan_fn=lambda: {"ok": True, "detail": "bad fix"},
            restart_fn=lambda: {"ok": True, "detail": "restarted"},
            verify_fn=lambda: {"ok": False, "detail": "2 failing"})
        checks.append({
            "name": "self-modify: verify fail → rollback",
            "status": "ok" if not r2["success"] and r2.get("rollback")
            and "rolled back" in (r2.get("verify") or {}).get("detail", "") else "fail",
            "detail": f"rollback={r2.get('rollback', {}).get('detail', '')[:40]}",
        })

        time.sleep(1.1)

        # 3. The plan-fail path: refused before any change.
        r3 = run_self_modification(
            agent="nurse",
            plan_fn=lambda: {"ok": False, "detail": "refused"})
        checks.append({
            "name": "self-modify: plan fail → no change",
            "status": "ok" if not r3["success"]
            and r3.get("plan", {}).get("detail") == "refused" else "fail",
            "detail": str(r3.get("plan")),
        })

        # 4. The NURSE snapshots before repairing (the loop's step 1) —
        #    verified at the MODULE level (calling repair() with a real
        #    failure triggers the full doctor discovery + verify, which would
        #    recurse into this very test). We assert the wiring exists:
        #    doctor/nurse imports data.snapshots and calls snapshot() in the
        #    repair path before any fix.
        import doctor.nurse as nurse_mod
        nurse_src = open(nurse_mod.__file__, encoding="utf-8").read()
        snap_in_repair = "from data.snapshots import snapshot" in nurse_src \
            and "snapshot(version=\"pre-repair\")" in nurse_src
        checks.append({
            "name": "nurse snapshots before repairing (wired)",
            "status": "ok" if snap_in_repair else "fail",
            "detail": "repair() calls snapshot(version=pre-repair) first",
        })

        # 5. The JANITOR snapshots before applying (dry-run never snapshots).
        import core.janitor as jn
        orig_jan_state = jn.STATE_FILE
        orig_snapshot = snap.snapshot
        jn.STATE_FILE = Path(_td) / "janitor.json"
        calls = {"n": 0}

        def fake_snapshot2(*a, **k):
            calls["n"] += 1
            return f"/tmp/fake-{calls['n']}.zip"

        snap.snapshot = fake_snapshot2
        try:
            before = calls["n"]
            jn.run_sweep(dry_run=True)
            dry_calls = calls["n"] - before
            jn.run_sweep(dry_run=False)
            apply_calls = calls["n"] - before - dry_calls
            checks.append({
                "name": "janitor snapshots only on --apply",
                "status": "ok" if dry_calls == 0 and apply_calls >= 1 else "fail",
                "detail": f"dry={dry_calls} apply={apply_calls}",
            })
        finally:
            snap.snapshot = orig_snapshot
            jn.STATE_FILE = orig_jan_state
            snap.SNAPSHOT_DIR = orig_dir
    return checks
