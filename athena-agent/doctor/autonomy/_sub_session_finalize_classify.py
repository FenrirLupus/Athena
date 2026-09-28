"""Session state + turn finalizer + result classification test.

the Operator's spec:
1. Session state machine — the Idle → Thinking → Responding → Idle flow,
   with three scopes (turn/conversation/persistent) + the
   clear-point discipline.
2. Turn finalizer — the clean end-of-turn step (sanitize + mark closed).
3. Tool + Skill result classification — the model sees simple classified
   outputs; the system handles the complex backends.
"""
from __future__ import annotations


def run() -> list[dict]:
    from core.session_state import (get_state, flow_of, FLOW_THINKING,
                                    FLOW_RESPONDING, FLOW_IDLE)
    from core.turn_finalizer import sanitize_reply, finalize_turn
    from core.result_classifier import (classify_result, present,
                                        OK, EMPTY, NOT_FOUND, DENIED, ERROR)

    checks = []

    # 1. The flow machine: Idle → Thinking → Responding → Done → Idle.
    s = get_state("state-test")
    s.start_turn("t1")
    thinking = s.turn.flow == FLOW_THINKING
    s.begin_response()
    responding = s.turn.flow == FLOW_RESPONDING
    s.finish_turn()
    cleared = s.turn.flow == FLOW_IDLE
    persistent = s.persistent.total_turns == 1
    checks.append({
        "name": "session flow machine (idle→thinking→responding→idle)",
        "status": "ok" if thinking and responding and cleared
        and persistent else "fail",
        "detail": f"thinking={thinking} responding={responding} "
                  f"cleared={cleared} persistent={persistent}",
    })
    # Conversation-scope reset: turn_count clears, persistent survives.
    s.reset_conversation("new-session")
    checks.append({
        "name": "conversation scope resets, persistent survives",
        "status": "ok" if s.conversation.turn_count == 0
        and s.persistent.total_turns == 1
        and s.conversation.session_id == "new-session" else "fail",
        "detail": f"turns={s.conversation.turn_count} "
                  f"persistent={s.persistent.total_turns}",
    })

    # 2. Turn finalizer: sanitize + close.
    dirty = "As an AI language model, I can't feel.\x00\x07Here.\n\n\n\nDone."
    clean = sanitize_reply(dirty)
    checks.append({
        "name": "turn finalizer sanitizes reply",
        "status": "ok" if "\x00" not in clean and "\x07" not in clean
        and "language model" not in clean and "Done" in clean else "fail",
        "detail": repr(clean[:50]),
    })
    finalize_turn("final-test", "ok")
    checks.append({
        "name": "finalizer marks turn closed (idle)",
        "status": "ok" if flow_of("final-test") == FLOW_IDLE else "fail",
        "detail": f"flow={flow_of('final-test')}",
    })

    # 3. Result classification: all five statuses + the short model view.
    cases = [
        ("file written: /tmp/x", OK),
        ("", EMPTY),
        ("No results found", NOT_FOUND),
        ("[denied: not allowed]", DENIED),
        ("Traceback: boom", ERROR),
    ]
    ok_all = all(classify_result(raw)["status"] == want
                 for raw, want in cases)
    short = present("x" * 500)
    checks.append({
        "name": "result classification: 5 statuses + short view",
        "status": "ok" if ok_all and len(short) <= 220 else "fail",
        "detail": str([classify_result(r)["status"] for r, _ in cases]),
    })
    return checks
