"""Offline contract for a locked, complete persisted scoring snapshot."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql

from aipulse.news.repository import NewsRepository
from aipulse.news.score import score_event


EVENT_KEY = sha256(b"shared paper").hexdigest()
STORED_AT = datetime(2026, 10, 16, 9, 30, 12, 123456)


def item(provider, external_id, *, metadata=None, published_at=STORED_AT):
    return SimpleNamespace(
        provider=provider, external_id=external_id,
        title="New AI paper", published_at=published_at,
        fetched_at=STORED_AT, metadata_json={} if metadata is None else metadata,
    )


class Result:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = rows

    def one_or_none(self):
        return self.row

    def all(self):
        return list(self.rows)


class InertSession:
    def __init__(self, *, event=None, items=None):
        self.event = event if event is not None else SimpleNamespace(id=7, event_key=EVENT_KEY)
        self.items = list(items if items is not None else [
            item("arxiv", "2601.12345", metadata={"version": "v2"}),
            item("hn", "82", metadata={"score": 120}),
        ])
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return Result(row=self.event)
        return Result(rows=self.items)

    async def commit(self):
        raise AssertionError("Repository must not commit")


def load(session, event_id=7):
    return asyncio.run(NewsRepository(session).load_event_score_snapshot(event_id))


def sql(statement):
    return str(statement.compile(
        dialect=mysql.dialect(), compile_kwargs={"literal_binds": True},
    ))


def test_locked_read_selects_event_then_all_persisted_items_by_event_id():
    session = InertSession()
    snapshot = load(session)
    assert snapshot.event_key == EVENT_KEY
    assert {(part.provider, part.external_id) for part in snapshot.items} == {
        ("arxiv", "2601.12345"), ("hn", "82"),
    }
    assert len(session.statements) == 2
    event_sql, items_sql = map(sql, session.statements)
    assert "FROM news_event" in event_sql and "news_event.id = 7" in event_sql
    assert event_sql.endswith("FOR UPDATE")
    assert "FROM news_item" in items_sql and "news_item.event_id = 7" in items_sql
    assert items_sql.endswith("FOR UPDATE")


def test_naive_mysql_datetimes_become_aware_utc_without_changing_instant():
    snapshot = load(InertSession())
    assert all(part.fetched_at == STORED_AT.replace(tzinfo=timezone.utc)
               for part in snapshot.items)
    assert all(part.published_at == STORED_AT.replace(tzinfo=timezone.utc)
               for part in snapshot.items)


def test_missing_hn_metric_remains_unknown_in_real_scoring_engine():
    snapshot = load(InertSession(items=[item("hn", "82", metadata={})]))
    result = score_event(snapshot, as_of=STORED_AT.replace(tzinfo=timezone.utc))
    assert "hn_score" in result.unknown_signals
    assert all(signal.name != "community" for signal in result.signals)


def test_stored_hn_metric_is_used_without_inventing_a_new_one():
    snapshot = load(InertSession())
    result = score_event(snapshot, as_of=STORED_AT.replace(tzinfo=timezone.utc))
    assert any(signal.name == "community" and signal.points == 10
               for signal in result.signals)


def test_missing_event_is_not_scored():
    session = InertSession()
    session.event = None
    with pytest.raises(LookupError):
        load(session)
    assert len(session.statements) == 1


def test_wrong_primary_key_is_rejected_before_reading_items():
    session = InertSession(event=SimpleNamespace(id=8, event_key=EVENT_KEY))
    with pytest.raises(ValueError):
        load(session)
    assert len(session.statements) == 1


def test_event_without_persisted_items_is_rejected():
    with pytest.raises(ValueError):
        load(InertSession(items=[]))


@pytest.mark.parametrize("bad_metadata", [None, [], {"score": object()}])
def test_invalid_stored_metadata_is_not_silently_replaced(bad_metadata):
    session = InertSession(items=[item("hn", "82", metadata=bad_metadata)])
    if bad_metadata is None:
        session.items[0].metadata_json = None
    with pytest.raises(ValueError):
        load(session)


def test_event_snapshot_does_not_open_new_session_or_commit():
    session = InertSession()
    assert load(session).event_key == EVENT_KEY
    assert len(session.statements) == 2
