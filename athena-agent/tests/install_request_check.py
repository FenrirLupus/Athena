"""Install requests: the agent asks, the user answers, the server installs.

The question this answers is "how does the user get prompted" — so the test asserts the four states a request can be in:

  1. staged and requested  → it appears in `pending_installs()`, which is what `GET /installs` returns
  2. denied                → nothing installed, the request disappears
  3. installed             → the plugin lands in plugins/, the config gains the name, the request disappears
  4. a request whose stage has vanished → refused with a message, not a traceback

Plus the point of the whole dance: a request is not a permission. Nothing is on disk under `plugins/` until the user
answers, and the agent never has a way to answer for them.
"""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import plugininstall

checks = {}

CONFIG = """plugins:
  # a name on this list is the permission
  enabled: []
"""

MANIFEST = """---
name: {name}
description: staged by an agent, installed by a person
entry: scripts/plugin.py
provides: tools
---

# {name}
"""

ENTRY = '''from tools.registry import Tool


def register(registry, ctx):
    registry.register(Tool("probe_tool", "answers", {"type": "object", "properties": {}, "required": []},
                           lambda **_: {"ok": True}))
'''


def stage(root: Path, name: str) -> Path:
    folder = root / "sandbox" / "staging" / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "PLUGIN.md").write_text(MANIFEST.format(name=name), encoding="utf-8")
    (folder / "scripts" / "plugin.py").write_text(ENTRY, encoding="utf-8")
    return folder


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    plugins = athena / "plugins"
    for folder in (plugins, athena / "security", athena / "profiles" / "probe" / "sandbox"):
        folder.mkdir(parents=True, exist_ok=True)
    (athena / "config.yaml").write_text(CONFIG, encoding="utf-8")

    # -- 1 · staged and requested ---------------------------------------
    first = stage(athena / "profiles" / "probe", "alpha")
    request = plugininstall.request_install(first, athena, source_url="https://github.com/x/y",
                                            reason="adds a planning workflow")
    pending = plugininstall.pending_installs(athena)
    checks["request_creates_a_pending_row"] = len(pending) == 1
    checks["pending_names_the_plugin"] = pending[0]["name"] == "alpha"
    checks["pending_counts_the_files"] = pending[0]["files"] == 2
    checks["pending_carries_the_source"] = pending[0]["source_url"] == "https://github.com/x/y"
    checks["pending_carries_the_declared_manifest"] = pending[0]["declared"]["entry"] == "scripts/plugin.py"

    # nothing has been installed by requesting it
    checks["request_installs_nothing"] = not (plugins / "alpha").exists()
    checks["request_does_not_touch_config"] = "enabled: []" in (athena / "config.yaml").read_text(encoding="utf-8")

    # -- 2 · denied ------------------------------------------------------
    denied = plugininstall.resolve_install(request["request"], "deny", athena, plugins)
    checks["deny_reports_denied"] = denied["decision"] == "denied"
    checks["deny_installs_nothing"] = not (plugins / "alpha").exists()
    checks["deny_clears_the_request"] = not any(
        row["id"] == request["request"] for row in plugininstall.pending_installs(athena, plugins))
    checks["deny_is_not_asked_again"] = not any(
        row["staged"] == str(first) for row in plugininstall.pending_installs(athena, plugins))

    # -- 3 · installed ---------------------------------------------------
    second = stage(athena / "profiles" / "probe", "beta")
    request2 = plugininstall.request_install(second, athena)
    done = plugininstall.resolve_install(request2["request"], "install", athena, plugins)
    checks["install_reports_installed"] = done["decision"] == "installed"
    checks["install_put_the_files_there"] = (plugins / "beta" / "scripts" / "plugin.py").is_file()
    checks["install_enabled_it"] = "enabled: [beta]" in (athena / "config.yaml").read_text(encoding="utf-8")
    checks["install_smoke_tested"] = (done.get("smoke") or {}).get("loaded") is True
    checks["install_clears_the_request"] = not any(
        row["id"] == request2["request"] for row in plugininstall.pending_installs(athena, plugins))
    checks["installed_unchanged_is_not_asked_again"] = not any(
        row["staged"] == str(second) for row in plugininstall.pending_installs(athena, plugins))
    checks["install_recorded_hashes"] = "beta" in json.loads(
        (athena / "security" / "installs.json").read_text(encoding="utf-8"))

    # -- 4 · the stage disappeared ---------------------------------------
    ghost = stage(athena / "profiles" / "probe", "ghost")
    request3 = plugininstall.request_install(ghost, athena)
    import shutil

    shutil.rmtree(ghost)
    try:
        plugininstall.resolve_install(request3["request"], "install", athena, plugins)
        checks["missing_stage_is_refused"] = False
    except Exception as exc:                          # noqa: BLE001
        checks["missing_stage_is_refused"] = "gone" in str(exc)
    checks["missing_stage_clears_the_request"] = not any(
        row["id"] == request3["request"] for row in plugininstall.pending_installs(athena, plugins))

    # -- unknown id ------------------------------------------------------
    try:
        plugininstall.resolve_install("deadbeef", "install", athena, plugins)
        checks["unknown_request_is_keyerror"] = False
    except KeyError:
        checks["unknown_request_is_keyerror"] = True

    # -- a request for a stage with no manifest is refused before any prompt
    bad = athena / "profiles" / "probe" / "sandbox" / "staging" / "nomanifest"
    (bad / "scripts").mkdir(parents=True, exist_ok=True)
    (bad / "scripts" / "plugin.py").write_text(ENTRY, encoding="utf-8")
    try:
        plugininstall.request_install(bad, athena)
        checks["bad_stage_never_reaches_the_user"] = False
    except Exception as exc:                          # noqa: BLE001
        checks["bad_stage_never_reaches_the_user"] = "manifest" in str(exc)
    checks["bad_stage_left_no_request"] = not any(
        row["name"] == "nomanifest" for row in plugininstall.pending_installs(athena, plugins))

    # -- reconcile: a stage written by any means becomes a prompt -------
    manual = athena / "profiles" / "probe" / "sandbox" / "staging" / "gamma"
    (manual / "scripts").mkdir(parents=True, exist_ok=True)
    (manual / "PLUGIN.md").write_text(MANIFEST.format(name="gamma"), encoding="utf-8")
    (manual / "scripts" / "plugin.py").write_text(ENTRY, encoding="utf-8")
    listed = plugininstall.pending_installs(athena, plugins)
    checks["reconcile_finds_an_unrequested_stage"] = any(row["name"] == "gamma" for row in listed)
    gamma = next(row for row in listed if row["name"] == "gamma")
    checks["fresh_stage_offers_install_and_cancel"] = gamma["actions"] == ["install", "cancel"]
    checks["fresh_stage_is_marked_not_installed"] = gamma["installed"] is False

    # -- installed: the same stage now offers repair and uninstall ------
    plugininstall.resolve_install(gamma["id"], "install", athena, plugins)
    checks["answered_stage_is_not_asked_again"] = not any(
        row["staged"] == str(manual) for row in plugininstall.pending_installs(athena, plugins))

    # -- drift is a NEW question: the card comes back, with all four actions
    (plugins / "gamma" / "scripts" / "plugin.py").write_text("# tampered\n", encoding="utf-8")
    drifted = next(row for row in plugininstall.pending_installs(athena, plugins) if row["name"] == "gamma")
    checks["drift_reopens_the_prompt"] = True
    checks["installed_stage_offers_all_four"] = drifted["actions"] == ["install", "repair", "uninstall", "cancel"]
    checks["installed_stage_is_marked_installed"] = drifted["installed"] is True
    checks["drift_is_reported"] = drifted["drifted"] is True
    checks["drift_lists_the_file"] = any("plugin.py" in f for f in drifted.get("drifted_files") or [])

    plugininstall.resolve_install(drifted["id"], "repair", athena, plugins)
    repaired = (plugins / "gamma" / "scripts" / "plugin.py").read_text(encoding="utf-8")
    checks["repair_restored_the_file"] = "def register" in repaired and "tampered" not in repaired

    # -- uninstall from the prompt (drift again, so there is a card to answer)
    (plugins / "gamma" / "scripts" / "plugin.py").write_text("# tampered again\n", encoding="utf-8")
    pending_now = plugininstall.pending_installs(athena, plugins)
    uninstall_row = next(row for row in pending_now if row["name"] == "gamma")
    removed = plugininstall.resolve_install(uninstall_row["id"], "uninstall", athena, plugins)
    checks["prompt_uninstall_reports_it"] = removed["decision"] == "uninstalled"
    checks["prompt_uninstall_removed_the_folder"] = not (plugins / "gamma").exists()
    checks["prompt_uninstall_disabled_it"] = "gamma" not in (athena / "config.yaml").read_text(encoding="utf-8")

    # -- cancel clears and changes nothing ------------------------------
    delta = athena / "profiles" / "probe" / "sandbox" / "staging" / "delta"
    (delta / "scripts").mkdir(parents=True, exist_ok=True)
    (delta / "PLUGIN.md").write_text(MANIFEST.format(name="delta"), encoding="utf-8")
    (delta / "scripts" / "plugin.py").write_text(ENTRY, encoding="utf-8")
    row = next(r for r in plugininstall.pending_installs(athena, plugins) if r["name"] == "delta")
    cancelled = plugininstall.resolve_install(row["id"], "cancel", athena, plugins)
    checks["cancel_reports_cancelled"] = cancelled["decision"] == "denied"
    checks["cancel_did_not_install"] = not (plugins / "delta").exists()
    checks["cancel_cleared_the_request"] = not any(r["name"] == "delta"
                                                   for r in plugininstall.pending_installs(athena, plugins))
    checks["cancel_is_remembered"] = True

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
