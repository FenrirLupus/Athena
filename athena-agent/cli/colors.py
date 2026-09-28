"""Shared ANSI + rich color utilities for Athena CLI modules.

ATHENA'S THEME — exactly 3 colors (the Operator's spec):
    RED    #FF3B30   — importance, errors, User side
    ORANGE #FFA500   — actions, Events/System
    YELLOW #FFD700   — highlights, Assistant side

Respects NO_COLOR and TERM=dumb, and only colors TTY output.
"""
from __future__ import annotations

import os
import sys

def should_use_color() -> bool:
    """True when colored output is appropriate (TTY, not disabled)."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if not sys.stdout.isatty():
        return False
    return True

# THE 08-16 THEME STATE (the GUI's ☀/🌙 toggle, terminal-side): a module
# flag so /theme can flip light/dark for the Rich consoles. Default dark
# (the Athena theme). Rich re-reads this on each render.
_DARK_MODE = True

def set_dark_mode(dark: bool) -> None:
    global _DARK_MODE
    _DARK_MODE = bool(dark)

def is_dark_mode() -> bool:
    return _DARK_MODE

# The 3-color theme (ANSI + the rich hex equivalents).
THEME = {
    "red":    {"ansi": "\033[31m",     "rich": "#FF3B30"},
    "orange": {"ansi": "\033[38;5;208m", "rich": "#FFA500"},
    "yellow": {"ansi": "\033[33m",     "rich": "#FFD700"},
}

def color(text: str, *codes) -> str:
    """Apply color codes to text (only when color output is appropriate)."""
    if not should_use_color():
        return text
    return "".join(codes) + text + Colors.RESET

class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"          # theme 1
    ORANGE = "\033[38;5;208m" # theme 2 (bright orange, 256-color)
    YELLOW = "\033[33m"       # theme 3
    # Internal only (not part of the theme — used sparingly for status).
    GREEN = "\033[32m"
    BLUE = "\033[34m"

# THE 08-16 PALETTE-AWARE THEME (the Operator's spec): the CLI reads the
# SAME 5-color palettes the website uses (config.yaml theme.light/dark),
# so the terminal matches the website's theme exactly — including
# light/dark mode. Each palette is 5 hex colors:
#   light: [#fafafa bg, #e1e1e1 surface, #fa7d00 primary, #fafa00 secondary, #000000 text]
#   dark:  [#1e1e1e bg, #323232 surface, #fa0000 primary, #fa7d00 secondary, #fafafa text]
# The named roles map onto them:
#   bg/surface  → the panel + border shades
#   primary     → the accent (red in dark / orange in light)
#   secondary   → the highlight (orange in dark / yellow in light)
#   text        → the content color
_DEFAULT_PALETTES = {
    "light": ["#fafafa", "#e1e1e1", "#fa7d00", "#fafa00", "#000000"],
    "dark":  ["#1e1e1e", "#323232", "#fa0000", "#fa7d00", "#fafafa"],
}

def palette(mode: str | None = None) -> list:
    """The 5-color palette for a mode (default: the active dark flag)."""
    mode = mode if mode in ("light", "dark") else ("dark" if _DARK_MODE else "light")
    try:
        from core.config import load_raw_config
        _t = load_raw_config().get("theme") or {}
        pal = _t.get(mode)
        if isinstance(pal, list) and len(pal) == 5:
            return [str(c) for c in pal]
    except Exception:
        pass
    return list(_DEFAULT_PALETTES[mode])

def theme_colors(mode: str | None = None) -> dict:
    """The named role colors for the mode (the 5-color theme)."""
    p = palette(mode)
    return {
        "bg":        p[0],
        "surface":   p[1],
        "primary":   p[2],
        "secondary": p[3],
        "text":      p[4],
        # The classic 3-name aliases (the CLI's original vocabulary):
        "red":       p[2],   # the primary accent
        "orange":    p[3],   # the secondary accent
        "yellow":    p[3],   # the highlight
    }

def red(text: str) -> str:
    return color(text, Colors.RED)

def orange(text: str) -> str:
    return color(text, Colors.ORANGE)

def yellow(text: str) -> str:
    return color(text, Colors.YELLOW)

def bold(text: str) -> str:
    return color(text, Colors.BOLD)

def dim(text: str) -> str:
    return color(text, Colors.DIM)

# Status-only helpers (success/good states — kept minimal).
def green(text: str) -> str:
    return color(text, Colors.GREEN)

def blue(text: str) -> str:
    return color(text, Colors.BLUE)
