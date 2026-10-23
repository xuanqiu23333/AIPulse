"""Offline orchestration tests; all collectors and sessions are inert fakes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module

import pytest

from aipulse.news.collectors.base import BaseCollector
from aipulse.news.repository import SourceConfiguration
from aipulse.news.types import (
    CollectionResult, CollectionStatus, CollectorErrorCode, NewsCandidate,
)


NOW = datetime(2026, 10, 15, 9, tzinfo=timezone.utc)


def service_module():
    return import_module("aipulse.news.service")


def source(provider="github", external_id="acme/agent"):
    return SourceConfiguration(
        provider=provider, external_id=external_id, source_type=provider,
        name=f"{provider} source", config_json={},
    )


def candidate(provider="github", external_id="701", *, url=None):
    urls = {
        "github": "https://github.com/acme/agent/releases/tag/v1",
        "arxiv": "https://arxiv.org/abs/2601.12345v1",
        "hn": "https://news.ycombinator.com/item?id=701",
    }
    return NewsCandidate(
        provider=provider, external_id=external_id,
        original_url=url or urls[provider], title="A news item",
        raw_excerpt="Bounded excerpt", published_at=NOW, fetched_at=NOW,
        metadata={"kind": provider},
    )


def collection(source_id, *items, status=CollectionStatus.SUCCESS,
               error_code=None, fetched_count=None, next_cursor=None):
    return CollectionResult(
        source_id=source_id, candidates=items, status=status,
        fetched_count=len(items) if fetched_count is None else fetched_count,
        error_code=error_code, next_cursor=next_cursor,
    )


class FakeDatabase:
    def __init__(self):
        self.sources = {}
        self.events = {}
        self.items = {}
        self.active = 0
        self.commits = 0
        self.rollbacks = 0
        self.open_sessions = 0
        self.fail_source = None
        self.fail_item = None


class FakeTransaction:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        assert self.db.active == 0
        self.snapshot = deepcopy((self.db.sources, self.db.events, self.db.items))
        self.db.active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.active -= 1
        if exc_type:
            self.db.sources, self.db.events, self.db.items = self.snapshot
            self.db.rollbacks += 1
        else:
            self.db.commits += 1


class FakeSession:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.open_sessions += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.open_sessions -= 1

    def begin(self):
        return FakeTransaction(self.db)

    async def commit(self):
        raise AssertionError("Use the transaction context, not manual commit")


class FakeRepository:
    def __init__(self, session):
        self.db = session.db

    async def ensure_source(self, config):
        assert self.db.active == 1
        if config.external_id == self.db.fail_source:
            raise RuntimeError("database password must stay private")
        key = (config.provider, config.external_id)
        return self.db.sources.setdefault(key, len(self.db.sources) + 1)

    async def ensure_event(self, event_key, title):
        assert self.db.active == 1
        return self.db.events.setdefault(event_key, len(self.db.events) + 1)

    async def upsert_item(self, item, *, event_id):
        assert self.db.active == 1
        if item.external_id == self.db.fail_item:
            raise RuntimeError("private SQL response and password")
        key = item.item_key
        if key not in self.db.items:
            self.db.items[key] = (len(self.db.items) + 1, item.source_id, event_id)
        return self.db.items[key][0]


class FakeCollector(BaseCollector):
    def __init__(self, db, result_factory):
        self.db = db
        self.result_factory = result_factory
        self.calls = []

    async def collect(self, source, *, cursor=None):
        assert self.db.active == 0
        assert self.db.open_sessions == 0
        self.calls.append((source, cursor))
        return self.result_factory(source)


def make_service(db, collectors):
    return service_module().NewsIngestionService(
        collectors=collectors, session_factory=lambda: FakeSession(db),
        repository_factory=FakeRepository,
    )


def ingest(service, *configs, cursors=None):
    request = service_module().IngestionRequest
    entries = [request(source=config, cursor=(cursors or {}).get(config.external_id))
               for config in configs]
    return asyncio.run(service.ingest(entries))


@pytest.mark.parametrize("provider,config_id,item_id", [
    ("github", "acme/agent", "701"),
    ("arxiv", "cs.AI", "2601.12345"),
    ("hn", "topstories", "802"),
])
def test_routes_three_providers_and_collects_outside_transactions(provider, config_id, item_id):
    db = FakeDatabase()
    adapter = FakeCollector(db, lambda src: collection(src.source_id, candidate(provider, item_id)))
    service = make_service(db, {provider: adapter})
    result = ingest(service, source(provider, config_id), cursors={config_id: "page-2"})

    assert result.sources[0].status is CollectionStatus.SUCCESS
    assert (result.sources[0].fetched_count, result.sources[0].accepted_count,
            result.sources[0].processed_count, result.sources[0].event_count) == (1, 1, 1, 1)
    assert adapter.calls[0][0].source_id == db.sources[(provider, config_id)]
    assert adapter.calls[0][1] == "page-2"
    assert db.commits == 2 and db.rollbacks == 0 and db.open_sessions == 0


def test_repeated_collection_is_idempotent_across_source_and_item():
    db = FakeDatabase()
    adapter = FakeCollector(db, lambda src: collection(src.source_id, candidate()))
    service = make_service(db, {"github": adapter})
    ingest(service, source())
    first = dict(db.items)
    ingest(service, source())

    assert db.items == first
    assert len(db.sources) == len(db.events) == len(db.items) == 1


def test_arxiv_and_hn_link_to_one_deterministic_event():
    db = FakeDatabase()
    arxiv = FakeCollector(db, lambda src: collection(src.source_id, candidate("arxiv", "2601.12345")))
    hn = FakeCollector(db, lambda src: collection(
        src.source_id, candidate("hn", "802", url="https://arxiv.org/abs/2601.12345v2")))
    service = make_service(db, {"arxiv": arxiv, "hn": hn})
    report = ingest(service, source("arxiv", "cs.AI"), source("hn", "topstories"))

    assert len(db.items) == 2 and len(db.events) == 1
    assert {row[2] for row in db.items.values()} == {1}
    assert all(entry.status is CollectionStatus.SUCCESS for entry in report.sources)
    assert report.processed_count == 2

    assert report.sources[0].committed_event_ids == (1,)
    assert report.sources[1].committed_event_ids == (1,)


def test_duplicate_candidates_in_one_page_are_processed_once():
    db = FakeDatabase()
    item = candidate()
    adapter = FakeCollector(db, lambda src: collection(src.source_id, item, item))
    entry = ingest(make_service(db, {"github": adapter}), source()).sources[0]

    assert (entry.accepted_count, entry.processed_count) == (2, 1)
    assert len(db.items) == 1


def test_partial_collection_saves_candidates_and_preserves_error_and_cursor():
    db = FakeDatabase()
    adapter = FakeCollector(db, lambda src: collection(
        src.source_id, candidate(), status=CollectionStatus.PARTIAL,
        error_code=CollectorErrorCode.RATE_LIMIT, fetched_count=3,
        next_cursor="3"))
    entry = ingest(make_service(db, {"github": adapter}), source()).sources[0]

    assert entry.status is CollectionStatus.PARTIAL
    assert (entry.fetched_count, entry.accepted_count, entry.processed_count) == (3, 1, 1)
    assert entry.error_code == "rate_limit" and entry.next_cursor == "3"
    assert len(db.items) == 1
    assert entry.committed_event_ids == (1,)


def test_failed_collection_is_isolated_and_does_not_open_write_transaction():
    db = FakeDatabase()
    github = FakeCollector(db, lambda src: collection(
        src.source_id, status=CollectionStatus.FAILED,
        error_code=CollectorErrorCode.TIMEOUT))
    hn = FakeCollector(db, lambda src: collection(src.source_id, candidate("hn", "802")))
    report = ingest(make_service(db, {"github": github, "hn": hn}),
                    source(), source("hn", "topstories"))

    assert [entry.status for entry in report.sources] == [CollectionStatus.FAILED, CollectionStatus.SUCCESS]
    assert report.sources[0].processed_count == 0 and report.sources[0].error_code == "timeout"
    assert len(db.items) == 1 and db.commits == 3 and db.rollbacks == 0


def test_collector_exception_is_sanitized_and_next_source_continues():
    db = FakeDatabase()
    def fail(_):
        raise RuntimeError("Authorization: Bearer secret-token")
    github = FakeCollector(db, fail)
    hn = FakeCollector(db, lambda src: collection(src.source_id, candidate("hn", "802")))
    report = ingest(make_service(db, {"github": github, "hn": hn}),
                    source(), source("hn", "topstories"))

    assert report.sources[0].status is CollectionStatus.FAILED
    assert report.sources[0].error_code == "unexpected"
    assert "secret-token" not in repr(report)
    assert report.sources[1].processed_count == 1


def test_persistence_exception_rolls_back_only_current_source():
    db = FakeDatabase()
    db.fail_item = "701"
    github = FakeCollector(db, lambda src: collection(src.source_id, candidate()))
    hn = FakeCollector(db, lambda src: collection(src.source_id, candidate("hn", "802")))
    report = ingest(make_service(db, {"github": github, "hn": hn}),
                    source(), source("hn", "topstories"))

    assert report.sources[0].status is CollectionStatus.FAILED
    assert report.sources[0].error_code == "persistence_failed"
    assert report.sources[0].processed_count == 0
    assert report.sources[0].committed_event_ids == ()
    assert report.sources[1].committed_event_ids == (1,)
    assert "password" not in repr(report)
    assert db.rollbacks == 1 and db.open_sessions == 0
    assert ("github", "701") not in db.items and len(db.events) == 1
    assert report.sources[1].processed_count == 1


def test_source_registration_failure_rolls_back_and_does_not_call_collector():
    db = FakeDatabase()
    db.fail_source = "acme/agent"
    adapter = FakeCollector(db, lambda src: collection(src.source_id, candidate()))
    entry = ingest(make_service(db, {"github": adapter}), source()).sources[0]

    assert entry.status is CollectionStatus.FAILED and entry.error_code == "persistence_failed"
    assert adapter.calls == [] and db.sources == {} and db.rollbacks == 1
    assert db.open_sessions == 0 and "password" not in repr(entry)


def test_wrong_collector_source_or_candidate_provider_is_rejected():
    db = FakeDatabase()
    wrong_id = FakeCollector(db, lambda src: collection(src.source_id + 1, candidate()))
    wrong_provider = FakeCollector(db, lambda src: collection(src.source_id, candidate("hn", "802")))
    for adapter in (wrong_id, wrong_provider):
        entry = ingest(make_service(db, {"github": adapter}), source()).sources[0]
        assert entry.status is CollectionStatus.FAILED
        assert entry.error_code == "invalid_response" and entry.processed_count == 0
    assert db.items == {} and db.events == {}


def test_unknown_provider_is_rejected_before_database_or_collector_call():
    db = FakeDatabase()
    entry = ingest(make_service(db, {}), source("other", "feed")).sources[0]

    assert entry.status is CollectionStatus.FAILED
    assert entry.error_code == "unsupported_provider"
    assert db.commits == db.rollbacks == db.open_sessions == 0


def test_unexpected_normalization_error_is_isolated_to_one_source(monkeypatch):
    db = FakeDatabase()
    module = service_module()
    original = module.normalize_candidate

    def normalize(item, source_id):
        if item.provider == "github":
            raise RuntimeError("untrusted response secret-token")
        return original(item, source_id)

    monkeypatch.setattr(module, "normalize_candidate", normalize)
    github = FakeCollector(db, lambda src: collection(src.source_id, candidate()))
    hn = FakeCollector(db, lambda src: collection(src.source_id, candidate("hn", "802")))
    report = ingest(make_service(db, {"github": github, "hn": hn}),
                    source(), source("hn", "topstories"))

    assert [entry.status for entry in report.sources] == [CollectionStatus.FAILED, CollectionStatus.SUCCESS]
    assert report.sources[0].error_code == "invalid_response"
    assert "secret-token" not in repr(report)
    assert len(db.items) == 1


def test_persistence_failure_preserves_partial_collection_diagnostics():
    db = FakeDatabase()
    db.fail_item = "701"
    adapter = FakeCollector(db, lambda src: collection(
        src.source_id, candidate(), status=CollectionStatus.PARTIAL,
        error_code=CollectorErrorCode.RATE_LIMIT, fetched_count=4,
        next_cursor="4"))
    entry = ingest(make_service(db, {"github": adapter}), source()).sources[0]

    assert entry.status is CollectionStatus.FAILED
    assert entry.collection_status is CollectionStatus.PARTIAL
    assert entry.collection_error_code == "rate_limit"
    assert (entry.fetched_count, entry.accepted_count, entry.processed_count) == (4, 1, 0)
    assert entry.next_cursor == "4" and db.items == {}
