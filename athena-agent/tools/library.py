"""The reference tools: what extra information exists, and the one you asked for.

Bundled with the skills tools in spirit — both are about *reading* rather than doing — but kept apart because a
reference belongs to whatever it documents: a skill, a tool, or a plugin.
"""
from __future__ import annotations

from pathlib import Path

from core import library
from tools.registry import Tool, ToolError


def build(profile_root: Path | None = None, athena_root: Path | None = None) -> list[Tool]:
    """Reference tools for one agent: `profile_root` decides which capabilities it can see."""

    def reference_list() -> dict:
        rows = library.inventory(profile_root, athena_root)
        return {"count": len(rows), "capabilities": rows}

    def reference_read(layer: str, name: str, path: str = "", max_chars: int = 20000) -> dict:
        try:
            return library.read(layer, name, path, profile_root, athena_root,
                                max_chars=max(500, min(int(max_chars), 60000)))
        except library.ReferenceError as exc:
            raise ToolError(str(exc)) from exc

    return [
        Tool("reference_list",
             "List the extra documentation (references/) carried by skills, tools and plugins.",
             {"type": "object", "properties": {}, "required": []}, reference_list),
        Tool("reference_read",
             "Read one reference file from a capability: layer is skill|tool|plugin, name is its folder.",
             {"type": "object",
              "properties": {"layer": {"type": "string", "enum": ["skill", "tool", "plugin"],
                                       "description": "Which layer owns the capability."},
                             "name": {"type": "string", "description": "Capability folder name."},
                             "path": {"type": "string", "description": "File inside its references/ folder."},
                             "max_chars": {"type": "integer", "description": "Characters to return."}},
              "required": ["layer", "name", "path"]}, reference_read),
    ]
