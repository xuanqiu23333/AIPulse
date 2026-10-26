"""Research REST contract with injected executor, no network or real database."""

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from aipulse.api.main import create_app
from aipulse.research.service import ResearchBusy
from aipulse.research.state import ResearchReport, ResearchRequest, RunMetrics, TaskSnapshot


TASK_ID = "d2b36d65-3b8a-4b20-89a1-f15307af328a"


class FakeEngine:
    async def dispose(self):
        pass


class FakeExecutor:
    def __init__(self, *, ready=True, busy=False):
        self.available = ready
        self.busy = busy
        self.submissions = []
        self.closed = False
        self.status = "queued"
        self.store = self

    async def ready(self):
        return self.available

    async def submit(self, request):
        if self.busy:
            raise ResearchBusy()
        if request.event_id == 777:
            raise LookupError("event_missing")
        self.submissions.append(request)
        return TASK_ID

    async def get_task(self, task_id):
        if task_id != TASK_ID:
            return None
        return TaskSnapshot(
            task_id=task_id, request=ResearchRequest(topic="LangGraph evidence review"),
            status=self.status, stage=self.status,
            created_at=datetime(2026, 10, 21, tzinfo=timezone.utc), started_at=None,
            finished_at=None, metrics=RunMetrics(), evidence_count=0,
            report_ready=self.status == "partial", error_code=None, stop_reason=None,
        )

    async def get_evidence(self, task_id):
        return [] if task_id == TASK_ID else None

    async def get_report(self, task_id):
        return ResearchReport(topic="LangGraph evidence review", status="partial", limitations=["No verified full text."])

    async def cancel(self, task_id):
        return task_id == TASK_ID

    async def shutdown(self):
        self.closed = True


def app_for(monkeypatch, *, enabled=False, ready=True, busy=False):
    monkeypatch.setenv("AIPULSE_ENABLE_RESEARCH", "1" if enabled else "0")
    monkeypatch.delenv("AIPULSE_ENABLE_SCHEDULED_COLLECTION", raising=False)
    executor = FakeExecutor(ready=ready, busy=busy)
    app = create_app(
        settings_loader=lambda: object(), engine_factory=lambda _settings: FakeEngine(),
        session_factory_builder=lambda _engine: object(),
        research_executor_factory=lambda *_args: executor,
    )
    return app, executor


def client_for(app, peer="127.0.0.1"):
    return TestClient(app, base_url="http://127.0.0.1", client=(peer, 40000), raise_server_exceptions=False)


def test_default_disabled_and_remote_calls_cannot_create_tasks(monkeypatch):
    app, executor = app_for(monkeypatch)
    with client_for(app) as client:
        disabled = client.post("/api/v1/research/tasks", json={"topic": "LangGraph research"})
    assert disabled.status_code == 403 and executor.submissions == [] and executor.closed
    app, executor = app_for(monkeypatch, enabled=True)
    with client_for(app, peer="203.0.113.9") as client:
        remote = client.post("/api/v1/research/tasks", json={"topic": "LangGraph research"})
    assert remote.status_code == 403 and executor.submissions == []


def test_invalid_parameters_and_missing_schema_never_submit(monkeypatch):
    app, executor = app_for(monkeypatch, enabled=True)
    with client_for(app) as client:
        for payload in ({"topic": "ab"}, {"topic": "https://127.0.0.1/"},
                        {"topic": "LangGraph research", "model_url": "https://evil.example"},
                        {"topic": "LangGraph research", "event_id": "7"}):
            assert client.post("/api/v1/research/tasks", json=payload).status_code == 422
        assert client.get("/api/v1/research/tasks/not-a-uuid").status_code == 422
    assert executor.submissions == []
    app, executor = app_for(monkeypatch, enabled=True, ready=False)
    with client_for(app) as client:
        result = client.post("/api/v1/research/tasks", json={"topic": "LangGraph research"})
    assert result.status_code == 503 and result.json()["code"] == "research_schema_not_ready"
    assert executor.submissions == []


def test_create_returns_202_and_gets_only_read_existing_state(monkeypatch):
    app, executor = app_for(monkeypatch, enabled=True)
    with client_for(app) as client:
        created = client.post("/api/v1/research/tasks", json={"topic": "LangGraph research", "focus": "limitations"})
        assert created.status_code == 202 and created.json()["task_id"] == TASK_ID
        assert client.get(f"/api/v1/research/tasks/{TASK_ID}").json()["status"] == "queued"
        assert client.get(f"/api/v1/research/tasks/{TASK_ID}/evidence").json()["evidence"] == []
        assert client.get(f"/api/v1/research/tasks/{TASK_ID}/report").status_code == 409
        executor.status = "partial"
        report = client.get(f"/api/v1/research/tasks/{TASK_ID}/report")
        assert report.status_code == 200 and report.json()["status"] == "partial"
    assert len(executor.submissions) == 1


def test_busy_unknown_event_and_get_while_disabled(monkeypatch):
    app, executor = app_for(monkeypatch, enabled=True, busy=True)
    with client_for(app) as client:
        assert client.post("/api/v1/research/tasks", json={"topic": "LangGraph research"}).status_code == 409
    app, executor = app_for(monkeypatch, enabled=True)
    with client_for(app) as client:
        assert client.post("/api/v1/research/tasks", json={"topic": "LangGraph research", "event_id": 777}).status_code == 404
    app, executor = app_for(monkeypatch, enabled=False)
    with client_for(app) as client:
        assert client.get(f"/api/v1/research/tasks/{TASK_ID}").status_code == 200
    assert executor.submissions == []


def test_legacy_routes_and_new_research_routes_are_registered(monkeypatch):
    app, _ = app_for(monkeypatch)
    paths = app.openapi()["paths"]
    for path in (
        "/api/v1/news/events", "/api/v1/news/events/{event_id}/summary",
        "/api/v1/briefs/daily", "/api/v1/news/collect/status",
        "/api/v1/research/tasks", "/api/v1/research/tasks/{task_id}/report",
    ):
        assert path in paths


def test_failed_legacy_report_returns_explicit_error_without_rewriting_history(monkeypatch):
    app, executor = app_for(monkeypatch)
    executor.status = "failed"
    historical = ResearchReport(
        topic="LangGraph evidence review", status="failed", prompt_version="research-zh-v1",
        limitations=["部分步骤降级或失败：report_output_truncated"],
        metrics=RunMetrics(model_calls=2, usage_missing_calls=1),
    )

    async def get_report(_task_id):
        return historical

    async def get_task(task_id):
        return TaskSnapshot(
            task_id=task_id, request=ResearchRequest(topic="LangGraph evidence review"),
            status="failed", stage="failed", created_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
            started_at=None, finished_at=None, metrics=RunMetrics(usage_missing_calls=1),
            evidence_count=5, report_ready=True, error_code="report_output_truncated", stop_reason=None,
        )

    executor.get_report, executor.get_task = get_report, get_task
    with client_for(app) as client:
        response = client.get(f"/api/v1/research/tasks/{TASK_ID}/report")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "report_output_truncated"
    assert response.json()["prompt_version"] == "research-zh-v1"
    assert response.json()["metrics"]["token_usage"] is None
    assert response.json()["metrics"]["usage_missing_calls"] == 1
    assert historical.error_code is None
    assert executor.submissions == []


def test_executor_shutdown_failure_does_not_skip_engine_cleanup(monkeypatch, caplog):
    monkeypatch.delenv("AIPULSE_ENABLE_SCHEDULED_COLLECTION", raising=False)
    closed = []

    class Engine:
        async def dispose(self):
            closed.append(True)

    executor = FakeExecutor()

    async def fail_shutdown():
        raise RuntimeError("secret-internal-shutdown-details")

    executor.shutdown = fail_shutdown
    app = create_app(
        settings_loader=lambda: object(), engine_factory=lambda _settings: Engine(),
        session_factory_builder=lambda _engine: object(),
        research_executor_factory=lambda *_args: executor,
    )
    with client_for(app) as client:
        assert client.get("/").status_code == 200
    assert closed == [True]
    assert "secret-internal" not in caplog.text
