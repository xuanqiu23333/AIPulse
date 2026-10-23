"""S1-1 engine construction must never select the development database for tests."""

import asyncio
from importlib import import_module

import pytest


def modules():
    try:
        return import_module("aipulse.config"), import_module("aipulse.storage.db")
    except ModuleNotFoundError as exc:
        pytest.fail(f"S1-1 database module is missing: {exc.name}", pytrace=False)


def settings(config, **overrides):
    values = {
        "db_host": "127.0.0.1", "db_port": 3306,
        "db_name": "aipulse_db", "db_user": "aipulse_app",
        "db_password": "dev@:/?#%", "test_db_host": "127.0.0.1",
        "test_db_port": 3306, "test_db_name": "aipulse_test_db",
        "test_db_user": "aipulse_test", "test_db_password": "test@:/?#%",
    }
    values.update(overrides)
    return config.DatabaseSettings(_env_file=None, **values)


def test_test_engine_rejects_dev_database_before_connect(monkeypatch):
    config, db = modules()
    monkeypatch.setattr(
        db, "create_async_engine", lambda *args, **kwargs: pytest.fail("engine created")
    )

    with pytest.raises(db.UnsafeTestDatabaseError) as error:
        db.create_test_engine(settings(config, test_db_name="aipulse_db"))

    assert "dev@:/?#%" not in str(error.value)
    assert "test@:/?#%" not in str(error.value)


def test_test_engine_rejects_dev_user_before_connect(monkeypatch):
    config, db = modules()
    monkeypatch.setattr(
        db, "create_async_engine", lambda *args, **kwargs: pytest.fail("engine created")
    )

    with pytest.raises(db.UnsafeTestDatabaseError):
        db.create_test_engine(settings(config, test_db_user="aipulse_app"))


def test_test_engine_rejects_unapproved_target_before_connect(monkeypatch):
    config, db = modules()
    monkeypatch.setattr(
        db, "create_async_engine", lambda *args, **kwargs: pytest.fail("engine created")
    )

    with pytest.raises(db.UnsafeTestDatabaseError):
        db.create_test_engine(settings(config, test_db_name="another_database"))


def test_test_engine_rejects_padded_target_before_connect(monkeypatch):
    config, db = modules()
    monkeypatch.setattr(
        db, "create_async_engine", lambda *args, **kwargs: pytest.fail("engine created")
    )

    with pytest.raises(db.UnsafeTestDatabaseError):
        db.create_test_engine(settings(config, test_db_name=" aipulse_test_db "))


def test_development_engine_rejects_other_database_before_connect(monkeypatch):
    config, db = modules()
    monkeypatch.setattr(
        db, "create_async_engine", lambda *args, **kwargs: pytest.fail("engine created")
    )

    with pytest.raises(ValueError):
        db.create_development_engine(settings(config, db_name="another_database"))


def test_development_engine_rejects_other_user_before_connect(monkeypatch):
    config, db = modules()
    monkeypatch.setattr(
        db, "create_async_engine", lambda *args, **kwargs: pytest.fail("engine created")
    )

    with pytest.raises(ValueError):
        db.create_development_engine(settings(config, db_user="root"))


def test_database_url_escapes_special_password_chars():
    config, db = modules()
    url = db.database_url(settings(config), test=False)

    assert url.drivername == "mysql+asyncmy"
    assert url.password == "dev@:/?#%"
    assert "dev%40%3A%2F%3F%23%25" in url.render_as_string(hide_password=False)
    assert url.query["charset"] == "utf8mb4"


def test_test_url_uses_independent_identity():
    config, db = modules()
    url = db.database_url(settings(config), test=True)

    assert url.database == "aipulse_test_db"
    assert url.username == "aipulse_test"
    assert url.port == 3306
    assert url.password == "test@:/?#%"


def test_engine_and_session_factory_are_lazy_and_disposable():
    config, db = modules()
    engine = db.create_development_engine(settings(config))
    try:
        session_factory = db.create_session_factory(engine)
        session = session_factory()
        assert session.bind is engine
        asyncio.run(session.close())
    finally:
        asyncio.run(db.close_engine(engine))
