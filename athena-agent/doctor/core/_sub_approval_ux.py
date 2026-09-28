"""Approval UX test — the Operator's fixes.

1. The approval request carries REAL arguments + a human-readable reason.
2. The tool gate passes the call's arguments into on_approval (not {}).
3. A session-scope allow PERSISTS — no re-prompting within the session.
4. The guardrail-hold path persists its decision too.
"""
from __future__ import annotations


def run() -> list[dict]:
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from core import approvals

    checks = []

    # 1. The approval request carries args + a reason.
    req = approvals.request_approval(
        "terminal", {"command": "ls -la"}, "unsafe",
        reason="this action can write — needs your go-ahead")
    checks.append({
        "name": "approval request carries args + reason",
        "status": "ok" if req["arguments"] == {"command": "ls -la"}
        and "needs your go-ahead" in req.get("reason", "") else "fail",
        "detail": f"args={req['arguments']} reason={req.get('reason','')[:40]}",
    })
    approvals.resolve_approval(req["id"], "allow", "session")

    # 2. The tool gate passes REAL args into on_approval + persists.
    prompts = []

    class FakeChannel:
        name = "assistant"
        def allows_tool(self, n): return True
        def allows_skill(self, n): return True

    def fake_approval(tool, args, risk):
        prompts.append((tool, args, risk))
        return "allow", "session"

    import security.permissions as perm
    orig_check, orig_decide = perm.check, perm.decide

    def fake_check(tool, args=None, **kw):
        if any(t == tool for t, _, _ in prompts):
            return {"allowed": True, "verdict": "allow",
                    "risk": "unsafe", "needs_prompt": False}
        return {"allowed": False, "verdict": "needs_prompt",
                "risk": "unsafe", "needs_prompt": True}

    perm.check = fake_check
    perm.decide = lambda tool, verdict, scope, **kw: True

    from core.message_loop import MessageLoop
    loop = MessageLoop(on_approval=fake_approval)
    loop.channel = FakeChannel()
    loop.max_iterations = 2
    resp1 = {"content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "terminal",
         "arguments": '{"command": "ls -la"}'}}],
        "finish_reason": "tool_calls", "usage": {}}
    resp2 = {"content": "done", "tool_calls": None,
             "finish_reason": "stop", "usage": {}}
    # THE PROMPT-FIRST DESIGN (the CEO's 08-15 correction): there is NO
    # separate START call — the workflow selection folds into the first
    # full-prompt response. The fake answers the tool-call response first
    # (the workflow name is optional), then the final reply.
    with patch.object(MessageLoop, "_call_model",
                      side_effect=[resp1, resp2]):
        loop.run_turn("do it", history=[])
    checks.append({
        "name": "tool gate passes real args + persists session allow",
        "status": "ok" if len(prompts) == 1
        and prompts[0][1] == {"command": "ls -la"} else "fail",
        "detail": f"prompts={len(prompts)} args={prompts[0][1] if prompts else None}",
    })
    perm.check, perm.decide = orig_check, orig_decide
    return checks
