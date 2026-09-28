"""Built-in computer tool — background desktop control (one tool).

Operates the computer via the cua-driver (the Operator's 08-12 spec):
capture (SOM overlays), click, double-click, right-click, type, key,
scroll, drag — WITHOUT stealing the operator's cursor or focus. The
screenshot comes back as base64 for vision models.
"""

import json
import tempfile
import base64
from pathlib import Path


def _capture(mode: str = "som", app: str = "", timeout: float = 30.0) -> str:
    from core import cua
    args = {}
    if app:
        args["app"] = app
    # cua-driver's capture: get_desktop_state for the full display.
    tmp = Path(tempfile.mkdtemp(prefix="athena-capture-"))
    shot = tmp / "shot.png"
    args["screenshot_out_file"] = str(shot)
    result = cua.call("get_desktop_state", args, timeout=timeout)
    if shot.exists() and shot.stat().st_size > 0:
        b64 = base64.b64encode(shot.read_bytes()).decode()
        size = shot.stat().st_size
        return json.dumps({"ok": True, "mode": mode, "png_b64": b64,
                           "size_bytes": size}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": result.get("detail", "capture failed")},
                      ensure_ascii=False)


def _computer(args: dict, timeout: float = 30.0) -> str:
    from core import cua
    action = str(args.get("action", "")).strip()
    if not action:
        return json.dumps({"ok": False, "detail": "action required"},
                          ensure_ascii=False)
    # Capture: full-display screenshot (SOM/vision).
    if action == "capture":
        return _capture(mode=str(args.get("mode", "som")),
                        app=str(args.get("app", "")), timeout=timeout)
    if action == "list_apps":
        return json.dumps(cua.call("list_apps", timeout=timeout), ensure_ascii=False)
    if action == "list_windows":
        return json.dumps(cua.call("list_windows", timeout=timeout), ensure_ascii=False)
    if action == "launch_app":
        return json.dumps(cua.call("launch_app",
                                   {"name": args.get("name", "")},
                                   timeout=timeout), ensure_ascii=False)
    if action == "click":
        from core import desktop
        # The desktop-layer fallback (xdotool/ydotool — remote/Linux
        # control that works across X11/Wayland without cua-driver).
        if args.get("coordinate") and not args.get("element"):
            x, y = args.get("coordinate")
            fb = desktop.input_click(int(x), int(y), timeout=timeout)
            if fb.get("ok"):
                return json.dumps(fb, ensure_ascii=False)
        payload = {}
        if args.get("element"):
            payload["element"] = args.get("element")
        if args.get("coordinate"):
            x, y = args.get("coordinate")
            payload["x"], payload["y"] = int(x), int(y)
        if args.get("app"):
            payload["app"] = args.get("app")
        if args.get("pid"):
            payload["pid"] = int(args.get("pid"))
        # The verify→escalate ladder (the Operator's PC-control spec):
        # background (default) → foreground when the surface refuses
        # (e.g. Chromium/Electron occluded renderers).
        dm = str(args.get("delivery_mode", "background")).strip()
        if dm in ("background", "foreground"):
            payload["delivery_mode"] = dm
        return json.dumps(cua.call("click", payload, timeout=timeout),
                          ensure_ascii=False)
    if action in ("type", "key", "scroll", "drag", "double_click",
                  "right_click", "set_value"):
        # The desktop-layer input fallback for type/key (remote/Linux).
        if action == "type" and args.get("text") and not args.get("element"):
            from core import desktop
            fb = desktop.input_type(str(args.get("text")), timeout=timeout)
            if fb.get("ok"):
                return json.dumps(fb, ensure_ascii=False)
        if action == "key" and args.get("keys"):
            from core import desktop
            fb = desktop.input_key(str(args.get("keys")), timeout=timeout)
            if fb.get("ok"):
                return json.dumps(fb, ensure_ascii=False)
        # Pass through the mapped arguments (incl. delivery_mode).
        payload = {k: v for k, v in args.items()
                   if k not in ("action",)}
        if args.get("pid"):
            payload["pid"] = int(args.get("pid"))
        return json.dumps(cua.call(action, payload, timeout=timeout),
                          ensure_ascii=False)
    return json.dumps({"ok": False, "detail": f"unknown action: {action}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="computer",
        description="Drive the desktop in the background via cua-driver: "
                    "capture (SOM screenshot), click, double_click, "
                    "right_click, type, key, scroll, drag, list_apps, "
                    "list_windows, launch_app. Does not steal focus.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["capture", "click", "double_click",
                                    "right_click", "type", "key", "scroll",
                                    "drag", "list_apps", "list_windows",
                                    "launch_app", "set_value"]},
                "mode": {"type": "string", "enum": ["som", "vision", "ax"]},
                "element": {"type": "integer", "description": "SOM element index"},
                "coordinate": {"type": "array", "items": {"type": "integer"},
                               "description": "[x, y] pixel coordinates"},
                "text": {"type": "string"},
                "keys": {"type": "string"},
                "app": {"type": "string"},
                "name": {"type": "string"},
                "pid": {"type": "integer", "description": "Target process pid"},
                "delivery_mode": {"type": "string",
                                  "enum": ["background", "foreground"],
                                  "description": "Input delivery: background "
                                                 "(default) or foreground "
                                                 "(escalation for "
                                                 "Chromium/Electron)"},
            },
            "required": ["action"],
        },
        fn=_computer,
    ))
    return ["computer"]
