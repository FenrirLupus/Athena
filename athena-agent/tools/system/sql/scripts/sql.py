"""sql — read a vault database, and only read it.

The vault is the agent's long-term record, so the two guarantees here are the whole tool: a database is opened with
SQLite's `mode=ro`, and a statement that could write is refused before it ever reaches SQLite. Neither is advisory —
`sql_query` accepts `SELECT`, `WITH`, `PRAGMA` and `EXPLAIN` and nothing else, and `sql_tables` lists what exists
without touching a row. Output is bounded (200 rows, 200 KB) because a vault holds every turn ever offloaded and one
careless `SELECT *` would spend the context the tool exists to protect.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from tools.registry import Tool, ToolError
from core import trace

MAX_ROWS = 200
MAX_BYTES = 200_000
DEFAULT_ROWS = 100


def _lane(ctx) -> str:
    """Which lane a trace line belongs to: the channel's name, else the profile folder the database lives in."""
    name = str(getattr(ctx, "channel", "") or "")
    root = getattr(ctx, "profile_root", None)
    return name or (Path(root).name if root else "-")

# The only statement kinds a reader needs. Everything else is a write, a maintenance operation, or an attempt to
# reach a second database.
_ALLOWED = frozenset({"SELECT", "WITH", "PRAGMA", "EXPLAIN"})
_WRITE_WORDS = frozenset({"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE", "VACUUM",
                          "REINDEX", "ATTACH", "DETACH", "TRUNCATE"})


def _words(sql: str) -> list[str]:
    """The bare words of a statement, with string literals, quoted identifiers and comments skipped.

    Skipping them matters in both directions: `WHERE content LIKE '%delete%'` must not look like a DELETE, and a
    write hidden after a comment (`/*x*/ DROP TABLE`) must still be seen.
    """
    found: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            quote_char = ch
            i += 1
            while i < n:
                if sql[i] == quote_char:
                    if i + 1 < n and sql[i + 1] == quote_char:      # '' inside a literal is an escaped quote
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "[":                                               # [bracketed identifier]
            close = sql.find("]", i + 1)
            i = n if close < 0 else close + 1
            continue
        if sql.startswith("--", i):
            newline = sql.find("\n", i + 2)
            i = n if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", i):
            close = sql.find("*/", i + 2)
            i = n if close < 0 else close + 2
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_$"):
                j += 1
            found.append(sql[i:j].upper())
            i = j
            continue
        i += 1
    return found


def _checked(sql: str) -> str:
    """Return the statement, or refuse it. The refusal happens here, before a connection is opened."""
    statement = (sql or "").strip()
    if not statement:
        raise ToolError("sql is required — pass one SELECT, WITH, PRAGMA or EXPLAIN statement")
    words = _words(statement)
    if not words:
        raise ToolError("sql contains no statement — pass one SELECT, WITH, PRAGMA or EXPLAIN statement")
    if words[0] not in _ALLOWED:
        raise ToolError(f"refused: {words[0]} is not a read statement. This tool only accepts SELECT, WITH, "
                        f"PRAGMA or EXPLAIN; the vault is never written to.")
    offending = sorted({w for w in words if w in _WRITE_WORDS})
    if offending:
        raise ToolError(f"refused: {offending[0]} appears in this statement. This tool only accepts SELECT, WITH, "
                        f"PRAGMA or EXPLAIN; the vault is never written to.")
    if statement.rstrip(";").strip().count(";") > 0:
        raise ToolError("refused: one statement per call — this tool does not run batches")
    return statement


def _ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


class _Reader:
    """One resolved database, plus the profile it is allowed to live in."""

    def __init__(self, base: Path | None):
        self.base = Path(base).resolve() if base else None

    def path(self, db: str | Path) -> Path:
        raw = str(db or "").strip()
        if not raw:
            raise ToolError("db is required — pass a path such as 'sessions/storage/VAULT.db'")
        if self.base is None:
            raise ToolError("no profile is bound to this agent, so no database can be located")
        candidate = Path(raw)
        target = (candidate if candidate.is_absolute() else self.base / candidate).resolve(strict=False)
        if target != self.base and self.base not in target.parents:
            raise ToolError(f"refused: {raw!r} is outside this agent's profile ({self.base}). A database may only "
                            f"be read from inside the profile that owns it.")
        if not target.is_file():
            raise ToolError(f"no database at {raw!r} — check the path; tier files live under sessions/storage/")
        return target

    def label(self, target: Path) -> str:
        try:
            return str(target.relative_to(self.base)) if self.base else str(target)
        except ValueError:
            return str(target)

    def connect(self, target: Path) -> sqlite3.Connection:
        # `?mode=ro` is the guarantee, not a convention: SQLite itself refuses a write on this handle.
        uri = f"file:{quote(str(target))}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        except sqlite3.Error as exc:
            raise ToolError(f"cannot open {self.label(target)} read-only: {exc}") from exc
        conn.row_factory = sqlite3.Row
        return conn


def _row_limit(limit) -> int:
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        wanted = DEFAULT_ROWS
    return max(1, min(wanted, MAX_ROWS))


def build(ctx=None) -> list[Tool]:
    """The two SQL tools, bound to one profile.

    `ctx` supplies the tree a database must live inside. No profile is ever wider than one agent: with no context
    the athena root is the boundary, never the whole disk.
    """
    from tools.loader import agent_roots

    profile_root, athena_root = agent_roots(ctx)
    reader = _Reader(profile_root if profile_root else athena_root)


    def sql_tables(db: str) -> dict:
        with trace.span("tool.sql.tables", channel=_lane(ctx), db=db) as sp:
            target = reader.path(db)
            conn = reader.connect(target)
            try:
                names = conn.execute(
                    "SELECT name, type FROM sqlite_master "
                    "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
                tables: dict = {}
                total = 0
                truncated = False
                for row in names:
                    if len(tables) >= MAX_ROWS:
                        truncated = True
                        break
                    name, kind = row["name"], row["type"]
                    columns = [{"name": c["name"], "type": c["type"] or "", "notnull": bool(c["notnull"]),
                                "pk": bool(c["pk"]), "default": c["dflt_value"]}
                               for c in conn.execute(
                                   "SELECT name, type, \"notnull\", pk, dflt_value FROM pragma_table_info(?)",
                                   (name,)).fetchall()]
                    try:
                        count = conn.execute(f"SELECT COUNT(*) FROM {_ident(name)}").fetchone()[0]
                    except sqlite3.Error:
                        count = None
                    entry = {"type": kind, "rows": count, "columns": columns}
                    cost = len(json.dumps(entry, ensure_ascii=False, default=str).encode("utf-8"))
                    if total + cost > MAX_BYTES:
                        truncated = True
                        break
                    tables[name] = entry
                    total += cost
            except sqlite3.Error as exc:
                raise ToolError(f"cannot read the schema of {reader.label(target)}: {exc}") from exc
            finally:
                conn.close()
            sp.ok(tables=len(tables), truncated=truncated, db=reader.label(target))
            return {"db": reader.label(target), "tables": tables, "count": len(tables), "truncated": truncated,
                    "advice": "Read-only. Pick the narrowest table and the columns you actually need."}

    def sql_query(db: str, sql: str, limit: int = DEFAULT_ROWS) -> dict:
        # the statement text stays out of the trail: it is the caller's data. Its length distinguishes one query
        # from another without copying a vault's contents into a log
        with trace.span("tool.sql.query", channel=_lane(ctx), db=db, sql_chars=len(sql or ""),
                        limit=_row_limit(limit)) as sp:
            statement = _checked(sql)
            wanted = _row_limit(limit)
            target = reader.path(db)
            conn = reader.connect(target)
            try:
                try:
                    cursor = conn.execute(statement)
                    description = cursor.description or []
                    raw = cursor.fetchmany(wanted + 1)
                except sqlite3.Error as exc:
                    raise ToolError(f"query failed: {exc}") from exc
                if not description:
                    raise ToolError("statement returned no result set — this tool is for reading, not for changes")
                columns = [d[0] for d in description]
                truncated = len(raw) > wanted
                rows: list[dict] = []
                spent = 0
                for record in raw[:wanted]:
                    row = {name: record[index] for index, name in enumerate(columns)}
                    cost = len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8")) + 1
                    if rows and spent + cost > MAX_BYTES:
                        truncated = True
                        break
                    rows.append(row)
                    spent += cost
            finally:
                conn.close()
            sp.ok(rows=len(rows), truncated=truncated, db=reader.label(target))
            return {"db": reader.label(target), "columns": columns, "rows": rows, "returned": len(rows),
                    "truncated": truncated, "max_rows": MAX_ROWS, "max_bytes": MAX_BYTES,
                    "advice": "Read-only. Rows are capped at 200 and 200 KB; narrow the query rather than paging."}

    return [
        Tool("tables",
             "List the tables (and views) in a SQLite database inside this profile, with columns and row counts.",
             {"type": "object",
              "properties": {"db": {"type": "string",
                                    "description": "Database path, relative to the profile (e.g. "
                                                   "sessions/storage/VAULT.db)."}},
              "required": ["db"]},
             sql_tables),
        Tool("query",
             "Run one read-only SQL statement (SELECT, WITH, PRAGMA or EXPLAIN) against a database in this profile.",
             {"type": "object",
              "properties": {"db": {"type": "string",
                                    "description": "Database path, relative to the profile."},
                             "sql": {"type": "string",
                                     "description": "One SELECT, WITH, PRAGMA or EXPLAIN statement."},
                             "limit": {"type": "integer",
                                       "description": f"Rows to return (default {DEFAULT_ROWS}, max {MAX_ROWS})."}},
              "required": ["db", "sql"]},
             sql_query),
    ]


def run(args=None, ctx=None):
    """The entry point the loader calls: `action` selects a verb, the rest are that verb's arguments."""
    from tools.loader import dispatch

    return dispatch(build, args, ctx, "sql")
