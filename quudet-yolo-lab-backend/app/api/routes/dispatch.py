"""Unified dispatch 鈥?node scheduling for local and remote execution.

All execution nodes (local Windows, remote Linux GPU servers) claim jobs
via ``claim-next`` and report progress via ``events``.  Authentication is
token-based 鈥?local nodes auto-generate their token via ``agent.runner``.

Design:
    API creates job 鈫?status=PENDING_ASSIGN 鈫?claim-next 鈫?agent executes
    鈫?events(log/progress/metrics/status) 鈫?terminal state
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile

logger = logging.getLogger(__name__)
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.compute_node import ComputeNode
from app.models.job_record import JobRecord
from app.models.uploaded_dataset import UploadedDataset
from app.models.user import User
from app.schemas.node import DispatchClaimResponse, DispatchEventRequest
from app.services.experiment_compare import update_experiment_group_status
from app.services.train_metrics import epoch_progress
from app.services.uploaded_dataset_yaml import resolve_train_yaml_path

router = APIRouter(prefix="/dispatch", tags=["dispatch"])


def _hash_node_token(token: str) -> str:
    settings = get_settings()
    raw = f"{settings.NODE_SHARED_TOKEN}:{token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_node(db: Session, node_id: str, token: str) -> ComputeNode:
    """Fetch and authenticate a node by ID + token hash.

    All nodes (local and remote) must pass a valid token.
    """
    node = db.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(404, "Node not found")
    if node.token_hash != _hash_node_token(token):
        raise HTTPException(401, "Invalid node token")
    node.last_seen_at = datetime.utcnow()
    node.status = "ONLINE"
    return node
    if node.token_hash != _hash_node_token(token):
        raise HTTPException(401, "Invalid node token")
    node.last_seen_at = datetime.utcnow()
    node.status = "ONLINE"
    return node

def _reserve_node_slot(db: Session, node_id: str) -> bool:
    """Atomically reserve one execution slot when the node has capacity."""
    result = db.execute(
        update(ComputeNode)
        .where(
            ComputeNode.id == node_id,
            ComputeNode.running_jobs < ComputeNode.max_concurrent_jobs,
        )
        .values(running_jobs=ComputeNode.running_jobs + 1)
    )
    return result.rowcount == 1


def _release_node_slot(db: Session, node_id: str) -> None:
    """Release a slot reserved before a job could be claimed."""
    db.execute(
        update(ComputeNode)
        .where(ComputeNode.id == node_id, ComputeNode.running_jobs > 0)
        .values(running_jobs=ComputeNode.running_jobs - 1)
    )

def _dataset_source_root(upload: UploadedDataset) -> Path:
    ep = (upload.extracted_path or "").strip()
    if ep and not ep.startswith("("):
        p = Path(ep)
        if p.exists() and p.is_dir():
            return p
    sp = Path(upload.stored_path)
    if sp.exists():
        if sp.is_dir():
            return sp
        return sp.parent
    raise HTTPException(404, "dataset source not found")


@router.post("/assign/{job_id}/{node_id}")
def assign_job(
    job_id: str,
    node_id: str,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    if not get_settings().effective_cluster_enabled:
        raise HTTPException(400, "Cluster mode disabled")
    job = db.get(JobRecord, job_id)
    node = db.get(ComputeNode, node_id)
    if job is None or node is None:
        raise HTTPException(404, "Job or node not found")
    if job.status in {"RUNNING", "SUCCESS"}:
        raise HTTPException(400, f"Job is already {job.status}")
    job.assigned_node_id = node_id
    job.dispatch_status = "PENDING_ASSIGN"
    job.status = "PENDING_ASSIGN"
    db.commit()
    return {"ok": True}


@router.post("/claim-next", response_model=DispatchClaimResponse)
def claim_next_job(
    body: dict,
    db: Annotated[Session, Depends(get_db)],
):
    node_id = str(body.get("node_id") or "").strip()
    token = str(body.get("token") or "").strip()

    if not node_id:
        raise HTTPException(422, "node_id is required")
    if not token:
        raise HTTPException(422, "token is required")

    node = _require_node(db, node_id, token)

    if not _reserve_node_slot(db, node_id):
        db.commit()
        return DispatchClaimResponse(claimed=False, reason="node-capacity-reached")

    node_caps = node.capabilities or {}

    def _job_matches_node(j: JobRecord, job_idx: int = 0) -> bool:
        """Check if a job's requirements match this node's capabilities.

        Logs filtering decisions at INFO level for observability.
        """
        payload = j.payload or {}
        job_id_short = j.id[:12] if j.id else "?"

        # execution_target constraint (model field first, then payload fallback)
        exec_target = j.execution_target or payload.get("execution_target", "auto")
        node_kind = node_caps.get("node_kind", "local")

        if exec_target == "local" and node_kind != "local":
            logger.info("SchedFilter[%s]: job=%s exec_target=local, but node kind=%s -> REJECT",
                        node_id, job_id_short, node_kind)
            return False
        if exec_target == "remote" and node_kind != "remote":
            logger.info("SchedFilter[%s]: job=%s exec_target=remote, but node kind=%s -> REJECT",
                        node_id, job_id_short, node_kind)
            return False

        # GPU constraint
        device = str(payload.get("device", "")).lower()
        requires_gpu = device.startswith("cuda") or payload.get("required_gpu", False)
        if requires_gpu:
            if not node_caps.get("has_gpu", False):
                logger.info("SchedFilter[%s]: job=%s requires GPU (device=%s), but node has_gpu=False -> REJECT",
                            node_id, job_id_short, device)
                return False

        logger.debug("SchedFilter[%s]: job=%s exec_target=%s device=%s -> ACCEPT",
                     node_id, job_id_short, exec_target, device or "auto")
        return True

    # Claim assigned jobs first, then unassigned
    job = (
        db.query(JobRecord)
        .filter(
            JobRecord.dispatch_status == "PENDING_ASSIGN",
            JobRecord.assigned_node_id == node_id,
            JobRecord.status.in_(["PENDING", "PENDING_ASSIGN"]),
        )
        .order_by(JobRecord.created_at.asc())
        .first()
    )
    if job is not None and not _job_matches_node(job):
        job = None  # assigned job doesn't match 鈥?skip to fallback

    if job is None:
        # Auto-claim unassigned jobs that match this node.
        job = (
            db.query(JobRecord)
            .filter(
                JobRecord.dispatch_status == "PENDING_ASSIGN",
                JobRecord.assigned_node_id.is_(None),
                JobRecord.status.in_(["PENDING", "PENDING_ASSIGN"]),
            )
            .order_by(JobRecord.created_at.asc())
            .all()
        )
        # Pick the first job that matches node capabilities
        job = next((j for j in job if _job_matches_node(j)), None)
        if job is not None:
            job.assigned_node_id = node_id

    if job is None:
        _release_node_slot(db, node_id)
        db.commit()
        return DispatchClaimResponse(claimed=False, reason="no-pending-job")

    job.status = "RUNNING"
    job.dispatch_status = "RUNNING_REMOTE"
    job.started_at = datetime.utcnow()
    job.last_heartbeat_at = datetime.utcnow()
    job.progress = 0
    job.metrics_cache = None
    if not job.log_path:
        job_dir = get_settings().artifacts_dir / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        job.log_path = str(job_dir / "run.log")
    db.commit()

    # Cascade RUNNING status to experiment group
    update_experiment_group_status(db, job.experiment_group_id)

    return DispatchClaimResponse(
        claimed=True,
        job={
            "id": job.id,
            "job_type": job.job_type,
            "payload": job.payload or {},
            "project_name": job.project_name,
            "dataset_id": job.dataset_id,
            "started_at": job.started_at.isoformat() if job.started_at else None,
        },
    )


@router.post("/events")
def dispatch_event(
    body: DispatchEventRequest,
    db: Annotated[Session, Depends(get_db)],
):
    node = _require_node(db, body.node_id, body.token)
    job = db.get(JobRecord, body.job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.assigned_node_id and job.assigned_node_id != body.node_id:
        raise HTTPException(403, "Job is assigned to another node")
    job.assigned_node_id = body.node_id
    job.last_heartbeat_at = datetime.utcnow()

    if body.event_type == "log":
        text = str(body.payload.get("text") or "")
        if text:
            log_path = Path(job.log_path) if job.log_path else (get_settings().artifacts_dir / job.id / "run.log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(text)
            job.log_path = str(log_path)
    elif body.event_type == "progress":
        p = int(body.payload.get("progress") or 0)
        if "epochs_done" in body.payload and "epochs_total" in body.payload:
            total = max(1, int(body.payload.get("epochs_total") or 0))
            done = max(0, int(body.payload.get("epochs_done") or 0))
            p = min(99, max(0, int(round(done * 100 / total)))) if job.status == "RUNNING" else min(100, int(round(done * 100 / total)))
        job.progress = max(0, min(100, p))
    elif body.event_type == "metrics":
        m = body.payload.get("metrics")
        if isinstance(m, dict):
            job.metrics_cache = m
            total_epochs = int((job.payload or {}).get("epochs") or 0)
            if total_epochs > 0 and isinstance(m.get("x"), list) and m.get("x"):
                prog = epoch_progress(m.get("x") or [], total_epochs, status=job.status)
                job.progress = prog["progress_percent"]
    elif body.event_type == "status":
        status = str(body.payload.get("status") or "").upper()
        if status in {"RUNNING", "SUCCESS", "FAILED"}:
            was_finish = status in {"SUCCESS", "FAILED"}
            job.status = status
            if status == "RUNNING":
                job.dispatch_status = "RUNNING_REMOTE"
            if was_finish:
                job.dispatch_status = "FINISHED_REMOTE"
                job.finished_at = datetime.utcnow()
                node.running_jobs = max(0, node.running_jobs - 1)
    elif body.event_type == "summary":
        if "result_summary" in body.payload:
            job.result_summary = str(body.payload.get("result_summary") or "")
        if "error_message" in body.payload:
            job.error_message = str(body.payload.get("error_message") or "")

    db.commit()

    # After a remote job reaches terminal status, cascade to group
    if body.event_type == "status":
        s = str(body.payload.get("status") or "").upper()
        if s in {"SUCCESS", "FAILED"}:
            update_experiment_group_status(db, job.experiment_group_id)

    return {"ok": True}


@router.get("/job-dataset/{job_id}")
def download_job_dataset_bundle(
    job_id: str,
    node_id: Annotated[str, Query(min_length=2)],
    token: Annotated[str, Query(min_length=4)],
    db: Annotated[Session, Depends(get_db)],
):
    _require_node(db, node_id, token)
    job = db.get(JobRecord, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if not job.dataset_id:
        raise HTTPException(404, "Job has no dataset")
    if job.assigned_node_id and job.assigned_node_id != node_id:
        raise HTTPException(403, "Job is assigned to another node")

    ds = db.get(UploadedDataset, int(job.dataset_id))
    if ds is None:
        raise HTTPException(404, "Dataset not found")

    yaml_abs = resolve_train_yaml_path(ds)
    if not yaml_abs:
        raise HTTPException(400, "Dataset yaml not found")

    source_root = _dataset_source_root(ds)
    yaml_path = Path(yaml_abs).resolve()
    try:
        yaml_rel = yaml_path.relative_to(source_root.resolve()).as_posix()
    except ValueError:
        # Fallback for uncommon layouts: keep filename at least.
        yaml_rel = yaml_path.name

    settings = get_settings()
    bundle_dir = settings.artifacts_dir / "dataset_bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_zip = bundle_dir / f"dataset_{ds.id}.zip"
    bundle_base = bundle_zip.with_suffix("")
    if bundle_zip.exists():
        bundle_zip.unlink()
    shutil.make_archive(str(bundle_base), "zip", root_dir=str(source_root))

    return FileResponse(
        str(bundle_zip),
        media_type="application/zip",
        filename=bundle_zip.name,
        headers={
            "x-dataset-id": str(ds.id),
            "x-data-yaml-rel": yaml_rel,
        },
    )


@router.get("/job-bundle/{job_id}")
def download_job_bundle(
    job_id: str,
    node_id: Annotated[str, Query(min_length=2)],
    token: Annotated[str, Query(min_length=4)],
    db: Annotated[Session, Depends(get_db)],
):
    """Package job snapshots (spec, command, model, data, env) + optional code snapshot as a zip.

    The agent downloads this before execution to ensure config/code consistency
    between master and remote nodes.
    """
    _require_node(db, node_id, token)
    job = db.get(JobRecord, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.assigned_node_id and job.assigned_node_id != node_id:
        raise HTTPException(403, "Job is assigned to another node")

    settings = get_settings()
    job_dir = settings.artifacts_dir / job_id
    snap_dir = job_dir / "snapshot"

    # Gather files to package
    files_to_pack: list[tuple[Path, str]] = []  # (source_path, arcname)

    # 1. Snapshot files (always include if present)
    snapshot_paths = [
        ("spec_snapshot.json", "snapshot/spec_snapshot.json"),
        ("resolved_command.txt", "snapshot/resolved_command.txt"),
        ("model_snapshot.yaml", "snapshot/model_snapshot.yaml"),
        ("data_snapshot.yaml", "snapshot/data_snapshot.yaml"),
        ("env_snapshot.json", "snapshot/env_snapshot.json"),
    ]
    for fname, arcname in snapshot_paths:
        p = snap_dir / fname
        if p.is_file():
            files_to_pack.append((p, arcname))

    # 2. Code snapshot (if exists on job record)
    code_path_str = job.code_snapshot_path
    if code_path_str:
        code_path = Path(code_path_str)
        if code_path.is_file():
            files_to_pack.append((code_path, f"code/{code_path.name}"))
        elif code_path.is_dir():
            # Walk the directory and add all files
            for f in code_path.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(code_path).as_posix()
                    files_to_pack.append((f, f"code/{rel}"))

    # 3. Model/data copy files if they exist outside snapshot dir
    if job.model_snapshot_path:
        mp = Path(job.model_snapshot_path)
        if mp.is_file() and mp.parent != snap_dir:
            files_to_pack.append((mp, f"model/{mp.name}"))
    if job.data_snapshot_path:
        dp = Path(job.data_snapshot_path)
        if dp.is_file() and dp.parent != snap_dir:
            files_to_pack.append((dp, f"data/{dp.name}"))

    if not files_to_pack:
        raise HTTPException(404, "No snapshot files available for this job (job may not have started yet)")

    # Build zip in a temp location
    bundle_dir = settings.artifacts_dir / "job_bundles"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_zip = bundle_dir / f"job_bundle_{job_id}.zip"
    bundle_base = bundle_zip.with_suffix("")
    if bundle_zip.exists():
        bundle_zip.unlink()

    # Use temp dir for staging to avoid conflicts
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for src, arcname in files_to_pack:
            dest = tmp / arcname
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        shutil.make_archive(str(bundle_base), "zip", root_dir=tmpdir)

    has_snapshot = snap_dir.exists() and any((snap_dir / f[0]).is_file() for f in snapshot_paths)

    return FileResponse(
        str(bundle_zip),
        media_type="application/zip",
        filename=f"job_bundle_{job_id}.zip",
        headers={
            "x-job-id": job_id,
            "x-has-snapshot": str(has_snapshot).lower(),
            "x-run-role": job.run_role or "",
            "x-seed": str(job.seed) if job.seed is not None else "",
        },
    )
