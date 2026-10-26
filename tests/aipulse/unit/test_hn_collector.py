"""Offline Hacker News Collector tests using only HTTPX MockTransport."""

import asyncio
import json
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import httpx
import pytest

from aipulse.news.collectors.base import BaseCollector
from aipulse.news.types import CollectionResult, CollectorSource, NewsCandidate


DATA = json.loads((Path(__file__).parents[1] / "fixtures" / "hn_items.json").read_text(encoding="utf-8"))
TOP = CollectorSource(source_id=11, provider="hn", external_id="topstories")
NEW = CollectorSource(source_id=12, provider="hn", external_id="newstories")


def collector_type():
    try:
        return import_module("aipulse.news.collectors.hn").HackerNewsCollector
    except ModuleNotFoundError as exc:
        pytest.fail(f"Hacker News Collector is missing: {exc.name}", pytrace=False)


def fixture_handler(request):
    assert request.url.scheme == "https" and request.url.host == "hacker-news.firebaseio.com"
    path = request.url.path
    if path == "/v0/topstories.json":
        return httpx.Response(200, json=DATA["topstories"])
    if path == "/v0/newstories.json":
        return httpx.Response(200, json=DATA["newstories"])
    if path.startswith("/v0/item/") and path.endswith(".json"):
        item_id = path.removeprefix("/v0/item/").removesuffix(".json")
        return httpx.Response(200, json=DATA["items"].get(item_id))
    pytest.fail(f"Unexpected API path: {path}")


def run_with(handler=fixture_handler, *, source=TOP, cursor=None, **options):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collector_type()(client, **options).collect(source, cursor=cursor)

    return asyncio.run(run())


def test_hn_id_is_stable_external_id():
    result = run_with()
    assert result.candidates[0].provider == "hn"
    assert result.candidates[0].external_id == "101"
    assert isinstance(result.candidates[0], NewsCandidate)


def test_topstories_returns_valid_candidates():
    result = run_with()
    assert result.source_id == 11 and result.status.value == "success"
    assert [item.external_id for item in result.candidates] == ["101", "102", "106"]
    assert result.fetched_count == 7 and result.next_cursor is None


def test_newstories_source_is_supported():
    result = run_with(source=NEW)
    assert result.source_id == 12 and result.status.value == "success"
    assert [item.external_id for item in result.candidates] == ["106", "101"]


def test_deleted_and_dead_items_are_skipped():
    def handler(request):
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[103, 104])
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "success" and result.candidates == ()
    assert result.fetched_count == 2


def test_non_story_items_are_skipped():
    result = run_with(lambda request: httpx.Response(200, json=[105]) if request.url.path.endswith("topstories.json") else fixture_handler(request))
    assert result.status.value == "success" and result.candidates == ()


def test_text_only_story_keeps_hn_url():
    result = run_with()
    ask = result.candidates[1]
    assert ask.title == "Ask HN: What changed?"
    assert str(ask.original_url) == "https://news.ycombinator.com/item?id=102"
    assert result.candidates[2].external_id == "106"
    assert str(result.candidates[2].original_url) == "https://news.ycombinator.com/item?id=106"


def test_external_url_preserves_discussion_link_without_fetching_it():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return fixture_handler(request)

    item = run_with(handler).candidates[0]
    assert str(item.original_url) == "https://example.org/ai-release"
    assert item.metadata["discussion_url"] == "https://news.ycombinator.com/item?id=101"
    assert all(url.startswith("https://hacker-news.firebaseio.com/v0/") for url in seen)


def test_unix_timestamp_converts_to_utc():
    item = run_with().candidates[0]
    assert item.published_at == datetime(2025, 10, 15, tzinfo=timezone.utc)
    assert item.published_at.tzinfo is timezone.utc
    assert item.fetched_at.tzinfo is timezone.utc


def test_html_content_is_safely_normalized():
    first, ask = run_with().candidates[:2]
    assert first.title == "AI release & tools"
    assert first.raw_excerpt == "New AI tools & notes."
    assert ask.raw_excerpt == "Hello world."
    assert "script" not in ask.raw_excerpt and "secret" not in ask.raw_excerpt


def test_score_and_comment_count_are_optional():
    metadata = run_with().candidates[2].metadata
    assert metadata["score"] is None and metadata["descendants"] is None
    assert metadata["author"] is None
    assert metadata["item_type"] == "story"


def test_item_fetch_failures_leave_other_items_available():
    def handler(request):
        if request.url.path == "/v0/item/102.json":
            return httpx.Response(503, text="Bearer private-token")
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "partial" and result.error_code.value == "unavailable"
    assert [item.external_id for item in result.candidates] == ["101", "106"]
    assert "private-token" not in repr(result)


def test_null_item_does_not_crash_collector():
    def handler(request):
        if request.url.path == "/v0/item/102.json":
            return httpx.Response(200, json=None)
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "partial" and result.error_code.value == "invalid_response"
    assert [item.external_id for item in result.candidates] == ["101", "106"]


def test_invalid_item_is_isolated():
    def handler(request):
        if request.url.path == "/v0/item/102.json":
            return httpx.Response(200, json={"id": 102, "type": "story", "title": "Missing time"})
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "partial" and result.error_code.value == "invalid_response"
    assert [item.external_id for item in result.candidates] == ["101", "106"]


@pytest.mark.parametrize("response,code", [
    (httpx.Response(503, text="private-token"), "unavailable"),
    (httpx.Response(429, text="private-token"), "rate_limit"),
    (httpx.Response(200, text="{private-token"), "invalid_response"),
])
def test_id_list_request_failure_returns_failed(response, code):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return response

    result = run_with(handler)
    assert result.status.value == "failed" and result.error_code.value == code
    assert result.candidates == () and seen == ["/v0/topstories.json"]
    assert "private-token" not in repr(result)


def test_max_items_and_concurrency_are_bounded():
    async def run():
        seen = []
        active = 0
        peak = 0

        async def handler(request):
            nonlocal active, peak
            seen.append(request.url.path)
            if request.url.path.endswith("topstories.json"):
                return httpx.Response(200, json=DATA["topstories"])
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return fixture_handler(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await collector_type()(client, max_ids=2, max_candidates=2, max_requests=3, concurrency=2).collect(TOP)
        return result, seen, peak

    result, seen, peak = asyncio.run(run())
    assert result.status.value == "success" and result.next_cursor == "2"
    assert result.fetched_count == 2 and len(result.candidates) == 2
    assert seen == ["/v0/topstories.json", "/v0/item/101.json", "/v0/item/102.json"]
    assert peak == 2


def test_max_candidate_budget_stops_before_more_requests():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return fixture_handler(request)

    result = run_with(handler, max_candidates=1, concurrency=3)
    assert [item.external_id for item in result.candidates] == ["101"]
    assert result.next_cursor == "1" and result.fetched_count == 1
    assert seen == ["/v0/topstories.json", "/v0/item/101.json"]


def test_max_http_request_budget_stops_before_more_items():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return fixture_handler(request)

    result = run_with(handler, max_requests=2, concurrency=3)
    assert [item.external_id for item in result.candidates] == ["101"]
    assert result.next_cursor == "1"
    assert len(seen) == 2


def test_cursor_validation():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return fixture_handler(request)

    for cursor in ("-1", "1.0", "01", "https://127.0.0.1/", "9" * 10000):
        result = run_with(handler, cursor=cursor)
        assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert seen == []
    result = run_with(handler, cursor="1", max_ids=1)
    assert [item.external_id for item in result.candidates] == ["102"]
    assert result.next_cursor == "2"


def test_duplicate_ids_are_handled():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("topstories.json"):
            return httpx.Response(200, json=[101, 101, 102])
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "success"
    assert [item.external_id for item in result.candidates] == ["101", "102"]
    assert result.fetched_count == 3
    assert seen.count("/v0/item/101.json") == 1


@pytest.mark.parametrize("error,code", [
    (httpx.ReadTimeout("private-token"), "timeout"),
    (httpx.ConnectError("private-token"), "unavailable"),
])
def test_timeout_and_rate_limit_return_safe_errors(error, code):
    def handler(request):
        if request.url.path.endswith("topstories.json"):
            raise error
        pytest.fail("Item must not be requested after list failure")

    result = run_with(handler)
    assert result.status.value == "failed" and result.error_code.value == code
    assert "private-token" not in repr(result)


def test_response_size_is_bounded():
    result = run_with(lambda request: httpx.Response(200, content=b"x" * 2049), max_response_bytes=2048)
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"


def test_collector_conforms_to_base_contract():
    assert issubclass(collector_type(), BaseCollector)
    result = run_with(lambda request: httpx.Response(200, json=[]))
    assert isinstance(result, CollectionResult)
    assert result.status.value == "success" and result.candidates == ()


def test_invalid_external_url_falls_back_to_discussion_with_gap():
    def handler(request):
        if request.url.path == "/v0/item/101.json":
            return httpx.Response(200, json={**DATA["items"]["101"], "url": "file:///C:/secret"})
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "partial" and result.error_code.value == "invalid_response"
    assert str(result.candidates[0].original_url) == "https://news.ycombinator.com/item?id=101"


def test_invalid_unix_timestamp_is_isolated():
    def handler(request):
        if request.url.path == "/v0/item/102.json":
            return httpx.Response(200, json={**DATA["items"]["102"], "time": 10**100})
        return fixture_handler(request)

    result = run_with(handler)
    assert result.status.value == "partial" and result.error_code.value == "invalid_response"
    assert [item.external_id for item in result.candidates] == ["101", "106"]


def test_item_404_and_invalid_json_are_isolated():
    for response in (httpx.Response(404), httpx.Response(200, text="not json")):
        def handler(request):
            if request.url.path == "/v0/item/102.json":
                return response
            return fixture_handler(request)

        result = run_with(handler)
        assert result.status.value == "partial"
        assert [item.external_id for item in result.candidates] == ["101", "106"]


def test_unconfigured_source_fails_without_request():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    source = CollectorSource(source_id=13, provider="hn", external_id="beststories")
    result = run_with(handler, source=source)
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert seen == []
