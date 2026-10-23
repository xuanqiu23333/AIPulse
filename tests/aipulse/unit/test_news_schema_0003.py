"""Offline revision gates for existing collection and the new summary cache."""

import asyncio

from aipulse.news.manual_run import schema_ready


TABLES = {
    "agent_run_log", "alembic_version", "daily_brief", "news_event",
    "news_item", "news_source", "research_evidence", "research_task",
}
BASE_COLUMNS = {
    "category": ("varchar(32)", "YES"),
    "importance_score": ("tinyint unsigned", "YES"),
    "score_reason": ("varchar(1000)", "YES"),
    "rule_version": ("varchar(100)", "YES"),
}
SUMMARY_COLUMNS = {
    "summary_json": ("json", "YES"),
    "summary_model": ("varchar(100)", "YES"),
    "summary_prompt_version": ("varchar(100)", "YES"),
    "summary_input_fingerprint": ("char(64)", "YES"),
    "summary_generated_at": ("datetime(6)", "YES"),
}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows


class Engine:
    def __init__(self, revision, columns):
        self.revision = revision
        self.columns = columns

    def connect(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def scalar(self, statement):
        return {
            "SELECT DATABASE()": "aipulse_db",
            "SELECT CURRENT_USER()": "aipulse_app@localhost",
        }[str(statement)]

    async def execute(self, statement):
        query = str(statement)
        if "information_schema.tables" in query:
            return Result(list(TABLES))
        if "alembic_version" in query:
            return Result([self.revision])
        if "information_schema.columns" in query:
            return Result([(name, *shape) for name, shape in self.columns.items()])
        raise AssertionError(query)


def test_collection_accepts_exact_0002_and_0003_and_summary_requires_0003():
    old = Engine("20261016_0002", BASE_COLUMNS)
    new = Engine("20261021_0003", BASE_COLUMNS | SUMMARY_COLUMNS)
    assert asyncio.run(schema_ready(old, require_app_identity=True))
    assert asyncio.run(schema_ready(new, require_app_identity=True))
    assert not asyncio.run(schema_ready(old, require_app_identity=True, require_summary_cache=True))
    assert asyncio.run(schema_ready(new, require_app_identity=True, require_summary_cache=True))


def test_partial_or_wrong_revision_is_rejected():
    assert not asyncio.run(schema_ready(Engine("20261021_0003", BASE_COLUMNS)))
    assert not asyncio.run(schema_ready(Engine("20261016_0002", BASE_COLUMNS | SUMMARY_COLUMNS)))
    assert not asyncio.run(schema_ready(Engine("unknown", BASE_COLUMNS | SUMMARY_COLUMNS)))
    drift = BASE_COLUMNS | SUMMARY_COLUMNS | {"summary_json": ("text", "YES")}
    assert not asyncio.run(schema_ready(Engine("20261021_0003", drift)))


def test_0004_preserves_news_and_summary_column_safety():
    engine = Engine("20261021_0004", BASE_COLUMNS | SUMMARY_COLUMNS)
    assert asyncio.run(schema_ready(engine, require_app_identity=True, require_summary_cache=True))
    assert not asyncio.run(schema_ready(Engine("20261021_0004", BASE_COLUMNS)))
