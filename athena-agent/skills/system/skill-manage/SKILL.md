---
name: skill-manage
category: system
description: Use when creating, changing or removing a skill — the instructions that tell an agent how to do something.
---

# Skill management

A skill is a folder holding `SKILL.md`: frontmatter plus instructions. It teaches *how* to do something; it does not
run anything. Tools run; skills explain.

```text
<profile>/skills/<category>/<name>/SKILL.md    the one you write — this agent only
<athena>/skills/<category>/<name>/SKILL.md     shared, read-only: readable and usable, never editable
```

Where you may write: **your own profile**. `<profile>/skills/`, `<profile>/tools/`, `<profile>/plugins/`.
The shared copies under `<athena>/` are **read-only** for you — you can read and use them, never change them.


Categories are folders (`system/`, or any name you like). A skill may also state `category:` in its frontmatter; the
frontmatter wins.

## Create

```markdown
---
name: pdf-receipts
category: finance
description: Use when a receipt arrives as a PDF and the amounts must be extracted.
---

# PDF receipts

Step one… step two… what to check before answering.
```

```text
mkdir -p <profile>/skills/finance/pdf-receipts
$EDITOR <profile>/skills/finance/pdf-receipts/SKILL.md
```

`description` is the only part an agent sees before loading the skill, so it must say **when** to use it, not what it
contains: "Use when …". `name` must be unique across the folders an agent can see — the profile copy wins on a clash,
silently.

## Modify

Edit the file. Nothing caches it: discovery reads `SKILL.md` on each turn, so a change is live on the next one.

Keep the frontmatter valid — a file whose header does not parse is skipped without an error, which looks exactly like
a skill that was never installed.

## Remove

```text
rm -rf <profile>/skills/finance/pdf-receipts
```

Deleting your copy removes it for this agent only — a shared skill of the same name reappears underneath it. You cannot
delete a system skill; you can shadow it with your own, or simply not use it.

## Check your work

There is no `athena skills` command. Verify on the filesystem:

```text
ls <profile>/skills/<category>/<name>/SKILL.md      the file is there
head -5 <profile>/skills/<category>/<name>/SKILL.md the header parses: `---`, name, description, `---`
```

A skill whose header does not parse is skipped **silently** — no warning, no error, it is simply not offered to the
agent. That is why the header is the first thing to check when a skill you wrote appears not to exist.
