from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import re

from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import get_settings
from app.database import SessionLocal
from app.models.job_record import JobRecord
from app.services.artifact_store import get_artifact_store
from app.services.experiment_compare import update_experiment_group_status
from app.services.snapshot_service import create_job_snapshot, write_artifacts_manifest
from app.services.train_metrics import epoch_progress, parse_results_csv, resolve_results_csv
from app.services.yolo_runner import build_command, yolo_executable


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove terminal ANSI escape codes (Ultralytics uses colored output)."""
    return _ANSI_RE.sub("", text or "")


def _try_extract_results_dir_from_log(log_text: str) -> Path | None:
    # Ultralytics typically logs: "Results saved to runs/train/exp"
    for line in reversed(log_text.splitlines()[-200:]):
        if "Results saved to" in line:
            try:
                clean = _strip_ansi(line)
                tail = clean.split("Results saved to", 1)[1].strip()
                return Path(tail)
            except Exception:
                return None
    return None


def _find_latest_results_file(base: Path, started_at: datetime | None) -> Path | None:
    candidates: list[Path] = []
    for pat in ("results.csv", "results.txt"):
        for p in base.rglob(pat):
            candidates.append(p)
    if not candidates:
        return None
    t0 = started_at.timestamp() if started_at else None
    # Prefer files modified after start
    filtered = []
    for p in candidates:
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if t0 is None or mt >= t0:
            filtered.append((mt, p))
    pool = filtered if filtered else [(p.stat().st_mtime, p) for p in candidates if p.exists()]
    pool.sort(key=lambda x: x[0], reverse=True)
    return pool[0][1] if pool else None


def _try_extract_results_saved_dir(log_text: str) -> Path | None:
    for line in reversed(log_text.splitlines()[-400:]):
        if "Results saved to" not in line:
            continue
        try:
            clean = _strip_ansi(line)
            tail = clean.split("Results saved to", 1)[1].strip()
            # output example: "runs\detect\checksave" (no trailing slash)
            return Path(tail)
        except Exception:
            return None
    return None


def _copy_predict_results_for_job(job_dir: Path, log_text: str, row: JobRecord) -> None:
    settings = get_settings()
    work_dir = settings.resolved_yolo_work_dir

    payload = row.payload or {}
    project = payload.get("project") or ""
    name = payload.get("name") or ""

    expected: Path | None = None
    try:
        if str(project).strip() and str(name).strip():
            # Ultralytics get_save_dir() effectively does:
            #   runs/<task>/<project>/<name>
            # even when project already starts with "runs/...".
            expected = (work_dir / "runs" / str(row.job_type) / str(project) / str(name)).resolve()
        elif str(name).strip():
            expected = (work_dir / "runs" / str(row.job_type) / str(name)).resolve()
    except Exception:
        expected = None

    # Prefer parsing log if available
    save_dir: Path | None = None
    save_dir_rel = _try_extract_results_saved_dir(log_text)
    if save_dir_rel is not None:
        # may already be relative to work_dir (e.g. runs\detect\xxx)
        # if it's absolute, joining still works.
        try:
            save_dir = (work_dir / save_dir_rel).resolve() if not save_dir_rel.is_absolute() else save_dir_rel.resolve()
        except Exception:
            save_dir = None

    # Fallback to expected directory
    if save_dir is None and expected is not None and expected.exists():
        save_dir = expected

    if save_dir is None or not save_dir.exists():
        return

    dest = job_dir / "results"
    dest.mkdir(parents=True, exist_ok=True)
    # Copy all visualization outputs + labels (may include jpg/png and txt)
    shutil.copytree(save_dir, dest, dirs_exist_ok=True)


def execute_job(job_id: str) -> None:
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        job = db.get(JobRecord, job_id)
        if job is None:
            return

        # Safety: skip jobs already in a terminal state (e.g. retried and
        # finished by a previous worker, or cancelled by reconciliation).
        if job.status in ("SUCCESS", "FAILED", "CANCELLED"):
            return

        store = get_artifact_store()
        job_rel = store.job_dir(job.id)
        # Ensure the job directory exists via store (writes a placeholder)
        placeholder_uri = store.write_text(f"{job_rel}/.job_meta", job_id)
        job_dir = Path(placeholder_uri).parent
        log_path = job_dir / "run.log"

        job.status = "RUNNING"
        job.started_at = datetime.utcnow()
        job.log_path = str(log_path)
        job.progress = 0
        job.metrics_cache = None
        db.commit()

        cmd = build_command(job.job_type, job.payload or {}, job_dir)
        work_dir = settings.resolved_yolo_work_dir

        # --- Snapshot: freeze all reproducible info before execution ---
        try:
            snap_dir = create_job_snapshot(
                job_id=job.id,
                job_type=job.job_type,
                payload=job.payload or {},
                cmd=cmd,
                run_role=job.run_role,
                seed=job.seed,
                experiment_group_id=job.experiment_group_id,
            )
            # Persist snapshot paths on job record
            job.spec_snapshot_path = str(snap_dir / "spec_snapshot.json")
            job.resolved_command_path = str(snap_dir / "resolved_command.txt")
            job.model_snapshot_path = str(snap_dir / "model_snapshot.yaml")
            job.data_snapshot_path = str(snap_dir / "data_snapshot.yaml")
            job.env_snapshot_path = str(snap_dir / "env_snapshot.json")
            db.commit()
        except Exception:
            # Snapshot failure must not block execution
            pass

        # 执行训练命令
        with log_path.open("w", encoding="utf-8", errors="replace") as log_f:
            log_f.write(f"# cwd: {work_dir}\n")
            log_f.write(f"# cmd: {' '.join(cmd)}\n")
            log_f.flush()
            
            # 启动后台线程监控训练进度
            import threading
            stop_monitor = threading.Event()
            
            def monitor_progress():
                """后台线程：按 results.csv 轮次更新进度与指标缓存"""
                from app.database import SessionLocal
                import time

                db_thread = SessionLocal()
                try:
                    while not stop_monitor.is_set():
                        time.sleep(5)
                        try:
                            job_row = db_thread.get(JobRecord, job_id)
                            if not job_row or job_row.status != "RUNNING":
                                break
                            log_text = None
                            if job_row.log_path and Path(job_row.log_path).is_file():
                                try:
                                    log_text = Path(job_row.log_path).read_text(
                                        encoding="utf-8", errors="replace"
                                    )
                                except OSError:
                                    log_text = None
                            csv_path = resolve_results_csv(job_row, work_dir, job_dir, log_text=log_text)
                            if not csv_path:
                                continue
                            parsed = parse_results_csv(csv_path)
                            if not parsed:
                                continue
                            total_epochs = int((job_row.payload or {}).get("epochs") or 0)
                            prog = epoch_progress(parsed.get("x") or [], total_epochs, status="RUNNING")
                            job_row.metrics_cache = parsed
                            job_row.progress = prog["progress_percent"]
                            db_thread.commit()
                        except Exception:
                            pass
                finally:
                    db_thread.close()
            
            # 只在训练任务时启动监控线程
            monitor_thread = None
            if job.job_type == "train":
                monitor_thread = threading.Thread(target=monitor_progress, daemon=True)
                monitor_thread.start()
            
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(work_dir),
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            finally:
                # 确保监控线程停止
                stop_monitor.set()
                if monitor_thread and monitor_thread.is_alive():
                    monitor_thread.join(timeout=2)
            
        job.progress = 100

        summary = ""
        try:
            raw = store.read_text(f"{job_rel}/run.log")
            summary = raw[-8000:] if raw else ""
        except Exception:
            pass

        row = db.get(JobRecord, job_id)
        if row:
            row.finished_at = datetime.utcnow()
            if proc.returncode == 0:
                row.status = "SUCCESS"
                row.result_summary = summary or "OK"
                # For train/val jobs, capture results.csv + explicitly parse into metrics_cache
                if row.job_type in ("train", "val"):
                    try:
                        full_log = store.read_text(f"{job_rel}/run.log") or ""
                    except Exception:
                        full_log = ""
                    rel_dir = _try_extract_results_dir_from_log(full_log)
                    work_dir = settings.resolved_yolo_work_dir
                    search_base = work_dir
                    if rel_dir:
                        # rel_dir may be relative to work_dir
                        search_base = (work_dir / rel_dir).resolve()
                    latest = _find_latest_results_file(search_base, row.started_at)
                    if latest is None:
                        # fallback by task subdirectory
                        for task_sub in ("train", "detect", "val"):
                            latest = _find_latest_results_file(work_dir / "runs" / task_sub, row.started_at)
                            if latest:
                                break
                    if latest and latest.is_file():
                        dest = job_dir / latest.name
                        dest.write_bytes(latest.read_bytes())
                        # Store a hint in result_summary footer
                        row.result_summary = (row.result_summary or "") + f"\n\n# metrics_file: {dest.name}"
                        # --- Explicitly record the original results.csv source path ---
                        row.metrics_source_path = str(latest.resolve())
                        # --- Explicitly parse results.csv into metrics_cache ---
                        try:
                            parsed = parse_results_csv(dest)
                            if parsed and parsed.get("series"):
                                row.metrics_cache = parsed
                        except Exception:
                            pass
                if row.job_type == "detect":
                    # Copy visual prediction outputs (runs/detect/<name>) into artifacts for UI.
                    try:
                        detect_log = store.read_text(f"{job_rel}/run.log") or ""
                    except Exception:
                        detect_log = ""
                    _copy_predict_results_for_job(job_dir, detect_log, row)
            else:
                row.status = "FAILED"
                row.error_message = f"yolo exited with code {proc.returncode}"
                row.result_summary = summary
                row.metrics_source_path = None
            db.commit()

            # --- Post-run: artifacts manifest + experiment group status ---
            try:
                manifest_path = write_artifacts_manifest(job_dir)
                row2 = db.get(JobRecord, job_id)
                if row2:
                    manifest_uri = store.write_text(
                        f"{job_rel}/snapshot/artifacts_manifest.json",
                        manifest_path.read_text(encoding="utf-8"),
                    )
                    row2.artifacts_manifest_path = manifest_uri
                    db.commit()
            except Exception:
                pass

            update_experiment_group_status(db, row.experiment_group_id)
    except Exception as exc:  # noqa: BLE001
        job = db.get(JobRecord, job_id)
        if job:
            job.status = "FAILED"
            job.finished_at = datetime.utcnow()
            job.error_message = str(exc)
            db.commit()
            update_experiment_group_status(db, job.experiment_group_id)
    finally:
        db.close()


@celery_app.task(
    name="quudet.run_yolo_job",
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=30,       # wait 30s, 60s, 120s between retries
    retry_backoff_max=300,  # cap at 5 minutes
    retry_jitter=True,
)
def run_yolo_job_task(job_id: str) -> None:
    """Celery task — the only entry point for YOLO job execution.

    Automatically retries up to 3 times on infrastructure failures
    (worker crash, network error, OOM, etc.).  YOLO non-zero exits
    are handled inside ``execute_job`` and do not trigger retries
    (they set status=FAILED in the DB, then return cleanly).
    """
    execute_job(job_id)
