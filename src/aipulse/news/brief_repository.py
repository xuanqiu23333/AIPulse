"""Small SQL boundary for one local-day brief and its saved snapshot."""

from datetime import date, datetime, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aipulse.models.entities import DailyBrief, NewsEvent, NewsItem
from aipulse.news.brief import (
    BRIEF_TIMEZONE, MAX_CANDIDATES_TO_SCAN, MIN_IMPORTANCE_SCORE,
    BriefCandidate, local_day_window,
)


class DailyBriefRepository:
    """Caller owns Session and transaction; this object never calls a model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_candidates(self, brief_date: date) -> tuple[BriefCandidate, ...]:
        start, end = local_day_window(brief_date)
        start_utc = start.replace(tzinfo=None)
        end_utc = end.replace(tzinfo=None)
        event_in_window = exists(
            select(NewsItem.id).where(
                NewsItem.event_id == NewsEvent.id,
                func.coalesce(NewsItem.published_at, NewsEvent.created_at) >= start_utc,
                func.coalesce(NewsItem.published_at, NewsEvent.created_at) < end_utc,
            )
        )
        rows = (await self._session.execute(
            select(
                NewsEvent.id, NewsEvent.title, NewsEvent.category,
                NewsEvent.importance_score,
            ).where(
                NewsEvent.status.in_(("active", "needs_review")),
                NewsEvent.importance_score >= MIN_IMPORTANCE_SCORE,
                event_in_window,
            ).order_by(
                NewsEvent.importance_score.desc(), NewsEvent.created_at.desc(),
                NewsEvent.id.desc(),
            ).limit(MAX_CANDIDATES_TO_SCAN)
        )).all()
        return tuple(BriefCandidate(*row) for row in rows)

    async def get(self, brief_date: date, *, for_update: bool = False) -> DailyBrief | None:
        statement = select(DailyBrief).where(
            DailyBrief.brief_date == brief_date, DailyBrief.timezone == BRIEF_TIMEZONE,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalars().one_or_none()

    def save(self, existing: DailyBrief | None, brief_date: date, content: str) -> None:
        generated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if existing is None:
            self._session.add(DailyBrief(
                brief_date=brief_date, timezone=BRIEF_TIMEZONE,
                content=content, status="complete", generated_at=generated_at,
            ))
        else:
            existing.content = content
            existing.status = "complete"
            existing.generated_at = generated_at
