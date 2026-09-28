---
name: computer
description: "Background desktop control — capture, click, type, key, scroll, drag, apps."
---

# Computer

The **computer** tool drives the desktop in the background via
cua-driver — WITHOUT stealing the operator's cursor or focus. The agent
and the operator can co-work on the same machine.

## Tools

- `computer` — capture (SOM screenshot), click, double_click,
  right_click, type, key, scroll, drag, list_apps, list_windows,
  launch_app

## Usage

```
computer {"action": "capture", "mode": "som"}
computer {"action": "click", "element": 14}
computer {"action": "click", "pid": 2289402, "coordinate": [2000, 500]}
computer {"action": "type", "text": "hello"}
computer {"action": "key", "keys": "ctrl+s"}
computer {"action": "list_apps"}
```

## Delivery modes (the verify→escalate ladder)

- `delivery_mode: background` (default) — input without stealing the
  operator's cursor/focus. Works for most surfaces.
- `delivery_mode: foreground` — the escalation rung. Chromium/Electron
  surfaces (occluded renderers) refuse background input; retry with
  foreground to deliver. The operator may see the window briefly raise.

Always try background first; escalate to foreground only when the
surface refuses.

## Vision loop

Capture returns a screenshot with numbered overlays (SOM) — a vision
model clicks by element index, then captures again to verify. This is
the autonomous visual operation loop.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/computer.py` — registers `computer`.

---
---
