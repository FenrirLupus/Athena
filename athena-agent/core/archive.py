"""Archiving — the vault copy and the index feed, in the one order that keeps them honest.

The operator's rule (documentation/proposals/recall/notes.md): the jsonl files are the active history, and what
leaves them is logged into the vault. Today the only writer is compression, which fires when the window fills —
at a 1,048,576-token window that is months. So this module adds the two things that stop completeness from
depending on an event that never happens:

  * `backfill()` — replay every snapshot and the live window into the vault (idempotent by uid);
  * `after_turn()` — copy the live window after every turn, then feed the index.

Order inside `after_turn` is the invariant, and it is not stylistic: the vault is written first, so the index can
be *behind* the store after a crash (a missed match costs a provider call) but can never be *ahead* of it (which
would quote a row the long-term store does not have).

Nothing here trims anything. `LATEST.jsonl` is truncated only by compression, and only after the vault write.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core import trace
from data.index import Index
from data.vault import Vault

__all__ = ["transcript_rows", "vault_uids", "missing", "backfill", "after_turn"]


def transcript_rows(root) -> list[dict]:
    """Every row the lane still has on disk: snapshots first, then the live window. Raw, never deduped.

    Deliberately not `Session.history_all()`: its key is `(ts, role, content[:200])`, so two long rows sharing a
    200-character prefix — two reads of the same file, say — collapse into one and the archive silently loses a
    row. The archive reads the files themselves.
    """
    sessions = Path(root) / "sessions"
    rows: list[dict] = []
    for path in sorted(sessions.glob("session_*.jsonl")) + ([sessions / "LATEST.jsonl"] if (sessions / "LATEST.jsonl").is_file() else []):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            trace.failure("archive.read.failed", exc, path=str(path))
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue               # a corrupt tail line costs that line, not the archive
            if isinstance(row, dict) and row.get("role"):
                rows.append(row)
    return rows


def vault_uids(storage) -> set[str]:
    """The identities the vault already holds, across the master and every tier (all read-only here)."""
    have: set[str] = set()
    for db in sorted(Path(storage).glob("*.db")):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            for (uid,) in conn.execute("SELECT uid FROM messages"):
                if uid:
                    have.add(uid)
        except sqlite3.Error:
            continue                    # an unreadable tier is a gap in the check, not a crash
        finally:
            conn.close()
    return have


def _uid(lane: str, row: dict) -> str:
    return Vault.identity(lane, row.get("ts"), row.get("role"), row.get("content") or "")


def missing(root, lane: str | None = None) -> int:
    """How many of the lane's own rows the vault does not hold. 0 is the only acceptable number."""
    root = Path(root)
    lane = str(lane or root.name)
    have = vault_uids(root / "sessions" / "storage")
    return sum(1 for row in transcript_rows(root) if _uid(lane, row) not in have)


def backfill(root, lane: str | None = None) -> dict:
    """Replay everything on disk into the vault. Idempotent: a second run writes nothing."""
    root = Path(root)
    lane = str(lane or root.name)
    rows = transcript_rows(root)
    vault = Vault(root / "sessions" / "storage" / "VAULT.db", lane)
    before_missing = missing(root, lane)
    before_rows = vault.count()
    try:
        submitted = vault.insert_messages(rows, "backfill")
    except sqlite3.Error as exc:
        trace.failure("archive.backfill.failed", exc, lane=lane, rows=len(rows))
        return {"lane": lane, "transcript": len(rows), "missing_before": before_missing,
                "submitted": 0, "written": 0, "missing_after": missing(root, lane), "error": str(exc)[:200]}
    after_rows = vault.count()
    report = {"lane": lane, "transcript": len(rows), "missing_before": before_missing,
              "submitted": submitted, "written": after_rows - before_rows, "vault_rows": after_rows,
              "missing_after": missing(root, lane)}
    trace.event("archive.backfill", level="info", **report)
    return report


def vault_known(root, lane: str) -> set | None:
    """The identities the master vault already holds for this lane — read-only, and never the index.

    The index may lag the store (it is fed second) and is rebuildable, so it cannot decide what reaches the
    durable record. `None` means the set could not be read; the caller then re-offers the whole window, which is
    today's behaviour rather than a guess.
    """
    db = Path(root) / "sessions" / "storage" / "VAULT.db"
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        return {row[0] for row in conn.execute(
            "SELECT uid FROM messages WHERE channel=? AND uid IS NOT NULL", (lane,))}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def after_turn(session, cfg: dict) -> dict:
    """Copy the live window into the vault, then feed the index. Never raises: it runs in a turn's `finally`.

    The vault write is reported loudly when it fails — a lost copy is a true loss — but it does not fail the turn:
    unlike compression, nothing is about to be destroyed, and the next turn re-reads the same window.
    """
    result: dict = {"vaulted": 0, "indexed": 0}
    try:
        rows = session.history()
    except Exception as exc:                          # noqa: BLE001
        trace.failure("archive.history.failed", exc, channel=getattr(session, "channel", ""))
        return result
    if not rows:
        return result
    try:
        session.vault.insert_messages(rows, "live", known=vault_known(session.root, session.channel))
        result["vaulted"] = len(rows)
    except sqlite3.Error as exc:
        trace.failure("archive.vault.failed", exc, channel=getattr(session, "channel", ""), rows=len(rows))
        return result                 # no vault row means no index row: indexed ⊆ vault
    try:
        index = Index.for_lane(session.root, session.channel)
        index.set_ceiling(int((cfg.get("recall") or {}).get("index_max_rows") or 0))
        result["indexed"] = index.add(rows)
    except Exception as exc:                          # noqa: BLE001 — a broken index is a cache miss, not a loss
        trace.failure("archive.index.failed", exc, channel=getattr(session, "channel", ""))
    return result
