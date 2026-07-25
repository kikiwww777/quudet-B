# Control Plane and Agent Deployment Design

Date: 2026-07-25
Status: Proposed

## Goal

Keep one QuuDet repository while separating runtime responsibilities so a control-plane host can optionally execute training and remote worker nodes can run only the training agent.

## Roles

### Control plane

The control-plane host runs the API, web UI, PostgreSQL, Redis, Celery, migrations, scheduling, experiment persistence, and resource-manifest management.

### Local control-plane agent

The control-plane host also runs `python -m app.agent.runner` as a separate, default-on process. It registers as `control-gpu-01` with `NODE_KIND=local` and communicates with the local API through `http://127.0.0.1:8000`.

The agent must not execute inside the API or Celery process. API restarts or control-plane maintenance must not terminate active training.

### Remote agent

A remote worker runs only `python -m app.agent.runner`. It claims jobs and provisioning plans through HTTP, caches resources locally, runs YOLO, and uploads logs, metrics, and results. It does not host API routes and does not connect directly to the control-plane database or Redis.

## Dependency profiles

### requirements-control.txt

Contains the current control-plane runtime: FastAPI, Uvicorn, SQLAlchemy, Alembic, PostgreSQL driver, Redis, Celery, authentication dependencies, and other API-only dependencies.

### requirements-agent.txt

Contains only agent execution dependencies: Python 3.11, the CUDA-compatible Torch build, Ultralytics, psutil, pydantic-settings if retained for agent configuration, and the small set of pure Python utilities used by the agent.

It must not contain FastAPI, Uvicorn, Celery, Redis, psycopg2, Alembic, or server-side authentication dependencies.

The control-plane host installs both profiles when it has a usable GPU. A remote worker installs only the agent profile.

## Code boundary

Agent-imported modules must be free of database models and API dependencies.

- Move CSV parsing and epoch-metric helpers to a pure shared module.
- Keep database-backed job lookup and persistence in control-plane modules.
- Keep agent command construction, provisioning, and HTTP protocol code within agent-safe or shared modules.
- Add an import smoke test proving the agent can import without PostgreSQL, Redis, Celery, or FastAPI packages installed.

## Configuration

Local control-plane agent configuration:

```env
MASTER_API_BASE=http://127.0.0.1:8000
NODE_ID=control-gpu-01
NODE_KIND=local
NODE_MAX_CONCURRENCY=1
```

Remote-agent configuration:

```env
MASTER_API_BASE=http://10.120.78.70:8000
NODE_ID=node-linux-01
NODE_KIND=remote
NODE_TOKEN=<node-specific-secret>
NODE_MAX_CONCURRENCY=1
```

Remote agents require Python 3.11, NVIDIA driver/CUDA support when using a GPU, Git, curl, tar, unzip, local resource-cache storage, and reachability to the control-plane API. They do not require PostgreSQL or Redis services.

## Startup behavior

`restart.bat` starts the control plane as it does now and also starts the local control-plane agent in a separate hidden process. The local agent has its own log file and restart behavior. It receives the same scheduling protocol as remote agents and competes for work normally.

## Validation

1. Build an agent-only Python 3.11 virtual environment.
2. Install `requirements-agent.txt` with no PostgreSQL, Redis, Celery, or FastAPI packages.
3. Import and start the agent; verify registration and heartbeat.
4. Run a provisioning plan and a YOLO job through the agent.
5. Start the control plane with `restart.bat`; verify it starts API, existing services, and the local agent independently.
6. Verify a remote agent and the local agent both receive and complete scheduled work.