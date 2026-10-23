"""FastAPI foundation is exercised with an offline database boundary."""

from importlib import import_module

import pytest
from fastapi.testclient import TestClient


def api_module():
    try:
        return import_module("aipulse.api.main")
    except ModuleNotFoundError as exc:
        pytest.fail(f"S1-4 API module is missing: {exc.name}", pytrace=False)


class FakeConnection:
    def __init__(self, available=True):
        self.available = available

    async def __aenter__(self):
        if not self.available:
            raise OSError("database-password-should-not-appear")
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, statement):
        assert str(statement) == "SELECT 1"
        return FakeResult()


class FakeResult:
    def scalar_one(self):
        return 1


class FakeEngine:
    def __init__(self, available=True):
        self.available = available
        self.closed = False

    def connect(self):
        return FakeConnection(self.available)

    async def dispose(self):
        self.closed = True


def app_with_engine(engine):
    return api_module().create_app(
        settings_loader=lambda: object(),
        engine_factory=lambda _settings: engine,
        session_factory_builder=lambda _engine: object(),
    )


def test_root_and_healthy_database_response():
    engine = FakeEngine()
    with TestClient(app_with_engine(engine)) as client:
        assert client.get("/").json() == {"service": "AIPulse", "status": "running"}
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"service": "AIPulse", "database": "up"}
    assert engine.closed


def test_health_reports_unavailable_without_exception_text():
    engine = FakeEngine(available=False)
    with TestClient(app_with_engine(engine)) as client:
        health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"service": "AIPulse", "database": "down"}
    assert "database-password-should-not-appear" not in health.text


def test_unexpected_errors_have_safe_consistent_response():
    app = app_with_engine(FakeEngine())

    @app.get("/probe-error")
    def probe_error():
        raise RuntimeError("sensitive-token-should-not-appear")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/probe-error")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert response.json()["message"]
    assert response.json()["request_id"]
    assert "sensitive-token-should-not-appear" not in response.text
