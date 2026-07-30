"""add experiment group idempotency key

Revision ID: 006_add_experiment_group_idempotency
Revises: 005_add_job_recovery_attempts
"""

from alembic import op
import sqlalchemy as sa


revision = "006_group_idempotency"
down_revision = "005_add_job_recovery_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiment_groups", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_index("ix_experiment_groups_idempotency_key", "experiment_groups", ["idempotency_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_experiment_groups_idempotency_key", table_name="experiment_groups")
    op.drop_column("experiment_groups", "idempotency_key")
