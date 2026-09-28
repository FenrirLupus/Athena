"""Security — tamper detection.

A hash manifest of the files that define Athena's behavior. On boot (and
on demand) the scanner recomputes hashes and compares; any unexpected
change is reported so a third-party modification is caught, not silently
accepted.

Manifest: operations/manifest.json (kept outside the scanned sanctum so
an attacker modifying athena-system/ can't also rewrite the baseline).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.config import ATHENA_ROOT, DEFAULT_PROFILE_ROOT

# The zones whose integrity matters. athena-system/ is the core; config and
# auth define behavior; plugins are third-party surface (any change there
# is worth flagging too).
SCAN_ZONES = (
    ATHENA_ROOT / "athena-system",
    ATHENA_ROOT / "config.yaml",
    ATHENA_ROOT / "authentication.json",
    DEFAULT_PROFILE_ROOT / "plugins",
)

# Dynamic zones never hashed: metric/event logs change constantly by
# design (daily files at .athena/logs/ and .athena/events/), so they're
# not tamper signals. logs-archive/ holds historical files migrated off
# the live path (and any quarantined root junk) — data, not code.
IGNORED_ZONES = (
    ATHENA_ROOT / "logs",
    ATHENA_ROOT / "events",
    ATHENA_ROOT / "athena-system" / "logs-archive",
)

# The manifest lives in the profile's operations/ (the Operator's 08-12
# home layout): machinery, not conversation. Kept outside the scanned
# sanctum so an attacker modifying athena-system/ can't rewrite it.
MANIFEST_PATH = DEFAULT_PROFILE_ROOT / "operations" / "manifest.json"


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"file unreadable for hash: {path.name}: {exc}",
                  source="security", action="integrity_hash")
        h.update(b"<unreadable>")
    return h.hexdigest()


def _in_ignored(path: Path) -> bool:
    for zone in IGNORED_ZONES:
        try:
            path.resolve().relative_to(zone.resolve())
            return True
        except ValueError:
            continue
    return False


def scan_files() -> dict[str, str]:
    """Hash every tracked file: {relative_path: sha256}."""
    result: dict[str, str] = {}
    for zone in SCAN_ZONES:
        if zone.is_file():
            result[str(zone.relative_to(ATHENA_ROOT))] = _file_hash(zone)
        elif zone.is_dir():
            for path in sorted(zone.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts \
                        and not _in_ignored(path):
                    result[str(path.relative_to(ATHENA_ROOT))] = _file_hash(path)
    return result


def build_manifest() -> dict:
    """Write the current baseline manifest. Returns a summary."""
    files = scan_files()
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps({"version": 1, "files": files}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {"tracked": len(files), "manifest": str(MANIFEST_PATH)}


def scan() -> dict:
    """Compare current files to the baseline. Returns the integrity report."""
    if not MANIFEST_PATH.exists():
        return {
            "ok": False,
            "reason": "no baseline manifest — run build_manifest() first",
            "changed": [],
            "added": [],
            "missing": [],
        }
    try:
        baseline = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")).get("files", {})
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"manifest unreadable: {exc}", source="security",
                  action="integrity_scan")
        return {"ok": False, "reason": "manifest unreadable", "changed": [], "added": [], "missing": []}

    current = scan_files()

    changed = [p for p, h in baseline.items() if current.get(p) != h]
    added = [p for p in current if p not in baseline]
    missing = [p for p in baseline if p not in current]

    return {
        "ok": not (changed or added or missing),
        "changed": changed,
        "added": added,
        "missing": missing,
    }
