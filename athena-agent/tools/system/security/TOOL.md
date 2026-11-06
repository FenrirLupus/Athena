---
name: security
description: What the security watchdogs have found, and what to do about it.
kind: script
script: scripts/security.py
actions: report
parameters: '{"type": "object", "properties": {"limit": {"type": "integer", "description": "Maximum findings to return."}}, "required": []}'
timeout_s: 60
---

# security

`security_report` — the findings of the two watchdogs (red: what an attacker could do; blue: what the system
enforces), with the counts and the path to the full report.

Read-only on purpose. A tool that could re-baseline would let an agent erase the evidence of its own tampering;
re-baselining is a user action (`./athena.sh security baseline`).
