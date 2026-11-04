# Athena

A self-hosted, persistent agent architecture. One server, a set of channels with permissions, and agents that
remember what they did. Built to run on a box you own, with your own keys, and to be updated from this repository.

## Channels and branches

Athena ships on channels, and every channel is a branch:

```text
latest           the current release; the channel `stable` reads from here
stable-<version> an archived release, kept as a recovery point
beta-<version>   the beta line for a release that is being hardened
alpha-<version>  the alpha line, where work starts
```

A version states its own channel: `N.0.0` is stable, `N.x.0` is beta, `N.0.x` is alpha. The digits are the
guarantee, not a label — the updater refuses a branch that carries a version of the wrong shape.

An install always starts from `latest`. Beta and alpha features inside it are modular and disabled until switched on,
and they are labelled as experimental wherever they appear.

## Quick start

```bash
git clone https://github.com/FenrirLupus/Athena.git
cd Athena
cat VERSION
bash athena-agent/athena.sh serve      # http://127.0.0.1:8765
```

The server reads its configuration from `~/.athena/`. Provider keys are registered once and stored in `~/.athena/.secret`
(mode 0600); they are never written into this repository and never returned by an API.

## Updating

```bash
cd Athena/athena-agent
./athena.sh update status              # installed version and channel
./athena.sh update --dry-run           # what is available, changing nothing
./athena.sh update --yes               # install the update on the current channel
./athena.sh update --channel beta      # switch channel
```

An update backs up the tree with a verified hash, stops the server, swaps the tree, restarts it, and accepts the new
build only when `/health` answers with the version it just installed. If it does not, the previous tree is restored and
the box keeps running.

## What is in this repository

```text
athena-agent/    the program: server, tools, channels, prompts, updater, tests
VERSION          the release this branch carries
DOCTRINE.md      the rules this repository is maintained under
LICENSE
```

`athena-agent/tests/run_gate.sh` runs the suite that guards a release. Documentation for the architecture lives in
`~/.athena/documentation/` on an installed system.

## Licence

See `LICENSE`.
