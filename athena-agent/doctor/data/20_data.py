"""Data surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every data submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

def _chk_updates() -> list[dict]:
    from data import updates
    from data.updates import overwrite, prepare_merge, nurse_merge, PATCH_DIR

    checks = []
    original_patch = updates.PATCH_DIR
    original_system = updates.SYSTEM_DIR
    with tempfile.TemporaryDirectory() as td:
        updates.PATCH_DIR = Path(td) / "patch"
        updates.SYSTEM_DIR = Path(td) / "system"  # ISOLATED — never the real system
        updates.SYSTEM_DIR.mkdir()
        try:
            # Merge: stage a file, nurse adds it.
            src = Path(td) / "brand-new.py"
            src.write_text("x = 1")
            pm = prepare_merge(str(src))
            checks.append({
                "name": "prepare_merge stages",
                "status": "ok" if pm.get("ok") and updates.PATCH_DIR.exists() else "fail",
                "detail": pm.get("detail", "")[:40],
            })
            # Non-nurse refused (a generic agent name — dynamic, not
            # tied to any specific profile).
            r = nurse_merge(agent="some-agent")
            checks.append({
                "name": "merge refused for non-nurse",
                "status": "ok" if "refused" in r.get("detail", "") else "fail",
                "detail": r.get("detail", "")[:40],
            })
            # Nurse merge adds the new file.
            r = nurse_merge(agent="nurse")
            added = r.get("added", [])
            checks.append({
                "name": "nurse merge adds new file",
                "status": "ok" if any("brand-new.py" in a for a in added) else "fail",
                "detail": f"added={added}",
            })
            # Overwrite gate.
            r = overwrite(str(src), agent="some-agent")
            checks.append({
                "name": "overwrite refused for non-nurse",
                "status": "ok" if "refused" in r.get("detail", "") else "fail",
                "detail": r.get("detail", "")[:40],
            })
        finally:
            updates.PATCH_DIR = original_patch
            updates.SYSTEM_DIR = original_system
    return checks


_SUBMODULES = [
    "updates",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.data._sub_{name}", str(path))
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
                    "name": f"data/{name}",
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
                "name": f"data/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks
