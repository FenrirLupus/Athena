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
    return [p for p in found if p.is_dir()]


def discover(profile_root: Path | None = None, athena_root: Path | None = None,
             plugin_names: list[str] | None = None) -> list[dict]:
    """Every skill visible to one agent, nearest first. A profile skill shadows a shared one with the same name."""
    by_name: dict[str, dict] = {}
    for base in roots(profile_root, athena_root, plugin_names):
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            skill_file = folder / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                meta, body = _frontmatter(skill_file.read_text(encoding="utf-8"))
            except OSError:
                continue
            name = meta.get("name") or folder.name
            if name in by_name:
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
    return sorted(by_name.values(), key=lambda s: s["name"])


def index(profile_root: Path | None = None, limit: int = INDEX_LIMIT,
          athena_root: Path | None = None, plugin_names: list[str] | None = None) -> list[str]:
    """The cheap form: one line per skill, for the prompt."""
    lines = []
    for skill in discover(profile_root, athena_root, plugin_names)[:limit]:
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
            refs = path.parent / "references"
            return {**skill, "body": body[:max_chars], "truncated": truncated,
                    "references": sorted(str(p.relative_to(refs)) for p in refs.rglob("*") if p.is_file())
                    if refs.is_dir() else [],
                    "siblings": sorted(p.name for p in path.parent.iterdir() if p.name != "SKILL.md")}
    known = ", ".join(s["name"] for s in discover(profile_root, athena_root, plugin_names)) or "none"
    raise SkillError(f"no skill named {wanted!r}; available: {known}")
