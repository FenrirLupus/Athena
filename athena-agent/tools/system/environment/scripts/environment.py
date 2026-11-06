"""environment — ask for this agent's environment to change, and see what is already waiting on the user.

The agent can prepare; it cannot install. `plugin_stage` covers plugins, and this covers the other shape: a tool pack
(TOOL.md + scripts) that needs to live under `.athena/tools/` and be named in `tools.packs`, or — where the user has
allowed it — operating-system packages for the box the agent lives in. The request goes on the same queue the UI
polls, so it arrives as the same card, with the same Install/Cancel decision.

One tool, two actions:

    request   write a request for a staged tool pack, or for box packages     needs the sandbox to be writable
    status    list the requests still waiting on the user                     read-only

Nothing here writes outside the sandbox. The install happens in the server, on the user's click.
"""
from __future__ import annotations

from pathlib import Path

from core import paths as paths_mod
from core import plugininstall
from core import trace
from tools.registry import Tool, ToolError


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile whose environment is being asked."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")

def _agent_of(ctx) -> str:
    """Who is asking: the lane's folder name."""
    scope = getattr(ctx, "scope", None)
    home = getattr(scope, "home", None)
    return getattr(home, "name", "") or ""


def _status(athena_root: Path) -> dict:
    """The requests still waiting on a click — read, never reconciled, so asking cannot answer anything."""
    try:
        rows = plugininstall._load_pending(athena_root)
    except Exception as exc:                          # noqa: BLE001 — an unreadable queue is a finding, not a crash
        raise ToolError(f"cannot read the install queue: {exc}") from exc
    pending = sorted(
        ({"id": rid, "name": row.get("name") or "", "kind": row.get("kind") or "plugin",
          "agent": row.get("agent") or "", "reason": row.get("reason") or "",
          "created_at": row.get("created_at") or 0}
         for rid, row in rows.items()),
        key=lambda row: row["created_at"])
    return {"pending": pending, "count": len(pending),
            "next": "a request is installed when the user answers its card; only they can install"}


def build(ctx=None) -> list[Tool]:
    def request(kind: str = "tool", name: str = "", source: str = "", reason: str = "") -> dict:
        athena_root = getattr(ctx, "athena_root", None)
        with trace.span("tool.environment.request", channel=_lane(ctx), kind=kind or "tool", pack=name or "-",
                        reason_chars=len(reason or "")) as sp:
            if athena_root is None:
                raise ToolError("no athena root is bound to this agent")

            kind = (kind or "tool").strip().lower()
            sandbox = Path(getattr(ctx, "profile_root", Path.cwd())) / "sandbox"
            try:
                sandbox = ctx.scope.check(str(sandbox), paths_mod.WRITE, tool="environment")
            except ValueError as exc:
                raise ToolError(f"the sandbox is not writable: {exc}") from exc

            if kind == "box":
                packages = name
                try:
                    queued = {**plugininstall.request_box_install(packages, Path(athena_root), reason=reason,
                                                                  agent=_agent_of(ctx)),
                              "next": "the user answers the card; a box change is not undone by uninstalling a plugin"}
                except Exception as exc:              # noqa: BLE001
                    raise ToolError(str(exc)) from exc
                sp.ok(kind="box", packages=len(str(name).split(",")))
                return queued
            if kind != "tool":
                raise ToolError("environment request supports kind='tool' (a staged TOOL.md pack) and kind='box' "
                                "(system packages, only where the user has enabled it)")
            if not str(name or "").strip():
                raise ToolError("environment request needs the pack's name (or `source` for an explicit path)")

            target = Path(source or (sandbox / "staging" / (name or ""))).expanduser()
            if not target.is_absolute():
                target = sandbox / target
            try:
                target = ctx.scope.check(str(target), paths_mod.READ, tool="environment")
            except ValueError as exc:
                raise ToolError(f"cannot read the staged pack: {exc}") from exc
            if not target.is_dir():
                raise ToolError(f"{target} is not a directory")

            try:
                queued = plugininstall.request_tool_install(target, Path(athena_root), reason=reason,
                                                            agent=_agent_of(ctx))
            except Exception as exc:                  # noqa: BLE001 — a bad stage is a message, not a traceback
                raise ToolError(str(exc)) from exc
            sp.ok(kind="tool", staged=str(target))
            return {**queued, "next": "the user answers the card; if they cancel, nothing is installed and the staged "
                                      "copy stays in your sandbox"}

    def status() -> dict:
        with trace.span("tool.environment.status", channel=_lane(ctx)) as sp:
            athena_root = getattr(ctx, "athena_root", None)
            if athena_root is None:
                raise ToolError("no athena root is bound to this agent")
            out = _status(Path(athena_root))
            sp.ok(pending=out.get("count", 0))
            return out

    return [
        Tool("request",
             "Ask the user to install a staged tool pack (or box packages) into this environment. Installs nothing.",
             {"type": "object",
              "properties": {"kind": {"type": "string", "enum": ["tool", "box"],
                                      "description": "'tool' is a staged TOOL.md pack, 'box' is operating-system "
                                                     "packages."},
                             "name": {"type": "string", "description": "Pack name, or for kind=box the package "
                                                                     "names (comma-separated)."},
                             "source": {"type": "string", "description": "Or an explicit path to the staged pack."},
                             "reason": {"type": "string", "description": "One line for the card."}},
              "required": ["name"]},
             request),
        Tool("status",
             "List the install requests still waiting on the user. Read-only.",
             {"type": "object", "properties": {}},
             status)]



def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "environment")
