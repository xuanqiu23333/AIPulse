"""Build an explicitly selected migration target without exposing credentials."""

from sqlalchemy.engine import URL

from aipulse.config import DatabaseSettings


class UnsafeMigrationTargetError(ValueError):
    """Reject migration accounts or databases outside the approved pair."""


def migration_url(
    settings: DatabaseSettings,
    *,
    target: str,
    user: str | None,
    password: str | None,
    database: str | None = None,
) -> URL:
    """Create a MySQL URL only for a matching dedicated migration account."""
    if target == "dev":
        expected_database = "aipulse_db"
        expected_user = "aipulse_migrate"
        expected_runtime_user = "aipulse_app"
        host, port = settings.db_host, settings.db_port
        runtime_database, runtime_user = settings.db_name, settings.db_user
    elif target == "test":
        expected_database = "aipulse_test_db"
        expected_user = "aipulse_test_migrate"
        expected_runtime_user = "aipulse_test"
        host, port = settings.test_db_host, settings.test_db_port
        runtime_database, runtime_user = settings.test_db_name, settings.test_db_user
    else:
        raise UnsafeMigrationTargetError("Unsafe migration target")

    if (
        runtime_database != expected_database
        or runtime_user != expected_runtime_user
        or database not in (None, expected_database)
        or user != expected_user
        or not password
        or host != "127.0.0.1"
        or port != 3306
    ):
        raise UnsafeMigrationTargetError("Unsafe migration target")

    return URL.create(
        "mysql+asyncmy",
        username=user,
        password=password,
        host=host,
        port=port,
        database=expected_database,
        query={"charset": "utf8mb4"},
    )
