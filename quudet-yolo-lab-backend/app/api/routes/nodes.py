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
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models.compute_node import ComputeNode
from app.models.job_record import JobRecord
from app.models.user import User
from app.schemas.node import (
    NodeControlCommandAck,
    NodeControlCommandRequest,
    NodeHeartbeatRequest,
    NodeRead,
    NodeRegisterRequest,
)

router = APIRouter(prefix="/nodes", tags=["nodes"])
MAX_NODE_RECOVERY_ATTEMPTS = 2
CONTROL_COMMAND_TTL_SECONDS = 120
CONTROL_COMMAND_ACTIONS = {"RECONNECT", "RESTART"}


def _control_commands(node: ComputeNode) -> list[dict]:
    return list((node.capabilities or {}).get("control_commands") or [])


def _store_control_commands(node: ComputeNode, commands: list[dict]) -> None:
    capabilities = dict(node.capabilities or {})
    capabilities["control_commands"] = commands[-20:]
    node.capabilities = capabilities


def _create_control_command(node: ComputeNode, action: str, requester: str) -> dict:
    if action not in CONTROL_COMMAND_ACTIONS:
        raise ValueError(f"Unsupported control action: {action}")
    now = datetime.utcnow()
    command = {
        "id": str(uuid4()),
        "action": action,
        "requested_by": requester,
        "requested_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=CONTROL_COMMAND_TTL_SECONDS)).isoformat(),
        "status": "PENDING",
    }
    commands = _control_commands(node)
    commands.append(command)
    _store_control_commands(node, commands)
    return command


def _next_control_command(node: ComputeNode) -> dict | None:
    now = datetime.utcnow()
    commands = _control_commands(node)
    changed = False
    for command in commands:
        if command.get("status") != "PENDING":
            continue
        try:
            expired = datetime.fromisoformat(str(command["expires_at"])) <= now
        except (KeyError, ValueError):
            expired = True
        if expired:
            command["status"] = "EXPIRED"
            changed = True
            continue
        if changed:
            _store_control_commands(node, commands)
        return command
    if changed:
        _store_control_commands(node, commands)
    return None


def _acknowledge_control_command(node: ComputeNode, command_id: str, result: str, error: str | None = None) -> bool:
    commands = _control_commands(node)
    for command in commands:
        if command.get("id") != command_id or command.get("status") != "PENDING":
            continue
        command["status"] = "ERROR" if error else "ACKNOWLEDGED"
        command["acknowledged_at"] = datetime.utcnow().isoformat()
        command["result"] = result
        if error:
            command["error"] = error
        _store_control_commands(node, commands)
        return True
    return False

def _merge_reported_running_jobs(current: int, reported: int) -> int:
    """Keep server-reserved slots when another agent reports itself idle."""
    return max(0, current, reported)


def _merge_node_capabilities(
    current: dict | None,
    reported: dict | None,
    *,
    preserve_active_runtime: bool = False,
) -> dict:
    """Preserve active telemetry only while the server still owns a job slot."""
    merged = dict(current or {})
    merged.update(reported or {})
    current_runtime = (current or {}).get("agent_runtime") or {}
    reported_runtime = (reported or {}).get("agent_runtime") or {}
    if (
        preserve_active_runtime
        and current_runtime.get("active_job_id")
        and not reported_runtime.get("active_job_id")
    ):
        merged["agent_runtime"] = current_runtime
    return merged

def _mark_node_offline(node: ComputeNode) -> None:
    """Mark a stale node offline and release its server-reserved slots."""
    node.status = "OFFLINE"
    node.running_jobs = 0


def _recover_lost_job(job: JobRecord, node_id: str) -> bool:
    """Return an expired remote job to scheduling until its recovery budget ends."""
    attempts = int(getattr(job, "recovery_attempts", 0) or 0)
    if attempts >= MAX_NODE_RECOVERY_ATTEMPTS:
        job.status = "FAILED"
        job.dispatch_status = "FAILED_REMOTE"
        job.finished_at = datetime.utcnow()
        job.error_message = (job.error_message or "") + f" | node {node_id} recovery budget exhausted"
        return False
    job.recovery_attempts = attempts + 1
    job.status = "PENDING_ASSIGN"
    job.dispatch_status = "RECOVERY_PENDING"
    job.assigned_node_id = None
    job.started_at = None
    job.last_heartbeat_at = None
    job.error_message = (job.error_message or "") + f" | node {node_id} lost; recovery attempt {job.recovery_attempts}"
    return True


def _reconcile_expired_work(db: Session) -> dict[str, list[str]]:
    """Requeue only work whose assigned node has already crossed its lease timeout."""
    now = datetime.utcnow()
    timeout = timedelta(seconds=max(5, get_settings().NODE_HEARTBEAT_TIMEOUT_SECONDS))
    recovered: list[str] = []
    exhausted: list[str] = []
    nodes = db.query(ComputeNode).all()
    for node in nodes:
        if not node.last_seen_at or now - node.last_seen_at <= timeout:
            continue
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
            (recovered if _recover_lost_job(job, node.id) else exhausted).append(str(job.id))
    return {"recovered_job_ids": recovered, "exhausted_job_ids": exhausted}


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
        node.capabilities = _merge_node_capabilities(
            node.capabilities,
            body.capabilities,
            preserve_active_runtime=node.running_jobs > 0,
        )
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
        node.capabilities = _merge_node_capabilities(
            node.capabilities,
            body.capabilities,
            preserve_active_runtime=node.running_jobs > 0,
        )
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


@router.post("/{node_id}/commands")
def create_node_command(
    node_id: str,
    body: NodeControlCommandRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
):
    node = db.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(404, "Node not found")
    command = _create_control_command(node, body.action, requester=user.email)
    db.commit()
    return command


@router.get("/{node_id}/commands/next")
def get_next_node_command(
    node_id: str,
    token: str,
    db: Annotated[Session, Depends(get_db)],
):
    node = _get_node_or_404(db, node_id, token)
    command = _next_control_command(node)
    db.commit()
    return {"command": command}


@router.post("/{node_id}/commands/ack")
def acknowledge_node_command(
    node_id: str,
    body: NodeControlCommandAck,
    db: Annotated[Session, Depends(get_db)],
):
    node = _get_node_or_404(db, node_id, body.token)
    if not _acknowledge_control_command(node, body.command_id, body.result, body.error):
        raise HTTPException(409, "Command is not pending")
    db.commit()
    return {"ok": True}


@router.post("/reconcile-expired-work")
def reconcile_expired_work(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    result = _reconcile_expired_work(db)
    db.commit()
    return result


@router.get("", response_model=list[NodeRead])
def list_nodes(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    rows = db.query(ComputeNode).order_by(ComputeNode.updated_at.desc()).all()
    _reconcile_expired_work(db)
    db.commit()
    return [NodeRead.model_validate(x) for x in rows]
