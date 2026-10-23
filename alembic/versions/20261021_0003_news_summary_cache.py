"""Add nullable structured AI summary cache to news_event.

Revision ID: 20261021_0003
Revises: 20261016_0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261021_0003"
down_revision = "20261016_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("news_event", sa.Column("summary_json", mysql.JSON(), nullable=True))
    op.add_column("news_event", sa.Column("summary_model", sa.String(100), nullable=True))
    op.add_column("news_event", sa.Column("summary_prompt_version", sa.String(100), nullable=True))
    op.add_column("news_event", sa.Column("summary_input_fingerprint", mysql.CHAR(64), nullable=True))
    op.add_column("news_event", sa.Column("summary_generated_at", mysql.DATETIME(fsp=6), nullable=True))


def downgrade() -> None:
    """Destructive reverse DDL; never run automatically."""
    op.drop_column("news_event", "summary_generated_at")
    op.drop_column("news_event", "summary_input_fingerprint")
    op.drop_column("news_event", "summary_prompt_version")
    op.drop_column("news_event", "summary_model")
    op.drop_column("news_event", "summary_json")
