"""CUA backend — Athena's computer-use driver wrapper.

Speaks to the `cua-driver` binary (the Operator's 08-12 spec): background
desktop control — screenshots, mouse, keyboard, scroll, drag,
app/window listing, and typed-browser navigation — WITHOUT stealing the
operator's cursor or focus.

The tools that use this:
  tools/computer/scripts/computer.py     — the `computer` tool
  tools/screenshot/scripts/screenshot.py — the `screenshot` tool
  tools/browser/scripts/browser.py       — the `browser` tool

The wrapper is a thin, safe shim: every call is `cua-driver call
<tool> '<json args>'`, outputs are JSON, screenshots can go to a file
or come back as base64. Failures degrade gracefully (never crash the
loop).
"""

from __future__ import annotations

import json
import shutil
import subprocess

_DRIVER = "cua-driver"


def _driver_path() -> str:
    """The cua-driver binary (PATH, else the local bin)."""
    found = shutil.which(_DRIVER)
    if found:
        return found
    from pathlib import Path
    local = Path.home() / ".local" / "bin" / _DRIVER
    return str(local) if local.exists() else _DRIVER


def available() -> bool:
    """Is cua-driver installed and callable?"""
    try:
        r = subprocess.run([_driver_path(), "status"],
                           capture_output=True, text=True, timeout=10,
                           env=_env())
        return r.returncode == 0
    except Exception:
        return False


def _env() -> dict:
    """The cua-driver child env (the established model):

    - starts from os.environ
    - injects the DISPLAY/WAYLAND/XDG_RUNTIME the desktop session uses
      (a daemon spawned outside the session lacks them otherwise)
    - disables cua-driver telemetry (third-party binary, never hands it
      secrets — the same policy everywhere)
    """
    import os
    env = dict(os.environ)
    # The desktop display (Bazzite/Wayland typically; X11 fallback).
    if not env.get("DISPLAY"):
        # Try the X11 socket; only set if present.
        for x in ("/tmp/.X11-unix/X0", "/tmp/.X11-unix/X1"):
            if os.path.exists(x):
                env["DISPLAY"] = ":0" if x.endswith("X0") else ":1"
                break
    if not env.get("WAYLAND_DISPLAY"):
        # The user's Wayland socket under their runtime dir.
        import glob
        sockets = glob.glob(f"/run/user/{os.getuid()}/wayland-*")
        if sockets:
            env["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])
    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    env.setdefault("CUA_DRIVER_RS_TELEMETRY_ENABLED", "0")
    return env


def call(name: str, args: dict | None = None, timeout: float = 30.0) -> dict:
    """Call a cua-driver tool. Returns the parsed JSON result (or an
    honest failure dict — never raises into the loop)."""
    args = args or {}
    try:
        r = subprocess.run(
            [_driver_path(), "call", name, json.dumps(args)],
            capture_output=True, text=True, timeout=timeout,
            env=_env(),
        )
        out = (r.stdout or "").strip()
        if not out:
            return {"ok": False, "detail": (r.stderr or "").strip()[:300]
                    or "no output"}
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data.setdefault("ok", r.returncode == 0)
                return data
            return {"ok": r.returncode == 0, "result": data}
        except ValueError:
            # Non-JSON (e.g. a base64 blob) — pass it through raw.
            return {"ok": r.returncode == 0, "raw": out[:2000]}
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"cua call failed: {name}: {exc}", source="core",
                  action="cua")
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def tools() -> list[str]:
    """The cua-driver tool names (for capability checks)."""
    try:
        r = subprocess.run([_driver_path(), "list-tools"],
                           capture_output=True, text=True, timeout=15)
        names = []
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if ":" in line and not line.startswith(("Cua", "cua")):
                names.append(line.split(":", 1)[0].strip())
        return names
    except Exception:
        return []
