"""Built-in tools loader (the Operator's 08-12 builtin spec).

Athena ships with GENERALIZED built-in tools inside athena-system/tools/
— added functionality, no theme, not catering to a specific audience.

TOOL STRUCTURE (mirrors skills — the Operator's spec):
    tools/<name>/TOOL.md         — the tool's INDEX (instructions +
                                   expectations for the agent)
    tools/<name>/references/     — the tool's library of knowledge
                                   (examples, further specification)
    tools/<name>/scripts/*.py    — ONE tool per script (a folder bundles
                                   related tools: calendar.py +
                                   calendar_add.py, etc.)

Each script has a register() that self-registers its tool into the
registry. The loader imports every script under every tool's scripts/
dir. The tools live INSIDE athena-system (a keep-file in the wipe test)
so they survive a wipe and spring Athena back fully operational.
"""

import importlib
import importlib.util
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"

# The built-in tools root — exposed for the prompt builder's index.
TOOLS_DIR = _TOOLS_DIR


def register_builtin_tools() -> list[str]:
    """Import + register every script under tools/<name>/scripts/.

    Returns the registered tool names. Idempotent (the registry dedups).
    """
    names: list[str] = []
    if not _TOOLS_DIR.is_dir():
        return names
    # Each tool folder: tools/<name>/ with TOOL.md + scripts/*.py
    for tool_dir in sorted(_TOOLS_DIR.iterdir()):
        if not tool_dir.is_dir() or tool_dir.name.startswith("_"):
            continue
        scripts_dir = tool_dir / "scripts"
        if not scripts_dir.is_dir():
            continue
        for py in sorted(scripts_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            mod_name = f"athena_tool_{tool_dir.name}_{py.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, str(py))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = mod
                spec.loader.exec_module(mod)
                if callable(getattr(mod, "register", None)):
                    names.extend(mod.register() or [])
                    # THE RESOLVED-LOAD CLEANUP (the 08-14 fix): the tool
                    # loaded SUCCESSFULLY — purge any past "failed to
                    # load" metric entries for it, so the nurse doesn't
                    # re-flag a bug that's already fixed.
                    try:
                        from metrics.logger import purge_entries
                        purge_entries(tool=py.name,
                                      needle="builtin tool failed to load")
                    except Exception:
                        pass
            except Exception as exc:
                # THE BUILTIN AUDIT (the Operator's 08-12 metrics spec):
                # a builtin tool that fails to import at boot is a
                # missing capability — it MUST reach the logs (a silent
                # `continue` hides a broken tool forever).
                try:
                    from core.logging import log_event
                    log_event(4, f"builtin tool failed to load {py.name}: {exc}",
                              source="core", tool="builtin_tools",
                              action="register", target=str(py))
                except Exception:
                    pass
                continue
    return names
