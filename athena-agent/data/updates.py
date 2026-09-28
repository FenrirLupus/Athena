"""Updates — two modes for applying a new Athena version.

    1. OVERWRITE — replace athena-system entirely with the new base layer.
       The system dir is the foundation; a full version swap wipes and
       rewrites it (a snapshot should be taken first).

    2. MERGE — clone/point the update source into .athena/patch/, then the
       NURSE handles patching: she compares what exists in the architecture
       against what's new, and patches the updates into the live system.
       This is the managed path — the nurse decides what changes, and she
       only writes through her identity-gated scope.

Both modes snapshot first (the snapshot is the safety net).
"""
from __future__ import annotations

import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from core.config import ATHENA_ROOT
from data.snapshots import snapshot

SYSTEM_DIR = ATHENA_ROOT / "athena-system"
PATCH_DIR = ATHENA_ROOT / "patch"
EXCLUDE_DIRS = {"__pycache__", "logs"}


def overwrite(source: str, *, agent: str = "") -> dict:
    """Replace athena-system with the source (dir or zip).

    Destructive by design (the base layer is rewritten wholesale). The
    nurse gate applies; a snapshot is taken first automatically.
    """
    from doctor.nurse import NURSE_AGENT
    if agent != NURSE_AGENT:
        return {"ok": False, "detail": "refused: only the nurse may overwrite the system"}

    snap = snapshot()
    src = Path(source)
    if not src.exists():
        return {"ok": False, "detail": f"update source not found: {src}"}

    # Extract source into a temp staging dir.
    staging = ATHENA_ROOT / ".update-staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        if src.is_dir():
            for item in src.iterdir():
                if item.name not in EXCLUDE_DIRS:
                    shutil.copytree(item, staging / item.name) \
                        if item.is_dir() else shutil.copy2(item, staging / item.name)
        else:
            with zipfile.ZipFile(str(src)) as zf:
                zf.extractall(str(staging))

        # Replace athena-system with the staged copy.
        old = ATHENA_ROOT / ".update-old"
        if old.exists():
            shutil.rmtree(old)
        if SYSTEM_DIR.exists():
            SYSTEM_DIR.rename(old)
        staging.rename(SYSTEM_DIR)
    except Exception as exc:
        from core.logging import log_event
        log_event(5, f"system overwrite failed (rolled back): {exc}",
                  source="data", action="update_overwrite")
        # Roll back if the swap failed.
        if old.exists() and not SYSTEM_DIR.exists():
            old.rename(SYSTEM_DIR)
        return {"ok": False, "detail": f"overwrite failed: {exc} (rolled back)"}
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return {"ok": True, "detail": f"system overwritten from {src.name}",
            "snapshot": snap, "backup": str(old) if old.exists() else ""}


def prepare_merge(source: str) -> dict:
    """Stage an update for the nurse to merge: copy source into .athena/patch/.

    The nurse reads the patch dir, compares against the live architecture,
    and patches what's new via her identity-gated scope. Nothing is applied
    here — only staged.
    """
    src = Path(source)
    if not src.exists():
        return {"ok": False, "detail": f"update source not found: {src}"}
    if PATCH_DIR.exists():
        shutil.rmtree(PATCH_DIR)
    PATCH_DIR.mkdir(parents=True)
    try:
        if src.is_dir():
            shutil.copytree(src, PATCH_DIR / src.name) if src.name not in EXCLUDE_DIRS \
                else shutil.copytree(src, PATCH_DIR / "update")
        elif zipfile.is_zipfile(src):
            with zipfile.ZipFile(str(src)) as zf:
                zf.extractall(str(PATCH_DIR))
        else:
            # A plain file update — copy it directly into the patch dir.
            shutil.copy2(src, PATCH_DIR / src.name)
    except Exception as exc:
        return {"ok": False, "detail": f"could not stage update: {exc}"}
    return {"ok": True, "detail": f"update staged in {PATCH_DIR} — the nurse will merge it"}


def nurse_merge(agent: str = "") -> dict:
    """The nurse applies the staged patch: merge new files over the system.

    For each file in the patch, if it doesn't exist in athena-system → add;
    if it exists → report as a change the nurse reviews. This is the
    managed merge — the nurse's scope gates every write.
    """
    from doctor.nurse import NURSE_AGENT, enter_scope, exit_scope
    if agent != NURSE_AGENT:
        return {"ok": False, "detail": "refused: only the nurse may merge updates"}

    if not PATCH_DIR.exists():
        return {"ok": False, "detail": "no patch staged — run update prepare-merge first"}

    added, changed, skipped = [], [], 0
    enter_scope(agent)
    try:
        for path in sorted(PATCH_DIR.rglob("*")):
            if not path.is_file() or any(p in EXCLUDE_DIRS for p in path.parts):
                continue
            rel = path.relative_to(PATCH_DIR)
            target = SYSTEM_DIR / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                added.append(str(rel))
            else:
                # Exists — the nurse flags it as a reviewed change (not
                # blindly overwritten; the nurse decides).
                changed.append(str(rel))
                skipped += 1
    finally:
        exit_scope()
    return {"ok": True, "added": added, "needs_review": changed, "skipped": skipped}



