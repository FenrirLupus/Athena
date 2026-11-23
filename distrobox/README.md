# Installing on Linux with Distrobox

## Why a box

Athena's program needs a Python virtual environment and a small system layer (Python, `sqlite3` with FTS5, `git`,
`rsync`). On a classic distribution those come from the package manager. On an **atomic or immutable** distribution
— Fedora Silverblue/Kinoite, Bazzite, openSUSE MicroOS, NixOS, SteamOS — the system layer is read-only by design,
so Athena runs in a Distrobox container instead.

`$HOME` is shared with the container, so the code, the config and the agent's own state live in the host's home
exactly as they would on a classic distribution. Nothing is duplicated and nothing outside `$HOME` is touched.

## Prerequisites

| need | check | note |
|---|---|---|
| distrobox | `distrobox --version` | any recent release |
| a container engine | `podman --version` or `docker --version` | podman is the default for rootless |
| a user session | `id -u` is not 0 | the server is a user service; running it as root is refused |
| FTS5 in the box's Python | the installer checks it | without FTS5 there is no vault search and no recall index |

## Supported images

The image is the operator's choice; the installer's default is the first row.

| image | why |
|---|---|
| `docker.io/library/ubuntu:24.04` | default: an ordinary LTS userland, and `apt` covers everything the box needs |
| `registry.fedoraproject.org/fedora:40` | for hosts already in the Fedora world; `dnf`, no extra repositories |
| `docker.io/library/archlinux:latest` | rolling; `pacman`, good when the host is Arch-based and tool versions must match |

The **box name is fixed** — `athena-agent` — because `./athena.sh box …` looks for exactly that name. The image is
not: pass `--image` to choose another.

## What it does, in order

1. checks the prerequisites and refuses as root, before writing anything;
2. creates the box if it is missing (`--nvidia` is passed automatically when `nvidia-smi` lists a card);
3. installs the system layer **inside** the box, choosing the package manager the image provides;
4. copies the program to `~/.athena/athena-agent`, preserving `profiles/`, `.secret`, `metrics/` and `sessions/`;
5. creates the venv once and reapplies `requirements.txt`;
6. proves it with `./athena.sh selftest` — the mock provider, offline, no credit;
7. prints the three commands that start her.

Every step prints what it runs. `--dry-run` prints them and writes nothing.

## Doing it by hand

The installer is a convenience; this is the same thing in five commands, if you would rather read it than run it:

```bash
distrobox create --name athena-agent --image docker.io/library/ubuntu:24.04 --nvidia --yes
distrobox enter athena-agent -- sudo apt-get update
distrobox enter athena-agent -- sudo apt-get install -y python3 python3-venv python3-pip git rsync sqlite3
rsync -a --exclude /profiles/ --exclude /.secret --exclude /sessions/ --exclude /metrics/ --exclude /.venv/ \
      ./athena-agent/ ~/.athena/athena-agent/
distrobox enter athena-agent -- bash -c 'python3 -m venv ~/.athena/.venv && ~/.athena/.venv/bin/pip install -q -r ~/.athena/athena-agent/requirements.txt'
distrobox enter athena-agent -- bash -c 'cd ~/.athena/athena-agent && ./athena.sh selftest'
```

The venv sits **beside** the code — `~/.athena/.venv` — because that is where `athena.sh` looks for it, and because a
reinstall rsyncs the code folder with `--delete`, which would otherwise remove the environment it is running from.

## Uninstall

The program is one folder; the state is another. Removing the code keeps the conversations:

```bash
rm -rf ~/.athena/athena-agent          # the program (the venv goes with it)
distrobox rm athena-agent              # the box
# left behind on purpose: ~/.athena/profiles/  ~/.athena/.secret  ~/.athena/backups/
```
