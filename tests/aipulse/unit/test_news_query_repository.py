"""Offline MySQL-dialect contract for event-level read queries."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql

from aipulse.models.entities import NewsEvent, NewsItem
from aipulse.news.repository import NewsRepository


STAMP = datetime(2026, 10, 17, 8, 30)


def sql(statement):
    return str(statement.compile(
        dialect=mysql.dialect(), compile_kwargs={"literal_binds": True},
    ))


class Rows:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def one_or_none(self):
        return self.values[0] if self.values else None


class RecordingSession:
    def __init__(self, *, pages=(), source_summaries=(), detail=None, originals=(), total=0):
        self.statements = []
        self.pages = list(pages)
        self.source_summaries = list(source_summaries)
        self.detail = detail
        self.originals = list(originals)
        self.total = total

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.total

    async def execute(self, statement):
        self.statements.append(statement)
        query = sql(statement)
        if "JOIN news_source " in query:
            return Rows(self.originals)
        if "FROM news_item" in query:
            return Rows(self.source_summaries)
        if "news_event.id =" in query:
            return Rows([self.detail] if self.detail else [])
        return Rows(self.pages)

    async def commit(self):
        raise AssertionError("Read repository must not commit")

    async def rollback(self):
        raise AssertionError("Read repository must not roll back")


def event(*, id=7, score=65, category="paper", status="active"):
    return NewsEvent(
        id=id, event_key=f"{id:064x}", title="Paper", summary="Summary",
        importance_score=score, category=category, score_reason="Reason",
        rule_version="rules:1", status=status, created_at=STAMP,
        updated_at=STAMP,
    )


def test_score_order_places_null_last_and_ties_use_time_then_id():
    session = RecordingSession()
    asyncio.run(NewsRepository(session).list_events(page=1, page_size=20, sort="score"))
    query = sql(session.statements[0])
    assert "news_event.importance_score IS NULL" in query
    assert "news_event.importance_score DESC" in query
    assert "news_event.created_at DESC" in query
    assert "news_event.id DESC" in query
    assert query.index("IS NULL") < query.index("importance_score DESC")
    assert " LIMIT 0, 20" in query


def test_latest_order_uses_real_event_created_at_and_stable_id():
    session = RecordingSession()
    asyncio.run(NewsRepository(session).list_events(page=2, page_size=3, sort="latest"))
    query = sql(session.statements[0])
    assert "news_event.created_at DESC, news_event.id DESC" in query
    assert "news_event.published_at" not in query
    assert " LIMIT 3, 3" in query


def test_category_score_and_visibility_filters_apply_to_count_and_page_without_join():
    session = RecordingSession(total=4)
    repo = NewsRepository(session)
    assert asyncio.run(repo.count_events(category="paper", min_score=50)) == 4
    assert asyncio.run(repo.list_events(
        page=1, page_size=20, category="paper", min_score=50,
    )) == ()
    assert len(session.statements) == 2
    for statement in session.statements:
        query = sql(statement)
        assert "news_event.category = 'paper'" in query
        assert "news_event.importance_score >= 50" in query
        assert "news_event.status IN ('active', 'needs_review')" in query
        assert " JOIN " not in query


def test_page_and_total_are_event_based_when_one_event_has_two_sources():
    session = RecordingSession(
        total=1, pages=[event()],
        source_summaries=[(7, "arxiv", 1), (7, "hn", 1)],
    )
    repo = NewsRepository(session)
    assert asyncio.run(repo.count_events()) == 1
    results = asyncio.run(repo.list_events(page=1, page_size=20))
    assert len(results) == 1
    assert results[0].event_id == 7
    assert results[0].providers == ("arxiv", "hn")
    assert results[0].item_count == 2
    assert "GROUP BY news_item.event_id, news_item.provider" in sql(session.statements[2])


def test_empty_page_avoids_source_lookup():
    session = RecordingSession()
    assert asyncio.run(NewsRepository(session).list_events(page=1, page_size=20)) == ()
    assert len(session.statements) == 1


@pytest.mark.parametrize("page,size", [(0, 20), (1, 0), (1, 101)])
def test_repository_rejects_unbounded_paging_before_sql(page, size):
    session = RecordingSession()
    with pytest.raises(ValueError):
        asyncio.run(NewsRepository(session).list_events(page=page, page_size=size))
    assert session.statements == []


def test_detail_loads_one_event_and_all_sources_in_two_queries():
    original = NewsItem(
        id=11, source_id=3, provider="arxiv", external_id="2601.12345",
        event_id=7, title="Paper", url="https://arxiv.org/abs/2601.12345",
        published_at=STAMP, fetched_at=STAMP,
    )
    story = NewsItem(
        id=12, source_id=4, provider="hn", external_id="999",
        event_id=7, title="HN discussion", url="https://news.ycombinator.com/item?id=999",
        published_at=None, fetched_at=STAMP,
    )
    session = RecordingSession(detail=event(), originals=[
        (original, "AI papers"), (story, "HN topstories"),
    ])
    result = asyncio.run(NewsRepository(session).get_event_detail(7))
    assert result.event_id == 7
    assert len(result.sources) == 2
    assert result.sources[0].source_name == "AI papers"
    assert result.sources[0].original_url == original.url
    assert result.sources[1].provider == "hn"
    assert result.providers == ("arxiv", "hn") and result.item_count == 2
    assert len(session.statements) == 2
    assert " JOIN news_source " in sql(session.statements[1])
    assert "news_item.event_id = 7" in sql(session.statements[1])


def test_hidden_or_missing_detail_returns_none_without_source_query():
    for stored in (None, event(status="hidden")):
        session = RecordingSession(detail=stored)
        assert asyncio.run(NewsRepository(session).get_event_detail(7)) is None
        assert len(session.statements) == 1


def test_queries_never_issue_dml_or_commit():
    session = RecordingSession(detail=event())
    repo = NewsRepository(session)
    asyncio.run(repo.count_events())
    asyncio.run(repo.list_events(page=1, page_size=20))
    asyncio.run(repo.get_event_detail(7))
    assert all(statement.is_select for statement in session.statements)
