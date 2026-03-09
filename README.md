# Mesh Optimizer

**Distributed hardware optimization for heterogeneous compute clusters.**

Mesh Optimizer automatically discovers, benchmarks, and optimizes hardware across your infrastructure. Deploy the agent on any machine — it connects to a central controller that orchestrates optimization across your entire fleet.

Supports **AMD GPUs**, **NVIDIA GPUs**, **Intel/AMD CPUs**, **FPGAs**, and **memory subsystems** across Linux and macOS.

## Quick Start

### Install from PyPI

```bash
pip install mesh-optimizer
```

### Or install from source

```bash
git clone https://github.com/slentoai/mesh-optimizer.git
cd mesh-optimizer
pip install -e .
```

### Configure

```bash
cp mesh_config.example.yaml mesh_config.yaml
# Edit mesh_config.yaml — set your controller_url and optional license key
```

### Run

```bash
# Start the agent
mesh-optimizer start --config mesh_config.yaml

# Check status
mesh-optimizer status

# View detected hardware
mesh-optimizer hardware

# Stop the agent
mesh-optimizer stop
```

## How It Works

```
+-------------------+         HTTPS          +---------------------+
|   Mesh Agent      | --------------------> |   Mesh Controller    |
|   (this package)  | <-------------------- |   (proprietary)      |
|                   |                        |                      |
|  - HW discovery   |    register            |  - Job routing       |
|  - Health metrics  |    heartbeat           |  - Optimization      |
|  - Probe runner    |    probe results       |  - Dashboard         |
|  - Job executor    |    job dispatch        |  - Analytics         |
+-------------------+                        +---------------------+
```

The **agent** (this package) runs on each node in your cluster. It:

1. **Discovers hardware** — CPUs, GPUs (AMD and NVIDIA), FPGAs, memory
2. **Registers with the controller** — sends hardware inventory and metadata
3. **Sends heartbeats** — CPU/GPU utilization, memory, temperatures, load
4. **Runs benchmark probes** — measures baseline hardware performance
5. **Executes jobs** — runs tasks dispatched by the controller

The **controller** is the central brain that analyzes data from all agents and makes optimization decisions. It is available as a pre-built binary from [portal.slentosystems.com](https://portal.slentosystems.com).

## NAT-Friendly

Agents behind firewalls or NAT work out of the box. Enable NAT mode in your config:

```yaml
node:
  nat_mode: true
  nat_poll_interval_s: 15
```

In NAT mode, the agent only makes **outbound** connections to the controller — no port forwarding required.

## Configuration

See [`mesh_config.example.yaml`](mesh_config.example.yaml) for all available options.

Key settings:

| Setting | Description |
|---------|-------------|
| `controller_url` | URL of your Mesh Optimizer controller |
| `node.agent_port` | Port for the agent API (default: 8400) |
| `node.nat_mode` | Enable for agents behind NAT/firewalls |
| `node.tags` | Tags for job routing (e.g., `["gpu", "amd"]`) |
| `licensing.license_key` | License key from the portal (optional) |
| `security.verify_tls` | Verify TLS certificates (default: true) |

## Licensing

Mesh Optimizer is free to use in **community mode** with unlimited nodes and basic features including hardware discovery, health monitoring, job routing, and dashboard access.

**Professional** and **Enterprise** tiers unlock continuous optimization, auto-tuning, and advanced analytics. Get a license key at [portal.slentosystems.com](https://portal.slentosystems.com).

### Automatic Controller Setup

When you add a Professional or Enterprise license key to your config, the agent automatically:

1. Validates your key against the Slento Systems portal
2. Downloads the controller binary for your platform
3. Starts the controller locally alongside the agent
4. Keeps the controller up to date on future launches

No manual controller installation required — just add your license key and start the agent.

```yaml
licensing:
  license_key: "MESH-XXXX-XXXX-XXXX-XXXX"  # From portal.slentosystems.com
```

```bash
mesh-optimizer start  # Downloads controller, starts everything
```

## API Endpoints

When running in standard mode (not NAT), the agent exposes:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Agent health and metrics |
| `/hardware` | GET | Detected hardware inventory |
| `/probe/run` | POST | Trigger a benchmark probe |
| `/jobs/submit` | POST | Submit a job for execution |
| `/jobs/{id}` | GET | Get job status |

## Requirements

- Python 3.10+
- Linux or macOS
- For GPU detection: `nvidia-smi` (NVIDIA) or `rocm-smi` (AMD)
- For FPGA detection: `lspci`

## Documentation

Full documentation is available at [docs.slentosystems.com](https://docs.slentosystems.com).

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

---

*The controller is available as a pre-built binary from [portal.slentosystems.com](https://portal.slentosystems.com).*
