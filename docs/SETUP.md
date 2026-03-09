# Setup Guide

## Prerequisites

- Python 3.10+
- Linux (required for sysfs/procfs tuning; hardware scanning works on macOS with reduced features)
- System packages: `lscpu`, `lspci` (for hardware detection)
- Optional: `rocm-smi` (AMD GPU detection), `nvidia-smi` (NVIDIA GPU detection)
- Optional: PyTorch 2.0+ (required on the controller node for JEPA training)
- Optional: Docker and Docker Compose (for containerized deployment)

## Installation

### From source (recommended)

```bash
git clone <repo-url> mesh-production
cd mesh-production
python -m venv .venv
source .venv/bin/activate

# Agent-only install (lightweight, no PyTorch)
pip install -e .

# Controller install (includes PyTorch for JEPA)
pip install -e ".[controller]"

# Development install (adds pytest, coverage)
pip install -e ".[dev]"
```

### From pip

```bash
# Agent node
pip install mesh-optimizer

# Controller node
pip install mesh-optimizer[controller]
```

## Single-Node Quick Start

Run both the controller and an agent on the same machine:

```bash
# Start controller (also starts a local agent, dashboard at /dashboard)
mesh-controller --config mesh/mesh_config.yaml
```

Verify it is running:

```bash
# Controller health
curl http://localhost:8401/health

# Agent health
curl http://localhost:8400/health

# Dashboard
open http://localhost:8401/dashboard
```

## Multi-Node Deployment

### Controller Setup

1. Create or edit `mesh/mesh_config.yaml` on the controller machine:

```yaml
cluster_name: my-mesh
controller_url: http://controller-host:8401

node:
  name: controller-node
  host: 0.0.0.0
  agent_port: 8400

controller:
  host: 0.0.0.0
  api_port: 8401
  dashboard_port: 8402
  heartbeat_interval_s: 10
  node_timeout_s: 120
  probe_interval_s: 21600
  retrain_interval_s: 21600
  atlas_dir: data
  jepa_model_path: data/jepa_policy_model.pt

security:
  require_auth: false
  rate_limit: 100

log_level: INFO
db_path: data/mesh.db
```

2. Open firewall ports:

```bash
# Controller API
sudo ufw allow 8401/tcp
# Dashboard (if running standalone)
sudo ufw allow 8402/tcp
# Agent API (if controller also runs an agent)
sudo ufw allow 8400/tcp
```

3. Start the controller:

```bash
mesh-controller --config mesh/mesh_config.yaml
```

### Agent Deployment to Remote Nodes

1. Copy the project to the remote node:

```bash
rsync -az --exclude='.venv' --exclude='data/' \
  mesh-production/ user@agent-host:~/mesh-production/
```

2. Install on the remote node:

```bash
ssh user@agent-host
cd ~/mesh-production
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

3. Create the agent config (or copy from controller and edit):

```yaml
cluster_name: my-mesh
controller_url: http://controller-host:8401

node:
  name: agent-gpu-1
  host: 0.0.0.0
  agent_port: 8400
  tags:
    - gpu
    - amd

log_level: INFO
```

4. Start the agent:

```bash
mesh-agent --config mesh/mesh_config.yaml
```

5. Install as a systemd service for persistence:

```bash
sudo cp systemd/mesh-agent.service /etc/systemd/system/
# Edit the service file to match your paths and user
sudo systemctl daemon-reload
sudo systemctl enable --now mesh-agent
```

### Verifying the Mesh

```bash
# Check controller sees all nodes
curl http://controller-host:8401/nodes | python -m json.tool

# Check specific agent health
curl http://agent-host:8400/health | python -m json.tool

# Check JEPA status
curl http://controller-host:8401/jepa/stats | python -m json.tool

# Check atlas data
curl http://controller-host:8401/atlas/summary | python -m json.tool
```

## Docker Deployment

The `docker/docker-compose.yml` provides a full stack with one controller and two agents:

```bash
cd docker
docker compose up -d
```

This starts:
- `mesh-controller` on ports 8401 (API) and 8402 (dashboard)
- `mesh-agent-1` on port 8410 (mapped from internal 8400)
- `mesh-agent-2` on port 8411 (mapped from internal 8400)

Check status:

```bash
docker compose ps
docker compose logs controller
docker compose logs agent-1
```

Scale agents:

```bash
# Add more agents by defining new services in docker-compose.yml
# or use docker compose scale (if using a generic agent service)
```

Environment variables for Docker agents:

| Variable | Description | Default |
|---|---|---|
| `MESH_CONTROLLER_URL` | Controller URL | `http://controller:8401` |
| `MESH_NODE_NAME` | Agent node name | hostname |
| `MESH_LOG_LEVEL` | Log level | `INFO` |

## Systemd Service Installation

### Agent service

```bash
# Copy the service file
sudo cp systemd/mesh-agent.service /etc/systemd/system/

# Edit paths if your installation differs
sudo systemctl edit mesh-agent.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now mesh-agent

# Check status
sudo systemctl status mesh-agent
journalctl -u mesh-agent -f
```

### Controller service

```bash
sudo cp systemd/mesh-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mesh-controller

journalctl -u mesh-controller -f
```

Both services are configured with systemd hardening:
- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `ProtectHome=read-only`
- `ReadWritePaths` limited to the `data/` directory
- `PrivateTmp=true`
- Restart on failure with 5s delay

## Configuration Reference

All configuration is in `mesh/mesh_config.yaml`. Every field can also be set programmatically via `MeshConfig.from_yaml()`.

### Top-level

| Field | Type | Default | Description |
|---|---|---|---|
| `cluster_name` | string | `rdna3-mesh` | Name of the mesh cluster |
| `controller_url` | string | `http://localhost:8401` | URL agents use to reach the controller |
| `log_level` | string | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR |
| `db_path` | string | `data/mesh.db` | Path to the SQLite database file |
| `known_nodes` | list | `[]` | Pre-configured node list (for static discovery) |

### `node` section

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | hostname | Human-readable node name |
| `host` | string | `0.0.0.0` | Bind address for the agent API |
| `agent_port` | int | `8400` | Port for the agent REST API |
| `ssh_user` | string | `craighass` | SSH user for remote operations |
| `ssh_key` | string | `~/.ssh/id_ed25519` | SSH key path |
| `tags` | list | `[]` | Labels for this node (e.g., `gpu`, `amd`, `fpga`) |

### `controller` section

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `0.0.0.0` | Bind address for controller API |
| `api_port` | int | `8401` | Controller REST API port |
| `dashboard_port` | int | `8402` | Dashboard port (when running standalone) |
| `heartbeat_interval_s` | float | `10.0` | How often agents send heartbeats |
| `controller_heartbeat_s` | float | `15.0` | How often controller checks for stale nodes |
| `node_timeout_s` | float | `120.0` | Seconds before a silent node is marked offline |
| `probe_interval_s` | float | `21600.0` | Seconds between probe runs (default 6 hours) |
| `retrain_interval_s` | float | `21600.0` | Seconds between JEPA retraining (default 6 hours) |
| `atlas_dir` | string | `data` | Directory for federated atlas databases |
| `jepa_model_path` | string | `data/jepa_policy_model.pt` | Path to the JEPA model file |

### `security` section

| Field | Type | Default | Description |
|---|---|---|---|
| `auth_secret` | string | (auto-generated) | HMAC-SHA256 signing secret. Auto-generated if empty. Set explicitly to share across restarts. |
| `require_auth` | bool | `false` | Enable token-based authentication on all endpoints |
| `token_ttl_hours` | int | `24` | Default API token TTL in hours |
| `token_db_path` | string | `data/mesh_tokens.db` | Path to the token SQLite database |
| `rate_limit` | int | `100` | Maximum requests per minute per IP address |

## Environment Variable Overrides

Environment variables can be used in Docker and systemd deployments. They are read by the start scripts:

| Variable | Maps to | Example |
|---|---|---|
| `MESH_CONTROLLER_URL` | `controller_url` | `http://10.0.0.1:8401` |
| `MESH_NODE_NAME` | `node.name` | `gpu-node-3` |
| `MESH_LOG_LEVEL` | `log_level` | `DEBUG` |

For Docker, set these in `docker-compose.yml` under `environment:`.

## Enabling Authentication

Authentication is opt-in. To enable it:

1. Set a stable secret in your config (or it regenerates on every restart):

```yaml
security:
  require_auth: true
  auth_secret: "your-64-char-hex-secret-here"
  token_ttl_hours: 8760  # 1 year for long-lived tokens
  rate_limit: 100
```

2. Generate the secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

3. Start the controller. It will now require Bearer tokens on all endpoints except `/health`, `/docs`, and `/nodes/register`.

4. Generate a registration token for each new agent:

```python
from mesh.security.tokens import TokenStore
store = TokenStore(db_path="data/mesh_tokens.db")
reg_token = store.create_registration_token(
    secret="your-64-char-hex-secret-here",
    ttl_hours=1,  # Token expires in 1 hour
)
print(f"Registration token: {reg_token}")
```

5. The agent uses this registration token when calling `POST /nodes/register`. On success, the controller returns an API token in the response that the agent uses for all subsequent requests.

6. The registration token is single-use and auto-expires. API tokens are long-lived (default 1 year) and can be revoked:

```python
store.revoke_token(token_id="abc123")
```

### Rate Limiting

When `require_auth` is true, the `ValidationMiddleware` enforces:
- **100 requests/minute per IP** (configurable via `rate_limit`)
- **1 MB max JSON payload**
- **10 MB max file upload** (for JEPA model sync)

Exceeding limits returns HTTP 429 (Too Many Requests) or 413 (Payload Too Large).

### Dashboard Security

The dashboard automatically applies:
- Security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`, CSP, `X-XSS-Protection`
- CSRF protection on state-changing requests (checks `Origin`/`Referer` headers)
- JSON API requests are exempt from CSRF (protected by SOP)
