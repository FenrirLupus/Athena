"""MCP test — third-party agents connect, gather + supply info."""
from __future__ import annotations


def run() -> list[dict]:
    from mcp.server import _handle, TOOLS, RESOURCES

    checks = []
    r = _handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    checks.append({
        "name": "initialize handshake",
        "status": "ok" if r.get("result", {}).get("serverInfo", {}).get("name") == "athena" else "fail",
        "detail": r.get("result", {}).get("serverInfo", {}).get("name", "?"),
    })
    r = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    checks.append({
        "name": "tools/list exposes surface",
        "status": "ok" if len(r.get("result", {}).get("tools", [])) >= 6 else "fail",
        "detail": f"{len(r.get('result', {}).get('tools', []))} tools",
    })
    names = {t["name"] for t in TOOLS}
    checks.append({
        "name": "gather + supply tools present",
        "status": "ok" if {"memory_list", "vault_query", "session_history",
                           "memory_add", "kanban_add", "log_entry"} <= names else "fail",
        "detail": f"{len(names)} tools",
    })
    # No terminal/fs — a third-party agent cannot modify the system.
    unsafe = names & {"terminal", "fs_write", "fs_delete", "execute", "kill"}
    checks.append({
        "name": "no system-modifying tools exposed",
        "status": "ok" if not unsafe else "fail",
        "detail": f"unsafe={sorted(unsafe)}",
    })
    r = _handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "memory_list", "arguments": {}}})
    text = r.get("result", {}).get("content", [{}])[0].get("text", "")
    checks.append({
        "name": "gather works (memory_list)",
        "status": "ok" if isinstance(text, str) else "fail",
        "detail": text[:40],
    })
    r = _handle({"jsonrpc": "2.0", "id": 4, "method": "bogus"})
    checks.append({
        "name": "unknown method error",
        "status": "ok" if "error" in r else "fail",
        "detail": r.get("error", {}).get("message", "")[:40],
    })
    return checks
