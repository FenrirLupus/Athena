"""JSONL-safe doctrine test — everything that fits logs; nothing breaks."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def run() -> list[dict]:
    from metrics import logger
    from metrics.logger import _jsonl_safe, log, LOGS_DIR

    checks = []
    original_logs = logger.LOGS_DIR
    with tempfile.TemporaryDirectory() as td:
        logger.LOGS_DIR = Path(td) / "logs"
        # ISOLATE: clear the active-file cache (full-run order safety).
        orig_active = dict(logger._active)
        logger._active.clear()
        try:
            # Coercion: binary → marker (can't fit JSONL, never lost).
            safe = _jsonl_safe(b"\x00\x01binary")
            checks.append({
                "name": "binary marked (not lost)",
                "status": "ok" if safe.startswith("[binary") else "fail",
                "detail": safe[:30],
            })
            # Dict/list → compact JSON.
            safe2 = _jsonl_safe({"a": 1, "b": [1, 2]})
            checks.append({
                "name": "dict serializes to JSON",
                "status": "ok" if safe2 == '{"a": 1, "b": [1, 2]}' else "fail",
                "detail": safe2,
            })
            # Oversized → truncated, line stays valid.
            safe3 = _jsonl_safe("x" * 10000)
            checks.append({
                "name": "oversized truncated",
                "status": "ok" if len(safe3) < 5000 and "+" in safe3 else "fail",
                "detail": f"len={len(safe3)}",
            })
            # Newlines escaped — never breaks the JSONL line.
            safe4 = _jsonl_safe("a\nb\r\nc")
            checks.append({
                "name": "newlines escaped",
                "status": "ok" if "\n" not in safe4 and "\\n" in safe4 else "fail",
                "detail": repr(safe4),
            })
            # Real writes produce valid JSONL for ALL value types.
            log(2, {"op": "backup", "n": 89}, source="data", action="backup")
            log(4, b"\x00\x01raw", source="db", action="read")
            log(5, "x" * 9000, source="security", action="tamper")
            log(1, None, source="test")
            f = next((Path(td) / "logs" / "default").glob("*_metric.log"))
            all_valid = True
            for line in f.read_text().splitlines():
                try:
                    json.loads(line)
                except json.JSONDecodeError:
                    all_valid = False
            checks.append({
                "name": "all log lines valid JSONL",
                "status": "ok" if all_valid else "fail",
                "detail": "binary + huge + dict + none all parse",
            })
        finally:
            logger.LOGS_DIR = original_logs
            logger._active.clear()
            logger._active.update(orig_active)
    return checks
