from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.job_record import JobRecord
    from app.models.provision_plan import ProvisionPlan


class ComputeNode(Base):
    __tablename__ = "compute_nodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OFFLINE")
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    running_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Cache resource inventory (upserted each heartbeat from Linux agent)
    cache_root: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cache_free_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    resource_cache: Mapped[list | None] = mapped_column(JSON, nullable=True)

    jobs = relationship("JobRecord", back_populates="assigned_node")
    provision_plans = relationship("ProvisionPlan", back_populates="node")
