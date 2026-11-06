"""Version — one number, one home, and a channel derived from it.

The version lives in one file at the tree root. Everything that reports a version reads it: the framework title,
`/health`, the pages, the sidecar runtime. The channel is derived from its digits rather than stored beside them,
so a release that claims a channel its version does not support is refused by arithmetic.

The channel is **derived from the digits**, not stored beside them, because a label drifts the first time someone
promotes without bumping:

    x.0.0   stable   what the box runs
    0.x.0   beta     feature-complete for the cycle, being watched
    0.0.x   alpha    under active development, no promise

That makes a lie arithmetically detectable: `release --check --channel stable` refuses `0.4.1`, and a version that does
not move forward is refused against the tag it claims to follow.
"""
from __future__ import annotations

import re
from pathlib import Path

from core import trace

SEMVER = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")
CHANNELS = ("alpha", "beta", "stable")
VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


class VersionError(ValueError):
    """A version that cannot be read, parsed, or promoted as claimed."""


def text() -> str:
    """The version string, from `VERSION` at the tree root. Missing file is an error, not a default."""
    try:
        raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VersionError(f"{VERSION_FILE} is missing — a build with no version cannot be released") from exc
    if not SEMVER.match(raw):
        raise VersionError(f"{VERSION_FILE} holds {raw!r}, which is not MAJOR.MINOR.PATCH")
    return raw


def parts() -> tuple[int, int, int]:
    major, minor, patch = text().split(".")
    return int(major), int(minor), int(patch)


def channel() -> str:
    """stable on a non-zero major, beta on a non-zero minor, alpha otherwise."""
    major, minor, _ = parts()
    if major > 0:
        return "stable"
    return "beta" if minor > 0 else "alpha"


def line() -> str:
    """`0.0.1 · alpha` — what a footer or a log line should print."""
    return f"{text()} · {channel()}"


def check(release_channel: str | None = None) -> dict:
    """Validate the version on its own terms. Raises VersionError with the reason."""
    version, derived = text(), channel()
    if release_channel and release_channel not in CHANNELS:
        raise VersionError(f"{release_channel!r} is not a channel — use {', '.join(CHANNELS)}")
    if release_channel and release_channel != derived:
        raise VersionError(f"version {version} is {derived}, not {release_channel}: bump the digit that matches the "
                           f"channel (x.0.0 stable, 0.x.0 beta, 0.0.x alpha)")
    trace.event("version.check", level="debug", version=version, channel=derived,
                asked=release_channel or "", file=str(VERSION_FILE))
    return {"version": version, "channel": derived}


def compare(other: str) -> int:
    """-1 if this version is older than `other`, 0 equal, 1 newer. Raises on a malformed argument."""
    if not SEMVER.match(str(other or "").strip()):
        raise VersionError(f"{other!r} is not MAJOR.MINOR.PATCH")
    left, right = parts(), tuple(int(part) for part in str(other).strip().split("."))
    return (left > right) - (left < right)
