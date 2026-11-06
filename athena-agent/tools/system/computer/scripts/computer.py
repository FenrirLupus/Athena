"""computer — a mouse, a keyboard, a screen, and a browser.

This is the layer closest to being *someone at the machine*, so it is the one that starts switched off:

    tools:
      computer:
        enabled: false        # must be turned on deliberately

Capabilities are detected, never assumed. This box has no `grim`, no browser and no playwright — so rather than a
tool that pretends, each verb answers with what it needs:

    {"ok": false, "error": "no screen-capture tool found; tried grim, spectacle, gnome-screenshot, import"}

Wayland and X11 are both handled, because a Bazzite/KDE desktop is Wayland while half the tools still speak X11:

| verb | Wayland | X11 |
|---|---|---|
| screenshot | grim, spectacle -b -n -o, gnome-screenshot -f | import -window root, gnome-screenshot |
| pointer / keys | ydotool (needs ydotoold running) | xdotool |
| browser | chromium/chrome/firefox `--headless` | same |

Screenshots are written into `sandbox/screenshots/` and the **path** is returned, not the pixels: a base64 PNG in a
transcript is thousands of tokens the model cannot use.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from core import trace
from tools.registry import Tool, ToolError


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile whose screen is being driven."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")

# KDE's compositor does not speak wlr-screencopy, so grim fails there and spectacle (KWin's own API) is the way in.
CAPTURE_KDE = (("spectacle", ["spectacle", "-b", "-n", "-o", "{out}"]),
               ("grim", ["grim", "{out}"]),
               ("gnome-screenshot", ["gnome-screenshot", "-f", "{out}"]))
CAPTURE_WAYLAND = (("grim", ["grim", "{out}"]),
                   ("spectacle", ["spectacle", "-b", "-n", "-o", "{out}"]),
                   ("gnome-screenshot", ["gnome-screenshot", "-f", "{out}"]))
CAPTURE_X11 = (("import", ["import", "-window", "root", "{out}"]),
               ("gnome-screenshot", ["gnome-screenshot", "-f", "{out}"]),
               ("scrot", ["scrot", "{out}"]))
BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "brave", "firefox")

# ydotool wants raw Linux key codes; it accepts a name and then silently does nothing, which looks identical to a
# keypress the compositor ignored. So names are translated here rather than passed through.
KEYCODES = {
    "escape": 1, "esc": 1, "tab": 15, "enter": 28, "return": 28, "space": 57, "backspace": 14, "delete": 111,
    "home": 102, "end": 107, "pageup": 104, "pagedown": 109,
    "left": 105, "right": 106, "up": 103, "down": 108,
    "ctrl": 29, "control": 29, "alt": 56, "shift": 42, "super": 125, "meta": 125, "win": 125,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66, "f9": 67, "f10": 68,
    "f11": 87, "f12": 88,
}
KEYCODES.update({chr(97 + i): code for i, code in enumerate(
    [30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 22, 17, 45, 21, 44, 2, 3, 4, 5, 6, 7])})
KEYCODES.update({str(i): code for i, code in zip(range(1, 10), range(2, 11))})
KEYCODES["0"] = 11
BUTTONS = {"left": "0x110", "right": "0x111", "middle": "0x112"}


def _host_prefix() -> list[str]:
    """When the server runs inside the distrobox, the desktop tools live on the host.

    `distrobox-host-exec` runs a command on the host as the same user, with the compositor session already in reach.
    Without it, GUI capture and input simply do not exist inside a container — the sockets are mounted, but the
    binaries and (for uinput) the permission are on the other side of the wall.
    """
    inside_container = os.environ.get("container") or Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if inside_container and shutil.which("distrobox-host-exec"):
        return ["distrobox-host-exec"]
    return []


def _call(argv: list[str], timeout: float):
    return subprocess.run([*_host_prefix(), *argv], capture_output=True, text=True, timeout=timeout)


def _desktop_kind() -> str:
    current = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "kde" in current or "plasma" in current:
        return "kde"
    return "wayland" if os.environ.get("WAYLAND_DISPLAY") else ("x11" if os.environ.get("DISPLAY") else "none")


def _first(commands):
    for name, template in commands:
        if shutil.which(name):
            return name, template
    return None, None


def build(ctx) -> list[Tool]:
    cfg = (ctx.cfg or {})
    block = cfg.get("tools") if isinstance(cfg.get("tools"), dict) else {}
    comp = (block or {}).get("computer") if isinstance((block or {}).get("computer"), dict) else {}
    enabled = bool((comp or {}).get("enabled", False))
    timeout = float((comp or {}).get("timeout_s") or 30)

    def guard_on():
        if not enabled:
            raise ToolError("the computer tool is switched off (tools.computer.enabled: false) — "
                            "moving the real pointer needs a deliberate decision, not a default")

    def sandbox_dir() -> Path:
        base = (ctx.profile_root / "sandbox") if ctx.profile_root else Path.cwd()
        folder = base / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def desktop() -> str:
        return "wayland" if os.environ.get("WAYLAND_DISPLAY") else ("x11" if os.environ.get("DISPLAY") else "none")

    def do_screenshot(name: str = "") -> dict:
        with trace.span("tool.computer.screenshot", channel=_lane(ctx), file=name or "auto",
                        desktop=_desktop_kind()) as sp:
            guard_on()
            folder = sandbox_dir()
            out = folder / (name or f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png")
            kind = _desktop_kind()
            order = CAPTURE_KDE if kind == "kde" else (CAPTURE_WAYLAND if kind == "wayland" else CAPTURE_X11)
            binary, template = _first(order)
            if not binary:
                tried = ", ".join(b for b, _ in order)
                sp.ok(ok=False, refused="no capture tool", tried=tried)
                return {"ok": False, "desktop": desktop(),
                        "error": f"no screen-capture tool found; tried {tried}",
                        "install": "sudo rpm-ostree install grim   (Wayland) or imagemagick (X11: import)"}
            argv = [part.replace("{out}", str(out)) for part in template]
            try:
                done = _call(argv, timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                sp.failed(exc)
                return {"ok": False, "error": str(exc), "tool": binary}
            if not out.is_file():
                sp.ok(ok=False, tool=binary, bytes=0, capture="no file")
                return {"ok": False, "tool": binary, "desktop": desktop(),
                        "error": (done.stderr or "capture produced no file").strip()[:400]}
            sp.ok(ok=True, tool=binary, bytes=out.stat().st_size, path=str(out))
            return {"ok": True, "path": str(out), "bytes": out.stat().st_size, "tool": binary, "desktop": desktop()}

    def _input(args: list[str]) -> dict:
        if shutil.which("ydotool"):
            argv = ["ydotool"] + args
            tool = "ydotool"
        elif shutil.which("xdotool"):
            mapping = {"mousemove": "mousemove", "click": "click", "type": "type", "key": "key"}
            verb, rest = args[0], args[1:]
            if verb not in mapping:
                raise ToolError(f"{verb} is not available through xdotool")
            argv = ["xdotool", mapping[verb]] + rest
            tool = "xdotool"
        else:
            return {"ok": False, "error": "no input tool found (ydotool on Wayland, xdotool on X11)",
                    "install": "sudo rpm-ostree install ydotool   (and enable ydotoold)"}
        try:
            done = _call(argv, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "tool": tool, "error": str(exc)}
        result = {"ok": done.returncode == 0, "tool": tool, "exit": done.returncode,
                  "error": (done.stderr or "").strip()[:300] or None}
        if not result["ok"] and tool == "ydotool" and done.returncode in (1, 2):
            # ydotool cannot inject without its daemon, and says nothing useful when it is missing
            result["hint"] = ("ydotoold is not running — it owns /dev/uinput and ydotool talks to it over a socket. "
                              "Start it once as a user service: systemctl --user enable --now ydotoold")
        return result

    def _move(x: int, y: int) -> dict:
        """Absolute first; if the daemon ignores it, clamp to the origin with a big relative move, then step.

        `ydotool mousemove --absolute X Y` is accepted and silently ignored by some builds — exit 0 and no movement,
        which is the worst kind of failure. The flags this build documents are `-a -x <n> -y <n>`.
        """
        absolute = _input(["mousemove", "-a", "-x", str(int(x)), "-y", str(int(y))])
        if absolute.get("ok"):
            return absolute
        _input(["mousemove", "--", "-6000", "-6000"])
        return _input(["mousemove", "--", str(int(x)), str(int(y))])

    def move_mouse(x: int = 0, y: int = 0) -> dict:
        with trace.span("tool.computer.mouse_move", channel=_lane(ctx), x=int(x), y=int(y)) as sp:
            guard_on()
            out = _move(x, y)
            sp.ok(ok=bool(out.get("ok")), tool=out.get("tool"))
            return out

    def click(x: int = 0, y: int = 0, button: str = "left") -> dict:
        with trace.span("tool.computer.mouse_click", channel=_lane(ctx), x=int(x), y=int(y),
                        button=str(button)) as sp:
            guard_on()
            moved = _move(x, y)
            if not moved.get("ok"):
                sp.ok(ok=False, tool=moved.get("tool"), reason="pointer did not move")
                return moved
            code = BUTTONS.get(str(button).lower())
            if code is None:
                raise ToolError(f"unknown button {button!r}; use left, right or middle")
            out = _input(["click", code])
            sp.ok(ok=bool(out.get("ok")), tool=out.get("tool"), exit=out.get("exit"))
            return out

    def type_text(text: str = "") -> dict:
        # the text may be anything a person would type; only its length reaches the trail
        with trace.span("tool.computer.type_text", channel=_lane(ctx), chars=len(str(text or ""))) as sp:
            guard_on()
            if not text:
                raise ToolError("text is required")
            out = _input(["type", "--", text])
            sp.ok(ok=bool(out.get("ok")), tool=out.get("tool"), exit=out.get("exit"))
            return out

    def key(combo: str = "") -> dict:
        with trace.span("tool.computer.press_key", channel=_lane(ctx), combo=str(combo)) as sp:
            guard_on()
            if not combo:
                raise ToolError("combo is required, e.g. 'ctrl+alt+t'")
            parts = [part.strip().lower() for part in str(combo).split("+") if part.strip()]
            codes = []
            for part in parts:
                if part not in KEYCODES:
                    raise ToolError(f"unknown key {part!r}; known names include escape, tab, enter, space, "
                                    f"delete, home, end, arrows, ctrl, alt, shift, super, f1-f12, letters, digits")
                codes.append(KEYCODES[part])
            # press in order, release in reverse — 'ctrl+c' must not release ctrl before c
            sequence = [f"{code}:1" for code in codes] + [f"{code}:0" for code in reversed(codes)]
            result = _input(["key"] + sequence)
            result["codes"] = codes
            sp.ok(ok=bool(result.get("ok")), keys=len(codes), tool=result.get("tool"))
            return result

    def browser(url: str = "", mode: str = "screenshot", wait_s: float = 3.0) -> dict:
        with trace.span("tool.computer.browser", channel=_lane(ctx), url_chars=len(str(url or "")),
                        mode=str(mode)) as sp:
            guard_on()
            if not url:
                raise ToolError("url is required")
            binary = next((b for b in BROWSERS if shutil.which(b)), "")
            if not binary:
                sp.ok(ok=False, refused="no browser")
                return {"ok": False, "error": "no browser found on this machine",
                        "install": "install chromium or firefox, or `pip install playwright` in the model runtime"}
            folder = sandbox_dir()
            out = folder / f"page-{time.strftime('%Y%m%d-%H%M%S')}.png"
            if binary == "firefox":
                argv = [binary, "--headless", "--screenshot", str(out), url]
            elif mode == "dom":
                argv = [binary, "--headless=new", "--disable-gpu", "--dump-dom", url]
            else:
                argv = [binary, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--screenshot={out}", "--window-size=1280,800", url]
            try:
                done = _call(argv, max(timeout, float(wait_s) + 25))
            except (OSError, subprocess.TimeoutExpired) as exc:
                sp.failed(exc)
                return {"ok": False, "browser": binary, "error": str(exc)}
            if mode == "dom":
                sp.ok(ok=done.returncode == 0, browser=binary, chars=len(done.stdout or ""))
                return {"ok": done.returncode == 0, "browser": binary, "dom": (done.stdout or "")[:20000]}
            if not out.is_file():
                sp.ok(ok=False, browser=binary, bytes=0, capture="no file")
                return {"ok": False, "browser": binary, "error": (done.stderr or "no screenshot").strip()[:400]}
            sp.ok(ok=True, browser=binary, bytes=out.stat().st_size, path=str(out))
            return {"ok": True, "browser": binary, "path": str(out), "bytes": out.stat().st_size}

    def _kwin_script(name: str, body: str) -> dict:
        """Run a KWin script through org.kde.KWin.Scripting.

        This is how a window is acted on *by identity* instead of by screen coordinate: no pointer, no guessing where
        a button is, and it works on Wayland where synthetic clicks are refused.
        """
        folder = sandbox_dir() / "kwin"
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / f"{name}.js"
        script.write_text(body, encoding="utf-8")
        load = _call(["qdbus", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.loadScript", str(script)],
                     timeout)
        if load.returncode != 0:
            return {"ok": False, "error": (load.stderr or load.stdout or "loadScript failed").strip()[:300],
                    "hint": "KWin scripting needs qdbus and a running Plasma session"}
        start = _call(["qdbus", "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.start"], timeout)
        return {"ok": start.returncode == 0, "script": str(script),
                "script_id": (load.stdout or "").strip()[:40] or None,
                "error": (start.stderr or "").strip()[:300] or None}

    def _window_action(match: str, action: str) -> dict:
        if not (match or "").strip():
            raise ToolError("match is required — a window class or part of its caption, e.g. 'dolphin'")
        body = (
            "const wanted = " + repr(str(match).lower()) + ";\n"
            "const list = (workspace.windowList ? workspace.windowList() : workspace.clientList());\n"
            "let hit = 0;\n"
            "for (const win of list) {\n"
            "  const cls = (win.resourceClass ? String(win.resourceClass) : '').toLowerCase();\n"
            "  const cap = (win.caption ? String(win.caption) : '').toLowerCase();\n"
            f"  if (cls.indexOf(wanted) !== -1 || cap.indexOf(wanted) !== -1) {{ {action}; hit += 1; }}\n"
            "}\n")
        # the name must be unique per action AND match: KWin hands back the existing id for a path it already
        # loaded, so a reused filename silently re-runs the previous script
        slug = re.sub(r"[^a-z0-9]+", "-", f"{action}-{match}".lower()).strip("-")[:40]
        result = _kwin_script(f"win-{slug}-{int(time.time() * 1000)}", body)
        result["match"] = match
        result["action"] = action
        return result

    def window_minimize(match: str = "") -> dict:
        with trace.span("tool.computer.window_minimize", channel=_lane(ctx), match=str(match)) as sp:
            guard_on()
            out = _window_action(match, "win.minimized = true")
            sp.ok(ok=bool(out.get("ok")), script_id=out.get("script_id"))
            return out

    def window_restore(match: str = "") -> dict:
        with trace.span("tool.computer.window_restore", channel=_lane(ctx), match=str(match)) as sp:
            guard_on()
            out = _window_action(match, "win.minimized = false; win.activate()")
            sp.ok(ok=bool(out.get("ok")), script_id=out.get("script_id"))
            return out

    def capabilities() -> dict:
        with trace.span("tool.computer.screen_capabilities", channel=_lane(ctx)) as sp:
            capture = _first(CAPTURE_WAYLAND if desktop() == "wayland" else CAPTURE_X11)[0]
            out = {"enabled": enabled, "desktop": desktop(), "compositor": _desktop_kind(),
                   "bridge": "distrobox-host-exec" if _host_prefix() else "local",
                   "screenshot": capture or "none",
                   "input": "ydotool" if shutil.which("ydotool") else ("xdotool" if shutil.which("xdotool") else "none"),
                   "browser": next((b for b in BROWSERS if shutil.which(b)), "none"),
                   "note": "set tools.computer.enabled: true in config.yaml to use these" if not enabled else ""}
            sp.ok(enabled=enabled, desktop=out["desktop"], screenshot=out["screenshot"], input=out["input"])
            return out

    return [
        Tool("screen_capabilities", "What screen capture, input and browser this machine actually offers.",
             {"type": "object", "properties": {}, "required": []}, capabilities),
        Tool("screenshot", "Capture the screen to sandbox/screenshots/ and return the path.",
             {"type": "object", "properties": {"name": {"type": "string", "description": "Optional filename."}},
              "required": []}, do_screenshot),
        Tool("mouse_move", "Move the pointer to absolute screen coordinates.",
             {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
              "required": ["x", "y"]}, move_mouse),
        Tool("mouse_click", "Move to coordinates and click a button.",
             {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                                               "button": {"type": "string",
                                                          "enum": ["left", "right", "middle"]}},
              "required": ["x", "y"]}, click),
        Tool("type_text", "Type text at the focused window.",
             {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, type_text),
        Tool("window_minimize", "Minimise windows by class or caption match, via KWin — no pointer needed.",
             {"type": "object", "properties": {"match": {"type": "string",
                                                        "description": "Window class or caption fragment, e.g. 'dolphin'."}},
              "required": ["match"]}, window_minimize),
        Tool("window_restore", "Restore and focus windows by class or caption match, via KWin.",
             {"type": "object", "properties": {"match": {"type": "string",
                                                        "description": "Window class or caption fragment."}},
              "required": ["match"]}, window_restore),
        Tool("press_key", "Press a key combination, e.g. ctrl+alt+t.",
             {"type": "object", "properties": {"combo": {"type": "string"}}, "required": ["combo"]}, key),
        Tool("browser", "Open a URL headlessly: screenshot it, or dump its DOM.",
             {"type": "object", "properties": {"url": {"type": "string"},
                                               "mode": {"type": "string", "enum": ["screenshot", "dom"],
                                                        "description": "screenshot (default) or dom."},
                                               "wait_s": {"type": "number"}},
              "required": ["url"]}, browser),
    ]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "computer")
