"""Opt-in, real-MySQL scoring tests. Never selected by the daily tests/ run.

FakeCollectors supply fixed candidates; all persistence and scoring use the
production SQLAlchemy Session, NewsRepository, and NewsScoringService.
"""

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text

from aipulse.models.entities import NewsEvent, NewsItem, NewsSource
from aipulse.news.collectors.base import BaseCollector
from aipulse.news.repository import NewsRepository
from aipulse.news.score import ScoreConfig
from aipulse.news.scoring_service import EventScoreStatus, NewsScoringService
from aipulse.news.service import IngestionRequest, NewsIngestionService
from aipulse.news.types import CollectionResult, CollectionStatus
from tests_mysql.mysql.safety import TestRows
from tests_mysql.mysql.scoring_support import (
    cleanup_scoring_rows, prepare_scoring_runtime,
)
from tests_mysql.mysql.test_news_ingestion_mysql import Case, partial, successful


pytestmark = pytest.mark.asyncio
RUN_UUID = uuid4().hex
AS_OF = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


class ScoringCase(Case):
    def scoring(self, responses, *, config=None, repository_factory=NewsRepository):
        ingestion = self.service(responses, repository_factory=repository_factory)
        return NewsScoringService(
            ingestion_service=ingestion,
            session_factory=self.guarded_factory,
            repository_factory=repository_factory,
            config=config,
        )

    async def event_for(self, candidate):
        async with self.read_session() as session:
            item = (await session.scalars(select(NewsItem).where(
                NewsItem.provider == candidate.provider,
                NewsItem.external_id == candidate.external_id,
            ))).one()
            return await session.get(NewsEvent, item.event_id)

    async def cleanup(self):
        # Close every test transaction before calling this method. Never widen
        # selectors if a test fails: report owned identities for manual review.
        if not self.rows.cleanup_statements():
            return
        try:
            async with self.read_session() as session:
                await cleanup_scoring_rows(session, self.rows)
                await session.commit()
            async with self.read_session() as session:
                for statement in self.rows.cleanup_statements():
                    count = await session.scalar(
                        select(func.count()).select_from(statement.table)
                        .where(statement.whereclause)
                    )
                    assert count == 0, "Owned scoring rows remain"
            assert await self.counts() == self.baseline_counts
        except Exception as exc:
            raise AssertionError(
                f"Scoped scoring cleanup failed for {self.rows.namespace}"
                f"{self.case_key} ({type(exc).__name__}); inspect only these identities"
            ) from None


@pytest_asyncio.fixture
async def case(request):
    runtime = prepare_scoring_runtime(
        cli_enabled=lambda: request.config.getoption("--run-aipulse-mysql"),
        env_value=lambda: os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE"),
    )
    context = ScoringCase(
        engine=runtime.engine,
        factory=runtime.session_factory,
        guarded_factory=runtime.guarded_factory,
        rows=TestRows(f"aipulse-it-{RUN_UUID}-"),
        case_key=uuid4().hex[:12], pytestconfig=request.config,
        baseline_counts={},
    )
    try:
        # read_session preflights the exact pinned connection before any query.
        context.baseline_counts = await context.counts()
        yield context
    finally:
        try:
            await context.cleanup()
        finally:
            await runtime.engine.dispose()


def one_source(case, *, provider="github", label="release", status="success"):
    source = case.source(provider, "source")
    item = case.github_item(label) if provider == "github" else case.arxiv_item()
    response = partial(item) if status == "partial" else successful(item)
    responses = {provider: {source.external_id: response}}
    return source, item, responses


async def test_single_event_saves_all_score_fields_without_changing_raw_identity(case):
    source, item, responses = one_source(case)
    report = await case.scoring(responses).ingest_and_score(
        [IngestionRequest(source)], as_of=AS_OF,
    )
    assert report.ingestion.processed_count == 1
    assert len(report.scores) == 1 and report.scores[0].status is EventScoreStatus.UPDATED
    score = report.scores[0].score
    assert score is not None and 0 <= score.importance_score <= 100
    async with case.read_session() as session:
        original = (await session.scalars(select(NewsItem).where(
            NewsItem.provider == "github", NewsItem.external_id == item.external_id,
        ))).one()
        saved = await session.get(NewsEvent, original.event_id)
        assert original.url == str(item.original_url) and original.title == item.title
        assert saved.event_key == score.event_key
        assert (saved.category, saved.importance_score,
                saved.score_reason, saved.rule_version) == (
            score.category, score.importance_score,
            score.score_reason, score.rule_version,
        )


async def test_same_score_is_unchanged_without_event_update_or_new_identity(case):
    source, item, responses = one_source(case)
    service = case.scoring(responses)
    first = await service.ingest_and_score([IngestionRequest(source)], as_of=AS_OF)
    counts = await case.counts()
    updates = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE NEWS_EVENT"):
            updates.append(statement)

    event.listen(case.engine.sync_engine, "before_cursor_execute", capture)
    try:
        second = await service.ingest_and_score([IngestionRequest(source)], as_of=AS_OF)
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", capture)
    assert first.scores[0].status is EventScoreStatus.UPDATED
    assert second.scores[0].status is EventScoreStatus.UNCHANGED
    assert second.scores[0].score == first.scores[0].score
    assert updates == [] and await case.counts() == counts


async def test_new_rule_configuration_rescores_same_event(case):
    source, item, responses = one_source(case)
    first = await case.scoring(responses).ingest_and_score(
        [IngestionRequest(source)], as_of=AS_OF,
    )
    new_config = ScoreConfig(base_points=ScoreConfig().base_points + 1)
    second = await case.scoring(responses, config=new_config).ingest_and_score(
        [IngestionRequest(source)], as_of=AS_OF,
    )
    assert first.scores[0].event_id == second.scores[0].event_id
    assert second.scores[0].status is EventScoreStatus.UPDATED
    assert second.scores[0].score.rule_version == new_config.rule_version
    assert second.scores[0].score != first.scores[0].score
    async with case.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsEvent).where(
            NewsEvent.event_key == first.scores[0].score.event_key,
        )) == 1


async def test_arxiv_and_hn_share_one_scored_event_with_two_originals(case):
    paper_source = case.source("arxiv", "paper-source")
    hn_source = case.source("hn", "hn-source")
    paper, story = case.arxiv_item(), case.hn_paper_item()
    report = await case.scoring({
        "arxiv": {paper_source.external_id: successful(paper)},
        "hn": {hn_source.external_id: successful(story)},
    }).ingest_and_score([
        IngestionRequest(paper_source), IngestionRequest(hn_source),
    ], as_of=AS_OF)
    assert report.ingestion.processed_count == 2
    assert len(report.scores) == 1
    assert report.scores[0].score.category == "paper"
    assert any(signal.name == "independent_sources"
               for signal in report.scores[0].score.signals)
    async with case.read_session() as session:
        items = (await session.scalars(select(NewsItem).where(
            NewsItem.external_id.in_([paper.external_id, story.external_id]),
        ))).all()
        assert len(items) == 2 and {row.event_id for row in items} == {
            report.scores[0].event_id,
        }


async def test_historical_persisted_item_contributes_to_later_score(case):
    paper_source = case.source("arxiv", "paper-source")
    hn_source = case.source("hn", "hn-source")
    paper, story = case.arxiv_item(), case.hn_paper_item()
    await case.service({"arxiv": {
        paper_source.external_id: successful(paper),
    }}).ingest([IngestionRequest(paper_source)])
    report = await case.scoring({"hn": {
        hn_source.external_id: successful(story),
    }}).ingest_and_score([IngestionRequest(hn_source)], as_of=AS_OF)
    assert len(report.scores) == 1
    assert any(signal.name == "independent_sources"
               for signal in report.scores[0].score.signals)
    async with case.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.event_id == report.scores[0].event_id,
        )) == 2


async def test_real_score_update_then_exception_rolls_back_only_score_transaction(case):
    source = case.source("github", "source")
    bad, good = case.github_item("bad"), case.github_item("good")
    bad_key = next(key for key, title in case.rows.events.items() if title == bad.title)

    class FailsAfterRealScoreUpdate(NewsRepository):
        async def save_event_score(self, event_id, score):
            changed = await super().save_event_score(event_id, score)
            if score.event_key == bad_key:
                raise RuntimeError("Injected after real SQLAlchemy scoring UPDATE")
            return changed

    report = await case.scoring({"github": {
        source.external_id: successful(bad, good),
    }}, repository_factory=FailsAfterRealScoreUpdate).ingest_and_score(
        [IngestionRequest(source)], as_of=AS_OF,
    )
    assert report.ingestion.processed_count == 2
    assert {outcome.status for outcome in report.scores} == {
        EventScoreStatus.UPDATED, EventScoreStatus.FAILED,
    }
    assert next(outcome for outcome in report.scores
                if outcome.status is EventScoreStatus.FAILED).error_code == "unexpected"
    async with case.read_session() as session:
        saved = (await session.scalars(select(NewsEvent).where(
            NewsEvent.event_key.in_([outcome.score.event_key
                                    for outcome in report.scores if outcome.score] + [bad_key]),
        ))).all()
        assert len(saved) == 2
        for row in saved:
            fields = (row.category, row.importance_score, row.score_reason, row.rule_version)
            if row.event_key == bad_key:
                assert fields == (None, None, None, None)
            else:
                assert all(value is not None for value in fields)
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.external_id.in_([bad.external_id, good.external_id]),
        )) == 2


async def test_rolled_back_source_is_absent_from_scoring_snapshot(case):
    bad_source = case.source("github", "bad-source")
    good_source = case.source("github", "good-source")
    bad, good = case.github_item("bad"), case.github_item("good")

    class FailsAfterRealItemWrite(NewsRepository):
        async def upsert_item(self, item, *, event_id):
            item_id = await super().upsert_item(item, event_id=event_id)
            if item.external_id == bad.external_id:
                raise RuntimeError("Injected after real item UPSERT")
            return item_id

    report = await case.scoring({"github": {
        bad_source.external_id: successful(bad),
        good_source.external_id: successful(good),
    }}, repository_factory=FailsAfterRealItemWrite).ingest_and_score([
        IngestionRequest(bad_source), IngestionRequest(good_source),
    ], as_of=AS_OF)
    assert [entry.status for entry in report.ingestion.sources] == [
        CollectionStatus.FAILED, CollectionStatus.SUCCESS,
    ]
    assert report.ingestion.sources[0].committed_event_ids == ()
    assert len(report.scores) == 1 and report.scores[0].status is EventScoreStatus.UPDATED
    async with case.read_session() as session:
        assert await session.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.external_id == bad.external_id,
        )) == 0
        assert await session.scalar(select(func.count()).select_from(NewsSource).where(
            NewsSource.external_id == bad_source.external_id,
        )) == 1  # registration was separately committed


async def test_partial_source_scores_committed_item_and_keeps_partial_status(case):
    source, item, responses = one_source(case, status="partial")
    report = await case.scoring(responses).ingest_and_score(
        [IngestionRequest(source)], as_of=AS_OF,
    )
    assert report.ingestion.partial_count == 1
    assert report.ingestion.sources[0].status is CollectionStatus.PARTIAL
    assert report.ingestion.sources[0].committed_event_ids == (
        report.scores[0].event_id,
    )
    assert report.scores[0].status is EventScoreStatus.UPDATED


async def test_nullable_unscored_event_can_be_scored_later(case):
    source, item, responses = one_source(case)
    await case.service(responses).ingest([IngestionRequest(source)])
    before = await case.event_for(item)
    assert (before.category, before.importance_score,
            before.score_reason, before.rule_version) == (None, None, None, None)
    report = await case.scoring(responses).ingest_and_score(
        [IngestionRequest(source)], as_of=AS_OF,
    )
    assert report.scores[0].status is EventScoreStatus.UPDATED
    assert (await case.event_for(item)).rule_version == report.scores[0].score.rule_version


async def test_two_connections_serialize_event_lock_with_bounded_wait(case):
    source, item, responses = one_source(case)
    await case.service(responses).ingest([IngestionRequest(source)])
    event_id = (await case.event_for(item)).id
    issued = asyncio.Event()
    sql_seen = []
    listener = None
    waiting = None
    async with case.read_session() as first, case.read_session() as second:
        first_connection = await first.scalar(select(func.connection_id()))
        second_connection = await second.scalar(select(func.connection_id()))
        assert first_connection != second_connection
        try:
            await NewsRepository(first).load_event_score_snapshot(event_id)
            second_conn = await second.connection()

            def before_cursor(_conn, _cursor, statement, _params, _context, _many):
                if "FOR UPDATE" in statement.upper() and "news_event" in statement:
                    sql_seen.append(statement)
                    issued.set()

            listener = before_cursor
            event.listen(second_conn.sync_connection, "before_cursor_execute", listener)
            waiting = asyncio.create_task(
                NewsRepository(second).load_event_score_snapshot(event_id)
            )
            await asyncio.wait_for(issued.wait(), timeout=5)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiting), timeout=0.25)
            # Release the first transaction's lock; the second should proceed.
            await first.commit()
            snapshot = await asyncio.wait_for(waiting, timeout=10)
            assert snapshot.event_key == (await case.event_for(item)).event_key
            assert sql_seen and all("FOR UPDATE" in sql.upper() for sql in sql_seen)
        finally:
            await first.rollback()
            if waiting is not None and not waiting.done():
                # Release locks before canceling a DB task, then close the Session.
                await first.rollback()
                waiting.cancel()
                try:
                    await waiting
                except asyncio.CancelledError:
                    pass
            if listener is not None:
                event.remove(second_conn.sync_connection, "before_cursor_execute", listener)
            await second.rollback()


async def test_locked_current_read_sees_committed_member_after_prior_rr_snapshot(case):
    paper_source = case.source("arxiv", "paper-source")
    hn_source = case.source("hn", "hn-source")
    paper, story = case.arxiv_item(), case.hn_paper_item()
    await case.service({"arxiv": {
        paper_source.external_id: successful(paper),
    }}).ingest([IngestionRequest(paper_source)])
    event_id = (await case.event_for(paper)).id
    async with case.read_session() as old_snapshot:
        isolation = await old_snapshot.scalar(text("SELECT @@transaction_isolation"))
        assert isolation.upper() == "REPEATABLE-READ"
        # Ordinary SELECT establishes the old consistent-read view.
        assert await old_snapshot.scalar(select(func.count()).select_from(NewsItem).where(
            NewsItem.event_id == event_id,
        )) == 1
        class CollectorWhileOtherSessionIsOpen(BaseCollector):
            async def collect(self, source, *, cursor=None):
                # The original FakeCollector expects *zero* checked-out
                # connections. Here the old-snapshot Session intentionally
                # holds one; ingestion itself must release its connection
                # before this fake HTTP slot.
                assert case.engine.pool.checkedout() == 1
                return CollectionResult(
                    source_id=source.source_id, candidates=(story,),
                    status=CollectionStatus.SUCCESS, fetched_count=1,
                )

        await NewsIngestionService(
            collectors={"hn": CollectorWhileOtherSessionIsOpen()},
            session_factory=case.guarded_factory,
        ).ingest([IngestionRequest(hn_source)])
        # Repository uses locking current reads for event and members.
        current = await NewsRepository(old_snapshot).load_event_score_snapshot(event_id)
        assert {(row.provider, row.external_id) for row in current.items} == {
            (paper.provider, paper.external_id),
            (story.provider, story.external_id),
        }
        await old_snapshot.rollback()


async def test_repeat_three_source_batch_keeps_count_and_score_stable(case):
    sources = [
        case.source("github", "github-source"),
        case.source("arxiv", "paper-source"),
        case.source("hn", "hn-source"),
    ]
    release, paper, story = case.github_item("release"), case.arxiv_item(), case.hn_paper_item()
    responses = {
        "github": {sources[0].external_id: successful(release)},
        "arxiv": {sources[1].external_id: successful(paper)},
        "hn": {sources[2].external_id: successful(story)},
    }
    service = case.scoring(responses)
    requests = [IngestionRequest(source) for source in sources]
    first = await service.ingest_and_score(requests, as_of=AS_OF)
    counts = await case.counts()
    second = await service.ingest_and_score(requests, as_of=AS_OF)
    assert first.ingestion.processed_count == second.ingestion.processed_count == 3
    assert len(first.scores) == len(second.scores) == 2
    assert {outcome.status for outcome in second.scores} == {EventScoreStatus.UNCHANGED}
    assert [(entry.event_id, entry.score) for entry in first.scores] == [
        (entry.event_id, entry.score) for entry in second.scores
    ]
    assert await case.counts() == counts
