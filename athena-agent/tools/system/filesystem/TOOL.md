---
name: filesystem
description: Read, create, change, move and delete files and directories — with one set of verbs on every OS.
kind: script
script: scripts/filesystem.py
actions: ls, read, write, append, mkdir, move, copy, delete, stat, find
parameters: '{"type": "object", "properties": {"action": {"type": "string", "enum": ["ls", "read", "write", "append", "mkdir", "move", "copy", "delete", "stat", "find"], "description": "The verb: List a directory inside your scope; Read a text file inside your scope; Create or overwrite a text file inside your scope; Append text to a file inside your scope; Create a directory inside your scope; Move or rename a file or directory inside your scope; Copy a file or directory inside your scope; Delete a file or directory inside your scope; Size, mode and mtime of a path inside your scope; Find paths matching a glob inside your scope."}, "path": {"type": "string", "description": "Directory to list; '.' means the agent's home. Required for action: append, delete, mkdir, read, stat, write."}, "limit": {"type": "integer", "description": "Maximum entries to return (default 200)."}, "max_chars": {"type": "integer", "description": "Characters to return (default 20000)."}, "content": {"type": "string", "description": "The full text to write. Required for action: append, write."}, "source": {"type": "string", "description": "Existing path. Required for action: copy, move."}, "destination": {"type": "string", "description": "New path. Required for action: copy, move."}, "pattern": {"type": "string", "description": "Glob, e.g. '*.py'. Required for action: find."}}, "required": ["action"]}'
timeout_s: 60
---

# filesystem

The agent's hands on the disk. Ten verbs, and they are deliberately *not* shell commands: the point is that the same
call works on Linux, on Windows and on a Mac, and that every one of them asks the scope guard first.

| verb | does | flag it needs |
|---|---|---|
| `ls` | list a directory | list |
| `read` | read a text file | read |
| `write` | create, or overwrite an existing file | write, or modify if it already exists |
| `append` | add to the end of a file | write / modify |
| `mkdir` | create a directory | write |
| `move` | move or rename | modify on the source, write on the destination |
| `copy` | copy a file or tree | read on the source, write on the destination |
| `delete` | remove a file or directory | modify |
| `stat` | size, mode, mtime | read |
| `find` | glob under a directory | list |

## Why verbs and not commands

`ls` is `dir` is `Get-ChildItem`; `rm` is `del` is `Remove-Item`. An agent that has been taught one shell's spelling
breaks the moment it runs somewhere else. The verbs are the cross-platform layer, and the guard sees the path in every
case — which a shell command would deny it.

## What it will not do

- Follow a symlink out of scope: the path is resolved before it is judged.
- Write to `athena-agent/`, to `.secret`, or into another agent's home.
- Prompt for anything inside those three — they are lines, not choices.

Anything merely *out of scope* is a question for the user, not a refusal, and the answer arrives through the approval
card in the interface.

## See also

`references/scope-map.md` — where each verb lands, and which of the five `ATHENA_*` places it may touch.
