"""The recall index — a derived, rebuildable read model over one lane's messages.

Not the store: the vault is (`data/vault.py`). This holds a copy of the text plus an FTS5 index, so a repeat can
be found in microseconds without loading a transcript or calling a model. Deleting the file costs nothing but a
rebuild, which is what makes it safe to treat as a cache rather than a record.

Two properties it keeps on purpose (documentation/proposals/recall/design.md §3b):

  * **one writer per file** — the lane's turns are serialised, so there is no lock of its own;
  * **index ⊆ vault** — `core/archive.py` writes the vault first and feeds this second, so the index can lag the
    store but can never hold a row the long-term store lacks. A lagging index costs a provider call; a leading one
    would quote a row that does not exist.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from core import timefmt
from core import trace
from data.vault import Vault

__all__ = ["Index", "path_for", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1
FILENAME = "recall.db"

# `[from @scout]` is how a peer's message is marked (server/channels.py:425). It is the author column's source of
# truth: a peer's question must never be answered with something the operator said.
_AUTHOR = re.compile(r"^\s*\[from @([A-Za-z0-9._-]+)\]\s*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS idx_messages (
    id       INTEGER PRIMARY KEY,
    uid      TEXT NOT NULL,
    channel  TEXT NOT NULL,
    ts       TEXT,
    role     TEXT NOT NULL,
    author   TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT '',
    content  TEXT NOT NULL,
    norm     TEXT NOT NULL,
    created  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_uid  ON idx_messages(channel, uid);
CREATE INDEX        IF NOT EXISTS idx_messages_role ON idx_messages(channel, role, id);

CREATE VIRTUAL TABLE IF NOT EXISTS idx_fts USING fts5(
    norm,
    content='idx_messages',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2 tokenchars '_.'"
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def path_for(root) -> Path:
    """`profiles/<lane>/sessions/storage/recall.db`, beside the vault it is derived from."""
    return Path(root) / "sessions" / "storage" / FILENAME


class Index:
    def __init__(self, path, lane: str):
        self.path = Path(path)
        self.lane = str(lane)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    @classmethod
    def for_lane(cls, root, lane: str) -> "Index":
        return cls(path_for(root), lane)

    def exists(self) -> bool:
        try:
            with self._conn() as conn:
                return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='idx_messages'"
                                    ).fetchone() is not None
        except sqlite3.Error:
            return False

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('lane', ?)", (self.lane,))
            lane = conn.execute("SELECT value FROM meta WHERE key='lane'").fetchone()
            if lane and lane[0] != self.lane:
                # a copied profile: the index describes someone else's thread and must not be used for this one
                raise ValueError(f"recall index {self.path} belongs to lane {lane[0]!r}, not {self.lane!r}")

    # -- writing -----------------------------------------------------------
    def add(self, rows, ingest=None) -> int:
        """Index a batch. Returns how many rows were actually new; a re-run writes 0.

        `ingest` normalises text (core.recall.normalize); passed in rather than imported here so this module owns
        storage and nothing else. Rows that are not a user or assistant message, that are `kind='seam'` (runtime
        markers, not speech), or whose text normalises to nothing are skipped.
        """
        from core.recall import normalize

        if ingest is not None:
            normalize = ingest
        # Cheap filters first, then identity, then the text work. Most rows of a live window are already held, and
        # normalising them again is exactly the cost this path removes; a held row counts as *offered* without
        # being normalised, which reproduces today's post-normalise count because an empty norm is never inserted.
        candidates = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            role = str(row.get("role") or "")
            if role not in ("user", "assistant"):
                continue
            kind = str(row.get("kind") or "")
            if kind == "seam":
                continue
            content = str(row.get("content") or "")
            # runtime notices are not the operator's words: an `[interrupt]` row is a message about a message
            # (server/admission.py), so matching or quoting it would put the runtime's voice in a quotation
            head = content.lstrip()
            if head.startswith("[interrupt]") or head.startswith("[delivery failed]"):
                continue
            candidates.append((role, kind, content, row.get("ts")))
        if not candidates:
            return 0

        written = 0
        with self._lock, self._conn() as conn:
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            try:
                ceiling = int(meta.get("max_rows") or 0)
            except (TypeError, ValueError):
                ceiling = 0
            held = int(conn.execute("SELECT COUNT(*) FROM idx_messages").fetchone()[0])
            known = {row[0] for row in conn.execute("SELECT uid FROM idx_messages WHERE channel=?", (self.lane,))}
            before_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM idx_messages").fetchone()[0])
            created = timefmt.stamp()
            prepared = []
            offered = 0
            for role, kind, content, ts in candidates:
                uid = Vault.identity(self.lane, ts, role, content)
                if uid in known:
                    offered += 1
                    continue
                norm = " ".join(normalize(content))
                if not norm.strip():
                    continue
                offered += 1
                match = _AUTHOR.match(content)
                prepared.append((uid, self.lane, ts, role, match.group(1) if match else "", kind,
                                 content, norm, created))
            if not prepared:
                trace.event("recall.index.add", level="debug", lane=self.lane, offered=offered,
                            written=0, ignored=offered)
                return 0
            room = max(0, ceiling - held) if ceiling else len(prepared)
            batch = prepared[:room]
            if batch:
                cursor = conn.executemany(
                    "INSERT INTO idx_messages (uid, channel, ts, role, author, kind, content, norm, created)"
                    " VALUES (?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(channel, uid) DO NOTHING", batch)
                count = cursor.rowcount
                written = count if isinstance(count, int) and count >= 0 else len(batch)
                # one backfill by the id watermark instead of an FTS insert per row: this module has one writer
                # per file, and ON CONFLICT DO NOTHING consumes no ids
                conn.execute(
                    "INSERT INTO idx_fts (rowid, norm) SELECT id, norm FROM idx_messages"
                    " WHERE channel=? AND id>?", (self.lane, before_id))
            if len(batch) < len(prepared):
                trace.event("recall.index.full", level="info", lane=self.lane, rows=held + written,
                            ceiling=ceiling, reason="ceiling reached — every turn falls back to the provider")
        trace.event("recall.index.add", level="debug", lane=self.lane, offered=offered,
                    written=written, ignored=offered - written)
        return written

    def set_ceiling(self, rows: int) -> None:
        """`recall.index_max_rows` — at the ceiling the index stops growing and turns pay for an answer.

        Written only when the stored value differs: it is an upsert and a WAL write, and the value changes when an
        operator edits the config, not once a turn.
        """
        with self._conn() as conn:
            current = conn.execute("SELECT value FROM meta WHERE key='max_rows'").fetchone()
            if current is not None and str(current[0]) == str(int(rows)):
                return
            conn.execute("INSERT INTO meta(key, value) VALUES('max_rows', ?)"
                         " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(rows)),))

    # -- reading -----------------------------------------------------------
    def count(self) -> int:
        try:
            with self._conn() as conn:
                return int(conn.execute("SELECT COUNT(*) FROM idx_messages WHERE channel=?", (self.lane,)).fetchone()[0])
        except sqlite3.Error:
            return 0

    def search(self, text: str, limit: int = 40, author: str = "", aliases=()) -> list[dict]:
        """Past questions that share words with `text`, best FTS rank first.

        Every token is quoted, so a message containing `AND`, `NEAR(` or `*` cannot become query syntax. Free
        answers are excluded as candidates: a quotation must never be its own source, and matching the original
        question instead keeps a repeated question free every time it is asked.

        `aliases` are the synonym map's expansion terms. Without them a paraphrase is often not in the candidate
        window at all — measured: `reply with ok` did not surface `say ok` in the top 40, and did at rank 2 once
        the query carried `reply`'s group.
        """
        from core.recall import tokens

        words = tokens(text)
        if not words:
            return []
        terms: list[str] = []
        for word in list(words) + list(aliases or ()):
            term = str(word).replace('"', "")
            if term and term not in terms:
                terms.append(term)
            if len(terms) >= 64:                     # MAX_QUERY_TERMS: an expanded query stays a query
                break
        match = " OR ".join('"' + term + '"' for term in terms)
        sql = ("SELECT m.id, m.uid, m.ts, m.content, m.author, bm25(idx_fts) AS rank"
               " FROM idx_fts JOIN idx_messages m ON m.id = idx_fts.rowid"
               " WHERE idx_fts MATCH ? AND m.channel = ? AND m.role = 'user'"
               " AND m.author = ? AND m.kind <> 'free'"
               " ORDER BY rank LIMIT ?")
        try:
            with self._conn() as conn:
                cur = conn.execute(sql, (match, self.lane, str(author or ""), int(limit)))
                return [{"id": r[0], "uid": r[1], "ts": r[2], "content": r[3], "author": r[4], "rank": r[5]}
                        for r in cur.fetchall()]
        except sqlite3.Error as exc:
            # FTS syntax from a hostile message, or a corrupt index: a trace line and no candidates, never a raise
            trace.failure("recall.index.search", exc, lane=self.lane, chars=len(match))
            return []

    def answer_for(self, candidate: dict) -> dict | None:
        """The recorded answer to a matched question: the last non-empty assistant row before the next user row."""
        sql = ("SELECT a.content, a.ts, a.uid FROM idx_messages a"
               " WHERE a.channel = ? AND a.role = 'assistant' AND a.kind <> 'free'"
               " AND length(trim(a.content)) > 0 AND a.id > ?"
               " AND a.id < COALESCE((SELECT MIN(u.id) FROM idx_messages u"
               "                      WHERE u.channel = ? AND u.role = 'user' AND u.id > ?), 9223372036854775807)"
               " ORDER BY a.id DESC LIMIT 1")
        try:
            with self._conn() as conn:
                row = conn.execute(sql, (self.lane, int(candidate.get("id") or 0), self.lane,
                                         int(candidate.get("id") or 0))).fetchone()
        except sqlite3.Error as exc:
            trace.failure("recall.index.answer", exc, lane=self.lane)
            return None
        if not row:
            return None
        return {"content": row[0], "ts": row[1], "uid": row[2]}

    def uids(self) -> set[str]:
        """Every identity the index holds for this lane — the set the `indexed ⊆ vault` check compares."""
        try:
            with self._conn() as conn:
                return {row[0] for row in conn.execute(
                    "SELECT uid FROM idx_messages WHERE channel=?", (self.lane,))}
        except sqlite3.Error:
            return set()

    def meta(self) -> dict:
        try:
            with self._conn() as conn:
                return dict(conn.execute("SELECT key, value FROM meta").fetchall())
        except sqlite3.Error:
            return {}

    def clear(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM idx_fts")
            conn.execute("DELETE FROM idx_messages WHERE channel=?", (self.lane,))
