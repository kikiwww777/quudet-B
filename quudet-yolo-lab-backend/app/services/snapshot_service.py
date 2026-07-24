"""
Snapshot service: freeze all reproducible information before job execution.

Generates a complete snapshot directory under `<job_dir>/snapshot/`:
    spec_snapshot.json      – original payload + role + experiment group info
    resolved_command.txt    – final CLI command
    model_snapshot.yaml     – copy of model YAML (or path record if .pt)
    data_snapshot.yaml      – copy of dataset YAML
    env_snapshot.json       – Python / torch / ultralytics / platform / GPU
    artifacts_manifest.json – inventory of expected outputs (populated post-run)
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings


def create_job_snapshot(
    job_id: str,
    job_type: str,
    payload: dict[str, Any],
    cmd: list[str],
    *,
    run_role: str | None = None,
    seed: int | None = None,
    experiment_group_id: str | None = None,
    experiment_group_name: str | None = None,
) -> Path:
    """Create all snapshot files under <artifacts_dir>/<job_id>/snapshot/.

    Returns the snapshot directory path.
    """
    settings = get_settings()
    job_dir = settings.artifacts_dir / job_id
    snap_dir = job_dir / "snapshot"
    snap_dir.mkdir(parents=True, exist_ok=True)

    _write_spec_snapshot(
        snap_dir, job_id, job_type, payload,
        run_role=run_role, seed=seed,
        experiment_group_id=experiment_group_id,
        experiment_group_name=experiment_group_name,
    )
    _write_resolved_command(snap_dir, cmd)
    _capture_model_snapshot(snap_dir, payload)
    _capture_data_snapshot(snap_dir, payload)
    _write_env_snapshot(snap_dir)

    return snap_dir


def write_artifacts_manifest(job_dir: Path, *, extra_paths: list[Path] | None = None) -> Path:
    """Scan job_dir for known YOLO outputs and write an artifacts_manifest.json.

    Called after execution completes.  Returns the manifest path.
    """
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": _scan_artifacts(job_dir),
    }
    if extra_paths:
        manifest["extra"] = [str(p) for p in extra_paths if p.exists()]

    manifest_path = job_dir / "snapshot" / "artifacts_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return manifest_path


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _write_spec_snapshot(
    snap_dir: Path,
    job_id: str,
    job_type: str,
    payload: dict[str, Any],
    *,
    run_role: str | None = None,
    seed: int | None = None,
    experiment_group_id: str | None = None,
    experiment_group_name: str | None = None,
) -> None:
    spec: dict[str, Any] = {
        "job_id": job_id,
        "job_type": job_type,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "run_role": run_role,
        "seed": seed,
        "experiment_group_id": experiment_group_id,
        "experiment_group_name": experiment_group_name,
    }
    (snap_dir / "spec_snapshot.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _write_resolved_command(snap_dir: Path, cmd: list[str]) -> None:
    (snap_dir / "resolved_command.txt").write_text(
        " ".join(cmd) + "\n", encoding="utf-8"
    )


def _capture_model_snapshot(snap_dir: Path, payload: dict[str, Any]) -> None:
    """If payload['model'] is a .yaml file, copy it; if .pt, record path only."""
    model = (payload or {}).get("model")
    if not model or not isinstance(model, str):
        return

    model_path = Path(model)
    if model_path.suffix in (".yaml", ".yml") and model_path.is_file():
        dest = snap_dir / "model_snapshot.yaml"
        shutil.copy2(model_path, dest)
    else:
        # Record path (e.g. for .pt weights or non-file references)
        (snap_dir / "model_snapshot.yaml").write_text(
            f"# model path (not copied)\nmodel: {model}\n", encoding="utf-8"
        )


def _capture_data_snapshot(snap_dir: Path, payload: dict[str, Any]) -> None:
    """If payload['data'] is a .yaml file, copy it."""
    data = (payload or {}).get("data")
    if not data or not isinstance(data, str):
        return

    data_path = Path(data)
    if data_path.suffix in (".yaml", ".yml") and data_path.is_file():
        dest = snap_dir / "data_snapshot.yaml"
        shutil.copy2(data_path, dest)
    else:
        (snap_dir / "data_snapshot.yaml").write_text(
            f"# data path (not copied)\ndata: {data}\n", encoding="utf-8"
        )


def _write_env_snapshot(snap_dir: Path) -> None:
    env: dict[str, Any] = {
        "platform": platform.platform(),
        "python_version": sys.version,
        "torch_version": _try_get_torch_version(),
        "ultralytics_version": _try_get_ultralytics_version(),
        "gpu_info": _try_get_gpu_info(),
    }
    (snap_dir / "env_snapshot.json").write_text(
        json.dumps(env, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _try_get_torch_version() -> str | None:
    try:
        import torch
        return torch.__version__
    except Exception:
        return None


def _try_get_ultralytics_version() -> str | None:
    try:
        import ultralytics
        return ultralytics.__version__
    except Exception:
        return None


def _try_get_gpu_info() -> list[str] | None:
    try:
        import torch
        if torch.cuda.is_available():
            return [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        return ["cpu"]
    except Exception:
        return None


def _scan_artifacts(job_dir: Path) -> list[dict[str, Any]]:
    """Walk job_dir and list key YOLO output files."""
    known_patterns = [
        "run.log",
        "results.csv",
        "results.txt",
        "args.yaml",
        "best.pt",
        "last.pt",
        "confusion_matrix.png",
        "confusion_matrix_normalized.png",
        "PR_curve.png",
        "P_curve.png",
        "R_curve.png",
        "F1_curve.png",
        "labels.jpg",
        "train_batch*.jpg",
        "val_batch*.jpg",
    ]

    files: list[dict[str, Any]] = []
    for root, _dirs, filenames in job_dir.walk():
        for fname in filenames:
            fpath = root / fname
            try:
                stat = fpath.stat()
                files.append(
                    {
                        "relative_path": str(fpath.relative_to(job_dir)).replace("\\", "/"),
                        "size_bytes": stat.st_size,
                        "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
            except OSError:
                pass
    return files
