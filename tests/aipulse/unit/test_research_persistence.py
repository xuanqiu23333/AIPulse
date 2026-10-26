"""Real repository and executor against recorded Sessions, never a MySQL server."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from aipulse.models.entities import AgentRunLog, ResearchTask
from aipulse.models.entities import ResearchEvidence as EvidenceRow
from aipulse.llm.gateway import LLMError, TokenUsage
from aipulse.research.graph import ResearchWorkflow
from aipulse.research.repository import ResearchStore
from aipulse.research.service import ResearchBusy, ResearchExecutor
from aipulse.research.state import EvidenceCandidate, PlanDraft, ReportDraft, ResearchRequest, VerifiedClaim
from aipulse.research.tools import SearchOutcome


class Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class RecordedSession:
    def __init__(self):
        self.records = []
        self.active_contexts = 0

    async def __aenter__(self):
        self.active_contexts += 1
        return self

    async def __aexit__(self, *_args):
        self.active_contexts -= 1

    def begin(self):
        return self

    def add(self, row):
        row.id = len(self.records) + 1
        if hasattr(type(row), "created_at"):
            row.created_at = datetime(2026, 10, 21)
        self.records.append(row)

    async def flush(self):
        pass

    async def scalar(self, statement):
        if "max(agent_run_log.seq)" in str(statement):
            return max([row.seq for row in self.records if isinstance(row, AgentRunLog)] or [0])
        return 7

    async def execute(self, statement):
        description = statement.column_descriptions[0]
        entity, expression = description["entity"], description["expr"]
        rows = [row for row in self.records if isinstance(row, entity)]
        if entity is ResearchTask:
            if "research_task.status IN" in str(statement):
                rows = [row for row in rows if row.status in {"queued", "running"}]
            return Result(rows[-1:])
        if entity is AgentRunLog:
            return Result(sorted(rows, key=lambda row: row.seq, reverse=True))
        if entity is EvidenceRow:
            if expression is not EvidenceRow:
                return Result([row.citation for row in rows])
            return Result(sorted(rows, key=lambda row: row.citation))
        raise AssertionError(str(statement))


class Gateway:
    def __init__(self, session, *, report_error=None):
        self.session = session
        self.report_error = report_error

    async def generate_json(self, request, schema, *, example):
        assert self.session.active_contexts == 0, "A Session was held during a model call"
        if schema is ReportDraft and self.report_error is not None:
            raise self.report_error
        value = (PlanDraft(questions=[{"question": "LangGraph architecture", "search_query": "LangGraph architecture"}])
                 if schema is PlanDraft else ReportDraft(
                     overview=[], conclusions=[], analyses=[], trends=[], limitations=["证据有限。"],
                 ))
        return SimpleNamespace(value=value, model="deepseek-v4-pro", usage=None, elapsed_seconds=0.01)


class Retriever:
    def __init__(self, session, *, blocking=False):
        self.session = session
        self.blocking = blocking

    async def search(self, query, request):
        assert self.session.active_contexts == 0, "A Session was held during retrieval"
        if self.blocking:
            await asyncio.sleep(10)
        return SearchOutcome(candidates=[EvidenceCandidate(
            source_type="arxiv", source_id="2601.00001",
            source_url="https://arxiv.org/abs/2601.00001", title="LangGraph architecture",
            content="LangGraph architecture is described using explicit state transitions. " +
                    "This abstract provides a scoped explanation of its workflow and does not independently verify the implementation.",
            coverage="abstract", quality_note="API abstract only.",
        )])


def executor_for(session, *, blocking=False, timeout=360, broken=False, report_error=None):
    store = ResearchStore(lambda: session)
    closed = []

    async def ready():
        return True

    @asynccontextmanager
    async def resources(_store, _settings, _external):
        if broken:
            raise RuntimeError("sensitive-password-provider-error")
        try:
            yield Gateway(session, report_error=report_error), Retriever(session, blocking=blocking)
        finally:
            closed.append(True)

    executor = ResearchExecutor(
        store, ready, settings_loader=lambda: SimpleNamespace(timeout_seconds=1),
        resources=resources, task_timeout=timeout,
    )
    return executor, store, closed


def test_repository_and_executor_persist_task_evidence_report_and_monotonic_logs():
    session = RecordedSession()
    executor, store, closed = executor_for(session)

    async def run():
        task_id = await executor.submit(ResearchRequest(topic="LangGraph architecture"))
        await executor._job
        snapshot = await executor.get_task(task_id)
        evidence = await store.get_evidence(task_id)
        report = await store.get_report(task_id)
        assert snapshot.status == "partial" and snapshot.report_ready
        assert snapshot.started_at is not None and snapshot.finished_at is not None
        assert len(evidence) == 1 and report.sources[0].citation == evidence[0].citation
        assert snapshot.metrics.model_calls == 2 and snapshot.metrics.usage_missing_calls == 2
        sequences = [event.seq for event in snapshot.events]
        assert sequences == sorted(set(sequences))
        assert snapshot.events[-1].event_type == "finished"
        assert all("prompt" not in event.payload and "reasoning_content" not in event.payload for event in snapshot.events)
        assert executor.active_task_id is None
        await executor.shutdown()
    asyncio.run(run())
    assert closed == [True] and session.active_contexts == 0


def test_busy_and_cancelled_before_start_release_slot_and_persist_terminal_state():
    session = RecordedSession()
    executor, store, _ = executor_for(session)

    async def run():
        task_id = await executor.submit(ResearchRequest(topic="LangGraph architecture"))
        with pytest.raises(ResearchBusy):
            await executor.submit(ResearchRequest(topic="Another research topic"))
        assert await executor.cancel(task_id)
        assert (await store.get_task(task_id)).status == "interrupted"
        assert executor.active_task_id is None
        next_id = await executor.submit(ResearchRequest(topic="Next research topic"))
        assert next_id != task_id
        await executor.shutdown()
    asyncio.run(run())


@pytest.mark.parametrize("has_usage", [True, False])
def test_truncated_report_persists_failure_usage_evidence_and_releases_executor(has_usage):
    session = RecordedSession()
    error = LLMError("output_truncated", usage=(
        TokenUsage(prompt_tokens=13, completion_tokens=31, total_tokens=44)
        if has_usage else None
    ), elapsed_seconds=0.2)
    executor, store, closed = executor_for(session, report_error=error)

    async def run():
        task_id = await executor.submit(ResearchRequest(topic="LangGraph architecture"))
        await executor._job
        snapshot = await executor.get_task(task_id)
        report = await store.get_report(task_id)
        assert snapshot.status == report.status == "failed"
        assert snapshot.error_code == report.error_code == "report_output_truncated"
        assert snapshot.finished_at is not None
        assert snapshot.metrics.model_calls == 2
        assert snapshot.metrics.usage_missing_calls == (1 if has_usage else 2)
        assert (snapshot.metrics.token_usage.total_tokens if snapshot.metrics.token_usage else None) == (44 if has_usage else None)
        assert len(await store.get_evidence(task_id)) == snapshot.evidence_count == 1
        assert snapshot.events[-1].event_type == "finished"
        assert any(event.payload.get("error_codes") == ["report_output_truncated"] for event in snapshot.events)
        assert executor.active_task_id is None and not executor._slot.locked()
        assert session.active_contexts == 0
        await executor.shutdown()
    asyncio.run(run())
    assert closed == [True]


def test_task_deadline_releases_lock_and_preserves_safe_failure():
    session = RecordedSession()
    executor, store, closed = executor_for(session, blocking=True, timeout=1)

    async def run():
        task_id = await executor.submit(ResearchRequest(topic="LangGraph architecture"))
        await executor._job
        task = await store.get_task(task_id)
        assert task.status == "failed" and task.error_code == "task_deadline"
        assert task.metrics.search_rounds == 1
        assert executor.active_task_id is None
        await executor.shutdown()
    asyncio.run(run())
    assert closed == [True]


def test_unexpected_resource_failure_is_sanitized_and_next_task_can_start():
    session = RecordedSession()
    executor, store, _ = executor_for(session, broken=True)

    async def run():
        task_id = await executor.submit(ResearchRequest(topic="LangGraph architecture"))
        await executor._job
        task = await store.get_task(task_id)
        assert task.status == "failed" and task.error_code == "research_execution_failed"
        assert "sensitive-password" not in task.model_dump_json()
        await executor.submit(ResearchRequest(topic="Next research topic"))
        await executor.shutdown()
    asyncio.run(run())


def test_get_projects_orphan_as_interrupted_without_database_write():
    session = RecordedSession()
    executor, store, _ = executor_for(session)

    async def run():
        await store.create("00000000-0000-4000-8000-000000000001", ResearchRequest(topic="LangGraph architecture"))
        await store.start("00000000-0000-4000-8000-000000000001")
        count = len(session.records)
        snapshot = await executor.get_task("00000000-0000-4000-8000-000000000001")
        assert snapshot.status == "interrupted" and snapshot.error_code == "execution_not_active"
        assert len(session.records) == count
        assert (await store.get_task(snapshot.task_id)).status == "running"
        await store.recover_incomplete()
        assert (await store.get_task(snapshot.task_id)).status == "interrupted"
    asyncio.run(run())


def test_repository_rejects_report_claim_referencing_unsaved_evidence():
    session = RecordedSession()
    store = ResearchStore(lambda: session)

    async def run():
        task_id = "00000000-0000-4000-8000-000000000002"
        request = ResearchRequest(topic="LangGraph architecture")
        await store.create(task_id, request)
        await store.start(task_id)
        state = await ResearchWorkflow(Gateway(session), Retriever(session)).run(request)
        bad_claim = VerifiedClaim(
            text_zh="来源材料称：这是一项没有真实编号的结论。", citations=["E999"],
            support_quotes=[{"citation": "E999", "quote": "This is not a saved piece of evidence."}],
        )
        state.report.conclusions = [bad_claim]
        with pytest.raises(ValueError, match="unsaved evidence"):
            await store.complete(task_id, state)
    asyncio.run(run())


def test_read_progress_never_exposes_unallowlisted_sensitive_payload_fields():
    from aipulse.research.repository import _public_event_payload

    public = _public_event_payload({
        "model_calls": 2, "error_code": "timeout",
        "Authorization": "Bearer secret", "prompt": "private prompt",
        "reasoning_content": "internal reasoning",
    })
    assert public == {"model_calls": 2, "error_code": "timeout"}


def test_schema_diagnostic_survives_recorded_persistence_without_raw_values():
    from pydantic import ValidationError
    from aipulse.llm.validation import schema_diagnostic

    with pytest.raises(ValidationError) as caught:
        ReportDraft.model_validate({'conclusions': 'sensitive-original-response'}, strict=True)
    diagnostic = schema_diagnostic(caught.value, ReportDraft, 'provider_output')
    error = LLMError('schema_mismatch', diagnostic=diagnostic,
                     usage=TokenUsage(prompt_tokens=8, completion_tokens=12, total_tokens=20))
    session = RecordedSession()
    executor, store, closed = executor_for(session, report_error=error)

    async def run():
        task_id = await executor.submit(ResearchRequest(topic='LangGraph architecture'))
        await executor._job
        report = await store.get_report(task_id)
        assert report.status == 'failed' and report.error_code == 'report_schema_mismatch'
        assert report.schema_diagnostics == [diagnostic] and report.validation_issues == []
        assert report.metrics.model_calls == 2 and report.metrics.token_usage.total_tokens == 20
        assert 'sensitive-original-response' not in report.model_dump_json()
        assert len(await store.get_evidence(task_id)) == 1
        assert (await store.get_task(task_id)).events[-1].event_type == 'finished'
        assert not executor._slot.locked() and session.active_contexts == 0
        await executor.shutdown()
    asyncio.run(run())
    assert closed == [True]
