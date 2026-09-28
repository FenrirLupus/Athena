---
name: browser
description: "Browser — Athena's OWN isolated silent browser (fetch/open/vision), never the operator's Chrome."
---

# Browser

The **browser** tool is Athena's OWN isolated browser (the Operator's 08-12
split): it never touches the operator's Chrome. The **computer** tool is
the one that controls the operator's PC.

## Tools

- `fetch` — get a page's TEXT silently (lynx → curl → urllib, no
  window)
- `open` — open a URL (silent by default; `visible: true` opens the OS
  browser only when explicitly asked)
- `vision` — fetch a page silently and route it to the vision model, so
  the agent "sees" the page without a window

## Chrome DevTools MCP (the Operator's 08-12 browser integration)

Athena can drive a REAL, isolated Chrome via the Chrome DevTools MCP
server (ChromeDevTools/chrome-devtools-mcp — npx). Connect through the
mcp tool with the isolated flag + the Chrome executable path:

```
mcp {"action": "connect", "name": "chrome", "kind": "stdio",
     "command": ["npx", "-y", "chrome-devtools-mcp@latest",
                 "--executablePath", "/var/lib/flatpak/app/com.google.Chrome/.../files/extra/chrome",
                 "--isolated"]}
```

29 tools register as `mcp_chrome_*` (navigate_page, take_screenshot,
take_snapshot, evaluate_script, click, type_text, list_pages,
performance traces, lighthouse, heap snapshots). The Chrome runs with
its OWN profile (--isolated) — never the operator's browser. Use
`--executablePath` (the -e flag), NOT a CHROME_PATH env (ignored).

## Usage

```
browser {"action": "fetch", "url": "https://example.com"}
browser {"action": "open", "url": "https://example.com"}
browser {"action": "open", "url": "https://example.com", "visible": true}
browser {"action": "vision", "url": "https://example.com", "prompt": "Summarize"}
```

## When to use

- The agent needs a page's content (silent, isolated).
- The operator explicitly asks to SEE a page (visible mode).
- Full browser automation (via the Chrome DevTools MCP connect).

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/browser.py` — registers `browser`.

## Backend

- `core/browser.py` — silent_fetch + default_browser_open
- `core/vision.py` — the vision routing for the vision action

---
---
