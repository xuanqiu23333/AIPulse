"""Offline SQLAlchemy contract for persisting one event score."""

import asyncio
from hashlib import sha256
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError

from aipulse.news.repository import NewsRepository
from aipulse.news.score import ScoreResult


EVENT_KEY = sha256(b"scored event").hexdigest()


def score(*, unchecked=False, **changes):
    values = dict(
        event_key=EVENT_KEY, category="paper", importance_score=42,
        score_reason="Confirmed paper source", rule_version="experimental-v1:abc",
        signals=(), unknown_signals=(),
    )
    values.update(changes)
    return (ScoreResult.model_construct(**values) if unchecked else ScoreResult(**values))


class Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class InertSession:
    def __init__(self, row=None, error=None):
        self.row = row if row is not None else SimpleNamespace(
            id=7, event_key=EVENT_KEY, category=None, importance_score=None,
            score_reason=None, rule_version=None,
        )
        self.error = error
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.error is not None and statement.is_update:
            raise self.error
        return Result(self.row) if statement.is_select else Result(None)

    async def commit(self):
        raise AssertionError("Repository must not commit")

    async def rollback(self):
        raise AssertionError("Caller owns rollback")


def run(session, result=None, *, event_id=7):
    return asyncio.run(NewsRepository(session).save_event_score(
        event_id=event_id, score=result or score(),
    ))


def compiled(statement):
    return str(statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


def test_score_fields_are_one_scoped_update_after_locking_read():
    session = InertSession()
    assert run(session) is True
    assert len(session.statements) == 2
    read, write = map(compiled, session.statements)
    assert "FROM news_event" in read and "news_event.id = 7" in read
    assert read.endswith("FOR UPDATE")
    assert write.startswith("UPDATE news_event SET ")
    assert "category='paper'" in write and "importance_score=42" in write
    assert "score_reason='Confirmed paper source'" in write
    assert "rule_version='experimental-v1:abc'" in write
    assert "WHERE news_event.id = 7 AND news_event.event_key = " in write
    assert "event_key=" not in write.split(" WHERE ")[0]
    assert "news_item" not in write and "title=" not in write and "url=" not in write


@pytest.mark.parametrize("value", [0, 100])
def test_score_endpoints_are_accepted(value):
    session = InertSession()
    assert run(session, score(importance_score=value)) is True
    assert "importance_score=" in compiled(session.statements[1])


@pytest.mark.parametrize("changes", [
    {"importance_score": -1}, {"importance_score": 101},
    {"importance_score": True},
    {"category": "unapproved"}, {"score_reason": "x" * 1001},
    {"rule_version": "x" * 101},
])
def test_invalid_score_is_rejected_before_any_sql(changes):
    session = InertSession()
    with pytest.raises((ValueError, ValidationError)):
        run(session, score(unchecked=True, **changes))
    assert session.statements == []


@pytest.mark.parametrize("event_id", [0, -1, True])
def test_invalid_event_id_is_rejected_before_any_sql(event_id):
    session = InertSession()
    with pytest.raises(ValueError):
        run(session, event_id=event_id)
    assert session.statements == []


def test_missing_event_fails_instead_of_being_treated_as_idempotent():
    session = InertSession()
    session.row = None
    with pytest.raises(LookupError):
        run(session)
    assert len(session.statements) == 1


def test_score_event_key_must_match_persisted_event():
    session = InertSession()
    session.row.event_key = sha256(b"other event").hexdigest()
    with pytest.raises(ValueError):
        run(session)
    assert len(session.statements) == 1


def test_resolved_primary_key_must_match_requested_event():
    session = InertSession()
    session.row.id = 8
    with pytest.raises(ValueError):
        run(session)
    assert len(session.statements) == 1


def test_identical_score_is_idempotent_without_write():
    session = InertSession(row=SimpleNamespace(
        id=7, event_key=EVENT_KEY, category="paper", importance_score=42,
        score_reason="Confirmed paper source", rule_version="experimental-v1:abc",
    ))
    assert run(session) is False
    assert run(session) is False
    assert all(statement.is_select for statement in session.statements)


def test_write_failure_propagates_to_transaction_owner():
    error = OperationalError("UPDATE", {}, Exception("lock wait timeout"))
    session = InertSession(error=error)
    with pytest.raises(OperationalError):
        run(session)
    assert len(session.statements) == 2


def test_existing_repository_methods_remain_available():
    assert callable(NewsRepository.ensure_source)
    assert callable(NewsRepository.ensure_event)
    assert callable(NewsRepository.upsert_item)
