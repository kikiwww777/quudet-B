"""
Expand an ExperimentGroupCreate into multiple JobRecord rows.

Handles:
- Per-run payload merging (group-level defaults + per-run overrides)
- Auto seed expansion (a single ExperimentRunCreate with multiple seeds → multiple jobs)
- Auto run_index assignment
- Auto project/name defaults
- Sweep grid expansion (cartesian product of parameter values × seeds)
"""

from __future__ import annotations

import itertools
from typing import Any

from app.schemas.experiment import ExperimentGroupCreate, ExperimentRunCreate, SweepSpec


def _merge_payload(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge: overrides win."""
    merged = dict(base)
    merged.update(overrides)
    return merged


def expand_runs(
    group: ExperimentGroupCreate,
    *,
    base_payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return a flat list of per-job dicts ready for JobRecord creation.

    Each dict contains:
        job_type, payload, project_name, run_role, seed, run_index, target_node_id
    """
    base = base_payload or {}
    expanded: list[dict[str, Any]] = []
    idx = 0
    augmentation_comparison = any(
        "mosaic" in run_spec.payload or "mixup" in run_spec.payload
        for run_spec in group.runs
    )

    for run_spec in group.runs:
        seeds = _normalize_seeds(run_spec)
        for seed in seeds:
            payload = _merge_payload(base, run_spec.payload)
            # Auto-fill seed in payload so YOLO runner can use it
            if seed is not None:
                payload["seed"] = seed

            # Auto-fill project/name if not set
            project = payload.get("project") or group.name
            name = payload.get("name") or _default_run_name(run_spec.role, seed, idx)
            payload["project"] = project
            payload["name"] = name
            if augmentation_comparison:
                _normalize_close_mosaic(payload)

            if run_spec.required_gpu:
                payload["required_gpu"] = True

            expanded.append(
                {
                    "job_type": run_spec.job_type,
                    "payload": payload,
                    "project_name": f"{project}/{name}",
                    "run_role": run_spec.role,
                    "seed": seed,
                    "run_index": idx,
                    "target_node_id": run_spec.target_node_id,
                    "execution_target": run_spec.execution_target,
                    "required_gpu": run_spec.required_gpu,
                }
            )
            idx += 1

    return expanded


def _normalize_close_mosaic(payload: dict[str, Any]) -> None:
    try:
        epochs = int(payload.get("epochs") or 0)
        close_mosaic = int(payload.get("close_mosaic") or 0)
    except (TypeError, ValueError):
        return
    if "close_mosaic" not in payload or (epochs and close_mosaic >= epochs):
        payload["close_mosaic"] = 0


def _normalize_seeds(run_spec: ExperimentRunCreate) -> list[int | None]:
    """Extract seeds from run_spec. Supports:
    - run_spec.seed as a single int
    - run_spec.payload["seeds"] as a list of ints
    - run_spec.payload["seed"] as a single int
    If none set, returns [None] (single run without seed).
    """
    # Explicit seed on the run spec wins
    if run_spec.seed is not None:
        return [run_spec.seed]

    # Check payload for seeds list
    seeds_raw = run_spec.payload.get("seeds")
    if isinstance(seeds_raw, list) and len(seeds_raw) > 0:
        return [int(s) for s in seeds_raw]

    # Check payload for single seed
    seed_raw = run_spec.payload.get("seed")
    if seed_raw is not None:
        return [int(seed_raw)]

    return [None]


def _default_run_name(role: str, seed: int | None, index: int) -> str:
    """Generate a reproducible default run name.

    Examples:
        baseline_seed42_01
        variant_02
        ablation_seed0_03
    """
    parts = [role]
    if seed is not None:
        parts.append(f"seed{seed}")
    parts.append(f"{index:02d}")
    return "_".join(parts)


def expand_sweep(
    group_name: str,
    sweep: SweepSpec,
) -> list[dict[str, Any]]:
    """Expand a SweepSpec into a flat list of per-job dicts.

    Generates the cartesian product of all grid values, then
    repeats each combination for every seed.

    Example:
        grid={"lr0": [0.001, 0.01], "batch": [16, 32]}, seeds=[42, 43]
        → 2×2×2 = 8 runs
    """
    if not sweep.grid:
        return []

    # 1. Build ordered list of (param_name, values)
    param_names = list(sweep.grid.keys())
    param_values = [sweep.grid[name] for name in param_names]

    # 2. Cartesian product: all combinations
    combinations = list(itertools.product(*param_values))

    # 3. Seeds
    seeds = sweep.seeds if sweep.seeds else [None]

    # 4. Expand
    expanded: list[dict[str, Any]] = []
    idx = 0
    base = dict(sweep.base_payload)

    for combo in combinations:
        # Build extra_args for this combination
        combo_extra: dict[str, Any] = {}
        combo_label_parts: list[str] = []
        for name, val in zip(param_names, combo):
            combo_extra[name] = val
            # Build a compact label: "lr0-0.001_batch-16"
            val_str = str(val).replace(".", "p")
            combo_label_parts.append(f"{name}-{val_str}")

        combo_label = "_".join(combo_label_parts)

        for seed in seeds:
            payload = dict(base)
            # Merge combo into extra_args
            existing_extra = dict(payload.get("extra_args") or {})
            existing_extra.update(combo_extra)
            payload["extra_args"] = existing_extra

            if seed is not None:
                payload["seed"] = seed

            # Auto-fill project/name
            project = payload.get("project") or group_name
            name = payload.get("name") or _sweep_run_name(combo_label, seed, idx)
            payload["project"] = project
            payload["name"] = name

            expanded.append(
                {
                    "job_type": sweep.job_type,
                    "payload": payload,
                    "project_name": f"{project}/{name}",
                    "run_role": "sweep",
                    "seed": seed,
                    "run_index": idx,
                    "target_node_id": None,
                }
            )
            idx += 1

    return expanded


def _sweep_run_name(combo_label: str, seed: int | None, index: int) -> str:
    """Generate a sweep run name.

    Examples:
        sweep_lr0-0p001_batch-16_seed42_00
        sweep_lr0-0p01_batch-32_03
    """
    parts = ["sweep", combo_label]
    if seed is not None:
        parts.append(f"seed{seed}")
    parts.append(f"{index:02d}")
    return "_".join(parts)
