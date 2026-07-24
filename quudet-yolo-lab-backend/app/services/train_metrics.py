"""Resolve train results.csv and epoch-based progress for a job."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.job_record import JobRecord


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def job_started_timestamp(job: JobRecord) -> float | None:
    try:
        t = job.started_at or job.created_at
        return t.timestamp() if t else None
    except Exception:
        return None


def build_results_csv_candidates(job: JobRecord, work_dir: Path, job_dir: Path) -> list[Path]:
    """Paths that may hold this job's results.csv (order: most specific first).

    Dynamically uses job.job_type to determine the YOLO task subdirectory.
    Handles both simple project names and full-path projects (e.g. ``runs/train/xxx``).
    """
    cands: list[Path] = []
    payload = job.payload or {}
    project = str(payload.get("project") or "").strip()
    name = str(payload.get("name") or "").strip()

    # Priority 1: copied into job artifact dir
    cands.append(job_dir / "results.csv")

    # Priority 2: resolved_command_path from snapshot (if recorded)
    if job.resolved_command_path:
        rcp = Path(job.resolved_command_path)
        if rcp.is_file():
            snap_dir = rcp.parent
            cands.append(snap_dir / "results.csv")

    # Priority 3: standard YOLO output paths, one per known task
    task_subdirs = []
    if job.job_type:
        task_subdirs.append(job.job_type)
    # Always add all common task dirs as fallback
    for t in ("train", "detect", "val"):
        if t not in task_subdirs:
            task_subdirs.append(t)

    # Normalise project: strip leading ``runs/{task}/`` prefix so we don't
    # double-nest when project already contains it.
    bare_project = _strip_runs_prefix(project)

    for task in task_subdirs:
        if bare_project and name:
            # runs/{task}/{bare_project}/{name}/
            cands.append(work_dir / "runs" / task / bare_project / name / "results.csv")
        if project and name:
            # project is used as-is (may already be a full path like ``runs/train/xxx``)
            cands.append(work_dir / project / name / "results.csv")
        # Glob match for Ultralytics increment_path variants
        if bare_project and name:
            for base in (work_dir / "runs" / task / bare_project, work_dir / "runs" / task / project):
                if base.is_dir():
                    exp_dirs = _glob_exp_dirs(base, name)
                    for d in exp_dirs:
                        cands.append(d / "results.csv")
        if name:
            cands.append(work_dir / "runs" / task / name / "results.csv")

    # Priority 4: legacy nested detect/train path (observed in some setups)
    for possible_base in [
        work_dir / "runs" / "detect" / "runs" / "train",
    ]:
        if possible_base.is_dir():
            exp_dirs = _glob_exp_dirs(possible_base, name)
            for d in exp_dirs:
                cands.append(d / "results.csv")

    # deduplicate preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in cands:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _strip_runs_prefix(project: str) -> str:
    """If ``project`` starts with ``runs/train/``, ``runs/detect/``, etc, strip it.

    This avoids double-nesting when the project value already contains
    the Ultralytics ``runs/{task}/`` prefix.
    """
    for task in ("train", "detect", "val", "segment", "classify"):
        prefix = f"runs/{task}/"
        if project.startswith(prefix):
            return project[len(prefix):]
    return project


def _glob_exp_dirs(base: Path, name: str) -> list[Path]:
    """Sorted list of directories matching ``name*`` under ``base``."""
    if not base.is_dir():
        return []
    dirs = sorted(
        [d for d in base.glob(f"{name}*") if d.is_dir()],
        key=lambda x: _mtime(x),
        reverse=True,
    )
    return dirs[:8]


def _rglob_fallback(work_dir: Path, started_at: datetime | None) -> Path | None:
    """Last-resort recursive search for results.csv under ``runs/``.

    Only called when all structured paths failed.  Limited to 500 matches
    to avoid performance issues in large repos.
    """
    runs_dir = work_dir / "runs"
    if not runs_dir.is_dir():
        return None
    t0 = started_at.timestamp() - 5 if started_at else None
    candidates: list[Path] = []
    for i, p in enumerate(runs_dir.rglob("results.csv")):
        if i >= 500:
            break
        if t0 is None or _mtime(p) >= t0:
            candidates.append(p)
    if not candidates:
        return None
    candidates.sort(key=_mtime, reverse=True)
    return candidates[0]


def resolve_results_csv(
    job: JobRecord,
    work_dir: Path,
    job_dir: Path,
    *,
    log_text: str | None = None,
) -> Path | None:
    """Pick results.csv for this job.

    Search priority (first match wins):
      1. ``metrics_source_path`` — explicit path recorded at execution time
      2. Job artifacts directory — ``job_dir / results.csv`` (copied by executor)
      3. Log-parsed ``Results saved to`` — ``<log_dir>/results.csv``
      4. Payload project/name directories — ``runs/train/<project>/<name>/results.csv``
      5. Fallback: recursive search under runs/ with time filter
    """
    t0 = job_started_timestamp(job)
    if t0 is not None:
        t0 -= 5.0

    # Priority 1: explicit metrics_source_path (most reliable)
    metrics_source = getattr(job, "metrics_source_path", None)
    if metrics_source:
        p = Path(metrics_source)
        if p.is_file():
            return p

    # Priority 2: job artifacts directory (copied by executor post-run)
    job_csv = job_dir / "results.csv"
    if job_csv.is_file():
        return job_csv

    # Priority 3: log-parsed "Results saved to" path
    if log_text:
        for line in reversed(log_text.splitlines()[-8000:]):
            if "Results saved to" not in line:
                continue
            try:
                clean = re.sub(r"\x1b\[[0-9;]*m", "", line)
                tail = clean.split("Results saved to", 1)[1].strip()
                p = Path(tail) / "results.csv"
                if p.is_file() and (t0 is None or _mtime(p) >= t0):
                    return p
            except Exception:
                continue

    # Priority 4: structured candidate paths from payload
    pool: list[Path] = []
    for p in build_results_csv_candidates(job, work_dir, job_dir):
        if p.is_file():
            if t0 is None or _mtime(p) >= t0:
                pool.append(p)

    if pool:
        pool.sort(key=_mtime, reverse=True)
        selected = pool[0]
        if len(pool) > 1:
            # Log ambiguity for debugging but return best match
            import logging
            logging.getLogger(__name__).warning(
                "resolve_results_csv: %d candidates for job %s, picked %s",
                len(pool), getattr(job, "id", "?"), selected,
            )
        return selected

    # Priority 5: last-resort recursive search
    return _rglob_fallback(work_dir, job.started_at if hasattr(job, "started_at") else None)


def parse_results_csv(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    series: dict[str, list[float]] = {}
    x: list[int] = []
    for i, r in enumerate(rows):
        epoch = r.get("epoch") or r.get("Epoch") or r.get("epochs")
        try:
            x.append(int(float(epoch)))  # type: ignore[arg-type]
        except Exception:
            x.append(i)
        for k, v in r.items():
            if not k or str(k).strip().lower() == "epoch":
                continue
            if v is None or str(v).strip() == "":
                continue
            try:
                series.setdefault(str(k).strip(), []).append(float(v))
            except Exception:
                series.setdefault(str(k).strip(), []).append(float("nan"))
    return {"x": x, "series": series}


def epoch_progress(x: list[Any], total_epochs: int, *, status: str = "") -> dict[str, int]:
    total = max(0, int(total_epochs or 0))
    if not x:
        done = 0
        pct = 100 if status.upper() == "SUCCESS" and total > 0 else 0
        return {"epochs_done": done, "epochs_total": total, "progress_percent": pct}
    try:
        max_epoch = max(int(float(n)) for n in x)
    except (TypeError, ValueError):
        max_epoch = len(x) - 1
    done = max(0, max_epoch + 1)
    if total > 0:
        pct = min(100, max(0, int(round(done * 100 / total))))
        if status.upper() == "SUCCESS":
            pct = 100
        elif status.upper() == "RUNNING":
            pct = min(pct, 99)
    else:
        pct = 100 if status.upper() == "SUCCESS" else 0
    return {"epochs_done": done, "epochs_total": total, "progress_percent": pct}


def resolve_results_csv_for_train(
    *,
    payload: dict[str, Any],
    work_dir: Path,
    job_dir: Path,
    started_at: datetime | None = None,
    log_text: str | None = None,
    job_type: str | None = None,
) -> Path | None:
    """Agent/local helper when only payload dict is available."""

    class _JobShim:
        def __init__(self) -> None:
            self.payload = payload
            self.started_at = started_at
            self.created_at = started_at
            self.job_type = job_type
            self.resolved_command_path = None  # agent-side resolver: no snapshot
            self.metrics_source_path = payload.get("_metrics_source_path") if isinstance(payload, dict) else None

    return resolve_results_csv(_JobShim(), work_dir, job_dir, log_text=log_text)  # type: ignore[arg-type]


def metrics_response(
    job: JobRecord,
    metrics: dict[str, Any],
    *,
    ok: bool = True,
    reason: str | None = None,
) -> dict[str, Any]:
    total_epochs = int((job.payload or {}).get("epochs") or 0)
    prog = epoch_progress(metrics.get("x") or [], total_epochs, status=job.status)
    out: dict[str, Any] = {
        "ok": ok,
        "type": "csv",
        "x": metrics.get("x") or [],
        "series": metrics.get("series") or {},
        **prog,
    }
    if reason:
        out["reason"] = reason
    return out
