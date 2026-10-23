"""Offline smoke checks for the guarded manual collection endpoint."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aipulse.api.main import create_app
from aipulse.news.scoring_service import EventScoreOutcome, EventScoreStatus, ScoredIngestionReport
from aipulse.news.service import IngestionReport, SourceIngestionResult
from aipulse.news.types import CollectionStatus


class FakeEngine:
    async def dispose(self):
        pass


def make_app(monkeypatch, *, enabled=False):
    if enabled:
        monkeypatch.setenv("AIPULSE_ENABLE_MANUAL_COLLECTION", "1")
    else:
        monkeypatch.delenv("AIPULSE_ENABLE_MANUAL_COLLECTION", raising=False)
    return create_app(
        settings_loader=lambda: object(),
        engine_factory=lambda _settings: FakeEngine(),
        session_factory_builder=lambda _engine: object(),
    )


def payload():
    return {"providers": ["github", "arxiv", "hn"], "limit_per_source": 5}


def test_route_registered_and_disabled_by_default(monkeypatch):
    app = make_app(monkeypatch)
    assert "/api/v1/news/collect" in app.openapi()["paths"]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/news/collect", json=payload())
    assert response.status_code == 403


@pytest.mark.parametrize("body", [
    {"providers": []},
    {"providers": ["github", "github"]},
    {"providers": ["other"]},
    {"providers": ["github"], "limit_per_source": 6},
    {"providers": ["github"], "limit_per_source": True},
    {"providers": ["github"], "url": "https://example.com"},
])
def test_invalid_request_is_rejected(monkeypatch, body):
    app = make_app(monkeypatch, enabled=True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/news/collect", json=body)
    assert response.status_code == 422


def test_non_loopback_is_rejected_before_database_access(monkeypatch):
    app = make_app(monkeypatch, enabled=True)
    with TestClient(
        app, base_url="http://127.0.0.1", raise_server_exceptions=False,
        client=("198.51.100.10", 41234),
    ) as client:
        response = client.post("/api/v1/news/collect", json=payload())
    assert response.status_code == 403


def test_non_local_host_is_rejected_even_from_loopback(monkeypatch):
    app = make_app(monkeypatch, enabled=True)
    with TestClient(
        app, base_url="http://example.com", raise_server_exceptions=False,
        client=("127.0.0.1", 41234),
    ) as client:
        response = client.post("/api/v1/news/collect", json=payload())
    assert response.status_code == 403


@pytest.mark.parametrize("headers,allowed", [
    ({"origin": "https://untrusted.example"}, False),
    ({"origin": "null"}, False),
    ({"sec-fetch-site": "cross-site"}, False),
    ({"origin": "http://127.0.0.1:5173", "sec-fetch-site": "same-site"}, True),
    ({"origin": "http://localhost:8000"}, True),
    ({}, True),
])
def test_browser_origin_guard_without_io(headers, allowed):
    from starlette.requests import Request
    from aipulse.api.collect import _loopback_request

    request = Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/",
        "query_string": b"", "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 41234),
        "headers": [(b"host", b"127.0.0.1:8000")] + [
            (key.encode(), value.encode()) for key, value in headers.items()
        ],
    })
    assert _loopback_request(request) is allowed


def test_busy_instance_returns_409(monkeypatch):
    app = make_app(monkeypatch, enabled=True)
    lock = app.state.manual_collection_lock
    assert lock.acquire(blocking=False)
    try:
        with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False,
                        client=("127.0.0.1", 41234)) as client:
            response = client.post("/api/v1/news/collect", json=payload())
        assert response.status_code == 409
    finally:
        lock.release()


def test_schema_error_releases_instance_lock(monkeypatch):
    import aipulse.api.collect as collect_api

    app = make_app(monkeypatch, enabled=True)

    async def unavailable(_engine, *, require_app_identity=False):
        raise RuntimeError("secret database detail")

    monkeypatch.setattr(collect_api, "schema_ready", unavailable)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False,
                    client=("127.0.0.1", 41234)) as client:
        response = client.post("/api/v1/news/collect", json=payload())
    assert response.status_code == 503
    assert "secret database detail" not in response.text
    assert app.state.manual_collection_lock.acquire(blocking=False)
    app.state.manual_collection_lock.release()


def test_selected_sources_and_report_are_forwarded_without_io(monkeypatch):
    import aipulse.api.collect as collect_api

    app = make_app(monkeypatch, enabled=True)
    calls = []

    async def ready(_engine, *, require_app_identity=False):
        calls.append(("schema", require_app_identity))
        return True

    def collectors(_client, *, providers, limit_per_source, http_timeout, github_token):
        calls.append(("collectors", tuple(providers), limit_per_source, http_timeout))
        return {provider: object() for provider in providers}

    async def run(requests, *, collectors, session_factory):
        calls.append(("run", tuple((r.source.provider, r.source.external_id) for r in requests)))
        return ScoredIngestionReport(
            ingestion=IngestionReport((SourceIngestionResult(
                provider="github", external_id="langchain-ai/langgraph", source_id=1,
                status=CollectionStatus.SUCCESS, fetched_count=5, accepted_count=5,
                processed_count=5, event_count=5, committed_event_ids=(1, 2, 3, 4, 5),
            ),)),
            scores=(EventScoreOutcome(event_id=1, status=EventScoreStatus.FAILED,
                                      error_code="persistence_failed"),),
        )

    monkeypatch.setattr(collect_api, "schema_ready", ready)
    monkeypatch.setattr(collect_api, "build_collectors", collectors)
    monkeypatch.setattr(collect_api, "run_collection", run)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False,
                    client=("127.0.0.1", 41234)) as client:
        response = client.post("/api/v1/news/collect", json={"providers": ["github"]})
    assert response.status_code == 200
    assert calls == [
        ("schema", True),
        ("collectors", ("github",), 5, 10.0),
        ("run", (("github", "langchain-ai/langgraph"),)),
    ]
    assert response.json()["sources"][0]["processed_count"] == 5
    assert response.json()["events_scored"] == 0
    assert response.json()["scoring_failed"] == 1
    assert response.json()["scoring_errors"] == [
        {"event_id": 1, "error_code": "persistence_failed"},
    ]
    assert response.json()["status"] == "partial"


def test_real_collector_wiring_is_selected_and_bounded():
    import httpx

    from aipulse.news.collectors.arxiv import ArxivCollector
    from aipulse.news.collectors.github import GitHubReleaseCollector
    from aipulse.news.collectors.hn import HackerNewsCollector
    from aipulse.news.manual_run import build_collectors

    async def check():
        async with httpx.AsyncClient() as client:
            collectors = build_collectors(
                client, providers=("github", "arxiv", "hn"),
                limit_per_source=5, http_timeout=10.0,
            )
            assert type(collectors["github"]) is GitHubReleaseCollector
            assert type(collectors["arxiv"]) is ArxivCollector
            assert type(collectors["hn"]) is HackerNewsCollector
            assert collectors["github"]._max_pages == collectors["arxiv"]._max_pages == 1
            assert collectors["hn"]._max_candidates == 5

    asyncio.run(check())


def test_schema_preflight_rejects_wrong_runtime_account():
    from aipulse.news.manual_run import schema_ready

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def scalar(self, statement):
            if str(statement) == "SELECT DATABASE()":
                return "aipulse_db"
            if str(statement) == "SELECT CURRENT_USER()":
                return "aipulse_migrate@localhost"
            raise AssertionError("No further schema query is allowed")

    class Engine:
        def connect(self):
            return Connection()

    assert asyncio.run(schema_ready(Engine(), require_app_identity=True)) is False


def test_shared_runner_calls_scoring_workflow(monkeypatch):
    import aipulse.news.manual_run as manual

    calls = []

    class FakeIngestion:
        def __init__(self, *, collectors, session_factory):
            calls.append("ingestion")

    class FakeScoring:
        def __init__(self, *, ingestion_service, session_factory):
            calls.append("scoring")

        async def ingest_and_score(self, requests, *, as_of):
            calls.append("ingest_and_score")
            return SimpleNamespace(requests=tuple(requests))

    monkeypatch.setattr(manual, "NewsIngestionService", FakeIngestion)
    monkeypatch.setattr(manual, "NewsScoringService", FakeScoring)
    report = asyncio.run(manual.run_collection(("request",), collectors={}, session_factory=object()))
    assert report.requests == ("request",)
    assert calls == ["ingestion", "scoring", "ingest_and_score"]


def test_cli_execute_still_uses_shared_runner_without_network_or_database(monkeypatch):
    import aipulse.news.cli as cli

    args = cli.parse_args([
        "--execute", "--provider", "github", "--github-repo", "langchain-ai/langgraph",
        "--limit-per-source", "5",
    ])
    calls = []

    async def ready(_engine):
        return True

    async def run(requests, *, collectors, session_factory):
        calls.append((tuple(r.source.provider for r in requests), tuple(collectors)))
        return ScoredIngestionReport(
            ingestion=IngestionReport((SourceIngestionResult(
                provider="github", external_id="langchain-ai/langgraph", source_id=1,
                status=CollectionStatus.SUCCESS,
            ),)),
            scores=(),
        )

    monkeypatch.setattr(cli, "load_database_settings", lambda: object())
    monkeypatch.setattr(cli, "create_development_engine", lambda _settings: FakeEngine())
    monkeypatch.setattr(cli, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(cli, "schema_ready", ready)
    monkeypatch.setattr(cli, "run_collection", run)
    assert asyncio.run(cli._run(args)) == 0
    assert calls == [(("github",), ("github", "arxiv", "hn"))]
