---
name: tools
description: Manage tools — list what is installed, read one, create a package, remove one you own, enable or
  disable it. A tool is TOOL.md plus scripts/<name>.py; anything without both is a skill, not a tool.
kind: script
script: scripts/tools.py
actions: list, read, create, remove, enable, disable
parameters: '{"type":"object","properties":{"action":{"type":"string","enum":["list","read","create","remove","enable","disable"]},"name":{"type":"string","description":"tool name (lowercase, hyphens)"},"description":{"type":"string","description":"what the new tool does, for action=create"},"verb":{"type":"string","description":"the single verb the new script exposes, default run"},"code":{"type":"string","description":"optional script body; omitted means a working scaffold"},"confirm":{"type":"boolean","description":"required for remove"},"limit":{"type":"integer"}},"required":["action"]}'
timeout_s: 60
---

# tools

| verb | does |
|---|---|
| `list` | every tool visible to you — origin, verbs, enabled, and any descriptor problem |
| `read` | one descriptor and the head of its script |
| `create` | write `tools/<name>/TOOL.md` + `scripts/<name>.py` in your own root, compile-check it, enable it |
| `remove` | delete a tool you own (*confirm*) — shipped tools can be disabled, never removed |
| `enable` / `disable` | toggle an installed tool in your config without touching its files |

## The rules it enforces

- **A package or nothing.** `create` writes the descriptor and the script together; if the script does not compile the
  half-written package is deleted rather than left to register as broken.
- **The program tree is read-only.** A tool that ships with Athena cannot be edited or removed by an agent — that is
  the boundary between the architecture and its inhabitants.
- **One name, one tool.** Creating a name that already exists is refused, because two packages with one name means
  whichever the loader finds first wins and nobody can predict which.
- **Removal is confirmed.** `remove` deletes a directory; without `confirm=true` it refuses.
