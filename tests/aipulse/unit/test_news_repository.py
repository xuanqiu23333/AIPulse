"""Repository orchestration against an inert AsyncSession shaped fake."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError, NoResultFound, OperationalError

from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.types import NewsCandidate


NOW = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)
EVENT_KEY = sha256(b"event identity").hexdigest()


def repository():
    try:
        return import_module("aipulse.news.repository")
    except ModuleNotFoundError as exc:
        pytest.fail(f"News repository is missing: {exc.name}", pytrace=False)


def source(*, external_id="acme/agent", name="Acme releases"):
    return repository().SourceConfiguration(
        provider="github", external_id=external_id, source_type="github_release", name=name,
    )


def item(*, provider="github", external_id="701", source_id=3):
    candidate = NewsCandidate(
        provider=provider, external_id=external_id,
        original_url="https://github.com/acme/agent/releases/tag/v1",
        title="Release v1", raw_excerpt="Notes", published_at=NOW,
        fetched_at=NOW, metadata={"tag_name": "v1", "prerelease": False},
    )
    return deduplicate_batch([normalize_candidate(candidate, source_id)])[0]


class Result:
    def __init__(self, row=None):
        self.row = row

    def one(self):
        if self.row is None:
            raise NoResultFound("No row was found when one was required")
        return self.row


class InertSession:
    """Record SQL and supply IDs/rows; never open an Engine or connection."""

    def __init__(self, *, ids=(), rows=(), error=None):
        self.ids = list(ids)
        self.rows = list(rows)
        self.error = error
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.error is not None:
            raise self.error
        return Result(self.rows.pop(0) if statement.is_select and self.rows else None)

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.ids.pop(0)

    async def commit(self):
        raise AssertionError("Repository must not commit")

    async def rollback(self):
        raise AssertionError("Caller owns rollback")


def test_same_source_config_reuses_database_identity_without_commit():
    session = InertSession(
        ids=[11, 11],
        rows=[SimpleNamespace(provider="github", external_id="acme/agent")] * 2,
    )
    repo = repository().NewsRepository(session)
    assert asyncio.run(repo.ensure_source(source())) == 11
    assert asyncio.run(repo.ensure_source(source())) == 11
    assert len(session.statements) == 6


def test_resolved_source_identity_mismatch_is_reported():
    session = InertSession(ids=[11], rows=[SimpleNamespace(provider="github", external_id="other/repo")])
    with pytest.raises(repository().SourceIdentityConflict):
        asyncio.run(repository().NewsRepository(session).ensure_source(source()))


def test_same_event_key_reuses_id_and_does_not_overwrite_title():
    session = InertSession(ids=[41, 41])
    repo = repository().NewsRepository(session)
    assert asyncio.run(repo.ensure_event(EVENT_KEY, "First title")) == 41
    assert asyncio.run(repo.ensure_event(EVENT_KEY, "Later title")) == 41
    assert len(session.statements) == 4


def test_repeated_platform_item_reuses_one_id():
    session = InertSession(
        ids=[71, 71],
        rows=[SimpleNamespace(provider="github", external_id="701", event_id=41)] * 2,
    )
    repo = repository().NewsRepository(session)
    assert asyncio.run(repo.upsert_item(item(), event_id=41)) == 71
    assert asyncio.run(repo.upsert_item(item(source_id=9), event_id=41)) == 71


def test_item_writeback_locks_current_row_by_last_insert_id_in_same_session():
    session = InertSession(
        ids=[71], rows=[SimpleNamespace(provider="github", external_id="701", event_id=41)],
    )
    assert asyncio.run(repository().NewsRepository(session).upsert_item(item(), event_id=41)) == 71

    assert len(session.statements) == 3
    upsert, last_id_query, verification = session.statements
    assert "ON DUPLICATE KEY UPDATE" in str(upsert.compile(dialect=mysql.dialect()))
    assert "LAST_INSERT_ID()" in str(last_id_query.compile(dialect=mysql.dialect()))
    verification_sql = str(verification.compile(
        dialect=mysql.dialect(), compile_kwargs={"literal_binds": True},
    ))
    assert "WHERE news_item.id = 71" in verification_sql
    assert verification_sql.endswith("FOR UPDATE")


def test_item_writeback_still_rejects_resolved_identity_mismatch():
    session = InertSession(
        ids=[71], rows=[SimpleNamespace(provider="hn", external_id="701", event_id=41)],
    )
    with pytest.raises(repository().ItemIdentityConflict):
        asyncio.run(repository().NewsRepository(session).upsert_item(item(), event_id=41))


def test_item_writeback_does_not_assume_success_for_missing_primary_key():
    session = InertSession(ids=[0], rows=[])
    with pytest.raises(NoResultFound):
        asyncio.run(repository().NewsRepository(session).upsert_item(item(), event_id=41))


def test_item_writeback_does_not_hide_lock_errors():
    class LockFailingSession(InertSession):
        async def execute(self, statement):
            if statement.is_select:
                self.statements.append(statement)
                raise OperationalError("SELECT", {}, Exception("lock wait timeout"))
            return await super().execute(statement)

    session = LockFailingSession(ids=[71])
    with pytest.raises(OperationalError):
        asyncio.run(repository().NewsRepository(session).upsert_item(item(), event_id=41))


def test_other_provider_with_same_external_id_remains_a_distinct_item():
    session = InertSession(ids=[71, 72], rows=[
        SimpleNamespace(provider="github", external_id="701", event_id=41),
        SimpleNamespace(provider="hn", external_id="701", event_id=41),
    ])
    repo = repository().NewsRepository(session)
    assert asyncio.run(repo.upsert_item(item(), event_id=41)) == 71
    assert asyncio.run(repo.upsert_item(item(provider="hn"), event_id=41)) == 72


def test_two_original_items_can_link_to_one_existing_event():
    session = InertSession(ids=[41, 71, 72], rows=[
        SimpleNamespace(provider="github", external_id="701", event_id=41),
        SimpleNamespace(provider="hn", external_id="702", event_id=41),
    ])
    repo = repository().NewsRepository(session)
    event_id = asyncio.run(repo.ensure_event(EVENT_KEY, "A release"))
    assert asyncio.run(repo.upsert_item(item(), event_id=event_id)) == 71
    assert asyncio.run(repo.upsert_item(item(provider="hn", external_id="702"), event_id=event_id)) == 72


def test_existing_event_link_mismatch_raises_instead_of_remerging():
    session = InertSession(ids=[71], rows=[SimpleNamespace(
        provider="github", external_id="701", event_id=99,
    )])
    with pytest.raises(repository().EventLinkConflict):
        asyncio.run(repository().NewsRepository(session).upsert_item(item(), event_id=41))


def test_database_integrity_error_is_propagated_for_caller_rollback():
    error = IntegrityError("INSERT", {}, Exception("duplicate key"))
    session = InertSession(error=error)
    with pytest.raises(IntegrityError):
        asyncio.run(repository().NewsRepository(session).ensure_event(EVENT_KEY, "A release"))


def test_repository_requires_injected_session_and_never_constructs_engine():
    with pytest.raises(TypeError):
        repository().NewsRepository()
    session = InertSession(ids=[41])
    asyncio.run(repository().NewsRepository(session).ensure_event(EVENT_KEY, "A release"))
    assert all(not statement.is_select for statement in session.statements if statement.is_insert)
