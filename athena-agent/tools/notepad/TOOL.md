---
name: notepad
description: "Write/list/read notes — saved natively to the workspace."
---

# Notepad

The **notepad** tools save notes NATIVELY to the profile's WORKSPACE
directory (`workspace/notes/`) — the same place the agent's work files
live. HANDS-OFF — the code in `scripts/notepad.py` handles the calls.

## Usage

```
note_write {"title": "Ideas", "body": "..."}
note_list {}
note_read {"name": "ideas"}
```

## Where notes live

- `profiles/<name>/workspace/notes/<slug>.md` — each note is a file.

## When to use

- The operator wants to take notes.
- The agent wants to persist a note alongside its work files.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/notepad.py` — the implementation (registers `note_write` +
  `note_list` + `note_read`).

---
---
