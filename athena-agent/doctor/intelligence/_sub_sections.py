"""Sections test — the --- delimited skill format + section-level merge."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from intelligence import sections
    from intelligence.sections import parse_sections, render_sections, write_section_file
    from intelligence.curator import merge_sections

    checks = []
    text = """---
# File Operations
- read files
- write files
---
## Vault Queries
- query the vault
---
### Memory Notes
- remember facts
---"""
    secs = parse_sections(text)
    checks.append({
        "name": "sections parsed",
        "status": "ok" if len(secs) == 3 else "fail",
        "detail": f"{len(secs)} sections",
    })
    titles = [s["title"] for s in secs]
    checks.append({
        "name": "section titles extracted",
        "status": "ok" if titles == ["File Operations", "Vault Queries", "Memory Notes"] else "fail",
        "detail": f"{titles}",
    })
    # Markdown priority: # = 1, ## = 2, ### = 3.
    prios = [s["priority"] for s in secs]
    checks.append({
        "name": "markdown priority levels",
        "status": "ok" if prios == [1, 2, 3] else "fail",
        "detail": f"priorities={prios}",
    })
    # Bullets preserved as content.
    bullets_ok = all(s["content"].startswith("- ") or "\n- " in s["content"] for s in secs)
    checks.append({
        "name": "bullet content preserved",
        "status": "ok" if bullets_ok else "fail",
        "detail": "",
    })
    # Round-trip: render → re-parse recovers ALL sections + priorities.
    rendered = render_sections(secs)
    reparsed = parse_sections(rendered)
    checks.append({
        "name": "round-trip recovers all sections",
        "status": "ok" if len(reparsed) == 3 and reparsed[0]["title"] == "File Operations" else "fail",
        "detail": f"{len(reparsed)} sections, first={reparsed[0]['title'] if reparsed else '?'}",
    })
    checks.append({
        "name": "round-trip preserves priorities",
        "status": "ok" if [s["priority"] for s in reparsed] == [1, 2, 3] else "fail",
        "detail": f"{[s['priority'] for s in reparsed]}",
    })
    # Skills loader treats sections as skills.
    from intelligence import skills as skills_mod
    with tempfile.TemporaryDirectory() as td:
        orig = skills_mod.SKILLS_DIR
        skills_mod.SKILLS_DIR = Path(td) / "skills"
        (Path(td) / "skills" / "ops").mkdir(parents=True)
        (Path(td) / "skills" / "ops" / "SKILL.md").write_text(text)
        try:
            loaded = skills_mod.load_skills()
            names = sorted(s.name for s in loaded)
            checks.append({
                "name": "loader yields one skill per section",
                "status": "ok" if names == ["file-operations", "memory-notes", "vault-queries"] else "fail",
                "detail": f"{names}",
            })
        finally:
            skills_mod.SKILLS_DIR = orig
    # Combined: YAML metadata FIRST, then sections.
    from intelligence.sections import parse_skill_document
    combined = """---
name: file-ops
description: "File operations"
version: 0.2.0
author: curator
---
# Read Files
- read a file
---
## Write Files
- write a file
---"""
    meta, secs2 = parse_skill_document(combined)
    checks.append({
        "name": "metadata block parsed separately",
        "status": "ok" if meta.get("name") == "file-ops" and len(secs2) == 2 else "fail",
        "detail": f"meta={meta.get('name')} sections={len(secs2)}",
    })
    checks.append({
        "name": "metadata not counted as a section",
        "status": "ok" if not any(s.get("title") == "file-ops" for s in secs2) else "fail",
        "detail": f"{[s.get('title') for s in secs2]}",
    })
    # EXACTLY ONE --- between sections (never doubled).
    dirty = [
        {"title": "A", "priority": 1, "content": "one\n---\nstray"},
        {"title": "B", "priority": 2, "content": "two"},
    ]
    out = render_sections(dirty)
    lines = out.splitlines()
    doubles = sum(1 for i in range(len(lines) - 1)
                  if lines[i].strip() == "---" and lines[i + 1].strip() == "---")
    checks.append({
        "name": "no doubled delimiters",
        "status": "ok" if doubles == 0 else "fail",
        "detail": f"doubles={doubles}",
    })
    # Stray --- in content is stripped but the content itself is kept.
    checks.append({
        "name": "stray delimiter stripped from content",
        "status": "ok" if "stray" in out and "---\n---" not in out else "fail",
        "detail": "content kept, delimiter removed",
    })
    # Doubled source delimiters collapse on parse.
    doubled_src = "---\n# A\none\n---\n---\n# B\ntwo\n---"
    parsed2 = parse_sections(doubled_src)
    checks.append({
        "name": "doubled source collapses",
        "status": "ok" if len(parsed2) == 2 else "fail",
        "detail": f"{len(parsed2)} sections",
    })
    # Section-level merge (mechanical, no provider) preserves all.
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "combined.md"
        r = merge_sections(path, secs[:2], secs[2:])
        merged = parse_sections(path.read_text())
        checks.append({
            "name": "section merge preserves all",
            "status": "ok" if r.get("ok") and len(merged) == 3 else "fail",
            "detail": f"merged={r.get('merged')} recovered={len(merged)} optimized={r.get('optimized')}",
        })
    return checks
