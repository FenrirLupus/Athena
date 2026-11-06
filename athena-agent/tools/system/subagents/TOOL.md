---
name: subagents
description: Hand a bounded task to a worker with its own folder, and get a summary back.
kind: script
script: scripts/subagents.py
actions: spawn
parameters: '{"type": "object", "properties": {"task": {"type": "string", "description": "The whole task, self-contained. Required for action: spawn."}, "max_turns": {"type": "integer", "description": "Model iterations allowed (default 6, max 24)."}}, "required": ["task"]}'
timeout_s: 60
---

# subagents

`spawn_subagent` — hand a bounded, self-contained task to a worker with its own folder, and get a summary back.

## What it deliberately cannot do

- Spawn from inside a worker: `is_subagent` is set on the child and this tool is removed from its registry.
- Reach outside its folder: the child's scope is re-rooted at `subagents/<slug>/`.
- Spend without limit: turns and dollars are arguments, not hopes.
