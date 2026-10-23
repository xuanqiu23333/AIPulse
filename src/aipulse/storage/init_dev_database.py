"""One-time, guarded initialization of the empty local development database.

Run only in a local interactive terminal after a verified mysqldump backup.
The migration account is never read from .env and its password is not logged.
"""

import argparse
import asyncio
import getpass
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import pool, text
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import create_async_engine

from aipulse.config import load_database_settings
from aipulse.models import Base
from aipulse.storage.migrations import migration_url


ROOT = Path(__file__).resolve().parents[3]
PYTHON = Path(sys.executable)
DATABASE = "aipulse_db"
ACCOUNT = "aipulse_migrate@localhost"
BASE_REVISION = "20261014_0001"
REVISION_CHAIN = ("20261014_0001", "20261016_0002", "20261021_0003", "20261021_0004")
FINAL_REVISION = REVISION_CHAIN[-1]
REVIEWED = {
    "20261014_0001_v1_base.py": "6F4B91D13B2E954898275C533FB805EFE8722BA9F9360DF35C9D96A980E5E0FD",
    "20261016_0002_news_event_scoring.py": "196162A7D9F328FD1B2942F0C813249FE86A90FA3660DECFFDAB54607EDB9BB2",
    "20261021_0003_news_summary_cache.py": "28AA91DC9F365D84500D7AD229296D1ECD4737B751423DA89CF880FAC0351FCC",
    "20261021_0004_research_payloads.py": "9ACAC97151E12FCCD93870919C1EED53025512238642B4D656386792AFEC5896",
}
BUSINESS_TABLES = {
    "news_source", "news_event", "news_item", "daily_brief",
    "research_task", "research_evidence", "agent_run_log",
}
ALL_TABLES = BUSINESS_TABLES | {"alembic_version"}
REQUIRED_PRIVILEGES = {
    "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "REFERENCES", "INDEX", "ALTER",
}
UNIQUE_KEYS = {
    ("alembic_version", "PRIMARY", ("version_num",)),
    ("news_source", "PRIMARY", ("id",)),
    ("news_source", "uq_news_source_provider_external_id", ("provider", "external_id")),
    ("news_event", "PRIMARY", ("id",)),
    ("news_event", "uq_news_event_event_key", ("event_key",)),
    ("news_item", "PRIMARY", ("id",)),
    ("news_item", "uq_news_item_provider_external_id", ("provider", "external_id")),
    ("daily_brief", "PRIMARY", ("id",)),
    ("daily_brief", "uq_daily_brief_date_timezone", ("brief_date", "timezone")),
    ("research_task", "PRIMARY", ("id",)),
    ("research_task", "uq_research_task_task_id", ("task_id",)),
    ("research_evidence", "PRIMARY", ("id",)),
    ("research_evidence", "uq_research_evidence_task_citation", ("research_task_id", "citation")),
    ("agent_run_log", "PRIMARY", ("id",)),
    ("agent_run_log", "uq_agent_run_log_task_seq", ("task_id", "seq")),
}
FOREIGN_KEYS = {
    ("news_item", "source_id", "news_source", "id"),
    ("news_item", "event_id", "news_event", "id"),
    ("research_task", "event_id", "news_event", "id"),
    ("research_evidence", "research_task_id", "research_task", "id"),
    ("agent_run_log", "task_id", "research_task", "task_id"),
}


class UnsafeDevelopmentMigration(RuntimeError):
    """A safety prerequisite or post-migration contract failed."""


def reviewed_config() -> Config:
    """Pin all reviewed bytes and the exact chain, independent of Git history."""
    for name, expected in REVIEWED.items():
        actual = hashlib.sha256((ROOT / "alembic" / "versions" / name).read_bytes()).hexdigest().upper()
        if actual != expected:
            raise UnsafeDevelopmentMigration("Reviewed Alembic revision changed")
    config = Config(str(ROOT / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    revisions = list(scripts.walk_revisions())
    if (
        scripts.get_heads() != [FINAL_REVISION]
        or len(revisions) != len(REVISION_CHAIN)
        or any(scripts.get_revision(revision).down_revision != previous
               for revision, previous in zip(REVISION_CHAIN, (None, *REVISION_CHAIN[:-1])))
    ):
        raise UnsafeDevelopmentMigration("Unexpected Alembic revision graph")
    return config


def verify_backup(path: Path, expected_sha256: str) -> dict[str, object]:
    """Require a recent, readable external structure export of the empty DB."""
    if not path.is_absolute() or path.is_symlink() or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise UnsafeDevelopmentMigration("Backup must be an absolute regular file")
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
        raise UnsafeDevelopmentMigration("Backup must be outside the project")
    stat = resolved.stat()
    age = datetime.now(timezone.utc).timestamp() - stat.st_mtime
    if not 100 <= stat.st_size <= 1_048_576 or not -60 <= age <= 3600:
        raise UnsafeDevelopmentMigration("Backup is empty or older than one hour")
    data = resolved.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if (digest.casefold() != expected_sha256.casefold()
            or b"MySQL dump" not in data[:512] or b"`aipulse_db`" not in data
            or b"-- Dump completed on " not in data
            or re.search(rb"(?im)^\s*(?:CREATE TABLE|INSERT INTO|REPLACE INTO)\b", data)):
        raise UnsafeDevelopmentMigration("Backup is not an empty development schema export")
    return {"path": str(resolved), "bytes": stat.st_size, "sha256": digest}


def verify_grants(connection) -> None:
    """Reject global privileges, test DB access, grant option, and missing DDL rights."""
    grants = [row[0] for row in connection.execute(text("SHOW GRANTS"))]
    approved = set()
    if not grants:
        raise UnsafeDevelopmentMigration("Migration grants unavailable")
    for grant in grants:
        if "WITH GRANT OPTION" in grant.upper():
            raise UnsafeDevelopmentMigration("Migration account has grant option")
        match = re.match(r"^GRANT (.+?) ON (.+?) TO ", grant, flags=re.IGNORECASE)
        if match is None:
            raise UnsafeDevelopmentMigration("Unrecognized migration grant")
        privileges = {part.strip().upper() for part in match.group(1).split(",")}
        scope = match.group(2).replace("`", "").lower()
        if scope == "*.*" and privileges == {"USAGE"}:
            continue
        if scope != "aipulse_db.*" or not privileges <= REQUIRED_PRIVILEGES:
            raise UnsafeDevelopmentMigration("Migration account has unexpected privileges")
        approved.update(privileges)
    if approved != REQUIRED_PRIVILEGES:
        raise UnsafeDevelopmentMigration("Migration account lacks reviewed privileges")


def verify_identity_and_empty(connection) -> None:
    """Check the exact connection that will execute DDL before any upgrade."""
    identity = connection.execute(text(
        "SELECT DATABASE(), CURRENT_USER(), VERSION(), @@port"
    )).one()
    if tuple(identity) != (DATABASE, ACCOUNT, "8.0.44", 3306):
        raise UnsafeDevelopmentMigration("Wrong migration connection identity")
    schema = connection.execute(text(
        "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
        "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :database"
    ), {"database": DATABASE}).all()
    if [tuple(row) for row in schema] != [("utf8mb4", "utf8mb4_0900_ai_ci")]:
        raise UnsafeDevelopmentMigration("Development schema charset or collation changed")
    tables = connection.execute(text(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = :database"
    ), {"database": DATABASE}).all()
    if tables:
        raise UnsafeDevelopmentMigration("Development database is not empty")
    verify_grants(connection)


def verify_schema(connection, revision: str) -> None:
    """Verify the fixed schema stage and absence of business rows."""
    if revision not in REVISION_CHAIN:
        raise UnsafeDevelopmentMigration("Unreviewed schema stage")
    tables = connection.execute(text(
        "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = :database ORDER BY TABLE_NAME"
    ), {"database": DATABASE}).all()
    if (
        {row[0] for row in tables} != ALL_TABLES or len(tables) != len(ALL_TABLES)
        or any((row[1], row[2]) != ("InnoDB", "utf8mb4_0900_ai_ci") for row in tables)
    ):
        raise UnsafeDevelopmentMigration("Unexpected development tables")
    versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if versions != [revision]:
        raise UnsafeDevelopmentMigration("Unexpected Alembic revision")
    columns = {
        (row[0], row[1]): (row[2].lower(), row[3])
        for row in connection.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = :database"
        ), {"database": DATABASE}).all()
    }
    expected = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if revision == BASE_REVISION and column.name in {"score_reason", "rule_version"}:
                continue
            if revision in REVISION_CHAIN[:2] and column.name in {
                "summary_json", "summary_model", "summary_prompt_version", "summary_input_fingerprint", "summary_generated_at",
            }:
                continue
            if revision != FINAL_REVISION and (table.name, column.name) in {
                ("research_task", "result_json"), ("research_evidence", "metadata_json"),
            }:
                continue
            type_name = column.type.compile(dialect=mysql.dialect()).lower()
            type_name = {"bool": "tinyint(1)", "boolean": "tinyint(1)", "integer": "int"}.get(type_name, type_name)
            expected[(table.name, column.name)] = (type_name, "YES" if column.nullable else "NO")
    expected[("alembic_version", "version_num")] = ("varchar(32)", "NO")
    if columns != expected:
        raise UnsafeDevelopmentMigration("Development schema columns differ")
    for table in sorted(BUSINESS_TABLES):
        if connection.scalar(text(f"SELECT COUNT(*) FROM `{table}`")) != 0:
            raise UnsafeDevelopmentMigration("Business rows appeared during migration")


def verify_constraints(connection) -> None:
    """Check reviewed primary, unique, and foreign key identities."""
    rows = connection.execute(text(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = :database AND NON_UNIQUE = 0 "
        "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
    ), {"database": DATABASE}).all()
    grouped: dict[tuple[str, str], list[str]] = {}
    for table, index, _sequence, column in rows:
        grouped.setdefault((table, index), []).append(column)
    actual_unique = {(table, index, tuple(columns)) for (table, index), columns in grouped.items()}
    if actual_unique != UNIQUE_KEYS:
        raise UnsafeDevelopmentMigration("Primary or unique keys differ")
    actual_foreign = {
        tuple(row) for row in connection.execute(text(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = :database AND REFERENCED_TABLE_NAME IS NOT NULL"
        ), {"database": DATABASE})
    }
    if actual_foreign != FOREIGN_KEYS:
        raise UnsafeDevelopmentMigration("Foreign keys differ")


def upgrade_once(connection, backup: dict[str, object]) -> None:
    """Preflight and run exactly 0001 -> 0002 -> 0003 -> 0004 once."""
    verify_identity_and_empty(connection)
    reviewed_config()
    record = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": DATABASE,
        "account": ACCOUNT,
        "server_version": "8.0.44",
        "initial_table_count": 0,
        "backup": backup,
        "revision_sha256": REVIEWED,
    }
    record_path = Path(str(backup["path"])).with_name(
        f"aipulse_db_preflight_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid4().hex[:8]}.json"
    )
    with record_path.open("x", encoding="utf-8") as output:
        json.dump(record, output, ensure_ascii=False, indent=2)
    print(f"Preflight record: {record_path}")
    for revision in REVISION_CHAIN:
        config = reviewed_config()
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, revision)
        finally:
            config.attributes.pop("connection", None)
        verify_schema(connection, revision)
        verify_constraints(connection)
        print(f"Verified Alembic {revision}")


async def run(password: str, backup: dict[str, object]) -> None:
    """Use only the guarded development migration URL and dispose the engine."""
    # A dump modified after the local prompt must never authorize DDL.
    backup = verify_backup(Path(str(backup["path"])), str(backup["sha256"]))
    settings = load_database_settings()
    url = migration_url(
        settings, target="dev", user="aipulse_migrate",
        password=password, database=DATABASE,
    )
    engine = create_async_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5})
    try:
        try:
            async with engine.begin() as connection:
                await connection.run_sync(upgrade_once, backup)
        except Exception:
            try:
                async with engine.connect() as connection:
                    if await connection.scalar(text("SELECT DATABASE()")) == DATABASE:
                        tables = (await connection.execute(text(
                            "SELECT TABLE_NAME FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA = :database ORDER BY TABLE_NAME"
                        ), {"database": DATABASE})).scalars().all()
                        versions = []
                        if "alembic_version" in tables:
                            versions = (await connection.execute(text(
                                "SELECT version_num FROM alembic_version"
                            ))).scalars().all()
                        score_columns = []
                        if "news_event" in tables:
                            score_columns = (await connection.execute(text(
                                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                                "WHERE TABLE_SCHEMA = :database AND TABLE_NAME = 'news_event' "
                                "AND COLUMN_NAME IN ('score_reason', 'rule_version') ORDER BY COLUMN_NAME"
                            ), {"database": DATABASE})).scalars().all()
                        print(
                            f"Read-only residual state: tables={','.join(tables)}; "
                            f"revisions={','.join(versions)}; scoring_columns={','.join(score_columns)}"
                        )
                    else:
                        print("Read-only residual state: wrong database target")
            except Exception as inspect_error:
                print(f"Read-only residual inspection failed: {type(inspect_error).__name__}")
            raise
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """Require local interactive input and a verified pre-migration backup."""
    parser = argparse.ArgumentParser(description="Initialize only empty aipulse_db through reviewed 0001-0004")
    parser.add_argument("--backup-file", type=Path, required=True)
    parser.add_argument("--backup-sha256", required=True)
    parser.add_argument("--check", action="store_true", help="offline revision and backup check; no connection or prompt")
    args = parser.parse_args(argv)
    if not args.check and not sys.stdin.isatty():
        raise UnsafeDevelopmentMigration("Interactive local terminal required")
    if sys.version_info < (3, 11):
        raise UnsafeDevelopmentMigration("Python 3.11 or newer is required")
    reviewed_config()
    backup = verify_backup(args.backup_file, args.backup_sha256)
    print(f"Backup verified: {backup['path']} ({backup['bytes']} bytes, SHA-256 {backup['sha256']})")
    if args.check:
        return 0
    password = getpass.getpass("aipulse_migrate password: ")
    if not password:
        raise UnsafeDevelopmentMigration("Migration password is required")
    asyncio.run(run(password, backup))
    print("Development database initialized to 20261021_0004")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Connector exceptions can contain connection details. Print only type.
        print("Initialization stopped: " + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
