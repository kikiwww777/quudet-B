import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.provision_plan import ProvisionPlan


class ResourceManifest(Base):
    """Immutable manifest for one concrete resource version.

    AR emits one manifest per discovered resource. The ``cache_key`` is
    derived from the manifest content so that a changed manifest produces a
    different cache slot even when the ``resource_id`` / ``version`` pair is
    the same.
    """

    __tablename__ = "resource_manifests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)  # dataset / weight / bundle
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")  # active / deprecated

    # Source info
    source: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Integrity metadata
    integrity: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Delivery configuration
    delivery: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Validation rules
    validation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Manual fallback instructions
    manual_fallback: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Provenance
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Cached content hash used as ``cache_key`` in the delivery block.
    # Computed at insert time from the serialised manifest fields.
    manifest_content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    # Human approval tracking
    integrity_approved: Mapped[bool] = mapped_column(default=False)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- relationships ---
    provisions: Mapped[list["ProvisionPlan"]] = relationship(
        "ProvisionPlan", back_populates="manifest"
    )

    @staticmethod
    def compute_cache_key(manifest_data: dict) -> str:
        """Deterministic content hash from the manifest's semantic fields.

        Excludes metadata fields (id, status, created_at, updated_at, etc.)
        so that re-registering the same logical manifest yields the same key.
        """
        core = {
            "resource_id": manifest_data.get("resource_id"),
            "resource_type": manifest_data.get("resource_type"),
            "version": manifest_data.get("version"),
            "source": manifest_data.get("source"),
            "integrity": manifest_data.get("integrity"),
            "delivery": manifest_data.get("delivery"),
            "validation": manifest_data.get("validation"),
            "manual_fallback": manifest_data.get("manual_fallback"),
        }
        raw = json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"
