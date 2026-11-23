#!/usr/bin/env bash
# Install Athena on Linux through a Distrobox container.
#
# Why a box: the program needs a Python venv and a few libraries, and on an atomic or immutable distribution
# (Fedora Silverblue, Bazzite, openSUSE MicroOS, NixOS) the system layer is read-only by design. The box carries
# the system layer; $HOME is shared, so the code, the config and the agent's own state live in the host's home
# exactly as they would on a classic distribution.
#
#   bash distrobox/install.sh [--prefix DIR] [--image IMG] [--name BOX] [--dry-run] [--yes]
#
# Exit codes: 0 installed · 1 refused (prerequisite missing) · 2 usage.
set -euo pipefail

NAME="athena-agent"
IMAGE="docker.io/library/ubuntu:24.04"
PREFIX="${HOME}/.athena"
DRY=0
YES=0
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO}/athena-agent"
TARGET="${PREFIX}/athena-agent"

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
have() { command -v "$1" >/dev/null 2>&1; }
run() { if [ "$DRY" = 1 ]; then echo "  + $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="${2:?}"; shift 2 ;;
    --image) IMAGE="${2:?}"; shift 2 ;;
    --name) NAME="${2:?}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) usage ;;
    *) echo "athena-install: unknown argument '$1'" >&2; usage ;;
  esac
done

say() { printf 'athena-install: %s\n' "$*"; }
die() { printf 'athena-install: %s\n' "$*" >&2; exit 1; }

# -- prerequisites, before anything is written ---------------------------------
[ "$(uname -s)" = "Linux" ] || die "this installer is for Linux; see the repository README"
have distrobox || die "distrobox is required — https://distrobox.it"
have podman || have docker || die "distrobox needs a container engine: podman or docker"
[ -d "$SRC" ] || die "no athena-agent/ beside this installer — run it from a clone:
    git clone https://github.com/FenrirLupus/Athena && cd Athena && ./install.sh --install"
[ "$(id -u)" != "0" ] || die "run this as the user who will own the agent, not as root — the server is a user service"

# an Nvidia card is the common case for local models; pass --nvidia only when one is present
GPU=()
if have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then GPU=(--nvidia); fi

say "platform   distrobox · box ${NAME} · image ${IMAGE}${GPU:+ (nvidia)}"
say "install    ${TARGET}   (code only: profiles/, .secret, metrics/, sessions/ are never touched)"
[ "$DRY" = 1 ] && say "dry run — nothing will be written"

if [ "$YES" != 1 ] && [ "$DRY" != 1 ]; then
  printf 'athena-install: proceed? [y/N] '; read -r answer
  case "$answer" in y|Y|yes) ;; *) die "cancelled" ;; esac
fi

# -- 1 · the box ---------------------------------------------------------------
if distrobox list 2>/dev/null | awk -F'|' 'NR>1 {gsub(/ /,"",$2); print $2}' | grep -qx "$NAME"; then
  say "box       ${NAME} already exists — reusing it"
else
  say "box       creating ${NAME}"
  run distrobox create --name "$NAME" --image "$IMAGE" "${GPU[@]}" --yes
fi

# -- 2 · the system layer, inside the box --------------------------------------
# The box's package manager decides the names; both families are covered because the image is the operator's choice.
IN_BOX='
  set -e
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip git rsync sqlite3 >/dev/null
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q python3 python3-pip git rsync sqlite >/dev/null
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python python-virtualenv git rsync sqlite >/dev/null
  elif command -v zypper >/dev/null 2>&1; then
    sudo zypper --non-interactive install python3 python3-venv git rsync sqlite3 >/dev/null
  else
    echo "athena-install: no supported package manager in the box" >&2; exit 1
  fi
  python3 -c "import sqlite3; sqlite3.connect(\":memory:\").execute(\"create virtual table t using fts5(x)\")" \
    || { echo "athena-install: this Python has no FTS5 — the vault and the recall index require it" >&2; exit 1; }
'
say "deps      python3 · pip · venv · git · rsync · sqlite3 (with FTS5), inside the box"
run distrobox enter "$NAME" -- bash --norc -c "$IN_BOX"

# -- 3 · the code, preserving state -------------------------------------------
# --delete keeps the tree honest, and the excludes are the state: an install may replace the program, never the
# agent's transcripts, vault, index, metrics or keys.
say "code      copying ${SRC} → ${TARGET}"
run mkdir -p "$TARGET"
run rsync -a --delete \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '/graphify/' --exclude '/.venv/' \
  --exclude '/profiles/' --exclude '/.secret' --exclude '/.env' --exclude '/sessions/' --exclude '/metrics/' \
  --exclude '/backups/' --exclude '/*.db' --exclude '/*.db-wal' --exclude '/*.db-shm' \
  "${SRC}/" "${TARGET}/"

# -- 4 · the venv -------------------------------------------------------------
# The venv lives BESIDE the code, at ${PREFIX}/.venv — athena.sh looks for it there, and keeping it outside the code
# tree means a reinstall (which rsyncs with --delete) can never wipe the environment it is running from.
VENV_STEP='cd "$1" && if [ ! -x "$2/bin/python3" ]; then python3 -m venv "$2"; fi && "$2/bin/pip" install -q -r requirements.txt'
say "venv      ${PREFIX}/.venv (created once; requirements reapplied)"
run distrobox enter "$NAME" -- bash --norc -c "$VENV_STEP" _ "$TARGET" "${PREFIX}/.venv"

# -- 5 · prove it, offline ----------------------------------------------------
# selftest uses the mock provider: no key, no network, no credit — and it is the install's verdict, not a note. An
# install that cannot pass it is an install that does not run, so its exit code is this script's exit code.
CHECK_STEP='cd "$1" && ./athena.sh selftest >/tmp/athena-install-selftest.txt 2>&1; code=$?; grep -E "SELFTEST|FAIL|differ" /tmp/athena-install-selftest.txt | tail -4; exit $code'
say "verify    selftest (mock provider, offline) — a failure fails the install"
run distrobox enter "$NAME" -- bash --norc -c "$CHECK_STEP" _ "$TARGET" "${PREFIX}/.venv"

# -- 6 · what to do next ------------------------------------------------------
if [ "$DRY" = 1 ]; then
  say "dry run — nothing was written; the commands above are what would run"
  exit 0
fi

cat <<EOF

athena-install: installed.

  cd ${TARGET}
  ./athena.sh box serve          # the server, inside the box, on http://127.0.0.1:8765
  ./athena.sh lane               # can this lane start? config · model · key · window
  ./athena.sh selftest           # the offline check

A provider key is not installed by this script. Put it in ${PREFIX}/.secret — one line, e.g.
  DEEPSEEK_API_KEY=sk-…          (chmod 600)
or let the setup wizard write it, and confirm with ./athena.sh lane.
EOF
