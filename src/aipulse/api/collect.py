"""Locally gated manual collection of three fixed news sources."""

import ipaddress
import os
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request

from aipulse.api.schemas import (
    ManualCollectionRequest, ManualCollectionResponse,
    ManualCollectionSourceResponse, ManualScoreFailureResponse,
    ScheduledCollectionStatus,
)
from aipulse.news.manual_run import build_collectors, run_collection, schema_ready
from aipulse.news.repository import SourceConfiguration
from aipulse.news.service import IngestionRequest
from aipulse.news.types import CollectionStatus


router = APIRouter(prefix="/api/v1/news", tags=["news"])

_SOURCES = {
    "github": "langchain-ai/langgraph",
    "arxiv": "cat:cs.AI",
    "hn": "topstories",
}
_SAFE_ERROR_CODES = {
    "timeout", "rate_limit", "invalid_response", "unavailable", "unexpected",
    "persistence_failed", "unsupported_provider", "event_missing",
    "identity_conflict", "invalid_snapshot",
}
_HTTP_TIMEOUT = 10.0


class CollectionPreflightError(RuntimeError):
    """The fixed development database is not ready for news writes."""


def _loopback_request(request: Request) -> bool:
    """Require local peer/Host and reject browser requests from external sites."""
    if request.client is None:
        return False
    try:
        peer = ipaddress.ip_address(request.client.host)
        host = request.url.hostname
        if request.headers.get("sec-fetch-site") == "cross-site":
            return False
        origin_header = request.headers.get("origin")
        if origin_header is not None:
            origin = urlsplit(origin_header)
            if (origin.scheme not in {"http", "https"}
                    or origin.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or origin.username is not None or origin.password is not None
                    or origin.path or origin.query or origin.fragment):
                return False
            # Validate malformed/out-of-range ports without logging the header.
            _ = origin.port
        return peer.is_loopback and host in {"127.0.0.1", "localhost", "::1"}
    except ValueError:
        return False


def _safe_code(code: str | None) -> str | None:
    return code if code in _SAFE_ERROR_CODES else ("unexpected" if code else None)


def _response(report) -> ManualCollectionResponse:
    sources = tuple(
        ManualCollectionSourceResponse(
            provider=source.provider,
            status=source.status.value,
            fetched_count=source.fetched_count,
            processed_count=source.processed_count,
            event_count=source.event_count,
            error_code=_safe_code(source.error_code),
        )
        for source in report.ingestion.sources
    )
    scored = sum(outcome.score is not None for outcome in report.scores)
    scoring_failed = len(report.scores) - scored
    scoring_errors = tuple(
        ManualScoreFailureResponse(
            event_id=outcome.event_id,
            error_code=_safe_code(outcome.error_code) or "unexpected",
        )
        for outcome in report.scores if outcome.score is None
    )
    if sources and all(source.status == CollectionStatus.FAILED.value for source in sources):
        status = CollectionStatus.FAILED.value
    elif scoring_failed or any(source.status != CollectionStatus.SUCCESS.value for source in sources):
        status = CollectionStatus.PARTIAL.value
    else:
        status = CollectionStatus.SUCCESS.value
    return ManualCollectionResponse(
        status=status, sources=sources,
        processed_count=report.ingestion.processed_count,
        events_scored=scored, scoring_failed=scoring_failed,
        scoring_errors=scoring_errors,
    )


async def run_fixed_collection(
    application: FastAPI, providers: tuple[str, ...] | list[str], limit_per_source: int,
) -> ManualCollectionResponse:
    """Run fixed sources after the caller acquires the instance collection lock."""
    try:
        ready = await schema_ready(application.state.engine, require_app_identity=True)
    except Exception:
        raise CollectionPreflightError("Development schema is unavailable") from None
    if not ready:
        raise CollectionPreflightError("Development schema is not ready")

    requests = tuple(
        IngestionRequest(SourceConfiguration(
            provider=provider,
            external_id=_SOURCES[provider],
            source_type=provider,
            name=_SOURCES[provider],
        ))
        for provider in providers
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT)) as client:
        collectors = build_collectors(
            client, providers=providers,
            limit_per_source=limit_per_source,
            http_timeout=_HTTP_TIMEOUT,
            github_token=os.environ.get("GITHUB_TOKEN"),
        )
        report = await run_collection(
            requests, collectors=collectors,
            session_factory=application.state.session_factory,
        )
    return _response(report)


@router.post("/collect", response_model=ManualCollectionResponse)
async def collect_news(body: ManualCollectionRequest, request: Request) -> ManualCollectionResponse:
    """Run one bounded batch when explicitly enabled on the local app instance."""
    if not request.app.state.manual_collection_enabled or not _loopback_request(request):
        raise HTTPException(status_code=403)

    lock = request.app.state.manual_collection_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409)
    try:
        try:
            return await run_fixed_collection(
                request.app, body.providers, body.limit_per_source,
            )
        except CollectionPreflightError:
            raise HTTPException(status_code=503) from None
    finally:
        lock.release()


@router.get("/collect/status", response_model=ScheduledCollectionStatus)
async def get_scheduled_collection_status(request: Request) -> ScheduledCollectionStatus:
    """Show the latest in-memory scheduler attempt to local callers."""
    if not _loopback_request(request):
        raise HTTPException(status_code=403)
    state = request.app.state
    return ScheduledCollectionStatus(
        enabled=state.scheduled_collection_enabled,
        active=state.scheduled_collection_active,
        running=not state.scheduled_collection_idle.is_set(),
        last_run=state.scheduled_collection_last_run,
    )
