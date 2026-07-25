from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AgentPaths:
    yolo_work_dir: Path
    data_dir: Path
    artifacts_dir: Path
    provision_cache_dir: Path


def get_agent_paths() -> AgentPaths:
    backend_dir = Path(__file__).resolve().parents[2]
    repo_root = backend_dir.parent
    yolo_work_dir = Path(os.getenv("YOLO_WORK_DIR") or repo_root).resolve()
    data_dir = Path(os.getenv("DATA_DIR") or backend_dir / "data").resolve()
    artifacts_dir = data_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    return AgentPaths(
        yolo_work_dir=yolo_work_dir,
        data_dir=data_dir,
        artifacts_dir=artifacts_dir,
        provision_cache_dir=artifacts_dir / "provision_cache",
    )
