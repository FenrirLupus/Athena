"""Timestamps: one format, UTC, and identical everywhere it is written.

Every metrics row, ledger line and registry entry is compared as a string by tools that parse it, so a stamp that
silently became local time, gained milliseconds or lost its Z would break them without breaking anything that
raises. The format is asserted here rather than trusted.
"""
import re
import sys
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from core import timefmt

CHECKS = {}
SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

CHECKS["stamp_shape"] = bool(SHAPE.match(timefmt.stamp()))
CHECKS["epoch_zero_is_utc"] = timefmt.stamp_from(0) == "1970-01-01T00:00:00Z"
CHECKS["known_epoch"] = timefmt.stamp_from(1700000000) == "2023-11-14T22:13:20Z"
CHECKS["negative_epoch"] = timefmt.stamp_from(-1) == "1969-12-31T23:59:59Z"
CHECKS["format_constant_matches"] = timefmt.FORMAT == "%Y-%m-%dT%H:%M:%SZ"
CHECKS["no_fractional_seconds"] = "." not in timefmt.stamp()

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
sys.exit(0 if all(CHECKS.values()) else 1)
