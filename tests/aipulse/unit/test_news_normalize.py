"""Offline behavior of deterministic candidate normalization and batch identity."""

from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError

from aipulse.news.types import NewsCandidate


UTC = timezone.utc
BASE_TIME = datetime(2026, 10, 15, 8, tzinfo=UTC)


def normalizer():
    try:
        return import_module("aipulse.news.normalize")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Normalization module is missing: {exc.name}", pytrace=False)


def candidate(**changes):
    fields = dict(
        provider="arxiv",
        external_id="2601.12345",
        original_url="https://arxiv.org/abs/2601.12345v1",
        title="A paper",
        raw_excerpt="First abstract",
        published_at=BASE_TIME,
        fetched_at=BASE_TIME,
        metadata={"version": "v1"},
    )
    fields.update(changes)
    return NewsCandidate(**fields)


def test_tracking_parameters_and_fragment_do_not_change_canonical_url():
    normalize_url = normalizer().normalize_url
    assert normalize_url("HTTPS://EXAMPLE.COM:443/paper?ref=reader&utm_source=hn#section") == (
        "https://example.com/paper?ref=reader"
    )
    assert normalize_url("https://example.com/paper?ref=reader&fbclid=abc&gclid=xyz") == (
        "https://example.com/paper?ref=reader"
    )


def test_resource_path_query_and_trailing_slash_remain_distinct():
    normalize_url = normalizer().normalize_url
    assert normalize_url("https://example.com/paper?id=1") != normalize_url(
        "https://example.com/paper?id=2"
    )
    assert normalize_url("https://example.com/paper") != normalize_url(
        "https://example.com/paper/"
    )
    assert normalize_url("https://example.com/paper?a=1&b=2") != normalize_url(
        "https://example.com/paper?b=2&a=1"
    )


def test_invalid_url_is_rejected_without_any_network_access(monkeypatch):
    import socket

    def unexpected_network(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    with pytest.raises(ValueError):
        normalizer().normalize_url("javascript:alert(1)")
    with pytest.raises(ValueError):
        normalizer().normalize_url("https://user:secret@example.com/paper")
    with pytest.raises(ValidationError):
        candidate(original_url="not-a-url")


def test_provider_external_id_and_utc_are_preserved():
    source = candidate(
        provider="arxiv", external_id=" 2601.12345 ",
        published_at=datetime(2026, 10, 15, 16, tzinfo=timezone(timedelta(hours=8))),
    )
    item = normalizer().normalize_candidate(source, source_id=7)
    assert (item.provider, item.external_id, item.source_id) == ("arxiv", "2601.12345", 7)
    assert item.published_at == BASE_TIME
    assert item.canonical_identity == "arxiv:2601.12345"
    assert len(item.content_hash) == 64


def test_same_platform_identity_from_two_configs_keeps_one_item_and_first_source():
    module = normalizer()
    early = module.normalize_candidate(candidate(fetched_at=BASE_TIME), source_id=7)
    late = module.normalize_candidate(
        candidate(fetched_at=BASE_TIME + timedelta(minutes=1)), source_id=3
    )
    result = module.deduplicate_batch([late, early])
    assert len(result) == 1
    assert result[0].item_key == ("arxiv", "2601.12345")
    assert result[0].source_id == 7
    assert result[0].source_ids == (3, 7)
    assert module.deduplicate_batch([early, late]) == result


def test_same_external_id_on_other_provider_is_not_deduplicated():
    module = normalizer()
    arxiv = module.normalize_candidate(candidate(external_id="123"), 1)
    hn = module.normalize_candidate(
        candidate(provider="hn", external_id="123", original_url="https://news.ycombinator.com/item?id=123"), 2
    )
    assert [item.item_key for item in module.deduplicate_batch([hn, arxiv])] == [
        ("arxiv", "123"), ("hn", "123")
    ]


def test_equal_content_hash_does_not_remove_distinct_original_ids():
    module = normalizer()
    first = module.normalize_candidate(candidate(external_id="2601.12345"), 1)
    second = module.normalize_candidate(candidate(external_id="2601.12346"), 1)
    assert first.content_hash == second.content_hash
    assert len(module.deduplicate_batch([first, second])) == 2


def test_newer_content_updates_item_and_flags_change_without_source_rewrite():
    module = normalizer()
    before = module.normalize_candidate(candidate(fetched_at=BASE_TIME), 8)
    after = module.normalize_candidate(
        candidate(raw_excerpt="Revised abstract", fetched_at=BASE_TIME + timedelta(hours=1)), 4
    )
    item, = module.deduplicate_batch([after, before])
    assert item.source_id == 8
    assert item.raw_excerpt == "Revised abstract"
    assert item.has_content_variants is True
    assert before.content_hash != after.content_hash
    assert module.content_changed(before.content_hash, item) is True
    assert module.content_changed(after.content_hash, item) is False


def test_tracking_only_change_does_not_change_content_hash():
    module = normalizer()
    first = module.normalize_candidate(candidate(original_url="https://arxiv.org/abs/2601.12345v1?utm_source=a"), 1)
    second = module.normalize_candidate(candidate(original_url="https://arxiv.org/abs/2601.12345v1?utm_source=b"), 1)
    assert first.content_hash == second.content_hash


def test_equal_timestamps_choose_a_stable_winner_and_source():
    module = normalizer()
    a = module.normalize_candidate(candidate(raw_excerpt="A"), 8)
    b = module.normalize_candidate(candidate(raw_excerpt="B"), 4)
    assert module.deduplicate_batch([a, b]) == module.deduplicate_batch([b, a])
    assert module.deduplicate_batch([a, b])[0].source_id == 4
