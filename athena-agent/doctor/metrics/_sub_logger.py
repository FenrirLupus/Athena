"""Metrics logger test — JSONL 5-level, per-profile, session rotation."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def run() -> list[dict]:
    from metrics import logger
    from metrics.logger import LEVELS, STATUS

    checks = []
    original = logger.LOGS_DIR
    with tempfile.TemporaryDirectory() as td:
        logger.LOGS_DIR = Path(td)
        logger._active.clear()
        try:
            logger.log(1, "File loaded successfully", profile="doctor-test",
                       tool="read", action="Read file", target="config.json")
            logger.log(2, "Found 15 matching files", profile="doctor-test",
                       tool="search", action="Search directory", target="/project")
            logger.log(3, "File exists, overwriting", profile="doctor-test",
                       tool="write", action="Modify file", target="settings.json")
            logger.log(5, "Permission denied, agent halted", profile="doctor-test",
                       tool="filesystem", action="Access directory", target="/system")
            p = logger.session_log_path("doctor-test")
            checks.append({
                "name": "log writes file",
                "status": "ok" if p.exists() else "fail",
                "detail": p.name,
            })
            import re
            ok_name = re.match(r"\d{4}-\d{2}-\d{2}_metric\.log$", p.name)
            checks.append({
                "name": "filename format YYYY-MM-DD_metric.log (daily)",
                "status": "ok" if ok_name else "fail",
                "detail": p.name,
            })
            checks.append({
                "name": "per-profile subfolder",
                "status": "ok" if p.parent.name == "doctor-test" else "fail",
                "detail": p.parent.name,
            })
            checks.append({
                "name": "5 severity levels + statuses",
                "status": "ok" if len(LEVELS) == 5 and len(STATUS) == 5
                and STATUS[5] == "CRITICAL" else "fail",
                "detail": f"levels={list(LEVELS.values())}",
            })
            # JSONL: one JSON object per line, exact schema.
            lines = p.read_text().strip().splitlines()
            checks.append({
                "name": "one JSON object per line",
                "status": "ok" if len(lines) == 4 else "fail",
                "detail": f"{len(lines)} lines",
            })
            first = json.loads(lines[0])
            ok_schema = all(k in first for k in
                            ("time", "level", "status", "source", "tool", "action", "target", "result"))
            checks.append({
                "name": "JSONL schema fields",
                "status": "ok" if ok_schema else "fail",
                "detail": f"keys={sorted(first.keys())}",
            })
            checks.append({
                "name": "source field tagged",
                "status": "ok" if first.get("source") in ("server", "cli", "gui", "runtime", "nurse", "curator", "test") else "fail",
                "detail": f"source={first.get('source')}",
            })
            entries = logger.parse_entries(p.read_text())
            worst = max(e["level"] for e in entries)
            checks.append({
                "name": "severity parsed",
                "status": "ok" if worst == 5 else "fail",
                "detail": f"worst={worst}",
            })
            # Status matches level.
            ok_status = all(STATUS[e["level"]] == e["status"] for e in entries)
            checks.append({
                "name": "status matches level",
                "status": "ok" if ok_status else "fail",
                "detail": "",
            })
            # ONE FILE PER PROFILE PER DAY (the Operator's 08-11 cleanup):
            # close + reopen the SAME day stays in the same file. The old
            # per-second spawning is gone — a restart no longer spawns a
            # fresh file.
            old = p
            logger.close_session("doctor-test")
            p2 = logger.session_log_path("doctor-test")
            checks.append({
                "name": "day stays in one file (no per-session spawn)",
                "status": "ok" if p2 == old else "fail",
                "detail": f"{old.name} → {p2.name} (same = correct)",
            })
            # A fresh day starts a new file (simulated by clearing the
            # cache — the next call re-resolves today's file).
            logger._active.clear()
            logger.close_session("doctor-test")
            p4 = logger.session_log_path("doctor-test")
            checks.append({
                "name": "cache clear still yields the daily file",
                "status": "ok" if p4 == old else "fail",
                "detail": f"{old.name} → {p4.name}",
            })
        finally:
            logger.LOGS_DIR = original
            logger._active.clear()
    return checks
