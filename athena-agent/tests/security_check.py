"""Two watchdogs, one baseline: does the split between inside and outside hold?

The claim under test is attribution: a changed file should be blamed on the **red team** (an agent or plugin reached
for it) only when the runtime ledger shows that attempt, and on the **blue team** (something outside) otherwise.

Six cases:

  1. clean tree, fresh baseline          → no findings
  2. file edited with nothing in the ledger → blue team, critical
  3. file edited after a recorded refusal   → red team, critical
  4. new plugin folder dropped in           → blue team, high
  5. file deleted                           → critical, whichever team
  6. permission bit changed                 → high

plus: the report says what an agent should *do*, and a missing baseline is itself a finding.
"""
import sys
import tempfile
import time
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import security

checks = {}


def build_tree(td: str) -> Path:
    athena = Path(td) / ".athena"
    for folder in ("athena-agent/core", "plugins/clock/scripts", "tools/system/terminal"):
        (athena / folder).mkdir(parents=True, exist_ok=True)
    (athena / "athena-agent" / "core" / "loop.py").write_text("# the turn loop\n", encoding="utf-8")
    (athena / "athena-agent" / "core" / "paths.py").write_text("# the guard\n", encoding="utf-8")
    (athena / "plugins" / "clock" / "PLUGIN.md").write_text("---\nname: clock\n---\n", encoding="utf-8")
    (athena / "plugins" / "clock" / "scripts" / "plugin.py").write_text("# plugin\n", encoding="utf-8")
    (athena / "tools" / "system" / "terminal" / "TOOL.md").write_text("---\nname: terminal\n---\n", encoding="utf-8")
    (athena / "config.yaml").write_text("model:\n  provider: mock\n", encoding="utf-8")
    return athena


with tempfile.TemporaryDirectory() as td:
    athena = build_tree(td)
    folder = athena / "security"
    ledger = security.Ledger(folder)
    baseline = security.Baseline(athena, folder)

    # -- 1. no baseline is itself a finding ------------------------------
    missing = security.check(athena, folder, ledger, baseline)
    checks["no_baseline_is_reported"] = len(missing) == 1 and missing[0]["kind"] == "no-baseline"

    built = baseline.build()
    checks["baseline_covers_critical_set"] = built["files"] >= 6
    clean = security.check(athena, folder, ledger, baseline)
    checks["clean_tree_has_no_findings"] = clean == []

    # -- a plugin's venv is generated, not installed: never hashed ----
    venv_dir = athena / "plugins" / "withvenv" / ".venv" / "lib" / "site-packages"
    venv_dir.mkdir(parents=True, exist_ok=True)
    (venv_dir / "six.py").write_text("# vendored\n", encoding="utf-8")
    (athena / "plugins" / "withvenv" / "PLUGIN.md").write_text("---\nname: withvenv\n---\n", encoding="utf-8")
    critical = [str(p.relative_to(athena)) for p in security._iter_critical(athena)]
    checks["venv_files_are_not_critical"] = not any(".venv" in c for c in critical)
    checks["plugin_descriptor_still_is"] = any("withvenv/PLUGIN.md" in c for c in critical)

    # -- 2. an outside edit ---------------------------------------------
    target = athena / "athena-agent" / "core" / "paths.py"
    target.write_text("# the guard, edited by something outside\n", encoding="utf-8")
    outside = security.check(athena, folder, ledger, baseline)
    checks["outside_edit_detected"] = any(f["kind"] == "modified" for f in outside)
    checks["outside_edit_is_blue"] = all(f["team"] == "blue" for f in outside if f["kind"] == "modified")
    checks["outside_edit_is_critical"] = any(f["severity"] == "critical" for f in outside)
    target.write_text("# the guard\n", encoding="utf-8")          # restore

    # -- 3. the same edit, but an agent reached for it first -------------
    ledger.record("red", "refused", "write: inside ATHENA_SYSTEM", str(target), "probe")
    target.write_text("# the guard, edited after a refused attempt\n", encoding="utf-8")
    inside = security.check(athena, folder, ledger, baseline)
    modified = [f for f in inside if f["kind"] == "modified"]
    checks["inside_edit_is_red"] = bool(modified) and all(f["team"] == "red" for f in modified)
    checks["inside_edit_explains_itself"] = any("internal attempt" in f["detail"] for f in modified)
    target.write_text("# the guard\n", encoding="utf-8")

    # -- 4. a dropped plugin folder -------------------------------------
    dropped = athena / "plugins" / "mystery"
    (dropped / "scripts").mkdir(parents=True)
    (dropped / "PLUGIN.md").write_text("---\nname: mystery\n---\n", encoding="utf-8")
    (dropped / "scripts" / "plugin.py").write_text("# unknown\n", encoding="utf-8")
    added = security.check(athena, folder, ledger, baseline)
    checks["dropped_plugin_detected"] = any(f["kind"] == "added" for f in added)
    checks["dropped_plugin_is_blue"] = all(f["team"] == "blue" for f in added if f["kind"] == "added")

    # -- 5. deletion -----------------------------------------------------
    (athena / "plugins" / "clock" / "scripts" / "plugin.py").unlink()
    removed = security.check(athena, folder, ledger, baseline)
    checks["deletion_detected"] = any(f["kind"] == "removed" for f in removed)
    checks["deletion_is_critical"] = any(f["severity"] == "critical" and f["kind"] == "removed" for f in removed)

    # -- 6. permissions --------------------------------------------------
    (athena / "athena-agent" / "core" / "loop.py").chmod(0o600)
    perms = security.check(athena, folder, ledger, baseline)
    checks["permission_change_detected"] = any(f["kind"] == "permissions" for f in perms)

    # -- the report is guidance, not just a list -------------------------
    text = security.report(perms, baseline.load(), len(ledger.recent()))
    checks["report_names_both_teams"] = "Red team" in text and "Blue team" in text
    checks["report_says_what_to_do"] = "what to do:" in text
    checks["report_mentions_the_baseline"] = "baseline:" in text

    # -- the watch thread writes REPORT.md and keeps last -----------------
    watch = security.SecurityWatch(athena, folder, interval_s=30)
    swept = watch.sweep()
    checks["watch_sweeps"] = isinstance(swept, dict) and "findings" in swept
    checks["watch_writes_report"] = (folder / security.REPORT_NAME).is_file()
    checks["watch_counts_by_team"] = set(swept["counts"]) == {"red", "blue"}

    # -- a polling client cannot flood the ledger ---------------------
    flood_folder = Path(td) / "flood"
    flood = security.Ledger(flood_folder)
    written = sum(1 for _ in range(50) if flood.record("blue", "inbound-401", "no token", "/approvals"))
    checks["identical_events_are_folded"] = written == 1
    # a long storm is summarised rather than silent: one row per hundred folded
    written += sum(1 for _ in range(250) if flood.record("blue", "inbound-401", "no token", "/approvals"))
    checks["storm_stays_bounded"] = written <= 4
    checks["suppressed_count_is_recorded"] = any(row.get("suppressed", 0) >= 100 for row in flood.recent())
    checks["a_different_path_still_records"] = flood.record("blue", "inbound-401", "no token", "/installs")
    checks["a_different_kind_still_records"] = flood.record("blue", "inbound-429", "rate", "/approvals")

    # -- and a clean sweep after re-baselining is quiet ------------------
    baseline.build()
    quiet = security.check(athena, folder, ledger, baseline)
    checks["rebaseline_clears_findings"] = quiet == []

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
