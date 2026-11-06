"""References travel with the capability that declares them.

A reference is not a capability and is never queried: it is the extra text a `SKILL.md`, `TOOL.md` or `PLUGIN.md`
points at, and it arrives **when the descriptor is loaded** — a skill carries its documents into `skills(action="read")`, a tool
carries its documents into the description an agent sees. There is no reference tool, because "remember to call
a reference tool" was a step an agent could forget, and the document it forgot was the one explaining how to use the
capability at all.

Three rules, the same for all three layers, because the failure modes are the same:

* **Contained.** A path outside the capability's own folder is refused, not followed. A reference is not a way to
  read `../../.secret`.
* **Bounded.** The whole set either fits the cap or none of it is inlined: over the cap, the load carries one line per
  file with its size and the total, and says so. A document is never cut mid-way — half a spec reads like a whole one.
* **Declared or discovered.** `references: a.md, b.md` in the frontmatter names the set; without that key, every file
  under `references/` is the set.
"""
from __future__ import annotations

import json
from pathlib import Path

from core import trace

# one capability may add this much text to a load; the three shipped references together are 4 kB
MAX_ATTACH_BYTES = 6000


class ReferenceError(ValueError):
    """A reference that cannot be attached: outside the folder, unreadable, or gone."""


def declared(meta: dict | None) -> list[str]:
    """The `references:` frontmatter entry, as a list. Flat frontmatter, so a comma list or an inline JSON list."""
    raw = (meta or {}).get("references")
    if raw in (None, ""):
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = [text]
        else:
            raw = [part.strip() for part in text.split(",")]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(entry).strip() for entry in raw if str(entry).strip()]


def files(folder, meta: dict | None = None) -> tuple[list[Path], list[str]]:
    """(the reference files, the problems) for one capability folder — contained, and never raising."""
    base = Path(folder).resolve()
    problems: list[str] = []
    wanted = declared(meta)
    if wanted:
        candidates = []
        for entry in wanted:
            candidate = (base / entry).resolve()
            if candidate != base and base not in candidate.parents:
                # a containment refusal, not a parse error: the capability may not read out of its own folder
                trace.failure("reference.refused", None, capability=base.name, path=str(entry),
                              rule="path resolves outside the capability folder — refused")
                problems.append(f"{entry!r} points outside {base.name}/ — refused")
                continue
            if not candidate.is_file():
                trace.event("reference.missing", level="debug", capability=base.name, path=str(entry),
                            rule="declared but not on disk — skipped")
                problems.append(f"{entry!r} does not exist — skipped")
                continue
            candidates.append(candidate)
        return candidates, problems
    tree = base / "references"
    if not tree.is_dir():
        return [], problems
    found = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved != base and base not in resolved.parents:
            trace.failure("reference.refused", None, capability=base.name, path=str(path),
                          rule="discovered file resolves outside the capability folder — refused")
            problems.append(f"{path.name!r} resolves outside {base.name}/ — refused")
            continue
        found.append(resolved)
    return found, problems


def attach(folder, meta: dict | None = None, *, cap: int = MAX_ATTACH_BYTES) -> dict:
    """The text one loaded capability carries: every reference, or the list when the set is over the cap."""
    base = Path(folder).resolve()
    with trace.span("reference.attach", level="info", capability=base.name,
                    declared=len(declared(meta)), cap=cap) as sp:
        found, problems = files(base, meta)
        rows = []
        total = 0
        for path in found:
            try:
                size = path.stat().st_size
            except OSError as exc:
                problems.append(f"{path.name!r} could not be read: {exc}")
                continue
            rows.append({"path": str(path.relative_to(base)), "bytes": size})
            total += size

        report = {"count": len(rows), "bytes": total, "files": rows, "problems": problems,
                  "attached": [], "omitted": [], "text": ""}
        if not rows:
            sp.ok(count=0, bytes=0, attached=[], omitted=[], problems=problems)
            report["text"] = ("\n\n## references\n\n" + "\n".join(f"- {problem}" for problem in problems)
                              if problems else "")
            return report

        if total > cap:
            # the whole set is over the cap: none of it is inlined, and the trail names what was left out
            report["omitted"] = [row["path"] for row in rows]
            sp.ok(count=len(rows), bytes=total, attached=[], omitted=report["omitted"], problems=problems,
                  over_cap=True, rule=f"set over the {cap}-byte cap for one load — omitted")
            lines = [f"\n\n## references — {len(rows)} file(s), {total} bytes, over the {cap}-byte cap for one load",
                     "None of them is inlined here; open the files in the capability's folder when one is needed.\n"]
            lines += [f"- {row['path']} — {row['bytes']} bytes (omitted)" for row in rows]
            lines += [f"- ! {problem}" for problem in problems]
            report["text"] = "\n".join(lines)
            return report

        blocks = []
        for row in rows:
            path = base / row["path"]
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(f"{row['path']!r} could not be read: {exc}")
                continue
            report["attached"].append(row["path"])
            blocks.append(f"\n\n### {row['path']} ({row['bytes']} bytes)\n\n{body.strip()}")
        head = f"\n\n## references — {len(report['attached'])} file(s), {total} bytes"
        tail = "".join(f"\n- ! {problem}" for problem in problems)
        report["text"] = head + "".join(blocks) + tail
        sp.ok(count=len(rows), bytes=total, attached=report["attached"], omitted=[], problems=problems,
              inline_chars=len(report["text"]))
        return report
