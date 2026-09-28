"""Metrics surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every metrics submodule's
checks (cli logging, events, jsonl-safe, logger, coverage, nurse watch,
log location) and merges them into a single report. Check names are
preserved 1:1 — the doctor count and the nurse's failure tracking stay
stable across consolidation.
"""
from __future__ import annotations

from pathlib import Path
import json
import tempfile

# The submodules this composer runs (renamed _sub_* so discovery sees
# only THIS module — one test module per category).
def _chk_cli_logging() -> list[dict]:
    from metrics import logger
    from metrics.logger import log as metric_log, LOGS_DIR

    checks = []
    original_logs = logger.LOGS_DIR
    with tempfile.TemporaryDirectory() as td:
        logger.LOGS_DIR = Path(td) / "logs"
        # ISOLATE: clear the per-profile active-file cache so log() opens
        # a NEW file in the patched dir (the full doctor run may have
        # already bound the real path).
        orig_active = dict(logger._active)
        logger._active.clear()
        import core.config
        original_root = core.config.ATHENA_ROOT
        core.config.ATHENA_ROOT = Path(td)
        try:
            # The CLI's repl() writes these three entries (start server,
            # start cli, end cli). Simulate the lifecycle.
            metric_log(1, "server session started", profile="default", source="server_loop")
            metric_log(1, "cli session started", profile="default", source="cli")
            metric_log(1, "cli session ended", profile="default", source="cli")
            logs = sorted((Path(td) / "logs" / "default").glob("*_metric.log"))
            checks.append({
                "name": "cli session writes metric log",
                "status": "ok" if logs else "fail",
                "detail": f"{len(logs)} log file(s)",
            })
            if logs:
                text = logs[0].read_text()
                entries = [json.loads(l) for l in text.splitlines() if l.strip()]
                results = [e.get("result", "") for e in entries]
                has_start = any("server session started" in r for r in results)
                has_cli_start = any("cli session started" in r for r in results)
                checks.append({
                    "name": "server + cli start logged",
                    "status": "ok" if has_start and has_cli_start else "fail",
                    "detail": f"results={results}",
                })
        finally:
            logger.LOGS_DIR = original_logs
            logger._active.clear()
            logger._active.update(orig_active)
            core.config.ATHENA_ROOT = original_root
    return checks


_SUBMODULES = [
    "cli_logging",
    "events",
    "jsonl_safe",
    "log_location",
    "logger",
    "logging_coverage",
    "nurse_watch",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.metrics._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        # Inline (folded) checks run directly; file-backed ones import.
        inline = globals().get(f"_chk_{name}")
        if inline is not None:
            try:
                checks.extend(inline())
            except Exception as exc:
                checks.append({
                    "name": f"metrics/{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:
            checks.append({
                "name": f"metrics/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks
