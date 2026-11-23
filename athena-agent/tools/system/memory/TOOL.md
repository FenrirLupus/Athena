---
name: memory
description: The agent's durable notes in memories/MEMORY.md: save, search, edit and forget one-line memories.
kind: script
script: scripts/memory.py
actions: save, search, edit, forget
parameters: '{"type": "object", "properties": {"action": {"type": "string", "enum": ["save", "search", "edit", "forget"], "description": "The verb: Remember a durable fact about the person, a decision, or a correction; Search this agent's memory index; Rewrite one remembered row in place; Remove remembered lines containing a phrase."}, "text": {"type": "string", "description": "the fact, one sentence Required for action: edit, save."}, "tag": {"type": "string", "description": "preference, decision, fact, person, correction"}, "query": {"type": "string"}, "limit": {"type": "integer"}, "match": {"type": "string", "description": "phrase identifying the existing row Required for action: edit, forget."}}, "required": ["action"]}'
timeout_s: 60
---

# memory

The agent's durable notes, kept one line per fact in `memories/MEMORY.md`. The index is read on every turn, so this
is the most expensive text an agent can write: capped at 200 rows and 24 kB, deduplicated on write, pruned by age.

| tool | does |
|---|---|
| `memory_save` | add one dated, tagged line; refuses a duplicate and prunes past the cap |
| `memory_search` | find rows by word, so the agent does not have to re-read the whole index |
| `memory_edit` | replace one row, keeping the date and tag rules |
| `memory_forget` | drop one row |

## The format is the guard

`- YYYY-MM-DD [tag] text` — the same shape the reader parses. A free-form paragraph is refused rather than
written, because a row the reader cannot parse is a memory that silently stops existing.
