"""References — added information, kept with the thing it documents, and readable without being usable.

The distinction being tested:

    references/   text to read (specs, examples, tables) — carries no capability
    assets/       data a plugin's code loads at runtime — not text for the agent
    scripts/      code that runs

and one rule that matters: a reference reader must not become a general file reader.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import library
from core import skills as skills_mod
from tools import library as library_tools
from tools import registry as registry_mod
from tools import filesystem

checks = {}


def make(folder: Path, descriptor: str, body: str = "", refs: dict | None = None, assets: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / descriptor).write_text(body, encoding="utf-8")
    for name, text in (refs or {}).items():
        target = folder / "references" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for name, text in (assets or {}).items():
        target = folder / "assets" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    for d in (home / "workspace", home / "sandbox"):
        d.mkdir(parents=True)

    make(athena / "skills" / "prose-skill", "SKILL.md",
         "---\nname: prose-skill\ndescription: a skill with references\n---\n\n# prose-skill\n",
         refs={"details.md": "# details\n", "nested/table.md": "| a | b |\n"})
    make(athena / "tools" / "some-tool", "TOOL.md",
         "---\nname: some_tool\nkind: command\ncommand: [\"true\"]\n---\n\n# some tool\n",
         refs={"df-output.md": "# df\n", "rows.md": "# rows\n"})
    make(athena / "plugins" / "some-plugin", "PLUGIN.md",
         "---\nname: some-plugin\nentry: scripts/plugin.py\n---\n\n# plugin\n",
         refs={"api.md": "# api\n"}, assets={"templates/report.j2": "{{ x }}\n"})
    (athena / "plugins" / "some-plugin" / "scripts").mkdir()
    (athena / "plugins" / "some-plugin" / "scripts" / "plugin.py").write_text(
        "def register(registry, ctx):\n    pass\n", encoding="utf-8")

    # -- discovery -------------------------------------------------------
    inv = library.inventory(home, athena)
    by_name = {(row["layer"], row["name"]): row for row in inv}
    checks["skill_references_found"] = by_name.get(("skill", "prose-skill"), {}).get("references") == \
                                       ["details.md", "nested/table.md"]
    checks["tool_references_found"] = by_name.get(("tool", "some-tool"), {}).get("references") == \
                                      ["df-output.md", "rows.md"]
    checks["plugin_references_found"] = by_name.get(("plugin", "some-plugin"), {}).get("references") == ["api.md"]

    # assets are not references
    checks["assets_are_not_references"] = "templates/report.j2" not in \
        by_name[("plugin", "some-plugin")]["references"]
    detail = library.list_for("plugin", "some-plugin", home, athena)
    checks["assets_listed_separately"] = detail["assets"] == ["templates/report.j2"]
    checks["scripts_listed_separately"] = detail["scripts"] == ["plugin.py"]

    # -- reading ---------------------------------------------------------
    got = library.read("tool", "some-tool", "df-output.md", home, athena)
    checks["reference_reads"] = got["content"].startswith("# df")
    checks["nested_reference_reads"] = library.read("skill", "prose-skill", "nested/table.md", home,
                                                    athena)["content"].startswith("| a | b |")

    # -- a reference reader must not become a file reader -----------------
    for label, args in (("dotdot_escape", ("tool", "some-tool", "../../SKILL.md")),
                        ("absolute_path", ("tool", "some-tool", "/etc/hostname")),
                        ("sibling_layer", ("tool", "some-tool", "../../plugins/some-plugin/PLUGIN.md"))):
        try:
            library.read(*args, profile_root=home, athena_root=athena)
            checks[f"refuses_{label}"] = False
        except library.ReferenceError as exc:
            checks[f"refuses_{label}"] = "escapes" in str(exc)

    try:
        library.read("widget", "x", "a.md", home, athena)
        checks["unknown_layer_refused"] = False
    except library.ReferenceError as exc:
        checks["unknown_layer_refused"] = "unknown layer" in str(exc)

    try:
        library.read("tool", "some-tool", "missing.md", home, athena)
        checks["missing_reference_refused"] = False
    except library.ReferenceError as exc:
        checks["missing_reference_refused"] = "no reference" in str(exc) and "df-output.md" in str(exc)

    try:
        library.read("tool", "ghost", "a.md", home, athena)
        checks["unknown_capability_refused"] = False
    except library.ReferenceError as exc:
        checks["unknown_capability_refused"] = "no tool named" in str(exc)

    # -- skills surface their references when opened ----------------------
    opened = skills_mod.read("prose-skill", home)
    checks["skill_read_lists_references"] = opened["references"] == ["details.md", "nested/table.md"]

    # -- the tools reach the same library --------------------------------
    ctx = filesystem.Context(scope=None, approvals=None)
    ctx.cfg = {"tools": {"packs": []}, "plugins": {"enabled": []}}
    ctx.profile_root = home
    ctx.athena_root = athena
    reg = registry_mod.default_registry(ctx.cfg).bind(ctx)
    checks["reference_tools_are_offered"] = {"reference_list", "reference_read"}.issubset(set(reg.names()))
    listing = reg.invoke("reference_list", {})
    checks["reference_list_tool_works"] = "some-tool" in listing and "prose-skill" in listing
    out = reg.invoke("reference_read", {"layer": "tool", "name": "some-tool", "path": "df-output.md"})
    checks["reference_read_tool_works"] = '"content": "# df' in out.replace("\\n", "\n") or "# df" in out
    refused = reg.invoke("reference_read", {"layer": "tool", "name": "some-tool", "path": "../../SKILL.md"})
    checks["reference_read_tool_refuses_escape"] = "error" in refused.lower() and "escapes" in refused

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
