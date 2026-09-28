"""Built-in browser tool — Athena's OWN isolated browser (one tool).

The Operator's 08-12 split:
  - BROWSER = Athena's isolated SILENT browser (never the operator's
    Chrome): terminal fetch (lynx/curl — no window) for pages, and the
    vision loop feeds page captures to the vision model.
  - COMPUTER = control of the OPERATOR's PC (click/type/scroll — the
    computer tool).

Actions:
  fetch  — get a page's TEXT silently (lynx → curl → urllib fallback)
  open   — open a URL (silent default; visible only when asked)
  vision — fetch a page and hand it to the vision model (via
           core.vision) so the agent "sees" the page without a window
"""

import json
import shutil
import subprocess


def _fetch_page(url: str, timeout: float = 30.0) -> dict:
    """Silent fetch: lynx → curl → urllib. Returns {text}."""
    lynx = shutil.which("lynx")
    if lynx:
        try:
            r = subprocess.run([lynx, "-dump", "-nolist", url],
                               capture_output=True, text=True,
                               timeout=timeout, errors="replace")
            if r.returncode == 0 and r.stdout.strip():
                return {"ok": True, "via": "lynx", "text": r.stdout[:20000]}
        except Exception:
            pass
    curl = shutil.which("curl")
    if curl:
        try:
            r = subprocess.run([curl, "-sL", "--max-time", str(int(timeout)),
                                url], capture_output=True, text=True,
                               timeout=timeout + 5, errors="replace")
            if r.returncode == 0:
                return {"ok": True, "via": "curl", "text": r.stdout[:20000]}
        except Exception:
            pass
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read(20000).decode("utf-8", errors="replace")
        return {"ok": True, "via": "urllib", "text": data}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _browser(args: dict, timeout: float = 30.0) -> str:
    action = str(args.get("action", "fetch")).strip()
    url = str(args.get("url", "")).strip()

    if action == "fetch":
        if not url:
            return json.dumps({"ok": False, "detail": "url required"},
                              ensure_ascii=False)
        r = _fetch_page(url, timeout)
        return json.dumps(r, ensure_ascii=False)

    if action == "open":
        if not url:
            return json.dumps({"ok": False, "detail": "url required"},
                              ensure_ascii=False)
        visible = bool(args.get("visible"))
        if visible:
            from core.browser import default_browser_open
            r = default_browser_open(url)
            return json.dumps(r, ensure_ascii=False)
        r = _fetch_page(url, timeout)
        r["mode"] = "silent"
        return json.dumps(r, ensure_ascii=False)

    if action == "vision":
        # Fetch a page silently, then hand it to the vision model via
        # the screenshot/vision loop (core.vision). The agent "sees"
        # the page without opening any window.
        if not url:
            return json.dumps({"ok": False, "detail": "url required"},
                              ensure_ascii=False)
        r = _fetch_page(url, timeout)
        if not r.get("ok"):
            return json.dumps(r, ensure_ascii=False)
        prompt = str(args.get("prompt", "")).strip()
        try:
            from core.vision import ask_text
            text = ("Here is the page content:\n" + r.get("text", "")[:6000])
            if prompt:
                text = prompt + "\n\n" + text
            answer = ask_text(text, timeout=timeout)
            return json.dumps({
                "ok": True, "url": url, "page_text": r.get("text", "")[:2000],
                "vision": json.loads(answer),
            }, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "detail": f"vision error: {exc}"},
                              ensure_ascii=False)

    return json.dumps({"ok": False, "detail": f"unknown action: {action}"},
                      ensure_ascii=False)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="browser",
        description="Athena's OWN isolated silent browser (the Operator's "
                    "08-12 split — never the operator's Chrome): fetch "
                    "(page text via lynx/curl, no window), open (silent "
                    "default), vision (fetch + route to the vision model "
                    "so the agent sees the page).",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["fetch", "open", "vision"]},
                "url": {"type": "string", "description": "Page URL"},
                "visible": {"type": "boolean",
                            "description": "Open visibly (default silent)"},
                "prompt": {"type": "string", "description": "Vision prompt"},
            },
            "required": ["action"],
        },
        fn=_browser,
    ))
    return ["browser"]
