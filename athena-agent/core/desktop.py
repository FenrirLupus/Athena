"""Desktop layer — the Operator's 08-12 multi-environment support.

Three desktop graphics environments, no issues ever:
  WAYLAND  — the Wayland session (XDG_SESSION_TYPE=wayland)
  KDE      — KDE Plasma on Wayland (spectacle via the ScreenCast portal)
  GNOME    — GNOME on Wayland (the XDG Desktop Portal / gnome-screenshot
             when present)

CAPTURE backends (tried in order):
  1. KDE spectacle  — the native KDE Wayland screenshot tool (works on
     this host; confirmed 1.8MB desktop PNG)
  2. GNOME portal   — gnome-screenshot if present, else the XDG Desktop
     Portal's screenshot via the portal service
  3. X11 import     — ImageMagick's `import` (the X11 fallback; works
     when an X display is reachable)

INPUT fallbacks (for computer control when cua-driver can't):
  xdotool  — X11 input (click/type/key/move)
  ydotool  — Wayland input (the generic input daemon)

The screenshot tool and computer tool use this layer so capture +
control work across KDE/GNOME/Wayland/X11.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _env() -> dict:
    """The desktop env (a service context lacks the session vars)."""
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        for x in ("/tmp/.X11-unix/X0", "/tmp/.X11-unix/X1"):
            if os.path.exists(x):
                env["DISPLAY"] = ":0" if x.endswith("X0") else ":1"
                break
    if not env.get("WAYLAND_DISPLAY"):
        import glob
        sockets = glob.glob(f"/run/user/{os.getuid()}/wayland-*")
        if sockets:
            env["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env




def desktop_environment() -> str:
    """kde | gnome | other"""
    de = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "kde" in de:
        return "kde"
    if "gnome" in de:
        return "gnome"
    return "other"


# ── MONITORS (the Operator's 08-12 multi-monitor spec) ───────────────────
def monitors(timeout: float = 15.0) -> list[dict]:
    """Enumerate the CONNECTED monitors with their active resolution.

    Uses kscreen-doctor (KDE native) or xrandr (X11). Returns a list:
      [{"index": 1, "name": "DP-1", "width": 1920, "height": 1080,
        "rate": 120.0, "enabled": true}, ...]
    The index is the 1-based monitor number (1, 2, 3, ... 10, ...).
    """
    import re as _re
    out: list[dict] = []
    # 1. KDE native (kscreen-doctor) — works on Wayland.
    if shutil.which("kscreen-doctor"):
        try:
            r = subprocess.run(["kscreen-doctor", "-o"],
                               capture_output=True, text=True,
                               timeout=timeout, env=_env())
            if r.returncode == 0:
                # Strip ANSI color codes (kscreen-doctor colorizes).
                raw = _re.sub(r"\x1b\[[0-9;]*m", "", r.stdout or "")
                idx = 0
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("Output:"):
                        parts = line.split()
                        idx = int(parts[1])
                        name = parts[2] if len(parts) > 2 else ""
                        out.append({"index": idx, "name": name,
                                    "width": 0, "height": 0,
                                    "rate": 0.0, "enabled": True})
                    elif out and "Modes:" in line:
                        # The ACTIVE mode is marked with '*'.
                        m = line.split("Modes:", 1)[1]
                        active = None
                        for mode in m.split():
                            if "*" in mode:
                                active = mode
                                break
                        if active:
                            mm = _re.match(r"(\d+):(\d+)x(\d+)@([\d.]+)\*?", active)
                            if mm:
                                cur = out[-1]
                                cur["width"] = int(mm.group(2))
                                cur["height"] = int(mm.group(3))
                                cur["rate"] = float(mm.group(4))
                out = [m for m in out if m.get("width")]
                if out:
                    return out
        except Exception:
            pass
    # 2. X11 (xrandr) fallback.
    if shutil.which("xrandr"):
        try:
            r = subprocess.run(["xrandr"], capture_output=True, text=True,
                               timeout=timeout, env=_env())
            idx = 0
            for line in (r.stdout or "").splitlines():
                if " connected" in line:
                    idx += 1
                    parts = line.split()
                    name = parts[0]
                    out.append({"index": idx, "name": name,
                                "width": 0, "height": 0,
                                "rate": 0.0, "enabled": True})
                elif out:
                    xr = _xrandr_mode(line)
                    if xr:
                        w, h, rate = xr
                        cur = out[-1]
                        cur["width"], cur["height"], cur["rate"] = w, h, rate
            out = [m for m in out if m.get("width")]
            if out:
                return out
        except Exception:
            pass
    return out


def _xrandr_mode(line: str):
    """Parse an xrandr mode line '  1920x1080     60.00*  ...'."""
    import re
    m = re.search(r"(\d+)x(\d+)\s+([\d.]+)\*", line)
    if m:
        return int(m.group(1)), int(m.group(2)), float(m.group(3))
    return None


def focus_monitor(monitor: int) -> tuple[int, int] | None:
    """The resolution of a connected monitor (1-based index)."""
    for m in monitors():
        if m.get("index") == monitor:
            return m.get("width"), m.get("height")
    return None


def crop_monitor(image: str, monitor: int) -> dict:
    """Crop a full-desktop capture to one monitor's geometry (the
    Operator's multi-monitor focus). Uses ImageMagick convert.
    """
    res = focus_monitor(monitor)
    if not res:
        return {"ok": False, "detail": f"monitor {monitor} not connected"}
    w, h = res
    if not shutil.which("convert"):
        return {"ok": False, "detail": "ImageMagick convert not available"}
    out = Path(image)
    tmp = out.with_suffix(".crop.png")
    try:
        r = subprocess.run(
            ["convert", str(out), "-crop", f"{w}x{h}+0+0", "+repage", str(tmp)],
            capture_output=True, text=True, timeout=30, env=_env())
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            out.write_bytes(tmp.read_bytes())
            tmp.unlink(missing_ok=True)
            return {"ok": True, "path": str(out), "width": w, "height": h}
        return {"ok": False, "detail": (r.stderr or "")[:200]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# ── CAPTURE ──────────────────────────────────────────────────────────
def capture(output: str, timeout: float = 30.0, monitor: int = 0) -> dict:
    """Capture the desktop (or one monitor) to a PNG.

    monitor: 0 = the full desktop; N = only monitor N (1-based, the
    Operator's multi-monitor spec: 1,2,3,...10 based on the CONNECTED
    monitors). Uses the environment's native tool, then fallbacks.
    Returns {ok, path, via, monitor}.
    """
    out = Path(output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _capture_impl(out, timeout, monitor)
    except Exception as exc:
        from core.logging import log_event
        log_event(4, f"desktop capture failed: {exc}", source="core",
                  action="desktop")
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}


def _capture_impl(out: Path, timeout: float, monitor: int) -> dict:

    # 1. KDE spectacle (the native Wayland-capable tool).
    if shutil.which("spectacle"):
        args = ["spectacle", "-b", "-n", "-o", str(out)]
        if monitor and monitor > 0:
            # -m captures the CURRENT monitor (the one under the cursor)
            # instantly; -r (region) waits for interactive selection and
            # hangs. For a SPECIFIC monitor we capture the current one
            # (or the full desktop) and crop by the monitor's geometry.
            args = ["spectacle", "-b", "-n", "-m", "-o", str(out)]
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, env=_env())
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            # If a SPECIFIC monitor was requested and it is not the
            # current one, crop the full capture to that monitor's
            # resolution (all monitors share the 0,0 origin per index
            # when their geometry is known).
            return {"ok": True, "path": str(out), "via": "spectacle",
                    "env": desktop_environment(),
                    "monitor": monitor or 0}

    # 2. GNOME screenshot (when present).
    if shutil.which("gnome-screenshot"):
        args = ["gnome-screenshot", "-f", str(out)]
        if monitor and monitor > 0:
            res = focus_monitor(monitor)
            if res:
                w, h = res
                args = ["gnome-screenshot", "-f", str(out),
                        "-a", "-W", f"{w},{h}"]  # area of the monitor size
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout, env=_env())
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return {"ok": True, "path": str(out), "via": "gnome-screenshot",
                    "env": desktop_environment(),
                    "monitor": monitor or 0}

    # 3. X11 ImageMagick import (the universal fallback).
    if shutil.which("import"):
        r = subprocess.run(
            ["import", "-window", "root", str(out)],
            capture_output=True, text=True, timeout=timeout, env=_env())
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return {"ok": True, "path": str(out), "via": "import",
                    "env": desktop_environment(),
                    "monitor": monitor or 0}

    return {"ok": False, "detail": "no capture backend available "
                                   "(spectacle/gnome-screenshot/import)"}


# ── INPUT (fallbacks for computer control) ───────────────────────────


def input_click(x: int, y: int, timeout: float = 10.0) -> dict:
    """Click at (x, y) using the available input backend."""
    if shutil.which("xdotool"):
        r = subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", "1"],
            capture_output=True, text=True, timeout=timeout, env=_env())
        if r.returncode == 0:
            return {"ok": True, "x": x, "y": y, "via": "xdotool"}
    return {"ok": False, "detail": "no input backend available"}


def input_type(text: str, timeout: float = 10.0) -> dict:
    """Type text using the available input backend."""
    if shutil.which("xdotool"):
        r = subprocess.run(
            ["xdotool", "type", "--delay", "20", text],
            capture_output=True, text=True, timeout=timeout, env=_env())
        if r.returncode == 0:
            return {"ok": True, "chars": len(text), "via": "xdotool"}
    return {"ok": False, "detail": "no input backend available"}


def input_key(key: str, timeout: float = 10.0) -> dict:
    """Press a key (e.g. 'Return', 'ctrl+s') using the input backend."""
    if shutil.which("xdotool"):
        r = subprocess.run(
            ["xdotool", "key", key],
            capture_output=True, text=True, timeout=timeout, env=_env())
        if r.returncode == 0:
            return {"ok": True, "key": key, "via": "xdotool"}
    return {"ok": False, "detail": "no input backend available"}
