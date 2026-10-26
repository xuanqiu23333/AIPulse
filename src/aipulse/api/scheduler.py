"""Opt-in APScheduler 3 job for one fixed, bounded news collection batch."""

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

from aipulse.api.collect import CollectionPreflightError, run_fixed_collection
from aipulse.api.schemas import ScheduledCollectionRun


_LOG = logging.getLogger(__name__)
_PROVIDERS = ("github", "arxiv", "hn")
_LIMIT_PER_SOURCE = 5
_INTERVAL_MINUTES = 60
_INTERVAL_SECONDS = _INTERVAL_MINUTES * 60
_INITIAL_DELAY_ENV = "AIPULSE_SCHEDULED_INITIAL_DELAY_SECONDS"


class ScheduledCollectionConfigurationError(ValueError):
    """An explicitly enabled scheduler has an invalid local configuration."""


def _initial_delay_seconds() -> int:
    raw = os.environ.get(_INITIAL_DELAY_ENV)
    if raw is None:
        return _INTERVAL_SECONDS
    if re.fullmatch(r"[1-9][0-9]{0,3}", raw) is None or int(raw) > _INTERVAL_SECONDS:
        raise ScheduledCollectionConfigurationError(
            f"{_INITIAL_DELAY_ENV} must be a decimal integer from 1 to {_INTERVAL_SECONDS}"
        )
    return int(raw)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def run_scheduled_collection(application: FastAPI) -> None:
    """Skip overlap, contain failures, and retain a safe in-memory result."""
    state = application.state
    if getattr(state, "scheduled_collection_stopping", False):
        return
    started_at = _now()
    lock = state.manual_collection_lock
    if not lock.acquire(blocking=False):
        state.scheduled_collection_last_run = ScheduledCollectionRun(
            started_at=started_at, finished_at=_now(),
            status="skipped", failure_codes=("overlap",),
        )
        _LOG.info("Scheduled news collection skipped: another collection is running")
        return

    state.scheduled_collection_idle.clear()
    state.scheduled_collection_last_run = ScheduledCollectionRun(
        started_at=started_at, finished_at=None, status="running",
    )
    try:
        result = await run_fixed_collection(application, _PROVIDERS, _LIMIT_PER_SOURCE)
        failure_codes = tuple(sorted({
            code for code in (
                *(source.error_code for source in result.sources),
                *(failure.error_code for failure in result.scoring_errors),
            ) if code is not None
        }))
        state.scheduled_collection_last_run = ScheduledCollectionRun(
            started_at=started_at, finished_at=_now(),
            status=result.status, sources=result.sources,
            processed_count=result.processed_count,
            failure_codes=failure_codes,
        )
        _LOG.info("Scheduled news collection finished: status=%s processed=%d",
                  result.status, result.processed_count)
    except CollectionPreflightError:
        state.scheduled_collection_last_run = ScheduledCollectionRun(
            started_at=started_at, finished_at=_now(),
            status="failed", failure_codes=("schema_not_ready",),
        )
        _LOG.warning("Scheduled news collection failed: schema_not_ready")
    except asyncio.CancelledError:
        state.scheduled_collection_last_run = ScheduledCollectionRun(
            started_at=started_at, finished_at=_now(),
            status="failed", failure_codes=("cancelled",),
        )
        raise
    except Exception:
        state.scheduled_collection_last_run = ScheduledCollectionRun(
            started_at=started_at, finished_at=_now(),
            status="failed", failure_codes=("unexpected",),
        )
        _LOG.error("Scheduled news collection failed: unexpected")
    finally:
        state.scheduled_collection_idle.set()
        lock.release()


def start_collection_scheduler(application: FastAPI, *, scheduler_factory=None):
    """Register one in-memory hourly job and start it on the app event loop."""
    initial_delay = _initial_delay_seconds()
    if scheduler_factory is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        scheduler_factory = AsyncIOScheduler
    trigger = IntervalTrigger(
        seconds=_INTERVAL_SECONDS,
        start_date=_now() + timedelta(seconds=initial_delay),
        timezone=timezone.utc,
    )
    scheduler = scheduler_factory(timezone=timezone.utc)
    scheduler.add_job(
        run_scheduled_collection, trigger,
        args=[application], id="aipulse-news-collection",
        max_instances=1, coalesce=True, misfire_grace_time=60,
    )
    scheduler.start()
    return scheduler
