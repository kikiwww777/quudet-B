"""Add execution_target to jobs table.

Revision ID: 003_add_execution_target
Revises: 002_add_metrics_source_path
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_add_execution_target"
down_revision: Union[str, None] = "002_add_metrics_source_path"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("execution_target", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "execution_target")
