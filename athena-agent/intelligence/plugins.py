"""Plugins — the hands-off buttons layer.

The space doctrine: plugins are hands-off buttons (zero judgment), skills
are the brain (when/how/why), tools are the hands (dumb operations). A
plugin bundles tools + skills behind a manifest; judgment about WHEN to use
them lives in skills, never in the plugin.

Layout:
    ~/.athena/plugins/<name>/plugin.yaml          — the manifest
                              /skills/<s>/SKILL.md — bundled skills
                              /tools/<t>/TOOL.md   — bundled tool docs
                              /scripts/...         — bundled scripts (future)

plugin.yaml shape (mirrors the space convention — top metadata, bottom
config):
    name: example-plugin
    version: 0.1.0
    description: "..."
    author: "..."
    provides_tools: [tool_a, ...]
    provides_skills: [skill_a, ...]
    config: { ... tunables read by the plugin itself ... }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.config import SHARED_PLUGINS

# The SHARED plugins home (.athena/plugins) — every profile's plugins/
# dir is a native symlink to it (the Operator's 08-10 spec: one set of
# plugins for the whole platform).
PLUGINS_DIR = SHARED_PLUGINS


@dataclass
class Plugin:
    name: str
    path: Path
    version: str = ""
    description: str = ""
    author: str = ""
    provides_tools: list = field(default_factory=list)
    provides_skills: list = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def skills_dir(self) -> Path:
        return self.path / "skills"

    def tools_dir(self) -> Path:
        return self.path / "tools"

    def scripts_dir(self) -> Path:
        return self.path / "scripts"


def _parse_manifest(path: Path) -> dict:
    """Read plugin.yaml (YAML, best-effort). Empty dict on failure."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        from core.logging import log_event
        log_event(3, f"plugin manifest parse failed: {path.name}: {exc}",
                  source="intelligence", action="load_plugins")
        return {}


def discover_plugins(plugins_root: Optional[Path] = None) -> list[Plugin]:
    """Discover plugins in <root>/plugins/<name>/ with a plugin.yaml.

    plugins_root: a profile's root (profiles/<name>/) uses its plugins/
    dir; the default profile uses the global .athena/plugins/.
    """
    base = plugins_root / "plugins" if plugins_root is not None else PLUGINS_DIR
    plugins = []
    if not base.exists():
        return plugins
    for pdir in sorted(base.iterdir()):
        if not pdir.is_dir():
            continue
        manifest_path = pdir / "plugin.yaml"
        if not manifest_path.exists():
            continue
        manifest = _parse_manifest(manifest_path)
        plugins.append(Plugin(
            name=manifest.get("name", pdir.name),
            path=pdir,
            version=str(manifest.get("version", "")),
            description=str(manifest.get("description", "")),
            author=str(manifest.get("author", "")),
            provides_tools=list(manifest.get("provides_tools", []) or []),
            provides_skills=list(manifest.get("provides_skills", []) or []),
            config=manifest.get("config", {}) or {},
        ))
    return plugins


def load_plugin_skills(plugin: Plugin):
    """Load a plugin's bundled skills (SKILL.md files)."""
    from .skills import _load_skill_dir
    if not plugin.skills_dir().exists():
        return []
    return _load_skill_dir(plugin.skills_dir(), f"plugin:{plugin.name}")


def load_plugin_tools(plugin: Plugin):
    """Load a plugin's bundled tools from its tools/ directory.

    A plugin tool is a Python module in tools/<name>/tool.py exposing a
    `register()` function, OR a TOOL.md doc only (documented, not yet
    executable). This is the extension point — safe and declarative.
    """
    from filesystem.tools import register, Tool
    if not plugin.tools_dir().exists():
        return []
    registered = []
    for tdir in sorted(plugin.tools_dir().iterdir()):
        if not tdir.is_dir():
            continue
        tool_py = tdir / "tool.py"
        if tool_py.exists():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    f"athena_plugin_{plugin.name}_{tdir.name}", tool_py
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "register"):
                    mod.register()
                    registered.append(tdir.name)
            except Exception as exc:
                from core.logging import log_event
                log_event(4, f"plugin tool load failed: {tdir.name}: {exc}",
                          source="intelligence", action="load_plugin_tools")
                continue
    return registered


def load_all(plugins_root: Optional[Path] = None) -> dict:
    """Discover + load all plugins (optionally a profile's plugins dir)."""
    plugins = discover_plugins(plugins_root=plugins_root)
    summary = []
    for plugin in plugins:
        tools = load_plugin_tools(plugin)
        skills = load_plugin_skills(plugin)
        summary.append({
            "plugin": plugin.name,
            "version": plugin.version,
            "tools_registered": tools,
            "skills": [sk.name for sk in skills],
        })
    return {"plugins": plugins, "summary": summary}


def activate(plugin: Plugin) -> dict:
    """SELF-REGISTRATION — the Operator's contract.

    A plugin registers itself: its tools into the tool registry, its
    skills into the skill index. Returns {tools, skills} counts.
    Idempotent: activating twice registers nothing twice (the registry
    dedups by name).
    """
    tools = load_plugin_tools(plugin)
    skills = load_plugin_skills(plugin)
    # GUARDRAILS (the Operator's safety spec): the plugin DECLARES its scope
    # when it activates — the guardrail registry knows what it can do.
    try:
        from security.guardrails import declare
        caps = [c for c in (plugin.config.get("capabilities")
                            if isinstance(plugin.config, dict) else None)
                or []]
        if not caps:
            caps = ["read"]
        declare(plugin.name, capabilities=caps,
                description=str(plugin.description or ""))
    except Exception:
        pass
    from core.logging import log_event
    log_event(2, f"plugin '{plugin.name}' v{plugin.version} activated: "
                 f"{len(tools)} tools, {len(skills)} skills",
              source="intelligence", action="plugin_activate")
    return {"plugin": plugin.name, "tools": tools, "skills": len(skills)}


