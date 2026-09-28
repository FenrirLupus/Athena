"""Snapshots — the immutable version record of the athena-system.

Snapshot the base layer of the system into a zip:

    {date}_{time}_{version}_snapshot.zip
    e.g. 2026-08-07_14-30-00_0.1.0_snapshot.zip

IMMUTABILITY: a snapshot file is never modified or overwritten. Every
snapshot gets a unique timestamped name; the file is write-once.

THE 3-VERSION WINDOW (the Operator's spec):
    latest  — the current system saved (what's running)
    +1      — the previous version (rollback target)
    +2      — one more older (safety backup)
    older   — evicted (pruned) automatically

Snapshots live in .athena/snapshots/. Restore re-extracts the zip over
the athena-system directory (the nurse's domain — restoring a snapshot is
a managed repair, so the same identity gate applies).

The snapshot is the safety net: before an update (patch → merge/overwrite),
take a snapshot; if the new version breaks the system, restore from the
window. We always keep an older version as the rollback backup.
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from core.config import ATHENA_ROOT
from core.config import VERSION as ATHENA_VERSION

SYSTEM_DIR = ATHENA_ROOT / "athena-system"
SNAPSHOT_DIR = ATHENA_ROOT / "snapshots"
# The snapshot version — SINGLE SOURCE: core.config.VERSION (the Operator's
# release model). Snapshots tag themselves with the same version scheme.
VERSION = ATHENA_VERSION

# The snapshot systems are kept TOGETHER (the Operator's spec): snapshots/
# holds the backups/ subfolder (the backup zips) and the patches/
# subfolder (patch files). Both are created on demand.
BACKUPS_SUBDIR = SNAPSHOT_DIR / "backups"
PATCHES_SUBDIR = SNAPSHOT_DIR / "patches"

# The immutability window: keep the newest N snapshots, evict older.
WINDOW = 3

# Never snapshot the metrics logs (they change by design) or caches.
EXCLUDE_DIRS = {"__pycache__", "logs"}


def _ensure_subdirs() -> None:
    """The snapshots/ tree: backups/ + patches/ subfolders exist."""
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        BACKUPS_SUBDIR.mkdir(parents=True, exist_ok=True)
        PATCHES_SUBDIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def snapshot(version: str = VERSION, dest_dir: str = "") -> str:
    """Zip athena-system into {date}_{time}_{version}_snapshot.zip.

    IMMUTABLE: the file is write-once — a unique timestamped name means a
    snapshot is never overwritten. Excludes __pycache__ and metrics logs.
    After writing, the window is pruned to WINDOW snapshots (the oldest
    beyond the window are evicted — we keep the latest + backups).
    """
    now = datetime.now()
    name = f"{now.strftime('%Y-%m-%d')}_{now.strftime('%H-%M-%S')}_{version}_snapshot.zip"
    dest = Path(dest_dir) / name if dest_dir else SNAPSHOT_DIR / name
    _ensure_subdirs()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():  # immutability: never overwrite an existing snapshot
        raise FileExistsError(f"snapshot already exists (immutable): {dest}")

    count = 0
    with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SYSTEM_DIR.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(SYSTEM_DIR)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            zf.write(path, str(rel))
            count += 1
    if not dest_dir:
        prune(WINDOW)
    from core.logging import log_event
    log_event(2, f"snapshot created: {name} ({count} files)", source="data",
              action="snapshot", target=name)
    return f"{dest} ({count} files)"


def list_snapshots() -> list[dict]:
    """All snapshots, newest first: {name, size, path}."""
    if not SNAPSHOT_DIR.exists():
        return []
    out = []
    for p in sorted(SNAPSHOT_DIR.glob("*_snapshot.zip"), reverse=True):
        out.append({"name": p.name, "size": p.stat().st_size, "path": str(p)})
    return out


def prune(keep: int = WINDOW) -> list[str]:
    """Evict snapshots beyond the window. Returns the evicted names.

    IMMUTABLE window: the newest `keep` are retained (latest + backups);
    anything older is removed. Keeps the rollback targets safe.
    """
    snaps = sorted(SNAPSHOT_DIR.glob("*_snapshot.zip"), reverse=True) \
        if SNAPSHOT_DIR.exists() else []
    evicted = []
    for p in snaps[keep:]:
        p.unlink()
        evicted.append(p.name)
    return evicted


def latest() -> dict | None:
    """The newest snapshot (the current system's saved version)."""
    snaps = list_snapshots()
    return snaps[0] if snaps else None


def window_status() -> dict:
    """The 3-version window state: {snapshots, keep, evicted_ok}."""
    snaps = list_snapshots()
    return {
        "window": WINDOW,
        "kept": len(snaps),
        "snapshots": [s["name"] for s in snaps],
        "within_window": len(snaps) <= WINDOW,
    }


def parse_version(name: str) -> str:
    """Extract the version from a snapshot filename."""
    m = re.search(r"_\d+-\d+-\d+_([^_]+)_snapshot\.zip$", name)
    return m.group(1) if m else "?"


def restore(snapshot: str, *, agent: str = "") -> dict:
    """Restore a snapshot over athena-system.

    Restoring replaces code — the nurse's managed domain. The identity
    gate applies: only the nurse agent may restore (the same rule as
    repairs). Returns {restored, skipped}.
    """
    from doctor.nurse import NURSE_AGENT

    if agent != NURSE_AGENT:
        return {"restored": 0, "skipped": 0,
                "detail": "refused: only the nurse agent may restore a snapshot"}

    src = Path(snapshot)
    if not src.exists():
        return {"restored": 0, "skipped": 0, "detail": f"snapshot not found: {src}"}

    restored = 0
    skipped = 0
    with zipfile.ZipFile(str(src)) as zf:
        for member in zf.namelist():
            rel = Path(member)
            if any(part in EXCLUDE_DIRS for part in rel.parts):
                skipped += 1
                continue
            target = SYSTEM_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as fin, open(target, "wb") as fout:
                import shutil
                shutil.copyfileobj(fin, fout)
            restored += 1
    from core.logging import log_event
    log_event(2, f"snapshot restored: {src.name} ({restored} files)", source="data",
              action="restore_snapshot", target=src.name)
    return {"restored": restored, "skipped": skipped, "detail": f"restored from {src.name}"}
