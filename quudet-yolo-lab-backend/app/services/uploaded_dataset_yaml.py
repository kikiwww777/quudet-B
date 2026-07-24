"""Resolve a YOLO ``data=...`` yaml path from an uploaded dataset record."""

from __future__ import annotations

from pathlib import Path

from app.models.uploaded_dataset import UploadedDataset

_MAX_YAML_SCAN = 150


def resolve_train_yaml_path(upload: UploadedDataset) -> str | None:
    """Return absolute path to a dataset yaml for ``yolo train data=...``, or None."""
    fn = (upload.filename or "").lower()
    sp = Path(upload.stored_path) if upload.stored_path else None

    if fn.endswith((".yaml", ".yml")):
        if sp and sp.is_file():
            return str(sp.resolve())
        return None

    ep = (upload.extracted_path or "").strip()
    if not ep or ep.startswith("("):
        return None
    root = Path(ep)
    if not root.is_dir():
        return None

    preferred_root = [
        "data.yaml",
        "data.yml",
        "dataset.yaml",
        "dataset.yml",
        "coco.yaml",
        "coco.yml",
    ]
    for rel in preferred_root:
        p = root / rel
        if p.is_file():
            return str(p.resolve())

    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        for rel in ("data.yaml", "data.yml", "dataset.yaml", "dataset.yml"):
            p = sub / rel
            if p.is_file():
                return str(p.resolve())

    for name in ("data.yaml", "data.yml"):
        found = sorted(root.rglob(name), key=lambda p: len(p.parts))
        for p in found:
            if p.is_file():
                return str(p.resolve())

    n = 0
    for p in root.rglob("*.yaml"):
        if not p.is_file():
            continue
        n += 1
        if n > _MAX_YAML_SCAN:
            break
        stem = p.stem.lower()
        if stem in {"data", "dataset"} or "coco" in stem:
            return str(p.resolve())

    n = 0
    for p in root.rglob("*.yml"):
        if not p.is_file():
            continue
        n += 1
        if n > _MAX_YAML_SCAN:
            break
        stem = p.stem.lower()
        if stem in {"data", "dataset"} or "coco" in stem:
            return str(p.resolve())

    # Last resort: shallowest yaml/yml (nonstandard filenames, small trees)
    pool: list[Path] = []
    for pat in ("*.yaml", "*.yml"):
        for p in root.rglob(pat):
            if p.is_file():
                pool.append(p)
            if len(pool) >= _MAX_YAML_SCAN:
                break
        if len(pool) >= _MAX_YAML_SCAN:
            break
    if pool:
        pool.sort(key=lambda p: (len(p.parts), str(p)))
        return str(pool[0].resolve())
    return None
