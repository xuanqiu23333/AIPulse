"""S1-1 configuration behavior without reading real credentials."""

from importlib import import_module

import pytest


CONFIG_KEYS = (
    "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
    "TEST_DB_HOST", "TEST_DB_PORT", "TEST_DB_NAME", "TEST_DB_USER", "TEST_DB_PASSWORD",
)


def config_module():
    try:
        return import_module("aipulse.config")
    except ModuleNotFoundError as exc:
        pytest.fail(f"S1-1 configuration module is missing: {exc.name}", pytrace=False)


def isolated_env(monkeypatch):
    for key in CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_env_file_loads_both_targets_and_process_env_wins(tmp_path, monkeypatch):
    isolated_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_NAME=aipulse_db\n"
        "DB_USER=aipulse_app\nDB_PASSWORD=file-only-secret\n"
        "TEST_DB_HOST=127.0.0.1\nTEST_DB_PORT=3307\n"
        "TEST_DB_NAME=aipulse_test_db\nTEST_DB_USER=aipulse_test\n"
        "TEST_DB_PASSWORD=test-only-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DB_PASSWORD", "process-only-secret")

    settings = config_module().load_database_settings(env_file=env_file)

    assert settings.db_host == "127.0.0.1"
    assert settings.db_port == 3306
    assert settings.test_db_host == "127.0.0.1"
    assert settings.test_db_port == 3307
    assert settings.db_password.get_secret_value() == "process-only-secret"
    assert settings.test_db_password.get_secret_value() == "test-only-secret"
    assert "process-only-secret" not in repr(settings)
    assert "test-only-secret" not in repr(settings)


def test_missing_password_has_safe_error(tmp_path, monkeypatch):
    isolated_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=127.0.0.1\nDB_NAME=aipulse_db\nDB_USER=aipulse_app\n"
        "DB_PORT=sensitive-marker\n",
        encoding="utf-8",
    )

    module = config_module()
    with pytest.raises(module.DatabaseConfigurationError) as error:
        module.load_database_settings(env_file=env_file)

    assert "sensitive-marker" not in str(error.value)
    assert "password" not in str(error.value).lower()


def test_blank_password_is_rejected_without_echoing_input(tmp_path, monkeypatch):
    isolated_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("DB_PASSWORD=\n", encoding="utf-8")

    module = config_module()
    with pytest.raises(module.DatabaseConfigurationError) as error:
        module.load_database_settings(env_file=env_file)

    assert "DB_PASSWORD" not in str(error.value)


def test_test_connection_settings_fall_back_to_local_dev_host_and_port(
    tmp_path, monkeypatch
):
    isolated_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=127.0.0.1\nDB_PORT=3306\nDB_NAME=aipulse_db\n"
        "DB_USER=aipulse_app\nDB_PASSWORD=dev-test-secret\n"
        "TEST_DB_NAME=aipulse_test_db\nTEST_DB_USER=aipulse_test\n"
        "TEST_DB_PASSWORD=test-test-secret\n",
        encoding="utf-8",
    )

    settings = config_module().load_database_settings(env_file=env_file)

    assert settings.test_db_host == "127.0.0.1"
    assert settings.test_db_port == 3306
