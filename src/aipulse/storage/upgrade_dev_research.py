"""Guarded, user-run-only populated development upgrade 0003 -> 0004."""

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
from alembic.script import ScriptDirectory
from sqlalchemy import text

from aipulse.storage.init_dev_database import ACCOUNT, ALL_TABLES, DATABASE, verify_grants
from aipulse.storage.upgrade_dev_summary import (
    ROOT, _driver_error_details, _migration_engine, _verify_schema, diagnose_connection,
    reviewed_config,
)


SOURCE_REVISION = "20261021_0003"
TARGET_REVISION = "20261021_0004"
FIELDS = {("research_task", "result_json"), ("research_evidence", "metadata_json")}


class UnsafeResearchUpgrade(RuntimeError):
    pass


def verify_backup(path: Path, expected_sha256: str) -> dict:
    if not path.is_absolute() or path.is_symlink() or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
        raise UnsafeResearchUpgrade("Invalid external backup path or digest")
    resolved = path.resolve(strict=True)
    if resolved.is_relative_to(ROOT.resolve()) or not resolved.is_file():
        raise UnsafeResearchUpgrade("Backup must be a regular file outside the project")
    stat = resolved.stat()
    age = datetime.now(timezone.utc).timestamp() - stat.st_mtime
    if not 1000 <= stat.st_size <= 1_073_741_824 or not -60 <= age <= 3600:
        raise UnsafeResearchUpgrade("Backup size or age invalid")
    digest, tables, inserts, versions = hashlib.sha256(), set(), Counter(), []
    completed = False
    with resolved.open("rb") as source:
        header = source.read(4096)
        if b"MySQL dump" not in header or b"`aipulse_db`" not in header:
            raise UnsafeResearchUpgrade("Backup does not identify aipulse_db")
        source.seek(0)
        for line in source:
            digest.update(line)
            match = re.match(rb"^CREATE TABLE `([a-z_]+)`", line)
            if match:
                tables.add(match.group(1).decode("ascii"))
            match = re.match(rb"^INSERT INTO `([a-z_]+)` VALUES ", line)
            if match:
                table = match.group(1).decode("ascii")
                inserts[table] += 1
                if table == "alembic_version":
                    versions.append(line.strip())
            completed |= line.startswith(b"-- Dump completed on ")
    if (digest.hexdigest().casefold() != expected_sha256.casefold() or not completed
            or tables != ALL_TABLES or set(inserts) - ALL_TABLES
            or versions != [b"INSERT INTO `alembic_version` VALUES ('20261021_0003');"]
            or any(inserts[name] < 1 for name in ("news_event", "news_item", "news_source"))):
        raise UnsafeResearchUpgrade("Backup digest, schema, revision or real-news rows differ")
    return {"path": resolved, "bytes": stat.st_size, "sha256": digest.hexdigest().upper(),
            "counts": {table: inserts[table] for table in ALL_TABLES}}


def verify_operations(config):
    revision = ScriptDirectory.from_config(config).get_revision(TARGET_REVISION).module
    if revision.down_revision != SOURCE_REVISION:
        raise UnsafeResearchUpgrade("Unexpected revision chain")
    actions = []

    class Recorder:
        def add_column(self, table, column):
            actions.append((table, column))

        def __getattr__(self, _name):
            raise UnsafeResearchUpgrade("Unexpected 0004 operation")

    original = revision.op
    try:
        revision.op = Recorder()
        revision.upgrade()
    finally:
        revision.op = original
    if {(table, column.name) for table, column in actions} != FIELDS or len(actions) != 2:
        raise UnsafeResearchUpgrade("Unexpected 0004 additions")
    if any(str(column.type).upper() != "JSON" or not column.nullable
           or column.default is not None or column.server_default is not None for _, column in actions):
        raise UnsafeResearchUpgrade("Unexpected 0004 column type")


def _upgrade(connection, backup, progress):
    progress["stage"] = "identity_and_schema_preflight"
    identity = tuple(connection.execute(text("SELECT DATABASE(), CURRENT_USER(), VERSION(), @@port")).one())
    if identity != (DATABASE, ACCOUNT, "8.0.44", 3306):
        raise UnsafeResearchUpgrade("Wrong migration connection identity")
    verify_grants(connection)
    before = _verify_schema(connection, SOURCE_REVISION)
    if before != backup["counts"]:
        raise UnsafeResearchUpgrade("Live row counts differ from backup")
    config = reviewed_config()
    verify_operations(config)
    config.attributes["connection"] = connection
    progress["stage"] = "alembic_0004"
    try:
        command.upgrade(config, TARGET_REVISION)
    finally:
        config.attributes.pop("connection", None)
    progress["stage"] = "post_upgrade_verification"
    after = _verify_schema(connection, TARGET_REVISION)
    if after != before:
        raise UnsafeResearchUpgrade("Row counts changed during upgrade")
    print(f"Verified {TARGET_REVISION}; events={after['news_event']}, items={after['news_item']}, sources={after['news_source']}")


async def run(password, backup_file, backup_sha256):
    engine = _migration_engine(password)
    progress = {"stage": "connection"}
    try:
        backup = verify_backup(backup_file, backup_sha256)
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade, backup, progress)
    except Exception as error:
        code, state = _driver_error_details(error)
        print(f"Upgrade failed: stage={progress['stage']}; category={type(error).__name__}; mysql_code={code}; sqlstate={state}")
        try:
            async with engine.connect() as connection:
                if await connection.scalar(text("SELECT DATABASE()")) == DATABASE:
                    versions = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
                    print(f"Read-only residual revisions: {versions}")
        except Exception:
            print("Read-only residual inspection unavailable")
        raise
    finally:
        await engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Protected aipulse_db 0003 -> 0004 upgrade; never auto-run")
    parser.add_argument("--backup-file", type=Path)
    parser.add_argument("--backup-sha256")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify-backup", action="store_true")
    mode.add_argument("--diagnose", action="store_true", help="SELECT-only connection diagnostic")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        raise UnsafeResearchUpgrade("Python 3.11 or newer is required")
    config = reviewed_config()
    verify_operations(config)
    if not args.verify_backup and not sys.stdin.isatty():
        raise UnsafeResearchUpgrade("Interactive local terminal required")
    if not args.diagnose:
        if args.backup_file is None or args.backup_sha256 is None:
            raise UnsafeResearchUpgrade("Backup file and SHA-256 required")
        backup = verify_backup(args.backup_file, args.backup_sha256)
        print(f"Backup verified: {backup['path']} ({backup['bytes']} bytes, SHA-256 {backup['sha256']})")
        if args.verify_backup:
            return 0
    password = getpass.getpass("aipulse_migrate password: ")
    if not password:
        raise UnsafeResearchUpgrade("Migration password is empty")
    if args.diagnose:
        return 0 if asyncio.run(diagnose_connection(password)) else 1
    asyncio.run(run(password, args.backup_file, args.backup_sha256))
    return 0


def cli():
    try:
        return main()
    except Exception as error:
        print("Research upgrade stopped: " + (str(error) if isinstance(error, UnsafeResearchUpgrade) else type(error).__name__), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
