"""News HTTP contract with dependency overrides and zero database connections."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aipulse.api.main import create_app


NAIVE_UTC = datetime(2026, 10, 17, 8, 30)


class FakeEngine:
    async def dispose(self):
        pass


class FakeRepository:
    def __init__(self, *, event=None, sources=()):
        self.event = event
        self.sources = sources
        self.calls = []

    async def count_events(self, **filters):
        self.calls.append(("count", filters))
        return 1 if self.event else 0

    async def list_events(self, **filters):
        self.calls.append(("list", filters))
        return (self.event,) if self.event else ()

    async def get_event_detail(self, event_id):
        self.calls.append(("detail", event_id))
        if self.event is None or event_id != self.event.event_id:
            return None
        return SimpleNamespace(**vars(self.event), summary="Event summary", sources=self.sources)


def sample_event(*, score=0):
    return SimpleNamespace(
        event_id=7, event_key="a" * 64, title="Model release",
        category="model" if score is not None else None,
        importance_score=score,
        score_reason="Published recently" if score is not None else None,
        rule_version="experimental-v1" if score is not None else None,
        status="active", created_at=NAIVE_UTC, updated_at=NAIVE_UTC,
        providers=("github", "hn"), item_count=2,
        database_password="must-not-leak", metadata_json={"token": "must-not-leak"},
    )


def client_for(repo):
    from aipulse.api.news import get_news_repository

    app = create_app(
        settings_loader=lambda: object(), engine_factory=lambda _s: FakeEngine(),
        session_factory_builder=lambda _e: object(),
    )
    app.dependency_overrides[get_news_repository] = lambda: repo
    return TestClient(app, raise_server_exceptions=False)


def test_list_defaults_filters_paging_and_utc_json():
    repo = FakeRepository(event=sample_event(score=0))
    with client_for(repo) as client:
        response = client.get("/api/v1/news/events")
    assert response.status_code == 200
    body = response.json()
    assert (body["page"], body["page_size"], body["total"]) == (1, 20, 1)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["importance_score"] == 0
    assert item["providers"] == ["github", "hn"] and item["item_count"] == 2
    assert datetime.fromisoformat(item["created_at"]) == NAIVE_UTC.replace(tzinfo=timezone.utc)
    assert item["created_at"].endswith("Z") or item["created_at"].endswith("+00:00")
    assert "database_password" not in item and "metadata_json" not in item
    assert repo.calls == [
        ("count", {"category": None, "min_score": None}),
        ("list", {"page": 1, "page_size": 20, "category": None,
                  "min_score": None, "sort": "score"}),
    ]


def test_filter_and_latest_sort_are_forwarded_without_database_connection():
    repo = FakeRepository(event=sample_event())
    with client_for(repo) as client:
        response = client.get(
            "/api/v1/news/events?page=3&page_size=5&category=model&min_score=0&sort=latest"
        )
    assert response.status_code == 200
    assert repo.calls[-1] == (
        "list", {"page": 3, "page_size": 5, "category": "model",
                 "min_score": 0, "sort": "latest"},
    )


def test_empty_list_is_normal_result():
    with client_for(FakeRepository()) as client:
        response = client.get("/api/v1/news/events")
    assert response.status_code == 200
    assert response.json()["total"] == 0 and response.json()["items"] == []


def test_detail_returns_all_safe_original_sources():
    original = SimpleNamespace(
        provider="github", external_id="release-7", title="Release title",
        original_url="https://github.com/acme/tool/releases/tag/v7",
        source_name="Acme releases", published_at=NAIVE_UTC,
        fetched_at=NAIVE_UTC, source_id=999, metadata_json={"authorization": "secret"},
    )
    second = SimpleNamespace(
        provider="hn", external_id="123", title="HN story",
        original_url="https://news.ycombinator.com/item?id=123",
        source_name="HN topstories", published_at=None, fetched_at=NAIVE_UTC,
    )
    with client_for(FakeRepository(event=sample_event(), sources=(original, second))) as client:
        response = client.get("/api/v1/news/events/7")
    assert response.status_code == 200
    body = response.json()
    assert body["event_id"] == 7 and body["summary"] == "Event summary"
    assert body["sources"][0]["provider"] == "github"
    assert body["sources"][0]["original_url"] == original.original_url
    assert body["sources"][0]["source_name"] == "Acme releases"
    assert len(body["sources"]) == 2
    assert body["sources"][1]["provider"] == "hn"
    assert body["sources"][1]["published_at"] is None
    assert datetime.fromisoformat(body["sources"][0]["fetched_at"]) == NAIVE_UTC.replace(tzinfo=timezone.utc)
    assert "source_id" not in body["sources"][0]
    assert "metadata_json" not in body["sources"][0]
    assert "secret" not in response.text and "must-not-leak" not in response.text


def test_unscored_event_preserves_nulls_in_list_and_detail():
    with client_for(FakeRepository(event=sample_event(score=None))) as client:
        listing = client.get("/api/v1/news/events").json()["items"][0]
        detail = client.get("/api/v1/news/events/7").json()
    for value in (listing, detail):
        assert value["importance_score"] is None
        assert value["category"] is None
        assert value["score_reason"] is None
        assert value["rule_version"] is None


def test_missing_event_is_safe_404():
    with client_for(FakeRepository()) as client:
        response = client.get("/api/v1/news/events/9")
    assert response.status_code == 404
    assert response.json()["code"] == "http_error"


@pytest.mark.parametrize("query", [
    "category=unknown", "sort=random", "page=0", "page_size=0",
    "page_size=101", "min_score=-1", "min_score=101", "min_score=1.5",
])
def test_invalid_query_returns_422_without_calling_repository(query):
    repo = FakeRepository(event=sample_event())
    with client_for(repo) as client:
        response = client.get(f"/api/v1/news/events?{query}")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert repo.calls == []


def test_invalid_event_id_returns_422():
    repo = FakeRepository()
    with client_for(repo) as client:
        response = client.get("/api/v1/news/events/0")
    assert response.status_code == 422
    assert repo.calls == []
