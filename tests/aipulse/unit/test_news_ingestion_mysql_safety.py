"""Offline tests for the service-level MySQL suite's existing safety boundary."""

import asyncio
from importlib import import_module

import pytest
from sqlalchemy.dialects import mysql

from tests_mysql.mysql.safety import TestRows, UnsafeMysqlIntegration


NAMESPACE = "aipulse-it-1234567890abcdef1234567890abcdef-"


def support():
    return import_module("tests_mysql.mysql.ingestion_support")


def test_cleanup_plan_scopes_all_three_providers_to_exact_run_ids():
    rows = TestRows(NAMESPACE)
    for provider in ("github", "arxiv", "hn"):
        rows.record_source(NAMESPACE + provider + "-source", provider=provider)
        rows.record_item(NAMESPACE + provider + "-item", provider=provider)
    rows.record_event("a" * 64, NAMESPACE + "event")

    statements = rows.cleanup_statements()
    assert len(statements) == 7
    assert [stmt.table.name for stmt in statements] == [
        "news_item", "news_item", "news_item", "news_event",
        "news_source", "news_source", "news_source",
    ]
    for statement in statements:
        compiled = statement.compile(dialect=mysql.dialect())
        assert " WHERE " in str(compiled) and " IN " in str(compiled)
        assert any(NAMESPACE in str(value) for value in compiled.params.values())
        if statement.table.name != "news_event":
            assert any(value in {"github", "arxiv", "hn"} for value in compiled.params.values())
    with pytest.raises(UnsafeMysqlIntegration):
        rows.record_item("some-other-run-item", provider="hn")


def test_event_cleanup_pairs_each_key_with_its_original_title():
    rows = TestRows(NAMESPACE)
    pairs = [("a" * 64, NAMESPACE + "first"), ("b" * 64, NAMESPACE + "second")]
    for key, title in pairs:
        rows.record_event(key, title)

    statement = rows.cleanup_statements()[0]
    compiled = statement.compile(dialect=mysql.dialect())
    assert "(news_event.event_key, news_event.title) IN" in str(compiled)
    assert any(set(value) == set(pairs) for value in compiled.params.values()
               if isinstance(value, list))


class Transaction:
    def __init__(self, raw):
        self.raw = raw

    async def __aenter__(self):
        self.raw.events.append("begin")
        self.raw.active = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.raw.events.append("rollback" if exc_type else "commit")
        self.raw.active = False


class RawSession:
    def __init__(self):
        self.events = []
        self.active = False
        self.connection_token = object()

    async def __aenter__(self):
        self.events.append("open")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.events.append("close")

    def begin(self):
        return Transaction(self)

    async def connection(self):
        assert self.active
        self.events.append("connection")
        return self.connection_token

    async def execute(self, statement):
        assert self.active
        self.events.append("execute")
        return statement


def make_factory(raw, *, cli, env, verify):
    return support().GuardedSessionFactory(
        lambda: raw, cli_enabled=lambda: cli(), env_value=lambda: env(),
        verify_connection=verify,
    )


def test_guarded_service_session_requires_both_switches_before_connection():
    raw = RawSession()
    async def verify(_):
        raise AssertionError("No connection is allowed")

    for cli, env in ((False, "1"), (True, None), (True, "yes")):
        factory = make_factory(raw, cli=lambda: cli, env=lambda: env, verify=verify)
        async def attempt():
            async with factory() as session:
                async with session.begin():
                    await session.execute("write")
        with pytest.raises(UnsafeMysqlIntegration):
            asyncio.run(attempt())
    assert "connection" not in raw.events and "execute" not in raw.events


def test_guarded_service_session_checks_its_write_connection_before_execute():
    raw = RawSession()
    async def verify(connection):
        assert connection is raw.connection_token
        raw.events.append("verify")

    factory = make_factory(raw, cli=lambda: True, env=lambda: "1", verify=verify)
    async def write():
        async with factory() as session:
            async with session.begin():
                assert await session.execute("write") == "write"
    asyncio.run(write())

    assert raw.events == ["open", "begin", "connection", "verify", "execute", "commit", "close"]


def test_guarded_service_session_rolls_back_if_identity_preflight_fails():
    raw = RawSession()
    async def reject(_):
        raise UnsafeMysqlIntegration("Wrong test database")

    factory = make_factory(raw, cli=lambda: True, env=lambda: "1", verify=reject)
    async def write():
        async with factory() as session:
            async with session.begin():
                await session.execute("write")
    with pytest.raises(UnsafeMysqlIntegration):
        asyncio.run(write())

    assert raw.events == ["open", "begin", "connection", "rollback", "close"]


def test_guarded_service_session_rechecks_permission_for_each_transaction():
    raw = RawSession()
    permission = {"value": "1"}
    async def verify(_):
        raw.events.append("verify")

    factory = make_factory(raw, cli=lambda: True,
                           env=lambda: permission["value"], verify=verify)
    async def write():
        async with factory() as session:
            async with session.begin():
                await session.execute("first")
            permission["value"] = "0"
            with pytest.raises(UnsafeMysqlIntegration):
                async with session.begin():
                    await session.execute("second")
    asyncio.run(write())

    assert raw.events.count("verify") == raw.events.count("execute") == 1
