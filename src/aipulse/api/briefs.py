"""Local, opt-in generation and read-only retrieval of one daily AI brief."""

from datetime import date, datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aipulse.api.collect import _loopback_request
from aipulse.api.schemas import (
    DailyBriefHighlight, DailyBriefRequest, DailyBriefResponse,
)
from aipulse.llm.config import LLMConfigurationError, load_deepseek_settings
from aipulse.llm.gateway import DeepSeekGateway, GenerationRequest, LLMError
from aipulse.news.brief import (
    BRIEF_PROMPT_VERSION, BRIEF_TIMEZONE, DailyBriefDraft, PreparedBrief,
    _plain, build_brief_input, validate_draft,
)
from aipulse.news.brief_repository import DailyBriefRepository
from aipulse.news.manual_run import schema_ready
from aipulse.news.repository import NewsRepository
from aipulse.news.summary import _valid_stored_url


router = APIRouter(prefix="/api/v1/briefs", tags=["briefs"])
_MAX_OUTPUT_TOKENS = 3000
_MAX_STORED_BYTES = 65_536
_SAFE_MODEL_ERRORS = {
    "authentication_failed", "insufficient_balance", "rate_limited",
    "service_unavailable", "connection_error", "timeout", "invalid_request",
    "request_rejected", "empty_response", "invalid_json", "schema_mismatch",
    "output_truncated", "invalid_response", "provider_error",
}


class StoredDailyBrief(BaseModel):
    """Versioned JSON inside the existing daily_brief.content LONGTEXT."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    prompt_version: str
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: DailyBriefResponse


def _safe_failure(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": "Daily brief unavailable", "request_id": str(uuid4())},
    )


async def brief_schema_ready(application: FastAPI) -> bool:
    try:
        return await schema_ready(
            application.state.engine, require_app_identity=True,
            require_summary_cache=True,
        )
    except Exception:
        return False


async def _prepare_in_session(session, brief_date: date) -> PreparedBrief:
    candidates = await DailyBriefRepository(session).list_candidates(brief_date)
    news = NewsRepository(session)
    rows = []
    for candidate in candidates:
        snapshot = await news.get_event_summary_input(candidate.event_id)
        if snapshot is not None:
            rows.append((candidate, snapshot))
    return build_brief_input(brief_date, rows)


async def load_prepared(application: FastAPI, brief_date: date) -> PreparedBrief:
    """Release the read Session before contacting DeepSeek."""
    async with application.state.session_factory() as session:
        return await _prepare_in_session(session, brief_date)


async def load_saved(application: FastAPI, brief_date: date) -> str | None:
    async with application.state.session_factory() as session:
        row = await DailyBriefRepository(session).get(brief_date)
        return row.content if row is not None and row.status == "complete" else None


def _decode(content: str | None, brief_date: date) -> StoredDailyBrief | None:
    if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_STORED_BYTES:
        return None
    try:
        stored = StoredDailyBrief.model_validate_json(content)
    except (ValidationError, ValueError):
        return None
    if (stored.response.brief_date != brief_date or stored.response.timezone != BRIEF_TIMEZONE
            or stored.response.status != "complete" or stored.response.model is None
            or stored.response.overview is None or stored.response.limitations is None
            or stored.response.generated_at is None or not stored.response.highlights
            or any(
                not highlight.sources or any(not _valid_stored_url(url) for url in highlight.sources)
                for highlight in stored.response.highlights
            )):
        return None
    return stored


def _cached_response(
    content: str | None, brief_date: date, *, fingerprint: str | None = None,
    model: str | None = None, require_current: bool = True,
) -> DailyBriefResponse | None:
    stored = _decode(content, brief_date)
    if stored is None:
        return None
    if (fingerprint is not None and stored.input_fingerprint != fingerprint) or (
        model is not None and stored.response.model != model
    ) or (require_current and stored.prompt_version != BRIEF_PROMPT_VERSION):
        return None
    return stored.response.model_copy(update={
        "cached": True, "rules_current": stored.prompt_version == BRIEF_PROMPT_VERSION,
    })


def _response_from_model(
    brief_date: date, prepared: PreparedBrief, draft: DailyBriefDraft,
    *, model: str, usage, latency_ms: int,
) -> DailyBriefResponse:
    validate_draft(draft, prepared)
    by_id = {event.event_id: event for event in prepared.events}
    summaries = {}
    sections = {"model_and_products": [], "tools_and_frameworks": [], "research": []}
    for highlight in draft.highlights:
        event = by_id[highlight.event_id]
        summary = (highlight.summary_zh if event.has_detail else
                   f"来源标题为《{_plain(event.title, 220)}》，存储材料不足以核实具体内容。")
        summaries[highlight.event_id] = summary
        if event.has_detail and event.section in sections:
            sections[event.section].append(summary)
    limitations = draft.limitations + " 本简报仅依据数据库保存的标题、摘录及缓存摘要，未核验来源全文。"
    if any(not event.has_detail for event in prepared.events):
        limitations += " 部分事件仅有标题或短摘录，具体细节未经核实。"
    detailed_count = sum(event.has_detail for event in prepared.events)
    if detailed_count == len(prepared.events):
        overview = draft.overview
    elif detailed_count == 0:
        overview = f"当天筛选出 {len(prepared.events)} 条 AI 相关记录，但仅有标题或短摘录，无法形成已核实的动态概览。"
    else:
        overview = (
            f"当天筛选出 {len(prepared.events)} 条 AI 相关记录，其中 {detailed_count} 条"
            "有较充分的已存摘录；其余记录的具体内容尚未核实。"
        )
    return DailyBriefResponse(
        brief_date=brief_date, timezone=BRIEF_TIMEZONE, status="complete",
        overview=overview,
        highlights=tuple(DailyBriefHighlight(
            event_id=highlight.event_id, title=by_id[highlight.event_id].title,
            category=by_id[highlight.event_id].category,
            importance_score=by_id[highlight.event_id].importance_score,
            summary_zh=summaries[highlight.event_id],
            sources=by_id[highlight.event_id].sources,
        ) for highlight in draft.highlights),
        model_and_products=tuple(sections["model_and_products"]),
        tools_and_frameworks=tuple(sections["tools_and_frameworks"]),
        research=tuple(sections["research"]),
        trends=tuple(draft.trends) if detailed_count >= 2 and detailed_count == len(prepared.events) else (),
        limitations=limitations, model=model, token_usage=usage,
        latency_ms=latency_ms, generated_at=datetime.now(timezone.utc), cached=False,
    )


async def persist_generated(
    application: FastAPI, brief_date: date, fingerprint: str,
    payload: StoredDailyBrief,
) -> bool:
    """Recheck selected evidence in a fresh short transaction before replacing a row."""
    content = payload.model_dump_json()
    if len(content.encode("utf-8")) > _MAX_STORED_BYTES:
        return False
    async with application.state.session_factory() as session:
        async with session.begin():
            repository = DailyBriefRepository(session)
            existing = await repository.get(brief_date, for_update=True)
            current = await _prepare_in_session(session, brief_date)
            if current.input_fingerprint != fingerprint:
                return False
            if existing is not None and _cached_response(
                existing.content, brief_date, fingerprint=fingerprint,
                model=payload.response.model,
            ) is not None:
                return False
            repository.save(existing, brief_date, content)
            return True


@router.get("/daily/{brief_date}", response_model=DailyBriefResponse)
async def get_daily_brief(
    brief_date: Annotated[date, Path()], request: Request,
) -> DailyBriefResponse | JSONResponse:
    if not _loopback_request(request):
        raise HTTPException(status_code=403)
    if request.query_params:
        raise HTTPException(status_code=422)
    if not await brief_schema_ready(request.app):
        return _safe_failure(503, "brief_schema_not_ready")
    try:
        content = await load_saved(request.app, brief_date)
    except Exception:
        return _safe_failure(503, "brief_storage_error")
    cached = _cached_response(content, brief_date, require_current=False)
    if cached is None:
        raise HTTPException(status_code=404)
    return cached


@router.post("/daily", response_model=DailyBriefResponse)
async def generate_daily_brief(
    body: DailyBriefRequest, request: Request,
) -> DailyBriefResponse | JSONResponse:
    if not request.app.state.daily_brief_enabled or not _loopback_request(request):
        raise HTTPException(status_code=403)
    if request.query_params:
        raise HTTPException(status_code=422)
    lock = request.app.state.daily_brief_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409)
    try:
        if not await brief_schema_ready(request.app):
            return _safe_failure(503, "brief_schema_not_ready")
        try:
            prepared = await load_prepared(request.app, body.brief_date)
        except Exception:
            return _safe_failure(503, "brief_storage_error")
        if not prepared.events:
            return DailyBriefResponse(
                brief_date=body.brief_date, timezone=BRIEF_TIMEZONE,
                status="empty", cached=False,
            )
        try:
            settings = load_deepseek_settings()
            content = await load_saved(request.app, body.brief_date)
        except LLMConfigurationError:
            return _safe_failure(503, "brief_configuration_error")
        except Exception:
            return _safe_failure(503, "brief_storage_error")
        cached = _cached_response(
            content, body.brief_date, fingerprint=prepared.input_fingerprint,
            model=settings.model,
        )
        if cached is not None:
            return cached
        example = DailyBriefDraft(
            overview="示例：仅根据所选记录概述当天的 AI 动态。",
            highlights=[{
                "event_id": event.event_id,
                "summary_zh": "示例：这条记录描述了一项更新，细节仍需核对。",
            } for event in prepared.events],
            trends=[],
            limitations="示例：仅依据已存储标题和摘录。",
        )
        request_model = GenerationRequest(
            system_prompt=prepared.system_prompt, user_prompt=prepared.user_prompt,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
        try:
            async with DeepSeekGateway(settings, max_retries=0) as gateway:
                result = await gateway.generate_json(
                    request_model, DailyBriefDraft, example=example,
                )
            response = _response_from_model(
                body.brief_date, prepared, result.value, model=result.model,
                usage=result.usage, latency_ms=round(result.elapsed_seconds * 1000),
            )
        except LLMError as error:
            code = error.code if error.code in _SAFE_MODEL_ERRORS else "provider_error"
            status = 504 if code == "timeout" else 503 if code in {
                "rate_limited", "service_unavailable", "authentication_failed",
                "insufficient_balance", "connection_error",
            } else 502
            return _safe_failure(status, f"brief_{code}")
        except Exception:
            return _safe_failure(502, "brief_invalid_response")
        stored = StoredDailyBrief(
            prompt_version=BRIEF_PROMPT_VERSION,
            input_fingerprint=prepared.input_fingerprint, response=response,
        )
        try:
            saved = await persist_generated(
                request.app, body.brief_date, prepared.input_fingerprint, stored,
            )
        except Exception:
            return _safe_failure(503, "brief_storage_error")
        if not saved:
            return _safe_failure(409, "brief_input_changed")
        return response
    finally:
        lock.release()
