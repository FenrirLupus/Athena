---
name: tool-manage
category: system
description: Use when adding, changing or removing a tool — a verb an agent calls, described by a TOOL.md.
---

# Tool management

A tool is a folder with `TOOL.md` and, for most kinds, the code it runs. A TOOL.md declares **what** the tool is and
**what arguments** it takes; the code does the work. Nothing about a tool is inferred from its code.

```text
<profile>/tools/<name>/TOOL.md      the declaration you write — visible to this agent only
<profile>/tools/<name>/scripts/     the code it runs, one file per tool
<athena>/tools/system/<name>/       the system tools: readable and callable, never editable
```

Both halves are required. A `.py` file on its own is not a tool — a capability without a `TOOL.md` is prose, and a
`TOOL.md` without a script is a promise nothing keeps.

**One package per capability, verbs inside.** The folder is named for the capability (`kanban`, not `kanban-add`;
`environment`, not `envrequest`), the script dispatches a verb, and the descriptor declares every verb with its
parameters. One descriptor registers **one tool**, whose `action` argument picks the verb.

The program's own tools are packages in the same shape under `tools/system/` in the program tree; the only difference
is that a system script defines `run(args, ctx)` and is called in-process, because those tools ask the scope guard
before every call and a subprocess has no scope.

Where you may write: **your own profile**. `<profile>/skills/`, `<profile>/tools/`, `<profile>/plugins/`.
The shared copies under `<athena>/` are **read-only** for you — you can read and use them, never change them.


## Create

```markdown
---
name: disk_usage
description: Report free space for a path inside the workspace.
kind: script
script: scripts/disk_usage.py
timeout_s: 20
---

Arguments: `{"path": "…"}`. Returns the free bytes and the mount point.
```

| kind | runs | needs |
|---|---|---|
| `script` | a file under `scripts/` | `script`, plus `actions:` and a one-line `parameters` |
| `command` | argv, substituted from the arguments | `command: ["df", "-h", "{path}"]` |
| `http` | one request | `url`, optional `method` |

A package with more than one verb puts the verbs in `actions:` and an `action` enum in `parameters`:

```markdown
---
name: kanban
description: The agent's own kanban board.
kind: script
script: scripts/kanban.py
actions: add, list, dispatch, close
parameters: '{"type": "object", "properties": {"action": {"type": "string", "enum": ["add", "list", "dispatch", "close"]}, "title": {"type": "string"}}, "required": ["action"]}'
timeout_s: 20
---
```

The loader refuses a descriptor whose `actions:` and `action` enum disagree. Whatever the capability puts under
`references/` arrives with it — you do not call a second tool to read documentation.

A `script` tool receives its arguments as JSON on argv[1] and in `ATHENA_ARGS`, and must print its result as JSON on
stdout. Exit non-zero for failure — a zero exit with an error string is reported as success. Dispatch on `action`
inside the script; do not add a package per verb.

Tools run sandboxed: the environment is scrubbed, the writable path is the agent's own profile, and the timeout is
`timeout_s` (default 20 s). A tool that needs more is a sign it should be a job, not a tool.

## Modify

Edit `TOOL.md` for description, arguments or timeout; edit the script for behaviour. The declaration is read fresh on
each turn, so both are live immediately.

Change the argument names in `TOOL.md` **and** the code together — a mismatch produces a tool the model calls correctly
and the script then fails on.

## Remove

```text
rm -rf <profile>/tools/disk_usage
```

Removing your folder removes the verb for this agent; a system tool of the same name is still there underneath. Check
nothing else calls it by name — a skill or instruction may tell an agent to use it, and that guidance would then point
at nothing.

## Check your work

```text
./athena.sh tools        # every tool on disk, with the declaration each one carries
```

A tool whose `TOOL.md` does not parse is skipped silently; if it is missing from the list, the header is the suspect.
