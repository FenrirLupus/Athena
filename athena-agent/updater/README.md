# Athena updater

One command, three channels, three branches, and a swap that can be undone. `./athena.sh update`.

## The three channels are three branches

| channel | branch | version shape | who runs it |
|---|---|---|---|
| **stable** | `main` | `x.0.0` (major > 0) | the box, for real work |
| **beta** | `beta` | `0.x.0` (minor > 0) | feature-complete for the cycle, being watched |
| **alpha** | `alpha` | `0.0.x` | under active development, no promise |

`main` is the default branch on purpose: it is what a clone lands on and what a release tag is cut from. **The branch
says which channel it carries, and the `VERSION` inside must agree with it.** A `beta` branch holding `1.2.0` is a beta
branch carrying a stable build; the updater refuses that pair rather than installing a box that reports a channel it is
not running:

```text
refused: branch 'main' carries stable builds, but this tree is 0.0.2 (alpha).
A stable release needs the x.0.0 digit bumped — the branch and the number must agree, …
```

## Commands

```bash
./athena.sh update                      # wizard, channel = the one installed today
./athena.sh update --channel beta       # switch channel: installs whatever the beta branch carries
./athena.sh update --yes                # fully scripted: no prompts, same steps
./athena.sh update --dry-run            # fetch and report; change nothing
./athena.sh update --check              # report only; does not even fetch from GitHub
./athena.sh update --from /path/build   # install a build you made, instead of a branch
./athena.sh update --from ./athena-0.0.2.tar.gz
./athena.sh update status               # installed version/channel, last attempt, rollback trees
./athena.sh update channels             # the table above, from the code
./athena.sh update --json               # machine-readable result
```

Exit codes, because a script has to tell "nothing happened" from "we undid it":

| code | meaning |
|---|---|
| 0 | installed (or a dry run / a check with nothing to do) |
| 2 | refused **before touching anything** (branch/version mismatch, older build, no such branch, declined) |
| 3 | applied, then **rolled back** because the new build did not answer `/health` |
| 1 | an unexpected crash (the attempt is still recorded) |

## The seven steps

```text
1  where the build comes from     github:FenrirLupus/Athena@alpha   (or a path / a tarball URL)
2  what is installed              0.0.1 · alpha   (+ "channel switch: alpha → beta" when it is one)
3  what is available              0.0.2 · alpha · 166 files · sha256 …
4  the change                     backup, rollback tree, restart, /health must answer the new version
5  confirm                        y/N  (skipped with --yes)
6  apply                          backup → stop → park → swap → start → health
7  result                         installed, with the rollback command printed
```

## What happens during step 6, in order

1. **verify** — the candidate's `VERSION` is readable and its channel matches the branch.
2. **backup** — the live tree becomes `backups/updater-<version>-<stamp>.zip` with a `.sha256` sidecar and a
   `.filelist.txt`. The zip is read back (`testzip`) before anything else happens.
3. **stop** — `athena.sh stop` from inside the tree, then wait until the port is *actually* free (40 s, then refuse).
4. **park** — `athena-agent` is renamed to `athena-agent.prev-<old version>`. A rename, never a delete.
5. **swap** — the staged build is copied into place.
6. **start + health** — the new tree starts and `/health` must answer with the version just installed, not merely
   answer.
7. **rollback on any failure** — stop, remove the new tree, rename the previous one back, start it, verify it, and raise
   with `rolled_back: True` (exit 3). The backup zip exists for the case where even the parked tree is unusable.

Every attempt — success, refusal after step 3, rollback — is appended to `<root>/updater/history.jsonl`.

## Where state lives (and why not in here)

`updater/` is *code*, and code is what an update replaces. Nothing durable is written inside it:

```text
<root>/backups/                     updater-<version>-<stamp>.zip + .sha256 + .filelist.txt
<root>/updater/state.json           current channel, branch, version, last outcome
<root>/updater/history.jsonl        one line per attempt, ever
<root>/updater/staging/<chan>-<ts>/ the downloaded build (removed after success; --keep-staging keeps it)
<root>/logs/updater-serve.log       what the restarted server printed
<root>/athena-agent.prev-<version>  the previous tree, kept beside the new one
```

## Cutting a release on a channel

The updater only reads. Publishing is a separate, gated action:

```bash
# on the branch that carries the channel you are releasing
echo 0.0.2 > athena-agent/VERSION        # bump the digit that matches the channel
bash tests/run_gate.sh                   # 25 suites
./athena.sh selftest
git add -A && git commit -m "0.0.2"
git push origin alpha                    # main = stable only, beta = beta only, alpha = alpha only
```

A box on `alpha` then runs `./athena.sh update` and gets it. A box moving up runs
`./athena.sh update --channel beta` once a `0.x.0` exists on `beta`.

## Deliberate refusals

- **Moving backwards.** An older version than the installed one is refused unless `--yes` is passed explicitly.
- **A branch with no build.** 404 from `codeload` becomes *"A branch with no build cannot be updated from — publish
  `athena-agent/` to that branch first."* — not a stack trace.
- **An archive that writes outside its own folder**, or one with no `athena-agent/VERSION` in it.
- **Swapping while the server still holds the port.** The updater waits, then refuses; it never swaps under a live
  process and hopes.
- **A build whose `/health` answers the wrong version** — answering at all is not the test; answering *as the new
  version* is.
