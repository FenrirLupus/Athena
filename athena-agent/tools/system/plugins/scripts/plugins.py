"""plugin_stage — prepare a plugin in the sandbox so a human can install it.

The agent's half of an installation, and only that half. It copies the parts of a checked-out repository that would
become a plugin into `sandbox/staging/<name>/`, writes a `PLUGIN.md` draft, and stops. It cannot write
`.athena/plugins/`: that directory is `ATHENA_ROOT`, read-only to agents, and an agent that can install code has a way
around every rule here.

    agent   sandbox_inspect → assess → plugin_stage        (this tool)
    user    ./athena.sh plugins install <name>

The manifest is the contract between the two halves: name, entry, description, what it provides. Installation refuses
without it, because a plugin without a manifest cannot be uninstalled cleanly later.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from core import paths as paths_mod
from core import trace
from tools.registry import Tool, ToolError

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build", ".next"}


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile whose plugins are being read."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")


def _agent_of(ctx) -> str:
    """The lane that is asking — a card that says "your queue" hides who staged it."""
    scope = getattr(ctx, "scope", None)
    home = getattr(scope, "home", None)
    return getattr(home, "name", "") or ""


def _draft_manifest(name: str, description: str, entry: str, provides: str, requires: str = "") -> str:
    entry_line = f"entry: {entry}\n" if entry else ""
    requires_line = f"requires: {requires}\n" if requires else ""
    return f"""---
name: {name}
description: {description or "describe what this plugin adds, in one line"}
{entry_line}provides: {provides or "tools"}
{requires_line}---

# {name}

Staged by an agent from an inspected repository. **A human should read this before installing.**

- what it adds:
- what it needs (network, files, credentials):
- where it came from:
- anything the inventory flagged:
"""


def build(ctx) -> list[Tool]:
    _CTX[0] = ctx

    def plugin_stage(name: str = "", source: str = "", entry: str = "scripts/plugin.py",
                     description: str = "", provides: str = "", requires: str = "") -> dict:
        with trace.span("tool.plugins.stage", channel=_lane(ctx), plugin=name, source=source) as sp:
            if not name:
                raise ToolError("a plugin name is required (it becomes the folder name)")
            if not source:
                raise ToolError("a source path is required — usually a folder inside sandbox/repos/<repo>/")

            slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
            sandbox = Path(getattr(ctx, "profile_root", Path.cwd())) / "sandbox"
            try:
                sandbox = ctx.scope.check(str(sandbox), paths_mod.WRITE, tool="plugin_stage")
            except ValueError as exc:
                raise ToolError(f"the sandbox is not writable: {exc}") from exc

            source_path = Path(source).expanduser()
            if not source_path.is_absolute():
                source_path = sandbox / source_path
            try:
                source_path = ctx.scope.check(str(source_path), paths_mod.READ, tool="plugin_stage")
            except ValueError as exc:
                raise ToolError(f"cannot read the source: {exc}") from exc
            if not source_path.is_dir():
                raise ToolError(f"{source_path} is not a directory")

            dest = sandbox / "staging" / slug
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)

            copied = 0
            for path in sorted(source_path.rglob("*")):
                if any(part in SKIP for part in path.parts):
                    continue
                target = dest / path.relative_to(source_path)
                if path.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1

            # shape follows the contents: a bundle of skills has no entry script, and demanding one would make every
            # documentation-shaped repository uninstallable
            has_skills = (dest / "skills").is_dir() or any(dest.glob("**/SKILL.md"))
            if not provides:
                provides = "skills" if has_skills and not (dest / entry).exists() else "tools"
            needs_entry = "tools" in provides.lower()
            if needs_entry and not (dest / entry).exists():
                sp.ok(files=copied, staged=str(dest), ok=False, refused="entry missing")
                return {"staged": str(dest), "files": copied, "manifest": str(dest / "PLUGIN.md"), "ok": False,
                        "problem": f"entry {entry!r} is missing but provides says {provides!r}. Copy the script, pass "
                                   f"entry=, or pass provides='skills' for a bundle with no executable part",
                        "next": "fix the shape, then stage again"}

            manifest = dest / "PLUGIN.md"
            if not manifest.exists():
                manifest.write_text(_draft_manifest(slug, description, entry if needs_entry else "",
                                                    provides, requires), encoding="utf-8")

            # ask the user through the interface; the server performs the install when they answer
            request = None
            athena_root = getattr(ctx, "athena_root", None)
            if athena_root is not None:
                try:
                    from core import plugininstall

                    request = plugininstall.request_install(dest, Path(athena_root), agent=_agent_of(ctx),
                                                            source_url=description and "", reason=description)
                except Exception as exc:                  # noqa: BLE001 — a queue failure must not lose the stage
                    request = {"error": f"{type(exc).__name__}: {exc}"}

            sp.ok(files=copied, staged=str(dest), ok=True, provides=provides)
            return {"staged": str(dest), "files": copied, "manifest": str(manifest), "ok": True,
                    "install_request": request,
                    "status": "awaiting the user's approval in the interface — do not install anything yourself",
                    "also_available": f"./athena.sh plugins install {slug}",
                    "note": ("nothing is installed yet and nothing from the repository was executed. The user either "
                             "approves the prompt or runs the command; the installer validates the manifest, hashes "
                             "every file, enables it by name, smoke-tests it in a sandboxed child, and then you "
                             "re-baseline.")}

    return [Tool("stage",
        "Prepare a plugin in your sandbox from an inspected repo (manifest + files). Does not install it.",
        {"type": "object",
         "properties": {"name": {"type": "string", "description": "Plugin name; becomes the folder name."},
                        "source": {"type": "string", "description": "Folder to stage from, e.g. sandbox/repos/x/skills."},
                        "entry": {"type": "string", "description": "Entry script inside the plugin (default scripts/plugin.py)."},
                        "description": {"type": "string", "description": "One line for the manifest."},
                        "provides": {"type": "string", "description": "tools | skills | tools,skills"},
                        "requires": {"type": "string",
                                     "description": "Python packages this plugin needs, comma-separated "
                                                    "(e.g. 'httpx>=0.27, pyyaml'). The installer builds a venv."}},
         "required": ["name", "source"]},
        plugin_stage),
        Tool("list", "Plugins present, where they live, and whether this agent has them enabled.",
             {"type": "object", "properties": {}}, lambda: plugins_list()),
        Tool("read", "One plugin's manifest, entry script head and files.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, plugins_read),
        Tool("create", "Create a plugin in your own plugins root and enable it.",
             {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                                               "provides": {"type": "string"}}, "required": ["name"]}, plugins_create),
        Tool("remove", "Delete a plugin you created. Installed plugins belong to the user.",
             {"type": "object", "properties": {"name": {"type": "string"}, "confirm": {"type": "boolean"}},
              "required": ["name"]}, plugins_remove),
        Tool("enable", "Enable a plugin for this agent.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
             lambda name="": _toggle(name, True)),
        Tool("disable", "Disable a plugin for this agent without removing it.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
             lambda name="": _toggle(name, False))]


# ---- managing plugins: create your own, remove it, enable or disable any ------------------------------------
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")


def _profile(ctx) -> Path:
    from tools import loader
    profile, _ = loader.agent_roots(ctx)
    return Path(profile)


def _roots(ctx) -> list[tuple[str, Path]]:
    from tools import loader
    profile, athena = loader.agent_roots(ctx)
    out = [("profile", Path(profile) / "plugins")]
    if athena:
        out.append(("athena", Path(athena) / "plugins"))
    return out


def _enabled(profile: Path) -> list[str]:
    cfg = profile / "config.yaml"
    if not cfg.is_file():
        return []
    for line in cfg.read_text().splitlines():
        if line.strip().startswith("enabled:"):
            m = re.search(r"\[(.*?)\]", line)
            if m:
                return [x.strip() for x in m.group(1).split(",") if x.strip()]
    return []


def _set_enabled(profile: Path, names: list[str]) -> None:
    cfg = profile / "config.yaml"
    text = cfg.read_text() if cfg.is_file() else "plugins:\n"
    line = f"  enabled: [{', '.join(names)}]"
    if re.search(r"^\s*enabled:.*$", text, re.M):
        text = re.sub(r"^\s*enabled:.*$", line.strip(), text, count=1, flags=re.M)
    else:
        text = text.rstrip() + "\n" + line + "\n"
    from core import durable
    durable.write_state(cfg, text)


def plugins_list() -> dict:
    from tools.registry import ToolError
    ctx = _CTX[0]
    with trace.span("tool.plugins.list", channel=_lane(ctx)) as sp:
        profile = _profile(ctx)
        on = set(_enabled(profile))
        rows = []
        for origin, root in _roots(ctx):
            if not root.is_dir():
                continue
            for folder in sorted(p for p in root.iterdir() if p.is_dir()):
                rows.append({"name": folder.name, "origin": origin,
                             "has_manifest": (folder / "PLUGIN.md").is_file(),
                             "enabled": folder.name in on, "path": str(folder)})
        sp.ok(count=len(rows), enabled=len(on))
        return {"ok": True, "count": len(rows), "plugins": rows, "enabled": sorted(on)}


def plugins_read(name: str = "") -> dict:
    ctx = _CTX[0]
    with trace.span("tool.plugins.read", channel=_lane(ctx), plugin=name) as sp:
        for origin, root in _roots(ctx):
            folder = root / str(name)
            if folder.is_dir():
                entry = next(iter(sorted((folder / "scripts").glob("*.py"))), None) if (folder / "scripts").is_dir() else None
                files = sorted(p.name for p in folder.rglob("*") if p.is_file())[:60]
                sp.ok(origin=origin, files=len(files), entry=entry.name if entry else "-")
                return {"ok": True, "name": name, "origin": origin, "path": str(folder),
                        "manifest": (folder / "PLUGIN.md").read_text()[:4000] if (folder / "PLUGIN.md").is_file() else "",
                        "entry": entry.name if entry else None,
                        "entry_head": entry.read_text()[:1500] if entry else "",
                        "files": files}
        from tools.registry import ToolError
        raise ToolError(f"no plugin named {name!r}")


def plugins_create(name: str = "", description: str = "", provides: str = "tools") -> dict:
    from tools.registry import ToolError
    ctx = _CTX[0]
    name = str(name or "").strip()
    with trace.span("tool.plugins.create", channel=_lane(ctx), plugin=name, provides=provides) as sp:
        if not NAME_RE.match(name):
            raise ToolError("a plugin name is lowercase letters, digits, hyphens and underscores, 2-49 characters")
        profile = _profile(ctx)
        folder = profile / "plugins" / name
        if folder.exists():
            raise ToolError(f"{folder} already exists")
        (folder / "scripts").mkdir(parents=True)
        (folder / "PLUGIN.md").write_text(
            f"---\nname: {name}\ndescription: {description or 'A plugin created by the agent.'}\n"
            f"entry: scripts/plugin.py\nprovides: {provides}\n---\n\n# {name}\n",
            encoding="utf-8")
        (folder / "scripts" / "plugin.py").write_text(
            '"""register(registry, ctx) is called when this plugin is enabled for an agent."""\n\n\n'
            'def register(registry, ctx) -> None:\n    return None\n', encoding="utf-8")
        on = _enabled(profile)
        if name not in on:
            _set_enabled(profile, on + [name])
        sp.ok(path=str(folder), files=2)
        return {"ok": True, "path": str(folder), "enabled": True,
                "note": "a plugin lives in your own plugins root; installing into .athena/plugins is the user's half"}


def plugins_remove(name: str = "", confirm: bool = False) -> dict:
    from tools.registry import ToolError
    ctx = _CTX[0]
    with trace.span("tool.plugins.remove", channel=_lane(ctx), plugin=name, confirm=bool(confirm)) as sp:
        profile = _profile(ctx)
        folder = profile / "plugins" / str(name)
        for origin, root in _roots(ctx):
            if origin == "athena" and (root / str(name)).is_dir():
                raise ToolError(f"{name} is installed in .athena/plugins — an agent cannot uninstall what the user installed")
        if not folder.is_dir():
            raise ToolError(f"no plugin named {name!r} in your own plugins root")
        if not confirm:
            raise ToolError(f"removing {name} deletes {folder}; call again with confirm=true")
        shutil.rmtree(folder)
        _set_enabled(profile, [x for x in _enabled(profile) if x != name])
        sp.ok(removed=str(folder))
        return {"ok": True, "removed": str(folder)}


def _toggle(name: str, on: bool) -> dict:
    from tools.registry import ToolError
    ctx = _CTX[0]
    with trace.span("tool.plugins.enable" if on else "tool.plugins.disable", channel=_lane(ctx), plugin=name) as sp:
        profile = _profile(ctx)
        if not any((root / str(name)).is_dir() for _, root in _roots(ctx)):
            raise ToolError(f"no plugin named {name!r} is present")
        names = [x for x in _enabled(profile) if x != name]
        if on:
            names.append(name)
        _set_enabled(profile, names)
        sp.ok(enabled=on, enabled_count=len(names))
        return {"ok": True, "name": name, "enabled": on, "enabled_list": names}


_CTX = [None]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "plugins")
