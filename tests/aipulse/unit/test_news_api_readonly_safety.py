"""Offline contracts for the separately opted-in MySQL read-only suite."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from tests_mysql.mysql.readonly import (
    UnsafeMysqlReadonly,
    ReadonlyAsyncSession,
    assert_read_statement,
    require_readonly_permission,
    set_readonly_session,
    verify_readonly_connection,
)


def test_readonly_requires_its_own_cli_switch():
    with pytest.raises(UnsafeMysqlReadonly):
        require_readonly_permission(readonly_enabled=False, write_enabled=False)
    require_readonly_permission(readonly_enabled=True, write_enabled=False)


def test_write_switch_never_enables_readonly_suite():
    with pytest.raises(UnsafeMysqlReadonly):
        require_readonly_permission(readonly_enabled=False, write_enabled=True)
    with pytest.raises(UnsafeMysqlReadonly):
        require_readonly_permission(readonly_enabled=True, write_enabled=True)


def test_readonly_collection_switch_skips_all_write_suites(monkeypatch):
    from tests_mysql.mysql.conftest import pytest_collection_modifyitems

    monkeypatch.delenv("AIPULSE_ALLOW_TEST_DB_WRITE", raising=False)

    class Item:
        def __init__(self, name):
            self.path = Path(name)
            self.markers = []

        def add_marker(self, marker):
            self.markers.append(marker)

    class Config:
        def getoption(self, name):
            return name == "--run-aipulse-mysql-readonly"

    readonly = Item("tests_mysql/mysql/test_news_api_readonly_mysql.py")
    write_suite = Item("tests_mysql/mysql/test_news_repository_mysql.py")
    unit = Item("tests/aipulse/unit/test_news_api.py")
    pytest_collection_modifyitems(Config(), [readonly, write_suite, unit])
    assert readonly.markers == []
    assert len(write_suite.markers) == 1
    assert unit.markers == []


@pytest.mark.asyncio
async def test_readonly_preflight_rejects_session_without_readonly_mode():
    class Connection:
        async def execute(self, statement):
            if "transaction_read_only" in str(statement):
                return SimpleNamespace(scalar_one=lambda: 0)
            raise AssertionError("Identity checks must not be reached")

    with pytest.raises(UnsafeMysqlReadonly):
        await verify_readonly_connection(Connection())


@pytest.mark.asyncio
async def test_readonly_preflight_propagates_identity_failure(monkeypatch):
    import tests_mysql.mysql.readonly as readonly

    async def reject(_connection):
        raise readonly.UnsafeMysqlReadonly("Wrong database")

    monkeypatch.setattr(readonly, "verify_live_test_connection", reject)

    class Connection:
        async def execute(self, statement):
            assert "transaction_read_only" in str(statement)
            return SimpleNamespace(scalar_one=lambda: 1)

    with pytest.raises(UnsafeMysqlReadonly, match="Wrong database"):
        await verify_readonly_connection(Connection())


def test_sql_guard_rejects_mutating_statements():
    assert_read_statement("SELECT COUNT(*) FROM news_event")
    assert_read_statement("SHOW CREATE TABLE `news_event`")
    for sql in ("INSERT INTO news_event", "UPDATE news_event", "DELETE FROM news_event",
                "CREATE TABLE x", "COMMIT", "SET SESSION TRANSACTION READ WRITE"):
        with pytest.raises(UnsafeMysqlReadonly):
            assert_read_statement(sql)


def test_new_connection_is_made_readonly_before_first_query():
    statements = []

    class Cursor:
        def execute(self, sql):
            statements.append(sql)

        def close(self):
            statements.append("closed")

    class Connection:
        def cursor(self):
            return Cursor()

    set_readonly_session(Connection())
    assert statements == ["SET SESSION TRANSACTION READ ONLY", "closed"]


@pytest.mark.asyncio
async def test_readonly_session_refuses_commit_and_flush():
    async with ReadonlyAsyncSession() as session:
        with pytest.raises(UnsafeMysqlReadonly):
            await session.commit()
        with pytest.raises(UnsafeMysqlReadonly):
            await session.flush()


def test_news_api_hides_database_exception_details():
    from fastapi.testclient import TestClient

    from aipulse.api.main import create_app
    from aipulse.api.news import get_news_repository

    class Engine:
        async def dispose(self):
            pass

    class BrokenRepository:
        async def count_events(self, **_filters):
            raise RuntimeError("mysql+asyncmy://user:secret@127.0.0.1/aipulse_test_db")

    app = create_app(
        settings_loader=lambda: object(),
        engine_factory=lambda _settings: Engine(),
        session_factory_builder=lambda _engine: object(),
    )
    app.dependency_overrides[get_news_repository] = lambda: BrokenRepository()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/news/events")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text
    assert "mysql+asyncmy" not in response.text
