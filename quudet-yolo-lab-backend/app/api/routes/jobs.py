from typing import Annotated

import csv
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_superuser
from app.config import get_settings
from app.database import get_db
from app.models.compute_node import ComputeNode
from app.models.job_record import JobRecord
from app.models.user import User
from app.schemas.job import JobCreate, JobListItem, JobRead
from app.services.artifact_store import get_artifact_store
from app.services.train_metrics import (
    metrics_response,
    parse_results_csv,
    resolve_results_csv,
)

from fastapi.responses import FileResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def _try_extract_results_dir_from_job_log(job: JobRecord) -> Path | None:
    """Extract Ultralytics save directory from run.log.

    Ultralytics may emit colored output like:
      Results saved to \x1b[1mD:\\...\\quudet-detect8\x1b[0m
    so we strip ANSI codes first.
    """

    if not job.log_path:
        return None
    p = Path(job.log_path)
    if not p.is_file():
        return None
    try:
        log_text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    for line in reversed(log_text.splitlines()[-8000:]):
        if "Results saved to" not in line:
            continue
        try:
            clean = _strip_ansi(line)
            tail = clean.split("Results saved to", 1)[1].strip()
            save_dir = Path(tail)
            if save_dir.exists() and save_dir.is_dir():
                return save_dir
        except Exception:
            continue
    return None


@router.post("", response_model=JobRead)
def create_job(
    body: JobCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    settings = get_settings()
    assigned_node_id: str | None = None
    if body.target_node_id:
        node = db.get(ComputeNode, body.target_node_id)
        if node is None:
            raise HTTPException(404, "target node not found")
        assigned_node_id = node.id

    job = JobRecord(
        job_type=body.job_type,
        status="PENDING_ASSIGN",
        payload=body.payload,
        project_name=body.project_name,
        owner_id=user.id,
        dataset_id=body.dataset_id,
        assigned_node_id=assigned_node_id,
        dispatch_status="PENDING_ASSIGN",
        metrics_cache=None if body.job_type == "train" else None,
        execution_target=body.execution_target,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobRead.model_validate(job)


@router.get("", response_model=list[JobListItem])
def list_jobs(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    q = db.query(JobRecord).filter(JobRecord.owner_id == user.id).order_by(JobRecord.created_at.desc()).limit(500)
    return [JobListItem.model_validate(j) for j in q.all()]


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    job = db.get(JobRecord, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.owner_id != user.id and not user.is_superuser:
        raise HTTPException(404, "Job not found")
    return JobRead.model_validate(job)


@router.get("/{job_id}/logs")
def job_logs(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    tail: int = 4000,
):
    job = db.get(JobRecord, job_id)
    if job is None or (job.owner_id != user.id and not user.is_superuser):
        raise HTTPException(404, "Job not found")

    store = get_artifact_store()
    rel = f"{store.job_dir(job_id)}/run.log"
    text = store.read_text(rel)
    if text is None:
        return {"content": "(log file missing)"}
    if tail and len(text) > tail:
        text = text[-tail:]
    return {"content": text}


@router.get("/{job_id}/metrics")
def job_metrics(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    job = db.get(JobRecord, job_id)
    if job is None or (job.owner_id != user.id and not user.is_superuser):
        raise HTTPException(404, "Job not found")

    settings = get_settings()
    if isinstance(job.metrics_cache, dict) and job.metrics_cache.get("x") is not None:
        return metrics_response(job, job.metrics_cache)

    store = get_artifact_store()
    job_dir = settings.artifacts_dir / job.id
    work_dir = settings.resolved_yolo_work_dir

    log_text: str | None = None
    if job.log_path:
        try:
            log_text = store.read_text(f"{store.job_dir(job.id)}/run.log")
        except Exception:
            log_text = None

    if job.job_type == "train":
        csv_path = resolve_results_csv(job, work_dir, job_dir, log_text=log_text)
        if csv_path:
            parsed = parse_results_csv(csv_path)
            if parsed:
                return metrics_response(job, parsed)
        if job.status in {"RUNNING", "PENDING_ASSIGN", "PENDING"}:
            return {
                "ok": False,
                "reason": "metrics not ready for this job yet (results.csv not found)",
                "epochs_done": 0,
                "epochs_total": int((job.payload or {}).get("epochs") or 0),
                "progress_percent": job.progress or 0,
            }

    txt_path = job_dir / "results.txt"
    if txt_path.is_file():
        # Best-effort parser for Ultralytics older formats:
        # - usually a table with an "epoch" column and metrics columns
        # - delimiter may be comma or whitespace
        raw = txt_path.read_text(encoding="utf-8", errors="replace")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return {"ok": False, "reason": "empty results.txt"}

        header_idx = None
        header_tokens: list[str] = []
        for i, ln in enumerate(lines[:80]):
            if "epoch" not in ln.lower():
                continue
            # Candidate header line
            if "," in ln:
                tokens = [t.strip() for t in ln.split(",") if t.strip()]
            else:
                # collapse spaces
                tokens = [t.strip() for t in re.split(r"\\s+", ln) if t.strip()]
            if any(t.lower().startswith("epoch") for t in tokens) and len(tokens) >= 3:
                header_idx = i
                header_tokens = tokens
                break

        # If no obvious header, fallback to first line
        if header_idx is None:
            header_idx = 0
            if "," in lines[0]:
                header_tokens = [t.strip() for t in lines[0].split(",") if t.strip()]
            else:
                header_tokens = [t.strip() for t in re.split(r"\\s+", lines[0]) if t.strip()]

        if not header_tokens or len(header_tokens) < 2:
            return {"ok": False, "reason": "unrecognized results.txt header"}

        # Build mapping: epoch token index + metric indices
        epoch_col = None
        metric_cols: list[tuple[str, int]] = []
        for idx, name in enumerate(header_tokens):
            low = name.lower()
            if low.startswith("epoch"):
                epoch_col = idx
            else:
                metric_cols.append((name, idx))

        series: dict[str, list[float]] = {}
        x: list[int] = []

        for ln in lines[header_idx + 1 :]:
            if "," in ln:
                parts = [p.strip() for p in ln.split(",") if p.strip()]
            else:
                parts = [p.strip() for p in re.split(r"\\s+", ln) if p.strip()]
            if len(parts) < 2:
                continue

            # Skip lines that don't contain numbers
            parsed = []
            for p in parts:
                try:
                    parsed.append(float(p))
                except Exception:
                    parsed.append(float("nan"))

            if not parsed:
                continue

            # Extract epoch
            if epoch_col is not None and epoch_col < len(parsed):
                try:
                    ep = int(parsed[epoch_col])
                except Exception:
                    ep = len(x)
            else:
                ep = len(x)
            x.append(ep)

            for name, idx in metric_cols:
                if idx < len(parsed):
                    v = parsed[idx]
                    if isinstance(v, float) and (v != v):  # NaN
                        series.setdefault(name, []).append(float("nan"))
                    else:
                        series.setdefault(name, []).append(float(v))

        if not x or not series:
            return {"ok": False, "reason": "failed to parse results.txt metrics"}

        return metrics_response(job, {"x": x, "series": series})

    total_epochs = int((job.payload or {}).get("epochs") or 0)
    return {
        "ok": False,
        "reason": "no metrics file found (results.csv/txt not captured yet)",
        "epochs_done": 0,
        "epochs_total": total_epochs,
        "progress_percent": job.progress or 0,
    }


@router.get("/{job_id}/images")
def job_images(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    job = db.get(JobRecord, job_id)
    if job is None or (job.owner_id != user.id and not user.is_superuser):
        raise HTTPException(404, "Job not found")

    settings = get_settings()

    def resolve_source_dir() -> Path | None:
        job_dir = settings.artifacts_dir / job.id
        artifacts_results = job_dir / "results"
        if artifacts_results.exists():
            return artifacts_results

        # Prefer parsing the exact "Results saved to ..." from run.log.
        # This prevents mismatching older run directories (Ultralytics increment_path).
        parsed = _try_extract_results_dir_from_job_log(job)
        if parsed is not None:
            return parsed

        payload = job.payload or {}
        project = (payload.get("project") or "").strip()
        name = (payload.get("name") or "").strip()
        task = job.job_type
        work_dir = settings.resolved_yolo_work_dir

        candidates: list[Path] = []
        if project and name:
            candidates.append((work_dir / "runs" / str(task) / project / name).resolve())
        if name:
            candidates.append((work_dir / "runs" / str(task) / name).resolve())
        if project and name:
            candidates.append((work_dir / project / name).resolve())

        for c in candidates:
            if c.exists() and c.is_dir():
                return c

        # Fallback: find a directory named as job name under runs/
        if name:
            runs_dir = (work_dir / "runs").resolve()
            if runs_dir.exists():
                latest = None
                latest_m = 0.0
                exts = {".jpg", ".jpeg", ".png", ".webp"}
                for d in runs_dir.rglob(name):
                    if not d.is_dir():
                        continue
                    # quick check: must contain at least one image
                    try:
                        ok = any((f.is_file() and f.suffix.lower() in exts) for f in d.rglob("*"))
                    except OSError:
                        ok = False
                    if ok:
                        try:
                            mt = d.stat().st_mtime
                        except OSError:
                            mt = 0.0
                        if mt >= latest_m:
                            latest_m = mt
                            latest = d
                if latest is not None:
                    return latest
        return None

    results_dir = resolve_source_dir()
    if results_dir is None or not results_dir.exists():
        return {"ok": False, "reason": "no results dir found (artifacts or runs)"}

    exts = {".jpg", ".jpeg", ".png", ".webp"}
    images: list[str] = []
    for p in results_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            images.append(p.relative_to(results_dir).as_posix())

    images.sort()
    items = [{"path": rel, "url": f"/api/v1/jobs/{job_id}/image/{rel}"} for rel in images]
    return {"ok": True, "images": items}


@router.get("/{job_id}/image/{image_path:path}")
def job_image(
    job_id: str,
    image_path: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    job = db.get(JobRecord, job_id)
    if job is None or (job.owner_id != user.id and not user.is_superuser):
        raise HTTPException(404, "Job not found")

    settings = get_settings()

    def resolve_source_dir() -> Path | None:
        job_dir = settings.artifacts_dir / job.id
        artifacts_results = job_dir / "results"
        if artifacts_results.exists():
            return artifacts_results

        parsed = _try_extract_results_dir_from_job_log(job)
        if parsed is not None:
            return parsed

        payload = job.payload or {}
        project = (payload.get("project") or "").strip()
        name = (payload.get("name") or "").strip()
        task = job.job_type
        work_dir = settings.resolved_yolo_work_dir
        candidates: list[Path] = []
        if project and name:
            candidates.append((work_dir / "runs" / str(task) / project / name).resolve())
        if name:
            candidates.append((work_dir / "runs" / str(task) / name).resolve())
        if project and name:
            candidates.append((work_dir / project / name).resolve())
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        return None

    results_dir = resolve_source_dir()
    if results_dir is None or not results_dir.exists():
        raise HTTPException(404, "results dir not found")

    candidate = (results_dir / image_path).resolve()
    if results_dir.resolve() not in candidate.parents and candidate.resolve() != results_dir.resolve():
        raise HTTPException(400, "invalid image path")
    if not candidate.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(str(candidate))


@router.delete("")
def delete_all_jobs(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_superuser)],
):
    db.query(JobRecord).delete(synchronize_session=False)
    db.commit()
    return {"ok": True}
