"""Built-in notification tool — desktop notifications (one tool).

The Operator's 08-12 spec: agents can NOTIFY the operator what's going on.
Uses notify-send (Linux desktop) — a popup on the operator's screen.
Reads the DISPLAY/WAYLAND env the same way cua does (a service context
lacks the desktop session vars otherwise).
"""

import json
import os
import shutil
import subprocess


def _notify(args: dict, timeout: float = 10.0) -> str:
    title = str(args.get("title", "Athena")).strip() or "Athena"
    body = str(args.get("body", "")).strip() or ""
    urgency = str(args.get("urgency", "normal")).strip()
    if urgency not in ("low", "normal", "critical"):
        urgency = "normal"
    notify = shutil.which("notify-send")
    if not notify:
        return json.dumps({"ok": False, "detail": "notify-send not available"},
                          ensure_ascii=False)
    # The desktop session env (a service lacks it otherwise).
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
    try:
        r = subprocess.run([notify, "-u", urgency, title, body],
                           capture_output=True, text=True, timeout=timeout,
                           env=env)
        return json.dumps({"ok": r.returncode == 0, "title": title,
                           "body": body[:80], "urgency": urgency,
                           "detail": (r.stderr or "").strip()[:200]},
                          ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "detail": str(exc)}, ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="notify",
        description="Send a desktop notification to the operator "
                    "(notify-send popup). title, body, urgency "
                    "(low|normal|critical).",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "urgency": {"type": "string",
                            "enum": ["low", "normal", "critical"]},
            },
            "required": ["body"],
        },
        fn=_notify,
    ))
    return ["notify"]
