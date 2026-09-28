"""Log-location test — the Operator's 08-11 cleanup (id 1536604249646047302):

  • Logs are per-profile: the DEFAULT profile logs at the shared root
    (.athena/logs/), named profiles under profiles/<name>/logs/ — never
    inside athena-system/ (code).
  • ONE file per profile per DAY — the per-session spawning that created
    a file every second-per-process is gone (537 metric files → daily).
  • Events (agent activity) also consolidate to ONE daily file per
    profile — no more file-per-tool-call explosion.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def run() -> list[dict]:
    import core.config as cfg_mod
    from metrics import logger, events

    checks = []

    # ── 1. real layout: logs at the platform root, not in athena-system ──
    checks.append({
        "name": "metrics logs live at .athena/logs (default profile)",
        "status": "ok" if str(logger.LOGS_DIR) == str(cfg_mod.ATHENA_ROOT / "logs")
        else "fail",
        "detail": f"LOGS_DIR = {logger.LOGS_DIR}",
    })
    real = cfg_mod.ATHENA_ROOT / "logs"
    checks.append({
        "name": "real logs dir exists at the platform root",
        "status": "ok" if real.exists() else "fail",
        "detail": f"exists: {real.exists()}",
    })
    code_local = cfg_mod.ATHENA_ROOT / "athena-system" / "logs"
    checks.append({
        "name": "no live log files under athena-system/logs",
        "status": "ok" if not code_local.exists() or not any(
            code_local.rglob("*_metric.log")) else "fail",
        "detail": "migrated to .athena/logs (archive kept in logs-archive/)",
    })

    # ── 2. daily naming: same profile+day reuses ONE file ──
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        original_logs_dir = logger.LOGS_DIR
        original_events_dir = events.ATHENA_ROOT
        try:
            logger.LOGS_DIR = tmp / "logs"
            events.ATHENA_ROOT = tmp  # events fall back to ATHENA_ROOT/events
            p1 = logger.session_log_path("doc-test")
            p2 = logger.session_log_path("doc-test")
            checks.append({
                "name": "same profile+day reuses one metric file",
                "status": "ok" if p1 == p2 else "fail",
                "detail": f"{p1.name} == {p2.name}",
            })
            # A different profile gets its own file.
            p3 = logger.session_log_path("other-agent")
            checks.append({
                "name": "different profiles get different files",
                "status": "ok" if p3 != p1 else "fail",
                "detail": f"{p1.name} vs {p3.name}",
            })
            # Event path also daily (single file per profile+day).
            e1 = events.event_log_path("doc-test")
            e2 = events.event_log_path("doc-test")
            checks.append({
                "name": "event log is one daily file (no per-call spawn)",
                "status": "ok" if e1 == e2 else "fail",
                "detail": f"{e1.name} == {e2.name}",
            })
            # Both writers emit valid JSONL.
            from metrics.logger import log
            log(1, "cleanup check", profile="doc-test", source="doctor",
                action="log_location")
            from metrics.events import log_event
            log_event(1, agent="doc-test", tool="read", action="Check",
                      target="logs", result="ok")
            bad = []
            for f in (p1, e1):
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip():
                        try:
                            json.loads(line)
                        except json.JSONDecodeError:
                            bad.append(f.name)
            checks.append({
                "name": "daily logs are valid JSONL",
                "status": "ok" if not bad else "fail",
                "detail": f"bad files: {bad}" if bad else "all lines parse",
            })
        finally:
            logger.LOGS_DIR = original_logs_dir
            events.ATHENA_ROOT = original_events_dir

    return checks