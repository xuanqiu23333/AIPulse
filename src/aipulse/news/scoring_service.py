"""Score committed news events once per ingestion batch from persisted rows.

One service instance serializes its complete ingest-and-score runs. Each event
gets a separate short scoring transaction. This does not coordinate other
service instances or writers that bypass the workflow.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Iterable

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from aipulse.news.repository import (
    EventScoreIdentityConflict, EventScoreNotFound, NewsRepository,
)
from aipulse.news.score import ScoreConfig, ScoreResult, score_event
from aipulse.news.service import IngestionReport, IngestionRequest, NewsIngestionService
from aipulse.news.types import CollectionStatus


class EventScoreStatus(StrEnum):
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    FAILED = "failed"


@dataclass(frozen=True)
class EventScoreOutcome:
    event_id: int
    status: EventScoreStatus
    score: ScoreResult | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ScoredIngestionReport:
    ingestion: IngestionReport
    scores: tuple[EventScoreOutcome, ...]


class NewsScoringService:
    """Run one entire collection batch before scoring its committed events."""

    def __init__(
        self,
        *,
        ingestion_service: NewsIngestionService,
        session_factory: Callable[[], AsyncSession],
        repository_factory: Callable[[AsyncSession], NewsRepository] = NewsRepository,
        config: ScoreConfig | None = None,
    ) -> None:
        if config is not None and not isinstance(config, ScoreConfig):
            raise ValueError("A valid scoring configuration is required")
        self._ingestion_service = ingestion_service
        self._session_factory = session_factory
        self._repository_factory = repository_factory
        self._config = config if config is not None else ScoreConfig()
        self._batch_lock = asyncio.Lock()

    async def ingest_and_score(
        self, requests: Iterable[IngestionRequest], *, as_of: datetime,
    ) -> ScoredIngestionReport:
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("Evaluation time must include timezone")
        as_of = as_of.astimezone(timezone.utc)
        async with self._batch_lock:
            ingestion = await self._ingestion_service.ingest(requests)
            event_ids = sorted({
                event_id
                for source in ingestion.sources
                if source.status in (CollectionStatus.SUCCESS, CollectionStatus.PARTIAL)
                for event_id in source.committed_event_ids
            })
            outcomes = []
            for event_id in event_ids:
                outcomes.append(await self._score_one(event_id, as_of=as_of))
            return ScoredIngestionReport(ingestion=ingestion, scores=tuple(outcomes))

    async def _score_one(self, event_id: int, *, as_of: datetime) -> EventScoreOutcome:
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    repository = self._repository_factory(session)
                    snapshot = await repository.load_event_score_snapshot(event_id)
                    score = score_event(snapshot, as_of=as_of, config=self._config)
                    changed = await repository.save_event_score(event_id, score)
            return EventScoreOutcome(
                event_id=event_id,
                status=EventScoreStatus.UPDATED if changed else EventScoreStatus.UNCHANGED,
                score=score,
            )
        except EventScoreNotFound:
            code = "event_missing"
        except EventScoreIdentityConflict:
            code = "identity_conflict"
        except (ValidationError, ValueError, TypeError):
            code = "invalid_snapshot"
        except SQLAlchemyError:
            code = "persistence_failed"
        except Exception:
            code = "unexpected"
        return EventScoreOutcome(
            event_id=event_id, status=EventScoreStatus.FAILED, error_code=code,
        )
