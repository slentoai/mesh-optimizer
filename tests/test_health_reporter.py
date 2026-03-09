"""Tests for health reporter."""
from __future__ import annotations

from mesh_optimizer.agent.health_reporter import collect_health
from mesh_optimizer.models import HealthSnapshot


def test_collect_health_returns_snapshot():
    """collect_health returns a valid HealthSnapshot."""
    snap = collect_health()
    assert isinstance(snap, HealthSnapshot)


def test_cpu_metrics():
    """CPU metrics are populated."""
    snap = collect_health()
    assert 0 <= snap.cpu_pct <= 100
    assert snap.memory_used_pct > 0
    assert snap.memory_available_mb > 0


def test_load_averages():
    """Load averages are non-negative."""
    snap = collect_health()
    assert snap.load_1m >= 0
    assert snap.load_5m >= 0
    assert snap.load_15m >= 0


def test_uptime():
    """Uptime is positive."""
    snap = collect_health()
    assert snap.uptime_s > 0


def test_disk_usage():
    """Disk usage is between 0 and 100."""
    snap = collect_health()
    assert 0 <= snap.disk_used_pct <= 100


def test_network_counters():
    """Network counters are non-negative."""
    snap = collect_health()
    assert snap.net_bytes_sent >= 0
    assert snap.net_bytes_recv >= 0


def test_gpu_metrics_are_lists():
    """GPU metric fields are always lists."""
    snap = collect_health()
    assert isinstance(snap.gpu_utilizations, list)
    assert isinstance(snap.gpu_temperatures, list)
    assert isinstance(snap.gpu_memory_used_mb, list)
