"""Installation: staged in the sandbox, installed by the user, verified before it is trusted.

The claims, each of which would be a lie if the test passed without it:

* `--force` replaces, and the replaced version is archived rather than lost"""
import importlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import plugininstall

checks = {}

CONFIG = """model:
  provider: mock
  name: mock-1
  window: 8192

plugins:
  # Plugins to load. A folder is not permission — a name on this list is.
  enabled: []
"""

MANIFEST = """---
name: demo
description: a demo plugin used by the installer test
entry: scripts/plugin.py
provides: tools
---

# demo
"""

ENTRY = '''from tools.registry import Tool


def register(registry, ctx):
    registry.register(Tool("demo_tool", "answers", {"type": "object", "properties": {}, "required": []},
                           lambda **_: {"demo": True}))
'''

BROKEN_ENTRY = '''def register(registry, ctx):
    raise RuntimeError("this plugin cannot load")
'''


def make_stage(root: Path, name="demo", manifest=MANIFEST, entry=ENTRY, entry_path="scripts/plugin.py"):
    folder = root / "staging" / name
    (folder / Path(entry_path).parent).mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (folder / "PLUGIN.md").write_text(manifest, encoding="utf-8")
    (folder / entry_path).write_text(entry, encoding="utf-8")
    return folder


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    (athena / "plugins").mkdir(parents=True)
    (athena / "security").mkdir()
    (athena / "profiles" / "probe" / "sandbox").mkdir(parents=True)
    (athena / "config.yaml").write_text(CONFIG, encoding="utf-8")
    staged = make_stage(athena / "profiles" / "probe" / "sandbox")

    # -- install ---------------------------------------------------------
    result = plugininstall.install(staged, athena / "plugins", athena)
    checks["installs_into_plugins"] = (athena / "plugins" / "demo" / "PLUGIN.md").is_file()
    checks["reports_file_count"] = result["files"] >= 2
    checks["enabled_in_config"] = "enabled: [demo]" in (athena / "config.yaml").read_text(encoding="utf-8")

    record = json.loads((athena / "security" / "installs.json").read_text(encoding="utf-8"))
    checks["install_record_has_hashes"] = "demo" in record and "scripts/plugin.py" in record["demo"]["files"]
    checks["record_names_the_source"] = str(staged) in record["demo"]["source"]

    smoke = result.get("smoke") or {}
    checks["smoke_loaded_the_plugin"] = smoke.get("loaded") is True
    checks["smoke_saw_the_tool"] = smoke.get("tools") == ["demo_tool"]
    checks["smoke_ran_isolated"] = smoke.get("isolation") == "process"

    # -- refusals --------------------------------------------------------
    def refused(fn):
        try:
            fn()
            return False
        except Exception:
            return True

    no_manifest = make_stage(athena / "profiles" / "probe" / "sandbox", name="nomanifest", manifest=None)
    checks["refuses_without_manifest"] = refused(
        lambda: plugininstall.install(no_manifest, athena / "plugins", athena))

    mismatched = make_stage(athena / "profiles" / "probe" / "sandbox", name="othername")
    checks["refuses_name_mismatch"] = refused(
        lambda: plugininstall.install(mismatched, athena / "plugins", athena))

    checks["refuses_missing_entry"] = refused(lambda: plugininstall.install(
        make_stage(athena / "profiles" / "probe" / "sandbox", name="noentry", manifest=MANIFEST.replace(
            "scripts/plugin.py", "scripts/nope.py")), athena / "plugins", athena))

    checks["refuses_overwrite_without_force"] = refused(
        lambda: plugininstall.install(staged, athena / "plugins", athena))

    # -- a broken plugin is rolled back ----------------------------------
    broken = make_stage(athena / "profiles" / "probe" / "sandbox", name="broken",
                        manifest=MANIFEST.replace("name: demo", "name: broken"), entry=BROKEN_ENTRY)
    broken_result = plugininstall.install(broken, athena / "plugins", athena)
    checks["broken_plugin_flagged"] = broken_result["smoke"]["loaded"] is False
    checks["broken_plugin_rolled_back"] = broken_result["enabled"].startswith("rolled back")
    checks["broken_plugin_not_enabled"] = "broken" not in (athena / "config.yaml").read_text(encoding="utf-8")

    # -- force replaces, and keeps the old one ---------------------------
    replacement = make_stage(athena / "profiles" / "probe" / "sandbox", name="demo",
                             manifest=MANIFEST.replace("a demo plugin used by the installer test", "version two"))
    forced = plugininstall.install(replacement, athena / "plugins", athena, force=True)
    checks["force_replaces"] = "version two" in (athena / "plugins" / "demo" / "PLUGIN.md").read_text(encoding="utf-8")
    checks["force_archives_the_old_one"] = Path(forced.get("replaced_backup", "")).is_dir()

    # -- installed listing ----------------------------------------------
    rows = plugininstall.installed(athena / "plugins", athena)
    checks["lists_installed"] = any(row["name"] == "demo" and row["present"] for row in rows)

    # -- uninstall -------------------------------------------------------
    removal = plugininstall.uninstall("demo", athena / "plugins", athena)
    checks["uninstall_disabled"] = removal["enabled"] == "disabled"
    checks["uninstall_removed_folder"] = not (athena / "plugins" / "demo").exists()
    checks["uninstall_archived"] = (Path(removal["archived_at"]) / "plugin" / "PLUGIN.md").is_file()
    checks["uninstall_wrote_manifest"] = (Path(removal["archived_at"]) / "MANIFEST.sha256").is_file()
    checks["uninstall_dropped_record"] = "demo" not in json.loads(
        (athena / "security" / "installs.json").read_text(encoding="utf-8"))
    checks["config_clean_after_uninstall"] = "enabled: []" in (athena / "config.yaml").read_text(encoding="utf-8")

    # -- the config edit kept the file parseable -------------------------
    text = (athena / "config.yaml").read_text(encoding="utf-8")
    checks["config_still_has_its_comments"] = "a name on this list is" in text

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
