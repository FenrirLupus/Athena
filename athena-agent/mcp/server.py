"""MCP server — third-party agents connect to Athena, supply + gather info.

Model Context Protocol over stdio (the standard MCP transport): a client
agent spawns `athena mcp` and speaks JSON-RPC 2.0 lines on stdin/stdout.

Methods (Athena's surface for third-party agents):
    tools/list                    — what Athena can expose
    resources/list                — what info can be gathered
    tools/call  {name, arguments} — gather (memory, vault, session) or
                                    supply (memory_add, kanban_add, log)

SECURITY: this is a read/supply surface for third parties. It does NOT
expose terminal/fs tools — a connected agent can gather and contribute
information, never modify the system.
"""
from __future__ import annotations

import json
import sys

# The info methods a third-party agent may call (gather + supply).
TOOLS = [
    {
        "name": "memory_list",
        "description": "Gather: the persistent memory notes (both sides).",
        "inputSchema": {"type": "object", "properties": {"profile": {"type": "string"}}},
    },
    {
        "name": "vault_query",
        "description": "Gather: search the vault (FTS) for entries matching a query.",
        "inputSchema": {"type": "object",
                        "properties": {"query": {"type": "string"},
                                       "profile": {"type": "string"}},
                        "required": ["query"]},
    },
    {
        "name": "session_history",
        "description": "Gather: recent session history as JSONL.",
        "inputSchema": {"type": "object",
                        "properties": {"session_id": {"type": "string"},
                                       "limit": {"type": "integer"},
                                       "profile": {"type": "string"}}},
    },
    {
        "name": "memory_add",
        "description": "Supply: save a note (side='assistant' or 'user').",
        "inputSchema": {"type": "object",
                        "properties": {"side": {"type": "string"},
                                       "content": {"type": "string"},
                                       "profile": {"type": "string"}},
                        "required": ["side", "content"]},
    },
    {
        "name": "kanban_add",
        "description": "Supply: add a kanban task (assignee = a profile agent).",
        "inputSchema": {"type": "object",
                        "properties": {"title": {"type": "string"},
                                       "assignee": {"type": "string"},
                                       "priority": {"type": "integer"}},
                        "required": ["title"]},
    },
    {
        "name": "log_entry",
        "description": "Supply: write a metric log entry (level 1-5).",
        "inputSchema": {"type": "object",
                        "properties": {"level": {"type": "integer"},
                                       "result": {"type": "string"},
                                       "tool": {"type": "string"},
                                       "profile": {"type": "string"}},
                        "required": ["level", "result"]},
    },
]

RESOURCES = [
    {"uri": "memory://assistant", "name": "Assistant memory"},
    {"uri": "memory://user", "name": "User memory"},
    {"uri": "vault://", "name": "Vault archive"},
    {"uri": "sessions://", "name": "Session history"},
]


def _call_tool(name: str, arguments: dict) -> dict:
    """Execute a third-party tool call (gather or supply)."""
    profile = str(arguments.get("profile", ""))
    if name == "memory_list":
        from intelligence.memory import read_all
        mem = read_all(profile)
        return {"content": [{"type": "text",
                             "text": json.dumps(mem, ensure_ascii=False)}]}
    if name == "vault_query":
        from context import retrieval
        query = str(arguments.get("query", ""))
        if not query:
            return {"content": [{"type": "text", "text": "query required"}]}
        r = retrieval.retrieve(query, profile=profile)
        text = "\n".join(
            f"- {x.get('content', '')[:200]}" for x in r.get("vault", [])[:5]
        ) or "(no vault matches)"
        return {"content": [{"type": "text", "text": text}]}
    if name == "session_history":
        from core import db as db_layer
        sid = arguments.get("session_id") or db_layer.find_last_session(profile=profile)
        if not sid:
            return {"content": [{"type": "text", "text": "no session"}]}
        limit = int(arguments.get("limit", 20))
        return {"content": [{"type": "text",
                             "text": db_layer.export_session_jsonl(sid, limit, profile)}]}
    if name == "memory_add":
        from intelligence.memory import add_entry
        path = add_entry(str(arguments.get("side", "assistant")),
                         str(arguments.get("content", "")), profile=profile)
        return {"content": [{"type": "text", "text": f"noted: {path}"}]}
    if name == "kanban_add":
        from autonomy.kanban import add_task
        task = add_task(str(arguments.get("title", "")),
                        assignee=str(arguments.get("assignee", "")),
                        priority=int(arguments.get("priority", 0)),
                        created_by="mcp")
        return {"content": [{"type": "text", "text": f"added {task['id'][:8]}"}]}
    if name == "log_entry":
        from metrics.logger import log
        path = log(int(arguments.get("level", 1)),
                   str(arguments.get("result", "")),
                   tool=str(arguments.get("tool", "mcp")),
                   profile=profile)
        return {"content": [{"type": "text", "text": f"logged: {path}"}]}
    return {"content": [{"type": "text", "text": f"unknown tool: {name}"}]}


def _handle(request: dict) -> dict:
    """Handle one JSON-RPC request."""
    req_id = request.get("id")
    method = request.get("method", "")

    if method == "initialize":
        try:
            from core.config import VERSION as _ATHENA_VERSION
        except Exception:
            _ATHENA_VERSION = "0.1.0"
        return {"jsonrpc": "2.0", "id": req_id,
                "result": {"protocolVersion": "2024-11-05",
                           "capabilities": {"tools": {}, "resources": {}},
                           "serverInfo": {"name": "athena", "version": _ATHENA_VERSION}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": RESOURCES}}
    if method == "tools/call":
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {}) or {}
        try:
            result = _call_tool(name, arguments)
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": result["content"], "isError": False}}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": f"error: {exc}"}],
                               "isError": True}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve() -> int:
    """The MCP loop: read JSON-RPC lines on stdin, answer on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(request, dict):
            continue
        response = _handle(request)
        print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0
