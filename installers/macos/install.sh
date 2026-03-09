#!/usr/bin/env bash
# Mesh Optimizer Installer for macOS
# Copyright (c) 2026 Slento Systems. All rights reserved.
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.mesh-optimizer"
VENV_DIR="$INSTALL_DIR/venv"
CONFIG_FILE="$INSTALL_DIR/mesh_config.yaml"
LOG_DIR="$INSTALL_DIR/logs"
PLIST_NAME="com.slentosystems.mesh-optimizer"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
SYMLINK_SYSTEM="/usr/local/bin/mesh-optimizer"
SYMLINK_USER="$HOME/.local/bin/mesh-optimizer"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
PREFERRED_PYTHON="python3.12"
PACKAGE_NAME="mesh-optimizer"

# ─── Flags ───────────────────────────────────────────────────────────────────
SILENT=false
INSTALL_SERVICE=false
for arg in "$@"; do
    case "$arg" in
        --silent)  SILENT=true ;;
        --service) INSTALL_SERVICE=true ;;
        --help|-h)
            echo "Usage: $0 [--silent] [--service] [--help]"
            echo "  --silent   Unattended install (no prompts, installs service)"
            echo "  --service  Install launchd service for auto-start on login"
            echo "  --help     Show this help"
            exit 0
            ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

if $SILENT; then
    INSTALL_SERVICE=true
fi

# ─── Colors & Formatting ────────────────────────────────────────────────────
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

# ─── Helpers ─────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[  OK]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
fail()    { echo -e "${RED}[FAIL]${RESET}  $*" >&2; exit 1; }

step_num=0
step() {
    step_num=$((step_num + 1))
    echo ""
    echo -e "${BOLD}${CYAN}[$step_num]${RESET} ${BOLD}$*${RESET}"
    echo -e "${DIM}$(printf '%.0s─' {1..60})${RESET}"
}

confirm() {
    if $SILENT; then return 0; fi
    local prompt="$1"
    echo -en "${YELLOW}$prompt [Y/n]${RESET} "
    read -r reply
    [[ -z "$reply" || "$reply" =~ ^[Yy] ]]
}

spinner() {
    local pid=$1 msg=$2
    local spin='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
        printf "\r  ${DIM}%s${RESET} %s" "${spin:i++%${#spin}:1}" "$msg"
        sleep 0.1
    done
    wait "$pid"
    local rc=$?
    printf "\r"
    return $rc
}

# ─── Banner ──────────────────────────────────────────────────────────────────
print_banner() {
    echo ""
    echo -e "${BOLD}${CYAN}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║                                                  ║"
    echo "  ║       Mesh Optimizer Installer for macOS         ║"
    echo "  ║                                                  ║"
    echo "  ║       Slento Systems                             ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${RESET}"
}

print_banner

# ─── Pre-flight: macOS only ──────────────────────────────────────────────────
if [[ "$(uname -s)" != "Darwin" ]]; then
    fail "This installer is for macOS only. Detected: $(uname -s)"
fi

# ─── Step 1: Detect macOS version and architecture ───────────────────────────
step "Detecting system"

MACOS_VERSION=$(sw_vers -productVersion)
MACOS_MAJOR=$(echo "$MACOS_VERSION" | cut -d. -f1)
MACOS_MINOR=$(echo "$MACOS_VERSION" | cut -d. -f2)
ARCH=$(uname -m)

case "$ARCH" in
    arm64) ARCH_LABEL="Apple Silicon (arm64)" ;;
    x86_64) ARCH_LABEL="Intel (x86_64)" ;;
    *) ARCH_LABEL="$ARCH" ;;
esac

info "macOS $MACOS_VERSION ($ARCH_LABEL)"

if [[ "$MACOS_MAJOR" -lt 12 ]]; then
    warn "macOS 12 (Monterey) or later is recommended. You have $MACOS_VERSION."
    if ! confirm "Continue anyway?"; then
        echo "Aborted." ; exit 0
    fi
fi

success "System check passed"

# ─── Step 2: Check / install Python ──────────────────────────────────────────
step "Checking Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+"

find_suitable_python() {
    # Check common Python paths in order of preference
    local candidates=(
        "$PREFERRED_PYTHON"
        "python3.12"
        "python3.11"
        "python3.10"
        "python3.13"
        "python3"
    )
    for cmd in "${candidates[@]}"; do
        if command -v "$cmd" &>/dev/null; then
            local ver
            ver=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ "$major" -eq "$MIN_PYTHON_MAJOR" && "$minor" -ge "$MIN_PYTHON_MINOR" ]] || \
               [[ "$major" -gt "$MIN_PYTHON_MAJOR" ]]; then
                PYTHON_CMD="$cmd"
                PYTHON_VERSION="$ver"
                return 0
            fi
        fi
    done
    return 1
}

install_homebrew() {
    info "Installing Homebrew..."
    if ! confirm "Homebrew is required to install Python. Install Homebrew now?"; then
        return 1
    fi
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add Homebrew to PATH for this session
    if [[ "$ARCH" == "arm64" ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    else
        eval "$(/usr/local/bin/brew shellenv)"
    fi
}

install_python_brew() {
    info "Installing Python via Homebrew..."
    brew install python@3.12
    # Homebrew Python may need linking
    brew link --overwrite python@3.12 2>/dev/null || true
}

install_python_org() {
    info "Downloading Python from python.org..."
    local py_ver="3.12.8"
    local pkg_name
    if [[ "$ARCH" == "arm64" ]]; then
        pkg_name="python-${py_ver}-macos11.pkg"
    else
        pkg_name="python-${py_ver}-macos11.pkg"
    fi
    local url="https://www.python.org/ftp/python/${py_ver}/${pkg_name}"
    local tmp_pkg
    tmp_pkg=$(mktemp /tmp/python-installer.XXXXXX.pkg)
    curl -fSL "$url" -o "$tmp_pkg" || fail "Failed to download Python from python.org"
    info "Running Python installer (may require password)..."
    sudo installer -pkg "$tmp_pkg" -target / || fail "Python installation failed"
    rm -f "$tmp_pkg"
}

PYTHON_CMD=""
PYTHON_VERSION=""

if find_suitable_python; then
    success "Found Python $PYTHON_VERSION ($(which "$PYTHON_CMD"))"
else
    warn "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ not found"

    installed=false

    # Strategy 1: Homebrew
    if command -v brew &>/dev/null; then
        info "Homebrew detected"
        if confirm "Install Python 3.12 via Homebrew?"; then
            install_python_brew
            installed=true
        fi
    else
        info "Homebrew not found"
        if confirm "Install Homebrew and Python?"; then
            install_homebrew && install_python_brew
            installed=true
        fi
    fi

    # Strategy 2: python.org
    if ! $installed; then
        if confirm "Download Python from python.org instead?"; then
            install_python_org
            installed=true
        fi
    fi

    if ! $installed; then
        fail "Python $MIN_PYTHON_MAJOR.$MIN_PYTHON_MINOR+ is required. Please install it and re-run this installer."
    fi

    # Re-scan after installation
    hash -r
    if ! find_suitable_python; then
        fail "Python was installed but could not be found on PATH. Try opening a new terminal."
    fi
    success "Installed Python $PYTHON_VERSION"
fi

# ─── Step 3: Create install directory ────────────────────────────────────────
step "Creating install directory"

if [[ -d "$INSTALL_DIR" ]]; then
    warn "Existing installation found at $INSTALL_DIR"
    if ! confirm "Overwrite existing installation?"; then
        echo "Aborted." ; exit 0
    fi
    rm -rf "$VENV_DIR"
fi

mkdir -p "$INSTALL_DIR"
mkdir -p "$LOG_DIR"
success "Created $INSTALL_DIR"

# ─── Step 4: Create virtual environment and install ──────────────────────────
step "Setting up Python environment"

info "Creating virtual environment..."
"$PYTHON_CMD" -m venv "$VENV_DIR" || fail "Failed to create virtual environment"
success "Virtual environment created"

info "Upgrading pip..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet 2>&1 | tail -1 || true
success "pip upgraded"

info "Installing $PACKAGE_NAME..."
if "$VENV_DIR/bin/pip" install "$PACKAGE_NAME" --quiet 2>/dev/null; then
    MESH_VERSION=$("$VENV_DIR/bin/pip" show "$PACKAGE_NAME" 2>/dev/null | grep -i '^Version:' | awk '{print $2}')
    success "Installed $PACKAGE_NAME ${MESH_VERSION:-latest}"
else
    warn "Package '$PACKAGE_NAME' not found on PyPI (may not be published yet)"
    info "Creating placeholder entry point"
    mkdir -p "$VENV_DIR/bin"
    cat > "$VENV_DIR/bin/mesh-optimizer" << 'ENTRY'
#!/usr/bin/env bash
echo "mesh-optimizer: package not yet installed. Run: ~/.mesh-optimizer/venv/bin/pip install mesh-optimizer"
exit 1
ENTRY
    chmod +x "$VENV_DIR/bin/mesh-optimizer"
    MESH_VERSION="(pending)"
fi

# ─── Step 5: Generate default config ────────────────────────────────────────
step "Generating configuration"

if [[ -f "$CONFIG_FILE" ]]; then
    info "Config already exists, backing up to mesh_config.yaml.bak"
    cp "$CONFIG_FILE" "${CONFIG_FILE}.bak"
fi

cat > "$CONFIG_FILE" << 'YAML'
# Mesh Optimizer Configuration
# Documentation: https://docs.slentosystems.com/mesh-optimizer/config

# ─── Agent Identity ──────────────────────────────────────────────────────────
# Unique identifier for this agent (auto-generated if empty)
# agent_id: ""

# Display name shown in the Mesh dashboard
# agent_name: "My Mac"

# ─── Controller Connection ──────────────────────────────────────────────────
# Mesh controller endpoint
# controller_url: "https://mesh.slentosystems.com"

# API key for authentication (obtain from dashboard)
# api_key: ""

# Connection timeout in seconds
# connect_timeout: 30

# Reconnect interval on connection loss (seconds)
# reconnect_interval: 10

# ─── Resource Limits ────────────────────────────────────────────────────────
# Maximum CPU cores to allocate (0 = auto-detect)
# max_cpu_cores: 0

# Maximum memory in MB (0 = 80% of available)
# max_memory_mb: 0

# Maximum disk usage in GB for cache/temp files
# max_disk_gb: 10

# ─── GPU Configuration ──────────────────────────────────────────────────────
# Enable GPU acceleration (requires Metal/MPS on Apple Silicon)
# gpu_enabled: true

# GPU memory limit in MB (0 = auto)
# gpu_memory_mb: 0

# ─── Scheduling ─────────────────────────────────────────────────────────────
# Only accept work during these hours (24h format, local time)
# schedule_start: "00:00"
# schedule_end: "23:59"

# Pause when on battery power
# pause_on_battery: true

# Pause when CPU temperature exceeds threshold (Celsius)
# thermal_limit: 90

# ─── Logging ─────────────────────────────────────────────────────────────────
# Log level: debug, info, warning, error
# log_level: "info"

# Log file (relative to install dir or absolute path)
# log_file: "logs/mesh-optimizer.log"

# Maximum log file size before rotation (MB)
# log_max_size_mb: 50

# Number of rotated log files to keep
# log_backups: 5

# ─── Advanced ────────────────────────────────────────────────────────────────
# Enable automatic updates
# auto_update: true

# Telemetry (anonymous usage stats)
# telemetry: true

# Custom work directory
# work_dir: "~/.mesh-optimizer/work"
YAML

success "Config written to $CONFIG_FILE"

# ─── Step 6: Create symlink ─────────────────────────────────────────────────
step "Creating command-line entry point"

MESH_BIN="$VENV_DIR/bin/mesh-optimizer"
SYMLINK_CREATED=""

if [[ -w "$(dirname "$SYMLINK_SYSTEM")" ]] || sudo -n true 2>/dev/null; then
    if $SILENT || confirm "Create symlink at $SYMLINK_SYSTEM (requires sudo)?"; then
        sudo ln -sf "$MESH_BIN" "$SYMLINK_SYSTEM" 2>/dev/null && SYMLINK_CREATED="$SYMLINK_SYSTEM"
    fi
fi

if [[ -z "$SYMLINK_CREATED" ]]; then
    mkdir -p "$(dirname "$SYMLINK_USER")"
    ln -sf "$MESH_BIN" "$SYMLINK_USER"
    SYMLINK_CREATED="$SYMLINK_USER"

    # Ensure ~/.local/bin is on PATH
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        warn "$HOME/.local/bin is not in your PATH"
        SHELL_RC=""
        case "$(basename "$SHELL")" in
            zsh)  SHELL_RC="$HOME/.zshrc" ;;
            bash) SHELL_RC="$HOME/.bash_profile" ;;
        esac
        if [[ -n "$SHELL_RC" ]]; then
            echo '' >> "$SHELL_RC"
            echo '# Mesh Optimizer' >> "$SHELL_RC"
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
            info "Added ~/.local/bin to PATH in $SHELL_RC"
        fi
    fi
fi

success "Symlink created at $SYMLINK_CREATED"

# ─── Step 7: launchd service ────────────────────────────────────────────────
step "Launch agent (auto-start on login)"

setup_launchd() {
    mkdir -p "$(dirname "$PLIST_PATH")"

    cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/mesh-optimizer</string>
        <string>--config</string>
        <string>${CONFIG_FILE}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/mesh-optimizer.stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/mesh-optimizer.stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>ProcessType</key>
    <string>Background</string>

    <key>LowPriorityBackgroundIO</key>
    <true/>

    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

    # Load the agent
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load -w "$PLIST_PATH" 2>/dev/null || true

    success "Launch agent installed at $PLIST_PATH"
    info "Service will auto-start on login"
    info "Manage with: launchctl load/unload $PLIST_PATH"
}

if $INSTALL_SERVICE; then
    setup_launchd
elif confirm "Install as a launch agent (auto-start on login)?"; then
    setup_launchd
else
    info "Skipped. You can install the service later with: $0 --service"
fi

# ─── Done ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║                                                  ║"
echo "  ║        Installation Complete                     ║"
echo "  ║                                                  ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${RESET}"
echo -e "  ${BOLD}Install path:${RESET}   $INSTALL_DIR"
echo -e "  ${BOLD}Python:${RESET}         $PYTHON_VERSION ($(which "$PYTHON_CMD"))"
echo -e "  ${BOLD}Version:${RESET}        ${MESH_VERSION:-unknown}"
echo -e "  ${BOLD}Config:${RESET}         $CONFIG_FILE"
echo -e "  ${BOLD}Logs:${RESET}           $LOG_DIR"
echo -e "  ${BOLD}Command:${RESET}        $SYMLINK_CREATED"
echo ""
echo -e "  ${BOLD}Next steps:${RESET}"
echo -e "    1. Edit ${CYAN}$CONFIG_FILE${RESET}"
echo -e "       Set your ${BOLD}api_key${RESET} and ${BOLD}controller_url${RESET}"
echo ""
echo -e "    2. Start the optimizer:"
echo -e "       ${DIM}\$ mesh-optimizer start${RESET}"
echo ""
echo -e "    3. Check status:"
echo -e "       ${DIM}\$ mesh-optimizer status${RESET}"
echo ""
echo -e "    4. View logs:"
echo -e "       ${DIM}\$ tail -f $LOG_DIR/mesh-optimizer.log${RESET}"
echo ""
echo -e "  ${DIM}Uninstall: bash $(dirname "$0")/uninstall.sh${RESET}"
echo ""
