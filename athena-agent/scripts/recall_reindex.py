"""Build or feed a lane's recall index.

Two modes:

    --rebuild    delete the file, recreate it and feed every row in transcript order (a cold profile, or after
                 the schema changes)
    (default)    feed whatever is missing — the incremental pass a cold start or a repair needs

It refuses to index while the vault is incomplete, and exits 3 saying so. That refusal is the whole point: an
index over a store with holes answers by omission, and a recall system that silently misses rows is worse than one
that pays for every answer.

    python3 scripts/recall_reindex.py --athena ~/.athena [--lane default] [--rebuild] [--verify]

Exit codes: 0 indexed · 2 the index is behind (nothing to do is 0) · 3 refused, vault incomplete.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve()
for parent in ROOT.parents:
    if (parent / "core").is_dir() and (parent / "server").is_dir():
        TREE = parent
        break
else:
    raise SystemExit("cannot find the tree")

sys.path.insert(0, str(TREE))

from config import loader          # noqa: E402
from core import archive           # noqa: E402
from data.index import Index       # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build or feed a lane's recall index.")
    parser.add_argument("--athena", default=str(TREE.parent))
    parser.add_argument("--lane", default="")
    parser.add_argument("--rebuild", action="store_true", help="delete and rebuild from scratch")
    parser.add_argument("--verify", action="store_true", help="assert indexed ⊆ vault")
    args = parser.parse_args(argv)

    cfg = loader.load()
    ceiling = int((cfg.get("recall") or {}).get("index_max_rows") or 0)
    profiles = Path(args.athena) / "profiles"
    reports = []
    refused = 0
    for lane_dir in sorted(p for p in profiles.glob("*") if p.is_dir()):
        if args.lane and lane_dir.name != args.lane:
            continue
        if not (lane_dir / "sessions").is_dir():
            continue
        held = archive.missing(lane_dir, lane_dir.name)
        if held:
            print(f"index refused: {held} row(s) missing from the vault for {lane_dir.name} — "
                  f"run scripts/vault_archive.py first", file=sys.stderr)
            refused += 1
            continue
        index = Index.for_lane(lane_dir, lane_dir.name)
        if args.rebuild:
            index.clear()
        index.set_ceiling(ceiling)
        started = time.perf_counter()
        rows = archive.transcript_rows(lane_dir)
        written = index.add(rows)
        elapsed = round(time.perf_counter() - started, 3)
        size = index.path.stat().st_size if index.path.is_file() else 0
        reports.append({"lane": lane_dir.name, "transcript": len(rows), "indexed": index.count(),
                        "written": written, "db_bytes": size, "elapsed_s": elapsed})
    print(json.dumps(reports, indent=2))
    if refused:
        return 3
    if args.verify:
        for report in reports:
            lane_dir = profiles / report["lane"]
            have = archive.vault_uids(lane_dir / "sessions" / "storage")
            index = Index.for_lane(lane_dir, report["lane"])
            indexed = index.uids()
            extra = indexed - have
            print(f"{report['lane']}: indexed ⊆ vault: {'ok' if not extra else f'{len(extra)} row(s) ahead'}")
            if extra:
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
