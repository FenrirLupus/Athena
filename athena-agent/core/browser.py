"""Browser — hands-off web access (the Operator's spec).

Two modes:
  - visible: opens the OS DEFAULT browser on the URL (xdg-open on Linux,
    `start` on Windows) — the user SEES the page. (Linux + Windows only;
    macOS is NOT a supported platform.)
  - silent: a terminal browser / fetch fallback (lynx/curl) that returns
    the page TEXT without opening any window — the "generic terminal
    browser" mode for quiet automation.

The tool is a hands-off button: it builds the real command and runs it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys


def _log(level: int, msg: str, source: str = "browser") -> None:
    """The browser is an operational module — its events are logged."""
    try:
        from metrics.logger import log
        log(level, msg, source=source)
    except Exception:
        pass


def default_browser_open(url: str) -> dict:
    """Open the URL in the OS default browser (visible, hands-off).

    Supported platforms (the Operator's spec): Linux (xdg-open) and Windows
    (start). macOS is NOT supported.
    """
    if sys.platform.startswith("win"):
        cmd = ["cmd", "/c", "start", "", url]
    else:
        cmd = ["xdg-open", url]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
        _log(2, f"browser opened: {url}", source="browser")
        return {"ok": True, "mode": "visible",
                "url": url, "pid": proc.pid,
                "detail": "opened in the default browser"}
    except Exception as exc:
        _log(3, f"browser open failed: {exc}", source="browser")
        return {"ok": False, "mode": "visible", "url": url,
                "detail": str(exc)}


def silent_fetch(url: str, timeout: float = 30.0) -> dict:
    """Fetch the page as TEXT without opening a window.

    Prefers a terminal browser (lynx/w3m); falls back to curl (raw HTML)
    then Python's urllib. The silent mode for quiet automation.
    """
    lynx = shutil.which("lynx")
    if lynx:
        try:
            r = subprocess.run([lynx, "-dump", "-nolist", url],
                               capture_output=True, text=True,
                               timeout=timeout, errors="replace")
            if r.returncode == 0 and r.stdout.strip():
                return {"ok": True, "mode": "silent", "via": "lynx",
                        "text": r.stdout[:20000]}
        except Exception:
            pass
    curl = shutil.which("curl")
    if curl:
        try:
            r = subprocess.run([curl, "-sL", "--max-time", str(int(timeout)),
                                url], capture_output=True, text=True,
                               timeout=timeout + 5, errors="replace")
            if r.returncode == 0:
                return {"ok": True, "mode": "silent", "via": "curl",
                        "text": r.stdout[:20000]}
        except Exception:
            pass
    # Python fallback (no external browser needed).
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read(20000).decode("utf-8", errors="replace")
        return {"ok": True, "mode": "silent", "via": "urllib",
                "text": data}
    except Exception as exc:
        return {"ok": False, "mode": "silent", "url": url,
                "detail": str(exc)}


def browser_open(url: str, *, visible: bool = False) -> dict:
    """The tool's entry: visible → default browser; silent → fetch."""
    if visible:
        return default_browser_open(url)
    return silent_fetch(url)
