"""Agent configuration — YAML-backed dataclass."""
from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class NodeConfig:
    """Configuration for this agent node."""
    name: str = ""
    host: str = "0.0.0.0"
    agent_port: int = 8400
    tags: List[str] = field(default_factory=list)

    # WAN support: the URL the controller should use to reach this agent.
    # Can be a public IP, domain, or reverse proxy URL.
    # If empty, falls back to auto-detected local hostname + agent_port.
    public_url: str = ""

    # NAT-friendly mode: when True, the agent does NOT start its own API server.
    # It only pushes heartbeats to the controller and polls for job assignments.
    # This allows agents behind NAT/firewalls to participate without port forwarding.
    nat_mode: bool = False

    # How often (seconds) to poll the controller for pending jobs in NAT mode.
    nat_poll_interval_s: float = 15.0

    # Community atlas sharing: when True, anonymized probe results (kernel throughput,
    # optimal block sizes, memory bandwidth, etc.) are periodically sent to the Slento
    # Systems community hub. This data is aggregated across all opted-in users to
    # improve the global JEPA model, which benefits everyone.
    #
    # What IS shared: hardware class (e.g. "RDNA3", "Ampere"), kernel performance
    #   numbers, optimal parameter values, and invariant boundaries.
    # What is NOT shared: hostnames, IP addresses, file paths, job commands,
    #   or any data that could identify you or your workloads.
    #
    # Set to False to opt out. Your mesh will still work perfectly — it just won't
    # contribute to (or benefit from) the community model improvements.
    share_atlas_data: bool = True


@dataclass
class ControllerConfig:
    """Settings for communicating with the controller."""
    heartbeat_interval_s: float = 10.0
    node_timeout_s: float = 120.0
    probe_interval_s: float = 21600.0  # 6 hours


@dataclass
class SecurityConfig:
    """Authentication and TLS settings."""
    auth_secret: str = ""
    require_auth: bool = False
    token_ttl_hours: int = 24
    rate_limit: int = 100
    tls_cert_path: str = ""
    tls_key_path: str = ""
    tls_ca_path: str = ""
    verify_tls: bool = True


@dataclass
class LicensingConfig:
    """License validation settings."""
    license_key: str = ""
    portal_url: str = "https://portal.slentosystems.com"


@dataclass
class AgentConfig:
    """Top-level agent configuration."""
    controller_url: str = "https://controller.example.com:8401"
    node: NodeConfig = field(default_factory=NodeConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    licensing: LicensingConfig = field(default_factory=LicensingConfig)
    log_level: str = "INFO"
    data_dir: str = "data"

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentConfig:
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

        cfg = cls()
        for simple_key in ("controller_url", "log_level", "data_dir"):
            if simple_key in raw:
                setattr(cfg, simple_key, raw[simple_key])

        section_map = {
            "node": cfg.node,
            "controller": cfg.controller,
            "security": cfg.security,
            "licensing": cfg.licensing,
        }
        for section_name, section_obj in section_map.items():
            if section_name in raw and isinstance(raw[section_name], dict):
                for k, v in raw[section_name].items():
                    if hasattr(section_obj, k):
                        setattr(section_obj, k, v)

        # Generate a random auth secret if none provided
        if not cfg.security.auth_secret:
            import secrets as _secrets
            cfg.security.auth_secret = _secrets.token_hex(32)

        return cfg

    def to_yaml(self, path: str | Path) -> None:
        """Save configuration to a YAML file."""
        import dataclasses
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dataclasses.asdict(self)
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    def get_node_name(self) -> str:
        """Return the configured node name, or the system hostname."""
        if self.node.name:
            return self.node.name
        return socket.gethostname()
