"""Safety — keep Athena within allowed areas (the access model, enforced).

The scope boundary from athena-structure.md, made MECHANICAL. Every tool
that touches the filesystem goes through this module:

    .athena/          — the outer boundary: traverse + write freely
    athena-system/    — the inner sanctum: read/traverse OK, never modify

A path is allowed only if it resolves INSIDE .athena/ (no escapes), and
writes are refused inside the sanctum zones. This is the safety layer —
it protects the domain from the agent's own tools.

(08-12: the readme/ oracle dir was REMOVED by the Operator — the wiki now
serves as the reference; the sanctum is athena-system/ alone.)
"""
from __future__ import annotations

import os
from pathlib import Path

from core.config import ATHENA_ROOT

# The sanctum zones: readable, never writable.
SANCTUM_DIRS = (
    ATHENA_ROOT / "athena-system",
)


class ScopeError(Exception):
    """Raised when a tool tries to touch something outside its scope."""


def resolve(path: str | Path) -> Path:
    """Canonicalize a path and verify it stays inside the outer boundary."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    resolved = p.resolve()  # follows symlinks; catches ../ escapes

    root = ATHENA_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ScopeError(
            f"path outside .athena/: {resolved} — refused"
        )
    return resolved


def is_sanctum(path: Path) -> bool:
    """True if the path is inside a read-only sanctum zone."""
    resolved = path.resolve()
    for zone in SANCTUM_DIRS:
        zone_resolved = zone.resolve()
        try:
            resolved.relative_to(zone_resolved)
            return True
        except ValueError:
            continue
    return False


def check_read(path: str | Path) -> Path:
    """Verify a path may be READ. Raises ScopeError when outside bounds.

    Secrets (authentication.json, .secret) are readable ONLY by the
    secret store — never by the agent's file tools (a leak of the
    credential file defeats the whole boundary).
    """
    resolved = resolve(path)
    if resolved.name == "authentication.json" or resolved.name == ".secret":
        raise ScopeError(
            f"read refused (credential file: {resolved.name}) — "
            "credentials stay sealed in the secret store")
    return resolved


def check_write(path: str | Path) -> Path:
    """Verify a path may be WRITTEN. Refuses outside .athena/ and inside
    the sanctum zones (athena-system/).

    EXCEPTION: the Nurse Agent — the ONLY agent allowed to repair the
    code. While the nurse is in scope, writes into athena-system/ are
    permitted.
    """
    resolved = resolve(path)
    if is_sanctum(resolved):
        # The nurse's privileged repair zone.
        from doctor.nurse import may_write

        if may_write(resolved):
            return resolved
        raise ScopeError(
            f"write to sanctum refused: {resolved} "
            f"(athena-system/ is read-only; only the nurse may repair the code)"
        )
    return resolved


def check_command(command: str) -> str:
    """Command guard: block the most dangerous operations that could
    escape the boundary. This is a safety net, NOT a sandbox — the real
    boundary is the scope checks on every file path.

    Two layers:
      • destructive patterns (rm -rf /, mkfs, shutdown, ...) — hard block
      • out-of-boundary path references (cat /etc/passwd, curl to
        external hosts via command) — flagged so the permission gate can
        prompt instead of silently allowing.
    """
    lowered = command.lower()
    # HARD BLOCK: destructive/system-wrecking operations, always refused.
    # THE 08-15 FIX: "shutdown"/"reboot" only block as STANDALONE commands
    # (the first token) — a bare substring inside another word ("grep
    # reboot", "sed s/reboot/.../") is a false positive on legit work.
    dangerous = ["rm -rf /", "rm -rf ~", "rm -rf .", "mkfs", "dd if=", ":(){"]
    for pattern in dangerous:
        if pattern in lowered:
            raise ScopeError(f"command refused (dangerous pattern: {pattern})")
    _tokens = lowered.strip().split()
    if _tokens and _tokens[0] in ("shutdown", "reboot", "poweroff", "halt"):
        raise ScopeError(f"command refused (dangerous pattern: {_tokens[0]})")
    # OUT-OF-BOUNDARY: paths outside the platform root + sensitive files.
    # Catches `cat /etc/passwd`, `ls /home/<user>`, `vim /etc/hosts` etc.
    # THE 08-15 PORTABILITY FIX: the platform-root exemption is derived
    # from ATHENA_ROOT (both the symlink + real /home forms) — never
    # hardcoded to a machine's username, so the guardrail works on ANY
    # machine.
    _ath = str(ATHENA_ROOT).lower()
    _ath_real = str(ATHENA_ROOT.resolve()).lower().replace("/var/home/", "/home/", 1)
    for token in ("/etc/", "/usr/", "/var/", "/bin/", "/root/", "/home/",
                  "/tmp/", "/proc/", "/sys/"):
        if token in lowered and _ath not in lowered and _ath_real not in lowered:
            raise ScopeError(f"command refused (path outside the platform: {token})")
    # SECRETS: never expose the credential file via commands.
    if "authentication.json" in lowered or ".secret" in lowered:
        raise ScopeError("command refused (credential file access)")
    return command
