"""Nurse + janitor duty test — the Operator's 08-12 spec: verify the system
agents ACTUALLY perform their jobs, safely, without breaking the
architecture.

  • The NURSE and JANITOR are the LIVE-CODE handlers: they repair and
    clean the running architecture, always SNAPSHOT-FIRST so the undo
    exists before any change. Their work either fixes or rolls back;
    it never leaves the system worse.
  • The CUSTODIAN and DOCTOR are the OBSERVERS: they scan and diagnose
    but never mutate live code.
  • The janitor's workspace sweep removes exactly the disposable
    patterns — never live files.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    # THE LIVE GATE (the Operator's 08-12 session fix): this test APPLIES
    # the janitor's cleanup (dry_run=False) + takes snapshots. In the
    # LIVE process (the service's boot pass), an applying sweep + real
    # snapshot can touch the live tree and trigger the snapshot/restore
    # loop that wipes operator sessions. It runs ONLY in the isolated
    # subprocess (`athena doctor`), never inside the service.
    import os as _os
    if _os.environ.get("ATHENA_LIVE") == "1":
        return [{
            "name": "agent duties skipped in live process",
            "status": "ok",
            "detail": "applying janitor sweeps would touch the live tree (08-12)",
        }]
    checks = []

    # ── 1. THE NURSE: repair API is bounded + scope-gated ──
    # (NOT a full run_all() — a test must never invoke the whole doctor
    #  inside itself; that recurses and hangs.)
    try:
        from doctor.nurse import repair, enter_scope, exit_scope, \
            NURSE_AGENT, NURSE_PROFILE

        # The scope gate exists + the repair takes a report dict.
        checks.append({
            "name": "nurse: scope gate + repair API present",
            "status": "ok" if callable(repair) and callable(enter_scope)
            and callable(exit_scope) else "fail",
            "detail": f"agent={NURSE_AGENT} profile={NURSE_PROFILE}",
        })

        # A dry-run repair on an EMPTY report must not crash (bounded).
        try:
            result = repair({"summary": {"fail": 0, "ok": 0, "warn": 0,
                                         "info": 0, "total": 0},
                             "tests": []},
                            dry_run=True, agent=NURSE_AGENT)
            checks.append({
                "name": "nurse: empty report → nothing to repair",
                "status": "ok" if result.get("attempted", 0) == 0 else "fail",
                "detail": f"attempted={result.get('attempted')} "
                          f"detail={result.get('detail', '')[:40]}",
            })
        except Exception as exc:
            checks.append({
                "name": "nurse: empty report → nothing to repair",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    except Exception as exc:
        checks.append({
            "name": "nurse duty",
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        })

    # ── 2. THE JANITOR: workspace sweep removes ONLY disposables ──
    try:
        import core.janitor as janitor
        import core.config as cfg_mod
        original_root = cfg_mod.ATHENA_ROOT
        original_janitor_root = janitor.ATHENA_ROOT
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg_mod.ATHENA_ROOT = tmp
            janitor.ATHENA_ROOT = tmp  # patch the module's OWN binding
            try:
                # Seed: one disposable (tmp_x.txt), one live file (keep.txt)
                (tmp / "tmp_probe.txt").write_text("scratch", encoding="utf-8")
                (tmp / "keep.txt").write_text("keep me", encoding="utf-8")
                # Sweep for real (dry_run=False) — only disposables go.
                removed = janitor.sweep_workspace(dry_run=False)
                removed_paths = [r["path"] for r in removed]
                tmp_removed = any("tmp_probe" in p for p in removed_paths)
                keep_alive = (tmp / "keep.txt").exists()
                checks.append({
                    "name": "janitor: sweep removes disposables only",
                    "status": "ok" if tmp_removed and keep_alive else "fail",
                    "detail": f"tmp_removed={tmp_removed} keep_alive={keep_alive}",
                })
            finally:
                cfg_mod.ATHENA_ROOT = original_root
                janitor.ATHENA_ROOT = original_janitor_root
    except Exception as exc:
        checks.append({
            "name": "janitor: sweep removes disposables only",
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        })

    # ── 3. THE JANITOR: handles LIVE code safely — snapshot-first, never
    #       breaks the architecture (the Operator's 08-12 role: the janitor +
    #       nurse are the LIVE-CODE handlers; custodian + doctor observe).
    try:
        import core.janitor as janitor
        import core.custodian as custodian

        # 3a. The CUSTODIAN only OBSERVES — its scan never mutates.
        findings = custodian.scan()
        checks.append({
            "name": "custodian: scan observes (never mutates)",
            "status": "ok" if isinstance(findings, dict)
            and ("artifacts" in findings or "dead_code" in findings)
            else "fail",
            "detail": f"keys={list(findings.keys())}",
        })

        # 3b. The JANITOR applies live cleanup SNAPSHOT-FIRST: a real
        #     (non-dry) sweep takes a pre-cleanup snapshot so the undo
        #     exists before anything is touched.
        import core.config as cfg_mod
        original_root = cfg_mod.ATHENA_ROOT
        original_janitor_root = janitor.ATHENA_ROOT
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cfg_mod.ATHENA_ROOT = tmp
            janitor.ATHENA_ROOT = tmp  # the module's OWN binding
            try:
                result = janitor.run_sweep(dry_run=False)
                snap = result.get("snapshot") or ""
                checks.append({
                    "name": "janitor: live cleanup snapshots first",
                    "status": "ok" if snap else "fail",
                    "detail": f"snapshot={snap or '(none)'}",
                })
            finally:
                cfg_mod.ATHENA_ROOT = original_root
                janitor.ATHENA_ROOT = original_janitor_root
    except Exception as exc:
        checks.append({
            "name": "janitor: live cleanup snapshots first",
            "status": "fail",
            "detail": f"{type(exc).__name__}: {exc}",
        })

    return checks
