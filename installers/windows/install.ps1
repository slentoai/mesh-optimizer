#Requires -Version 5.1
<#
.SYNOPSIS
    Mesh Optimizer Installer for Windows
.DESCRIPTION
    Installs the mesh-optimizer Python agent on Windows with venv isolation,
    PATH configuration, and optional service registration.
.PARAMETER Silent
    Run in unattended mode — accepts all defaults, no prompts.
.PARAMETER InstallDir
    Override the default install directory ($env:LOCALAPPDATA\MeshOptimizer).
.PARAMETER SkipService
    Skip the service/scheduled task registration prompt.
.EXAMPLE
    .\install.ps1
    .\install.ps1 -Silent
    .\install.ps1 -Silent -InstallDir "D:\MeshOptimizer"
#>
[CmdletBinding()]
param(
    [switch]$Silent,
    [string]$InstallDir = "$env:LOCALAPPDATA\MeshOptimizer",
    [switch]$SkipService
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Banner {
    $banner = @"

  __  __           _        ___        _   _           _
 |  \/  | ___  ___| |__    / _ \ _ __ | |_(_)_ __ ___ (_)_______ _ __
 | |\/| |/ _ \/ __| '_ \  | | | | '_ \| __| | '_ ` _ \| |_  / _ \ '__|
 | |  | |  __/\__ \ | | | | |_| | |_) | |_| | | | | | | |/ /  __/ |
 |_|  |_|\___||___/_| |_|  \___/| .__/ \__|_|_| |_| |_|_/___\___|_|
                                 |_|
"@
    Write-Host $banner -ForegroundColor Cyan
    Write-Host "  Mesh Optimizer Installer for Windows" -ForegroundColor White
    Write-Host "  ====================================`n" -ForegroundColor DarkGray
}

function Write-Step  { param([string]$Msg) Write-Host "[*] " -ForegroundColor Cyan -NoNewline; Write-Host $Msg }
function Write-Ok    { param([string]$Msg) Write-Host "[+] " -ForegroundColor Green -NoNewline; Write-Host $Msg }
function Write-Warn  { param([string]$Msg) Write-Host "[!] " -ForegroundColor Yellow -NoNewline; Write-Host $Msg }
function Write-Err   { param([string]$Msg) Write-Host "[-] " -ForegroundColor Red -NoNewline; Write-Host $Msg }
function Write-Info  { param([string]$Msg) Write-Host "    " -NoNewline; Write-Host $Msg -ForegroundColor DarkGray }

function Confirm-Prompt {
    param([string]$Question, [bool]$Default = $true)
    if ($Silent) { return $Default }
    $suffix = if ($Default) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$Question $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $Default }
    return $answer -match '^[Yy]'
}

function Refresh-PathEnv {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path    = "$machinePath;$userPath"
}

function Test-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Python {
    # Try common names in order of preference
    foreach ($cmd in @("python3", "python", "py")) {
        $exe = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($exe) {
            try {
                $verStr = & $exe.Source --version 2>&1
                if ($verStr -match '(\d+)\.(\d+)\.(\d+)') {
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    if ($major -ge 3 -and $minor -ge 10) {
                        return $exe.Source
                    }
                }
            } catch { }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Banner

# --- Step 1: Admin check ---------------------------------------------------
Write-Step "Checking privileges..."
if (Test-Admin) {
    Write-Ok "Running as Administrator."
} else {
    Write-Warn "Not running as Administrator. Some operations (service install, system PATH) may fail."
    Write-Info "Re-run as Admin if you need service registration.`n"
}

# --- Step 2: Python check ---------------------------------------------------
Write-Step "Checking for Python 3.10+..."
$pythonExe = Find-Python

if (-not $pythonExe) {
    Write-Warn "Python 3.10+ not found on PATH."
    Write-Step "Attempting to install Python 3.12..."

    $installed = $false

    # Try winget first
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info "Using winget..."
        try {
            $wingetArgs = @("install", "Python.Python.3.12", "--accept-source-agreements", "--accept-package-agreements")
            if ($Silent) { $wingetArgs += "--silent" }
            & winget @wingetArgs 2>&1 | ForEach-Object { Write-Info $_ }
            if ($LASTEXITCODE -eq 0) {
                $installed = $true
                Write-Ok "Python 3.12 installed via winget."
            }
        } catch {
            Write-Warn "winget install failed: $_"
        }
    }

    # Fallback: direct download
    if (-not $installed) {
        Write-Info "winget not available or failed. Downloading from python.org..."
        $pyUrl      = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
        $pyInstaller = Join-Path $env:TEMP "python-3.12.8-amd64.exe"

        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $wc = New-Object System.Net.WebClient
            Write-Info "Downloading $pyUrl ..."
            $wc.DownloadFile($pyUrl, $pyInstaller)

            Write-Info "Running silent install (InstallAllUsers=0, PrependPath=1)..."
            $proc = Start-Process -FilePath $pyInstaller `
                -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1" `
                -Wait -PassThru
            if ($proc.ExitCode -eq 0) {
                $installed = $true
                Write-Ok "Python 3.12 installed from python.org."
            } else {
                Write-Err "Installer exited with code $($proc.ExitCode)."
            }
        } catch {
            Write-Err "Download/install failed: $_"
        } finally {
            if (Test-Path $pyInstaller) { Remove-Item $pyInstaller -Force -ErrorAction SilentlyContinue }
        }
    }

    # Refresh PATH and re-check
    Refresh-PathEnv
    $pythonExe = Find-Python

    if (-not $pythonExe) {
        Write-Err "Could not find or install Python 3.10+. Please install Python manually and re-run."
        exit 1
    }
}

$pyVer = & $pythonExe --version 2>&1
Write-Ok "Found: $pyVer ($pythonExe)"

# --- Step 3: Create install directory ---------------------------------------
Write-Step "Creating install directory: $InstallDir"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Ok "Created $InstallDir"
} else {
    Write-Warn "Directory already exists. Existing files may be overwritten."
}

# --- Step 4: Create virtual environment -------------------------------------
$venvDir = Join-Path $InstallDir "venv"
Write-Step "Creating Python virtual environment..."
if (Test-Path $venvDir) {
    Write-Warn "venv already exists at $venvDir — reusing."
} else {
    & $pythonExe -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to create virtual environment."
        exit 1
    }
    Write-Ok "venv created at $venvDir"
}

$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvPip    = Join-Path $venvDir "Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    Write-Err "venv python not found at $venvPython"
    exit 1
}

# --- Step 5: Install mesh-optimizer -----------------------------------------
Write-Step "Installing mesh-optimizer..."
& $venvPip install --upgrade pip 2>&1 | ForEach-Object { Write-Info $_ }
& $venvPip install mesh-optimizer 2>&1 | ForEach-Object { Write-Info $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install mesh-optimizer failed."
    exit 1
}
Write-Ok "mesh-optimizer installed."

# --- Step 6: Create default config ------------------------------------------
$configPath = Join-Path $InstallDir "mesh_config.yaml"
Write-Step "Writing default config: $configPath"

$configContent = @"
# Mesh Optimizer Configuration
# ============================================================
# Uncomment and edit values as needed. Defaults are shown.

# --- Agent Identity ---------------------------------------------------------
# agent_id: ""                    # Unique agent ID (auto-generated if blank)
# agent_name: "mesh-agent-01"    # Human-readable name for this agent

# --- Controller Connection --------------------------------------------------
# controller_url: "https://mesh.example.com"   # Mesh controller endpoint
# api_key: ""                                   # API key for authentication
# tls_verify: true                              # Verify TLS certificates
# reconnect_interval_sec: 30                    # Seconds between reconnects

# --- Optimization Targets ---------------------------------------------------
# targets:
#   - type: "gpu"                 # gpu | cpu | memory | network
#     device_index: 0             # Device ordinal
#     metrics:
#       - throughput
#       - latency
#       - power

# --- Telemetry & Logging ----------------------------------------------------
# log_level: "info"               # debug | info | warning | error
# log_file: "mesh_optimizer.log"  # Log file path (relative to install dir)
# telemetry_interval_sec: 10      # How often to push metrics

# --- Resource Limits --------------------------------------------------------
# max_cpu_percent: 5.0            # Max CPU the agent itself may use
# max_memory_mb: 256              # Max memory the agent itself may use

# --- Service Mode -----------------------------------------------------------
# run_as_service: false           # If true, agent runs as a background service
# service_restart_on_failure: true
"@

Set-Content -Path $configPath -Value $configContent -Encoding UTF8
Write-Ok "Config written."

# --- Step 7: Create CLI wrapper script --------------------------------------
$wrapperPath = Join-Path $InstallDir "mesh-optimizer.cmd"
Write-Step "Creating CLI wrapper: $wrapperPath"

$wrapperContent = @"
@echo off
REM Mesh Optimizer CLI wrapper
REM Activates the venv and forwards all arguments to mesh-optimizer.

set "MESH_DIR=$InstallDir"
set "VENV_DIR=%MESH_DIR%\venv"

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo ERROR: venv not found at %VENV_DIR%. Please re-run the installer.
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"
mesh-optimizer --config "%MESH_DIR%\mesh_config.yaml" %*
"@

Set-Content -Path $wrapperPath -Value $wrapperContent -Encoding ASCII
Write-Ok "CLI wrapper created."

# --- Step 8: Add to user PATH -----------------------------------------------
Write-Step "Checking user PATH..."
$currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentUserPath -and $currentUserPath.Split(';') -contains $InstallDir) {
    Write-Ok "Install directory already on user PATH."
} else {
    Write-Info "Adding $InstallDir to user PATH..."
    $newPath = if ($currentUserPath) { "$currentUserPath;$InstallDir" } else { $InstallDir }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    $env:Path = "$env:Path;$InstallDir"
    Write-Ok "Added to user PATH. Open a new terminal for changes to take effect."
}

# --- Step 9: Service / Scheduled Task registration --------------------------
if (-not $SkipService) {
    Write-Host ""
    Write-Step "Service Registration"
    Write-Info "Mesh Optimizer can run as a background service so it starts on boot."
    Write-Host ""

    $registerService = Confirm-Prompt "  Install as a Windows service or scheduled task?" $false

    if ($registerService) {
        $nssmExe = Get-Command nssm -ErrorAction SilentlyContinue

        if ($nssmExe) {
            # --- NSSM service -----------------------------------------------
            Write-Step "Registering Windows service via NSSM..."
            $svcName = "MeshOptimizer"
            try {
                & nssm install $svcName $venvPython `
                    "-m" "mesh_optimizer" "--config" "`"$configPath`""
                & nssm set $svcName AppDirectory $InstallDir
                & nssm set $svcName DisplayName "Mesh Optimizer Agent"
                & nssm set $svcName Description "Mesh Optimizer performance agent"
                & nssm set $svcName Start SERVICE_DELAYED_AUTO_START
                & nssm set $svcName AppStdout (Join-Path $InstallDir "service_stdout.log")
                & nssm set $svcName AppStderr (Join-Path $InstallDir "service_stderr.log")
                & nssm set $svcName AppRotateFiles 1
                & nssm set $svcName AppRotateBytes 10485760
                Write-Ok "Service '$svcName' registered. Start with: nssm start $svcName"
            } catch {
                Write-Err "NSSM service registration failed: $_"
            }
        } else {
            # --- Scheduled Task fallback ------------------------------------
            Write-Step "NSSM not found. Creating a Scheduled Task instead..."
            Write-Info "(Install NSSM from https://nssm.cc for a proper Windows service.)"

            $taskName = "MeshOptimizer"
            try {
                $action  = New-ScheduledTaskAction `
                    -Execute $venvPython `
                    -Argument "-m mesh_optimizer --config `"$configPath`"" `
                    -WorkingDirectory $InstallDir

                $trigger = New-ScheduledTaskTrigger -AtLogon
                $settings = New-ScheduledTaskSettingsSet `
                    -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries `
                    -RestartCount 3 `
                    -RestartInterval (New-TimeSpan -Minutes 1) `
                    -ExecutionTimeLimit ([TimeSpan]::Zero)

                Register-ScheduledTask -TaskName $taskName `
                    -Action $action -Trigger $trigger -Settings $settings `
                    -Description "Mesh Optimizer performance agent" `
                    -Force | Out-Null

                Write-Ok "Scheduled task '$taskName' created (runs at logon)."
                Write-Info "Manage with: Get-ScheduledTask -TaskName $taskName"
            } catch {
                Write-Err "Scheduled task creation failed: $_"
                Write-Info "You may need to run as Administrator for task registration."
            }
        }
    } else {
        Write-Info "Skipped service registration. You can run manually with: mesh-optimizer"
    }
}

# --- Done -------------------------------------------------------------------
Write-Host ""
Write-Host "  ========================================" -ForegroundColor Green
Write-Host "   Installation Complete!" -ForegroundColor Green
Write-Host "  ========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Install directory : " -NoNewline; Write-Host $InstallDir -ForegroundColor Cyan
Write-Host "  Config file       : " -NoNewline; Write-Host $configPath -ForegroundColor Cyan
Write-Host "  CLI wrapper       : " -NoNewline; Write-Host $wrapperPath -ForegroundColor Cyan
Write-Host "  Python venv       : " -NoNewline; Write-Host $venvDir -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Edit $configPath with your controller URL and API key." -ForegroundColor DarkGray
Write-Host "    2. Open a new terminal and run: " -ForegroundColor DarkGray -NoNewline
Write-Host "mesh-optimizer" -ForegroundColor Yellow
Write-Host "    3. Or run directly: " -ForegroundColor DarkGray -NoNewline
Write-Host "$wrapperPath" -ForegroundColor Yellow
Write-Host ""
Write-Host "  To uninstall, run: " -ForegroundColor DarkGray -NoNewline
Write-Host ".\uninstall.ps1" -ForegroundColor Yellow
Write-Host ""
