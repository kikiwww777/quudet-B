import csv
import io
import os
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.experiment_group import ExperimentGroup
from app.models.job_record import JobRecord
from app.models.user import User
from app.schemas.experiment import (
    ExperimentComparisonRead,
    ExperimentGroupCreate,
    ExperimentGroupDetailRead,
    ExperimentGroupRead,
)
from app.schemas.job import JobRead
from app.services.experiment_compare import compare_experiment_group
from app.services.job_expand_service import expand_runs, expand_sweep

# ── Experiment Preparation gate ──────────────────────────────────────────
_PREP_GATE = None


def _get_prep_gate():
    global _PREP_GATE
    if _PREP_GATE is not None:
        return _PREP_GATE
    here = Path(__file__).resolve().parent
    for ancestor in [here] + list(here.parents):
        candidate = ancestor / "experiment_preparation"
        if candidate.exists() and (candidate / "__init__.py").exists():
            sys.path.insert(0, str(ancestor))
            from experiment_preparation.quudet_adapter import check_experiment_group as cg  # type: ignore[import-untyped]  # noqa: E402
            _PREP_GATE = cg
            return _PREP_GATE
    return None


router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.post("", response_model=ExperimentGroupDetailRead, status_code=201)
def create_experiment_group(
    body: ExperimentGroupCreate,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Create an experiment group and expand its runs into individual jobs.

    Accepts either explicit runs or a sweep grid (not both).
    """
    # ── Preparation gate ──
    gate = _get_prep_gate()
    skip = os.environ.get("EXPERIMENT_PREPARATION_SKIP", "").lower() in ("1", "true", "yes")
    gate_target_node_id: str | None = None
    if gate is not None and not skip:
        result = gate(body.model_dump())
        if not result["allowed"]:
            is_provisioning = result.get("status") == "provisioning"
            raise HTTPException(
                status_code=412,
                detail={
                    "message": "Resources are being provisioned — poll provision_plan_ids and retry"
                    if is_provisioning
                    else "Experiment preparation is blocked",
                    "preparation_report": {
                        "status": result.get("status", "blocked"),
                        "actions": result.get("actions", []),
                        "provision_plan_ids": result.get("provision_plan_ids", []),
                    },
                },
            )

        gate_target_node_id = result.get("target_node_id")

    settings = get_settings()

    # 1. Create ExperimentGroup row
    group = ExperimentGroup(
        name=body.name,
        description=body.description,
        hypothesis_id=body.hypothesis_id,
        gap_id=body.gap_id,
        paper_ids=body.paper_ids,
        dataset_name=body.dataset_name,
        primary_metric=body.primary_metric,
        owner_id=user.id,
        status="PENDING",
    )
    db.add(group)
    db.flush()  # get group.id before creating jobs

    # 2. Expand → individual JobRecords (runs or sweep)
    if body.sweep is not None:
        expanded = expand_sweep(body.name, body.sweep)
    else:
        expanded = expand_runs(body)
    created_jobs: list[JobRecord] = []

    for run_info in expanded:
        assigned_node_id = run_info.get("target_node_id") or gate_target_node_id

        job = JobRecord(
            job_type=run_info["job_type"],
            # All training/val/detect jobs go through node scheduling (PENDING_ASSIGN)
            status="PENDING_ASSIGN",
            payload=run_info["payload"],
            project_name=run_info["project_name"],
            owner_id=user.id,
            assigned_node_id=assigned_node_id,
            dispatch_status="PENDING_ASSIGN",
            experiment_group_id=group.id,
            run_role=run_info["run_role"],
            seed=run_info.get("seed"),
            run_index=run_info["run_index"],
            execution_target=run_info.get("execution_target"),
        )
        db.add(job)
        created_jobs.append(job)

    # 4. Update group status
    group.status = _derive_group_status(created_jobs)
    db.commit()
    db.refresh(group)

    # Build response
    return _build_detail_response(group, created_jobs)


@router.get("", response_model=list[ExperimentGroupRead])
def list_experiment_groups(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """List experiment groups owned by the current user."""
    groups = (
        db.query(ExperimentGroup)
        .filter(ExperimentGroup.owner_id == user.id)
        .order_by(ExperimentGroup.created_at.desc())
        .limit(500)
        .all()
    )
    return [_build_group_read(g) for g in groups]


@router.get("/{group_id}", response_model=ExperimentGroupDetailRead)
def get_experiment_group(
    group_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Get experiment group detail including associated jobs."""
    group = db.get(ExperimentGroup, group_id)
    if group is None:
        raise HTTPException(404, "Experiment group not found")
    if group.owner_id != user.id and not user.is_superuser:
        raise HTTPException(404, "Experiment group not found")

    runs = (
        db.query(JobRecord)
        .filter(JobRecord.experiment_group_id == group.id)
        .order_by(JobRecord.run_index)
        .all()
    )
    return _build_detail_response(group, runs)


@router.get("/{group_id}/compare", response_model=ExperimentComparisonRead)
def compare_group(
    group_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Aggregate all runs and return a structured comparison result."""
    group = db.get(ExperimentGroup, group_id)
    if group is None:
        raise HTTPException(404, "Experiment group not found")
    if group.owner_id != user.id and not user.is_superuser:
        raise HTTPException(404, "Experiment group not found")

    result = compare_experiment_group(group_id, db)
    if result is None:
        raise HTTPException(404, "Experiment group not found")

    # Cache the comparison result on the group for later quick access
    group.comparison_cache = result
    db.commit()

    return ExperimentComparisonRead(**result)


@router.get("/{group_id}/export.csv")
def export_group_csv(
    group_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Export experiment group results as CSV."""
    group = db.get(ExperimentGroup, group_id)
    if group is None:
        raise HTTPException(404, "Experiment group not found")
    if group.owner_id != user.id and not user.is_superuser:
        raise HTTPException(404, "Experiment group not found")

    result = compare_experiment_group(group_id, db)
    if result is None:
        raise HTTPException(404, "Experiment group not found")

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["run_role", "seed", "run_index", "status", "job_id"])
    for run in result["runs"]:
        writer.writerow([
            run.get("run_role"),
            run.get("seed"),
            run.get("run_index"),
            run.get("status"),
            run.get("job_id"),
        ])

    # Aggregates section
    writer.writerow([])
    writer.writerow(["role", "mean", "std", "n", "metric"])
    for role, agg in result.get("aggregates", {}).items():
        writer.writerow([
            role,
            agg.get("mean"),
            agg.get("std"),
            agg.get("n"),
            agg.get("metric"),
        ])

    # Delta section
    delta = result.get("delta_vs_baseline")
    if delta:
        writer.writerow([])
        writer.writerow(["delta_role", "absolute", "relative_percent"])
        for role, d in delta.items():
            writer.writerow([role, d.get("absolute"), d.get("relative_percent")])

    csv_content = output.getvalue()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={group.name}_results.csv"},
    )


@router.get("/{group_id}/export.md")
def export_group_md(
    group_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    """Export experiment group results as a Markdown comparison table."""
    group = db.get(ExperimentGroup, group_id)
    if group is None:
        raise HTTPException(404, "Experiment group not found")
    if group.owner_id != user.id and not user.is_superuser:
        raise HTTPException(404, "Experiment group not found")

    result = compare_experiment_group(group_id, db)
    if result is None:
        raise HTTPException(404, "Experiment group not found")

    lines: list[str] = []
    lines.append(f"# {group.name}")
    lines.append("")
    if group.description:
        lines.append(f"{group.description}")
        lines.append("")
    lines.append(f"**Primary metric:** `{result.get('primary_metric')}`")
    lines.append(f"**Status:** {group.status}")
    lines.append("")

    # Runs table
    lines.append("## Runs")
    lines.append("")
    lines.append("| run_index | run_role | seed | status | job_id |")
    lines.append("|-----------|----------|------|--------|--------|")
    for run in result["runs"]:
        lines.append(
            f"| {run.get('run_index')} "
            f"| {run.get('run_role')} "
            f"| {run.get('seed') or ''} "
            f"| {run.get('status')} "
            f"| {run.get('job_id')} |"
        )
    lines.append("")

    # Aggregates table
    lines.append("## Aggregate Metrics")
    lines.append("")
    lines.append(f"| Role | Mean | Std | N |")
    lines.append(f"|------|------|-----|---|")
    for role, agg in result.get("aggregates", {}).items():
        mean_str = f"{agg['mean']:.4f}" if agg.get("mean") is not None else "N/A"
        std_str = f"{agg['std']:.4f}" if agg.get("std") is not None else "N/A"
        lines.append(f"| {role} | {mean_str} | {std_str} | {agg['n']} |")
    lines.append("")

    # Delta vs baseline
    delta = result.get("delta_vs_baseline")
    if delta:
        lines.append("## Delta vs Baseline")
        lines.append("")
        lines.append("| Role | Absolute | Relative (%) |")
        lines.append("|------|----------|-------------|")
        for role, d in delta.items():
            abs_str = f"{d['absolute']:+.4f}"
            rel_str = f"{d['relative_percent']:+.1f}%"
            lines.append(f"| {role} | {abs_str} | {rel_str} |")
        lines.append("")

    # Summary text
    summary = result.get("summary_text")
    if summary:
        lines.append("## Summary")
        lines.append("")
        lines.append("```")
        lines.append(summary)
        lines.append("```")
        lines.append("")

    # Reproducibility evidence
    lines.append("## Reproducibility Evidence")
    lines.append("")
    lines.append("| Job | Role | Model | Data | Command Snapshot |")
    lines.append("|-----|------|-------|------|-----------------|")
    for run in result["runs"]:
        job_id = run.get("job_id", "")
        role = run.get("run_role", "")
        # Try to read snapshot files from the job's artifacts
        job_db = db.get(JobRecord, job_id)
        if job_db:
            model_snap = f"`{job_db.model_snapshot_path or '-'}`" if job_db.model_snapshot_path else "-"
            data_snap = f"`{job_db.data_snapshot_path or '-'}`" if job_db.data_snapshot_path else "-"
            cmd_snap = f"`{job_db.resolved_command_path or '-'}`" if job_db.resolved_command_path else "-"
            lines.append(f"| {job_id[:12]}... | {role} | {model_snap} | {data_snap} | {cmd_snap} |")
        else:
            lines.append(f"| {job_id[:12]}... | {role} | - | - | - |")
    lines.append("")

    md_content = "\n".join(lines)
    return PlainTextResponse(
        content=md_content,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={group.name}_report.md"},
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _derive_group_status(jobs: list[JobRecord]) -> str:
    """Determine group-level status from its runs."""
    statuses = {j.status for j in jobs}
    # If any job was enqueued to Celery, its status might still be PENDING
    if "RUNNING" in statuses:
        return "RUNNING"
    if all(s == "SUCCESS" for s in statuses):
        return "SUCCESS"
    if all(s == "FAILED" for s in statuses):
        return "FAILED"
    if "FAILED" in statuses:
        return "PARTIAL"
    return "PENDING"


def _build_group_read(group: ExperimentGroup) -> ExperimentGroupRead:
    return ExperimentGroupRead(
        id=group.id,
        name=group.name,
        description=group.description,
        hypothesis_id=group.hypothesis_id,
        gap_id=group.gap_id,
        paper_ids=group.paper_ids,
        dataset_name=group.dataset_name,
        primary_metric=group.primary_metric,
        owner_id=group.owner_id,
        status=group.status,
        created_at=group.created_at,
        finished_at=group.finished_at,
        summary_path=group.summary_path,
        run_count=len(group.runs) if group.runs else 0,
    )


def _build_detail_response(
    group: ExperimentGroup,
    jobs: list[JobRecord],
) -> ExperimentGroupDetailRead:
    base = _build_group_read(group)
    return ExperimentGroupDetailRead(
        **base.model_dump(),
        runs=[JobRead.model_validate(j) for j in jobs],
    )
