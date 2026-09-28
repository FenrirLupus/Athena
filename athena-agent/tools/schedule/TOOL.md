---
name: schedule
description: "List and set scheduled reminders — a plain JSONL reminder store."
---

# Schedule

The **schedule** tools manage reminders (a JSONL file under the
profile's runtime dir). Two tools:

- `schedule` — list scheduled reminders.
- `schedule_set` — set a reminder.

They are HANDS-OFF — the code in `scripts/schedule.py` handles the
calls. Do NOT use terminal to chase the schedule file; the tools ARE
the implementation.

## Usage

```
schedule {"profile": "default"}                      # list reminders
schedule_set {"time": "09:00", "task": "Morning check", "repeat": "daily"}
```

## When to use

- The operator asks to be reminded.
- The operator wants their schedule / upcoming tasks.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/schedule.py` — the implementation (registers `schedule` +
  `schedule_set`).

---
Standard Markdown Schema: 4 delimiters (2 Header, 2 Footer). schedule tool.
---
