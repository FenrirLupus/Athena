"""Events — the agent activity log (levels 1-2 ONLY, no nurse attention).

The metrics system is for the DOCTOR/NURSE — levels 1-5, where 3/4/5
trigger the nurse's attention for diagnosis and repair. The metric logs
live CENTRALIZED at athena-system/logs/ precisely because the nurse
diagnoses across ALL profiles from one place.

The EVENT system is the AGENT ACTIVITY log — PER-AGENT: every tool call,
skill use, and agent action is recorded in the agent's OWN directory
(events/ subfolder of the profile root). Only levels 1 (Good) and
2 (Notice) — nothing here ever trips the nurse. It is a pure activity
record the curator uses to learn (repeated success → skill, repeated
friction → fix).

Layout (per profile root):
    .athena/events/                          ← default profile
    .athena/profiles/<name>/events/          ← named profiles

ONE file per profile per DAY ({YYYY-MM-DD}_event.log) — the Operator's
cleanup: the old per-session naming spawned a file every second.

JSONL, one object per line (the same shape as metrics):
    {"time", "level"(1|2), "status", "agent", "tool", "action",
     "target", "result"}
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.config import ATHENA_ROOT

_lock = threading.Lock()
_active: dict[str, Path] = {}


def _profile_dir(profile: str) -> Path:
    """The profile's OWN events dir (per-agent, not centralized).

    The AGENT-ROLE names ("assistant", "system") are NOT profiles — they
    are roles inside the DEFAULT profile, so they resolve to the default
    profile's events dir (profiles/.default/events/). Named profiles
    (".nurse", "profile-agent", ...) resolve to their own root. Only a
    truly unknown name falls back to the shared root.
    """
    from intelligence.profiles import get_profile
    # The agent-role aliases: assistant/system/operator → the default profile.
    if profile in ("assistant", "system", "operator", "user"):
        p = get_profile("")
    else:
        p = get_profile(profile)
    if p is not None:
        d = p.root / "events"
    else:
        d = ATHENA_ROOT / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d


def event_log_path(profile: str = "default") -> Path:
    """THE CONSOLIDATED STREAM (the Operator's 08-12 spec): events + logs
    are ONE file now — this returns the {date}_metric.log path (the old
    *_event.log files are retired). Kept for backward-compat callers."""
    from metrics.logger import session_log_path
    return session_log_path(profile)


def close_session(profile: str = "default") -> None:
    """End the session's event file — the next session starts a fresh one."""
    with _lock:
        _active.pop(profile, None)


def log_event(level: int, *, agent: str = "default", tool: str = "runtime",
              action: str = "", target: str = "", result: str = "") -> str:
    """Record an agent activity event.

    THE CONSOLIDATED STREAM (the Operator's 08-12 spec): events + logs
    are ONE stream now. log_event delegates to the metric logger — the
    event fields (agent/tool/action/result) land in the SAME
    {date}_metric.log as the rich terminal entries, carrying the agent
    field. The old *_event.log writer is retired (no dual files).

    level must be 1 (Good) or 2 (Notice) — the event system NEVER logs
    3/4/5. Those belong to the metrics/nurse pipeline. A level outside
    1-2 is clamped to 2.
    """
    level = 2 if int(level) not in (1, 2) else int(level)
    from metrics.logger import log
    return log(level, result, profile=agent, agent=agent, source=agent,
               tool=tool, action=action, target=target)


def read_events(profile: str = "default", limit: int = 50) -> list[dict]:
    """The recent event entries for a profile (newest first).

    Reads the CONSOLIDATED {date}_metric.log (the one stream — the old
    *_event.log files are retired).
    """
    from metrics.logger import read_session, parse_entries
    text = read_session(profile=profile)
    entries = parse_entries(text)
    entries.sort(key=lambda e: e.get("time", ""), reverse=True)
    return entries[:max(1, min(limit, 500))]


def usage_summary(profile: str = "default", since: str = "") -> dict:
    """Aggregate event usage for the curator: {tool: count} and {skill: count}.

    This is the LEARN-BY-DOING view — the curator reads it to decide what
    becomes a skill (repeated success) or a fix (repeated friction).
    """
    counts: dict[str, int] = {}
    for entry in read_events(profile, limit=5000):
        if since and entry.get("time", "") < since:
            continue
        key = entry.get("tool", "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {"profile": profile, "counts": counts, "total": sum(counts.values())}
