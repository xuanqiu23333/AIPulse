"""Seven V1 entities; importing them defines metadata but performs no DDL."""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from aipulse.models.base import Base, utc_now


MYSQL_TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class NewsSource(Base):
    """One configured AI information source."""

    __tablename__ = "news_source"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_news_source_provider_external_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(191), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(191), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048))
    config_json: Mapped[dict] = mapped_column(mysql.JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), default=utc_now, onupdate=utc_now, nullable=False
    )

    items: Mapped[list["NewsItem"]] = relationship(back_populates="source")


class NewsEvent(Base):
    """One deduplicated news event that can have multiple source items."""

    __tablename__ = "news_event"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_news_event_event_key"),
        Index("ix_news_event_status_created_at", "status", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    event_key: Mapped[str] = mapped_column(mysql.CHAR(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    # Keep existing event reads usable until the separately approved 0003 upgrade.
    summary_json: Mapped[dict | None] = mapped_column(mysql.JSON, deferred=True)
    summary_model: Mapped[str | None] = mapped_column(String(100), deferred=True)
    summary_prompt_version: Mapped[str | None] = mapped_column(String(100), deferred=True)
    summary_input_fingerprint: Mapped[str | None] = mapped_column(mysql.CHAR(64), deferred=True)
    summary_generated_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=6), deferred=True)
    importance_score: Mapped[int | None] = mapped_column(mysql.TINYINT(unsigned=True))
    category: Mapped[str | None] = mapped_column(String(32))
    score_reason: Mapped[str | None] = mapped_column(String(1000))
    rule_version: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), default=utc_now, onupdate=utc_now, nullable=False
    )

    items: Mapped[list["NewsItem"]] = relationship(back_populates="event")
    research_tasks: Mapped[list["ResearchTask"]] = relationship(back_populates="event")


class NewsItem(Base):
    """An original item preserved independently of event aggregation."""

    __tablename__ = "news_item"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_news_item_provider_external_id"),
        Index("ix_news_item_published_at", "published_at"),
        Index("ix_news_item_source_id", "source_id"),
        Index("ix_news_item_canonical_url_hash", "canonical_url_hash"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    source_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), ForeignKey("news_source.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(191), nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), ForeignKey("news_event.id")
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url_hash: Mapped[str] = mapped_column(mysql.CHAR(64), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    # Callers explicitly provide a bounded, vetted mapping; no JSON server default.
    metadata_json: Mapped[dict] = mapped_column(mysql.JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=6))
    # Observation time from NewsCandidate, stored as naive UTC for MySQL DATETIME.
    fetched_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), nullable=False)
    content_hash: Mapped[str] = mapped_column(mysql.CHAR(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), default=utc_now, onupdate=utc_now, nullable=False
    )

    source: Mapped[NewsSource] = relationship(back_populates="items")
    event: Mapped[NewsEvent | None] = relationship(back_populates="items")


class DailyBrief(Base):
    """One brief per calendar date and IANA timezone."""

    __tablename__ = "daily_brief"
    __table_args__ = (
        UniqueConstraint("brief_date", "timezone", name="uq_daily_brief_date_timezone"),
        CheckConstraint("CHAR_LENGTH(TRIM(timezone)) > 0", name="ck_daily_brief_timezone_nonempty"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    brief_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str | None] = mapped_column(mysql.LONGTEXT)
    generated_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=6))
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)

    @validates("timezone")
    def validate_timezone(self, _key: str, value: str) -> str:
        """Reject empty, non-IANA, and untrimmed timezone names at ORM assignment."""
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 64:
            raise ValueError("A valid IANA timezone is required")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("A valid IANA timezone is required") from None
        return value


class ResearchTask(Base):
    """A requested research run, optionally linked to a selected news event."""

    __tablename__ = "research_task"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_research_task_task_id"),
        Index("ix_research_task_status_created_at", "status", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(36), nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        mysql.BIGINT(unsigned=True), ForeignKey("news_event.id")
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict | None] = mapped_column(mysql.JSON, deferred=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=6))
    finished_at: Mapped[datetime | None] = mapped_column(mysql.DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        mysql.DATETIME(fsp=6), default=utc_now, onupdate=utc_now, nullable=False
    )

    event: Mapped[NewsEvent | None] = relationship(back_populates="research_tasks")
    evidence: Mapped[list["ResearchEvidence"]] = relationship(back_populates="task")
    logs: Mapped[list["AgentRunLog"]] = relationship(back_populates="task")


class ResearchEvidence(Base):
    """A source and citation attached to one research task."""

    __tablename__ = "research_evidence"
    __table_args__ = (
        UniqueConstraint(
            "research_task_id", "citation", name="uq_research_evidence_task_citation"
        ),
        Index("ix_research_evidence_task", "research_task_id"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    research_task_id: Mapped[int] = mapped_column(
        mysql.BIGINT(unsigned=True), ForeignKey("research_task.id"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column(mysql.JSON, deferred=True)
    citation: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), default=utc_now, nullable=False)

    task: Mapped[ResearchTask] = relationship(back_populates="evidence")


class AgentRunLog(Base):
    """A sanitized node event for later progress replay."""

    __tablename__ = "agent_run_log"
    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_agent_run_log_task_seq"),
        Index("ix_agent_run_log_task_created_at", "task_id", "created_at"),
        MYSQL_TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(mysql.BIGINT(unsigned=True), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("research_task.task_id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(mysql.JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(mysql.DATETIME(fsp=6), default=utc_now, nullable=False)

    task: Mapped[ResearchTask] = relationship(back_populates="logs")
