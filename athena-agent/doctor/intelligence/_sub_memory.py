"""Memory test — the --- block format + the Operator's budget contract."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from intelligence import memory as mem_mod

    checks = []
    # Isolate: _root → a temp dir (no real profile writes).
    orig_root = mem_mod._root
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mem_mod._root = lambda profile="": root
        try:
            # 1. add_entry writes a --- block (single delimiter between
            #    memories: 2 entries → 3 delimiters).
            mem_mod.add_entry("assistant", "Fact one about the vault",
                              title="Vault - The archive")
            mem_mod.add_entry("assistant", "The user prefers brief answers",
                              title="Communication")
            text = (root / "assistant" / "MEMORY.md").read_text()
            checks.append({
                "name": "entries are --- blocks (one delimiter between)",
                "status": "ok" if text.count("---") == 3
                and "# Vault - The archive" in text
                and "- Fact one about the vault" in text
                and "---\n### Communication" in text else "fail",
                "detail": text[:60].replace("\n", " | "),
            })
            # 2. read_entries returns {title, bullets} dicts.
            entries = mem_mod.read_entries("assistant")
            ok_shape = entries and "title" in entries[0] and "bullets" in entries[0]
            checks.append({
                "name": "entries parse to dicts",
                "status": "ok" if ok_shape else "fail",
                "detail": f"{len(entries)} entries",
            })
            # 3. Word caps: title ≤128, bullet ≤64.
            long_title = " ".join(["word"] * 200)
            long_bullet = " ".join(["word"] * 100)
            shrunk = mem_mod.shrink([{"title": long_title, "bullets": [long_bullet]}])
            checks.append({
                "name": "word caps (128/64)",
                "status": "ok" if len(shrunk[0]["title"].split()) == 128
                and len(shrunk[0]["bullets"][0].split()) == 64 else "fail",
                "detail": f"title={len(shrunk[0]['title'].split())} "
                          f"bullet={len(shrunk[0]['bullets'][0].split())}",
            })
            # 4. The token budget is 6400.
            checks.append({
                "name": "token budget 6400",
                "status": "ok" if mem_mod.TOKEN_BUDGET == 6400 else "fail",
                "detail": str(mem_mod.TOKEN_BUDGET),
            })
            # 5. Shrink drops oldest bullets/entries when over budget.
            big = [{"title": "t", "bullets": [f"fact {i}" for i in range(300)]}]
            shrunk_big = mem_mod.shrink(big, budget=50)
            checks.append({
                "name": "shrink fits budget",
                "status": "ok" if not mem_mod.over_budget(shrunk_big, 50) else "fail",
                "detail": f"{mem_mod.token_estimate(shrunk_big)} tokens",
            })
            # 6. The 6-level priority: more # = more important.
            mem_mod.add_entry("assistant", "trivial", title="T1", priority=1)
            mem_mod.add_entry("assistant", "critical", title="C1", priority=6)
            prio_entries = mem_mod.read_entries("assistant")
            by_title = {e["title"]: e for e in prio_entries}
            checks.append({
                "name": "priority parses from # count",
                "status": "ok" if by_title.get("T1", {}).get("priority") == 1
                and by_title.get("C1", {}).get("priority") == 6 else "fail",
                "detail": f"T1={by_title.get('T1', {}).get('priority')} "
                          f"C1={by_title.get('C1', {}).get('priority')}",
            })
            text6 = (root / "assistant" / "MEMORY.md").read_text()
            checks.append({
                "name": "priority renders as # count",
                "status": "ok" if "# T1" in text6 and "###### C1" in text6 else "fail",
                "detail": "T1=1#, C1=6#",
            })
            # 7. TIER chunking: levels 5-6 / 3-4 / 1-2 — the top tier always
            #    survives a tiny budget; lower tiers fill as budget allows.
            mixed = [
                {"title": "Low", "bullets": ["low fact " + "x" * 50], "priority": 1},
                {"title": "Mid", "bullets": ["mid fact " + "x" * 50], "priority": 4},
                {"title": "High", "bullets": ["high fact " + "x" * 50], "priority": 6},
            ]
            tiny = mem_mod.shrink(mixed, budget=5)
            checks.append({
                "name": "tiny budget keeps top tier only",
                "status": "ok" if tiny and all(e.get("priority", 3) >= 5 for e in tiny)
                and any(e["title"] == "High" for e in tiny) else "fail",
                "detail": f"kept={[e['title'] for e in tiny]}",
            })
            med = mem_mod.shrink(mixed, budget=30)
            checks.append({
                "name": "bigger budget fills lower tiers",
                "status": "ok" if len(med) == 3 else "fail",
                "detail": f"kept={[e['title'] for e in med]}",
            })
        finally:
            mem_mod._root = orig_root
    return checks
