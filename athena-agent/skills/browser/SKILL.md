---
name: browser
description: "Use the built-in browser tool — Athena's own isolated silent browser (fetch/open/vision)."
---

# Browser

The built-in `browser` tool is Athena's OWN isolated silent browser
(the Operator's 08-12 split — it never touches the operator's Chrome; the
computer tool controls the operator's PC):

```
browser {"action": "fetch", "url": "https://example.com"}
browser {"action": "open", "url": "https://example.com"}
browser {"action": "open", "url": "https://example.com", "visible": true}
browser {"action": "vision", "url": "https://example.com", "prompt": "Summarize"}
```

`fetch` gets the page text silently (lynx/curl, no window). `vision`
fetches + routes to the vision model so the agent "sees" the page.
Use when the agent needs a page's content — isolated, never the
operator's browser.

**Requirements:** `fetch`/`open`/`vision` are keyless (lynx/curl — no
credentials). The Chrome DevTools MCP connect needs `npx` on PATH and
`--executablePath` pointing at a Chrome binary (e.g. the Flatpak
Chrome) — no API key required.

---
---
