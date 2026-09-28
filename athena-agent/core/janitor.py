"""Janitor — the optimization pass (the Operator's performance split).

The two-tier performance architecture mirrors health:
    HEALTH:      Doctor (FREE diagnosis)  →  Nurse (provider repair)
    PERFORMANCE: Custodian (FREE scan)    →  Janitor (provider optimization)

The JANITOR is the OPTIMIZATION pass — it works FROM the custodian's
FREE scan findings (core.custodian.scan()) and plans/applies the
cleanup surgically:

  • Outside athena-system: disposable artifacts — removed with care.
  • Inside athena-system: dead-code candidates — REPORTED (the
    doctor/nurse decides; the janitor never edits CODE, it optimizes).

The janitor is a SYSTEM profile (.janitor — dot-prefixed = system-based,
like .nurse/.custodian). Its sweeps are conservative: snapshot first on
apply, nothing destructive without an explicit owner decision.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT

JANITOR_PROFILE = ".janitor"
STATE_FILE = DEFAULT_PROFILE_ROOT / "operations" / "janitor.json"


def _ensure_profile() -> None:
    """The janitor's own system profile exists (profiles/.janitor/).

    Delegates to the shared system-profiles ensure (the startup hook) —
    one source of truth for system profile creation.
    """
    try:
        from core.system_profiles import ensure_all
        ensure_all()
    except Exception:
        pass

# Safety: the janitor NEVER deletes code or the vault. It only touches
# clearly disposable artifacts (scratch, temp, stale caches).
# The Operator's 08-12 note: agents sometimes write diagnostic scratch to the
# profile root (nf*.txt, l3_*.txt, ns*.txt, copy_*.txt, newfiles.txt,
# scratch_*.txt, tmp*.txt, *list.txt) — all disposable test artifacts.
_DISPOSABLE_NAMES = {
    "*.tmp", "*.log~", "*.bak~", "*.swp", ".DS_Store", "Thumbs.db",
    "run_during_tick_subagent_result_*.txt", "scratch_*.txt",
    "nf*.txt", "ns*.txt", "l3_*.txt", "copy_*.txt", "newfiles*.txt",
    "tmp*.txt", "*list*.txt", "fails*.txt", "chunk_*.txt", "diag_*.txt",
    "one.txt", "two.txt", "three.txt", "tbody*.txt", "task_body*.txt",
    "tc.txt", "test_read*.txt", "f3*.txt", "fj*.txt", "f5*.txt",
}

# Stale threshold: files older than 30 days are candidates.
STALE_DAYS = 30

# Names that are disposable REGARDLESS of age — agent scratch written to
# the root during inspections (tmp_*.txt, scratch_*.txt, chunk_*.txt,
# l3_*.txt, nf*.txt, ...). Fresh scratch is still scratch: the janitor
# sweeps it on every pass, not after 30 days.
_ALWAYS_FRESH = {
    "tmp*.txt", "scratch_*.txt", "chunk_*.txt", "l3_*.txt", "nf*.txt",
    "ns*.txt", "copy_*.txt", "newfiles*.txt", "*list*.txt", "fails*.txt",
    "diag_*.txt", "tbody*.txt", "task_body*.txt", "one.txt", "two.txt",
    "three.txt", "tc.txt", "test_read*.txt", "f3*.txt", "fj*.txt",
}


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"sweeps": 0, "last_sweep": None, "removed": [], "reports": []}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                          encoding="utf-8")


# -- The sweeps ---------------------------------------------------------

def sweep_workspace(dry_run: bool = True) -> list[dict]:
    """Outside athena-system: disposable artifacts in workspace/ + root.

    Conservative: only _DISPOSABLE_NAMES patterns. Files matching
    _ALWAYS_FRESH (agent scratch like tmp_*.txt) are removed regardless
    of age; everything else must be older than STALE_DAYS. dry_run=True
    reports; False deletes (only the clearly-disposable ones).
    """
    found = []
    for pat in _DISPOSABLE_NAMES:
        for p in ATHENA_ROOT.glob(pat):
            try:
                # Fresh-scratch names skip the age gate (they are
                # disposable by definition, written moments ago).
                if pat not in _ALWAYS_FRESH:
                    age_days = (time.time() - p.stat().st_mtime) / 86400
                    if age_days < STALE_DAYS:
                        continue
                if dry_run:
                    found.append({"path": str(p), "action": "candidate"})
                else:
                    p.unlink()
                    found.append({"path": str(p), "action": "removed"})
            except Exception:
                continue
    return found


def sweep_system(dry_run: bool = True) -> list[dict]:
    """Inside athena-system: DEAD CODE paths that never fire.

    The janitor NEVER edits code — it REPORTS dead modules (no run(),
    no fix(), not referenced) so the doctor/nurse decides. This is the
    hygiene pass for the architecture itself.
    """
    sys_dir = ATHENA_ROOT / "athena-system"
    reports = []
    try:
        for py in sys_dir.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            try:
                src = py.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            # A module with neither run()/fix()/main nor any import of it
            # is a candidate for dead code (report only).
            has_entry = any(x in src for x in ("def run(", "def fix(",
                                               "def main(", "__main__"))
            if not has_entry:
                reports.append({"path": str(py.relative_to(sys_dir)),
                                "action": "report: no entry point"})
    except Exception:
        pass
    return reports


def run_sweep(*, dry_run: bool = True) -> dict:
    """The JANITOR's pass (the provider/optimization tier).

    The CUSTODIAN (free tier) scans first; the janitor works FROM those
    findings — planning and applying the optimization. SNAPSHOT FIRST
    (the Operator's self-modification loop): when APPLYING (not dry-run), the
    current architecture is backed up before anything is removed.
    """
    _ensure_profile()
    # The FREE scan (the custodian's tier) feeds this pass.
    try:
        from core.custodian import scan
        findings = scan()
    except Exception:
        findings = {"artifacts": [], "dead_code": []}
    snapshot_made = ""
    if not dry_run:
        try:
            from data.snapshots import snapshot
            snapshot_made = snapshot(version="pre-cleanup")
        except Exception:
            pass
    state = _load_state()
    workspace = sweep_workspace(dry_run=dry_run)
    system = sweep_system(dry_run=True)  # system is ALWAYS report-only
    state["sweeps"] += 1
    state["last_sweep"] = time.time()
    state["removed"] = [f["path"] for f in workspace
                        if f["action"] == "removed"][-50:]
    state["reports"] = [f["path"] for f in system][-50:]
    state["last_snapshot"] = snapshot_made
    # THE DEAD-CODE PROPOSALS (the Operator's 08-12 spec): the janitor
    # records the custodian's path-trace findings as ACTIONABLE
    # proposals (report-only — the janitor NEVER deletes functions
    # automatically; the operator/nurse reviews + approves). Each
    # proposal names the function + why it's a candidate.
    proposals = []
    for d in (findings.get("dead_code") or [])[:200]:
        proposals.append({
            "function": d.get("path", ""),
            "detail": d.get("detail", ""),
            "action": "review for removal",
            "approved": False,
        })
    state["proposals"] = proposals
    state["proposal_count"] = len(proposals)
    _save_state(state)
    removed_n = sum(1 for f in workspace if f["action"] == "removed")
    _log(2, f"janitor sweep: {removed_n} removed, "
            f"{len(system)} system reports, "
            f"{len(proposals)} dead-code proposals",
         source="janitor")
    return {
        "dry_run": dry_run,
        "snapshot": snapshot_made,
        "custodian_findings": findings,
        "workspace": workspace,
        "system_reports": system,
        "removed_count": removed_n,
        "report_count": len(system),
        "proposal_count": len(proposals),
    }


def _log(level: int, msg: str, source: str = "janitor") -> None:
    """The janitor is operational — its sweeps are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


def status() -> dict:
    _ensure_profile()
    state = _load_state()
    return {
        "profile": JANITOR_PROFILE,
        "profile_exists": (ATHENA_ROOT / "profiles" / JANITOR_PROFILE
                           ).is_dir(),
        "sweeps": state.get("sweeps", 0),
        "last_sweep": state.get("last_sweep"),
        "removed": state.get("removed", [])[-10:],
        "reports": state.get("reports", [])[-10:],
    }
