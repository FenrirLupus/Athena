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
from core import timefmt, trace

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
    # a refusal is a decision, and it is the one a caller sees: each says which rule bound it
    if not 1 <= int(limit) <= LIMIT_MAX:
        trace.event("ledger.refused", level="error", channel="ledger", arg="limit", value=limit,
                    rule=f"1..{LIMIT_MAX}")
        raise ValueError(f"limit must be between 1 and {LIMIT_MAX}")
    if since is not None and since < 0:
        trace.event("ledger.refused", level="error", channel="ledger", arg="since", value=since,
                    rule="zero or a unix timestamp")
        raise ValueError("since must be zero or a unix timestamp")
    if within is not None and not 0 < int(within) <= MAX_WINDOW_S:
        trace.event("ledger.refused", level="error", channel="ledger", arg="within", value=within,
                    rule=f"1..{MAX_WINDOW_S}s")
        raise ValueError(f"within must be between 1 and {MAX_WINDOW_S} seconds")
    wanted = str(min_level or "info").lower()
    if wanted not in LEVELS:
        trace.event("ledger.refused", level="error", channel="ledger", arg="min_level", value=wanted,
                    rule=", ".join(LEVELS))
        raise ValueError(f"min_level must be one of {', '.join(LEVELS)}")

    path = Path(security_dir) / "events.jsonl"
    floor = None
    if within is not None:
        floor = datetime.now(tz=timezone.utc).timestamp() - int(within)

    rows: list[dict] = []
    skipped = 0
    with trace.span("ledger.read", channel="ledger", limit=int(limit), since=since,
                    within=within, min_level=wanted) as sp:
        if path.is_file():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()[-MAX_ROWS:]
            except OSError as exc:
                # an unreadable ledger answers "no events" — the one failure that must not look quiet
                trace.failure("ledger.read", exc, channel="ledger", path=str(path))
                lines = []
            for index, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    skipped += 1
                    trace.event("ledger.row.skipped", level="trace", channel="ledger", line=index,
                                reason="not json", chars=len(line))
                    continue
                if not isinstance(raw, dict):
                    skipped += 1
                    trace.event("ledger.row.skipped", level="trace", channel="ledger", line=index,
                                reason="not an object", shape=type(raw).__name__)
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
        matched = len(rows)
        truncated = matched > int(limit)
        rows = rows[-int(limit):]
        cursor = rows[-1]["at_epoch"] if rows else (since or 0)
        sp.ok(rows=len(rows), matched=matched, skipped=skipped, truncated=truncated,
              exists=path.is_file(), cursor=round(float(cursor), 3))
    return {"cursor": cursor, "truncated": truncated, "rows": rows, "skipped": skipped}


def turn_rows(profile_dir: Path | str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """The newest turn measurements for one lane, oldest first — the terminal's second source."""
    if not 1 <= int(limit) <= LIMIT_MAX:
        raise ValueError(f"limit must be between 1 and {LIMIT_MAX}")
    path = Path(profile_dir) / "metrics" / "turns.jsonl"
    if not path.is_file():
        trace.event("ledger.turns", level="trace", channel="ledger", lane=Path(profile_dir).name,
                    exists=False, rows=0)
        return []
    rows: list[dict] = []
    skipped = 0
    with trace.span("ledger.turns", channel="ledger", lane=Path(profile_dir).name,
                    limit=int(limit)) as sp:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[-MAX_ROWS:]
        except OSError as exc:
            trace.failure("ledger.turns", exc, channel="ledger", path=str(path))
            lines = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                skipped += 1
                trace.event("ledger.row.skipped", level="trace", channel="ledger", line=index,
                            source="turns", reason="not json", chars=len(line))
                continue
            if isinstance(row, dict):
                rows.append(row)
        rows = rows[-int(limit):]
        sp.ok(rows=len(rows), skipped=skipped)
    return rows


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
    # rows with no parseable timestamp fold to 0 and sort to the front, where the window cuts them: the
    # count is the only sign that they were dropped instead of old
    undated = sum(1 for row in merged if not row["at_epoch"])
    merged = merged[-max(1, int(limit)):]
    trace.event("ledger.fold", level="debug", channel="ledger", events=len(events), turns=len(turns),
                merged=len(merged), limit=int(limit), undated=undated)
    return merged
