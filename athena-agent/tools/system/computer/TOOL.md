---
name: computer
description: Operate the machine as a user — pointer, keyboard, screen captures, and a headless browser.
kind: script
script: scripts/computer.py
actions: screen_capabilities, screenshot, mouse_move, mouse_click, type_text, window_minimize, window_restore, press_key, browser
parameters: '{"type": "object", "properties": {"action": {"type": "string", "enum": ["screen_capabilities", "screenshot", "mouse_move", "mouse_click", "type_text", "window_minimize", "window_restore", "press_key", "browser"], "description": "The verb: What screen capture, input and browser this machine actually offers; Capture the screen to sandbox/screenshots/ and return the path; Move the pointer to absolute screen coordinates; Move to coordinates and click a button; Type text at the focused window; Minimise windows by class or caption match, via KWin — no pointer needed; Restore and focus windows by class or caption match, via KWin; Press a key combination, e; Open a URL headlessly: screenshot it, or dump its DOM."}, "name": {"type": "string", "description": "Optional filename."}, "x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string", "enum": ["left", "right", "middle"]}, "text": {"type": "string"}, "match": {"type": "string", "description": "Window class or caption fragment, e.g. 'dolphin'. Required for action: window_minimize, window_restore."}, "combo": {"type": "string"}, "url": {"type": "string"}, "mode": {"type": "string", "enum": ["screenshot", "dom"], "description": "screenshot (default) or dom."}, "wait_s": {"type": "number"}}, "required": ["action"]}'
timeout_s: 60
---

# computer

The closest layer to being *someone sitting at the machine*, and the only one that starts switched off:

```yaml
tools:
  computer:
    enabled: false      # turn it on deliberately
```

## The verbs

| verb | does | needs |
|---|---|---|
| `screen_capabilities` | reports what this machine can actually do | nothing |
| `screenshot` | captures the screen to `sandbox/screenshots/` | grim (Wayland) or import/gnome-screenshot (X11) |
| `mouse_move` | moves the pointer to absolute coordinates | ydotool (Wayland) or xdotool (X11) |
| `mouse_click` | moves, then clicks left/right/middle | the same |
| `type_text` | types into the focused window | the same |
| `press_key` | presses a combination, e.g. `ctrl+alt+t` | the same |
| `browser` | opens a URL headlessly: `screenshot` or `dump-dom` | chromium/firefox, or playwright |

## How it reaches the desktop

Each verb is one binary invocation. Inside the distrobox the call is prefixed with **`distrobox-host-exec`**, because
the display session and the GUI tools live on the host: the container sees the Wayland socket, the X11 socket and
`/dev/uinput`, but not `spectacle` or `ydotool`.

`screen_capabilities` reports `bridge: distrobox-host-exec | local` so it is never a guess.

Input needs one more thing: `ydotool` talks to **`ydotoold`**, which owns `/dev/uinput`.

```bash
systemctl --user enable --now ydotoold      # once, on the host
```

Until that runs, `mouse_move` and `type_text` return `exit 2` with a hint saying exactly that.

## Capability is detected, never assumed

Every verb reports what it needed and what it found:

```text
{"ok": false, "desktop": "wayland",
 "error": "no screen-capture tool found; tried grim, spectacle, gnome-screenshot, import",
 "install": "sudo rpm-ostree install grim"}
```

A tool that guesses is worse than one that says "I cannot". On this machine there is `bwrap`, `ydotool`, `xdotool`
and `import`, but **no `grim`, no browser and no playwright** — so a screenshot through `import` works only if XWayland
can see the screen, and `browser` will honestly report that nothing is installed.

## What was learned by using it

- **`ydotool mousemove --absolute X Y` is accepted and silently ignored** by this build: exit 0, no movement, no
  error. The flags it documents are `-a -x <n> -y <n>`, and even those did not move the pointer here. A no-op that
  reports success is the worst failure mode, so the tool documents the limitation rather than hiding it.
- **Key names are not key names.** `ydotool key super` silently does nothing; `ydotool key 125:1 125:0` opens the
  KDE launcher. The tool translates names to Linux key codes for exactly this reason.
- **`spectacle -u` (window under cursor) needs an interactive pick**, so it is useless as an automated check.
- **The reliable way to act on a window is the compositor's own API, not a click.** `window_minimize("dolphin")`
  runs a KWin script through `org.kde.KWin.Scripting`: it acts on windows by class/caption, works on Wayland, and
  never depends on where a button happens to be. Verified: Dolphin minimised, desktop revealed; restored, same call
  with `minimized = false`.

## Screenshots come back as a path

`sandbox/screenshots/screen-20261101-121500.png`, not base64. A PNG in a transcript is thousands of tokens the model
cannot use; a path is something it can `read` (if it is text) or hand to the user.

## Why it is off by default

Moving a real pointer moves the *user's* pointer. A model that misreads a screen and clicks can send a message, close
a window, or confirm a dialog — actions with consequences outside every tree the scope guard knows about. So this is
the one tool where the default is "no", and enabling it is a sentence the user has to write themselves.

## See also

`references/desktop-notes.md` — Wayland vs X11, why `ydotoold` must be running, and what each capture tool misses.
