"""Terminal log tailer — web mode turns the terminal into the metrics logger.

When Athena runs as a 24/7 web server, the terminal is no longer the CLI
— it becomes the METRICS LOGGER: every metric-log entry (all 5 levels) is
printed live to the terminal as it happens, so the operator watching the
terminal sees exactly what the platform is doing.

This is a change-detecting tail (the nurse_watch pattern): we poll the
log file's size, read new lines, and print them. Levels are colorized
(1-2 dim, 3 yellow, 4 red, 5 bold red) so attention pops.
"""
from __future__ import annotations

import time
from pathlib import Path

from metrics.logger import session_log_path, colorize_level


def _colorize_entry(entry_text: str) -> str:
    """Colorize a raw JSONL entry by its level."""
    try:
        import json
        obj = json.loads(entry_text)
        level = int(obj.get("level", 0) or 0)
        return colorize_level(level, entry_text)
    except Exception:
        return entry_text


def tail_forever(profile: str = "default", *, interval_s: float = 1.0,
                 stop_event=None) -> None:
    """Print every new metric-log entry to stdout, forever.

    The web-mode terminal logger. Starts from the CURRENT end of the log
    (past entries aren't replayed — the terminal shows what happens NOW).
    """
    seen = 0
    try:
        # Find the current log file; anchor at its current end.
        path: Path = session_log_path(profile)
        try:
            seen = path.stat().st_size
        except OSError:
            seen = 0
    except Exception:
        path = Path("_metric.log")

    print(f"[athena] terminal = metrics logger (profile: {profile}) — "
          f"live stream on")
    while stop_event is None or not stop_event.is_set():
        try:
            if path.exists():
                size = path.stat().st_size
                if size > seen:
                    with path.open("r", encoding="utf-8") as f:
                        f.seek(seen)
                        new = f.read()
                    seen = size
                    for line in new.splitlines():
                        if line.strip():
                            print(_colorize_entry(line), flush=True)
        except Exception:
            pass
        time.sleep(interval_s)
