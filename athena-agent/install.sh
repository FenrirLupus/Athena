#!/bin/bash
# ============================================================================
# Athena Agent Installer
# ============================================================================
# One-line install for Linux/macOS (Windows: use install.bat).
# THE 08-16 DUMB-INSTALL RULE: Athena ALWAYS installs into ~/.athena,
# no matter where this script is run from (Downloads, Desktop, home,
# the zip-extract folder, or a future curl pipe). The destination is
# fixed; the source is wherever the script + code are.
#
# Usage:
#   bash install.sh                     (from the extracted folder, or by path)
#   bash ~/Downloads/athena-system/install.sh   (from anywhere)
#   ATHENA_ROOT=/mnt/Drive/Athena bash install.sh   (custom root, e.g. a hard drive)
# ============================================================================

set -e

# Guard against environment leakage.
if [ -n "${PYTHONPATH:-}" ]; then
    echo "  Ignoring inherited PYTHONPATH during install"
    unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
    echo "  Ignoring inherited PYTHONHOME during install"
    unset PYTHONHOME
fi

# Colors (the Athena theme — red/orange/yellow only)
RED='\033[0;31m'
ORANGE='\033[0;33m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

log_info()    { echo -e "${YELLOW}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn()    { echo -e "${ORANGE}⚠${NC} $1"; }
log_error()   { echo -e "${RED}✗${NC} $1"; }

# Configuration
REPO_URL_SSH="git@github.com:FenrirLupus/Athena-Agent.git"
REPO_URL_HTTPS="https://github.com/FenrirLupus/Athena-Agent.git"
# THE DUMB-INSTALL RULE: the destination is ALWAYS ~/.athena (or
# $ATHENA_ROOT if the user sets it — e.g. a hard-drive install). It
# NEVER depends on where the script was run from.
ATHENA_ROOT="${ATHENA_ROOT:-$HOME/.athena}"
INSTALL_DIR="${ATHENA_INSTALL_DIR:-$ATHENA_ROOT/athena-system}"
# The SOURCE: where the code currently is. If this script's own folder
# holds the code (the zip-extract case), that's the source to copy FROM.
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
if [ -f "$SRC_DIR/athena.py" ] && [ -f "$SRC_DIR/requirements.txt" ]; then
    CODE_SRC="$SRC_DIR"
else
    CODE_SRC=""   # no local code → the clone path will fetch it
fi
BRANCH="main"
USE_VENV=true
RUN_SETUP=true

# Detect non-interactive mode (curl | bash — no TTY)
if [ -t 0 ]; then
    IS_INTERACTIVE=true
else
    IS_INTERACTIVE=false
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-venv)
            USE_VENV=false
            shift
            ;;
        --skip-setup)
            RUN_SETUP=false
            shift
            ;;
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Athena Agent Installer"
            echo ""
            echo "Usage: install.sh [OPTIONS]"
            echo "  --no-venv      Don't create the virtual environment"
            echo "  --skip-setup   Skip the interactive API-key setup"
            echo "  --branch NAME  Git branch to install (default: main)"
            echo "  --dir PATH     Install directory (default: ~/.athena/athena-system)"
            echo "  -h, --help     Show this help"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

print_banner() {
    echo ""
    echo -e "${ORANGE}${BOLD:-}"
    echo "┌──────────────────────────────────────────────┐"
    echo "│            Athena Agent Installer            │"
    echo "├──────────────────────────────────────────────┤"
    echo "│  The self-hosted autonomous 24/7 agent.      │"
    echo "└──────────────────────────────────────────────┘"
    echo -e "${NC}"
}

# ============================================================================
# Stage 1: prerequisites
# ============================================================================
check_prereqs() {
    log_info "Checking prerequisites..."
    if ! command -v python3 >/dev/null 2>&1; then
        log_error "python3 not found. Install Python 3.10+ and re-run."
        exit 1
    fi
    if ! command -v git >/dev/null 2>&1; then
        log_error "git not found. Install git and re-run."
        exit 1
    fi
    PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    log_success "python3 $PYVER + git present"
}

# ============================================================================
# Stage 2: repository (the code ALWAYS lands at $INSTALL_DIR = ~/.athena/...)
# ============================================================================
clone_repo() {
    # Already installed → keep.
    if [ -f "$INSTALL_DIR/athena.py" ] && [ -f "$INSTALL_DIR/requirements.txt" ]; then
        log_success "Athena already installed at $INSTALL_DIR — keeping the code"
        return 0
    fi
    if [ -d "$INSTALL_DIR/.git" ]; then
        log_info "Athena already installed at $INSTALL_DIR — pulling latest..."
        cd "$INSTALL_DIR"
        git pull --ff-only origin "$BRANCH" 2>/dev/null || log_warn "pull failed (continuing)"
        return 0
    fi
    if [ -n "$CODE_SRC" ]; then
        # The zip-extract case: the code sits with this script (Downloads,
        # Desktop, anywhere) — copy it into the canonical .athena home.
        log_info "Copying the Athena code from $CODE_SRC → $INSTALL_DIR..."
        mkdir -p "$ATHENA_ROOT"
        mkdir -p "$INSTALL_DIR"
        cp -r "$CODE_SRC"/. "$INSTALL_DIR/"
        rm -rf "$INSTALL_DIR/__pycache__" 2>/dev/null || true
        find "$INSTALL_DIR" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
        log_success "Code copied to $INSTALL_DIR"
        return 0
    fi
    if [ -d "$INSTALL_DIR" ]; then
        log_error "Directory exists but is not a git repo: $INSTALL_DIR"
        log_info "Remove it or choose a different directory with --dir"
        exit 1
    fi
    log_info "Cloning Athena (branch: $BRANCH)..."
    mkdir -p "$(dirname "$INSTALL_DIR")"
    # THE 08-17 SUBFOLDER CLONE (the repo layout fix): the repo root is
    # README + LICENSE + athena-system/. The clone is a normal depth-1
    # clone (small — a few hundred files), then the athena-system/
    # subfolder is MOVED UP so the code lands DIRECTLY in INSTALL_DIR
    # (never nested — a nested athena-system/athena-system/ would break
    # the requirements path). Sparse checkout proved unreliable under
    # `bash <(curl ...)` (the subfolder never populated), so a plain
    # clone + move is the robust path.
    _clone_ok=false
    if GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5" \
        git clone --depth 1 --branch "$BRANCH" "$REPO_URL_SSH" "$INSTALL_DIR" 2>/dev/null; then
        _clone_ok=true
    else
        rm -rf "$INSTALL_DIR" 2>/dev/null
        if git clone --depth 1 --branch "$BRANCH" "$REPO_URL_HTTPS" "$INSTALL_DIR"; then
            _clone_ok=true
        else
            log_error "Failed to clone repository"
            exit 1
        fi
    fi
    if [ "$_clone_ok" = true ]; then
        # Move the athena-system/ subfolder up into INSTALL_DIR.
        if [ -d "$INSTALL_DIR/athena-system" ]; then
            _tmp="$(dirname "$INSTALL_DIR")/.athena-code-$$"
            mv "$INSTALL_DIR/athena-system" "$_tmp" 2>/dev/null || true
            rm -rf "$INSTALL_DIR" 2>/dev/null || true
            mv "$_tmp" "$INSTALL_DIR" 2>/dev/null || true
            # Drop the .git the clone left (the installed copy doesn't
            # need it — a future install pulls from the repo).
            rm -rf "$INSTALL_DIR/.git" 2>/dev/null || true
        fi
        log_success "Cloned via HTTPS"
    fi
}

# ============================================================================
# Stage 3: virtual environment
# ============================================================================
setup_venv() {
    if [ "$USE_VENV" = false ]; then
        log_info "Skipping virtual environment (--no-venv)"
        return 0
    fi
    VENV_DIR="$ATHENA_ROOT/.venv"
    # Idempotent: keep a working venv.
    if [ -x "$VENV_DIR/bin/python" ]; then
        log_success "Virtual environment already present at $VENV_DIR (kept)"
        return 0
    fi
    log_info "Creating virtual environment at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
    log_success "Virtual environment ready ($("$VENV_DIR/bin/python" --version 2>/dev/null))"
}

# ============================================================================
# Stage 4: dependencies
# ============================================================================
install_deps() {
    log_info "Installing dependencies (fastapi, uvicorn, pyyaml, ...)..."
    if [ "$USE_VENV" = true ]; then
        "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel >/dev/null
        "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
    else
        python3 -m pip install -r "$INSTALL_DIR/requirements.txt"
    fi
    log_success "Dependencies installed"
}

# ============================================================================
# Stage 5: the `athena` command
# ============================================================================
link_command() {
    log_info "Linking the 'athena' command..."
    BIN_DIR="${ATHENA_BIN:-$HOME/.local/bin}"
    mkdir -p "$BIN_DIR"
    chmod +x "$INSTALL_DIR/launcher.sh" 2>/dev/null || true
    ln -sf "$INSTALL_DIR/launcher.sh" "$BIN_DIR/athena"
    log_success "athena command linked: $BIN_DIR/athena"
}

# ============================================================================
# Stage 6: seed the runtime dirs (the wipe keep-list)
# ============================================================================
seed_runtime() {
    log_info "Seeding the runtime dirs (profiles, workflows, ...)..."
    mkdir -p "$ATHENA_ROOT/profiles" "$ATHENA_ROOT/workflows" \
             "$ATHENA_ROOT/skills" "$ATHENA_ROOT/tools" "$ATHENA_ROOT/plugins"
    log_success "Runtime dirs ready at $ATHENA_ROOT"
}

# ============================================================================
# Stage 7: interactive setup (API keys) — skippable
# ============================================================================
run_setup() {
    if [ "$RUN_SETUP" = false ] || [ "$IS_INTERACTIVE" = false ]; then
        log_info "Skipping interactive setup (--skip-setup / non-TTY)."
        log_info "Configure providers later via: athena setup"
        return 0
    fi
    log_info "Interactive setup — configure your provider keys."
    echo ""
    echo "  Athena reads credentials from $ATHENA_ROOT/authentication.json"
    echo "  (the two-store model: .secret holds keys, authentication.json"
    echo "  holds the registry). Run 'athena setup' after install to fill them."
    echo ""
}

# ============================================================================
# Done
# ============================================================================
main() {
    print_banner
    check_prereqs
    clone_repo
    setup_venv
    install_deps
    link_command
    seed_runtime
    run_setup
    echo ""
    log_success "Athena Agent installed!"
    log_success "  system:   $INSTALL_DIR"
    log_success "  data:     $ATHENA_ROOT"
    log_success "  command:  athena"
    echo ""
    echo "  Athena is SET UP in your home folder: $ATHENA_ROOT"
    echo "  The release ZIP and this extracted folder are now just"
    echo "  PORTABLE COPIES — they are NOT used by Athena. You may"
    echo "  safely delete them (the zip + this duplicate folder)."
    echo ""
    echo "  Next: run 'athena setup' to configure providers, then 'athena web'"
    echo "  to start the GUI server."
}

main "$@"
