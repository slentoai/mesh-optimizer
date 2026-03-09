"""Tests for hardware scanner — works on any machine."""
from __future__ import annotations

from mesh_optimizer.agent.hardware_scanner import scan_hardware
from mesh_optimizer.models import HardwareInventory, CPUInfo


def test_scan_returns_inventory():
    """scan_hardware returns a valid HardwareInventory."""
    inv = scan_hardware()
    assert isinstance(inv, HardwareInventory)
    assert inv.hostname != ""
    assert inv.platform != ""
    assert inv.memory_total_mb > 0


def test_cpu_detected():
    """CPU info is populated."""
    inv = scan_hardware()
    assert isinstance(inv.cpu, CPUInfo)
    assert inv.cpu.cores > 0
    assert inv.cpu.physical_cores > 0


def test_hostname_override():
    """Hostname override is respected."""
    inv = scan_hardware(hostname_override="test-host-123")
    assert inv.hostname == "test-host-123"


def test_gpus_is_list():
    """GPUs field is always a list (may be empty if no GPU)."""
    inv = scan_hardware()
    assert isinstance(inv.gpus, list)


def test_fpgas_is_list():
    """FPGAs field is always a list."""
    inv = scan_hardware()
    assert isinstance(inv.fpgas, list)


def test_pytorch_detection():
    """has_pytorch is a boolean."""
    inv = scan_hardware()
    assert isinstance(inv.has_pytorch, bool)
