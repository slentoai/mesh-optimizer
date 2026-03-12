"""Execute dispatched jobs on this node with sandboxing."""
from __future__ import annotations

import asyncio
import logging
import re
import resource
import subprocess
import sys
import time
from typing import Any, Dict, List, Set

from mesh_optimizer.models import JobInfo, JobResult, JobStatus, JobType

logger = logging.getLogger(__name__)

# Cached optimization env vars (populated on first use via set_hardware_info)
_optimization_env: Dict[str, str] = {}

# Active jobs tracked by this executor
_active_jobs: Dict[str, asyncio.Task] = {}


# -- Command Sandbox ----------------------------------------------------------

# Patterns that must never appear in command strings
_BLOCKED_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bos\.system\b"),
    re.compile(r"\bos\.popen\b"),
    re.compile(r"\bos\.exec[a-z]*\b"),
    re.compile(r"\bos\.spawn[a-z]*\b"),
    re.compile(r"\bos\.remove\b"),
    re.compile(r"\bos\.unlink\b"),
    re.compile(r"\bos\.rmdir\b"),
    re.compile(r"\bos\.rename\b"),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\bshutil\.rmtree\b"),
    re.compile(r"\bshutil\.move\b"),
    re.compile(r"\b__import__\b"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bcompile\s*\("),
    re.compile(r"\bopen\s*\(.*(w|a|x)"),
    re.compile(r"\bsocket\b"),
    re.compile(r"\bctypes\b"),
]

# Allowed command prefixes. The controller sends commands that start with these.
_ALLOWED_COMMANDS: Set[str] = {
    "from mesh_optimizer.",
    "import mesh_optimizer.",
    "from product_runners.",
    "import product_runners.",
}

# Resource limits for sandboxed commands
_MAX_CPU_TIME_S = 7200
_MAX_MEMORY_BYTES = 8 * 1024 * 1024 * 1024


def register_allowed_command(prefix: str) -> None:
    """Register an additional allowed command prefix."""
    _ALLOWED_COMMANDS.add(prefix)


class CommandSandbox:
    """Validates commands against an allowlist and blocks dangerous patterns."""

    @staticmethod
    def validate(command: str, node_id: str = "unknown") -> None:
        """Validate a command string. Raises ValueError if rejected."""
        if not command or not command.strip():
            raise ValueError("Empty command")

        if "\x00" in command:
            raise ValueError("Command contains null bytes")

        cmd_stripped = command.strip()
        allowed = any(cmd_stripped.startswith(prefix) for prefix in _ALLOWED_COMMANDS)
        if not allowed:
            logger.warning(
                "SECURITY: Blocked command from node %s: %.100s",
                node_id, cmd_stripped,
            )
            raise ValueError(
                f"Command not in allowlist. Must start with one of: "
                f"{sorted(_ALLOWED_COMMANDS)}"
            )

        for pattern in _BLOCKED_PATTERNS:
            if pattern.search(command):
                logger.warning(
                    "SECURITY: Blocked dangerous pattern '%s' from node %s: %.100s",
                    pattern.pattern, node_id, cmd_stripped,
                )
                raise ValueError(f"Command contains blocked pattern: {pattern.pattern}")

    @staticmethod
    def apply_resource_limits():
        """Apply resource limits to child processes (Linux only)."""
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (_MAX_CPU_TIME_S, _MAX_CPU_TIME_S))
            resource.setrlimit(resource.RLIMIT_AS, (_MAX_MEMORY_BYTES, _MAX_MEMORY_BYTES))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        except (ValueError, OSError) as e:
            logger.debug("Could not set resource limits: %s", e)


_sandbox = CommandSandbox()


async def execute_job(job: JobInfo, node_id: str = "unknown") -> JobResult:
    """Execute a job and return the result."""
    t0 = time.time()
    logger.info("Executing job %s: type=%s", job.job_id, job.job_type)

    # Track this task so get_active_job_count() is accurate
    task = asyncio.current_task()
    if task is not None:
        _active_jobs[job.job_id] = task

    try:
        if job.job_type == JobType.PROBE:
            result = await _run_probe_job(job)
        else:
            result = await _run_command_job(job, node_id)

        duration = time.time() - t0
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.COMPLETED,
            result=result,
            duration_s=round(duration, 2),
        )
    except Exception as e:
        duration = time.time() - t0
        logger.error("Job %s failed: %s", job.job_id, e)
        return JobResult(
            job_id=job.job_id,
            status=JobStatus.FAILED,
            error=str(e),
            duration_s=round(duration, 2),
        )
    finally:
        _active_jobs.pop(job.job_id, None)


async def _run_probe_job(job: JobInfo) -> Dict[str, Any]:
    """Run a probe job."""
    from mesh_optimizer.agent.probe_runner import ProbeRunner
    runner = ProbeRunner()
    probe_types = job.args.get("probe_types")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, lambda: runner.run(probe_types=probe_types)
    )
    return result


async def _run_command_job(job: JobInfo, node_id: str = "unknown") -> Dict[str, Any]:
    """Run a command job with sandboxing."""
    if not job.command:
        return {"error": "No command specified"}

    _sandbox.validate(job.command, node_id=node_id)

    logger.info(
        "AUDIT: Command execution node=%s job=%s cmd=%.200s",
        node_id, job.job_id, job.command,
    )

    timeout = job.args.get("timeout_s", 3600)

    # Build subprocess env with atlas-derived optimizations
    sub_env = None
    if _optimization_env:
        from mesh_optimizer.agent.gpu_optimizer import build_subprocess_env
        sub_env = build_subprocess_env(_optimization_env)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", job.command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=sub_env,
        preexec_fn=CommandSandbox.apply_resource_limits,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": f"Job timed out after {timeout}s"}

    return {
        "returncode": proc.returncode,
        "stdout": stdout.decode()[-10000:],
        "stderr": stderr.decode()[-5000:],
    }


def set_hardware_info(hardware_info, job_type: str = "general") -> None:
    """Initialize optimization env vars for this executor.

    Called once by NodeAgent after hardware scan so that all subprocess
    jobs automatically inherit atlas-derived optimizations.
    """
    global _optimization_env
    from mesh_optimizer.agent.gpu_optimizer import get_optimization_env
    _optimization_env = get_optimization_env(hardware_info, job_type=job_type)
    if _optimization_env:
        logger.info(
            "Job executor: %d optimization env vars loaded for subprocess jobs",
            len(_optimization_env),
        )


def get_active_job_count() -> int:
    return sum(1 for t in _active_jobs.values() if not t.done())
