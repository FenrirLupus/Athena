---
name: clipboard
description: "Clipboard — read/write the system clipboard (filesystem expansion)."
---

# Clipboard

The **clipboard** tool reads and writes the system clipboard — an
EXPANSION of the filesystem tools (the Operator's 08-12 spec). Text moves
between the agent's world and the operator's desktop.

## Tools

- `clipboard_read` — read the clipboard text
- `clipboard_write` — write text to the clipboard

## Usage

```
clipboard_read {}
clipboard_write {"text": "copied from Athena"}
```

## Backends

Uses wl-clipboard (Wayland), xclip, or xsel — whichever is present.

## When to use

- The operator wants to paste something the agent produced.
- The agent needs the text the operator copied.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/clipboard.py` — registers the clipboard tools.

---
---
