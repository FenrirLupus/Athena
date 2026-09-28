"""Events test — agent activity log (levels 1-2 only), never trips nurse."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def run() -> list[dict]:
    from metrics import events, nurse_watch
    from metrics.events import log_event, read_events, usage_summary, event_log_path

    checks = []
    with tempfile.TemporaryDirectory() as td:
        # Isolate: patch the LOGGER's log root to a tempdir (the
        # documented isolation hook — metrics.logger._profile_dir honors
        # a redirected LOGS_DIR before resolving profiles). The
        # consolidated stream writes under LOGS_DIR/<profile>/.
        from metrics import logger as logger_mod
        import metrics.logger as L
        orig_logs_dir = L.LOGS_DIR
        L.LOGS_DIR = Path(td) / "logs"
        try:
            # Levels 1 and 2 record fine.
            log_event(1, agent="doctor-test", tool="read", action="Read file",
                      target="config.yaml", result="File loaded successfully")
            log_event(2, agent="doctor-test", tool="search", action="Search directory",
                      target="/project", result="Found 15 files")
            entries = read_events("doctor-test")
            checks.append({
                "name": "events 1-2 recorded",
                "status": "ok" if len(entries) == 2 else "fail",
                "detail": f"{len(entries)} entries",
            })
            # A 3/4/5 attempt is CLAMPED to 2 (never escapes to metrics).
            log_event(5, agent="doctor-test", tool="kill",
                      action="Attempt critical", target="/system",
                      result="should not be critical")
            entries = read_events("doctor-test")
            levels = {e["level"] for e in entries}
            checks.append({
                "name": "no level 3/4/5 in events (clamped to 2)",
                "status": "ok" if not (levels & {3, 4, 5}) else "fail",
                "detail": f"levels={sorted(levels)}",
            })
            # The NURSE does not watch events — only metrics 3/4/5.
            # Simulate: point the nurse watch at the events dir and confirm
            # no attention (events are never 3/4/5).
            orig_logs_dir = nurse_watch.LOGS_DIR
            nurse_watch.LOGS_DIR = Path(td) / "logs" / "doctor-test"
            try:
                from metrics.nurse_watch import check_logs, reset_watch
                reset_watch()
                result = check_logs()
                checks.append({
                    "name": "nurse never attends to events",
                    "status": "ok" if result["ok"] else "fail",
                    "detail": f"ok={result['ok']} max={result['max_level']}",
                })
            finally:
                nurse_watch.LOGS_DIR = orig_logs_dir
            # JSONL shape + per-profile dir + filename format.
            # THE CONSOLIDATED STREAM (the Operator's 08-12 spec): events
            # now live in the {date}_metric.log (the ONE stream), carrying
            # the agent field alongside the rich fields.
            import re
            path = event_log_path("doctor-test")
            ok_name = re.match(r"\d{4}-\d{2}-\d{2}_metric\.log$", path.name)
            checks.append({
                "name": "event filename format YYYY-MM-DD_metric.log (consolidated)",
                "status": "ok" if ok_name else "fail",
                "detail": path.name,
            })
            first = json.loads(path.read_text().splitlines()[0])
            ok_schema = all(k in first for k in
                            ("time", "level", "status", "agent", "tool", "action", "target", "result"))
            checks.append({
                "name": "event JSONL schema (consolidated)",
                "status": "ok" if ok_schema else "fail",
                "detail": f"keys={sorted(first.keys())}",
            })
            # Usage summary (the curator's learn-by-doing view).
            s = usage_summary("doctor-test")
            checks.append({
                "name": "usage summary aggregates",
                "status": "ok" if s["total"] == 3 and s["counts"].get("read") == 1 else "fail",
                "detail": f"total={s['total']} counts={s['counts']}",
            })
        finally:
            L.LOGS_DIR = orig_logs_dir
    return checks
