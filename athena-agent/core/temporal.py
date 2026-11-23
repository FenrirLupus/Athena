"""A lane's sense of elapsed time — a record, an anchor, and a boundary row.

The problem this exists for (documentation/proposals/temporal/design.md §1): every stored row carries an
immutable UTC stamp (`core/session.py::add`), and the prompt never shows one — `context/stack.py::build`
passes the rows through and `providers/wire.py` rebuilds each message as role + content only. Two messages
sixteen days apart are token-adjacent, and adjacency reads as "this conversation is in progress". So the
agent answers last week's question as this minute's.

Three layers, one record:

  A  anchor      ephemeral, one turn   how long ago the last exchange was, and what that implies
  B  seam row    permanent, one per gap  a `[Time]` row so a *later* turn reads the past as past
  C  evidence    ephemeral, derived      how old the last tool read is

Rules this module keeps, because each one is a failure the design rejected:

  * time is written by code, never inferred by the model; nothing here asks a model anything
  * `gap_s` is computed in exactly ONE place (`note_turn_open`), so the prompt and the response gate can
    never disagree about when the lane last spoke
  * absolute stamps are stored; relative phrasing ("2 weeks 2 days ago") is rendered per turn and never
    persisted — a stored "3 hours ago" ages into a lie within the day
  * the pure half takes `now` as an argument. Nothing in this module reads the clock unless it is the two
    writers, and those are the turn's own open/close
  * the transcript is the source of truth; the record is a dated observation of it, so losing the record
    costs nothing (the next turn rebuilds it from rows)

Stdlib only, and no import of `channelstate`: a lane's position and its last contact are different facts and
must not reset each other (design.md §4).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core import durable
from core import jsonio
from core import timefmt
from core import trace

__all__ = [
    "FILENAME", "SCHEMA", "CONTINUOUS_UNDER_S", "HOUR", "DAY", "WEEK",
    "path", "read", "write", "enabled", "elapsed", "band", "seam_due", "is_seam",
    "seam_already_written", "seam_written_today", "seam_room_today",
    "newest_exchange", "newest_tool_batch", "observe", "anchor", "seam_text", "age_seconds",
    "note_turn_open", "note_turn_close",
]

FILENAME = "temporal.json"
SCHEMA = 1

# The band ladder is not configurable (design.md §3.1): four numbers an operator can change are four numbers
# that can drift, and these have to distinguish 1 h / 1 d / 1 w or the requirement is unmet.
CONTINUOUS_UNDER_S = 60
HOUR = 3600
DAY = 86400
WEEK = 604800

# The only instruction-shaped line in the anchor, and the reason the reply differs between bands. One per
# band: "continuous" and "sitting" are the same instruction because in both the thread is still open.
_BAND_LINE = {
    "continuous": "This is still the same sitting, so continue the thread without re-introducing it.",
    "sitting": "This is still the same sitting, so continue the thread without re-introducing it.",
    "away": "Minutes have passed since that exchange: anything you stated as current then may have changed, "
            "so re-check it before relying on it.",
    "hours": "Hours have passed since that exchange: anything you stated as current then may have changed, "
             "so re-check it before relying on it.",
    "days": "A day or more has passed. Treat anything you asserted then as an observation from that day, "
            "not as current state.",
    "weeks": "A week or more has passed. Treat everything you said before that point as an observation from "
             "then, not as current state, and say what has to be re-checked.",
}

_MARKER = re.compile(r"^\s*\[(?:Voice|Message|Time)\]\s*")
QUOTE_CHARS = 120


# -- the record ------------------------------------------------------------
def path(root) -> Path:
    """The record's path: a sibling of `state.json`, readable without loading a transcript."""
    return Path(root) / FILENAME


def read(root) -> dict:
    """The lane's record, or `{}` when absent, unreadable, a version we do not know, or another lane's.

    A copied profile is the case the lane check exists for: a record that says `"lane": "scout"` sitting in
    `profiles/atlas/` describes a different thread, and reading it would give atlas scout's gap.
    """
    data = jsonio.read_json(path(root), default={})
    if not isinstance(data, dict) or not data:
        return {}
    try:
        version = int(data.get("schema") or 0)
    except (TypeError, ValueError):
        version = 0
    if version != SCHEMA:
        trace.event("temporal.read.refused", level="debug", lane=Path(root).name,
                    schema=str(data.get("schema")), expected=SCHEMA)
        return {}
    lane = str(data.get("lane") or "")
    if lane and lane != Path(root).name:
        trace.event("temporal.read.refused", level="debug", lane=Path(root).name,
                    recorded_lane=lane, reason="record belongs to another lane")
        return {}
    return data


def write(root, record: dict) -> dict:
    """Write the record atomically (unique temp name, fsync, rename) and return what was written."""
    payload = dict(record or {})
    payload["schema"] = SCHEMA
    payload["lane"] = Path(root).name
    durable.write_state(path(root), json.dumps(payload, ensure_ascii=False, indent=2,
                                               sort_keys=True) + "\n")
    return payload


def enabled(cfg: dict) -> bool:
    """`temporal.enabled`, defaulting to true; false makes every function here inert (no file, no rows)."""
    value = (cfg.get("temporal") or {}).get("enabled")
    return True if value is None else bool(value)


# -- pure: phrasing --------------------------------------------------------
def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}" + ("" if count == 1 else "s")


def elapsed(seconds) -> str:
    """Human phrasing for a duration — at most two units, largest first, floored.

    `1449205` → `2 weeks 2 days`. Days and weeks pluralise; h/min/s do not, because "1 h" is how a duration
    is written. Floored rather than rounded: a gap of 59.9 s is "59 s", and rounding up would let a
    continuous exchange announce a minute that did not pass.
    """
    try:
        total = int(float(seconds or 0))
    except (TypeError, ValueError):
        total = 0
    total = max(0, total)
    if total < 60:
        return f"{total} s"
    if total < HOUR:
        # "45 min", never "45 mins": h/min/s are written as abbreviations, days and weeks are words
        return f"{total // 60} min"
    if total < DAY:
        hours, minutes = total // HOUR, (total % HOUR) // 60
        return f"{hours} h" + (f" {minutes} min" if minutes else "")
    days, rest = total // DAY, total % DAY
    if days < 7:
        hours = rest // HOUR
        return _plural(days, "day") + (f" {hours} h" if hours else "")
    weeks, spare = days // 7, days % 7
    return _plural(weeks, "week") + (f" {_plural(spare, 'day')}" if spare else "")


def band(gap_s, cfg: dict) -> str:
    """Which band a gap falls in. 1 h / 1 d / 1 w are the requirement; the `away` edge is the seam quantum.

    The seam quantum is the one configurable edge, and the `away` band starts exactly there — a persisted
    `[Time]` row saying minutes passed, beside an instruction saying "still the same sitting", would be two
    answers to one question. At the shipped 15 minutes that gives a quarter-hour grain: a lane quiet for 15
    minutes already reads as away, and the sub-hour bands below an hour are what make an AFK quarter-hour
    legible rather than rounding it up to "hours".
    """
    temporal_cfg = cfg.get("temporal") or {}
    floor = temporal_cfg.get("continuous_under_seconds")
    floor = CONTINUOUS_UNDER_S if floor is None else float(floor)
    quantum = temporal_cfg.get("seam_after_minutes")
    quantum = 60.0 if quantum is None else float(quantum)
    away_after = quantum * 60 if quantum > 0 else HOUR
    gap = float(gap_s or 0)
    if gap < floor:
        return "continuous"
    if gap < away_after:
        return "sitting"
    if gap < HOUR:
        return "away"
    if gap < DAY:
        return "hours"
    if gap < WEEK:
        return "days"
    return "weeks"


def seam_due(gap_s, cfg: dict) -> bool:
    """Is this gap long enough to leave a persisted boundary row? `0` means one on every gap."""
    minutes = (cfg.get("temporal") or {}).get("seam_after_minutes")
    minutes = 60.0 if minutes is None else float(minutes)
    gap = float(gap_s or 0)
    if minutes <= 0:
        return gap > 0
    return gap >= minutes * 60


def seam_room_today(cfg: dict) -> bool:
    """May this lane leave a `[Time]` row at all today? `seam_max_per_day: 0` means never — anchor only."""
    cap = (cfg.get("temporal") or {}).get("seam_max_per_day")
    cap = 1 if cap is None else int(cap)
    return cap > 0


# -- pure: reading the transcript -----------------------------------------
def is_seam(row: dict) -> bool:
    """A row written by this module rather than by the person or the agent."""
    return isinstance(row, dict) and row.get("kind") == "seam"


def seam_already_written(rows, last_exchange_ts: str) -> bool:
    """The once-only rule: a seam row newer than the last exchange means this gap is already marked.

    Read from the transcript, not from a flag in the record — a crash after the row was written and before
    the reply landed would otherwise leave a flag saying "not yet" beside the row saying "done".
    """
    cutoff = str(last_exchange_ts or "")
    return any(is_seam(row) and str(row.get("ts") or "") > cutoff for row in rows or [])


def seam_written_today(rows, now: float) -> bool:
    """Has this lane already left a `[Time]` row today (UTC)?

    The persisted entry is a marker, not a log. A lane that idles and resumes ten times in a day must not
    leave ten near-identical lines: the anchor already states the exact interval on every resuming turn, so
    the transcript only needs one boundary per day to keep the past readable as past.
    """
    day = timefmt.stamp_from(now)[:10]
    return any(is_seam(row) and str(row.get("ts") or "").startswith(day) for row in rows or [])


def newest_exchange(rows) -> dict | None:
    """The newest real exchange row — a seam row is a marker, not something either side said."""
    for row in reversed(list(rows or [])):
        if is_seam(row):
            continue
        if row.get("role") in ("user", "assistant"):
            return row
    return None


def newest_tool_batch(rows) -> tuple[list[str], str] | None:
    """The newest run of consecutive `tool` rows: distinct names in order (max 3), and the oldest stamp.

    The newest *batch*, not "the last turn's rows": a turn boundary cannot be read reliably from rows —
    interruptions inject `user` rows mid-turn and a failed turn leaves a partial tool round — while a batch
    is exactly "what I last read". The oldest stamp is the honest age, since a batch is seconds wide.
    """
    names: list[str] = []
    stamps: list[str] = []
    for row in reversed(list(rows or [])):
        if row.get("role") != "tool":
            break
        name = str(row.get("name") or "")
        if name and name not in names:
            names.append(name)
        stamp = str(row.get("ts") or "")
        if stamp:
            stamps.append(stamp)
    if not names and not stamps:
        return None
    return names[:3], (min(stamps) if stamps else "")


def age_seconds(stamp: str, now: float) -> int | None:
    """How old a stamp is, in whole seconds — or `None` when it cannot be read.

    The one place a stamp is turned into an age, so a row, the anchor and (later) the gate all subtract the
    same way. An unreadable stamp is `None`, never a guessed number.
    """
    try:
        return int(max(0.0, float(now) - timefmt.parse(stamp)))
    except ValueError:
        return None


def observe(rows) -> dict:
    """Everything the record takes from the transcript: the last exchange, its line, the last tool batch."""
    exchange = newest_exchange(rows)
    last_ts = str((exchange or {}).get("ts") or "")
    last_line = ""
    # only when the newest exchange is the agent's own row: after a failed turn the newest row is the user's,
    # and "your last line then" would be a lie about who spoke last
    if exchange is not None and exchange.get("role") == "assistant":
        text = _MARKER.sub("", str(exchange.get("content") or "")).strip()
        if text:
            last_line = (text[:QUOTE_CHARS] + "…") if len(text) > QUOTE_CHARS else text
    batch = newest_tool_batch(rows)
    names, oldest = (batch if batch else ([], ""))
    return {"last_exchange_ts": last_ts, "last_line": last_line,
            "last_tool_ts": oldest, "last_tool_names": list(names)}


# -- pure: rendering -------------------------------------------------------
def anchor(record: dict, now: float, cfg: dict) -> str:
    """The block the prompt carries this turn, or `""` when there is nothing honest to say.

    Pure: same `(record, now, cfg)` gives the same bytes, which is what lets the caller freeze it for the
    whole turn and keep the cached prefix stable across iterations. Order is deliberate — `_fit()` truncates
    the tail, so the gap and its instruction (which exist nowhere else) come before the quote and the tool
    line (both re-derivable from the transcript in the same window).
    """
    if not enabled(cfg) or not isinstance(record, dict) or not record:
        return ""
    last_ts = str(record.get("last_exchange_ts") or "")
    gap = record.get("gap_s")
    if not last_ts or gap is None:
        return ""
    try:
        gap_s = float(gap)
    except (TypeError, ValueError):
        return ""
    lines = [f"Now: {timefmt.stamp_from(now)}. Last exchange in this thread: {last_ts} — "
             f"{elapsed(gap_s)} ago.",
             _BAND_LINE.get(band(gap_s, cfg), _BAND_LINE["weeks"])]
    quote = str(record.get("last_line") or "").strip()
    if quote:
        lines.append(f'Your last line then: "{quote}"')
    names = [str(n) for n in (record.get("last_tool_names") or []) if n]
    tool_ts = str(record.get("last_tool_ts") or "")
    if names and tool_ts:
        try:
            age = max(0.0, float(now) - timefmt.parse(tool_ts))
        except ValueError:
            age = None
        when = f"{tool_ts}, {elapsed(age)} ago" if age is not None else tool_ts
        lines.append(f"Last tool reads: {', '.join(names)} ({when}).")
    return "# Now\n\n" + "\n".join(lines)


def seam_text(record: dict, now: float) -> str:
    """The persisted boundary row's text — two absolute stamps and the interval between them.

    No "ago": this text is permanent, and a relative phrase written once is wrong by the next turn. The
    `[Time]` prefix follows the marker convention the loop already owns (`[Message]`, `[Voice]`), and its
    meaning is taught once in the static instructions rather than re-explained per row.
    """
    last_ts = str((record or {}).get("last_exchange_ts") or "")
    gap_s = float((record or {}).get("gap_s") or 0)
    return (f"[Time] {elapsed(gap_s)} passed between the previous exchange ({last_ts}) and this one "
            f"({timefmt.stamp_from(now)}). The turns above are from that earlier time.")


# -- the two writers -------------------------------------------------------
def note_turn_open(root, lane: str, now: float, cfg: dict, rows) -> dict:
    """Open a turn: rebuild the record from the transcript and write it. The only place `gap_s` is computed."""
    if not enabled(cfg):
        return {}
    observed = observe(rows)
    record = {
        "observed_at": timefmt.stamp_from(now),
        "last_exchange_ts": observed["last_exchange_ts"],
        "gap_s": None,
        "gap_band": "",
        "last_line": observed["last_line"],
        "last_tool_ts": observed["last_tool_ts"],
        "last_tool_names": observed["last_tool_names"],
    }
    if record["last_exchange_ts"]:
        try:
            gap = max(0.0, float(now) - timefmt.parse(record["last_exchange_ts"]))
        except ValueError as exc:
            # a stamp the reader cannot parse is treated as no stamp: no gap, no anchor, byte-identical
            # prompt — rather than a guessed interval
            trace.event("temporal.gap.unreadable", level="debug", lane=lane,
                        stamp=record["last_exchange_ts"], error=str(exc)[:120])
        else:
            record["gap_s"] = int(gap)
            record["gap_band"] = band(gap, cfg)
    written = write(root, record)
    trace.event("temporal.open", level="debug", lane=lane, gap_s=record["gap_s"],
                gap_band=record["gap_band"], last_exchange=record["last_exchange_ts"],
                tools=len(record["last_tool_names"]))
    return written


def note_turn_close(root, record: dict, now: float, status: str) -> dict:
    """Close a turn: when it ended and how. The gate's "last contact" and the UI's age read this."""
    if not record:
        return {}
    merged = dict(record)
    merged["last_turn_end_ts"] = timefmt.stamp_from(now)
    merged["last_status"] = str(status or "")
    try:
        return write(root, merged)
    except OSError as exc:                       # a full disk must not turn a finished turn into a failure
        trace.failure("temporal.close.write", exc, lane=Path(root).name, status=merged["last_status"])
        return merged
