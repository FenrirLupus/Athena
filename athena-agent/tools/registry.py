"""Tool registry — the minimal surface 0.0.1 needs to prove the iterative loop.

A Tool is a name, a description (the routing line the model sees), a JSON schema for its
arguments, and a callable. Registration refuses duplicates: a silent last-one-wins once deleted a
live safety guard (documentation/architecture/05-extensions.md).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

# Tools may read what an agent was given, and nothing else. Attachments land in
# `profiles/<channel>/workspace/`, so that is the one tree a file tool is allowed to open.
WORKSPACE_ROOT = Path(__import__("os").environ.get("ATHENA_ROOT") or (Path.home() / ".athena")) / "profiles"
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
        """Run a tool and ALWAYS return a string — a refusal is information for the model."""
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return f"error: unknown tool {name!r}; available: {', '.join(self.names()) or 'none'}"
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
            return (f"error: {name} did not finish within {timeout:.0f}s. The call was abandoned so this agent can "
                    f"keep working; it may still be running in the background.")
        if "err" in outcome:
            exc = outcome["err"]
            if isinstance(exc, TypeError):
                return f"error: bad arguments for {name}: {exc}"
            return f"error: {name} failed: {type(exc).__name__}: {exc}"
        out = outcome.get("out", "")
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        if len(text) > self.max_result_bytes:
            text = text[:self.max_result_bytes] + f"\n[truncated at {self.max_result_bytes} bytes]"
        return text


    def bind(self, ctx) -> "Registry":
        """A registry for one agent: the built-ins plus the filesystem tools, all checked against `ctx.scope`.

        A new object rather than a mutated one — the manager is shared by every channel, and a per-agent scope
        stored on the shared registry would be whichever agent happened to be built last.
        """
        from tools import filesystem

        clone = Registry(max_result_bytes=self.max_result_bytes)
        clone.cfg = getattr(self, "cfg", {})
        for tool in getattr(self, "_builtins", []):
            clone.register(tool)
        for tool in filesystem.build(ctx):
            clone.register(tool)

        # skills: which ones this agent can see depends on its profile, so they are bound here too
        from tools import skills as skill_tools

        profile_root = getattr(ctx, "profile_root", None)
        athena_root_ctx = getattr(ctx, "athena_root", None)
        enabled_plugins = [str(n) for n in (((getattr(ctx, "cfg", None) or {}).get("plugins") or {})
                                            .get("enabled") or [])]
        for tool in skill_tools.build(profile_root, athena_root_ctx, enabled_plugins):
            clone.register(tool)

        # environment requests: the agent asks for a tool pack; the user's click installs it
        from tools import envrequest as env_tools

        for tool in env_tools.build(ctx):
            clone.register(tool)

        # installation: the agent stages a plugin in its sandbox; a human runs the installer
        from tools import pluginstage as stage_tools

        for tool in stage_tools.build(ctx):
            clone.register(tool)

        # repository intake: clone into the sandbox, inventory it, run none of it
        from tools import repo as repo_tools

        for tool in repo_tools.build(ctx):
            clone.register(tool)

        # delegation: a bounded worker with its own folder and no ability to delegate further
        from tools import subagents as subagent_tools

        for tool in subagent_tools.build(ctx):
            clone.register(tool)

        # the watchdogs' findings, readable by the agent that has to act on them
        from tools import security as security_tools

        for tool in security_tools.build(athena_root_ctx):
            clone.register(tool)

        # references: the added information a skill, tool or plugin carries
        from tools import library as library_tools

        for tool in library_tools.build(profile_root, athena_root_ctx):
            clone.register(tool)

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

        # tool packs: user-authored verbs declared in .athena/tools/<name>/tool.yaml
        from core import toolpacks as packs_mod

        clone.pack_report = packs_mod.load_all(cfg_now, clone, profile_root, athena_root, ctx=ctx)
        return clone


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
