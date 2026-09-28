"""Built-in search tool — web search + GoDaddy domain search (one tool).

The Operator's 08-12 spec: a native search engine tool. GoDaddy's free API
is DOMAIN search (find/register domain names — the native GoDaddy
offering), so this tool provides BOTH:

  web     — free keyless web search (Athena's OWN backend — the
            existing web_search depended on the host runtime; this is
            native, no external runtime dependency)
  domains — GoDaddy's free domain-search API (api.godaddy.com, keyless
            suggestions endpoint)

Every backend is free + keyless. Results are honest: failures report
the reason.
"""

import json
import re
import urllib.parse
import urllib.request


def _fetch(url: str, timeout: float = 12.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Athena/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode(errors="replace")
    except Exception:
        return ""


def _web(args: dict, timeout: float = 12.0) -> str:
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 5) or 5)
    if not query:
        return json.dumps({"ok": False, "detail": "query required"},
                          ensure_ascii=False)
    # DuckDuckGo Lite HTML (keyless, no JS).
    html = _fetch("https://lite.duckduckgo.com/lite/?q=" +
                  urllib.parse.quote(query), timeout)
    results = []
    if html:
        # Lite layout: <a rel="nofollow" href="URL">TITLE</a> + snippet.
        links = re.findall(r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>',
                           html, re.S)
        snippets = re.findall(r'<td class="result-snippet">(.*?)</td>', html, re.S)
        for i, (url, title) in enumerate(links[:limit]):
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            results.append({
                "url": urllib.parse.unquote(url),
                "title": clean_title,
                "description": re.sub(r"<[^>]+>", "", snippets[i]).strip()
                if i < len(snippets) else "",
            })
    if results:
        return json.dumps({"ok": True, "engine": "duckduckgo-lite",
                           "results": results}, ensure_ascii=False)
    return json.dumps({"ok": False, "detail": "no results (backend blocked?)"},
                      ensure_ascii=False)


def _domains(args: dict, timeout: float = 12.0) -> str:
    """GoDaddy's free domain-search suggestions (the native API)."""
    name = str(args.get("name", "")).strip()
    if not name:
        return json.dumps({"ok": False, "detail": "name required"},
                          ensure_ascii=False)
    # GoDaddy's public suggestions endpoint (keyless).
    url = ("https://api.godaddy.com/v1/domains/suggestions?"
           + urllib.parse.urlencode({"query": name, "limit": 10}))
    html = _fetch(url, timeout)
    if not html:
        return json.dumps({"ok": False,
                           "detail": "GoDaddy suggestions unavailable "
                                     "(may require an API key)"},
                          ensure_ascii=False)
    try:
        data = json.loads(html)
        domains = [{"domain": d.get("domain", ""),
                    "available": d.get("available", False)}
                   for d in data]
        return json.dumps({"ok": True, "engine": "godaddy",
                           "domains": domains}, ensure_ascii=False)
    except ValueError:
        return json.dumps({"ok": False,
                           "detail": "GoDaddy suggestions need a key: "
                                     "the free native API requires a "
                                     "GoDaddy developer key (see "
                                     "developer.godaddy.com)"},
                          ensure_ascii=False)


def _search(args: dict, timeout: float = 12.0) -> str:
    kind = str(args.get("kind", "web")).strip()
    if kind == "domains":
        return _domains(args, timeout)
    return _web(args, timeout)


def register() -> list[str]:
    from filesystem.tools import Tool, register
    register(Tool(
        name="search",
        description="Search — the native search tool (the Operator's 08-12 "
                    "spec): web search via free keyless backends, and "
                    "GoDaddy domain search (the free native GoDaddy API). "
                    "kind: web|domains.",
        parameters={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["web", "domains"]},
                "query": {"type": "string", "description": "Web query"},
                "name": {"type": "string", "description": "Domain name (domains)"},
                "limit": {"type": "integer"},
            },
            "required": ["kind"],
        },
        fn=_search,
    ))
    return ["search"]
