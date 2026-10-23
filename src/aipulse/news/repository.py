"""MySQL news persistence with caller-owned AsyncSession transactions.

This module constructs SQL but never creates an Engine, commits, or rolls
back. The caller must keep related source/event/item calls inside one
``async with session.begin()`` and let exceptions trigger its rollback.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from aipulse.models.entities import NewsEvent, NewsItem, NewsSource
from aipulse.news.normalize import DeduplicatedNewsItem
from aipulse.news.persistence_contract import (
    canonical_url_hash,
    metadata_for_storage,
    to_mysql_utc_datetime,
)
from aipulse.news.score import ScoreEvent, ScoreItem, ScoreResult, _CATEGORIES


class SourceIdentityConflict(ValueError):
    """The resolved source row does not match the requested configuration identity."""


class ItemIdentityConflict(ValueError):
    """The resolved row does not have the requested original identity."""


class EventLinkConflict(ValueError):
    """The original item is already linked to a different event or is stale."""


class EventScoreNotFound(LookupError):
    """The requested news event does not exist."""


class EventScoreIdentityConflict(ValueError):
    """The score was computed for a different event identity."""


def _stored_utc(value: datetime | None) -> datetime | None:
    """Interpret a MySQL DATETIME(6) value using the app's naive-UTC contract."""

    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError("Stored event timestamp must be naive UTC")
    return value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class SourceConfiguration:
    """Fields needed to create a news_source, separate from a news item ID."""

    provider: str
    external_id: str
    source_type: str
    name: str
    url: str | None = None
    config_json: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class EventListRecord:
    """Publicly displayable event fields, independent of an ORM Session."""

    event_id: int
    event_key: str
    title: str
    category: str | None
    importance_score: int | None
    score_reason: str | None
    rule_version: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    providers: tuple[str, ...]
    item_count: int


@dataclass(frozen=True)
class OriginalNewsRecord:
    """A bounded original-source projection for event details."""

    provider: str
    external_id: str
    title: str
    original_url: str
    source_name: str
    published_at: datetime | None
    fetched_at: datetime


@dataclass(frozen=True)
class EventDetailRecord(EventListRecord):
    summary: str | None
    sources: tuple[OriginalNewsRecord, ...]


@dataclass(frozen=True)
class SummaryItemRecord:
    """Stored original evidence; content is a collector excerpt, not a fetched page."""

    provider: str
    title: str
    excerpt: str | None
    original_url: str
    source_name: str
    published_at: datetime | None
    external_id: str = ""


@dataclass(frozen=True)
class SummaryCacheRecord:
    payload: dict
    model: str
    prompt_version: str
    input_fingerprint: str


@dataclass(frozen=True)
class SummaryInputRecord:
    event_id: int
    title: str
    items: tuple[SummaryItemRecord, ...]
    truncated: bool
    cache: SummaryCacheRecord | None = None


_VISIBLE_EVENT_STATUSES = ("active", "needs_review")
_SUMMARY_ITEM_LIMIT = 8


def _event_filters(*, category: str | None, min_score: int | None):
    filters = [NewsEvent.status.in_(_VISIBLE_EVENT_STATUSES)]
    if category is not None:
        filters.append(NewsEvent.category == category)
    if min_score is not None:
        filters.append(NewsEvent.importance_score >= min_score)
    return tuple(filters)


def _event_record(event: NewsEvent, *, providers: tuple[str, ...], item_count: int):
    return dict(
        event_id=event.id, event_key=event.event_key, title=event.title,
        category=event.category, importance_score=event.importance_score,
        score_reason=event.score_reason, rule_version=event.rule_version,
        status=event.status, created_at=event.created_at,
        updated_at=event.updated_at, providers=providers, item_count=item_count,
    )


def build_source_upsert(source: SourceConfiguration):
    """Atomic identity acquisition with a defensive post-write identity check."""

    table = NewsSource.__table__
    statement = mysql_insert(table).values(
        provider=source.provider, external_id=source.external_id,
        source_type=source.source_type, name=source.name, url=source.url,
        config_json=source.config_json, enabled=source.enabled,
    )
    return statement.on_duplicate_key_update(
        id=func.LAST_INSERT_ID(table.c.id)
    )


def build_event_upsert(event_key: str, title: str):
    """Create or reuse an event without changing its title or timestamps."""

    table = NewsEvent.__table__
    statement = mysql_insert(table).values(event_key=event_key, title=title)
    return statement.on_duplicate_key_update(
        id=func.LAST_INSERT_ID(table.c.id)
    )


def build_item_upsert(item: DeduplicatedNewsItem, event_id: int):
    """Build a MySQL UPSERT whose time guard uses the *stored* fetched_at.

    MySQL single-table assignments can be evaluated left to right. All
    timestamp-guarded mutable columns therefore precede the fetched_at
    assignment. The protected identity fields and first source_id never
    appear on the UPDATE side. An event mismatch is left unchanged for the
    repository to detect and propagate to the transaction owner.
    """

    if type(event_id) is not int or event_id <= 0:
        raise ValueError("A persisted event ID is required")
    table = NewsItem.__table__
    candidate = item.candidate
    statement = mysql_insert(table).values(
        source_id=item.source_id, provider=item.provider,
        external_id=item.external_id, event_id=event_id,
        title=candidate.title, url=str(candidate.original_url),
        canonical_url_hash=canonical_url_hash(item),
        content=candidate.raw_excerpt,
        metadata_json=metadata_for_storage(candidate),
        published_at=to_mysql_utc_datetime(candidate.published_at),
        fetched_at=to_mysql_utc_datetime(candidate.fetched_at),
        content_hash=item.content_hash,
    )
    incoming = statement.inserted
    existing = table.c
    compatible_event = or_(existing.event_id.is_(None), existing.event_id == incoming.event_id)
    newer = and_(incoming.fetched_at > existing.fetched_at, compatible_event)
    linkable = and_(existing.event_id.is_(None), incoming.fetched_at >= existing.fetched_at)
    updates = [
        ("id", func.LAST_INSERT_ID(existing.id)),
        ("title", func.IF(newer, incoming.title, existing.title)),
        ("url", func.IF(newer, incoming.url, existing.url)),
        ("canonical_url_hash", func.IF(newer, incoming.canonical_url_hash, existing.canonical_url_hash)),
        ("content", func.IF(newer, incoming.content, existing.content)),
        ("metadata_json", func.IF(newer, incoming.metadata_json, existing.metadata_json)),
        ("published_at", func.IF(newer, incoming.published_at, existing.published_at)),
        ("content_hash", func.IF(newer, incoming.content_hash, existing.content_hash)),
        ("updated_at", func.IF(or_(newer, linkable), func.UTC_TIMESTAMP(6), existing.updated_at)),
        ("event_id", func.IF(linkable, incoming.event_id, existing.event_id)),
        ("fetched_at", func.IF(newer, incoming.fetched_at, existing.fetched_at)),
    ]
    return statement.on_duplicate_key_update(updates)


class NewsRepository:
    """Perform SQL only; session and transaction ownership stay with caller."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_events(
        self, *, category: str | None = None, min_score: int | None = None,
    ) -> int:
        """Count visible logical events without joining their original items."""

        return await self._session.scalar(
            select(func.count()).select_from(NewsEvent).where(
                *_event_filters(category=category, min_score=min_score)
            )
        )

    async def list_events(
        self, *, page: int, page_size: int, category: str | None = None,
        min_score: int | None = None, sort: str = "score",
    ) -> tuple[EventListRecord, ...]:
        """Page event rows first, then aggregate providers for that page only."""

        if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Invalid news page")
        if sort not in ("score", "latest"):
            raise ValueError("Invalid news sort")
        ordering = (
            (NewsEvent.importance_score.is_(None), NewsEvent.importance_score.desc(),
             NewsEvent.created_at.desc(), NewsEvent.id.desc())
            if sort == "score" else (NewsEvent.created_at.desc(), NewsEvent.id.desc())
        )
        statement = (
            select(NewsEvent)
            .where(*_event_filters(category=category, min_score=min_score))
            .order_by(*ordering)
            .limit(page_size).offset((page - 1) * page_size)
        )
        events = (await self._session.execute(statement)).scalars().all()
        if not events:
            return ()

        event_ids = [event.id for event in events]
        summaries = (await self._session.execute(
            select(NewsItem.event_id, NewsItem.provider, func.count())
            .where(NewsItem.event_id.in_(event_ids))
            .group_by(NewsItem.event_id, NewsItem.provider)
            .order_by(NewsItem.event_id, NewsItem.provider)
        )).all()
        by_event: dict[int, list[tuple[str, int]]] = {event_id: [] for event_id in event_ids}
        for event_id, provider, count in summaries:
            by_event[event_id].append((provider, count))
        return tuple(
            EventListRecord(**_event_record(
                event,
                providers=tuple(provider for provider, _ in by_event[event.id]),
                item_count=sum(count for _, count in by_event[event.id]),
            ))
            for event in events
        )

    async def get_event_detail(self, event_id: int) -> EventDetailRecord | None:
        """Load one visible event and all original source links in two queries."""

        if type(event_id) is not int or event_id <= 0:
            raise ValueError("A positive event ID is required")
        event = (await self._session.execute(
            select(NewsEvent).where(NewsEvent.id == event_id)
        )).scalars().one_or_none()
        if event is None or event.status not in _VISIBLE_EVENT_STATUSES:
            return None
        rows = (await self._session.execute(
            select(NewsItem, NewsSource.name)
            .join(NewsSource, NewsItem.source_id == NewsSource.id)
            .where(NewsItem.event_id == event_id)
            .order_by(NewsItem.id)
        )).all()
        sources = tuple(
            OriginalNewsRecord(
                provider=item.provider, external_id=item.external_id,
                title=item.title, original_url=item.url, source_name=source_name,
                published_at=item.published_at, fetched_at=item.fetched_at,
            )
            for item, source_name in rows
        )
        providers = tuple(sorted({source.provider for source in sources}))
        return EventDetailRecord(
            **_event_record(event, providers=providers, item_count=len(sources)),
            summary=event.summary, sources=sources,
        )

    async def get_event_summary_input(
        self, event_id: int, *, for_update: bool = False,
    ) -> SummaryInputRecord | None:
        """Read one visible event and a bounded set of stored item excerpts."""

        if type(event_id) is not int or event_id <= 0:
            raise ValueError("A positive event ID is required")
        event_query = select(NewsEvent).options(
            undefer(NewsEvent.summary_json), undefer(NewsEvent.summary_model),
            undefer(NewsEvent.summary_prompt_version),
            undefer(NewsEvent.summary_input_fingerprint),
        ).where(NewsEvent.id == event_id)
        if for_update:
            event_query = event_query.with_for_update()
        event = (await self._session.execute(event_query)).scalars().one_or_none()
        if event is None or event.status not in _VISIBLE_EVENT_STATUSES:
            return None
        item_query = (
            select(
                NewsItem.provider, NewsItem.external_id, NewsItem.title, NewsItem.content,
                NewsItem.url, NewsSource.name, NewsItem.published_at,
            )
            .join(NewsSource, NewsItem.source_id == NewsSource.id)
            .where(NewsItem.event_id == event_id)
            .order_by(NewsItem.id)
            .limit(_SUMMARY_ITEM_LIMIT + 1)
        )
        if for_update:
            item_query = item_query.with_for_update()
        rows = (await self._session.execute(item_query)).all()
        items = tuple(
            SummaryItemRecord(
                provider=provider, external_id=external_id, title=title, excerpt=content,
                original_url=url, source_name=source_name, published_at=published_at,
            )
            for provider, external_id, title, content, url, source_name, published_at
            in rows[:_SUMMARY_ITEM_LIMIT]
        )
        cache = None
        if all((event.summary_json, event.summary_model, event.summary_prompt_version,
                event.summary_input_fingerprint)):
            cache = SummaryCacheRecord(
                payload=event.summary_json, model=event.summary_model,
                prompt_version=event.summary_prompt_version,
                input_fingerprint=event.summary_input_fingerprint,
            )
        return SummaryInputRecord(
            event_id=event.id, title=event.title, items=items,
            truncated=len(rows) > _SUMMARY_ITEM_LIMIT,
            cache=cache,
        )

    async def save_event_summary_cache(
        self, event_id: int, *, payload: dict, model: str,
        prompt_version: str, input_fingerprint: str, generated_at: datetime,
    ) -> None:
        """Write a validated cache after caller rechecks locked evidence."""

        await self._session.execute(
            update(NewsEvent).where(NewsEvent.id == event_id).values(
                summary_json=payload, summary_model=model,
                summary_prompt_version=prompt_version,
                summary_input_fingerprint=input_fingerprint,
                summary_generated_at=generated_at,
            )
        )

    async def ensure_source(self, source: SourceConfiguration) -> int:
        await self._session.execute(build_source_upsert(source))
        source_id = await self._session.scalar(select(func.LAST_INSERT_ID()))
        row = (await self._session.execute(
            select(NewsSource.provider, NewsSource.external_id).where(NewsSource.id == source_id)
        )).one()
        if (row.provider, row.external_id) != (source.provider, source.external_id):
            raise SourceIdentityConflict("Source configuration unique-key collision")
        return source_id

    async def ensure_event(self, event_key: str, title: str) -> int:
        await self._session.execute(build_event_upsert(event_key, title))
        return await self._session.scalar(select(func.LAST_INSERT_ID()))

    async def upsert_item(self, item: DeduplicatedNewsItem, *, event_id: int) -> int:
        await self._session.execute(build_item_upsert(item, event_id))
        item_id = await self._session.scalar(select(func.LAST_INSERT_ID()))
        row = (await self._session.execute(
            select(NewsItem.provider, NewsItem.external_id, NewsItem.event_id)
            .where(NewsItem.id == item_id)
            .with_for_update()
        )).one()
        if (row.provider, row.external_id) != item.item_key:
            raise ItemIdentityConflict("Original news identity collision")
        if row.event_id != event_id:
            raise EventLinkConflict("News item event association conflicts")
        return item_id

    async def load_event_score_snapshot(self, event_id: int) -> ScoreEvent:
        """Lock an event, then read all persisted originals for one score.

        Call this before any ordinary consistent read in a fresh transaction.
        The event lock coordinates scoring writers; item locks provide current
        reads but do not replace a cross-process event-write protocol.
        """

        if type(event_id) is not int or event_id <= 0:
            raise ValueError("A persisted event ID is required")
        event = (await self._session.execute(
            select(NewsEvent.id, NewsEvent.event_key)
            .where(NewsEvent.id == event_id).with_for_update()
        )).one_or_none()
        if event is None:
            raise EventScoreNotFound("News event does not exist")
        if event.id != event_id:
            raise EventScoreIdentityConflict("Resolved event ID differs from requested event")

        rows = (await self._session.execute(
            select(
                NewsItem.provider, NewsItem.external_id, NewsItem.title,
                NewsItem.published_at, NewsItem.fetched_at, NewsItem.metadata_json,
            )
            .where(NewsItem.event_id == event_id)
            .order_by(NewsItem.provider, NewsItem.external_id)
            .with_for_update()
        )).all()
        if not rows:
            raise ValueError("Persisted event has no news items")
        items = []
        for row in rows:
            if type(row.metadata_json) is not dict:
                raise ValueError("Stored news metadata must be a JSON object")
            fetched_at = _stored_utc(row.fetched_at)
            if fetched_at is None:
                raise ValueError("Stored news fetch time is required")
            items.append(ScoreItem(
                provider=row.provider, external_id=row.external_id,
                title=row.title, published_at=_stored_utc(row.published_at),
                fetched_at=fetched_at, metadata=row.metadata_json,
            ))
        # The canonical identity itself is not stored; the stable event key is
        # sufficient for ScoreEvent, whose scoring rules use persisted items.
        return ScoreEvent(
            event_key=event.event_key,
            canonical_identity=f"persisted:{event.event_key}",
            items=tuple(items),
        )

    async def save_event_score(self, event_id: int, score: ScoreResult) -> bool:
        """Save one validated score atomically; caller owns the transaction.

        Returns True when fields changed and False for an identical saved score.
        The locking read serializes writers of this row, but does not establish
        whether the supplied score was computed from the latest event snapshot.
        """

        if type(event_id) is not int or event_id <= 0:
            raise ValueError("A persisted event ID is required")
        if not isinstance(score, ScoreResult):
            raise ValueError("A valid ScoreResult is required")
        if type(score.importance_score) is not int:
            raise ValueError("Event importance score must be an integer")
        # Revalidate even model_construct() instances, which bypass Pydantic.
        score = ScoreResult.model_validate(score.model_dump())
        if score.category not in _CATEGORIES:
            raise ValueError("Invalid event score")

        row = (await self._session.execute(
            select(
                NewsEvent.id, NewsEvent.event_key, NewsEvent.category,
                NewsEvent.importance_score, NewsEvent.score_reason,
                NewsEvent.rule_version,
            ).where(NewsEvent.id == event_id).with_for_update()
        )).one_or_none()
        if row is None:
            raise EventScoreNotFound("News event does not exist")
        if row.id != event_id or row.event_key != score.event_key:
            raise EventScoreIdentityConflict("Score event identity differs from persisted event")
        fields = (
            score.category, score.importance_score,
            score.score_reason, score.rule_version,
        )
        if fields == (
            row.category, row.importance_score,
            row.score_reason, row.rule_version,
        ):
            return False
        await self._session.execute(
            update(NewsEvent)
            .where(NewsEvent.id == event_id, NewsEvent.event_key == score.event_key)
            .values(
                category=score.category,
                importance_score=score.importance_score,
                score_reason=score.score_reason,
                rule_version=score.rule_version,
            )
        )
        return True
