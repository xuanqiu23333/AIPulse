"""Offline checks for the opt-in single-process news scheduler."""

import asyncio
from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aipulse.api.main import create_app
from aipulse.api.schemas import ManualCollectionResponse, ManualCollectionSourceResponse


class FakeEngine:
    async def dispose(self):
        pass


def app_without_database(monkeypatch, *, enabled=False):
    if enabled:
        monkeypatch.setenv("AIPULSE_ENABLE_SCHEDULED_COLLECTION", "1")
    else:
        monkeypatch.delenv("AIPULSE_ENABLE_SCHEDULED_COLLECTION", raising=False)
    return create_app(
        settings_loader=lambda: object(),
        engine_factory=lambda _settings: FakeEngine(),
        session_factory_builder=lambda _engine: object(),
    )


def test_scheduler_disabled_by_default_and_status_is_empty(monkeypatch):
    import aipulse.api.main as main

    monkeypatch.setenv("AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS", "invalid-but-disabled")
    monkeypatch.setattr(main, "start_collection_scheduler", lambda _app: (_ for _ in ()).throw(
        AssertionError("scheduler must not start")))
    app = app_without_database(monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 40000)) as client:
        response = client.get("/api/v1/news/collect/status")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False, "active": False, "running": False, "last_run": None,
    }


def test_enabled_lifespan_starts_and_stops_scheduler(monkeypatch):
    import aipulse.api.main as main

    calls = []

    class FakeScheduler:
        def shutdown(self, *, wait):
            calls.append(("shutdown", wait))

    def start(_app):
        calls.append(("start", None))
        return FakeScheduler()

    monkeypatch.setattr(main, "start_collection_scheduler", start)
    app = app_without_database(monkeypatch, enabled=True)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 40000)) as client:
        response = client.get("/api/v1/news/collect/status")
        assert response.json()["active"] is True
    assert calls == [("start", None), ("shutdown", False)]


@pytest.mark.asyncio
async def test_lifespan_waits_for_scheduled_work_before_closing_engine(monkeypatch):
    import aipulse.api.main as main

    events = []
    monkeypatch.setenv("AIPULSE_ENABLE_SCHEDULED_COLLECTION", "1")

    class Engine:
        async def dispose(self):
            events.append("engine_closed")

    class Scheduler:
        def __init__(self, app):
            self.app = app
            app.state.scheduled_collection_idle.clear()

        def shutdown(self, *, wait):
            events.append("scheduler_stopped")
            loop = asyncio.get_running_loop()
            loop.call_soon(self.finish)

        def finish(self):
            events.append("job_finished")
            self.app.state.scheduled_collection_idle.set()

    monkeypatch.setattr(main, "start_collection_scheduler", Scheduler)
    app = create_app(
        settings_loader=lambda: object(), engine_factory=lambda _settings: Engine(),
        session_factory_builder=lambda _engine: object(),
    )
    async with app.router.lifespan_context(app):
        pass
    assert events == ["scheduler_stopped", "job_finished", "engine_closed"]


@pytest.mark.parametrize("initial_delay", (None, "120"))
def test_scheduler_registers_one_hourly_non_overlapping_job(monkeypatch, initial_delay):
    from apscheduler.triggers.interval import IntervalTrigger
    from aipulse.api.scheduler import start_collection_scheduler

    if initial_delay is None:
        monkeypatch.delenv("AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS", raising=False)
    else:
        monkeypatch.setenv("AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS", initial_delay)
    seen = []

    class FakeScheduler:
        def __init__(self, **kwargs):
            seen.append(("construct", kwargs))

        def add_job(self, func, trigger, **kwargs):
            seen.append(("add", func, trigger, kwargs))

        def start(self):
            seen.append(("start", None))

    app = SimpleNamespace(state=SimpleNamespace())
    before = datetime.now(timezone.utc)
    start_collection_scheduler(app, scheduler_factory=FakeScheduler)
    after = datetime.now(timezone.utc)
    _, job, trigger, options = seen[1]
    assert isinstance(trigger, IntervalTrigger)
    first = trigger.get_next_fire_time(None, before)
    delay = int(initial_delay) if initial_delay else 3600
    assert before + timedelta(seconds=delay) <= first <= after + timedelta(seconds=delay)
    assert trigger.get_next_fire_time(first, first) - first == timedelta(seconds=3600)
    assert options["max_instances"] == 1 and options["coalesce"] is True
    assert options["args"] == [app]
    assert len([call for call in seen if call[0] == "add"]) == 1
    assert seen[-1] == ("start", None)


@pytest.mark.parametrize("bad_delay", ("0", "-1", "3601", "abc", "1.5", " 120", "0120"))
def test_invalid_initial_delay_rejects_scheduler_start(monkeypatch, bad_delay):
    from aipulse.api.scheduler import (
        ScheduledCollectionConfigurationError, start_collection_scheduler,
    )

    monkeypatch.setenv("AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS", bad_delay)

    def factory(**_kwargs):
        raise AssertionError("invalid configuration must be rejected before scheduler creation")

    with pytest.raises(ScheduledCollectionConfigurationError, match="AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS"):
        start_collection_scheduler(SimpleNamespace(), scheduler_factory=factory)


def test_invalid_initial_delay_fails_enabled_app_startup(monkeypatch):
    from aipulse.api.scheduler import ScheduledCollectionConfigurationError

    monkeypatch.setenv("AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS", "not-an-integer")
    app = app_without_database(monkeypatch, enabled=True)
    with pytest.raises(ScheduledCollectionConfigurationError, match="AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS"):
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 40000)):
            pass


def fake_scheduled_app():
    idle = asyncio.Event()
    idle.set()
    return SimpleNamespace(state=SimpleNamespace(
        manual_collection_lock=Lock(), scheduled_collection_idle=idle,
        scheduled_collection_last_run=None,
    ))


def test_scheduled_run_records_sources_counts_and_safe_failures(monkeypatch):
    import aipulse.api.scheduler as scheduler

    app = fake_scheduled_app()
    calls = []

    async def run(_app, providers, limit_per_source):
        calls.append((providers, limit_per_source))
        return ManualCollectionResponse(
            status="partial",
            sources=(ManualCollectionSourceResponse(
                provider="github", status="partial", fetched_count=5,
                processed_count=4, event_count=4, error_code="invalid_response",
            ),),
            processed_count=4, events_scored=4, scoring_failed=0,
            scoring_errors=(),
        )

    monkeypatch.setattr(scheduler, "run_fixed_collection", run)
    asyncio.run(scheduler.run_scheduled_collection(app))
    state = app.state.scheduled_collection_last_run
    assert calls == [(('github', 'arxiv', 'hn'), 5)]
    assert state.status == "partial" and state.processed_count == 4
    assert state.sources[0].provider == "github"
    assert state.failure_codes == ("invalid_response",)
    assert state.started_at.tzinfo is timezone.utc
    assert state.finished_at is not None
    assert app.state.manual_collection_lock.acquire(blocking=False)
    app.state.manual_collection_lock.release()


def test_scheduled_overlap_skips_without_running_collection(monkeypatch):
    import aipulse.api.scheduler as scheduler

    app = fake_scheduled_app()
    assert app.state.manual_collection_lock.acquire(blocking=False)
    try:
        asyncio.run(scheduler.run_scheduled_collection(app))
        state = app.state.scheduled_collection_last_run
        assert state.status == "skipped" and state.failure_codes == ("overlap",)
    finally:
        app.state.manual_collection_lock.release()


def test_scheduled_failure_is_contained_and_releases_lock(monkeypatch, caplog):
    import aipulse.api.scheduler as scheduler

    for error_type, code in (
        (scheduler.CollectionPreflightError, "schema_not_ready"),
        (RuntimeError, "unexpected"),
    ):
        app = fake_scheduled_app()

        async def fail(*_args):
            raise error_type("sensitive detail must not leak")

        monkeypatch.setattr(scheduler, "run_fixed_collection", fail)
        asyncio.run(scheduler.run_scheduled_collection(app))
        state = app.state.scheduled_collection_last_run
        assert state.status == "failed" and state.failure_codes == (code,)
        assert state.finished_at is not None and app.state.scheduled_collection_idle.is_set()
        assert app.state.manual_collection_lock.acquire(blocking=False)
        app.state.manual_collection_lock.release()
    assert "sensitive detail must not leak" not in caplog.text
