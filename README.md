# Athena

A self-hosted agent that keeps every agent resident and reasons on demand.

Athena is a server plus a set of agent lanes: each lane has its own thread, its own memory, its own skills and its
own budget, and stays alive between conversations. The model is called when a turn needs judgement — and, when the
same question has been answered before, it is not called at all: the answer is quoted from the lane's own record,
with its source row and its age attached.

## Layout

```
athena-agent/      the program. This is what runs — nothing else in this repository is loaded at runtime.
distrobox/         the Linux installer: creates the box, installs the system layer, copies the program in
install.sh         the front door: --list, --install, --platform, --prefix, --dry-run
```

Two folders, one job each: the program, and the way to put it on a machine. A second platform is a second folder
with an `install.sh` in it — `install.sh` finds them by looking.

## Install

Linux, any distribution, through Distrobox — one line, or from a clone:

```bash
# one shot: fetches the branch, reattaches your terminal for its one question, installs
curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena/alpha-0.0.4/install.sh | bash

# unattended (no question, for a script or a fresh box)
curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena/alpha-0.0.4/install.sh | bash -s -- --yes

# what would it do here, and where?
curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena/alpha-0.0.4/install.sh | bash -s -- --list
curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena/alpha-0.0.4/install.sh | bash -s -- --install --dry-run

# or from a clone
git clone https://github.com/FenrirLupus/Athena && cd Athena && ./install.sh --install
```

`--list` says what this machine can install and what each platform needs; `--install` detects it, shows the plan,
asks once, then runs `<platform>/install.sh`. `--prefix DIR` chooses the install root, `--ref BRANCH` a channel other
than `alpha-0.0.4`, `--dry-run` prints the commands without writing anything.

**Windows is not published yet.** When it is, it is the same shape with the platform's own entry point —
`install.bat` (or `install.ps1`) at the root and `windows/` beside it — because the dispatcher already treats a
platform as a folder with an installer in it, and only the entry point differs per operating system.

[distrobox/README.md](distrobox/README.md) explains what is written and what is deliberately left alone — the
agent's own profiles, keys, transcripts and vault are never touched by an install.

## Run

```bash
cd ~/.athena/athena-agent
./athena.sh box serve      # http://127.0.0.1:8765
./athena.sh box lane       # can this lane start? config · model · key · window
./athena.sh box selftest   # the offline check, mock provider, no credit
```

Run her **through the box** (`box`). The virtual environment is built with the box's Python, so its interpreter
belongs to the box — invoking it from the host picks the host's `python3`, which is a different version and has none
of the requirements. `./athena.sh box <command>` is the supported form on a Distrobox install.

## Requirements

Linux with a Python 3.11+ virtual environment, SQLite **with FTS5** (the vault and the recall index use it), and
`git`/`rsync` for an install. A provider key goes in `~/.athena/.secret`, one line, mode 0600 — never in config.

## What is deliberately not in this repository

The design record — the architecture documents, the proposals and their reviews — is maintained outside this tree
and is not published. Code comments that cite a `documentation/…` path refer to that record, not to a file here.
