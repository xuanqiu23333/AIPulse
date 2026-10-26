"""Keep real MySQL integration tests outside the default offline test suite."""

import os

import pytest

from tests_mysql.mysql.readonly import UnsafeMysqlReadonly, require_readonly_permission
from tests_mysql.mysql.safety import UnsafeMysqlIntegration, require_write_permission


def pytest_addoption(parser):
    parser.addoption(
        "--run-aipulse-mysql", action="store_true", default=False,
        help="Allow the separately authorized AIPulse MySQL test suite",
    )
    parser.addoption(
        "--run-aipulse-mysql-readonly", action="store_true", default=False,
        help="Allow only the separately authorized read-only news API smoke suite",
    )


def pytest_collection_modifyitems(config, items):
    write_enabled = config.getoption("--run-aipulse-mysql")
    readonly_enabled = config.getoption("--run-aipulse-mysql-readonly")
    mysql_items = [
        item for item in items
        if ("tests_mysql", "mysql") in tuple(zip(item.path.parts, item.path.parts[1:]))
    ]
    readonly_items = [
        item for item in mysql_items if item.path.name == "test_news_api_readonly_mysql.py"
    ]
    write_items = [item for item in mysql_items if item not in readonly_items]

    if readonly_items:
        if readonly_enabled:
            try:
                require_readonly_permission(
                    readonly_enabled=readonly_enabled, write_enabled=write_enabled,
                )
            except UnsafeMysqlReadonly as exc:
                raise pytest.UsageError(str(exc)) from None
        else:
            for item in readonly_items:
                item.add_marker(pytest.mark.skip(reason="read-only MySQL suite requires its CLI opt-in"))

    if write_items:
        if not write_enabled:
            for item in write_items:
                item.add_marker(pytest.mark.skip(reason="real MySQL suite requires explicit CLI opt-in"))
        else:
            try:
                require_write_permission(
                    cli_enabled=write_enabled,
                    env_value=os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
                )
            except UnsafeMysqlIntegration as exc:
                raise pytest.UsageError(str(exc)) from None
