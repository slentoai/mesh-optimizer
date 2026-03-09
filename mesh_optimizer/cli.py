"""CLI entry point for the mesh-optimizer agent.

Usage:
    mesh-optimizer start [--config CONFIG] [--port PORT]
    mesh-optimizer stop
    mesh-optimizer status
    mesh-optimizer hardware
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

DEFAULT_CONFIG = "mesh_config.yaml"
PID_FILE = "/tmp/mesh-optimizer-agent.pid"


def main():
    parser = argparse.ArgumentParser(
        prog="mesh-optimizer",
        description="Mesh Optimizer agent — distributed hardware optimization",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # start
    start_p = sub.add_parser("start", help="Start the agent daemon")
    start_p.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config YAML")
    start_p.add_argument("--port", type=int, default=None, help="Override agent port")
    start_p.add_argument("--log-level", default=None, help="Log level (DEBUG, INFO, WARNING)")

    # stop
    sub.add_parser("stop", help="Stop the running agent daemon")

    # status
    sub.add_parser("status", help="Show agent status")

    # hardware
    sub.add_parser("hardware", help="Show detected hardware")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "start":
        _cmd_start(args)
    elif args.command == "stop":
        _cmd_stop()
    elif args.command == "status":
        _cmd_status()
    elif args.command == "hardware":
        _cmd_hardware()


def _cmd_start(args):
    """Start the agent: load config, wire components, run."""
    from mesh_optimizer.config import AgentConfig
    from mesh_optimizer.agent.node_agent import NodeAgent
    from mesh_optimizer.api.agent_api import app, wire_agent

    config = AgentConfig.from_yaml(args.config)
    if args.port:
        config.node.agent_port = args.port
    if args.log_level:
        config.log_level = args.log_level

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("mesh_optimizer")

    # Validate license and get tier info
    tier = _validate_license(config, logger)

    # Auto-download and start controller for paid tiers
    controller_proc = None
    if tier in ("professional", "enterprise"):
        from mesh_optimizer.upgrade import maybe_upgrade_and_start
        controller_proc = maybe_upgrade_and_start(
            portal_url=config.licensing.portal_url,
            license_key=config.licensing.license_key,
            tier=tier,
            config_path=args.config,
        )
        if controller_proc:
            logger.info("Controller running (PID %d)", controller_proc.pid)
            # Point agent at local controller if no external one configured
            if "example.com" in config.controller_url:
                config.controller_url = "http://127.0.0.1:8401"
                logger.info("Using local controller at %s", config.controller_url)

    # Create agent and wire into the API app
    agent = NodeAgent(config)
    wire_agent(node_agent=agent)

    # Write PID file
    Path(PID_FILE).write_text(str(os.getpid()))

    # Signal handlers for graceful shutdown
    def _handle_signal(signum, frame):
        logger.info("Received signal %d, shutting down", signum)
        if controller_proc and controller_proc.poll() is None:
            logger.info("Stopping controller (PID %d)", controller_proc.pid)
            controller_proc.terminate()
            controller_proc.wait(timeout=10)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if config.node.nat_mode:
        logger.info("NAT mode enabled — no API server, agent will push/poll only")
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(agent.start())
        except (KeyboardInterrupt, SystemExit):
            loop.run_until_complete(agent.stop())
        finally:
            _cleanup_pid()
            loop.close()
    else:
        import uvicorn
        logger.info("Starting agent on %s:%d", config.node.host, config.node.agent_port)
        try:
            uvicorn.run(
                app,
                host=config.node.host,
                port=config.node.agent_port,
                log_level=config.log_level.lower(),
            )
        finally:
            if controller_proc and controller_proc.poll() is None:
                controller_proc.terminate()
            _cleanup_pid()


def _cmd_stop():
    """Stop the running agent by sending SIGTERM to the PID."""
    pid_path = Path(PID_FILE)
    if not pid_path.exists():
        print("Agent is not running (no PID file found)")
        sys.exit(1)

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to agent (PID {pid})")
        pid_path.unlink(missing_ok=True)
    except ProcessLookupError:
        print(f"Agent process {pid} not found — cleaning up PID file")
        pid_path.unlink(missing_ok=True)
    except PermissionError:
        print(f"Permission denied sending signal to PID {pid}")
        sys.exit(1)


def _cmd_status():
    """Check if the agent is running and optionally query its health endpoint."""
    pid_path = Path(PID_FILE)
    if not pid_path.exists():
        print("Agent is not running (no PID file)")
        return

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, 0)  # Check if process exists
        print(f"Agent is running (PID {pid})")
    except ProcessLookupError:
        print(f"Agent PID {pid} is stale — process not found")
        pid_path.unlink(missing_ok=True)
        return

    # Try to hit the health endpoint
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:8400/health", timeout=3)
        data = json.loads(resp.read())
        print(json.dumps(data, indent=2))
    except Exception:
        print("  (could not reach health endpoint — agent may be in NAT mode)")


def _cmd_hardware():
    """Detect and display local hardware."""
    from mesh_optimizer.agent.hardware_scanner import scan_hardware
    inv = scan_hardware()
    print(json.dumps(inv.model_dump(), indent=2))


def _validate_license(config, logger) -> str:
    """Validate license key against the portal API. Returns the tier name."""
    if not config.licensing.license_key:
        logger.info("No license key configured — running in community mode")
        return "community"

    try:
        import urllib.request
        import urllib.error

        url = f"{config.licensing.portal_url}/api/validate.php"
        post_data = json.dumps({
            "license_key": config.licensing.license_key,
        }).encode()
        req = urllib.request.Request(url, data=post_data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "mesh-optimizer-agent/0.1.0")

        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("valid"):
            tier = data.get("tier", "community")
            logger.info(
                "License validated: tier=%s, max_nodes=%s, expires=%s",
                tier,
                data.get("max_nodes", "unlimited"),
                data.get("expires_at", "never"),
            )
            return tier
        else:
            logger.warning("License key is not valid — running in community mode")
            return "community"
    except urllib.error.HTTPError as e:
        logger.warning("License validation failed (HTTP %d) — running in community mode", e.code)
        return "community"
    except Exception as e:
        logger.warning("Could not reach license server: %s — running in community mode", e)
        return "community"


def _cleanup_pid():
    Path(PID_FILE).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
