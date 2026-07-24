from typing import Annotated

from fastapi import APIRouter, Depends
import re
from pathlib import Path

from app.api.deps import get_current_user
from app.config import get_settings
from app.models.user import User
from app.services.yolo_options import (
    build_model_yaml_with_scale,
    list_official_weights,
    list_scales_default,
    parse_model_summary_from_yaml,
    scan_dataset_yamls,
    scan_model_yamls,
    scan_user_weights,
)

router = APIRouter(prefix="/options", tags=["options"])


@router.get("/yolo")
def yolo_options(
    _: Annotated[User, Depends(get_current_user)],
):
    settings = get_settings()
    work = settings.resolved_yolo_work_dir
    return {
        "work_dir": str(work),
        "model_yamls": scan_model_yamls(work),
        "datasets": scan_dataset_yamls(work),
        "scales": list_scales_default(),
        "official_weights": list_official_weights(),
        "user_weights": scan_user_weights(work),
    }


@router.get("/resolve-model")
def resolve_model_path(
    user: Annotated[User, Depends(get_current_user)],
    yaml_path: str,
    scale: str = "n",
):
    _ = user
    return {"model": build_model_yaml_with_scale(yaml_path, scale)}


@router.get("/model-info")
def model_info(
    user: Annotated[User, Depends(get_current_user)],
    yaml_path: str,
    scale: str = "n",
):
    _ = user
    settings = get_settings()
    work = settings.resolved_yolo_work_dir
    resolved = build_model_yaml_with_scale(yaml_path, scale)
    info = parse_model_summary_from_yaml(work, resolved, scale=scale)
    return {"resolved_model": resolved, "info": info}


@router.get("/suggest-train-name")
def suggest_train_name(
    _: Annotated[User, Depends(get_current_user)],
    prefix: str = "quudet-train",
):
    """Suggest next experiment name based on existing runs directories.

    We scan: <work_dir>/runs/detect/runs/train and find max suffix for prefix.
    Example: quudet-train5 -> suggest quudet-train6.
    """
    settings = get_settings()
    work = settings.resolved_yolo_work_dir
    base = (Path(work) / "runs" / "detect" / "runs" / "train").resolve()
    if not base.exists():
        return {"ok": True, "base": str(base), "suggested": prefix}
    pat = re.compile(rf"^{re.escape(prefix)}(?P<n>\\d+)?$", re.IGNORECASE)
    max_n = 0
    for d in base.iterdir():
        if not d.is_dir():
            continue
        m = pat.match(d.name)
        if not m:
            continue
        n_raw = m.group("n")
        if n_raw:
            try:
                max_n = max(max_n, int(n_raw))
            except ValueError:
                pass
        else:
            # prefix without number counts as 1
            max_n = max(max_n, 1)
    suggested = f"{prefix}{max_n + 1}" if max_n else prefix
    return {"ok": True, "base": str(base), "suggested": suggested}
