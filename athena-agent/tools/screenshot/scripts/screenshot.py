"""Built-in screenshot tool — visual capture for vision models (one tool).

Snaps a screenshot of the desktop (or a window) and returns it as
base64 PNG — the input a vision model uses to navigate visually. This
is the hand-in-hand partner of the browser/computer tools (the Operator's
08-12 spec: screenshot + vision model = autonomous visual navigation).
"""

import json
import base64
import tempfile
from pathlib import Path


def _screenshot(args: dict, timeout: float = 30.0) -> str:
    from core import cua
    from core.desktop import capture as desktop_capture, monitors as _monitors
    app = str(args.get("app", "")).strip()
    mode = str(args.get("mode", "vision")).strip()

    # The monitors action: enumerate the CONNECTED displays (the Operator's
    # multi-monitor spec — focus any of 1..N by resolution).
    if str(args.get("action", "")) == "monitors":
        return json.dumps({"ok": True, "monitors": _monitors(timeout)},
                          ensure_ascii=False)

    tmp = Path(tempfile.mkdtemp(prefix="athena-shot-"))
    shot = tmp / "shot.png"

    # 1. The DESKTOP capture (the Operator's 08-12 multi-env layer): KDE
    #    spectacle → GNOME screenshot → X11 import. Works on Wayland,
    #    KDE, and GNOME with NO cua-driver dependency. monitor=N
    #    focuses one connected display (the Operator's multi-monitor spec).
    monitor = int(args.get("monitor", 0) or 0)
    captured = desktop_capture(str(shot), timeout=timeout, monitor=monitor)
    if not captured.get("ok"):
        # 2. Fallback: cua-driver's capture (window-scoped).
        payload = {"screenshot_out_file": str(shot)}
        if app:
            payload["app"] = app
        cua.call("get_desktop_state", payload, timeout=timeout)
    if not (shot.exists() and shot.stat().st_size > 0):
        return json.dumps({"ok": False,
                           "detail": captured.get("detail", "screenshot failed")},
                          ensure_ascii=False)
    # 3. Per-monitor focus: crop to the requested display's geometry.
    if monitor > 0:
        from core.desktop import crop_monitor
        crop = crop_monitor(str(shot), monitor)
        if not crop.get("ok"):
            return json.dumps(crop, ensure_ascii=False)
    b64 = base64.b64encode(shot.read_bytes()).decode()
    # The vision routing (the Operator's 08-12 spec): if `describe` is set,
    # send the capture to the configured vision model (LM Studio Qwen)
    # and return its answer alongside the image.
    prompt = str(args.get("prompt", "")).strip()
    if args.get("describe"):
        try:
            from core.vision import describe
            answer = describe(shot, prompt, timeout=timeout)
            return json.dumps({
                "ok": True,
                "mode": mode,
                "capture_via": captured.get("via", "cua"),
                "png_b64": b64,
                "size_bytes": shot.stat().st_size,
                "mime": "image/png",
                "vision": json.loads(answer),
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "detail": f"vision error: {exc}"},
                              ensure_ascii=False)
    return json.dumps({
        "ok": True,
        "mode": mode,
        "capture_via": captured.get("via", "cua"),
        "png_b64": b64,
        "size_bytes": shot.stat().st_size,
        "mime": "image/png",
    }, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="screenshot",
        description="Take a screenshot of the desktop (or one monitor) "
                    "and return it as base64 PNG — for vision models to "
                    "navigate visually. monitors: enumerate the CONNECTED "
                    "displays. monitor=N focuses one (1..N). With "
                    "describe=true, also routes the capture to the "
                    "configured vision model (LM Studio Qwen) and returns "
                    "its answer.",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["capture", "monitors"]},
                "app": {"type": "string",
                        "description": "Optional app/window to capture"},
                "mode": {"type": "string",
                         "enum": ["vision", "som", "ax"],
                         "description": "Capture mode"},
                "monitor": {"type": "integer",
                            "description": "Focus monitor N (1-based)"},
                "describe": {"type": "boolean",
                             "description": "Route to the vision model"},
                "prompt": {"type": "string",
                           "description": "Vision prompt (with describe)"},
            },
            "required": [],
        },
        fn=_screenshot,
    ))
    return ["screenshot"]
