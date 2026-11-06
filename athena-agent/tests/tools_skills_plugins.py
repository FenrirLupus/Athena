"""Tools, skills and plugins: does each system actually deliver something to a turn?

This is an integration test on purpose. Each of the three had a way of looking finished while delivering nothing:
tools registered nowhere visible, a skills folder never read, a plugin directory that was only a convention.
"""
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import plugins as plugins_mod
from core import skills as skills_mod
from tools import filesystem, loader, registry as registry_mod

checks = {}


def make_skill(root: Path, name: str, desc: str, when: str = "", body: str = "# body\n"):
    folder = root / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nwhen: {when}\n---\n\n{body}", encoding="utf-8")
    return folder


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    (home / "workspace").mkdir(parents=True)
    (home / "sandbox").mkdir()
    (athena / "plugins").mkdir()
    (home / "plugins").mkdir()

    # -- skills: discovery, shadowing, index, read ------------------------
    make_skill(athena, "shared-one", "Use when doing the shared thing.", body="# shared body\n")
    make_skill(athena, "shadowed", "the SHARED version", body="# shared shadowed\n")
    make_skill(home, "shadowed", "the PROFILE version", body="# profile shadowed\n")
    make_skill(home, "only-mine", "Use when only this agent needs it.")

    # a folded YAML description must not be reported as ">-"
    folded = home / "skills" / "folded"
    folded.mkdir(parents=True, exist_ok=True)
    (folded / "SKILL.md").write_text(
        "---\nname: folded\ndescription: >-\n  Drafts a specification section-by-section\n  from a concepts brief.\n"
        "---\n\n# folded\n", encoding="utf-8")

    found = {s["name"]: s for s in skills_mod.discover(home)}
    checks["shared_skill_visible"] = "shared-one" in found
    checks["profile_skill_visible"] = "only-mine" in found
    checks["profile_shadows_shared"] = found["shadowed"]["description"] == "the PROFILE version"
    checks["origin_labelled"] = found["only-mine"]["origin"] == "profile" and \
                                found["shared-one"]["origin"] == "shared"
    checks["folded_description_parsed"] = found["folded"]["description"].startswith("Drafts a specification")
    checks["folded_description_not_literal_marker"] = ">-" not in found["folded"]["description"]

    index = skills_mod.index(home)
    checks["index_is_one_line_per_skill"] = len(index) == 4 and all("\n" not in line for line in index)
    checks["index_carries_the_description"] = any("shared-one: Use when doing" in line for line in index)

    body = skills_mod.read("only-mine", home)
    checks["read_returns_the_body"] = body["body"].strip() == "# body"
    checks["read_omits_frontmatter"] = "---" not in body["body"][:20]
    try:
        skills_mod.read("nope", home)
        checks["unknown_skill_refused"] = False
    except skills_mod.SkillError as exc:
        checks["unknown_skill_refused"] = "no skill named" in str(exc) and "shared-one" in str(exc)

    # -- the two skill tools reach the same library -----------------------
    skill_tools = loader.script_module("skills")

    class SkillsCtx:
        """A context is what a tool script gets: the roots, and the config that decides which plugins count."""

        def __init__(self):
            self.profile_root, self.athena_root, self.cfg = home, athena, {}

    tools = loader.action_map(skill_tools.build, SkillsCtx())

    listing = tools["list"]()
    checks["skill_list_tool_counts"] = listing["count"] == 4
    checks["skill_read_tool_opens"] = "profile shadowed" in tools["read"](name="shadowed")["body"]

    # -- plugins: off unless named, broken ones skipped -------------------
    good = athena / "plugins" / "good"
    (good / "scripts").mkdir(parents=True)
    (good / "assets").mkdir()
    (good / "PLUGIN.md").write_text("---\nname: good\ndescription: registers one tool\n"
                                    "entry: scripts/plugin.py\n---\n\n# good\n", encoding="utf-8")
    (good / "scripts" / "plugin.py").write_text(
        "from tools.registry import Tool\n"
        "def register(registry, ctx):\n"
        "    registry.register(Tool('good_tool', 'ok', {'type': 'object', 'properties': {}, 'required': []},\n"
        "                           lambda: 'ok'))\n", encoding="utf-8")
    bad = athena / "plugins" / "bad"
    (bad / "scripts").mkdir(parents=True)
    (bad / "PLUGIN.md").write_text("---\nname: bad\nentry: scripts/plugin.py\n---\n\n# bad\n", encoding="utf-8")
    (bad / "scripts" / "plugin.py").write_text("def register(registry, ctx):\n    raise RuntimeError('boom')\n",
                                               encoding="utf-8")

    reg = registry_mod.Registry()
    off = plugins_mod.load_all({}, reg, home, athena)
    checks["nothing_loads_by_default"] = off["loaded"] == [] and "good_tool" not in reg.names()

    reg2 = registry_mod.Registry()
    on = plugins_mod.load_all({"plugins": {"enabled": ["good"]}}, reg2, home, athena)
    checks["named_plugin_loads"] = [row["name"] for row in on["loaded"]] == ["good"] and "good_tool" in reg2.names()
    checks["plugin_reports_its_tools"] = on["loaded"][0]["tools"] == ["good_tool"]

    reg3 = registry_mod.Registry()
    broken = plugins_mod.load_all({"plugins": {"enabled": ["bad", "good"]}}, reg3, home, athena)
    checks["broken_plugin_skipped"] = [r["name"] for r in broken["skipped"]] == ["bad"]
    checks["broken_plugin_does_not_stop_others"] = "good_tool" in reg3.names()
    checks["broken_plugin_reports_why"] = "boom" in broken["skipped"][0]["error"]

    reg4 = registry_mod.Registry()
    missing = plugins_mod.load_all({"plugins": {"enabled": ["ghost"]}}, reg4, home, athena)
    checks["missing_plugin_reported"] = missing["missing"] == ["ghost"]

    # -- a bound registry has all three sources ---------------------------
    ctx = filesystem.Context(scope=None, approvals=None)
    ctx.cfg = {"plugins": {"enabled": ["good"]}}
    ctx.profile_root = home
    ctx.athena_root = athena
    bound = registry_mod.default_registry({"plugins": {"enabled": ["good"]}}).bind(ctx)
    names = set(bound.names())
    checks["bound_has_filesystem_tools"] = "filesystem" in names
    checks["bound_has_skill_tools"] = "skills" in names
    checks["bound_has_plugin_tools"] = "good_tool" in names
    checks["bound_has_builtins"] = {"echo", "add"} <= names
    checks["every_tool_has_an_object_schema"] = all(
        (t["function"]["parameters"] or {}).get("type") == "object" for t in bound.schemas())

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
