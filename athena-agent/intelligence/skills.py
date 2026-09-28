"""Skills — the brain layer.

The space doctrine: skills are the BRAIN (when/how/why — judgment), plugins
are hands-off buttons, tools are the hands (dumb operations). A skill is a
SKILL.md file with YAML frontmatter (name, description) and body content.

Layout:
    ~/.athena/skills/<name>/SKILL.md      — global skills (all channels can
                                            load, subject to the channel gate)
    plugins/<plugin>/skills/<name>/SKILL.md — plugin-bundled skills

Loading is gated by the channel's allowed-skills list (default deny in
channels.py). Skills that the channel may not use are never injected.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from core.config import SHARED_SKILLS

# The SHARED skills home (.athena/skills) — every profile's skills/ dir
# is a native symlink to it (the Operator's 08-10 spec: one skill set for
# the whole platform).
SKILLS_DIR = SHARED_SKILLS


class Skill:
    """One skill: name, description (from frontmatter), body (the content).

    priority (1-3): the markdown importance of the skill — # = 1 (highest),
    ## = 2, ### = 3. From the section format; 1 for regular frontmatter
    skills.

    THE STANDARDIZED EXECUTION SCHEMA (the Operator's 08-12 spec): skills
    execute through the SAME schema as tools — {type: function, function:
    {name, description, parameters}} — so the model invokes
    skill:doctor / skill:network exactly like a tool. The skill's
    parameters default to {name} (what skill_load accepted); a skill's
    frontmatter may declare richer args later.
    """

    def __init__(self, name: str, path: Path, description: str = "",
                 body: str = "", source: str = "local", priority: int = 1,
                 references: str = "", parameters: dict | None = None):
        self.name = name
        self.path = path
        self.description = description
        self.body = body
        self.source = source  # local | plugin:<name>
        self.priority = priority
        # REFERENCES (the Operator's 08-12 spec): the skill's library of
        # knowledge — the contents of the skill's references/ dir,
        # concatenated so the agent gets the full picture, not just the
        # SKILL.md body. Skills have NO code — references ARE their
        # depth.
        self.references = references
        # THE STANDARD SCHEMA (the 08-12 fix): same shape as Tool. The
        # default parameter is the skill's name (what skill_load
        # accepted); a skill may declare richer args via frontmatter.
        self.parameters = parameters or {
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": f"The skill name ({name})"},
            },
            "required": ["name"],
        }

    def to_tool_schema(self) -> dict:
        """The OpenAI function schema — IDENTICAL shape to Tool.schema()
        (the 08-12 standardized execution schema: skills and tools are
        advertised and invoked the same way).

        THE PATTERN-SAFE NAME (the 08-14 zen-400 fix): OpenAI-compatible
        relays enforce the tool-name pattern ^[a-zA-Z0-9_-] — a colon in
        "skill:name" makes the whole request HTTP 400 ("Invalid
        'tools[N].function.name'"). The advertised name is sanitized to
        "skill_name"; the dispatcher accepts both forms.
        """
        return {
            "type": "function",
            "function": {
                "name": f"skill_{self.name}",
                "description": self.description or f"Apply the {self.name} skill",
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Skill {self.name} ({self.source}) P{self.priority}>"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter (--- ... ---) from the body. Best-effort."""
    body = text
    meta: dict = {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        raw = m.group(1)
        body = text[m.end():]
        for line in raw.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def _load_references(skill_dir: Path) -> str:
    """The skill's references/ library — concatenated (the Operator's spec).

    A skill's depth lives in its references/ dir (examples, further
    specification). Read every .md/.txt there (sorted, excluding the
    README placeholder) so the agent sees the full knowledge, not just
    the SKILL.md body. Skills have NO code — references are their depth.
    """
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return ""
    parts = []
    for f in sorted(refs_dir.iterdir()):
        if not f.is_file() or f.name in ("README.md",):
            continue
        try:
            parts.append(f.read_text(encoding="utf-8", errors="replace").strip())
        except Exception:
            continue
    return "\n\n".join(parts)


def _load_skill_dir(dirpath: Path, source: str) -> list[Skill]:
    skills = []
    # Recurse: skills may be nested (skills/conversation/<name>/SKILL.md).
    for sk_path in sorted(dirpath.rglob("SKILL.md")):
        try:
            text = sk_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            from core.logging import log_event
            log_event(3, f"skill read failed: {sk_path.name}: {exc}",
                      source="intelligence", action="load_skills")
            continue
        meta, body = _parse_frontmatter(text)
        name = meta.get("name", sk_path.parent.name)
        if meta.get("name"):
            # YAML metadata FIRST, then --- delimited sections
            # (the Operator's combined format). The metadata describes the file;
            # each section after is a skill.
            from intelligence.sections import parse_skill_document
            _, sections = parse_skill_document(text)
            if len(sections) > 1 or (sections and sections[0].get("title")):
                # A single section with a title: the frontmatter `name:`
                # is the CANONICAL skill name (the Operator's strict --- format
                # has one skill per file; the title is the body's heading).
                if len(sections) == 1 and meta.get("name"):
                    skills.append(Skill(
                        name=name,
                        path=sk_path,
                        description=meta.get("description", ""),
                        body=sections[0].get("content", "").strip(),
                        source=source,
                        priority=int(sections[0].get("priority", 1) or 1),
                        references=_load_references(sk_path.parent),
                    ))
                    continue
                for sec in sections:
                    title = sec.get("title") or name
                    if not title:
                        continue
                    skills.append(Skill(
                        name=_slug(title),
                        path=sk_path,
                        description=meta.get("description", f"Section: {title}"),
                        body=sec.get("content", "").strip(),
                        source=source,
                        priority=int(sec.get("priority", 1) or 1),
                        references=_load_references(sk_path.parent),
                    ))
                continue
            # Metadata + a plain body (no sections) → a single skill.
            skills.append(Skill(
                name=name,
                path=sk_path,
                description=meta.get("description", ""),
                body=body.strip(),
                source=source,
                references=_load_references(sk_path.parent),
            ))
            continue
        # No frontmatter — try the SECTION format: each --- delimited
        # block is a skill (title = the # heading).
        from intelligence.sections import parse_sections
        sections = parse_sections(text)
        if len(sections) > 1 or (sections and sections[0].get("title")):
            for sec in sections:
                title = sec.get("title") or sk_path.parent.name
                if not title:
                    continue
                skills.append(Skill(
                    name=_slug(title),
                    path=sk_path,
                    description=f"Section: {title}",
                    body=sec.get("content", "").strip(),
                    source=source,
                    priority=int(sec.get("priority", 1) or 1),
                    references=_load_references(sk_path.parent),
                ))
        else:
            # Plain body, no frontmatter, no sections — one nameless skill.
            skills.append(Skill(
                name=name,
                path=sk_path,
                description=meta.get("description", ""),
                body=body.strip(),
                source=source,
                references=_load_references(sk_path.parent),
            ))
    return skills


def _slug(title: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9-]+", "-", title.lower()).strip("-") or "section"


def load_skills(plugin_skills: Optional[list[Skill]] = None,
                profile_dir: Optional[Path] = None) -> list[Skill]:
    """Load all skills: the global skills dir + profile skills +
    plugin-bundled skills.

    profile_dir: a named profile's root (profiles/<name>/); its skills/
    subdirectory is loaded as that profile's local skills. The DEFAULT
    profile uses the root .athena/skills/ (SKILLS_DIR).

    Dedup by NAME: local skills win over plugin-bundled ones with the same
    name (the profile's own copy is the source of truth).
    """
    skills: list[Skill] = []
    if profile_dir is not None:
        pdir = Path(profile_dir) / "skills"
        if pdir.exists():
            skills.extend(_load_skill_dir(pdir, "profile"))
    else:
        if SKILLS_DIR.exists():
            skills.extend(_load_skill_dir(SKILLS_DIR, "local"))
    seen = {sk.name for sk in skills}
    if plugin_skills:
        for sk in plugin_skills:
            if sk.name not in seen:
                skills.append(sk)
                seen.add(sk.name)
    # GUARDRAILS (the Operator's safety spec): every skill declares its scope
    # when loaded — the guardrail registry knows what each skill may do.
    try:
        from security.guardrails import declare
        for sk in skills:
            declare(sk.name, capabilities=["read"],
                    description=sk.description)
    except Exception:
        pass
    return skills


def skills_index(skills: list[Skill]) -> str:
    """A compact index of available skills (name + description) for the
    prompt — the model sees WHAT exists so it knows what it can apply.

    Skills are CALLABLE (the Operator's 08-12 mirror rule — they work
    exactly like tools): to apply a skill, invoke skill_load with its
    name. Ordered by markdown priority (P1 first), then name.
    """
    if not skills:
        return ""
    lines = ["Available skills (invoke skill_load {name} to apply one):"]
    for sk in sorted(skills, key=lambda s: (s.priority, s.name)):
        desc = f" — {sk.description}" if sk.description else ""
        marker = "#" * sk.priority if sk.priority > 1 else ""
        prefix = f" {marker}" if marker else ""
        lines.append(f"- [{sk.name}]{prefix}{desc}")
    return "\n".join(lines)


def filter_by_channel(skills: list[Skill], channel) -> list[Skill]:
    """Default deny: only skills the channel allows are usable."""
    if channel is None:
        return skills
    return [sk for sk in skills if channel.allows_skill(sk.name)]
