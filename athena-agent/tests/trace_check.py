"""Tracing contract: the trail says what happened, and nothing it says can be a secret.

`core/trace.py` is the only place instrumentation is allowed to write diagnostics, so its behaviour is asserted here:
the levels mean what the docstring says, every span gets an exit line, an exception carries the file and line that
raised it, a credential in any position never reaches the console or the buffer, and memory stays bounded per lane.
Run under `tests/run_gate.sh`; a change that makes any of these false is a change that makes a failed turn unreadable.
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import trace

CHECKS = {}


def capture(fn):
    """Run `fn` and return what the trace wrote to stderr."""
    buf = io.StringIO()
    with redirect_stderr(buf):
        fn()
    return buf.getvalue()


def boom():
    raise RuntimeError("kaboom")


RAISE_LINE = next(i for i, line in enumerate(Path(__file__).read_text().splitlines(), 1)
                  if 'raise RuntimeError("kaboom")' in line)


def lines_for(out, glyph, name):
    return [ln for ln in out.splitlines() if f" {glyph} {name} " in ln]


# ── off: the null object ────────────────────────────────────────────────────────────────────────────────────────
def off_run():
    trace.event("t.off.event")
    with trace.span("t.off.span") as sp:
        sp.ok(n=1)
    try:
        with trace.span("t.off.fail"):
            boom()
    except RuntimeError:
        pass
    trace.failure("t.off.failure")


trace.configure(level="off")
trace.clear()
out = capture(off_run)
CHECKS["off_prints_nothing"] = out == ""
CHECKS["off_records_nothing"] = trace.buffer() == []

# ── debug: one entry and exactly one exit per span ──────────────────────────────────────────────────────────────
def debug_run():
    with trace.span("t.span", channel="dbg", turn=1, tool="ls") as sp:
        sp.ok(rows=3)
    with trace.span("t.span.no_ok", channel="dbg"):      # body returns without ok(): still one exit
        pass


trace.configure(level="debug", console=True)
trace.clear()
out = capture(debug_run)
CHECKS["debug_one_entry"] = len(lines_for(out, "→", "t.span")) == 1
CHECKS["debug_one_exit"] = len(lines_for(out, "←", "t.span")) == 1
CHECKS["debug_exit_carries_ms_and_status"] = all(k in lines_for(out, "←", "t.span")[0]
                                                 for k in ("ms=", "status=ok", "rows=3"))
CHECKS["debug_unclosed_span_gets_exit"] = len(lines_for(out, "←", "t.span.no_ok")) == 1
CHECKS["debug_span_pairs_balanced"] = (len(lines_for(out, "→", "t.span")) == 1
                                       and len(lines_for(out, "←", "t.span")) == 1)
CHECKS["lane_and_turn_in_head"] = any(ln.startswith("[trace ") and " dbg#1 " in ln for ln in out.splitlines())

# an event is one line, whatever is in it
def newline_run():
    trace.event("t.multiline", channel="dbg", text="one\ntwo")


CHECKS["event_stays_one_line"] = len(capture(newline_run).splitlines()) == 1

# ── failure: recorded with the raising frame, at error level even when the span is silent ────────────────────────
def fail_run():
    try:
        with trace.span("t.fail", channel="dbg", tool="loader"):
            boom()
    except RuntimeError:
        pass


trace.clear()
out = capture(fail_run)
fail_lines = lines_for(out, "✗", "t.fail")
CHECKS["exception_records_failure"] = len(fail_lines) == 1
CHECKS["failure_carries_file_and_line"] = bool(fail_lines) and f"trace_check.py:{RAISE_LINE} " in fail_lines[0]
CHECKS["failure_carries_exception"] = bool(fail_lines) and "RuntimeError: kaboom" in fail_lines[0]
CHECKS["failure_has_ms"] = bool(fail_lines) and "ms=" in fail_lines[0]
CHECKS["failure_in_buffer"] = any(e["kind"] == "✗" and e["name"] == "t.fail" for e in trace.buffer("dbg"))

# at info the span's own lines are below the threshold, but a failure inside it is not
def info_run():
    try:
        with trace.span("t.info.span", channel="dbg"):
            boom()
    except RuntimeError:
        pass


trace.configure(level="info")
trace.clear()
out = capture(info_run)
CHECKS["info_hides_span_lines"] = lines_for(out, "→", "t.info.span") == []
CHECKS["info_keeps_failures"] = len(lines_for(out, "✗", "t.info.span")) == 1
CHECKS["info_level_event_prints"] = capture(lambda: trace.event("t.info.event", channel="dbg")) != ""
CHECKS["info_level_debug_event_hidden"] = capture(
    lambda: trace.event("t.debug.event", level="debug", channel="dbg")) == ""


# a policy refusal has no exception to blame: the frame that decided is the one recorded
def refusal_run():
    trace.failure("t.refusal", channel="dbg", reason="timeout")


REFUSAL_LINE = next(i for i, line in enumerate(Path(__file__).read_text().splitlines(), 1)
                    if 'trace.failure("t.refusal"' in line)
trace.configure(level="error")
trace.clear()
out = capture(refusal_run)
refusal_lines = lines_for(out, "✗", "t.refusal")
CHECKS["refusal_without_exception_has_frame"] = (
    len(refusal_lines) == 1
    and f"trace_check.py:{REFUSAL_LINE} in refusal_run" in refusal_lines[0])


# a refusal recorded as an event at error level is still a refusal: it carries its frame too
def error_event_run():
    trace.event("t.error.event", level="error", rule="refused")


ERROR_EVENT_LINE = next(i for i, line in enumerate(Path(__file__).read_text().splitlines(), 1)
                        if 'trace.event("t.error.event"' in line)
trace.clear()
out = capture(error_event_run)
error_event_lines = lines_for(out, "·", "t.error.event")
CHECKS["error_event_carries_frame"] = (
    len(error_event_lines) == 1
    and f"trace_check.py:{ERROR_EVENT_LINE} in error_event_run" in error_event_lines[0])

# ── secrets: a key by name, a token by shape, at any depth ──────────────────────────────────────────────────────
SECRET_STR = "sk-live-abcdefghijklmnopqrstuvwxyz012345"


def secret_run():
    trace.event("t.secret", channel="dbg",
                api_key=SECRET_STR,
                headers={"Authorization": "Bearer abcdefghijklmnop123456"},
                note=f"using {SECRET_STR} and ghp_{'a' * 24}",
                nested={"items": [{"password": "hunter2", "other": SECRET_STR}]},
                ok_field="visible")


trace.configure(level="debug")
trace.clear()
out = capture(secret_run)
CHECKS["secret_key_dropped"] = SECRET_STR not in out
CHECKS["secret_token_shape_masked"] = "ghp_" + "a" * 24 not in out
CHECKS["secret_bearer_masked"] = "abcdefghijklmnop123456" not in out
CHECKS["secret_nested_masked"] = "hunter2" not in out
CHECKS["secret_marked_redacted"] = "[REDACTED]" in out
CHECKS["nonsecret_survives"] = "ok_field=visible" in out
CHECKS["secret_absent_from_buffer"] = SECRET_STR not in json.dumps(trace.buffer("dbg"))


def counts_run():
    trace.event("t.counts", channel="dbg", tokens=1200, prompt_tokens=900, max_tokens=1024)


counts_out = capture(counts_run)
CHECKS["token_counts_not_masked"] = all(k in counts_out for k in ("tokens=1200", "prompt_tokens=900",
                                                                 "max_tokens=1024"))

# ── buffer: per lane, bounded, oldest dropped ──────────────────────────────────────────────────────────────────
trace.configure(level="debug", buffer=5, console=False)
trace.clear()
for i in range(12):
    trace.event("t.buf", channel="lane-a", i=i)
for i in range(3):
    trace.event("t.buf", channel="lane-b", i=i)
CHECKS["buffer_bounded_by_configure"] = len(trace.buffer("lane-a")) == 5
CHECKS["buffer_per_lane"] = len(trace.buffer("lane-b")) == 3
CHECKS["buffer_oldest_dropped"] = [e["fields"]["i"] for e in trace.buffer("lane-a")] == list(range(7, 12))
CHECKS["buffer_limit_applies_to_new_lanes"] = len(trace.buffer("lane-c")) <= 5
CHECKS["buffer_other_lane_untouched"] = all(e["fields"]["i"] < 12 for e in trace.buffer("lane-b"))
trace.event("t.buf", channel="lane-c")
CHECKS["buffer_new_lane_bounded"] = len(trace.buffer("lane-c")) == 1
CHECKS["buffer_lane_isolated"] = len(trace.buffer("lane-b")) == 3

# ── dump: the trail outlives the process ───────────────────────────────────────────────────────────────────────
target = Path(tempfile.mkdtemp(prefix="athena-trace-check-")) / "nested" / "trail.json"
written = trace.dump("lane-a", path=str(target))
CHECKS["dump_writes_path"] = Path(written).is_file()
CHECKS["dump_is_json_list"] = isinstance(json.loads(target.read_text()), list)
CHECKS["dump_has_the_lane"] = len(json.loads(target.read_text())) == 5
CHECKS["dump_creates_parent"] = target.parent.is_dir()

# restore the process default so an imported trace does not colour anything after this suite
trace.configure(level="off", buffer=trace.DEFAULT_BUFFER, console=True)

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)
