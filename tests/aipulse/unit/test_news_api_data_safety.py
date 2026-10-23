"""Offline guards for the opt-in populated news API MySQL suite."""

import pytest
from sqlalchemy.dialects import mysql
from types import SimpleNamespace

from tests_mysql.mysql.api_data_support import (
    build_data_api,
    normalized_schema_definitions,
    prepare_api_data_runtime,
)
from tests_mysql.mysql.readonly import ReadonlySnapshot
from tests_mysql.mysql.safety import TestRows, UnsafeMysqlIntegration


@pytest.mark.parametrize("cli,permit", [
    (False, None), (False, "1"), (True, None), (True, "true"),
])
def test_data_runtime_refuses_missing_write_opt_in_before_loading_credentials(cli, permit):
    calls = []

    def forbidden():
        calls.append("credentials read")
        raise AssertionError("Credentials must not be loaded")

    with pytest.raises(UnsafeMysqlIntegration):
        prepare_api_data_runtime(
            cli_enabled=lambda: cli, env_value=lambda: permit,
            load_settings=forbidden,
        )
    assert calls == []


def test_authorized_runtime_keeps_write_and_api_engines_separate():
    engines = iter((object(), object()))
    calls = []

    def make_engine(_settings):
        engine = next(engines)
        calls.append(("engine", engine))
        return engine

    runtime = prepare_api_data_runtime(
        cli_enabled=lambda: True, env_value=lambda: "1",
        load_settings=lambda: object(), make_engine=make_engine,
        make_sessions=lambda engine: (lambda: engine),
        install_guard=lambda engine: calls.append(("readonly", engine)),
        make_readonly_sessions=lambda engine: (lambda: engine),
    )
    assert runtime.write_engine is not runtime.api_engine
    assert calls == [
        ("engine", runtime.write_engine),
        ("engine", runtime.api_engine),
        ("readonly", runtime.api_engine),
    ]


def test_runtime_rejects_a_shared_write_and_api_engine():
    shared_engine = object()
    with pytest.raises(UnsafeMysqlIntegration, match="separate"):
        prepare_api_data_runtime(
            cli_enabled=lambda: True, env_value=lambda: "1",
            load_settings=lambda: object(), make_engine=lambda _settings: shared_engine,
        )


@pytest.mark.asyncio
async def test_api_lifespan_uses_only_dedicated_readonly_engine():
    class Engine:
        def __init__(self):
            self.disposed = False

        async def dispose(self):
            self.disposed = True

    write_engine, api_engine = Engine(), Engine()
    api_factory = object()
    app = build_data_api(SimpleNamespace(
        write_engine=write_engine, api_engine=api_engine, api_factory=api_factory,
    ))
    async with app.router.lifespan_context(app):
        assert app.state.engine is api_engine
        assert app.state.session_factory is api_factory
    assert api_engine.disposed is True
    assert write_engine.disposed is False


@pytest.mark.asyncio
async def test_api_dependency_preflights_and_rolls_back_its_actual_session(monkeypatch):
    import tests_mysql.mysql.api_data_support as support
    from aipulse.api.news import get_news_repository
    from aipulse.news.repository import NewsRepository

    calls = []
    connection = object()

    async def verify(actual):
        calls.append(("verify", actual))

    monkeypatch.setattr(support, "verify_readonly_connection", verify)

    class Session:
        async def connection(self):
            calls.append(("connection", None))
            return connection

        def in_transaction(self):
            return True

        async def rollback(self):
            calls.append(("rollback", None))

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            pass

    session = Session()
    runtime = SimpleNamespace(api_engine=object(), api_factory=SessionContext)
    app = build_data_api(runtime)
    dependency = app.dependency_overrides[get_news_repository]
    async for repository in dependency():
        assert isinstance(repository, NewsRepository)
        assert repository._session is session
    assert calls == [
        ("connection", None), ("verify", connection), ("rollback", None),
    ]


def test_schema_comparison_ignores_only_auto_increment_counter():
    first = ReadonlySnapshot(
        counts=(("news_event", 0),),
        definitions=(("news_event", "CREATE TABLE x (id bigint AUTO_INCREMENT) ENGINE=InnoDB AUTO_INCREMENT=10"),),
    )
    second = ReadonlySnapshot(
        counts=(("news_event", 0),),
        definitions=(("news_event", "CREATE TABLE x (id bigint AUTO_INCREMENT) ENGINE=InnoDB AUTO_INCREMENT=17"),),
    )
    changed = ReadonlySnapshot(
        counts=(("news_event", 0),),
        definitions=(("news_event", "CREATE TABLE x (id bigint AUTO_INCREMENT, extra int) ENGINE=InnoDB AUTO_INCREMENT=17"),),
    )
    assert normalized_schema_definitions(first) == normalized_schema_definitions(second)
    assert normalized_schema_definitions(first) != normalized_schema_definitions(changed)


def test_uuid_cleanup_never_targets_another_run_or_unqualified_tables():
    rows = TestRows("aipulse-it-" + "a" * 32 + "-")
    rows.record_source(rows.namespace + "source", provider="arxiv")
    rows.record_item(rows.namespace + "paper", provider="arxiv")
    rows.record_event("b" * 64, rows.namespace + "event")
    statements = rows.cleanup_statements()
    assert [statement.table.name for statement in statements] == [
        "news_item", "news_event", "news_source",
    ]
    sql = [str(statement.compile(dialect=mysql.dialect())) for statement in statements]
    assert all(" WHERE " in statement for statement in sql)
    with pytest.raises(UnsafeMysqlIntegration):
        rows.record_item("aipulse-it-" + "c" * 32 + "-paper", provider="arxiv")
