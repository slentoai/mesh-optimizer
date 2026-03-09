"""Run basic benchmark probes and report results to the controller."""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)


class ProbeRunner:
    """Runs lightweight hardware probes to measure baseline performance.

    The controller may request probes to understand the capabilities of this
    node. Results are sent back to the controller for analysis.
    """

    def run(
        self,
        probe_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run probes and return a results dictionary.

        Args:
            probe_types: Optional list of probe types to run. If None, runs all.

        Returns:
            Dict with status, results, and duration.
        """
        all_probes = {
            "memory_bandwidth": self._probe_memory_bandwidth,
            "cpu_throughput": self._probe_cpu_throughput,
            "disk_io": self._probe_disk_io,
            "gpu_available": self._probe_gpu_available,
        }

        if probe_types:
            probes = {k: v for k, v in all_probes.items() if k in probe_types}
        else:
            probes = all_probes

        t0 = time.time()
        results = {}

        for name, func in probes.items():
            try:
                results[name] = func()
            except Exception as e:
                logger.warning("Probe %s failed: %s", name, e)
                results[name] = {"error": str(e)}

        duration = time.time() - t0
        logger.info("Probes complete in %.1fs: %s", duration, list(results.keys()))

        return {
            "status": "ok",
            "results": results,
            "duration_s": round(duration, 2),
        }

    def _probe_memory_bandwidth(self) -> Dict[str, Any]:
        """Estimate memory bandwidth using a simple buffer copy benchmark."""
        import array
        size = 64 * 1024 * 1024  # 64 MB
        buf = bytearray(size)
        t0 = time.perf_counter()
        _ = bytes(buf)  # force a copy
        elapsed = time.perf_counter() - t0
        bw_gbps = (size / (1024 ** 3)) / elapsed if elapsed > 0 else 0
        return {
            "buffer_size_mb": size // (1024 * 1024),
            "bandwidth_gbps": round(bw_gbps, 2),
        }

    def _probe_cpu_throughput(self) -> Dict[str, Any]:
        """Measure single-core compute throughput with a simple loop."""
        iterations = 5_000_000
        t0 = time.perf_counter()
        x = 1.0
        for _ in range(iterations):
            x = x * 1.000001 + 0.000001
        elapsed = time.perf_counter() - t0
        mops = iterations / elapsed / 1e6 if elapsed > 0 else 0
        return {
            "iterations": iterations,
            "mops": round(mops, 1),
            "elapsed_s": round(elapsed, 3),
        }

    def _probe_disk_io(self) -> Dict[str, Any]:
        """Check disk I/O counters from psutil."""
        try:
            counters = psutil.disk_io_counters()
            if counters:
                return {
                    "read_bytes": counters.read_bytes,
                    "write_bytes": counters.write_bytes,
                    "read_count": counters.read_count,
                    "write_count": counters.write_count,
                }
        except Exception:
            pass
        return {"error": "disk I/O counters unavailable"}

    def _probe_gpu_available(self) -> Dict[str, Any]:
        """Check if GPUs are accessible."""
        gpus = []

        # NVIDIA
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            )
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    gpus.append({
                        "name": parts[0],
                        "vendor": "nvidia",
                        "vram_mb": int(float(parts[1])),
                    })
        except Exception:
            pass

        # AMD
        try:
            out = subprocess.check_output(
                ["rocm-smi", "--showproductname", "--json"],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            )
            import json
            data = json.loads(out)
            for key, val in data.items():
                if key.startswith("card"):
                    gpus.append({
                        "name": val.get("Card Series", "AMD GPU"),
                        "vendor": "amd",
                    })
        except Exception:
            pass

        return {"gpu_count": len(gpus), "gpus": gpus}
