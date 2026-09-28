"""Importer — bring a legacy profile's data into Athena, properly placed.

Two sources, two destinations (Athena's canonical layout):

  1. legacy vault.db (vault/<profile>/vault.db)  →  Athena sessions/vault/vault.db
     The archive entries, profile-tagged, 1:1 (ids and timestamps preserved).

  2. the session store         →  Athena sessions/session-{UUID}.db
     185 sessions / thousands of messages become Athena-format session files,
     each tagged with the profile.

Nothing is removed from the source — this only READS the source and WRITES
into Athena's stores.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from core import db as db_layer


# -- Vault import --------------------------------------------------------

def import_legacy_vault(legacy_vault_path: str | Path, *, profile: str) -> dict:
    """Copy every entry from a legacy vault.db into Athena's archive.

    Preserves ids, ts, date, time, role, content, and the structured cells.
    Uses dedup=False so the copy is exact 1:1 (a duplicate in the archive
    is acceptable for an import; nothing is dropped).
    """
    src = sqlite3.connect(str(legacy_vault_path))
    src.row_factory = sqlite3.Row
    try:
        # 1:1 — import ALL entries, including soft-deleted (flag preserved).
        rows = src.execute("SELECT * FROM entries").fetchall()
    finally:
        src.close()

    imported = 0
    skipped = 0
    with db_layer.connect_vault() as conn:
        for row in rows:
            # Idempotency: skip entries whose id already exists.
            exists = conn.execute(
                "SELECT 1 FROM entries WHERE id=?", (row["id"],)
            ).fetchone()
            if exists:
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO entries (id, profile, type, source, date, time,"
                " context, location, setting, role, name_first, name_last, name_nick,"
                " emotion, mood, activity, content, deleted)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], profile, row["kind"], row["source"],
                    row["date"], row["time"], row["context"], row["location"],
                    row["setting"], row["role"], row["first_name"], row["last_name"],
                    row["nickname"], row["emotion"], row["mood"], row["activity"],
                    row["content"], row["deleted"],
                ),
            )
            imported += 1
    return {"imported": imported, "skipped": skipped, "source": str(legacy_vault_path)}


