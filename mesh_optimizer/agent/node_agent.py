"""Main agent daemon — register, heartbeat, probe, and job execution loops."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import time
from typing import Optional

from mesh_optimizer.config import AgentConfig
from mesh_optimizer.agent.hardware_scanner import scan_hardware
from mesh_optimizer.agent.health_reporter import collect_health
from mesh_optimizer.agent.probe_runner import ProbeRunner
from mesh_optimizer.models import (
    HardwareInventory, NodeHeartbeat, NodeRegistration, NodeStatus,
    JobInfo, JobStatus,
)
from mesh_optimizer.net.client import MeshClient

logger = logging.getLogger(__name__)

# File where last-known hardware fingerprint is cached between restarts
_HW_CACHE_FILE = ".mesh-optimizer-hw-fingerprint.json"


class NodeAgent:
    """Agent daemon running on each mesh node.

    The agent performs four core functions:
    1. Registers with the controller and maintains a heartbeat
    2. Collects health metrics (CPU, GPU, memory, disk)
    3. Runs benchmark probes when requested by the controller
    4. Executes dispatched jobs (in NAT mode, polls for them)
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.hostname = config.get_node_name()
        self.node_id = self._generate_node_id()
        self.hardware: Optional[HardwareInventory] = None
        self.status = NodeStatus.ONLINE
        self.registered = False
        self._running = False
        self._last_probe_time = 0.0
        self._controller_alive = False
        self._client: Optional[MeshClient] = None
        self._probe_runner = ProbeRunner()

    def _generate_node_id(self) -> str:
        """Generate a stable node ID from hostname + architecture."""
        raw = f"{self.hostname}-{platform.machine()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    async def start(self):
        """Start the agent: scan hardware, register, begin background loops."""
        self._running = True
        self._hardware_changed = False
        self._client = MeshClient(timeout=10.0)
        await self._client.__aenter__()
        logger.info("Node agent starting: id=%s hostname=%s", self.node_id, self.hostname)

        # Discover local hardware
        self.hardware = scan_hardware(hostname_override=self.hostname)
        logger.info(
            "Hardware: %d CPUs, %d GPUs, %d FPGAs, %d TPUs, %dMB RAM",
            self.hardware.cpu.cores,
            len(self.hardware.gpus),
            len(self.hardware.fpgas),
            len(self.hardware.tpus),
            self.hardware.memory_total_mb,
        )
        if self.hardware.has_coral:
            avail = sum(1 for t in self.hardware.tpus if t.available)
            logger.info("Coral Edge TPU: %d device(s), %d available", len(self.hardware.tpus), avail)

        # Apply atlas-derived optimization env vars to the agent process and
        # configure the job executor so subprocess jobs inherit them too.
        from mesh_optimizer.agent.gpu_optimizer import get_optimization_env, apply_to_process_env
        from mesh_optimizer.agent.job_executor import set_hardware_info
        opt_env = get_optimization_env(self.hardware)
        apply_to_process_env(opt_env)
        set_hardware_info(self.hardware)
        logger.info("GPU optimizations applied: %d env vars", len(opt_env))

        # Check if hardware changed since last run
        self._hardware_changed = self._detect_hardware_change()
        if self._hardware_changed:
            logger.info("HARDWARE CHANGE DETECTED — will run probes immediately after registration")

        # Start background tasks
        tasks = [
            self._registration_loop(),
            self._heartbeat_loop(),
            self._probe_loop(),
        ]
        if self.config.node.nat_mode:
            tasks.append(self._job_poll_loop())
        if self.config.node.share_atlas_data:
            tasks.append(self._community_sync_loop())
        await asyncio.gather(*tasks)

    async def stop(self):
        """Graceful shutdown: deregister from controller and close connections."""
        self._running = False
        logger.info("Node agent stopping")

        if self.registered and self._client:
            try:
                url = f"{self.config.controller_url}/nodes/{self.node_id}?archive=true"
                status, _ = await self._client.delete(url)
                if status == 200:
                    logger.info("Deregistered from controller")
                else:
                    logger.debug("Deregistration returned %d", status)
            except Exception as e:
                logger.debug("Deregistration failed: %s", e)

        if self._client:
            await self._client.close()
            self._client = None

    # -- Registration ---------------------------------------------------------

    async def _registration_loop(self):
        """Retry registration until successful."""
        while self._running:
            if not self.registered:
                try:
                    await self._register()
                except Exception as e:
                    logger.warning("Registration failed: %s", e)
            await asyncio.sleep(30)

    async def _register(self):
        """Register this node with the controller."""
        if self.config.node.public_url:
            agent_url = self.config.node.public_url
        else:
            agent_port = self.config.node.agent_port
            agent_url = f"http://{self.hostname}:{agent_port}"

        reg = NodeRegistration(
            node_id=self.node_id,
            hostname=self.hostname,
            agent_url=agent_url,
            hardware=self.hardware,
            tags=self.config.node.tags,
            nat_mode=self.config.node.nat_mode,
        )

        url = f"{self.config.controller_url}/nodes/register"
        status, data = await self._client.post(url, json=reg.model_dump(mode="json"))
        if status == 200:
            self.registered = True
            self._controller_alive = True
            logger.info("Registered with controller")
        else:
            logger.warning("Registration rejected (%d): %s", status, str(data)[:200])

    # -- Heartbeat ------------------------------------------------------------

    async def _heartbeat_loop(self):
        """Send periodic health snapshots to the controller."""
        interval = self.config.controller.heartbeat_interval_s
        while self._running:
            await asyncio.sleep(interval)
            if not self.registered:
                continue
            try:
                health = collect_health()
                hb = NodeHeartbeat(
                    node_id=self.node_id,
                    health=health,
                )
                url = f"{self.config.controller_url}/nodes/{self.node_id}/heartbeat"
                status, _ = await self._client.post(url, json=hb.model_dump(mode="json"))
                self._controller_alive = status == 200
            except Exception as e:
                self._controller_alive = False
                logger.debug("Heartbeat failed: %s", e)

    # -- Probes ---------------------------------------------------------------

    async def _probe_loop(self):
        """Run benchmark probes on a schedule and report results to the controller."""
        while self._running and not self.registered:
            await asyncio.sleep(5)

        # If hardware changed, probe immediately instead of waiting for the schedule
        if self._hardware_changed:
            logger.info("Running immediate probe cycle due to hardware change")
            try:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._probe_runner.run()
                )
                self._last_probe_time = time.time()
                if result.get("status") == "ok":
                    await self._submit_probe_results(result)
                    logger.info("Hardware change probe completed and submitted")
            except Exception as e:
                logger.error("Hardware change probe failed: %s", e)
            self._hardware_changed = False

        probe_interval = self.config.controller.probe_interval_s
        while self._running:
            now = time.time()
            if now - self._last_probe_time >= probe_interval:
                logger.info("Starting scheduled probe run")
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: self._probe_runner.run()
                    )
                    self._last_probe_time = time.time()

                    if result.get("status") == "ok":
                        await self._submit_probe_results(result)
                except Exception as e:
                    logger.error("Probe run failed: %s", e)

            await asyncio.sleep(60)

    async def _submit_probe_results(self, result: dict):
        """Send probe results to the controller."""
        payload = {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "results": result.get("results", {}),
            "duration_s": result.get("duration_s", 0),
        }
        url = f"{self.config.controller_url}/nodes/{self.node_id}/probe-results"
        try:
            status, _ = await self._client.post(url, json=payload)
            if status == 200:
                logger.info("Probe results submitted to controller")
            else:
                logger.warning("Probe result submission failed (%d)", status)
        except Exception as e:
            logger.warning("Probe result submission error: %s", e)

    # -- Job Polling (NAT mode) -----------------------------------------------

    async def _job_poll_loop(self):
        """NAT mode: poll the controller for pending job assignments.

        Since the controller cannot push jobs to us behind NAT, we periodically
        ask the controller if there are any jobs assigned to this node.
        """
        poll_interval = self.config.node.nat_poll_interval_s
        while self._running and not self.registered:
            await asyncio.sleep(5)

        logger.info("NAT mode: job polling started (interval=%.0fs)", poll_interval)
        while self._running:
            await asyncio.sleep(poll_interval)
            if not self.registered or not self._controller_alive:
                continue
            try:
                url = f"{self.config.controller_url}/nodes/{self.node_id}/pending-jobs"
                status, data = await self._client.get(url)
                if status == 200 and data:
                    jobs = data if isinstance(data, list) else []
                    for job_data in jobs:
                        logger.info("NAT poll: received job %s", job_data.get("job_id", "?"))
                        from mesh_optimizer.agent.job_executor import execute_job
                        job = JobInfo(**job_data)
                        job.status = JobStatus.RUNNING
                        result = await execute_job(job, node_id=self.node_id)
                        # Report result back to controller
                        result_url = f"{self.config.controller_url}/jobs/{job.job_id}/result"
                        await self._client.post(result_url, json={
                            "job_id": job.job_id,
                            "status": result.status.value,
                            "result": result.result,
                            "error": result.error,
                            "duration_s": result.duration_s,
                        })
            except Exception as e:
                logger.debug("NAT job poll failed: %s", e)

    # -- Community Atlas Sharing ------------------------------------------------

    async def _community_sync_loop(self):
        """Periodically send anonymized atlas data to the Slento community hub.

        This allows the global JEPA model to learn from diverse hardware across
        all opted-in users, improving optimization recommendations for everyone.

        Data is anonymized before sending: no hostnames, IPs, file paths, or
        job commands are included — only hardware class, kernel performance
        numbers, and optimal parameter values.
        """
        HUB_URL = "https://hub.slentosystems.com/api/v1/community/atlas"
        SYNC_INTERVAL_S = 21600.0  # Every 6 hours (aligned with probe interval)

        # Wait for registration + first probe
        while self._running and not self.registered:
            await asyncio.sleep(10)
        await asyncio.sleep(300)  # Let first probe complete

        logger.info("Community atlas sharing enabled — syncing every %.0fh",
                     SYNC_INTERVAL_S / 3600)

        while self._running:
            try:
                await self._send_community_data(HUB_URL)
            except Exception as e:
                logger.debug("Community sync failed: %s", e)
            await asyncio.sleep(SYNC_INTERVAL_S)

    async def _send_community_data(self, hub_url: str):
        """Collect and send anonymized atlas data to the community hub."""
        if not self._client or not self.hardware:
            return

        # Anonymize hardware: only share architecture class, not exact model
        hw_class = self._anonymize_hardware()

        # Ask controller for our latest atlas summary (probe results only)
        try:
            url = f"{self.config.controller_url}/nodes/{self.node_id}/atlas-export"
            status, data = await self._client.get(url)
            if status != 200 or not data:
                return
        except Exception:
            return

        # Strip any identifying information
        payload = {
            "hardware_class": hw_class,
            "data_points": data.get("data_points", []),
            "invariants": data.get("invariants", []),
            "agent_version": "1.0.0",
        }

        # Remove any fields that could identify the user
        for dp in payload["data_points"]:
            dp.pop("hostname", None)
            dp.pop("node_id", None)
            dp.pop("machine_name", None)
            dp.pop("file_path", None)
            dp.pop("command", None)

        for inv in payload["invariants"]:
            inv.pop("hostname", None)
            inv.pop("node_id", None)

        if not payload["data_points"]:
            return

        try:
            status, resp = await self._client.post(hub_url, json=payload)
            if status == 200:
                logger.info("Community atlas: shared %d data points (hw=%s)",
                           len(payload["data_points"]), hw_class)
            else:
                logger.debug("Community hub returned %d", status)
        except Exception as e:
            logger.debug("Community hub unreachable: %s", e)

    def _anonymize_hardware(self) -> str:
        """Return a hardware class string that doesn't identify the specific machine.

        Examples: "RDNA3_48CU", "Ampere_sm75", "Zen5_16C", "Xeon_8C"
        """
        parts = []

        # GPU class
        if self.hardware.gpus:
            gpu = self.hardware.gpus[0]
            arch = gpu.arch or "unknown"
            if "gfx11" in arch:
                parts.append(f"RDNA3_{gpu.vram_mb // 1024}GB")
            elif "gfx12" in arch:
                parts.append(f"RDNA4_{gpu.vram_mb // 1024}GB")
            elif "sm_" in arch:
                parts.append(f"CUDA_{arch}_{gpu.vram_mb // 1024}GB")
            else:
                parts.append(f"GPU_{gpu.vram_mb // 1024}GB")

        # CPU class
        cpu = self.hardware.cpu
        if cpu.cores:
            vendor = "x86"
            model_lower = cpu.model.lower()
            if "ryzen" in model_lower or "epyc" in model_lower or "amd" in model_lower:
                vendor = "AMD"
            elif "xeon" in model_lower or "intel" in model_lower or "core" in model_lower:
                vendor = "Intel"
            elif "apple" in model_lower or "m1" in model_lower or "m2" in model_lower:
                vendor = "Apple"
            parts.append(f"{vendor}_{cpu.cores}C")

        # Memory class (rounded to nearest power of 2)
        ram_gb = self.hardware.memory_total_mb // 1024
        parts.append(f"{ram_gb}GB")

        return "_".join(parts) if parts else "unknown"

    def _hardware_fingerprint(self) -> dict:
        """Build a compact fingerprint of the current hardware for change detection."""
        if not self.hardware:
            return {}
        fp = {
            "cpu_model": self.hardware.cpu.model,
            "cpu_cores": self.hardware.cpu.cores,
            "memory_mb": self.hardware.memory_total_mb,
            "gpus": sorted([
                {"name": g.name, "vendor": g.vendor, "vram_mb": g.vram_mb}
                for g in self.hardware.gpus
            ], key=lambda g: g["name"]),
            "fpgas": sorted([
                {"name": f.name, "vendor": f.vendor}
                for f in self.hardware.fpgas
            ], key=lambda f: f["name"]),
        }
        return fp

    def _detect_hardware_change(self) -> bool:
        """Compare current hardware against last-known fingerprint.

        Returns True if hardware changed (GPU swap, new device, etc.).
        Saves the current fingerprint for next startup.
        """
        import json
        from pathlib import Path

        cache_path = Path(self.config.data_dir) / _HW_CACHE_FILE
        current_fp = self._hardware_fingerprint()

        changed = False
        if cache_path.exists():
            try:
                previous_fp = json.loads(cache_path.read_text())
                if previous_fp != current_fp:
                    changed = True
                    # Log what changed
                    prev_gpus = {g["name"] for g in previous_fp.get("gpus", [])}
                    curr_gpus = {g["name"] for g in current_fp.get("gpus", [])}
                    added = curr_gpus - prev_gpus
                    removed = prev_gpus - curr_gpus
                    if added:
                        logger.info("New GPU(s) detected: %s", ", ".join(added))
                    if removed:
                        logger.info("GPU(s) removed: %s", ", ".join(removed))
                    prev_fpgas = {f["name"] for f in previous_fp.get("fpgas", [])}
                    curr_fpgas = {f["name"] for f in current_fp.get("fpgas", [])}
                    if curr_fpgas - prev_fpgas:
                        logger.info("New FPGA(s) detected: %s", ", ".join(curr_fpgas - prev_fpgas))
                    if prev_fpgas - curr_fpgas:
                        logger.info("FPGA(s) removed: %s", ", ".join(prev_fpgas - curr_fpgas))
                    if previous_fp.get("memory_mb") != current_fp.get("memory_mb"):
                        logger.info("Memory changed: %dMB → %dMB",
                                   previous_fp.get("memory_mb", 0), current_fp.get("memory_mb", 0))
            except Exception as e:
                logger.debug("Could not read previous fingerprint: %s", e)
                changed = True  # First run or corrupted — probe to be safe
        else:
            # First run on this machine
            changed = True

        # Save current fingerprint
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(current_fp, indent=2))
        except Exception as e:
            logger.debug("Could not save hardware fingerprint: %s", e)

        return changed

    @property
    def is_controller_alive(self) -> bool:
        return self._controller_alive
