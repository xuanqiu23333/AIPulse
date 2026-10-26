"""One-time, guarded initial migration for the approved MySQL test database.

Historical local test-DB maintenance only; the one-revision gate is closed
when later revisions exist. Not a development bootstrap. Run only from a local interactive terminal. The migration password is read
without echo and never placed in an environment variable, URL string, or log.
"""

import asyncio
import getpass
import hashlib
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from aipulse.config import load_database_settings
from aipulse.storage.db import _ensure_test_target
from aipulse.storage.migrations import migration_url


ROOT = Path(__file__).resolve().parents[3]
REVISION = "20261014_0001"
APPROVED_SHA256 = "6F4B91D13B2E954898275C533FB805EFE8722BA9F9360DF35C9D96A980E5E0FD"
REQUIRED_PRIVILEGES = {
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "REFERENCES", "INDEX", "ALTER",
}


class UnsafeTestMigrationError(RuntimeError):
    """A migration precondition failed before the first DDL statement."""


def _assert_reviewed_source() -> None:
    migration = ROOT / "alembic" / "versions" / "20261014_0001_v1_base.py"
    if hashlib.sha256(migration.read_bytes()).hexdigest().upper() != APPROVED_SHA256:
        raise UnsafeTestMigrationError("Reviewed migration file changed")


def reviewed_config() -> Config:
    """Freeze the approved file and historical sole-revision boundary."""
    _assert_reviewed_source()
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    if scripts.get_heads() != [REVISION] or len(list(scripts.walk_revisions())) != 1:
        raise UnsafeTestMigrationError("Unexpected Alembic revision graph")
    return config


def _verify_grants(grants: list[str]) -> None:
    approved = set()
    if not grants:
        raise UnsafeTestMigrationError("Migration grants unavailable")
    for grant in grants:
        if "WITH GRANT OPTION" in grant.upper():
            raise UnsafeTestMigrationError("Migration account has grant option")
        match = re.match(r"^GRANT (.+?) ON (.+?) TO ", grant, flags=re.IGNORECASE)
        if match is None:
            raise UnsafeTestMigrationError("Unrecognized migration grant")
        privileges = {part.strip().upper() for part in match.group(1).split(",")}
        scope = match.group(2).replace("`", "").lower()
        if scope == "*.*" and privileges == {"USAGE"}:
            continue
        if scope != "aipulse_test_db.*" or not privileges <= REQUIRED_PRIVILEGES:
            raise UnsafeTestMigrationError("Migration account has unexpected privileges")
        approved.update(privileges)
    if approved != REQUIRED_PRIVILEGES:
        raise UnsafeTestMigrationError("Migration account lacks reviewed privileges")


def preflight_and_upgrade(connection, config: Config) -> None:
    """Check the very connection Alembic will use, then upgrade exactly once."""
    database = connection.execute(text("SELECT DATABASE()")).scalar_one()
    user = connection.execute(text("SELECT CURRENT_USER()")).scalar_one()
    version = connection.execute(text("SELECT VERSION()")).scalar_one()
    if (database, user, version) != (
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
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = :database ORDER BY TABLE_NAME"
    ), {"database": "aipulse_test_db"}).all()
    if tables:
        raise UnsafeTestMigrationError("Test database is not empty")

    grants = [row[0] for row in connection.execute(text("SHOW GRANTS")).all()]
    _verify_grants(grants)
    # Recheck at the last possible point before Alembic loads the revision.
    reviewed_config()
    config.attributes["connection"] = connection
    try:
        command.upgrade(config, REVISION)
    finally:
        config.attributes.pop("connection", None)


async def _run(password: str) -> None:
    settings = load_database_settings()
    _ensure_test_target(settings)
    url = migration_url(
        settings, target="test", user="aipulse_test_migrate", password=password,
        database="aipulse_test_db",
    )
    config = reviewed_config()
    engine = create_async_engine(
        url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(preflight_and_upgrade, config)
    finally:
        await engine.dispose()


def main() -> int:
    """Use a local TTY so the password never enters a command argument or log."""
    if not sys.stdin.isatty():
        raise UnsafeTestMigrationError("Interactive local terminal required")
    if sys.version_info < (3, 11):
        raise UnsafeTestMigrationError("Python 3.11 or newer is required")
    reviewed_config()
    password = getpass.getpass("aipulse_test_migrate password: ")
    if not password:
        raise UnsafeTestMigrationError("Migration password is required")
    asyncio.run(_run(password))
    print("Initial test database migration completed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Authentication exceptions can embed connection details. Never print
        # their message or traceback; inspect residual tables separately.
        print("Migration stopped: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
