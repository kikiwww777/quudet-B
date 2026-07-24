from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobTypeLiteral = Literal["train", "val", "detect"]


class JobCreate(BaseModel):
    job_type: JobTypeLiteral
    project_name: str | None = None
    dataset_id: int | None = None
    target_node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    execution_target: Literal["local", "remote", "auto"] | None = None
    required_gpu: bool = False


class JobListItem(BaseModel):
    id: str
    job_type: str
    status: str
    project_name: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: int = 0
    assigned_node_id: str | None = None
    dispatch_status: str = "LOCAL"
    experiment_group_id: str | None = None
    run_role: str | None = None
    seed: int | None = None

    model_config = {"from_attributes": True}


class JobRead(BaseModel):
    id: str
    job_type: str
    status: str
    project_name: str | None
    payload: dict[str, Any] | None
    log_path: str | None
    result_summary: str | None
    error_message: str | None
    dataset_id: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: int = 0
    assigned_node_id: str | None = None
    dispatch_status: str = "LOCAL"
    last_heartbeat_at: datetime | None
    experiment_group_id: str | None = None
    run_role: str | None = None
    seed: int | None = None
    run_index: int | None = None
    execution_target: str | None = None
    metrics_cache: dict[str, Any] | None = None
    spec_snapshot_path: str | None = None
    resolved_command_path: str | None = None
    model_snapshot_path: str | None = None
    data_snapshot_path: str | None = None
    code_snapshot_path: str | None = None
    env_snapshot_path: str | None = None
    artifacts_manifest_path: str | None = None
    metrics_source_path: str | None = None

    model_config = {"from_attributes": True}
