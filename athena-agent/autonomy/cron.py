"""Cron schedule parsing — the schedule vocabulary, stdlib only.

Supports three schedule forms:
    cron expression:  "*/15 * * * *"   (5 fields: min hour day month weekday)
    interval:         "every 30m" | "every 2h" | "every 1d"
    one-shot:         ISO timestamp "2026-08-07T09:00:00" (fires once)

The matcher decides if a given datetime matches a cron expression.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# 5-field cron: minute hour day-of-month month day-of-week
_CRON_FIELDS = ("minute", "hour", "day", "month", "weekday")
_FIELD_RANGES = {"minute": (0, 59), "hour": (0, 23), "day": (1, 31),
                 "month": (1, 12), "weekday": (0, 6)}  # 0=Sunday


def _parse_field(spec: str, field: str) -> set[int]:
    """Parse one cron field: '*' | '*/n' | 'a,b' | 'a-b' | 'n' (or list)."""
    lo, hi = _FIELD_RANGES[field]
    values: set[int] = set()
    if spec == "*":
        return set(range(lo, hi + 1))
    for part in spec.split(","):
        part = part.strip()
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                values.update(range(lo, hi + 1, step))
            else:
                start = int(base)
                values.update(range(start, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            values.update(range(int(a), int(b) + 1))
        else:
            values.add(int(part))
    return {v for v in values if lo <= v <= hi}


class CronExpr:
    """A parsed 5-field cron expression."""

    def __init__(self, expression: str):
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(f"cron expression must have 5 fields: {expression!r}")
        self.fields = {
            name: _parse_field(spec, name)
            for name, spec in zip(_CRON_FIELDS, parts)
        }

    def matches(self, dt: datetime) -> bool:
        return (
            dt.minute in self.fields["minute"]
            and dt.hour in self.fields["hour"]
            and dt.day in self.fields["day"]
            and dt.month in self.fields["month"]
            and dt.weekday() in self.fields["weekday"]
        )

    def next_after(self, dt: datetime) -> datetime:
        """Find the next matching datetime after dt (minute resolution)."""
        candidate = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(60 * 24 * 366):  # bound: search up to a year ahead
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError("no matching time within a year")


_INTERVAL_RE = re.compile(r"every\s+(\d+)\s*([smhdw])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def normalize_schedule(spec: str) -> str:
    """Expand SHORT schedule forms into their full representation.

    - condensed cron: "03***"  → "0 3 * * *"   (min hour * * *)
    - condensed cron: "30 9 * * *" stays as-is (already full)
    - bare interval:  "30m"    → "every 30m"
    The full-length form always wins when already valid.
    """
    s = spec.strip()
    if not s:
        return s

    # Full 5-field cron — already long form, leave it.
    if len(s.split()) == 5:
        return s

    # Condensed cron: digits+stars with NO spaces, e.g. 03*** / 30 9* no.
    # Pattern: minute(1-2 digits) hour(1-2 digits) then 3 stars.
    m = re.match(r"^(\d{1,2})(\d{1,2})\*{3}$", s)
    if m:
        minute, hour = m.group(1), m.group(2)
        return f"{minute} {hour} * * *"
    # Pattern: minute only then 4 stars (every hour at that minute): 5****
    m = re.match(r"^(\d{1,2})\*{4}$", s)
    if m:
        return f"{m.group(1)} * * * *"

    # Bare interval: "30m" → "every 30m"
    m = re.match(r"^(\d+)\s*([smhdw])$", s, re.IGNORECASE)
    if m:
        return f"every {m.group(1)}{m.group(2).lower()}"

    return s


def parse_interval(spec: str) -> timedelta | None:
    """Parse an interval → timedelta. Supports the CUSTOM H/M/S form:

        'every 30m'        → 0:30:00
        'every 2h'         → 2:00:00
        'every 90s'        → 0:01:30
        'every 2h 30m'     → 2:30:00
        'every 1h 30m 15s' → 1:30:15
        'every 1d' / 'every 1w' → 1 day / 1 week

    Also accepts the SHORT form without 'every': '30m' | '2h' | '1d'.
    """
    s = spec.strip()
    body = s[6:].strip() if s.lower().startswith("every ") else s
    total = 0
    matched = False
    for part in re.findall(r"(\d+)\s*([smhdw])", body, re.IGNORECASE):
        total += int(part[0]) * _UNIT_SECONDS[part[1].lower()]
        matched = True
    return timedelta(seconds=total) if matched else None


def is_interval(spec: str) -> bool:
    return parse_interval(spec) is not None


def is_one_shot(spec: str) -> bool:
    """ISO timestamp one-shots: 2026-08-07T09:00:00 (fires once)."""
    try:
        datetime.fromisoformat(spec.strip())
        return True
    except ValueError:
        return False


def compute_next(schedule: str, now: datetime | None = None) -> str:
    """Compute the next run time for any schedule form. Returns ISO str."""
    now = now or datetime.now()
    s = normalize_schedule(schedule)

    if is_interval(s):
        delta = parse_interval(s)
        return (now + delta).isoformat(timespec="seconds")

    if is_one_shot(s):
        return s  # one-shot fires at its own timestamp

    # Cron expression.
    try:
        return CronExpr(s).next_after(now).isoformat(timespec="seconds")
    except ValueError as exc:
        raise ValueError(f"unrecognized schedule: {schedule!r} ({exc})")


def is_due(schedule: str, last_run_at: str | None, now: datetime | None = None) -> bool:
    """Whether a job with this schedule is due NOW.

    - intervals: due if (now - last_run) >= interval, or never run.
    - one-shots: due if now >= timestamp and it hasn't run.
    - cron:      due if the current minute matches (tracked by last_run).
    """
    now = now or datetime.now()
    s = normalize_schedule(schedule)

    if is_interval(s):
        delta = parse_interval(s)
        if not last_run_at:
            return True
        try:
            last = datetime.fromisoformat(last_run_at)
        except ValueError:
            return True
        return (now - last) >= delta

    if is_one_shot(s):
        try:
            target = datetime.fromisoformat(s)
        except ValueError:
            return False
        return now >= target and not last_run_at

    # Cron: due if the current minute matches AND we haven't run this minute.
    try:
        expr = CronExpr(s)
    except ValueError:
        return False
    if not expr.matches(now):
        return False
    if last_run_at:
        try:
            last = datetime.fromisoformat(last_run_at)
            if last.replace(second=0, microsecond=0) == now.replace(second=0, microsecond=0):
                return False  # already ran this minute
        except ValueError:
            pass
    return True
