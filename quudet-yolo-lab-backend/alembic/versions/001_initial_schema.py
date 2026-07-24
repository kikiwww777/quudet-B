"""Initial schema — all models from the Phase 1 baseline.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-07-08

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)

    # --- compute_nodes ---
    op.create_table(
        "compute_nodes",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'OFFLINE'")),
        sa.Column("token_hash", sa.String(128), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("running_jobs", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- uploaded_datasets ---
    op.create_table(
        "uploaded_datasets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("stored_path", sa.String(1024), nullable=False),
        sa.Column("extracted_path", sa.String(1024), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ),
    )
    op.create_index(op.f("ix_uploaded_datasets_id"), "uploaded_datasets", ["id"], unique=False)

    # --- experiment_groups ---
    op.create_table(
        "experiment_groups",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("hypothesis_id", sa.String(256), nullable=True),
        sa.Column("gap_id", sa.String(256), nullable=True),
        sa.Column("paper_ids", sa.JSON(), nullable=True),
        sa.Column("dataset_name", sa.String(256), nullable=True),
        sa.Column("primary_metric", sa.String(256), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("summary_path", sa.String(1024), nullable=True),
        sa.Column("comparison_cache", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- jobs ---
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("project_name", sa.String(512), nullable=True),
        sa.Column("log_path", sa.String(1024), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # Dispatch / cluster fields
        sa.Column("assigned_node_id", sa.String(64), nullable=True),
        sa.Column("dispatch_status", sa.String(32), nullable=False, server_default=sa.text("'LOCAL'")),
        sa.Column("metrics_cache", sa.JSON(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        # Experiment-group fields
        sa.Column("experiment_group_id", sa.String(36), nullable=True),
        sa.Column("run_role", sa.String(32), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("run_index", sa.Integer(), nullable=True),
        sa.Column("spec_snapshot_path", sa.String(1024), nullable=True),
        sa.Column("resolved_command_path", sa.String(1024), nullable=True),
        sa.Column("model_snapshot_path", sa.String(1024), nullable=True),
        sa.Column("data_snapshot_path", sa.String(1024), nullable=True),
        sa.Column("code_snapshot_path", sa.String(1024), nullable=True),
        sa.Column("env_snapshot_path", sa.String(1024), nullable=True),
        sa.Column("artifacts_manifest_path", sa.String(1024), nullable=True),
        sa.Column("metrics_source_path", sa.String(1024), nullable=True),
        sa.Column("execution_target", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ),
        sa.ForeignKeyConstraint(["dataset_id"], ["uploaded_datasets.id"], ),
        sa.ForeignKeyConstraint(["assigned_node_id"], ["compute_nodes.id"], ),
        sa.ForeignKeyConstraint(["experiment_group_id"], ["experiment_groups.id"], ),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("experiment_groups")
    op.drop_table("uploaded_datasets")
    op.drop_table("compute_nodes")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
