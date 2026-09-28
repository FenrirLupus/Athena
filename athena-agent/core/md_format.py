"""Markdown formatter — the Athena Standard Markdown Schema (Operator 08-12).

TWO SCHEMAS:
  1. STANDARD Markdown Schema — every system .md file:
         ---
         HEADER          (YAML / variables / comments / instructions)
         ---
         <empty line>
         BODY            (any markdown, NO delimiters inside)
         <empty line>
         ---
         FOOTER          (YAML / variables / comments / instructions)
         ---
     The HARD RULE: exactly FOUR (4) --- delimiters per file — 2 wrap
     the Header, 2 wrap the Footer. The body is OPAQUE: the formatter
     never parses or restructures it. It only ensures the sandwich.
     An empty line sits after the header's bottom --- (before the
     body) and before the footer's top --- (after the body).

  2. LIST Markdown Schema — MEMORY.md only. A pure list of entries
     (--- delimited blocks as content separators). It is NOT part of
     the Standard schema and is NEVER reformatted.

The formatter's job: given a file's text + a header (and optional
footer), build the sandwich. Existing content becomes the body as-is.
Idempotent: a file already in the schema is unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

_DELIM = "---"

# The LIST schema files — exempt from the Standard sandwich.
LIST_SCHEMA_FILES = {"MEMORY.md"}


def _is_list_schema(path: Path | None) -> bool:
    """True when the file is a LIST-schema file (MEMORY.md only)."""
    return path is not None and path.name in LIST_SCHEMA_FILES


def _strip_delims(text: str) -> str:
    """Extract the BODY from a possibly-framed file.

    When the text is already the Standard sandwich (4+ ---), the body
    is everything between the header's bottom --- and the footer's top
    --- (blank edges trimmed). Otherwise, strip leading/trailing ---
    frames + hugging blanks — the old R1-R7 style — so the rebuild is
    clean: the body is the content in between.
    """
    lines = text.splitlines()
    delim_positions = [i for i, l in enumerate(lines) if l.strip() == _DELIM]
    # Only treat as the Standard sandwich when there are EXACTLY 4
    # delimiters AND the last is the file's final line (a real footer
    # frame). An old-style file with 4+ delims but content after the
    # last --- is NOT a sandwich — its delimiters were section frames.
    if (len(delim_positions) == 4
            and delim_positions[0] == 0
            and delim_positions[-1] == len(lines) - 1):
        h1 = delim_positions[1]      # header's bottom ---
        f0 = delim_positions[-2]     # footer's top ---
        body = lines[h1 + 1:f0]
        # Trim blank edges.
        while body and body[0].strip() == "":
            body.pop(0)
        while body and body[-1].strip() == "":
            body.pop()
        return "\n".join(body).strip()
    # Old style: strip the leading FRONTMATTER frame (the first --- pair)
    # + any trailing frame, so the body is the real content only.
    while lines and lines[0].strip() == "":
        lines.pop(0)
    while lines and lines[-1].strip() == "":
        lines.pop()
    # Drop the first frontmatter block (--- ... ---) if present.
    if lines and lines[0].strip() == _DELIM:
        lines.pop(0)
        while lines and lines[0].strip() == "":
            lines.pop(0)
        if lines and lines[0].strip() != _DELIM:
            # consume the header content until the next ---
            while lines and lines[0].strip() != _DELIM:
                lines.pop(0)
        if lines and lines[0].strip() == _DELIM:
            lines.pop(0)
            while lines and lines[0].strip() == "":
                lines.pop(0)
    # A DUPLICATED header (the bad-pass artifact): the header content
    # + --- repeated right after the frontmatter. Collapse consecutive
    # --- runs and drop a repeated frontmatter-looking block.
    while True:
        # collapse a run of delims to one, then drop it + a repeated
        # key: value header block
        if lines and lines[0].strip() == _DELIM:
            lines.pop(0)
            while lines and lines[0].strip() == "":
                lines.pop(0)
            continue
        if lines and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*:", lines[0]):
            # a key: value block (dup header) — skip until the next ---
            consumed = False
            while lines and lines[0].strip() != _DELIM:
                lines.pop(0)
                consumed = True
            if consumed:
                continue
        break
    # Strip trailing frame delimiters.
    while lines and lines[-1].strip() == _DELIM:
        lines.pop()
        while lines and lines[-1].strip() == "":
            lines.pop()
    return "\n".join(lines).strip()


def _clean_section(text: str) -> str:
    """A section's content, trimmed of frame delimiters + blank edges."""
    lines = text.splitlines()
    while lines and lines[0].strip() in ("", _DELIM):
        lines.pop(0)
    while lines and lines[-1].strip() in ("", _DELIM):
        lines.pop()
    return "\n".join(lines).strip()


def _read_header_footer(path: Path, text: str):
    """Extract the current header + footer from an in-schema file.

    Returns (header, footer) when the file is already the Standard
    sandwich; (None, None) when it isn't (so the caller rebuilds).
    """
    lines = text.splitlines()
    # The file must be EXACTLY the Standard sandwich: 4 delimiters,
    # the first on line 0, the last on the final line.
    if not lines or lines[0].strip() != _DELIM:
        return None, None
    delim_positions = [i for i, l in enumerate(lines) if l.strip() == _DELIM]
    if (len(delim_positions) != 4
            or delim_positions[-1] != len(lines) - 1):
        return None, None
    h0, h1 = delim_positions[0], delim_positions[1]
    f0, f1 = delim_positions[-2], delim_positions[-1]
    header = "\n".join(lines[h0 + 1:h1]).strip()
    footer = "\n".join(lines[f0 + 1:f1]).strip()
    return header, footer


def format_md(text: str, header: str = "", footer: str = "",
              path: Path | None = None) -> str:
    """Normalize markdown text to the Standard sandwich schema.

    header/footer: the section contents (may be empty). When the file
    already carries a header/footer (in-schema), they are PRESERVED
    over the defaults. The body is whatever remains — untouched.

    LIST-schema files (MEMORY.md) return unchanged.
    """
    if path is not None and _is_list_schema(path):
        return text
    # If the file is already the sandwich, preserve its header/footer.
    if path is not None:
        cur_h, cur_f = _read_header_footer(path, text)
        if cur_h is not None:
            header = cur_h
            footer = cur_f if cur_f is not None else footer

    # PRESERVE THE FRONTMATTER (the 08-12 formatter fix): a file that
    # opens with --- YAML --- carries its OWN schema (SKILL.md, TOOL.md,
    # the identity files' frontmatter). That block is the HEADER — never
    # strip it as a frame. Extract it first so the rebuild keeps it.
    if not header and text.lstrip().startswith(_DELIM):
        lines = text.splitlines()
        if lines and lines[0].strip() == _DELIM:
            for i in range(1, len(lines)):
                if lines[i].strip() == _DELIM:
                    cand = "\n".join(lines[1:i]).strip()
                    # Only treat as frontmatter when it LOOKS like YAML.
                    if any(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*:", l)
                           for l in cand.splitlines()):
                        header = cand
                    break

    # The body: strip the OLD framing (leading/trailing delims) so the
    # rebuild is clean — the body is the content in between.
    body = _strip_delims(text)
    h = _clean_section(header)
    f = _clean_section(footer)

    parts = [_DELIM]
    if h:
        parts.append(h)
    parts.append(_DELIM)
    parts.append("")
    parts.append(body if body else "")
    parts.append("")
    parts.append(_DELIM)
    if f:
        parts.append(f)
    parts.append(_DELIM)
    return "\n".join(parts) + "\n"


def format_file(path: Path, header: str = "", footer: str = "") -> bool:
    """Format one .md file in place (Standard schema). Returns changed.

    LIST-schema files (MEMORY.md) are never touched.
    """
    if _is_list_schema(path):
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    new = format_md(text, header=header, footer=footer, path=path)
    if new == text:
        return False
    try:
        path.write_text(new, encoding="utf-8")
        return True
    except OSError:
        return False


def _skip(path: Path, root: Path, excluded: tuple) -> bool:
    """Skip paths outside the .athena scope / excluded subtrees."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    for part in rel.parts:
        if part in excluded:
            return True
    return False


def format_tree(root: Path | None = None,
                excluded: tuple = ("pycache", "readme", "documentation",
                                   ".athena", "node_modules", ".wiki",
                                   "logs-archive")) -> dict:
    """Format every Standard-schema .md under a root.

    Returns {checked, changed, files}. MEMORY.md (List schema) skipped.
    The readme/ + documentation/ dirs are EXEMPT (the Operator's 08-12 spec):
    they exist only as examples of what Athena is and how she operates —
    not part of the system schema. The .wiki/ clone is the operator's
    local mirror of the GitHub wiki — its pages are the wiki's own
    formatting, never reformatted.
    """
    if root is None:
        from core.config import ATHENA_ROOT
        root = ATHENA_ROOT
    checked = 0
    changed = 0
    files: list[str] = []
    for p in sorted(root.rglob("*.md")):
        if not p.is_file() or _skip(p, root, excluded):
            continue
        checked += 1
        if format_file(p):
            changed += 1
            try:
                files.append(str(p.relative_to(root)))
            except ValueError:
                files.append(str(p))
    return {"checked": checked, "changed": changed, "files": files}


def format_profile_files() -> dict:
    """Format the per-profile system .md files (Standard schema).

    ASSISTANT.md / USER.md / EMOTION.md get the sandwich. MEMORY.md
    (List schema) is never touched.
    """
    from core.config import ATHENA_ROOT
    targets = []
    for root in (ATHENA_ROOT / "profiles").glob("*"):
        for side in ("assistant", "user"):
            for name in ("ASSISTANT.md", "USER.md", "MEMORY.md", "EMOTION.md"):
                p = root / side / name
                if p.exists():
                    targets.append(p)
    changed = 0
    files = []
    for p in targets:
        if format_file(p):
            changed += 1
            files.append(str(p))
    return {"checked": len(targets), "changed": changed, "files": files}


def delimiter_lines(text: str) -> list[int]:
    """Line numbers of --- delimiters (1-based; the frontmatter check)."""
    return [i + 1 for i, line in enumerate(text.splitlines())
            if line.strip() == "---"]
