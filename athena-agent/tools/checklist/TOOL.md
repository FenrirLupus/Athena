---
name: checklist
description: "Sequenced checklists + a flat todo list for tracking work safely."
---

# Checklist

The **checklist** tools manage SEQUENCED task lists (used for safe
step-by-step implementation) PLUS a flat **todo** list. HANDS-OFF —
the code in `scripts/` handles the calls.

## Tools

- `checklist_new` / `checklist_add` / `checklist_toggle` /
  `checklist_show` — sequenced checklists (one item at a time)
- `todo_add` / `todo_list` / `todo_toggle` / `todo_clear` — flat task
  list (quick everyday tracking)

## Usage

```
checklist_new {"name": "release"}
checklist_add {"name": "release", "item": "run the doctor"}
checklist_toggle {"name": "release", "index": 1}
todo_add {"task": "email the team"}
todo_list {}
todo_toggle {"index": 1}
```

## When to use

- The operator wants to track what needs doing.
- The agent plans a safe implementation (diagnose → build → verify) —
  the checklist is taken SEQUENTIALLY; the todo is a flat list.

## References

- `references/` — (empty; the tools are self-contained)

## Scripts

- `scripts/checklist.py` — the checklist family
- `scripts/todo.py` — the todo list family

---
---
