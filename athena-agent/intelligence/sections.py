"""Sections — the section-based skill format (--- delimited blocks).

A skill FILE may be organized into SECTIONS, each bounded by `---`:

    ---
    # Section 1
    content...
    ---
    # Section 2
    content...
    ---
    # Section 3
    content...
    ---

Each section is a mergeable unit (a skill or a category). The `---`
delimiters mark the boundaries so the curator can:

    - MERGE similar sections (combine two sections into one)
    - REORGANIZE (move a section into a different file/category)
    - OPTIMIZE (a provider call simplifies the merged content)

The loader treats each section as a skill: name = the `# Title` line,
body = the content between delimiters. Backwards compatible with the
plain SKILL.md format (frontmatter + body).
"""
from __future__ import annotations

import re
from pathlib import Path


def parse_sections(text: str) -> list[dict]:
    """Split text into sections.

    Returns [{title, content}] in order. A section begins after a `---`
    line and runs until the next `---`. The first line of the section may
    be `# Title` (the section's name); content is everything after it.

    If no `---` delimiters are found, the whole text is one section
    (title from the first `# heading` if present, else "").
    """
    lines = text.splitlines()
    sections: list[dict] = []
    current: list[str] = []
    seen_delimiter = False
    last_was_delimiter = False

    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            # A boundary: close the current section (if any content) and
            # continue accumulating for the NEXT section. Every --- both
            # ends one section and begins the next. CONSECUTIVE --- lines
            # collapse to one (never doubled delimiters).
            seen_delimiter = True
            if not last_was_delimiter and current and any(l.strip() for l in current):
                sections.append(_finalize_section(current))
                current = []
            last_was_delimiter = True
            continue
        last_was_delimiter = False
        # Content between delimiters accumulates for the current section.
        if seen_delimiter:
            current.append(line)

    # Trailing content after the last --- (or a lone section).
    if current and any(l.strip() for l in current):
        sections.append(_finalize_section(current))
    elif not sections and text.strip():
        # No delimiters — the whole text is one section.
        sections.append(_finalize_section(lines))

    return sections


def _finalize_section(lines: list[str]) -> dict:
    """A section: title from the leading heading, priority from its level.

    MARKDOWN PRIORITY (the Operator's spec):
        #   → priority 1 (highest importance)
        ##  → priority 2
        ### → priority 3
        -   → bullets organize the content (single facts/lines)

    Returns {title, priority, content}. Sub-headings inside the content
    keep their own levels; the section's priority is the LEADING heading.
    """
    title = ""
    priority = 0
    content = []
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m and i == 0:
            level = len(m.group(1))
            title = m.group(2).strip()
            priority = min(level, 3)  # 1..3 per the spec (#, ##, ###)
        else:
            content.append(line)
    if not priority:
        priority = 1  # no heading → treat as top-level
    return {"title": title, "priority": priority, "content": "\n".join(content).strip()}


def _strip_delimiter_lines(content: str) -> str:
    """Remove stray --- lines from a section's content so they can never
    render as doubled delimiters. Content is data; --- is the boundary."""
    lines = [ln for ln in content.splitlines() if ln.strip() != "---"]
    return "\n".join(lines).strip()


def render_sections(sections: list[dict]) -> str:
    """Render sections back to the --- delimited format.

    Every section is bounded: the document starts with a leading --- and
    each section ends with ---, so a round-trip re-parse recovers ALL
    sections (including the first).

    EXACTLY ONE --- between sections: delimiter lines inside a section's
    content are stripped (--- is the boundary, never content), so the
    output can never contain doubled delimiters.
    """
    out = []
    for sec in sections:
        block = []
        title = sec.get("title", "")
        priority = int(sec.get("priority", 1) or 1)
        if title:
            # Render the heading at its priority level (#, ##, ###).
            block.append("#" * priority + f" {title}")
        content = _strip_delimiter_lines(sec.get("content", ""))
        if content:
            block.append(content)
        out.append("\n".join(block))
    # LEADING --- bounds the first section; every section then ends with
    # --- so a re-parse recovers all of them.
    return "---\n" + "\n---\n".join(out) + "\n---"




def write_section_file(path: Path, sections: list[dict]) -> None:
    """Write sections back to a file (--- delimited)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_sections(sections) + "\n", encoding="utf-8")


def parse_skill_document(text: str) -> tuple[dict, list[dict]]:
    """Parse the document: YAML metadata FIRST, sections after.

    The Operator's format:

        ---
        name: file-operations        ← block 1: YAML METADATA (native)
        description: "..."
        version: 0.2.0
        ---
        # File Operations            ← block 2+: CONTENT SECTIONS
        - read files
        ---
        ## Vault Queries
        ---

    Returns (metadata, sections). The FIRST --- block is the metadata
    (never a content section); every block after is a section with its
    title + priority + content.
    """
    from intelligence.skills import _parse_frontmatter
    meta, body = _parse_frontmatter(text)
    if meta:
        # The body is everything after the first --- block. Re-add the
        # opening --- so the FIRST content section is bounded too (the
        # metadata block consumed the original opening delimiter).
        sections = parse_sections("---\n" + body)
    else:
        sections = parse_sections(text)
    return meta, sections
