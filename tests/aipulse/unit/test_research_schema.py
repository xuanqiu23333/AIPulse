"""Offline schema/revision contracts, including backward-compatible news gates."""

import asyncio
import hashlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import mysql

from aipulse.models.entities import ResearchEvidence, ResearchTask
from aipulse.news.manual_run import _REQUIRED_TABLES, _SCORING_COLUMNS, _SUMMARY_COLUMNS
from aipulse.research.repository import research_schema_ready


ROOT = Path(__file__).parents[3]


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class Engine:
    def __init__(self, revision="20261021_0004", *, identity="aipulse_app@localhost", fields=None):
        self.revision = revision
        self.identity = identity
        self.fields = fields if fields is not None else [
            ("research_task", "result_json", "json", "YES"),
            ("research_evidence", "metadata_json", "json", "YES"),
        ]

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, statement):
        return "aipulse_db" if "DATABASE()" in str(statement) else self.identity

    async def execute(self, statement):
        sql = str(statement).lower()
        if "information_schema.tables" in sql:
            return Result(list(_REQUIRED_TABLES))
        if "alembic_version" in sql:
            return Result([self.revision])
        if "table_name = 'news_event'" in sql:
            return Result([(key, *shape) for key, shape in (_SCORING_COLUMNS | _SUMMARY_COLUMNS).items()])
        if "information_schema.columns" in sql:
            return Result(self.fields)
        raise AssertionError(sql)


def test_research_requires_exact_0004_and_both_nullable_json_fields():
    assert asyncio.run(research_schema_ready(Engine()))
    assert not asyncio.run(research_schema_ready(Engine("20261021_0003")))
    assert not asyncio.run(research_schema_ready(Engine(fields=[])))
    assert not asyncio.run(research_schema_ready(Engine(identity="aipulse_migrate@localhost")))
    assert not asyncio.run(research_schema_ready(Engine(fields=[
        ("research_task", "result_json", "text", "YES"),
        ("research_evidence", "metadata_json", "json", "YES"),
    ])))


def test_0004_only_adds_two_nullable_json_columns_matching_orm(monkeypatch):
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["20261021_0004"]
    revision = scripts.get_revision("20261021_0004").module
    assert revision.down_revision == "20261021_0003"
    actions = []

    class Recorder:
        def add_column(self, table, column):
            actions.append((table, column))

        def __getattr__(self, name):
            raise AssertionError(f"Unexpected migration operation: {name}")

    monkeypatch.setattr(revision, "op", Recorder())
    revision.upgrade()
    assert [(table, column.name) for table, column in actions] == [
        ("research_task", "result_json"), ("research_evidence", "metadata_json"),
    ]
    for table, column in actions:
        model = ResearchTask if table == "research_task" else ResearchEvidence
        assert column.nullable and column.default is None and column.server_default is None
        assert column.type.compile(dialect=mysql.dialect()) == "JSON"
        assert model.__table__.c[column.name].nullable
    assert ResearchTask.result_json.property.deferred
    assert ResearchEvidence.metadata_json.property.deferred


def test_applied_revisions_remain_byte_identical():
    hashes = {
        "20261014_0001_v1_base.py": "6F4B91D13B2E954898275C533FB805EFE8722BA9F9360DF35C9D96A980E5E0FD",
        "20261016_0002_news_event_scoring.py": "196162A7D9F328FD1B2942F0C813249FE86A90FA3660DECFFDAB54607EDB9BB2",
        "20261021_0003_news_summary_cache.py": "28AA91DC9F365D84500D7AD229296D1ECD4737B751423DA89CF880FAC0351FCC",
    }
    for name, expected in hashes.items():
        assert hashlib.sha256((ROOT / "alembic" / "versions" / name).read_bytes()).hexdigest().upper() == expected
