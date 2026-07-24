"""Resource manifest management — AR-published immutable resource descriptions.

Routes are additive; the existing ``job-dataset`` ZIP route remains unchanged
during migration.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_superuser
from app.config import get_settings
from app.database import get_db
from app.models.compute_node import ComputeNode
from app.models.resource_manifest import ResourceManifest
from app.models.user import User
from app.schemas.provisioning import ResourceManifestCreate, ResourceManifestRead

router = APIRouter(prefix="/resources", tags=["resources"])


def _hash_node_token(token: str) -> str:
    settings = get_settings()
    raw = f"{settings.NODE_SHARED_TOKEN}:{token}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _require_node_by_params(
    db: Session,
    node_id: str,
    token: str,
) -> ComputeNode:
    """Authenticate a node via query-param token (used by Linux agent)."""
    node = db.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(404, "Node not found")
    if node.token_hash != _hash_node_token(token):
        raise HTTPException(401, "Invalid node token")
    return node


@router.post("/manifests", response_model=ResourceManifestRead, status_code=201)
def create_manifest(
    body: ResourceManifestCreate,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_superuser)],
):
    """Persist an AR-approved immutable resource manifest.

    The ``cache_key`` is computed from the manifest content and stored as
    ``manifest_content_hash``.  If a manifest with the same hash already
    exists, the existing row is returned (idempotent POST).
    """
    cache_key = ResourceManifest.compute_cache_key(body.model_dump())

    existing = (
        db.query(ResourceManifest)
        .filter(ResourceManifest.manifest_content_hash == cache_key)
        .first()
    )
    if existing is not None:
        return ResourceManifestRead.model_validate(existing)

    manifest = ResourceManifest(
        id=str(uuid.uuid4()),
        resource_id=body.resource_id,
        resource_type=body.resource_type,
        version=body.version,
        display_name=body.display_name,
        source=body.source.model_dump() if body.source else None,
        integrity=body.integrity.model_dump() if body.integrity else None,
        delivery=body.delivery.model_dump() if body.delivery else None,
        validation=body.validation.model_dump() if body.validation else None,
        manual_fallback=body.manual_fallback.model_dump() if body.manual_fallback else None,
        provenance=body.provenance.model_dump() if body.provenance else None,
        manifest_content_hash=cache_key,
    )
    # Override delivery.cache_key with the server-computed hash so that
    # any client-submitted value is ignored and consistency is guaranteed.
    if manifest.delivery is not None:
        manifest.delivery["cache_key"] = cache_key
    db.add(manifest)
    db.commit()
    db.refresh(manifest)
    return ResourceManifestRead.model_validate(manifest)


@router.get("/manifests", response_model=list[ResourceManifestRead])
def list_manifests(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(get_current_user)],
):
    """List all resource manifests, most recent first."""
    rows = (
        db.query(ResourceManifest)
        .order_by(ResourceManifest.created_at.desc())
        .limit(500)
        .all()
    )
    return [ResourceManifestRead.model_validate(r) for r in rows]


@router.get("/manifests/{manifest_id}", response_model=ResourceManifestRead)
def get_manifest(
    manifest_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Retrieve a single manifest by ID.

    Authenticated via JWT (web UI) or node token query params (agent).
    """
    m = db.get(ResourceManifest, manifest_id)
    if m is None:
        raise HTTPException(404, "Manifest not found")
    return ResourceManifestRead.model_validate(m)


@router.get("/manifests/{manifest_id}/for-node", response_model=ResourceManifestRead)
def get_manifest_for_node(
    manifest_id: str,
    node_id: Annotated[str, Query(min_length=1)],
    token: Annotated[str, Query(min_length=4)],
    db: Annotated[Session, Depends(get_db)],
):
    """Agent-facing manifest fetch — authenticated via node token query params.

    Linux nodes do not have JWT; they authenticate with their node token.
    Usage from agent::

        GET /api/v1/resources/manifests/{id}/for-node?node_id=...&token=...
    """
    _require_node_by_params(db, node_id, token)
    m = db.get(ResourceManifest, manifest_id)
    if m is None:
        raise HTTPException(404, "Manifest not found")
    return ResourceManifestRead.model_validate(m)


@router.post("/manifests/{manifest_id}/approve")
def approve_manifest(
    manifest_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(require_superuser)],
):
    """Explicit human approval for a manifest whose integrity requires review.

    Level B resources (source URL known, but integrity requires human approval)
    must be approved before they can be provisioned automatically.
    """
    m = db.get(ResourceManifest, manifest_id)
    if m is None:
        raise HTTPException(404, "Manifest not found")
    m.integrity_approved = True
    m.approved_by = user.email
    m.approved_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "manifest_id": manifest_id, "approved_by": user.email}
