"""Built-in MCP tool — Model Context Protocol, BOTH directions (one tool).

The Operator's 08-12 spec: the MCP door has two sides and the agent drives
both autonomously:

  INBOUND  — Athena IS an MCP provider (web/mcp.py, mounted at /mcp):
             other agents/clients connect to HER, list her tools, call
             them. The tool reports the inbound server state (URL +
             tool count) so the agent knows what she exposes.

  OUTBOUND — Athena connects OUT to other MCP servers (stdio or http):
             connect, list connected, call a remote tool, disconnect.
             A connected server's tools register as
             `mcp_<server>_<tool>` through the SAME registry/gate.

This tool wraps mcp/registry.py + mcp/client.py for the agent loop.
"""

import json


def _mcp(args: dict, timeout: float = 60.0) -> str:
    from mcp.registry import connect_mcp, disconnect_mcp
    from mcp.client import list_connected, call as mcp_call

    action = str(args.get("action", "")).strip()
    if not action:
        return json.dumps({"ok": False, "detail": "action required"},
                          ensure_ascii=False)

    if action == "connect":
        name = str(args.get("name", "")).strip()
        kind = str(args.get("kind", "http")).strip()
        target = str(args.get("target", "")).strip()
        if not name:
            return json.dumps({"ok": False, "detail": "name required"},
                              ensure_ascii=False)
        if kind == "stdio":
            command = args.get("command") or []
            if not command:
                return json.dumps({"ok": False,
                                   "detail": "command list required for stdio"},
                                  ensure_ascii=False)
            r = connect_mcp(name, "stdio", target, command=command,
                            env=args.get("env") or None)
        else:
            if not target:
                return json.dumps({"ok": False,
                                   "detail": "target URL required for http"},
                                  ensure_ascii=False)
            r = connect_mcp(name, "http", target,
                            api_key=str(args.get("api_key", "")))
        return json.dumps(r, ensure_ascii=False)

    if action == "disconnect":
        name = str(args.get("name", "")).strip()
        if not name:
            return json.dumps({"ok": False, "detail": "name required"},
                              ensure_ascii=False)
        return json.dumps(disconnect_mcp(name), ensure_ascii=False)

    if action == "list":
        connected = list_connected()
        return json.dumps({"ok": True, "servers": connected},
                          ensure_ascii=False)

    if action == "call":
        name = str(args.get("name", "")).strip()
        tool = str(args.get("tool", "")).strip()
        if not name or not tool:
            return json.dumps({"ok": False,
                               "detail": "name and tool required"},
                              ensure_ascii=False)
        r = mcp_call(name, tool, args.get("arguments") or {})
        return json.dumps(r, ensure_ascii=False)

    if action == "inbound":
        # Athena's own MCP provider (the inbound door).
        from core.config import ATHENA_ROOT, load_config
        cfg = load_config("")
        port = (cfg.get("web") or {}).get("port") or 51420
        return json.dumps({
            "ok": True,
            "server": "athena",
            "url": f"http://127.0.0.1:{port}/mcp",
            "provider_schema": f"http://127.0.0.1:{port}/mcp/v1/chat/completions",
            "capabilities": {"tools": True},
        }, ensure_ascii=False)

    return json.dumps({"ok": False, "detail": f"unknown action: {action}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="mcp",
        description="Model Context Protocol, both directions: connect "
                    "(http/stdio), disconnect, list, call a remote MCP "
                    "tool, or report the inbound /mcp server. Remote tools "
                    "register as mcp_<server>_<tool>.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["connect", "disconnect", "list", "call",
                                    "inbound"]},
                "name": {"type": "string", "description": "Server name"},
                "kind": {"type": "string", "enum": ["http", "stdio"]},
                "target": {"type": "string", "description": "URL for http"},
                "command": {"type": "array", "items": {"type": "string"},
                            "description": "Command list for stdio"},
                "env": {"type": "object",
                        "description": "Child env (e.g. CHROME_PATH)"},
                "api_key": {"type": "string"},
                "tool": {"type": "string", "description": "Remote tool name"},
                "arguments": {"type": "object"},
            },
            "required": ["action"],
        },
        fn=_mcp,
    ))
    return ["mcp"]
