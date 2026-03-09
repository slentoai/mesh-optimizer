"""FastAPI endpoints exposed by the agent (port 8400).

Provides health, hardware inventory, probe triggers, and job submission
endpoints. The controller calls these to inspect and dispatch work to this node.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from mesh_optimizer.agent.hardware_scanner import scan_hardware
from mesh_optimizer.agent.health_reporter import collect_health
from mesh_optimizer.agent.job_executor import execute_job, get_active_job_count
from mesh_optimizer.agent.probe_runner import ProbeRunner
from mesh_optimizer.models import (
    HardwareInventory, JobInfo, JobStatus, JobSubmission, StatusResponse,
)

logger = logging.getLogger(__name__)

# In-memory job tracking (capped at _MAX_JOBS to prevent unbounded growth)
_MAX_JOBS = 1000
_jobs: dict[str, JobInfo] = {}
_hardware_cache: Optional[HardwareInventory] = None


def _prune_jobs():
    """Remove oldest completed jobs when _jobs exceeds _MAX_JOBS."""
    if len(_jobs) <= _MAX_JOBS:
        return
    completed = [
        (jid, j) for jid, j in _jobs.items()
        if j.status in (JobStatus.COMPLETED, JobStatus.FAILED)
    ]
    completed.sort(key=lambda x: x[1].submitted_at or datetime.min)
    to_remove = len(_jobs) - _MAX_JOBS
    for jid, _ in completed[:to_remove]:
        del _jobs[jid]

# Set externally before the app starts
_node_agent = None


def wire_agent(node_agent=None):
    """Wire in the NodeAgent instance before startup."""
    global _node_agent
    if node_agent is not None:
        _node_agent = node_agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _hardware_cache
    _hardware_cache = scan_hardware()
    logger.info("Agent API started: %s", _hardware_cache.hostname)

    if _node_agent is not None:
        asyncio.create_task(_node_agent.start())

    yield

    if _node_agent is not None:
        await _node_agent.stop()


app = FastAPI(
    title="Mesh Optimizer Agent",
    version="0.1.0",
    description="Hardware optimization agent — connects to the Mesh Optimizer controller.",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Return agent health status and key metrics."""
    h = collect_health()
    controller_alive = _node_agent.is_controller_alive if _node_agent else False
    last_probe = _node_agent._last_probe_time if _node_agent else 0.0

    data = {
        "cpu_pct": h.cpu_pct,
        "memory_used_pct": h.memory_used_pct,
        "load_1m": h.load_1m,
        "uptime_s": h.uptime_s,
        "active_jobs": get_active_job_count(),
        "controller_connected": controller_alive,
        "last_probe_time": last_probe,
    }

    all_ok = controller_alive or not (_node_agent and _node_agent.registered)

    if not all_ok and _node_agent and _node_agent.registered:
        return JSONResponse(
            status_code=503,
            content=StatusResponse(
                status="degraded",
                message="Controller connection lost",
                data=data,
            ).model_dump(mode="json"),
        )

    return StatusResponse(status="ok", data=data)


@app.get("/hardware")
async def hardware() -> HardwareInventory:
    """Return the detected hardware inventory."""
    global _hardware_cache
    if _hardware_cache is None:
        _hardware_cache = scan_hardware()
    return _hardware_cache


@app.post("/probe/run")
async def run_probe(iterations: int = 20) -> StatusResponse:
    """Trigger a probe run in the background."""
    runner = ProbeRunner()

    async def _run():
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, runner.run)
        logger.info("Probe complete: %s", result)

    asyncio.create_task(_run())
    return StatusResponse(status="ok", message="Probe started")


@app.post("/jobs/submit")
async def submit_job(submission: JobSubmission) -> JobInfo:
    """Execute a dispatched job on this node."""
    job_id = str(uuid.uuid4())[:8]
    job = JobInfo(
        job_id=job_id,
        job_type=submission.job_type,
        command=submission.command,
        args=submission.args,
        status=JobStatus.RUNNING,
        submitted_at=datetime.utcnow(),
        started_at=datetime.utcnow(),
        priority=submission.priority,
    )
    _jobs[job_id] = job
    _prune_jobs()

    async def _execute():
        result = await execute_job(job)
        job.status = result.status
        job.result = result.result
        job.error = result.error
        job.completed_at = datetime.utcnow()

    asyncio.create_task(_execute())
    return job


@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JobInfo:
    """Return the status of a submitted job."""
    if job_id not in _jobs:
        raise HTTPException(404, f"Job {job_id} not found")
    return _jobs[job_id]


def create_agent_app() -> FastAPI:
    """Factory for the agent FastAPI app."""
    return app
