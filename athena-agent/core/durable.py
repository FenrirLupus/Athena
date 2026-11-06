"""Durable writes: the shapes a file must be written in so a crash leaves it whole.

Twelve call sites had their own version of "write a temp file and rename it", and they disagreed in ways that
matter: a secret must keep 0600 through the rename, two writers must not collide on a fixed temp name, and a
directory swap is not a file write at all. One function with flags for all of that was measured loosening a
secret file to 0644 and truncating a short write, so each contract gets its own function here.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["write_secret", "write_state", "fsync_dir"]


def fsync_dir(path) -> None:
    """Flush a directory entry so a rename survives a crash; a platform that refuses is not an error."""
    try:
        fd = os.open(Path(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
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
    target.parent.mkdir(parents=True, exist_ok=True)
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, target)
    fsync_dir(target.parent)
    return target


def write_state(path, payload: bytes | str, *, mode: int = 0o644) -> Path:
    """Write state that several writers may touch: a unique temp name, so two writers cannot collide.

    `mode` defaults to what a plain `write_text` produced, because a state file that suddenly became 0600 would be
    a silent permission change rather than a durability fix.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix="." + target.name + ".", suffix=".tmp")
    try:
        _write_all(fd, data)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, target)
    return target
