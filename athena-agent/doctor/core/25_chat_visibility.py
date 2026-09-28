"""The chat visibility + interrupt + patch fixes — the Operator's 08-15 spec:

1. The loop emits WORKING-STATE events per iteration (the GUI's thinking
   block stays open/live while the agent works).
2. The patch tool normalizes string hunks (no 'str' object has no
   attribute 'get' crash).
3. The interrupt delivers ONE clean message (the doubled-fire + fragment
   guards live in the GUI; the loop's acknowledgment is verified here).
"""
from __future__ import annotations


def run() -> list[dict]:
    from core.message_loop import MessageLoop
    checks = []

    # 1. The working-state event fires per iteration.
    events = []
    ml = MessageLoop.__new__(MessageLoop)
    ml._emit = lambda kind, detail, extra="": events.append((kind, detail))
    ml.streaming = False
    ml.max_iterations = 2
    ml.system_prompt = ""
    ml.interrupt_flag = lambda: False
    ml.channel = None
    ml.loop_guardrails = None
    ml.on_event = None
    ml.on_approval = None
    ml.workflow_name = None
    ml.workflow = None
    ml.chain_hops = 0
    ml._tool_transcript = []
    ml.session_id = "wf-test"
    ml._pending_user = ""
    try:
        from unittest.mock import patch as _patch
        calls = []
        def fake_post_json(url, key, payload, timeout=60.0):
            calls.append(payload)
            n = len(calls)
            return {"choices": [{"message": {
                "content": "workflow: conversation\nhi" if n == 1 else "hi there",
                "tool_calls": []}, "finish_reason": "stop"}], "usage": {}}
        with _patch("providers.provider._post_json", side_effect=fake_post_json):
            ml.run_turn("hi", history=[])
    except Exception:
        pass
    working = [e for e in events if e[0] == "state"]
    checks.append({
        "name": "chat: working-state events per iteration",
        "status": "ok" if working else "fail",
        "detail": f"{len(working)} state events",
    })

    # 2. The patch tool normalizes string hunks (no crash).
    try:
        from filesystem.tools import TOOLS
        patch_tool = TOOLS.get("patch")
        ok_patch = False
        if patch_tool is not None:
            from pathlib import Path as _P
            import tempfile
            _base = _P.home() / '.athena' / 'workspace'
            _base.mkdir(parents=True, exist_ok=True)
            p = _base / 'patch_hunk_test.txt'
            p.write_text("line one\nline two\n")
            try:
                res = patch_tool.run({"path": str(p),
                                      "hunks": '[{"old": "one", "new": "1"}]'})
                ok_patch = ("1" in p.read_text()) if p.exists() else False
            finally:
                p.unlink(missing_ok=True)
        checks.append({
            "name": "patch: string hunks normalized (no crash)",
            "status": "ok" if ok_patch else "fail",
            "detail": f"patch applied: {ok_patch}",
        })
    except Exception as exc:
        checks.append({
            "name": "patch: string hunks normalized (no crash)",
            "status": "fail",
            "detail": f"error: {exc}",
        })

    # 3. The interrupted turn returns a clean acknowledgment.
    ml2 = MessageLoop.__new__(MessageLoop)
    ml2.streaming = False
    ml2.max_iterations = 3
    ml2.system_prompt = ""
    ml2.interrupt_flag = lambda: True   # interrupted immediately
    ml2.channel = None
    ml2.loop_guardrails = None
    ml2.on_event = None
    ml2.on_approval = None
    ml2.workflow_name = None
    ml2.workflow = None
    ml2.chain_hops = 0
    ml2._tool_transcript = []
    ml2.session_id = "int-test"
    ml2._pending_user = ""
    r = ml2.run_turn("do it", history=[])
    checks.append({
        "name": "interrupt: clean acknowledgment",
        "status": "ok" if r.exit_reason == "interrupted_by_user"
        and "Interruption" in r.reply else "fail",
        "detail": f"exit={r.exit_reason} reply={r.reply[:40]}",
    })
    return checks
