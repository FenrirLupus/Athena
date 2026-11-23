"""Box packages: off by default, name-only, user-approved, recorded.

The container's OS layer is the one environment that is *not* scoped to an artefact — a package installed for one
plugin is installed for all of them and for the server. So the mechanism exists, and the default is "no".

What is asserted:

  * with `security.allow_box_installs: false` a request is refused, with the reason
  * with it true, a request queues with `kind: "box"`, the package list, and a warning that it changes the box
  * a package name that is not a name (`;`, spaces, paths, options) is refused — nothing reaches apt
  * install runs `apt-get … --dry-run` in the test (no real change), records the attempt, and reports honestly
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from core import plugininstall

checks = {}

OFF = """security:
  allow_box_installs: false
plugins:
  enabled: []
tools:
  packs: []
"""

ON = """security:
  allow_box_installs: true
plugins:
  enabled: []
tools:
  packs: []
"""

with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    for folder in (athena / "plugins", athena / "tools", athena / "security",
                   athena / "profiles" / "probe" / "sandbox"):
        folder.mkdir(parents=True, exist_ok=True)
    (athena / "config.yaml").write_text(OFF, encoding="utf-8")

    # -- 1 · disabled by default ---------------------------------------
    checks["off_by_default"] = plugininstall._box_allowed(athena) is False
    try:
        plugininstall.request_box_install(["cowsay"], athena)
        checks["refused_when_disabled"] = False
    except Exception as exc:                          # noqa: BLE001
        checks["refused_when_disabled"] = "disabled on this installation" in str(exc)
    checks["nothing_queued_while_disabled"] = plugininstall.pending_installs(athena, athena / "plugins") == []

    # -- 2 · enabled: the request queues with a warning -----------------
    (athena / "config.yaml").write_text(ON, encoding="utf-8")
    checks["enabled_when_asked"] = plugininstall._box_allowed(athena) is True
    request = plugininstall.request_box_install(["cowsay"], athena, reason="a tool script calls cowsay")
    rows = plugininstall.pending_installs(athena, athena / "plugins")
    row = next((r for r in rows if r["kind"] == "box"), None)
    checks["box_request_queued"] = bool(row)
    checks["box_request_lists_packages"] = (row or {}).get("packages") == ["cowsay"]
    checks["box_request_warns_about_the_box"] = "affects every agent" in ((row or {}).get("warning") or "")
    checks["box_request_offers_install_and_cancel"] = (row or {}).get("actions") in (["install", "cancel"],
                                                                                   ["cancel"])

    # -- 3 · package names only ---------------------------------------
    for bad in ("cowsay; rm -rf /", "/usr/bin/evil", "--option", "pkg with space", "a" * 80):
        try:
            plugininstall.request_box_install([bad], athena)
            checks[f"refuses_{bad[:12]!r}"] = False
        except Exception as exc:                      # noqa: BLE001
            checks[f"refuses_{bad[:12]!r}"] = "not a package name" in str(exc)

    # -- 4 · a dry-run install records the attempt ---------------------
    before = plugininstall.pending_installs(athena, athena / "plugins")
    result = plugininstall.install_box(["definitely-not-a-real-package-xyz"], athena, dry_run=True, timeout=120)
    checks["dry_run_reports_failure_for_a_fake_package"] = result.get("ok") is False
    checks["dry_run_records_the_attempt"] = (athena / "security" / "box_installs.json").is_file()
    log = json.loads((athena / "security" / "box_installs.json").read_text(encoding="utf-8"))
    checks["log_marks_it_as_dry_run"] = log[-1]["dry_run"] is True
    checks["log_names_the_packages"] = log[-1]["packages"] == ["definitely-not-a-real-package-xyz"]

    # -- 5 · cancel leaves the box alone ------------------------------
    row = next(r for r in plugininstall.pending_installs(athena, athena / "plugins") if r["kind"] == "box")
    cancelled = plugininstall.resolve_install(row["id"], "cancel", athena, athena / "plugins")
    checks["cancel_reported"] = cancelled["decision"] == "denied"
    checks["cancel_did_not_install"] = not any(
        r["kind"] == "box" for r in plugininstall.pending_installs(athena, athena / "plugins"))

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
