"""Nurse watcher test — change-detecting, levels 3/4/5 only."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from metrics import logger, nurse_watch
    from metrics.nurse_watch import check_logs, changed_files, reset_watch

    checks = []
    original = logger.LOGS_DIR
    with tempfile.TemporaryDirectory() as td:
        logger.LOGS_DIR = Path(td)
        logger._active.clear()
        nurse_watch.LOGS_DIR = Path(td)
        try:
            # Healthy log: only levels 1-2 → NO attention.
            logger.log(1, "good", profile="doctor-test")
            logger.log(2, "notice", profile="doctor-test")
            reset_watch()
            result = check_logs()
            checks.append({
                "name": "levels 1-2 need no attention",
                "status": "ok" if result["ok"] and not result["attention"] else "fail",
                "detail": f"ok={result['ok']} max={result['max_level']}",
            })

            # Warning 3 → attention.
            logger.log(3, "warning something odd", profile="doctor-test")
            result = check_logs()
            checks.append({
                "name": "level 3 gets attention",
                "status": "ok" if not result["ok"] and len(result["attention"]) >= 1 else "fail",
                "detail": f"max={result['max_level']} attention={len(result['attention'])}",
            })

            # Critical 5 → attention with the entry.
            logger.log(5, "critical failure", profile="doctor-test")
            result = check_logs()
            entries = result["attention"][0]["entries"] if result["attention"] else []
            worst = max(e["level"] for e in entries) if entries else 0
            checks.append({
                "name": "critical captured with entry",
                "status": "ok" if worst == 5 else "fail",
                "detail": f"worst={worst}",
            })

            # Change-detection: unchanged files don't re-flag.
            reset_watch()
            check_logs()  # baseline scan
            result2 = check_logs()  # nothing changed → no attention
            checks.append({
                "name": "unchanged logs are free",
                "status": "ok" if result2["ok"] else "fail",
                "detail": f"ok={result2['ok']} (no new writes)",
            })
        finally:
            logger.LOGS_DIR = original
            nurse_watch.LOGS_DIR = original
            logger._active.clear()
            reset_watch()
    return checks
