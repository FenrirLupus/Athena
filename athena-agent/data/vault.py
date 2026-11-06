"""The vault — long-term storage in SQLite, written copy-first.

Every turn that leaves the live window is copied here *before* the live file is truncated, so the
vault is durable before anything is destructive. Rows are never silently deduped: a write happens
or it is reported as refused (documentation/architecture/15-session-storage.md §5).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from core import timefmt

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel        TEXT NOT NULL,
    snapshot    TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    ts          TEXT,
    tool_call_id TEXT,
    name        TEXT,
    created     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel);
CREATE INDEX IF NOT EXISTS idx_messages_snapshot ON messages(snapshot);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created);
"""


class Vault:
    def __init__(self, path: Path, channel: str):
        self.path = Path(path)
        self.channel = channel
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            self._migrate(conn)
            conn.executescript(SCHEMA)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Older vaults name the column `lane`; the term is `channel` now.

        The vault is a rebuildable copy, but a rename that leaves old files unreadable would look like
        data loss, so the column is renamed in place (SQLite 3.25+) instead of demanding a rebuild.
        """
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        except sqlite3.DatabaseError:
            return
        if "lane" in columns and "channel" not in columns:
            try:
                conn.execute("ALTER TABLE messages RENAME COLUMN lane TO channel")
                conn.execute("DROP INDEX IF EXISTS idx_messages_lane")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel)")
            except sqlite3.DatabaseError:
                pass

    def insert_messages(self, messages: list[dict], snapshot: str) -> int:
        """Copy a batch of turns into the vault. Returns how many rows were written."""
        if not messages:
            return 0
        now = timefmt.stamp()
        rows = [(self.channel, snapshot, m.get("role", ""), m.get("content", "") or "",
                 m.get("ts"), m.get("tool_call_id"), m.get("name"), now)
                for m in messages]
        with self._lock:
            with self._conn() as conn:
                conn.executemany(
                    "INSERT INTO messages (channel, snapshot, role, content, ts, tool_call_id, name, created)"
                    " VALUES (?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM messages WHERE channel=?", (self.channel,)).fetchone()[0])

    def snapshots(self) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT snapshot, COUNT(*) AS n, MIN(created) FROM messages WHERE channel=?"
                " GROUP BY snapshot ORDER BY MIN(id)", (self.channel,))
            return [{"snapshot": r[0], "messages": r[1], "first_seen": r[2]} for r in cur.fetchall()]

    def search(self, needle: str, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT role, content, snapshot, ts FROM messages WHERE channel=? AND content LIKE ?"
                " ORDER BY id DESC LIMIT ?", (self.channel, f"%{needle}%", int(limit)))
            return [{"role": r[0], "content": r[1], "snapshot": r[2], "ts": r[3]} for r in cur.fetchall()]

    def recent(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT role, content, snapshot, ts FROM messages WHERE channel=? ORDER BY id DESC LIMIT ?",
                (self.channel, int(limit)))
            return [{"role": r[0], "content": r[1], "snapshot": r[2], "ts": r[3]} for r in cur.fetchall()]
