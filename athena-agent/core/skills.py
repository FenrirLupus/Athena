"""Skills — procedural memory the agent loads on demand, not context it pays for every turn.

A skill is a folder with a `SKILL.md` and whatever it needs beside it (scripts, references, templates). The
shape is deliberate:

    .athena/skills/<name>/SKILL.md          shared by every agent
    profiles/<lane>/skills/<name>/SKILL.md  this agent only, shadows the shared one by name

`SKILL.md` opens with a short frontmatter block:

    ---
    name: sandbox-testing
    description: Use when testing a script inside the agent's own sandbox.
    when: a script needs to run before it is trusted
    ---

Two levels of cost, and that is the whole point of the design:

    index()  → name + description + when, one line each: cheap, always in the prompt
    read()   → the whole SKILL.md, only when the agent decides it needs it

A library of fifty skills therefore costs fifty lines of prompt, not fifty documents. The agent is told what exists
and spends context only on what it opens.
"""
from __future__ import annotations

from pathlib import Path
from core import frontmatter
from core import references as references_mod
from core import trace

INDEX_LIMIT = 12
DESC_CHARS = 160


class SkillError(ValueError):
    pass


def _frontmatter(text: str) -> tuple[dict, str]:
    """Split `---` frontmatter from the body."""
    return frontmatter.parse(text)


def roots(profile_root: Path | None = None, athena_root: Path | None = None,
          plugin_names: list[str] | None = None) -> list[Path]:
    """Where to look, nearest first: the profile's skills, the shared ones, then those an enabled plugin ships."""
    found = []
    if profile_root is not None:
        found.append(Path(profile_root) / "skills")
    root = Path(athena_root) if athena_root else (
        Path(profile_root).parents[1] if profile_root is not None else None)
    if root is not None:
        found.append(root / "skills")
        for name in (plugin_names or []):
            plugin_skills = root / "plugins" / name / "skills"
            if plugin_skills.is_dir():
                found.append(plugin_skills)
    present = [p for p in found if p.is_dir()]
    trace.event("skills.roots", level="debug", searched=len(found), found=len(present),
                dirs=[str(p) for p in present])
    return present


def discover(profile_root: Path | None = None, athena_root: Path | None = None,
             plugin_names: list[str] | None = None) -> list[dict]:
    """Every skill visible to one agent, nearest first. A profile skill shadows a shared one with the same name."""
    by_name: dict[str, dict] = {}
    bases = roots(profile_root, athena_root, plugin_names)
    for base in bases:
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            skill_file = folder / "SKILL.md"
            if not skill_file.is_file():
                # a category folder: skills/<category>/<name>/SKILL.md. The category is recorded, not inferred
                # from the name, so a skill can be filed without being renamed.
                for nested in sorted(folder.iterdir()) if folder.is_dir() else []:
                    inner = nested / "SKILL.md"
                    if not (nested.is_dir() and inner.is_file()):
                        continue
                    try:
                        meta_in, body_in = _frontmatter(inner.read_text(encoding="utf-8"))
                    except OSError:
                        continue
                    name_in = meta_in.get("name") or nested.name
                    if name_in in by_name:
                        kept = by_name[name_in].get("dir") or by_name[name_in].get("root")
                        trace.event("skills.shadowed", level="debug", skill=name_in,
                                    rule="nearer-root-wins", kept_root=kept, dropped_root=str(base))
                        continue
                    by_name[name_in] = {"name": name_in, "category": meta_in.get("category") or folder.name,
                                        "path": str(inner), "description": meta_in.get("description") or "",
                                        "body": body_in, "root": str(base)}
                    trace.event("skills.category", level="debug", skill=name_in,
                                category=by_name[name_in]["category"],
                                rule="frontmatter" if meta_in.get("category") else "folder-name")
                continue
            try:
                meta, body = _frontmatter(skill_file.read_text(encoding="utf-8"))
            except OSError:
                continue
            name = meta.get("name") or folder.name
            if name in by_name:
                kept = by_name[name].get("dir") or by_name[name].get("root")
                trace.event("skills.shadowed", level="debug", skill=name, rule="nearer-root-wins",
                            kept_root=kept, dropped_root=str(base))
                continue                      # the nearer root already claimed this name
            by_name[name] = {
                "name": name,
                "description": (meta.get("description") or "").strip()[:DESC_CHARS],
                "when": (meta.get("when") or "").strip()[:DESC_CHARS],
                "path": str(skill_file),
                "dir": str(folder),
                "origin": ("profile" if profile_root and str(base).startswith(str(profile_root))
                           else ("plugin" if "/plugins/" in str(base) else "shared")),
                "chars": len(body),
            }
    trace.event("skills.discover", level="debug", count=len(by_name), roots=len(bases))
    return sorted(by_name.values(), key=lambda s: s["name"])


def index(profile_root: Path | None = None, limit: int = INDEX_LIMIT,
          athena_root: Path | None = None, plugin_names: list[str] | None = None) -> list[str]:
    """The cheap form: one line per skill, for the prompt."""
    found = discover(profile_root, athena_root, plugin_names)
    if len(found) > limit:
        trace.event("skills.index", level="debug", shown=limit, available=len(found),
                    rule="capped-at-limit")
    lines = []
    for skill in found[:limit]:
        cue = skill["description"] or skill["when"] or "(no description)"
        lines.append(f"- {skill['name']}: {cue}")
    return lines


def read(name: str, profile_root: Path | None = None, max_chars: int = 20000,
         athena_root: Path | None = None, plugin_names: list[str] | None = None) -> dict:
    """The expensive form: the whole skill, body only (frontmatter already served the index)."""
    wanted = (name or "").strip()
    if not wanted:
        raise SkillError("a skill name is required; ask for the index first")
    for skill in discover(profile_root, athena_root, plugin_names):
        if skill["name"] == wanted:
            path = Path(skill["path"])
            try:
                meta, body = _frontmatter(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise SkillError(f"cannot read {path}: {exc}") from exc
            truncated = len(body) > max_chars
            # references arrive with the skill: a document the capability points at is part of operating it, and an
            # agent that has to remember a second call will not make it
            attached = references_mod.attach(path.parent, meta)
            if truncated or attached["problems"]:
                trace.event("skills.read", level="debug", skill=wanted, chars=len(body),
                            max_chars=max_chars, truncated=truncated,
                            references=len(attached["files"]), reference_problems=len(attached["problems"]))
            return {**skill, "body": body[:max_chars] + attached["text"], "truncated": truncated,
                    "references": [row["path"] for row in attached["files"]],
                    "reference_problems": attached["problems"],
                    "siblings": sorted(p.name for p in path.parent.iterdir() if p.name != "SKILL.md")}

    known = ", ".join(s["name"] for s in discover(profile_root, athena_root, plugin_names)) or "none"
    trace.failure("skills.refused", None, rule="unknown-skill", skill=wanted,
                  error=f"SkillError: no skill named {wanted!r}", known=known)
    raise SkillError(f"no skill named {wanted!r}; available: {known}")
