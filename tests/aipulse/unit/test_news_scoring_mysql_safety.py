"""Offline proof that scoring integration setup is gated before DB access."""

import asyncio
from importlib import import_module

import pytest
from sqlalchemy.dialects import mysql

from tests_mysql.mysql.safety import TestRows, UnsafeMysqlIntegration


def support():
    return import_module("tests_mysql.mysql.scoring_support")


def test_missing_either_write_opt_in_never_loads_credentials_or_creates_engine():
    for cli, env in ((False, None), (True, None), (False, "1"), (True, "true")):
        touched = []

        def forbidden(*args, **kwargs):
            touched.append((args, kwargs))
            raise AssertionError("Setup reached credentials or Engine before authorization")

        with pytest.raises(UnsafeMysqlIntegration):
            support().prepare_scoring_runtime(
                cli_enabled=lambda: cli, env_value=lambda: env,
                load_settings=forbidden, make_engine=forbidden,
                make_sessions=forbidden,
            )
        assert touched == []


def test_authorized_setup_reuses_existing_guarded_session_factory():
    events = []
    runtime = support().prepare_scoring_runtime(
        cli_enabled=lambda: True, env_value=lambda: "1",
        load_settings=lambda: events.append("settings") or object(),
        make_engine=lambda settings: events.append("engine") or object(),
        make_sessions=lambda engine: events.append("sessions") or object(),
    )
    assert events == ["settings", "engine", "sessions"]
    assert type(runtime.guarded_factory).__name__ == "GuardedSessionFactory"
    assert runtime.engine is not None and runtime.session_factory is not None


class InertCleanupSession:
    def __init__(self, *, foreign_links=0):
        self.foreign_links = foreign_links
        self.selected = []
        self.deleted = []

    async def execute(self, statement):
        if statement.is_delete:
            self.deleted.append(statement)
        else:
            self.selected.append(statement)

        class Result:
            def scalars(self):
                return self

            def all(self):
                return [17]

        return Result()

    async def scalar(self, statement):
        self.selected.append(statement)
        sql = str(statement.compile(dialect=mysql.dialect()))
        return self.foreign_links if "news_item.event_id IN" in sql else 1


def owned_rows():
    rows = TestRows("aipulse-it-" + "a" * 32 + "-")
    rows.record_source(rows.namespace + "source")
    rows.record_item(rows.namespace + "item")
    rows.record_event("b" * 64, rows.namespace + "event")
    return rows


def test_scoring_cleanup_uses_only_owned_child_event_source_deletes():
    session = InertCleanupSession()
    asyncio.run(support().cleanup_scoring_rows(session, owned_rows()))
    assert [statement.table.name for statement in session.deleted] == [
        "news_item", "news_event", "news_source",
    ]
    for statement in session.deleted:
        sql = str(statement.compile(dialect=mysql.dialect()))
        assert " WHERE " in sql and "aipulse-it" not in sql
        assert statement.whereclause is not None


def test_scoring_cleanup_refuses_event_with_unowned_remaining_item():
    session = InertCleanupSession(foreign_links=1)
    with pytest.raises(UnsafeMysqlIntegration):
        asyncio.run(support().cleanup_scoring_rows(session, owned_rows()))
    assert [statement.table.name for statement in session.deleted] == ["news_item"]
