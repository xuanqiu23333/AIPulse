"""Bounded, read-only event queries for the local news UI."""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from aipulse.api.schemas import NewsEventDetail, NewsEventPage, NewsEventResponse
from aipulse.news.repository import NewsRepository


Category = Literal["model", "paper", "tool", "product", "industry", "discussion", "other"]
Sort = Literal["score", "latest"]

router = APIRouter(prefix="/api/v1/news", tags=["news"])


async def get_news_repository(request: Request) -> AsyncIterator[NewsRepository]:
    """Scope one read-only Session to the request; never commit a query."""

    async with request.app.state.session_factory() as session:
        yield NewsRepository(session)


@router.get("/events", response_model=NewsEventPage)
async def list_news_events(
    repository: Annotated[NewsRepository, Depends(get_news_repository)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    category: Category | None = None,
    min_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    sort: Sort = "score",
) -> NewsEventPage:
    total = await repository.count_events(category=category, min_score=min_score)
    rows = await repository.list_events(
        page=page, page_size=page_size, category=category,
        min_score=min_score, sort=sort,
    )
    return NewsEventPage(
        page=page, page_size=page_size, total=total,
        items=tuple(NewsEventResponse.model_validate(row) for row in rows),
    )


@router.get("/events/{event_id}", response_model=NewsEventDetail)
async def get_news_event(
    event_id: Annotated[int, Path(ge=1)],
    repository: Annotated[NewsRepository, Depends(get_news_repository)],
) -> NewsEventDetail:
    row = await repository.get_event_detail(event_id)
    if row is None:
        raise HTTPException(status_code=404)
    return NewsEventDetail.model_validate(row)
