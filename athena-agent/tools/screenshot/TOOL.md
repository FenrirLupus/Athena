---
name: screenshot
description: "Take a screenshot of the desktop (or an app window) and return it as base64 PNG — for vision models to navigate visually. With describe=true, also routes the capture to the configured vision model (LM Studio Qwen) and returns its answer."
---

# Screenshot

The **screenshot** tool snaps the desktop (or an app window) and returns
it as base64 PNG — the input a vision model uses to navigate visually.
Hand-in-hand with the browser and computer tools (the Operator's 08-12 spec:
screenshot + vision model = autonomous visual navigation).

## Capture backends (the Operator's 08-12 multi-environment support)

Works on ALL desktop environments — no cua-driver dependency:

- **KDE** — `spectacle` (the native Wayland screenshot tool)
- **GNOME** — `gnome-screenshot` (when present)
- **Wayland/X11 generic** — ImageMagick `import` (root window)

Backends are tried in order; the `capture_via` field reports which one
worked. cua-driver's capture is the last-resort fallback.

## Usage

```
screenshot {}
screenshot {"action": "monitors"}                        # list connected displays
screenshot {"monitor": 2}                                # focus monitor 2
screenshot {"monitor": 3, "describe": true, "prompt": "What is on this monitor?"}
screenshot {"app": "Firefox"}
screenshot {"describe": true, "prompt": "What is on the screen?"}
```

## Multi-monitor focus (the Operator's 08-12 spec)

`screenshot {"action": "monitors"}` enumerates the CONNECTED displays
with their active resolution (KDE kscreen-doctor / X11 xrandr):

```
{"index": 1, "name": "DP-1", "width": 1920, "height": 1080, "rate": 119.88}
{"index": 2, "name": "DP-2", ...}
{"index": 3, "name": "HDMI-A-1", ...}
```

`monitor: N` (1..10+) captures ONLY that display — the full-desktop
shot is cropped to the monitor's geometry. Combined with `describe`,
the vision model focuses exactly where you point it.

## Requirements (credentials — for `describe`)

- **Capture** (plain screenshot) — keyless (KDE spectacle / GNOME /
  X11 import — no credentials).
- **`describe: true`** — REQUIRES the vision provider to be configured:
  - `LMSTUDIO_API_KEY` in `.secret` (the operator's LM Studio token)
  - `provider.selection.vision` in config.yaml: `{provider: lmstudio,
    model: lmstudio-community/qwen2.5-vl-3b-instruct}`
  - The LM Studio server reachable at its configured base_url (from
    authentication.json).
- Check first: `screenshot {"action": "monitors"}` works keyless; the
  vision describe returns "no vision provider/model configured" when
  the selection is missing.

## The loop

```
screenshot → vision model sees the page → browser/computer acts → screenshot again
```

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/screenshot.py` — registers `screenshot`.

---
---
