"""Local research task creation, status polling, evidence and report retrieval."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aipulse.api.collect import _loopback_request
from aipulse.research.service import ResearchBusy, ResearchUnavailable
from aipulse.research.state import ResearchEvidence, ResearchReport, ResearchRequest, TaskSnapshot


router = APIRouter(prefix="/api/v1/research", tags=["research"])


class TaskAccepted(BaseModel):
    task_id: str
    status: Literal["queued"] = "queued"


class EvidenceResponse(BaseModel):
    task_id: str
    evidence: list[ResearchEvidence]


def _failure(code):
    return JSONResponse(status_code=503, content={
        "code": code, "message": "Research unavailable", "request_id": str(uuid4()),
    })


async def _executor(request: Request, *, write=False):
    if not _loopback_request(request) or (write and not request.app.state.research_enabled):
        raise HTTPException(status_code=403)
    if request.query_params:
        raise HTTPException(status_code=422)
    executor = request.app.state.research_executor
    if executor is None:
        raise ResearchUnavailable()
    if not await executor.ready():
        raise ResearchUnavailable("research_schema_not_ready")
    return executor


@router.post("/tasks", response_model=TaskAccepted, status_code=202)
async def create_research_task(body: ResearchRequest, request: Request):
    try:
        executor = await _executor(request, write=True)
        task_id = await executor.submit(body)
        return TaskAccepted(task_id=task_id)
    except ResearchBusy:
        raise HTTPException(status_code=409) from None
    except ResearchUnavailable as error:
        return _failure(error.code)
    except LookupError:
        raise HTTPException(status_code=404) from None
    except HTTPException:
        raise
    except Exception:
        return _failure("research_storage_error")


@router.get("/tasks/{task_id}", response_model=TaskSnapshot)
async def get_research_task(task_id: UUID, request: Request):
    try:
        executor = await _executor(request)
        result = await executor.get_task(str(task_id))
        if result is None:
            raise HTTPException(status_code=404)
        return result
    except ResearchUnavailable as error:
        return _failure(error.code)
    except HTTPException:
        raise
    except Exception:
        return _failure("research_storage_error")


@router.get("/tasks/{task_id}/evidence", response_model=EvidenceResponse)
async def get_research_evidence(task_id: UUID, request: Request):
    try:
        executor = await _executor(request)
        evidence = await executor.store.get_evidence(str(task_id))
        if evidence is None:
            raise HTTPException(status_code=404)
        return EvidenceResponse(task_id=str(task_id), evidence=evidence)
    except ResearchUnavailable as error:
        return _failure(error.code)
    except HTTPException:
        raise
    except Exception:
        return _failure("research_storage_error")


@router.get("/tasks/{task_id}/report", response_model=ResearchReport)
async def get_research_report(task_id: UUID, request: Request):
    try:
        executor = await _executor(request)
        snapshot = await executor.get_task(str(task_id))
        if snapshot is None:
            raise HTTPException(status_code=404)
        if snapshot.status in {"queued", "running"}:
            raise HTTPException(status_code=409)
        report = await executor.store.get_report(str(task_id))
        if report is None:
            raise HTTPException(status_code=404)
        if snapshot.status == "failed" or report.status == "failed":
            # Add failure semantics for legacy payloads without rewriting history.
            report = report.model_copy(update={
                "status": "failed",
                "error_code": report.error_code or snapshot.error_code or "research_failed",
            })
        return report
    except ResearchUnavailable as error:
        return _failure(error.code)
    except HTTPException:
        raise
    except Exception:
        return _failure("research_storage_error")


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_research(task_id: UUID, request: Request):
    try:
        executor = await _executor(request, write=True)
        if await request.body():
            raise HTTPException(status_code=422)
        if not await executor.cancel(str(task_id)):
            raise HTTPException(status_code=409)
        return {"task_id": str(task_id), "status": "cancelling"}
    except ResearchUnavailable as error:
        return _failure(error.code)
    except HTTPException:
        raise
    except Exception:
        return _failure("research_storage_error")
