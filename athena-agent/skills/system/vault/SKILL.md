---
name: vault
category: system
description: Use when you need to recall something that is no longer in your live context — the vault holds every turn ever offloaded, in tiers by age. Read it; never write it.
---

# The vault

Every turn that leaves your live window is copied into the vault **before** the session file is truncated. It is the
long-term record of everything you and the person have said, and it is the only place that memory survives when the
context window drops it.

## You read it. You never write it

The vault is written by the session layer and maintained by a sweep. There is no agent operation that modifies it, and
there must never be one:

- **no writing** — not a new row, not an edit, not a "fix" to a timestamp. A vault you can write is a vault whose
  evidence you can manufacture.
- **no deleting** — not even rows that look wrong. Say what you found; the operator decides.
- **no schema changes, no VACUUM, no reindexing** — those are maintenance, and maintenance is the sweep's job.
- **a read-only handle is the only handle.** Open with SQLite's `mode=ro` (`file:…?mode=ro&immutable=1` for a sealed
  year) so a mistake fails instead of damaging the record.

If a read fails, say so plainly rather than working around it. A workaround that writes is worse than no answer.

## What is on disk

```text
<profile>/sessions/storage/
  VAULT.db                 the master — every row ever offloaded, appended, never pruned
  vault-weekly.db          copies of rows aged  ≤  7 days
  vault-monthly.db         copies of rows aged  ≤ 30 days
  vault-quarterly.db       copies of rows aged  ≤ 90 days
  vault-annual.db          copies of rows aged  ≤ 365 days
  vault-year-2025.db       one sealed file per completed calendar year (+ .manifest.json), read-only
```

Each tier is a **rolling window**, not a bucket: a two-day-old row is in weekly, monthly, quarterly *and* annual at the
same time. The master holds everything, so a row missing from a tier is still in the master. A year file is decided by
**calendar year**, not by age — a row from last November was sealed into `vault-year-2025.db` even though it is only a
few months old.

Every row is one entry, and the columns are the entry:

```text
id · channel · snapshot · role · content · ts · tool_call_id · name · created · uid
```

`uid` is the identity — `sha256(channel | ts | role | content)`. The same message appearing in three files is one
message; dedupe on `uid` before you report anything, or you will tell the person they said something three times.

`ts` is when the message happened (UTC, `Z`); `created` is when the vault saw it. Age and year are decided on `ts` when
it exists, falling back to `created`. Use the same rule or your reasoning will disagree with the files.

## How to look something up

**Newest first, and stop when you have it.** The tiers overlap, so starting at the widest file wastes the read:

```text
1  vault-weekly.db      the last 7 days — the answer is here most of the time
2  vault-monthly.db     ≤ 30 days
3  vault-quarterly.db   ≤ 90 days
4  vault-annual.db      ≤ 365 days
5  vault-year-<YYYY>.db the closed year the date falls in, when the tiers do not reach it
```

Search by content (`content LIKE '%phrase%'`), by time (`ts BETWEEN …`), by role, or by `snapshot` to see which
offloaded session a turn came from. Prefer a narrow `LIMIT` and widen only when the first pass finds nothing.

Report what you found with its `ts` and which file it came from — a recollection without a timestamp is a guess wearing
a citation.

## What the vault is not

- **Not your memory index.** `memories/MEMORY.md` is the curated, dated, capped list you write with `memory(action="save")`.
  The vault is raw history; memory is what you decided mattered. Recall from the vault, remember with memory.
- **Not writable by you**, so it is not a scratchpad.
- **Not complete by itself.** It holds what was *offloaded*, so very recent turns may still be only in the live session.

## Reading it

`read` on a `.db` returns bytes, not rows — use the SQL tools, which open every database **read-only** (`mode=ro`) and
refuse anything that is not a `SELECT`, `WITH`, `PRAGMA` or `EXPLAIN`:

```text
sql(action="tables", db="sessions/storage/VAULT.db")          what exists, with row counts
sql(action="query", db="sessions/storage/vault-weekly.db",
          sql="SELECT role, content, ts FROM messages WHERE content LIKE '%phrase%' ORDER BY ts DESC LIMIT 20")
```

Newest first, and stop when you have it: weekly → monthly → quarterly → annual → the year file the date falls in.
Dedupe on `uid` before you report anything — the tiers overlap, so the same row can come back more than once. A write
keyword is refused by the tool itself, so there is nothing to work around and nothing to attempt.
