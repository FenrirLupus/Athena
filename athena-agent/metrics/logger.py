"""Metrics logger — 5-level severity logs in JSONL (one JSON object per line).

Layout:
    .athena/logs/<profile>/{YYYY-MM-DD}_metric.log

    Logs live at the SHARED HOUSE ROOT (.athena/logs/) — a globally used
    thing, not code-local: the nurse diagnoses across ALL profiles from
    one place, and the CLI reads them from there too.

    ONE file per profile per DAY — a day's entries append to the same
    file (the old per-session file explosion is gone: a restart no
    longer spawns a new file, and readers glob *_metric.log).

Filename: {YYYY-MM-DD}_metric.log  (daily)

Each line is one JSON object:
    {"time": "...", "level": 1-5, "status": "...", "tool": "...",
     "action": "...", "target": "...", "result": "..."}

Severity levels (the platform's 5-level table):
    1 Good      — everything normal
    2 Notice    — minor event, worth recording
    3 Warning   — unexpected, attention may be needed
    4 Error     — significant problem, recovery required
    5 Critical  — severe failure, operation cannot continue

Status maps to level: 1→SUCCESS, 2→INFO, 3→WARNING, 4→ERROR, 5→CRITICAL
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from core.config import ATHENA_ROOT

# The legacy shared root (kept for import-compat). Per-profile logs now
# live under the PROFILE's OWN root (profiles/<name>/logs/) — the Operator's
# per-profile Events/Logs layout (08-12). _profile_dir resolves the
# profile root; the shared root remains only as the default fallback.
LOGS_DIR = ATHENA_ROOT / "logs"

LEVELS = {
    1: "Good",
    2: "Notice",
    3: "Warning",
    4: "Error",
    5: "Critical",
}

STATUS = {
    1: "SUCCESS",
    2: "INFO",
    3: "WARNING",
    4: "ERROR",
    5: "CRITICAL",
}

# Severity colors (the Operator's spec): the level's importance at a glance.
#    1 Green · 2 Blue · 3 Yellow · 4 Orange · 5 Red
SEVERITY_COLORS = {
    1: "green",
    2: "blue",
    3: "yellow",
    4: "orange",
    5: "red",
}

_lock = threading.Lock()
# Per-profile active file path — one file per server session.
_active: dict[str, Path] = {}


def _profile_dir(profile: str) -> Path:
    """The profile's OWN logs dir (per-agent, not centralized).

    The Operator's 08-12 per-profile layout: named profiles log under
    profiles/<name>/logs/; the default profile keeps .athena/logs/
    (its root IS the profile root). Mirrors the events layout.

    THE ROLE-ALIAS ROUTING (the 08-12 fix): the role names
    ("assistant", "system", "user") are NOT profiles — they are roles
    inside the DEFAULT profile, so they resolve to the default profile's
    logs dir. Without this, their tool calls landed in
    .athena/logs/<role>/ and the console/terminal missed them (the
    cross-diagnosis gap the Operator caught).

    TEST ISOLATION: when a doctor test patches LOGS_DIR to a tempdir,
    honor the patch (write there) — the per-profile resolution only
    applies when LOGS_DIR is the production default.
    """
    try:
        from core.config import ATHENA_ROOT as _root
        _default = _root / "logs"
        if LOGS_DIR != _default:
            # A test (or explicit config) redirected the log root.
            d = LOGS_DIR / (profile or "default")
            d.mkdir(parents=True, exist_ok=True)
            return d
        # THE ROLE ALIASES → the DEFAULT profile (the 08-12 fix).
        if profile in ("assistant", "system", "user", "operator"):
            from intelligence.profiles import default_profile
            d = default_profile().root / "logs"
            d.mkdir(parents=True, exist_ok=True)
            return d
        from intelligence.profiles import get_profile
        p = get_profile(profile)
        # The profile's OWN root counts ONLY when it lives under the
        # CURRENT root — a test that patches ATHENA_ROOT to a tempdir
        # must not resolve to the real profile dir.
        if p is not None and p.root.resolve().is_relative_to(_root.resolve()):
            d = p.root / "logs"
        else:
            d = _default / (profile or "default")
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        d = LOGS_DIR / (profile or "default")
        d.mkdir(parents=True, exist_ok=True)
        return d


def session_log_path(profile: str = "default") -> Path:
    """The ACTIVE daily log file for a profile.

    ONE file per profile per day: every caller that day appends to the
    same {YYYY-MM-DD}_metric.log. No per-restart spawning, no -1 split.
    Readers glob *_metric.log so the shape stays stable.
    """
    with _lock:
        today = datetime.now().strftime("%Y-%m-%d")
        cached = _active.get(profile)
        if cached is not None and cached.name.startswith(today) \
                and cached.parent.exists():
            return cached
        d = _profile_dir(profile)
        path = d / f"{today}_metric.log"
        _active[profile] = path
        return path


def close_session(profile: str = "default") -> None:
    """End the session's log file — the next log starts a fresh one.

    Idempotent: closing an already-closed session is a no-op. Only the
    SESSION OWNER should call this (the standalone server, or the CLI
    when it runs the server beside it) — so a session always maps to
    exactly ONE metric log + ONE event log.
    """
    with _lock:
        _active.pop(profile, None)
    try:
        from metrics.events import close_session as close_events
        close_events(profile)
    except Exception:
        pass


def log(level: int, result: str, *, profile: str = "default",
        source: str = "runtime", tool: str = "runtime",
        action: str = "", target: str = "", agent: str = "") -> str:
    """Write one JSONL metric entry to the session's log. Returns the path.

    THE CONSOLIDATED STREAM (the Operator's 08-12 spec): ONE stream per
    profile — the old events (console) + logs (terminal) are now ONE
    {date}_metric.log. The entry carries the event fields (agent/tool/
    action) AND the rich detail (source/result) in a single line, plus
    the code + reason the listener extracts.

    The METRICS LISTENER (the .mkv recorder): every L3+ entry is
    auto-classified — an HTTP status (404/401/500/429) or a Python
    exception name (TimeoutError/KeyError/...) is extracted as `code`,
    and the error classifier's category (transient/config/resource/
    logic) becomes `reason`. No call site needs to pass them explicitly;
    the listener catches everything that flows through.
    """
    level = int(level)
    if level not in LEVELS:
        level = 1
    now = datetime.now(timezone.utc)
    entry = {
        "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": level,
        "status": STATUS.get(level, "INFO"),
        "source": _jsonl_safe(source),   # server | cli | gui | runtime | nurse | curator
        "agent": _jsonl_safe(agent) or _jsonl_safe(source),
        "tool": _jsonl_safe(tool or source),
        "action": _jsonl_safe(action),
        "target": _jsonl_safe(target),
        "result": _jsonl_safe(result),
    }
    # THE METRICS LISTENER (the Operator's 08-12 spec): extract the
    # error code + reason from L3+ entries automatically.
    if level >= 3:
        code, reason = _extract_code_reason(result)
        if code:
            entry["code"] = code
        if reason:
            entry["reason"] = reason
    path = session_log_path(profile)
    with _lock:
        # Defensive: the dir may have been removed while the server runs
        # (e.g. a manual cleanup) — never crash the writer for that.
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        # THE ROOT AGGREGATE (the Operator's 08-12 spec): every write
        # ALSO appends to .athena/logs/{date}_metric.log — the appended
        # version of all profiles' logs in ONE file (the Developer
        # Terminal reads this). ONLY when writing to the PRODUCTION
        # default — a redirected LOGS_DIR (doctor test isolation) must
        # never leak test entries into the real aggregate (the
        # doctor-test residue the Operator caught).
        try:
            from core.config import ATHENA_ROOT
            _default_logs = ATHENA_ROOT / "logs"
            if LOGS_DIR == _default_logs:
                agg = ATHENA_ROOT / "logs" / path.name
                agg.parent.mkdir(parents=True, exist_ok=True)
                with open(agg, "a", encoding="utf-8") as fh2:
                    fh2.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass  # the aggregate is best-effort — never break the write
    return str(path)


def purge_entries(tool: str = "", needle: str = "",
                  profile: str = "default") -> int:
    """Remove resolved-failure entries from the metric logs (the 08-14
    fix): when a tool loads successfully, its past 'failed to load'
    entries are purged so the nurse never re-flags a fixed bug.

    Returns how many entries were removed. Safe: only lines containing
    the needle (and tool, if given) are dropped; the file is rewritten
    in place under the lock.
    """
    removed = 0
    path = session_log_path(profile)
    targets = [path]
    # THE ROOT AGGREGATE (the 08-14 fix): the same resolved-failure
    # entries were appended to .athena/logs/ — purge them there too.
    try:
        from core.config import ATHENA_ROOT
        agg = ATHENA_ROOT / "logs" / f"{path.stem}.log"
        if agg.exists() and agg != path:
            targets.append(agg)
    except Exception:
        pass
    for target in targets:
        if not target.exists():
            continue
        try:
            with _lock:
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                kept = []
                for line in lines:
                    # Match the needle in the result AND the tool name in
                    # the LINE (the result text carries the py.name, e.g.
                    # "builtin tool failed to load timeline_query.py: ...").
                    if (needle and needle in line
                            and (not tool or tool in line)):
                        removed += 1
                        continue
                    kept.append(line)
                if removed:
                    target.write_text("\n".join(kept) + ("\n" if kept else ""),
                                      encoding="utf-8")
        except Exception:
            continue
    return removed


# THE CODE/REASON EXTRACTION (the Operator's 08-12 spec): a small
# pattern listener that pulls the error code + classified reason out of
# any message. HTTP statuses and Python exception names are recognized;
# the error classifier's category labels the reason.
_HTTP_CODE = re.compile(r"\b(?:HTTP\s*)?([45]\d\d)\b")
_PY_EXC = re.compile(
    r"\b([A-Z][A-Za-z]*(?:Error|Exception|Interrupt|Exit))\b")


def _extract_code_reason(result: str) -> tuple[str, str]:
    """(code, reason) from a message — empty when nothing matches."""
    text = str(result or "")
    code = ""
    m = _HTTP_CODE.search(text)
    if m:
        code = m.group(1)
    else:
        m = _PY_EXC.search(text)
        if m:
            code = m.group(1)
    try:
        from core.error_classifier import describe
        d = describe(text)
        reason = d.get("label", "")
    except Exception:
        reason = ""
    return code, reason


# JSONL-safe coercion: everything that can fit the format is logged;
# everything that can't is represented (truncated / marked) — never lost,
# never breaking the line.
_MAX_FIELD = 4000  # per-field cap — keeps a line parseable and lean


def _jsonl_safe(value) -> str:
    """Coerce any value into a JSONL-safe string.

    - dict/list: compact JSON (truncated)
    - bytes: binary marker + length (raw bytes don't fit JSONL)
    - None/bool/int/float: str()
    - objects: str()
    - oversized: truncated with a marker
    """
    try:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, bytes):
            return f"[binary {len(value)} bytes]"
        if isinstance(value, (dict, list)):
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                text = str(value)
        else:
            text = str(value)
        # Sanitize control chars that would break a JSONL line.
        text = text.replace("\n", "\\n").replace("\r", "\\r")
        if len(text) > _MAX_FIELD:
            return text[:_MAX_FIELD] + f"...[+{len(text) - _MAX_FIELD} chars]"
        return text
    except Exception:
        return "[unserializable]"


def read_session(profile: str = "default", path: str = "") -> str:
    """Read a session's log file (full text)."""
    p = Path(path) if path else session_log_path(profile)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def colorize_level(level: int, text: str = "") -> str:
    """Color a level's label per severity (1 green … 5 red). Plain when
    output isn't a TTY (NO_COLOR etc.). The nurse reads the color too."""
    from cli.colors import green, blue, yellow, orange, red

    level = int(level)
    text = text or LEVELS.get(level, f"L{level}")
    paint = {1: green, 2: blue, 3: yellow, 4: orange, 5: red}.get(level)
    return paint(text) if paint else text


def parse_entries(text: str) -> list[dict]:
    """Parse JSONL lines into entry dicts. Skips blank/invalid lines."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        level = obj.get("level")
        try:
            level = int(level)
        except (TypeError, ValueError):
            continue
        if level not in LEVELS:
            continue
        entries.append({
            "level": level,
            "name": LEVELS.get(level, "?"),
            "status": obj.get("status", ""),
            "source": obj.get("source", ""),
            "agent": obj.get("agent", ""),
            "tool": obj.get("tool", ""),
            "action": obj.get("action", ""),
            "target": obj.get("target", ""),
            "code": obj.get("code", ""),
            "reason": obj.get("reason", ""),
            "message": obj.get("result", ""),
            "time": obj.get("time", ""),
        })
    return entries
