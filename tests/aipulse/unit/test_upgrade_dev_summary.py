"""Offline safety checks for the one-time development 0003 upgrade entry."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from aipulse.storage import upgrade_dev_summary as upgrade


def _test_directory() -> Path:
    return Path(os.environ.get("AIPULSE_OFFLINE_TEST_DIR", tempfile.gettempdir()))


def test_reviewed_chain_and_only_five_nullable_additions():
    config = upgrade.reviewed_config()
    assert config is not None
    assert upgrade.SOURCE_REVISION == "20261016_0002"
    assert upgrade.TARGET_REVISION == "20261021_0003"


def test_backup_requires_real_rows_external_path_and_matching_hash():
    lines = [
        "-- MySQL dump 10.13  Distrib 8.0.44",
        "-- Current Database: `aipulse_db`",
        "-- " + "x" * 1100,
    ]
    for table in sorted(upgrade.ALL_TABLES):
        lines.append(f"CREATE TABLE `{table}` ();")
    lines.extend([
        "INSERT INTO `alembic_version` VALUES ('20261016_0002');",
        "INSERT INTO `news_event` VALUES (1);",
        "INSERT INTO `news_item` VALUES (1);",
        "INSERT INTO `news_source` VALUES (1);",
        "-- Dump completed on 2026-10-21 12:00:00",
    ])
    backup = _test_directory() / f"offline_0003_{uuid4().hex}.sql"
    try:
        backup.write_text("\n".join(lines) + "\n", encoding="utf-8")
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        verified = upgrade.verify_backup(backup, digest)
        assert verified["counts"]["news_event"] == 1
        assert verified["counts"]["news_item"] == 1
        assert verified["counts"]["news_source"] == 1
        with pytest.raises(upgrade.UnsafeSummaryUpgrade):
            upgrade.verify_backup(backup, "0" * 64)
        backup.write_text(("\n".join(lines[:-4]) + "\n"), encoding="utf-8")
        with pytest.raises(upgrade.UnsafeSummaryUpgrade):
            upgrade.verify_backup(backup, hashlib.sha256(backup.read_bytes()).hexdigest())
    finally:
        backup.unlink(missing_ok=True)


def test_backup_cannot_live_in_project(monkeypatch):
    root = _test_directory()
    monkeypatch.setattr(upgrade, "ROOT", root)
    inside = root / f"offline_0003_{uuid4().hex}.sql"
    try:
        inside.write_text("-- MySQL dump", encoding="utf-8")
        with pytest.raises(upgrade.UnsafeSummaryUpgrade):
            upgrade.verify_backup(inside, hashlib.sha256(inside.read_bytes()).hexdigest())
    finally:
        inside.unlink(missing_ok=True)


def test_verify_backup_mode_never_prompts_or_connects(monkeypatch):
    monkeypatch.setattr(upgrade, "reviewed_config", lambda: object())
    monkeypatch.setattr(upgrade, "verify_backup", lambda *_args: {
        "path": Path("C:/backup.sql"), "bytes": 1234, "sha256": "A" * 64,
        "counts": {"news_event": 21, "news_item": 21, "news_source": 3},
    })
    monkeypatch.setattr(upgrade.getpass, "getpass", lambda *_args: pytest.fail("password prompt reached"))
    monkeypatch.setattr(upgrade, "run", lambda *_args: pytest.fail("database reached"))
    assert upgrade.main([
        "--verify-backup", "--backup-file", "C:/backup.sql", "--backup-sha256", "A" * 64,
    ]) == 0


def test_wrong_connection_identity_never_reaches_upgrade(monkeypatch):
    class Result:
        def one(self):
            return ("aipulse_db", "aipulse_app@localhost", "8.0.44", 3306)

    class Connection:
        def execute(self, _statement):
            return Result()

    monkeypatch.setattr(upgrade.command, "upgrade", lambda *_args: pytest.fail("DDL reached"))
    with pytest.raises(upgrade.UnsafeSummaryUpgrade):
        upgrade.preflight_and_upgrade(Connection(), {"counts": {}})


def test_diagnose_mode_uses_getpass_and_never_runs_upgrade(monkeypatch):
    seen = []

    async def diagnose(password):
        seen.append(password)
        return True

    monkeypatch.setattr(upgrade.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(upgrade, "reviewed_config", lambda: object())
    monkeypatch.setattr(upgrade.getpass, "getpass", lambda *_args: "special@:/#%")
    monkeypatch.setattr(upgrade, "diagnose_connection", diagnose)
    monkeypatch.setattr(upgrade, "verify_backup", lambda *_args: pytest.fail("backup reached"))
    monkeypatch.setattr(upgrade.command, "upgrade", lambda *_args: pytest.fail("DDL reached"))
    assert upgrade.main(["--diagnose"]) == 0
    assert seen == ["special@:/#%"]


def test_diagnose_rejects_empty_password_before_engine(monkeypatch):
    monkeypatch.setattr(upgrade.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(upgrade, "reviewed_config", lambda: object())
    monkeypatch.setattr(upgrade.getpass, "getpass", lambda *_args: "")
    monkeypatch.setattr(upgrade, "_migration_engine", lambda *_args: pytest.fail("engine reached"))
    with pytest.raises(upgrade.UnsafeSummaryUpgrade, match="Migration password is empty"):
        upgrade.main(["--diagnose"])


def test_diagnose_reports_only_safe_code_and_connection_stage(monkeypatch, capsys):
    class FailingConnection:
        async def __aenter__(self):
            raise OperationalError("connect", {}, Exception(1045, "secret password in driver text"))

        async def __aexit__(self, *_args):
            return None

    class Engine:
        disposed = False

        def connect(self):
            return FailingConnection()

        async def dispose(self):
            self.disposed = True

    engine = Engine()
    monkeypatch.setattr(upgrade, "_migration_engine", lambda *_args: engine)
    monkeypatch.setattr(upgrade.command, "upgrade", lambda *_args: pytest.fail("DDL reached"))
    assert asyncio.run(upgrade.diagnose_connection("private")) is False
    output = capsys.readouterr().out
    assert "stage=connection" in output
    assert "mysql_code=1045" in output
    assert "sqlstate=unavailable" in output
    assert "secret" not in output and "private" not in output
    assert engine.disposed


def test_upgrade_failure_reports_stage_and_code_without_exception_text(monkeypatch, capsys):
    class FailingConnection:
        async def __aenter__(self):
            raise OperationalError("connect", {}, Exception(1045, "secret driver details"))

        async def __aexit__(self, *_args):
            return None

    class Engine:
        disposed = False

        def begin(self):
            return FailingConnection()

        def connect(self):
            return FailingConnection()

        async def dispose(self):
            self.disposed = True

    engine = Engine()
    monkeypatch.setattr(upgrade, "_migration_engine", lambda *_args: engine)
    monkeypatch.setattr(upgrade, "verify_backup", lambda *_args: {"counts": {}})
    monkeypatch.setattr(upgrade.command, "upgrade", lambda *_args: pytest.fail("DDL reached"))
    with pytest.raises(OperationalError):
        asyncio.run(upgrade.run("private", Path("C:/backup.sql"), "A" * 64))
    output = capsys.readouterr().out
    assert "stage=connection" in output and "mysql_code=1045" in output
    assert "Read-only residual inspection unavailable" in output
    assert "secret" not in output and "private" not in output
    assert engine.disposed


def test_diagnose_reads_only_identity_and_revision(monkeypatch, capsys):
    statements = []

    class Result:
        def __init__(self, value):
            self.value = value

        def one(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return self.value

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            sql = str(statement)
            statements.append(sql)
            if "CURRENT_USER()" in sql:
                return Result(("aipulse_db", "aipulse_migrate@localhost", "8.0.44", 3306))
            return Result([upgrade.SOURCE_REVISION])

    class Engine:
        disposed = False

        def connect(self):
            return Connection()

        async def dispose(self):
            self.disposed = True

    engine = Engine()
    monkeypatch.setattr(upgrade, "_migration_engine", lambda *_args: engine)
    monkeypatch.setattr(upgrade.command, "upgrade", lambda *_args: pytest.fail("DDL reached"))
    assert asyncio.run(upgrade.diagnose_connection("private")) is True
    assert statements == [
        "SELECT DATABASE(), CURRENT_USER(), VERSION(), @@port",
        "SELECT version_num FROM alembic_version",
    ]
    assert "revisions=['20261016_0002']" in capsys.readouterr().out
    assert engine.disposed
