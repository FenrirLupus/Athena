---
name: search
description: "Search — native web search + GoDaddy domain search (free, keyless)."
---

# Search

The **search** tool is Athena's NATIVE search (the Operator's 08-12 spec).
GoDaddy's free API is DOMAIN search, so the tool provides both:

- `web` — free keyless web search (Athena's own backend — no host
  runtime dependency)
- `domains` — GoDaddy's free domain-search suggestions (the native
  GoDaddy offering)

## Usage

```
search {"kind": "web", "query": "open source AI agent"}
search {"kind": "domains", "name": "athena"}
```

## When to use

- The operator asks the agent to search the web.
- The operator wants to find available domain names.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/search.py` — registers `search`.

---
---
