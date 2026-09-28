---
name: mcp
description: "Model Context Protocol — inbound (/mcp) and outbound (connect/call/disconnect) connections."
---

# MCP

The **mcp** tool gives the agent full control over Model Context
Protocol connections — BOTH directions (the Operator's 08-12 spec):

- **INBOUND** — Athena IS an MCP provider: `web/mcp.py` mounts the
  server at `/mcp` (initialize, tools/list, tools/call, plus the
  OpenAI-compatible provider schema). Other agents/clients connect to
  HER, list her tools, call them. The tool's `inbound` action reports
  this server's URL + capabilities.

- **OUTBOUND** — Athena connects OUT to other MCP servers (stdio or
  http), lists their tools, calls them, disconnects. A connected
  server's tools register as `mcp_<server>_<tool>` through the SAME
  registry/gate as native tools.

## Usage

```
mcp {"action": "inbound"}                                   # her own door
mcp {"action": "connect", "name": "notes", "kind": "http", "target": "http://localhost:9000/mcp"}
mcp {"action": "list"}                                      # connected servers
mcp {"action": "call", "name": "notes", "tool": "create_note", "arguments": {"title": "hi"}}
mcp {"action": "disconnect", "name": "notes"}
mcp {"action": "connect", "name": "srv", "kind": "stdio", "command": ["python", "server.py"]}
```

## When to use

- The operator wants Athena to talk to another MCP server.
- Another agent connects to Athena's /mcp door.
- The agent needs a remote capability exposed as a local tool.

## Requirements (credentials)

- **`inbound`** — keyless (Athena's own /mcp door, always available).
- **`connect` http** — REQUIRES the remote server's `api_key` (or its
  own auth) when the remote enforces one. stdio servers need no key
  but must be launchable (e.g. `npx` present).
- **`connect` stdio** — no key, but the command must exist on PATH.
- Check first: `mcp {"action": "list"}` shows current connections.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/mcp.py` — registers `mcp`.

## Backend

- `mcp/client.py` — the client (stdio/http handshake + RPC)
- `mcp/registry.py` — connect/disconnect + namespace registration
- `web/mcp.py` — the inbound server at /mcp

---
---
