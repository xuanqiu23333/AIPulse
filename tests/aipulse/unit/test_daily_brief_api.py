"""Offline route and fee-boundary checks for one-day brief generation."""

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aipulse.api.main import create_app
from aipulse.news.brief import BriefCandidate, build_brief_input
from aipulse.news.repository import SummaryInputRecord, SummaryItemRecord


def prepared(excerpt="A framework update is available."):
    item = SummaryItemRecord(
        provider="github", external_id="1", title="Framework release",
        excerpt=excerpt,
        original_url="https://github.com/example/repo/releases/tag/v1",
        source_name="langchain-ai/langgraph", published_at=None,
    )
    snapshot = SummaryInputRecord(1, "Framework release", (item,), False)
    return build_brief_input(date(2026, 10, 21), [
        (BriefCandidate(1, "Framework release", "tool", 50), snapshot),
    ])


class FakeEngine:
    async def dispose(self):
        pass


def app_for(monkeypatch, *, enabled=True, evidence=None, cache=None, failure=None):
    import aipulse.api.briefs as briefs_api

    monkeypatch.setenv("AIPULSE_ENABLE_DAILY_BRIEF", "1" if enabled else "0")
    monkeypatch.delenv("AIPULSE_ENABLE_SCHEDULED_COLLECTION", raising=False)
    app = create_app(
        settings_loader=lambda: object(), engine_factory=lambda _settings: FakeEngine(),
        session_factory_builder=lambda _engine: object(),
    )
    calls = []

    async def ready(_app):
        calls.append("schema")
        return True

    async def load(_app, _date):
        calls.append("read")
        return evidence

    async def saved(_app, _date):
        calls.append("cache")
        return cache

    async def persist(_app, _date, _fingerprint, payload):
        calls.append("save")
        return True

    class FakeGateway:
        def __init__(self, _settings, *, max_retries):
            assert max_retries == 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            calls.append("closed")

        async def generate_json(self, _request, schema, *, example):
            calls.append("model")
            if failure:
                raise failure
            return SimpleNamespace(
                value=schema(
                    overview="今天记录了一项框架更新，细节以来源为准。",
                    highlights=[{"event_id": 1, "summary_zh": "框架发布了新版本，摘录未提供更多细节。"}],
                    trends=[], limitations="仅依据已存储摘录。",
                ),
                model="deepseek-v4-pro", usage=None, elapsed_seconds=0.2,
            )

    monkeypatch.setattr(briefs_api, "brief_schema_ready", ready)
    monkeypatch.setattr(briefs_api, "load_prepared", load)
    monkeypatch.setattr(briefs_api, "load_saved", saved)
    monkeypatch.setattr(briefs_api, "persist_generated", persist)
    monkeypatch.setattr(briefs_api, "load_deepseek_settings", lambda: SimpleNamespace(model="deepseek-v4-pro"))
    monkeypatch.setattr(briefs_api, "DeepSeekGateway", FakeGateway)
    return app, calls


def client_for(app, peer="127.0.0.1"):
    return TestClient(app, raise_server_exceptions=False,
                      base_url="http://127.0.0.1", client=(peer, 40000))


def test_disabled_and_nonlocal_requests_never_read_or_pay(monkeypatch):
    app, calls = app_for(monkeypatch, enabled=False, evidence=prepared())
    with client_for(app) as client:
        disabled = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    assert disabled.status_code == 403 and calls == []

    app, calls = app_for(monkeypatch, evidence=prepared())
    with client_for(app, peer="203.0.113.4") as client:
        remote = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    assert remote.status_code == 403 and calls == []


def test_empty_day_and_invalid_input_never_pay(monkeypatch):
    app, calls = app_for(monkeypatch, evidence=build_brief_input(date(2026, 10, 21), []))
    with client_for(app) as client:
        empty = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
        invalid = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21", "url": "https://x"})
        numeric = client.post("/api/v1/briefs/daily", json={"brief_date": 1792540800})
    assert empty.status_code == 200 and empty.json()["status"] == "empty"
    assert invalid.status_code == 422
    assert numeric.status_code == 422
    assert "model" not in calls and "save" not in calls


def test_single_model_call_and_backend_sources(monkeypatch):
    app, calls = app_for(monkeypatch, evidence=prepared())
    with client_for(app) as client:
        result = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    assert result.status_code == 200
    assert result.json()["highlights"][0]["sources"] == [
        "https://github.com/example/repo/releases/tag/v1",
    ]
    assert calls.count("model") == 1 and calls.count("save") == 1
    assert result.json()["cached"] is False


def test_model_failure_never_saves(monkeypatch):
    from aipulse.llm.gateway import LLMError

    app, calls = app_for(monkeypatch, evidence=prepared(), failure=LLMError("timeout"))
    with client_for(app) as client:
        result = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    assert result.status_code == 504
    assert "save" not in calls
    assert app.state.daily_brief_lock.acquire(blocking=False)
    app.state.daily_brief_lock.release()


def _saved_content(evidence, *, prompt_version=None):
    from aipulse.api.briefs import StoredDailyBrief
    from aipulse.api.schemas import DailyBriefHighlight, DailyBriefResponse
    from aipulse.news.brief import BRIEF_PROMPT_VERSION

    response = DailyBriefResponse(
        brief_date=date(2026, 10, 21), timezone="Asia/Shanghai", status="complete",
        overview="今天记录了一项框架更新，细节以来源为准。",
        highlights=(DailyBriefHighlight(
            event_id=1, title="Framework release", category="tool",
            importance_score=50, summary_zh="框架发布了新版本，摘录未提供更多细节。",
            sources=evidence.events[0].sources,
        ),),
        limitations="仅依据已存储摘录。", model="deepseek-v4-pro",
        generated_at=datetime(2026, 10, 21, tzinfo=timezone.utc), cached=False,
    )
    return StoredDailyBrief(
        prompt_version=prompt_version or BRIEF_PROMPT_VERSION,
        input_fingerprint=evidence.input_fingerprint, response=response,
    ).model_dump_json()


def test_cached_post_and_get_never_call_model(monkeypatch):
    evidence = prepared()
    app, calls = app_for(monkeypatch, enabled=True, evidence=evidence,
                         cache=_saved_content(evidence))
    with client_for(app) as client:
        post = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
        get = client.get("/api/v1/briefs/daily/2026-10-21")
    assert post.status_code == 200 and post.json()["cached"] is True
    assert get.status_code == 200 and get.json()["cached"] is True
    assert "model" not in calls and "save" not in calls


def test_old_rules_remain_readable_but_do_not_hit_new_post_cache(monkeypatch):
    evidence = prepared()
    old_content = _saved_content(evidence, prompt_version="daily-brief-zh-v1")
    app, calls = app_for(monkeypatch, evidence=evidence, cache=old_content)
    with client_for(app) as client:
        old = client.get("/api/v1/briefs/daily/2026-10-21")
        refreshed = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    assert old.status_code == 200 and old.json()["rules_current"] is False
    assert refreshed.status_code == 200 and refreshed.json()["cached"] is False
    assert calls.count("model") == 1


def test_changed_evidence_invalidates_post_cache(monkeypatch):
    previous = prepared()
    current = prepared(excerpt="The framework has a newer release with changed details.")
    app, calls = app_for(monkeypatch, evidence=current, cache=_saved_content(previous))
    with client_for(app) as client:
        result = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    assert result.status_code == 200 and result.json()["cached"] is False
    assert calls.count("model") == 1 and calls.count("save") == 1


def test_generation_failure_preserves_previous_readable_brief(monkeypatch):
    from aipulse.llm.gateway import LLMError

    previous = prepared()
    app, calls = app_for(
        monkeypatch, evidence=prepared(excerpt="Updated stored excerpt."),
        cache=_saved_content(previous), failure=LLMError("timeout"),
    )
    with client_for(app) as client:
        failed = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
        old = client.get("/api/v1/briefs/daily/2026-10-21")
    assert failed.status_code == 504 and "save" not in calls
    assert old.status_code == 200 and old.json()["cached"] is True


def test_get_rejects_untrusted_links_in_corrupt_cache(monkeypatch):
    evidence = prepared()
    corrupt = _saved_content(evidence).replace(
        "https://github.com/example/repo/releases/tag/v1", "http://127.0.0.1/private",
    )
    app, calls = app_for(monkeypatch, evidence=evidence, cache=corrupt)
    with client_for(app) as client:
        result = client.get("/api/v1/briefs/daily/2026-10-21")
    assert result.status_code == 404
    assert "model" not in calls


def test_write_rechecks_current_input_and_rejects_stale_result(monkeypatch):
    import aipulse.api.briefs as briefs_api

    original = prepared()
    changed = prepared(excerpt="A changed news excerpt arrived while the model ran.")
    payload = briefs_api.StoredDailyBrief.model_validate_json(_saved_content(original))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return self

    class Repository:
        def __init__(self, _session):
            pass

        async def get(self, _date, *, for_update):
            assert for_update
            return None

        def save(self, *_args):
            raise AssertionError("Stale brief must not be saved")

    async def current(_session, _date):
        return changed

    monkeypatch.setattr(briefs_api, "DailyBriefRepository", Repository)
    monkeypatch.setattr(briefs_api, "_prepare_in_session", current)
    app = SimpleNamespace(state=SimpleNamespace(session_factory=lambda: Session()))
    assert asyncio.run(briefs_api.persist_generated(
        app, date(2026, 10, 21), original.input_fingerprint, payload,
    )) is False


def test_second_post_while_running_returns_409(monkeypatch):
    app, calls = app_for(monkeypatch, evidence=prepared())
    assert app.state.daily_brief_lock.acquire(blocking=False)
    try:
        with client_for(app) as client:
            result = client.post("/api/v1/briefs/daily", json={"brief_date": "2026-10-21"})
    finally:
        app.state.daily_brief_lock.release()
    assert result.status_code == 409 and calls == []


def test_news_and_summary_routes_remain_registered(monkeypatch):
    app, _ = app_for(monkeypatch, evidence=prepared())
    routes = set(app.openapi()["paths"])
    assert "/api/v1/news/events" in routes
    assert "/api/v1/news/events/{event_id}/summary" in routes
    assert "/api/v1/briefs/daily" in routes
    assert "/api/v1/briefs/daily/{brief_date}" in routes
