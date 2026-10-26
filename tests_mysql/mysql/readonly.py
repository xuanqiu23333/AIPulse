"""Test-only protection and snapshots for explicitly opted-in MySQL reads."""

from dataclasses import dataclass
import re

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests_mysql.mysql.safety import EXPECTED_TABLES, verify_live_test_connection


BUSINESS_TABLES = tuple(sorted(EXPECTED_TABLES - {"alembic_version"}))
_READ_SQL = re.compile(r"\s*(?:SELECT|SHOW)\b", re.IGNORECASE)


class UnsafeMysqlReadonly(RuntimeError):
    """A read-only integration prerequisite failed."""


def require_readonly_permission(*, readonly_enabled: bool, write_enabled: bool) -> None:
    """The write-suite switch cannot grant access to this separate suite."""
    if readonly_enabled is not True or write_enabled is True:
        raise UnsafeMysqlReadonly("Read-only MySQL suite requires its dedicated CLI switch")


def set_readonly_session(dbapi_connection) -> None:
    """Apply MySQL's read-only default to a newly opened physical connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
    finally:
        cursor.close()


def assert_read_statement(statement: str) -> None:
    """Reject every SQLAlchemy statement outside the smoke suite's read set."""
    if not _READ_SQL.match(statement):
        raise UnsafeMysqlReadonly("A non-read SQL statement was blocked")


def install_readonly_guard(engine) -> None:
    """Configure every physical test connection before its first transaction."""

    @event.listens_for(engine.sync_engine, "connect")
    def _readonly_session(dbapi_connection, _connection_record):
        set_readonly_session(dbapi_connection)

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _reject_non_reads(_connection, _cursor, statement, _parameters, _context, _many):
        assert_read_statement(statement)


class ReadonlyAsyncSession(AsyncSession):
    """Reject accidental ORM writes and commits in addition to server protection."""

    async def commit(self):
        raise UnsafeMysqlReadonly("Commit is forbidden in the read-only suite")

    async def flush(self, objects=None):
        raise UnsafeMysqlReadonly("Flush is forbidden in the read-only suite")


async def verify_readonly_connection(connection) -> None:
    """Check the actual API connection, including its server-side access mode."""
    mode = (await connection.execute(text(
        "SELECT @@session.transaction_read_only AS is_read_only"
    ))).scalar_one()
    if mode != 1:
        raise UnsafeMysqlReadonly("MySQL connection is not in read-only mode")
    await verify_live_test_connection(connection)


@dataclass(frozen=True)
class ReadonlySnapshot:
    counts: tuple[tuple[str, int], ...]
    definitions: tuple[tuple[str, str], ...]


async def capture_readonly_snapshot(connection) -> ReadonlySnapshot:
    """Read exact row counts and SHOW CREATE TABLE output without changing data."""
    await verify_readonly_connection(connection)
    counts = []
    for table in BUSINESS_TABLES:
        count = (await connection.execute(text(f"SELECT COUNT(*) FROM `{table}`"))).scalar_one()
        counts.append((table, count))
    definitions = []
    for table in sorted(EXPECTED_TABLES):
        ddl = (await connection.execute(text(f"SHOW CREATE TABLE `{table}`"))).one()[1]
        definitions.append((table, ddl))
    return ReadonlySnapshot(tuple(counts), tuple(definitions))
