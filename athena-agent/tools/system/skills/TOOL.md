---
name: skills
description: List the skills this agent can see, and open the one the task matches.
kind: script
script: scripts/skills.py
actions: list, read, create, remove, enable, disable
parameters: '{"type":"object","properties":{"action":{"type":"string","enum":["list","read","create","remove","enable","disable"],"description":"The verb: List the skills available to you: name, description and when to use each; Open one skill in full."},"name":{"type":"string","description":"skill name"},"max_chars":{"type":"integer"},"description":{"type":"string","description":"when to use it (action=create)"},"category":{"type":"string","description":"folder under skills/ (action=create), default local"},"body":{"type":"string","description":"the instruction text (action=create)"},"confirm":{"type":"boolean","description":"required for remove"}},"required":["action"]}'
timeout_s: 60
---

# skills

| tool | does |
|---|---|
| `skill_list` | every skill this agent can see: name, description, when to use it |
| `skill_read` | one skill in full, charged against the context window |

Skills live in `.athena/skills/` and `profiles/<lane>/skills/`; a profile skill shadows a shared one of the same
name. Reading is what the tool is for — a skill body is text the user wrote, not data the agent found.
