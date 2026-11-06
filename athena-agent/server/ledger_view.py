"""Reading the security ledger for the interface: one ordered view, in the shape the interface consumes.

The contract (plan §8):

    {"cursor": <float>, "truncated": <bool>,
     "rows": [{"id", "at" (ISO), "at_epoch", "level", "team", "kind", "path", "lane", "text"}]}

`truncated` says the caller asked for fewer rows than matched — without it a short answer is indistinguishable from a
quiet system. `limit` outside 1-500 is refused rather than clamped: a caller asking for zero rows wants an error, not
one row. `within` limits the window in seconds; `min_level` filters by severity, and `level` is derived here so the
terminal, the bell and any other reader agree on what a kind means.

This module never writes. A reader that could write could erase the record of its own tampering.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from core import timefmt

MAX_ROWS = 4000
DEFAULT_LIMIT = 200
LIMIT_MAX = 500
LEVELS = ("info", "warn", "bad")
MAX_WINDOW_S = 7 * 24 * 3600


def level_of(kind: str) -> str:
    """The severity a kind implies, in one place so every reader classifies a row the same way."""
    text = str(kind or "").lower()
    if any(word in text for word in ("403", "401", "refus", "tamper", "red", "escape", "denied")):
        return "bad"
    if any(word in text for word in ("warn", "429", "stale", "drift", "storm")):
        return "warn"
    return "info"


def _row_id(row: dict) -> str:
    basis = "|".join(str(row.get(key) or "") for key in ("team", "kind", "path", "at"))
    return hashlib.sha256(basis.encode()).hexdigest()[:12]


def _iso(epoch: float) -> str:
    return timefmt.stamp_from(epoch)


def read_events(security_dir: Path | str, since: float | None = None, limit: int = DEFAULT_LIMIT,
                within: int | None = None, min_level: str = "info") -> dict:
    """Ledger rows newer than `since`, oldest first, in the documented shape."""
    if limit is None:
        limit = DEFAULT_LIMIT
    if not 1 <= int(limit) <= LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {LIMIT_MAX}")
    if since is not None and since < 0:
        raise ValueError("since must be zero or a unix timestamp")
    if within is not None and not 0 < int(within) <= MAX_WINDOW_S:
        raise ValueError(f"within must be between 1 and {MAX_WINDOW_S} seconds")
    wanted = str(min_level or "info").lower()
    if wanted not in LEVELS:
        raise ValueError(f"min_level must be one of {', '.join(LEVELS)}")

    path = Path(security_dir) / "events.jsonl"
    floor = None
    if within is not None:
        floor = datetime.now(tz=timezone.utc).timestamp() - int(within)

    rows: list[dict] = []
    skipped = 0
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-MAX_ROWS:]
        except OSError:
            lines = []
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except ValueError:
                skipped += 1
                continue
            if not isinstance(raw, dict):
                skipped += 1
                continue
            at_epoch = float(raw.get("at") or 0)
            if since is not None and at_epoch <= since:
                continue
            if floor is not None and at_epoch < floor:
                continue
            level = level_of(raw.get("kind"))
            if LEVELS.index(level) < LEVELS.index(wanted):
                continue
            rows.append({
                "id": _row_id(raw),
                "at": _iso(at_epoch),
                "at_epoch": at_epoch,
                "level": level,
                "team": raw.get("team") or "",
                "kind": raw.get("kind") or "",
                "path": raw.get("path") or "",
                "lane": raw.get("lane") or "",
                "text": raw.get("detail") or raw.get("kind") or "",
            })

    rows.sort(key=lambda row: row["at_epoch"])
    truncated = len(rows) > int(limit)
    rows = rows[-int(limit):]
    cursor = rows[-1]["at_epoch"] if rows else (since or 0)
    return {"cursor": cursor, "truncated": truncated, "rows": rows, "skipped": skipped}


def turn_rows(profile_dir: Path | str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """The newest turn measurements for one lane, oldest first — the terminal's second source."""
    if not 1 <= int(limit) <= LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {LIMIT_MAX}")
    path = Path(profile_dir) / "metrics" / "turns.jsonl"
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_ROWS:]
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows[-int(limit):]


def timeline(events: list[dict], turns: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Merge the ledger and the turn log into one list ordered by a numeric key, each row naming its source."""
    def epoch(value) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError):
            return 0.0

    merged = [{"source": "ledger", "at_epoch": float(row.get("at_epoch") or 0), **row} for row in events]
    merged += [{"source": "turn", "at_epoch": epoch(row.get("ts")), **row} for row in turns]
    merged.sort(key=lambda row: row["at_epoch"])
    return merged[-max(1, int(limit)):]
