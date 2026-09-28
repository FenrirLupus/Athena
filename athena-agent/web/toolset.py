"""Web Toolset — the web category (the Operator's spec).

The web tools live under one toolset category: browser (hands-off
default-app open / silent terminal fetch), web_search (the configured
search backend), web_extract (clean page text). Registered into the
same tool registry the filesystem tools use.
"""
from __future__ import annotations

import json


def _log_tool_error(name: str, exc: Exception) -> None:
    """Log a web-tool failure to the metrics LOGS (the Operator's 08-12
    audit): the caller sees {"ok": False} in the reply, but the operator
    must ALSO see the error in the terminal log with the tool name —
    a tool that fails silently is a ghost in the house."""
    try:
        from core.logging import log_event
        log_event(4, f"web tool {name} failed: {exc}",
                  source="filesystem", tool=name, action="tool_call")
    except Exception:
        pass


def _browser_impl(args: dict, timeout: float = 60.0) -> str:
    from core.browser import browser_open
    url = str(args.get("url", ""))
    if not url:
        return "error: url required"
    visible = bool(args.get("visible", False))
    r = browser_open(url, visible=visible)
    return json.dumps(r, ensure_ascii=False)[:4000]


def _search_impl(args: dict, timeout: float = 60.0) -> str:
    """web_search — ATHENA'S OWN search backend (the Operator's 08-12
    separation spec: Athena uses athena_tools, never a shared runtime — she
    she runs her own architecture with her own venv).

    DuckDuckGo-lite HTML is parsed directly (no API key, zero tokens —
    the autonomous/local-over-provider doctrine). Returns up to N results.
    """
    import urllib.parse as _up
    import urllib.request as _ur
    import re as _re
    import html as _html
    query = str(args.get("query", ""))
    limit = int(args.get("limit", 5) or 5)
    if not query:
        return "error: query required"
    try:
        # DDG-lite serves results via POST (GET returns the shell page).
        url = "https://lite.duckduckgo.com/lite/"
        data = _up.urlencode({"q": query}).encode("utf-8")
        req = _ur.Request(url, data=data, headers={
            "User-Agent": "Athena/0.1 (+self-hosted research agent)"})
        with _ur.urlopen(req, timeout=timeout) as resp:
            page = resp.read().decode("utf-8", errors="replace")
        # Result links + snippet cells (the lite layout: result-link rows
        # with a result-snippet cell below each).
        links = _re.findall(
            r'<a[^>]+rel="nofollow"[^>]+href="(http[^"]+)"[^>]*>(.*?)</a>',
            page, _re.S | _re.I)
        snips = _re.findall(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>',
            page, _re.S | _re.I)
        out = []
        seen = set()
        for i, (href, title) in enumerate(links):
            if len(out) >= limit:
                break
            u = _html.unescape(href)
            if u in seen:
                continue
            seen.add(u)
            out.append({
                "url": u,
                "title": _re.sub(r"<[^>]+>", "",
                                 _html.unescape(title)).strip()[:120],
                "description": _re.sub(r"<[^>]+>", "",
                                       _html.unescape(snips[i])).strip()[:200]
                if i < len(snips) else "",
            })
        if not out:
            return json.dumps({"ok": True, "results": [],
                               "detail": "no results"}, ensure_ascii=False)
        return json.dumps({"ok": True, "results": out}, ensure_ascii=False)
    except Exception as exc:
        _log_tool_error("web_search", exc)
        return json.dumps({"ok": False, "detail": str(exc)})


def _extract_impl(args: dict, timeout: float = 60.0) -> str:
    """web_extract — the clean text of a page (ATHENA'S OWN extractor;
    the 08-12 separation spec: never a shared runtime).

    Uses the same silent-fetch chain as core.browser (lynx → curl →
    urllib) and strips the tags for clean text. Athena's own venv, her
    own architecture, her own implementation.
    """
    from core.browser import silent_fetch
    urls = args.get("urls", [])
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        return "error: urls required"
    try:
        out = []
        for u in urls[:3]:
            r = silent_fetch(str(u), timeout=timeout)
            if r.get("ok"):
                text = r.get("text", "")
                out.append({"url": u, "content": text[:15000],
                            "error": ""})
            else:
                out.append({"url": u, "content": "",
                            "error": r.get("detail", "fetch failed")})
        return json.dumps({"ok": True, "results": out}, ensure_ascii=False)[:8000]
    except Exception as exc:
        _log_tool_error("web_extract", exc)
        return json.dumps({"ok": False, "detail": str(exc)})


def register() -> list[str]:
    """Register the web toolset's tools. Returns the registered names."""
    from filesystem.tools import Tool, register
    names = []
    tools = [
        Tool(
            "browser_open",
            "Open a URL in the OS default browser (visible) OR fetch its "
            "text silently (visible=false uses a terminal browser/fetch). "
            "Use visible=true when the user should see the page; "
            "visible=false for quiet automation.",
            {"type": "object",
             "properties": {
                 "url": {"type": "string",
                         "description": "the URL to open or fetch"},
                 "visible": {"type": "boolean",
                             "description": "open in the default browser "
                                            "(true) or fetch silently (false)"},
             },
             "required": ["url"]},
            _browser_impl,
        ),
        Tool(
            "web_search",
            "Search the web. Returns up to `limit` results with url, "
            "title, description.",
            {"type": "object",
             "properties": {
                 "query": {"type": "string",
                           "description": "the search query"},
                 "limit": {"type": "integer", "description": "max results"},
             },
             "required": ["query"]},
            _search_impl,
        ),
        Tool(
            "web_extract",
            "Extract the clean text content of one or more web pages.",
            {"type": "object",
             "properties": {
                 "urls": {"type": "array", "items": {"type": "string"},
                          "description": "the URLs to extract"},
             },
             "required": ["urls"]},
            _extract_impl,
        ),
    ]
    for t in tools:
        register(t)
        names.append(t.name)
    return names
