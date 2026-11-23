---
name: github_update
trigger: ["update athena", "update the athena", "upgrade athena", "athena update", "new athena version", "update the server", "update the architecture", "switch channel", "switch to beta", "switch to stable", "switch to alpha", "release channel", "alpha channel", "beta channel", "stable channel"]
priority: 18
---

# Updating Athena between release channels

Athena updates itself from **three branches, one per channel**. You may check and prepare freely; the apply step stops
and restarts the server, which ends the turn you are running in — so treat it as an action to hand to the operator, or
to run detached and report honestly (step 3).

## 1 · The channels are branches, and the digits decide

```text
stable   branch main    version  x.0.0   (major > 0)   what a box runs for real work
beta     branch beta    version  0.x.0   (minor > 0)   feature-complete, being watched
alpha    branch alpha   version  0.0.x                 under development, no promise
```

The channel is **derived from the version file**, never stored beside it, and a branch carrying a version of the wrong
shape is refused before anything is touched. Never edit `VERSION` to make a build claim a channel: the updater
compares the digits with the branch and stops.

## 2 · Look first — read-only, safe during a turn

```bash
./athena.sh update status                     # installed version · channel · last attempt · rollback trees
./athena.sh update channels                   # the table above, from the code itself
./athena.sh update --check                    # installed vs target; does not even contact GitHub
./athena.sh update --channel beta --dry-run   # fetch, verify, report the change — changes nothing
```

`--dry-run` is the one to reach for when asked "is there an update?": it prints the available version, its channel,
file count and hash, and the live tree keeps running untouched.

## 3 · Apply — scripted, and detached from your own turn

```bash
# a normal update on the channel already installed
setsid nohup ./athena.sh update --yes \
  >> "$HOME/.athena/logs/update-$(date +%Y%m%dT%H%M%S).log" 2>&1 &

# switching channel
setsid nohup ./athena.sh update --yes --channel beta >> "$HOME/.athena/logs/update.log" 2>&1 &
```

`--yes` removes every prompt. `setsid` matters: the updater restarts the server, and a job still attached to the
server's process group dies with it — detached, it completes the swap and the next turn sees the new build.

What `--yes` does, in order, and what you must be able to explain if asked:

1. verifies the candidate (readable `VERSION`, channel agrees with the branch),
2. backs up the live tree to `~/.athena/backups/` (zip + `.sha256` + file list, read back to prove it),
3. stops the server and waits until the port is genuinely free,
4. renames the live tree to `athena-agent.prev-<old version>` — parked, not deleted,
5. copies the new tree in,
6. starts it and waits for `/health` to answer **with the new version**,
7. on any failure: stop, remove the new tree, put the previous one back, start and re-verify it.

## 4 · Read the exit code, do not guess

| code | meaning | what you say |
|---|---|---|
| **0** | installed | the new version, from `/health`, not from hope |
| **2** | refused before touching anything | the reason verbatim (wrong channel, older build, no such branch, declined) |
| **3** | applied, then rolled back | the previous version is running again, and the log says why |
| **1** | unexpected crash | the attempt is still in `history.jsonl`; read the log |

After a **0**, prove it: `curl -s http://127.0.0.1:8765/health` must show the new `version` and its `channel`, then
`./athena.sh update status`. After a **3**, do not retry in a loop — read `~/.athena/logs/updater-serve.log` and report
the failure. A rolled-back box is a working box.

## 5 · Switching channels

`./athena.sh update --channel stable|beta|alpha`. It installs whatever that branch carries, so the target build must
already exist there; if it does not, the updater says so plainly ("A branch with no build cannot be updated from —
publish `athena-agent/` to that branch first") and changes nothing. Switching up is normal; switching **down** (to an
older version) is refused unless the operator explicitly insists with `--yes`.

## 6 · Never

- **Never push, tag, or create a branch.** Publishing is the operator's gated action; this procedure is read-only
  against GitHub.
- **Never edit `VERSION`, `updater/` code, or `state.json`** to force an outcome.
- **Never delete `athena-agent.prev-*` or the `updater-*.zip` backups** — they are the rollback.
- **Never claim an update succeeded** without `/health` answering with the new version.
- **Never start an update while a subagent is running**, or while you are mid-task: the restart kills in-flight work.
  Finish, report, then hand over or run it detached.
- **Never update to "fix" something you have not diagnosed.** A version change is not a repair.

## 7 · Where it keeps things

```text
~/.athena/backups/updater-<version>-<stamp>.zip        the tree before the swap (+ .sha256, .filelist.txt)
~/.athena/updater/state.json                           current channel, branch, version
~/.athena/updater/history.jsonl                        one line per attempt, ever
~/.athena/updater/staging/                             the downloaded build (removed after success)
~/.athena/logs/updater-serve.log                       what the restarted server printed
~/.athena/athena-agent.prev-<version>                  the previous tree, beside the live one
```

A build made on this machine can be installed without GitHub: `./athena.sh update --from /path/to/athena-agent`
(or a `.tar.gz`). The same verification, backup and rollback apply.
