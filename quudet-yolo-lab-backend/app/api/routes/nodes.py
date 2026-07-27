"""Node management 鈥?unified scheduling for local and remote execution nodes.

All execution environments (local Windows, local Linux, remote GPU servers)
register as ``ComputeNode`` instances and claim jobs via ``claim-next``.

Authentication: every node uses a token (32-char hex).  Local nodes
auto-generate theirs via ``agent.runner._generate_node_token()``; remote
nodes receive it from deployment config.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.compute_node import ComputeNode
from app.models.job_record import JobRecord
from app.models.user import User
from app.schemas.node import NodeHeartbeatRequest, NodeRead, NodeRegisterRequest

router = APIRouter(prefix="/nodes", tags=["nodes"])

def _merge_reported_running_jobs(current: int, reported: int) -> int:
    """Keep server-reserved slots when another agent reports itself idle."""
    return max(0, current, reported)

def _mark_node_offline(node: ComputeNode) -> None:
    """Mark a stale node offline and release its server-reserved slots."""
    node.status = "OFFLINE"
    node.running_jobs = 0


def _hash_node_token(token: str) -> str:
    settings = get_settings()
    raw = f"{settings.NODE_SHARED_TOKEN}:{token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_node_or_404(db: Session, node_id: str, token: str) -> ComputeNode:
    """Fetch and authenticate a node by ID + token.

    All nodes (local and remote) must authenticate with their token.
    The token hash is compared against the stored hash.
    """
    node = db.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(404, "Node not found")
    if node.token_hash != _hash_node_token(token):
        raise HTTPException(401, "Invalid node token")
    return node


@router.post("/register")
def register_node(
    body: NodeRegisterRequest,
    db: Annotated[Session, Depends(get_db)],
):
    node = db.get(ComputeNode, body.node_id)
    token_hash = _hash_node_token(body.token)
    now = datetime.utcnow()

    if node is None:
        node = ComputeNode(
            id=body.node_id,
            display_name=body.display_name,
            base_url=body.base_url,
            status="ONLINE",
            token_hash=token_hash,
            capabilities=body.capabilities,
            max_concurrent_jobs=max(1, body.max_concurrent_jobs),
            running_jobs=0,
            last_seen_at=now,
        )
        db.add(node)
    else:
        node.display_name = body.display_name
        node.base_url = body.base_url
        node.token_hash = token_hash
        node.capabilities = body.capabilities
        node.max_concurrent_jobs = max(1, body.max_concurrent_jobs)
        node.status = "ONLINE"
        node.last_seen_at = now
    db.commit()
    return {"ok": True, "node_id": body.node_id}


@router.post("/{node_id}/heartbeat")
def node_heartbeat(
    node_id: str,
    body: NodeHeartbeatRequest,
    db: Annotated[Session, Depends(get_db)],
):
    node = _get_node_or_404(db, node_id, body.token)
    node.running_jobs = _merge_reported_running_jobs(node.running_jobs, body.running_jobs)
    if body.capabilities:
        node.capabilities = body.capabilities
    # Store resource cache inventory from Linux nodes
    if body.cache_root is not None:
        node.cache_root = body.cache_root
    if body.cache_free_bytes is not None:
        node.cache_free_bytes = body.cache_free_bytes
    if body.resource_cache is not None:
        node.resource_cache = [r.model_dump() for r in body.resource_cache]
    node.status = "ONLINE"
    node.last_seen_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.get("", response_model=list[NodeRead])
def list_nodes(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    now = datetime.utcnow()
    timeout = timedelta(seconds=max(5, get_settings().NODE_HEARTBEAT_TIMEOUT_SECONDS))
    rows = db.query(ComputeNode).order_by(ComputeNode.updated_at.desc()).all()
    for node in rows:
        if node.last_seen_at and now - node.last_seen_at > timeout and node.status != "OFFLINE":
            _mark_node_offline(node)
            stale_jobs = (
                db.query(JobRecord)
                .filter(
                    JobRecord.assigned_node_id == node.id,
                    JobRecord.dispatch_status == "RUNNING_REMOTE",
                    JobRecord.status == "RUNNING",
                )
                .all()
            )
            for job in stale_jobs:
                job.status = "FAILED"
                job.dispatch_status = "FAILED_REMOTE"
                job.finished_at = now
                job.error_message = (
                    job.error_message
                    or f"Node {node.id} heartbeat timeout (> {timeout.total_seconds():.0f}s), job marked failed."
                )
    db.commit()
    return [NodeRead.model_validate(x) for x in rows]
