"""Add metrics_source_path to jobs table.

Revision ID: 002_add_metrics_source_path
Revises: 001_initial_schema
Create Date: 2026-07-09

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_add_metrics_source_path"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("metrics_source_path", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "metrics_source_path")
