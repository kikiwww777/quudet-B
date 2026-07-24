"""
Build YOLO CLI command from job record.
Must match Ultralytics 8.x CLI: https://docs.ultralytics.com/usage/cli/
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from app.config import get_settings


def _pick(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        v = payload.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return default


def _imgsz_train_val(payload: dict[str, Any]) -> str:
    """Train/val: Ultralytics 仅支持正方形 imgsz；若宽高不同则取 max(宽,高)。"""
    w_s = _pick(payload, "imgsz_w", "img_w", "width", default="")
    h_s = _pick(payload, "imgsz_h", "img_h", "height", default="")
    legacy = _pick(payload, "imgsz", "image_size", default="")
    if w_s and h_s:
        try:
            w_i, h_i = int(w_s), int(h_s)
            if w_i <= 0 or h_i <= 0:
                return legacy or "640"
            if w_i == h_i:
                return str(w_i)
            return str(max(w_i, h_i))
        except ValueError:
            pass
    if legacy:
        return legacy
    return "640"


def _imgsz_predict(payload: dict[str, Any]) -> str:
    """Predict：可为整数或 [高, 宽] 列表字符串。"""
    w_s = _pick(payload, "imgsz_w", "img_w", "width", default="")
    h_s = _pick(payload, "imgsz_h", "img_h", "height", default="")
    legacy = _pick(payload, "imgsz", default="")
    if w_s and h_s:
        try:
            w_i, h_i = int(w_s), int(h_s)
            if w_i <= 0 or h_i <= 0:
                return legacy or "640"
            if w_i == h_i:
                return str(w_i)
            return f"[{h_i},{w_i}]"
        except ValueError:
            pass
    if legacy:
        return legacy
    return "640"


def _append_train_opt(cmd: list[str], payload: dict[str, Any], key: str, *alt: str) -> None:
    v = _pick(payload, key, *alt, default="")
    if v:
        cmd.append(f"{key}={v}")


_SAFE_SCALAR_TYPES = (str, int, float, bool)

# Extra-args keys that would collide with first-class params or the
# whitelist – these are silently dropped to avoid duplicate definitions.
_EXTRA_ARGS_BLOCKLIST: set[str] = {
    "model", "data", "epochs", "batch", "imgsz", "device",
    "project", "name", "pretrained", "hyp", "freeze", "optimizer",
    "seed", "workers", "lr0", "lrf", "momentum", "weight_decay",
    "warmup_epochs", "cos_lr", "amp", "mosaic", "mixup", "cutmix",
    "copy_paste", "close_mosaic",
    # Safety: block anything that looks like a shell injection vector
    "shell", "command", "cmd",
}


def _append_extra_args(cmd: list[str], payload: dict[str, Any]) -> None:
    """Safely append extra_args from payload as key=value.

    Rules:
    - Only simple scalar values (str, int, float, bool) are allowed.
    - Keys matching first-class params are silently dropped.
    - No shell metacharacters in keys.
    """
    extra: dict[str, Any] = payload.get("extra_args") or {}
    if not isinstance(extra, dict):
        return

    for key, value in extra.items():
        # Validate key
        if not isinstance(key, str) or not key.strip():
            continue
        key = key.strip()
        if key in _EXTRA_ARGS_BLOCKLIST:
            continue
        # Key must be a valid identifier-like string (no shell metacharacters)
        if not _is_safe_key(key):
            continue

        # Validate value: only simple scalars
        if not isinstance(value, _SAFE_SCALAR_TYPES):
            continue

        # Format value
        if isinstance(value, bool):
            val_str = str(value).lower()
        else:
            val_str = str(value)
        cmd.append(f"{key}={val_str}")


def _is_safe_key(key: str) -> bool:
    """Reject keys that contain shell metacharacters or spaces."""
    dangerous = {" ", "|", "&", ";", "$", "`", "(", ")", "{", "}", "<", ">", "\n", "\r", "'", '"', "\\"}
    return not any(ch in key for ch in dangerous)


def _work_dir() -> Path:
    return get_settings().resolved_yolo_work_dir


def yolo_executable() -> str:
    # Try system PATH first
    found = shutil.which("yolo") or shutil.which("yolo.exe")
    if found:
        return found
    # Try venv Scripts directory (Windows)
    import sys
    venv_scripts = Path(sys.executable).parent
    for name in ("yolo.exe", "yolo"):
        candidate = venv_scripts / name
        if candidate.is_file():
            return str(candidate)
    # Fallback: use the current Python (venv) directly with entrypoint
    import sys as _sys
    return str(_sys.executable)


def build_command(job_type: str, payload: dict[str, Any], job_dir: Path) -> list[str]:
    """Return argv list for subprocess (no shell)."""
    # yapf: disable
    payload = payload or {}
    wd = _work_dir()
    exe = yolo_executable()

    # If using python as fallback, use entrypoint directly
    exe_is_python = "python" in Path(exe).stem.lower()
    use_python_module = exe_is_python
    use_python_entrypoint = exe_is_python

    if job_type == "train":
        model = _pick(payload, "model", "根配置文件", "model_yaml")
        data = _pick(payload, "data", "数据集配置", "dataset_yaml")
        epochs = _pick(payload, "epochs", "训练轮次", default="100")
        batch = _pick(payload, "batch", "批次大小", default="16")
        imgsz = _imgsz_train_val(payload)
        device = _pick(payload, "device", "设备")
        project = _pick(payload, "project", "项目名称", default="runs/train")
        name = _pick(payload, "name", "实验名称", default="exp")
        pretrained = _pick(payload, "pretrained", "weights", "预训练权重（可选）")
        hyp = _pick(payload, "hyp", "超参数文件")
        freeze = _pick(payload, "freeze", "冻结模型参数", "冻结层数")
        optimizer = _pick(payload, "optimizer", "优化器")
        # --- 科研化: seed 作为一级标准字段 ---
        seed = _pick(payload, "seed")

        cmd: list[str] = []
        if use_python_entrypoint:
            cmd.extend([exe, "-c", "from ultralytics.cfg import entrypoint; entrypoint()"])
        elif use_python_module:
            cmd.extend([exe, "-m", "ultralytics"])
        else:
            cmd.append(exe)
        cmd.extend([
            "train",
            f"model={model}",
            f"data={data}",
            f"epochs={epochs}",
            f"batch={batch}",
            f"imgsz={imgsz}",
            f"project={project}",
            f"name={name}",
        ])
        if pretrained:
            cmd.append(f"pretrained={pretrained}")
        if hyp:
            cmd.append(f"hyp={hyp}")
        if freeze:
            cmd.append(f"freeze={freeze}")
        if optimizer:
            cmd.append(f"optimizer={optimizer}")
        if device:
            cmd.append(f"device={device}")
        if seed:
            cmd.append(f"seed={seed}")
        _append_train_opt(cmd, payload, "workers")
        _append_train_opt(cmd, payload, "lr0")
        _append_train_opt(cmd, payload, "lrf")
        _append_train_opt(cmd, payload, "momentum")
        _append_train_opt(cmd, payload, "weight_decay")
        _append_train_opt(cmd, payload, "warmup_epochs")
        _append_train_opt(cmd, payload, "cos_lr")
        _append_train_opt(cmd, payload, "amp")
        _append_train_opt(cmd, payload, "mosaic")
        _append_train_opt(cmd, payload, "mixup")
        _append_train_opt(cmd, payload, "cutmix")
        _append_train_opt(cmd, payload, "copy_paste")
        _append_train_opt(cmd, payload, "close_mosaic")

        # --- Extra args: safe scalar-only key=value append ---
        _append_extra_args(cmd, payload)

        return cmd

    if job_type == "val":
        model = _pick(payload, "model", "weights", "模型权重")
        data = _pick(payload, "data", "数据集配置")
        batch = _pick(payload, "batch", "批次大小", default="1")
        imgsz = _imgsz_train_val(payload)
        conf = _pick(payload, "conf", "置信度阈值", default="0.001")
        iou = _pick(payload, "iou", "IoU阈值", default="0.50")
        device = _pick(payload, "device", "设备")
        project = _pick(payload, "project", "项目名称", default="runs/val")
        name = _pick(payload, "name", "实验名称", default="exp")

        cmd = []
        if use_python_entrypoint:
            cmd.extend([exe, "-c", "from ultralytics.cfg import entrypoint; entrypoint()"])
        elif use_python_module:
            cmd.extend([exe, "-m", "ultralytics"])
        else:
            cmd.append(exe)
        cmd.extend([
            "val",
            f"model={model}",
            f"data={data}",
            f"batch={batch}",
            f"imgsz={imgsz}",
            f"conf={conf}",
            f"iou={iou}",
            f"project={project}",
            f"name={name}",
        ])
        if device:
            cmd.append(f"device={device}")
        _append_extra_args(cmd, payload)
        return cmd

    if job_type == "detect":
        model = _pick(payload, "model", "weights", "模型权重")
        default_src = str(wd / "ultralytics-main" / "ultralytics" / "assets" / "bus.jpg")
        if not Path(default_src).is_file():
            default_src = str(wd)
        source = _pick(payload, "source", "输入路径", default=default_src)
        if source in ("", "."):
            source = default_src
        imgsz = _imgsz_predict(payload)
        conf = _pick(payload, "conf", "置信度阈值", default="0.25")
        iou = _pick(payload, "iou", "IoU阈值", default="0.50")
        device = _pick(payload, "device", "设备")
        project = _pick(payload, "project", "项目名称", default="runs/detect")
        name = _pick(payload, "name", "实验名称", default="exp")
        classes = _pick(payload, "classes", "类别过滤（可选）")
        cmd = []
        if use_python_entrypoint:
            cmd.extend([exe, "-c", "from ultralytics.cfg import entrypoint; entrypoint()"])
        elif use_python_module:
            cmd.extend([exe, "-m", "ultralytics"])
        else:
            cmd.append(exe)
        cmd.extend([
            "predict",
            f"model={model}",
            f"source={source}",
            f"imgsz={imgsz}",
            f"conf={conf}",
            f"iou={iou}",
            f"project={project}",
            f"name={name}",
            # Ensure Ultralytics saves visualized predictions (with boxes) to disk.
            # Without this, some environments may run predict without producing images.
            "save=True",
        ])
        if device:
            cmd.append(f"device={device}")
        if classes:
            cmd.append(f"classes={classes}")
        _append_extra_args(cmd, payload)
        return cmd

    raise ValueError(f"Unknown job_type: {job_type}")


def resolve_paths_in_payload(payload: dict[str, Any], dataset_storage_path: str | None) -> dict[str, Any]:
    """Attach dataset zip path hint for unzip in worker if needed."""
    out = dict(payload or {})
    if dataset_storage_path:
        out["_dataset_path"] = dataset_storage_path
    return out
