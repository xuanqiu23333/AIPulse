"""Offline orchestration tests; the separate snapshot tests cover real SQL."""

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module

import pytest
from sqlalchemy.exc import OperationalError

from aipulse.news.repository import EventScoreIdentityConflict, EventScoreNotFound
from aipulse.news.score import ScoreConfig, ScoreEvent, ScoreItem
from aipulse.news.service import IngestionReport, SourceIngestionResult
from aipulse.news.types import CollectionStatus


AS_OF = datetime(2026, 10, 17, 8, tzinfo=timezone.utc)
KEY_A = sha256(b"event A").hexdigest()
KEY_B = sha256(b"event B").hexdigest()


def scoring_module():
    return import_module("aipulse.news.scoring_service")


def source_result(provider, event_ids=(), *, status=CollectionStatus.SUCCESS,
                  processed_count=1):
    return SourceIngestionResult(
        provider=provider, external_id=f"{provider}-config", source_id=1,
        status=status, processed_count=processed_count,
        committed_event_ids=tuple(event_ids),
    )


def snapshot(key=KEY_A, *, providers=("github",), hn_score=None):
    items = []
    for provider in providers:
        metadata = {"score": hn_score} if provider == "hn" and hn_score is not None else {}
        items.append(ScoreItem(
            provider=provider, external_id=f"{provider}-101",
            title="New AI model release" if provider == "github" else "New AI paper",
            published_at=AS_OF, fetched_at=AS_OF, metadata=metadata,
        ))
    return ScoreEvent(event_key=key, canonical_identity=f"persisted:{key}", items=tuple(items))


class FakeIngestion:
    def __init__(self, report, ledger):
        self.report = report
        self.ledger = ledger
        self.calls = 0

    async def ingest(self, requests):
        assert self.ledger.active == 0
        self.calls += 1
        self.ledger.log.append("all_sources_committed")
        return self.report


class Ledger:
    def __init__(self, snapshots=None):
        self.snapshots = snapshots or {7: snapshot()}
        self.scores = {}
        self.log = []
        self.active = 0
        self.commits = 0
        self.rollbacks = 0
        self.open_sessions = 0
        self.fail_on = {}
        self.fail_commit_once = False


class FakeTransaction:
    def __init__(self, ledger):
        self.ledger = ledger

    async def __aenter__(self):
        assert self.ledger.active == 0
        self.ledger.active = 1
        self.before_scores = dict(self.ledger.scores)
        self.ledger.log.append("score_transaction_started")

    async def __aexit__(self, exc_type, exc, tb):
        self.ledger.active = 0
        if exc_type or self.ledger.fail_commit_once:
            self.ledger.scores = self.before_scores
            self.ledger.rollbacks += 1
            if self.ledger.fail_commit_once and not exc_type:
                self.ledger.fail_commit_once = False
                self.ledger.log.append("score_transaction_ended")
                raise RuntimeError("Injected commit failure")
        else:
            self.ledger.commits += 1
        self.ledger.log.append("score_transaction_ended")


class FakeSession:
    def __init__(self, ledger):
        self.ledger = ledger

    async def __aenter__(self):
        self.ledger.open_sessions += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.ledger.open_sessions -= 1

    def begin(self):
        return FakeTransaction(self.ledger)

    async def commit(self):
        raise AssertionError("Service must use the transaction context")


class FakeRepository:
    def __init__(self, session):
        self.ledger = session.ledger

    async def load_event_score_snapshot(self, event_id):
        assert self.ledger.active == 1
        self.ledger.log.append(f"load:{event_id}")
        error = self.ledger.fail_on.get(("load", event_id))
        if error:
            raise error
        return self.ledger.snapshots[event_id]

    async def save_event_score(self, event_id, score):
        assert self.ledger.active == 1
        self.ledger.log.append(f"save:{event_id}")
        error = self.ledger.fail_on.get(("save", event_id))
        if error:
            raise error
        assert score.event_key == self.ledger.snapshots[event_id].event_key
        fields = (score.category, score.importance_score,
                  score.score_reason, score.rule_version)
        changed = self.ledger.scores.get(event_id) != fields
        self.ledger.scores[event_id] = fields
        return changed


def workflow(ledger, *sources):
    ingestion = FakeIngestion(IngestionReport(tuple(sources)), ledger)
    service = scoring_module().NewsScoringService(
        ingestion_service=ingestion,
        session_factory=lambda: FakeSession(ledger),
        repository_factory=FakeRepository,
    )
    return service, ingestion


def run(service):
    return asyncio.run(service.ingest_and_score((), as_of=AS_OF))


def test_scoring_starts_after_ingestion_and_uses_a_separate_transaction():
    ledger = Ledger()
    service, ingestion = workflow(ledger, source_result("github", (7,)))
    result = run(service)
    assert ingestion.calls == 1
    assert ledger.log == [
        "all_sources_committed", "score_transaction_started", "load:7",
        "save:7", "score_transaction_ended",
    ]
    assert ledger.commits == 1 and ledger.open_sessions == 0
    assert result.ingestion.processed_count == 1
    assert result.scores[0].status.value == "updated"
    assert ledger.scores[7][0] == "model"


def test_shared_arxiv_hn_event_is_scored_once_using_persisted_items():
    ledger = Ledger({7: snapshot(providers=("arxiv", "hn"), hn_score=120)})
    service, _ = workflow(ledger,
        source_result("arxiv", (7,)), source_result("hn", (7,)))
    result = run(service)
    assert len(result.scores) == 1 and result.scores[0].event_id == 7
    assert ledger.log.count("load:7") == ledger.log.count("save:7") == 1
    assert ledger.scores[7][0] == "paper"
    assert any(signal.name == "community" and signal.points == 10
               for signal in result.scores[0].score.signals)
    assert any(signal.name == "independent_sources"
               for signal in result.scores[0].score.signals)


def test_historical_persisted_item_participates_in_rescoring():
    ledger = Ledger({7: snapshot(providers=("arxiv", "hn"))})
    service, _ = workflow(ledger, source_result("hn", (7,)))
    result = run(service)
    assert any(signal.name == "independent_sources"
               for signal in result.scores[0].score.signals)
    assert "hn_score" in result.scores[0].score.unknown_signals
    assert all(signal.name != "community" for signal in result.scores[0].score.signals)


def test_failed_source_and_rollback_ids_are_excluded_but_partial_commit_scores():
    ledger = Ledger({7: snapshot(providers=("hn",))})
    service, _ = workflow(ledger,
        source_result("github", (99,), status=CollectionStatus.FAILED, processed_count=0),
        source_result("hn", (7,), status=CollectionStatus.PARTIAL))
    result = run(service)
    assert [item.event_id for item in result.scores] == [7]
    assert result.ingestion.failed_count == result.ingestion.partial_count == 1
    assert result.ingestion.processed_count == 1


def test_same_score_is_reported_unchanged_on_repeated_batch():
    ledger = Ledger()
    service, _ = workflow(ledger, source_result("github", (7, 7)))
    first, second = run(service), run(service)
    assert first.scores[0].status.value == "updated"
    assert second.scores[0].status.value == "unchanged"
    assert len(first.scores) == len(second.scores) == 1


def test_empty_batch_has_no_scoring_session():
    ledger = Ledger()
    service, _ = workflow(ledger, source_result("github", (), processed_count=0))
    result = run(service)
    assert result.scores == () and ledger.commits == 0
    assert ledger.open_sessions == 0


def test_failed_score_rolls_back_only_its_transaction_and_next_event_continues():
    ledger = Ledger({7: snapshot(), 8: snapshot(KEY_B)})
    ledger.fail_on[("save", 7)] = RuntimeError("secret database response")
    service, _ = workflow(ledger, source_result("github", (7, 8)))
    result = run(service)
    assert [(row.event_id, row.status.value) for row in result.scores] == [
        (7, "failed"), (8, "updated"),
    ]
    assert result.scores[0].error_code == "unexpected"
    assert "secret" not in repr(result)
    assert ledger.commits == ledger.rollbacks == 1
    assert 8 in ledger.scores and 7 not in ledger.scores


@pytest.mark.parametrize("error,code", [
    (EventScoreNotFound("missing"), "event_missing"),
    (EventScoreIdentityConflict("mismatch"), "identity_conflict"),
])
def test_missing_or_wrong_event_has_explicit_failed_outcome(error, code):
    ledger = Ledger()
    ledger.fail_on[("load", 7)] = error
    service, _ = workflow(ledger, source_result("github", (7,)))
    result = run(service)
    assert result.scores[0].status.value == "failed"
    assert result.scores[0].error_code == code
    assert ledger.rollbacks == 1 and ledger.commits == 0


def test_fixed_utc_as_of_and_configuration_produce_reproducible_score():
    ledger = Ledger()
    service, _ = workflow(ledger, source_result("github", (7,)))
    first, second = run(service), run(service)
    assert first.scores[0].score == second.scores[0].score
    assert first.scores[0].score.rule_version.startswith("experimental-v1:")


def test_injected_score_config_changes_score_and_rule_version():
    ledger = Ledger()
    service, ingestion = workflow(ledger, source_result("github", (7,)))
    default = run(service).scores[0].score
    customized = scoring_module().NewsScoringService(
        ingestion_service=ingestion,
        session_factory=lambda: FakeSession(ledger),
        repository_factory=FakeRepository,
        config=ScoreConfig(base_points=20),
    )
    result = run(customized).scores[0].score
    assert result.importance_score == default.importance_score + 10
    assert result.rule_version != default.rule_version


def test_commit_failure_is_failed_outcome_without_false_success():
    ledger = Ledger()
    ledger.fail_commit_once = True
    service, _ = workflow(ledger, source_result("github", (7,)))
    result = run(service)
    assert result.scores[0].status.value == "failed"
    assert result.scores[0].score is None
    assert ledger.scores == {} and ledger.rollbacks == 1 and ledger.commits == 0


def test_later_batch_reads_newly_persisted_event_membership_again():
    ledger = Ledger({7: snapshot(providers=("arxiv",))})
    service, _ = workflow(ledger, source_result("arxiv", (7,)))
    first = run(service)
    ledger.snapshots[7] = snapshot(providers=("arxiv", "hn"), hn_score=120)
    second = run(service)
    assert first.scores[0].status.value == second.scores[0].status.value == "updated"
    assert second.scores[0].score.importance_score > first.scores[0].score.importance_score
    assert any(signal.name == "independent_sources"
               for signal in second.scores[0].score.signals)


def test_scoring_rule_error_rolls_back_one_event_and_continues(monkeypatch):
    ledger = Ledger({7: snapshot(), 8: snapshot(KEY_B)})
    service, _ = workflow(ledger, source_result("github", (7, 8)))
    module = scoring_module()
    original = module.score_event

    def bad_first(snapshot_value, *, as_of, config):
        if snapshot_value.event_key == KEY_A:
            raise ValueError("Invalid persisted scoring snapshot")
        return original(snapshot_value, as_of=as_of, config=config)

    monkeypatch.setattr(module, "score_event", bad_first)
    result = run(service)
    assert [(row.event_id, row.status.value, row.error_code) for row in result.scores] == [
        (7, "failed", "invalid_snapshot"), (8, "updated", None),
    ]
    assert ledger.rollbacks == ledger.commits == 1
    assert "save:7" not in ledger.log


def test_database_lock_failure_is_reported_without_leaking_statement():
    ledger = Ledger()
    ledger.fail_on[("load", 7)] = OperationalError(
        "SELECT secret-password", {}, Exception("lock wait timeout"),
    )
    service, _ = workflow(ledger, source_result("github", (7,)))
    result = run(service)
    assert result.scores[0].status.value == "failed"
    assert result.scores[0].error_code == "persistence_failed"
    assert "secret-password" not in repr(result)


def test_invalid_as_of_is_rejected_before_ingestion():
    ledger = Ledger()
    service, ingestion = workflow(ledger, source_result("github", (7,)))
    with pytest.raises(ValueError):
        asyncio.run(service.ingest_and_score((), as_of=AS_OF.replace(tzinfo=None)))
    assert ingestion.calls == 0 and ledger.log == []


def test_single_flight_serializes_two_complete_batches_on_one_instance():
    ledger = Ledger()
    service, ingestion = workflow(ledger, source_result("github", (7,)))
    active = 0
    peak = 0

    async def delayed_ingest(requests):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ingestion.report

    ingestion.ingest = delayed_ingest

    async def two_runs():
        return await asyncio.gather(
            service.ingest_and_score((), as_of=AS_OF),
            service.ingest_and_score((), as_of=AS_OF),
        )

    results = asyncio.run(two_runs())
    assert peak == 1
    assert [result.scores[0].status.value for result in results] == [
        "updated", "unchanged",
    ]
