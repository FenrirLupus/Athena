"""Tool registry — the minimal surface 0.0.1 needs to prove the iterative loop.

A Tool is a name, a description (the routing line the model sees), a JSON schema for its
arguments, and a callable. Registration refuses duplicates: a silent last-one-wins once deleted a
live safety guard (documentation/architecture/05-extensions.md).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from core import trace
from core.root import athena_root

# Tools may read what an agent was given, and nothing else. Attachments land in
# `profiles/<channel>/workspace/`, so that is the one tree a file tool is allowed to open.
WORKSPACE_ROOT = athena_root() / "profiles"
READ_CAP_BYTES = 200_000


class ToolError(RuntimeError):
    pass


class Tool:
    def __init__(self, name: str, description: str, parameters: dict, fn):
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.fn = fn

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name,
                             "description": self.description,
                             "parameters": self.parameters}}


class Registry:
    def __init__(self, max_result_bytes: int = 8000):
        self._tools: dict[str, Tool] = {}
        self._lock = threading.Lock()
        self.max_result_bytes = int(max_result_bytes)
        self.channel = ""                 # the lane this registry is bound to; "" until `bind` says otherwise

    # -- registration ------------------------------------------------------
    def register(self, tool: Tool) -> None:
        with self._lock:
            if tool.name in self._tools:
                raise ToolError(f"duplicate tool name: {tool.name!r} is already registered")
            self._tools[tool.name] = tool

    def discard(self, name: str) -> None:
        """    Remove a tool by name, which is how a non-additive plugin is rolled back."""
        with self._lock:
            self._tools.pop(name, None)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._tools)

    # -- model-facing ------------------------------------------------------
    def schemas(self) -> list[dict]:
        with self._lock:
            return [t.schema() for t in self._tools.values()]

    # -- invocation --------------------------------------------------------
    def invoke(self, name: str, arguments: dict, timeout: float = 60.0) -> str:
        """Run a tool and ALWAYS return a string — a refusal is information for the model.

        The one place a tool call is entered, so it is the one place the trace wraps it: `→ tool.call` with the
        tool name and the *names* of the arguments (never their values — one of them may be a credential), then
        `← tool.call` with status, ms and the size of what the model got back. A tool that raises is recorded as
        a failure with the exception and the tool name, and still returns its error string as before.
        """
        lane = self.channel or "-"
        keys = sorted(str(key) for key in (arguments or {}))
        with trace.span("tool.call", channel=lane, tool=name, args=keys, timeout=timeout) as sp:
            text, extra = self._invoke(name, arguments, timeout, lane, keys)
            exc = extra.pop("exc", None)
            if exc is not None:
                # the exit of a failed call is the ✗ line: status=failed with the exception and the frame that
                # raised it. A failure is recorded at `error` even when this span's own lines are below the level.
                sp.failed(exc)
            else:
                sp.ok(bytes=len(text), **extra)
        return text

    def _invoke(self, name: str, arguments: dict, timeout: float, lane: str,
                keys: list[str]) -> tuple[str, dict]:
        """The call itself: returns (what the model sees, facts for the exit line). Never changes the string."""
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return (f"error: unknown tool {name!r}; available: {', '.join(self.names()) or 'none'}",
                    {"refused": "unknown tool"})
        # The timeout is enforced here, not merely accepted. A Python thread cannot be killed, so a call that
        # overruns is ABANDONED: the lane answers with an error instead of blocking for ever on its only worker.
        # That is the difference between one hung tool and a dead channel.
        outcome: dict = {}

        def call() -> None:
            try:
                outcome["out"] = tool.fn(**arguments)
            except BaseException as exc:               # noqa: BLE001 — carried back to the model as text
                outcome["err"] = exc

        worker = threading.Thread(target=call, name=f"tool:{name}", daemon=True)
        worker.start()
        worker.join(timeout if timeout and timeout > 0 else None)
        if worker.is_alive():
            trace.event("tool.abandoned", level="info", channel=lane, tool=name, timeout_s=timeout)
            return ((f"error: {name} did not finish within {timeout:.0f}s. The call was abandoned so this agent can "
                     f"keep working; it may still be running in the background."),
                    {"abandoned": True, "timeout_s": float(timeout or 0)})
        if "err" in outcome:
            exc = outcome["err"]
            # the tool's name and the exception travel with the exit line: a failure is a frame, not a guess
            if isinstance(exc, TypeError):
                return f"error: bad arguments for {name}: {exc}", {"exc": exc}
            return (f"error: {name} failed: {type(exc).__name__}: {exc}", {"exc": exc})
        out = outcome.get("out", "")
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        if len(text) > self.max_result_bytes:
            text = text[:self.max_result_bytes] + f"\n[truncated at {self.max_result_bytes} bytes]"
            return text, {"truncated": True}
        return text, {}


    def bind(self, ctx) -> "Registry":
        """A registry for one agent: the primitives plus every package the loader finds, bound to `ctx.scope`.

        A new object rather than a mutated one — the manager is shared by every channel, and a per-agent scope
        stored on the shared registry would be whichever agent happened to be built last.

        There is no list of tools here any more. `tools.loader` discovers `TOOL.md` packages across the program, the
        home and the profile, imports each script by path, and reports what it skipped; a tool that is not a package
        is not a tool.
        """
        from tools import loader

        clone = Registry(max_result_bytes=self.max_result_bytes)
        clone.cfg = getattr(self, "cfg", {})
        # what the trace calls this lane: the agent bound here, never a caller's claim — so a line can be
        # attributed to the channel whose turn produced it without threading a name through every call
        clone.channel = _lane_name(ctx)
        for tool in getattr(self, "_builtins", []):
            clone.register(tool)

        profile_root = getattr(ctx, "profile_root", None)
        athena_root_ctx = getattr(ctx, "athena_root", None)
        clone.pack_report = loader.load(clone, ctx=ctx, profile_root=profile_root, athena_root=athena_root_ctx)

        # plugins: only the ones named in `plugins.enabled`, and a broken one is reported not raised
        from core import plugins as plugins_mod

        cfg_now = getattr(ctx, "cfg", None) or clone.cfg or {}
        athena_root = getattr(ctx, "athena_root", None)
        # the lane's own `plugins:` block, if it has one — without this the ceiling is the only list that ever
        # reaches load_all and a narrowed lane silently runs everything
        lane_cfg = None
        if profile_root is not None:
            try:
                from core import agentconfig as _agentconfig

                lane_cfg = {"plugins": (_agentconfig.read(profile_root) or {}).get("plugins")}
            except Exception:                      # noqa: BLE001 — an unreadable lane config is not fatal
                lane_cfg = None
        clone.plugin_report = plugins_mod.load_all(cfg_now, clone, profile_root, athena_root, lane_cfg=lane_cfg)
        return clone


def _lane_name(ctx) -> str:
    """The lane label a trace uses for a bound registry: the channel's name, else its profile folder."""
    name = str(getattr(ctx, "channel", "") or "")
    if name:
        return name
    root = getattr(ctx, "profile_root", None)
    return Path(root).name if root else "-"


def _scoped_path(raw: str, root: Path | None = None) -> Path | None:
    """The path if it is inside some agent's workspace, else None.

    Resolved first, so a symlink pointing out of the workspace is refused rather than followed.
    """
    try:
        candidate = Path(raw).expanduser().resolve()
        root = Path(root or WORKSPACE_ROOT).resolve()
    except (OSError, RuntimeError):
        return None
    if root not in candidate.parents:
        return None
    if "workspace" not in candidate.parts:
        return None
    return candidate


def default_registry(cfg: dict | None = None, workspace_root: Path | None = None, ctx=None) -> Registry:
    """The 0.0.1 built-ins. `workspace_root` exists so a test can exercise the scope rule against a
    temporary tree instead of the live profiles directory."""
    ws_root = Path(workspace_root) if workspace_root else WORKSPACE_ROOT
    reg = Registry(max_result_bytes=int(((cfg or {}).get("tools") or {}).get("max_result_bytes") or 8000))

    reg.register(Tool(
        name="echo",
        description="Return the given text unchanged; use it to prove a round trip.",
        parameters={"type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to return."}},
                    "required": ["text"]},
        fn=lambda text="": str(text),
    ))

    reg.register(Tool(
        name="add",
        description="Add two integers and return the sum.",
        parameters={"type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"]},
        fn=lambda a=0, b=0: int(a) + int(b),
    ))

    def read_file(path: str = "", max_chars: int = 8000) -> str:
        """Read a text file the agent was given. Refuses anything outside an agent workspace."""
        target = _scoped_path(path, ws_root)
        if target is None:
            return ("error: read_file may only open files under .athena/profiles/<agent>/workspace/ — "
                    "an attachment's path is shown in the turn that delivered it")
        if not target.is_file():
            return f"error: no such file: {path}"
        try:
            size = target.stat().st_size
        except OSError as exc:
            return f"error: cannot stat {path}: {exc}"
        if size > READ_CAP_BYTES:
            return f"error: {path} is {size} bytes; the cap is {READ_CAP_BYTES}"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"error: cannot read {path}: {exc}"
        limit = max(200, min(int(max_chars or 8000), 20000))
        if len(text) > limit:
            return text[:limit] + f"\n… truncated at {limit} characters of {len(text)}"
        return text

    reg.register(Tool(
        name="read_file",
        description="Read a text file inside the agent workspace, e.g. a file the user attached.",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string", "description": "Absolute path to the file."},
                                   "max_chars": {"type": "integer", "description": "Characters to return."}},
                    "required": ["path"]},
        fn=read_file,
    ))
    # the three built-ins are agent-independent; the filesystem set is bound to one agent's scope at call time
    reg.cfg = cfg or {}
    reg._builtins = [t for t in reg._tools.values()]
    if ctx is not None:
        return reg.bind(ctx)
    return reg
