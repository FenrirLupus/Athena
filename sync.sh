#!/usr/bin/env bash
# Copy the live program tree into this repository working copy, so a push is one command and cannot pick up state.
#
#   ./sync.sh            copy ~/.athena/athena-agent → ./athena-agent
#   ./sync.sh --check    report what would change, copy nothing
#
# The excludes mirror athena-agent/.gitignore, and are anchored for the same reason it is: an unanchored `metrics/`
# once dropped tools/system/metrics/ from a release. Patterns beginning with `/` mean "at this tree's root"; state
# only ever lives there (beside the code, at the installation root), never inside a tool or skill package.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${SOURCE:-${HOME}/.athena/athena-agent}"
TARGET="${HERE}/athena-agent"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

[ -d "${SOURCE}" ] || { echo "sync: no program tree at ${SOURCE}" >&2; exit 1; }

FLAGS=(-a --delete
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '*.pyo' --exclude '/graphify/'
  --exclude '/profiles/' --exclude '/sessions/' --exclude '/metrics/' --exclude '/backups/'
  --exclude '/.venv/' --exclude '/.secret' --exclude '/.env' --exclude '/.env.*'
  --exclude '/security/api_token' --exclude '/security/*.secret'
  --exclude '/*.db' --exclude '/*.db-wal' --exclude '/*.db-shm' --exclude '*.session'
  --exclude '/state.json' --exclude '/temporal.json' --exclude '/identity.json')
[ "$CHECK" = 1 ] && FLAGS+=(--dry-run --itemize-changes)

echo "sync: ${SOURCE}/ → ${TARGET}/$([ "$CHECK" = 1 ] && echo ' (check only)')"
rsync "${FLAGS[@]}" "${SOURCE}/" "${TARGET}/"

# rsync's --delete does not remove files that an --exclude protects, so anything that reached the staging tree by
# another route (a test run, a review, a hand copy) survives every later sync. Prune those classes explicitly: this
# is a staging copy of source, never a working installation, so nothing here is worth preserving.
if [ "$CHECK" = 0 ] && [ -d "${TARGET}" ]; then
  find "${TARGET}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "${TARGET}" -name '*.py[co]' -delete 2>/dev/null || true
  for junk in graphify .venv profiles sessions metrics backups; do
    rm -rf "${TARGET:?}/${junk}"
  done
  find "${TARGET}" -maxdepth 1 \( -name '*.db' -o -name '*.db-wal' -o -name '*.db-shm' \) -delete 2>/dev/null || true
fi

if [ "$CHECK" = 0 ]; then
  live=$(find "${SOURCE}" -type f ! -path '*/__pycache__/*' ! -name '*.py[co]' ! -path '*/graphify/*' \
        ! -path "${SOURCE}/profiles/*" ! -path "${SOURCE}/sessions/*" ! -path "${SOURCE}/metrics/*" \
        ! -path "${SOURCE}/backups/*" ! -path "${SOURCE}/.venv/*" ! -name '*.db*' | wc -l)
  staged=$(find "${TARGET}" -type f | wc -l)
  echo "sync: done — ${staged} files staged, $(du -sh "${TARGET}" | cut -f1) · live tree expects ${live}"
  # A silent shortfall is how a tool pack goes missing from a release: say so instead of trusting rsync's exit code.
  [ "$staged" -eq "$live" ] || { echo "sync: MISMATCH — ${live} live vs ${staged} staged; check the excludes" >&2; exit 1; }
fi
