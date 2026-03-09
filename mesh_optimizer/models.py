"""Pydantic models for agent API payloads."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


# -- Enums -------------------------------------------------------------------

class NodeStatus(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, Enum):
    PROBE = "probe"
    BENCHMARK = "benchmark"
    TRAINING = "training"
    INFERENCE = "inference"
    CUSTOM = "custom"


# -- Hardware Models ----------------------------------------------------------

class GPUInfo(BaseModel):
    name: str = ""
    vendor: str = ""  # "amd", "nvidia"
    vram_mb: int = 0
    arch: str = ""
    driver: str = ""
    temperature_c: float = 0.0
    utilization_pct: float = 0.0
    memory_used_mb: int = 0


class CPUInfo(BaseModel):
    model: str = ""
    cores: int = 0
    physical_cores: int = 0
    threads_per_core: int = 1
    freq_mhz: float = 0.0
    l1d_kb: int = 0
    l2_kb: int = 0
    l3_kb: int = 0
    numa_nodes: int = 1


class FPGAInfo(BaseModel):
    name: str = ""
    vendor: str = ""
    pcie_bdf: str = ""
    driver: str = ""


class HardwareInventory(BaseModel):
    hostname: str = ""
    platform: str = ""
    cpu: CPUInfo = Field(default_factory=CPUInfo)
    memory_total_mb: int = 0
    gpus: List[GPUInfo] = Field(default_factory=list)
    fpgas: List[FPGAInfo] = Field(default_factory=list)
    has_pytorch: bool = False
    has_rocm: bool = False
    has_cuda: bool = False


# -- Health Models ------------------------------------------------------------

class HealthSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    cpu_pct: float = 0.0
    cpu_freq_mhz: float = 0.0
    memory_used_pct: float = 0.0
    memory_available_mb: int = 0
    disk_used_pct: float = 0.0
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    gpu_utilizations: List[float] = Field(default_factory=list)
    gpu_temperatures: List[float] = Field(default_factory=list)
    gpu_memory_used_mb: List[int] = Field(default_factory=list)
    net_bytes_sent: int = 0
    net_bytes_recv: int = 0
    uptime_s: float = 0.0


# -- Node Models --------------------------------------------------------------

class NodeRegistration(BaseModel):
    node_id: str
    hostname: str
    agent_url: str
    hardware: HardwareInventory
    tags: List[str] = Field(default_factory=list)
    nat_mode: bool = False

    @field_validator("agent_url")
    @classmethod
    def validate_agent_url(cls, v: str) -> str:
        parsed = urlparse(v.strip())
        if parsed.scheme not in ("http", "https"):
            raise ValueError("agent_url must use http or https scheme")
        if not parsed.hostname:
            raise ValueError("agent_url must have a hostname")
        if parsed.port is not None and (parsed.port < 1 or parsed.port > 65535):
            raise ValueError(f"agent_url port out of range: {parsed.port}")
        return v.strip()


class NodeHeartbeat(BaseModel):
    node_id: str
    health: HealthSnapshot
    active_jobs: int = 0


# -- Job Models ---------------------------------------------------------------

class JobSubmission(BaseModel):
    job_type: JobType = JobType.CUSTOM
    command: str = Field(default="", max_length=10000)
    args: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    timeout_s: float = 3600.0

    @field_validator("command")
    @classmethod
    def validate_command(cls, v: str) -> str:
        if "\x00" in v:
            raise ValueError("Command must not contain null bytes")
        return v


class JobInfo(BaseModel):
    job_id: str = ""
    job_type: JobType = JobType.CUSTOM
    command: str = ""
    args: Dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    assigned_node: Optional[str] = None
    submitted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    priority: int = 0


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_s: float = 0.0


# -- Response Models ----------------------------------------------------------

class StatusResponse(BaseModel):
    status: str = "ok"
    message: str = ""
    data: Optional[Dict[str, Any]] = None


# -- License Models -----------------------------------------------------------

class LicenseTier(str, Enum):
    COMMUNITY = "community"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class LicenseInfo(BaseModel):
    license_key: str = ""
    tier: LicenseTier = LicenseTier.COMMUNITY
    max_nodes: int = 0
    features: List[str] = Field(default_factory=list)
    valid: bool = True
    expires_at: Optional[datetime] = None
