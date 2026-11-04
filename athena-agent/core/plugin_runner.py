"""plugin_runner — hosts ONE plugin in its own process and speaks JSON lines over stdin/stdout.

Why a separate process: a plugin is arbitrary Python. In the server's process it can rebind `Scope.check` and the
watchdogs can only detect-and-restore afterwards. In its own process it can rebind whatever it likes and affect
nothing, because the guard it would patch does not exist there.

The child is started under bubblewrap (see `core/plugins.py`): read-only root, credentials masked, no writable tree
of its own. It computes; it does not reach the disk or the network without going through the parent's tools.

Protocol (one JSON object per line, both directions):

    child  → {"ok": true, "tools": [{"name", "description", "parameters"}, …]}
    parent → {"call": "<tool>", "args": {...}}
    child  → {"ok": true|false, "result": "<string>"}
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path

# The runner is started by file path, so sys.path[0] is `core/` — the project root has to be put back or the plugin
# cannot import `tools.registry`, which is the one thing it is allowed to use.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    os.chdir(ROOT)
except OSError:
    pass


class ShimRegistry:
    """Collects the plugin's tools instead of running them in the server."""

    def __init__(self):
        self.tools: dict[str, object] = {}

    def register(self, tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        self.tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self.tools)

    def discard(self, name: str) -> None:
        self.tools.pop(name, None)


def _send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        _send({"ok": False, "error": "usage: plugin_runner <plugin_dir> <entry.py>"})
        return 2
    plugin_dir, entry = Path(argv[0]), Path(argv[1])

    class Ctx:
        cfg: dict = {}
        profile_root = plugin_dir
        athena_root = None

    registry = ShimRegistry()
    try:
        spec = importlib.util.spec_from_file_location("athena_plugin_child", entry)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise RuntimeError(f"{entry.name} has no register(registry, ctx)")
        register(registry, Ctx)
    except Exception as exc:                          # noqa: BLE001 — reported, not raised
        _send({"ok": False, "error": f"{type(exc).__name__}: {exc}",
               "trace": traceback.format_exc(limit=4).splitlines()[-4:]})
        return 1

    _send({"ok": True, "tools": [{"name": tool.name, "description": tool.description,
                                  "parameters": tool.parameters} for tool in registry.tools.values()]})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _send({"ok": False, "result": "error: the parent sent something that is not JSON"})
            continue
        name = request.get("call")
        tool = registry.tools.get(name)
        if tool is None:
            _send({"ok": False, "result": f"error: no tool named {name!r} in this plugin"})
            continue
        try:
            out = tool.fn(**(request.get("args") or {}))
            _send({"ok": True, "result": out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)})
        except Exception as exc:                      # noqa: BLE001 — a tool error, not a crash
            _send({"ok": False, "result": f"error: {name} failed: {type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
