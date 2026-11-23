---
name: sandbox
description: Handle untrusted code without trusting it: clone and inventory it (inspect), or execute it under an inescapable environment and report every connection, listener, process and write it attempts (run).
kind: script
script: scripts/sandbox.py
actions: inspect, run
parameters: '{"type":"object","properties":{"action":{"type":"string","enum":["inspect","run"]},"url":{"type":"string","description":"repository URL or local path (action=inspect)"},"name":{"type":"string","description":"folder under sandbox/repos (action=inspect)"},"path":{"type":"string","description":"a clone already inside the sandbox (action=run)"},"entry":{"type":"string","description":"command to run inside the clone; omitted means main.py/app.py/run.sh"},"timeout_s":{"type":"integer","description":"wall-clock kill, default 120"},"allow_net":{"type":"boolean","description":"keep a network namespace of its own; still no route added"}},"required":["action"]}'
timeout_s: 60
---

# repo

`sandbox_inspect` — clone a repository into the agent's sandbox and inventory it: licence, dependencies, entry points,
hooks, agent instructions, and a scan for the things that should stop a decision.

## What it will not do

- Run any of it. Nothing in the clone is imported or executed; the tool reads files.
- Clone outside the sandbox, or keep a repository the scope guard refuses.
