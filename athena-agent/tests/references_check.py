"""References travel with the capability that declares them, and there is no tool to fetch them.

The rule this suite pins down:

    a skill, tool or plugin is *loaded* with its descriptor and every reference it declares

and the three things that go wrong with that:

  * a document cut off mid-way, which reads like a complete one;
  * a set too large for one load, silently eating the context it was meant to help;
  * a path pointing out of the capability, which would make a reference a general file reader.

There is no `reference_list`/`reference_read`: an agent that had to remember a second call forgot it, and the document
it forgot was the one explaining how to use the capability.
"""
import json
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import references
from core import skills as skills_mod
from tools import loader, registry as registry_mod

checks = {}


def make(folder: Path, descriptor: str, name: str, body: str = "", refs: dict | None = None,
         header: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / descriptor).write_text(
        f"---\nname: {name}\ndescription: probe capability\n{header}---\n\n{body}", encoding="utf-8")
    for rel, text in (refs or {}).items():
        target = folder / "references" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return folder


with tempfile.TemporaryDirectory() as td:
    athena = Path(td) / ".athena"
    home = athena / "profiles" / "probe"
    for d in (home / "workspace", home / "sandbox"):
        d.mkdir(parents=True)

    # -- a skill carries its references -----------------------------------
    make(athena / "skills" / "prose-skill", "SKILL.md", "prose-skill", "# prose-skill\n",
         refs={"details.md": "# details\nuse the narrow verb\n", "nested/table.md": "| a | b |\n"})
    opened = skills_mod.read("prose-skill", home, athena_root=athena)
    checks["skill_body_is_there"] = "# prose-skill" in opened["body"]
    checks["skill_reference_text_attached"] = "use the narrow verb" in opened["body"]
    checks["skill_reference_paths_named"] = "references/details.md" in opened["body"] and \
        "references/nested/table.md" in opened["body"]
    checks["skill_reference_list_recorded"] = sorted(opened["references"]) == ["references/details.md",
                                                                              "references/nested/table.md"]

    # -- a declared list wins over the folder ------------------------------
    make(athena / "skills" / "declared", "SKILL.md", "declared", "# declared\n",
         refs={"one.md": "# one\n", "two.md": "# two\n"}, header="references: references/one.md\n")
    only = skills_mod.read("declared", home, athena_root=athena)
    checks["declared_reference_included"] = "# one" in only["body"]
    checks["undeclared_reference_left_out"] = "# two" not in only["body"]

    # -- a path out of the capability is refused, not followed ------------
    make(athena / "skills" / "escaped", "SKILL.md", "escaped", "# escaped\n",
         header="references: ../../../config.yaml\n")
    out = skills_mod.read("escaped", home, athena_root=athena)
    checks["escape_refused"] = "refused" in " ".join(out["reference_problems"]).lower()
    checks["escape_text_not_attached"] = "# escaped" in out["body"] and "model:" not in out["body"]

    # -- over the cap: a list with sizes, and nothing inlined ------------
    make(athena / "skills" / "big", "SKILL.md", "big", "# big\n",
         refs={"a.md": "A" * (references.MAX_ATTACH_BYTES // 2 + 100),
               "b.md": "B" * (references.MAX_ATTACH_BYTES // 2 + 100)})
    loaded = skills_mod.read("big", home, athena_root=athena)
    checks["over_cap_not_inlined"] = "A" * 50 not in loaded["body"]
    checks["over_cap_lists_each_file"] = "references/a.md" in loaded["body"] and "references/b.md" in loaded["body"]
    checks["over_cap_states_sizes"] = "bytes (omitted)" in loaded["body"]
    checks["over_cap_names_the_limit"] = str(references.MAX_ATTACH_BYTES) in loaded["body"]

    # -- a loaded tool carries its verbs and its references ---------------
    tool_dir = home / "tools" / "probe-tool"
    make(tool_dir, "TOOL.md", "probe_tool", "# probe_tool\n\ntwo verbs, one package\n",
         refs={"notes.md": "PROBE-REFERENCE-MARKER\n"},
         header=("kind: script\nscript: scripts/probe.py\nactions: alpha, beta\n"
                 "parameters: '{\"type\": \"object\", \"properties\": "
                 "{\"action\": {\"type\": \"string\", \"enum\": [\"alpha\", \"beta\"]}}, "
                 "\"required\": [\"action\"]}'\n"))
    (tool_dir / "scripts").mkdir()
    (tool_dir / "scripts" / "probe.py").write_text(
        "import json, sys\nargs = json.loads(sys.argv[1] if len(sys.argv) > 1 else '{}')\n"
        "print(json.dumps({'ok': True, 'action': args.get('action')}))\n", encoding="utf-8")

    reg = registry_mod.Registry()
    report = loader.load(reg, profile_root=home, athena_root=athena, cfg={"tools": {"packs": ["probe-tool"]}})
    row = next((entry for entry in report["loaded"] if entry["name"] == "probe-tool"), {})
    checks["tool_package_registers_one_tool"] = row.get("tool") == "probe_tool"
    checks["tool_declares_its_actions"] = row.get("actions") == ["alpha", "beta"]
    description = reg._tools["probe_tool"].description if "probe_tool" in reg.names() else ""
    checks["tool_description_names_its_script"] = "probe.py" in description
    checks["tool_description_names_its_verbs"] = "alpha" in description and "beta" in description
    checks["tool_description_carries_the_body"] = "two verbs, one package" in description
    checks["tool_reference_text_attached"] = "PROBE-REFERENCE-MARKER" in description
    checks["tool_report_names_the_reference"] = (row.get("references") or {}).get("attached") == ["references/notes.md"]
    checks["one_tool_per_descriptor"] = len([name for name in reg.names() if name.startswith("probe")]) == 1

    # -- a descriptor whose actions and schema disagree is reported, not loaded ---
    bad = home / "tools" / "mismatched"
    make(bad, "TOOL.md", "mismatched", "# mismatched\n",
         header=("kind: script\nscript: scripts/mismatched.py\nactions: one, two\n"
                 "parameters: '{\"type\": \"object\", \"properties\": "
                 "{\"action\": {\"type\": \"string\", \"enum\": [\"one\"]}}}'\n"))
    (bad / "scripts").mkdir()
    (bad / "scripts" / "mismatched.py").write_text("print('{}')\n", encoding="utf-8")
    reg2 = registry_mod.Registry()
    report2 = loader.load(reg2, profile_root=home, athena_root=athena, cfg={"tools": {"packs": ["mismatched"]}})
    checks["mismatch_reported"] = any(entry["name"] == "mismatched" and "disagree" in entry["error"]
                                      for entry in report2["skipped"])
    checks["mismatch_not_registered"] = "mismatched" not in reg2.names()

    # -- and there is no reference tool to call ---------------------------
    real = registry_mod.Registry()
    loader.load(real, profile_root=home, athena_root=athena, cfg={})
    checks["no_reference_tool"] = not ({"reference_list", "reference_read"} & set(real.names()))

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print(f"\n{sum(checks.values())}/{len(checks)} — RESULT:", "PASS" if all(checks.values()) else "FAIL")
raise SystemExit(0 if all(checks.values()) else 1)
