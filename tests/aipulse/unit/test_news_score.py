"""Offline, event-level expectations for the experimental rule scorer."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aipulse.news.merge import merge_events
from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.score import ScoreConfig, ScoreEvent, ScoreItem, score_event
from aipulse.news.types import NewsCandidate


AS_OF = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def candidate(
    provider: str, external_id: str, title: str, *, url: str | None = None,
    published_at: datetime = AS_OF, metadata: dict | None = None,
) -> NewsCandidate:
    return NewsCandidate(
        provider=provider, external_id=external_id,
        original_url=url or f"https://example.org/{provider}/{external_id}",
        title=title, raw_excerpt="", published_at=published_at,
        fetched_at=AS_OF, metadata=metadata or {},
    )


def event(*candidates: NewsCandidate) -> ScoreEvent:
    items = deduplicate_batch(
        normalize_candidate(item, index) for index, item in enumerate(candidates, 1)
    )
    groups = merge_events(items)
    assert len(groups) == 1, "Fixture candidates must refer to one event"
    return ScoreEvent.from_group(groups[0], items)


@pytest.mark.parametrize("item,category", [
    (candidate("github", "1", "New LLM model release"), "model"),
    (candidate("arxiv", "2601.12345", "A study of transformers",
               url="https://arxiv.org/abs/2601.12345"), "paper"),
    (candidate("github", "2", "CLI tool release"), "tool"),
    (candidate("hn", "3", "AI app product launch"), "product"),
    (candidate("hn", "4", "AI startup funding round"), "industry"),
    (candidate("hn", "5", "Ask HN: What are your workflows?"), "discussion"),
    (candidate("hn", "6", "Unrelated bulletin"), "other"),
])
def test_classifies_confirmed_signals_and_falls_back_to_other(item, category):
    assert score_event(event(item), as_of=AS_OF).category == category


def test_category_conflict_uses_stable_priority():
    paper = candidate("arxiv", "2601.12345", "New model toolkit",
                      url="https://arxiv.org/abs/2601.12345")
    story = candidate("hn", "7", "Ask HN: New model toolkit",
                      url="https://arxiv.org/abs/2601.12345v2")
    assert score_event(event(paper, story), as_of=AS_OF).category == "paper"
    assert score_event(event(story, paper), as_of=AS_OF).category == "paper"


def test_score_is_bounded_and_reasons_identify_actual_signals():
    result = score_event(event(candidate("github", "1", "New LLM model release")),
                         as_of=AS_OF, config=ScoreConfig(base_points=90, github_points=50))
    assert result.importance_score == 100
    assert result.score_reason
    assert {signal.name for signal in result.signals} >= {"base", "provider", "recency", "topic"}
    assert result.event_key and len(result.event_key) == 64


def test_same_input_time_and_configuration_reproduce_full_result():
    snapshot = event(candidate("github", "1", "New LLM model release"))
    config = ScoreConfig()
    assert score_event(snapshot, as_of=AS_OF, config=config) == score_event(
        snapshot, as_of=AS_OF, config=config
    )


def test_rule_version_changes_with_effective_configuration():
    snapshot = event(candidate("github", "1", "New LLM model release"))
    original = score_event(snapshot, as_of=AS_OF)
    changed = score_event(snapshot, as_of=AS_OF, config=ScoreConfig(github_points=20))
    assert original.rule_version.startswith("experimental-v1:")
    assert original.rule_version != changed.rule_version
    assert original.importance_score != changed.importance_score


def test_missing_publication_time_is_unknown_not_invented():
    original = event(candidate("hn", "1", "Unrelated bulletin"))
    snapshot = ScoreEvent(
        event_key=original.event_key, canonical_identity=original.canonical_identity,
        items=(ScoreItem(provider="hn", external_id="1", title="Unrelated bulletin",
                         published_at=None, metadata={}),),
    )
    result = score_event(snapshot, as_of=AS_OF)
    assert "published_at" in result.unknown_signals
    assert "recency" not in {signal.name for signal in result.signals}


def test_missing_hn_score_is_unknown_not_zero_metric():
    result = score_event(event(candidate("hn", "1", "Ask HN: Thoughts?")), as_of=AS_OF)
    assert "hn_score" in result.unknown_signals
    assert "community" not in {signal.name for signal in result.signals}
    assert "HN score=0" not in result.score_reason


@pytest.mark.parametrize("bad", [-1, 100_001, True, "500", 1.5])
def test_invalid_or_extreme_hn_metric_is_ignored(bad):
    result = score_event(event(candidate("hn", "1", "Ask HN: Thoughts?",
                                         metadata={"score": bad, "authorization": "secret"})),
                         as_of=AS_OF)
    assert "hn_score" in result.unknown_signals
    assert "community" not in {signal.name for signal in result.signals}
    assert "secret" not in result.score_reason


def test_valid_hn_metric_is_counted_once_and_bounded():
    story = candidate("hn", "1", "Ask HN: Thoughts?", metadata={"score": 500})
    single = score_event(event(story), as_of=AS_OF)
    repeated = score_event(event(story, story), as_of=AS_OF)
    assert repeated.importance_score == single.importance_score
    assert [signal.points for signal in single.signals if signal.name == "community"] == [15]


def test_independent_providers_add_one_cross_source_bonus():
    paper = candidate("arxiv", "2601.12345", "A research paper",
                      url="https://arxiv.org/abs/2601.12345")
    story = candidate("hn", "7", "A research paper",
                      url="https://arxiv.org/abs/2601.12345v2", metadata={"score": 20})
    single = score_event(event(paper), as_of=AS_OF)
    combined = score_event(event(paper, story), as_of=AS_OF)
    repeated = score_event(event(paper, story, story), as_of=AS_OF)
    assert combined.importance_score > single.importance_score
    assert repeated == combined
    assert [signal.points for signal in combined.signals if signal.name == "independent_sources"] == [8]


def test_item_order_does_not_change_event_score():
    paper = candidate("arxiv", "2601.12345", "A research paper",
                      url="https://arxiv.org/abs/2601.12345")
    story = candidate("hn", "7", "A research paper",
                      url="https://arxiv.org/abs/2601.12345v2", metadata={"score": 100})
    assert score_event(event(paper, story), as_of=AS_OF) == score_event(
        event(story, paper), as_of=AS_OF
    )


def test_recency_uses_injected_utc_as_of_and_honors_24_hour_boundary():
    published = AS_OF - timedelta(hours=24)
    item = candidate("github", "1", "CLI tool release", published_at=published)
    utc_result = score_event(event(item), as_of=AS_OF)
    offset_time = AS_OF.astimezone(timezone(timedelta(hours=8)))
    offset_result = score_event(event(item), as_of=offset_time)
    assert utc_result == offset_result
    assert [signal.points for signal in utc_result.signals if signal.name == "recency"] == [12]


def test_future_publication_is_unknown_and_naive_as_of_is_rejected():
    snapshot = event(candidate("hn", "1", "Unrelated bulletin",
                               published_at=AS_OF + timedelta(minutes=1)))
    assert "published_at" in score_event(snapshot, as_of=AS_OF).unknown_signals
    with pytest.raises(ValueError, match="timezone"):
        score_event(snapshot, as_of=AS_OF.replace(tzinfo=None))


def test_empty_or_unrelated_event_input_is_rejected():
    snapshot = event(candidate("github", "1", "CLI tool release"))
    with pytest.raises(ValidationError):
        ScoreEvent(event_key=snapshot.event_key,
                   canonical_identity=snapshot.canonical_identity, items=())
    unrelated = deduplicate_batch([normalize_candidate(
        candidate("github", "2", "Different release"), 1,
    )])[0]
    group = merge_events(deduplicate_batch([normalize_candidate(
        candidate("github", "1", "CLI tool release"), 1,
    )]))[0]
    with pytest.raises(ValueError, match="membership"):
        ScoreEvent.from_group(group, [unrelated])


@pytest.mark.parametrize("metadata", [
    {"score": float("nan")},
    {"score": object()},
    {"note": "x" * 17_000},
])
def test_direct_snapshot_rejects_non_json_or_oversized_metadata(metadata):
    with pytest.raises(ValidationError, match="Metadata"):
        ScoreItem(provider="hn", external_id="1", title="Discussion",
                  published_at=AS_OF, metadata=metadata)
