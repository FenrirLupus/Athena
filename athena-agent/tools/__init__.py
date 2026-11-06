"""Tools — one verb (or one family of verbs) per package: `TOOL.md` plus `scripts/`.

`tools.registry` holds `Tool` and `Registry`; `tools.loader` is the only thing that decides what a tool is, where it
comes from, and how it is called. There is no per-tool module in this directory any more: `tools.filesystem` is
answered by the loader from `tools/system/filesystem/scripts/filesystem.py`, so an import and a registration reach
the same module object and neither can drift from the other.
"""
from __future__ import annotations

from tools import loader

loader.install_finder()

__all__ = ["loader", "registry"]
