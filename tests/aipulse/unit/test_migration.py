"""Inspect the migration without connecting to or changing MySQL."""

from importlib import import_module
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Column, Index, MetaData, Table, UniqueConstraint
from sqlalchemy.dialects import mysql

from aipulse.config import DatabaseSettings
from aipulse.models import Base


PROJECT = Path(__file__).resolve().parents[3]


def migration_script():
    ini = PROJECT / "alembic.ini"
    if not ini.exists():
        pytest.fail("S1-3 Alembic configuration is missing", pytrace=False)
    scripts = ScriptDirectory.from_config(Config(str(ini)))
    heads = scripts.get_heads()
    assert len(heads) == 1
    return scripts.get_revision("20261014_0001").module


def test_initial_revision_creates_exact_base_tables_without_live_connection(monkeypatch):
    script = migration_script()
    tables = {}

    class Recorder:
        def create_table(self, name, *elements, **kwargs):
            tables[name] = {element.name for element in elements if isinstance(element, Column)}

        def create_index(self, *args, **kwargs):
            pass

    monkeypatch.setattr(script, "op", Recorder())
    script.upgrade()

    assert set(tables) == set(Base.metadata.tables)
    for name, columns in tables.items():
        expected = set(Base.metadata.tables[name].columns.keys())
        if name == "news_event":
            expected -= {
                "score_reason", "rule_version", "summary_json", "summary_model",
                "summary_prompt_version", "summary_input_fingerprint", "summary_generated_at",
            }
        if name == "research_task":
            expected -= {"result_json"}
        if name == "research_evidence":
            expected -= {"metadata_json"}
        assert columns == expected


def test_initial_revision_has_the_reviewed_news_identity_constraints(monkeypatch):
    """The first revision must enforce the same identities as its ORM models."""
    script = migration_script()
    unique_by_table = {}

    class Recorder:
        def create_table(self, name, *elements, **_kwargs):
            table = Table(name, MetaData(), *elements)
            unique_by_table[name] = {
                tuple(column.name for column in element.columns)
                for element in table.constraints
                if isinstance(element, UniqueConstraint)
            }

        def create_index(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(script, "op", Recorder())
    script.upgrade()

    assert ("provider", "external_id") in unique_by_table["news_source"]
    assert ("source_type", "name") not in unique_by_table["news_source"]
    assert ("provider", "external_id") in unique_by_table["news_item"]
    assert ("source_id", "external_id") not in unique_by_table["news_item"]
    assert ("content_hash",) not in unique_by_table["news_item"]
    assert ("event_key",) in unique_by_table["news_event"]
    assert ("brief_date", "timezone") in unique_by_table["daily_brief"]
    assert ("brief_date",) not in unique_by_table["daily_brief"]


def test_all_seven_migration_tables_match_orm_columns_constraints_and_indexes(monkeypatch):
    """Execute only an operation recorder; no Engine or database is created."""
    script = migration_script()
    scripts = ScriptDirectory.from_config(Config(str(PROJECT / "alembic.ini")))
    scoring_script = scripts.get_revision("20261016_0002").module
    summary_script = scripts.get_revision("20261021_0003").module
    research_script = scripts.get_revision("20261021_0004").module
    captured = MetaData()

    class Recorder:
        def create_table(self, name, *elements, **kwargs):
            Table(name, captured, *elements, **kwargs)

        def create_index(self, name, table_name, columns, **kwargs):
            Index(name, *(captured.tables[table_name].c[column] for column in columns), **kwargs)

        def add_column(self, table_name, column):
            captured.tables[table_name].append_column(column)

    recorder = Recorder()
    monkeypatch.setattr(script, "op", recorder)
    monkeypatch.setattr(scoring_script, "op", recorder)
    monkeypatch.setattr(summary_script, "op", recorder)
    monkeypatch.setattr(research_script, "op", recorder)
    script.upgrade()
    scoring_script.upgrade()
    summary_script.upgrade()
    research_script.upgrade()

    assert set(captured.tables) == set(Base.metadata.tables)
    assert {"fetched_at", "metadata_json", "canonical_url_hash"} <= set(
        captured.tables["news_item"].columns.keys()
    )

    def signature(table):
        columns = {
            column.name: (
                str(column.type.compile(dialect=mysql.dialect())),
                column.nullable,
                column.primary_key,
                tuple(sorted(foreign_key.target_fullname for foreign_key in column.foreign_keys)),
            )
            for column in table.columns
        }
        uniques = {
            (constraint.name, tuple(column.name for column in constraint.columns))
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
        }
        checks = {
            (constraint.name, str(constraint.sqltext))
            for constraint in table.constraints if isinstance(constraint, CheckConstraint)
        }
        indexes = {
            (index.name, tuple(column.name for column in index.columns), index.unique)
            for index in table.indexes
        }
        options = tuple(
            (key, table.dialect_options["mysql"][key])
            for key in ("engine", "charset", "collate")
        )
        return columns, uniques, checks, indexes, options

    for table_name in Base.metadata.tables:
        assert signature(captured.tables[table_name]) == signature(Base.metadata.tables[table_name])


def test_migration_target_rejects_wrong_account_and_cross_database():
    try:
        module = import_module("aipulse.storage.migrations")
    except ModuleNotFoundError as exc:
        pytest.fail(f"S1-3 migration target guard is missing: {exc.name}", pytrace=False)
    settings = DatabaseSettings(
        _env_file=None,
        db_password="fake-dev-secret",
        test_db_name="aipulse_test_db",
        test_db_user="aipulse_test",
        test_db_password="fake-test-secret",
    )

    with pytest.raises(ValueError):
        module.migration_url(settings, target="dev", user="aipulse_app", password="x")
    with pytest.raises(ValueError):
        module.migration_url(
            settings, target="test", user="aipulse_test_migrate", password="x",
            database="aipulse_db",
        )
    unsafe_runtime = settings.model_copy(update={"db_user": "other_app"})
    with pytest.raises(ValueError):
        module.migration_url(
            unsafe_runtime, target="dev", user="aipulse_migrate", password="x"
        )

    url = module.migration_url(
        settings, target="test", user="aipulse_test_migrate", password="x@:/#%"
    )
    assert url.database == "aipulse_test_db"
    assert url.username == "aipulse_test_migrate"
    assert url.password == "x@:/#%"
    assert url.query["charset"] == "utf8mb4"
