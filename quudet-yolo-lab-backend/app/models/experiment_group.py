import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.job_record import JobRecord


class ExperimentGroup(Base):
    __tablename__ = "experiment_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    hypothesis_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    gap_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    paper_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dataset_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    primary_metric: Mapped[str | None] = mapped_column(String(256), nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING"
    )  # PENDING / RUNNING / PARTIAL / SUCCESS / FAILED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    comparison_cache: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    runs: Mapped[list["JobRecord"]] = relationship(
        "JobRecord", back_populates="experiment_group", order_by="JobRecord.run_index"
    )
