"""Loop guardrails test — runaway-loop protection (adapted).

The Operator's audit found Athena had intent guards but not loop guards.
This verifies: exact-failure block, no-progress block, loop caps,
warn vs hard-stop tiers, and the message-loop wiring.
"""
from __future__ import annotations


def run() -> list[dict]:
    from security.loop_guardrails import LoopGuardrails, classify_failure

    checks = []

    # 1. Exact-failure: a MUTATING call fails 5× → BLOCK (read-only
    #    tools are exempt — the 08-14 fix: repeated reads are legit
    #    monitoring, governed by no-progress instead).
    g = LoopGuardrails({"hard_stop_enabled": True})
    block = None
    for _ in range(6):
        block = g.before_call("write", {"path": "/x", "content": "y"})
        g.after_call("write", {"path": "/x", "content": "y"}, "error: not found")
    checks.append({
        "name": "loop guard: exact-failure blocks after 5 (mutating)",
        "status": "ok" if block and block["action"] == "block"
        and block["code"] == "repeated_exact_failure_block" else "fail",
        "detail": str(block.get("code")) if block else "never blocked",
    })

    # 1b. Read-only exemption: identical READS are NOT exact-failure
    #     blocked (the 08-14 fix) — even failing reads.
    gr = LoopGuardrails({"hard_stop_enabled": True})
    read_block = None
    for _ in range(8):
        read_block = gr.before_call("read", {"path": "/x"})
        gr.after_call("read", {"path": "/x"}, "error: not found")
    checks.append({
        "name": "loop guard: read-only exact-failure exempt",
        "status": "ok" if not (read_block and read_block["action"] == "block")
        else "fail",
        "detail": "read exempt from exact-failure block",
    })

    # 2. No-progress: read-only tool returns the SAME result 8× → BLOCK
    #    (the threshold raised 5→8 in the 08-14 fix).
    g2 = LoopGuardrails({"hard_stop_enabled": True})
    block2 = None
    for _ in range(10):
        block2 = g2.before_call("read", {"path": "/same"})
        g2.after_call("read", {"path": "/same"}, "the same content")
    checks.append({
        "name": "loop guard: no-progress blocks after 8",
        "status": "ok" if block2 and block2["action"] == "block"
        and block2["code"] == "idempotent_no_progress_block" else "fail",
        "detail": str(block2.get("code")) if block2 else "never blocked",
    })

    # 3. Same-tool halt: a tool fails 8× this turn (any args) → HALT.
    g3 = LoopGuardrails({"hard_stop_enabled": True})
    halt = None
    for i in range(8):
        g3.after_call("terminal", {"command": f"cmd {i}"}, "error: boom")
    halt = g3.after_call("terminal", {"command": "cmd 8"}, "error: boom")
    checks.append({
        "name": "loop guard: same-tool failures halt after 8",
        "status": "ok" if halt and halt["action"] == "halt"
        and g3.halt_reason else "fail",
        "detail": str(halt.get("code")) if halt else "never halted",
    })

    # 4. Loop caps: web_search blocked at the per-turn ceiling.
    g4 = LoopGuardrails({"loop_caps": {"max_web_searches": 3}})
    cap = None
    for _ in range(4):
        cap = g4.before_call("web_search", {"query": "x"})
    checks.append({
        "name": "loop guard: web_search cap blocks",
        "status": "ok" if cap and cap["code"] == "loop_web_search_cap" else "fail",
        "detail": str(cap.get("code")) if cap else "never capped",
    })

    # 5. Warn tier: warnings nudge but NEVER block (hard_stop off).
    g5 = LoopGuardrails({"hard_stop_enabled": False})
    saw_warn = False
    for _ in range(4):
        dec = g5.after_call("read", {"path": "/w"}, "error: nope")
        if dec and dec["action"] == "warn":
            saw_warn = True
        # before_call must NEVER block without hard_stop
        assert g5.before_call("read", {"path": "/w"}) is None
    checks.append({
        "name": "loop guard: warn tier nudges, never blocks",
        "status": "ok" if saw_warn else "fail",
        "detail": "warn seen with hard_stop disabled",
    })

    # 6. Failure classifier: error-looking results are failures.
    checks.append({
        "name": "loop guard: failure classifier",
        "status": "ok" if classify_failure("x", "error: boom")
        and classify_failure("x", "Traceback ...") and not classify_failure("x", "all good") else "fail",
        "detail": "error/Traceback → fail; plain text → ok",
    })
    return checks
