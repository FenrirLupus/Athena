"""The skills toolset: what exists, and the one you asked for.

Two tools, mirroring the two levels of cost — `skill_list` is cheap and answers "what do I have", `skill_read`
opens one and spends the context. Nothing here touches the filesystem through the guard because skills live in
`.athena/skills` and `profiles/<lane>/skills`, which are readable by construction; the *content* is what the guard
protects, and a skill body is text the user wrote, not data the agent found.
"""
from __future__ import annotations

from pathlib import Path

from core import skills as skills_mod
from core import trace
from tools.registry import Tool, ToolError


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile whose skills are being read."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")


def build(ctx=None) -> list[Tool]:
    """Skills tools for one agent; `ctx` decides which skills it can see, plugins included."""
    from tools.loader import agent_roots

    profile_root, athena_root = agent_roots(ctx)
    plugin_names = list((getattr(ctx, "cfg", None) or {}).get("plugins", {}).get("enabled")
                        or []) if isinstance((getattr(ctx, "cfg", None) or {}).get("plugins"), dict) else []


    def skill_list() -> dict:
        with trace.span("tool.skills.list", channel=_lane(ctx)) as sp:
            found = skills_mod.discover(profile_root, athena_root, plugin_names)
            sp.ok(count=len(found))
            return {"count": len(found), "skills": found}

    def skill_read(name: str, max_chars: int = 20000) -> dict:
        with trace.span("tool.skills.read", channel=_lane(ctx), skill=name) as sp:
            try:
                out = skills_mod.read(name, profile_root, max_chars=max(500, min(int(max_chars), 60000)),
                                      athena_root=athena_root, plugin_names=plugin_names)
            except skills_mod.SkillError as exc:
                raise ToolError(str(exc)) from exc
            sp.ok(chars=len(out.get("body") or ""), truncated=bool(out.get("truncated")))
            return out


    # ---- managing skills: create your own, remove it, disable or re-enable one -------------------------------
    import re as _re
    import shutil as _shutil

    NAME = _re.compile(r"^[a-z][a-z0-9-]{1,48}$")

    def _folder_of(name: str):
        """Where a skill lives, or why it cannot be managed."""
        rows = skills_mod.discover(profile_root, athena_root, plugin_names)
        row = next((r for r in rows if r.get("name") == name), None)
        if not row:
            # a disabled skill is parked, so discovery no longer lists it — look for the folder directly
            for folder in sorted((Path(profile_root) / "skills").glob(f"*/{name}")):
                if (folder / "SKILL.md").is_file() or (folder / "SKILL.md.disabled").is_file():
                    return folder.resolve()
            raise ToolError(f"no skill named {name!r}")
        path = Path(row.get("path") or row.get("dir") or "")
        if not path.name:
            raise ToolError(f"{name} has no folder this agent can manage")
        if path.name == "SKILL.md":
            path = path.parent
        resolved = path.resolve()
        program = Path(__file__).resolve().parents[3] / "skills"
        if str(resolved).startswith(str(program.resolve())):
            raise ToolError(f"{name} ships with Athena; it can be disabled but not removed")
        if athena_root and str(resolved).startswith(str((Path(athena_root) / "skills").resolve())) \
                and not str(resolved).startswith(str((Path(profile_root) / "skills").resolve())):
            raise ToolError(f"{name} is installed in the shared skills root; only the operator removes those")
        return resolved

    def skill_create(name: str = "", description: str = "", category: str = "local", body: str = "") -> dict:
        name = str(name or "").strip()
        category = str(category or "local").strip()
        with trace.span("tool.skills.create", channel=_lane(ctx), skill=name, category=category) as sp:
            if not NAME.match(name):
                raise ToolError("a skill name is lowercase letters, digits and hyphens, 2-49 characters")
            if any(r.get("name") == name for r in skills_mod.discover(profile_root, athena_root, plugin_names)):
                raise ToolError(f"a skill named {name!r} already exists; two would be indistinguishable by name")
            if not NAME.match(category):
                raise ToolError("a category is lowercase letters, digits and hyphens")
            folder = Path(profile_root) / "skills" / category / name
            folder.mkdir(parents=True, exist_ok=False)
            text = (f"---\nname: {name}\ndescription: {description or 'Use when ...'}\ncategory: {category}\n---\n\n"
                    f"# {name}\n\n{body or description or ''}\n")
            (folder / "SKILL.md").write_text(text, encoding="utf-8")
            (folder / "references").mkdir(exist_ok=True)
            loads = any(r.get("name") == name for r in skills_mod.discover(profile_root, athena_root, plugin_names))
            sp.ok(path=str(folder), bytes=len(text.encode()), loads=loads)
            return {"ok": True, "path": str(folder), "category": category, "loads": loads}

    def skill_remove(name: str = "", confirm: bool = False) -> dict:
        with trace.span("tool.skills.remove", channel=_lane(ctx), skill=name, confirm=bool(confirm)) as sp:
            folder = _folder_of(name)
            if not confirm:
                raise ToolError(f"removing {name} deletes {folder}; call again with confirm=true")
            _shutil.rmtree(folder)
            # an empty category folder is a broken skill to discovery, so prune it
            parent = folder.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
            still = any(r.get("name") == name for r in skills_mod.discover(profile_root, athena_root, plugin_names))
            sp.ok(removed=str(folder), still_listed=still)
            return {"ok": True, "removed": str(folder), "still_listed": still}

    def _skill_toggle(name: str, on: bool) -> dict:
        with trace.span("tool.skills.enable" if on else "tool.skills.disable", channel=_lane(ctx),
                        skill=name) as sp:
            folder = _folder_of(name)
            live, parked = folder / "SKILL.md", folder / "SKILL.md.disabled"
            if on:
                if not parked.is_file():
                    raise ToolError(f"{name} is already enabled")
                parked.rename(live)
            else:
                if not live.is_file():
                    raise ToolError(f"{name} is already disabled")
                live.rename(parked)
            listed = any(r.get("name") == name for r in skills_mod.discover(profile_root, athena_root, plugin_names))
            sp.ok(name=name, enabled=listed)
            return {"ok": True, "name": name, "enabled": listed,
                    "note": "a disabled skill is parked as SKILL.md.disabled, so its text is still on disk"}

    def skill_enable(name: str = "") -> dict:
        return _skill_toggle(name, True)

    def skill_disable(name: str = "") -> dict:
        return _skill_toggle(name, False)

    return [
        Tool("list", "List the skills available to you: name, description and when to use each.",
             {"type": "object", "properties": {}, "required": []}, skill_list),
        Tool("read", "Open one skill in full. Load it only when the task matches its description.",
             {"type": "object",
              "properties": {"name": {"type": "string", "description": "Skill name from skill_list."},
                             "max_chars": {"type": "integer", "description": "Characters to return."}},
              "required": ["name"]}, skill_read),
        Tool("create", "Create a skill in your own skills root: a SKILL.md and an empty references/.",
             {"type": "object", "properties": {"name": {"type": "string"}, "description": {"type": "string"},
                                               "category": {"type": "string"}, "body": {"type": "string"}},
              "required": ["name"]}, skill_create),
        Tool("remove", "Delete a skill you created. Shipped and shared skills belong to the operator.",
             {"type": "object", "properties": {"name": {"type": "string"}, "confirm": {"type": "boolean"}},
              "required": ["name"]}, skill_remove),
        Tool("enable", "Re-enable a skill you disabled.", {"type": "object",
             "properties": {"name": {"type": "string"}}, "required": ["name"]}, skill_enable),
        Tool("disable", "Disable a skill without deleting it: SKILL.md is parked, not removed.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, skill_disable),
    ]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "skills")
