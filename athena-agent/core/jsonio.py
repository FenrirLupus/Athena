"""Reading JSON that may not exist yet.

A configuration, registry or log file that has never been written is the normal first-run state, not an error, so
every reader needs the same tolerant behaviour: a missing file, an unreadable one, or a file holding invalid JSON all
produce the caller's default rather than an exception. Keeping that in one place means the exception list cannot
drift between readers — one of them silently omitted `UnicodeDecodeError` before this existed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["read_json"]


def read_json(path, default: Any = None) -> Any:
    """Parse a JSON file, returning `default` when it is missing, unreadable, or not valid JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
