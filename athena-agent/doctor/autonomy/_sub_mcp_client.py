"""MCP client test — Athena connects OUT to other MCP servers (x1-x3).

the Operator's spec: her /mcp door lets other agents talk to HER; the client
lets HER talk to THEM. A connected server's tools become her tools
(namespaced mcp_<server>_<tool>) through the SAME executor/gate.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_SERVER_SRC = '''#!/usr/bin/env python3
import json, sys
TOOLS = [
    {"name": "echo", "description": "Echo text.",
     "inputSchema": {"type": "object",
                     "properties": {"text": {"type": "string"}}}},
]
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    m = req.get("method", "")
    if m == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"),
                          "result": {"protocolVersion": "2024-11-05",
                                     "capabilities": {"tools": {}},
                                     "serverInfo": {"name": "test", "version": "1"}}}), flush=True)
    elif m == "tools/list":
        print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"),
                          "result": {"tools": TOOLS}}), flush=True)
    elif m == "tools/call":
        p = req.get("params", {})
        a = p.get("arguments", {}) or {}
        text = f"echo: {a.get('text', '')}"
        print(json.dumps({"jsonrpc": "2.0", "id": req.get("id"),
                          "result": {"content": [{"type": "text", "text": text}]}}), flush=True)
'''


def run() -> list[dict]:
    checks = []
    with tempfile.TemporaryDirectory() as td:
        server_py = Path(td) / "server.py"
        server_py.write_text(_SERVER_SRC, encoding="utf-8")

        from mcp.registry import connect_mcp, disconnect_mcp
        from mcp.client import list_connected, call as mcp_call
        from filesystem.tools import TOOLS, execute_tool_call

        # 1. Connect + register.
        r = connect_mcp("test-srv", "stdio", "python3",
                        command=["python3", str(server_py)])
        checks.append({
            "name": "mcp client: connect + register tools",
            "status": "ok" if r.get("ok") and r.get("tools_registered") == 1 else "fail",
            "detail": f"registered={r.get('tools_registered')}",
        })

        # 2. Namespaced in the shared registry.
        ns = [t for t in TOOLS if t.startswith("mcp_test_srv")]
        checks.append({
            "name": "mcp tools namespaced (mcp_<server>_<tool>)",
            "status": "ok" if ns == ["mcp_test_srv_echo"] else "fail",
            "detail": str(ns),
        })

        # 3. Call through the SHARED executor (the gated path).
        out = execute_tool_call({"function": {
            "name": "mcp_test_srv_echo", "arguments": '{"text": "hi"}'}})
        checks.append({
            "name": "mcp remote call via shared executor",
            "status": "ok" if out.strip() == "echo: hi" else "fail",
            "detail": out.strip(),
        })

        # 3b. SYSTEM HANDS-OFF (the Operator's spec): a system-channel event gets
        #     NO approval surface (the wrapper is hands-off — unsafe tools
        #     fail closed silently); user/assistant events keep it.
        import core.conversation_loop as cl
        from unittest.mock import patch as _patch
        from intelligence.profiles import default_profile
        from unittest.mock import MagicMock
        # SESSION HYGIENE (the Operator's 08-12 spec): this test's
        # _process_event persists to session "s1" — redirect the sessions
        # dir to a tempdir so the REAL profile's sessions/ never gets a
        # non-UUID session-s1.db file. Real sessions are ALWAYS
        # session-{UUID}.db.
        import core.db as dbmod
        from pathlib import Path as _Path
        built = {}
        _orig_sessions = dbmod.sessions_dir
        _td = tempfile.TemporaryDirectory()
        _td_path = _Path(_td.name)
        dbmod.sessions_dir = staticmethod(
            lambda *a, **k: _td_path / "sessions")
        (_td_path / "sessions").mkdir(parents=True, exist_ok=True)

        def fake_init(self, *a, **kw):
            built["on_approval"] = kw.get("on_approval")
            built["channel"] = getattr(kw.get("channel"), "name", "?")

        class _FakeResult:
            """A minimal TurnResult so the REAL turn never executes.

            This test verifies the APPROVAL capture (system hands-off vs
            user approval) — it must not run a real turn. Without the
            stub, the real run_turn executes against a __new__-built
            loop that skipped __init__ (max_iterations etc. missing),
            logging an L4 error — which the nurse then sees and tries to
            "repair", looping forever. The stub keeps the test's intent
            and silences the leak.
            """
            reply = "ok"
            tool_transcript = []
            exit_reason = "completed"
            reasoning = None
            usage = {}
            api_calls = 0
            tool_calls_made = 0
            updated_history = []
            raw = None

        with _patch.object(cl.MessageLoop, "__init__", fake_init), \
             _patch.object(cl.MessageLoop, "run_turn",
                           lambda self, *a, **kw: _FakeResult()):
            loop = cl.ConversationLoop.__new__(cl.ConversationLoop)
            loop.profile = default_profile()
            loop.cfg = {"message_loop": {}, "compression": {},
                        "retrieval": {"enabled": False}}
            loop.providers = MagicMock()
            loop.on_event = None
            loop.on_approval = lambda *a: ("allow", "once")
            loop.responses = []
            loop.session_id = "s1"
            loop.all_skills = []
            loop._pending = MagicMock()
            loop._thoughts = []
            built.clear()
            loop._process_event({"id": "e1", "ts": 0, "session_id": "s1",
                                 "channel": "system", "content": "run the sweep"})
            sys_approval = built.get("on_approval")
            built.clear()
            loop._process_event({"id": "e2", "ts": 0, "session_id": "s1",
                                 "channel": "user", "content": "check weather"})
            user_approval = built.get("on_approval")
            checks.append({
                "name": "system hands-off: no approval for system channel",
                "status": "ok" if sys_approval is None
                and user_approval is not None else "fail",
                "detail": f"system={sys_approval} user={user_approval is not None}",
            })

            # 3c. THE BOUNDS RULE (the Operator's spec): in-platform work (.athena)
            #     is approved without a prompt; only OUT-OF-BOUNDS access
            #     (outside .athena, sanctum writes, network) prompts.
            # THE CLEAN-STORE FIX (the 08-15 fix): the operator's LIVE
            # grants (a real permissions.yaml with terminal allowed) would
            # make an out-of-bounds terminal call allowed — the test must
            # assert the ENGINE on a CLEAN store, not the operator's state.
            import security.permissions as _perm
            import tempfile as _tf
            from pathlib import Path as _P
            _orig_rp = _perm._rules_path
            _perm._rules_path = staticmethod(
                lambda profile="": _P(_tf.gettempdir()) / "doctor_perm" / "permissions.yaml")
            try:
                from security.permissions import check as perm_check
                _H = str(Path.home() / '.athena')
                in1 = perm_check("write", {"path": f"{_H}/workspace/x.txt"})
                in2 = perm_check("terminal", {"command": f"ls {_H}/workspace"})
                out1 = perm_check("write", {"path": "/etc/cron.d/x"})
                out2 = perm_check("write", {"path": f"{_H}/athena-system/core/x.py"})
                out3 = perm_check("terminal", {"command": "curl https://example.com"})
                checks.append({
                    "name": "bounds rule: in-platform approved, out-of-bounds prompts",
                    "status": "ok" if not in1["needs_prompt"] and not in2["needs_prompt"]
                    and out1["needs_prompt"] and out2["needs_prompt"]
                    and out3["needs_prompt"] else "fail",
                    "detail": f"in={not in1['needs_prompt']}/{not in2['needs_prompt']} "
                              f"out={out1['needs_prompt']}/{out2['needs_prompt']}/{out3['needs_prompt']}",
                })
            finally:
                _perm._rules_path = _orig_rp

        # 4. Disconnect unregisters.
        r2 = disconnect_mcp("test-srv")
        after = [t for t in TOOLS if t.startswith("mcp_test_srv")]
        checks.append({
            "name": "mcp disconnect unregisters namespace",
            "status": "ok" if r2.get("ok") and not after else "fail",
            "detail": f"removed={r2.get('tools_removed')}",
        })
        # SESSION HYGIENE: restore the real sessions dir + drop the tempdir.
        dbmod.sessions_dir = _orig_sessions
        _td.cleanup()
    return checks
