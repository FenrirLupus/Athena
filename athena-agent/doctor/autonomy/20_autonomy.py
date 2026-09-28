"""Autonomy surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every autonomy submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path

from datetime import datetime, timedelta


def _chk_cron() -> list[dict]:
    from autonomy.cron import parse_interval, normalize_schedule, compute_next

    checks = []
    cases = [
        ("every 30m", timedelta(minutes=30)),
        ("every 2h", timedelta(hours=2)),
        ("every 90s", timedelta(seconds=90)),
        ("every 2h 30m", timedelta(hours=2, minutes=30)),
        ("every 1h 30m 15s", timedelta(hours=1, minutes=30, seconds=15)),
        ("every 1d", timedelta(days=1)),
        ("every 1w", timedelta(weeks=1)),
        ("30m", timedelta(minutes=30)),  # short form
    ]
    all_ok = True
    details = []
    for spec, expected in cases:
        got = parse_interval(spec)
        ok = got == expected
        if not ok:
            all_ok = False
        details.append(f"{spec}={got}")
    checks.append({
        "name": "interval parse (incl. H/M/S multi-part)",
        "status": "ok" if all_ok else "fail",
        "detail": "; ".join(details),
    })
    # A custom H/M/S interval computes a next run.
    nxt = compute_next("every 2h 30m")
    checks.append({
        "name": "custom interval computes next",
        "status": "ok" if nxt else "fail",
        "detail": nxt,
    })
    return checks


_SUBMODULES = [
    "agent_duties",
    "cron",
    "delegation",
    "mcp_client",
    "resource_lifecycle",
    "service",
    "services",
    "session_finalize_classify",
    "state_layout",
    "subagents",
    "supervisor",
    "tier1_adaptations",
    "tool_output_classify",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.autonomy._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        # Inline (folded) checks run directly; file-backed ones import.
        inline = globals().get(f"_chk_{name}")
        if inline is not None:
            try:
                checks.extend(inline())
            except Exception as exc:
                checks.append({
                    "name": f"autonomy/{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:
            checks.append({
                "name": f"autonomy/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks
