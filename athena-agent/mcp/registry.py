"""MCP registry — connected MCP servers become Athena's tools (x2).

When Athena connects to another MCP server, its tools are registered
into the SAME tool registry as local tools, namespaced as
`mcp_<server>_<tool>` (the Operator's spec: MCP is the application layer —
her tools and the remote's tools are one surface, gated identically).

  • connect   → handshake + register the remote's tools
  • call      → routes through the permission gate (a remote tool is
                just a tool; the interactive layer decides)
  • disconnect → unregister the namespace
"""
from __future__ import annotations

from filesystem.tools import Tool, register, TOOLS, schemas


def _ns(server: str, tool: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in server.lower())
    return f"mcp_{safe}_{tool}"


def _sync_registry(server: str, tools: list[dict]) -> int:
    """Register (or refresh) the remote's tools under the namespace."""
    count = 0
    for t in tools or []:
        name = t.get("name", "")
        if not name:
            continue
        fn_name = _ns(server, name)
        if fn_name in TOOLS:
            continue
        register(Tool(
            name=fn_name,
            description=f"[mcp:{server}] {t.get('description', name)}",
            parameters=t.get("inputSchema", {"type": "object"}),
            fn=_make_remote(server, name),
        ))
        count += 1
    return count


def _make_remote(server: str, tool: str):
    """A wrapper that routes a call to the remote MCP server."""
    def remote(arguments: dict, timeout: float = 60.0) -> str:
        from mcp.client import call
        r = call(server, tool, arguments)
        return r.get("result") if r.get("ok") else f"error: {r.get('detail')}"
    return remote


def connect_mcp(name: str, kind: str, target: str, api_key: str = "",
                command: list[str] | None = None, env: dict | None = None) -> dict:
    """Connect to an MCP server (stdio or http) and register its tools.

    env: optional child environment (e.g. CHROME_PATH for the Chrome
    DevTools MCP server — the Operator's 08-12 browser integration).
    """
    from mcp.client import connect_stdio, connect_http
    if kind == "stdio" and command:
        r = connect_stdio(name, command, env=env)
    else:
        r = connect_http(name, target, api_key)
    if not r.get("ok"):
        return r
    added = _sync_registry(name, r.get("tools", []))
    r["tools_registered"] = added
    return r


def disconnect_mcp(name: str) -> dict:
    """Disconnect + unregister the namespace."""
    from mcp.client import disconnect
    r = disconnect(name)
    if r.get("ok"):
        prefix = _ns(name, "")  # "mcp_<server>_"
        for tname in [t for t in TOOLS if t.startswith(prefix)]:
            TOOLS.pop(tname, None)
        r["tools_removed"] = True
    return r
