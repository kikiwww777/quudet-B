from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ResourceTypeLiteral = Literal["dataset", "weight", "bundle"]
ProvisionStateLiteral = Literal[
    "PENDING", "DOWNLOADING", "VERIFYING", "READY", "FAILED", "MANUAL_REQUIRED"
]


# ── Resource Manifest ───────────────────────────────────────────────────────


class ManifestSource(BaseModel):
    kind: str = "official_direct"
    url: str | None = None
    official_page: str | None = None
    license: str | None = None


class ManifestIntegrity(BaseModel):
    archive_sha256: str | None = None
    expected_size_bytes: int = 0
    manifest_schema: str = "resource-manifest/v1"


class ManifestDelivery(BaseModel):
    archive_format: str = "zip"
    extract_subdir: str | None = None
    cache_key: str | None = Field(default=None, exclude=True)  # server-derived; input ignored
    target_relative_path: str | None = None
    allow_resume: bool = True
    preparer_kind: str = "yolo_ready"
    output_data_yaml_path: str | None = None
    preparer_options: dict[str, Any] = Field(default_factory=dict)


class ManifestValidation(BaseModel):
    kind: str | None = None
    yaml_relative_path: str | None = None
    required_paths: list[str] = Field(default_factory=list)


class ManifestManualFallback(BaseModel):
    allowed: bool = False
    instructions: str | None = None


class ManifestProvenance(BaseModel):
    discovery_id: str | None = None
    selected_by: str | None = None
    created_at: str | None = None


class ResourceManifestCreate(BaseModel):
    resource_id: str = Field(..., min_length=1, max_length=256)
    resource_type: ResourceTypeLiteral = "dataset"
    version: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=512)
    source: ManifestSource = Field(default_factory=ManifestSource)
    integrity: ManifestIntegrity = Field(default_factory=ManifestIntegrity)
    delivery: ManifestDelivery = Field(default_factory=ManifestDelivery)
    validation: ManifestValidation = Field(default_factory=ManifestValidation)
    manual_fallback: ManifestManualFallback = Field(default_factory=ManifestManualFallback)
    provenance: ManifestProvenance = Field(default_factory=ManifestProvenance)


class ResourceManifestRead(BaseModel):
    id: str
    resource_id: str
    resource_type: str
    version: str
    display_name: str
    status: str
    source: dict | None = None
    integrity: dict | None = None
    delivery: dict | None = None
    validation: dict | None = None
    manual_fallback: dict | None = None
    provenance: dict | None = None
    manifest_content_hash: str | None = None
    integrity_approved: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Node Resource Inventory ─────────────────────────────────────────────────


class CacheResourceEntry(BaseModel):
    resource_id: str
    cache_key: str
    status: str = "READY"
    verified_at: str | None = None
    local_uri: str | None = None


class NodeResourceInventoryUpdate(BaseModel):
    cache_root: str | None = None
    free_bytes: int | None = None
    resources: list[CacheResourceEntry] = Field(default_factory=list)


# ── Provision Plan ──────────────────────────────────────────────────────────


class ProvisionPlanCreate(BaseModel):
    """Request body for creating a provisioning plan."""
    node_id: str = Field(..., min_length=1, max_length=64)
    manifest_id: str = Field(..., min_length=1)
    cache_key: str | None = None  # ignored — server derives from manifest
    requested_by: str | None = None
    expires_at: datetime | None = None


class ProvisionPlanRead(BaseModel):
    id: str
    node_id: str
    manifest_id: str
    cache_key: str
    state: str
    requested_by: str | None = None
    expires_at: datetime | None = None
    archive_sha256: str | None = None
    bytes_downloaded: int | None = None
    local_uri: str | None = None
    download_progress: int = 0
    error_message: str | None = None
    validator_result: dict | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Provision Claim ──────────────────────────────────────────────────────────


class ProvisionClaimResponse(BaseModel):
    claimed: bool
    reason: str | None = None
    provision: dict[str, Any] | None = None


# ── Provision Events ────────────────────────────────────────────────────────


class ProvisionEventRequest(BaseModel):
    node_id: str = Field(..., min_length=1)
    token: str = Field(..., min_length=4)
    event_type: Literal["progress", "status", "receipt", "log"]
    provision_id: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


# ── Provision Receipt (terminal event payload) ──────────────────────────────


class ProvisionReceipt(BaseModel):
    provision_id: str
    state: ProvisionStateLiteral = "READY"
    cache_key: str | None = None
    archive_sha256: str | None = None
    bytes_downloaded: int = 0
    local_uri: str | None = None
    validator: dict[str, Any] = Field(default_factory=dict)
    completed_at: str | None = None
