"""Console — the running process's own log, kept in memory for the terminal overlay.

A handler on the root logger copies every emitted record into a bounded ring, so the web terminal can show what the
server is actually doing rather than a reconstruction of it from other endpoints. Bounded because a log that grows
without limit is a memory leak wearing a feature's clothes; the cursor is a sequence number, so a client that falls
behind skips rather than duplicates.
"""
from __future__ import annotations

import collections
import json
import logging
import threading
import time
from pathlib import Path

MAX_ROWS = 800

_rows: collections.deque = collections.deque(maxlen=MAX_ROWS)
_lock = threading.Lock()
_seq = 0


class Ring(logging.Handler):
    """Copy each record into the shared ring, formatted once."""

    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        try:
            text = self.format(record)
        except Exception:
            text = record.getMessage()
        with _lock:
            _seq += 1
            _rows.append({
                "seq": _seq,
                "at": round(time.time(), 3),
                "level": (record.levelname or "info").lower(),
                "logger": record.name,
                "text": text[:2000],
            })


def install() -> logging.Handler:
    """Attach the ring once, to the root logger and to the ASGI server's own loggers.

    A server framework configures its loggers with propagation off, so a handler on root alone captures nothing the
    server itself says — which is most of what a console is for.
    """
    handler = Ring()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    # the stream mirror is the authority; attaching the ring to the server's loggers as well made every line
    # appear twice, once from the logger and once from the stream it also writes to
    targets = [logging.getLogger()]
    for target in targets:
        if not any(isinstance(h, Ring) for h in target.handlers):
            target.addHandler(handler)
    return handler


def recent(since: int = 0, limit: int = 200) -> list[dict]:
    with _lock:
        rows = [row for row in _rows if row["seq"] > since]
    return rows[-limit:] if limit else rows


def _log_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "metrics" / "console"


def _persist(row: dict) -> None:
    """Append to the day's file, so the terminal can page through what has already scrolled away."""
    try:
        directory = _log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.localtime(row["at"]))
        with (directory / f"console-{day}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def history(page: int = 1, size: int = 128) -> dict:
    """Dated log files, newest first, cut into pages of `size` lines."""
    size = max(1, min(int(size or 128), 512))
    rows = []
    try:
        files = sorted(_log_dir().glob("console-*.jsonl"), reverse=True)
    except Exception:
        files = []
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
    rows.sort(key=lambda r: r.get("at") or 0, reverse=True)
    total = len(rows)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(int(page or 1), pages))
    chunk = rows[(page - 1) * size: page * size]
    groups, seen = [], None
    for row in chunk:
        day = time.strftime("%Y-%m-%d", time.localtime(row.get("at") or 0))
        if day != seen:
            groups.append({"date": day, "rows": []})
            seen = day
        groups[-1]["rows"].append(row)
    return {"page": page, "pages": pages, "size": size, "total": total, "groups": groups}


def cursor() -> int:
    with _lock:
        return _seq


class _Tee:
    """Copy what the process writes to its stream into the ring, then write it on unchanged.

    The server prints its startup line, its bind address and its errors to stdout and stderr rather than through a
    logger, so a logger-only tap shows an empty console while the real one has content. This mirrors the stream.
    """

    def __init__(self, stream):
        self._stream = stream
        self._buffer = ""

    def write(self, text):
        try:
            if isinstance(text, str) and text:
                self._buffer += text
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    if line.strip():
                        _append(line, _classify(line))
        except Exception:
            pass
        return self._stream.write(text)

    def flush(self):
        return self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _append(text: str, level: str = "info") -> None:
    global _seq
    with _lock:
        _seq += 1
        row = {"seq": _seq, "at": round(time.time(), 3), "level": level,
               "logger": "console", "text": text[:2000]}
        _rows.append(row)
    _persist(row)


WARN_WORDS = ("warning", "warn:", "deprecat", "retry", "retrying", "slow")
CAUT_WORDS = ("timed out", "timeout", "abandoned", "waiting", "pending", "unavailable", "stalled")
CRIT_WORDS = ("critical", "error", "traceback", "exception", "failed", "refused", "denied", "crash")


def _classify(text: str) -> str:
    """The three levels, read off the line itself — a console line has no record object to consult."""
    low = (text or "").lower()
    if any(word in low for word in CRIT_WORDS):
        return "critical"
    if any(word in low for word in CAUT_WORDS):
        return "caution"
    if any(word in low for word in WARN_WORDS):
        return "warning"
    return "info"


def _stream_level() -> str:
    import sys as _sys
    return "error" if _sys.stderr is getattr(_sys, "__stderr__", None) else "info"


def tap_streams() -> None:
    """Mirror stdout and stderr into the ring exactly once."""
    import sys as _sys
    for name in ("stdout", "stderr"):
        stream = getattr(_sys, name, None)
        if stream is not None and not isinstance(stream, _Tee):
            setattr(_sys, name, _Tee(stream))
