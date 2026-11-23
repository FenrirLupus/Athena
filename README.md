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

Linux, any distribution, through Distrobox:

```bash
git clone https://github.com/FenrirLupus/Athena && cd Athena
./install.sh --list
./install.sh --install
```

[distrobox/README.md](distrobox/README.md) explains what is written and what is deliberately left alone — the
agent's own profiles, keys, transcripts and vault are never touched by an install.

## Run

```bash
cd ~/.athena/athena-agent
./athena.sh box serve      # http://127.0.0.1:8765
./athena.sh lane           # can this lane start? config · model · key · window
./athena.sh selftest       # the offline check, mock provider, no credit
```

## Requirements

Linux with a Python 3.11+ virtual environment, SQLite **with FTS5** (the vault and the recall index use it), and
`git`/`rsync` for an install. A provider key goes in `~/.athena/.secret`, one line, mode 0600 — never in config.

## What is deliberately not in this repository

The design record — the architecture documents, the proposals and their reviews — is maintained outside this tree
and is not published. Code comments that cite a `documentation/…` path refer to that record, not to a file here. One
tracing note does ship, at `athena-agent/documentation/optimization/`, because it documents measurements the code
makes rather than decisions about it.
