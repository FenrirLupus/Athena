"""Every lane on this machine must be able to start — and a broken one must fail the suite, naming itself."""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from config import loader
from core import lanediagnose

CHECKS = {}
paths = loader.paths(loader.load())
rows = lanediagnose.diagnose_all(paths["agents"])
CHECKS["lanes_found"] = len(rows) >= 1
CHECKS["lanes_parse_and_resolve"] = all(row["ok"] for row in rows) if rows else True
for row in rows:
    if not row["ok"]:
        print(f"    {row['lane']}: {'; '.join(row['problems'])}")

# a temporary lane that cannot parse fails, and names the line
with tempfile.TemporaryDirectory() as td:
    broken = Path(td) / "ghost"
    broken.mkdir()
    (broken / "config.yaml").write_text("model:\n  b: [\n", encoding="utf-8")
    row = lanediagnose.diagnose(broken)
    CHECKS["broken_lane_fails_with_a_line"] = (not row["ok"]
                                               and any("line 3" in problem for problem in row["problems"]))
    all_rows = lanediagnose.diagnose_all(td)
    CHECKS["diagnose_all_reports_it"] = len(all_rows) == 1 and all_rows[0]["lane"] == "ghost"

# a lane with no config at all is legal: it inherits the global provider and must not fail
with tempfile.TemporaryDirectory() as td:
    bare = Path(td) / "bare"
    bare.mkdir()
    row = lanediagnose.diagnose(bare)
    CHECKS["a_lane_with_no_config_is_not_a_failure"] = row["ok"] or not row["config"]["present"]

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT: {'PASS' if all(CHECKS.values()) else 'FAIL'}"
      f" ({len(rows)} lane{'s' if len(rows) != 1 else ''})")
sys.exit(0 if all(CHECKS.values()) else 1)
