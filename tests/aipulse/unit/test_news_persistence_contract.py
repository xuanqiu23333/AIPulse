"""Pure rules for the future repository; these tests make no database calls."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from importlib import import_module

import pytest

from aipulse.news.normalize import normalize_candidate
from aipulse.news.types import NewsCandidate


def contract():
    try:
        return import_module("aipulse.news.persistence_contract")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Persistence contract module is missing: {exc.name}", pytrace=False)


def news_candidate(url: str = "https://example.org/paper?issue=1") -> NewsCandidate:
    moment = datetime(2026, 10, 15, 8, 30, 1, 123456, tzinfo=timezone.utc)
    return NewsCandidate(
        provider="hn", external_id="42", original_url=url,
        title="A paper", raw_excerpt="An excerpt", published_at=moment,
        fetched_at=moment, metadata={},
    )


def test_aware_candidate_time_is_converted_to_naive_utc_mysql_datetime():
    convert = contract().to_mysql_utc_datetime
    china = timezone(timedelta(hours=8))
    incoming = datetime(2026, 10, 15, 16, 30, 1, 123456, tzinfo=china)
    assert convert(incoming) == datetime(2026, 10, 15, 8, 30, 1, 123456)
    assert convert(news_candidate().fetched_at).tzinfo is None


def test_naive_incoming_time_is_rejected_not_guessed():
    with pytest.raises(ValueError):
        contract().to_mysql_utc_datetime(datetime(2026, 10, 15, 8))


def test_canonical_url_hash_uses_existing_normalized_url():
    first = normalize_candidate(news_candidate("https://EXAMPLE.org/paper?issue=1&utm_source=hn"), 1)
    second = normalize_candidate(news_candidate("https://example.org/paper?issue=1&fbclid=x"), 2)
    different = normalize_candidate(news_candidate("https://example.org/paper?issue=2"), 3)
    hashed = contract().canonical_url_hash
    assert hashed(first) == sha256(b"https://example.org/paper?issue=1").hexdigest()
    assert hashed(first) == hashed(second)
    assert hashed(first) != hashed(different)
    assert len(hashed(first)) == 64


def test_storage_metadata_keeps_only_expected_collector_fields_and_copies_values():
    gh = NewsCandidate(
        provider="github", external_id="701",
        original_url="https://github.com/acme/agent/releases/tag/v1",
        title="v1", raw_excerpt="release", published_at=news_candidate().published_at,
        fetched_at=news_candidate().fetched_at,
        metadata={
            "tag_name": "v1", "prerelease": False,
            "api_token": "must-not-persist", "authorization": "Bearer secret",
            "raw_response": {"entire": "payload"},
        },
    )
    assert contract().metadata_for_storage(gh) == {"tag_name": "v1", "prerelease": False}
    assert "api_token" in gh.metadata  # the rule does not mutate the collector object

    arxiv = NewsCandidate(
        provider="arxiv", external_id="2601.12345",
        original_url="https://arxiv.org/abs/2601.12345v1",
        title="paper", raw_excerpt="abstract", published_at=gh.published_at,
        fetched_at=gh.fetched_at,
        metadata={"version": 1, "authors": ["Researcher"], "categories": ["cs.AI"]},
    )
    stored = contract().metadata_for_storage(arxiv)
    assert stored == {"version": 1, "authors": ["Researcher"], "categories": ["cs.AI"]}
    stored["authors"].append("changed")
    assert arxiv.metadata["authors"] == ["Researcher"]


def test_storage_metadata_uses_empty_object_for_unknown_or_empty_fields():
    assert contract().metadata_for_storage(news_candidate()) == {}
    incoming = news_candidate().model_copy(update={"metadata": {"token": "secret"}})
    assert contract().metadata_for_storage(incoming) == {}


@pytest.mark.parametrize(
    ("stored", "incoming", "expected"),
    [
        (datetime(2026, 10, 15, 8), datetime(2026, 10, 15, 9, tzinfo=timezone.utc), True),
        (datetime(2026, 10, 15, 8), datetime(2026, 10, 15, 7, tzinfo=timezone.utc), False),
        (datetime(2026, 10, 15, 8), datetime(2026, 10, 15, 8, tzinfo=timezone.utc), False),
    ],
)
def test_future_source_update_only_when_observation_is_strictly_newer(stored, incoming, expected):
    assert contract().should_update_source_fields(stored, incoming) is expected
