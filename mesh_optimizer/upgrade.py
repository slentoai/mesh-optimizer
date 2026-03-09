"""Auto-upgrade: download and install the controller binary for paid tiers.

When a Professional or Enterprise license is detected, this module downloads
the pre-compiled controller binary from the Slento Systems portal and installs
it locally. The agent can then start the controller alongside itself.
"""
from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# Where the controller binary lives
INSTALL_DIR = Path.home() / ".mesh-optimizer"
CONTROLLER_BIN = INSTALL_DIR / "mesh-controller"
VERSION_FILE = INSTALL_DIR / ".controller-version"

# Portal download API
DOWNLOAD_API = "/api/v1/releases/controller"


def get_platform_tag() -> str:
    """Return platform tag for binary download: linux-x86_64, linux-aarch64, darwin-x86_64, etc."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    # Normalize
    if machine in ("x86_64", "amd64"):
        machine = "x86_64"
    elif machine in ("aarch64", "arm64"):
        machine = "aarch64"
    return f"{system}-{machine}"


def controller_installed() -> bool:
    """Check if the controller binary exists and is executable."""
    return CONTROLLER_BIN.exists() and os.access(CONTROLLER_BIN, os.X_OK)


def controller_version() -> str | None:
    """Return the installed controller version, or None."""
    if not VERSION_FILE.exists():
        return None
    return VERSION_FILE.read_text().strip()


def check_upgrade(portal_url: str, license_key: str) -> dict | None:
    """Check if a controller upgrade is available.

    Returns release info dict if an upgrade is available, None otherwise.
    """
    import json
    import urllib.parse

    try:
        url = f"{portal_url}{DOWNLOAD_API}/check"
        params = urllib.parse.urlencode({
            "key": license_key,
            "platform": get_platform_tag(),
            "current_version": controller_version() or "0.0.0",
        })
        req = urllib.request.Request(f"{url}?{params}", method="GET")
        req.add_header("User-Agent", "mesh-optimizer-agent")
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())

        if data.get("upgrade_available"):
            return data
        return None
    except Exception as e:
        logger.debug("Upgrade check failed: %s", e)
        return None


def download_controller(portal_url: str, license_key: str) -> bool:
    """Download and install the controller binary.

    Returns True if successful, False otherwise.
    """
    platform_tag = get_platform_tag()
    url = f"{portal_url}{DOWNLOAD_API}/download?key={license_key}&platform={platform_tag}"

    logger.info("Downloading controller binary for %s...", platform_tag)

    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "mesh-optimizer-agent")

        resp = urllib.request.urlopen(req, timeout=300)  # 5 min timeout for large binary

        # Read response headers
        content_length = resp.headers.get("Content-Length")
        expected_sha256 = resp.headers.get("X-SHA256")
        version = resp.headers.get("X-Version", "unknown")

        INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        # Download to temp file first
        tmp_fd, tmp_path = tempfile.mkstemp(dir=INSTALL_DIR, prefix=".controller-dl-")
        try:
            hasher = hashlib.sha256()
            total = 0
            with os.fdopen(tmp_fd, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    total += len(chunk)

            # Verify checksum if provided
            if expected_sha256:
                actual_sha256 = hasher.hexdigest()
                if actual_sha256 != expected_sha256:
                    logger.error(
                        "SHA256 mismatch: expected %s, got %s",
                        expected_sha256, actual_sha256,
                    )
                    os.unlink(tmp_path)
                    return False

            # Make executable
            os.chmod(tmp_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)

            # Atomic replace
            shutil.move(tmp_path, str(CONTROLLER_BIN))

            # Write version
            VERSION_FILE.write_text(version)

            logger.info(
                "Controller v%s installed (%s bytes) at %s",
                version, total, CONTROLLER_BIN,
            )
            return True

        except Exception:
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.warning("License does not include controller access")
        elif e.code == 404:
            logger.warning("No controller binary available for platform %s", platform_tag)
        else:
            logger.error("Download failed (HTTP %d)", e.code)
        return False
    except Exception as e:
        logger.error("Controller download failed: %s", e)
        return False


def start_controller(config_path: str | Path = "") -> subprocess.Popen | None:
    """Start the controller binary as a subprocess.

    Returns the Popen handle, or None if the controller isn't installed.
    """
    if not controller_installed():
        return None

    cmd = [str(CONTROLLER_BIN)]
    if config_path:
        cmd.extend(["--config", str(config_path)])

    logger.info("Starting controller: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from agent process
        )
        logger.info("Controller started (PID %d)", proc.pid)
        return proc
    except Exception as e:
        logger.error("Failed to start controller: %s", e)
        return None


def maybe_upgrade_and_start(portal_url: str, license_key: str, tier: str,
                            config_path: str = "") -> subprocess.Popen | None:
    """Full upgrade flow: check license tier, download if needed, start controller.

    Called from the CLI after license validation.
    Returns the controller process handle, or None.
    """
    if tier not in ("professional", "enterprise"):
        logger.debug("Community tier — controller not included")
        return None

    # Check if already installed and up to date
    if controller_installed():
        release = check_upgrade(portal_url, license_key)
        if release:
            logger.info(
                "Controller upgrade available: %s -> %s",
                controller_version(), release.get("version"),
            )
            download_controller(portal_url, license_key)
        else:
            logger.info("Controller v%s is up to date", controller_version())
    else:
        # First install
        logger.info("Professional/Enterprise license detected — installing controller...")
        if not download_controller(portal_url, license_key):
            logger.warning("Controller download failed — agent will run standalone")
            return None

    # Start it
    return start_controller(config_path)
