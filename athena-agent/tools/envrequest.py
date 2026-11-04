"""env_request — ask the server to install a tool pack into the box the agent lives in.

The agent can prepare; it cannot install. `plugin_stage` covers plugins, and this covers the other shape: a staged tool
pack (TOOL.md + scripts) that needs to live under `.athena/tools/` and be named in `tools.packs`. The request goes on
the same queue the UI polls, so it arrives as the same card, with the same Install/Cancel decision.

Nothing here writes outside the sandbox. The install happens in the server, on the user's click.
"""
from __future__ import annotations

from pathlib import Path

from core import paths as paths_mod
from core import plugininstall
from tools.registry import Tool, ToolError


def _agent_of(ctx) -> str:
    """Who is asking: the lane's folder name."""
    scope = getattr(ctx, "scope", None)
    home = getattr(scope, "home", None)
    return getattr(home, "name", "") or ""


def build(ctx) -> list[Tool]:
    def env_request(kind: str = "tool", name: str = "", source: str = "", reason: str = "") -> dict:
        kind = (kind or "tool").strip().lower()
        athena_root = getattr(ctx, "athena_root", None)
        if athena_root is None:
            raise ToolError("no athena root is bound to this agent")
        sandbox = Path(getattr(ctx, "profile_root", Path.cwd())) / "sandbox"
        try:
            sandbox = ctx.scope.check(str(sandbox), paths_mod.WRITE, tool="env_request")
        except ValueError as exc:
            raise ToolError(f"the sandbox is not writable: {exc}") from exc

        if kind == "box":
            packages = name
            try:
                return {**plugininstall.request_box_install(packages, Path(athena_root), reason=reason,
                                                            agent=_agent_of(ctx)),
                        "next": "the user answers the card; a box change is not undone by uninstalling a plugin"}
            except Exception as exc:                  # noqa: BLE001
                raise ToolError(str(exc)) from exc
        if kind != "tool":
            raise ToolError("env_request supports kind='tool' (a staged TOOL.md pack) and kind='box' "
                            "(system packages, only where the user has enabled it)")

        target = Path(source or (sandbox / "staging" / (name or ""))).expanduser()
        if not target.is_absolute():
            target = sandbox / target
        try:
            target = ctx.scope.check(str(target), paths_mod.READ, tool="env_request")
        except ValueError as exc:
            raise ToolError(f"cannot read the staged pack: {exc}") from exc
        if not target.is_dir():
            raise ToolError(f"{target} is not a directory")

        try:
            request = plugininstall.request_tool_install(target, Path(athena_root), reason=reason,
                                                         agent=_agent_of(ctx))
        except Exception as exc:                      # noqa: BLE001 — a bad stage is a message, not a traceback
            raise ToolError(str(exc)) from exc
        return {**request, "next": "the user answers the card; if they cancel, nothing is installed and the staged "
                                   "copy stays in your sandbox"}

    return [Tool(
        "env_request",
        "Ask the user to install a staged tool pack into the box (TOOL.md + scripts). Does not install anything.",
        {"type": "object",
         "properties": {"kind": {"type": "string", "enum": ["tool", "box"],
                                 "description": "Only 'tool' today: a staged TOOL.md pack."},
                        "name": {"type": "string", "description": "Pack name, or for kind=box the package names "
                                                                "(comma-separated)."},
                        "source": {"type": "string", "description": "Or an explicit path to the staged pack."},
                        "reason": {"type": "string", "description": "One line for the card."}},
         "required": ["name"]},
        env_request)]
