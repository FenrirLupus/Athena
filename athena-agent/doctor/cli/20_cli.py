"""Cli surface test — consolidated (the Operator's 08-12 directive).

ONE module per category: this composer runs every cli submodule's
checks and merges them into a single report. Check names are preserved
1:1 — the doctor count and the nurse's failure tracking stay stable
across consolidation.
"""
from __future__ import annotations

from pathlib import Path

import os


def _chk_colors() -> list[dict]:
    import cli.colors as colors

    checks = []
    colors.should_use_color = lambda: True  # force color for the test
    red = colors.red("x")
    orange = colors.orange("x")
    checks.append({
        "name": "red tag uses red code",
        "status": "ok" if "\033[31m" in red else "fail",
        "detail": repr(red),
    })
    checks.append({
        "name": "orange uses 256-color code",
        "status": "ok" if "\033[38;5;208m" in orange else "fail",
        "detail": repr(orange),
    })
    checks.append({
        "name": "bold wraps prompt",
        "status": "ok" if "\033[1m" in colors.bold(orange) else "fail",
        "detail": "",
    })

    # NO_COLOR respected
    colors.should_use_color = lambda: os.environ.get("NO_COLOR") is None and True
    os.environ["NO_COLOR"] = "1"
    try:
        plain = colors.red("x")
        checks.append({
            "name": "NO_COLOR strips codes",
            "status": "ok" if plain == "x" else "fail",
            "detail": repr(plain),
        })
    finally:
        del os.environ["NO_COLOR"]
    return checks


def _chk_parser() -> list[dict]:
    from cli.main import CLI, _completer
    import readline

    checks = []
    c = CLI.__new__(CLI)
    parsed = c.parse_command(r"\kanban update abc done")
    checks.append({
        "name": "slash parser (module, args, status)",
        "status": "ok" if parsed == ("kanban", ["update", "abc", "done"]) else "fail",
        "detail": f"{parsed}",
    })
    parsed2 = c.parse_command("/cron add nightly 03*** check")
    checks.append({
        "name": "slash parser (both starters)",
        "status": "ok" if parsed2 and parsed2[0] == "cron" else "fail",
        "detail": f"{parsed2}",
    })
    checks.append({
        "name": "plain text → chat (None)",
        "status": "ok" if c.parse_command("hello there") is None else "fail",
        "detail": "",
    })
    readline.get_line_buffer = lambda: "/life"
    checks.append({
        "name": "completion reads registry",
        "status": "ok" if _completer("life", 0) == "lifecycle" else "fail",
        "detail": f"got {_completer('life', 0)}",
    })
    return checks


_SUBMODULES = [
    "banner",
    "colors",
    "parser",
    "profile_switch",
]


def _load_sub(name: str):
    """Import a submodule by its _sub_* file name (namespace package)."""
    import importlib.util
    here = Path(__file__).parent
    path = here / f"_sub_{name}.py"
    spec = importlib.util.spec_from_file_location(
        f"doctor.cli._sub_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod



def run() -> list[dict]:
    checks: list[dict] = []
    for name in _SUBMODULES:
        # Inline (folded) checks run directly; file-backed ones import.
        inline = globals().get(f"_chk_{name}")
        if inline is not None:
            try:
                checks.extend(inline())
            except Exception as exc:
                checks.append({
                    "name": f"cli/{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                })
            continue
        try:
            mod = _load_sub(name)
            if callable(getattr(mod, "run", None)):
                checks.extend(mod.run())
        except Exception as exc:
            checks.append({
                "name": f"cli/{name}",
                "status": "fail",
                "detail": f"{type(exc).__name__}: {exc}",
            })
    return checks
