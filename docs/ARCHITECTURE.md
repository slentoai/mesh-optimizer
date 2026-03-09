# Architecture

## System Components

The mesh has three main layers: **agents** (per-node), **controller** (central), and **dashboard** (UI).

### Agent (per node)

Each node runs a `NodeAgent` daemon that manages:

- **HardwareScanner** (`mesh/agent/hardware_scanner.py`) -- detects CPUs (model, cores, cache, NUMA), GPUs (NVIDIA via `nvidia-smi`, AMD via `rocm-smi` or sysfs), FPGAs (via `lspci`), memory, and framework availability
- **HealthReporter** (`mesh/agent/health_reporter.py`) -- collects CPU%, memory%, load averages, GPU utilization/temperature, disk usage, network bytes, uptime via `psutil`
- **ProbeRunner** (`mesh/agent/probe_runner.py`) -- runs micro-benchmarks (bandwidth, compute, LDS, WMMA, etc.) and produces atlas data points + invariants
- **SystemOptimizer** (`mesh/agent/system_optimizer.py`) -- applies OS tuning: CPU governor, hugepages, NUMA balancing, VM settings, scheduler, IO schedulers, network buffers
- **ProcessMonitor** (`mesh/agent/system_optimizer.py:ProcessMonitor`) -- event-driven process watcher that classifies and optimizes new processes
- **JobExecutor** (`mesh/agent/job_executor.py`) -- runs dispatched jobs (probe, benchmark, training, inference, custom)

### Controller (central)

The controller node runs:

- **Coordinator** (`mesh/controller/coordinator.py`) -- node registry, heartbeat processing, health check loop, atlas ingestion, job submission and dispatch
- **JEPATrainer** (`mesh/controller/jepa_trainer.py`) -- wraps the JEPA policy engine for periodic retraining and online feedback
- **JobRouter** (`mesh/controller/job_router.py`) -- scores nodes based on hardware capability, load, memory pressure, and JEPA predictions
- **FederatedAtlas** (`mesh/controller/federated_atlas.py`) -- merges per-node atlas databases into a unified view
- **MeshDB** (`mesh/db/mesh_db.py`) -- SQLite database for nodes, health history, jobs, atlas sync log, JEPA training log

### Dashboard

- **FastAPI + Jinja2** (`mesh/dashboard/app.py`) -- proxies requests to the controller API and renders HTML templates
- Pages: node overview, node detail, JEPA stats/schedule/training log, jobs, atlas summary, tuning
- SSE endpoint (`/api/sse`) pushes live health updates every 5 seconds

## Data Flow

```
Agent starts
  |
  +--> scan_hardware() --> HardwareInventory
  |
  +--> register with controller (POST /nodes/register)
  |      Controller stores in nodes table, returns api_token
  |
  +--> heartbeat loop (every 10s)
  |      collect_health() --> POST /nodes/{id}/heartbeat
  |      Controller records in health_history
  |
  +--> probe loop (every 6h)
  |      run_probes() --> atlas DB
  |      extract_atlas_data() --> POST /atlas/sync
  |      Controller ingests into federated atlas
  |
  +--> optimization loop (every 6h)
  |      SystemOptimizer.apply_all() --> sysctl, sysfs writes
  |      ProcessMonitor (continuous) --> NUMA bind, IO class, nice
  |
  +--> job execution (on dispatch from controller)
         POST /jobs/submit --> execute_job() --> POST result back
```

```
Controller starts
  |
  +--> load persisted nodes from DB
  +--> start health_check_loop (every 15s, marks stale nodes offline)
  +--> start retrain_loop (every 6h)
  |      Load all atlas DBs --> train JEPA --> push model to agents
  |
  +--> API serves requests from agents and dashboard
  +--> Dashboard mounted at /dashboard
```

## Agent Lifecycle

1. **Startup**: Agent scans hardware, generates a stable node_id from `hostname + arch`
2. **Registration**: Agent POSTs `NodeRegistration` to controller. Retries every 30s on failure.
3. **Heartbeat**: Every `heartbeat_interval_s` (default 10s), agent sends a `HealthSnapshot`. The controller updates `last_heartbeat` and records history.
4. **Probe**: Every `probe_interval_s` (default 6h), agent runs micro-benchmarks. Results are stored locally in an atlas DB, then synced to the controller.
5. **Optimization**: On the same interval, `SystemOptimizer.apply_all()` fetches recommendations from the controller and applies OS-level tuning. The `ProcessMonitor` runs continuously in a background thread, checking for new processes every 5s (or instantly via proc connector if running as root).
6. **Shutdown**: On SIGTERM/SIGINT, agent deregisters from controller (`DELETE /nodes/{id}?archive=true`), closes HTTP client, and exits cleanly.

## JEPA Training Pipeline

```
Atlas DBs (per node)
  |
  v
FederatedAtlas.get_federated_summary()
  |
  v
JEPAPolicyEngine.retrain(epochs=20, batch_size=512)
  |  - Loads all data points from atlas DBs
  |  - Extracts topological features (Betti numbers, persistence, spectral)
  |  - Trains 249K-param JEPA: 28D input -> 256D latent -> 6D perf prediction
  |  - Saves model to jepa_model_path
  |
  v
JEPATrainer._sync_model_to_agents()
  |  - Reads model file
  |  - POSTs to each agent's /jepa/sync endpoint (multipart form)
  |
  v
Agent saves model locally -> used by SystemOptimizer for recommendations
```

**Online learning**: The `/jepa/feedback` endpoint accepts real job performance data and updates the model incrementally (< 1ms per call).

**Retrain scheduling**: Default 6 hours, adjustable at runtime via `POST /jepa/schedule?interval_s=N`.

## Job Routing Algorithm

When a job is submitted to `POST /jobs/submit` without a `target_node`:

1. **Filter**: Only consider nodes with `status == ONLINE`
2. **Score** each node (higher is better):
   - Start with `score = 1.0`
   - **Load factor**: `score *= (1 - load_1m/cores * 0.7)` -- penalize busy nodes
   - **Memory pressure**: multiply by 0.3 if >90% used, 0.7 if >80%
   - **Hardware match** (for training/inference jobs):
     - GPU nodes: `score *= (1 + total_vram/24000)` -- scale by VRAM
     - AMD GPU bonus: `score *= 1.2`
     - No GPU penalty: `score *= 0.3`
   - **CPU capability**: `score *= (0.5 + 0.5 * min(cores/16, 2.0))`
   - **Memory availability**: `score *= (0.5 + 0.5 * min(available_gb/32, 1.0))`
   - **PyTorch bonus** (for ML jobs): `score *= 1.3`
3. **Select** the node with the highest score
4. **Dispatch**: Controller POSTs the job to the selected node's `/jobs/submit` endpoint

If a `target_node` is specified (by node_id or hostname), routing is skipped.

## Process Optimization

The `SystemOptimizer.optimize_processes()` method scans all running processes and applies per-process tuning:

**Classification rules** (in priority order):

| Pattern | Detection | NUMA Bind | IO Class | Nice |
|---|---|---|---|---|
| Vivado | name or cmdline contains "vivado" | yes (auto-balance) | best-effort | 0 |
| ML/Training | python + train/torch/tensorflow/jax/cuda/hip | yes (auto-balance) | best-effort | 0 |
| Compilers | gcc/g++/cc1/clang/rustc/javac | yes (auto-balance) | best-effort | 5 |
| Databases | postgres/mysql/mongod/redis | yes (pin to node 0) | best-effort | 0 |
| Java (heavy) | java with >500MB RSS | yes (auto-balance) | best-effort | 0 |
| Heavy generic | >2GB RSS and >4 threads | yes (auto-balance) | best-effort | 0 |

Processes under 100MB RSS (50MB for event-driven monitor) are skipped.

**ProcessMonitor** operates in two modes:
- **Netlink proc connector** (if root): instant fork/exec notifications
- **Polling** (fallback): scans `/proc` every 5 seconds

Debounce: a PID is not re-optimized within 60 seconds.

## Security Model

### Authentication

- **Opt-in**: Set `security.require_auth: true` in config
- **HMAC-SHA256 tokens**: Signed with a shared secret, contain node_id + role + expiry
- **Registration flow**: One-time registration token -> API token on successful registration
- **Token storage**: SQLite database (`mesh_tokens.db`) tracks all tokens with revocation support
- **AuthMiddleware**: Starlette middleware that validates Bearer tokens on every request (except exempt paths)

### Rate Limiting

- **ValidationMiddleware**: Enforces per-IP request limits (100/min default)
- **Payload size limits**: 1MB for JSON, 10MB for file uploads
- In-memory state, resets on restart

### CSRF Protection

- **CSRFMiddleware**: Checks `Origin`/`Referer` headers on state-changing requests (POST/PUT/PATCH/DELETE)
- JSON API requests are exempt (same-origin policy provides protection)
- Allowed origins are derived from the dashboard port configuration

### Security Headers

Applied to all dashboard responses:
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; ...`

### Systemd Hardening

Both service files include:
- `NoNewPrivileges=true`
- `ProtectSystem=strict` (read-only filesystem except allowed paths)
- `ProtectHome=read-only`
- `ReadWritePaths` limited to `data/`
- `PrivateTmp=true`

## Database Schema

SQLite with WAL mode. File: `data/mesh.db`.

### nodes

```sql
CREATE TABLE nodes (
    node_id TEXT PRIMARY KEY,
    hostname TEXT,
    agent_url TEXT,
    status TEXT DEFAULT 'unknown',     -- online, offline, degraded, archived
    hardware_json TEXT,                 -- JSON: HardwareInventory
    tags_json TEXT,                     -- JSON array of strings
    registered_at TEXT,                 -- ISO 8601
    last_heartbeat TEXT                 -- ISO 8601
);
```

### health_history

```sql
CREATE TABLE health_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    cpu_pct REAL,
    memory_used_pct REAL,
    load_1m REAL,
    load_5m REAL,
    load_15m REAL,
    gpu_utilizations_json TEXT,
    disk_used_pct REAL,
    uptime_s REAL,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);
-- Index: idx_health_node_ts ON health_history(node_id, timestamp)
```

### jobs

```sql
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT,
    command TEXT,
    args_json TEXT,
    status TEXT DEFAULT 'pending',     -- pending, running, completed, failed, cancelled
    assigned_node TEXT,
    submitted_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    result_json TEXT,
    error TEXT,
    priority INTEGER DEFAULT 0
);
-- Index: idx_jobs_status ON jobs(status)
```

### atlas_sync_log

```sql
CREATE TABLE atlas_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    atlas_db_name TEXT,
    data_points_count INTEGER,
    invariants_count INTEGER,
    synced_at TEXT
);
```

### jepa_training_log

```sql
CREATE TABLE jepa_training_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    training_samples INTEGER,
    epochs INTEGER,
    final_loss REAL,
    mean_error REAL,
    duration_s REAL,
    trained_at TEXT
);
```

### tokens (separate DB: mesh_tokens.db)

```sql
CREATE TABLE tokens (
    token_id TEXT PRIMARY KEY,
    token_type TEXT NOT NULL,          -- 'registration' or 'api'
    node_id TEXT,
    role TEXT DEFAULT 'node',
    token_value TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    used INTEGER DEFAULT 0,
    revoked INTEGER DEFAULT 0
);
```

## Concurrency Model

- **Agent**: Single Python process with asyncio event loop. Heartbeat, probe, registration, and optimization run as concurrent asyncio tasks. Probe and optimization use `run_in_executor()` to avoid blocking the event loop. ProcessMonitor runs in a dedicated daemon thread.
- **Controller**: Single Python process with asyncio. Health check and retrain loops run as background tasks. Job dispatch uses `asyncio.create_task()`.
- **Database**: SQLite with WAL mode for concurrent reads. Writes are serialized with a threading lock and retry on `SQLITE_BUSY` (3 retries, 100ms delay, plus 5s busy timeout).
- **HTTP client**: Shared `aiohttp.ClientSession` per agent/controller (not per-request) for connection pooling.

## Failover Design (Planned)

Leader election is designed but not yet implemented:

- **Approach**: Raft-lite or lowest-load-wins election among nodes with `election` capability
- **Split-brain prevention**: Fencing tokens to ensure only one leader is active
- **Failover target**: Automatic within 2 minutes of leader loss
- **State transfer**: DB replication or shared storage (NFS/S3) for the mesh.db and atlas data

Currently, the controller is a single point of failure. Agents continue to run independently when the controller is down -- they just cannot sync atlas data or receive new jobs.
