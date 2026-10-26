"""Deterministic, offline classification and experimental event scoring.

The inputs are event snapshots, not ORM rows. These provisional weights are
injected and versioned; no database, model, clock, or network is consulted.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aipulse.news.merge import EventGroup
from aipulse.news.normalize import DeduplicatedNewsItem
from aipulse.news.persistence_contract import metadata_for_storage
from aipulse.news.types import ExternalId, Provider, Title


_CATEGORIES = ("paper", "model", "tool", "product", "industry", "discussion", "other")
_PATTERNS = {
    "paper": re.compile(r"\b(?:paper|preprint|research study)\b|论文|预印本", re.I),
    "model": re.compile(r"\b(?:llm|model|language model|foundation model)\b|大模型|模型", re.I),
    "tool": re.compile(r"\b(?:tool|toolkit|cli|sdk|framework|library)\b|工具|框架", re.I),
    "product": re.compile(r"\b(?:product|app|application|platform|launch)\b|产品|应用", re.I),
    "industry": re.compile(r"\b(?:funding|acquisition|regulation|policy|startup)\b|融资|收购|监管|政策", re.I),
    "discussion": re.compile(r"\b(?:ask hn|show hn|discussion|debate)\b|讨论", re.I),
}
_PROVIDERS = ("github", "arxiv", "hn")


class ScoreItem(BaseModel):
    """One original item in an event; publication may be absent in a snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    provider: Provider
    external_id: ExternalId
    title: Title
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)

    @field_validator("published_at", "fetched_at")
    @classmethod
    def aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Timestamp must include timezone")
            return value.astimezone(timezone.utc)
        return value

    @field_validator("metadata")
    @classmethod
    def bounded_json(cls, value: dict) -> dict:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ValueError("Metadata must be JSON serializable") from None
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("Metadata exceeds size limit")
        return value


class ScoreEvent(BaseModel):
    """One deterministic event identity with its original source snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    event_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_identity: str = Field(min_length=1)
    items: tuple[ScoreItem, ...] = Field(min_length=1)

    @classmethod
    def from_group(
        cls, group: EventGroup, items: Iterable[DeduplicatedNewsItem]
    ) -> "ScoreEvent":
        """Adapt the existing merge output without changing its identities."""

        if not isinstance(group, EventGroup) or not group.members:
            raise ValueError("Valid event membership required")
        members = {member.item_key for member in group.members}
        selected: dict[tuple[str, str], DeduplicatedNewsItem] = {}
        for item in items:
            if not isinstance(item, DeduplicatedNewsItem):
                raise ValueError("Valid event membership required")
            if item.item_key not in members or item.canonical_identity != group.canonical_identity:
                raise ValueError("Event membership does not match supplied items")
            current = selected.get(item.item_key)
            if current is None or _item_order(item) > _item_order(current):
                selected[item.item_key] = item
        if set(selected) != members or any(
            member.event_key != group.event_key for member in group.members
        ):
            raise ValueError("Event membership does not match supplied items")
        return cls(
            event_key=group.event_key,
            canonical_identity=group.canonical_identity,
            items=tuple(
                ScoreItem(
                    provider=item.provider, external_id=item.external_id,
                    title=item.candidate.title, published_at=item.published_at,
                    fetched_at=item.fetched_at,
                    metadata=metadata_for_storage(item.candidate),
                )
                for _, item in sorted(selected.items())
            ),
        )


def _item_order(item: DeduplicatedNewsItem) -> tuple:
    candidate_json = json.dumps(
        item.candidate.model_dump(mode="json"), sort_keys=True,
        ensure_ascii=False, separators=(",", ":"),
    )
    return item.fetched_at, item.content_hash, item.canonical_url, candidate_json


class ScoreConfig(BaseModel):
    """Injectable experimental point weights; product calibration is pending."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    base_points: int = Field(default=10, ge=0, le=100)
    github_points: int = Field(default=16, ge=0, le=100)
    arxiv_points: int = Field(default=14, ge=0, le=100)
    hn_points: int = Field(default=6, ge=0, le=100)
    independent_source_points: int = Field(default=8, ge=0, le=100)
    fresh_day_points: int = Field(default=12, ge=0, le=100)
    fresh_week_points: int = Field(default=6, ge=0, le=100)
    fresh_day_hours: int = Field(default=24, ge=1, le=168)
    fresh_week_hours: int = Field(default=168, ge=2, le=8760)
    hn_score_20_points: int = Field(default=5, ge=0, le=100)
    hn_score_100_points: int = Field(default=10, ge=0, le=100)
    hn_score_500_points: int = Field(default=15, ge=0, le=100)
    max_valid_hn_score: int = Field(default=100_000, ge=500, le=1_000_000)
    topic_points: int = Field(default=8, ge=0, le=100)

    @model_validator(mode="after")
    def ordered_recency_windows(self) -> "ScoreConfig":
        if self.fresh_week_hours <= self.fresh_day_hours:
            raise ValueError("Week freshness window must exceed day window")
        return self

    @property
    def rule_version(self) -> str:
        payload = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return "experimental-v1:" + sha256(payload.encode("utf-8")).hexdigest()[:16]


class ScoreSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    points: int = Field(ge=0)
    detail: str


class ScoreResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_key: str
    category: str
    importance_score: int = Field(ge=0, le=100)
    score_reason: str = Field(max_length=1000)
    rule_version: str = Field(max_length=100)
    signals: tuple[ScoreSignal, ...]
    unknown_signals: tuple[str, ...]


def _category(item: ScoreItem) -> str:
    if item.provider == "arxiv":
        return "paper"
    for category in _CATEGORIES[:-1]:
        if _PATTERNS[category].search(item.title):
            return category
    if item.provider == "github":
        return "tool"
    return "other"


def _unique_items(event: ScoreEvent) -> tuple[ScoreItem, ...]:
    selected: dict[tuple[str, str], ScoreItem] = {}
    for item in event.items:
        key = item.provider, item.external_id
        current = selected.get(key)
        order = lambda value: (
            value.fetched_at or datetime.min.replace(tzinfo=timezone.utc),
            json.dumps(value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False),
        )
        if current is None or order(item) > order(current):
            selected[key] = item
    return tuple(selected[key] for key in sorted(selected))


def score_event(
    event: ScoreEvent, *, as_of: datetime, config: ScoreConfig | None = None,
) -> ScoreResult:
    """Score one merged event once, without inventing absent source metrics."""

    if not isinstance(event, ScoreEvent) or not isinstance(as_of, datetime):
        raise ValueError("A valid event and evaluation time are required")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Evaluation time must include timezone")
    as_of = as_of.astimezone(timezone.utc)
    config = config if config is not None else ScoreConfig()
    if not isinstance(config, ScoreConfig):
        raise ValueError("A valid scoring configuration is required")

    items = _unique_items(event)
    category = min((_category(item) for item in items), key=_CATEGORIES.index)
    providers = tuple(sorted({item.provider for item in items}))
    weights = {
        "github": config.github_points, "arxiv": config.arxiv_points,
        "hn": config.hn_points,
    }
    signals = [ScoreSignal(name="base", points=config.base_points,
                           detail="Experimental baseline")]
    unknown = []
    known_providers = [provider for provider in providers if provider in _PROVIDERS]
    if known_providers:
        strongest = max(known_providers, key=lambda provider: (weights[provider], provider))
        signals.append(ScoreSignal(name="provider", points=weights[strongest],
                                   detail=f"Confirmed provider: {strongest}"))
    if len(providers) > 1:
        signals.append(ScoreSignal(
            name="independent_sources",
            points=min(2, len(providers) - 1) * config.independent_source_points,
            detail=f"Distinct providers: {', '.join(providers)}",
        ))

    known_dates = [item.published_at for item in items
                   if item.published_at is not None and item.published_at <= as_of]
    if known_dates:
        age = as_of - max(known_dates)
        if age <= timedelta(hours=config.fresh_day_hours):
            points, description = config.fresh_day_points, "Published within day window"
        elif age <= timedelta(hours=config.fresh_week_hours):
            points, description = config.fresh_week_points, "Published within week window"
        else:
            points, description = 0, "Known publication is older than week window"
        signals.append(ScoreSignal(name="recency", points=points, detail=description))
    else:
        unknown.append("published_at")

    hn_scores = [item.metadata.get("score") for item in items if item.provider == "hn"]
    if hn_scores:
        valid_scores = [value for value in hn_scores
                        if type(value) is int and 0 <= value <= config.max_valid_hn_score]
        if valid_scores:
            highest = max(valid_scores)
            points = (config.hn_score_500_points if highest >= 500 else
                      config.hn_score_100_points if highest >= 100 else
                      config.hn_score_20_points if highest >= 20 else 0)
            signals.append(ScoreSignal(name="community", points=points,
                                       detail=f"Validated HN score={highest}"))
        else:
            unknown.append("hn_score")
    elif "hn" in providers:
        unknown.append("hn_score")

    if any(_PATTERNS["model"].search(item.title) or
           re.search(r"\b(?:ai|machine learning|transformer)\b|人工智能", item.title, re.I)
           for item in items):
        signals.append(ScoreSignal(name="topic", points=config.topic_points,
                                   detail="Explicit AI topic in title"))

    total = min(100, sum(signal.points for signal in signals))
    reason = "; ".join(f"{signal.detail} (+{signal.points})" for signal in signals)
    if unknown:
        reason += "; unavailable: " + ", ".join(unknown)
    return ScoreResult(
        event_key=event.event_key, category=category, importance_score=total,
        score_reason=reason, rule_version=config.rule_version,
        signals=tuple(signals), unknown_signals=tuple(unknown),
    )
