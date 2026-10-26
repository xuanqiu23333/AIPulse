"""Local, opt-in, one-event AI summary with validated database cache."""

from datetime import datetime, timezone
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from aipulse.api.collect import _loopback_request
from aipulse.api.schemas import NewsSummaryResponse
from aipulse.llm.config import LLMConfigurationError, load_deepseek_settings
from aipulse.llm.gateway import DeepSeekGateway, GenerationRequest, LLMError
from aipulse.news.manual_run import schema_ready
from aipulse.news.repository import NewsRepository, SummaryInputRecord
from aipulse.news.summary import (
    SUMMARY_PROMPT_VERSION, PreparedSummaryInput, SummaryCachePayload,
    SummaryDraft, SummarySourceUnavailable, prepare_summary_input,
)


router = APIRouter(prefix="/api/v1/news", tags=["news"])
_MAX_OUTPUT_TOKENS = 2048
_SAFE_MODEL_ERRORS = {
    "authentication_failed", "insufficient_balance", "rate_limited",
    "service_unavailable", "connection_error", "timeout", "invalid_request",
    "request_rejected", "empty_response", "invalid_json", "schema_mismatch",
    "output_truncated", "invalid_response", "provider_error",
}
_EXAMPLE = SummaryDraft(
    summary_zh="示例记录介绍了一项示例更新，具体事实以输入记录为准。",
    key_points=["示例记录提及一项更新。"],
    limitations="此处仅演示 JSON 结构，不代表当前新闻事实。",
)


def _safe_failure(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code, "message": "AI summary could not be generated",
            "request_id": str(uuid4()),
        },
    )


async def _summary_schema_available(application: FastAPI) -> bool:
    try:
        return await schema_ready(
            application.state.engine,
            require_app_identity=True, require_summary_cache=True,
        )
    except Exception:
        return False


async def load_summary_input(application: FastAPI, event_id: int) -> SummaryInputRecord | None:
    """Close the read Session before any paid network operation begins."""

    async with application.state.session_factory() as session:
        return await NewsRepository(session).get_event_summary_input(event_id)


def _cached_response(
    snapshot: SummaryInputRecord, prepared: PreparedSummaryInput, *, model: str | None,
) -> NewsSummaryResponse | None:
    cache = snapshot.cache
    if (cache is None or cache.input_fingerprint != prepared.input_fingerprint
            or cache.prompt_version != SUMMARY_PROMPT_VERSION
            or (model is not None and cache.model != model)):
        return None
    try:
        payload = SummaryCachePayload.model_validate(cache.payload)
    except (ValidationError, TypeError, ValueError):
        return None
    return _summary_response(snapshot, prepared, payload, model=cache.model, cached=True)


def _summary_response(
    snapshot: SummaryInputRecord, prepared: PreparedSummaryInput,
    payload: SummaryCachePayload, *, model: str, cached: bool,
) -> NewsSummaryResponse:
    return NewsSummaryResponse(
        event_id=snapshot.event_id, title=snapshot.title,
        summary_zh=payload.draft.summary_zh,
        key_points=payload.draft.key_points,
        limitations=f"{payload.draft.limitations} {prepared.limitations}",
        sources=prepared.sources, model=model,
        token_usage=payload.token_usage,
        latency_ms=payload.latency_ms, cached=cached,
    )


async def persist_summary(
    application: FastAPI, event_id: int, fingerprint: str,
    model: str, prompt_version: str, payload: SummaryCachePayload,
) -> bool:
    """Recheck locked current evidence in a fresh, short write transaction."""

    async with application.state.session_factory() as session:
        async with session.begin():
            repository = NewsRepository(session)
            current = await repository.get_event_summary_input(event_id, for_update=True)
            if current is None:
                return False
            try:
                current_prepared = prepare_summary_input(current)
            except SummarySourceUnavailable:
                return False
            if current_prepared.input_fingerprint != fingerprint:
                return False
            await repository.save_event_summary_cache(
                event_id, payload=payload.model_dump(mode="json"), model=model,
                prompt_version=prompt_version, input_fingerprint=fingerprint,
                generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            return True


@router.get("/events/{event_id}/summary", response_model=NewsSummaryResponse)
async def get_news_event_summary(
    event_id: Annotated[int, Path(ge=1)], request: Request,
) -> NewsSummaryResponse | JSONResponse:
    """Read a current validated cache; this path never loads model settings."""

    if not _loopback_request(request):
        raise HTTPException(status_code=403)
    if request.query_params:
        raise HTTPException(status_code=422)
    if not await _summary_schema_available(request.app):
        return _safe_failure(503, "summary_schema_not_ready")
    try:
        snapshot = await load_summary_input(request.app, event_id)
    except Exception:
        return _safe_failure(503, "summary_storage_error")
    if snapshot is None:
        raise HTTPException(status_code=404)
    try:
        prepared = prepare_summary_input(snapshot)
    except SummarySourceUnavailable:
        raise HTTPException(status_code=404) from None
    cached = _cached_response(snapshot, prepared, model=None)
    if cached is None:
        raise HTTPException(status_code=404)
    return cached


@router.post("/events/{event_id}/summary", response_model=NewsSummaryResponse)
async def summarize_news_event(
    event_id: Annotated[int, Path(ge=1)], request: Request,
) -> NewsSummaryResponse | JSONResponse:
    """Return current cache or generate, recheck, and persist one summary."""

    if not request.app.state.ai_summary_enabled or not _loopback_request(request):
        raise HTTPException(status_code=403)
    if request.query_params or await request.body():
        raise HTTPException(status_code=422)
    lock = request.app.state.ai_summary_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409)
    try:
        if not await _summary_schema_available(request.app):
            return _safe_failure(503, "summary_schema_not_ready")
        try:
            snapshot = await load_summary_input(request.app, event_id)
        except Exception:
            return _safe_failure(503, "summary_storage_error")
        if snapshot is None:
            raise HTTPException(status_code=404)
        try:
            prepared = prepare_summary_input(snapshot)
        except SummarySourceUnavailable:
            return _safe_failure(503, "summary_source_unavailable")
        try:
            settings = load_deepseek_settings()
        except LLMConfigurationError:
            return _safe_failure(503, "summary_configuration_error")
        cached = _cached_response(snapshot, prepared, model=settings.model)
        if cached is not None:
            return cached

        generation = GenerationRequest(
            system_prompt=prepared.system_prompt,
            user_prompt=prepared.user_prompt,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
        try:
            # A summary POST sends at most one paid completion request.
            async with DeepSeekGateway(settings, max_retries=0) as gateway:
                result = await gateway.generate_json(
                    generation, SummaryDraft, example=_EXAMPLE,
                )
        except LLMError as error:
            code = error.code if error.code in _SAFE_MODEL_ERRORS else "provider_error"
            status = (504 if code == "timeout" else 503 if code in {
                "rate_limited", "service_unavailable", "authentication_failed",
                "insufficient_balance", "connection_error",
            } else 502)
            return _safe_failure(status, f"summary_{code}")
        except Exception:
            return _safe_failure(502, "summary_unavailable")

        payload = SummaryCachePayload(
            draft=result.value, token_usage=result.usage,
            latency_ms=round(result.elapsed_seconds * 1000),
        )
        try:
            saved = await persist_summary(
                request.app, event_id, prepared.input_fingerprint,
                result.model, SUMMARY_PROMPT_VERSION, payload,
            )
        except Exception:
            return _safe_failure(503, "summary_storage_error")
        if not saved:
            return _safe_failure(409, "summary_input_changed")
        return _summary_response(snapshot, prepared, payload, model=result.model, cached=False)
    finally:
        lock.release()
