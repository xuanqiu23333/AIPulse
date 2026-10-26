"""Offline guards for the prepared 0004 entry; never opens an Engine."""

import hashlib
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aipulse.storage import upgrade_dev_research as upgrade


def test_reviewed_0004_has_only_two_json_additions():
    upgrade.verify_operations(upgrade.reviewed_config())


def test_backup_requires_current_0003_real_rows_and_matching_digest():
    root = Path(os.environ.get("AIPULSE_OFFLINE_TEST_DIR", tempfile.gettempdir()))
    path = root / f"offline_research_backup_{uuid4().hex}.sql"
    lines = ["-- MySQL dump", "-- Current Database: `aipulse_db`", "-- " + "x" * 1100]
    lines += [f"CREATE TABLE `{table}` ();" for table in sorted(upgrade.ALL_TABLES)]
    lines += ["INSERT INTO `alembic_version` VALUES ('20261021_0003');"]
    lines += [f"INSERT INTO `{table}` VALUES (1);" for table in ("news_event", "news_item", "news_source")]
    lines += ["-- Dump completed on 2026-10-21 12:00:00"]
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        backup = upgrade.verify_backup(path, digest)
        assert backup["counts"]["news_event"] == 1
        with pytest.raises(upgrade.UnsafeResearchUpgrade):
            upgrade.verify_backup(path, "0" * 64)
        path.write_text(path.read_text(encoding="utf-8").replace("20261021_0003", "20261016_0002"), encoding="utf-8")
        with pytest.raises(upgrade.UnsafeResearchUpgrade):
            upgrade.verify_backup(path, hashlib.sha256(path.read_bytes()).hexdigest())
    finally:
        path.unlink(missing_ok=True)


def test_verify_backup_mode_never_prompts_or_connects(monkeypatch):
    monkeypatch.setattr(upgrade, "reviewed_config", lambda: object())
    monkeypatch.setattr(upgrade, "verify_operations", lambda _config: None)
    monkeypatch.setattr(upgrade, "verify_backup", lambda *_args: {"path": "external.sql", "bytes": 1200, "sha256": "A" * 64})
    monkeypatch.setattr(upgrade.getpass, "getpass", lambda *_args: pytest.fail("Password prompt reached"))
    monkeypatch.setattr(upgrade, "run", lambda *_args: pytest.fail("Migration reached"))
    assert upgrade.main(["--verify-backup", "--backup-file", "C:/backup.sql", "--backup-sha256", "A" * 64]) == 0


def test_same_preflight_connection_is_injected_and_fixed_0004_is_selected(monkeypatch):
    counts = {table: 1 for table in upgrade.ALL_TABLES}
    calls = []
    config = SimpleNamespace(attributes={})

    class Connection:
        def execute(self, _statement):
            return SimpleNamespace(one=lambda: ("aipulse_db", "aipulse_migrate@localhost", "8.0.44", 3306))

    connection = Connection()
    monkeypatch.setattr(upgrade, "verify_grants", lambda conn: calls.append(("grants", conn)))
    monkeypatch.setattr(upgrade, "_verify_schema", lambda conn, revision: counts.copy())
    monkeypatch.setattr(upgrade, "reviewed_config", lambda: config)
    monkeypatch.setattr(upgrade, "verify_operations", lambda _config: None)

    def command(configured, revision):
        assert configured.attributes["connection"] is connection
        calls.append(("upgrade", revision))

    monkeypatch.setattr(upgrade.command, "upgrade", command)
    upgrade._upgrade(connection, {"counts": counts}, {})
    assert ("upgrade", "20261021_0004") in calls
    assert "connection" not in config.attributes
