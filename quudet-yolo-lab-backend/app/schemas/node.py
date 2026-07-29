from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NodeRegisterRequest(BaseModel):
    node_id: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=4, max_length=256)
    base_url: str | None = None
    max_concurrent_jobs: int = 1
    capabilities: dict[str, Any] = Field(default_factory=dict)


class CacheResourceEntry(BaseModel):
    resource_id: str
    cache_key: str
    status: str = "READY"
    verified_at: str | None = None
    local_uri: str | None = None


class NodeHeartbeatRequest(BaseModel):
    token: str = Field(min_length=4, max_length=256)
    running_jobs: int = 0
    capabilities: dict[str, Any] = Field(default_factory=dict)
    # Linux node resource cache inventory
    cache_root: str | None = None
    cache_free_bytes: int | None = None
    resource_cache: list[CacheResourceEntry] | None = None


class NodeControlCommandRequest(BaseModel):
    action: Literal["RECONNECT", "RESTART"]


class NodeControlCommandAck(BaseModel):
    token: str = Field(min_length=4, max_length=256)
    command_id: str = Field(min_length=8, max_length=64)
    result: str = Field(min_length=1, max_length=256)
    error: str | None = Field(default=None, max_length=512)


class NodeRead(BaseModel):
    id: str
    display_name: str
    base_url: str | None
    status: str
    capabilities: dict[str, Any] | None
    max_concurrent_jobs: int
    running_jobs: int
    last_seen_at: datetime | None
    updated_at: datetime | None
    cache_root: str | None = None
    cache_free_bytes: int | None = None
    resource_cache: list | None = None

    model_config = {"from_attributes": True}


class DispatchClaimResponse(BaseModel):
    claimed: bool
    reason: str | None = None
    job: dict[str, Any] | None = None


class DispatchEventRequest(BaseModel):
    node_id: str
    token: str
    event_type: Literal["log", "progress", "metrics", "status", "summary"]
    job_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
