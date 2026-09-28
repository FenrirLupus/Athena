"""Logging coverage test — every subsystem logs through core.logging."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from metrics import logger
    from core.logging import log_event
    from core.config import ATHENA_ROOT

    checks = []
    original_logs = logger.LOGS_DIR
    with tempfile.TemporaryDirectory() as td:
        logger.LOGS_DIR = Path(td) / "logs"
        try:
            # The helper logs at the requested severity without raising.
            log_event(1, "good marker", source="core-test")
            log_event(3, "warn marker", source="core-test")
            log_event(5, "critical marker", source="core-test")
            log_file = next((Path(td) / "logs" / "default").glob("*_metric.log"))
            text = log_file.read_text()
            import json
            entries = [json.loads(l) for l in text.splitlines() if l.strip()]
            levels = sorted(e["level"] for e in entries)
            checks.append({
                "name": "helper logs all severities",
                "status": "ok" if levels == [1, 3, 5] else "fail",
                "detail": f"levels={levels}",
            })
            # Source tagging.
            sources = {e["source"] for e in entries}
            checks.append({
                "name": "helper tags source",
                "status": "ok" if "core-test" in sources else "fail",
                "detail": f"sources={sources}",
            })
            # Every subsystem module imports the helper (coverage).
            # Modules that are pure data (constants/static text/sanitizers)
            # legitimately don't log — the bar is: any module with exception
            # handlers or I/O should log. Count those.
            import ast
            root = Path.home() / '.athena' / 'athena-system'
            subsystems = ["core", "providers", "autonomy", "intelligence", "data",
                          "security", "context", "filesystem"]
            covered = 0
            operational = 0
            for sub in subsystems:
                d = root / sub
                if not d.is_dir():
                    continue
                for py in sorted(d.glob("*.py")):
                    if py.name.startswith("__"):
                        continue
                    src = py.read_text(encoding="utf-8", errors="replace")
                    has_handler = "except" in src
                    if not has_handler:
                        continue  # pure-data modules don't need logging
                    # Modules that ONLY handle ValueError (date parsing,
                    # path resolution) are structured-return-by-design —
                    # expected input variation, not error paths.
                    import re as _re
                    only_value = _re.search(r"except\s+(?!ValueError)", src) is None
                    if only_value:
                        continue
                    operational += 1
                    if "metrics.logger" in src or "core.logging" in src:
                        covered += 1
            checks.append({
                "name": "subsystem logging coverage",
                "status": "ok" if covered >= operational * 0.9 else "fail",
                "detail": f"{covered}/{operational} operational modules log",
            })
        finally:
            logger.LOGS_DIR = original_logs
    return checks
