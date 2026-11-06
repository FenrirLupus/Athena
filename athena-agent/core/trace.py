"""Real-time trace: what Athena is doing, as she does it.

One line per event on the console, correlated by channel and turn, bounded in memory so it can run all day. Off by
default — when the level is off every call here is a null object and costs one comparison.

The lane is the `channel=` an event was given, or the lane bound around the call with `lane_scope` / `@lane_bound`:
the turn loop binds once per turn and the channel worker once per job, so a helper that can be called from any lane
— a durable write, a config read, a window probe — still says which one it happened in. A line emitted outside any
turn carries `-`, which is honest rather than guessed.

    from core import trace
    trace.event("model.request", channel="default", model="deepseek-chat", tokens=1200)
    with trace.span("tool.call", channel="default", tool="filesystem", action="ls") as sp:
        sp.ok(entries=12)
    trace.failure("turn.failed", exc)

Levels, lowest to highest: off · error · info · debug · trace. `off` prints nothing and records nothing; `error` keeps
failures only; `info` adds events; `debug` adds span entry and exit lines; `trace` is for per-token detail and costs
what it says.

Line format, one event per line, always on stderr:

    [trace 12:04:31.087] default#7 → tool.call tool=filesystem timeout=60.0
    [trace 12:04:31.402] default#7 ← tool.call tool=filesystem timeout=60.0 ms=315.0 status=ok bytes=812
    [trace 12:04:32.010] default#7 ✗ tool.call exc=TimeoutError: read timed out where=loader.py:88 in load ms=601.2

Every value goes through `_redact`, which drops fields whose *name* names a credential, masks anything matching a
token pattern, and shortens long strings. Instrumentation that can leak a key is a vulnerability, not a diagnostic.
"""
from __future__ import annotations

import contextvars
import functools
import json
import os
import re
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

LEVELS = {"off": 0, "error": 1, "info": 2, "debug": 3, "trace": 4}
DEFAULT_LEVEL = "off"
DEFAULT_BUFFER = 2000

SECRET_KEYS = re.compile(
    r"(?:^|[_-])(?:token|secrets?|password|passwd|api[_-]?key|authorization|cookie|credentials?|"
    r"private[_-]?key|access[_-]?key|session[_-]?id|client[_-]?secret|signing[_-]?key)$", re.I)
TOKENISH = re.compile(
    r"\b(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_\-]{16,}|hf_[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}"
    r")\b")
BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{12,}")
MAX_VALUE = 300

_lock = threading.RLock()
_emitting = threading.local()
_state: dict[str, str] = {}
_level = LEVELS[DEFAULT_LEVEL]
_buffer_limit = DEFAULT_BUFFER
_buffers: dict[str, deque] = {}
_console = True


def _redact(value, depth: int = 0):
    """A value safe to print: secrets masked, long strings cut, structure kept shallow."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if SECRET_KEYS.search(str(k)) else _redact(v, depth + 1))
                for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        value = TOKENISH.sub("[REDACTED]", value)
        value = BEARER.sub(r"\1[REDACTED]", value)
        return value if len(value) <= MAX_VALUE else value[:MAX_VALUE] + f"…(+{len(value) - MAX_VALUE})"
    return value


def _clean(text: str) -> str:
    """One event is one line: an embedded newline would break every reader of the trail."""
    return (str(text).replace("\r", "\\r").replace("\n", "\\n"))


def _render(value) -> str:
    if isinstance(value, str):
        return _clean(value)
    return _clean(json.dumps(value, ensure_ascii=False, default=str))


def configure(level: str = "", console: bool | None = None, buffer: int = 0) -> dict:
    """Set the level at runtime. `off` makes every later call a no-op."""
    global _level, _console, _buffer_limit
    with _lock:
        if level:
            _level = _normalise(level, default=None)
        if console is not None:
            _console = bool(console)
        if buffer:
            _buffer_limit = max(1, int(buffer))
            for lane, buf in list(_buffers.items()):
                _buffers[lane] = deque(buf, maxlen=_buffer_limit)
    return {"level": level_of(), "console": _console, "buffer": _buffer_limit}


def _normalise(level, default: str | None = "info") -> int:
    if isinstance(level, int):
        return level
    value = LEVELS.get(str(level).strip().lower())
    if value is not None:
        return value
    return _level if default is None else LEVELS[default]


def health() -> dict:
    """Whether the tracer itself is intact — a tracer that silently drops lines is worse than none."""
    return {"level": level_of(), "console": _console, "console_failed": _state.get("console_failed", ""),
            "lanes": len(_buffers)}


def level_of() -> str:
    for name, value in LEVELS.items():
        if value == _level:
            return name
    return "custom"


def enabled(level: str = "info") -> bool:
    """Guard expensive field-building: `if trace.enabled("debug"): trace.event(...)`."""
    return _level >= _normalise(level)


def clear(channel: str = "") -> None:
    """Forget the trail for one lane, or every lane — after a failed turn has been read."""
    with _lock:
        if channel:
            _buffers.pop(str(channel), None)
        else:
            _buffers.clear()


def _lane(fields: dict) -> str:
    return str(fields.get("channel") or fields.get("lane") or current_lane() or "-")


# The lane a call is running in. One binding per turn or job — a durable write, a config read, a window probe are
# called from every lane and cannot name one, so threading `channel=` through each signature would be thirty
# chances to miss one. Empty means "outside any turn"; the line then says `-`, which is honest rather than guessed.
_LANE: contextvars.ContextVar = contextvars.ContextVar("athena_trace_lane", default=("", None))


def current_lane() -> str:
    """The lane bound around this call, or "" when there is none."""
    return _LANE.get()[0]


def current_turn() -> int | None:
    """The turn bound around this call, or None — the lane's own counter, not a guess."""
    return _LANE.get()[1]


@contextmanager
def lane_scope(name: str = "", turn: int | None = None):
    """Bind the lane every line inside this block inherits when it passes no `channel=` of its own.

    An empty `name` keeps the lane already bound, so a nested scope can set a turn without erasing the lane.
    Context-local, so two channel workers on two threads never see each other's lane.
    """
    outer_lane, outer_turn = _LANE.get()
    token = _LANE.set((str(name) if name else outer_lane, turn if turn is not None else outer_turn))
    try:
        yield
    finally:
        _LANE.reset(token)


def lane_bound(fn):
    """Decorator for a function that *is* one lane: `@trace.lane_bound def run_turn(*, channel, …)`.

    Binds the call's own `channel=` (or `lane=`) keyword for the whole body without changing its signature, so the
    helpers it calls — which never receive a channel — still say which lane they happened in.
    """
    @functools.wraps(fn)
    def bound(*args, **kwargs):
        with lane_scope(kwargs.get("channel") or kwargs.get("lane") or ""):
            return fn(*args, **kwargs)
    return bound


def _emit(kind: str, name: str, fields: dict) -> None:
    if _level <= 0:                                  # off: nothing printed, nothing stored
        return
    explicit = bool(fields.get("channel") or fields.get("lane"))
    lane = _lane(fields)
    stamp = time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
    head = f"[trace {stamp}] {lane}"
    turn = fields.get("turn")
    if turn is None and not explicit:                # the lane came from the scope: its turn comes with it
        turn = current_turn()
    if turn is not None:
        head += f"#{turn}"
    parts = []
    for key, value in fields.items():
        if key in ("channel", "lane", "turn"):
            continue
        safe = "[REDACTED]" if SECRET_KEYS.search(str(key)) else _redact(value)
        parts.append(f"{key}={_render(safe)}")
    line = f"{head} {kind} {_clean(name)}" + ((" " + " ".join(parts)) if parts else "")
    with _lock:
        buf = _buffers.get(lane)
        if buf is None or buf.maxlen != _buffer_limit:
            buf = _buffers[lane] = deque(buf or (), maxlen=_buffer_limit)
        buf.append({"t": time.time(), "kind": kind, "name": name, "fields": _redact(fields)})
        if _console and not getattr(_emitting, "busy", False):
            _emitting.busy = True
            try:
                print(line, file=sys.stderr, flush=True)
            except Exception as exc:                      # noqa: BLE001 — the tracer must not take the process down
                _state["console_failed"] = f"{type(exc).__name__}: {exc}"
                try:                                      # last resort: the raw fd, bypassing print entirely
                    os.write(2, f"[trace] console write failed: {_state['console_failed']}\n".encode())
                except Exception:                         # noqa: BLE001 — nothing left to report to
                    pass
                try:                                      # recorded in the buffer; the console is skipped while busy
                    trace.event("trace.console.failed", level="error", channel="-",
                                reason=_state["console_failed"])
                except Exception:                         # noqa: BLE001 — the tracer reports, never raises
                    pass
            finally:
                _emitting.busy = False


def event(name: str, level: str = "info", **fields) -> None:
    threshold = _normalise(level)
    if _level >= threshold:
        # anything recorded *at* error level is a refusal or a defect: it gets the frame that recorded it, the same
        # way a failure does, so a reader can act on it without guessing which call site refused. Info and below do
        # not — a `where=` on every heartbeat buries the six lines that matter
        if threshold <= LEVELS["error"]:
            fields.setdefault("where", _caller(2))
        _emit("·", name, fields)


def failure(name: str, exc: BaseException | None = None, **fields) -> None:
    """Recorded at `error` and above, whatever level the surrounding span ran at — a failure with no frame is the
    thing we are actually fixing. At `off` it is silent like everything else.

    A policy refusal has no exception to blame, so the frame that *made the decision* is recorded instead: a `✗`
    line with no `where=` is a line you cannot act on at 3am."""
    if _level < LEVELS["error"]:
        return
    if exc is not None:
        fields.setdefault("exc", f"{type(exc).__name__}: {exc}")
        fields.setdefault("where", _tb(exc))
    else:
        fields.setdefault("where", _caller(2))
    _emit("✗", name, fields)


def _caller(depth: int = 1) -> str:
    """`file.py:line in function` for the frame that called the recorder."""
    try:
        frame = sys._getframe(depth)
    except ValueError:
        return "?"
    return f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno} in {frame.f_code.co_name}"


def _tb(exc: BaseException) -> str:
    import traceback
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "?"
    last = frames[-1]
    return f"{Path(last.filename).name}:{last.lineno} in {last.name}"


class _Span:
    """The outbound half of a call: duration, result or failure, one line each way."""

    def __init__(self, name: str, kind: str, fields: dict, live: bool = True):
        self.name, self.kind, self.fields = name, kind, fields
        self.started = time.time()
        self.live = live
        self.closed = False

    def ok(self, **result) -> None:
        if self.closed or not self.live:
            return
        self.closed = True
        self.fields.update(result)
        self.fields["ms"] = round((time.time() - self.started) * 1000, 1)
        _emit("←", self.name, {**self.fields, "status": "ok"})

    def failed(self, exc: BaseException) -> None:
        if self.closed:
            return
        self.closed = True
        self.fields["ms"] = round((time.time() - self.started) * 1000, 1)
        self.fields["status"] = "failed"
        failure(self.name, exc, **self.fields)


@contextmanager
def span(name: str, level: str = "debug", **fields):
    """`with trace.span("tool.call", channel=…, tool=…) as sp: sp.ok(rows=3)` — one line in, one line out.

    A body that returns without calling `sp.ok()` still gets its exit line, because a span with no exit is
    indistinguishable from a hang."""
    live = _level >= _normalise(level)
    if not live:
        sp = _Span(name, "→", dict(fields), live=False)
        try:
            yield sp
        except BaseException as exc:
            sp.failed(exc)                  # the span's own lines are below the level; the failure is not
            raise
        return
    _emit("→", name, dict(fields))
    sp = _Span(name, "→", dict(fields))
    try:
        yield sp
    except BaseException as exc:
        sp.failed(exc)
        raise
    else:
        sp.ok()


def buffer(channel: str = "", limit: int = 200) -> list[dict]:
    """The trail for one lane, oldest first — what a failure is read against."""
    with _lock:
        if channel:
            buf = _buffers.get(str(channel), deque())
            return list(buf)[-int(limit):]
        merged: list[dict] = []
        for buf_ in _buffers.values():
            merged.extend(buf_)
        merged.sort(key=lambda e: e["t"])
        return merged[-int(limit):]


def dump(channel: str = "", path: str = "") -> str:
    """Write the trail to a file so it survives the process, and say where it went."""
    events = buffer(channel, 2000)
    target = Path(path).expanduser() if path else \
        Path(f"/tmp/athena-trace-{channel or 'all'}-{int(time.time())}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(events, indent=1, default=str), encoding="utf-8")
    return str(target)


def _from_env() -> None:
    level = os.environ.get("ATHENA_TRACE", "").strip()
    console = os.environ.get("ATHENA_TRACE_CONSOLE", "").strip()
    if level.lower() in ("1", "true", "yes", "on"):
        level = "debug"
    configure(level=level or DEFAULT_LEVEL,
              console=None if not console else console not in ("0", "false", "no"),
              buffer=int(os.environ.get("ATHENA_TRACE_BUFFER", "0") or 0))


_from_env()

# The emitter referring to itself: this is what lets a degraded tracer report its own failure, and what makes
# `scripts/trace_coverage.py` count this module rather than exempt it.
import core.trace as trace  # noqa: E402  (a self-import: the module is already in sys.modules)
