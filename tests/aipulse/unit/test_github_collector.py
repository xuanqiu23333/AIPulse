"""Offline GitHub Releases adapter tests; every request uses MockTransport."""

import asyncio
import json
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import httpx
import pytest

from aipulse.news.collectors.base import BaseCollector
from aipulse.news.types import CollectionResult, CollectorSource, NewsCandidate


FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "github_releases.json").read_text(encoding="utf-8"))
SOURCE = CollectorSource(source_id=7, provider="github", external_id="langchain-ai/langgraph")


def collector_type():
    try:
        return import_module("aipulse.news.collectors.github").GitHubReleaseCollector
    except ModuleNotFoundError as exc:
        pytest.fail(f"GitHub collector is missing: {exc.name}", pytrace=False)


def run_with(handler, *, source=SOURCE, cursor=None, **options):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await collector_type()(client, **options).collect(source, cursor=cursor)

    return asyncio.run(run())


def ok(request):
    assert request.url.host == "api.github.com"
    assert request.url.path == "/repos/langchain-ai/langgraph/releases"
    return httpx.Response(200, json=FIXTURE)


def next_link(page, *, base="https://api.github.com/repos/langchain-ai/langgraph/releases"):
    return f'<{base}?per_page=1&page={page}>; rel="next"'


def test_release_id_is_stable_external_id_and_utc_fields():
    result = run_with(ok)
    assert result.source_id == SOURCE.source_id
    assert result.status.value == "success"
    assert result.fetched_count == 3
    assert len(result.candidates) == 1
    item = result.candidates[0]
    assert isinstance(item, NewsCandidate)
    assert item.provider == "github" and item.external_id == "101"
    assert str(item.original_url) == FIXTURE[0]["html_url"]
    assert item.title == "LangGraph 1.2.0" and item.raw_excerpt == "A stable release."
    assert item.published_at == datetime(2026, 10, 15, tzinfo=timezone.utc)
    assert item.fetched_at.tzinfo is timezone.utc
    assert item.metadata["tag_name"] == "v1.2.0"


def test_title_uses_tag_name_when_name_missing():
    result = run_with(ok, include_prereleases=True)
    assert [item.external_id for item in result.candidates] == ["101", "102"]
    assert result.candidates[1].title == "v1.3.0-rc1"


def test_draft_is_always_filtered_even_if_prereleases_enabled():
    result = run_with(ok, include_prereleases=True)
    assert "103" not in [item.external_id for item in result.candidates]
    assert result.fetched_count == 3


def test_prereleases_are_excluded_by_default_and_included_when_enabled():
    default = run_with(ok)
    included = run_with(ok, include_prereleases=True)
    assert [item.external_id for item in default.candidates] == ["101"]
    assert [item.external_id for item in included.candidates] == ["101", "102"]


def test_empty_release_list_is_success():
    result = run_with(lambda request: httpx.Response(200, json=[]))
    assert result.status.value == "success" and result.candidates == ()
    assert result.fetched_count == 0 and result.next_cursor is None


def test_only_configured_repository_is_requested():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    result = run_with(handler)
    assert result.status.value == "success"
    assert len(seen) == 1 and seen[0].startswith("https://api.github.com/repos/langchain-ai/langgraph/releases?")


def test_link_pagination_is_bounded_and_cursor_resumes():
    seen = []

    def handler(request):
        page = int(request.url.params["page"])
        seen.append(page)
        release = {**FIXTURE[0], "id": 100 + page}
        return httpx.Response(200, json=[release], headers={"Link": next_link(page + 1)})

    first = run_with(handler, max_pages=2, max_items=2, per_page=1)
    assert first.status.value == "success"
    assert [item.external_id for item in first.candidates] == ["101", "102"]
    assert first.next_cursor == "3" and seen == [1, 2]
    resumed = run_with(handler, cursor=first.next_cursor, max_pages=1, max_items=1, per_page=1)
    assert [item.external_id for item in resumed.candidates] == ["103"]
    assert seen == [1, 2, 3]


def test_total_record_budget_stops_before_another_page():
    seen = []

    def handler(request):
        seen.append(int(request.url.params["page"]))
        page = seen[-1]
        return httpx.Response(
            200, json=[{**FIXTURE[0], "id": 100 + page}],
            headers={"Link": next_link(page + 1)},
        )

    result = run_with(handler, per_page=1, max_pages=5, max_items=2)
    assert result.status.value == "success"
    assert result.fetched_count == 2 and len(result.candidates) == 2
    assert result.next_cursor == "3" and seen == [1, 2]


def test_manual_five_item_page_does_not_fetch_linked_next_page():
    seen = []

    def handler(request):
        seen.append(int(request.url.params["page"]))
        releases = [{**FIXTURE[0], "id": 201 + index} for index in range(5)]
        link = next_link(
            2, base="https://api.github.com/repositories/676672661/releases"
        ).replace("per_page=1", "per_page=5")
        return httpx.Response(200, json=releases, headers={"Link": link})

    result = run_with(handler, per_page=5, max_pages=1, max_items=5)
    assert seen == [1]
    assert result.status.value == "success"
    assert result.fetched_count == len(result.candidates) == 5
    assert result.next_cursor == "2"


def test_canonical_link_advances_page_on_configured_repository_only():
    seen = []

    def handler(request):
        seen.append((request.url.path, int(request.url.params["page"])))
        page = seen[-1][1]
        headers = {}
        if page == 1:
            headers["Link"] = next_link(
                2, base="https://api.github.com/repositories/676672661/releases"
            )
        return httpx.Response(200, json=[{**FIXTURE[0], "id": 300 + page}], headers=headers)

    result = run_with(handler, per_page=1, max_pages=2, max_items=2)
    assert result.status.value == "success"
    assert seen == [
        ("/repos/langchain-ai/langgraph/releases", 1),
        ("/repos/langchain-ai/langgraph/releases", 2),
    ]


@pytest.mark.parametrize("status", [403, 429])
def test_rate_limit_is_classified_without_retry(status):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, text="Bearer top-secret", headers={"Retry-After": "60"})

    result = run_with(handler, token="top-secret")
    assert result.status.value == "failed" and result.error_code.value == "rate_limit"
    assert result.candidates == () and len(seen) == 1
    assert seen[0].headers["Authorization"] == "Bearer top-secret"
    assert "top-secret" not in repr(result) and "top-secret" not in result.error_summary


def test_later_page_failure_retains_earlier_candidates_and_retry_cursor():
    def handler(request):
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=[FIXTURE[0]], headers={"Link": next_link(2)})
        return httpx.Response(503, text="private response body")

    result = run_with(handler, per_page=1, max_pages=3)
    assert result.status.value == "partial"
    assert [item.external_id for item in result.candidates] == ["101"]
    assert result.error_code.value == "unavailable" and result.next_cursor == "2"
    assert "private response body" not in repr(result)


def test_timeout_and_connection_errors_are_safe():
    for error, code in ((httpx.ReadTimeout("secret"), "timeout"), (httpx.ConnectError("secret"), "unavailable")):
        def handler(request):
            raise error

        result = run_with(handler)
        assert result.status.value == "failed" and result.error_code.value == code
        assert "secret" not in repr(result)


def test_invalid_json_is_failed_without_echoing_body():
    result = run_with(lambda request: httpx.Response(200, text="{Bearer top-secret"))
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert "top-secret" not in repr(result)


def test_bad_release_is_isolated_and_good_release_survives():
    bad = {**FIXTURE[0], "id": None}
    result = run_with(lambda request: httpx.Response(200, json=[bad, FIXTURE[0]]))
    assert result.status.value == "partial"
    assert result.fetched_count == 2
    assert [item.external_id for item in result.candidates] == ["101"]
    assert result.error_code.value == "invalid_response"


def test_all_invalid_release_records_fail_without_candidates():
    result = run_with(lambda request: httpx.Response(200, json=[{"id": "Bearer top-secret"}]))
    assert result.status.value == "failed" and result.fetched_count == 1
    assert result.error_code.value == "invalid_response"
    assert "top-secret" not in repr(result)


@pytest.mark.parametrize("link", [
    next_link(2, base="https://evil.example/steal"),
    next_link(2, base="http://api.github.com/repos/langchain-ai/langgraph/releases"),
    next_link(2, base="https://api.github.com/repos/other/repo/releases"),
    next_link(2, base="https://127.0.0.1/repos/langchain-ai/langgraph/releases"),
    next_link(2, base="https://api.github.com:8443/repos/langchain-ai/langgraph/releases"),
])
def test_malicious_next_link_is_rejected_without_request(link):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[FIXTURE[0]], headers={"Link": link})

    result = run_with(handler, per_page=1)
    assert result.status.value == "partial" and result.error_code.value == "invalid_response"
    assert [item.external_id for item in result.candidates] == ["101"]
    assert len(seen) == 1


def test_oversized_response_and_wrong_shape_are_rejected():
    for response in (httpx.Response(200, content=b"x" * 4097), httpx.Response(200, json={"id": 1})):
        result = run_with(lambda request: response, max_response_bytes=4096)
        assert result.status.value == "failed"
        assert result.error_code.value == "invalid_response"


def test_excerpt_is_bounded_to_candidate_limit():
    long_note = {**FIXTURE[0], "body": "x" * 5000}
    result = run_with(lambda request: httpx.Response(200, json=[long_note]))
    assert result.status.value == "success"
    assert len(result.candidates[0].raw_excerpt) == 4000


def test_invalid_source_and_cursor_do_not_make_http_requests():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    wrong = CollectorSource(source_id=7, provider="arxiv", external_id="x")
    for source, cursor in (
        (wrong, None), (SOURCE, "https://127.0.0.1/"), (SOURCE, "0"),
        (SOURCE, "9" * 10000),
    ):
        result = run_with(handler, source=source, cursor=cursor)
        assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert seen == []


def test_collector_implements_existing_abstract_contract():
    collector = collector_type()
    assert issubclass(collector, BaseCollector)
    result = run_with(lambda request: httpx.Response(200, json=[]))
    assert isinstance(result, CollectionResult)
