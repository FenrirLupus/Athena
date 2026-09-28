---
name: computer
description: "Use the built-in computer tool for background desktop control — capture, click, type, key, scroll."
---

# Computer

The built-in `computer` tool drives the desktop in the background
(cua-driver) without stealing focus:

- `capture` — SOM screenshot with numbered elements
- `click` / `double_click` / `right_click` — by element index or coordinates
- `type` / `key` — keyboard input
- `scroll` / `drag` — navigation
- `list_apps` / `list_windows` / `launch_app`

```json
{"action": "capture", "mode": "som"}
{"action": "click", "element": 14}
{"action": "click", "pid": 2289402, "coordinate": [2000, 500]}
{"action": "click", "pid": 2289402, "coordinate": [2000, 500], "delivery_mode": "foreground"}
```

Try `background` first (default, no focus steal). If a surface refuses
(Chromium/Electron occluded renderers), retry with
`delivery_mode: foreground` — the escalation rung.

Use for autonomous visual desktop operation: capture → see → act →
capture again.

---
---
