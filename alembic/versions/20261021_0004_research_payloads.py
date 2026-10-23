"""Add nullable research result and evidence metadata payloads.

Revision ID: 20261021_0004
Revises: 20261021_0003
Execution requires a separately authorized backup and protected migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261021_0004"
down_revision = "20261021_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_task", sa.Column("result_json", mysql.JSON(), nullable=True))
    op.add_column("research_evidence", sa.Column("metadata_json", mysql.JSON(), nullable=True))


def downgrade() -> None:
    """Destructive reverse DDL; never execute automatically."""
    op.drop_column("research_evidence", "metadata_json")
    op.drop_column("research_task", "result_json")
