"""Guard the reviewed 0001 -> 0002 migration of the local MySQL test DB.

Local test-database maintenance only, not the development bootstrap.
This module is inert on import. Run it only from an approved local terminal.
MySQL DDL can commit implicitly; a partial upgrade requires manual review.
"""

import asyncio
import getpass
import hashlib
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import String, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from aipulse.config import load_database_settings
from aipulse.storage.db import _ensure_test_target
from aipulse.storage.first_test_migration import (
    UnsafeTestMigrationError as InitialMigrationError,
    _verify_grants,
)
from aipulse.storage.migrations import migration_url


ROOT = Path(__file__).resolve().parents[3]
SOURCE_REVISION = "20261014_0001"
TARGET_REVISION = "20261016_0002"
APPROVED_0001_SHA256 = "6F4B91D13B2E954898275C533FB805EFE8722BA9F9360DF35C9D96A980E5E0FD"
APPROVED_0002_SHA256 = "196162A7D9F328FD1B2942F0C813249FE86A90FA3660DECFFDAB54607EDB9BB2"
EXPECTED_TABLES = frozenset({
    "agent_run_log", "alembic_version", "daily_brief", "news_event",
    "news_item", "news_source", "research_evidence", "research_task",
})
BASE_EVENT_COLUMNS = (
    ("id", "bigint unsigned", "NO"),
    ("event_key", "char(64)", "NO"),
    ("title", "varchar(512)", "NO"),
    ("summary", "text", "YES"),
    ("importance_score", "tinyint unsigned", "YES"),
    ("category", "varchar(32)", "YES"),
    ("status", "varchar(24)", "NO"),
    ("created_at", "datetime(6)", "NO"),
    ("updated_at", "datetime(6)", "NO"),
)
EXPECTED_EVENT_CONSTRAINTS = frozenset({
    ("news_event", "PRIMARY", "PRIMARY KEY", "id", None, None),
    ("news_event", "uq_news_event_event_key", "UNIQUE", "event_key", None, None),
    ("news_item", "FOREIGN KEY", "event_id", "news_event", "id"),
    ("research_task", "FOREIGN KEY", "event_id", "news_event", "id"),
})
EXPECTED_EVENT_INDEXES = frozenset({
    ("ix_news_event_status_created_at", 1, 1, "status"),
    ("ix_news_event_status_created_at", 1, 2, "created_at"),
    ("PRIMARY", 0, 1, "id"),
    ("uq_news_event_event_key", 0, 1, "event_key"),
})


class UnsafeTestMigrationError(RuntimeError):
    """A reviewed incremental migration prerequisite failed."""


def _assert_reviewed_source() -> None:
    for filename, approved_hash in (
        ("20261014_0001_v1_base.py", APPROVED_0001_SHA256),
        ("20261016_0002_news_event_scoring.py", APPROVED_0002_SHA256),
    ):
        path = ROOT / "alembic" / "versions" / filename
        if hashlib.sha256(path.read_bytes()).hexdigest().upper() != approved_hash:
            raise UnsafeTestMigrationError("Reviewed migration file changed")


def _assert_reviewed_operations(revision) -> None:
    actions = []

    class Recorder:
        def add_column(self, table_name, column):
            actions.append((table_name, column))

        def __getattr__(self, name):
            raise UnsafeTestMigrationError("Unexpected migration operations")

    original = revision.op
    revision.op = Recorder()
    try:
        revision.upgrade()
    except Exception:
        raise UnsafeTestMigrationError("Unexpected migration operations") from None
    finally:
        revision.op = original

    if [(table, column.name) for table, column in actions] != [
        ("news_event", "score_reason"), ("news_event", "rule_version"),
    ]:
        raise UnsafeTestMigrationError("Unexpected migration operations")
    for (_, column), length in zip(actions, (1000, 100), strict=True):
        if (
            not isinstance(column.type, String)
            or column.type.length != length
            or column.nullable is not True
            or column.default is not None
            or column.server_default is not None
        ):
            raise UnsafeTestMigrationError("Unexpected migration operations")


def reviewed_config() -> Config:
    """Pin the two byte-reviewed files, single chain, and fixed upgrade SQL."""
    _assert_reviewed_source()
    from aipulse.storage.init_dev_database import reviewed_config as reviewed_chain, UnsafeDevelopmentMigration
    try:
        config = reviewed_chain()
    except UnsafeDevelopmentMigration as error:
        raise UnsafeTestMigrationError(str(error)) from None
    scripts = ScriptDirectory.from_config(config)
    _assert_reviewed_operations(scripts.get_revision(TARGET_REVISION).module)
    return config


def preflight_and_upgrade(connection, config: Config) -> None:
    """Verify the very connection Alembic will use, then upgrade once."""
    identity = connection.execute(text(
        "SELECT DATABASE() AS db_name, CURRENT_USER() AS account_name, "
        "VERSION() AS server_version"
    )).one()
    if tuple(identity) != (
        "aipulse_test_db", "aipulse_test_migrate@localhost", "8.0.44"
    ):
        raise UnsafeTestMigrationError("Wrong migration connection identity")

    schema = connection.execute(text(
        "SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
        "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :database"
    ), {"database": "aipulse_test_db"}).all()
    if [tuple(row) for row in schema] != [
        ("aipulse_test_db", "utf8mb4", "utf8mb4_0900_ai_ci")
    ]:
        raise UnsafeTestMigrationError("Test database charset or collation changed")

    tables = connection.execute(text(
        "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = :database ORDER BY TABLE_NAME"
    ), {"database": "aipulse_test_db"}).all()
    if (
        {row[0] for row in tables} != EXPECTED_TABLES
        or len(tables) != len(EXPECTED_TABLES)
        or any((row[1], row[2]) != ("InnoDB", "utf8mb4_0900_ai_ci") for row in tables)
    ):
        raise UnsafeTestMigrationError("Unexpected test database tables")

    revisions = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    if [tuple(row) for row in revisions] != [(SOURCE_REVISION,)]:
        raise UnsafeTestMigrationError("Unexpected Alembic starting revision")

    columns = connection.execute(text(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = :database AND TABLE_NAME = 'news_event' "
        "ORDER BY ORDINAL_POSITION"
    ), {"database": "aipulse_test_db"}).all()
    if tuple(tuple(row) for row in columns) != BASE_EVENT_COLUMNS:
        raise UnsafeTestMigrationError("Unexpected news_event columns")

    constraints = connection.execute(text(
        "SELECT tc.TABLE_NAME, tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, "
        "kcu.COLUMN_NAME, kcu.REFERENCED_TABLE_NAME, kcu.REFERENCED_COLUMN_NAME "
        "FROM information_schema.TABLE_CONSTRAINTS tc "
        "LEFT JOIN information_schema.KEY_COLUMN_USAGE kcu "
        "ON tc.CONSTRAINT_SCHEMA = kcu.CONSTRAINT_SCHEMA "
        "AND tc.TABLE_NAME = kcu.TABLE_NAME AND tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
        "WHERE tc.CONSTRAINT_SCHEMA = :database "
        "AND (tc.TABLE_NAME = 'news_event' OR kcu.REFERENCED_TABLE_NAME = 'news_event') "
        "ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION"
    ), {"database": "aipulse_test_db"}).all()
    normalized = set()
    for row in constraints:
        if row[2] == "FOREIGN KEY":
            normalized.add((row[0], row[2], row[3], row[4], row[5]))
        else:
            normalized.add(tuple(row))
    if normalized != EXPECTED_EVENT_CONSTRAINTS or len(constraints) != len(EXPECTED_EVENT_CONSTRAINTS):
        raise UnsafeTestMigrationError("Unexpected news_event constraints")

    indexes = connection.execute(text(
        "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = :database "
        "AND TABLE_NAME = 'news_event' ORDER BY INDEX_NAME, SEQ_IN_INDEX"
    ), {"database": "aipulse_test_db"}).all()
    if {tuple(row) for row in indexes} != EXPECTED_EVENT_INDEXES or len(indexes) != len(EXPECTED_EVENT_INDEXES):
        raise UnsafeTestMigrationError("Unexpected news_event indexes")

    grants = [row[0] for row in connection.execute(text("SHOW GRANTS")).all()]
    try:
        _verify_grants(grants)
    except InitialMigrationError:
        raise UnsafeTestMigrationError("Unexpected migration account privileges") from None

    # Only the final freshly reviewed Config may select Alembic's scripts.
    # Never upgrade with a caller-supplied Config, even after a source recheck.
    approved_config = reviewed_config()
    approved_config.attributes["connection"] = connection
    try:
        command.upgrade(approved_config, TARGET_REVISION)
    finally:
        approved_config.attributes.pop("connection", None)


async def _run(password: str) -> None:
    settings = load_database_settings()
    _ensure_test_target(settings)
    url = migration_url(
        settings, target="test", user="aipulse_test_migrate", password=password,
        database="aipulse_test_db",
    )
    config = reviewed_config()
    engine = create_async_engine(url, poolclass=pool.NullPool,
                                 connect_args={"connect_timeout": 5})
    try:
        async with engine.begin() as connection:
            await connection.run_sync(preflight_and_upgrade, config)
    finally:
        await engine.dispose()


def main() -> int:
    """Prompt locally without echo; never put the migration secret in .env."""
    if not sys.stdin.isatty():
        raise UnsafeTestMigrationError("Interactive local terminal required")
    if sys.version_info < (3, 11):
        raise UnsafeTestMigrationError("Python 3.11 or newer is required")
    reviewed_config()
    password = getpass.getpass("aipulse_test_migrate password: ")
    if not password:
        raise UnsafeTestMigrationError("Migration password is required")
    asyncio.run(_run(password))
    print("Test database scoring migration completed")
    return 0


def cli() -> int:
    """Report only exception type; connector errors may include credentials."""
    try:
        return main()
    except Exception as exc:
        print("Migration stopped: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
