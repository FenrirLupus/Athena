---
name: checklist
description: "Use the built-in checklist tools to plan and track work sequentially — safe step-by-step implementation."
---

# Checklist

The built-in checklist tools track SEQUENCED tasks — taken one at a
time, in order:

- `checklist_new` — create a checklist
- `checklist_add` — add an item
- `checklist_toggle` — mark an item done (1-based index)
- `checklist_show` — show progress

```json
{"name": "release", "item": "run the doctor"}
```

The agent uses a checklist to plan/diagnose/implement SAFELY: complete
step 1, verify, then step 2. The operator uses it to keep track of
what's needed.

## Todo (flat task list)

The todo tools manage a flat task list:

- `todo_add` — add a task
- `todo_list` — list tasks
- `todo_toggle` — toggle done (1-based index)
- `todo_clear` — remove completed tasks

```json
{"task": "email the team"}
{"index": 1}
```

Use for quick everyday tracking. For SEQUENCED step-by-step work, use
the checklist instead.

---
---
