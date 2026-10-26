"""The seven V1 MySQL mappings are checked without touching a database."""

from datetime import date, datetime, timezone
from importlib import import_module

import pytest
from sqlalchemy import CheckConstraint, DateTime, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable


def models():
    try:
        return import_module("aipulse.models")
    except ModuleNotFoundError as exc:
        pytest.fail(f"S1-2 models are missing: {exc.name}", pytrace=False)


def test_metadata_has_exact_seven_v1_tables():
    metadata = models().Base.metadata
    assert set(metadata.tables) == {
        "news_source", "news_item", "news_event", "daily_brief",
        "research_task", "research_evidence", "agent_run_log",
    }


def test_named_unique_constraints_and_lookup_indexes_exist():
    metadata = models().Base.metadata
    uniques = {
        table: {
            tuple(column.name for column in constraint.columns)
            for constraint in metadata.tables[table].constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        for table in metadata.tables
    }
    assert ("provider", "external_id") in uniques["news_source"]
    assert ("source_type", "name") not in uniques["news_source"]
    assert ("provider", "external_id") in uniques["news_item"]
    assert ("content_hash",) not in uniques["news_item"]
    assert ("source_id", "external_id") not in uniques["news_item"]
    assert ("brief_date", "timezone") in uniques["daily_brief"]
    assert ("brief_date",) not in uniques["daily_brief"]
    assert ("task_id",) in uniques["research_task"]
    item_indexes = {
        tuple(column.name for column in index.columns)
        for index in metadata.tables["news_item"].indexes
    }
    assert ("published_at",) in item_indexes
    assert ("source_id",) in item_indexes


def test_news_identity_columns_and_unique_keys_support_reingestion():
    """A stable source/item/event identity survives mutable news content."""
    metadata = models().Base.metadata
    expected = {
        "news_source": {"provider", "external_id"},
        "news_item": {"provider", "external_id"},
        "news_event": {"event_key"},
    }
    for table_name, columns in expected.items():
        assert columns <= set(metadata.tables[table_name].columns.keys())

    def unique_columns(table_name):
        return {
            tuple(column.name for column in constraint.columns)
            for constraint in metadata.tables[table_name].constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }

    assert ("provider", "external_id") in unique_columns("news_source")
    assert ("provider", "external_id") in unique_columns("news_item")
    assert ("source_id", "external_id") not in unique_columns("news_item")
    assert ("content_hash",) not in unique_columns("news_item")
    assert ("event_key",) in unique_columns("news_event")
    for table_name, column_name in (
        ("news_source", "provider"),
        ("news_source", "external_id"),
        ("news_item", "provider"),
        ("news_item", "external_id"),
        ("news_event", "event_key"),
    ):
        assert not metadata.tables[table_name].c[column_name].nullable


def test_source_display_name_is_not_a_second_database_identity():
    source = models().Base.metadata.tables["news_source"]
    ddl = str(CreateTable(source).compile(dialect=mysql.dialect()))
    assert "UNIQUE (provider, external_id)" in ddl
    assert "UNIQUE (source_type, name)" not in ddl
    assert not any(
        index.unique and tuple(column.name for column in index.columns) == ("source_type", "name")
        for index in source.indexes
    )


def test_daily_brief_identity_includes_required_iana_timezone():
    module = models()
    brief = module.Base.metadata.tables["daily_brief"]
    column = brief.c.timezone
    assert column.type.length == 64
    assert column.nullable is False
    assert column.default is None and column.server_default is None
    ddl = str(CreateTable(brief).compile(dialect=mysql.dialect()))
    assert "UNIQUE (brief_date, timezone)" in ddl
    assert "UNIQUE (brief_date)" not in ddl
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_daily_brief_timezone_nonempty"
        for constraint in brief.constraints
    )
    same_day = date(2026, 10, 15)
    shanghai = module.DailyBrief(brief_date=same_day, timezone="Asia/Shanghai")
    utc = module.DailyBrief(brief_date=same_day, timezone="UTC")
    assert (shanghai.brief_date, shanghai.timezone) != (utc.brief_date, utc.timezone)


@pytest.mark.parametrize("value", ["", "   ", "Invalid/Timezone", " Asia/Shanghai "])
def test_daily_brief_rejects_blank_or_invalid_timezone(value):
    with pytest.raises(ValueError):
        models().DailyBrief(brief_date=date(2026, 10, 15), timezone=value)


def test_all_and_only_utc_datetime_columns_compile_to_mysql_microseconds():
    metadata = models().Base.metadata
    expected = {
        "news_source": {"created_at", "updated_at"},
        "news_event": {"created_at", "updated_at", "summary_generated_at"},
        "news_item": {"published_at", "fetched_at", "created_at", "updated_at"},
        "daily_brief": {"generated_at"},
        "research_task": {"started_at", "finished_at", "created_at", "updated_at"},
        "research_evidence": {"created_at"},
        "agent_run_log": {"created_at"},
    }
    for table_name, names in expected.items():
        columns = metadata.tables[table_name].c
        assert {column.name for column in columns if isinstance(column.type, DateTime)} == names
        for name in names:
            assert isinstance(columns[name].type, mysql.DATETIME)
            assert columns[name].type.compile(dialect=mysql.dialect()) == "DATETIME(6)"
    assert metadata.tables["daily_brief"].c.brief_date.type.compile(dialect=mysql.dialect()) == "DATE"


def test_item_mysql_ddl_preserves_cross_config_identity_and_distinct_sources():
    """One platform ID is unique globally; equal content is not an identity."""
    item = models().Base.metadata.tables["news_item"]
    ddl = str(CreateTable(item).compile(dialect=mysql.dialect()))
    assert "UNIQUE (provider, external_id)" in ddl
    assert "UNIQUE (source_id, external_id)" not in ddl
    assert "UNIQUE (content_hash)" not in ddl
    assert item.c.source_id.foreign_keys
    assert item.c.content_hash.nullable is False


def test_news_item_has_explicit_utc_observation_and_bounded_metadata_columns():
    item = models().Base.metadata.tables["news_item"]
    assert isinstance(item.c.fetched_at.type, mysql.DATETIME)
    assert item.c.fetched_at.type.fsp == 6
    assert item.c.fetched_at.nullable is False
    assert item.c.fetched_at.default is None
    assert item.c.fetched_at.server_default is None
    assert isinstance(item.c.metadata_json.type, mysql.JSON)
    assert item.c.metadata_json.nullable is False
    assert item.c.metadata_json.default is None
    assert item.c.metadata_json.server_default is None
    assert isinstance(item.c.canonical_url_hash.type, mysql.CHAR)
    assert item.c.canonical_url_hash.type.length == 64
    assert item.c.canonical_url_hash.nullable is False
    assert {tuple(column.name for column in index.columns) for index in item.indexes} >= {
        ("canonical_url_hash",),
    }
    assert item.c.fetched_at is not item.c.updated_at
    ddl = str(CreateTable(item).compile(dialect=mysql.dialect()))
    assert "fetched_at DATETIME(6) NOT NULL" in ddl
    assert "metadata_json JSON NOT NULL" in ddl


def test_news_item_existing_identity_and_event_relation_survive_snapshot_columns():
    item = models().Base.metadata.tables["news_item"]
    unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in item.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("provider", "external_id") in unique
    assert ("content_hash",) not in unique
    assert ("canonical_url_hash",) not in unique
    assert {key.target_fullname for key in item.c.source_id.foreign_keys} == {"news_source.id"}
    assert {key.target_fullname for key in item.c.event_id.foreign_keys} == {"news_event.id"}


def test_relationships_have_real_foreign_keys():
    module = models()
    metadata = module.Base.metadata
    foreign_keys = {
        table: {str(key.target_fullname) for key in metadata.tables[table].foreign_keys}
        for table in metadata.tables
    }
    assert "news_source.id" in foreign_keys["news_item"]
    assert "news_event.id" in foreign_keys["news_item"]
    assert "research_task.id" in foreign_keys["research_evidence"]
    assert "research_task.task_id" in foreign_keys["agent_run_log"]
    assert "items" in inspect(module.NewsSource).relationships
    assert "evidence" in inspect(module.ResearchTask).relationships


def test_mysql_ddl_uses_innodb_utf8mb4_and_native_json():
    metadata = models().Base.metadata
    source = metadata.tables["news_source"]
    ddl = str(CreateTable(source).compile(dialect=mysql.dialect()))
    assert "ENGINE=InnoDB" in ddl
    assert "CHARSET=utf8mb4" in ddl
    assert isinstance(source.c.config_json.type, mysql.JSON)
    assert isinstance(metadata.tables["agent_run_log"].c.payload_json.type, mysql.JSON)


def test_timestamp_default_represents_utc_without_mysql_timezone_ambiguity():
    source = models().Base.metadata.tables["news_source"]
    default = source.c.created_at.default.arg(None)
    assert isinstance(default, datetime)
    assert default.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - default).total_seconds()) < 5
