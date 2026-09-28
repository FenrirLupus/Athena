#!/usr/bin/env bash
# Athena launcher — Linux (.sh) — the ROOT copy (the CEO's layout: all 4
# install/launcher files live in athena-system/, not launchers/).
# Thin wrapper: all logic lives in athena.py (SAME directory). Runs it
# with ATHENA'S OWN python environment (.athena/.venv — the Operator's
# portability spec 08-12).
#
# THE 08-16 SELF-HEALING FIX: when the venv is MISSING (a wipe, a fresh
# extract, a new machine), the launcher REBUILDS it automatically from
# requirements.txt instead of silently falling back to the system python
# (which lacks fastapi → the service crashed). The launcher is the
# guarantee: the venv always exists before athena.py runs.
#
# Resolves symlinks so the `athena` command (a symlink into ~/.local/bin)
# finds the real script regardless of where it is invoked from.
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
# athena.py lives in the SAME directory as this launcher.
ATHENA_SYSTEM="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
# THE 08-16 CANONICAL-ROOT CHECK (the Operator's spec): Athena's root is
# ALWAYS ~/.athena. If this launcher sits in the canonical spot
# (~/.athena/athena-system) OR its parent IS ~/.athena, use the derived
# parent — that covers the normal zip-unzipped-inside-.athena layout
# (.athena/athena-system/). If the launcher was run from a STRAY copy
# (Downloads, Desktop, /tmp), fall back to the REAL home — the launcher
# must never point at a random directory.
_DERIVED_ROOT="$(cd "$ATHENA_SYSTEM/.." && pwd)"
_HOME_ATHENA="$HOME/.athena"
if [ "$_DERIVED_ROOT" = "$_HOME_ATHENA" ] \
        || [ "$(cd "$_HOME_ATHENA" 2>/dev/null && pwd)" = "$_DERIVED_ROOT" ]; then
    ATHENA_ROOT="$_DERIVED_ROOT"
else
    ATHENA_ROOT="$_HOME_ATHENA"
fi

# The venv path (Athena's OWN environment — never a shared runtime).
VENV_DIR="$ATHENA_ROOT/.venv"
ATHENA_PY="$VENV_DIR/bin/python3"

# THE SELF-HEALING CHECK: if the venv (or its python) is missing, rebuild
# it from requirements.txt. This covers: a wiped .athena, a fresh zip
# extract, a new machine. The rebuild is quiet unless it fails.
if [ ! -x "$ATHENA_PY" ]; then
    echo "athena: .venv missing — rebuilding from requirements.txt..."
    if ! command -v python3 >/dev/null 2>&1; then
        echo "athena: no python3 found (need python3 to build the venv)" >&2
        exit 1
    fi
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel >/dev/null 2>&1 || true
    "$VENV_DIR/bin/pip" install -r "$ATHENA_SYSTEM/requirements.txt"
    echo "athena: .venv rebuilt + dependencies installed"
fi

if [ ! -x "$ATHENA_PY" ]; then
    echo "athena: venv rebuild failed (no python at $ATHENA_PY)" >&2
    exit 1
fi

exec "$ATHENA_PY" "$ATHENA_SYSTEM/athena.py" "$@"
