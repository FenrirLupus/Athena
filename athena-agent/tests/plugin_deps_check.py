"""Per-plugin dependencies: declared in the manifest, installed by the installer, used by the child.

The claim being tested is narrow and checkable:

  * a plugin that declares `requires:` gets its own `.venv` inside its folder, built at install time
  * the plugin's code **runs on that interpreter** — the server's venv does not have the package, so a passing call
    proves the venv is what made it work
  * a manifest that demands something uninstallable is refused and **rolled back**, not left half-enabled
  * uninstall takes the environment with it (archived, not deleted)
  * a plugin with no `requires:` gets no venv at all — no cost for the common case

The one package used is `six`: tiny, stable, and absent from the server's venv, which is what makes the proof work.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import plugininstall, plugins
from tools.registry import Registry

checks = {}

CONFIG = """plugins:
  # a name on this list is the permission
  enabled: []
"""

MANIFEST = """---
name: {name}
description: uses a declared dependency
entry: scripts/plugin.py
provides: tools
{requires}---

# {name}
"""

ENTRY = '''from tools.registry import Tool


def register(registry, ctx):
    def version(**_):
        import six                      # only present in this plugin's venv
        return {"six": six.__version__}

    registry.register(Tool("dep_probe", "reports the dependency version",
                           {"type": "object", "properties": {}, "required": []}, version))
'''


def stage(root: Path, name: str, requires: str) -> Path:
    folder = root / "sandbox" / "staging" / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "PLUGIN.md").write_text(MANIFEST.format(name=name, requires=requires), encoding="utf-8")
    (folder / "scripts" / "plugin.py").write_text(ENTRY, encoding="utf-8")
    return folder


# -- the server's venv must NOT already have the package, or this proves nothing
try:
    import six                                                  # noqa: F401
    checks["precondition_server_lacks_six"] = False
except ImportError:
    checks["precondition_server_lacks_six"] = True

with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    plugins_root = athena / "plugins"
    for folder in (plugins_root, athena / "security", athena / "profiles" / "probe" / "sandbox"):
        folder.mkdir(parents=True, exist_ok=True)
    (athena / "config.yaml").write_text(CONFIG, encoding="utf-8")

    # -- 1 · a plugin that declares a dependency ------------------------
    staged = stage(athena / "profiles" / "probe", "depful", "requires: [\"six\"]\n")
    result = plugininstall.install(staged, plugins_root, athena)

    env = result.get("environment") or {}
    checks["venv_created"] = env.get("venv") is True
    checks["venv_on_disk"] = (plugins_root / "depful" / ".venv" / "bin" / "python3").exists()
    checks["package_recorded"] = any(str(p).startswith("six") for p in env.get("packages") or [])
    checks["plugin_enabled"] = result["enabled"] == "enabled"
    checks["record_holds_the_environment"] = "environment" in json.loads(
        (athena / "security" / "installs.json").read_text(encoding="utf-8"))["depful"]

    # -- 2 · the child runs on the plugin's interpreter -----------------
    registry = Registry()
    report = plugins.load_all({"plugins": {"enabled": ["depful"]}}, registry, None, athena)
    loaded = [row for row in report["loaded"] if row["name"] == "depful"]
    checks["plugin_loads"] = bool(loaded)
    checks["isolation_is_process"] = (loaded[0].get("isolation") == "process") if loaded else False
    called = registry.invoke("dep_probe", {}) if "dep_probe" in registry.names() else ""
    checks["dependency_importable_in_the_child"] = "six" in called and '"six":' in called
    checks["server_still_lacks_it"] = "ImportError" not in called and "No module named" not in called

    # -- 3 · the venv is what made the difference ----------------------
    plain = subprocess.run([sys.executable, "-c", "import six"], capture_output=True, text=True)
    checks["installed_without_touching_the_server"] = plain.returncode != 0

    # -- 4 · an uninstallable requirement is rolled back ---------------
    broken = stage(athena / "profiles" / "probe", "needy", "requires: [\"definitely-not-a-package-xyz==9.9.9\"]\n")
    broken_result = plugininstall.install(broken, plugins_root, athena)
    checks["bad_dependency_flagged"] = bool((broken_result.get("environment") or {}).get("error"))
    checks["bad_dependency_rolled_back"] = broken_result["enabled"].startswith("rolled back")
    checks["bad_dependency_not_enabled"] = "needy" not in (athena / "config.yaml").read_text(encoding="utf-8")

    # -- 5 · no requirements, no venv ----------------------------------
    plain_manifest = stage(athena / "profiles" / "probe", "plain", "")
    plain_result = plugininstall.install(plain_manifest, plugins_root, athena)
    checks["no_requires_no_venv"] = (plain_result.get("environment") or {}).get("venv") is False

    # -- 6 · uninstall takes the environment with it -------------------
    removal = plugininstall.uninstall("depful", plugins_root, athena)
    checks["uninstall_archived_the_venv"] = (Path(removal["archived_at"]) / "plugin" / ".venv").exists()
    checks["uninstall_removed_the_folder"] = not (plugins_root / "depful").exists()

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
