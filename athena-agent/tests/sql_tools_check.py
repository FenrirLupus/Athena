"""SQL tools: a read tool that cannot write, and cannot leave the profile.

The vault is the reason this exists, so the checks are the ones that matter for it: rows come back, a write statement
is refused before it reaches SQLite, and a database outside the agent's own profile is refused outright.

The tools are reached the way the loader reaches them — through `tools/system/sql/TOOL.md` and the script it names,
imported by path. This suite once passed against `tools.sql`, a module nothing registered; code no agent can call is
not worth a green tick.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import _boot  # noqa: F401 — puts the project root on sys.path

from tools import loader
from tools.registry import ToolError

CHECKS = {}


class Ctx:
    """What a tool script's `build(ctx)` gets: the profile whose databases these are, and the athena root."""

    def __init__(self, root):
        self.profile_root = Path(root)
        self.athena_root = Path(root)
        self.cfg = {}
        self.scope = None


row = loader.descriptor_named("sql", athena_root=None)
CHECKS["package_discovered"] = row is not None and row["kind"] == "script" and row["name"] == "sql"
CHECKS["package_is_a_folder"] = bool(row and Path(row["dir"]).is_dir() and (Path(row["dir"]) / "TOOL.md").is_file())
CHECKS["script_is_by_the_package"] = bool(row and Path(row["script"]).parent.name == "scripts")
sql_tools = loader.import_script(row["script"]) if row else None

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    storage = root / "sessions" / "storage"
    storage.mkdir(parents=True)
    db = storage / "VAULT.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, ts TEXT)")
    conn.executemany("INSERT INTO messages (role, content, ts) VALUES (?,?,?)",
                     [("user", "hello there", "2026-11-01T00:00:00Z"),
                      ("assistant", "hello back", "2026-11-01T00:00:05Z")])
    conn.commit()
    conn.close()

    tools = loader.action_map(sql_tools.build, Ctx(root))


    tables = tools["tables"](db="sessions/storage/VAULT.db")
    CHECKS["tables_listed"] = "messages" in tables["tables"] and tables["tables"]["messages"]["rows"] == 2
    CHECKS["columns_listed"] = any(c["name"] == "content" for c in tables["tables"]["messages"]["columns"])

    found = tools["query"](db="sessions/storage/VAULT.db",
                                  sql="SELECT role, content FROM messages ORDER BY id")
    CHECKS["select_returns_rows"] = found["returned"] == 2 and found["rows"][0]["content"] == "hello there"

    like = tools["query"](db="sessions/storage/VAULT.db",
                                 sql="SELECT content FROM messages WHERE content LIKE '%back%'")
    CHECKS["search_works"] = like["returned"] == 1 and like["rows"][0]["content"] == "hello back"

    for statement in ("INSERT INTO messages (role, content) VALUES ('x','y')",
                      "UPDATE messages SET content='y'",
                      "DELETE FROM messages",
                      "DROP TABLE messages",
                      "ALTER TABLE messages ADD COLUMN extra TEXT"):
        try:
            tools["query"](db="sessions/storage/VAULT.db", sql=statement)
            refused = False
        except ToolError:
            refused = True
        CHECKS[f"refused:{statement.split()[0].lower()}"] = refused

    CHECKS["rows_unchanged_after_refusals"] = tools["query"](
        db="sessions/storage/VAULT.db", sql="SELECT COUNT(*) AS n FROM messages")["rows"][0]["n"] == 2

    outside = Path(td).parent / "outside.db"
    sqlite3.connect(outside).close()
    try:
        tools["tables"](db=str(outside))
        CHECKS["outside_profile_refused"] = False
    except ToolError:
        CHECKS["outside_profile_refused"] = True
    outside.unlink(missing_ok=True)

for name, ok in CHECKS.items():
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(f"\n{sum(CHECKS.values())}/{len(CHECKS)} — RESULT:", "PASS" if all(CHECKS.values()) else "FAIL")
raise SystemExit(0 if all(CHECKS.values()) else 1)
