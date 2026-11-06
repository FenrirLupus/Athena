"""The filesystem toolset — the first thing that can actually write, and therefore the first thing that needs
the scope guard.

Every function here goes through `Scope.check()` before it touches anything, which means each call is asking the
same question the whole architecture asks: *may this agent do this, here?* Three answers are possible:

    allowed     → the call happens
    refused     → a hard line (a `.secret`, the system tree, another agent's home), never negotiable
    in doubt    → out of scope, so the broker asks the user and this call **waits** for the answer

The tool names are the verbs a person would use. Nothing clever: `ls`, `read`, `write`, `append`, `mkdir`, `move`,
`copy`, `delete`, `stat`, `find`.
"""
from __future__ import annotations

from pathlib import Path

from core import paths as paths_mod
from core import trace
from tools.registry import Tool, ToolError

MAX_READ = 200_000


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile folder the tools are bound to."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")


class Context:
    """What a tool needs to know about the agent calling it: its scope, and who to ask."""

    def __init__(self, scope=None, approvals=None, agent_id: str = ""):
        self.scope = scope
        self.approvals = approvals
        # the lane's minted id, when the caller knows it: a trace or a metric row attributed by label cannot tell a
        # re-created lane from the one that did the work
        self.agent_id = agent_id

    def check(self, path: str, flag: str, tool: str) -> Path:
        if self.scope is None:
            raise ToolError("no scope is bound to this tool call — refusing to touch the filesystem")
        try:
            return self.scope.check(str(path), flag, approvals=self.approvals, tool=tool)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc




PROTECTED_NAMES = (".secret", "api_token")


def _smuggled(root: Path) -> str:
    """The first protected file under `root`, or "" — a directory copy is a way around a per-path check.

    `copy()` resolved the *source* path through the guard, so copying one file called `.secret` was refused. Copying
    its parent was not: `copytree` carried every `.secret` and the API token into a granted workspace, which is the
    whole credential set in cleartext. The check has to look inside the tree, not only at its door.
    """
    try:
        entries = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
    except OSError:
        return ""
    for entry in entries:
        if entry.name in PROTECTED_NAMES or entry.name.startswith(".secret."):
            return str(entry)
    return ""


def build(ctx: Context | None = None) -> list[Tool]:
    """The ten filesystem tools, bound to one agent's scope.

    `ctx` is the agent's context: whatever carries `check()` (the channel's Context, or this module's own) is used
    as it is; without one, the tools still exist and every call refuses, which is the honest answer when no agent is
    bound.
    """
    if ctx is None or not hasattr(ctx, "check"):
        wrapped = Context(scope=getattr(ctx, "scope", None), approvals=getattr(ctx, "approvals", None),
                          agent_id=str(getattr(ctx, "agent_id", "") or ""))
        for attribute in ("profile_root", "athena_root", "cfg"):
            setattr(wrapped, attribute, getattr(ctx, attribute, None))
        ctx = wrapped

    def ls(path: str = ".", limit: int = 200) -> dict:
        with trace.span("tool.filesystem.ls", channel=_lane(ctx), path=path, limit=limit) as sp:
            target = ctx.check(path, paths_mod.LIST, "ls")
            if not target.is_dir():
                raise ToolError(f"{target} is not a directory")
            rows = []
            for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))[:max(1, min(limit, 500))]:
                try:
                    size = entry.stat().st_size if entry.is_file() else 0
                except OSError:
                    size = 0
                rows.append({"name": entry.name, "dir": entry.is_dir(), "bytes": size})
            sp.ok(entries=len(rows), path=str(target))
            return {"path": str(target), "entries": rows}

    def read(path: str, max_chars: int = 20000) -> dict:
        with trace.span("tool.filesystem.read", channel=_lane(ctx), path=path) as sp:
            target = ctx.check(path, paths_mod.READ, "read")
            if not target.is_file():
                raise ToolError(f"{target} is not a file")
            cap = max(200, min(int(max_chars), MAX_READ))
            text = target.read_text(encoding="utf-8", errors="replace")
            content = text[:cap] + ("\n[truncated]" if len(text) > cap else "")
            sp.ok(bytes=len(text.encode()), returned=len(content), path=str(target))
            return {"path": str(target), "bytes": target.stat().st_size, "content": content}

    def write(path: str, content: str = "") -> dict:
        # creating is `write`; overwriting something that exists is `modify` — the two are not the same risk
        target = Path(path).expanduser()
        flag = paths_mod.MODIFY if target.exists() else paths_mod.WRITE
        with trace.span("tool.filesystem.write", channel=_lane(ctx), path=str(target), chars=len(content)) as sp:
            resolved = ctx.check(str(target), flag, "write")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            existed = resolved.exists()
            resolved.write_text(content, encoding="utf-8")
            sp.ok(bytes=len(content.encode()), overwrote=existed, path=str(resolved))
            return {"path": str(resolved), "bytes": len(content.encode()), "overwrote": existed}

    def append(path: str, content: str = "") -> dict:
        target = Path(path).expanduser()
        flag = paths_mod.MODIFY if target.exists() else paths_mod.WRITE
        with trace.span("tool.filesystem.append", channel=_lane(ctx), path=str(target), chars=len(content)) as sp:
            resolved = ctx.check(str(target), flag, "append")
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with resolved.open("a", encoding="utf-8") as handle:
                handle.write(content)
            sp.ok(appended=len(content.encode()), path=str(resolved))
            return {"path": str(resolved), "appended": len(content.encode())}

    def mkdir(path: str) -> dict:
        with trace.span("tool.filesystem.mkdir", channel=_lane(ctx), path=path) as sp:
            resolved = ctx.check(str(Path(path).expanduser()), paths_mod.WRITE, "mkdir")
            resolved.mkdir(parents=True, exist_ok=True)
            sp.ok(created=resolved.is_dir(), path=str(resolved))
            return {"path": str(resolved), "created": resolved.is_dir()}

    def move(source: str, destination: str) -> dict:
        src = Path(source).expanduser()
        dst = Path(destination).expanduser()
        with trace.span("tool.filesystem.move", channel=_lane(ctx), source=str(src), destination=str(dst)) as sp:
            src_resolved = ctx.check(str(src), paths_mod.MODIFY, "move")      # moving takes it away
            dst_flag = paths_mod.MODIFY if dst.exists() else paths_mod.WRITE
            dst_resolved = ctx.check(str(dst), dst_flag, "move")
            if not src_resolved.exists():
                raise ToolError(f"{src_resolved} does not exist")
            dst_resolved.parent.mkdir(parents=True, exist_ok=True)
            src_resolved.replace(dst_resolved)
            sp.ok(source=str(src_resolved), destination=str(dst_resolved))
            return {"from": str(src_resolved), "to": str(dst_resolved)}

    def copy(source: str, destination: str) -> dict:
        import shutil

        src = Path(source).expanduser()
        dst = Path(destination).expanduser()
        with trace.span("tool.filesystem.copy", channel=_lane(ctx), source=str(src), destination=str(dst)) as sp:
            src = ctx.check(str(src), paths_mod.READ, "copy")
            dst_flag = paths_mod.MODIFY if dst.exists() else paths_mod.WRITE
            dst_resolved = ctx.check(str(dst), dst_flag, "copy")
            if not src.exists():
                raise ToolError(f"{src} does not exist")
            smuggled = _smuggled(src)
            if smuggled:
                raise ToolError(f"{smuggled} is a credentials file and cannot be copied, nor can a directory that "
                                f"contains one — copy the ordinary files instead")
            dst_resolved.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst_resolved, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst_resolved)
            sp.ok(source=str(src), destination=str(dst_resolved))
            return {"from": str(src), "to": str(dst_resolved)}

    def delete(path: str) -> dict:
        import shutil

        with trace.span("tool.filesystem.delete", channel=_lane(ctx), path=path) as sp:
            target = ctx.check(str(Path(path).expanduser()), paths_mod.MODIFY, "delete")
            if not target.exists():
                raise ToolError(f"{target} does not exist")
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            sp.ok(deleted=True, path=str(target))
            return {"path": str(target), "deleted": True}

    def stat(path: str) -> dict:
        with trace.span("tool.filesystem.stat", channel=_lane(ctx), path=path) as sp:
            target = ctx.check(str(Path(path).expanduser()), paths_mod.READ, "stat")
            if not target.exists():
                raise ToolError(f"{target} does not exist")
            info = target.stat()
            sp.ok(bytes=info.st_size, dir=target.is_dir(), path=str(target))
            return {"path": str(target), "dir": target.is_dir(), "bytes": info.st_size,
                    "mode": oct(info.st_mode)[-3:], "modified": int(info.st_mtime)}

    def find(path: str = ".", pattern: str = "*", limit: int = 200) -> dict:
        with trace.span("tool.filesystem.find", channel=_lane(ctx), path=path, pattern=pattern) as sp:
            target = ctx.check(path, paths_mod.LIST, "find")
            hits = []
            for entry in target.rglob(pattern):
                if entry.name in PROTECTED_NAMES or entry.name.startswith(".secret."):
                    continue                        # a listing of names is reconnaissance for the copy hole
                try:
                    hits.append(str(entry.relative_to(target)))
                except ValueError:
                    continue
                if len(hits) >= max(1, min(int(limit), 1000)):
                    break
            sp.ok(matches=len(hits), path=str(target))
            return {"path": str(target), "pattern": pattern, "matches": hits}

    def schema(props: dict, required: list[str]) -> dict:
        """A real JSON Schema — the wire format the model is given, not a shorthand for it."""
        return {"type": "object", "properties": props, "required": required}

    def S(description: str) -> dict:
        return {"type": "string", "description": description}

    def I(description: str) -> dict:
        return {"type": "integer", "description": description}

    return [
        Tool("ls", "List a directory inside your scope.",
             schema({"path": S("Directory to list; '.' means the agent's home."),
                     "limit": I("Maximum entries to return (default 200).")}, []), ls),
        Tool("read", "Read a text file inside your scope.",
             schema({"path": S("Absolute path, or relative to the agent's home."),
                     "max_chars": I("Characters to return (default 20000).")}, ["path"]), read),
        Tool("write", "Create or overwrite a text file inside your scope. Overwriting needs the modify flag.",
             schema({"path": S("Where to write."),
                     "content": S("The full text to write.")}, ["path", "content"]), write),
        Tool("append", "Append text to a file inside your scope.",
             schema({"path": S("File to append to."),
                     "content": S("Text to add.")}, ["path", "content"]), append),
        Tool("mkdir", "Create a directory inside your scope.",
             schema({"path": S("Directory to create.")}, ["path"]), mkdir),
        Tool("move", "Move or rename a file or directory inside your scope.",
             schema({"source": S("Existing path."), "destination": S("New path.")}, ["source", "destination"]), move),
        Tool("copy", "Copy a file or directory inside your scope.",
             schema({"source": S("Existing path."), "destination": S("Where the copy goes.")},
                    ["source", "destination"]), copy),
        Tool("delete", "Delete a file or directory inside your scope. This destroys data.",
             schema({"path": S("Path to remove.")}, ["path"]), delete),
        Tool("stat", "Size, mode and mtime of a path inside your scope.",
             schema({"path": S("Path to inspect.")}, ["path"]), stat),
        Tool("find", "Find paths matching a glob inside your scope.",
             schema({"path": S("Root of the search; '.' means the agent's home."),
                     "pattern": S("Glob, e.g. '*.py'."),
                     "limit": I("Maximum matches (default 200).")}, ["pattern"]), find),
    ]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "filesystem")
