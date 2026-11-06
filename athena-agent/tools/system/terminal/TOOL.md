---
name: terminal
description: Run shell commands, contained by bubblewrap so they can only write inside the agent's own trees.
kind: script
script: scripts/terminal.py
actions: run
parameters: '{"type": "object", "properties": {"command": {"type": "string", "description": "The shell command to run. Required for action: run."}, "cwd": {"type": "string", "description": "Working directory; defaults to sandbox/."}, "timeout_s": {"type": "number", "description": "Seconds before it is killed."}, "network": {"type": "boolean", "description": "Allow network access (default from config)."}}, "required": ["command"]}'
timeout_s: 60
---

# terminal

A real shell — `bash -lc` — because some work is a pipeline and not a sequence of file calls.

## Where it runs

```
bwrap  --ro-bind / /                    everything readable, nothing writable
       --bind   <sandbox>   <sandbox>   this agent's scratch tree
       --bind   <workspace> <workspace> this agent's working tree
       --bind   <granted>   <granted>   any workspace the user granted
       --tmpfs  /tmp
       --die-with-parent --new-session --unshare-pid
```

Inside that, `rm -rf ~/.athena/athena-agent` fails with `Read-only file system`. Not because a rule said no, but
because the mount is read-only — which is the only kind of "no" a shell respects.

## Arguments

| argument | default | meaning |
|---|---|---|
| `command` | — | the shell line, run with `bash -lc` |
| `cwd` | `sandbox/` | must be a directory you may write; anything else is refused |
| `timeout_s` | 120 | killed at the deadline |
| `network` | config (`true`) | `false` adds `--unshare-net` |

## When containment is impossible

If `bwrap` is missing the tool **refuses to run at all** — it does not fall back to an uncontained shell. A tool that
silently degrades from "cannot touch anything" to "can touch everything" is worse than no tool, because the agent has
no way to tell which one it got.

`tools.terminal.require_containment: false` accepts that risk deliberately. Setting it is a decision, not a fix.

## Reading the result

`contained: true` means the command ran inside the sandbox described above. If `bwrap` refuses to start, the result
says `"the command did NOT run uncontained"` — a failed command is never a silent bypass.

## See also

`references/containment.md` — what a shell still reaches even under bubblewrap, and why the agent's `HOME` is the
profile rather than the user's.
