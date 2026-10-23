"""Opt-in real MySQL contract tests; run only after separate write approval."""

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from aipulse.config import load_database_settings
from aipulse.models.entities import NewsEvent, NewsItem, NewsSource
from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.repository import (
    EventLinkConflict,
    NewsRepository,
    SourceConfiguration,
)
from aipulse.news.types import NewsCandidate
from aipulse.storage.db import create_session_factory, create_test_engine
from tests_mysql.mysql.safety import (
    TestRows,
    cleanup_owned_rows,
    require_write_permission,
    verify_live_test_connection,
)


pytestmark = pytest.mark.asyncio
RUN_UUID = uuid4().hex
OBSERVED_AT = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)


@dataclass
class Case:
    """One test's identities and preflighted Session lifecycle."""

    engine: object
    factory: object
    rows: TestRows
    case_key: str
    pytestconfig: object

    def identity(self, label: str) -> str:
        return f"{self.rows.namespace}{self.case_key}-{label}"

    def source(self, label: str, *, name: str | None = None) -> SourceConfiguration:
        external_id = self.identity(label)
        self.rows.record_source(external_id)
        return SourceConfiguration(
            provider="github", external_id=external_id,
            source_type="github_release", name=name or external_id,
        )

    def event(self, label: str) -> tuple[str, str]:
        title = self.identity(label)
        key = sha256(title.encode("utf-8")).hexdigest()
        self.rows.record_event(key, title)
        return key, title

    def item(
        self, label: str, source_id: int, *, title: str = "Integration release",
        excerpt: str = "First notes", fetched_at: datetime = OBSERVED_AT,
        metadata: dict | None = None,
    ):
        external_id = self.identity(label)
        candidate = NewsCandidate(
            provider="github", external_id=external_id,
            original_url=f"https://github.com/aipulse/integration/releases/tag/{external_id}",
            title=title, raw_excerpt=excerpt, published_at=OBSERVED_AT,
            fetched_at=fetched_at,
            metadata=metadata if metadata is not None else {"tag_name": "v1"},
        )
        item = deduplicate_batch([normalize_candidate(candidate, source_id)])[0]
        self.rows.record_item(external_id)
        return item

    @asynccontextmanager
    async def session(self):
        # Recheck both switches before creating a connection or issuing DML.
        require_write_permission(
            cli_enabled=self.pytestconfig.getoption("--run-aipulse-mysql"),
            env_value=os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
        )
        async with self.factory() as session:
            connection = await session.connection()
            await verify_live_test_connection(connection)
            try:
                yield session
            finally:
                if session.in_transaction():
                    await session.rollback()

    async def cleanup(self):
        if not self.rows.cleanup_statements():
            return
        try:
            async with self.session() as session:
                await cleanup_owned_rows(session, self.rows)
                await session.commit()
        except Exception as exc:
            raise AssertionError(
                f"Scoped MySQL cleanup failed for {self.rows.namespace}"
                f"{self.case_key}; review only those identities ({type(exc).__name__})"
            ) from None


@pytest_asyncio.fixture
async def case(request):
    require_write_permission(
        cli_enabled=request.config.getoption("--run-aipulse-mysql"),
        env_value=os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
    )
    # create_test_engine checks host, port, database and runtime account before
    # opening a socket. Its settings loader does not print credential values.
    engine = create_test_engine(load_database_settings())
    identity = sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:12]
    context = Case(
        engine=engine,
        factory=create_session_factory(engine),
        rows=TestRows(f"aipulse-it-{RUN_UUID}-"),
        case_key=identity,
        pytestconfig=request.config,
    )
    try:
        yield context
    finally:
        try:
            await context.cleanup()
        finally:
            await engine.dispose()


async def test_ensure_source_creates_and_reuses_config_identity(case):
    source = case.source("source")
    async with case.session() as session:
        repo = NewsRepository(session)
        first_id = await repo.ensure_source(source)
        assert first_id > 0
        assert await repo.ensure_source(source) == first_id
        count = await session.scalar(select(func.count()).select_from(NewsSource).where(
            NewsSource.provider == "github", NewsSource.external_id == source.external_id
        ))
        assert count == 1


async def test_distinct_configs_may_share_display_name(case):
    shared_name = case.identity("display")
    async with case.session() as session:
        repo = NewsRepository(session)
        first = await repo.ensure_source(case.source("source-a", name=shared_name))
        second = await repo.ensure_source(case.source("source-b", name=shared_name))
        assert first != second
        rows = (await session.scalars(select(NewsSource).where(
            NewsSource.name == shared_name
        ))).all()
        assert len(rows) == 2


async def test_ensure_event_reuses_event_key_without_replacing_title(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        event_id = await repo.ensure_event(key, title)
        assert event_id > 0
        assert await repo.ensure_event(key, "Later title") == event_id
        event = await session.get(NewsEvent, event_id)
        assert event.event_key == key and event.title == title


async def test_upsert_item_inserts_real_row_and_returns_primary_key(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        candidate = case.item("item", source_id)
        item_id = await repo.upsert_item(candidate, event_id=event_id)
        row = await session.get(NewsItem, item_id)
        assert item_id > 0
        assert (row.provider, row.external_id, row.source_id, row.event_id) == (
            "github", candidate.external_id, source_id, event_id
        )
        assert row.fetched_at == OBSERVED_AT.replace(tzinfo=None)


async def test_duplicate_item_identity_keeps_one_row(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        candidate = case.item("item", source_id)
        first = await repo.upsert_item(candidate, event_id=event_id)
        assert await repo.upsert_item(candidate, event_id=event_id) == first
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.provider == "github", NewsItem.external_id == candidate.external_id
        )) == 1


async def test_newer_fetched_at_updates_content_and_hash(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        first = case.item("item", source_id, excerpt="Old notes")
        newer = case.item(
            "item", source_id, excerpt="New notes",
            fetched_at=OBSERVED_AT + timedelta(seconds=1),
        )
        item_id = await repo.upsert_item(first, event_id=event_id)
        assert await repo.upsert_item(newer, event_id=event_id) == item_id
        row = await session.get(NewsItem, item_id, populate_existing=True)
        assert (row.content, row.content_hash, row.fetched_at) == (
            "New notes", newer.content_hash,
            (OBSERVED_AT + timedelta(seconds=1)).replace(tzinfo=None),
        )


async def test_equal_or_older_observation_never_reverts_content(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        newest = case.item("item", source_id, excerpt="Current")
        item_id = await repo.upsert_item(newest, event_id=event_id)
        original = await session.get(NewsItem, item_id, populate_existing=True)
        updated_at = original.updated_at
        for observation, excerpt in (
            (OBSERVED_AT, "Equal but different"),
            (OBSERVED_AT - timedelta(seconds=1), "Older"),
        ):
            await repo.upsert_item(
                case.item("item", source_id, excerpt=excerpt, fetched_at=observation),
                event_id=event_id,
            )
        row = await session.get(NewsItem, item_id, populate_existing=True)
        assert (row.content, row.content_hash, row.fetched_at, row.updated_at) == (
            "Current", newest.content_hash, OBSERVED_AT.replace(tzinfo=None), updated_at
        )


async def test_repeated_item_preserves_first_source_and_created_at(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        first_source = await repo.ensure_source(case.source("source-a"))
        other_source = await repo.ensure_source(case.source("source-b"))
        event_id = await repo.ensure_event(key, title)
        item_id = await repo.upsert_item(case.item("item", first_source), event_id=event_id)
        created_at = (await session.get(NewsItem, item_id)).created_at
        assert await repo.upsert_item(case.item(
            "item", other_source, fetched_at=OBSERVED_AT + timedelta(seconds=1)
        ), event_id=event_id) == item_id
        row = await session.get(NewsItem, item_id, populate_existing=True)
        assert row.source_id == first_source
        assert row.created_at == created_at


async def test_metadata_json_updates_only_approved_fields(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        first = case.item("item", source_id, metadata={
            "tag_name": "v1", "authorization": "not-stored",
        })
        item_id = await repo.upsert_item(first, event_id=event_id)
        newer = case.item(
            "item", source_id, fetched_at=OBSERVED_AT + timedelta(seconds=1),
            metadata={"tag_name": "v2", "prerelease": True, "token": "not-stored"},
        )
        await repo.upsert_item(newer, event_id=event_id)
        row = await session.get(NewsItem, item_id, populate_existing=True)
        assert row.metadata_json == {"tag_name": "v2", "prerelease": True}


async def test_existing_event_link_conflict_raises_and_preserves_link(case):
    first_key, first_title = case.event("event-a")
    next_key, next_title = case.event("event-b")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        first_event_id = await repo.ensure_event(first_key, first_title)
        next_event_id = await repo.ensure_event(next_key, next_title)
        item_id = await repo.upsert_item(case.item("item", source_id), event_id=first_event_id)
        with pytest.raises(EventLinkConflict):
            await repo.upsert_item(
                case.item(
                    "item", source_id, excerpt="Conflicting newer notes",
                    fetched_at=OBSERVED_AT + timedelta(seconds=1),
                ),
                event_id=next_event_id,
            )
        row = await session.get(NewsItem, item_id, populate_existing=True)
        assert row.event_id == first_event_id
        assert row.content == "First notes"


async def test_missing_event_foreign_key_fails_and_rolls_back(case):
    candidate = None
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        candidate = case.item("item", source_id)
        with pytest.raises(IntegrityError):
            await repo.upsert_item(candidate, event_id=2**64 - 1)
    async with case.session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.external_id == candidate.external_id
        )) == 0


async def test_caller_commit_is_visible_in_independent_session(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        candidate = case.item("item", source_id)
        item_id = await repo.upsert_item(candidate, event_id=event_id)
        await session.commit()
    async with case.session() as session:
        row = await session.get(NewsItem, item_id)
        assert row is not None and row.external_id == candidate.external_id


async def test_caller_rollback_discards_source_event_and_item(case):
    key, title = case.event("event")
    source = case.source("source")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(source)
        event_id = await repo.ensure_event(key, title)
        candidate = case.item("item", source_id)
        await repo.upsert_item(candidate, event_id=event_id)
        await session.rollback()
    async with case.session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsSource).where(
            NewsSource.external_id == source.external_id
        )) == 0
        assert await session.scalar(select(func.count()).select_from(NewsEvent).where(
            NewsEvent.event_key == key
        )) == 0
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.external_id == candidate.external_id
        )) == 0


async def test_concurrent_independent_sessions_reuse_one_item_identity(case):
    key, title = case.event("event")
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        event_id = await repo.ensure_event(key, title)
        await session.commit()

    candidate = case.item("item", source_id)
    barrier = asyncio.Barrier(2)

    async def worker():
        async with case.session() as session:
            connection_id = await session.scalar(select(func.connection_id()))
            await barrier.wait()
            item_id = await NewsRepository(session).upsert_item(candidate, event_id=event_id)
            await session.commit()
            return item_id, connection_id

    first, second = await asyncio.wait_for(
        asyncio.gather(worker(), worker()), timeout=15
    )
    assert first[0] == second[0]
    assert first[1] != second[1]
    async with case.session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.provider == "github", NewsItem.external_id == candidate.external_id
        )) == 1


async def test_failed_write_releases_session_connection_and_leaves_no_item(case):
    candidate = None
    async with case.session() as session:
        repo = NewsRepository(session)
        source_id = await repo.ensure_source(case.source("source"))
        candidate = case.item("item", source_id)
        with pytest.raises(IntegrityError):
            await repo.upsert_item(candidate, event_id=2**64 - 1)
    async with case.session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.external_id == candidate.external_id
        )) == 0
    assert case.engine.pool.checkedout() == 0
