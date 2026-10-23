"""Offline tests for the reviewed 0001 -> 0002 MySQL test migration gate."""

import asyncio
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.script import ScriptDirectory

from tests.aipulse.unit.test_first_test_migration import APPROVED_GRANTS


def migration_entry():
    try:
        return import_module("aipulse.storage.second_test_migration")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Incremental migration entry is missing: {exc.name}", pytrace=False)


def test_reviewed_incremental_migration_has_one_fixed_target():
    entry = migration_entry()
    config = entry.reviewed_config()
    assert config is not None
    assert entry.TARGET_REVISION == "20261016_0002"


@pytest.mark.parametrize("name", ["APPROVED_0001_SHA256", "APPROVED_0002_SHA256"])
def test_reviewed_source_rejects_either_hash_change(monkeypatch, name):
    entry = migration_entry()
    monkeypatch.setattr(entry, name, "0" * 64)
    with pytest.raises(entry.UnsafeTestMigrationError, match="migration file changed"):
        entry.reviewed_config()


def test_reviewed_source_rejects_wrong_revision_chain(monkeypatch):
    entry = migration_entry()
    real = ScriptDirectory.from_config

    def broken(config):
        scripts = real(config)
        return SimpleNamespace(
            get_heads=lambda: [entry.TARGET_REVISION],
            walk_revisions=lambda: list(scripts.walk_revisions()),
            get_revision=lambda revision: SimpleNamespace(
                down_revision="wrong" if revision == entry.TARGET_REVISION else None,
                module=scripts.get_revision(revision).module,
            ),
        )

    monkeypatch.setattr(entry.ScriptDirectory, "from_config", broken)
    with pytest.raises(entry.UnsafeTestMigrationError, match="revision graph"):
        entry.reviewed_config()


def test_reviewed_source_rejects_extra_migration_operation(monkeypatch):
    entry = migration_entry()
    real = ScriptDirectory.from_config

    def changed(config):
        scripts = real(config)
        target = scripts.get_revision(entry.TARGET_REVISION)

        def unsafe_upgrade():
            target.module.op.drop_table("news_item")

        monkeypatch.setattr(target.module, "upgrade", unsafe_upgrade)
        return scripts

    monkeypatch.setattr(entry.ScriptDirectory, "from_config", changed)
    with pytest.raises(entry.UnsafeTestMigrationError, match="migration operations"):
        entry.reviewed_config()


EXPECTED_TABLES = (
    "agent_run_log", "alembic_version", "daily_brief", "news_event",
    "news_item", "news_source", "research_evidence", "research_task",
)
BASE_COLUMNS = (
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
CONSTRAINTS = (
    ("news_event", "PRIMARY", "PRIMARY KEY", "id", None, None),
    ("news_event", "uq_news_event_event_key", "UNIQUE", "event_key", None, None),
    ("news_item", "news_item_ibfk_2", "FOREIGN KEY", "event_id", "news_event", "id"),
    ("research_task", "research_task_ibfk_1", "FOREIGN KEY", "event_id", "news_event", "id"),
)
INDEXES = (
    ("ix_news_event_status_created_at", 1, 1, "status"),
    ("ix_news_event_status_created_at", 1, 2, "created_at"),
    ("PRIMARY", 0, 1, "id"),
    ("uq_news_event_event_key", 0, 1, "event_key"),
)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    def scalar_one(self):
        return self.one()[0]


class InertConnection:
    """The only database surface in these tests; it never opens a socket."""

    def __init__(self, *, database="aipulse_test_db", account="aipulse_test_migrate@localhost",
                 version="8.0.44", schema=None, tables=None, revisions=None, columns=None,
                 constraints=None, indexes=None, grants=None, fail_identity=False):
        self.identity = (database, account, version)
        self.schema = schema if schema is not None else (
            ("aipulse_test_db", "utf8mb4", "utf8mb4_0900_ai_ci"),
        )
        self.tables = tables if tables is not None else tuple(
            (name, "InnoDB", "utf8mb4_0900_ai_ci") for name in EXPECTED_TABLES
        )
        self.revisions = revisions if revisions is not None else (("20261014_0001",),)
        self.columns = columns if columns is not None else BASE_COLUMNS
        self.constraints = constraints if constraints is not None else CONSTRAINTS
        self.indexes = indexes if indexes is not None else INDEXES
        self.grants = grants if grants is not None else APPROVED_GRANTS
        self.fail_identity = fail_identity
        self.queries = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)
        if sql.startswith("SELECT DATABASE()"):
            if self.fail_identity:
                raise RuntimeError("identity query failed")
            return Result((self.identity,))
        if "information_schema.SCHEMATA" in sql:
            return Result(self.schema)
        if "information_schema.TABLES" in sql:
            return Result(self.tables)
        if sql == "SELECT version_num FROM alembic_version":
            return Result(self.revisions)
        if "information_schema.COLUMNS" in sql:
            return Result(self.columns)
        if "information_schema.TABLE_CONSTRAINTS" in sql:
            return Result(self.constraints)
        if "information_schema.STATISTICS" in sql:
            return Result(self.indexes)
        if sql == "SHOW GRANTS":
            return Result(tuple((grant,) for grant in self.grants))
        raise AssertionError(f"Unexpected query: {sql}")


def _preflight(monkeypatch, connection):
    entry = migration_entry()
    config = SimpleNamespace(attributes={})
    monkeypatch.setattr(entry, "reviewed_config", lambda: config)
    calls = []

    def upgrade(actual_config, revision):
        calls.append((actual_config, revision, actual_config.attributes.get("connection"),
                      tuple(connection.queries)))

    monkeypatch.setattr(entry.command, "upgrade", upgrade)
    return entry, config, calls


def test_all_checks_precede_fixed_upgrade_on_same_connection(monkeypatch):
    connection = InertConnection()
    entry, config, calls = _preflight(monkeypatch, connection)
    entry.preflight_and_upgrade(connection, config)
    assert len(calls) == 1
    assert calls[0][:3] == (config, "20261016_0002", connection)
    assert calls[0][3] == tuple(connection.queries)
    assert connection.queries[0].startswith("SELECT DATABASE()")
    assert connection.queries[-1] == "SHOW GRANTS"
    assert all(query.startswith("SELECT ") or query == "SHOW GRANTS"
               for query in connection.queries)


def test_upgrade_uses_finally_reviewed_config_not_caller_supplied_config(monkeypatch):
    entry = migration_entry()
    connection = InertConnection()
    reviewed = SimpleNamespace(attributes={})
    unreviewed = SimpleNamespace(attributes={}, script_location="unapproved")
    monkeypatch.setattr(entry, "reviewed_config", lambda: reviewed)
    calls = []

    def record(actual_config, revision):
        calls.append((actual_config, revision, actual_config.attributes.get("connection")))

    monkeypatch.setattr(entry.command, "upgrade", record)
    entry.preflight_and_upgrade(connection, unreviewed)
    assert calls == [(reviewed, "20261016_0002", connection)]
    assert "connection" not in unreviewed.attributes


@pytest.mark.parametrize("overrides", [
    {"database": "aipulse_db"},
    {"account": "root@localhost"},
    {"account": "aipulse_test@localhost"},
    {"version": "8.0.43"},
    {"schema": ()},
    {"schema": (("aipulse_test_db", "latin1", "latin1_swedish_ci"),)},
    {"tables": (("alembic_version", "InnoDB", "utf8mb4_0900_ai_ci"),)},
    {"tables": tuple((name, "InnoDB", "utf8mb4_0900_ai_ci") for name in EXPECTED_TABLES)
               + (("unexpected", "InnoDB", "utf8mb4_0900_ai_ci"),)},
    {"revisions": ()},
    {"revisions": (("20261016_0002",),)},
    {"revisions": (("20261014_0001",), ("20261014_0001",))},
    {"columns": BASE_COLUMNS + (("score_reason", "varchar(1000)", "YES"),)},
    {"columns": BASE_COLUMNS + (("rule_version", "varchar(100)", "YES"),)},
    {"columns": BASE_COLUMNS + (("score_reason", "varchar(1000)", "YES"),
                                ("rule_version", "varchar(100)", "YES"))},
    {"columns": BASE_COLUMNS[:-1]},
    {"constraints": CONSTRAINTS[:-1]},
    {"indexes": INDEXES[:-1]},
    {"grants": APPROVED_GRANTS + (
        "GRANT INSERT ON `aipulse_db`.* TO `aipulse_test_migrate`@`localhost`",
    )},
    {"grants": APPROVED_GRANTS + (
        "GRANT CREATE ON *.* TO `aipulse_test_migrate`@`localhost`",
    )},
    {"grants": APPROVED_GRANTS + (
        "GRANT SELECT ON `aipulse_test_db`.* TO `aipulse_test_migrate`@`localhost` WITH GRANT OPTION",
    )},
])
def test_unsafe_database_state_or_grants_stop_before_upgrade(monkeypatch, overrides):
    connection = InertConnection(**overrides)
    entry, config, calls = _preflight(monkeypatch, connection)
    with pytest.raises(entry.UnsafeTestMigrationError):
        entry.preflight_and_upgrade(connection, config)
    assert calls == []
    assert "connection" not in config.attributes


def test_identity_query_failure_propagates_without_upgrade(monkeypatch):
    connection = InertConnection(fail_identity=True)
    entry, config, calls = _preflight(monkeypatch, connection)
    with pytest.raises(RuntimeError, match="identity query failed"):
        entry.preflight_and_upgrade(connection, config)
    assert len(connection.queries) == 1
    assert calls == []


def test_upgrade_exception_is_not_retried_or_followed_by_downgrade(monkeypatch):
    connection = InertConnection()
    entry, config, _ = _preflight(monkeypatch, connection)
    calls = []

    def fail(actual_config, revision):
        calls.append((actual_config, revision))
        raise RuntimeError("DDL failed")

    monkeypatch.setattr(entry.command, "upgrade", fail)
    monkeypatch.setattr(entry.command, "downgrade", lambda *_: pytest.fail("no downgrade"))
    with pytest.raises(RuntimeError, match="DDL failed"):
        entry.preflight_and_upgrade(connection, config)
    assert calls == [(config, "20261016_0002")]


def test_cli_redacts_exception_message_with_password_and_dsn(monkeypatch, capsys):
    entry = migration_entry()
    secret = "secret-from-local-tty"

    def fail():
        raise RuntimeError(f"{secret} mysql+asyncmy://hidden@localhost/db")

    monkeypatch.setattr(entry, "main", fail)
    assert entry.cli() == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert secret not in output.out + output.err
    assert "mysql+asyncmy://" not in output.out + output.err


def test_main_uses_local_getpass_without_logging_password(monkeypatch, capsys):
    entry = migration_entry()
    secret = "not-a-real-secret"
    monkeypatch.setattr(entry.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(entry.sys, "executable", __import__("sys").executable)
    monkeypatch.setattr(entry, "reviewed_config", lambda: object())
    monkeypatch.setattr(entry.getpass, "getpass", lambda _: secret)
    seen = []
    monkeypatch.setattr(entry, "_run", lambda password: seen.append(password))
    monkeypatch.setattr(entry.asyncio, "run", lambda task: task)
    assert entry.main() == 0
    assert secret not in capsys.readouterr().out


def test_run_supplies_one_connection_to_guarded_upgrade_and_disposes_engine(monkeypatch):
    from aipulse.config import DatabaseSettings

    entry = migration_entry()
    settings = DatabaseSettings(
        _env_file=None, db_password="fake-dev", test_db_name="aipulse_test_db",
        test_db_user="aipulse_test", test_db_password="fake-test",
    )
    monkeypatch.setattr(entry, "load_database_settings", lambda: settings)
    reviewed = SimpleNamespace(attributes={})
    monkeypatch.setattr(entry, "reviewed_config", lambda: reviewed)
    connection = object()
    observed = []

    class AsyncConnection:
        async def run_sync(self, callback, config):
            observed.append(("run_sync", callback, config))
            callback(connection, config)

    class Begin:
        async def __aenter__(self):
            observed.append(("begin",))
            return AsyncConnection()

        async def __aexit__(self, *args):
            observed.append(("exit",))

    class Engine:
        def begin(self):
            return Begin()

        async def dispose(self):
            observed.append(("dispose",))

    def new_engine(url, **kwargs):
        observed.append(("engine", url.username, url.database, url.host, url.port))
        return Engine()

    monkeypatch.setattr(entry, "create_async_engine", new_engine)
    monkeypatch.setattr(entry, "preflight_and_upgrade", lambda actual, config:
                        observed.append(("guard", actual, config)))
    asyncio.run(entry._run("memory-only-migration-password"))
    assert observed == [
        ("engine", "aipulse_test_migrate", "aipulse_test_db", "127.0.0.1", 3306),
        ("begin",),
        ("run_sync", entry.preflight_and_upgrade, reviewed),
        ("guard", connection, reviewed),
        ("exit",),
        ("dispose",),
    ]


def test_business_test_gate_rejects_old_revision_after_real_migration():
    from tests.aipulse.unit.test_mysql_integration_safety import ReadOnlyConnection, safety

    connection = ReadOnlyConnection(revisions=("20261014_0001",))
    with pytest.raises(safety().UnsafeMysqlIntegration):
        asyncio.run(safety().verify_live_test_connection(connection))


def test_business_gate_can_be_strictly_switched_to_0002_after_real_migration(monkeypatch):
    from tests.aipulse.unit.test_mysql_integration_safety import ReadOnlyConnection, safety

    guard = safety()
    monkeypatch.setattr(guard, "EXPECTED_REVISION", "20261016_0002")
    asyncio.run(guard.verify_live_test_connection(
        ReadOnlyConnection(revisions=("20261016_0002",))
    ))
    with pytest.raises(guard.UnsafeMysqlIntegration):
        asyncio.run(guard.verify_live_test_connection(
            ReadOnlyConnection(revisions=("20261014_0001",))
        ))
