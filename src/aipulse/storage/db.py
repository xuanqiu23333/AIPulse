"""Construct lazy MySQL AsyncEngines and guard the independent test target."""

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from aipulse.config import DatabaseSettings


class UnsafeTestDatabaseError(ValueError):
    """Reject an unsafe test target before making an Engine."""


class UnsafeDevelopmentDatabaseError(ValueError):
    """Reject an unapproved development target before making an Engine."""


def _ensure_development_target(settings: DatabaseSettings) -> None:
    """Limit the development runtime account to the approved local database."""
    if (
        settings.db_name != "aipulse_db"
        or settings.db_user != "aipulse_app"
        or settings.db_host != "127.0.0.1"
        or settings.db_port != 3306
        or not settings.db_password.get_secret_value()
    ):
        raise UnsafeDevelopmentDatabaseError("Unsafe development database target")


def _ensure_test_target(settings: DatabaseSettings) -> None:
    """Require the approved, independent local test database identity."""
    test_name = settings.test_db_name or ""
    test_user = settings.test_db_user or ""
    dev_name = settings.db_name
    dev_user = settings.db_user
    test_password = settings.test_db_password

    if (
        test_name != "aipulse_test_db"
        or test_user != "aipulse_test"
        or test_name.casefold() == dev_name.casefold()
        or test_user.casefold() == dev_user.casefold()
        or not test_password
        or not test_password.get_secret_value()
        or settings.test_db_host != "127.0.0.1"
        or settings.test_db_port != 3306
    ):
        raise UnsafeTestDatabaseError("Unsafe test database target")


def database_url(settings: DatabaseSettings, *, test: bool = False) -> URL:
    """Build a URL without manual quoting of special password characters."""
    if test:
        _ensure_test_target(settings)
        assert settings.test_db_password is not None
        return URL.create(
            "mysql+asyncmy",
            username=settings.test_db_user,
            password=settings.test_db_password.get_secret_value(),
            host=settings.test_db_host,
            port=settings.test_db_port,
            database=settings.test_db_name,
            query={"charset": "utf8mb4"},
        )

    _ensure_development_target(settings)
    return URL.create(
        "mysql+asyncmy",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={"charset": "utf8mb4"},
    )


def _new_engine(url: URL) -> AsyncEngine:
    """Prepare a small connection pool; construction does not open a socket."""
    return create_async_engine(
        url,
        pool_size=3,
        max_overflow=2,
        pool_timeout=10,
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


def create_development_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Construct the development Engine without connecting immediately."""
    return _new_engine(database_url(settings))


def create_test_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Reject unsafe test settings before constructing an Engine."""
    return _new_engine(database_url(settings, test=True))


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create short-lived AsyncSessions for later service and API use."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def close_engine(engine: AsyncEngine) -> None:
    """Release the connection pool during application shutdown."""
    await engine.dispose()
