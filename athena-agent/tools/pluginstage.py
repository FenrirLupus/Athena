"""plugin_stage — prepare a plugin in the sandbox so a human can install it.

The agent's half of an installation, and only that half. It copies the parts of a checked-out repository that would
become a plugin into `sandbox/staging/<name>/`, writes a `PLUGIN.md` draft, and stops. It cannot write
`.athena/plugins/`: that directory is `ATHENA_ROOT`, read-only to agents, and an agent that can install code has a way
around every rule here.

    agent   repo_inspect → assess → plugin_stage        (this tool)
    user    ./athena.sh plugins install <name>

The manifest is the contract between the two halves: name, entry, description, what it provides. Installation refuses
without it, because a plugin without a manifest cannot be uninstalled cleanly later.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from core import paths as paths_mod
from tools.registry import Tool, ToolError

SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build", ".next"}


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
    def plugin_stage(name: str = "", source: str = "", entry: str = "scripts/plugin.py",
                     description: str = "", provides: str = "", requires: str = "") -> dict:
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

        return {"staged": str(dest), "files": copied, "manifest": str(manifest), "ok": True,
                "install_request": request,
                "status": "awaiting the user's approval in the interface — do not install anything yourself",
                "also_available": f"./athena.sh plugins install {slug}",
                "note": ("nothing is installed yet and nothing from the repository was executed. The user either "
                         "approves the prompt or runs the command; the installer validates the manifest, hashes "
                         "every file, enables it by name, smoke-tests it in a sandboxed child, and then you "
                         "re-baseline.")}

    return [Tool(
        "plugin_stage",
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
        plugin_stage)]
