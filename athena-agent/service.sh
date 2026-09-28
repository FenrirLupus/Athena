#!/usr/bin/env bash
# Athena service control — Linux (.sh) — the ROOT copy.
# Installs + starts Athena as a systemd USER service (auto-restart on
# boot + crash). Linux only (Windows uses service.bat).
# Usage: bash service.sh [install|start|stop|restart|status]
set -euo pipefail

SERVICE="athena.service"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$SERVICE"
DEST="$HOME/.config/systemd/user/$SERVICE"
CMD="${1:-install}"

case "$CMD" in
    install)
        echo "[service] installing $SERVICE -> $DEST"
        mkdir -p "$HOME/.config/systemd/user"
        # THE 08-15 PORTABILITY FIX: substitute the real binary dir into
        # the @ATHENA_BIN@ template so the unit points at THIS machine's
        # ~/.local/bin. THE 08-16 FIX: the SYSTEM unit's @ATHENA_USER@ +
        # @ATHENA_BIN@ are substituted too (the root athena-system.service
        # is a pure template — no machine info hardcoded).
        BIN_DIR="$HOME/.local/bin"
        USER_NAME="$(whoami)"
        # USER unit (systemd --user): substitute the binary dir.
        sed "s|@ATHENA_BIN@|$BIN_DIR|g" "$SRC" > "$DEST"
        # SYSTEM unit (plain systemctl): substitute user + bin.
        SYS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/athena-system.service"
        SYS_DEST="/etc/systemd/system/athena-system.service"
        if [ -w "/etc/systemd/system" ]; then
            sed -e "s|@ATHENA_BIN@|$BIN_DIR|g" -e "s|@ATHENA_USER@|$USER_NAME|g" \
                "$SYS_SRC" > "$SYS_DEST"
            systemctl daemon-reload
            echo "[service] system unit staged at $SYS_DEST"
        else
            echo "[service] (system unit needs root — staged manually:"
            echo "  sed -e 's|@ATHENA_BIN@|$BIN_DIR|g' -e 's|@ATHENA_USER@|$USER_NAME|g' \\"
            echo "    $SYS_SRC > /etc/systemd/system/athena-system.service)"
        fi
        systemctl --user daemon-reload
        systemctl --user enable --now "$SERVICE"
        sleep 2
        if systemctl --user is-active --quiet "$SERVICE"; then
            echo "[service] ACTIVE:"
            systemctl --user status "$SERVICE" --no-pager | head -6
        else
            echo "[service] failed to start — check: journalctl --user -u $SERVICE -n 50"
        fi
        ;;
    start)
        systemctl --user start "$SERVICE"
        echo "[service] started"
        ;;
    stop)
        systemctl --user stop "$SERVICE"
        echo "[service] stopped"
        ;;
    restart)
        systemctl --user restart "$SERVICE"
        echo "[service] restarted"
        ;;
    status)
        systemctl --user status "$SERVICE" --no-pager | head -8
        ;;
    *)
        echo "usage: bash service.sh [install|start|stop|restart|status]"
        exit 1
        ;;
esac
