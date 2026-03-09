"""Tests for AgentConfig YAML loading and defaults."""
from __future__ import annotations

import tempfile
from pathlib import Path

from mesh_optimizer.config import AgentConfig


def test_default_config():
    """Default config has sane values."""
    cfg = AgentConfig()
    assert cfg.controller_url == "https://controller.example.com:8401"
    assert cfg.node.agent_port == 8400
    assert cfg.log_level == "INFO"
    assert cfg.node.nat_mode is False


def test_from_yaml_missing_file():
    """Loading a nonexistent file returns defaults."""
    cfg = AgentConfig.from_yaml("/tmp/nonexistent_mesh_config_test.yaml")
    assert cfg.controller_url == "https://controller.example.com:8401"


def test_from_yaml_roundtrip():
    """Config can be saved to YAML and reloaded."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        path = f.name

    cfg = AgentConfig()
    cfg.controller_url = "https://my-controller:9000"
    cfg.node.name = "test-node"
    cfg.node.tags = ["gpu", "amd"]
    cfg.node.nat_mode = True
    cfg.licensing.license_key = "TEST-KEY-123"
    cfg.to_yaml(path)

    loaded = AgentConfig.from_yaml(path)
    assert loaded.controller_url == "https://my-controller:9000"
    assert loaded.node.name == "test-node"
    assert loaded.node.tags == ["gpu", "amd"]
    assert loaded.node.nat_mode is True
    assert loaded.licensing.license_key == "TEST-KEY-123"

    Path(path).unlink(missing_ok=True)


def test_get_node_name_auto():
    """get_node_name falls back to hostname when name is empty."""
    import socket
    cfg = AgentConfig()
    assert cfg.get_node_name() == socket.gethostname()


def test_get_node_name_override():
    """get_node_name returns the configured name."""
    cfg = AgentConfig()
    cfg.node.name = "my-node"
    assert cfg.get_node_name() == "my-node"


def test_auth_secret_generated():
    """Auth secret is auto-generated if empty."""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
        f.write("controller_url: http://localhost:8401\n")
        path = f.name

    cfg = AgentConfig.from_yaml(path)
    assert len(cfg.security.auth_secret) == 64  # 32 bytes hex

    Path(path).unlink(missing_ok=True)
