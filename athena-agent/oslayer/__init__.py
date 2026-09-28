"""Platform layer — the same wrapper names, two OS backends.

Every filesystem/process/network tool has ONE wrapper name (read, write,
append, replace, ...) and TWO implementations: linux/ and windows/. The
wrapper dispatches to the backend for the current OS — the calling agent
never cares which OS it runs on.

    from platform import platform
    platform.read(path)      # -> linux.read(...) or windows.read(...)

Backends live in systems/platform/linux.py and systems/platform/windows.py.
Both expose the SAME callables; where an implementation differs (shell
syntax, process control, downloads) each backend provides its own.
"""
from __future__ import annotations

import sys

from oslayer.linux import LinuxBackend
from oslayer.windows import WindowsBackend

if sys.platform.startswith("win"):
    platform = WindowsBackend()
else:
    platform = LinuxBackend()

# The canonical wrapper-name list (the tool registry uses these).
WRAPPER_NAMES = [
    "read", "write", "append", "replace", "patch", "delete", "copy",
    "move", "rename", "list", "tree", "find", "search", "mkdir", "exists",
    "stat", "hash", "execute", "terminal", "process", "kill", "download",
    "upload", "compress", "extract",
]


def backend_name() -> str:
    """Which backend is active (for the doctor test)."""
    return platform.name
