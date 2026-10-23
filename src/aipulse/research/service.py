"""One in-process research executor with deadlines, terminal states and cleanup."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from uuid import uuid4

import httpx

from aipulse.llm.config import LLMConfigurationError, load_deepseek_settings
from aipulse.llm.gateway import DeepSeekGateway
from aipulse.research.graph import ResearchWorkflow
from aipulse.research.repository import ResearchStore, research_schema_ready
from aipulse.research.state import TASK_TIMEOUT_SECONDS, ResearchRequest, RunMetrics
from aipulse.research.tools import ArxivSearch, LocalNewsSearch, PublicAPISearch, ResearchRetriever
from aipulse.runtime.context import bind_context, new_context
from aipulse.runtime.logging import log_event


_LOG = logging.getLogger(__name__)


class ResearchBusy(RuntimeError):
    pass


class ResearchUnavailable(RuntimeError):
    def __init__(self, code="research_unavailable"):
        self.code = code
        super().__init__(code)


@asynccontextmanager
async def research_resources(store, settings, external_enabled):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10), follow_redirects=False, trust_env=False,
    ) as client:
        providers = [LocalNewsSearch(store.search_news)]
        if external_enabled:
            providers += [
                ArxivSearch(client),
                PublicAPISearch(client, "github", github_token=os.environ.get("GITHUB_TOKEN")),
                PublicAPISearch(client, "hn"),
            ]
        async with DeepSeekGateway(settings, max_retries=0) as gateway:
            yield gateway, ResearchRetriever(providers)


class ResearchExecutor:
    """One active task; no queue, automatic retry or paid recovery after restart."""

    def __init__(self, store, ready, *, settings_loader=load_deepseek_settings,
                 resources=research_resources, external_enabled=False,
                 task_timeout=TASK_TIMEOUT_SECONDS):
        self.store = store
        self.ready = ready
        self._settings_loader = settings_loader
        self._resources = resources
        self._external_enabled = external_enabled
        self._timeout = min(TASK_TIMEOUT_SECONDS, task_timeout)
        self._slot = Lock()
        self._job = None
        self.active_task_id = None
        self._recovered = False
        self._stopping = False

    async def submit(self, request: ResearchRequest) -> str:
        if self._stopping:
            raise ResearchUnavailable("research_stopping")
        if not self._slot.acquire(blocking=False):
            raise ResearchBusy()
        spawned = False
        task_id = None
        try:
            if not await self.ready():
                raise ResearchUnavailable("research_schema_not_ready")
            try:
                settings = self._settings_loader()
            except LLMConfigurationError:
                raise ResearchUnavailable("research_configuration_error") from None
            if not self._recovered:
                await self.store.recover_incomplete()
                self._recovered = True
            task_id = str(uuid4())
            await self.store.create(task_id, request)
            self.active_task_id = task_id
            self._job = asyncio.create_task(self._execute(task_id, request, settings), name="aipulse-research")
            spawned = True
            return task_id
        except BaseException:
            if task_id is not None:
                await self._safe_fail(task_id, "task_creation_interrupted", interrupted=True)
            raise
        finally:
            if not spawned:
                self.active_task_id = None
                self._slot.release()

    async def _safe_fail(self, task_id, code, metrics=None, *, interrupted=False):
        try:
            await asyncio.wait_for(self.store.fail(task_id, code, metrics, interrupted=interrupted), timeout=5)
        except Exception:
            log_event(_LOG, "research_terminal_write_failed", error_code="storage_unavailable")

    async def _execute(self, task_id, request, settings):
        workflow = None
        last_state = None
        started = monotonic()

        async def progress(node, state):
            nonlocal last_state
            last_state = state
            await self.store.progress(task_id, node, state)
            log_event(_LOG, "research_progress", node_name=node, round_no=state.rounds, count=len(state.evidence))

        async def persist(state):
            nonlocal last_state
            last_state = state
            await self.store.complete(task_id, state)

        def metrics():
            if workflow is None:
                return RunMetrics(elapsed_ms=round((monotonic() - started) * 1000))
            return workflow.budget.metrics(
                max(last_state.rounds if last_state else 0, workflow.attempted_rounds),
                max(last_state.supplements if last_state else 0, workflow.attempted_supplements),
            )

        with bind_context(new_context(task_id=task_id)):
            try:
                async with asyncio.timeout(self._timeout):
                    await self.store.start(task_id)
                    async with self._resources(self.store, settings, self._external_enabled) as (gateway, retriever):
                        workflow = ResearchWorkflow(
                            gateway, retriever, progress=progress, persist=persist,
                            model_timeout=settings.timeout_seconds,
                        )
                        workflow.budget.started = started
                        await workflow.run(request)
            except asyncio.CancelledError:
                await self._safe_fail(task_id, "task_cancelled", metrics(), interrupted=True)
            except TimeoutError:
                await self._safe_fail(task_id, "task_deadline", metrics())
            except LLMConfigurationError:
                await self._safe_fail(task_id, "research_configuration_error", metrics())
            except Exception:
                await self._safe_fail(task_id, "research_execution_failed", metrics())
            finally:
                self.active_task_id = None
                self._slot.release()

    async def get_task(self, task_id):
        snapshot = await self.store.get_task(task_id)
        if (snapshot is not None and snapshot.status in {"queued", "running"}
                and task_id != self.active_task_id):
            return snapshot.model_copy(update={
                "status": "interrupted", "stage": "interrupted",
                "error_code": "execution_not_active",
                "finished_at": snapshot.finished_at or datetime.now(timezone.utc),
            })
        return snapshot

    async def cancel(self, task_id):
        if task_id != self.active_task_id or self._job is None or self._job.done():
            return False
        self._job.cancel()
        await asyncio.wait({self._job}, timeout=1)
        await self._finish_cancelled_before_start(task_id)
        return True

    async def _finish_cancelled_before_start(self, task_id):
        if (task_id is not None and self._job is not None and self._job.done()
                and self.active_task_id == task_id):
            try:
                await self._safe_fail(task_id, "task_cancelled", interrupted=True)
            finally:
                self.active_task_id = None
                self._slot.release()

    async def shutdown(self):
        self._stopping = True
        if self._job is not None and not self._job.done():
            task_id = self.active_task_id
            self._job.cancel()
            await asyncio.wait({self._job}, timeout=8)
            if not self._job.done():
                await self._safe_fail(task_id, "shutdown_interrupted", interrupted=True)
        await self._finish_cancelled_before_start(self.active_task_id)


def create_research_executor(engine, session_factory, external_enabled=False):
    async def ready():
        return await research_schema_ready(engine)
    return ResearchExecutor(ResearchStore(session_factory), ready, external_enabled=external_enabled)
