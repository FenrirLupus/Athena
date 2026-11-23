"""Vault sweep — copy aged rows out of the master into tiers, and seal completed years.

The master (`VAULT.db`) is never modified. Every tier is a copy, keyed by the row's own identity (`uid`), so a
re-run is free and an interrupted run is completed by the next one. Sealing writes one immutable file per
completed year: `vault-year-<YYYY>.db`, chmod 0444, with a manifest beside it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve()
for parent in ROOT.parents:
    if (parent / "core").is_dir() and (parent / "server").is_dir():
        TREE = parent
        break
else:
    raise SystemExit("cannot find the tree")

sys.path.insert(0, str(TREE))
from data.vault import Vault  # noqa: E402
from core import trace     # noqa: E402

LANE = "vault"
TIERS = (("vault-weekly.db", 7), ("vault-monthly.db", 30), ("vault-quarterly.db", 90), ("vault-annual.db", 365))


def rows_of(db: Path) -> list[dict]:
    if not db.is_file():
        return []
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute("SELECT channel, snapshot, role, content, ts, tool_call_id, name, created, uid FROM messages")
        return [dict(zip(("channel", "snapshot", "role", "content", "ts", "tool_call_id", "name", "created", "uid"), r))
                for r in cur.fetchall()]
    finally:
        conn.close()


def when(row: dict) -> datetime | None:
    for key in ("ts", "created"):
        value = str(row.get(key) or "")
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def sweep_lane(storage: Path, lane: str, now: datetime) -> dict:
    master = storage / "VAULT.db"
    if not master.is_file():
        master = storage / "vault.db"                     # pre-rename layout
    if not master.is_file():
        trace.event("vault.skipped", channel=LANE, lane=lane, reason="no master",
                    storage=str(storage), tried=("VAULT.db", "vault.db"))
        return {"lane": lane, "skipped": "no master"}
    with trace.span("vault.sweep", channel=LANE, lane=lane, master=master.name,
                    storage=str(storage.parent.parent), as_of=now.strftime("%Y-%m-%d")) as sp:
        rows = rows_of(master)
        moved = {}
        for name, days in TIERS:
            selected = [r for r in rows if (w := when(r)) and (now - w) <= timedelta(days=days)]
            if not selected:
                moved[name] = 0
                trace.event("vault.tier", level="debug", channel=LANE, lane=lane, tier=name, days=days, rows=0,
                            why="no rows inside the window")
                continue
            target = Vault(storage / name, lane)
            moved[name] = target.insert_messages(
                [{"role": r["role"], "content": r["content"], "ts": r["ts"], "name": r["name"],
                  "tool_call_id": r["tool_call_id"]} for r in selected], snapshot=name)
            detail = {"tier": name, "days": days, "rows": moved[name], "selected": len(selected)}
            if trace.enabled("debug"):
                db = storage / name
                detail["bytes"] = db.stat().st_size if db.is_file() else 0
                detail["undated"] = len(rows) - sum(1 for r in rows if when(r))
            trace.event("vault.tier", level="debug", channel=LANE, lane=lane, **detail)
        sp.ok(master_rows=len(rows), rows_moved=sum(moved.values()), tiers=len(TIERS),
              tiers_written=sum(1 for n in moved.values() if n))
    return {"lane": lane, "master_rows": len(rows), "tiers": moved}


def seal_years(storage: Path, lane: str, now: datetime) -> list[dict]:
    sealed = []
    rows = rows_of(storage / "VAULT.db")
    years = sorted({w.year for r in rows if (w := when(r)) and w.year < now.year})
    for year in years:
        target = storage / f"vault-year-{year}.db"
        if target.is_file():
            trace.event("vault.seal.skipped", level="debug", channel=LANE, lane=lane, year=year,
                        reason="already sealed", path=target.name)
            continue
        picked = [r for r in rows if (w := when(r)) and w.year == year]
        if not picked:
            trace.event("vault.seal.skipped", level="debug", channel=LANE, lane=lane, year=year,
                        reason="no dated rows in that year")
            continue
        with trace.span("vault.seal", channel=LANE, lane=lane, year=year, rows=len(picked)) as sp:
            vault = Vault(target, lane)
            vault.insert_messages([{"role": r["role"], "content": r["content"], "ts": r["ts"], "name": r["name"],
                                    "tool_call_id": r["tool_call_id"]} for r in picked], snapshot=f"year-{year}")
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            manifest = {"year": year, "rows": len(picked), "sha256": digest,
                        "min_ts": min(str(r["ts"]) for r in picked), "max_ts": max(str(r["ts"]) for r in picked),
                        "sealed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            (storage / f"vault-year-{year}.manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.chmod(target, 0o444)
            sealed.append(manifest)
            sp.ok(sha256=digest[:12], bytes=target.stat().st_size, mode="0444")
    return sealed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Copy aged vault rows into tiers and seal completed years.")
    ap.add_argument("--athena", default=str(TREE.parent))
    ap.add_argument("--now", default="", help="ISO timestamp to treat as now (for tests)")
    ap.add_argument("--lane", default="", help="one lane only")
    args = ap.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    profiles = Path(args.athena) / "profiles"
    trace.event("vault.start", channel=LANE, athena=str(Path(args.athena)), lane=args.lane or "*",
                as_of=now.isoformat())
    report = []
    skipped = []
    for lane_dir in sorted(p for p in profiles.glob("*") if p.is_dir()):
        if args.lane and lane_dir.name != args.lane:
            continue
        storage = lane_dir / "sessions" / "storage"
        if not storage.is_dir():
            skipped.append(lane_dir.name)
            trace.event("vault.skipped", channel=LANE, lane=lane_dir.name, reason="no storage",
                        path=str(storage))
            continue
        report.append(sweep_lane(storage, lane_dir.name, now))
        report[-1]["sealed"] = seal_years(storage, lane_dir.name, now)
    trace.event("vault.done", channel=LANE, lanes=len(report), skipped=len(skipped),
                rows_moved=sum(sum(r.get("tiers", {}).values()) for r in report),
                sealed=sum(len(r.get("sealed") or []) for r in report))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
