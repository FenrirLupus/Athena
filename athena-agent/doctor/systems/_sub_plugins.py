"""Plugin contract test — the Operator's 08-12 community-modding spec.

PLUGINS ARE NOT NATIVE. Athena ships with NO bundled plugins — the
shared plugins home is EMPTY by default. Plugins are the COMMUNITY
modding layer: a way to modify Athena natively through community-based
modding (the operator installs their own; Athena does not ship any).

This test asserts the CONTRACT without depending on a bundled plugin:
  - the plugin loader discovers what the operator installed (empty now)
  - the loader never crashes on an empty home
  - a community plugin placed in the shared home would register itself
"""
from __future__ import annotations


def run() -> list[dict]:
    from intelligence.plugins import discover_plugins

    checks = []
    plugins = discover_plugins()
    names = [p.name for p in plugins]

    # 1. NO bundled plugins ship with Athena (the Operator's 08-12 spec:
    #    plugins are community modding, not Athena's own).
    checks.append({
        "name": "no bundled plugins (community modding only)",
        "status": "ok" if not names else "fail",
        "detail": f"plugins={names} (want empty — Athena ships none)",
    })

    # 2. The loader handles an empty home without crashing.
    checks.append({
        "name": "plugin discovery safe on empty home",
        "status": "ok",
        "detail": f"discovered={len(plugins)}",
    })

    # 3. A community plugin in the shared home would register itself
    #    (the self-registration contract, tested against a temp plugin).
    import tempfile
    from pathlib import Path
    from core.config import SHARED_PLUGINS
    with tempfile.TemporaryDirectory(prefix="plugin-contract-") as td:
        from intelligence import plugins as _plugins
        orig_root = _plugins.PLUGINS_DIR
        tmp_plugins = Path(td) / "plugins"
        try:
            # A minimal community plugin (manifest only, no tools/skills).
            pdir = tmp_plugins / "community-test"
            pdir.mkdir(parents=True)
            (pdir / "plugin.yaml").write_text(
                "name: community-test\n"
                "description: 'A community plugin the operator installed'\n"
                "provides_tools: []\n"
                "provides_skills: []\n",
                encoding="utf-8")
            _plugins.PLUGINS_DIR = tmp_plugins
            found = discover_plugins()
            found_names = [p.name for p in found]
            checks.append({
                "name": "community plugin registers (self-registration)",
                "status": "ok" if "community-test" in found_names else "fail",
                "detail": f"found={found_names}",
            })
        finally:
            _plugins.PLUGINS_DIR = orig_root

    return checks
