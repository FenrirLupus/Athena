"""The skills toolset: what exists, and the one you asked for.

Two tools, mirroring the two levels of cost — `skill_list` is cheap and answers "what do I have", `skill_read`
opens one and spends the context. Nothing here touches the filesystem through the guard because skills live in
`.athena/skills` and `profiles/<lane>/skills`, which are readable by construction; the *content* is what the guard
protects, and a skill body is text the user wrote, not data the agent found.
"""
from __future__ import annotations

from pathlib import Path

from core import skills as skills_mod
from tools.registry import Tool, ToolError


def build(profile_root: Path | None = None, athena_root: Path | None = None,
          plugin_names: list[str] | None = None) -> list[Tool]:
    """Skills tools for one agent; the roots decide which skills that agent can see, plugins included."""

    def skill_list() -> dict:
        found = skills_mod.discover(profile_root, athena_root, plugin_names)
        return {"count": len(found), "skills": found}

    def skill_read(name: str, max_chars: int = 20000) -> dict:
        try:
            return skills_mod.read(name, profile_root, max_chars=max(500, min(int(max_chars), 60000)),
                                   athena_root=athena_root, plugin_names=plugin_names)
        except skills_mod.SkillError as exc:
            raise ToolError(str(exc)) from exc

    return [
        Tool("skill_list", "List the skills available to you: name, description and when to use each.",
             {"type": "object", "properties": {}, "required": []}, skill_list),
        Tool("skill_read", "Open one skill in full. Load it only when the task matches its description.",
             {"type": "object",
              "properties": {"name": {"type": "string", "description": "Skill name from skill_list."},
                             "max_chars": {"type": "integer", "description": "Characters to return."}},
              "required": ["name"]}, skill_read),
    ]
