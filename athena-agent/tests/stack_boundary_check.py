"""The history window must not start mid-tool-round.

A provider rejects a `tool` message that has no preceding `tool_calls` message, and the trim works newest-first — so a
tight budget can cut exactly there. This reproduces that cut deliberately and asserts the assembled prompt is legal.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context import stack

checks = {}


def msg(role, content="", **kw):
    return {"role": role, "content": content, **kw}


def assemble(history, window=2000):
    cfg = {"model": {"window": window},
           "budget": {"system_pct": 5, "assistant_pct": 5, "user_pct": 5, "summary_pct": 10,
                      "history_pct": 25, "memory_pct": 5, "output_pct": 10}}
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "profiles" / "probe"
        for sub in ("assistant", "user", "memories"):
            (root / sub).mkdir(parents=True)
        messages, _report = stack.build(
            instructions="be brief",
            channel_root=root,
            history=history,
            user_text="",
            cfg=cfg,
            summary="",
            factor=1.0,
        )
    return messages


# a normal round: user → assistant(tool_calls) → tool → assistant
history = [
    msg("user", "do the thing"),
    msg("assistant", "", tool_calls=[{"id": "c1", "name": "echo", "arguments": {"text": "x"}}]),
    msg("tool", "x", tool_call_id="c1", name="echo"),
    msg("assistant", "done"),
]
out = assemble(history)
roles = [m["role"] for m in out]
checks["normal_round_survives"] = roles == ["system", "user", "assistant", "tool", "assistant"]

# the dangerous shape: the window opens on a tool message
broken = [
    msg("tool", "orphaned result", tool_call_id="c0", name="echo"),
    msg("assistant", "…"),
    msg("user", "next"),
]
out2 = assemble(broken)
roles2 = [m["role"] for m in out2]
checks["no_leading_tool_message"] = roles2[:2] != ["system", "tool"] and "tool" not in roles2[:2]
checks["content_after_the_fix_kept"] = "next" in str(out2)

# and a tool row in the middle of a valid round is untouched
middle = [
    msg("user", "q"),
    msg("assistant", "", tool_calls=[{"id": "c9", "name": "echo", "arguments": {}}]),
    msg("tool", "r", tool_call_id="c9", name="echo"),
    msg("assistant", "a"),
    msg("user", "q2"),
]
out3 = assemble(middle)
checks["midround_tool_kept"] = [m["role"] for m in out3] == ["system", "user", "assistant", "tool", "assistant", "user"]

# a window that can only fit the tail still must not open on a tool row
tiny = [
    msg("user", "x" * 400),
    msg("assistant", "", tool_calls=[{"id": "c1", "name": "echo", "arguments": {}}]),
    msg("tool", "y" * 400, tool_call_id="c1", name="echo"),
    msg("assistant", "z" * 400),
]
out4 = assemble(tiny, window=900)
checks["tight_window_is_legal"] = not (out4 and out4[0]["role"] == "tool")

for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
print("\nRESULT:", "PASS" if all(checks.values()) else "FAIL")
