"""Tools — a verb with its own code, described by a `TOOL.md` in a package.

A tool is the layer below a plugin: it *does* something (unlike a skill, which is prose), but it is one declared
verb — or one family of verbs — with its own script, rather than a program that can register anything.

    <root>/tools/<name>/TOOL.md          what it is, and what arguments it takes
    <root>/tools/<name>/scripts/         the code it actually runs
    <root>/tools/<name>/references/      optional: the text it carries

**The loader is `tools/loader.py`**, in the package it loads. This module is the historical name for that machinery:
`core` callers, the CLI and the suites import it, so it re-exports the loader instead of keeping a second discovery
pass that could disagree with the first. Changing what a tool is means changing one file.

Four kinds:

| kind | runs | note |
|---|---|---|
| `python` | imported by path, in-process; `build(ctx)` returns its Tools | the program's own tools: they ask the scope guard before every call, and a subprocess has no scope |
| `script` | a file under `scripts/`, with the interpreter its extension asks for | arguments arrive as JSON |
| `command` | an argv with `{placeholders}` filled in | no shell, so a value cannot become a second command |
| `http` | GET/POST a URL, parsed JSON back | no credentials injected |

**Arguments reach a script as JSON**, in `ATHENA_ARGS` and as `argv[1]`, so a script never parses a command line
and a value containing a space or a quote cannot change what runs. The five `ATHENA_*` variables are exported to
the script, and its `cwd` is the agent's `sandbox/`.

**Who may write one.** `.athena/tools/` is inside `ATHENA_ROOT`, which is read-and-list for agents: an agent sees
the tools and cannot author one. A tool is a capability, and capabilities come from the person.

**Trust.** A tool's script is user-authored code running with the server's privileges — the scope guard does not
police it, exactly as it does not police a plugin. That is why the folder is not agent-writable.
"""
from __future__ import annotations

import sys

from core import trace
from tools.loader import (                                        # noqa: F401 — re-exported on purpose
    INTERPRETERS,
    KINDS,
    MAX_OUTPUT,
    PROGRAM_TOOLS,
    DescriptorError,
    PackError,
    action_map,
    agent_roots,
    dispatch,
    bases,
    discover,
    discover_with_notes,
    enabled_names,
    import_script,
    load,
    load_all,
    script_module,
    system_names,
    _actions,
    _contained,
    _parameters,
    _run_command,
    _run_http,
    _run_script,
    _scrubbed_env,
    _tool_from,
)

__all__ = ["KINDS", "PackError", "DescriptorError", "discover", "discover_with_notes", "enabled_names",
           "system_names", "load", "load_all", "script_module", "import_script", "agent_roots"]


# One line, at import, when the historical name is reached for: it says who still uses it, then gets out of the way.
# The work is traced in `tools.loader`, where it happens — a line per call here would only ever say "a wrapper ran".
def _importer() -> str:
    """The module that imported this shim, skipping the import machinery's own frames."""
    frame = sys._getframe(1)
    while frame is not None:
        name = str(frame.f_globals.get("__name__") or "")
        if name and name != __name__ and not name.startswith(("importlib", "_frozen_importlib")):
            return f"{name} at {frame.f_code.co_filename.rsplit('/', 1)[-1]}:{frame.f_lineno}"
        frame = frame.f_back
    return "?"


trace.event("toolpacks.shim", level="debug", module=__name__, caller=_importer())
