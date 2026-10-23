"""Add nullable event scoring explanation and rule version.

Revision ID: 20261016_0002
Revises: 20261014_0001

The reverse operation removes stored explanations and versions permanently;
it is provided for Alembic completeness and must not run automatically.
"""

from alembic import op
import sqlalchemy as sa


revision = "20261016_0002"
down_revision = "20261014_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow old events to remain unscored; do not backfill invented values."""
    op.add_column("news_event", sa.Column("score_reason", sa.String(1000), nullable=True))
    op.add_column("news_event", sa.Column("rule_version", sa.String(100), nullable=True))


def downgrade() -> None:
    """Destructive reverse DDL, requiring separate review before any execution."""
    op.drop_column("news_event", "rule_version")
    op.drop_column("news_event", "score_reason")
