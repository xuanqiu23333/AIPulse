"""Lazy, test-only engines for populated API reads and scoped fixture writes."""

from dataclasses import dataclass
import re
from typing import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from aipulse.api.main import create_app
from aipulse.api.news import get_news_repository
from aipulse.config import load_database_settings
from aipulse.news.repository import NewsRepository
from aipulse.storage.db import create_session_factory, create_test_engine
from tests_mysql.mysql.ingestion_support import GuardedSessionFactory
from tests_mysql.mysql.readonly import (
    ReadonlyAsyncSession, ReadonlySnapshot, install_readonly_guard,
    verify_readonly_connection,
)
from tests_mysql.mysql.safety import UnsafeMysqlIntegration, require_write_permission


_AUTO_INCREMENT_COUNTER = re.compile(r"\bAUTO_INCREMENT=\d+\b")


@dataclass(frozen=True)
class ApiDataRuntime:
    write_engine: object
    api_engine: object
    write_factory: object
    guarded_write_factory: GuardedSessionFactory
    api_factory: object


def _readonly_sessions(engine):
    return async_sessionmaker(
        engine, class_=ReadonlyAsyncSession, expire_on_commit=False, autoflush=False,
    )


def prepare_api_data_runtime(
    *,
    cli_enabled: Callable[[], bool],
    env_value: Callable[[], str | None],
    load_settings: Callable = load_database_settings,
    make_engine: Callable = create_test_engine,
    make_sessions: Callable = create_session_factory,
    install_guard: Callable = install_readonly_guard,
    make_readonly_sessions: Callable = _readonly_sessions,
) -> ApiDataRuntime:
    """Check both write opt-ins before reading credentials or making Engines."""
    require_write_permission(cli_enabled=cli_enabled(), env_value=env_value())
    settings = load_settings()
    write_engine = make_engine(settings)
    api_engine = make_engine(settings)
    if api_engine is write_engine:
        raise UnsafeMysqlIntegration("Write and API Engines must be separate")
    install_guard(api_engine)
    write_factory = make_sessions(write_engine)
    return ApiDataRuntime(
        write_engine=write_engine,
        api_engine=api_engine,
        write_factory=write_factory,
        guarded_write_factory=GuardedSessionFactory(
            write_factory, cli_enabled=cli_enabled, env_value=env_value,
        ),
        api_factory=make_readonly_sessions(api_engine),
    )


def normalized_schema_definitions(snapshot: ReadonlySnapshot) -> tuple[tuple[str, str], ...]:
    """Ignore MySQL's insert counter while retaining every structural clause."""
    return tuple(
        (table, _AUTO_INCREMENT_COUNTER.sub("AUTO_INCREMENT=<counter>", ddl))
        for table, ddl in snapshot.definitions
    )


def build_data_api(runtime: ApiDataRuntime):
    """Pin FastAPI lifespan and dependency to the separate read-only Engine."""
    app = create_app(
        settings_loader=lambda: object(),
        engine_factory=lambda _settings: runtime.api_engine,
        session_factory_builder=lambda _engine: runtime.api_factory,
    )

    async def readonly_repository():
        async with runtime.api_factory() as session:
            connection = await session.connection()
            await verify_readonly_connection(connection)
            try:
                yield NewsRepository(session)
            finally:
                if session.in_transaction():
                    await session.rollback()

    app.dependency_overrides[get_news_repository] = readonly_repository
    return app
