"""Offline safety checks for the one-time test database migration entry."""

from importlib import import_module
from types import SimpleNamespace

import pytest


APPROVED_GRANTS = (
    "GRANT USAGE ON *.* TO `aipulse_test_migrate`@`localhost`",
    "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, REFERENCES, INDEX, ALTER "
    "ON `aipulse_test_db`.* TO `aipulse_test_migrate`@`localhost`",
)


def migration_entry():
    try:
        return import_module("aipulse.storage.first_test_migration")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Safe test migration entry is missing: {exc.name}", pytrace=False)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one(self):
        return self.rows[0][0]

    def all(self):
        return self.rows


class InertConnection:
    """Only supplies fixed query results; it cannot open a database socket."""

    def __init__(self, *, database="aipulse_test_db", user="aipulse_test_migrate@localhost",
                 version="8.0.44", charset="utf8mb4", collation="utf8mb4_0900_ai_ci",
                 tables=(), grants=APPROVED_GRANTS):
        self.values = {
            "SELECT DATABASE()": [(database,)],
            "SELECT CURRENT_USER()": [(user,)],
            "SELECT VERSION()": [(version,)],
            "SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME "
            "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :database":
                [(database, charset, collation)],
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = :database ORDER BY TABLE_NAME":
                [(name,) for name in tables],
            "SHOW GRANTS": [(grant,) for grant in grants],
        }
        self.queries = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params))
        return Result(self.values[sql])


def test_guarded_upgrade_uses_the_same_prechecked_connection(monkeypatch):
    entry = migration_entry()
    connection = InertConnection()
    config = SimpleNamespace(attributes={})
    calls = []

    def record_upgrade(actual_config, revision):
        calls.append((actual_config, revision, len(connection.queries)))

    monkeypatch.setattr(entry.command, "upgrade", record_upgrade)
    # Exercise the historical connection guard independently of the now
    # deliberately closed one-revision entry point.
    monkeypatch.setattr(entry, "reviewed_config", lambda: config)
    entry.preflight_and_upgrade(connection, config)

    assert calls == [(config, entry.REVISION, 6)]
    assert "connection" not in config.attributes
    assert [sql for sql, _ in connection.queries[:3]] == [
        "SELECT DATABASE()", "SELECT CURRENT_USER()", "SELECT VERSION()",
    ]


@pytest.mark.parametrize("overrides", [
    {"database": "aipulse_db"},
    {"user": "aipulse_test@localhost"},
    {"version": "8.0.43"},
    {"charset": "latin1"},
    {"collation": "utf8mb4_general_ci"},
    {"tables": ("alembic_version",)},
    {"tables": ("news_source",)},
    {"grants": APPROVED_GRANTS + (
        "GRANT INSERT ON `aipulse_db`.* TO `aipulse_test_migrate`@`localhost`",
    )},
    {"grants": APPROVED_GRANTS + (
        "GRANT INSERT ON *.* TO `aipulse_test_migrate`@`localhost`",
    )},
    {"grants": (APPROVED_GRANTS[0],)},
])
def test_unsafe_identity_schema_or_grants_stop_before_upgrade(monkeypatch, overrides):
    entry = migration_entry()
    connection = InertConnection(**overrides)
    config = SimpleNamespace(attributes={})
    monkeypatch.setattr(entry.command, "upgrade", lambda *_: pytest.fail("upgrade must not run"))

    with pytest.raises(entry.UnsafeTestMigrationError):
        entry.preflight_and_upgrade(connection, config)

    assert "connection" not in config.attributes


def test_historical_initial_entry_refuses_to_run_after_new_revision_is_added():
    entry = migration_entry()
    entry._assert_reviewed_source()
    with pytest.raises(entry.UnsafeTestMigrationError, match="revision graph"):
        entry.reviewed_config()
