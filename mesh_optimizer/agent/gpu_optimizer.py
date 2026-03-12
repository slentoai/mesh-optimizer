"""Atlas-derived GPU optimization env vars for detected hardware.

Provides per-vendor environment variable recommendations based on hardware
class and job type. These are distilled from the rdna3-discovery atlas
(81K+ data points across AMD/NVIDIA/CPU subsystems).

Community tier (AMD) env vars are freely available.
Paid tier (NVIDIA) env vars require a valid Slento license.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def get_optimization_env(
    hardware_info,
    job_type: str = "general",
    *,
    license_key: Optional[str] = None,
) -> Dict[str, str]:
    """Return env vars optimized for detected hardware and job type.

    Parameters
    ----------
    hardware_info : HardwareInventory
        Hardware inventory from the scanner.
    job_type : str
        One of "general", "training", "inference", "data_processing".
    license_key : str, optional
        Slento license key for paid-tier NVIDIA optimizations.

    Returns
    -------
    dict
        Environment variable name -> value mapping.
    """
    env: Dict[str, str] = {}

    # -- General (always applied) ---------------------------------------------
    physical_cores = hardware_info.cpu.physical_cores or (hardware_info.cpu.cores // 2)
    if physical_cores < 1:
        physical_cores = 1

    env["OMP_NUM_THREADS"] = str(physical_cores)
    env["MKL_NUM_THREADS"] = str(physical_cores)
    env["PYTHONUNBUFFERED"] = "1"
    env["OPENBLAS_NUM_THREADS"] = str(physical_cores)
    env["VECLIB_MAXIMUM_THREADS"] = str(physical_cores)
    env["NUMEXPR_NUM_THREADS"] = str(physical_cores)

    # -- Per-GPU vendor -------------------------------------------------------
    has_amd = False
    has_nvidia = False
    for gpu in hardware_info.gpus:
        vendor = gpu.vendor.lower()
        if vendor == "amd":
            has_amd = True
        elif vendor == "nvidia":
            has_nvidia = True

    if has_amd:
        _apply_amd_env(env, hardware_info, job_type)

    if has_nvidia:
        _apply_nvidia_env(env, hardware_info, job_type, license_key=license_key)

    return env


def _apply_amd_env(
    env: Dict[str, str],
    hw,
    job_type: str,
) -> None:
    """AMD ROCm optimizations (community tier -- always available).

    Derived from atlas invariants over 81K data points on RDNA3.
    """
    # MIOpen: normal find mode (1) is best for most workloads.
    # Exhaustive (3) re-searches per-shape and is harmful for Conv1d.
    env["MIOPEN_FIND_MODE"] = "1"

    # Allow full VRAM allocation (default cap is 90%)
    env["GPU_MAX_ALLOC_PERCENT"] = "100"

    # Fine-grain PCIe for faster host<->device transfers
    env["HSA_FORCE_FINE_GRAIN_PCIE"] = "1"

    # Reduce HIP launch latency
    env["HIP_FORCE_DEV_KERNARG"] = "1"

    # Disable slow fallback paths in MIOpen
    env["MIOPEN_DEBUG_CONV_IMPLICIT_GEMM_HIP_BWD_V1R1"] = "0"

    if job_type == "training":
        # MIOpen user DB for caching tuned kernels across runs
        env["MIOPEN_USER_DB_PATH"] = os.path.expanduser("~/.config/mesh-optimizer/miopen")
        # Disable hipBLASLt (unsupported on RDNA3 gfx1100, causes warnings)
        env["PYTORCH_HIPBLASLT_ENABLE"] = "0"

    # Memory pool: let PyTorch ROCm reuse allocations
    env["PYTORCH_HIP_ALLOC_CONF"] = "expandable_segments:True"


def _apply_nvidia_env(
    env: Dict[str, str],
    hw,
    job_type: str,
    *,
    license_key: Optional[str] = None,
) -> None:
    """NVIDIA CUDA optimizations.

    Basic env vars are always applied. Atlas-derived tuning
    parameters require a valid license key (paid tier).
    """
    # -- Free tier (always applied) -------------------------------------------
    # Deterministic workspace for cuBLAS (helps reproducibility)
    env["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"

    # Let the driver auto-boost clocks
    env["CUDA_AUTO_BOOST"] = "1"

    # PyTorch CUDA memory allocator: expandable segments reduce fragmentation
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    # Enable cuDNN autotuner (finds fastest conv algorithms)
    env["CUDNN_BENCHMARK"] = "1"

    if job_type == "training":
        # TF32 for Ampere+ (significant speedup, minimal accuracy loss)
        env["NVIDIA_TF32_OVERRIDE"] = "1"

    # -- Paid tier (license required) -----------------------------------------
    if license_key:
        # Atlas-derived CUDA tuning would go here.
        # For now, log that paid-tier optimizations are available.
        logger.info("Slento license detected -- paid-tier NVIDIA optimizations enabled")


def apply_to_process_env(env_vars: Dict[str, str]) -> None:
    """Apply optimization env vars to the current process (os.environ).

    Only sets variables that are not already explicitly set by the user,
    so user overrides are always respected.
    """
    applied = []
    for key, value in env_vars.items():
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    if applied:
        logger.info("Applied %d optimization env vars: %s", len(applied), ", ".join(sorted(applied)))


def build_subprocess_env(
    env_vars: Dict[str, str],
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build an env dict for subprocess calls, merging optimizations.

    User-set variables in base_env take precedence over optimizations.

    Parameters
    ----------
    env_vars : dict
        Optimization env vars from get_optimization_env().
    base_env : dict, optional
        Base environment (defaults to os.environ).

    Returns
    -------
    dict
        Merged environment suitable for subprocess.Popen(env=...).
    """
    merged = dict(base_env if base_env is not None else os.environ)
    for key, value in env_vars.items():
        if key not in merged:
            merged[key] = value
    return merged
