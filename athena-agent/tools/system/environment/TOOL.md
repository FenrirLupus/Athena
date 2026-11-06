---
name: environment
description: Ask for a tool pack (or box packages) to be installed into this environment, and see what is still waiting on the user.
kind: script
script: scripts/environment.py
actions: request, status
parameters: '{"type": "object", "properties": {"action": {"type": "string", "enum": ["request", "status"], "description": "The verb: Ask the user to install a staged tool pack (or box packages) into this environment; List the install requests still waiting on the user."}, "kind": {"type": "string", "enum": ["tool", "box"], "description": "'tool' is a staged TOOL.md pack, 'box' is operating-system packages."}, "name": {"type": "string", "description": "Pack name, or for kind=box the package names (comma-separated). Required for action: request."}, "source": {"type": "string", "description": "Or an explicit path to the staged pack."}, "reason": {"type": "string", "description": "One line for the card."}}, "required": ["action"]}'
timeout_s: 60
---

# environment

The agent's half of an installation, and only that half. Two verbs:

| verb | does |
|---|---|
| `request` | write a request for a staged tool pack (`kind: tool`) or for operating-system packages (`kind: box`) into the queue the interface polls |
| `status` | list the requests still waiting on a click — read-only, and it answers nothing |

A human sees the same approval card a plugin install produces and clicks Install or Cancel. `request` needs the
sandbox to be writable (it reads the staged pack from there); `status` needs nothing and changes nothing, so asking
what is pending can never itself answer a card.

Nothing here writes outside the sandbox, because a capability is something the user grants.
