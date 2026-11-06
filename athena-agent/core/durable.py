"""Durable writes: the shapes a file must be written in so a crash leaves it whole.

Twelve call sites had their own version of "write a temp file and rename it", and they disagreed in ways that
matter: a secret must keep 0600 through the rename, two writers must not collide on a fixed temp name, and a
directory swap is not a file write at all. One function with flags for all of that was measured loosening a
secret file to 0644 and truncating a short write, so each contract gets its own function here.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from core import trace

__all__ = ["write_secret", "write_state", "fsync_dir"]


def fsync_dir(path) -> None:
    """Flush a directory entry so a rename survives a crash; a platform that refuses is not an error."""
    try:
        fd = os.open(Path(path), os.O_RDONLY)
    except OSError as exc:
        # not an error, but it is a *silent* durability downgrade: the rename is not yet on disk
        trace.event("durable.fsync.skipped", level="debug", path=str(path), stage="open",
                    error=type(exc).__name__)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        trace.event("durable.fsync.skipped", level="debug", path=str(path), stage="fsync",
                    error=type(exc).__name__)
    finally:
        os.close(fd)


def _write_all(fd: int, payload: bytes) -> None:
    """Write every byte: `os.write` may accept less than it was given, and a short write truncates the file."""
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("write returned no progress")
        view = view[written:]


def write_secret(path, payload: bytes | str, *, mode: int = 0o600) -> Path:
    """Write something whose permissions matter: created at `mode`, fsynced, renamed, and the rename fsynced."""
    target = Path(path)
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    with trace.span("durable.write", level="debug", kind="secret", path=str(target),
                    bytes=len(data), mode=oct(mode)) as sp:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(tmp, mode)
        except OSError as exc:              # the open mode is the fallback; say it happened
            trace.event("durable.chmod.skipped", level="debug", path=str(tmp), error=type(exc).__name__)
        os.replace(tmp, target)
        fsync_dir(target.parent)
        return target


def write_state(path, payload: bytes | str, *, mode: int = 0o644) -> Path:
    """Write state that several writers may touch: a unique temp name, so two writers cannot collide.

    `mode` defaults to what a plain `write_text` produced, because a state file that suddenly became 0600 would be
    a silent permission change rather than a durability fix.
    """
    target = Path(path)
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    with trace.span("durable.write", level="debug", kind="state", path=str(target),
                    bytes=len(data), mode=oct(mode)) as sp:
        target.parent.mkdir(parents=True, exist_ok=True)
        kept_mode = False
        if target.exists():                   # keep the mode the file already had; a rewrite is not a re-permission
            try:
                mode = stat.S_IMODE(target.stat().st_mode)
                kept_mode = True
            except OSError as exc:            # the default mode stands in — a silent permission choice
                trace.event("durable.mode.default", level="debug", path=str(target),
                            mode=oct(mode), error=type(exc).__name__)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix="." + target.name + ".", suffix=".tmp")
        try:
            _write_all(fd, data)
            os.fsync(fd)
        except Exception:
            os.close(fd)
            try:
                os.unlink(tmp)
            except OSError as exc:            # a stray temp file is the fallback; name it rather than hide it
                trace.event("durable.tmp.orphan", level="debug", path=tmp, error=type(exc).__name__)
            raise
        else:
            os.close(fd)
        try:
            os.chmod(tmp, mode)
        except OSError as exc:
            trace.event("durable.chmod.skipped", level="debug", path=str(tmp), error=type(exc).__name__)
        os.replace(tmp, target)
        sp.ok(mode=oct(mode), kept_mode=kept_mode)
        return target
