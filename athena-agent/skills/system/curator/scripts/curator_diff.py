"""One unified-diff implementation, used by both the writer and the checker.

The first version wrote the patch with `diff(1)` and verified it with `difflib`, and the two disagree about files
that do not end in a newline and about CRLF — so honest proposals were refused as tampered. There is now exactly one
function: whatever it produces is what the proposal stores, and the same function is what apply recomputes.
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:            # the tree that holds core/
    if (_parent / "core" / "trace.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
from core import trace

LANE = "curator"

__all__ = ["unified", "normalise"]


def normalise(text: str) -> str:
    """Line endings and a missing final newline are properties of the file, not of the change."""
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def unified(old: str, new: str, rel: str, context: int = 3) -> str:
    """A unified diff with stable newline handling: every emitted line ends in exactly one newline."""
    a = [line for line in normalise(old).split("\n")]
    b = [line for line in normalise(new).split("\n")]
    out = []
    for line in difflib.unified_diff(a, b, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="\n", n=context):
        out.append(line if line.endswith("\n") else line + "\n")
    text = "".join(out)
    _report(text, old, new, rel, context)
    return text


def _report(text: str, old: str, new: str, rel: str, context: int) -> None:
    """What the diff consists of, by count — an empty one is what a caller refuses on, so the reason is recorded."""
    if not trace.enabled("debug"):
        return
    if not text:
        trace.event("curator.diff.empty", level="debug", channel=LANE, file=rel, context=context,
                    reason="identical" if normalise(old) == normalise(new) else "line endings only",
                    old_chars=len(old), new_chars=len(new))
        return
    body = text.splitlines()
    trace.event("curator.diff", level="debug", channel=LANE, file=rel, context=context,
                added=sum(1 for line in body if line.startswith("+") and not line.startswith("+++")),
                removed=sum(1 for line in body if line.startswith("-") and not line.startswith("---")),
                lines=len(body), chars=len(text))
