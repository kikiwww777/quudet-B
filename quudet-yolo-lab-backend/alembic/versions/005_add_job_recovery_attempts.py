"""add job recovery attempts

Revision ID: 005_add_job_recovery_attempts
Revises: 004_add_resource_provisioning
"""

from alembic import op
import sqlalchemy as sa

revision = "005_add_job_recovery_attempts"
down_revision = "004_add_resource_provisioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("recovery_attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("jobs", "recovery_attempts")
