"""Opt-in MySQL service tests; FakeCollectors make every news input offline.

These tests are collected only by an explicit tests_mysql target. The existing
directory gate skips them without the CLI switch and rejects the switch without
the separate environment permit before any fixture creates an Engine.
"""

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from aipulse.config import load_database_settings
from aipulse.models.base import Base
from aipulse.models.entities import NewsEvent, NewsItem, NewsSource
from aipulse.news.collectors.base import BaseCollector
from aipulse.news.merge import merge_events
from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.repository import NewsRepository, SourceConfiguration
from aipulse.news.service import IngestionRequest, NewsIngestionService
from aipulse.news.types import (
    CollectionResult, CollectionStatus, CollectorErrorCode, NewsCandidate,
)
from aipulse.storage.db import create_session_factory, create_test_engine
from tests_mysql.mysql.ingestion_support import GuardedSessionFactory
from tests_mysql.mysql.safety import (
    EXPECTED_TABLES, TestRows, cleanup_owned_rows, require_write_permission,
    verify_live_test_connection,
)


pytestmark = pytest.mark.asyncio
RUN_UUID = uuid4().hex
OBSERVED_AT = datetime(2026, 10, 16, 9, tzinfo=timezone.utc)
BUSINESS_TABLES = tuple(sorted(EXPECTED_TABLES - {"alembic_version"}))


@dataclass
class Case:
    engine: object
    factory: object
    guarded_factory: GuardedSessionFactory
    rows: TestRows
    case_key: str
    pytestconfig: object
    baseline_counts: dict[str, int]

    def identity(self, label: str) -> str:
        return f"{self.rows.namespace}{self.case_key}-{label}"

    def source(self, provider: str, label: str) -> SourceConfiguration:
        external_id = self.identity(label)
        self.rows.record_source(external_id, provider=provider)
        return SourceConfiguration(
            provider=provider, external_id=external_id, source_type=provider,
            name=external_id,
        )

    def candidate(
        self, provider: str, label: str, *, url: str,
        external_id: str | None = None, title: str | None = None,
        excerpt: str = "Source excerpt", fetched_at: datetime = OBSERVED_AT,
    ) -> NewsCandidate:
        external_id = external_id or self.identity(label)
        self.rows.record_item(external_id, provider=provider)
        metadata = {
            "github": {"tag_name": "v1"},
            "arxiv": {"version": "v1"},
            "hn": {"score": 5},
        }[provider]
        item = NewsCandidate(
            provider=provider, external_id=external_id, original_url=url,
            title=title or self.identity(label + "-title"),
            raw_excerpt=excerpt, published_at=OBSERVED_AT,
            fetched_at=fetched_at, metadata=metadata,
        )
        event = merge_events(deduplicate_batch([normalize_candidate(item, 1)]))[0]
        # The first observed title is the repository's immutable event title.
        if event.event_key not in self.rows.events:
            self.rows.record_event(event.event_key, item.title)
        return item

    def github_item(self, label: str, **kwargs) -> NewsCandidate:
        tag = self.identity(label + "-tag")
        return self.candidate(
            "github", label,
            url=f"https://github.com/aipulse/integration/releases/tag/{tag}",
            **kwargs,
        )

    def paper_url(self) -> str:
        # Old-style arXiv ID grammar allows a UUID-scoped synthetic identity.
        return f"https://arxiv.org/abs/{self.identity('paper')}/1234567"

    def arxiv_item(self) -> NewsCandidate:
        paper_id = f"{self.identity('paper')}/1234567"
        return self.candidate(
            "arxiv", "arxiv-item", external_id=paper_id, url=self.paper_url(),
        )

    def hn_paper_item(self) -> NewsCandidate:
        return self.candidate(
            "hn", "hn-paper-story", url=self.paper_url() + "v2",
        )

    def service(self, responses, *, repository_factory=NewsRepository) -> NewsIngestionService:
        collectors = {
            provider: FakeCollector(self.engine, provider_responses)
            for provider, provider_responses in responses.items()
        }
        return NewsIngestionService(
            collectors=collectors, session_factory=self.guarded_factory,
            repository_factory=repository_factory,
        )

    @asynccontextmanager
    async def read_session(self):
        require_write_permission(
            cli_enabled=self.pytestconfig.getoption("--run-aipulse-mysql"),
            env_value=os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
        )
        async with self.factory() as session:
            # Reads and scoped cleanup use this same pinned connection.
            await verify_live_test_connection(await session.connection())
            yield session

    async def counts(self) -> dict[str, int]:
        async with self.read_session() as session:
            return {
                name: await session.scalar(
                    select(func.count()).select_from(Base.metadata.tables[name])
                )
                for name in BUSINESS_TABLES
            }

    async def cleanup(self) -> None:
        if not self.rows.cleanup_statements():
            return
        try:
            async with self.read_session() as session:
                await cleanup_owned_rows(session, self.rows)
                await session.commit()
            async with self.read_session() as session:
                for statement in self.rows.cleanup_statements():
                    count = await session.scalar(
                        select(func.count()).select_from(statement.table)
                        .where(statement.whereclause)
                    )
                    assert count == 0, "UUID test rows remain after scoped cleanup"
            assert await self.counts() == self.baseline_counts
        except Exception as exc:
            raise AssertionError(
                f"Scoped ingestion cleanup failed for {self.rows.namespace}"
                f"{self.case_key}; inspect only those identities ({type(exc).__name__})"
            ) from None


class FakeCollector(BaseCollector):
    def __init__(self, engine, responses):
        self.engine = engine
        self.responses = responses
        self.calls = []

    async def collect(self, source, *, cursor=None):
        # The registration transaction must have closed before this HTTP slot.
        assert self.engine.pool.checkedout() == 0
        self.calls.append((source, cursor))
        result = self.responses[source.external_id]
        items, status, error_code = result
        return CollectionResult(
            source_id=source.source_id, candidates=tuple(items), status=status,
            fetched_count=len(items), error_code=error_code,
        )


def successful(*items):
    return items, CollectionStatus.SUCCESS, None


def partial(*items):
    return items, CollectionStatus.PARTIAL, CollectorErrorCode.RATE_LIMIT


def failed():
    return (), CollectionStatus.FAILED, CollectorErrorCode.TIMEOUT


@pytest_asyncio.fixture
async def case(request):
    require_write_permission(
        cli_enabled=request.config.getoption("--run-aipulse-mysql"),
        env_value=os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
    )
    engine = create_test_engine(load_database_settings())
    factory = create_session_factory(engine)
    context = Case(
        engine=engine, factory=factory,
        guarded_factory=GuardedSessionFactory(
            factory,
            cli_enabled=lambda: request.config.getoption("--run-aipulse-mysql"),
            env_value=lambda: os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
        ),
        rows=TestRows(f"aipulse-it-{RUN_UUID}-"),
        case_key=uuid4().hex[:12], pytestconfig=request.config,
        baseline_counts={},
    )
    try:
        context.baseline_counts = await context.counts()
        yield context
    finally:
        try:
            await context.cleanup()
        finally:
            await engine.dispose()


async def test_service_single_github_source_creates_linked_rows(case):
    config = case.source("github", "source")
    item = case.github_item("release")
    report = await case.service({"github": {config.external_id: successful(item)}}).ingest(
        [IngestionRequest(config)]
    )

    assert report.success_count == 1 and report.processed_count == 1
    assert report.sources[0].event_count == 1
    async with case.read_session() as session:
        source_row = (await session.scalars(select(NewsSource).where(
            NewsSource.provider == "github", NewsSource.external_id == config.external_id,
        ))).one()
        item_row = (await session.scalars(select(NewsItem).where(
            NewsItem.provider == "github", NewsItem.external_id == item.external_id,
        ))).one()
        event_row = await session.get(NewsEvent, item_row.event_id)
        assert item_row.source_id == source_row.id
        assert event_row is not None and event_row.event_key in case.rows.events
        assert item_row.url == str(item.original_url)


async def test_service_repeated_collection_reuses_all_three_identities(case):
    config = case.source("github", "source")
    item = case.github_item("release")
    service = case.service({"github": {config.external_id: successful(item)}})
    first = await service.ingest([IngestionRequest(config)])
    first_counts = await case.counts()
    second = await service.ingest([IngestionRequest(config)])

    assert first.processed_count == second.processed_count == 1
    assert await case.counts() == first_counts
    assert first_counts["news_source"] == case.baseline_counts["news_source"] + 1
    assert first_counts["news_event"] == case.baseline_counts["news_event"] + 1
    assert first_counts["news_item"] == case.baseline_counts["news_item"] + 1


async def test_service_arxiv_and_hn_preserve_two_originals_under_one_event(case):
    paper_source = case.source("arxiv", "paper-source")
    hn_source = case.source("hn", "hn-source")
    paper = case.arxiv_item()
    story = case.hn_paper_item()
    report = await case.service({
        "arxiv": {paper_source.external_id: successful(paper)},
        "hn": {hn_source.external_id: successful(story)},
    }).ingest([IngestionRequest(paper_source), IngestionRequest(hn_source)])

    assert report.processed_count == 2
    async with case.read_session() as session:
        rows = (await session.scalars(select(NewsItem).where(
            NewsItem.external_id.in_([paper.external_id, story.external_id]),
        ))).all()
        assert len(rows) == 2 and len({row.event_id for row in rows}) == 1
        assert {(row.provider, row.external_id, row.url) for row in rows} == {
            (paper.provider, paper.external_id, str(paper.original_url)),
            (story.provider, story.external_id, str(story.original_url)),
        }


async def test_service_similar_titles_without_common_identity_stay_separate(case):
    config = case.source("hn", "hn-source")
    title = case.identity("similar-title")
    first = case.candidate(
        "hn", "first-story", url=f"https://example.org/{case.identity('article-a')}",
        title=title,
    )
    second = case.candidate(
        "hn", "second-story", url=f"https://example.org/{case.identity('article-b')}",
        title=title,
    )
    report = await case.service({"hn": {
        config.external_id: successful(first, second),
    }}).ingest([IngestionRequest(config)])

    assert report.sources[0].event_count == 2
    async with case.read_session() as session:
        rows = (await session.scalars(select(NewsItem).where(
            NewsItem.provider == "hn",
            NewsItem.external_id.in_([first.external_id, second.external_id]),
        ))).all()
        assert len(rows) == 2 and rows[0].event_id != rows[1].event_id


async def test_service_older_fetch_does_not_revert_newer_content(case):
    config = case.source("github", "source")
    newest = case.github_item("release", excerpt="New notes")
    older = case.github_item(
        "release", excerpt="Old notes", fetched_at=OBSERVED_AT - timedelta(seconds=1),
    )
    service = case.service({"github": {config.external_id: successful(newest)}})
    await service.ingest([IngestionRequest(config)])
    service = case.service({"github": {config.external_id: successful(older)}})
    report = await service.ingest([IngestionRequest(config)])

    assert report.processed_count == 1
    async with case.read_session() as session:
        row = (await session.scalars(select(NewsItem).where(
            NewsItem.provider == "github", NewsItem.external_id == newest.external_id,
        ))).one()
        assert row.content == "New notes"
        assert row.fetched_at == OBSERVED_AT.replace(tzinfo=None)


async def test_service_partial_source_persists_valid_candidate(case):
    config = case.source("github", "source")
    item = case.github_item("release")
    report = await case.service({"github": {config.external_id: partial(item)}}).ingest(
        [IngestionRequest(config)]
    )

    entry = report.sources[0]
    assert entry.status is CollectionStatus.PARTIAL
    assert entry.collection_error_code == entry.error_code == "rate_limit"
    assert entry.processed_count == 1
    async with case.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.provider == "github", NewsItem.external_id == item.external_id,
        )) == 1


async def test_service_failed_source_does_not_block_next_source(case):
    bad_source = case.source("github", "failed-source")
    good_source = case.source("hn", "good-source")
    item = case.candidate(
        "hn", "good-story", url=f"https://example.org/{case.identity('good-article')}",
    )
    report = await case.service({
        "github": {bad_source.external_id: failed()},
        "hn": {good_source.external_id: successful(item)},
    }).ingest([IngestionRequest(bad_source), IngestionRequest(good_source)])

    assert report.failed_count == report.success_count == 1
    assert report.sources[0].processed_count == 0
    assert report.sources[1].processed_count == 1
    async with case.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.external_id == item.external_id,
        )) == 1


async def test_service_persistence_exception_rolls_back_one_source_only(case):
    first_source = case.source("github", "first-source")
    failed_source = case.source("github", "failed-source")
    last_source = case.source("github", "last-source")
    first = case.github_item("first-release")
    bad = case.github_item("failed-release")
    last = case.github_item("last-release")

    class FailingAfterRealWrite(NewsRepository):
        async def upsert_item(self, item, *, event_id):
            item_id = await super().upsert_item(item, event_id=event_id)
            if item.external_id == bad.external_id:
                raise RuntimeError("Injected test-only failure after real UPSERT")
            return item_id

    report = await case.service({"github": {
        first_source.external_id: successful(first),
        failed_source.external_id: successful(bad),
        last_source.external_id: successful(last),
    }}, repository_factory=FailingAfterRealWrite).ingest([
        IngestionRequest(first_source), IngestionRequest(failed_source),
        IngestionRequest(last_source),
    ])

    assert [entry.status for entry in report.sources] == [
        CollectionStatus.SUCCESS, CollectionStatus.FAILED, CollectionStatus.SUCCESS,
    ]
    assert [entry.processed_count for entry in report.sources] == [1, 0, 1]
    assert report.sources[1].error_code == "persistence_failed"
    async with case.read_session() as session:
        rows = (await session.scalars(select(NewsItem).where(
            NewsItem.external_id.in_([first.external_id, bad.external_id, last.external_id]),
        ))).all()
        assert {row.external_id for row in rows} == {first.external_id, last.external_id}
        assert await session.scalar(select(func.count()).select_from(NewsEvent).where(
            NewsEvent.event_key == next(
                key for key, title in case.rows.events.items() if title == bad.title
            ),
        )) == 0
        # Source registration commits before the fake HTTP request and is cleaned.
        assert await session.scalar(select(func.count()).select_from(NewsSource).where(
            NewsSource.external_id == failed_source.external_id,
        )) == 1


async def test_service_repeated_three_provider_batch_keeps_identity_counts(case):
    github_source = case.source("github", "github-source")
    paper_source = case.source("arxiv", "paper-source")
    hn_source = case.source("hn", "hn-source")
    release = case.github_item("release")
    paper = case.arxiv_item()
    story = case.hn_paper_item()
    service = case.service({
        "github": {github_source.external_id: successful(release)},
        "arxiv": {paper_source.external_id: successful(paper)},
        "hn": {hn_source.external_id: successful(story)},
    })
    requests = [
        IngestionRequest(github_source), IngestionRequest(paper_source),
        IngestionRequest(hn_source),
    ]
    first = await service.ingest(requests)
    counts = await case.counts()
    second = await service.ingest(requests)

    assert first.processed_count == second.processed_count == 3
    assert await case.counts() == counts
    assert counts["news_source"] == case.baseline_counts["news_source"] + 3
    assert counts["news_item"] == case.baseline_counts["news_item"] + 3
    assert counts["news_event"] == case.baseline_counts["news_event"] + 2
