"""One-time guarded upgrade of populated local aipulse_db from 0002 to 0003.

Run only in a user's interactive PowerShell after a real mysqldump backup.
MySQL DDL may commit implicitly: failure requires read-only inspection, never retry.
"""

import argparse
import asyncio
import getpass
import hashlib
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import pool, text
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from aipulse.config import load_database_settings
from aipulse.models import Base
from aipulse.storage.init_dev_database import (
    ACCOUNT, ALL_TABLES, DATABASE,
    verify_constraints, verify_grants,
)
from aipulse.storage.migrations import migration_url


ROOT = Path(__file__).resolve().parents[3]
SOURCE_REVISION = "20261016_0002"
TARGET_REVISION = "20261021_0003"
REVIEWED = {
    "20261014_0001_v1_base.py": "6F4B91D13B2E954898275C533FB805EFE8722BA9F9360DF35C9D96A980E5E0FD",
    "20261016_0002_news_event_scoring.py": "196162A7D9F328FD1B2942F0C813249FE86A90FA3660DECFFDAB54607EDB9BB2",
    "20261021_0003_news_summary_cache.py": "28AA91DC9F365D84500D7AD229296D1ECD4737B751423DA89CF880FAC0351FCC",
    "20261021_0004_research_payloads.py": "9ACAC97151E12FCCD93870919C1EED53025512238642B4D656386792AFEC5896",
}
SUMMARY_COLUMNS = {
    "summary_json": "json",
    "summary_model": "varchar(100)",
    "summary_prompt_version": "varchar(100)",
    "summary_input_fingerprint": "char(64)",
    "summary_generated_at": "datetime(6)",
}


class UnsafeSummaryUpgrade(RuntimeError):
    """A backup, identity, schema, or reviewed migration prerequisite failed."""


def _assert_reviewed_upgrade(revision) -> None:
    actions = []

    class Recorder:
        def add_column(self, table_name, column):
            actions.append((table_name, column))

        def __getattr__(self, _name):
            raise UnsafeSummaryUpgrade("Unexpected 0003 operation")

    original = revision.op
    revision.op = Recorder()
    try:
        revision.upgrade()
    except Exception:
        raise UnsafeSummaryUpgrade("Unexpected 0003 operation") from None
    finally:
        revision.op = original
    if [(table, column.name) for table, column in actions] != [
        ("news_event", name) for name in SUMMARY_COLUMNS
    ]:
        raise UnsafeSummaryUpgrade("Unexpected 0003 columns")
    dialect = mysql.dialect()
    for _, column in actions:
        if (
            column.type.compile(dialect=dialect).lower() != SUMMARY_COLUMNS[column.name]
            or column.nullable is not True
            or column.default is not None
            or column.server_default is not None
        ):
            raise UnsafeSummaryUpgrade("Unexpected 0003 column definition")


def reviewed_config() -> Config:
    """Pin exact revision bytes, one chain, and only five ADD COLUMNs."""

    for name, expected in REVIEWED.items():
        actual = hashlib.sha256((ROOT / "alembic" / "versions" / name).read_bytes()).hexdigest().upper()
        if actual != expected:
            raise UnsafeSummaryUpgrade("Reviewed migration file changed")
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revisions = list(scripts.walk_revisions())
    if (
        scripts.get_heads() != ["20261021_0004"]
        or len(revisions) != 4
        or scripts.get_revision("20261021_0004").down_revision != TARGET_REVISION
        or scripts.get_revision(TARGET_REVISION).down_revision != SOURCE_REVISION
        or scripts.get_revision(SOURCE_REVISION).down_revision != "20261014_0001"
        or scripts.get_revision("20261014_0001").down_revision is not None
    ):
        raise UnsafeSummaryUpgrade("Unexpected Alembic revision graph")
    _assert_reviewed_upgrade(scripts.get_revision(TARGET_REVISION).module)
    return config


def verify_backup(path: Path, expected_sha256: str) -> dict:
    """Validate a recent external single-row INSERT dump without printing data."""

    if not path.is_absolute() or path.is_symlink() or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise UnsafeSummaryUpgrade("Invalid backup path or digest")
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
        raise UnsafeSummaryUpgrade("Backup must be a regular file outside the project")
    stat = resolved.stat()
    age = datetime.now(timezone.utc).timestamp() - stat.st_mtime
    if not 1_000 <= stat.st_size <= 1_073_741_824 or not -60 <= age <= 3600:
        raise UnsafeSummaryUpgrade("Backup size or age is invalid")

    digest = hashlib.sha256()
    tables = set()
    inserts = Counter()
    version_rows = []
    completed = False
    with resolved.open("rb") as source:
        header = source.read(4096)
        if b"MySQL dump" not in header or b"`aipulse_db`" not in header:
            raise UnsafeSummaryUpgrade("Backup header does not identify aipulse_db")
        source.seek(0)
        for line in source:
            digest.update(line)
            create = re.match(rb"^CREATE TABLE `([a-z_]+)`", line)
            if create:
                tables.add(create.group(1).decode("ascii"))
            insert = re.match(rb"^INSERT INTO `([a-z_]+)` VALUES ", line)
            if insert:
                table = insert.group(1).decode("ascii")
                inserts[table] += 1
                if table == "alembic_version":
                    version_rows.append(line.strip())
            if line.startswith(b"-- Dump completed on "):
                completed = True
    if digest.hexdigest().lower() != expected_sha256.lower():
        raise UnsafeSummaryUpgrade("Backup SHA-256 differs")
    if not completed or tables != ALL_TABLES or set(inserts) - ALL_TABLES:
        raise UnsafeSummaryUpgrade("Backup tables differ from the reviewed schema")
    if version_rows != [b"INSERT INTO `alembic_version` VALUES ('20261016_0002');"]:
        raise UnsafeSummaryUpgrade("Backup Alembic revision differs")
    if any(inserts[table] < 1 for table in ("news_event", "news_item", "news_source")):
        raise UnsafeSummaryUpgrade("Backup does not include real news rows")
    return {
        "path": resolved, "bytes": stat.st_size,
        "sha256": digest.hexdigest().upper(),
        "counts": {table: inserts[table] for table in ALL_TABLES},
    }


def _normalized_type(type_name: str) -> str:
    value = type_name.lower()
    return {"bool": "tinyint(1)", "boolean": "tinyint(1)", "integer": "int"}.get(value, value)


def _verify_schema(connection, revision: str) -> dict[str, int]:
    tables = connection.execute(text(
        "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME"
    )).all()
    if (
        {row[0] for row in tables} != ALL_TABLES or len(tables) != len(ALL_TABLES)
        or any((row[1], row[2]) != ("InnoDB", "utf8mb4_0900_ai_ci") for row in tables)
    ):
        raise UnsafeSummaryUpgrade("Development tables differ")
    versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if versions != [revision]:
        raise UnsafeSummaryUpgrade("Unexpected Alembic revision")
    actual = {
        (table, name): (type_name.lower(), nullable)
        for table, name, type_name, nullable in connection.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
        )).all()
    }
    expected = {
        (table.name, column.name): (
            _normalized_type(column.type.compile(dialect=mysql.dialect())),
            "YES" if column.nullable else "NO",
        )
        for table in Base.metadata.tables.values() for column in table.columns
        if (revision == "20261021_0004" or (table.name, column.name) not in {
            ("research_task", "result_json"), ("research_evidence", "metadata_json"),
        }) and (revision in {TARGET_REVISION, "20261021_0004"} or not (
            table.name == "news_event" and column.name in SUMMARY_COLUMNS
        ))
    }
    expected[("alembic_version", "version_num")] = ("varchar(32)", "NO")
    if actual != expected:
        raise UnsafeSummaryUpgrade("Development columns differ")
    verify_constraints(connection)
    return {table: connection.scalar(text(f"SELECT COUNT(*) FROM `{table}`")) for table in sorted(ALL_TABLES)}


def preflight_and_upgrade(connection, backup: dict, progress: dict | None = None) -> None:
    """Check and upgrade using this exact connection; never retry a DDL step."""

    if progress is not None:
        progress["stage"] = "identity check"
    identity = tuple(connection.execute(text(
        "SELECT DATABASE(), CURRENT_USER(), VERSION(), @@port"
    )).one())
    if identity != (DATABASE, ACCOUNT, "8.0.44", 3306):
        raise UnsafeSummaryUpgrade("Wrong migration connection identity")
    if progress is not None:
        progress["stage"] = "schema preflight"
    schema = connection.execute(text(
        "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
        "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = DATABASE()"
    )).all()
    if [tuple(row) for row in schema] != [("utf8mb4", "utf8mb4_0900_ai_ci")]:
        raise UnsafeSummaryUpgrade("Development charset or collation differs")
    verify_grants(connection)
    before = _verify_schema(connection, SOURCE_REVISION)
    if before != backup["counts"]:
        raise UnsafeSummaryUpgrade("Backup row counts differ from live development data")
    print(f"Preflight 0002 verified: events={before['news_event']}, items={before['news_item']}, sources={before['news_source']}")

    # Recheck reviewed bytes immediately before DDL, then inject this same connection.
    config = reviewed_config()
    config.attributes["connection"] = connection
    try:
        if progress is not None:
            progress["stage"] = "Alembic upgrade"
        command.upgrade(config, TARGET_REVISION)
    finally:
        config.attributes.pop("connection", None)
    if progress is not None:
        progress["stage"] = "post-upgrade verification"
    after = _verify_schema(connection, TARGET_REVISION)
    if after != before:
        raise UnsafeSummaryUpgrade("Business row counts changed during upgrade")
    print(f"Verified Alembic {TARGET_REVISION}; events={after['news_event']}, items={after['news_item']}, sources={after['news_source']}")


async def run(password: str, backup_file: Path, backup_sha256: str) -> None:
    engine = _migration_engine(password)
    progress = {"stage": "backup verification"}
    try:
        try:
            backup = verify_backup(backup_file, backup_sha256)
            progress["stage"] = "connection"
            async with engine.begin() as connection:
                await connection.run_sync(preflight_and_upgrade, backup, progress)
        except Exception as error:
            code, state = _driver_error_details(error)
            print(
                f"Upgrade failure: stage={progress['stage']}; "
                f"category={type(error).__name__}; mysql_code={code}; sqlstate={state}"
            )
            try:
                async with engine.connect() as connection:
                    if await connection.scalar(text("SELECT DATABASE()")) == DATABASE:
                        versions = (await connection.execute(text(
                            "SELECT version_num FROM alembic_version"
                        ))).scalars().all()
                        columns = (await connection.execute(text(
                            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'news_event' "
                            "AND COLUMN_NAME IN ('summary_json','summary_model',"
                            "'summary_prompt_version','summary_input_fingerprint','summary_generated_at')"
                        ))).scalars().all()
                        print(f"Read-only residual state: revisions={versions}; summary_columns={sorted(columns)}")
            except Exception:
                print("Read-only residual inspection unavailable")
            raise
    finally:
        await engine.dispose()


def _migration_engine(password: str):
    """Use the identical guarded URL and driver for upgrade and diagnosis."""
    settings = load_database_settings()
    url = migration_url(
        settings, target="dev", user="aipulse_migrate", password=password,
        database=DATABASE,
    )
    return create_async_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5})


def _driver_error_details(error: Exception) -> tuple[str, str]:
    """Extract only numeric MySQL code and validated SQLSTATE, never error text."""
    original = error.orig if isinstance(error, DBAPIError) else error
    args = getattr(original, "args", ())
    code = args[0] if args and type(args[0]) is int else None
    state = getattr(original, "sqlstate", None)
    if isinstance(state, bytes):
        state = state.decode("ascii", errors="ignore")
    return (
        str(code) if code is not None and 0 < code < 100000 else "unavailable",
        state if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state) else "unavailable",
    )


async def diagnose_connection(password: str) -> bool:
    """Probe the migration connection using SELECT only; never invoke Alembic."""
    stage = "configuration"
    engine = None
    try:
        engine = _migration_engine(password)
        stage = "connection"
        async with engine.connect() as connection:
            stage = "identity query"
            identity = tuple((await connection.execute(text(
                "SELECT DATABASE(), CURRENT_USER(), VERSION(), @@port"
            ))).one())
            if identity != (DATABASE, ACCOUNT, "8.0.44", 3306):
                print("Diagnostic failed: stage=identity check; connection identity differs")
                return False
            stage = "revision query"
            versions = (await connection.execute(text(
                "SELECT version_num FROM alembic_version"
            ))).scalars().all()
            print(
                f"Diagnostic succeeded: database={identity[0]}; user={identity[1]}; "
                f"mysql_version={identity[2]}; port={identity[3]}; revisions={versions}"
            )
            return True
    except Exception as error:
        code, state = _driver_error_details(error)
        print(
            f"Diagnostic failed: stage={stage}; category={type(error).__name__}; "
            f"mysql_code={code}; sqlstate={state}"
        )
        return False
    finally:
        if engine is not None:
            await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded populated aipulse_db upgrade 0002 -> 0003")
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--backup-sha256")
    parser.add_argument("--verify-backup", action="store_true", help="offline backup validation only")
    parser.add_argument("--diagnose", action="store_true", help="read-only migration connection diagnosis")
    args = parser.parse_args(argv)
    if args.verify_backup and args.diagnose:
        raise UnsafeSummaryUpgrade("Select only one mode")
    if not args.diagnose and (args.backup_file is None or args.backup_sha256 is None):
        raise UnsafeSummaryUpgrade("Backup file and SHA-256 are required")
    if not args.verify_backup and not sys.stdin.isatty():
        raise UnsafeSummaryUpgrade("Interactive local terminal required")
    if sys.version_info < (3, 11):
        raise UnsafeSummaryUpgrade("Python 3.11 or newer is required")
    reviewed_config()
    if args.diagnose:
        password = getpass.getpass("aipulse_migrate password: ")
        if not password:
            raise UnsafeSummaryUpgrade("Migration password is empty")
        return 0 if asyncio.run(diagnose_connection(password)) else 1
    backup = verify_backup(args.backup_file, args.backup_sha256)
    print(f"Backup verified: {backup['path']} ({backup['bytes']} bytes, SHA-256 {backup['sha256']})")
    print(f"Backup rows: events={backup['counts']['news_event']}, items={backup['counts']['news_item']}, sources={backup['counts']['news_source']}")
    if args.verify_backup:
        return 0
    password = getpass.getpass("aipulse_migrate password: ")
    if not password:
        raise UnsafeSummaryUpgrade("Migration password is required")
    asyncio.run(run(password, args.backup_file, args.backup_sha256))
    print("Development summary migration completed")
    return 0


def cli() -> int:
    try:
        return main()
    except Exception as error:
        # Driver exceptions may include connection details; report only category.
        if isinstance(error, UnsafeSummaryUpgrade):
            print("Upgrade stopped: " + str(error), file=sys.stderr)
        else:
            print("Upgrade stopped: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
