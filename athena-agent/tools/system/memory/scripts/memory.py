"""Memory: the agent's own durable notes, kept in `memories/MEMORY.md`.

The index is read on every turn by `context/stack.py`, so what lands here costs prompt tokens forever. That shapes
the design: one line per fact, a hard cap, dedupe on write, and no model involved anywhere — a memory system that
needs an LLM to tidy itself is one that spends credits to grow.

Row format, the same shape the reader parses:

    - YYYY-MM-DD [preference] Fenrir prefers plain text over markdown in the terminal
"""
from __future__ import annotations

import re
from pathlib import Path

from core import durable, timefmt
from core import trace
from tools.registry import Tool, ToolError

__all__ = ["build", "offer_candidates", "ROWS", "BYTES", "ROW"]

ROWS = 200          # oldest rows are pruned past this
BYTES = 24_000      # a memory that outgrows this is a log, not an index
ROW = re.compile(r"^- (\d{4}-\d{2}-\d{2}) \[([a-z0-9 _-]{1,24})\] (.+)$")


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile whose memory is being read."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")

HEADER = ("# Memory — the shared index\n\n"
          "One line per fact. Dated, tagged, and short enough to be worth the tokens it costs on every turn.\n")


def _file(profile_root: Path | None) -> Path:
    base = Path(profile_root) if profile_root else Path(".")
    return base / "memories" / "MEMORY.md"


def _rows(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if ROW.match(line)]


def _write(path: Path, rows: list[str]) -> None:
    durable.write_state(path, HEADER + "\n" + "\n".join(rows) + "\n")


def _prune(rows: list[str]) -> list[str]:
    kept = rows[-ROWS:]
    while kept and len("\n".join(kept).encode("utf-8")) > BYTES:
        kept.pop(0)
    return kept


def _bare(row: str) -> str:
    """The text of a row, without its date or its marker, so the same fact is recognised in either file."""
    text = re.sub(r"^- \d{4}-\d{2}-\d{2} ", "", row)
    text = re.sub(r"^\[[^\]]+\] ", "", text)              # a memory tag
    text = re.sub(r"^from-(user|assistant) ", "", text)     # a candidate marker
    return text.strip().lower()


CANDIDATES = "CANDIDATES.md"
CANDIDATE_MAX = 40


def offer_candidates(profile_root: Path | None, messages: list[dict]) -> int:
    """Offer evicted turns to the agent as candidate facts. Returns how many new lines were added.

    Nothing is promoted: a candidate is a reminder, not a memory. Promotion stays an explicit `memory_save`, so the
    index cannot quietly fill with whatever happened to be old. This is the only part of memory that runs on its own.
    """
    base = Path(profile_root) if profile_root else Path(".")
    folder = base / "memories"
    known = {_bare(r) for r in _rows(folder / "MEMORY.md")}
    path = folder / CANDIDATES
    existing = [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- ")] \
        if path.is_file() else []
    have = {_bare(line) for line in existing}

    fresh = []
    for msg in messages:
        text = " ".join(str(msg.get("content") or "").split())
        if msg.get("role") not in ("user", "assistant") or not text or len(text) > 240:
            continue                                    # skips tool rows and long passages nobody wrote as a fact
        line = f"- {timefmt.stamp()[:10]} from-{msg.get('role')} {text}"
        key = _bare(line)
        if key in known or key in have:
            continue
        have.add(key)
        fresh.append(line)

    if not fresh:
        return 0
    kept = (existing + fresh)[-CANDIDATE_MAX:]
    durable.write_state(path, "# Candidates — turns that were evicted, not facts yet\n\n"
                             "Save the ones still true with `memory_save`; delete the rest.\n\n"
                             + "\n".join(kept) + "\n")
    return len(fresh)


def build(ctx=None) -> list[Tool]:
    """Memory tools for one agent; `ctx` decides whose `memories/MEMORY.md` is read and written."""
    from tools.loader import agent_roots

    profile_root, athena_root = agent_roots(ctx)

    def memory_save(text: str, tag: str = "note") -> dict:
        body = " ".join(str(text or "").split())
        label = re.sub(r"[^a-z0-9 _-]", "", str(tag or "").strip().lower())[:24] or "note"
        # the fact itself is not logged — its length and tag are enough to tell one save from another
        with trace.span("tool.memory.save", channel=_lane(ctx), chars=len(body), tag=label) as sp:
            if not body:
                raise ToolError("nothing to remember")
            path = _file(profile_root)
            rows = _rows(path)
            row = f"- {timefmt.stamp()[:10]} [{label}] {body}"
            if any(_bare(r) == _bare(row) for r in rows):
                sp.ok(saved=False, rows=len(rows), duplicate=True)
                return {"saved": False, "reason": "already remembered", "rows": len(rows)}
            rows = _prune(rows + [row])
            try:
                _write(path, rows)
            except OSError as exc:
                raise ToolError(f"memory is not writable here: {exc}") from exc
            sp.ok(saved=True, rows=len(rows))
            return {"saved": True, "rows": len(rows), "row": row}

    def memory_search(query: str = "", limit: int = 12) -> dict:
        needle = str(query or "").strip().lower()
        with trace.span("tool.memory.search", channel=_lane(ctx), query_chars=len(needle), limit=limit) as sp:
            rows = _rows(_file(profile_root))
            hits = [r for r in rows if needle in r.lower()] if needle else rows
            sp.ok(matches=len(hits), total=len(rows))
            return {"matches": hits[-max(1, min(int(limit), 50)):], "total": len(rows)}

    def memory_edit(match: str, text: str, tag: str = "") -> dict:
        """Rewrite one existing row in place, keeping its date. Ambiguity is reported, never guessed."""
        needle = str(match or "").strip().lower()
        body = " ".join(str(text or "").split())
        with trace.span("tool.memory.edit", channel=_lane(ctx), match_chars=len(needle), chars=len(body)) as sp:
            if not needle or not body:
                raise ToolError("memory_edit needs the phrase to find and the new text")
            path = _file(profile_root)
            rows = _rows(path)
            hits = [i for i, row in enumerate(rows) if needle in row.lower()]
            if not hits:
                sp.ok(updated=0, rows=len(rows), reason="no match")
                return {"updated": 0, "reason": "no row contains that phrase", "rows": len(rows)}
            if len(hits) > 1:
                sp.ok(updated=0, rows=len(rows), reason="ambiguous")
                return {"updated": 0, "reason": "more than one row matches; narrow the phrase",
                        "matches": [rows[i] for i in hits], "rows": len(rows)}
            index = hits[0]
            old_label = ROW.match(rows[index]).group(2)
            label = re.sub(r"[^a-z0-9 _-]", "", str(tag or "").strip().lower())[:24] or old_label
            date = ROW.match(rows[index]).group(1)          # an edit keeps the day the fact was learned
            rows[index] = f"- {date} [{label}] {body}"
            try:
                _write(path, rows)
            except OSError as exc:
                raise ToolError(f"memory is not writable here: {exc}") from exc
            sp.ok(updated=1, rows=len(rows))
            return {"updated": 1, "row": rows[index], "rows": len(rows)}

    def memory_forget(match: str) -> dict:
        needle = str(match or "").strip().lower()
        with trace.span("tool.memory.forget", channel=_lane(ctx), match_chars=len(needle)) as sp:
            if not needle:
                raise ToolError("say what to forget")
            path = _file(profile_root)
            rows = _rows(path)
            kept = [r for r in rows if needle not in r.lower()]
            removed = len(rows) - len(kept)
            if removed:
                _write(path, kept)
            sp.ok(removed=removed, rows=len(kept))
            return {"removed": removed, "rows": len(kept)}

    return [
        Tool("save",
             "Remember a durable fact about the person, a decision, or a correction. One line; never task progress.",
             {"type": "object", "required": ["text"],
              "properties": {"text": {"type": "string", "description": "the fact, one sentence"},
                             "tag": {"type": "string",
                                     "description": "preference, decision, fact, person, correction"}}},
             memory_save),
        Tool("search",
             "Search this agent's memory index.",
             {"type": "object",
              "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
              "required": []},
             memory_search),
        Tool("edit",
             "Rewrite one remembered row in place. Fails loudly when the phrase matches none or several.",
             {"type": "object", "required": ["match", "text"],
              "properties": {"match": {"type": "string", "description": "phrase identifying the existing row"},
                             "text": {"type": "string", "description": "the corrected fact"},
                             "tag": {"type": "string", "description": "new tag; empty keeps the old one"}}},
             memory_edit),
        Tool("forget",
             "Remove remembered lines containing a phrase.",
             {"type": "object", "required": ["match"],
              "properties": {"match": {"type": "string"}}},
             memory_forget),
    ]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "memory")
