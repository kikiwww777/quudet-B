"""Reconciliation service — background state repair for jobs and experiment groups.

Purpose:
  Prevent long-running experiments from "silently hanging" when a Celery worker
  crashes, the API restarts mid-experiment, or a job's status becomes orphaned.

Mechanism:
  A periodic Celery task (or manual API call) scans the database for anomalous
  states and transitions them to a correct terminal / non-terminal status.

Design principles:
  - Conservative: prefer to leave a job RUNNING rather than kill it prematurely.
  - Idempotent: running reconciliation multiple times is safe.
  - Self-limiting: each run bounds the number of jobs it touches.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.experiment_group import ExperimentGroup
from app.models.job_record import JobRecord
from app.models.provision_plan import ProvisionPlan
from app.services.experiment_compare import update_experiment_group_status

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A job in RUNNING status whose last_heartbeat_at is older than this
# (or has no heartbeat and started longer ago) is considered stuck.
STUCK_HEARTBEAT_TIMEOUT_SECONDS: int = 300  # 5 minutes

# Jobs that never received a heartbeat at all are given this grace period
# from their started_at timestamp before being marked stuck.
STUCK_NO_HEARTBEAT_GRACE_SECONDS: int = 600  # 10 minutes

# Jobs in PENDING_ASSIGN status older than this are marked FAILED
# (the remote agent probably never picked them up).
STALE_PENDING_ASSIGN_TIMEOUT_SECONDS: int = 3600  # 1 hour

# Provisions that stay PENDING / DOWNLOADING / VERIFYING longer than this
# are marked FAILED (agent likely crashed or lost the network).
STALE_PROVISION_TIMEOUT_SECONDS: int = 7200  # 2 hours

# Provisions in DOWNLOADING / VERIFYING with no recent heartbeat.
PROVISION_HEARTBEAT_TIMEOUT_SECONDS: int = 600  # 10 minutes

# Maximum number of jobs / groups to touch in a single reconciliation run.
_BATCH_LIMIT = 50


# ---------------------------------------------------------------------------
# Reconciliation steps
# ---------------------------------------------------------------------------


def reconcile_stuck_jobs(db: Session, *, batch: int = _BATCH_LIMIT) -> dict[str, Any]:
    """Mark stuck RUNNING jobs as FAILED.

    A job is considered stuck when:
      1. It has a ``last_heartbeat_at`` older than ``STUCK_HEARTBEAT_TIMEOUT_SECONDS``.
      2. It has no ``last_heartbeat_at`` but its ``started_at`` is older than
         ``STUCK_NO_HEARTBEAT_GRACE_SECONDS``.
      3. It has no ``started_at`` either but was ``created_at`` more than
         ``STUCK_NO_HEARTBEAT_GRACE_SECONDS`` ago (unlikely, but defensive).

    Returns a summary dict.
    """
    now = datetime.now(timezone.utc)
    touched: list[str] = []
    impacted_groups: set[str] = set()

    # --- Condition 1: heartbeat too old ---
    stuck = (
        db.query(JobRecord)
        .filter(
            JobRecord.status == "RUNNING",
            JobRecord.last_heartbeat_at.isnot(None),
            JobRecord.last_heartbeat_at < now.replace(tzinfo=None),  # DB stores naive UTC
        )
        .all()
    )
    # Apply the timeout filter in Python because timezone-aware ↔ naive comparison
    # is fragile across different SQLAlchemy / PG drivers.
    stuck = [
        j for j in stuck
        if j.last_heartbeat_at is not None
        and (now - j.last_heartbeat_at.replace(tzinfo=timezone.utc)).total_seconds()
        > STUCK_HEARTBEAT_TIMEOUT_SECONDS
    ]

    for job in stuck[:batch]:
        job.status = "FAILED"
        job.finished_at = now
        job.error_message = (
            job.error_message or ""
        ) + f" | [reconciliation] no heartbeat for >{STUCK_HEARTBEAT_TIMEOUT_SECONDS}s"
        touched.append(job.id)
        if job.experiment_group_id:
            impacted_groups.add(job.experiment_group_id)
        batch -= 1

    # --- Condition 2: no heartbeat, started too long ago ---
    if batch > 0:
        no_hb = (
            db.query(JobRecord)
            .filter(
                JobRecord.status == "RUNNING",
                JobRecord.last_heartbeat_at.is_(None),
                JobRecord.started_at.isnot(None),
            )
            .all()
        )
        for job in no_hb:
            if batch <= 0:
                break
            if job.started_at is None:
                continue
            age = (now - job.started_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age > STUCK_NO_HEARTBEAT_GRACE_SECONDS:
                job.status = "FAILED"
                job.finished_at = now
                job.error_message = (
                    job.error_message or ""
                ) + f" | [reconciliation] no heartbeat for >{STUCK_NO_HEARTBEAT_GRACE_SECONDS}s after start"
                touched.append(job.id)
                if job.experiment_group_id:
                    impacted_groups.add(job.experiment_group_id)
                batch -= 1

    # --- Condition 3: stale PENDING_ASSIGN (remote agent never claimed) ---
    if batch > 0:
        stale_pending = (
            db.query(JobRecord)
            .filter(
                JobRecord.status == "PENDING_ASSIGN",
                JobRecord.created_at.isnot(None),
            )
            .all()
        )
        for job in stale_pending:
            if batch <= 0:
                break
            if job.created_at is None:
                continue
            age = (now - job.created_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age > STALE_PENDING_ASSIGN_TIMEOUT_SECONDS:
                job.status = "FAILED"
                job.finished_at = now
                job.error_message = (
                    job.error_message or ""
                ) + f" | [reconciliation] stale PENDING_ASSIGN for >{STALE_PENDING_ASSIGN_TIMEOUT_SECONDS}s"
                touched.append(job.id)
                if job.experiment_group_id:
                    impacted_groups.add(job.experiment_group_id)
                batch -= 1

    db.commit()

    # Cascade to impacted groups
    for gid in impacted_groups:
        update_experiment_group_status(db, gid)
    db.commit()

    return {
        "step": "stuck_jobs",
        "jobs_marked_failed": len(touched),
        "job_ids": touched,
        "groups_updated": len(impacted_groups),
    }


def reconcile_orphaned_groups(db: Session) -> dict[str, Any]:
    """Find RUNNING groups whose every job is already terminal — recalculate status.

    This handles edge cases where a job status was updated directly in the DB
    (or by a crashed worker) without triggering ``update_experiment_group_status``.
    """
    now = datetime.now(timezone.utc)
    fixed_groups: list[str] = []

    running_groups = (
        db.query(ExperimentGroup)
        .filter(ExperimentGroup.status == "RUNNING")
        .all()
    )

    for group in running_groups:
        runs = (
            db.query(JobRecord)
            .filter(JobRecord.experiment_group_id == group.id)
            .all()
        )
        if not runs:
            continue
        statuses = {r.status for r in runs}
        # If all job statuses are terminal (SUCCESS / FAILED / CANCELLED)
        # but the group is still RUNNING, recalculate.
        if statuses.issubset({"SUCCESS", "FAILED", "CANCELLED"}):
            old_status = group.status
            update_experiment_group_status(db, group.id)
            db.refresh(group)
            if group.status != old_status:
                fixed_groups.append(group.id)

    db.commit()

    return {
        "step": "orphaned_groups",
        "groups_recalculated": len(fixed_groups),
        "group_ids": fixed_groups,
    }


def reconcile_stuck_provisions(db: Session) -> dict[str, Any]:
    """Mark stale / timed-out provisioning plans as FAILED.

    Covers:
      1. Plans stuck in DOWNLOADING/VERIFYING with stale heartbeat.
      2. Plans in PENDING that never got claimed within STALE_PROVISION_TIMEOUT.
    """
    now = datetime.now(timezone.utc)
    touched: list[str] = []

    # --- Condition 1: DOWNLOADING / VERIFYING with stale heartbeat ---
    active = (
        db.query(ProvisionPlan)
        .filter(ProvisionPlan.state.in_(["DOWNLOADING", "VERIFYING"]))
        .all()
    )
    for plan in active:
        hb = plan.last_heartbeat_at
        if hb is not None:
            age = (now - hb.replace(tzinfo=timezone.utc)).total_seconds()
        elif plan.claimed_at is not None:
            age = (now - plan.claimed_at.replace(tzinfo=timezone.utc)).total_seconds()
        else:
            age = (now - plan.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age > PROVISION_HEARTBEAT_TIMEOUT_SECONDS:
            plan.state = "FAILED"
            plan.error_message = (
                plan.error_message or ""
            ) + f" | [reconciliation] stale heartbeat for >{PROVISION_HEARTBEAT_TIMEOUT_SECONDS}s"
            plan.completed_at = now
            touched.append(plan.id)

    # --- Condition 2: PENDING that was never claimed ---
    stale_pending = (
        db.query(ProvisionPlan)
        .filter(
            ProvisionPlan.state == "PENDING",
            ProvisionPlan.created_at.isnot(None),
        )
        .all()
    )
    for plan in stale_pending:
        age = (now - plan.created_at.replace(tzinfo=timezone.utc)).total_seconds()
        if age > STALE_PROVISION_TIMEOUT_SECONDS:
            plan.state = "FAILED"
            plan.error_message = (
                plan.error_message or ""
            ) + f" | [reconciliation] stale PENDING for >{STALE_PROVISION_TIMEOUT_SECONDS}s"
            plan.completed_at = now
            touched.append(plan.id)

    db.commit()
    return {
        "step": "stuck_provisions",
        "provisions_marked_failed": len(touched),
        "provision_ids": touched,
    }


def reconcile_all(db: Session | None = None, *, batch: int = _BATCH_LIMIT) -> list[dict[str, Any]]:
    """Run all reconciliation steps in order.

    Accepts an optional ``db`` session; creates one if not provided (useful
    when called from a Celery task outside the request lifecycle).
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    results: list[dict[str, Any]] = []
    try:
        results.append(reconcile_stuck_jobs(db, batch=batch))
        results.append(reconcile_orphaned_groups(db))
        results.append(reconcile_stuck_provisions(db))
    finally:
        if close_db:
            db.close()

    return results


# ---------------------------------------------------------------------------
# Celery task (for periodic scheduling)
# ---------------------------------------------------------------------------


@celery_app.task(name="quudet.reconcile")
def reconcile_task() -> list[dict[str, Any]]:
    """Celery task that runs all reconciliation steps.

    Schedule this periodically via ``celery beat``:
        celery -A app.celery_app beat --loglevel info
    """
    return reconcile_all()
