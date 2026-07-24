"""
Experiment comparison service.

Aggregate all runs in an experiment group, group by run_role,
and produce a research-friendly comparison with:
- Per-run final metrics
- Aggregated mean/std per role
- Delta vs baseline
- Best run identification

Also provides `update_experiment_group_status()` for lifecycle management
(shared by executor.py and dispatch.py).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.experiment_group import ExperimentGroup
from app.models.job_record import JobRecord


def compare_experiment_group(group_id: str, db: Session) -> dict[str, Any] | None:
    """Build a structured comparison dict for an experiment group.

    Returns None if the group does not exist.
    """
    group = db.get(ExperimentGroup, group_id)
    if group is None:
        return None

    runs: list[JobRecord] = (
        db.query(JobRecord)
        .filter(JobRecord.experiment_group_id == group_id)
        .order_by(JobRecord.run_index)
        .all()
    )

    primary_metric = group.primary_metric or "metrics/mAP50-95(B)"

    # 1. Per-run metrics extraction
    run_items: list[dict[str, Any]] = []
    for r in runs:
        final_metrics = _extract_final_metrics(r, primary_metric)
        run_items.append(
            {
                "job_id": r.id,
                "run_role": r.run_role,
                "seed": r.seed,
                "run_index": r.run_index,
                "status": r.status,
                "metrics": final_metrics,  # key must match RunCompareItem.metrics
            }
        )

    # 2. Group by run_role
    by_role: dict[str, list[dict]] = {}
    for item in run_items:
        role = item["run_role"] or "unknown"
        by_role.setdefault(role, []).append(item)

    # 3. Aggregates per role (only for successful runs with metrics)
    aggregates: dict[str, Any] = {}
    for role, items in by_role.items():
        values = _collect_metric_values(items, primary_metric)
        if values:
            aggregates[role] = {
                "mean": round(statistics.mean(values), 6),
                "std": round(statistics.pstdev(values), 6) if len(values) >= 2 else 0.0,
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "n": len(values),
                "metric": primary_metric,
            }
        else:
            aggregates[role] = {
                "mean": None,
                "std": None,
                "min": None,
                "max": None,
                "n": 0,
                "metric": primary_metric,
            }

    # 4. Delta vs baseline
    delta = None
    baseline_agg = aggregates.get("baseline")
    if baseline_agg and baseline_agg["mean"] is not None:
        delta = {}
        for role, agg in aggregates.items():
            if role == "baseline" or agg["mean"] is None:
                continue
            absolute = round(agg["mean"] - baseline_agg["mean"], 6)
            relative = round(absolute / baseline_agg["mean"] * 100, 2) if baseline_agg["mean"] != 0 else None
            delta[role] = {
                "absolute": absolute,
                "relative_percent": relative,
                "baseline_mean": baseline_agg["mean"],
                f"{role}_mean": agg["mean"],
            }

    # 5. Best run (highest primary metric)
    best_run_id = None
    best_value = float("-inf")
    for item in run_items:
        val = _get_primary_value(item, primary_metric)
        if val is not None and val > best_value:
            best_value = val
            best_run_id = item["job_id"]

    # 6. Track which YOLO key was actually resolved from primary_metric
    primary_metric_resolved = None
    for item in run_items:
        resolved = item.get("_primary_metric_resolved")
        if resolved:
            primary_metric_resolved = resolved
            break

    # 7. Summary text
    summary_text = _build_summary_text(aggregates, delta, primary_metric)

    return {
        "group_id": group_id,
        "primary_metric": primary_metric,
        "primary_metric_resolved": primary_metric_resolved,
        "status": group.status,
        "runs": run_items,
        "aggregates": aggregates,
        "delta_vs_baseline": delta,
        "best_run_id": best_run_id,
        "summary_text": summary_text,
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _extract_final_metrics(run: JobRecord, primary_metric: str) -> dict[str, float] | None:
    """Extract the final (last-epoch) metric values from a run's metrics_cache."""
    cache = run.metrics_cache
    if not isinstance(cache, dict):
        return None

    series = cache.get("series") or {}
    if not series:
        return None

    final: dict[str, float] = {}
    for metric_name, values in series.items():
        if isinstance(values, list) and len(values) > 0:
            # Take the last valid (non-NaN) value
            for v in reversed(values):
                try:
                    fv = float(v)
                    # Check for NaN
                    if fv == fv:
                        final[metric_name] = round(fv, 6)
                        break
                except (TypeError, ValueError):
                    continue
    return final if final else None


# ---------------------------------------------------------------------------
# Metric key alias resolution
# ---------------------------------------------------------------------------
# Maps a normalised key to the list of real YOLO/Ultralytics column names
# that the parser may have produced from results.csv.  Handles common
# research-semantic names (e.g. "mAP@50") → internal keys (e.g. "metrics/mAP50(B)").
_METRIC_ALIASES: dict[str, list[str]] = {
    "map50": [
        "metrics/mAP50(B)",
        "metrics/mAP50",
        "mAP50(B)",
        "mAP50",
    ],
    "map50_95": [
        "metrics/mAP50-95(B)",
        "metrics/mAP50-95",
        "mAP50-95(B)",
        "mAP50-95",
    ],
    "precision": [
        "metrics/precision(B)",
        "metrics/precision",
        "precision(B)",
        "precision",
    ],
    "recall": [
        "metrics/recall(B)",
        "metrics/recall",
        "recall(B)",
        "recall",
    ],
    "f1_score": [
        "metrics/f1(B)",
        "metrics/f1",
        "f1(B)",
        "f1",
    ],
    "map50_95_small": [
        "metrics/mAP50-95(S)",
        "mAP50-95(S)",
        "metrics/mAP50-95(B)",
    ],
    "map50_small": [
        "metrics/mAP50(S)",
        "mAP50(S)",
        "metrics/mAP50(B)",
    ],
    "ap_small": [
        "metrics/mAP50(S)",
        "mAP50(S)",
        "metrics/mAP50(B)",
    ],
    "map50_95_medium": [
        "metrics/mAP50-95(M)",
        "mAP50-95(M)",
    ],
    "map50_95_large": [
        "metrics/mAP50-95(L)",
        "mAP50-95(L)",
    ],
}


def _normalise_metric_key(key: str) -> str:
    """Normalise a metric key for alias lookup.

    - lowercases
    - strips whitespace
    - removes ``@`` (mAP@50 → map50)
    - replaces ``:``, ``-``, ``.`` with ``_``
    - collapses multiple ``_``
    """
    normalized = key.strip().lower()
    # Remove @ entirely (mAP@50 → map50)
    normalized = normalized.replace("@", "")
    # Replace separator chars with _
    for c in (":", "-", "."):
        normalized = normalized.replace(c, "_")
    # Collapse multiple underscores
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("_")
    return normalized


def _resolve_metric_key(primary_metric: str, available_keys: set[str]) -> str | None:
    """Resolve a research-semantic metric name to a real YOLO key.

    Resolution order:
    1. Direct hit — if ``primary_metric`` is already in ``available_keys``.
    2. Alias table — normalise the name, look up ``_METRIC_ALIASES``, return
       the first alias that exists in ``available_keys``.
    3. Fuzzy scan — iterate ``available_keys``, normalise each, return the
       first that matches the normalised ``primary_metric``.
    """
    # 1. Direct
    if primary_metric in available_keys:
        return primary_metric

    # 2. Alias table
    norm = _normalise_metric_key(primary_metric)
    candidates = _METRIC_ALIASES.get(norm)
    if candidates:
        for c in candidates:
            if c in available_keys:
                return c

    # 3. Fuzzy: normalise every available key and compare
    for avail in available_keys:
        if _normalise_metric_key(avail) == norm:
            return avail

    return None


def _get_primary_value(item: dict[str, Any], primary_metric: str) -> float | None:
    """Get the primary metric value from a run item.

    Uses ``_resolve_metric_key`` to handle research-semantic names like
    ``mAP@50`` mapping to YOLO-internal ``metrics/mAP50(B)``.
    """
    metrics = item.get("metrics") or {}
    if not isinstance(metrics, dict) or not metrics:
        return None

    resolved = _resolve_metric_key(primary_metric, set(metrics.keys()))
    if resolved is None:
        # Stash the miss for debugging (item is passed by reference)
        item["_primary_metric_resolved"] = None
        return None

    item["_primary_metric_resolved"] = resolved
    return metrics.get(resolved)


def _collect_metric_values(items: list[dict], primary_metric: str) -> list[float]:
    """Collect non-None primary metric values from a list of run items."""
    values: list[float] = []
    for item in items:
        if item.get("status") != "SUCCESS":
            continue
        v = _get_primary_value(item, primary_metric)
        if v is not None:
            values.append(v)
    return values


# ---------------------------------------------------------------------------
# Experiment group lifecycle status update
# ---------------------------------------------------------------------------

_TERMINAL = frozenset({"SUCCESS", "FAILED", "CANCELLED"})
_NON_TERMINAL = frozenset({"PENDING", "RUNNING", "RETRYING"})


def update_experiment_group_status(db: Session, group_id: str | None) -> None:
    """Re-evaluate experiment group status based on all its runs.

    Call this after any job in the group changes status.

    State machine:
        PENDING                    — no jobs terminal, no jobs running
        RUNNING                    — at least one job non-terminal
        SUCCESS                    — all jobs terminal, all SUCCESS
        PARTIAL                    — all jobs terminal, mixed SUCCESS/FAILED/CANCELLED
        FAILED                     — all jobs terminal, all FAILED
        CANCELLED                  — all jobs terminal, all CANCELLED
    """
    if not group_id:
        return
    group = db.get(ExperimentGroup, group_id)
    if group is None:
        return

    runs = (
        db.query(JobRecord)
        .filter(JobRecord.experiment_group_id == group_id)
        .all()
    )
    if not runs:
        return

    statuses = {r.status for r in runs}

    # Are all jobs in a terminal state?
    all_terminal = statuses.issubset(_TERMINAL)

    if all_terminal:
        if all(s == "SUCCESS" for s in statuses):
            new_status = "SUCCESS"
        elif all(s == "FAILED" for s in statuses):
            new_status = "FAILED"
        elif all(s == "CANCELLED" for s in statuses):
            new_status = "CANCELLED"
        else:
            new_status = "PARTIAL"
    else:
        # At least one job is still in a non-terminal state (PENDING, RUNNING, RETRYING …)
        # → group is RUNNING
        new_status = "RUNNING"

    if new_status != group.status:
        group.status = new_status
        if new_status in ("SUCCESS", "FAILED", "CANCELLED", "PARTIAL"):
            group.finished_at = datetime.now(timezone.utc)
        db.commit()


def _build_summary_text(
    aggregates: dict[str, Any],
    delta: dict[str, Any] | None,
    primary_metric: str,
) -> str:
    """Build a human-readable summary string.

    Always returns a non-empty string, even when no metrics are available.
    """
    lines: list[str] = []
    lines.append(f"Experiment Comparison Summary")
    lines.append(f"Primary metric: {primary_metric}")
    lines.append("")

    if not aggregates:
        lines.append("  No run data available for aggregation.")
        return "\n".join(lines)

    has_any_metrics = False
    for role, agg in aggregates.items():
        if agg["mean"] is not None:
            has_any_metrics = True
            lines.append(
                f"  {role}: {agg['mean']:.4f} +/- {agg['std']:.4f}  (n={agg['n']})"
            )
        else:
            lines.append(f"  {role}: no metrics available")

    if not has_any_metrics:
        lines.append("")
        lines.append("  No successful runs with valid metrics — aggregation not possible.")
        return "\n".join(lines)

    if delta:
        lines.append("")
        lines.append("Delta vs baseline:")
        for role, d in delta.items():
            sign = "+" if d["absolute"] >= 0 else ""
            rel = d.get("relative_percent")
            if rel is not None:
                lines.append(
                    f"  {role}: {sign}{d['absolute']:.4f}  ({sign}{rel:.2f}%)"
                )
            else:
                lines.append(f"  {role}: {sign}{d['absolute']:.4f}  (relative%: N/A — baseline mean is 0)")

    return "\n".join(lines)
