"""Add resource manifest, provision plan tables, and node cache columns.

Revision ID: 004_add_resource_provisioning
Revises: 003_add_execution_target
Create Date: 2026-07-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004_add_resource_provisioning"
down_revision: Union[str, None] = "003_add_execution_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    # ``Base.metadata.create_all`` is used by the development API bootstrap.
    # It can create these new tables without updating Alembic's revision marker,
    # so every DDL action here must tolerate pre-existing schema objects.
    if "resource_manifests" not in table_names:
        op.create_table(
            "resource_manifests",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("resource_id", sa.String(256), nullable=False),
            sa.Column("resource_type", sa.String(32), nullable=False),
            sa.Column("version", sa.String(128), nullable=False),
            sa.Column("display_name", sa.String(512), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
            sa.Column("source", sa.JSON(), nullable=True),
            sa.Column("integrity", sa.JSON(), nullable=True),
            sa.Column("delivery", sa.JSON(), nullable=True),
            sa.Column("validation", sa.JSON(), nullable=True),
            sa.Column("manual_fallback", sa.JSON(), nullable=True),
            sa.Column("provenance", sa.JSON(), nullable=True),
            sa.Column("manifest_content_hash", sa.String(128), nullable=True, unique=True),
            sa.Column("integrity_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("approved_by", sa.String(128), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )

    if "provision_plans" not in table_names:
        op.create_table(
            "provision_plans",
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("node_id", sa.String(64), nullable=False),
            sa.Column("manifest_id", sa.String(36), nullable=False),
            sa.Column("cache_key", sa.String(128), nullable=False),
            sa.Column("state", sa.String(32), nullable=False, server_default=sa.text("'PENDING'")),
            sa.Column("requested_by", sa.String(128), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("archive_sha256", sa.String(128), nullable=True),
            sa.Column("bytes_downloaded", sa.BigInteger(), nullable=True),
            sa.Column("local_uri", sa.String(1024), nullable=True),
            sa.Column("download_progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("validator_result", sa.JSON(), nullable=True),
            sa.Column("claimed_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(["node_id"], ["compute_nodes.id"]),
            sa.ForeignKeyConstraint(["manifest_id"], ["resource_manifests.id"]),
        )

    # Partial unique index: only one active (non-terminal) plan per (node, cache_key).
    # Prevents duplicate downloads from concurrent requests.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_resource_manifests_resource_id "
        "ON resource_manifests (resource_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_provision_plans_node_id "
        "ON provision_plans (node_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_provision_plans_active_unique "
        "ON provision_plans (node_id, cache_key) "
        "WHERE state NOT IN ('READY', 'FAILED')"
    )

    # --- Add cache columns to compute_nodes ---
    compute_node_columns = {
        column["name"] for column in sa.inspect(bind).get_columns("compute_nodes")
    }
    if "cache_root" not in compute_node_columns:
        op.add_column("compute_nodes", sa.Column("cache_root", sa.String(512), nullable=True))
    if "cache_free_bytes" not in compute_node_columns:
        op.add_column("compute_nodes", sa.Column("cache_free_bytes", sa.BigInteger(), nullable=True))
    if "resource_cache" not in compute_node_columns:
        op.add_column("compute_nodes", sa.Column("resource_cache", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_provision_plans_active_unique")
    op.drop_column("compute_nodes", "resource_cache")
    op.drop_column("compute_nodes", "cache_free_bytes")
    op.drop_column("compute_nodes", "cache_root")
    op.drop_index(op.f("ix_provision_plans_node_id"), table_name="provision_plans")
    op.drop_table("provision_plans")
    op.drop_index(op.f("ix_resource_manifests_resource_id"), table_name="resource_manifests")
    op.drop_table("resource_manifests")
