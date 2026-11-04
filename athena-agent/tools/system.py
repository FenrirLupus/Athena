"""Where the program's own tool implementations live, by name.

`athena-agent/tools/` holds the code; this module is the *catalogue* the `tools/system/` descriptors point at with
`kind: builtin` and `impl: <name>`. That indirection is the point: the descriptor says what a system tool is and how
to use it, the implementation stays in the program where the guard can see it, and a descriptor never contains a
path to run.

| impl | tools | notes |
|---|---|---|
| `filesystem` | ls, read, write, append, mkdir, move, copy, delete, stat, find | every call goes through `Scope.check()`; the verb names are already the cross-platform layer (`ls` is `dir` is `Get-ChildItem`) |
| `terminal` | terminal | a shell inside `bwrap`, read-only root, only sandbox/workspace writable |
| `computer` | screen_capabilities, screenshot, mouse_move, mouse_click, type_text, press_key, browser | off unless `tools.computer.enabled: true` |
"""
from __future__ import annotations

IMPLS = ("filesystem", "terminal", "computer")


class UnknownImpl(RuntimeError):
    pass


def build_impl(name: str, ctx, registry=None) -> list:
    """The tools one implementation provides, minus anything already registered."""
    wanted = (name or "").strip().lower()
    if wanted not in IMPLS:
        raise UnknownImpl(f"unknown builtin implementation {name!r}; expected one of {', '.join(IMPLS)}")

    if wanted == "filesystem":
        from tools import filesystem
        tools = filesystem.build(ctx)
    elif wanted == "terminal":
        from tools import terminal
        tools = terminal.build(ctx)
    else:
        from tools import computer
        tools = computer.build(ctx)

    if registry is not None:
        existing = set(registry.names())
        tools = [tool for tool in tools if tool.name not in existing]
    return tools
