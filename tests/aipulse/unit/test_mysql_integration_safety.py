"""Offline checks for the opt-in MySQL integration test boundary."""

import asyncio
import os
import re
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import mysql


def safety():
    return import_module("tests_mysql.mysql.safety")


@pytest.mark.parametrize(
    ("cli_enabled", "env_value"),
    [(False, None), (False, "1"), (True, None), (True, ""), (True, "yes"), (True, "0")],
)
def test_write_permission_requires_both_explicit_switches(cli_enabled, env_value):
    with pytest.raises(safety().UnsafeMysqlIntegration):
        safety().require_write_permission(cli_enabled=cli_enabled, env_value=env_value)


def test_write_permission_accepts_only_exact_opt_in():
    safety().require_write_permission(cli_enabled=True, env_value="1")


class Result:
    def __init__(self, rows, query=None):
        self.rows = rows
        self.query = query

    def one(self):
        assert len(self.rows) == 1
        aliases = re.findall(r"\bAS\s+([a-z_]+)\b", self.query, flags=re.IGNORECASE)
        assert len(aliases) == 3
        return SimpleNamespace(**dict(zip(aliases, self.rows[0])))

    def scalars(self):
        return self

    def all(self):
        return [row[0] for row in self.rows]


class ReadOnlyConnection:
    def __init__(
        self, *, database="aipulse_test_db", account="aipulse_test@localhost",
        version="8.0.44", tables=None, revisions=("20261016_0002",),
    ):
        self.identity = [(database, account, version)]
        self.tables = [(name,) for name in (tables or safety().EXPECTED_TABLES)]
        self.revisions = [(revision,) for revision in revisions]
        self.queries = []

    async def execute(self, statement):
        query = str(statement)
        self.queries.append(query)
        if query.startswith("SELECT DATABASE()"):
            return Result(self.identity, query)
        if "information_schema.TABLES" in query:
            return Result(self.tables)
        if query == "SELECT version_num FROM alembic_version":
            return Result(self.revisions)
        raise AssertionError("Unexpected or mutating query")


def test_preflight_checks_the_supplied_connection_without_writes():
    connection = ReadOnlyConnection()
    asyncio.run(safety().verify_live_test_connection(connection))
    assert len(connection.queries) == 3
    assert all(query.startswith("SELECT ") for query in connection.queries)
    assert all(
        expression in connection.queries[0]
        for expression in ("DATABASE()", "CURRENT_USER()", "VERSION()")
    )


def test_preflight_identity_query_uses_safe_aliases_matching_result_fields():
    connection = ReadOnlyConnection()
    asyncio.run(safety().verify_live_test_connection(connection))
    assert re.findall(
        r"\bAS\s+([a-z_]+)\b", connection.queries[0], flags=re.IGNORECASE
    ) == ["db_name", "account_name", "server_version"]


def test_preflight_propagates_identity_query_failure_before_followup_queries():
    class FailingIdentityConnection(ReadOnlyConnection):
        async def execute(self, statement):
            self.queries.append(str(statement))
            raise RuntimeError("identity query failed")

    connection = FailingIdentityConnection()
    with pytest.raises(RuntimeError, match="identity query failed"):
        asyncio.run(safety().verify_live_test_connection(connection))
    assert len(connection.queries) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"database": "aipulse_db"},
        {"account": "root@localhost"},
        {"account": "aipulse_test_migrate@localhost"},
        {"version": "8.0.43"},
        {"tables": ("alembic_version",)},
        {"tables": (*(
            "news_source", "news_event", "news_item", "daily_brief", "research_task",
            "research_evidence", "agent_run_log", "alembic_version",
        ), "unreviewed_table")},
        {"revisions": ()},
        {"revisions": ("20261014_0001",)},
        {"revisions": ("wrong_revision",)},
    ],
)
def test_preflight_rejects_wrong_identity_schema_or_revision(overrides):
    connection = ReadOnlyConnection(**overrides)
    with pytest.raises(safety().UnsafeMysqlIntegration):
        asyncio.run(safety().verify_live_test_connection(connection))
    assert all(query.startswith("SELECT ") for query in connection.queries)


def test_cleanup_plan_is_scoped_to_recorded_uuid_identities():
    rows = safety().TestRows("aipulse-it-1234567890abcdef1234567890abcdef-")
    rows.record_source("aipulse-it-1234567890abcdef1234567890abcdef-source")
    rows.record_item("aipulse-it-1234567890abcdef1234567890abcdef-item")
    rows.record_event(
        "a" * 64, "aipulse-it-1234567890abcdef1234567890abcdef-event"
    )
    statements = rows.cleanup_statements()
    assert len(statements) == 3
    compiled = [statement.compile(dialect=mysql.dialect()) for statement in statements]
    assert [statement.table.name for statement in statements] == [
        "news_item", "news_event", "news_source"
    ]
    assert all(" WHERE " in str(statement) for statement in compiled)
    assert all(" IN " in str(statement) for statement in compiled)
    assert all(any(rows.namespace in str(value) for value in statement.params.values())
               for statement in compiled)


def test_cleanup_plan_refuses_unowned_identity():
    rows = safety().TestRows("aipulse-it-1234567890abcdef1234567890abcdef-")
    with pytest.raises(safety().UnsafeMysqlIntegration):
        rows.record_item("another-run-item")


@pytest.mark.parametrize("existing_rows", [0, 1])
def test_cleanup_executes_only_scoped_deletes_for_rows_that_exist(existing_rows):
    rows = safety().TestRows("aipulse-it-1234567890abcdef1234567890abcdef-")
    rows.record_source(rows.namespace + "source")
    rows.record_item(rows.namespace + "item")
    rows.record_event("a" * 64, rows.namespace + "event")

    class InertSession:
        def __init__(self):
            self.reads = []
            self.deletes = []

        async def scalar(self, statement):
            self.reads.append(statement)
            return existing_rows

        async def execute(self, statement):
            self.deletes.append(statement)

    session = InertSession()
    asyncio.run(safety().cleanup_owned_rows(session, rows))
    assert len(session.reads) == 3
    assert all("WHERE " in str(statement.compile(dialect=mysql.dialect()))
               for statement in session.reads)
    assert [statement.table.name for statement in session.deletes] == (
        ["news_item", "news_event", "news_source"] if existing_rows else []
    )


def run_isolated_suite(*arguments, env_value=None):
    environment = os.environ.copy()
    environment.pop("AIPULSE_ALLOW_TEST_DB_WRITE", None)
    if env_value is not None:
        environment["AIPULSE_ALLOW_TEST_DB_WRITE"] = env_value
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests_mysql/mysql", "-q",
            "-p", "no:cacheprovider", *arguments,
        ],
        cwd=Path(__file__).resolve().parents[3],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_integration_suite_without_cli_or_env_skips_before_database_access():
    result = run_isolated_suite()
    assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-1000:]
    assert "skipped" in result.stdout


def test_environment_permission_alone_still_skips_integration_suite():
    result = run_isolated_suite(env_value="1")
    assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-1000:]
    assert "skipped" in result.stdout


def test_cli_without_environment_permission_fails_before_database_access():
    result = run_isolated_suite("--run-aipulse-mysql")
    assert result.returncode != 0
    assert "AIPULSE_ALLOW_TEST_DB_WRITE=1" in result.stderr
