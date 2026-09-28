"""MCP client — Athena connects OUT to other MCP servers (the Operator's spec).

Her /mcp door lets OTHER agents talk to HER; this client lets HER talk
to THEM. Two transports:

  • stdio: spawn a server command, speak JSON-RPC 2.0 lines on its
    stdin/stdout (the standard MCP transport for CLI servers).
  • http:  POST JSON-RPC to an MCP HTTP endpoint (e.g. another agent's
    /mcp door — including Athena's own self-provider door).

A connected MCP server's tools become Athena's tools (the registry layer
x2): tools/call routes to the remote server through the SAME permission
gate as local tools — the interactive layer decides, never the remote.
"""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.request

# The connected MCP servers: name -> {kind, target, tools, proc}
_CONNECTED: dict[str, dict] = {}
_LOCK = threading.Lock()


def connect_stdio(name: str, command: list[str], env: dict | None = None) -> dict:
    """Spawn an MCP server over stdio and handshake (initialize).

    env: optional extra environment for the child (e.g. CHROME_PATH
    for the Chrome DevTools MCP server — the Operator's 08-12 browser
    integration). Merged over os.environ.
    """
    import os as _os
    child_env = dict(_os.environ)
    if env:
        child_env.update(env)
    with _LOCK:
        if name in _CONNECTED:
            return {"ok": False, "detail": f"already connected: {name}"}
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=child_env,
            )
            tools = _stdio_handshake(proc)
            _CONNECTED[name] = {"kind": "stdio", "target": command[0],
                                "tools": tools, "proc": proc}
            return {"ok": True, "name": name, "tools": tools,
                    "detail": f"stdio server '{name}' connected"}
        except Exception as exc:
            return {"ok": False, "detail": f"connect failed: {exc}"}


def connect_http(name: str, base_url: str, api_key: str = "") -> dict:
    """Connect to an HTTP MCP endpoint (JSON-RPC POST)."""
    with _LOCK:
        if name in _CONNECTED:
            return {"ok": False, "detail": f"already connected: {name}"}
        try:
            tools = _http_handshake(base_url, api_key)
            _CONNECTED[name] = {"kind": "http", "target": base_url,
                                "tools": tools, "api_key": api_key}
            return {"ok": True, "name": name, "tools": tools,
                    "detail": f"http MCP '{name}' connected at {base_url}"}
        except Exception as exc:
            return {"ok": False, "detail": f"connect failed: {exc}"}


def list_connected() -> list[dict]:
    """The connected MCP servers: {name, kind, target, tool_count}."""
    with _LOCK:
        return [{"name": n, "kind": v["kind"], "target": v["target"],
                 "tool_count": len(v.get("tools", []))}
                for n, v in sorted(_CONNECTED.items())]


def disconnect(name: str) -> dict:
    """Drop an MCP connection (kills a stdio server process)."""
    with _LOCK:
        entry = _CONNECTED.pop(name, None)
        if entry is None:
            return {"ok": False, "detail": f"not connected: {name}"}
        if entry.get("kind") == "stdio" and entry.get("proc") is not None:
            try:
                entry["proc"].terminate()
            except Exception:
                pass
        return {"ok": True, "detail": f"disconnected: {name}"}


def call(name: str, tool: str, arguments: dict | None = None) -> dict:
    """Call a tool on a connected MCP server. Returns the result."""
    with _LOCK:
        entry = _CONNECTED.get(name)
    if entry is None:
        return {"ok": False, "detail": f"not connected: {name}"}
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}},
    }
    try:
        if entry["kind"] == "stdio":
            response = _stdio_rpc(entry["proc"], request)
        else:
            response = _http_rpc(entry["target"], request,
                                 entry.get("api_key", ""))
        if "error" in response:
            return {"ok": False, "detail": str(response["error"])}
        result = response.get("result", {})
        text = "\n".join(
            c.get("text", "") for c in result.get("content", [])
            if isinstance(c, dict))
        return {"ok": True, "result": text or str(result)}
    except Exception as exc:
        return {"ok": False, "detail": f"call failed: {exc}"}


# -- Transport helpers ---------------------------------------------------

def _stdio_handshake(proc) -> list[dict]:
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {}, "clientInfo": {"name": "athena"}}}
    _stdio_rpc(proc, init)
    resp = _stdio_rpc(proc, {"jsonrpc": "2.0", "id": 1,
                             "method": "tools/list"})
    return resp.get("result", {}).get("tools", []) or []


def _http_handshake(base_url: str, api_key: str = "") -> list[dict]:
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05",
                       "capabilities": {}, "clientInfo": {"name": "athena"}}}
    _http_rpc(base_url, init, api_key)
    resp = _http_rpc(base_url, {"jsonrpc": "2.0", "id": 1,
                                "method": "tools/list"}, api_key)
    return resp.get("result", {}).get("tools", []) or []


def _stdio_rpc(proc, request: dict) -> dict:
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("stdio server has no pipes")
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("stdio server closed")
    return json.loads(line)


def _http_rpc(base_url: str, request: dict, api_key: str = "") -> dict:
    url = base_url.rstrip("/")
    if not url.endswith("/initialize") and request["method"] == "initialize":
        url = f"{url}/initialize"
    elif not url.endswith("/tools/list") and request["method"] == "tools/list":
        url = f"{url}/tools/list"
    elif not url.endswith("/tools/call") and request["method"] == "tools/call":
        url = f"{url}/tools/call"
    payload = json.dumps(request).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "Athena/0.1"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))
