"""Auto-discovery of CPU, GPU, FPGA, and memory hardware."""
from __future__ import annotations

import logging
import multiprocessing
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

import psutil

from mesh_optimizer.models import CPUInfo, FPGAInfo, GPUInfo, HardwareInventory

logger = logging.getLogger(__name__)


def scan_hardware(hostname_override: str = "") -> HardwareInventory:
    """Detect all hardware on this machine and return an inventory."""
    inv = HardwareInventory(
        hostname=hostname_override or platform.node(),
        platform=platform.system(),
        memory_total_mb=psutil.virtual_memory().total // (1024 * 1024),
    )
    inv.cpu = _detect_cpu()
    inv.gpus = _detect_gpus()
    inv.fpgas = _detect_fpgas()
    inv.has_pytorch = _check_pytorch()
    inv.has_rocm = shutil.which("rocm-smi") is not None
    inv.has_cuda = shutil.which("nvidia-smi") is not None
    return inv


# -- CPU Detection ------------------------------------------------------------

def _detect_cpu() -> CPUInfo:
    info = CPUInfo(cores=multiprocessing.cpu_count() or 1)

    # Model name
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        info.model = line.split(":")[1].strip()
                        break
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            info.model = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            pass

    # Detailed info from lscpu (Linux)
    try:
        out = subprocess.check_output("lscpu", text=True, stderr=subprocess.DEVNULL)
        cores_per = 1
        sockets = 1
        for line in out.splitlines():
            if "Core(s) per socket:" in line:
                cores_per = int(line.split(":")[1].strip())
            elif "Socket(s):" in line:
                sockets = int(line.split(":")[1].strip())
            elif "Thread(s) per core:" in line:
                info.threads_per_core = int(line.split(":")[1].strip())
            elif "NUMA node(s):" in line:
                info.numa_nodes = int(line.split(":")[1].strip())
            elif "CPU max MHz:" in line:
                info.freq_mhz = float(line.split(":")[1].strip())
            elif "CPU MHz:" in line and info.freq_mhz == 0:
                info.freq_mhz = float(line.split(":")[1].strip())
        info.physical_cores = cores_per * sockets
    except Exception:
        info.physical_cores = info.cores // 2

    # Cache sizes from sysfs
    try:
        for idx in range(10):
            cache_dir = f"/sys/devices/system/cpu/cpu0/cache/index{idx}"
            if not os.path.exists(cache_dir):
                break
            level = open(f"{cache_dir}/level").read().strip()
            ctype = open(f"{cache_dir}/type").read().strip()
            size_str = open(f"{cache_dir}/size").read().strip()
            size_kb = int(re.match(r"(\d+)", size_str).group(1))
            if "M" in size_str:
                size_kb *= 1024
            if level == "1" and "Data" in ctype:
                info.l1d_kb = size_kb
            elif level == "2":
                info.l2_kb = size_kb
            elif level == "3":
                info.l3_kb = size_kb
    except Exception:
        pass

    return info


# -- GPU Detection ------------------------------------------------------------

def _detect_gpus() -> list[GPUInfo]:
    gpus = []
    gpus.extend(_detect_nvidia_gpus())
    gpus.extend(_detect_amd_gpus())
    return gpus


def _detect_nvidia_gpus() -> list[GPUInfo]:
    gpus = []
    if not shutil.which("nvidia-smi"):
        return gpus
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,temperature.gpu,"
                "utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                gpus.append(GPUInfo(
                    name=parts[0],
                    vendor="nvidia",
                    vram_mb=int(float(parts[1])),
                    driver=parts[2],
                    temperature_c=float(parts[3]) if parts[3] != "N/A" else 0.0,
                    utilization_pct=float(parts[4]) if parts[4] != "N/A" else 0.0,
                    memory_used_mb=int(float(parts[5])) if parts[5] != "N/A" else 0,
                ))
    except Exception as e:
        logger.warning("nvidia-smi failed: %s", e)
    return gpus


def _detect_amd_gpus() -> list[GPUInfo]:
    gpus = []
    if not shutil.which("rocm-smi"):
        return gpus
    try:
        out = subprocess.check_output(
            [
                "rocm-smi", "--showproductname", "--showmeminfo", "vram",
                "--showtemp", "--showuse", "--json",
            ],
            text=True, stderr=subprocess.DEVNULL, timeout=10,
        )
        import json
        data = json.loads(out)
        for key, val in data.items():
            if not key.startswith("card"):
                continue
            gpu = GPUInfo(vendor="amd")
            gpu.name = val.get("Card Series", val.get("Card series", "AMD GPU"))
            vram_total = val.get("VRAM Total Memory (B)", 0)
            if vram_total:
                gpu.vram_mb = int(vram_total) // (1024 * 1024)
            vram_used = val.get("VRAM Total Used Memory (B)", 0)
            if vram_used:
                gpu.memory_used_mb = int(vram_used) // (1024 * 1024)
            temp = val.get("Temperature (Sensor edge) (C)", 0)
            if temp:
                gpu.temperature_c = float(temp)
            use = val.get("GPU use (%)", 0)
            if use:
                gpu.utilization_pct = float(use)
            gpus.append(gpu)
    except Exception as e:
        logger.warning("rocm-smi failed: %s", e)

    # Fallback: sysfs DRM
    if not gpus:
        try:
            drm = Path("/sys/class/drm")
            for card in sorted(drm.glob("card[0-9]*")):
                device = card / "device"
                vendor_file = device / "vendor"
                if vendor_file.exists():
                    vendor_id = vendor_file.read_text().strip()
                    if vendor_id == "0x1002":  # AMD
                        name = "AMD GPU"
                        product = device / "product_name"
                        if product.exists():
                            name = product.read_text().strip()
                        vram = 0
                        mem_file = device / "mem_info_vram_total"
                        if mem_file.exists():
                            vram = int(mem_file.read_text().strip()) // (1024 * 1024)
                        gpus.append(GPUInfo(name=name, vendor="amd", vram_mb=vram))
        except Exception:
            pass

    return gpus


# -- FPGA Detection -----------------------------------------------------------

def _detect_fpgas() -> list[FPGAInfo]:
    fpgas = []
    try:
        out = subprocess.check_output(
            ["lspci", "-nn"], text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
        for line in out.splitlines():
            lower = line.lower()
            if "xilinx" in lower or "altera" in lower or "intel.*fpga" in lower:
                bdf = line.split()[0]
                vendor = "xilinx" if "xilinx" in lower else "altera/intel"
                name = line.split(": ", 1)[1] if ": " in line else "FPGA"
                driver = ""
                driver_link = Path(f"/sys/bus/pci/devices/0000:{bdf}/driver")
                if driver_link.is_symlink():
                    driver = driver_link.resolve().name
                fpgas.append(FPGAInfo(
                    name=name, vendor=vendor, pcie_bdf=bdf, driver=driver,
                ))
    except Exception:
        pass
    return fpgas


# -- Utility ------------------------------------------------------------------

def _check_pytorch() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False
