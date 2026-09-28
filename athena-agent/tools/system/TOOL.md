---
name: system
description: "System tools BUNDLE — machine health, domain subsystems (doctor/custodian/nurse/janitor), and the CORE systems (memory/emotion/vault/session/kanban)."
---

# System

The **system** tool is the SYSTEM bundle (the Operator's 08-12 spec): the
doctor, custodian, nurse, and janitor are the domain's system tools, and
memory/emotion/vault/session/kanban are the CORE systems — grouped here
so the agent can tap into them properly.

## Machine health

- `system_info` — OS, kernel, machine, cores, memory, python
- `process_list` — running processes (pid/name/state)
- `disk_usage` — total/used/free for a path

## Domain subsystems (read-only probes)

- `doctor` · `custodian` · `nurse` · `janitor` — schedules + roles

## CORE systems

- `memory` — read/add/clear entries (side: assistant|user)
- `emotion` — table (the 24×24 grid), highlight (vector→cells), name
  (axis+value→emotion)
- `vault` — record entries (type: message/tool/skill) + query the raw
  entries table
- `session` — list active sessions, get/drop a session state
- `kanban` — the Queen/Worker/Drone board: list, add, update, delegate,
  spawn subagents

## Usage

```
system {"action": "system_info"}
system {"action": "doctor"}
system {"action": "memory", "op": "read", "side": "assistant"}
system {"action": "memory", "op": "add", "side": "user", "content": "..."}
system {"action": "emotion", "op": "table"}
system {"action": "emotion", "op": "name", "axis": "joy", "value": 2}
system {"action": "vault", "op": "record", "kind": "message", "content": "..."}
system {"action": "vault", "op": "query", "category": "message"}
system {"action": "session", "op": "list"}
```

## When to use

- Machine health or house subsystem status.
- Reading/writing memory, emotion vectors, the vault, or sessions —
  the CORE systems the agent needs to tap into.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/system.py` — registers `system`.

---
---
