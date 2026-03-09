# API Reference

The mesh exposes two REST APIs built with FastAPI:

- **Agent API** (port 8400) -- runs on every node, serves local hardware/health data and executes jobs
- **Controller API** (port 8401) -- runs on the primary node, manages the cluster

Both APIs return JSON. FastAPI auto-generates OpenAPI docs at `/docs` (Swagger UI) and `/redoc`.

## Authentication

Authentication is opt-in (see [SETUP.md](SETUP.md#enabling-authentication)). When enabled, all requests (except exempt paths) must include a Bearer token:

```
Authorization: Bearer <token>
```

**Exempt paths** (no token required): `/health`, `/docs`, `/openapi.json`, `/nodes/register`

### Registration Flow

1. Admin generates a one-time registration token on the controller
2. Agent sends `POST /nodes/register` with the registration token in the `Authorization` header
3. Controller validates the token, registers the node, and returns an API token in the response
4. Agent uses the API token for all subsequent requests

### Token Format

Tokens are HMAC-SHA256 signed: `base64(json_payload).base64(signature)`

Payload fields: `node_id`, `role`, `iat` (issued at), `exp` (expiry)

### Error Responses (Auth)

```json
// 401 - Missing or invalid token
{"detail": "Missing or invalid Authorization header"}

// 401 - Expired token
{"detail": "Token expired"}

// 429 - Rate limited
{"detail": "Rate limit exceeded"}

// 413 - Payload too large
{"detail": "Payload too large (2000000 bytes, max 1048576)"}
```

---

## Error Response Format

All errors use a structured format:

```json
{
  "error": {
    "code": "NODE_NOT_FOUND",
    "message": "Node abc123def456 not found",
    "details": {
      "node_id": "abc123def456"
    }
  }
}
```

Error codes: `NODE_NOT_FOUND` (404), `AUTH_FAILED` (401), `VALIDATION_ERROR` (422).

Pydantic validation errors return FastAPI's default 422 format with field-level details.

---

## Agent API (port 8400)

### GET /health

Returns agent health status including system metrics and subsystem checks.

```bash
curl http://localhost:8400/health
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "",
  "data": {
    "cpu_pct": 12.5,
    "memory_used_pct": 45.2,
    "load_1m": 1.34,
    "uptime_s": 86400.0,
    "active_jobs": 0,
    "db_healthy": true,
    "controller_connected": true,
    "last_probe_time": 1741305600.0
  }
}
```

**Response 503** (controller disconnected):

```json
{
  "status": "degraded",
  "message": "Controller connection lost",
  "data": { ... }
}
```

### GET /hardware

Returns the full hardware inventory of this node.

```bash
curl http://localhost:8400/hardware
```

**Response 200:**

```json
{
  "hostname": "gpu-node-1",
  "platform": "Linux",
  "cpu": {
    "model": "Intel(R) Xeon(R) Silver 4108 CPU @ 1.80GHz",
    "cores": 16,
    "physical_cores": 8,
    "threads_per_core": 2,
    "freq_mhz": 3000.0,
    "l1d_kb": 32,
    "l2_kb": 1024,
    "l3_kb": 11264,
    "numa_nodes": 2
  },
  "memory_total_mb": 65536,
  "gpus": [
    {
      "name": "Radeon RX 7900 XTX",
      "vendor": "amd",
      "vram_mb": 24576,
      "arch": "",
      "driver": "",
      "temperature_c": 45.0,
      "utilization_pct": 0.0,
      "memory_used_mb": 128
    }
  ],
  "fpgas": [
    {
      "name": "Xilinx Device [10ee:7025]",
      "vendor": "xilinx",
      "pcie_bdf": "41:00.0",
      "driver": "vfio-pci"
    }
  ],
  "has_pytorch": true,
  "has_rocm": true,
  "has_cuda": false
}
```

### POST /probe/run

Trigger a probe run in the background. Returns immediately.

```bash
curl -X POST "http://localhost:8400/probe/run?iterations=20"
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "Probe started"
}
```

### POST /jobs/submit

Execute a job on this agent (called by the controller during job dispatch).

```bash
curl -X POST http://localhost:8400/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "benchmark",
    "command": "python run_bench.py",
    "args": {"size": 4096},
    "priority": 5,
    "timeout_s": 3600
  }'
```

**Request body (JobSubmission):**

| Field | Type | Required | Description |
|---|---|---|---|
| `job_type` | string | no | `probe`, `benchmark`, `training`, `inference`, `custom` (default: `custom`) |
| `command` | string | no | Command to execute |
| `args` | object | no | Arguments passed to the job executor |
| `target_node` | string | no | Not used on agent-side |
| `priority` | int | no | Higher = higher priority (default: 0) |
| `timeout_s` | float | no | Job timeout in seconds (default: 3600) |

**Response 200:**

```json
{
  "job_id": "a1b2c3d4",
  "job_type": "benchmark",
  "command": "python run_bench.py",
  "args": {"size": 4096},
  "status": "running",
  "assigned_node": null,
  "submitted_at": "2026-03-08T12:00:00",
  "started_at": "2026-03-08T12:00:00",
  "completed_at": null,
  "result": null,
  "error": null,
  "priority": 5
}
```

### GET /jobs/{job_id}

Get the status of a specific job.

```bash
curl http://localhost:8400/jobs/a1b2c3d4
```

**Response 200:** Same format as JobInfo above, with updated `status`, `result`, and `completed_at`.

**Response 404:**

```json
{"detail": "Job a1b2c3d4 not found"}
```

### POST /tuning/apply

Apply system tuning on this node.

```bash
curl -X POST http://localhost:8400/tuning/apply \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "Dry run complete",
  "data": {
    "cpu_governor": "DRY_RUN: would write 'performance' -> /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor",
    "hugepages": "DRY_RUN: would write '1024' -> /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages",
    "vm_tuning": "DRY_RUN: ...",
    "network": "DRY_RUN: ..."
  }
}
```

### POST /jepa/sync

Receive a retrained JEPA model from the controller. Multipart form upload.

```bash
curl -X POST http://localhost:8400/jepa/sync \
  -F "model=@data/jepa_policy_model.pt" \
  -F "mean_error=0.02" \
  -F "training_samples=83174"
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "Model saved (1024000 bytes)",
  "data": {
    "mean_error": 0.02,
    "training_samples": 83174
  }
}
```

---

## Controller API (port 8401)

### GET /health

Controller health with subsystem status.

```bash
curl http://localhost:8401/health
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "",
  "data": {
    "node_count": 3,
    "online_nodes": 3,
    "db_healthy": true,
    "jepa_initialized": true,
    "last_retrain": "2026-03-08T06:00:00"
  }
}
```

**Response 503:** Returned when the database is unhealthy.

### POST /nodes/register

Register a new node with the controller.

```bash
curl -X POST http://localhost:8401/nodes/register \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <registration-token>" \
  -d '{
    "node_id": "abc123def456",
    "hostname": "gpu-node-1",
    "agent_url": "http://gpu-node-1:8400",
    "hardware": {
      "hostname": "gpu-node-1",
      "platform": "Linux",
      "cpu": {"model": "Xeon 4108", "cores": 16},
      "memory_total_mb": 65536,
      "gpus": [{"name": "RX 7900 XTX", "vendor": "amd", "vram_mb": 24576}],
      "fpgas": [],
      "has_pytorch": true,
      "has_rocm": true,
      "has_cuda": false
    },
    "tags": ["gpu", "amd"]
  }'
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "Node abc123def456 registered",
  "data": {
    "node_id": "abc123def456",
    "api_token": "eyJub2RlX2lk..."
  }
}
```

The `api_token` is only returned when authentication is enabled.

### POST /nodes/{node_id}/heartbeat

Submit a health snapshot from an agent.

```bash
curl -X POST http://localhost:8401/nodes/abc123def456/heartbeat \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "abc123def456",
    "health": {
      "cpu_pct": 15.0,
      "memory_used_pct": 42.0,
      "load_1m": 1.2,
      "load_5m": 1.1,
      "load_15m": 0.9,
      "gpu_utilizations": [5.0],
      "gpu_temperatures": [45.0],
      "disk_used_pct": 60.0,
      "uptime_s": 86400
    },
    "active_jobs": 0
  }'
```

**Response 200:**

```json
{"status": "ok", "message": ""}
```

### GET /nodes

List all registered nodes (excludes archived).

```bash
curl http://localhost:8401/nodes
```

**Response 200:** Array of `NodeInfo` objects.

### GET /nodes/{node_id}

Get detailed info for a specific node.

```bash
curl http://localhost:8401/nodes/abc123def456
```

**Response 200:** Single `NodeInfo` object with hardware, last health, and status.

**Response 404:** `NODE_NOT_FOUND` error.

### DELETE /nodes/{node_id}

Remove or archive a node.

```bash
# Archive (mark offline, keep in DB)
curl -X DELETE "http://localhost:8401/nodes/abc123def456?archive=true"

# Delete (remove from DB)
curl -X DELETE "http://localhost:8401/nodes/abc123def456"
```

**Response 200:**

```json
{"status": "ok", "message": "Node gpu-node-1 archived"}
```

### POST /atlas/sync

Ingest atlas probe data from an agent.

```bash
curl -X POST http://localhost:8401/atlas/sync \
  -H "Content-Type: application/json" \
  -d '{
    "node_id": "abc123def456",
    "atlas_db_name": "gpu-node-1_atlas.db",
    "data_points": [
      {"kernel_type": "bandwidth", "params": {"block_size": 256}, "metric": 867.0}
    ],
    "invariants": [
      {"kernel_type": "bandwidth", "rule": "block_size >= 128", "confidence": 0.95}
    ]
  }'
```

**Response 200:**

```json
{
  "status": "ok",
  "message": "Ingested 1 data points from abc123def456",
  "data": {"data_points": 1}
}
```

### GET /atlas/summary

Get a summary of all federated atlas data.

```bash
curl http://localhost:8401/atlas/summary
```

**Response 200:** Object with atlas database names, data point counts, kernel types, and invariant counts.

### POST /jobs/submit

Submit a job for routing and execution.

```bash
curl -X POST http://localhost:8401/jobs/submit \
  -H "Content-Type: application/json" \
  -d '{
    "job_type": "training",
    "command": "python train.py --epochs 10",
    "args": {"dataset": "imagenet", "batch_size": 32},
    "target_node": null,
    "priority": 10,
    "timeout_s": 7200
  }'
```

Set `target_node` to a node_id or hostname to pin to a specific node. Leave `null` for auto-routing.

**Response 200:** `JobInfo` object with `job_id` and initial status.

### GET /jobs

List jobs with optional status filter.

```bash
# All jobs (most recent 50)
curl http://localhost:8401/jobs

# Only pending jobs
curl "http://localhost:8401/jobs?status=pending&limit=10"
```

**Response 200:** Array of `JobInfo` objects.

### GET /jobs/{job_id}

Get a specific job by ID.

```bash
curl http://localhost:8401/jobs/a1b2c3d4
```

### GET /jepa/stats

Get current JEPA model statistics.

```bash
curl http://localhost:8401/jepa/stats
```

**Response 200:**

```json
{
  "trained": true,
  "online_updates": 42,
  "memory_bank_size": 1024,
  "training_samples": 83174,
  "mean_error": 0.02,
  "confidence_by_regime": {
    "bandwidth": 0.87,
    "compute": 0.92,
    "wmma": 0.88,
    "atomic": 0.43
  },
  "last_retrain": "2026-03-08T06:00:00"
}
```

### POST /jepa/retrain

Trigger an immediate JEPA retrain.

```bash
curl -X POST "http://localhost:8401/jepa/retrain?epochs=20"
```

**Response 200:**

```json
{
  "status": "ok",
  "data": {
    "samples": 83174,
    "final_loss": 0.018,
    "mean_error": 0.02,
    "epochs": 20
  }
}
```

### GET /jepa/schedule

Get the current retrain interval.

```bash
curl http://localhost:8401/jepa/schedule
```

**Response 200:**

```json
{"interval_s": 21600}
```

### POST /jepa/schedule

Update the retrain interval. Minimum 60 seconds.

```bash
curl -X POST "http://localhost:8401/jepa/schedule?interval_s=3600"
```

**Response 200:**

```json
{"status": "ok", "message": "Retrain interval set to 3600.0s"}
```

### GET /jepa/training-log

Get recent JEPA training history.

```bash
curl "http://localhost:8401/jepa/training-log?limit=5"
```

**Response 200:**

```json
[
  {
    "id": 10,
    "training_samples": 83174,
    "epochs": 20,
    "final_loss": 0.018,
    "mean_error": 0.02,
    "duration_s": 12.5,
    "trained_at": "2026-03-08T06:00:00"
  }
]
```

### POST /jepa/feedback

Submit online learning feedback from a completed job.

```bash
curl -X POST http://localhost:8401/jepa/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "workload": {"compute_intensity": 5.0, "memory_intensity": 2.0},
    "config": {"block_size": 256, "ilp_chains": 7},
    "actual_performance": {"throughput": 50.0, "latency": 0.5},
    "machine_name": "gpu-node-1"
  }'
```

**Response 200:**

```json
{"status": "ok", "data": {"prediction_error": 0.03}}
```

### GET /nodes/{node_id}/recommendations

Get system tuning recommendations for a specific node. Accepts node_id or hostname.

```bash
curl http://localhost:8401/nodes/abc123def456/recommendations
```

**Response 200:** `SystemTuning` object with all recommended settings:

```json
{
  "cpu_governor": "performance",
  "hugepages_2m": 2048,
  "transparent_hugepages": "madvise",
  "numa_balancing": true,
  "swappiness": 10,
  "dirty_ratio": 20,
  "dirty_background_ratio": 5,
  "vfs_cache_pressure": 50,
  "sched_autogroup": false,
  "sched_util_clamp_min": 128,
  "sched_util_clamp_max": 1024,
  "sched_rr_timeslice_ms": 100,
  "io_schedulers": {},
  "net_rmem_max": 16777216,
  "net_wmem_max": 16777216,
  "net_tcp_rmem": "4096 131072 16777216",
  "net_tcp_wmem": "4096 65536 16777216",
  "net_somaxconn": 65535,
  "irq_affinity": {},
  "applied_at": null,
  "node_id": "abc123def456"
}
```

---

## Rate Limiting

When authentication is enabled, the `ValidationMiddleware` enforces per-IP rate limits:

| Limit | Value | Response |
|---|---|---|
| Requests per minute per IP | 100 (configurable) | HTTP 429 |
| Max JSON payload | 1 MB | HTTP 413 |
| Max file upload | 10 MB | HTTP 413 |

Rate limit state is in-memory (resets on restart). Stale entries are cleaned up periodically.
