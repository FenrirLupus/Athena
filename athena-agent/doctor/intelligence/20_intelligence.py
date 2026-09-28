"""Intelligence surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every intelligence submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path

_SUBMODULES = [
    "curator",
    "memory",
    "sections",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.intelligence._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:  # a diagnostic must never crash the run
            checks.append({
                "name": f"intelligence/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks
