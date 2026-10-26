"""Short SQL transactions for the existing three research tables and local search."""

from datetime import datetime, timezone
import re

from pydantic import ValidationError
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import undefer

from aipulse.models.entities import AgentRunLog, NewsEvent, NewsItem, NewsSource, ResearchTask
from aipulse.models.entities import ResearchEvidence as EvidenceRow
from aipulse.news.manual_run import schema_ready
from aipulse.research.state import (
    MAX_EVIDENCE, RESEARCH_REVISION, EvidenceCandidate, ProgressEvent,
    ResearchEvidence, ResearchRequest, ResearchState, RunMetrics, TaskPayload, TaskSnapshot,
)
from aipulse.research.tools import search_terms


_TERMINAL = {"completed", "partial", "failed", "interrupted"}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc(value):
    return None if value is None else value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _public_event_payload(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in ("schema_version", "search_rounds", "supplementary_rounds", "evidence_count", "model_calls"):
        if type(value.get(key)) is int and 0 <= value[key] <= 100:
            result[key] = value[key]
    for key in ("error_code", "status"):
        if isinstance(value.get(key), str) and re.fullmatch(r"[a-z_]{1,64}", value[key]):
            result[key] = value[key]
    if isinstance(value.get("error_codes"), list):
        result["error_codes"] = [code for code in value["error_codes"][:20]
                                 if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code)]
    return result


async def research_schema_ready(engine) -> bool:
    try:
        if not await schema_ready(engine, require_app_identity=True, require_summary_cache=True):
            return False
        async with engine.connect() as connection:
            versions = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
            if versions != [RESEARCH_REVISION]:
                return False
            columns = (await connection.execute(text(
                "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND ((TABLE_NAME = 'research_task' AND COLUMN_NAME = 'result_json') "
                "OR (TABLE_NAME = 'research_evidence' AND COLUMN_NAME = 'metadata_json'))"
            ))).all()
            return {tuple(row) for row in columns} == {
                ("research_task", "result_json", "json", "YES"),
                ("research_evidence", "metadata_json", "json", "YES"),
            }
    except Exception:
        return False


def _payload(task: ResearchTask) -> TaskPayload:
    try:
        return TaskPayload.model_validate(task.result_json)
    except (ValidationError, TypeError, ValueError):
        return TaskPayload(request=ResearchRequest(topic="历史研究任务"), stage="legacy_task", error_code="legacy_payload")


class ResearchStore:
    """The graph never receives a Session; every method opens and closes its own."""

    def __init__(self, session_factory):
        self._sessions = session_factory

    async def _task(self, session, task_id, *, lock=False):
        statement = select(ResearchTask).options(undefer(ResearchTask.result_json)).where(ResearchTask.task_id == task_id)
        if lock:
            statement = statement.with_for_update()
        return (await session.execute(statement)).scalars().one_or_none()

    async def _event(self, session, task, node, event_type, payload):
        seq = (await session.scalar(select(func.max(AgentRunLog.seq)).where(AgentRunLog.task_id == task.task_id)) or 0) + 1
        session.add(AgentRunLog(
            task_id=task.task_id, seq=seq, node_name=node,
            event_type=event_type, payload_json=payload,
        ))

    async def create(self, task_id: str, request: ResearchRequest):
        async with self._sessions() as session:
            async with session.begin():
                if request.event_id is not None:
                    exists = await session.scalar(select(NewsEvent.id).where(
                        NewsEvent.id == request.event_id, NewsEvent.status.in_(("active", "needs_review")),
                    ))
                    if exists is None:
                        raise LookupError("event_not_found")
                task = ResearchTask(
                    task_id=task_id, event_id=request.event_id, query=request.topic,
                    status="queued", result_json=TaskPayload(request=request).model_dump(mode="json"),
                )
                session.add(task)
                await session.flush()
                await self._event(session, task, "queued", "queued", {"schema_version": 1})

    async def start(self, task_id: str):
        async with self._sessions() as session:
            async with session.begin():
                task = await self._task(session, task_id, lock=True)
                if task is None or task.status != "queued":
                    raise RuntimeError("task_not_queued")
                task.status = "running"
                task.started_at = _now()
                payload = _payload(task).model_copy(update={"stage": "running"})
                task.result_json = payload.model_dump(mode="json")

    async def _save_evidence(self, session, task, evidence):
        existing = (await session.execute(
            select(EvidenceRow).options(undefer(EvidenceRow.metadata_json))
            .where(EvidenceRow.research_task_id == task.id).with_for_update()
        )).scalars().all()
        by_citation = {row.citation: row for row in existing}
        for item in evidence:
            metadata = item.model_dump(mode="json", exclude={"source_url", "title", "content", "citation"})
            row = by_citation.get(item.citation)
            if row is None:
                row = EvidenceRow(research_task_id=task.id, citation=item.citation)
                session.add(row)
            row.source_url = item.source_url
            row.title = item.title
            row.content = item.content
            row.metadata_json = metadata

    async def progress(self, task_id: str, node: str, state: ResearchState):
        async with self._sessions() as session:
            async with session.begin():
                task = await self._task(session, task_id, lock=True)
                if task is None or task.status in _TERMINAL:
                    return
                await self._save_evidence(session, task, state.evidence)
                payload = _payload(task).model_copy(update={
                    "stage": node, "metrics": state.metrics, "evidence_count": len(state.evidence),
                    "stop_reason": state.stop_reason,
                })
                task.result_json = payload.model_dump(mode="json")
                await self._event(session, task, node, "progress", {
                    "search_rounds": state.rounds, "supplementary_rounds": state.supplements,
                    "evidence_count": len(state.evidence), "model_calls": state.metrics.model_calls,
                    "error_codes": state.errors,
                })

    async def complete(self, task_id: str, state: ResearchState):
        if state.report is None:
            raise ValueError("Validated report required")
        async with self._sessions() as session:
            async with session.begin():
                task = await self._task(session, task_id, lock=True)
                if task is None or task.status != "running":
                    raise RuntimeError("task_not_running")
                await self._save_evidence(session, task, state.evidence)
                await session.flush()
                saved = set((await session.execute(select(EvidenceRow.citation).where(
                    EvidenceRow.research_task_id == task.id,
                ))).scalars().all())
                if {source.citation for source in state.report.sources} != saved:
                    raise ValueError("Report/evidence persistence mismatch")
                claims = (state.report.overview + state.report.conclusions + state.report.trends
                          + [claim for section in state.report.analyses for claim in section.claims])
                if any(
                    not claim.citations or not set(claim.citations) <= saved
                    or set(claim.citations) != {support.citation for support in claim.support_quotes}
                    for claim in claims
                ):
                    raise ValueError("Report claim references unsaved evidence")
                task.status = state.report.status
                task.finished_at = _now()
                task.result_json = TaskPayload(
                    request=state.request, stage=task.status, report=state.report,
                    metrics=state.metrics, evidence_count=len(state.evidence),
                    stop_reason=state.stop_reason,
                    error_code=state.report.error_code or next((code for code in state.errors if code.startswith("report_")),
                                    state.errors[0] if state.errors else None),
                ).model_dump(mode="json")
                await self._event(session, task, "persist_result", "finished", {
                    "status": task.status, "evidence_count": len(state.evidence),
                    "model_calls": state.metrics.model_calls,
                })

    async def fail(self, task_id: str, error_code: str, metrics: RunMetrics | None = None, *, interrupted=False):
        async with self._sessions() as session:
            async with session.begin():
                task = await self._task(session, task_id, lock=True)
                if task is None or task.status in _TERMINAL:
                    return
                task.status = "interrupted" if interrupted else "failed"
                task.finished_at = _now()
                payload = _payload(task).model_copy(update={
                    "stage": task.status, "error_code": error_code,
                    "metrics": metrics or _payload(task).metrics,
                })
                task.result_json = payload.model_dump(mode="json")
                await self._event(session, task, "executor", "failed", {"error_code": error_code})

    async def recover_incomplete(self):
        """Called only by an explicitly enabled new POST, never by GET/import."""
        async with self._sessions() as session:
            async with session.begin():
                tasks = (await session.execute(
                    select(ResearchTask).options(undefer(ResearchTask.result_json))
                    .where(ResearchTask.status.in_(("queued", "running")))
                    .order_by(ResearchTask.id).limit(100).with_for_update()
                )).scalars().all()
                for task in tasks:
                    task.status = "interrupted"
                    task.finished_at = _now()
                    task.result_json = _payload(task).model_copy(update={
                        "stage": "interrupted", "error_code": "process_restart",
                    }).model_dump(mode="json")
                    await self._event(session, task, "executor", "interrupted", {"error_code": "process_restart"})

    async def get_task(self, task_id: str) -> TaskSnapshot | None:
        async with self._sessions() as session:
            task = await self._task(session, task_id)
            if task is None:
                return None
            payload = _payload(task)
            logs = (await session.execute(select(AgentRunLog).where(
                AgentRunLog.task_id == task_id,
            ).order_by(AgentRunLog.seq.desc()).limit(50))).scalars().all()
            return TaskSnapshot(
                task_id=task.task_id, request=payload.request, status=task.status,
                stage=payload.stage, created_at=_utc(task.created_at),
                started_at=_utc(task.started_at), finished_at=_utc(task.finished_at),
                metrics=payload.metrics, evidence_count=payload.evidence_count,
                report_ready=payload.report is not None, error_code=payload.error_code,
                stop_reason=payload.stop_reason,
                events=[ProgressEvent(
                    seq=row.seq, node_name=row.node_name, event_type=row.event_type,
                    payload=_public_event_payload(row.payload_json), created_at=_utc(row.created_at),
                ) for row in reversed(logs)],
            )

    async def get_report(self, task_id: str):
        async with self._sessions() as session:
            task = await self._task(session, task_id)
            return None if task is None else _payload(task).report

    async def get_evidence(self, task_id: str) -> list[ResearchEvidence] | None:
        async with self._sessions() as session:
            task = await self._task(session, task_id)
            if task is None:
                return None
            rows = (await session.execute(
                select(EvidenceRow).options(undefer(EvidenceRow.metadata_json))
                .where(EvidenceRow.research_task_id == task.id)
                .order_by(EvidenceRow.citation).limit(MAX_EVIDENCE)
            )).scalars().all()
            evidence = []
            for row in rows:
                if not isinstance(row.metadata_json, dict):
                    continue
                try:
                    evidence.append(ResearchEvidence.model_validate({
                        **row.metadata_json, "source_url": row.source_url,
                        "title": row.title, "content": row.content or "", "citation": row.citation,
                    }))
                except ValidationError:
                    continue
            return evidence

    async def search_news(self, query: str, event_id: int | None) -> list[EvidenceCandidate]:
        terms = search_terms(query)
        filters = [or_(NewsItem.title.icontains(term, autoescape=True), NewsItem.content.icontains(term, autoescape=True)) for term in terms]
        if event_id is not None:
            filters.append(NewsEvent.id == event_id)
        if not filters:
            return []
        async with self._sessions() as session:
            rows = (await session.execute(
                select(NewsItem, NewsSource.name)
                .join(NewsEvent, NewsItem.event_id == NewsEvent.id)
                .join(NewsSource, NewsItem.source_id == NewsSource.id)
                .where(NewsEvent.status.in_(("active", "needs_review")), or_(*filters))
                .order_by(
                    case((NewsEvent.id == event_id, 0), else_=1) if event_id else NewsEvent.importance_score.desc(),
                    NewsEvent.importance_score.desc(), NewsItem.id.desc(),
                ).limit(12)
            )).all()
            result = []
            for item, source_name in rows:
                try:
                    result.append(EvidenceCandidate(
                        source_type="stored_news", source_id=f"{item.provider}:{item.external_id}"[:191],
                        source_url=item.url, title=item.title, content=(item.content or "")[:2000],
                        coverage="excerpt" if item.content else "title_only",
                        quality_note=f"Stored {item.provider} collector excerpt; no new full-page fetch or independent verification.",
                        published_at=_utc(item.published_at), retrieved_at=_utc(item.fetched_at),
                    ))
                except ValidationError:
                    continue
                if len(result) == 3:
                    break
            return result
