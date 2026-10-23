"""Offline checks for the populated MySQL API suite's HTTP JSON assertions."""

from datetime import datetime, timezone

import pytest

from tests_mysql.mysql.test_news_api_data_mysql import ApiCase


class StaticClient:
    def __init__(self, body):
        self.body = body

    async def get(self, _path):
        return type("Response", (), {"status_code": 200, "json": lambda _self: self.body})()


def event_json(*, score=0, timestamp="2026-10-15T09:00:00Z"):
    return {
        "event_id": 7, "event_key": "a" * 64, "title": "Synthetic event",
        "category": "model" if score is not None else None,
        "importance_score": score,
        "score_reason": "Synthetic reason" if score is not None else None,
        "rule_version": "test-v1" if score is not None else None,
        "status": "active", "created_at": timestamp, "updated_at": timestamp,
        "providers": ["github"], "item_count": 1,
    }


@pytest.mark.asyncio
async def test_api_case_accepts_serialized_utc_time_and_preserves_zero_score():
    body = {"page": 1, "page_size": 20, "total": 1, "items": [event_json()]}
    case = ApiCase(client=StaticClient(body), ids={}, namespace="unused")

    page = await case.page()

    assert (page.page, page.page_size, page.total) == (1, 20, 1)
    assert page.items[0].created_at == datetime(2026, 10, 15, 9, tzinfo=timezone.utc)
    assert page.items[0].importance_score == 0


@pytest.mark.asyncio
async def test_api_case_accepts_plus_zero_utc_and_keeps_null_score_distinct():
    body = {"page": 2, "page_size": 1, "total": 2,
            "items": [event_json(score=None, timestamp="2026-10-15T09:00:00+00:00")]}
    page = await ApiCase(client=StaticClient(body), ids={}, namespace="unused").page()
    assert page.items[0].created_at.utcoffset().total_seconds() == 0
    assert page.items[0].importance_score is None
    assert page.items[0].category is None


@pytest.mark.parametrize("bad", [
    "2026-10-15T09:00:00", "2026-10-15T17:00:00+08:00", "not-a-date",
])
def test_http_json_rejects_naive_or_invalid_timestamps(bad):
    from tests_mysql.mysql.test_news_api_data_mysql import parse_utc_json_timestamp

    with pytest.raises((TypeError, ValueError), match="UTC"):
        parse_utc_json_timestamp(bad)


def test_detail_json_parses_nested_source_times_and_required_shape():
    from tests_mysql.mysql.test_news_api_data_mysql import detail_json_view

    detail = event_json()
    detail.update(summary=None, sources=[{
        "provider": "arxiv", "external_id": "2601.12345", "title": "Paper",
        "original_url": "https://arxiv.org/abs/2601.12345",
        "source_name": "AI papers", "published_at": None,
        "fetched_at": "2026-10-16T12:00:00+00:00",
    }])
    parsed = detail_json_view(detail)
    assert parsed.sources[0].published_at is None
    assert parsed.sources[0].fetched_at == datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    assert parsed.sources[0].provider == "arxiv"


def test_http_page_requires_items_and_integer_total():
    from tests_mysql.mysql.test_news_api_data_mysql import page_json_view

    with pytest.raises((TypeError, ValueError, AssertionError)):
        page_json_view({"page": 1, "page_size": 20, "total": "1", "items": []})
    with pytest.raises((TypeError, ValueError, AssertionError)):
        page_json_view({"page": 1, "page_size": 20, "total": 1})


def test_detail_json_rejects_naive_nested_timestamp():
    from tests_mysql.mysql.test_news_api_data_mysql import detail_json_view

    detail = event_json()
    detail.update(summary=None, sources=[{
        "provider": "hn", "external_id": "10", "title": "Story",
        "original_url": "https://news.ycombinator.com/item?id=10",
        "source_name": "HN", "published_at": None,
        "fetched_at": "2026-10-16T12:00:00",
    }])
    with pytest.raises((TypeError, ValueError), match="UTC"):
        detail_json_view(detail)
