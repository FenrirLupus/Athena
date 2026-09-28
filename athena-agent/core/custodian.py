"""Custodian — the FREE scan tier of the JANITOR (the Operator's spec).

The two-tier performance architecture mirrors health:
    HEALTH:      .nurse/doctor/ (FREE diagnosis)  →  the Nurse repairs
    PERFORMANCE: .janitor/custodian/ (FREE scan)  →  the Janitor optimizes

The CUSTODIAN is the FREE scan — NOT a separate agent. It is the
janitor's own free tier, living inside the .janitor profile (mirroring
how the doctor's free tier lives inside .nurse/doctor/). It scans the
architecture — disposable artifacts outside athena-system and dead-code
candidates inside — and REPORTS. Zero provider calls, zero tokens (like
the doctor). Its findings feed the JANITOR, who plans and applies the
optimization. They operate in that order: custodian scans → janitor
optimizes — same profile, same team, different tiers.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from core.config import ATHENA_ROOT

# The custodian's home is INSIDE the janitor's profile (like the
# doctor's home is inside the nurse's): profiles/.janitor/custodian/.
JANITOR_PROFILE = ".janitor"
CUSTODIAN_DIR = ATHENA_ROOT / "profiles" / JANITOR_PROFILE / "custodian"
STATE_FILE = CUSTODIAN_DIR / "scan-state.json"

# Disposable artifact patterns (outside athena-system).
_DISPOSABLE_NAMES = {
    "*.tmp", "*.log~", "*.bak~", "*.swp", ".DS_Store", "Thumbs.db",
    "run_during_tick_subagent_result_*.txt", "scratch_*.txt",
}

# Stale threshold: files older than 30 days are candidates.
STALE_DAYS = 30

def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"scans": 0, "last_scan": None, "reports": []}

def _save_state(state: dict) -> None:
    CUSTODIAN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                              encoding="utf-8")
    except Exception as exc:
        _log(3, f"custodian state write failed: {exc}", source="custodian")

def _log(level: int, msg: str, source: str = "custodian") -> None:
    """The custodian is operational — failures are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass

def scan_artifacts() -> list[dict]:
    """FREE: disposable artifacts outside athena-system (report only)."""
    found = []
    for pat in _DISPOSABLE_NAMES:
        for p in ATHENA_ROOT.glob(pat):
            try:
                age_days = (time.time() - p.stat().st_mtime) / 86400
                if age_days < STALE_DAYS:
                    continue
                found.append({"path": str(p), "kind": "artifact",
                              "age_days": round(age_days, 1)})
            except Exception:
                continue
    return found

def scan_dead_code() -> list[dict]:
    """FREE: dead-code candidates inside athena-system (report only).

    THE MAPPER-SOURCED SCAN (the Operator's 08-15 fix): the custodian
    previously ran its OWN hand-rolled path-tracer (the 08-12 version),
    which lacked the mapper's 5 accuracy rules (inheritance edges,
    dict-dispatch tables, decorator registration, f-string dynamic
    lookup, config/manifest refs) — so it reported ~282 false positives
    every scan (the SAME dynamic-dispatch code the mapper now correctly
    maps as alive).

    Now the scan uses the TIMELINE MAPPER's `map_operations` as the ONE
    source of truth: the mapper's node states (alive/dead/connection)
    already encode every reference mechanism. Only the mapper-confirmed
    DEAD members are reported. No duplicate logic, no drift.
    """
    reports = []
    try:
        from pathlib import Path as _Path
        from timeline.mapper import map_operations
        sys_dir = ATHENA_ROOT / "athena-system"
        graph = map_operations(_Path(sys_dir))
        for n in graph.get("nodes", []):
            if (n.get("state") == "dead"
                    and n.get("kind") in ("function", "method", "class")):
                reports.append({
                    "path": n.get("id", ""),
                    "kind": "dead-code",
                    "detail": (f"unreachable from any entry point "
                               f"(line {n.get('line', '?')})"),
                })
    except Exception:
        pass  # a scan failure must never break the janitor
    return reports

def scan() -> dict:
    """The CUSTODIAN's pass: the FREE scan. Zero provider calls.

    Returns {artifacts, dead_code} — the findings the JANITOR optimizes
    from. Recorded in state (.janitor/custodian/scan-state.json) AND
    logged to the ONE metrics stream (the Operator's 08-12 spec: every
    system's findings are cross-diagnosable — a silent scan is a lying
    scan; the metrics log shows what the custodian actually found).
    """
    _ensure_dir()
    state = _load_state()
    artifacts = scan_artifacts()
    dead = scan_dead_code()
    state["scans"] += 1
    state["last_scan"] = time.time()
    state["reports"] = {
        "artifacts": [f["path"] for f in artifacts][-50:],
        "dead_code": [f["path"] for f in dead][-50:],
    }
    _save_state(state)
    # THE METRICS STREAM: the scan's findings land in the log — a
    # finding (L3 warning) vs a clean scan (L2 info) are both visible.
    try:
        _n_art = len(artifacts)
        _n_dead = len(dead)
        if _n_art or _n_dead:
            _log(3, f"custodian scan #{state['scans']}: {_n_art} artifacts, "
                    f"{_n_dead} dead-code candidates",
                 source="custodian")
        else:
            _log(2, f"custodian scan #{state['scans']}: clean — "
                    f"no artifacts, no dead-code", source="custodian")
    except Exception:
        pass
    return {"artifacts": artifacts, "dead_code": dead,
            "scans": state["scans"]}

def status() -> dict:
    _ensure_dir()
    state = _load_state()
    return {
        "profile": JANITOR_PROFILE,
        "home": str(CUSTODIAN_DIR),
        "home_exists": CUSTODIAN_DIR.is_dir(),
        "scans": state.get("scans", 0),
        "last_scan": state.get("last_scan"),
        "reports": state.get("reports", {}),
    }

def _ensure_dir() -> None:
    """The custodian's home (inside the janitor profile) exists — and
    the janitor profile itself is ensured by the startup hook."""
    try:
        CUSTODIAN_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
