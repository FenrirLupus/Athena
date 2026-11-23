# Wayland, X11, and the tools that bridge them

A Wayland session (`XDG_SESSION_TYPE=wayland`) with XWayland available (`DISPLAY=:0`) has both toolchains, and
they are not interchangeable.

## Capture

| tool | works on | note |
|---|---|---|
| `grim` | Wayland | the preferred one where it is present |
| `spectacle -b -n -o` | Wayland (KDE) | background capture |
| `gnome-screenshot -f` | both | slow, asks the compositor |
| `import -window root` | X11 / XWayland | what a typical X11 session has; sees the X root, which on Wayland may be only XWayland windows |

A Wayland capture needs the compositor's cooperation. `grim` talks the protocol; `import` talks X11 and therefore may
photograph an empty root window instead of the real desktop. If a screenshot comes back black, that is why.

## Input

- **`ydotool`** works on both, through `/dev/uinput` — but **`ydotoold` must be running** and the user must be able to
  open the device. `ydotool: failed to connect to socket` means the daemon is not up.
- **`xdotool`** works only on X11/XWayland windows, and cannot move the pointer over native Wayland surfaces.

So on a Wayland session input will usually work for XWayland apps and fail for native ones.

## Browser

With no browser and no playwright on PATH, headless capture needs one of:

```
chromium --headless=new --screenshot=out.png --window-size=1280,800 <url>
firefox  --headless --screenshot out.png <url>
```

`playwright` would be the better answer for real interaction (clicking, waiting for selectors), but it is a ~300 MB
install and belongs in the model runtime venv, not in the agent's own.
