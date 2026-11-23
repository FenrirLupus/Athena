"""One-time backfill: replay every lane's snapshots and live window into its vault.

The vault's completeness was tied to compression, which fires when the window fills — months apart at a
1,048,576-token window. This closes the historical gap once, idempotently: every row's identity is a hash of its
own text, so a re-run writes nothing and an interrupted run is finished by the next one.

    python3 scripts/vault_archive.py --athena ~/.athena [--lane default]

Exit codes: 0 every lane complete · 2 a lane still has rows the vault does not hold.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve()
for parent in ROOT.parents:
    if (parent / "core").is_dir() and (parent / "server").is_dir():
        TREE = parent
        break
else:
    raise SystemExit("cannot find the tree")

sys.path.insert(0, str(TREE))

from core import archive  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay snapshots and the live window into the vault.")
    parser.add_argument("--athena", default=str(TREE.parent))
    parser.add_argument("--lane", default="", help="one lane only")
    args = parser.parse_args(argv)

    profiles = Path(args.athena) / "profiles"
    reports = []
    for lane_dir in sorted(p for p in profiles.glob("*") if p.is_dir()):
        if args.lane and lane_dir.name != args.lane:
            continue
        if not (lane_dir / "sessions").is_dir():
            continue
        reports.append(archive.backfill(lane_dir, lane_dir.name))
    print(json.dumps(reports, indent=2))
    incomplete = [r for r in reports if r.get("missing_after")]
    if incomplete:
        for r in incomplete:
            print(f"INCOMPLETE {r['lane']}: {r['missing_after']} row(s) still missing from the vault",
                  file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
