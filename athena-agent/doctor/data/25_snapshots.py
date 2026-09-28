"""Snapshots test — immutable 3-version window, nurse-gated restore."""
from __future__ import annotations

import re
import tempfile
import time
from pathlib import Path


def run() -> list[dict]:
    from data import snapshots
    from data.snapshots import (snapshot, list_snapshots, restore, prune,
                                window_status, parse_version, SNAPSHOT_DIR)

    checks = []
    original_dir = snapshots.SNAPSHOT_DIR
    original_window = snapshots.WINDOW
    original_system = snapshots.SYSTEM_DIR
    with tempfile.TemporaryDirectory() as td:
        tmp_root = Path(td)
        snapshots.SNAPSHOT_DIR = tmp_root / "snapshots"
        snapshots.WINDOW = 3
        # The restore target is the snapshot's OWN temp system dir — the
        # test must NEVER write into the real athena-system tree (a full
        # doctor run restores over live code otherwise).
        snapshots.SYSTEM_DIR = tmp_root / "system"
        (snapshots.SYSTEM_DIR / "core").mkdir(parents=True, exist_ok=True)
        (snapshots.SYSTEM_DIR / "core" / "placeholder.py").write_text(
            "# seed", encoding="utf-8")
        try:
            s = snapshot()
            # snapshot() returns "path (N files)" — take the path part.
            snap_path = s.split(" (")[0]
            # format: {date}_{time}_{version}_snapshot.zip
            ok_name = re.match(
                r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_0\.1\.0_snapshot\.zip$",
                Path(snap_path).name,
            )
            checks.append({
                "name": "snapshot filename format",
                "status": "ok" if ok_name else "fail",
                "detail": Path(snap_path).name,
            })
            snaps = list_snapshots()
            checks.append({
                "name": "snapshot listed",
                "status": "ok" if len(snaps) == 1 else "fail",
                "detail": f"{len(snaps)} snapshot(s)",
            })
            # Immutability: each snapshot gets a UNIQUE name (never
            # overwrites a previous one — the write-once contract).
            first_path = list_snapshots()[0]["path"]
            time.sleep(1.1)  # distinct timestamp so names differ
            s2 = snapshot(version="0.1.0")  # same version, new timestamp
            s2_path = s2.split(" (")[0]
            checks.append({
                "name": "immutable (unique names, no overwrite)",
                "status": "ok" if s2_path != first_path else "fail",
                "detail": "unique per timestamp",
            })
            # Restore gate: only the nurse.
            r = restore(snaps[0]["path"], agent="some-agent")
            checks.append({
                "name": "restore refused for non-nurse",
                "status": "ok" if "refused" in r.get("detail", "") else "fail",
                "detail": r.get("detail", "")[:40],
            })
            r = restore(snaps[0]["path"], agent="nurse")
            checks.append({
                "name": "restore works for nurse",
                "status": "ok" if r.get("restored", 0) > 0 else "fail",
                "detail": f"restored {r.get('restored', 0)} files",
            })
            # The 3-version window: 4 snapshots → 3 kept (oldest evicted).
            for v in ["0.1.1", "0.2.0", "0.3.0"]:
                time.sleep(1.1)
                snapshot(version=v)
            status = window_status()
            kept_names = status["snapshots"]
            checks.append({
                "name": "3-version window keeps 3",
                "status": "ok" if status["kept"] == 3 and status["within_window"] else "fail",
                "detail": f"kept={status['kept']} of {status['window']}",
            })
            checks.append({
                "name": "oldest evicted, latest kept",
                "status": "ok" if "0.1.0_snapshot" not in " ".join(kept_names)
                and kept_names[0].endswith("0.3.0_snapshot.zip") else "fail",
                "detail": kept_names[0] if kept_names else "none",
            })
            checks.append({
                "name": "parse_version reads version",
                "status": "ok" if parse_version(kept_names[0]) == "0.3.0" else "fail",
                "detail": parse_version(kept_names[0]) if kept_names else "?",
            })
            evicted = prune(3)
            checks.append({
                "name": "prune within window is a no-op",
                "status": "ok" if evicted == [] else "fail",
                "detail": f"evicted={evicted}",
            })
        finally:
            snapshots.SNAPSHOT_DIR = original_dir
            snapshots.WINDOW = original_window
            snapshots.SYSTEM_DIR = original_system
    return checks
