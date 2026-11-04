"""spawn_subagent — hand a bounded, self-contained task to a worker and get a summary back.

The tool exists so an agent can *delegate* rather than inflate its own context. What it deliberately cannot do:

- spawn from inside a worker (`is_subagent` is set on the child's context, and the child's registry has this tool
  removed) — an unbounded agent tree is a fork bomb with extra steps;
- reach outside the worker's own folder — the child scope is re-rooted at `subagents/<slug>/`;
- spend without limit — turns and dollars are arguments, not hopes.
"""
from __future__ import annotations

from pathlib import Path

from core import subagents as subagent_core
from tools.registry import Tool, ToolError


def build(ctx) -> list[Tool]:
    """`ctx` is the calling agent's context: scope, cfg, roots, provider, and whether it is itself a worker."""

    def _child_registry(trace, scope):
        """The worker's toolset: the normal set, bound to the worker's narrow scope, minus this tool."""
        from tools import filesystem
        from tools import registry as registry_mod

        child = filesystem.Context(scope=scope, approvals=None)
        child.cfg = getattr(ctx, "cfg", {}) or {}
        child.profile_root = Path(trace.folder)
        child.athena_root = getattr(ctx, "athena_root", None)
        child.is_subagent = True
        child.provider = None
        registry = registry_mod.default_registry(child.cfg).bind(child)
        registry.discard("spawn_subagent")            # the child may not delegate further
        return registry

    def spawn_subagent(task: str, max_turns: int = 6) -> dict:
        if getattr(ctx, "is_subagent", False):
            raise ToolError("a worker cannot spawn workers — do the task yourself")
        provider = getattr(ctx, "provider", None)
        if provider is None:
            raise ToolError("no provider is bound to this agent, so a worker could not run")
        parent_root = getattr(ctx, "profile_root", None)
        if parent_root is None:
            raise ToolError("no profile root is bound to this agent")

        try:
            result = subagent_core.spawn(
                parent_scope=ctx.scope,
                parent_cfg=getattr(ctx, "cfg", {}) or {},
                parent_lane=Path(parent_root).name,
                parent_root=Path(parent_root),
                task=task,
                provider=provider,
                tools_factory=_child_registry,
                parent_agent_id=str(getattr(ctx, "agent_id", "") or ""),
                max_turns=max(1, min(int(max_turns or 6), 24)),
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

        return {"status": result.get("status"), "answer": (result.get("reply") or result.get("error") or "")[:4000],
                "turns": result.get("turns"), "tool_calls": result.get("tool_calls"),
                "cost_usd": result.get("cost_usd"), "trace": result.get("trace"),
                "artifact": result.get("artifact"),
                "note": "the full trace is in the artifact; read it only if the summary is not enough"}

    return [Tool(
        "spawn_subagent",
        "Delegate one bounded task to a worker with its own context and scope. Returns a summary and a trace path.",
        {"type": "object",
         "properties": {"task": {"type": "string", "description": "The whole task, self-contained."},
                        "max_turns": {"type": "integer", "description": "Model iterations allowed (default 6, max 24)."}},
         "required": ["task"]},
        spawn_subagent)]
