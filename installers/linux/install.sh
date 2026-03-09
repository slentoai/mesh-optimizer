#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
#  Mesh Optimizer — Linux Installer
#  https://slentosystems.com
#
#  Usage:
#    curl -fsSL https://mesh.slentosystems.com/install.sh | bash
#    bash install.sh --silent              # unattended
#    bash install.sh --license MESH-XXXX   # pre-configure license
# ──────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$HOME/.mesh-optimizer"
VENV_DIR="$INSTALL_DIR/venv"
CONFIG_FILE="$INSTALL_DIR/mesh_config.yaml"
LOG_DIR="$INSTALL_DIR/logs"
BIN_LINK="/usr/local/bin/mesh-optimizer"
SERVICE_NAME="mesh-optimizer"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

# ── CLI args ──
SILENT=false
LICENSE_KEY=""
CONTROLLER_URL=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --silent|-s)     SILENT=true; shift ;;
        --license|-l)    LICENSE_KEY="$2"; shift 2 ;;
        --controller|-c) CONTROLLER_URL="$2"; shift 2 ;;
        --help|-h)       echo "Usage: install.sh [--silent] [--license KEY] [--controller URL]"; exit 0 ;;
        *)               shift ;;
    esac
done

# ── Colors ──
if [ -t 1 ]; then
    BOLD='\033[1m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    RED='\033[0;31m'
    CYAN='\033[0;36m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    BOLD='' GREEN='' YELLOW='' RED='' CYAN='' DIM='' RESET=''
fi

banner() {
    echo ""
    echo -e "${CYAN}${BOLD}  ╔══════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}  ║       Mesh Optimizer — Linux Installer   ║${RESET}"
    echo -e "${CYAN}${BOLD}  ║       slentosystems.com                  ║${RESET}"
    echo -e "${CYAN}${BOLD}  ╚══════════════════════════════════════════╝${RESET}"
    echo ""
}

info()    { echo -e "  ${GREEN}✓${RESET} $1"; }
warn()    { echo -e "  ${YELLOW}!${RESET} $1"; }
fail()    { echo -e "  ${RED}✗${RESET} $1"; exit 1; }
step()    { echo -e "\n${BOLD}[$1/$TOTAL_STEPS]${RESET} $2"; }
ask()     { if [ "$SILENT" = false ]; then read -r -p "  $1 [Y/n] " r; [[ "$r" =~ ^[Nn] ]] && return 1; fi; return 0; }

TOTAL_STEPS=6

# ══════════════════════════════════════════════════════════════
banner

# ── Step 1: Detect OS ──
step 1 "Detecting system..."

if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="$ID"
    DISTRO_VERSION="${VERSION_ID:-unknown}"
    info "Distribution: $PRETTY_NAME"
elif [ -f /etc/redhat-release ]; then
    DISTRO="rhel"
    DISTRO_VERSION=$(cat /etc/redhat-release | grep -oP '[\d.]+')
    info "Distribution: $(cat /etc/redhat-release)"
else
    DISTRO="unknown"
    DISTRO_VERSION="unknown"
    warn "Could not detect distribution"
fi

ARCH=$(uname -m)
info "Architecture: $ARCH"
info "Kernel: $(uname -r)"

# ── Step 2: Check/install Python ──
step 2 "Checking Python..."

find_python() {
    for cmd in python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [ "$major" -ge "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_CMD=""
if PYTHON_CMD=$(find_python); then
    info "Found: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"
else
    warn "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ not found"

    if ask "Install Python? (requires sudo)"; then
        case "$DISTRO" in
            ubuntu|debian|pop|linuxmint)
                sudo apt-get update -qq
                sudo apt-get install -y -qq python3 python3-venv python3-pip
                ;;
            fedora)
                sudo dnf install -y python3 python3-pip
                ;;
            centos|rhel|rocky|alma)
                sudo dnf install -y python3.11 python3.11-pip || sudo yum install -y python3 python3-pip
                ;;
            arch|manjaro)
                sudo pacman -Sy --noconfirm python python-pip
                ;;
            opensuse*|sles)
                sudo zypper install -y python311 python311-pip
                ;;
            *)
                fail "Unsupported distro '$DISTRO'. Install Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ manually and re-run."
                ;;
        esac

        PYTHON_CMD=$(find_python) || fail "Python installation failed. Install Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ manually."
        info "Installed: $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"
    else
        fail "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ is required."
    fi
fi

# Check venv module
if ! "$PYTHON_CMD" -m venv --help &>/dev/null; then
    warn "venv module not available, installing..."
    case "$DISTRO" in
        ubuntu|debian|pop|linuxmint)
            sudo apt-get install -y -qq python3-venv
            ;;
        *)
            warn "Could not auto-install venv module. You may need: sudo apt install python3-venv (or equivalent)"
            ;;
    esac
fi

# ── Step 3: Create install directory & venv ──
step 3 "Setting up Mesh Optimizer..."

mkdir -p "$INSTALL_DIR" "$LOG_DIR"
info "Install directory: $INSTALL_DIR"

if [ -d "$VENV_DIR" ]; then
    info "Virtual environment exists, upgrading..."
else
    "$PYTHON_CMD" -m venv "$VENV_DIR"
    info "Virtual environment created"
fi

# Activate and install
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q 2>/dev/null
info "pip upgraded"

# ── Step 4: Install mesh-optimizer ──
step 4 "Installing mesh-optimizer..."

pip install mesh-optimizer -q 2>/dev/null || {
    # If not yet on PyPI, install from source if available
    if [ -f "setup.py" ] || [ -f "pyproject.toml" ]; then
        pip install -e . -q
        info "Installed from local source"
    else
        # Install dependencies directly for now
        pip install aiohttp fastapi psutil pydantic pyyaml "uvicorn[standard]" -q
        warn "Package not yet on PyPI — installed dependencies directly"
    fi
}
info "mesh-optimizer installed"

# ── Step 5: Configure ──
step 5 "Configuring..."

if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" << 'YAML'
# Mesh Optimizer Configuration
# Full docs: https://docs.slentosystems.com/configuration

# Controller URL — set this to your controller's address
# For local controller (auto-started with Professional license): http://127.0.0.1:8401
# For remote controller: https://your-controller.example.com:8401
controller_url: "http://127.0.0.1:8401"

# Node settings
node:
  # Friendly name for this node (defaults to hostname)
  name: ""
  # Port for the agent API (other nodes/controller connect here)
  agent_port: 8400
  # Tags for job routing (e.g., ["gpu", "amd", "production"])
  tags: []
  # NAT mode — set true if this node is behind a firewall
  # In NAT mode, the agent only makes outbound connections
  nat_mode: false

  # Community atlas sharing: anonymized probe data is sent to improve the
  # global optimization model. Set to false to opt out.
  share_atlas_data: true

# Controller communication
controller:
  heartbeat_interval_s: 10.0
  probe_interval_s: 21600  # 6 hours

# Licensing
# Get a free key at https://portal.slentosystems.com
# Community (free): unlimited nodes, basic features
# Professional: continuous optimization, auto-tuning, controller included
licensing:
  license_key: ""
  portal_url: "https://portal.slentosystems.com"

# Security (optional)
security:
  require_auth: false
  verify_tls: true

# Logging
log_level: "INFO"
YAML

    # Apply CLI args to config
    if [ -n "$LICENSE_KEY" ]; then
        sed -i "s/license_key: \"\"/license_key: \"$LICENSE_KEY\"/" "$CONFIG_FILE"
        info "License key configured"
    fi
    if [ -n "$CONTROLLER_URL" ]; then
        sed -i "s|controller_url: \"http://127.0.0.1:8401\"|controller_url: \"$CONTROLLER_URL\"|" "$CONFIG_FILE"
        info "Controller URL configured"
    fi

    info "Config created: $CONFIG_FILE"
else
    info "Config exists, not overwriting: $CONFIG_FILE"
fi

# Create CLI wrapper
WRAPPER="$INSTALL_DIR/bin/mesh-optimizer"
mkdir -p "$INSTALL_DIR/bin"
cat > "$WRAPPER" << WRAPPER
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
exec mesh-optimizer "\$@"
WRAPPER
chmod +x "$WRAPPER"

# Symlink to PATH
if [ -w /usr/local/bin ]; then
    ln -sf "$WRAPPER" "$BIN_LINK" 2>/dev/null && info "Command available: mesh-optimizer"
elif [ -w "$HOME/.local/bin" ]; then
    mkdir -p "$HOME/.local/bin"
    ln -sf "$WRAPPER" "$HOME/.local/bin/mesh-optimizer"
    info "Command available: ~/.local/bin/mesh-optimizer"
    warn "Make sure ~/.local/bin is in your PATH"
else
    if ask "Create symlink at /usr/local/bin? (requires sudo)"; then
        sudo ln -sf "$WRAPPER" "$BIN_LINK"
        info "Command available: mesh-optimizer"
    else
        info "Run directly: $WRAPPER"
    fi
fi

# ── Step 6: System service ──
step 6 "System service..."

if command -v systemctl &>/dev/null; then
    SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

    install_service() {
        sudo tee "$SERVICE_FILE" > /dev/null << UNIT
[Unit]
Description=Mesh Optimizer Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$VENV_DIR/bin/mesh-optimizer start --config $CONFIG_FILE
Restart=on-failure
RestartSec=10
StandardOutput=append:$LOG_DIR/agent.log
StandardError=append:$LOG_DIR/agent-error.log

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$INSTALL_DIR

[Install]
WantedBy=multi-user.target
UNIT
        sudo systemctl daemon-reload
        sudo systemctl enable "$SERVICE_NAME"
        info "Service installed and enabled"

        if ask "Start the service now?"; then
            sudo systemctl start "$SERVICE_NAME"
            info "Service started"
        fi
    }

    if [ -f "$SERVICE_FILE" ]; then
        info "Service already installed — updating"
        install_service
    elif [ "$SILENT" = true ]; then
        install_service
    else
        # Install by default — only skip if user explicitly opts out
        info "The agent runs as a system service so it starts automatically on boot."
        info "Hardware changes (GPU swaps, new devices) are detected on each restart"
        info "and probed immediately."
        if ask "Install system service? (recommended)"; then
            install_service
        else
            info "Skipped — start manually: mesh-optimizer start"
        fi
    fi
else
    info "systemd not found — start manually: mesh-optimizer start"
fi

# ── Done ──
echo ""
echo -e "${GREEN}${BOLD}  ══════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✓ Mesh Optimizer installed successfully!  ${RESET}"
echo -e "${GREEN}${BOLD}  ══════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo -e "    ${CYAN}mesh-optimizer hardware${RESET}     Show detected hardware"
echo -e "    ${CYAN}mesh-optimizer start${RESET}        Start the agent"
echo -e "    ${CYAN}mesh-optimizer status${RESET}       Check agent status"
echo -e "    ${CYAN}mesh-optimizer stop${RESET}         Stop the agent"
echo ""
echo -e "  ${BOLD}Configuration:${RESET}"
echo -e "    ${DIM}$CONFIG_FILE${RESET}"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "    1. Get a free license key at ${CYAN}https://portal.slentosystems.com${RESET}"
echo -e "    2. Add it to your config: ${CYAN}licensing.license_key${RESET}"
echo -e "    3. Run ${CYAN}mesh-optimizer start${RESET}"
echo ""
echo -e "  ${BOLD}Documentation:${RESET} ${CYAN}https://docs.slentosystems.com${RESET}"
echo -e "  ${BOLD}Support:${RESET}       ${CYAN}support@slentosystems.com${RESET}"
echo ""
