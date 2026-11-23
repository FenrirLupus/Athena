"""Per-lane plugin enablement, end to end through a real registry bind.

The unit checks prove `lane_enabled()` computes the right lists. This proves the list actually *reaches* `load_all` —
which it did not for a while: the function was correct and nothing passed it, so every lane ran the global ceiling and
the feature was inert. No unit test could have caught that; only a real bind can.

Run: ../.venv/bin/python3 tests/lane_plugins_check.py
"""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import identity
from tools import registry as registry_mod
from tools.filesystem import Context

checks: dict[str, bool] = {}


def note(name: str, passed) -> None:
    checks[name] = bool(passed)

PLUGIN = """---
name: {name}
description: records that it loaded
entry: scripts/plugin.py
---

# {name}
"""

ENTRY = '''def register(registry, ctx):
    registry.register(_Tool())


class _Tool:
    name = "{name}"
    description = "loaded"
    parameters = {{"type": "object", "properties": {{}}, "required": []}}

    def __call__(self, **kwargs):
        return {{"loaded": "{name}"}}
'''


def make_plugin(root: Path, name: str) -> None:
    folder = root / "plugins" / name
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "PLUGIN.md").write_text(PLUGIN.format(name=name), encoding="utf-8")
    (folder / "scripts" / "plugin.py").write_text(ENTRY.format(name=name), encoding="utf-8")


def ctx_for(profile_root: Path, athena_root: Path, ceiling: dict) -> Context:
    ctx = Context(scope=None, approvals=None)
    ctx.cfg = ceiling
    ctx.profile_root = profile_root
    ctx.athena_root = athena_root
    return ctx


def bind(profile_root: Path, athena_root: Path, ceiling: dict):
    return registry_mod.Registry().bind(ctx_for(profile_root, athena_root, ceiling))


def loaded_of(registry) -> set:
    report = getattr(registry, "plugin_report", None) or {}
    names = set()
    for row in report.get("loaded") or []:
        names.add(row.get("name") if isinstance(row, dict) else str(row))
    return {n for n in names if n}


with tempfile.TemporaryDirectory() as td:
    athena = Path(td)
    for plugin_name in ("alpha", "beta", "gamma"):
        make_plugin(athena, plugin_name)
    profiles = athena / "profiles"
    ceiling = {"plugins": {"enabled": ["alpha", "beta"], "isolation": "inprocess"},
               "tools": {"enabled": True}}

    def lane(name: str, plugins: str = "") -> Path:
        root = profiles / name
        root.mkdir(parents=True, exist_ok=True)
        identity.mint(root)
        if plugins:
            (root / "config.yaml").write_text(f"plugins:\n  enabled: {plugins}\n", encoding="utf-8")
        return root

    # -- a lane with no list inherits the ceiling -------------------------
    plain = lane("plain")
    reg_plain = bind(plain, athena, ceiling)
    note("inheriting_lane_reports_the_ceiling", loaded_of(reg_plain) == {"alpha", "beta"})
    note("inheriting_lane_registers_both_tools", {"alpha", "beta"} <= set(reg_plain.names()))

    # -- a narrowed lane gets only its list ------------------------------
    narrow = lane("narrow", "[alpha]")
    reg_narrow = bind(narrow, athena, ceiling)
    note("narrowed_lane_loads_only_its_list", loaded_of(reg_narrow) == {"alpha"})
    note("narrowed_lane_does_not_register_the_others", "beta" not in set(reg_narrow.names()))

    # -- naming a plugin outside the ceiling is blocked, reported, logged -
    ghost = lane("ghostlane", "[gamma]")
    reg_ghost = bind(ghost, athena, ceiling)
    note("blocked_plugin_is_not_loaded", "gamma" not in set(reg_ghost.names()))
    report = getattr(reg_ghost, "plugin_report", {}) or {}
    note("report_names_the_block", report.get("blocked") == ["gamma"])
    events = athena / "security" / "events.jsonl"
    note("block_is_written_to_the_ledger",
         events.is_file() and "plugin-not-permitted" in events.read_text(encoding="utf-8"))
    note("ledger_row_names_the_lane",
         events.is_file() and "ghostlane" in events.read_text(encoding="utf-8"))

    # -- an empty list means none, not "inherit" -------------------------
    none_lane = lane("none", "[]")
    reg_none = bind(none_lane, athena, ceiling)
    note("empty_list_loads_nothing", loaded_of(reg_none) == set() and "alpha" not in set(reg_none.names()))

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
ok = sum(checks.values())
print(f"\n{ok}/{len(checks)} — RESULT:", "PASS" if ok == len(checks) else "FAIL")
