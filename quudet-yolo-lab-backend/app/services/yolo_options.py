"""Scan ultralytics-main cfg tree for model/dataset YAMLs and build weight presets."""

from __future__ import annotations

import re
from pathlib import Path

# Official .pt names (Ultralytics hub — auto-download when missing)
_OFFICIAL_WEIGHT_GROUPS: list[tuple[str, list[str]]] = [
    ("YOLOv8 检测", [f"yolov8{s}.pt" for s in "nsmlx"]),
    ("YOLOv9 检测", ["yolov9t.pt", "yolov9s.pt", "yolov9m.pt", "yolov9c.pt", "yolov9e.pt"]),
    ("YOLOv10 检测", [f"yolov10{s}.pt" for s in "nsmlbx"]),
    ("YOLO11 检测", [f"yolo11{s}.pt" for s in "nsmlx"]),
    ("YOLO12 检测", [f"yolo12{s}.pt" for s in "nsmlx"]),
    ("YOLO26 检测", [f"yolo26{s}.pt" for s in "nsmlx"]),
]


def _stem_has_scale_suffix(stem: str) -> bool:
    """True if stem already encodes n/s/m/l/x/t (matches Ultralytics guess_model_scale idea)."""
    return bool(re.search(r"yolo(e-)?[v]?\d+([nslmxt])", stem, re.I))


def _relative_to_work(path: Path, work: Path) -> str:
    try:
        return str(path.resolve().relative_to(work.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def scan_model_yamls(work_dir: Path) -> list[dict]:
    root = work_dir / "ultralytics-main" / "ultralytics" / "cfg" / "models"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.rglob("*.yaml")):
        if p.name == "default.yaml":
            continue
        rel = _relative_to_work(p, work_dir)
        stem = p.stem
        parts = p.relative_to(root).parts
        folder = parts[0] if parts else ""
        out.append(
            {
                "path": rel,
                "name": p.name,
                "stem": stem,
                "folder": folder,
                "scale_locked": _stem_has_scale_suffix(stem),
            }
        )
    return out


def scan_dataset_yamls(work_dir: Path) -> list[dict]:
    root = work_dir / "ultralytics-main" / "ultralytics" / "cfg" / "datasets"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(root.glob("*.yaml")):
        rel = _relative_to_work(p, work_dir)
        out.append({"path": rel, "name": p.name, "stem": p.stem})
    return out


def list_official_weights() -> list[dict]:
    items: list[dict] = []
    for group, names in _OFFICIAL_WEIGHT_GROUPS:
        for w in names:
            items.append({"group": group, "value": w, "label": w})
    return items


def scan_user_weights(work_dir: Path, artifacts_dir: Path | None = None, max_files: int = 300) -> list[dict]:
    """Find .pt under runs/ and server artifacts (training outputs)."""
    from app.config import get_settings

    bases: list[Path] = [work_dir / "runs"]
    ad = artifacts_dir or get_settings().artifacts_dir
    bases.append(Path(ad))
    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for p in base.rglob("*.pt"):
            found.append(p)
            if len(found) >= max_files:
                break
        if len(found) >= max_files:
            break
    seen: set[str] = set()
    out: list[dict] = []
    for p in sorted(found, key=lambda x: str(x))[:max_files]:
        rel = _relative_to_work(p, work_dir)
        if rel in seen:
            continue
        seen.add(rel)
        out.append({"path": rel, "label": rel})
    return out


def build_model_yaml_with_scale(yaml_rel_path: str, scale: str) -> str:
    """If base yaml (e.g. yolov8.yaml), return yolov8n.yaml in same directory."""
    p = Path(yaml_rel_path.replace("\\", "/"))
    stem = p.stem
    if _stem_has_scale_suffix(stem):
        return yaml_rel_path
    if not scale:
        return yaml_rel_path
    # yolov8 + n -> yolov8n ; yolo11 + s -> yolo11s
    new_name = f"{stem}{scale}.yaml"
    return str(p.with_name(new_name)).replace("\\", "/")


def list_scales_default() -> list[str]:
    return ["n", "s", "m", "l", "x"]


def parse_model_summary_from_yaml(work_dir: Path, yaml_rel_path: str, scale: str = "n") -> dict:
    """
    Best-effort parse of parameter count / GFLOPS from model YAML comments.
    Example in yolov8.yaml scales section:
      n: [...] # YOLOv8n summary: ... 3157200 parameters ... 8.9 GFLOPS
    """
    p = (work_dir / yaml_rel_path).resolve()
    # Ultralytics may accept model names like yolov8n.yaml even if the file
    # doesn't exist; it internally unifies the path to yolov8.yaml.
    # Mirror that here for parameter-info parsing.
    if not p.is_file():
        unified_rel = re.sub(r"(\d+)([nslmx])(.+)?$", r"\1\3", yaml_rel_path)  # yolov8n.yaml -> yolov8.yaml
        p2 = (work_dir / unified_rel).resolve()
        if p2.is_file():
            p = p2

    text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    # Prefer scale line (if present)
    chosen = ""
    for line in text:
        s = line.strip()
        if s.startswith(f"{scale}:") and "#" in s and "summary:" in s.lower():
            chosen = s.split("#", 1)[1].strip()
            break
    # Fallback: first summary line
    if not chosen:
        for line in text:
            if "summary:" in line.lower():
                chosen = line.split("#", 1)[-1].strip()
                break

    params = None
    gflops = None
    layers = None
    if chosen:
        m = re.search(r"(\d+)\s+layers", chosen)
        if m:
            layers = int(m.group(1))
        m = re.search(r"(\d+)\s+parameters", chosen)
        if m:
            params = int(m.group(1))
        m = re.search(r"([\d.]+)\s*GFLOPS", chosen, re.IGNORECASE)
        if m:
            try:
                gflops = float(m.group(1))
            except ValueError:
                gflops = None

    return {
        "ok": True,
        "yaml": yaml_rel_path.replace("\\", "/"),
        "scale": scale,
        "summary": chosen,
        "layers": layers,
        "parameters": params,
        "gflops": gflops,
    }
