"""Explicit JSON response contracts for public read-only news endpoints."""

import re
from datetime import date, datetime, timezone

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aipulse.llm.gateway import TokenUsage


class _PublicSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")


class NewsSourceResponse(_PublicSchema):
    provider: str
    external_id: str
    title: str
    original_url: str
    source_name: str
    published_at: datetime | None
    fetched_at: datetime

    @field_validator("published_at", "fetched_at", mode="before")
    @classmethod
    def mysql_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class NewsEventResponse(_PublicSchema):
    event_id: int
    event_key: str
    title: str
    category: str | None
    importance_score: int | None = Field(ge=0, le=100)
    score_reason: str | None
    rule_version: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    providers: tuple[str, ...]
    item_count: int = Field(ge=0)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def mysql_utc(cls, value: datetime) -> datetime:
        converted = _utc(value)
        if converted is None:
            raise ValueError("Event timestamp is required")
        return converted


class NewsEventPage(_PublicSchema):
    page: int
    page_size: int
    total: int
    items: tuple[NewsEventResponse, ...]


class NewsEventDetail(NewsEventResponse):
    summary: str | None
    sources: tuple[NewsSourceResponse, ...]


class NewsSummaryResponse(BaseModel):
    """Validated AI summary; no model-generated source URLs are accepted."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: int = Field(gt=0)
    title: str
    summary_zh: str
    key_points: list[str]
    limitations: str
    sources: tuple[str, ...]
    model: str
    token_usage: TokenUsage | None
    latency_ms: int = Field(ge=0)
    cached: bool


class DailyBriefRequest(BaseModel):
    """The caller chooses a local calendar date, never a prompt or model."""

    model_config = ConfigDict(extra="forbid", strict=True)
    brief_date: date = Field(strict=False)

    @field_validator("brief_date", mode="before")
    @classmethod
    def iso_calendar_date(cls, value: object) -> object:
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("Date must use YYYY-MM-DD")
        return value


class DailyBriefHighlight(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: int = Field(gt=0)
    title: str
    category: str | None
    importance_score: int = Field(ge=0, le=100)
    summary_zh: str
    sources: tuple[str, ...]


class DailyBriefResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    brief_date: date
    timezone: Literal["Asia/Shanghai"]
    status: Literal["complete", "empty"]
    overview: str | None = None
    highlights: tuple[DailyBriefHighlight, ...] = ()
    model_and_products: tuple[str, ...] = ()
    tools_and_frameworks: tuple[str, ...] = ()
    research: tuple[str, ...] = ()
    trends: tuple[str, ...] = ()
    limitations: str | None = None
    model: str | None = None
    token_usage: TokenUsage | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    generated_at: datetime | None = None
    cached: bool
    rules_current: bool = True


CollectionProvider = Literal["github", "arxiv", "hn"]
CollectionStatusResponse = Literal["success", "partial", "failed"]


class ManualCollectionRequest(BaseModel):
    """Accept only a provider subset and a small per-source item budget."""

    model_config = ConfigDict(extra="forbid", strict=True)

    providers: list[CollectionProvider] = Field(min_length=1, max_length=3)
    limit_per_source: int = Field(default=5, ge=1, le=5, strict=True)

    @model_validator(mode="after")
    def unique_providers(self) -> "ManualCollectionRequest":
        if len(set(self.providers)) != len(self.providers):
            raise ValueError("Providers must be unique")
        return self


class ManualCollectionSourceResponse(BaseModel):
    provider: CollectionProvider
    status: CollectionStatusResponse
    fetched_count: int = Field(ge=0)
    processed_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    error_code: str | None = None


class ManualScoreFailureResponse(BaseModel):
    event_id: int = Field(gt=0)
    error_code: str


class ManualCollectionResponse(BaseModel):
    status: CollectionStatusResponse
    sources: tuple[ManualCollectionSourceResponse, ...]
    processed_count: int = Field(ge=0)
    events_scored: int = Field(ge=0)
    scoring_failed: int = Field(ge=0)
    scoring_errors: tuple[ManualScoreFailureResponse, ...]


class ScheduledCollectionRun(BaseModel):
    """Latest in-memory scheduler attempt, including overlap skips."""

    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "success", "partial", "failed", "skipped"]
    sources: tuple[ManualCollectionSourceResponse, ...] = ()
    processed_count: int = Field(default=0, ge=0)
    failure_codes: tuple[str, ...] = ()


class ScheduledCollectionStatus(BaseModel):
    enabled: bool
    active: bool
    running: bool
    last_run: ScheduledCollectionRun | None


def _utc(value: datetime | None) -> datetime | None:
    """Interpret MySQL's timezone-free DATETIME columns as application UTC."""

    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("Invalid stored timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
