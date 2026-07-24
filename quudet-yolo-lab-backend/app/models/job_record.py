import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.experiment_group import ExperimentGroup


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)  # train, val, detect
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("uploaded_datasets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    assigned_node_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("compute_nodes.id"), nullable=True)
    dispatch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="LOCAL")
    metrics_cache: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # --- 实验组 / 科研改造新增字段 ---
    experiment_group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("experiment_groups.id"), nullable=True
    )
    run_role: Mapped[str | None] = mapped_column(String(32), nullable=True)  # baseline / variant / ablation / repeat
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spec_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    resolved_command_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    model_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    data_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    code_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    env_snapshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifacts_manifest_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # --- 结果文件定位 ---
    metrics_source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # 显式记录此 run 实际使用的 results.csv 路径，消除路径猜测不确定性。

    # --- 调度字段 ---
    execution_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # local / remote / auto — 任务应被哪种节点领取（None = auto）

    owner = relationship("User", back_populates="jobs")
    assigned_node = relationship("ComputeNode", back_populates="jobs")
    experiment_group: Mapped["ExperimentGroup | None"] = relationship(
        "ExperimentGroup", back_populates="runs"
    )
