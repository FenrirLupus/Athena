"""security_report — what the two watchdogs have found, and what to do about it.

Read-only on purpose. A tool that could re-baseline would let an agent erase the evidence of its own tampering, which
is the one thing this subsystem exists to prevent. Re-baselining is a user action (`./athena.sh security baseline`).
"""
from __future__ import annotations

from pathlib import Path

from core import security, trace
from tools.registry import Tool, ToolError

LANE = "security"


def _under(path: Path, parent: Path) -> bool:
    """Whether `path` resolves inside `parent` — the tree a report is allowed to describe."""
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError):
        return False


def build(ctx=None) -> list[Tool]:
    """The security report tool; `ctx` supplies the athena root the watchdogs wrote their findings under."""
    from tools.loader import agent_roots

    profile_root, athena_root = agent_roots(ctx)
    root = athena_root


    def security_report(limit: int = 20) -> dict:
        if root is None:
            trace.event("security.refused", level="error", channel=LANE, rule="no-root",
                        reason="no athena root is bound to this agent")
            raise ToolError("no athena root is bound to this agent — the report cannot be located")
        if profile_root is not None and not _under(profile_root, root):
            # scanning a tree the agent does not live in would report someone else's findings as its own
            trace.event("security.refused", level="error", channel=LANE, rule="outside-tree",
                        root=str(root), profile=str(profile_root))
            raise ToolError(f"refusing to scan {root}: this agent's profile lives at {profile_root}, "
                            f"outside that tree")
        folder = root / "security"
        report_path = folder / security.REPORT_NAME
        kept = max(1, min(int(limit or 20), 100))
        with trace.span("security.report", channel=LANE, root=str(root), limit=kept) as sp:
            ledger = security.Ledger(folder)
            baseline = security.Baseline(root, folder)
            findings = security.check(root, folder, ledger, baseline)[:kept]
            text = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
            counts = {"red": sum(1 for f in findings if f["team"] == "red"),
                      "blue": sum(1 for f in findings if f["team"] == "blue")}
            sp.ok(findings=len(findings), red=counts["red"], blue=counts["blue"], report_bytes=len(text))
        return {
            "findings": findings,
            "counts": counts,
            "report_path": str(report_path) if report_path.is_file() else "",
            "report": text[:4000],
            "advice": ("Report what you found to the user and continue elsewhere. Do not attempt repairs, do not "
                       "re-baseline, do not retry a refused action.")
                      if findings else "nothing to report",
        }

    return [Tool("report",
        "What the red-team (inside) and blue-team (outside) watchdogs have found, with guidance.",
        {"type": "object",
         "properties": {"limit": {"type": "integer", "description": "Maximum findings to return."}},
         "required": []},
        security_report)]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "security")
