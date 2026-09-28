#!/usr/bin/env bash
# Athena uninstaller — Linux (.sh) — the ROOT copy.
# Removes Athena FULLY: the .athena home (code + venv + data), the
# `athena` command, and the systemd service (user + system units).
#
# Usage: bash uninstall.sh [--keep-data]
#   --keep-data   keep ~/.athena (code/data) but remove the command + service
#
# Safety: this deletes ~/.athena — your profiles, sessions, memories,
# provider keys (.secret), everything. Confirm before proceeding.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ATHENA_ROOT="$(cd "$SRC_DIR/.." && pwd)"
KEEP_DATA=false
if [ "${1:-}" = "--keep-data" ]; then
    KEEP_DATA=true
fi

echo ""
echo "┌──────────────────────────────────────────────┐"
echo "│            Athena Agent Uninstaller           │"
echo "└──────────────────────────────────────────────┘"
echo ""
echo "  This will remove Athena from:"
echo "    system:  $SRC_DIR"
if [ "$KEEP_DATA" = false ]; then
    echo "    data:    $ATHENA_ROOT  (profiles, sessions, memories, keys)"
fi
echo "    command: $HOME/.local/bin/athena"
echo "    service: athena.service (user) + athena-system.service (system)"
echo ""

# Confirm (skip when non-interactive — the caller knows what they asked).
if [ -t 0 ]; then
    read -r -p "  Type YES to remove Athena: " ans
    if [ "$ans" != "YES" ]; then
        echo "  Aborted."
        exit 1
    fi
fi

# 1. Stop + disable the user service (if installed).
if systemctl --user list-unit-files 2>/dev/null | grep -q "^athena.service"; then
    echo "[uninstall] stopping + disabling athena.service..."
    systemctl --user stop athena.service 2>/dev/null || true
    systemctl --user disable athena.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/athena.service"
    systemctl --user daemon-reload 2>/dev/null || true
fi

# 2. Stop + remove the system service (if installed + writable).
if [ -f "/etc/systemd/system/athena-system.service" ]; then
    echo "[uninstall] removing the system unit (may need root)..."
    if [ -w "/etc/systemd/system" ]; then
        systemctl stop athena-system.service 2>/dev/null || true
        systemctl disable athena-system.service 2>/dev/null || true
        rm -f /etc/systemd/system/athena-system.service
        systemctl daemon-reload 2>/dev/null || true
    else
        echo "[uninstall] (run as root to remove /etc/systemd/system/athena-system.service)"
    fi
fi

# 3. Remove the `athena` command.
rm -f "$HOME/.local/bin/athena"
echo "[uninstall] command removed: $HOME/.local/bin/athena"

# 4. Remove the data home (unless --keep-data).
if [ "$KEEP_DATA" = false ]; then
    if [ -d "$ATHENA_ROOT" ]; then
        echo "[uninstall] removing $ATHENA_ROOT ..."
        rm -rf "$ATHENA_ROOT"
        echo "[uninstall] data removed"
    fi
else
    echo "[uninstall] keeping $ATHENA_ROOT (--keep-data)"
fi

echo ""
echo "✓ Athena has been uninstalled."
echo "  (The release zip / extracted folder you ran this from is left untouched.)"
