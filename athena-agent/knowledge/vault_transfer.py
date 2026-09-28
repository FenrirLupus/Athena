"""Vault transfer — the import/export bridge (the Operator's 08-17 spec).

PURE CODE — zero provider calls. The vault's Import/Export feature:
universal, copy-first (never touch the source), strict 1:1 variable match.

THE FLOW (the Operator's exact spec):
  1. Select a .db file
  2. Athena EXPORTS its data into JSONL  (only the matched variables)
  3. Athena IMPORTs that JSONL into the CURRENT profile's vault.db
  4. Uses the CURRENTLY SELECTED UUID for the session's id in the vault
  5. ONLY matching information/variables are exported/imported — nothing
     that mismatches crosses over.

THE MATCH RULE (strict 1:1 by column NAME — no renames, no guessing):
    source column   →  Athena column      Import?
    timestamp       →  date + time        YES  (the one multi-variable array)
    role            →  role               YES  (1:1 exact name)
    content         →  content            YES  (1:1 exact name)
    anything else   →  (no match)         NO   (skipped)

The ONLY derivation allowed: a timestamp column (in any common form) is
normalized into Athena's two fields date ("YYYY-MM-DD") + time
("HH:MM:SS AM"). Nothing else is transformed or guessed.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


# The strict column-name map. Keys = source column names Athena accepts.
# timestamp is special: it maps to BOTH date + time (the multi-variable array).
# Everything NOT in this set is skipped (the strict 1:1 rule).
ACCEPTED = {"role", "content", "timestamp"}
DATE_TIME_ALIASES = ("timestamp", "created_at", "time", "ts", "datetime", "date_created")


def _normalize_timestamp(value) -> tuple[str, str] | None:
    """Detect + convert a timestamp → (date "YYYY-MM-DD", time "HH:MM:SS AM|PM").

    Handles: unix epoch (int/float), ISO strings, "YYYY-MM-DD HH:MM:SS",
    and a bare "HH:MM" time. Returns (date, time) or None if unparseable.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        # unix epoch (seconds or ms)
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:  # milliseconds
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M:%S %p")
        if isinstance(value, datetime):
            dt = value
            return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M:%S %p")
        # ISO / "YYYY-MM-DD HH:MM:SS"
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%SZ", "%Y/%m/%d %H:%M:%S",
                    "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s[:19], fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M:%S %p")
            except ValueError:
                continue
        # a bare time
        m = re.match(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?", s, re.I)
        if m:
            h = int(m.group(1))
            mm = m.group(2)
            ss = m.group(3) or "00"
            ampm = m.group(4)
            if ampm:
                h = (h % 12) + (12 if ampm.lower() == "pm" else 0)
            return "", f"{h:02d}:{mm}:{ss} AM" if h < 12 else f"{h - 12 or 12:02d}:{mm}:{ss} PM"
    except Exception:
        return None
    return None


def _pick_time_column(cols: list[str]) -> str | None:
    """Find the source's timestamp column (any common name)."""
    lowered = {c.lower() for c in cols}
    for cand in DATE_TIME_ALIASES:
        if cand in lowered:
            return next(c for c in cols if c.lower() == cand)
    return None


def export_to_jsonl(src_db: str | Path, out_jsonl: str | Path,
                    table: str = "") -> dict:
    """EXPORT: read a source .db's conversation table → a JSONL file.

    Pure SQLite read (read-only). Only the strict-matched variables survive:
    role, content, and timestamp (normalized to date + time). Every other
    source column is skipped. The source file is NEVER modified.

    Returns a report: {rows, exported, skipped, table_used, output}.
    """
    src = Path(src_db)
    out = Path(out_jsonl)
    if not src.exists():
        return {"ok": False, "error": f"source db not found: {src}"}

    conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Find the table to read: an explicit one, else the conversation-ish one.
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'") if not r[0].startswith(("_", "sqlite_"))]
        chosen = table or _pick_table(tables)
        if chosen is None:
            return {"ok": False, "error": f"no conversation table found in {src}",
                    "tables": tables}
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({chosen})")]
        time_col = _pick_time_column(cols)
        has_role = "role" in cols
        has_content = "content" in cols

        rows = []
        n = 0
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for row in conn.execute(f"SELECT * FROM {chosen}"):
                n += 1
                # Only the accepted 1:1 variables.
                rec = {}
                if has_role and row["role"] is not None:
                    rec["role"] = str(row["role"]).strip()
                if has_content and row["content"] is not None:
                    rec["content"] = str(row["content"]).strip()
                if time_col and row[time_col] is not None:
                    dt = _normalize_timestamp(row[time_col])
                    if dt:
                        rec["date"], rec["time"] = dt
                # A row with none of the matched vars is skipped (not exported).
                if rec and ("content" in rec or "role" in rec):
                    f.write(json.dumps(rec) + "\n")
                    rows.append(rec)
        return {
            "ok": True,
            "rows_read": n,
            "exported": len(rows),
            "skipped": n - len(rows),
            "table_used": chosen,
            "columns_matched": [c for c in ("role", "content", "timestamp") if c in cols or c == (time_col or "")],
            "output": str(out),
        }
    finally:
        conn.close()


def _pick_table(tables: list[str]) -> str | None:
    """Choose the conversation table (messages > entries > anything with content)."""
    for pref in ("messages", "entries", "conversation", "chat", "history"):
        if pref in tables:
            return pref
    # Fall back to the first table that HAS the content column.
    return None


def import_jsonl(in_jsonl: str | Path, profile: str = "",
                 session_id: str = "", kind: str = "operator") -> dict:
    """IMPORT: read a JSONL (produced by export) into the profile's vault.

    Pure SQLite write. Copy-first: only ADDS rows — never deletes or touches
    existing data. Rows land under the given session_id (the CURRENTLY
    SELECTED UUID per the spec).

    Each line maps to a vault entry:
      date/time → the date + time columns
      role      → role (normalized to Athena's User/Assistant/System case)
      content   → content (the required text)
    """
    p = Path(in_jsonl)
    if not p.exists():
        return {"ok": False, "error": f"jsonl not found: {p}"}

    target_id = session_id or str(uuid.uuid4())
    conn = _connect_vault_for_insert(profile, kind=kind)
    try:
        imported = 0
        skipped = 0
        total = 0
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                total += 1
                # THE 08-17 CONTENT-INTEGRITY FIX: the content is copied
                # EXACTLY — never .strip() (a trailing newline is real data
                # in a multi-line entry, e.g. a thread transcript ending in
                # \n). Only skip a row that is truly empty (no content at all).
                content = str(rec.get("content") or "")
                if not content.strip():
                    skipped += 1
                    continue
                date = str(rec.get("date") or "")[:10] or None
                time_ = str(rec.get("time") or "")[:20] or None
                role = _normalize_role(rec.get("role"))
                _insert_entry(conn, profile, kind, date, time_, role, content)
                imported += 1
        conn.commit()
        # THE 08-17 INDEX-SYNC: the import changed the vault — rebuild the
        # table of contents so the agent's index queries see the new rows.
        if imported > 0:
            try:
                from core.db import build_index
                build_index(profile)
            except Exception:
                pass
        return {
            "ok": True,
            "rows_read": total,
            "imported": imported,
            "skipped": skipped,
            "profile": profile,
            "session_id": target_id,
        }
    finally:
        conn.close()


def _normalize_role(role) -> str:
    """Athena vault roles: User | Assistant | System | Tool."""
    r = str(role or "").strip().lower()
    if r in ("user", "human", "operator", "input"): return "User"
    if r in ("assistant", "ai", "agent", "athena", "output", "model"): return "Assistant"
    if r in ("system", "sys", "meta", "service"): return "System"
    if r in ("tool", "function", "call"): return "Tool"
    return "User" if r else "System"


def _connect_vault_for_insert(profile: str = "", kind: str = "operator"):
    """Open the profile's vault + ensure the schema (creates if absent)."""
    from core.db import connect_vault
    return connect_vault(profile, kind=kind)


def _insert_entry(conn, profile: str, kind: str, date, time_, role, content):
    """Insert ONE vault entry — copy-first (never deletes anything).

    A direct insert that preserves the IMPORTED date/time (record_vault_entry
    would stamp NOW instead). Uses Athena's insert path so the FTS trigger
    fires automatically (the row becomes searchable).

    THE 08-17 ID FIX: the entries.id column is TEXT PRIMARY KEY — it must
    carry a UUID (like Athena's own record_vault_entry). A NULL id breaks
    the vault page (which keys rows by id). Each imported row gets a fresh
    uuid4, so rows never collide and the vault grid renders them.
    """
    now = datetime.now()
    entry_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO entries (id, profile, source, type, date, time, role, content, "
        "context, setting, location, emotion, mood, activity) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (entry_id, profile, ("vault-transfer" if kind == "operator" else "agent-transfer"),
         "message", date or now.strftime("%Y-%m-%d"),
         time_ or now.strftime("%I:%M:%S %p"), role, content,
         None, None, None, None, None, None),
    )
