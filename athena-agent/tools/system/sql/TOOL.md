---
name: sql
description: Read-only SQL against a SQLite database the agent owns: SELECT, WITH, PRAGMA and EXPLAIN only.
kind: script
script: scripts/sql.py
actions: tables, query
parameters: '{"type": "object", "properties": {"action": {"type": "string", "enum": ["tables", "query"], "description": "The verb: List the tables (and views) in a SQLite database inside this profile, with columns and row counts; Run one read-only SQL statement (SELECT, WITH, PRAGMA or EXPLAIN) against a database in this profile."}, "db": {"type": "string", "description": "Database path, relative to the profile (e.g. sessions/storage/VAULT.db). Required for action: query, tables."}, "sql": {"type": "string", "description": "One SELECT, WITH, PRAGMA or EXPLAIN statement. Required for action: query."}, "limit": {"type": "integer", "description": "Rows to return (default 100, max 200)."}}, "required": ["action"]}'
timeout_s: 60
---

# sql

Read-only SQL against a SQLite database the agent owns — the vault is the reason it exists.

| tool | does |
|---|---|
| `sql_tables` | tables, their columns, and a row count for each |
| `sql_query` | one `SELECT`, `WITH`, `PRAGMA` or `EXPLAIN`, capped at 200 rows |

## What it will not do

- Write. The connection is opened `mode=ro` and a statement carrying a write keyword (outside a string literal) is
  refused before it reaches SQLite — two independent walls, so a bug in one is still a refusal.
- Leave the profile. `db` resolves inside the calling agent's own tree; `../../.secret` is not a database.
