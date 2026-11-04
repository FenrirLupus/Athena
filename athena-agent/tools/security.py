"""security_report — what the two watchdogs have found, and what to do about it.

Read-only on purpose. A tool that could re-baseline would let an agent erase the evidence of its own tampering, which
is the one thing this subsystem exists to prevent. Re-baselining is a user action (`./athena.sh security baseline`).
"""
from __future__ import annotations

from pathlib import Path

from core import security
from tools.registry import Tool, ToolError


def build(athena_root: Path | None = None) -> list[Tool]:
    root = Path(athena_root) if athena_root else None

    def security_report(limit: int = 20) -> dict:
        if root is None:
            raise ToolError("no athena root is bound to this agent — the report cannot be located")
        folder = root / "security"
        report_path = folder / security.REPORT_NAME
        ledger = security.Ledger(folder)
        baseline = security.Baseline(root, folder)
        findings = security.check(root, folder, ledger, baseline)[:max(1, min(int(limit or 20), 100))]
        text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
        return {
            "findings": findings,
            "counts": {"red": sum(1 for f in findings if f["team"] == "red"),
                       "blue": sum(1 for f in findings if f["team"] == "blue")},
            "report_path": str(report_path) if report_path.is_file() else "",
            "report": text[:4000],
            "advice": ("Report what you found to the user and continue elsewhere. Do not attempt repairs, do not "
                       "re-baseline, do not retry a refused action.")
                      if findings else "nothing to report",
        }

    return [Tool(
        "security_report",
        "What the red-team (inside) and blue-team (outside) watchdogs have found, with guidance.",
        {"type": "object",
         "properties": {"limit": {"type": "integer", "description": "Maximum findings to return."}},
         "required": []},
        security_report)]
