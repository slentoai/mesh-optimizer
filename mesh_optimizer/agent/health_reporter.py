"""Cross-platform health metrics collection using psutil."""
from __future__ import annotations

import logging
import time
from datetime import datetime

import psutil

from mesh_optimizer.models import HealthSnapshot

logger = logging.getLogger(__name__)

_boot_time = psutil.boot_time()


def collect_health() -> HealthSnapshot:
    """Collect a health snapshot of the current machine."""
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    load = psutil.getloadavg()
    freq = psutil.cpu_freq()

    snap = HealthSnapshot(
        timestamp=datetime.utcnow(),
        cpu_pct=psutil.cpu_percent(interval=0.1),
        cpu_freq_mhz=freq.current if freq else 0.0,
        memory_used_pct=vm.percent,
        memory_available_mb=vm.available // (1024 * 1024),
        disk_used_pct=disk.percent,
        load_1m=load[0],
        load_5m=load[1],
        load_15m=load[2],
        net_bytes_sent=net.bytes_sent,
        net_bytes_recv=net.bytes_recv,
        uptime_s=time.time() - _boot_time,
    )

    # GPU metrics
    snap.gpu_utilizations, snap.gpu_temperatures, snap.gpu_memory_used_mb = (
        _gpu_metrics()
    )

    return snap


def _gpu_metrics() -> tuple[list[float], list[float], list[int]]:
    """Collect GPU utilization, temperature, and memory usage."""
    utils: list[float] = []
    temps: list[float] = []
    mems: list[int] = []

    # NVIDIA GPUs
    try:
        import subprocess
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,temperature.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                utils.append(float(parts[0]) if parts[0] != "N/A" else 0.0)
                temps.append(float(parts[1]) if parts[1] != "N/A" else 0.0)
                mems.append(int(float(parts[2])) if parts[2] != "N/A" else 0)
    except Exception:
        pass

    # AMD GPUs
    try:
        import json
        import subprocess
        out = subprocess.check_output(
            ["rocm-smi", "--showuse", "--showtemp", "--showmeminfo", "vram", "--json"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
        data = json.loads(out)
        for key in sorted(data.keys()):
            if not key.startswith("card"):
                continue
            val = data[key]
            use = val.get("GPU use (%)", 0)
            utils.append(float(use) if use else 0.0)
            temp = val.get("Temperature (Sensor edge) (C)", 0)
            temps.append(float(temp) if temp else 0.0)
            vram_used = val.get("VRAM Total Used Memory (B)", 0)
            mems.append(int(vram_used) // (1024 * 1024) if vram_used else 0)
    except Exception:
        pass

    return utils, temps, mems
