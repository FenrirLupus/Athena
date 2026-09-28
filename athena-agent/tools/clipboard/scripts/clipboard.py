"""Built-in clipboard tool — the system clipboard (one tool).

An EXPANSION of the filesystem tools (the Operator's 08-12 spec): read and
write the system clipboard so text can move between the agent's world
and the operator's desktop. Uses xclip/xsel/wl-clipboard when present,
else cua-driver's clipboard tools.
"""

import json
import shutil
import subprocess


def _runner(args: list[str], timeout: float = 10.0) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _read(args: dict, timeout: float = 10.0) -> str:
    # Prefer cua-driver's clipboard (it manages the display session).
    try:
        from core import cua
        res = cua.call("clipboard_read", {"include_text": True},
                       timeout=timeout)
        txt = ""
        if isinstance(res, dict):
            for k, v in res.items():
                if isinstance(v, str) and v and k in ("text", "clipboard",
                                                      "plain_text", "value"):
                    txt = v
                    break
        if txt:
            return json.dumps({"ok": True, "clipboard": txt[:4000]},
                              ensure_ascii=False)
    except Exception:
        pass
    # Fallback: wl-paste (Wayland) / xclip / xsel with the display env.
    if shutil.which("wl-paste"):
        out = _runner(["wl-paste"], timeout)
        if out:
            return json.dumps({"ok": True, "clipboard": out[:4000]},
                              ensure_ascii=False)
    if shutil.which("xclip"):
        out = _runner(["xclip", "-selection", "clipboard", "-o"], timeout)
        if out:
            return json.dumps({"ok": True, "clipboard": out[:4000]},
                              ensure_ascii=False)
    if shutil.which("xsel"):
        out = _runner(["xsel", "--clipboard", "--output"], timeout)
        if out:
            return json.dumps({"ok": True, "clipboard": out[:4000]},
                              ensure_ascii=False)
    return json.dumps({"ok": False, "detail": "no clipboard tool available"},
                      ensure_ascii=False)


def _write(args: dict, timeout: float = 10.0) -> str:
    text = str(args.get("text", ""))
    # Prefer cua-driver's clipboard (manages the display session).
    try:
        from core import cua
        res = cua.call("clipboard_write", {"text": text}, timeout=timeout)
        if isinstance(res, dict) and res.get("ok"):
            return json.dumps({"ok": True, "copied": len(text)},
                              ensure_ascii=False)
    except Exception:
        pass
    # Fallback: wl-copy / xclip with the desktop env.
    env = dict(os.environ)
    if not env.get("WAYLAND_DISPLAY"):
        import glob
        sockets = glob.glob(f"/run/user/{os.getuid()}/wayland-*")
        if sockets:
            env["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if shutil.which("wl-copy"):
        try:
            r = subprocess.run(["wl-copy"], input=text, capture_output=True,
                               text=True, timeout=timeout, env=env)
            if r.returncode == 0:
                return json.dumps({"ok": True, "copied": len(text)},
                                  ensure_ascii=False)
        except Exception:
            pass
    if shutil.which("xclip"):
        try:
            r = subprocess.run(["xclip", "-selection", "clipboard"],
                               input=text, capture_output=True, text=True,
                               timeout=timeout, env=env)
            if r.returncode == 0:
                return json.dumps({"ok": True, "copied": len(text)},
                                  ensure_ascii=False)
        except Exception:
            pass
    return json.dumps({"ok": False, "detail": "no clipboard tool available"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    for name, desc, fn, props, req in (
        ("clipboard_read", "Read the system clipboard text.", _read,
         {}, []),
        ("clipboard_write", "Write text to the system clipboard.", _write,
         {"text": {"type": "string"}}, ["text"]),
    ):
        register(Tool(
            name=name,
            description=desc,
            parameters={"type": "object", "properties": props,
                        "required": req},
            fn=fn,
        ))
    return ["clipboard_read", "clipboard_write"]
