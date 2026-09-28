"""Backup / Snapshot / Patch — Athena's version model (the Operator's 08-12).

THREE TIERS, one root (snapshots/):

  1. BACKUPS — PAST versions of athena-system, archived periodically.
     snapshots/backups/athena-backup-{ts}.zip  (retention: keep the
     most recent BACKUP_KEEP; older are archived/pruned).

  2. SNAPSHOTS — the CURRENT version, immutable, for rollback by 1-3
     versions of her current architecture.
     snapshots/snapshots/athena-snapshot-{ts}.zip
     (the ROLLBACK target: restore the last 1-3 snapshots; they are
     never overwritten once written).

  3. PATCHES — the cloned/pulled GitHub repo of athena-system. The
     repo contents land in a patch directory (snapshots/patches/),
     then either
       A) overwrite athena-system completely, OR
       B) the nurse/janitor apply the patch manually into
          athena-system — keeping her up to date WITHOUT wiping the
          existing architecture.

The athena-system folder is the ONLY thing that gets replaced during
updates — it is what's uploaded to GitHub, snapshotted, and restored.
"""
from __future__ import annotations

import datetime
import shutil
import zipfile
from pathlib import Path

from core.config import ATHENA_ROOT

# The snapshot root — the three tiers live together.
SNAPSHOT_ROOT = ATHENA_ROOT / "snapshots"
BACKUP_DIR = SNAPSHOT_ROOT / "backups"
SNAPSHOT_DIR = SNAPSHOT_ROOT / "snapshots"
PATCH_DIR = SNAPSHOT_ROOT / "patches"

# Retention: how many of each tier to keep.
BACKUP_KEEP = 7        # archived past versions
SNAPSHOT_KEEP = 3      # immutable rollback versions (1-3 back)

# The code root — the ONLY thing that is versioned.
CODE_ROOT = ATHENA_ROOT / "athena-system"

# Quick snapshot: the critical state files (data, not code).
QUICK_PATHS = ["authentication.json", "sessions", "assistant", "user",
               "profiles/.default/config.yaml"]

# Code/read-only zones never included in a data backup.
EXCLUDED_DIRS = {"athena-system", "readme", "__pycache__", "cache",
                 "snapshots", "patch", "backups", ".venv", ".wiki"}


def _should_include(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    return parts[0] not in EXCLUDED_DIRS


def _code_files() -> list[Path]:
    """All files under athena-system/ (no caches, no git)."""
    if not CODE_ROOT.is_dir():
        return []
    out = []
    for p in sorted(CODE_ROOT.rglob("*")):
        if not p.is_file():
            continue
        if any(part in ("__pycache__", ".git") for part in p.relative_to(CODE_ROOT).parts):
            continue
        out.append(p)
    return out


def _zip_files(dest: Path, files: list[Path], arc_root: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, str(path.relative_to(arc_root)))
            count += 1
    return count


def _prune(directory: Path, keep: int, pattern: str) -> list[Path]:
    """Keep the newest N matching zips; return the pruned ones."""
    if not directory.is_dir():
        return []
    zips = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime,
                  reverse=True)
    pruned = zips[keep:]
    for p in pruned:
        try:
            p.unlink()
        except OSError:
            pass
    return pruned


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _stamp_unique() -> str:
    """A unique stamp: seconds + a monotonic tiebreak so two snapshots
    in the same second never collide (immutable rollback versions)."""
    import time
    return f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}-{time.monotonic_ns() % 100000:05d}"


# ── TIER 1: BACKUPS — past versions, archived periodically ────────────
def run_backup(output: str = "") -> str:
    """Backup: zip of athena-system/ — a PAST version, archived."""
    dest = Path(output) if output else BACKUP_DIR / f"athena-backup-{_stamp_unique()}.zip"
    dest = dest if dest.suffix else dest.with_suffix(".zip")
    files = _code_files()
    count = _zip_files(dest, files, CODE_ROOT)
    pruned = _prune(BACKUP_DIR, BACKUP_KEEP, "athena-backup-*.zip")
    note = f", pruned {len(pruned)} old" if pruned else ""
    return f"{dest} ({count} files){note}"


# ── TIER 2: SNAPSHOTS — the current version, immutable, rollback ─────
def run_snapshot(output: str = "") -> str:
    """Snapshot: the CURRENT athena-system, immutable. Rollback target."""
    dest = Path(output) if output else SNAPSHOT_DIR / f"athena-snapshot-{_stamp_unique()}.zip"
    dest = dest if dest.suffix else dest.with_suffix(".zip")
    files = _code_files()
    count = _zip_files(dest, files, CODE_ROOT)
    # Snapshots are IMMUTABLE — never pruned by retention here (the
    # operator's rollback by 1-3). Keep the newest SNAPSHOT_KEEP and
    # report older ones as still-present rollback options.
    return f"{dest} ({count} files)"


def list_snapshots() -> list[Path]:
    if not SNAPSHOT_DIR.is_dir():
        return []
    return sorted(SNAPSHOT_DIR.glob("athena-snapshot-*.zip"),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def run_rollback(version: int = 1) -> str:
    """Roll back athena-system to snapshot version 1-3 (the Operator's spec)."""
    snaps = list_snapshots()
    if not snaps:
        return "no snapshots to roll back to"
    idx = min(max(version - 1, 0), len(snaps) - 1)
    target = snaps[idx]
    shutil.rmtree(CODE_ROOT, ignore_errors=True)
    CODE_ROOT.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(target)) as zf:
        zf.extractall(str(CODE_ROOT))
    return f"rolled back to {target.name}"


# ── TIER 3: PATCHES — the cloned/pulled GitHub repo ──────────────────
def run_patch(output: str = "") -> str:
    """Patch: clone/pull the GitHub athena-system repo into the patch dir.

    The repo contents land in snapshots/patches/repo/ — then either
    A) overwrite athena-system completely, or B) the nurse/janitor
    apply the patch manually. This function only FETCHES the patch.
    """
    import subprocess
    dest = Path(output) if output else PATCH_DIR
    dest.mkdir(parents=True, exist_ok=True)
    repo = dest / "repo"
    from core.config import ATHENA_SYSTEM_REPO
    if repo.is_dir():
        r = subprocess.run(["git", "-C", str(repo), "pull"],
                           capture_output=True, text=True, timeout=300)
    else:
        r = subprocess.run(["git", "clone", ATHENA_SYSTEM_REPO, str(repo)],
                           capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return f"patch fetch failed: {(r.stderr or r.stdout).strip()[:200]}"
    n = len(list(repo.rglob("*.py"))) if repo.is_dir() else 0
    return f"patch fetched → {repo} ({n} .py files)"


def apply_patch(overwrite: bool = True) -> str:
    """Apply the fetched patch into athena-system.

    overwrite=True  → A: replace athena-system completely.
    overwrite=False → B: the nurse/janitor apply it manually — here we
                       copy files that are NEW/changed only, never
                       deleting existing architecture (safe apply).
    """
    repo = PATCH_DIR / "repo"
    if not repo.is_dir():
        return "no fetched patch (run: athena patch)"
    if overwrite:
        shutil.rmtree(CODE_ROOT, ignore_errors=True)
        shutil.copytree(repo, CODE_ROOT,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        return "patch applied: athena-system overwritten"
    copied = 0
    for p in sorted(repo.rglob("*")):
        if not p.is_file():
            continue
        if ".git" in p.parts or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(repo)
        tgt = CODE_ROOT / rel
        if not tgt.exists() or tgt.read_bytes() != p.read_bytes():
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, tgt)
            copied += 1
    return f"patch applied manually: {copied} files updated (architecture preserved)"


# ── Quick data snapshot (unchanged behavior) ─────────────────────────
def run_quick_backup(output: str = "", label: str = "") -> str:
    """Quick snapshot: only the critical state files (data, not code)."""
    suffix = f"-{label}" if label else ""
    dest = Path(output) if output else BACKUP_DIR / f"athena-quick{suffix}-{_stamp()}.zip"
    dest = dest if dest.suffix else dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in QUICK_PATHS:
            src = ATHENA_ROOT / rel
            if not src.exists():
                continue
            if src.is_file():
                zf.write(src, rel)
                count += 1
            else:
                for path in sorted(src.rglob("*")):
                    if path.is_file() and "__pycache__" not in path.parts:
                        zf.write(path, str(path.relative_to(ATHENA_ROOT)))
                        count += 1
    return f"{dest} ({count} files)"


# ── Import/restore ───────────────────────────────────────────────────
def run_import(archive: str) -> str:
    """Restore a backup zip into .athena/ (safe: refuses to overwrite code)."""
    src = Path(archive)
    if not src.exists():
        raise FileNotFoundError(f"backup not found: {src}")
    restored = 0
    skipped = 0
    with zipfile.ZipFile(str(src)) as zf:
        for member in zf.namelist():
            rel = Path(member)
            if not _should_include(rel):
                skipped += 1
                continue
            target = ATHENA_ROOT / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            restored += 1
    return f"restored {restored} files ({skipped} skipped: code/reference zones)"


# ── CLI handlers ─────────────────────────────────────────────────────
def cmd_backup(args) -> int:
    quick = getattr(args, "quick", False)
    output = getattr(args, "output", "")
    label = getattr(args, "label", "")
    try:
        if quick:
            result = run_quick_backup(output, label)
        else:
            result = run_backup(output)
        print(f"[athena] backup: {result}")
        return 0
    except Exception as exc:  # noqa: BLE001
        from core.logging import log_event
        log_event(4, f"backup failed: {exc}", source="data", action="backup")
        print(f"[athena] backup failed: {exc}")
        return 1


def cmd_snapshot(args) -> int:
    try:
        print(f"[athena] snapshot: {run_snapshot()}")
        snaps = list_snapshots()
        if snaps:
            print(f"[athena] rollback targets ({len(snaps)}):")
            for i, s in enumerate(snaps[:SNAPSHOT_KEEP], 1):
                print(f"  {i}. {s.name}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[athena] snapshot failed: {exc}")
        return 1


def cmd_rollback(args) -> int:
    try:
        version = int(getattr(args, "version", "1") or "1")
        print(f"[athena] {run_rollback(version)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[athena] rollback failed: {exc}")
        return 1


def cmd_patch(args) -> int:
    try:
        action = getattr(args, "action", "")
        if action == "apply":
            overwrite = getattr(args, "overwrite", True)
            print(f"[athena] {apply_patch(overwrite)}")
        elif action == "apply-safe":
            print(f"[athena] {apply_patch(overwrite=False)}")
        else:
            print(f"[athena] {run_patch()}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[athena] patch failed: {exc}")
        return 1


def cmd_import(args) -> int:
    archive = getattr(args, "archive", "") or (args.args[0] if getattr(args, "args", None) else "")
    if not archive:
        print("[athena] usage: athena import <backup.zip>")
        return 1
    try:
        print(f"[athena] {run_import(archive)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[athena] import failed: {exc}")
        return 1
