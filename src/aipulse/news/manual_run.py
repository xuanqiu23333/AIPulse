"""Shared bounded wiring for one manual news ingestion and scoring run."""

from datetime import datetime, timezone
from typing import Iterable, Mapping

import httpx
from sqlalchemy import text

from aipulse.news.collectors.arxiv import ArxivCollector
from aipulse.news.collectors.base import BaseCollector
from aipulse.news.collectors.github import GitHubReleaseCollector
from aipulse.news.collectors.hn import HackerNewsCollector
from aipulse.news.scoring_service import NewsScoringService, ScoredIngestionReport
from aipulse.news.service import IngestionRequest, NewsIngestionService


_SCORING_REVISION = "20261016_0002"
_SUMMARY_REVISION = "20261021_0003"
_RESEARCH_REVISION = "20261021_0004"
_REQUIRED_TABLES = {
    "agent_run_log", "alembic_version", "daily_brief", "news_event",
    "news_item", "news_source", "research_evidence", "research_task",
}
_SCORING_COLUMNS = {
    "category": ("varchar(32)", "YES"),
    "importance_score": ("tinyint unsigned", "YES"),
    "score_reason": ("varchar(1000)", "YES"),
    "rule_version": ("varchar(100)", "YES"),
}
_SUMMARY_COLUMNS = {
    "summary_json": ("json", "YES"),
    "summary_model": ("varchar(100)", "YES"),
    "summary_prompt_version": ("varchar(100)", "YES"),
    "summary_input_fingerprint": ("char(64)", "YES"),
    "summary_generated_at": ("datetime(6)", "YES"),
}
_PROVIDERS = frozenset({"github", "arxiv", "hn"})


def build_collectors(
    client: httpx.AsyncClient, *, providers: Iterable[str],
    limit_per_source: int, http_timeout: float, github_token: str | None = None,
) -> dict[str, BaseCollector]:
    """Build only selected real collectors with one page and finite budgets."""
    selected = set(providers)
    if not selected or not selected <= _PROVIDERS or not 1 <= limit_per_source <= 100:
        raise ValueError("Invalid manual collection sources or limit")
    limit = limit_per_source
    collectors: dict[str, BaseCollector] = {}
    if "github" in selected:
        collectors["github"] = GitHubReleaseCollector(
            client, token=github_token, per_page=limit, max_pages=1,
            max_items=limit, timeout=http_timeout,
        )
    if "arxiv" in selected:
        collectors["arxiv"] = ArxivCollector(
            client, per_page=limit, max_pages=1, max_items=limit,
            timeout=http_timeout,
        )
    if "hn" in selected:
        collectors["hn"] = HackerNewsCollector(
            client, max_ids=min(500, limit * 3), max_candidates=limit,
            max_requests=min(500, limit * 3) + 1, timeout=http_timeout,
        )
    return collectors


async def schema_ready(
    engine, *, require_app_identity: bool = False, require_summary_cache: bool = False,
) -> bool:
    """Accept reviewed news-compatible revisions and verify required news columns."""
    async with engine.connect() as connection:
        if await connection.scalar(text("SELECT DATABASE()")) != "aipulse_db":
            return False
        if require_app_identity and await connection.scalar(text("SELECT CURRENT_USER()")) != "aipulse_app@localhost":
            return False
        tables = set((await connection.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        ))).scalars())
        if not _REQUIRED_TABLES.issubset(tables):
            return False
        revisions = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
        if len(revisions) != 1 or revisions[0] not in {_SCORING_REVISION, _SUMMARY_REVISION, _RESEARCH_REVISION}:
            return False
        if require_summary_cache and revisions[0] not in {_SUMMARY_REVISION, _RESEARCH_REVISION}:
            return False
        columns = {
            name: (type_name.lower(), nullable)
            for name, type_name, nullable in (await connection.execute(text(
            "SELECT column_name, column_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'news_event'"
        ))).all()
        }
        if any(columns.get(name) != shape for name, shape in _SCORING_COLUMNS.items()):
            return False
        if revisions[0] == _SCORING_REVISION:
            return not any(name in columns for name in _SUMMARY_COLUMNS)
        return all(columns.get(name) == shape for name, shape in _SUMMARY_COLUMNS.items())


async def run_collection(
    requests: Iterable[IngestionRequest], *,
    collectors: Mapping[str, BaseCollector], session_factory,
    as_of: datetime | None = None,
) -> ScoredIngestionReport:
    """Use existing source transactions and score only their committed events."""
    ingestion = NewsIngestionService(
        collectors=collectors, session_factory=session_factory,
    )
    workflow = NewsScoringService(
        ingestion_service=ingestion, session_factory=session_factory,
    )
    return await workflow.ingest_and_score(
        requests, as_of=as_of or datetime.now(timezone.utc),
    )
