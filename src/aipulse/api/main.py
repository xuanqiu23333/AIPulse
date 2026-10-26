"""Minimal FastAPI application and read-only database health probe."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from aipulse.api.collect import router as collect_router
from aipulse.api.briefs import router as briefs_router
from aipulse.api.news import router as news_router
from aipulse.api.summary import router as summary_router
from aipulse.api.research import router as research_router
from aipulse.api.scheduler import (
    ScheduledCollectionConfigurationError, start_collection_scheduler,
)
from aipulse.config import load_database_settings
from aipulse.research.service import create_research_executor
from aipulse.storage.db import (
    close_engine,
    create_development_engine,
    create_session_factory,
)


_LOG = logging.getLogger(__name__)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "request_id": str(uuid4())},
    )


def create_app(
    *,
    settings_loader=load_database_settings,
    engine_factory=create_development_engine,
    session_factory_builder=create_session_factory,
    research_executor_factory=create_research_executor,
) -> FastAPI:
    """Build the HTTP app without opening a database connection at import time."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        settings = settings_loader()
        engine = engine_factory(settings)
        scheduler = None
        research_executor = None
        try:
            application.state.engine = engine
            application.state.session_factory = session_factory_builder(engine)
            research_executor = research_executor_factory(
                engine, application.state.session_factory,
                application.state.research_external_enabled,
            )
            application.state.research_executor = research_executor
            if application.state.scheduled_collection_enabled:
                try:
                    scheduler = start_collection_scheduler(application)
                    application.state.scheduled_collection_active = True
                except ScheduledCollectionConfigurationError:
                    raise
                except Exception:
                    _LOG.error("Scheduled news collection could not start")
            yield
        finally:
            if research_executor is not None:
                try:
                    await research_executor.shutdown()
                except Exception:
                    _LOG.error("Research executor could not stop cleanly")
            application.state.scheduled_collection_stopping = True
            if scheduler is not None:
                try:
                    scheduler.shutdown(wait=False)
                except Exception:
                    _LOG.error("Scheduled news collection could not stop cleanly")
                await application.state.scheduled_collection_idle.wait()
                application.state.scheduled_collection_active = False
            await close_engine(engine)

    application = FastAPI(title="AIPulse", lifespan=lifespan)
    application.state.manual_collection_enabled = os.environ.get("AIPULSE_ENABLE_MANUAL_COLLECTION") == "1"
    application.state.manual_collection_lock = Lock()
    application.state.ai_summary_enabled = os.environ.get("AIPULSE_ENABLE_AI_SUMMARY") == "1"
    application.state.ai_summary_lock = Lock()
    application.state.daily_brief_enabled = os.environ.get("AIPULSE_ENABLE_DAILY_BRIEF") == "1"
    application.state.daily_brief_lock = Lock()
    application.state.research_enabled = os.environ.get("AIPULSE_ENABLE_RESEARCH") == "1"
    application.state.research_external_enabled = os.environ.get("AIPULSE_ENABLE_RESEARCH_EXTERNAL") == "1"
    application.state.research_executor = None
    application.state.scheduled_collection_enabled = os.environ.get("AIPULSE_ENABLE_SCHEDULED_COLLECTION") == "1"
    application.state.scheduled_collection_active = False
    application.state.scheduled_collection_stopping = False
    application.state.scheduled_collection_last_run = None
    application.state.scheduled_collection_idle = asyncio.Event()
    application.state.scheduled_collection_idle.set()
    application.include_router(news_router)
    application.include_router(summary_router)
    application.include_router(briefs_router)
    application.include_router(research_router)
    application.include_router(collect_router)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        return _error_response(exc.status_code, "http_error", "HTTP request failed")

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        return _error_response(422, "validation_error", "Invalid request")

    @application.exception_handler(Exception)
    async def internal_error(_request: Request, _exc: Exception):
        return _error_response(500, "internal_error", "Internal server error")

    @application.get("/")
    async def root():
        return {"service": "AIPulse", "status": "running"}

    @application.get("/health")
    async def health(request: Request):
        try:
            async with request.app.state.engine.connect() as connection:
                result = await connection.execute(text("SELECT 1"))
                if result.scalar_one() == 1:
                    return {"service": "AIPulse", "database": "up"}
        except Exception:
            pass
        return JSONResponse(
            status_code=503,
            content={"service": "AIPulse", "database": "down"},
        )

    return application


app = create_app()
