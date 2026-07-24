from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

RunRoleLiteral = Literal["baseline", "variant", "ablation", "repeat", "sweep"]


class ExperimentRunCreate(BaseModel):
    role: RunRoleLiteral = "variant"
    seed: int | None = None
    run_index: int | None = None
    job_type: Literal["train", "val", "detect"] = "train"
    target_node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    execution_target: Literal["local", "remote", "auto"] | None = None
    required_gpu: bool = False


class SweepSpec(BaseModel):
    """Hyperparameter grid sweep definition.

    Example:
        SweepSpec(
            grid={"lr0": [0.001, 0.01], "batch": [16, 32]},
            seeds=[42, 43],
            job_type="train",
            base_payload={"model": "yolo11n.pt", "data": "visdrone.yaml", "epochs": "100"},
        )
    """
    grid: dict[str, list[Any]] = Field(..., min_length=1)
    seeds: list[int] = Field(default_factory=list)
    job_type: Literal["train", "val", "detect"] = "train"
    base_payload: dict[str, Any] = Field(default_factory=dict)


class ExperimentGroupCreate(BaseModel):
    name: str
    description: str | None = None
    hypothesis_id: str | None = None
    gap_id: str | None = None
    paper_ids: list[str] | None = None
    dataset_name: str | None = None
    primary_metric: str = "metrics/mAP50-95(B)"
    runs: list[ExperimentRunCreate] = Field(default_factory=list)
    sweep: SweepSpec | None = None

    @model_validator(mode="after")
    def check_runs_or_sweep(self):
        if not self.runs and self.sweep is None:
            raise ValueError("Either 'runs' or 'sweep' must be provided")
        if self.runs and self.sweep is not None:
            raise ValueError("Use either 'runs' or 'sweep', not both")
        return self


class ExperimentGroupRead(BaseModel):
    id: str
    name: str
    description: str | None
    hypothesis_id: str | None
    gap_id: str | None
    paper_ids: list | None
    dataset_name: str | None
    primary_metric: str | None
    owner_id: int
    status: str
    created_at: datetime
    finished_at: datetime | None
    summary_path: str | None
    run_count: int = 0

    model_config = {"from_attributes": True}


class ExperimentGroupDetailRead(ExperimentGroupRead):
    runs: list[Any] = Field(default_factory=list)


class RunCompareItem(BaseModel):
    job_id: str
    run_role: str | None
    seed: int | None
    run_index: int | None
    status: str
    metrics: dict[str, Any] | None


class ExperimentComparisonRead(BaseModel):
    group_id: str
    primary_metric: str | None
    primary_metric_resolved: str | None = None
    status: str | None = None
    runs: list[RunCompareItem]
    aggregates: dict[str, Any] = Field(default_factory=dict)
    delta_vs_baseline: dict[str, Any] | None = None
    best_run_id: str | None = None
    summary_text: str | None = None
