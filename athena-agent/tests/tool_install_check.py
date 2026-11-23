"""Tool packs get the same lifecycle as plugins: stage, ask, install on the click, enable, verify.

The gap this closes: `plugin_stage` had a card, tool packs had a config line. An agent could prepare a `TOOL.md` pack
and still have to tell the user "edit `tools.packs` yourself" — which reads as "there is no way", and once produced a
flat refusal to install something that was already on the machine.

What is asserted:

  * a staged pack validates (TOOL.md present, name matches folder, script exists) or is refused before any prompt
  * installing copies it to `.athena/tools/`, adds it to `tools.packs`, records hashes, and loads the tool
  * the request appears on the same queue the UI polls, with `kind: "tool"` and Install/Cancel
  * cancel installs nothing and does not touch the config
  * a pack whose script is missing never reaches the user
"""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import plugininstall
from tools import registry as registry_mod

checks = {}

CONFIG = """tools:
  enabled: true
  packs: [existing]

plugins:
  enabled: []
"""

TOOL_MD = """---
name: {name}
description: says hello from a tool pack
kind: script
script: scripts/hello.py
---

# {name}
"""

SCRIPT = '''import json, sys
args = json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}")
print("hello", args.get("who", "nobody"))
'''


def stage(root: Path, name: str, script: bool = True) -> Path:
    folder = root / "sandbox" / "staging" / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "TOOL.md").write_text(TOOL_MD.format(name=name), encoding="utf-8")
    if script:
        (folder / "scripts" / "hello.py").write_text(SCRIPT, encoding="utf-8")
    return folder


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    plugins_root = athena / "plugins"
    tools_root = athena / "tools"
    for folder in (plugins_root, tools_root, athena / "security", athena / "profiles" / "probe" / "sandbox"):
        folder.mkdir(parents=True, exist_ok=True)
    (athena / "config.yaml").write_text(CONFIG, encoding="utf-8")

    # -- 1 · validation before any prompt ------------------------------
    broken = stage(athena / "profiles" / "probe", "broken", script=False)
    try:
        plugininstall.request_tool_install(broken, athena)
        checks["missing_script_refused_before_prompt"] = False
    except Exception as exc:                          # noqa: BLE001
        checks["missing_script_refused_before_prompt"] = "script" in str(exc)
    checks["nothing_queued_for_a_bad_pack"] = plugininstall.pending_installs(athena, plugins_root) == []

    # -- 2 · the request is on the same queue as a plugin --------------
    staged = stage(athena / "profiles" / "probe", "greeter")
    request = plugininstall.request_tool_install(staged, athena, reason="expose a hello verb")
    rows = plugininstall.pending_installs(athena, plugins_root)
    row = next((r for r in rows if r["name"] == "greeter"), None)
    checks["request_queued"] = bool(row)
    checks["request_has_kind_tool"] = (row or {}).get("kind") == "tool"
    checks["request_offers_install_and_cancel"] = (row or {}).get("actions") == ["install", "cancel"]
    checks["request_states_it_is_not_installed"] = (row or {}).get("installed") is False
    checks["request_installs_nothing"] = not (tools_root / "greeter").exists()

    # -- 3 · the click installs it ------------------------------------
    done = plugininstall.resolve_install(request["request"], "install", athena, plugins_root)
    checks["install_reports_installed"] = done["decision"] == "installed"
    checks["install_copied_the_pack"] = (tools_root / "greeter" / "TOOL.md").is_file()
    checks["install_reports_the_tool"] = done.get("tools") == ["greeter"]
    checks["install_enabled_the_pack"] = "greeter" in plugininstall._packs_names(
        (athena / "config.yaml").read_text(encoding="utf-8"))
    checks["existing_pack_untouched"] = "existing" in plugininstall._packs_names(
        (athena / "config.yaml").read_text(encoding="utf-8"))
    checks["install_recorded_hashes"] = "tool:greeter" in json.loads(
        (athena / "security" / "installs.json").read_text(encoding="utf-8"))
    checks["request_cleared"] = not any(
        r["id"] == request["request"] for r in plugininstall.pending_installs(athena, plugins_root))

    # -- 3b · the card can say who asked ------------------------------
    staged4 = stage(athena / "profiles" / "probe", "attributed")
    req4 = plugininstall.request_tool_install(staged4, athena, reason="needs a card", agent="default")
    row4 = next(r for r in plugininstall.pending_installs(athena, plugins_root) if r["id"] == req4["request"])
    checks["request_records_the_agent"] = row4.get("agent") == "default"
    checks["request_records_the_reason"] = row4.get("reason") == "needs a card"

    # -- 3c · a promise the build cannot keep is refused --------------
    promoted = stage(athena / "profiles" / "probe", "promises")
    (promoted / "TOOL.md").write_text(TOOL_MD.format(name="promises") + "", encoding="utf-8")
    import pathlib as _pl

    (promoted / "PLUGIN.md").write_text("---\nname: promises\nentry: scripts/hello.py\nprovides: tools, agents\n---\n",
                                        encoding="utf-8")
    try:
        plugininstall.install(promoted, plugins_root, athena)
        checks["unhosted_provides_refused"] = False
    except Exception as exc:                          # noqa: BLE001
        checks["unhosted_provides_refused"] = "does not host" in str(exc)

    # -- 4 · the tool actually works ----------------------------------
    cfg = {"tools": {"packs": ["greeter"]}}
    registry = registry_mod.Registry()
    from core import toolpacks

    toolpacks.load_all(cfg, registry, None, athena)
    checks["tool_is_loadable"] = "greeter" in registry.names()
    out = registry.invoke("greeter", {"who": "athena"})
    checks["tool_answers"] = "hello athena" in out

    # -- 5 · cancel changes nothing -----------------------------------
    second = stage(athena / "profiles" / "probe", "second")
    req2 = plugininstall.request_tool_install(second, athena)
    cancelled = plugininstall.resolve_install(req2["request"], "cancel", athena, plugins_root)
    checks["cancel_reported"] = cancelled["decision"] == "denied"
    checks["cancel_installed_nothing"] = not (tools_root / "second").exists()
    checks["cancel_left_config_alone"] = "second" not in plugininstall._packs_names(
        (athena / "config.yaml").read_text(encoding="utf-8"))

    # -- 5b · a request whose stage was cleaned up is pruned --------
    third = stage(athena / "profiles" / "probe", "third")
    req3 = plugininstall.request_tool_install(third, athena)
    import shutil

    shutil.rmtree(third)
    remaining = plugininstall.pending_installs(athena, plugins_root)
    checks["stale_request_pruned"] = not any(r["id"] == req3["request"] for r in remaining)
    checks["no_dead_card_offering_only_cancel"] = not any(r.get("actions") == ["cancel"] for r in remaining)

    # -- 6 · name mismatch is refused ---------------------------------
    mismatched = stage(athena / "profiles" / "probe", "wrongfolder")
    (mismatched / "TOOL.md").write_text(TOOL_MD.format(name="somethingelse"), encoding="utf-8")
    try:
        plugininstall.install_tool(mismatched, tools_root, athena)
        checks["name_mismatch_refused"] = False
    except Exception as exc:                          # noqa: BLE001
        checks["name_mismatch_refused"] = "does not match" in str(exc)

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
