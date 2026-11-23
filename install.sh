#!/usr/bin/env bash
# Athena — the front door. One dispatcher, no logic of its own beyond detection.
#
# Every platform is a folder at the repository root with an install.sh inside it:
#   distrobox/     Linux through a Distrobox container (recommended, and required on atomic distributions)
#   athena-agent/  the program itself — never a platform
#
#   ./install.sh --list                 what this machine can install, and what each platform needs
#   ./install.sh --install              detect, show the plan, confirm, run <platform>/install.sh
#   ./install.sh --platform distrobox   override the detection
#   ./install.sh --prefix DIR           install root (default ~/.athena)
#   ./install.sh --dry-run              print the plan, write nothing
#   ./install.sh --ref BRANCH           which branch to fetch when this script is run on its own
#
# This file also works alone: given no platform beside it, it fetches the branch it came from into a temporary
# directory and re-runs itself there. That is what makes the download-and-run form work —
#   curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena/alpha-0.0.4/install.sh -o /tmp/athena-install.sh
#   bash /tmp/athena-install.sh --list
# A pipe (`curl … | bash`) is refused on purpose: the installer asks a question, and a script cannot read an answer
# from its own source.
#
# Exit codes: 0 done · 1 refused (unsupported platform or missing prerequisite) · 2 usage.
set -euo pipefail

ORIG_ARGS=("$@")

# Where this file actually is. A piped script has no path to itself, and cannot ask a question or find its own
# platform folders — say so plainly instead of failing later as a mysterious "cancelled".
SRC="${BASH_SOURCE[0]:-}"
if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  cat >&2 <<'PIPED'
athena: this script is being piped. Save it, then run it:
  curl -fsSL https://raw.githubusercontent.com/FenrirLupus/Athena/alpha-0.0.4/install.sh -o /tmp/athena-install.sh
  bash /tmp/athena-install.sh --list
PIPED
  exit 2
fi
HERE="$(cd "$(dirname "$SRC")" && pwd)"
PREFIX="${HOME}/.athena"
PROGRAM="athena-agent"
REPO_URL="${ATHENA_URL:-https://github.com/FenrirLupus/Athena.git}"
REF="${ATHENA_REF:-alpha-0.0.4}"
PLATFORM=""
MODE=""
DRY=0
PASS_THROUGH=()

usage() { sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }
have() { command -v "$1" >/dev/null 2>&1; }

platforms() {                   # every root folder that carries an install.sh, minus the program
  for dir in "${HERE}"/*/; do
    [ -d "$dir" ] || continue
    name="$(basename "$dir")"
    [ "$name" = "$PROGRAM" ] && continue
    [ -f "${dir}install.sh" ] && echo "$name"
  done
}

platform_state() {
  case "$1" in
    distrobox)
      if [ "$(uname -s)" != "Linux" ]; then echo "needs Linux"
      elif ! have distrobox; then echo "needs distrobox (https://distrobox.it)"
      elif have podman || have docker; then echo "available"
      else echo "needs podman or docker"; fi ;;
    linux)
      if [ "$(uname -s)" = "Linux" ]; then echo "available"; else echo "needs Linux"; fi ;;
    *) echo "unknown platform" ;;
  esac
}

list_platforms() {
  echo "Athena can be installed on:"
  for name in $(platforms); do
    printf '  %-10s %s\n' "$name" "$(platform_state "$name")"
  done
  echo
  echo "Program: ${PROGRAM}/ · kernel: $(uname -s) · HOME: ${HOME} · install root: ${PREFIX}"
}

detect_platform() {
  for name in $(platforms); do
    if [ "$(platform_state "$name")" = "available" ]; then echo "$name"; return 0; fi
  done
  return 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --list) MODE="list"; shift ;;
    --install) MODE="install"; shift ;;
    --platform) PLATFORM="${2:-}"; [ -n "$PLATFORM" ] || usage; shift 2 ;;
    --prefix) PREFIX="${2:-}"; [ -n "$PREFIX" ] || usage; shift 2 ;;
    --ref) REF="${2:-}"; [ -n "$REF" ] || usage; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --yes|-y) PASS_THROUGH+=("--yes"); shift ;;
    -h|--help) usage ;;
    *) PASS_THROUGH+=("$1"); shift ;;
  esac
done

# -- bootstrap: this file run on its own ---------------------------------------
# A copied install.sh has no platform folders beside it. Fetch the branch and continue there, so the download-and-run
# form needs no clone of its own. The temporary tree is removed afterwards; the installer never installs from it
# directly, only copies the program out of it.
if [ -z "$(platforms)" ] && [ "${ATHENA_BOOTSTRAPPED:-0}" != 1 ]; then
  have git || { echo "athena: no platform folder beside this script, and no git to fetch one." >&2
                echo "athena: clone instead — git clone ${REPO_URL} && cd Athena && ./install.sh --list" >&2; exit 1; }
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/athena-install.XXXXXX")"
  echo "athena: fetching ${REF} from ${REPO_URL} (nothing is installed from this copy)"
  if ! git clone --depth 1 --branch "$REF" "$REPO_URL" "${TMP}/Athena" >/dev/null 2>&1; then
    rm -rf "$TMP"; echo "athena: could not fetch '${REF}' from ${REPO_URL} — pass --ref BRANCH to choose another" >&2; exit 1
  fi
  set +e
  ATHENA_BOOTSTRAPPED=1 bash "${TMP}/Athena/install.sh" "${ORIG_ARGS[@]}"
  rc=$?
  set -e
  rm -rf "$TMP"
  exit "$rc"
fi

[ "$MODE" = "list" ] && { list_platforms; exit 0; }
[ -n "$MODE" ] || { list_platforms; exit 0; }

if [ -z "$PLATFORM" ]; then
  PLATFORM="$(detect_platform)" || {
    echo "athena: no installable platform found on this machine." >&2
    list_platforms >&2
    exit 1
  }
fi

SCRIPT="${HERE}/${PLATFORM}/install.sh"
[ -f "$SCRIPT" ] || { echo "athena: no '${PLATFORM}' platform folder (looked for ${SCRIPT})." >&2; exit 1; }
STATE="$(platform_state "$PLATFORM")"
[ "$STATE" = "available" ] || { echo "athena: ${PLATFORM} is not installable here — ${STATE}." >&2; exit 1; }

ARGS=(--prefix "$PREFIX")
[ "$DRY" = 1 ] && ARGS+=(--dry-run)
[ ${#PASS_THROUGH[@]} -gt 0 ] && ARGS+=("${PASS_THROUGH[@]}")

echo "athena: platform ${PLATFORM} · install root ${PREFIX} · code → ${PREFIX}/${PROGRAM}"
echo "athena: running ${SCRIPT#"${HERE}"/} ${ARGS[*]}"
exec bash "$SCRIPT" "${ARGS[@]}"
