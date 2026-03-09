#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  Mesh Optimizer — Linux Uninstaller
# ──────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$HOME/.mesh-optimizer"
BIN_LINK="/usr/local/bin/mesh-optimizer"
LOCAL_BIN="$HOME/.local/bin/mesh-optimizer"
SERVICE_NAME="mesh-optimizer"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

echo ""
echo -e "${BOLD}Mesh Optimizer — Uninstaller${RESET}"
echo ""

# Stop and remove service
if [ -f "$SERVICE_FILE" ]; then
    echo -e "  ${YELLOW}Stopping service...${RESET}"
    sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    sudo rm -f "$SERVICE_FILE"
    sudo systemctl daemon-reload
    echo -e "  ${GREEN}✓${RESET} Service removed"
fi

# Remove symlinks
if [ -L "$BIN_LINK" ]; then
    sudo rm -f "$BIN_LINK"
    echo -e "  ${GREEN}✓${RESET} Removed $BIN_LINK"
fi
if [ -L "$LOCAL_BIN" ]; then
    rm -f "$LOCAL_BIN"
    echo -e "  ${GREEN}✓${RESET} Removed $LOCAL_BIN"
fi

# Remove install directory
if [ -d "$INSTALL_DIR" ]; then
    read -r -p "  Remove $INSTALL_DIR and all data? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy] ]]; then
        rm -rf "$INSTALL_DIR"
        echo -e "  ${GREEN}✓${RESET} Removed $INSTALL_DIR"
    else
        echo -e "  ${YELLOW}!${RESET} Kept $INSTALL_DIR"
    fi
else
    echo -e "  ${YELLOW}!${RESET} Install directory not found"
fi

echo ""
echo -e "  ${GREEN}✓ Mesh Optimizer uninstalled${RESET}"
echo ""
