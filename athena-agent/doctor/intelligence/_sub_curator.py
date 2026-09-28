"""Curator test — learn-by-doing: session.db + events → skills (factual)."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from intelligence import curator
    from intelligence.curator import (scan, candidates, review, create_skill,
                                      merge_skills, archive_skill, _similar,
                                      SKILLS_DIR, ARCHIVE_DIR, STATE_PATH)

    checks = []
    original_skills = curator.SKILLS_DIR
    original_archive = curator.ARCHIVE_DIR
    original_state = curator.STATE_PATH
    with tempfile.TemporaryDirectory() as td:
        curator.SKILLS_DIR = Path(td) / "skills"
        curator.ARCHIVE_DIR = Path(td) / "skills" / ".archive"
        curator.STATE_PATH = Path(td) / "state.json"
        try:
            # Similarity gate.
            checks.append({
                "name": "similarity gate",
                "status": "ok" if _similar("read-file", "read-files") >= 0.8
                and _similar("read", "vault-query") < 0.8 else "fail",
                "detail": f"read-file~read-files={_similar('read-file', 'read-files'):.2f}",
            })
            # Create a skill (factual authoring).
            r = create_skill("vault-export", "Export the vault",
                             "# vault export\n\nHow to export.", author="curator")
            checks.append({
                "name": "create skill",
                "status": "ok" if r.get("ok") else "fail",
                "detail": r.get("detail", r.get("name", "")),
            })
            # A second create with the same name is refused.
            r2 = create_skill("vault-export", "dup", "dup")
            checks.append({
                "name": "no duplicate create",
                "status": "ok" if not r2.get("ok") else "fail",
                "detail": r2.get("detail", "")[:40],
            })
            # Update bumps the version.
            r3 = curator.update_skill("vault-export", body="Extra knowledge.")
            checks.append({
                "name": "update bumps version",
                "status": "ok" if r3.get("ok") and r3.get("version") == "0.1.1" else "fail",
                "detail": f"version={r3.get('version')}",
            })
            # Merge: create a similar skill, merge it into the keeper.
            create_skill("vault-export-alt", "Export vault alt", "Alt content.")
            m = merge_skills("vault-export", "vault-export-alt")
            checks.append({
                "name": "merge absorbs duplicate",
                "status": "ok" if m.get("ok") and m.get("keeper") == "vault-export"
                and "Absorbed from" in (Path(td) / "skills" / "vault-export" / "SKILL.md").read_text() else "fail",
                "detail": f"keeper={m.get('keeper')} absorbed={m.get('absorbed')}",
            })
            # Archive moves the skill out of the active tree.
            create_skill("stale-thing", "Old skill", "Old body.")
            a = archive_skill("stale-thing")
            checks.append({
                "name": "archive moves stale skill",
                "status": "ok" if a.get("ok") and not (Path(td) / "skills" / "stale-thing" / "SKILL.md").exists()
                and (Path(td) / "skills" / ".archive" / "stale-thing.md").exists() else "fail",
                "detail": a.get("archived_to", "")[:40],
            })
            # Review runs with dry_run safety (no writes on dry run).
            rv = review(dry_run=True)
            checks.append({
                "name": "review dry-run safe",
                "status": "ok" if rv.get("dry_run") and rv.get("scan") else "fail",
                "detail": f"actions={len(rv.get('actions', []))}",
            })
            # The candidates filter: registered TOOLS are not skill
            # candidates (already tools), but non-tool repeated actions are.
            fake_report = {
                "tools_used": {"read": 5, "vault_export": 4},
                "friction": [],
                "skills": [],
            }
            cand = candidates(fake_report)
            names = [x["tool"] for x in cand["skill_candidates"]]
            checks.append({
                "name": "tool wrapper not a skill candidate",
                "status": "ok" if "read" not in names else "fail",
                "detail": f"candidates={names}",
            })
            checks.append({
                "name": "non-tool action is a skill candidate",
                "status": "ok" if "vault_export" in names else "fail",
                "detail": f"candidates={names}",
            })
            # Friction groups by TOOL, not by exact error text.
            import intelligence.curator as cur_mod
            orig_entry = cur_mod._event_entries
            cur_mod._event_entries = lambda profile="", limit=2000: [
                {"tool": "vault_query", "status": "INFO", "result": "error: no rows"},
                {"tool": "vault_query", "status": "INFO", "result": "error: different message"},
                {"tool": "vault_query", "status": "INFO", "result": "error: yet another"},
                {"tool": "read", "status": "GOOD", "result": "ok"},
            ]
            try:
                s = scan()
                fr = [f for f in s["friction"] if f["tool"] == "vault_query"]
                checks.append({
                    "name": "friction grouped by tool",
                    "status": "ok" if fr and fr[0]["count"] == 3 else "fail",
                    "detail": f"count={fr[0]['count'] if fr else 0}",
                })
            finally:
                cur_mod._event_entries = orig_entry
        finally:
            curator.SKILLS_DIR = original_skills
            curator.ARCHIVE_DIR = original_archive
            curator.STATE_PATH = original_state
    return checks
