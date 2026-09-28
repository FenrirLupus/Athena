---
name: calendar
description: "The PLANNER — events and tasks on a daily, weekly, monthly, or yearly basis (YYYY-MM-DD)."
---

# Calendar (the Planner)

The **calendar** tools are a PLANNER: they manage EVENTS and TASKS on a
daily, weekly, monthly, or yearly basis, all keyed by the **YYYY-MM-DD**
format. HANDS-OFF — the code in `scripts/` handles the calls.

## Tools

- `calendar` — plan: list events/tasks for a period
- `calendar_add` — add an event or task (YYYY-MM-DD)

## Usage

```
calendar {"period": "week"}                         # this week's plan
calendar {"period": "month", "date": "2026-09-01"}  # September plan
calendar_add {"date": "2026-09-01", "kind": "event", "title": "Stand-up", "time": "09:00"}
calendar_add {"date": "2026-09-01", "kind": "task", "title": "Ship the release"}
```

## When to use

- The operator asks about their calendar / upcoming dates.
- The operator wants to remember an event or a to-do.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/calendar.py` — the planner list tool
- `scripts/calendar_add.py` — the planner add tool

---
---
