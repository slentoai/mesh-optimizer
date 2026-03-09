#Requires -Version 5.1
<#
.SYNOPSIS
    Mesh Optimizer Uninstaller for Windows
.DESCRIPTION
    Removes the mesh-optimizer agent: install directory, user PATH entry,
    Windows service (NSSM) or scheduled task.
.PARAMETER Silent
    Run in unattended mode — no confirmation prompts.
.PARAMETER InstallDir
    Override the default install directory ($env:LOCALAPPDATA\MeshOptimizer).
.PARAMETER KeepConfig
    Preserve the mesh_config.yaml file (copies it to Desktop before removal).
.EXAMPLE
    .\uninstall.ps1
    .\uninstall.ps1 -Silent
    .\uninstall.ps1 -Silent -KeepConfig
#>
[CmdletBinding()]
param(
    [switch]$Silent,
    [string]$InstallDir = "$env:LOCALAPPDATA\MeshOptimizer",
    [switch]$KeepConfig
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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

function Test-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  Mesh Optimizer Uninstaller" -ForegroundColor Cyan
Write-Host "  =========================`n" -ForegroundColor DarkGray

if (-not (Test-Path $InstallDir)) {
    Write-Warn "Install directory not found: $InstallDir"
    Write-Info "Nothing to uninstall."
    exit 0
}

if (-not (Confirm-Prompt "Remove Mesh Optimizer from $InstallDir?" $true)) {
    Write-Info "Cancelled."
    exit 0
}

$errors = 0

# --- Step 1: Stop and remove service / scheduled task -----------------------
Write-Step "Checking for registered service or scheduled task..."

$svcName  = "MeshOptimizer"
$taskName = "MeshOptimizer"

# NSSM service
$nssmExe = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssmExe) {
    try {
        $status = & nssm status $svcName 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Stopping NSSM service '$svcName'..."
            & nssm stop $svcName 2>&1 | Out-Null
            Start-Sleep -Seconds 2
            Write-Info "Removing NSSM service '$svcName'..."
            & nssm remove $svcName confirm 2>&1 | Out-Null
            Write-Ok "NSSM service removed."
        }
    } catch {
        Write-Info "No NSSM service found (this is fine)."
    }
}

# Scheduled task
try {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Write-Info "Removing scheduled task '$taskName'..."
        if ($task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        }
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Ok "Scheduled task removed."
    } else {
        Write-Info "No scheduled task found."
    }
} catch {
    Write-Info "No scheduled task found."
}

# --- Step 2: Backup config if requested ------------------------------------
if ($KeepConfig) {
    $configSrc = Join-Path $InstallDir "mesh_config.yaml"
    if (Test-Path $configSrc) {
        $backupDst = Join-Path ([Environment]::GetFolderPath("Desktop")) "mesh_config_backup.yaml"
        Write-Step "Backing up config to $backupDst"
        try {
            Copy-Item -Path $configSrc -Destination $backupDst -Force
            Write-Ok "Config backed up."
        } catch {
            Write-Warn "Could not back up config: $_"
            $errors++
        }
    }
}

# --- Step 3: Remove user PATH entry ----------------------------------------
Write-Step "Removing install directory from user PATH..."
try {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $parts   = $userPath.Split(';') | Where-Object { $_ -ne $InstallDir -and $_ -ne "" }
        $newPath = $parts -join ';'
        if ($newPath -ne $userPath) {
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            Write-Ok "Removed from user PATH."
        } else {
            Write-Info "Install directory was not on user PATH."
        }
    }
} catch {
    Write-Warn "Could not modify user PATH: $_"
    Write-Info "You may need to remove '$InstallDir' from PATH manually."
    $errors++
}

# --- Step 4: Remove install directory ---------------------------------------
Write-Step "Removing install directory: $InstallDir"
try {
    # Kill any running mesh-optimizer processes using files in the install dir
    $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) }
        catch { $false }
    }
    if ($procs) {
        Write-Info "Stopping $($procs.Count) running mesh-optimizer process(es)..."
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Ok "Install directory removed."
} catch {
    Write-Err "Failed to remove $InstallDir : $_"
    Write-Info "Some files may be locked. Close any terminals using mesh-optimizer and retry."
    $errors++
}

# --- Done -------------------------------------------------------------------
Write-Host ""
if ($errors -eq 0) {
    Write-Host "  ========================================" -ForegroundColor Green
    Write-Host "   Uninstall Complete!" -ForegroundColor Green
    Write-Host "  ========================================" -ForegroundColor Green
} else {
    Write-Host "  ========================================" -ForegroundColor Yellow
    Write-Host "   Uninstall finished with $errors warning(s)" -ForegroundColor Yellow
    Write-Host "  ========================================" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  Mesh Optimizer has been removed from this system." -ForegroundColor DarkGray
if ($KeepConfig) {
    Write-Host "  Your config was saved to your Desktop." -ForegroundColor DarkGray
}
Write-Host "  Open a new terminal for PATH changes to take effect." -ForegroundColor DarkGray
Write-Host ""
