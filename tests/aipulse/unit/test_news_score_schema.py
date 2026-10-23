"""Offline contract for the incremental event-scoring columns."""

from hashlib import sha256
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects import mysql

from aipulse.models.entities import NewsEvent
from aipulse.news.score import ScoreResult


ROOT = Path(__file__).resolve().parents[3]
INITIAL = "20261014_0001"
SCORING = "20261016_0002"
INITIAL_SHA256 = "6f4b91d13b2e954898275c533fb805efe8722ba9f9360df35c9d96a980e5e0fd"


def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def scoring_revision():
    revision = scripts().get_revision(SCORING)
    assert revision is not None
    return revision.module


@pytest.mark.parametrize("field,length", [("score_reason", 1000), ("rule_version", 100)])
def test_scoring_columns_are_nullable_bounded_strings_without_default(field, length):
    column = NewsEvent.__table__.c[field]
    assert isinstance(column.type, String)
    assert column.type.compile(dialect=mysql.dialect()) == f"VARCHAR({length})"
    assert column.nullable is True
    assert column.default is None and column.server_default is None
    assert field in ScoreResult.model_fields


@pytest.mark.parametrize("field,length", [("score_reason", 1000), ("rule_version", 100)])
def test_score_result_rejects_values_exceeding_storage_length(field, length):
    data = dict(event_key="a" * 64, category="other", importance_score=0,
                score_reason="reason", rule_version="experimental-v1:123",
                signals=(), unknown_signals=())
    data[field] = "x" * (length + 1)
    with pytest.raises(ValidationError):
        ScoreResult(**data)


def test_old_unscored_event_requires_no_fabricated_score_fields():
    row = NewsEvent(event_key="a" * 64, title="Before scoring")
    assert row.category is None and row.importance_score is None
    assert row.score_reason is None and row.rule_version is None


def test_reviewed_initial_file_is_unchanged_and_chain_has_one_head():
    initial = ROOT / "alembic" / "versions" / "20261014_0001_v1_base.py"
    assert sha256(initial.read_bytes()).hexdigest() == INITIAL_SHA256
    directory = scripts()
    assert directory.get_heads() == [SCORING]
    assert len(list(directory.walk_revisions())) == 2
    assert directory.get_revision(SCORING).down_revision == INITIAL
    assert directory.get_revision(INITIAL).down_revision is None


def test_incremental_upgrade_only_adds_the_two_nullable_event_columns(monkeypatch):
    revision = scoring_revision()
    actions = []

    class Recorder:
        def add_column(self, table_name, column):
            actions.append((table_name, column))

        def __getattr__(self, name):
            raise AssertionError(f"Unexpected migration operation: {name}")

    monkeypatch.setattr(revision, "op", Recorder())
    revision.upgrade()
    assert [(table, column.name) for table, column in actions] == [
        ("news_event", "score_reason"), ("news_event", "rule_version"),
    ]
    for table, column in actions:
        orm_column = NewsEvent.__table__.c[column.name]
        assert column.type.compile(dialect=mysql.dialect()) == orm_column.type.compile(
            dialect=mysql.dialect()
        )
        assert column.nullable is True and orm_column.nullable is True
        assert column.default is None and column.server_default is None


def test_existing_event_identity_and_relationships_are_unchanged():
    table = NewsEvent.__table__
    assert table.c.id.primary_key
    assert table.c.event_key.nullable is False
    assert any(
        constraint.name == "uq_news_event_event_key"
        and tuple(column.name for column in constraint.columns) == ("event_key",)
        for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
    )
    assert {key.target_fullname for key in table.c.id.foreign_keys} == set()
    assert table.c.category.type.compile(dialect=mysql.dialect()) == "VARCHAR(32)"
    assert table.c.importance_score.type.compile(dialect=mysql.dialect()) == "TINYINT UNSIGNED"
