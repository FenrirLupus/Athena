"""Reading JSON that may not exist yet.

A configuration, registry or log file that has never been written is the normal first-run state, not an error, so
every reader needs the same tolerant behaviour: a missing file, an unreadable one, or a file holding invalid JSON all
produce the caller's default rather than an exception. Keeping that in one place means the exception list cannot
drift between readers — one of them silently omitted `UnicodeDecodeError` before this existed.

The default is a substitution, so each one gets a line: a config that quietly fell back to 1.0, or a registry that
answered "empty" because its file was half-written, is exactly the failure a reader cannot see at the call site.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import trace

__all__ = ["read_json"]


def read_json(path, default: Any = None) -> Any:
    """Parse a JSON file, returning `default` when it is missing, unreadable, or not valid JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        trace.event("jsonio.default", level="debug", path=str(path), reason="missing")
    except (OSError, UnicodeDecodeError) as exc:
        trace.event("jsonio.default", level="debug", path=str(path), reason=type(exc).__name__,
                    error=str(exc)[:200])
    except json.JSONDecodeError as exc:
        trace.event("jsonio.default", level="debug", path=str(path), reason="invalid-json",
                    line=exc.lineno, column=exc.colno, error=exc.msg[:200])
    return default
