"""Provisioning plan lifecycle — resource preparation on Linux nodes.

Nodes claim provisioning plans before claiming training jobs.  A plan
transitions through::

    PENDING → DOWNLOADING → VERIFYING → READY
                                       → FAILED
                                       → MANUAL_REQUIRED

The API returns a non-terminal preparation state while provisioning is
running; AR / Preparation polls that state and retries experiment submission
only after every required resource is READY.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.compute_node import ComputeNode
from app.models.provision_plan import ProvisionPlan
from app.models.resource_manifest import ResourceManifest
from app.models.user import User
from app.schemas.provisioning import (
    ProvisionClaimResponse,
    ProvisionEventRequest,
    ProvisionPlanCreate,
    ProvisionPlanRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/provisioning", tags=["provisioning"])
PROVISION_RECLAIM_SECONDS = 120


# ── Helpers ──────────────────────────────────────────────────────────────────


def _hash_node_token(token: str) -> str:
    settings = get_settings()
    raw = f"{settings.NODE_SHARED_TOKEN}:{token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_node(db: Session, node_id: str, token: str) -> ComputeNode:
    """Fetch and authenticate a node by ID + token hash."""
    node = db.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(404, "Node not found")
    if node.token_hash != _hash_node_token(token):
        raise HTTPException(401, "Invalid node token")
    node.last_seen_at = datetime.utcnow()
    node.status = "ONLINE"
    return node


def _get_provision_or_404(db: Session, provision_id: str) -> ProvisionPlan:
    p = db.get(ProvisionPlan, provision_id)
    if p is None:
        raise HTTPException(404, "Provision plan not found")
    return p


# ── Create ────────────────────────────────────────────────────────────────────


@router.post("", response_model=ProvisionPlanRead, status_code=201)
def create_provision_plan(
    body: ProvisionPlanCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """Request resource preparation on a selected node.

    Called by Preparation after AR produces a manifest and a compatible node
    is identified.  The plan sits in ``PENDING`` until the Linux agent claims
    it.
    """
    # Validate node exists
    node = db.get(ComputeNode, body.node_id)
    if node is None:
        raise HTTPException(404, f"Node '{body.node_id}' not found")

    # Validate manifest exists
    manifest = db.get(ResourceManifest, body.manifest_id)
    if manifest is None:
        raise HTTPException(404, f"Manifest '{body.manifest_id}' not found")

    # Server-derived cache_key — reject client-submitted value for safety
    cache_key = manifest.manifest_content_hash or ""
    if not cache_key:
        raise HTTPException(400, "Manifest has no content hash; cannot create provision plan")

    # Dedup: ensure only one active plan per (node, cache_key)
    existing_pending = (
        db.query(ProvisionPlan)
        .filter(
            ProvisionPlan.node_id == body.node_id,
            ProvisionPlan.cache_key == cache_key,
            ProvisionPlan.state.in_(["PENDING", "DOWNLOADING", "VERIFYING"]),
        )
        .first()
    )
    if existing_pending is not None:
        return ProvisionPlanRead.model_validate(existing_pending)

    # Check for existing READY provision (cache hit)
    existing_ready = (
        db.query(ProvisionPlan)
        .filter(
            ProvisionPlan.node_id == body.node_id,
            ProvisionPlan.cache_key == cache_key,
            ProvisionPlan.state == "READY",
        )
        .first()
    )
    if existing_ready is not None:
        return ProvisionPlanRead.model_validate(existing_ready)

    plan = ProvisionPlan(
        node_id=body.node_id,
        manifest_id=body.manifest_id,
        cache_key=cache_key,
        state="PENDING",
        requested_by=body.requested_by,
        expires_at=body.expires_at,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    logger.info("ProvisionPlan %s created for node=%s resource=%s", plan.id, body.node_id, manifest.resource_id)
    return ProvisionPlanRead.model_validate(plan)


# ── Poll ──────────────────────────────────────────────────────────────────────


@router.get("/{provision_id}", response_model=ProvisionPlanRead)
def get_provision_plan(
    provision_id: str,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """Poll provisioning state — called by AR / Preparation before submission."""
    return ProvisionPlanRead.model_validate(_get_provision_or_404(db, provision_id))


@router.get("", response_model=list[ProvisionPlanRead])
def list_provision_plans(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
    node_id: str | None = None,
    state: str | None = None,
):
    """List provisioning plans, optionally filtered by node and/or state."""
    q = db.query(ProvisionPlan)
    if node_id:
        q = q.filter(ProvisionPlan.node_id == node_id)
    if state:
        q = q.filter(ProvisionPlan.state == state.upper())
    q = q.order_by(ProvisionPlan.created_at.desc()).limit(200)
    return [ProvisionPlanRead.model_validate(p) for p in q.all()]


# ── Claim-next (Linux agent) ──────────────────────────────────────────────────


@router.post("/claim-next", response_model=ProvisionClaimResponse)
def claim_next_provision(
    body: dict,
    db: Annotated[Session, Depends(get_db)],
):
    """Atomically claim one pending provision plan for a node.

    The Linux agent calls this before ``/dispatch/claim-next`` for training
    jobs.  Only one plan is claimed per call; the agent should loop.
    """
    node_id = str(body.get("node_id") or "").strip()
    token = str(body.get("token") or "").strip()

    if not node_id:
        raise HTTPException(422, "node_id is required")
    if not token:
        raise HTTPException(422, "token is required")

    node = _require_node(db, node_id, token)

    # Reclaim a plan after an agent restart. The provisioner stages downloads
    # by provision ID, therefore reclaiming the same plan resumes its partial
    # archive instead of starting a competing transfer.
    stale_before = datetime.utcnow() - timedelta(seconds=PROVISION_RECLAIM_SECONDS)
    db.query(ProvisionPlan).filter(
        ProvisionPlan.node_id == node_id,
        ProvisionPlan.state.in_(["DOWNLOADING", "VERIFYING"]),
        ProvisionPlan.last_heartbeat_at.isnot(None),
        ProvisionPlan.last_heartbeat_at < stale_before,
    ).update({
        ProvisionPlan.state: "PENDING",
        ProvisionPlan.error_message: "Interrupted provision reclaimed by restarted agent; resuming cached partial download.",
    }, synchronize_session=False)
    db.flush()

    # Check for existing PENDING plans assigned to this node
    plan = (
        db.query(ProvisionPlan)
        .filter(
            ProvisionPlan.node_id == node_id,
            ProvisionPlan.state == "PENDING",
        )
        .order_by(ProvisionPlan.created_at.asc())
        .first()
    )

    if plan is None:
        db.commit()
        return ProvisionClaimResponse(claimed=False, reason="no-pending-provision")

    now = datetime.utcnow()
    plan.state = "DOWNLOADING"
    plan.claimed_at = now
    plan.last_heartbeat_at = now
    db.commit()

    return ProvisionClaimResponse(
        claimed=True,
        provision={
            "id": plan.id,
            "manifest_id": plan.manifest_id,
            "cache_key": plan.cache_key,
            "node_id": plan.node_id,
            "state": plan.state,
        },
    )


# ── Events (Linux agent) ──────────────────────────────────────────────────────


@router.post("/{provision_id}/events")
def provision_event(
    provision_id: str,
    body: ProvisionEventRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """Report progress, logs, failure, or receipt for a provision plan."""
    node = _require_node(db, body.node_id, body.token)
    plan = _get_provision_or_404(db, provision_id)

    if plan.node_id != body.node_id:
        raise HTTPException(403, "Provision plan is assigned to another node")

    plan.last_heartbeat_at = datetime.utcnow()

    if body.event_type == "progress":
        pct = max(0, min(100, int(body.payload.get("progress") or 0)))
        plan.download_progress = pct
        if "bytes_downloaded" in body.payload:
            plan.bytes_downloaded = int(body.payload["bytes_downloaded"])

    elif body.event_type == "status":
        new_state = str(body.payload.get("state") or "").upper()
        if new_state == "DOWNLOADING":
            plan.state = "DOWNLOADING"
        elif new_state == "VERIFYING":
            plan.state = "VERIFYING"
        elif new_state == "FAILED":
            plan.state = "FAILED"
            plan.error_message = str(body.payload.get("error_message") or "")
            plan.completed_at = datetime.utcnow()
        elif new_state == "MANUAL_REQUIRED":
            plan.state = "MANUAL_REQUIRED"
            plan.error_message = str(body.payload.get("error_message") or "")

    elif body.event_type == "receipt":
        # Terminal READY state — full receipt
        plan.state = "READY"
        plan.archive_sha256 = str(body.payload.get("archive_sha256") or "")
        plan.bytes_downloaded = int(body.payload.get("bytes_downloaded") or 0)
        plan.local_uri = str(body.payload.get("local_uri") or "")
        plan.validator_result = body.payload.get("validator")
        plan.download_progress = 100
        plan.completed_at = datetime.utcnow()

        # Update node cache inventory
        _upsert_node_cache_entry(node, plan)

    elif body.event_type == "log":
        # Log text — stored on the plan for debugging
        text = str(body.payload.get("text") or "")
        if text:
            current = plan.error_message or ""
            plan.error_message = (current + text)[-4096:]  # keep last ~4K chars

    db.commit()
    return {"ok": True}


def _upsert_node_cache_entry(node: ComputeNode, plan: ProvisionPlan) -> None:
    """Add or update an entry in the node's resource cache inventory."""
    manifest = None
    try:
        manifest = plan.manifest
    except Exception:
        return

    if manifest is None:
        return

    entry = {
        "resource_id": manifest.resource_id,
        "cache_key": plan.cache_key,
        "status": "READY",
        "verified_at": datetime.utcnow().isoformat(),
        "local_uri": plan.local_uri or "",
    }

    node_cache: list = node.resource_cache or []
    replaced = False
    for i, existing in enumerate(node_cache):
        if isinstance(existing, dict) and existing.get("cache_key") == plan.cache_key:
            node_cache[i] = entry
            replaced = True
            break
    if not replaced:
        node_cache.append(entry)

    node.resource_cache = node_cache
