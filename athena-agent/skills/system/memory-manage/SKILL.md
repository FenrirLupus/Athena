---
name: memory-manage
category: system
description: Use when a durable fact about the person, a decision or a correction is worth keeping. Writes this agent's own memory index, which is read on every turn.
---

# Memory

Your index is `<your profile>/memories/MEMORY.md`. It is read **on every turn** and injected into the prompt, so each
row costs tokens forever, in every conversation. That one fact drives every rule here: memory is for what stays true,
not for what happened today.

Three tools manage it. Never edit the file by hand — the tools write atomically and dedupe for you.

| tool | arguments | does |
|---|---|---|
| `memory(action="save")` | `text`, `tag` | appends one dated row; refuses a duplicate fact |
| `memory(action="search")` | `query`, `limit` | rows containing `query`; newest rows when `query` is empty |
| `memory(action="edit")` | `match`, `text`, `tag` | rewrites **one** row in place, keeping its date |
| `memory(action="forget")` | `match` | removes **every** row containing `match` |

## How memory works

```text
- YYYY-MM-DD [tag] the fact, in one sentence
```

- **Dated** by the tool — you never type the date.
- **Tagged** with why it earns its tokens: `person`, `preference`, `decision`, `correction`, `fact`.
- **One line.** A row that needs a paragraph is a task note, not a memory.
- **Capped** at 200 rows and 24,000 bytes. Past that the **oldest** rows are pruned first, so a fact you keep
  re-saving survives and a fact you never repeat is the first to go.
- **Deduped on the fact.** Saving the same text again — different tag, later day — is refused and changes nothing:
  `{"saved": false, "reason": "already remembered"}`.
- **Already in your prompt.** You do not load memory. It arrives with every turn; use `memory(action="search")` when the index
  is long and you want the rows about one subject.

## Creating a memory

```text
memory(action="save", text="Fenrir prefers plain text over markdown in the terminal", tag="preference")
→ {"saved": true, "rows": 12, "row": "- YYYY-MM-DD [preference] Fenrir prefers plain text…"}
```

One fact per call. Three things to remember is three calls — each is separately prunable, forgettable and searchable.

Make the sentence stand alone: `"prefers plain text"` is useless in six weeks, `"Fenrir prefers plain text over
markdown in the terminal"` still reads correctly with nothing around it.

## Removing a memory

```text
memory(action="search", query="plain text")    ← see exactly what the phrase matches first
memory(action="forget", match="plain text")    ← then remove it
→ {"removed": 2, "rows": 10}
```

`match` is a case-insensitive substring and removes **every** row containing it, so search first — a short phrase can
take more than you meant. Use it for a fact that is no longer true, a row you wrote by mistake, or something the
person asked you to drop.

## Modifying a memory

```text
memory(action="edit", match="prefers markdown",
            text="Fenrir prefers plain text over markdown in the terminal",
            tag="preference")
→ {"updated": 1, "row": "- YYYY-MM-DD [preference] Fenrir prefers plain text…", "rows": 12}
```

`match` finds the row; `text` replaces it; `tag` may change it and an empty tag keeps the old one. The **date is kept**
— an edit is the same fact corrected, not a new fact learned today.

It refuses to guess. No match returns `{"updated": 0, "reason": "no row contains that phrase"}`; more than one match
returns `{"updated": 0, "reason": "more than one row matches; narrow the phrase"}` with the candidates, so you can
quote the exact wording and try again. Narrow the phrase rather than deleting both.

If `memory(action="edit")` is unavailable, the two-step equivalent is forget-then-save, in that order — the reverse can leave
two versions and the dedupe will refuse the corrected text as "already remembered" while the old wording stays.

When the change is a correction — something you got wrong and the person fixed — keep the lesson explicitly:

```text
memory(action="save", text="Athena's live tree is .athena/athena-agent, not the repo clone", tag="correction")
```

## What belongs here

| tag | use for |
|---|---|
| `person` | who the operator is — name, role, how they want to be addressed |
| `preference` | a standing choice: output style, tools they like or refuse, working hours |
| `decision` | something settled that would otherwise be re-litigated |
| `correction` | a mistake that was fixed — the most valuable rows, because they stop it recurring |
| `fact` | a stable detail about the box, the projects, the environment |

## What never belongs here

- **Task progress** — "started slice 2", "waiting on CI". Session state, and it reads as current fact next week.
- **What the repository already records** — code, docs, changelog. The row goes stale when the file changes, and you
  will believe the row.
- **Credentials, keys, tokens, `.secret` paths.** A key in memory is a key in every future prompt.
- **Speculation about the person.** "They probably want X" is not a fact and will be treated as one later.
- **Self-assessment.** "I did that well" displaces a fact and tells nobody anything.

## Candidates are not memories

When compression evicts old turns it appends possible keepers to `memories/CANDIDATES.md` — turns that might hold
something worth keeping. Nothing is promoted automatically.

```text
read memories/CANDIDATES.md
memory(action="save", …) for the two or three lines still true
delete the rest — candidates have no cap of their own
```

Promotion is your decision. An index that fills itself is a transcript with a different filename.
