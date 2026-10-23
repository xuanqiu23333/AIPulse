"""Opt-in, real-MySQL API reads; this module never inserts fixture data."""

import os

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aipulse.api.main import create_app
from aipulse.api.news import get_news_repository
from aipulse.api.schemas import NewsEventPage
from aipulse.config import load_database_settings
from aipulse.news.repository import NewsRepository
from aipulse.storage.db import create_test_engine
from tests_mysql.mysql.readonly import (
    ReadonlyAsyncSession,
    UnsafeMysqlReadonly,
    capture_readonly_snapshot,
    install_readonly_guard,
    require_readonly_permission,
    verify_readonly_connection,
)


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def readonly_client(pytestconfig):
    # Collection skips this module by default. Recheck before reading .env or
    # constructing an Engine, so direct fixture invocation cannot bypass it.
    require_readonly_permission(
        readonly_enabled=pytestconfig.getoption("--run-aipulse-mysql-readonly"),
        write_enabled=pytestconfig.getoption("--run-aipulse-mysql"),
    )
    if os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE") == "1":
        raise UnsafeMysqlReadonly("The write permit must not be enabled for read-only smoke tests")

    settings = load_database_settings()
    engine = create_test_engine(settings)
    install_readonly_guard(engine)
    factory = async_sessionmaker(
        engine, class_=ReadonlyAsyncSession, expire_on_commit=False, autoflush=False,
    )
    app = create_app(
        settings_loader=lambda: settings,
        engine_factory=lambda _settings: engine,
        session_factory_builder=lambda _engine: factory,
    )

    async def test_repository():
        async with factory() as session:
            connection = await session.connection()
            await verify_readonly_connection(connection)
            try:
                yield NewsRepository(session)
            finally:
                if session.in_transaction():
                    await session.rollback()

    app.dependency_overrides[get_news_repository] = test_repository

    try:
        async with app.router.lifespan_context(app):
            async with engine.connect() as connection:
                before = await capture_readonly_snapshot(connection)
            counts = dict(before.counts)
            if counts["news_event"] != 0:
                raise UnsafeMysqlReadonly("Read-only empty-event smoke prerequisite failed")

            try:
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://aipulse.test"
                ) as client:
                    yield client
            finally:
                async with engine.connect() as connection:
                    after = await capture_readonly_snapshot(connection)
                if after != before:
                    raise UnsafeMysqlReadonly("Read-only smoke changed test data or schema")
    finally:
        app.dependency_overrides.clear()


async def test_default_page_is_empty_and_validated(readonly_client):
    response = await readonly_client.get("/api/v1/news/events")
    assert response.status_code == 200
    assert response.json() == {"page": 1, "page_size": 20, "total": 0, "items": []}
    assert NewsEventPage.model_validate(response.json()).items == ()


@pytest.mark.parametrize("query", [
    "?page=2&page_size=5",
    "?category=paper",
    "?min_score=50",
    "?sort=score",
    "?sort=latest",
    "?category=model&min_score=80&sort=latest&page=3&page_size=10",
])
async def test_valid_filters_and_sort_return_empty_page(readonly_client, query):
    response = await readonly_client.get(f"/api/v1/news/events{query}")
    assert response.status_code == 200
    body = NewsEventPage.model_validate(response.json())
    assert body.items == () and body.total == 0
    assert body.page == (3 if "page=3" in query else 2 if "page=2" in query else 1)
    assert body.page_size == (5 if "page_size=5" in query else 10 if "page_size=10" in query else 20)


async def test_missing_event_returns_sanitized_404(readonly_client):
    response = await readonly_client.get("/api/v1/news/events/987654321")
    assert response.status_code == 404
    assert response.json()["code"] == "http_error"
    assert "mysql" not in response.text.lower()
    assert "password" not in response.text.lower()


async def test_invalid_input_returns_sanitized_422(readonly_client):
    response = await readonly_client.get("/api/v1/news/events?page=0")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "mysql" not in response.text.lower()
