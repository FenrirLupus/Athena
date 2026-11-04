"""Shared stack and lockfiles: share the libraries, pin the versions.

Four claims, each checkable:

  1. a plugin venv **sees** the server's libraries without installing them (a `.pth` seed, read-only)
  2. the first install **writes** `requirements.lock` from what resolved
  3. a plugin that ships a lock installs **from the lock** — the pinned version, not the newest
  4. the shared stack and the lock are **recorded**, so a later change is visible rather than mysterious

Uses `six` (tiny) and the server venv's `httpx` (already there) so nothing exotic is required.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import plugininstall, plugins
from tools.registry import Registry

checks = {}

CONFIG = """plugins:
  enabled: []
"""

MANIFEST = """---
name: {name}
description: dependency and lock behaviour
entry: scripts/plugin.py
provides: tools
{requires}---

# {name}
"""

# imports a package it did NOT declare (shared) and one it did
ENTRY = '''from tools.registry import Tool


def register(registry, ctx):
    def probe(**_):
        import six
        import httpx
        return {"six": six.__version__, "httpx": httpx.__version__}

    registry.register(Tool("probe_both", "reads a declared and a shared package",
                           {"type": "object", "properties": {}, "required": []}, probe))
'''


def stage(root: Path, name: str, requires: str = "", lock: str | None = None) -> Path:
    folder = root / "sandbox" / "staging" / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "PLUGIN.md").write_text(MANIFEST.format(name=name, requires=requires), encoding="utf-8")
    (folder / "scripts" / "plugin.py").write_text(ENTRY, encoding="utf-8")
    if lock is not None:
        (folder / "requirements.lock").write_text(lock, encoding="utf-8")
    return folder


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    plugins_root = athena / "plugins"
    for folder in (plugins_root, athena / "security", athena / "profiles" / "probe" / "sandbox"):
        folder.mkdir(parents=True, exist_ok=True)
    (athena / "config.yaml").write_text(CONFIG, encoding="utf-8")

    # -- 1 & 2 · declared dependency, no lock --------------------------
    staged = stage(athena / "profiles" / "probe", "sharer", 'requires: ["six"]\n')
    result = plugininstall.install(staged, plugins_root, athena)
    env = result.get("environment") or {}

    checks["venv_created"] = env.get("venv") is True
    checks["shared_stack_recorded"] = bool(env.get("shared_stack"))
    checks["lock_written_on_first_install"] = env.get("lock_written") is True
    lock_file = plugins_root / "sharer" / "requirements.lock"
    checks["lock_on_disk"] = lock_file.is_file()
    checks["lock_pins_the_resolved_version"] = any(line.startswith("six==") for line in
                                                   lock_file.read_text(encoding="utf-8").splitlines())
    checks["nothing_installed_into_the_shared_venv"] = not any(
        "six" in str(p) for p in (Path(env.get("shared_stack") or "/nonexistent")).glob("six*"))

    # -- 1 · the shared library is visible without being installed -----
    registry = Registry()
    report = plugins.load_all({"plugins": {"enabled": ["sharer"]}}, registry, None, athena)
    loaded = [row for row in report["loaded"] if row["name"] == "sharer"]
    checks["plugin_loads"] = bool(loaded)
    called = registry.invoke("probe_both", {}) if "probe_both" in registry.names() else ""
    checks["declared_package_works"] = '"six":' in called
    checks["shared_package_works"] = '"httpx":' in called      # httpx was never declared
    venv_root = plugins_root / "sharer" / ".venv"
    checks["shared_package_not_duplicated"] = not any(
        path.name.startswith("httpx") for path in venv_root.rglob("*") if path.is_dir())

    # -- 3 · a shipped lock decides the version ------------------------
    pinned = stage(athena / "profiles" / "probe", "pinned", 'requires: ["six"]\n', lock="six==1.16.0\n")
    pinned_result = plugininstall.install(pinned, plugins_root, athena)
    pinned_env = pinned_result.get("environment") or {}
    checks["installed_from_lock"] = pinned_env.get("from_lock") is True
    checks["did_not_rewrite_the_lock"] = pinned_env.get("lock_written") is False
    pinned_python = plugins_root / "pinned" / ".venv" / "bin" / "python3"
    import subprocess
    version = subprocess.run([str(pinned_python), "-c", "import six; print(six.__version__)"],
                             capture_output=True, text=True)
    checks["lock_pinned_the_older_version"] = version.stdout.strip() == "1.16.0"

    # -- 4 · the request reports the lock, so the card can say so ------
    staged_again = stage(athena / "profiles" / "probe", "pending_pin", 'requires: ["six"]\n', lock="six==1.16.0\n")
    plugininstall.request_install(staged_again, athena)
    row = next(r for r in plugininstall.pending_installs(athena, plugins_root) if r["name"] == "pending_pin")
    checks["pending_reports_the_lock"] = (row.get("lock") or {}).get("present") is True
    checks["pending_counts_locked_packages"] = (row.get("lock") or {}).get("packages") == 1
    checks["pending_carries_the_lock_hash"] = bool((row.get("lock") or {}).get("sha256"))

    # -- 5 · an unsatisfiable lock fails and rolls back ----------------
    broken = stage(athena / "profiles" / "probe", "badpin", 'requires: ["six"]\n', lock="six==99.99.99\n")
    broken_result = plugininstall.install(broken, plugins_root, athena)
    checks["bad_lock_reports_an_error"] = bool((broken_result.get("environment") or {}).get("error"))
    checks["bad_lock_rolled_back"] = broken_result["enabled"].startswith("rolled back")
    checks["bad_lock_not_enabled"] = "badpin" not in (athena / "config.yaml").read_text(encoding="utf-8")

    # -- 6 · the install record carries both facts --------------------
    record = json.loads((athena / "security" / "installs.json").read_text(encoding="utf-8"))["sharer"]
    checks["record_stores_the_shared_stack"] = bool((record.get("environment") or {}).get("shared_stack"))
    checks["record_stores_the_lock"] = bool((record.get("environment") or {}).get("lock_written"))

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
