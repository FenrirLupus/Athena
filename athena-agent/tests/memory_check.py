"""Memory: the agent can write what it will read back.

The index is injected into every prompt, so these checks are about the three things that go wrong with it: a fact
stored twice, an index that never shrinks, and a write that leaves the file half-written.
"""
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from tools import loader

CHECKS = {}


class Ctx:
    """The minimum a tool script's `build(ctx)` needs: whose memory, and which athena root."""

    def __init__(self, profile_root, athena_root=None):
        self.profile_root = Path(profile_root)
        self.athena_root = Path(athena_root) if athena_root else self.profile_root
        self.cfg = {}
        self.scope = None


def tools_at(root):
    return loader.action_map(memory.build, Ctx(root))


# the implementation comes from the package's TOOL.md, not from a module name: one source of truth
_row = loader.descriptor_named("memory")
assert _row is not None and _row["kind"] == "script", "the memory tool is not a package the loader can find"
memory = loader.import_script(_row["script"])


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    t = tools_at(root)
    CHECKS["four_tools"] = sorted(t) == ["edit", "forget", "save", "search"]

    first = t["save"](text="Fenrir prefers plain text in the terminal", tag="preference")
    CHECKS["save_writes_a_row"] = first["saved"] and memory.ROW.match(first["row"]) is not None

    again = t["save"](text="Fenrir prefers plain text in the terminal", tag="fact")
    CHECKS["same_fact_is_not_stored_twice"] = again["saved"] is False and again["rows"] == 1

    CHECKS["search_finds_it"] = len(t["search"](query="plain text")["matches"]) == 1
    CHECKS["search_misses_others"] = t["search"](query="nothing like this")["matches"] == []

    for i in range(memory.ROWS + 10):                    # more rows than the cap allows
        t["save"](text=f"filler fact number {i}", tag="fact")
    rows = memory._rows(root / "memories" / "MEMORY.md")
    CHECKS["cap_holds"] = len(rows) == memory.ROWS
    CHECKS["cap_drops_the_oldest"] = all("plain text" not in r for r in rows)

    CHECKS["no_temp_files_left"] = not list((root / "memories").glob("*.tmp"))

    edited = t["edit"](match="filler fact number 209", text="a corrected fact", tag="decision")
    after = memory._rows(root / "memories" / "MEMORY.md")
    CHECKS["edit_rewrites_in_place"] = edited["updated"] == 1 and any("a corrected fact" in r for r in after)
    CHECKS["edit_keeps_the_original_date"] = any(
        r.startswith(edited["row"].split(" [")[0]) and "a corrected fact" in r for r in after)
    CHECKS["edit_changes_the_tag"] = "[decision] a corrected fact" in edited["row"]
    CHECKS["edit_reports_no_match"] = t["edit"](match="nothing here", text="x")["updated"] == 0
    CHECKS["edit_reports_ambiguity"] = t["edit"](match="filler fact", text="x")["updated"] == 0
    CHECKS["edit_does_not_add_rows"] = len(after) == memory.ROWS

    removed = t["forget"](match="filler fact")
    CHECKS["forget_removes_rows"] = removed["removed"] == memory.ROWS - 1 and removed["rows"] == 1  # one row was edited, so it no longer matches "filler fact"

    fresh = tools_at(root)
    CHECKS["survives_a_new_tool_instance"] = fresh["search"](query="a corrected fact")["total"] == 1

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    t = tools_at(root)
    t["save"](text="the box runs Bazzite on an Nvidia card", tag="fact")
    msgs = [{"role": "user", "content": "the box runs Bazzite on an Nvidia card"},
            {"role": "user", "content": "please add a dark mode toggle to the settings page"},
            {"role": "tool", "content": "tool output that is not a fact"},
            {"role": "assistant", "content": "x" * 400}]
    added = memory.offer_candidates(root, msgs)
    text = (root / "memories" / "CANDIDATES.md").read_text(encoding="utf-8")
    CHECKS["candidates_offered"] = added == 1 and "dark mode toggle" in text
    CHECKS["candidates_skip_known_facts"] = "Bazzite" not in text
    CHECKS["candidates_skip_tool_rows"] = "not a fact" not in text
    CHECKS["candidates_skip_long_passes"] = "xxxx" not in text
    CHECKS["candidates_do_not_touch_the_index"] = "dark mode" not in \
        (root / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    again = memory.offer_candidates(root, msgs)
    CHECKS["candidates_are_not_re_offered"] = again == 0

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
raise SystemExit(0 if all(CHECKS.values()) else 1)
