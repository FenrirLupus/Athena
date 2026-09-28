# Autonomy Tools

The **Autonomy** category — the agent's self-management + diagnostic
toolset (the Operator's 08-12/08-14 spec). These are the tools an agent
uses to understand the house, diagnose problems, and coordinate the hive.

Every tool in this category is a **hands-off button** — the agent decides
when/how/why to use it (skills are the brain; tools are the hands).

## The tools

| Tool | Purpose |
|---|---|
| `timeline_query` | Query the Timeline System graphs index — a node's neighborhood, state (alive/sick/dead/connection), and cross-refs. The STRUCTURE half of diagnosis. |
| `timeline_status` | The graphs overview — healthy/caution/warning/connection/unused counts per graph, freshness. The heat map at a glance. |
| `metric_summary` | Recent L3+ metric entries with code/reason from the metric logs. The EVENTS half of diagnosis. |

The hive-management tools (from the bee-hive build):

| Tool | Purpose |
|---|---|
| `delegate` | Hand a task to a worker bee, a drone subagent, or both (the fallback rule) |
| `worker_status` | Which worker bees are live + their state (wake/hibernate/sleep) |
| `board_summary` | The open work per agent board |
| `report_to_admin` | A worker reports its proposal/summary → the queen's board |
| `schedule_task` | Register recurring/one-shot jobs (cron) |
| `coordinate` | Agent-to-agent multi-thread: split a task across workers+drones, run in parallel |

## How they fit the fusion

A diagnostic turn combines them:
1. `metric_summary` — what broke (the error, code, reason)
2. `timeline_query` — where it sits + what it touches (the structure)
3. Both together = root cause, not a guess

## The category structure

```
tools/autonomy/
├── TOOL.md              ← this file
└── scripts/
    ├── timeline_query.py    ← one tool per .py (or a module of related tools)
    ├── timeline_status.py
    └── metric_summary.py
```

Each script self-registers via `register()` (the loader imports every
script under `tools/<name>/scripts/` at boot).
