"""Create the seven reviewed V1 base tables.

Revision ID: 20261014_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20261014_0001"
down_revision = None
branch_labels = None
depends_on = None


_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    """Create V1 tables; execution requires a separate migration approval."""
    op.create_table(
        "news_source",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(191), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(191), nullable=False),
        sa.Column("url", sa.String(2048)),
        sa.Column("config_json", mysql.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_news_source_provider_external_id"
        ),
        **_OPTIONS,
    )
    op.create_table(
        "news_event",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("event_key", mysql.CHAR(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("importance_score", mysql.TINYINT(unsigned=True)),
        sa.Column("category", sa.String(32)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_news_event_event_key"),
        **_OPTIONS,
    )
    op.create_index(
        "ix_news_event_status_created_at", "news_event", ["status", "created_at"]
    )
    op.create_table(
        "daily_brief",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("brief_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("content", mysql.LONGTEXT()),
        sa.Column("generated_at", mysql.DATETIME(fsp=6)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.UniqueConstraint("brief_date", "timezone", name="uq_daily_brief_date_timezone"),
        sa.CheckConstraint("CHAR_LENGTH(TRIM(timezone)) > 0", name="ck_daily_brief_timezone_nonempty"),
        **_OPTIONS,
    )
    op.create_table(
        "research_task",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column("task_id", sa.String(36), nullable=False),
        sa.Column("event_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("news_event.id")),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", mysql.DATETIME(fsp=6)),
        sa.Column("finished_at", mysql.DATETIME(fsp=6)),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("task_id", name="uq_research_task_task_id"),
        **_OPTIONS,
    )
    op.create_index(
        "ix_research_task_status_created_at", "research_task", ["status", "created_at"]
    )
    op.create_table(
        "news_item",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "source_id", mysql.BIGINT(unsigned=True),
            sa.ForeignKey("news_source.id"), nullable=False,
        ),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(191), nullable=False),
        sa.Column("event_id", mysql.BIGINT(unsigned=True), sa.ForeignKey("news_event.id")),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("canonical_url_hash", mysql.CHAR(64), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("metadata_json", mysql.JSON(), nullable=False),
        sa.Column("published_at", mysql.DATETIME(fsp=6)),
        sa.Column("fetched_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("content_hash", mysql.CHAR(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "provider", "external_id", name="uq_news_item_provider_external_id"
        ),
        **_OPTIONS,
    )
    op.create_index("ix_news_item_published_at", "news_item", ["published_at"])
    op.create_index("ix_news_item_source_id", "news_item", ["source_id"])
    op.create_index("ix_news_item_canonical_url_hash", "news_item", ["canonical_url_hash"])
    op.create_table(
        "research_evidence",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "research_task_id", mysql.BIGINT(unsigned=True),
            sa.ForeignKey("research_task.id"), nullable=False,
        ),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("citation", sa.String(64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint(
            "research_task_id", "citation", name="uq_research_evidence_task_citation"
        ),
        **_OPTIONS,
    )
    op.create_index(
        "ix_research_evidence_task", "research_evidence", ["research_task_id"]
    )
    op.create_table(
        "agent_run_log",
        sa.Column("id", mysql.BIGINT(unsigned=True), primary_key=True),
        sa.Column(
            "task_id", sa.String(36), sa.ForeignKey("research_task.task_id"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", mysql.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.UniqueConstraint("task_id", "seq", name="uq_agent_run_log_task_seq"),
        **_OPTIONS,
    )
    op.create_index(
        "ix_agent_run_log_task_created_at", "agent_run_log", ["task_id", "created_at"]
    )


def downgrade() -> None:
    """Describe reverse DDL for review; never run automatically."""
    op.drop_index("ix_agent_run_log_task_created_at", table_name="agent_run_log")
    op.drop_table("agent_run_log")
    op.drop_index("ix_research_evidence_task", table_name="research_evidence")
    op.drop_table("research_evidence")
    op.drop_index("ix_news_item_source_id", table_name="news_item")
    op.drop_index("ix_news_item_published_at", table_name="news_item")
    op.drop_index("ix_news_item_canonical_url_hash", table_name="news_item")
    op.drop_table("news_item")
    op.drop_index("ix_research_task_status_created_at", table_name="research_task")
    op.drop_table("research_task")
    op.drop_table("daily_brief")
    op.drop_index("ix_news_event_status_created_at", table_name="news_event")
    op.drop_table("news_event")
    op.drop_table("news_source")
