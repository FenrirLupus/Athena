---
name: mcp
description: "Use the built-in mcp tool to manage MCP connections — inbound (/mcp) and outbound (connect/call/disconnect)."
---

# MCP

The built-in `mcp` tool manages Model Context Protocol connections in
BOTH directions:

**Inbound** — Athena is an MCP provider at `/mcp` (other agents connect
to her):
```
mcp {"action": "inbound"}
```

**Outbound** — Athena connects to other MCP servers:
```
mcp {"action": "connect", "name": "notes", "kind": "http", "target": "http://localhost:9000/mcp"}
mcp {"action": "list"}
mcp {"action": "call", "name": "notes", "tool": "create_note", "arguments": {"title": "hi"}}
mcp {"action": "disconnect", "name": "notes"}
```

Connected tools register as `mcp_<server>_<tool>` — use them like any
native tool. Use when the operator wants Athena to talk to another MCP
server or expose her capabilities.

**Requirements:** `inbound` is keyless. http connects need the remote
server's `api_key` when it enforces one; stdio needs the command on
PATH.

---
---
