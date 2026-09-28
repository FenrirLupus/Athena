"""Knowledge enrichment — the hourly pass that tends the archive.

The knowledge system's ENRICHMENT sweep (the Operator's spec, 08-09):

  The session happens → populates vault.db naturally. Every hour a check
  runs: has the vault been modified in the last hour? If yes, a provider
  call fills in EACH incomplete row one by one — the INCOMPLETE rows are
  the baseline, and the +/- 3 rows above and below the target row are the
  additive information that helps fill THAT row (the sliding window:
  - rows = previous history, + rows = next history).

  The context / setting / location / emotion / mood / activity columns
  are populated ONLY where applicable, decided from the content alone —
  one-shot, iterating until all applicable information is filled in.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from core.db import connect_vault, COLUMN_FAMILIES

# The columns the enrichment pass may fill (the scene columns).
ENRICHABLE = [
    "context",        # ≤128-word semantic summary of what is happening
    "setting",        # the details of the environment (what KIND of room)
    "location",       # just where it is happening
    "emotion",        # internal feeling
    "mood",           # outward display
    "activity",       # what the actor is doing
]
WINDOW = 3            # +/- rows above and below the target row
MAX_ITER = 4          # one-shot iterations until applicable fields fill


def _is_incomplete(row: dict) -> bool:
    """A row is a candidate when ANY enrichable column is empty/NULL."""
    return any(not str(row.get(col) or "").strip() for col in ENRICHABLE)


def incomplete_rows(profile: str = "", limit: int = 500) -> list[dict]:
    """The baseline: rows missing at least one scene column (recent first)."""
    conn = connect_vault(profile)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
        rows = conn.execute(
            f"SELECT rowid, {', '.join(cols)} FROM entries WHERE deleted=0 "
            "ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows if _is_incomplete(dict(r))]
    finally:
        conn.close()


def sliding_window(target_rowid: int, profile: str = "",
                   n: int = WINDOW) -> dict:
    """The +/-n rows around the target (previous history + next history).

    Returns {"previous": [...], "target": {...}, "next": [...]} — the
    sliding window of information used to fill the TARGET row only.
    """
    conn = connect_vault(profile)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
        col_list = ", ".join(cols)
        prev = conn.execute(
            f"SELECT {col_list} FROM entries WHERE deleted=0 AND rowid<? "
            "ORDER BY rowid DESC LIMIT ?", (target_rowid, n)).fetchall()
        nxt = conn.execute(
            f"SELECT {col_list} FROM entries WHERE deleted=0 AND rowid>? "
            "ORDER BY rowid ASC LIMIT ?", (target_rowid, n)).fetchall()
        tgt = conn.execute(
            f"SELECT {col_list} FROM entries WHERE rowid=?",
            (target_rowid,)).fetchone()
        return {
            "previous": [dict(r) for r in prev],
            "target": dict(tgt) if tgt else {},
            "next": [dict(r) for r in nxt],
        }
    finally:
        conn.close()


def vault_modified_since(seconds: int = 3600, profile: str = "") -> bool:
    """Change-detecting gate: was the vault written in the last `seconds`?

    The hourly check ONLY fires the enrichment pass when the vault has
    new rows (this is the free GATE — no provider call when nothing
    changed).
    """
    from core.db import vault_path
    p = Path(vault_path(profile))
    if not p.exists():
        return False
    age = time.time() - p.stat().st_mtime
    return age < seconds


def _parse_enrichment(raw: str) -> dict:
    """Parse the model's JSON (tolerating fenced blocks)."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _prompt_for(window: dict) -> str:
    """Build the one-shot enrichment prompt for the target row."""
    t = window["target"]
    content = str(t.get("content") or "")
    role = str(t.get("role") or "")
    prev = []
    for r in window["previous"]:
        prev.append(f"- [{r.get('role')}] {str(r.get('content') or '')[:200]}")
    nxt = []
    for r in window["next"]:
        nxt.append(f"- [{r.get('role')}] {str(r.get('content') or '')[:200]}")
    prev_txt = "\n".join(prev) or "(none)"
    nxt_txt = "\n".join(nxt) or "(none)"
    return f"""You are the knowledge system's enrichment pass. Given the target row
below and its sliding window (previous history = -3 rows, next history =
+3 rows), fill in ONLY the fields that are APPLICABLE to the situation,
decided from the CONTENT alone. Leave a field null when it does not apply.

Target row ({role}):
{content}

Previous history (-3 rows):
{prev_txt}

Next history (+3 rows):
{nxt_txt}

Return STRICT JSON with any subset of these keys (null when not applicable):
{{
  "context": "one sentence, <=128 words, describing what is happening semantically",
  "setting": "the details of the environment (what kind of room/place, stray details)",
  "location": "just where it is happening",
  "emotion": "internal feeling if derivable",
  "mood": "outward display if derivable",
  "activity": "what the actor is doing if derivable"
}}
Fill in nothing that cannot be derived from the content. JSON only."""


def enrich_row(window: dict, providers=None, max_iter: int = MAX_ITER) -> dict:
    """Fill ONE row via the iterating provider call (1-shot until done).

    Returns the fields to write: {col: value} for applicable fields only.
    """
    from core.message_loop import MessageLoop
    loop = MessageLoop(providers=providers)
    prompt = _prompt_for(window)
    fields: dict = {}
    missing = set(ENRICHABLE)
    for _ in range(max_iter):
        try:
            result = loop.run_turn(prompt, history=[])
        except Exception:
            break
        parsed = _parse_enrichment(result.reply)
        if not parsed:
            # Keep iterating — ask again with what's still missing.
            prompt = (prompt + "\n\nStill missing: "
                      + ", ".join(sorted(missing))
                      + ". Return JSON with ONLY these, or {} if truly none apply.")
            continue
        for col in list(missing):
            val = parsed.get(col)
            if val is not None and str(val).strip():
                fields[col] = str(val).strip()
                missing.discard(col)
        if not missing:
            break
    return fields


def apply_fields(target_rowid: int, fields: dict, profile: str = "") -> int:
    """Write the enriched fields to the target row. Returns rows updated."""
    if not fields:
        return 0
    conn = connect_vault(profile)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
        sets = []
        vals = []
        for col, val in fields.items():
            if col in cols:
                sets.append(f"{col}=?")
                vals.append(val)
        if not sets:
            return 0
        vals.append(target_rowid)
        conn.execute(f"UPDATE entries SET {', '.join(sets)} WHERE rowid=?",
                     vals)
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def run_once(profile: str = "", limit: int = 500, dry_run: bool = False,
             providers=None) -> dict:
    """The hourly enrichment sweep (the change-detecting gate already ran).

    For each incomplete row: build the ±3 sliding window → provider call
    (iterating until applicable fields fill) → write the fields.
    """
    from core.logging import log_event
    rows = incomplete_rows(profile=profile, limit=limit)
    filled = 0
    updated = []
    for row in rows:
        rowid = row["rowid"]
        window = sliding_window(rowid, profile=profile)
        if dry_run:
            updated.append({"rowid": rowid, "fields": ENRICHABLE})
            filled += 1
            continue
        fields = enrich_row(window, providers=providers)
        if fields:
            n = apply_fields(rowid, fields, profile=profile)
            if n:
                filled += 1
                updated.append({"rowid": rowid, "fields": list(fields)})
    result = {"candidates": len(rows), "filled": filled,
              "updated": updated[:20], "at": datetime.now().isoformat(
                  timespec="seconds")}
    log_event(2, f"enrichment pass: {filled}/{len(rows)} rows filled",
              source="knowledge", action="enrich")
    return result
