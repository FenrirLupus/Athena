"""Channel ↔ branch arithmetic, shared by the updater and by anything that has to name a build.

Three channels, three branches, and the rule that keeps them honest: **the branch says which channel it carries, and
the `VERSION` inside must agree with it**. A branch called `beta` holding `1.2.0` is a beta branch carrying a stable
build; that disagreement is refused rather than installed, because the alternative is a box that says "beta" while
running something else.

      branch      channel     version shape
      main        stable      x.0.0   (major > 0)
      beta        beta        0.x.0   (minor > 0)
      alpha       alpha       0.0.x

`main` is the stable branch on purpose: it is the default branch a clone lands on, and the one a release tag is cut
from. Beta and alpha are long-lived branches beside it, never merges into `main` that skipped a version bump.
"""
from __future__ import annotations

import re
from pathlib import Path

from core import trace

LANE = "updater"

SEMVER = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$")

# channel -> branch. One mapping, used in both directions, so a channel can never quietly mean two branches.
CHANNEL_BRANCH = {"stable": "main", "beta": "beta", "alpha": "alpha"}
BRANCH_CHANNEL = {branch: channel for channel, branch in CHANNEL_BRANCH.items()}
CHANNELS = ("stable", "beta", "alpha")


class ChannelError(ValueError):
    """A channel, a branch and a version that cannot all be true at once."""


def parts(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(str(version).strip())
    if not match:
        trace.event("updater.refused", level="error", channel=LANE, reason="version-shape",
                    value=str(version)[:40], rule="x.y.z")
        raise ChannelError(f"{version!r} is not a version of the form x.y.z")
    return int(match["major"]), int(match["minor"]), int(match["patch"])


def channel_of(version: str) -> str:
    """The channel the digits imply — never a label stored beside them."""
    major, minor, _patch = parts(version)
    channel = "stable" if major > 0 else ("beta" if minor > 0 else "alpha")
    trace.event("updater.channel", level="debug", channel=LANE, version=str(version), channel_name=channel,
                rule="digits")
    return channel


def branch_for(channel: str) -> str:
    wanted = str(channel or "").strip().lower()
    if wanted not in CHANNEL_BRANCH:
        raise ChannelError(f"unknown channel {channel!r}; known channels: {', '.join(CHANNELS)}")
    trace.event("updater.branch", level="debug", channel=LANE, channel_name=wanted,
                branch=CHANNEL_BRANCH[wanted])
    return CHANNEL_BRANCH[wanted]


def channel_for_branch(branch: str) -> str:
    name = str(branch or "").strip()
    if name not in BRANCH_CHANNEL:
        raise ChannelError(f"unknown branch {branch!r}; the updater reads {', '.join(BN for BN in BRANCH_CHANNEL)}")
    trace.event("updater.branch", level="debug", channel=LANE, branch=name,
                channel_name=BRANCH_CHANNEL[name])
    return BRANCH_CHANNEL[name]


def bump_hint(channel: str) -> str:
    return {"stable": "x.0.0", "beta": "0.x.0", "alpha": "0.0.x"}[channel]


def read_version(tree: Path) -> str:
    """The version of a tree on disk, from its `VERSION` file. A missing file is an error, never a default."""
    target = Path(tree) / "VERSION"
    if not target.is_file():
        raise ChannelError(f"{target} is missing — that is not a build this updater can install")
    text = target.read_text(encoding="utf-8").strip()
    parts(text)                                       # validates the shape
    return text


def check_pair(version: str, branch: str) -> None:
    """Refuse a branch and a version that disagree, naming the digit to bump."""
    expected = channel_for_branch(branch)
    actual = channel_of(version)
    if expected != actual:
        trace.event("updater.refused", level="error", channel=LANE, reason="branch-version",
                    branch=branch, version=str(version), expected=expected, actual=actual,
                    bump=bump_hint(expected))
        raise ChannelError(
            f"branch {branch!r} carries {expected} builds, but this tree is {version} ({actual}). "
            f"A {expected} release needs the {bump_hint(expected)} digit bumped — the branch and the number "
            f"must agree, or the box will report a channel it is not running.")
    trace.event("updater.pair", level="debug", channel=LANE, branch=branch, version=str(version),
                channel_name=expected)


def compare(left: str, right: str) -> int:
    """-1 / 0 / 1, so a "newer" claim can be checked before anything is replaced."""
    a, b = parts(left), parts(right)
    return (a > b) - (a < b)
