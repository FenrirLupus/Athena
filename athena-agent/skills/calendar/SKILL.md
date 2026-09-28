---
name: calendar
description: "Use the built-in calendar tools to read and add events."
---

# Calendar

Two built-in tools:

- `calendar` — list the stored events.
- `calendar_add` — add an event (`date`, `title`, optional `detail`).

```json
{"date": "2026-08-15", "title": "Stand-up"}
```

Use them when the operator asks about their calendar or wants to
remember an upcoming date.

## Planner (periods)

The calendar is a PLANNER — events and tasks on a daily, weekly,
monthly, or yearly basis, keyed by YYYY-MM-DD:

- `calendar` — plan: list events/tasks for a period
- `calendar_add` — add an event or task

```json
{"period": "month", "date": "2026-09-01"}
{"date": "2026-09-01", "kind": "event", "title": "Stand-up"}
```

---
---
