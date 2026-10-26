"""Orchestrate one bounded collection call per configured news source.

The source identity is acquired in a short transaction before external HTTP.
Normalization and event grouping are pure work outside transactions. A second
short transaction persists one source's accepted page. The repository owns SQL;
this service owns Session lifetime and commit/rollback via ``session.begin()``.
"""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from aipulse.news.collectors.base import BaseCollector
from aipulse.news.merge import merge_events
from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.repository import NewsRepository, SourceConfiguration
from aipulse.news.types import (
    CollectionResult, CollectionStatus, CollectorError, CollectorSource,
)


_PROVIDERS = frozenset({"github", "arxiv", "hn"})
_ERROR_SUMMARIES = {
    "unsupported_provider": "Source provider is not configured",
    "invalid_response": "Source response was invalid",
    "persistence_failed": "Source persistence failed",
    "unexpected": "Source collection failed",
}


@dataclass(frozen=True)
class IngestionRequest:
    """One configured source and its opaque provider pagination cursor."""

    source: SourceConfiguration
    cursor: str | None = None


@dataclass(frozen=True)
class SourceIngestionResult:
    """Counts for one attempt, not counts of newly inserted database rows.

    ``processed_count`` counts unique original identities committed in this
    attempt; ``event_count`` counts event groups touched. Repository methods
    do not disclose insert-versus-update, so neither count claims new rows.
    """

    provider: str
    external_id: str
    source_id: int | None
    status: CollectionStatus
    collection_status: CollectionStatus | None = None
    collection_error_code: str | None = None
    fetched_count: int = 0
    accepted_count: int = 0
    processed_count: int = 0
    event_count: int = 0
    next_cursor: str | None = None
    error_code: str | None = None
    error_summary: str | None = None
    committed_event_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class IngestionReport:
    sources: tuple[SourceIngestionResult, ...]

    @property
    def processed_count(self) -> int:
        return sum(result.processed_count for result in self.sources)

    @property
    def success_count(self) -> int:
        return sum(result.status is CollectionStatus.SUCCESS for result in self.sources)

    @property
    def partial_count(self) -> int:
        return sum(result.status is CollectionStatus.PARTIAL for result in self.sources)

    @property
    def failed_count(self) -> int:
        return sum(result.status is CollectionStatus.FAILED for result in self.sources)


class NewsIngestionService:
    """Run injected provider collectors sequentially with source isolation."""

    def __init__(
        self,
        *,
        collectors: Mapping[str, BaseCollector],
        session_factory: Callable[[], AsyncSession],
        repository_factory: Callable[[AsyncSession], NewsRepository] = NewsRepository,
    ) -> None:
        self._collectors = dict(collectors)
        self._session_factory = session_factory
        self._repository_factory = repository_factory

    async def ingest(self, requests: Iterable[IngestionRequest]) -> IngestionReport:
        results = []
        for request in requests:
            results.append(await self._ingest_one(request))
        return IngestionReport(tuple(results))

    async def _ingest_one(self, request: IngestionRequest) -> SourceIngestionResult:
        config = request.source
        identity = {"provider": config.provider, "external_id": config.external_id}
        collector = self._collectors.get(config.provider)
        if config.provider not in _PROVIDERS or collector is None:
            return self._failed(identity, None, "unsupported_provider")

        try:
            CollectorSource(source_id=1, provider=config.provider, external_id=config.external_id)
        except ValidationError:
            return self._failed(identity, None, "invalid_response")

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    source_id = await self._repository_factory(session).ensure_source(config)
            source = CollectorSource(
                source_id=source_id, provider=config.provider, external_id=config.external_id,
            )
        except Exception:
            return self._failed(identity, None, "persistence_failed")

        try:
            result = await collector.collect(source, cursor=request.cursor)
        except CollectorError as exc:
            return self._failed(identity, source_id, exc.code.value, str(exc))
        except Exception:
            return self._failed(identity, source_id, "unexpected")

        if (
            not isinstance(result, CollectionResult)
            or result.source_id != source_id
            or any(item.provider != config.provider for item in result.candidates)
        ):
            return self._failed(identity, source_id, "invalid_response")

        collected = {
            **identity,
            "source_id": source_id,
            "fetched_count": result.fetched_count,
            "accepted_count": len(result.candidates),
            "next_cursor": result.next_cursor,
        }
        if result.status is CollectionStatus.FAILED:
            return self._failed(
                identity, source_id, result.error_code.value, result.error_summary,
                collection_status=result.status,
                collection_error_code=result.error_code.value,
                **{key: collected[key] for key in ("fetched_count", "accepted_count", "next_cursor")},
            )

        try:
            items = deduplicate_batch(
                normalize_candidate(candidate, source_id) for candidate in result.candidates
            )
            groups = merge_events(items)
        except Exception:
            return self._failed(
                identity, source_id, "invalid_response",
                collection_status=result.status,
                collection_error_code=result.error_code.value if result.error_code else None,
                fetched_count=result.fetched_count,
                accepted_count=len(result.candidates),
                next_cursor=result.next_cursor,
            )

        committed_event_ids: tuple[int, ...] = ()
        if items:
            item_by_key = {item.item_key: item for item in items}
            try:
                async with self._session_factory() as session:
                    async with session.begin():
                        repository = self._repository_factory(session)
                        event_ids = []
                        for group in groups:
                            title = item_by_key[group.members[0].item_key].candidate.title
                            event_id = await repository.ensure_event(group.event_key, title)
                            event_ids.append(event_id)
                            for member in group.members:
                                await repository.upsert_item(
                                    item_by_key[member.item_key], event_id=event_id,
                                )
                committed_event_ids = tuple(sorted(set(event_ids)))
            except Exception:
                return self._failed(
                    identity, source_id, "persistence_failed",
                    collection_status=result.status,
                    collection_error_code=result.error_code.value if result.error_code else None,
                    fetched_count=result.fetched_count,
                    accepted_count=len(result.candidates),
                    next_cursor=result.next_cursor,
                )

        return SourceIngestionResult(
            **collected, status=result.status, collection_status=result.status,
            collection_error_code=result.error_code.value if result.error_code else None,
            processed_count=len(items),
            event_count=len(groups),
            committed_event_ids=committed_event_ids,
            error_code=result.error_code.value if result.error_code else None,
            error_summary=result.error_summary,
        )

    @staticmethod
    def _failed(
        identity: dict[str, str], source_id: int | None, code: str,
        summary: str | None = None, *, fetched_count: int = 0,
        accepted_count: int = 0, next_cursor: str | None = None,
        collection_status: CollectionStatus | None = None,
        collection_error_code: str | None = None,
    ) -> SourceIngestionResult:
        return SourceIngestionResult(
            **identity, source_id=source_id, status=CollectionStatus.FAILED,
            collection_status=collection_status,
            collection_error_code=collection_error_code,
            fetched_count=fetched_count, accepted_count=accepted_count,
            next_cursor=next_cursor, error_code=code,
            error_summary=summary or _ERROR_SUMMARIES.get(code, "Source collection failed"),
        )
