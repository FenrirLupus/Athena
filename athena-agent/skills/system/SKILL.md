---
name: system
description: "Use the built-in system tools — machine health, domain subsystems, and the CORE systems (memory/emotion/vault/session/kanban)."
---

# System

The built-in `system` tool bundles machine health + the domain's system
subsystem status (doctor, custodian, nurse, janitor) + the CORE systems
an agent needs to tap into:

```
system {"action": "system_info"}
system {"action": "process_list", "limit": 10}
system {"action": "doctor"}
system {"action": "memory", "op": "read", "side": "assistant"}
system {"action": "memory", "op": "add", "side": "user", "content": "..."}
system {"action": "emotion", "op": "table"}
system {"action": "vault", "op": "record", "kind": "message", "content": "..."}
system {"action": "vault", "op": "query"}
system {"action": "session", "op": "list"}
system {"action": "kanban", "op": "list"}
system {"action": "kanban", "op": "delegate", "title": "...", "assignee": "worker-bee"}
```

Use for machine health, domain subsystem status, and the CORE systems
(memory, emotion, vault, session, kanban). The status actions are
read-only probes.

---
---
