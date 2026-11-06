"""Home capabilities: every skill, tool and plugin under the athena home still declares itself.

`tests/run_gate.sh` only sees the program tree, so a change applied to `.athena/skills` or `.athena/plugins` was
reported as "applied and gated" while nothing had looked at it. This suite is that look: every manifest under the
home must exist, parse, carry a name, and — for skills — a description, because a manifest whose header broke is
silently skipped by the loader and looks exactly like a capability that was never installed.
"""
import re
import sys
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import frontmatter

HOME = Path(__file__).resolve().parents[2]          # <athena>/athena-agent → <athena>
CHECKS = {}

# a plugin ships skills too, and those are exactly what an apply is likely to touch
SKILL_DIRS = ([HOME / "skills",
               *sorted((HOME / "profiles").glob("*/skills")),
               *sorted((HOME / "plugins").glob("*/skills"))])
TOOL_DIRS = [HOME / "tools", *sorted((HOME / "profiles").glob("*/tools"))]
PLUGIN_DIRS = [HOME / "plugins", *sorted((HOME / "profiles").glob("*/plugins"))]


def manifests(folders, name):
    found = []
    for folder in folders:
        if folder.is_dir():
            found.extend(sorted(folder.rglob(name)))
    return found


skills = manifests(SKILL_DIRS, "SKILL.md")
tools = manifests(TOOL_DIRS, "TOOL.md")
plugins = manifests(PLUGIN_DIRS, "PLUGIN.md")
CHECKS["there_are_capabilities"] = bool(skills or tools or plugins)


def parsed(path):
    meta, _ = frontmatter.parse(path.read_text(encoding="utf-8", errors="replace"))
    return meta


bad_skills, bad_tools, bad_plugins = [], [], []
for path in skills:
    meta = parsed(path)
    if not meta.get("name") or not meta.get("description"):
        bad_skills.append(str(path.relative_to(HOME)))
for path in tools:
    meta = parsed(path)
    if not meta.get("name") or not meta.get("kind"):
        bad_tools.append(str(path.relative_to(HOME)))
for path in plugins:
    meta = parsed(path)
    # a plugin that ships skills has no entry point; `provides` is the declaration that matters there
    if not meta.get("name") or not (meta.get("entry") or meta.get("provides")):
        bad_plugins.append(str(path.relative_to(HOME)))

CHECKS["skills_declare_name_and_description"] = not bad_skills
CHECKS["tools_declare_name_and_kind"] = not bad_tools
CHECKS["plugins_declare_name_and_entry"] = not bad_plugins

# a skill folder without a SKILL.md is a capability the loader cannot see
RESERVED = {"references", "scripts", "assets", "templates", "personas", "agents", "skills", "tools", "plugins"}
empty = [str(folder.relative_to(HOME)) for folder in
         [p for base in SKILL_DIRS if base.is_dir() for p in base.rglob("*") if p.is_dir()]
         if folder.name not in RESERVED
         and not (folder / "SKILL.md").is_file() and not any(folder.glob("*/SKILL.md"))]
CHECKS["no_empty_skill_folders"] = not empty

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
for label, rows in (("skill", bad_skills), ("tool", bad_tools), ("plugin", bad_plugins), ("empty", empty)):
    for row in rows:
        print(f"    broken {label}: {row}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)
