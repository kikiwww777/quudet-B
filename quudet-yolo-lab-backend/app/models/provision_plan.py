import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.resource_manifest import ResourceManifest
    from app.models.compute_node import ComputeNode


class ProvisionPlan(Base):
    """A plan to provision one resource on one node.

    Only one active (non-terminal) plan per (node_id, cache_key) is
    allowed — enforced by a partial unique index.
    """

    __tablename__ = "provision_plans"
    __table_args__ = (
        Index(
            "ix_provision_plans_active_unique",
            "node_id",
            "cache_key",
            unique=True,
            sqlite_where=text("state NOT IN ('READY', 'FAILED')"),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    node_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("compute_nodes.id"), nullable=False, index=True
    )
    manifest_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("resource_manifests.id"), nullable=False
    )
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)

    # Provision state machine
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="PENDING"
    )  # PENDING / DOWNLOADING / VERIFYING / READY / FAILED / MANUAL_REQUIRED

    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Verified delivery attributes (populated on READY)
    archive_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bytes_downloaded: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    local_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Download progress (for heartbeat / polling)
    download_progress: Mapped[int] = mapped_column(default=0)  # 0-100 percent

    # Error context on FAILED
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    validator_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Timing
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # --- relationships ---
    node: Mapped["ComputeNode"] = relationship(back_populates="provision_plans")
    manifest: Mapped["ResourceManifest"] = relationship(back_populates="provisions")
