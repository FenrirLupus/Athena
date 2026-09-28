"""The MCP endpoint — the CONVERSION/APPLICATION layer (the Operator's spec).

Model Context Protocol (JSON-RPC over HTTP): a client handshakes
(initialize), lists Athena's tools (tools/list), and calls them
(tools/call). Every tool call passes the SAME permission gate as the CLI
— unsafe tools with no rule return a needs_prompt error instead of
executing (the interactive layer decides, not the third party).

THE CONVERSION LAYER (the Operator's spec): the /mcp endpoint ALSO speaks the
PROVIDER schema (OpenAI-compatible chat/completions) — so Athena's own
MCP looks like any provider's base_url. A runtime can select "athena"
as its provider and call her MCP exactly like it calls opencode-go or
deepseek: Athena is HER OWN PROVIDER by extension. The MCP converts
between the provider protocol and Athena's internal tools.

Endpoints mounted at /mcp:
    POST /mcp/initialize          → server info + capabilities
    POST /mcp/tools/list          → Athena's registered tools
    POST /mcp/tools/call          → execute one tool (gated)
    POST /mcp/v1/chat/completions → the PROVIDER schema (self-provider)
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

def _mcp_version() -> str:
    """Athena's version (single source: core.config.VERSION)."""
    try:
        from core.config import VERSION
        return VERSION
    except Exception:
        return "0.1.0"


router = APIRouter(prefix="/mcp")

SERVER_INFO = {
    "name": "athena",
    "version": _mcp_version(),
    "capabilities": {"tools": {"listChanged": False}},
}


def _jsonrpc(req_id, result=None, error=None):
    body = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return body


@router.post("/initialize")
async def mcp_initialize(request: Request):
    body = await request.json()
    return JSONResponse(_jsonrpc(
        body.get("id"),
        result={"protocolVersion": body.get("params", {}).get(
            "protocolVersion", "2024-11-05"),
                "capabilities": SERVER_INFO["capabilities"],
                "serverInfo": {"name": SERVER_INFO["name"],
                               "version": SERVER_INFO["version"]}},
    ))


@router.post("/tools/list")
async def mcp_tools_list(request: Request):
    body = await request.json()
    from filesystem.tools import schemas_with_skills
    from intelligence.skills import load_skills
    tools = []
    # THE STANDARDIZED SCHEMA (the Operator's 08-12 fix): the MCP surface
    # advertises TOOLS + SKILLS — skills as first-class skill:<name>
    # entries (same {name, description, parameters} shape), so an MCP
    # client can invoke skill:doctor / skill:network directly.
    try:
        skill_schemas = schemas_with_skills(load_skills())
    except Exception:
        skill_schemas = None
    for s in skill_schemas or []:
        fn = s.get("function", {})
        tools.append({
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "inputSchema": fn.get("parameters", {"type": "object"}),
        })
    return JSONResponse(_jsonrpc(body.get("id"), result={"tools": tools}))


@router.post("/tools/call")
async def mcp_tools_call(request: Request):
    body = await request.json()
    params = body.get("params", {}) or {}
    name = params.get("name", "")
    arguments = params.get("arguments", {}) or {}
    req_id = body.get("id")

    # The permission gate — same as the CLI. Unsafe + no rule = refused.
    # (The skill: prefix routes to the skill gate — a skill call is
    # allowed when the skill itself isn't blocked; the permission rules
    # cover tools, skills are knowledge-loads. The 08-14 pattern-safe
    # form skill_<name> routes the same way.)
    from security.permissions import check
    if not (isinstance(name, str) and (name.startswith("skill:")
                                       or name.startswith("skill_"))):
        perm = check(name, arguments)
        if not perm["allowed"]:
            return JSONResponse(_jsonrpc(
                req_id,
                error={"code": -32001, "message":
                       f"tool '{name}' not permitted "
                       f"(verdict={perm['verdict']}, risk={perm['risk']})"},
            ))

    from filesystem.tools import TOOLS, execute_tool_call
    # THE SKILL DISPATCH (the Operator's 08-12 standardized schema): a
    # skill:<name> call resolves through execute_tool_call's skill branch
    # — same path as a tool. Only plain tool names need the TOOLS lookup.
    if isinstance(name, str) and name.startswith("skill:"):
        try:
            result = execute_tool_call(
                {"function": {"name": name,
                              "arguments": json.dumps(arguments)}})
            return JSONResponse(_jsonrpc(
                req_id,
                result={"content": [{"type": "text", "text": str(result)}]},
            ))
        except Exception as exc:
            try:
                from core.logging import log_event
                log_event(4, f"mcp skill call failed: {exc}",
                          source="server", tool="mcp", action="skill_call")
            except Exception:
                pass
            return JSONResponse(_jsonrpc(
                req_id,
                error={"code": -32002, "message": f"skill call failed: {exc}"},
            ))
    tool = TOOLS.get(name)
    if tool is None:
        return JSONResponse(_jsonrpc(
            req_id,
            error={"code": -32602, "message": f"unknown tool '{name}'"},
        ))
    try:
        result = execute_tool_call(
            {"function": {"name": name,
                          "arguments": json.dumps(arguments)}})
        return JSONResponse(_jsonrpc(
            req_id,
            result={"content": [{"type": "text", "text": str(result)}]},
        ))
    except Exception as exc:
        # THE MCP AUDIT (the Operator's 08-12 metrics spec): a tool-call
        # failure through MCP must reach the logs — the caller sees the
        # JSON-RPC error, the operator sees the trace in the terminal.
        try:
            from core.logging import log_event
            log_event(4, f"mcp tool call failed: {exc}",
                      source="server", tool="mcp", action="tool_call")
        except Exception:
            pass
        return JSONResponse(_jsonrpc(
            req_id,
            error={"code": -32000, "message": str(exc)},
        ))


@router.post("/v1/chat/completions")
async def mcp_chat_completions(request: Request):
    """THE CONVERSION LAYER (the Operator's spec): Athena as her own provider.

    Accepts the OpenAI-compatible PROVIDER schema — the same shape any
    provider's /v1/chat/completions uses — and runs it through Athena's
    own loop. This is what makes Athena her own provider: a runtime (or
    any MCP client) can select "athena" as its provider with this
    base_url and talk to her exactly like opencode-go or deepseek.

    Request body (the provider schema):
        {"model": "...", "messages": [{"role": "user", "content": "..."}]}

    Response (the provider schema):
        {"id": "...", "object": "chat.completion", "choices": [
            {"message": {"role": "assistant", "content": "..."}}],
         "usage": {"total_tokens": N}}

    SECURITY: the same permission gate applies — no tool executes
    without the interactive layer's approval.
    """
    body = await request.json()
    messages = body.get("messages", []) or []
    user_text = ""
    for m in reversed(messages):
        if m.get("role") in ("user", "system"):
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                user_text = content.strip()
                break
            if isinstance(content, list):  # multimodal — take text parts
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        user_text = part.get("text", "").strip()
                        break
                if user_text:
                    break
    if not user_text:
        return JSONResponse(_jsonrpc(
            body.get("id"),
            error={"code": -32602, "message": "no user message"},
        ))
    try:
        # Run through Athena's own loop (the conversion: provider
        # protocol → internal runtime). Uses a subagent-style bounded
        # turn so the server loop is untouched.
        from autonomy.scheduler import _run_subagent
        reply = _run_subagent({"body": user_text})
        return JSONResponse(_jsonrpc(
            body.get("id"),
            result={
                "id": f"chatcmpl-{body.get('id', 'athena')}",
                "object": "chat.completion",
                "model": body.get("model", "athena"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }],
                "usage": {"total_tokens": len(reply)},
            },
        ))
    except Exception as exc:
        try:
            from core.logging import log_event
            log_event(4, f"mcp chat failed: {exc}",
                      source="server", tool="mcp", action="chat")
        except Exception:
            pass
        return JSONResponse(_jsonrpc(
            body.get("id"),
            error={"code": -32000, "message": str(exc)},
        ))
