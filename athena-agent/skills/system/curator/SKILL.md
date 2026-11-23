---
name: curator
category: system
description: Use when evaluating the system's own plugins, tools and skills — finding what is dead, duplicated, over-complicated or slow, and improving it without losing a feature.
---

# Curator

The job is to leave fewer moving parts doing the same work, with proof. Not "looks cleaner" — **measured**, and with a
gate that would fail if a feature disappeared.

Scope: **plugins, tools, skills and the agents themselves** — their manifests, code, SKILL.md files and
per-agent configuration. **Not the program tree**: `athena-agent/` is read-only and outside curator scope, so
an agent never proposes editing the code it runs on. Not the operator's decisions, and not anything you
cannot verify.

## The tools

Six scripts exist, in the program tree at
`<athena>/athena-agent/skills/system/curator/scripts/` — **not** beside this file. There is no `scripts/`
in this skill's own folder (that mirror was deleted deliberately), so a relative path fails. Run them by that absolute
path from any working directory; each resolves the tree and the Athena home from its own location, never from `cwd`.

| script | does | cost |
|---|---|---|
| `scan.py` | duplicate blocks ≥ 90 % similar, capability inventory, largest files → dated `.md` + `.json` in `documentation/optimization/curator/` | free |
| `replay.py` | what the model was told before vs after a change, from metrics rows (`--lane`, `--before`, `--after`) | free |
| `draft.py` | drafting prompts for the newest findings **plus a price estimate** (`--limit`, `--rate`) — writes them, sends nothing | free, stops before spending |
| `propose.py` | `PROPOSAL.md` + `diff.patch` + `content.new` + `content.old` + `verify.json` under `documentation/optimization/proposals/<slug>-<stamp>/`; touches no target | free |
| `apply.py` | hash check → content check → backup freshness → atomic write → gate → revert on failure | free, but it writes |
| `curator_diff.py` | the single unified-diff implementation `propose.py` and `apply.py` import; never run directly | — |

```text
CURATOR=<athena>/athena-agent/skills/system/curator/scripts

python "$CURATOR/scan.py"                                    # takes no arguments; --help just runs the scan
python "$CURATOR/replay.py" --lane default --before <ISO> --after <ISO>   # both windows must be non-empty
python "$CURATOR/draft.py" --limit 3
python "$CURATOR/propose.py" --title "…" --file <path> --new <new-file> --rationale "…"
python "$CURATOR/apply.py" <proposal-folder> --dry-run        # read the diff, then repeat with --yes
```

Prove a find before proposing on it: `scan.py` for the measurement, `replay.py` for the before/after cost, `draft.py`
when the proposal needs prose — it prices the prompt and stops. Nothing here imports a provider, `urllib`, `requests`
or any model client, so no step can spend tokens, and only `apply.py` changes a target: everything before it leaves text you
can read and throw away.

**Where you may write.** Your own profile, `~/.athena/profiles/<lane>/` — its `skills/`, `tools/`, `config.yaml`.
That is the scope the scan measures and the only place your edits belong. `athena-agent/` and the shared
`~/.athena/{plugins,tools,skills}/` are `ATHENA_ROOT`: **read-only to agents** — read them, propose against them,
never edit them. The scripts write only `documentation/optimization/curator/` and
`documentation/optimization/proposals/`.

Nightly: `~/.config/systemd/user/athena-curator.{service,timer}` runs the scan at 03:30. The timer is **installed and disabled** until the operator enables it (`systemctl --user enable --now athena-curator.timer`). The unit has no network and no model call.

## 1 Inventory before opinion

```text
list every capability          plugins/<name>/  tools/<name>/  skills/<category>/<name>/
read each one                  its manifest, its code, its SKILL.md
count what uses it             references in code, in other skills, in config, in tests
```

Two numbers decide most findings: **how many callers** a thing has, and **how many copies** of a pattern exist. Write
them down before proposing anything.

## 2 What "dead" actually means

A grep with no hits is **not** proof. These reach code without naming it:

| mechanism | example |
|---|---|
| route decorators | a handler whose only caller is an HTTP URL |
| CLI verb tables | a subcommand reached by an argv string |
| plugin entry points | `register(registry, ctx)` via `getattr` |
| manifest dispatch | `kind: script|command|http` choosing a runner |
| config-driven names | a dotted key read once, far from its definition |
| string-built names | `"notif-" + kind` producing a class or id |
| re-exports | a name imported *from* a module that never uses it |

Rule a name dead only when none of those paths can reach it, and cite the checks you ran. For a re-export, ask what
imports it *from* this module before deleting — that has already broken a test suite once.

## 3 Duplication: count, then decide

Merge at **three or more** occurrences, or when two things are **90 % or more similar** by block comparison. Below three, a copy is often clearer than a helper.

Before merging, list what differs between the copies — and if the answer is "indentation and variable names", merge.
If they differ in *contract*, they are not duplicates:

```text
atomic writes    one wrote 0600 secrets and fsynced the directory, another used mkstemp for concurrent
                 writers, a third swapped a directory. One helper with five flags loosened the secret file
                 to 0644 and truncated a short write. Three named functions, not one with knobs.
```

A helper nobody adopts is dead weight. Adopt it at the same moment you write it, or do not write it.

## 4 Simplifying and speeding up

Measure, change, measure again. Both numbers, quoted:

```text
before   prompt 67,000 tokens, 1.58 s per turn
after    prompt  4,300 tokens, 1.02 s per turn
```

A speed-up you cannot show is a preference. And check the change does not buy speed with quality: a smaller prompt is
only free until it drops the instruction that made the answer right.

## 5 Removing safely

```text
1  backup first          the code and the docs, not the runtime state
2  one deletion per step  small enough that a failure is obviously yours
3  re-run the gate        every step, not at the end
4  keep the reason        in the commit, in the changelog, in the report
```

Leave alone, and say why: anything with an approval path, anything reached only at install time, and anything whose
absence would be silent — a security module deleted here once made the server fall back to keeping keys in-process,
with no test and no import to notice.

## 6 Report

Per finding, one line each:

```text
SEV (blocker/major/nit) | file:line | what is wrong | why it matters
```

Then the three buckets, never blended:

- **verified** — a command ran, its output quoted
- **unverified** — written, parses, never exercised
- **not built** — named, with the one missing call

A summary that says "done" over a mix of those three is the report failing at its only job.

## 7 Where judgement is not yours

Deleting an endpoint is removing API surface. Deleting a documented capability is a product decision. Merging two
things that differ in contract is a redesign. Recommend, show the evidence, and let the operator choose — those are
not cleanup, and treating them as cleanup is how a tidy-up removes something someone was using.
