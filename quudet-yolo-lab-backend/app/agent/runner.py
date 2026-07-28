"""Unified node agent runner — local and remote execution nodes.

All execution environments (local Windows/Linux, remote GPU servers)
run this agent to claim and execute YOLO tasks.

Environment variables:
    MASTER_API_BASE       — quudet API base URL (default: http://127.0.0.1:8000)
    NODE_ID               — unique node identifier (default: hostname)
    NODE_NAME             — human-readable name (default: NODE_ID)
    NODE_TOKEN            — auth token (empty = local node, uses DISABLE_AUTH)
    NODE_MAX_CONCURRENCY  — max parallel jobs (default: 1)
    POLL_INTERVAL_SECONDS — claim-next poll interval (default: 4)
    HEARTBEAT_INTERVAL_SECONDS — heartbeat interval (default: 5)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

from app.agent.runtime_paths import get_agent_paths
from app.shared.train_metrics import (
    epoch_progress,
    parse_results_csv,
    resolve_results_csv_for_train,
)
from app.services.yolo_runner import build_command

# Import the ResourceProvisioner (lazy — only used on Linux nodes)
try:
    from app.agent.resource_provisioner import ResourceProvisioner
except ImportError:
    ResourceProvisioner = None  # type: ignore


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _generate_node_token(node_id: str) -> str:
    """Generate a deterministic token for a local node.

    Uses the node ID + a machine-local fingerprint so the token is
    stable across restarts but not portable across machines.
    """
    raw = f"{node_id}:local-node:{socket.gethostname()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


MASTER_API_BASE = _env("MASTER_API_BASE", "http://127.0.0.1:8000").rstrip("/")
NODE_ID = _env("NODE_ID", socket.gethostname())
NODE_NAME = _env("NODE_NAME", NODE_ID)
NODE_TOKEN = _env("NODE_TOKEN", "")
if not NODE_TOKEN:
    NODE_TOKEN = _generate_node_token(NODE_ID)
NODE_MAX_CONCURRENCY = max(1, int(_env("NODE_MAX_CONCURRENCY", "1")))
POLL_INTERVAL = max(2, int(_env("POLL_INTERVAL_SECONDS", "4")))
HEARTBEAT_INTERVAL = max(3, int(_env("HEARTBEAT_INTERVAL_SECONDS", "5")))

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STATE = {
    "running_jobs": 0,
    "active_job_id": None,
    "active_pid": None,
    "active_command": None,
    "phase": "idle",
    "last_output_at": None,
    "exit_code": None,
}
_STATIC_CAPABILITIES: dict | None = None
_AGENT_INSTANCE_ID = str(uuid.uuid4())


def _set_runtime(**values: object) -> None:
    with _RUNTIME_LOCK:
        _RUNTIME_STATE.update(values)


def _runtime_snapshot() -> dict:
    with _RUNTIME_LOCK:
        return dict(_RUNTIME_STATE)


# ---------------------------------------------------------------------------
# Node capability auto-detection
# ---------------------------------------------------------------------------

def collect_node_capabilities() -> dict:
    """Auto-detect node capabilities — OS, Python, GPU, YOLO, etc.

    Returns a dict suitable for the ``capabilities`` field of
    ``ComputeNode``.

    ``node_kind`` is set from the ``NODE_KIND`` env var (default ``local``).
    This is a deployment-level decision — the admin knows whether a node
    is local or remote — and cannot be reliably inferred from IP or hostname
    because topology may change over time.
    """
    global _STATIC_CAPABILITIES
    if _STATIC_CAPABILITIES is not None:
        caps = dict(_STATIC_CAPABILITIES)
        caps["agent_runtime"] = _runtime_snapshot()
        return caps

    caps: dict = {
        "node_kind": _env("NODE_KIND", "local"),
        "hostname": socket.gethostname(),
        "platform": sys.platform,
        "os_type": platform.system().lower(),
        "os_release": platform.release(),
        "python_version": sys.version.split()[0],
        "started_at": datetime.utcnow().isoformat(),
        "agent_instance_id": _AGENT_INSTANCE_ID,
        "path_style": "windows" if os.name == "nt" else "posix",
    }

    # --- torch / CUDA ---
    try:
        import torch
        caps["torch_version"] = torch.__version__
        caps["has_gpu"] = torch.cuda.is_available()
        if caps["has_gpu"]:
            caps["gpu_count"] = torch.cuda.device_count()
            caps["gpu_names"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
            caps["cuda_available"] = True
            try:
                caps["cuda_version"] = torch.version.cuda
            except Exception:
                pass
            try:
                caps["vram_gb"] = [round(torch.cuda.get_device_properties(i).total_memory / 1e9, 1)
                                   for i in range(torch.cuda.device_count())]
            except Exception:
                caps["vram_gb"] = []
        else:
            caps["gpu_count"] = 0
            caps["gpu_names"] = []
            caps["cuda_available"] = False
    except Exception:
        caps["torch_version"] = None
        caps["has_gpu"] = False
        caps["gpu_count"] = 0
        caps["cuda_available"] = False

    # --- ultralytics ---
    try:
        import ultralytics
        caps["ultralytics_version"] = ultralytics.__version__
        caps["yolo_cli_available"] = True
    except Exception:
        caps["ultralytics_version"] = None
        caps["yolo_cli_available"] = shutil.which("yolo") is not None

    # --- CPU / memory ---
    try:
        caps["cpu_count"] = os.cpu_count() or 0
    except Exception:
        caps["cpu_count"] = 0

    try:
        import psutil
        caps["memory_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
        caps["disk_free_gb"] = round(psutil.disk_usage("/").free / 1e9, 1)
    except Exception:
        caps["memory_gb"] = None
        caps["disk_free_gb"] = None

    _STATIC_CAPABILITIES = caps
    result = dict(caps)
    result["agent_runtime"] = _runtime_snapshot()
    return result


def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = Request(
        f"{MASTER_API_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except HTTPError as e:
        raise RuntimeError(f"{path} HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e
    except URLError as e:
        raise RuntimeError(f"{path} network error: {e}") from e


def _get(path: str) -> dict:
    """HTTP GET with JSON response.  Used by the provisioning subsystem."""
    req = Request(
        f"{MASTER_API_BASE}{path}",
        headers={"User-Agent": "QuuDet-Agent/1.0"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except HTTPError as e:
        raise RuntimeError(f"{path} HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e
    except URLError as e:
        raise RuntimeError(f"{path} network error: {e}") from e


def _download_dataset_bundle(job_id: str, dataset_id: int) -> tuple[Path, str]:
    paths = get_agent_paths()
    node_ds_root = paths.artifacts_dir / "node_datasets"
    node_ds_root.mkdir(parents=True, exist_ok=True)
    cache_dir = node_ds_root / str(dataset_id)
    zip_path = node_ds_root / f"{dataset_id}.zip"

    query = urlencode({"node_id": NODE_ID, "token": NODE_TOKEN})
    url = f"{MASTER_API_BASE}/api/v1/dispatch/job-dataset/{job_id}?{query}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=300) as resp:
            yaml_rel = (resp.headers.get("x-data-yaml-rel") or "").strip().replace("\\", "/")
            if not yaml_rel:
                raise RuntimeError("missing dataset yaml header from master")
            with zip_path.open("wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except HTTPError as e:
        raise RuntimeError(f"dataset download HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e
    except URLError as e:
        raise RuntimeError(f"dataset download network error: {e}") from e

    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(cache_dir)

    yaml_local = (cache_dir / yaml_rel).resolve()
    if not yaml_local.is_file():
        raise RuntimeError(f"dataset yaml missing after extract: {yaml_local}")
    return yaml_local, yaml_rel


def _download_job_bundle(job_id: str) -> Path:
    """Download the job snapshot/config bundle from master and extract it.

    Returns the extraction directory (under node cache).
    """
    paths = get_agent_paths()
    cache_root = paths.artifacts_dir / "node_job_bundles"
    cache_root.mkdir(parents=True, exist_ok=True)
    bundle_dir = cache_root / job_id
    zip_path = cache_root / f"{job_id}.zip"

    query = urlencode({"node_id": NODE_ID, "token": NODE_TOKEN})
    url = f"{MASTER_API_BASE}/api/v1/dispatch/job-bundle/{job_id}?{query}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=120) as resp:
            with zip_path.open("wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except HTTPError as e:
        # Bundle is optional — don't fail the job if not available
        if e.code == 404:
            return bundle_dir  # empty dir, caller handles
        raise RuntimeError(f"job bundle download HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e
    except URLError as e:
        raise RuntimeError(f"job bundle download network error: {e}") from e

    if bundle_dir.exists():
        shutil.rmtree(bundle_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(bundle_dir)
    return bundle_dir


def _job_started_at(job: dict) -> datetime | None:
    raw = job.get("started_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _metrics_for_job(job: dict) -> dict | None:
    paths = get_agent_paths()
    work_dir = paths.yolo_work_dir
    job_dir = paths.artifacts_dir / str(job.get("id"))
    csv_path = resolve_results_csv_for_train(
        payload=dict(job.get("payload") or {}),
        work_dir=work_dir,
        job_dir=job_dir,
        started_at=_job_started_at(job),
        job_type=str(job.get("job_type") or ""),
    )
    if not csv_path:
        return None
    return parse_results_csv(csv_path)


def _emit_event(job_id: str, event_type: str, payload: dict) -> None:
    _post(
        "/api/v1/dispatch/events",
        {
            "node_id": NODE_ID,
            "token": NODE_TOKEN,
            "event_type": event_type,
            "job_id": job_id,
            "payload": payload,
        },
    )


def register_node() -> None:
    _post(
        "/api/v1/nodes/register",
        {
            "node_id": NODE_ID,
            "display_name": NODE_NAME,
            "token": NODE_TOKEN,
            "base_url": "",
            "max_concurrent_jobs": NODE_MAX_CONCURRENCY,
            "capabilities": collect_node_capabilities(),
        },
    )


def claim_next_job() -> dict | None:
    res = _post("/api/v1/dispatch/claim-next", {"node_id": NODE_ID, "token": NODE_TOKEN})
    if not res.get("claimed"):
        return None
    return res.get("job")


def execute_job(job: dict) -> None:
    job_id = str(job.get("id"))
    paths = get_agent_paths()
    work_dir = paths.yolo_work_dir
    payload = dict(job.get("payload") or {})
    job_type = str(job.get("job_type"))
    dataset_id = job.get("dataset_id")
    _set_runtime(active_job_id=job_id, active_pid=None, active_command=None, phase="preparing", last_output_at=datetime.utcnow().isoformat(), exit_code=None)

    # --- Step 0: Download job snapshot/config bundle ---
    bundle_dir: Path | None = None
    try:
        bundle_dir = _download_job_bundle(job_id)
        _emit_event(job_id, "log", {"text": f"# job bundle downloaded to {bundle_dir}\n"})
    except Exception as e:
        _emit_event(job_id, "log", {"text": f"# job bundle download skipped: {e}\n"})
        bundle_dir = None

    # --- Use snapshot yamls to override payload if available ---
    if bundle_dir is not None:
        # Prefer snapshot model yaml over payload model
        model_snap = bundle_dir / "snapshot" / "model_snapshot.yaml"
        if model_snap.is_file():
            payload["model"] = str(model_snap).replace("\\", "/")
            _emit_event(job_id, "log", {"text": f"# using snapshot model: {payload['model']}\n"})

        # Prefer snapshot data yaml over payload data
        data_snap = bundle_dir / "snapshot" / "data_snapshot.yaml"
        if data_snap.is_file():
            payload["data"] = str(data_snap).replace("\\", "/")
            _emit_event(job_id, "log", {"text": f"# using snapshot data: {payload['data']}\n"})

        # If code snapshot exists, add to PYTHONPATH or use as workspace reference
        code_dir = bundle_dir / "code"
        if code_dir.is_dir() and any(code_dir.iterdir()):
            _emit_event(job_id, "log", {"text": f"# code snapshot available at {code_dir}\n"})

        # Log snapshot info for reproducibility
        env_snap = bundle_dir / "snapshot" / "env_snapshot.json"
        if env_snap.is_file():
            try:
                _emit_event(job_id, "log", {"text": f"# env snapshot: {env_snap.read_text('utf-8')[:500]}\n"})
            except Exception:
                pass

    # --- Step 1: Check if dataset is in local provision cache ---
    cache_data_yaml = _resolve_cached_data(payload, dataset_id)
    if cache_data_yaml:
        payload["data"] = str(cache_data_yaml).replace("\\", "/")
        _emit_event(job_id, "log", {"text": f"# using provisioned cache data: {payload['data']}\n"})
    elif dataset_id:
        # --- Step 1b: Download dataset bundle (legacy path) ---
        try:
            ds_id = int(dataset_id)
            yaml_local, yaml_rel = _download_dataset_bundle(job_id, ds_id)
            payload["data"] = str(yaml_local).replace("\\", "/")
            _emit_event(job_id, "log", {"text": f"# dataset synced dataset_id={ds_id} yaml={yaml_rel}\n"})
        except Exception as e:
            _emit_event(job_id, "summary", {"error_message": f"dataset sync failed: {e}"})
            _emit_event(job_id, "status", {"status": "FAILED"})
            return

    payload["project"] = str(paths.artifacts_dir)
    payload["name"] = job_id
    cmd = build_command(
        job_type,
        payload,
        paths.artifacts_dir / job_id,
        work_dir=work_dir,
    )
    _set_runtime(active_command=" ".join(cmd), phase="starting")
    _emit_event(job_id, "status", {"status": "RUNNING"})
    _emit_event(job_id, "log", {"text": f"# agent node={NODE_ID}\n# cmd={' '.join(cmd)}\n"})

    proc = subprocess.Popen(
        cmd,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    _set_runtime(active_pid=proc.pid, phase="running", last_output_at=datetime.utcnow().isoformat())

    line_buffer: list[str] = []
    last_metrics_push = 0.0
    last_progress_pct = -1
    while True:
        line = proc.stdout.readline() if proc.stdout else ""
        if line:
            _set_runtime(last_output_at=datetime.utcnow().isoformat())
            line_buffer.append(line)
            if len(line_buffer) >= 8:
                _emit_event(job_id, "log", {"text": "".join(line_buffer)})
                line_buffer = []
        if proc.poll() is not None:
            break
        now = time.time()
        if now - last_metrics_push > 8:
            last_metrics_push = now
            snap = _metrics_for_job(job)
            if snap:
                _emit_event(job_id, "metrics", {"metrics": snap})
                total_epochs = int((payload or {}).get("epochs") or 0)
                prog = epoch_progress(snap.get("x") or [], total_epochs, status="RUNNING")
                if prog["progress_percent"] > last_progress_pct:
                    last_progress_pct = prog["progress_percent"]
                    _emit_event(
                        job_id,
                        "progress",
                        {
                            "progress": prog["progress_percent"],
                            "epochs_done": prog["epochs_done"],
                            "epochs_total": prog["epochs_total"],
                        },
                    )

    if line_buffer:
        _emit_event(job_id, "log", {"text": "".join(line_buffer)})

    code = proc.wait()
    _set_runtime(phase="finished", exit_code=code, last_output_at=datetime.utcnow().isoformat())
    snap = _metrics_for_job(job)
    if snap:
        _emit_event(job_id, "metrics", {"metrics": snap})
    total_epochs = int((payload or {}).get("epochs") or 0)
    if snap:
        final_prog = epoch_progress(
            snap.get("x") or [],
            total_epochs,
            status="SUCCESS" if code == 0 else "RUNNING",
        )
        pct = 100 if code == 0 else final_prog["progress_percent"]
    else:
        pct = 100 if code == 0 else max(0, last_progress_pct)
    _emit_event(
        job_id,
        "progress",
        {
            "progress": pct,
            "epochs_done": (snap and epoch_progress(snap.get("x") or [], total_epochs)["epochs_done"]) or 0,
            "epochs_total": total_epochs,
        },
    )
    if code == 0:
        _emit_event(job_id, "summary", {"result_summary": "remote node finished successfully"})
        _emit_event(job_id, "status", {"status": "SUCCESS"})
    else:
        _emit_event(job_id, "summary", {"error_message": f"remote yolo exited with {code}"})
        _emit_event(job_id, "status", {"status": "FAILED"})


# ═══════════════════════════════════════════════════════════════════════════
# Resource Provisioning (Linux agent)
# ═══════════════════════════════════════════════════════════════════════════


def _provision_cache_root() -> Path:
    """Return the node-local cache root directory.

    Override via ``PROVISION_CACHE_ROOT`` env var.  On Linux defaults to
    ``/srv/quudet/cache`` (auto-created if missing); falls back to the
    agent-local data directory.
    """
    env = os.getenv("PROVISION_CACHE_ROOT", "").strip()
    if env:
        return Path(env)
    if sys.platform == "win32":
        return get_agent_paths().provision_cache_dir
    # Linux: try /srv/quudet/cache, auto-create if parent exists
    default = Path("/srv/quudet/cache")
    try:
        default.mkdir(parents=True, exist_ok=True)
        return default
    except (OSError, PermissionError):
        # Fallback to agent-local cache
        return get_agent_paths().provision_cache_dir


def _get_provisioner() -> ResourceProvisioner | None:
    if ResourceProvisioner is None:
        return None
    if not hasattr(_get_provisioner, "_instance"):
        _get_provisioner._instance = ResourceProvisioner(_provision_cache_root())
    return _get_provisioner._instance


def _emit_provision_event(provision_id: str, event_type: str, payload: dict) -> None:
    _post(
        f"/api/v1/provisioning/{provision_id}/events",
        {
            "node_id": NODE_ID,
            "token": NODE_TOKEN,
            "event_type": event_type,
            "provision_id": provision_id,
            "payload": payload,
        },
    )



def _build_node_auth_query(node_id: str, token: str) -> str:
    """Encode node credentials safely for a URL query string."""
    return urlencode({"node_id": node_id, "token": token})

def claim_next_provision() -> dict | None:
    """Claim the next pending provisioning plan from the master."""
    try:
        res = _post("/api/v1/provisioning/claim-next", {"node_id": NODE_ID, "token": NODE_TOKEN})
        if not res.get("claimed"):
            return None
        return res.get("provision")
    except Exception as e:
        logger.warning("claim-next-provision failed: %s", e)
        return None


def execute_provision(provision: dict) -> None:
    """Execute one provision plan: download, verify, extract, cache, report."""
    provision_id = str(provision.get("id", ""))
    manifest_id = str(provision.get("manifest_id", ""))

    if not provision_id or not manifest_id:
        logger.error("Invalid provision payload: missing id/manifest_id")
        _emit_provision_event(provision_id or "unknown", "status", {"state": "FAILED", "error_message": "invalid payload"})
        return

    logger.info("Provisioning start: id=%s manifest=%s", provision_id, manifest_id)

    # Fetch manifest via node-authenticated GET endpoint
    try:
        manifest_resp = _get(
            f"/api/v1/resources/manifests/{manifest_id}/for-node?{_build_node_auth_query(NODE_ID, NODE_TOKEN)}"
        )
    except Exception as e:
        logger.error("Failed to fetch manifest %s: %s", manifest_id, e)
        _emit_provision_event(provision_id, "status", {"state": "FAILED", "error_message": f"manifest fetch failed: {e}"})
        return

    def on_progress(pct: int, bytes_dl: int) -> None:
        _emit_provision_event(provision_id, "progress", {"progress": pct, "bytes_downloaded": bytes_dl})

    def on_log(msg: str) -> None:
        _emit_provision_event(provision_id, "log", {"text": f"{msg}\n"})

    _emit_provision_event(provision_id, "status", {"state": "DOWNLOADING"})

    provisioner = _get_provisioner()
    try:
        receipt = provisioner.provision(
            manifest=manifest_resp,
            provision_id=provision_id,
            on_progress=on_progress,
            on_log=on_log,
        )
        _emit_provision_event(provision_id, "receipt", receipt)
        logger.info("Provision READY: id=%s uri=%s", provision_id, receipt.get("local_uri"))
    except RuntimeError as e:
        logger.error("Provision FAILED: id=%s error=%s", provision_id, e)
        _emit_provision_event(provision_id, "status", {"state": "FAILED", "error_message": str(e)})
    except Exception as e:
        logger.exception("Provision unexpected error: id=%s", provision_id)
        _emit_provision_event(provision_id, "status", {"state": "FAILED", "error_message": f"unexpected: {e}"})


def run_provision_loop() -> int:
    """Claim and execute pending provisioning plans.

    Returns the number of plans executed (0 if none).  The caller should
    call this before attempting to claim training jobs.
    """
    executed = 0
    max_per_cycle = 3  # avoid blocking training for too long
    for _ in range(max_per_cycle):
        provision = claim_next_provision()
        if provision is None:
            break
        executed += 1
        execute_provision(provision)
    return executed


# ═══════════════════════════════════════════════════════════════════════════
# Cache-aware data resolution
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_cached_data(payload: dict, dataset_id: int | None) -> Path | None:
    """Check if the provision cache already has the dataset the job needs.

    Resolution order:
      1. Explicit ``cache://`` URI → direct alias lookup.
      2. Known resource name → match receipt ``resource_id``
         (prevents cross-dataset contamination).
      3. Fallback: scan only when *exactly one* dataset is cached.

    Returns a ``Path`` to a local YAML file, or ``None`` (→ legacy ZIP download).
    """
    provisioner = _get_provisioner()
    if provisioner is None:
        return None

    data_ref = str(payload.get("data") or "").strip()
    cache_root = _provision_cache_root()

    # 1. Explicit cache URI — trust it
    if data_ref.startswith("cache://"):
        rel = data_ref[len("cache://"):].lstrip("/")
        result = _find_yaml_in_dir(cache_root / rel)
        if result:
            return result

    # 2. Resolve resource_id from the data reference
    resource_id = _resolve_data_to_resource_id(data_ref)
    try:
        inv = provisioner.list_cache_inventory()
    except Exception:
        inv = []

    if resource_id:
        for entry in inv:
            if entry.get("resource_id") != resource_id:
                continue
            local_uri = (entry.get("local_uri") or "")
            if not local_uri.startswith("cache://"):
                continue
            rel = local_uri[len("cache://"):].lstrip("/")
            result = _find_yaml_in_dir(cache_root / rel)
            if result:
                return result
        # Also check alias directory
        name = _resource_id_to_short_name(resource_id)
        if name:
            result = _find_yaml_in_dir(cache_root / "datasets" / name)
            if result:
                return result
        return None  # matched resource but no cache hit

    # 3. No known resource_id — alias check
    if data_ref:
        name = data_ref.replace(".yaml", "").replace(".yml", "").split(".")[0].replace("/", "_")
        result = _find_yaml_in_dir(cache_root / "datasets" / name)
        if result:
            return result

    # 4. Desperate fallback: only if exactly ONE dataset cached
    if len(inv) == 1:
        local_uri = (inv[0].get("local_uri") or "")
        if local_uri.startswith("cache://"):
            rel = local_uri[len("cache://"):].lstrip("/")
            result = _find_yaml_in_dir(cache_root / rel)
            if result:
                return result

    return None


def _resolve_data_to_resource_id(data_ref: str) -> str | None:
    """Map a payload ``data`` field to a known AR resource ID."""
    try:
        from experiment_preparation.quudet_adapter import _map_dataset_to_resource
        return _map_dataset_to_resource(data_ref)
    except Exception:
        return None


def _resource_id_to_short_name(resource_id: str) -> str | None:
    """Extract a short directory-friendly name from a resource ID."""
    try:
        parts = resource_id.split(":")
        if len(parts) >= 3:
            candidate = parts[-1]
            for prefix in ("2012-", "2017-", "2019-"):
                candidate = candidate.replace(prefix, "")
            candidate = candidate.split("-")[0]
            return candidate
        return parts[-1] if parts else None
    except Exception:
        return None


def _find_yaml_in_dir(directory: Path) -> Path | None:
    """Look for a YAML dataset descriptor inside *directory*."""
    if not directory.is_dir():
        return None
    for ext in (".yaml", ".yml"):
        for fpath in directory.rglob(f"*{ext}"):
            if fpath.is_file():
                return fpath
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════


def _heartbeat_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            heartbeat(_runtime_snapshot()["running_jobs"], _collect_cache_inventory())
        except Exception as e:
            print(f"[agent] heartbeat failed (will retry): {e}")
        stop_event.wait(HEARTBEAT_INTERVAL)


def run_forever() -> None:
    """Register, continuously report runtime state, and execute claimed jobs."""
    print(f"[agent] node_id={NODE_ID} kind={_env('NODE_KIND', 'local')} master={MASTER_API_BASE}")
    register_node()
    stop_event = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(stop_event,), daemon=True).start()
    running = 0

    while True:
        if running >= NODE_MAX_CONCURRENCY:
            time.sleep(1)
            continue

        try:
            run_provision_loop()
        except Exception as e:
            print(f"[agent] provision loop error (will retry): {e}")

        try:
            job = claim_next_job()
            if not job:
                time.sleep(POLL_INTERVAL)
                continue
        except Exception as e:
            print(f"[agent] claim-next failed (will retry): {e}")
            time.sleep(POLL_INTERVAL)
            continue

        running += 1
        _set_runtime(running_jobs=running)
        job_id = str(job.get("id"))
        try:
            execute_job(job)
        except Exception as e:
            message = f"agent execution failed: {type(e).__name__}: {e}"
            print(f"[agent] {message}")
            try:
                _emit_event(job_id, "summary", {"error_message": message})
                _emit_event(job_id, "status", {"status": "FAILED"})
            except Exception as report_error:
                print(f"[agent] failed to report job error: {report_error}")
        finally:
            running = max(0, running - 1)
            _set_runtime(
                running_jobs=running,
                active_job_id=None,
                active_pid=None,
                active_command=None,
                phase="idle",
            )

def _collect_cache_inventory() -> dict | None:
    """Collect resource cache inventory for heartbeat."""
    try:
        provisioner = _get_provisioner()
        resources = provisioner.list_cache_inventory()
        free_bytes = provisioner.cache_free_bytes()
        return {
            "cache_root": str(_provision_cache_root()),
            "cache_free_bytes": free_bytes,
            "resources": resources,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Support cache inventory in heartbeat
# ---------------------------------------------------------------------------


def heartbeat(running_jobs: int, cache_inventory: dict | None = None) -> None:
    body = {
        "token": NODE_TOKEN,
        "running_jobs": running_jobs,
        "capabilities": collect_node_capabilities(),
    }
    if cache_inventory:
        body["cache_root"] = cache_inventory.get("cache_root")
        body["cache_free_bytes"] = cache_inventory.get("cache_free_bytes")
        body["resource_cache"] = cache_inventory.get("resources", [])

    _post(f"/api/v1/nodes/{NODE_ID}/heartbeat", body)


if __name__ == "__main__":
    run_forever()
