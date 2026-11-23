---
name: sandbox
description: Use when handling untrusted code — a repository, a script, a package — before trusting, installing or copying from it. Inspect it, run it under watch, report what it does.
category: system
---

# Sandbox

Untrusted code is not a reading problem. Static inspection tells you what the code *contains*; only running it under
observation tells you what it *does* — what it dials, what it listens on, what it writes, what it spawns.

Never run untrusted code inline. Not in your workspace, not in a scratch folder, not "just this once" to see the error.

## The procedure

```text
1  acquire     sandbox inspect        clone into sandbox/repos/<name>; no execution, ever
2  read        the inventory         licence, dependencies, entry points, hooks, agent instructions, red flags
3  decide      is running it needed  if the inventory answers the question, stop. Most repos never need step 4
4  run         under isolation       sandbox run — its own namespaces, read-only root, no route out, no access
                                     to .secret or the vault, cpu/mem limits and a wall-clock kill
5  watch       network + filesystem  every outbound attempt terminates on a monitor that records it; every
                                     listener is found and probed; writes land in an overlay you keep
6  report      evidence              connections attempted, listeners opened, files touched, exit status, budget
7  destroy     the environment       namespace and overlay go; the report stays
```

Step 3 is the one agents skip. If `repo inspect` says the dependency list and entry points are ordinary, running it
buys nothing and costs an isolation you have to build correctly.

## How to run it

`sandbox run` does it: `path` (a clone inside your sandbox), optional `entry`, `timeout_s`. It refuses `allow_net` —
a network you share with the host is not a sandbox — so a run either has no route at all, or you wired a namespace and
a recording proxy yourself, in which case use `references/run-procedure.md` for profile B.

Read the report for what it proves: `blocked.network` and `blocked.writes` are enforcement (they happened), while
`evidence.*` is observation (what was attempted). Without a tracer the second is empty, and an empty list means "not
recorded", never "nothing tried".

## What isolation must hold

- **Its own namespaces** — pid, net, ipc, mount, user. A process that shares the host's network is not sandboxed.
- **Read-only everything** — root bound read-only, `/tmp` and `/home` replaced by tmpfs, no `.secret`, no `vault.db`,
  no profile. A write that cannot happen cannot leak.
- **A budget** — cpu, memory, pids and wall clock. Code that mines or forks until the box dies must die first.
- **A monitor on the network** — either no route at all (you learn intent, every connection fails) or one veth to a
  recording proxy (you learn intent and whether it would have worked). Never a bridged interface.
- **Evidence you keep** — the observed connections and writes are the deliverable; the environment is disposable.

## What it is not

`bwrap` is strong against ordinary code and against accident. It is not a defence against a deliberate kernel exploit;
that class needs gVisor or a micro-VM. Say which you used when you report, because "sandboxed" without a qualifier
claims more than it delivers.

## Reporting

Every sandbox run ends with a short evidence block — what it connected to, what it listened on, what it wrote, what it
exited with. An untrusted run with no report is a run nobody can decide anything from.
