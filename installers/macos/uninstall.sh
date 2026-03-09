#!/usr/bin/env bash
# Mesh Optimizer Uninstaller for macOS
# Copyright (c) 2026 Slento Systems. All rights reserved.
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.mesh-optimizer"
PLIST_NAME="com.slentosystems.mesh-optimizer"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
SYMLINK_SYSTEM="/usr/local/bin/mesh-optimizer"
SYMLINK_USER="$HOME/.local/bin/mesh-optimizer"

# ─── Flags ───────────────────────────────────────────────────────────────────
SILENT=false
KEEP_CONFIG=false
for arg in "$@"; do
    case "$arg" in
        --silent)      SILENT=true ;;
        --keep-config) KEEP_CONFIG=true ;;
        --help|-h)
            echo "Usage: $0 [--silent] [--keep-config] [--help]"
            echo "  --silent       Unattended uninstall (no prompts)"
            echo "  --keep-config  Keep configuration file (mesh_config.yaml)"
            echo "  --help         Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# ─── Colors ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]] && ! $SILENT; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' CYAN='' BOLD='' DIM='' RESET=''
fi

info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[  OK]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }

confirm() {
    if $SILENT; then return 0; fi
    local prompt="$1"
    echo -en "${YELLOW}$prompt [y/N]${RESET} "
    read -r reply
    [[ "$reply" =~ ^[Yy] ]]
}

# ─── Banner ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${RED}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║                                                  ║"
echo "  ║       Mesh Optimizer Uninstaller for macOS       ║"
echo "  ║                                                  ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${RESET}"

# ─── Confirm ─────────────────────────────────────────────────────────────────
if ! $SILENT; then
    echo -e "  This will remove Mesh Optimizer and all associated files."
    echo ""
    if ! confirm "Proceed with uninstall?"; then
        echo "Aborted."
        exit 0
    fi
    echo ""
fi

removed=0

# ─── Step 1: Stop and unload launchd service ────────────────────────────────
info "Checking for launch agent..."

if [[ -f "$PLIST_PATH" ]]; then
    launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    success "Removed launch agent ($PLIST_NAME)"
    removed=$((removed + 1))
else
    info "No launch agent found (skipped)"
fi

# ─── Step 2: Remove symlinks ────────────────────────────────────────────────
info "Removing command-line entry points..."

if [[ -L "$SYMLINK_SYSTEM" ]]; then
    target=$(readlink "$SYMLINK_SYSTEM" 2>/dev/null || true)
    if [[ "$target" == *"mesh-optimizer"* ]]; then
        sudo rm -f "$SYMLINK_SYSTEM" 2>/dev/null || rm -f "$SYMLINK_SYSTEM" 2>/dev/null || warn "Could not remove $SYMLINK_SYSTEM (try with sudo)"
        success "Removed $SYMLINK_SYSTEM"
        removed=$((removed + 1))
    fi
fi

if [[ -L "$SYMLINK_USER" ]]; then
    target=$(readlink "$SYMLINK_USER" 2>/dev/null || true)
    if [[ "$target" == *"mesh-optimizer"* ]]; then
        rm -f "$SYMLINK_USER"
        success "Removed $SYMLINK_USER"
        removed=$((removed + 1))
    fi
fi

# ─── Step 3: Save config if requested ───────────────────────────────────────
CONFIG_SAVED=""
if $KEEP_CONFIG && [[ -f "$INSTALL_DIR/mesh_config.yaml" ]]; then
    CONFIG_SAVED=$(mktemp /tmp/mesh_config_backup.XXXXXX.yaml)
    cp "$INSTALL_DIR/mesh_config.yaml" "$CONFIG_SAVED"
    info "Config backed up to $CONFIG_SAVED"
fi

# ─── Step 4: Remove install directory ────────────────────────────────────────
info "Removing installation directory..."

if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    success "Removed $INSTALL_DIR"
    removed=$((removed + 1))
else
    info "Install directory not found (skipped)"
fi

# ─── Step 5: Restore config if kept ─────────────────────────────────────────
if [[ -n "$CONFIG_SAVED" ]]; then
    mkdir -p "$INSTALL_DIR"
    mv "$CONFIG_SAVED" "$INSTALL_DIR/mesh_config.yaml"
    success "Config preserved at $INSTALL_DIR/mesh_config.yaml"
fi

# ─── Step 6: Clean up shell rc ──────────────────────────────────────────────
info "Cleaning up shell configuration..."

for rc_file in "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.bashrc"; do
    if [[ -f "$rc_file" ]] && grep -q "# Mesh Optimizer" "$rc_file" 2>/dev/null; then
        # Remove the Mesh Optimizer PATH lines
        sed -i.bak '/# Mesh Optimizer/d' "$rc_file"
        sed -i.bak '/\.local\/bin.*mesh/d' "$rc_file" 2>/dev/null || true
        # Clean up empty lines left behind but keep the backup for safety
        rm -f "${rc_file}.bak"
        success "Cleaned $rc_file"
    fi
done

# ─── Done ────────────────────────────────────────────────────────────────────
echo ""
if [[ $removed -gt 0 ]]; then
    echo -e "${BOLD}${GREEN}  Mesh Optimizer has been uninstalled.${RESET}"
else
    echo -e "${BOLD}${YELLOW}  No Mesh Optimizer installation was found.${RESET}"
fi
echo ""
echo -e "  ${DIM}Removed $removed component(s).${RESET}"
if [[ -n "$CONFIG_SAVED" ]] || ($KEEP_CONFIG && [[ -f "$INSTALL_DIR/mesh_config.yaml" ]]); then
    echo -e "  ${DIM}Configuration file was preserved.${RESET}"
fi
echo ""
