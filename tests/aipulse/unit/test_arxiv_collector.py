"""arXiv query adapter tests; all HTTP traffic is intercepted by MockTransport."""

import asyncio
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import httpx
import pytest

from aipulse.news.collectors.base import BaseCollector
from aipulse.news.types import CollectionResult, CollectorSource, NewsCandidate


FEED = (Path(__file__).parents[1] / "fixtures" / "arxiv_feed.xml").read_bytes()
EMPTY_FEED = b'<feed xmlns="http://www.w3.org/2005/Atom" />'
SOURCE = CollectorSource(source_id=9, provider="arxiv", external_id="cat:cs.AI OR cat:cs.LG")


def arxiv_module():
    try:
        return import_module("aipulse.news.collectors.arxiv")
    except ModuleNotFoundError as exc:
        pytest.fail(f"arXiv collector is missing: {exc.name}", pytrace=False)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def time(self):
        return self.now

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


def run_with(handler, *, source=SOURCE, cursor=None, clock=None, **options):
    async def run():
        fake = clock or FakeClock()
        module = arxiv_module()
        limiter = module.ArxivRateLimiter(clock=fake.time, sleep=fake.sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = module.ArxivCollector(client, rate_limiter=limiter, **options)
            return await collector.collect(source, cursor=cursor)

    return asyncio.run(run())


def sample_response(request):
    assert request.url == "https://export.arxiv.org/api/query?search_query=cat%3Acs.AI+OR+cat%3Acs.LG&start=0&max_results=30&sortBy=submittedDate&sortOrder=descending"
    return httpx.Response(200, content=FEED)


def feed_for(paper_id):
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
        f'<id>https://arxiv.org/abs/{paper_id}</id>'
        '<title>One paper</title><summary>Abstract.</summary>'
        '<published>2026-01-01T00:00:00Z</published>'
        '<updated>2026-01-02T00:00:00Z</updated>'
        '</entry></feed>'
    ).encode("utf-8")


def test_arxiv_version_keeps_base_paper_id():
    result = run_with(sample_response)
    assert result.status.value == "success"
    first = result.candidates[0]
    assert first.provider == "arxiv" and first.external_id == "2601.12345"
    assert first.metadata["version"] == 2


def test_legacy_arxiv_id_preserved():
    result = run_with(sample_response)
    assert result.candidates[1].external_id == "hep-ex/0307015"
    assert result.candidates[1].metadata["version"] == 2
    assert str(result.candidates[1].original_url) == "https://arxiv.org/abs/hep-ex/0307015v2"


def test_published_and_updated_are_distinct():
    first = run_with(sample_response).candidates[0]
    assert first.published_at == datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert first.metadata["updated_at"] == "2026-02-11T04:30:00+00:00"
    assert first.fetched_at.tzinfo is timezone.utc
    assert first.published_at.isoformat() != first.metadata["updated_at"]


def test_atom_namespace_parsing_and_query_defaults():
    result = run_with(sample_response)
    assert result.fetched_count == 2 and len(result.candidates) == 2
    assert isinstance(result.candidates[0], NewsCandidate)
    assert result.candidates[0].title == "Robust AI Research"
    assert result.candidates[0].raw_excerpt == "A multi-line abstract."


def test_multiple_authors_and_categories():
    metadata = run_with(sample_response).candidates[0].metadata
    assert metadata["authors"] == ["Alice Example", "Bob Example"]
    assert metadata["categories"] == ["cs.AI", "cs.LG"]
    assert metadata["primary_category"] == "cs.AI"
    assert metadata["pdf_url"] == "https://arxiv.org/pdf/2601.12345v2"
    assert metadata["doi"] == "10.1234/example.2026"


def test_missing_optional_metadata():
    metadata = run_with(sample_response).candidates[1].metadata
    assert metadata["authors"] == [] and metadata["categories"] == []
    assert metadata.get("primary_category") is None
    assert metadata.get("pdf_url") is None and metadata.get("doi") is None


def test_empty_query_disables_collection():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=FEED)

    # A validated CollectorSource rejects this; the adapter also fails closed
    # when an unvalidated configuration reaches the boundary.
    unconfigured = CollectorSource.model_construct(source_id=9, provider="arxiv", external_id="  ")
    result = run_with(handler, source=unconfigured)
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert seen == []


def test_empty_feed_returns_success():
    result = run_with(lambda request: httpx.Response(200, content=EMPTY_FEED))
    assert result.status.value == "success" and result.candidates == ()
    assert result.fetched_count == 0 and result.next_cursor is None


def test_arxiv_pagination_is_bounded():
    seen = []

    def handler(request):
        start = int(request.url.params["start"])
        seen.append((start, request.url.params["max_results"]))
        return httpx.Response(200, content=feed_for(f"2601.1234{start}v1"))

    result = run_with(handler, per_page=1, max_pages=2, max_items=3)
    assert result.status.value == "success"
    assert [c.external_id for c in result.candidates] == ["2601.12340", "2601.12341"]
    assert result.fetched_count == 2 and result.next_cursor == "2"
    assert seen == [(0, "1"), (1, "1")]


def test_total_item_budget_stops_before_another_page():
    seen = []

    def handler(request):
        start = int(request.url.params["start"])
        seen.append(start)
        return httpx.Response(200, content=feed_for(f"2601.1234{start}v1"))

    result = run_with(handler, per_page=1, max_pages=5, max_items=2)
    assert result.status.value == "success" and result.fetched_count == 2
    assert len(result.candidates) == 2 and result.next_cursor == "2"
    assert seen == [0, 1]


def test_explicit_sort_parameters_are_sent_to_fixed_endpoint():
    def handler(request):
        assert request.url.host == "export.arxiv.org"
        assert request.url.params["sortBy"] == "lastUpdatedDate"
        assert request.url.params["sortOrder"] == "ascending"
        return httpx.Response(200, content=EMPTY_FEED)

    result = run_with(handler, sort_by="lastUpdatedDate", sort_order="ascending")
    assert result.status.value == "success"


def test_offset_cursor_is_validated():
    seen = []

    def handler(request):
        seen.append(request.url.params["start"])
        return httpx.Response(200, content=EMPTY_FEED)

    for bad in ("-1", "1.5", "https://127.0.0.1/", "9" * 10000):
        result = run_with(handler, cursor=bad)
        assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert seen == []
    assert run_with(handler, cursor="5").status.value == "success"
    assert seen == ["5"]


def test_arxiv_requests_are_serialized():
    async def run():
        active = 0
        max_active = 0
        fake = FakeClock()

        async def handler(request):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return httpx.Response(200, content=EMPTY_FEED)

        module = arxiv_module()
        limiter = module.ArxivRateLimiter(clock=fake.time, sleep=fake.sleep)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            collector = module.ArxivCollector(client, rate_limiter=limiter)
            results = await asyncio.gather(collector.collect(SOURCE), collector.collect(SOURCE))
        return max_active, results

    max_active, results = asyncio.run(run())
    assert max_active == 1
    assert [r.status.value for r in results] == ["success", "success"]


def test_arxiv_rate_limit_spacing():
    fake = FakeClock()
    starts = []

    def handler(request):
        starts.append(fake.time())
        start = request.url.params["start"]
        return httpx.Response(200, content=feed_for(f"2601.1234{start}v1"))

    result = run_with(handler, clock=fake, per_page=1, max_pages=2)
    assert result.status.value == "success"
    assert starts == [0.0, 3.0] and fake.waits == [3.0]


def test_malformed_entry_isolated():
    bad = FEED.replace(b"hep-ex/0307015v2", b"invalid-id")
    result = run_with(lambda request: httpx.Response(200, content=bad))
    assert result.status.value == "partial" and result.fetched_count == 2
    assert [c.external_id for c in result.candidates] == ["2601.12345"]
    assert result.error_code.value == "invalid_response"


def test_malformed_arxiv_url_is_isolated():
    bad = FEED.replace(
        b"<id>http://arxiv.org/abs/hep-ex/0307015v2</id>",
        b"<id>http://[::1/abs/foo</id>",
    )
    result = run_with(lambda request: httpx.Response(200, content=bad))
    assert result.status.value == "partial" and result.fetched_count == 2
    assert [c.external_id for c in result.candidates] == ["2601.12345"]
    assert result.error_code.value == "invalid_response"


def test_malformed_xml_returns_safe_error():
    result = run_with(lambda request: httpx.Response(200, content=b"<feed><entry>Bearer private-token"))
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert "private-token" not in repr(result)


@pytest.mark.parametrize("payload", [
    b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///C:/secret">]><feed>&x;</feed>',
    '<!DOCTYPE feed [<!ENTITY x "EXPANDED">]><feed>&x;</feed>'.encode("utf-16"),
])
def test_xml_entity_payload_is_rejected(payload):
    result = run_with(lambda request: httpx.Response(200, content=payload))
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"
    assert result.candidates == ()


def test_page_failure_preserves_candidates():
    seen = []

    def handler(request):
        start = request.url.params["start"]
        seen.append(start)
        if start == "0":
            return httpx.Response(200, content=feed_for("2601.12345v1"))
        return httpx.Response(503, text="private-token")

    result = run_with(handler, per_page=1)
    assert result.status.value == "partial" and result.error_code.value == "unavailable"
    assert [c.external_id for c in result.candidates] == ["2601.12345"]
    assert result.next_cursor == "1" and seen == ["0", "1"]
    assert "private-token" not in repr(result)


def test_response_size_is_bounded():
    result = run_with(lambda request: httpx.Response(200, content=b"x" * 2049), max_response_bytes=2048)
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"


def test_collector_conforms_to_base_contract():
    module = arxiv_module()
    assert issubclass(module.ArxivCollector, BaseCollector)
    result = run_with(lambda request: httpx.Response(200, content=EMPTY_FEED))
    assert isinstance(result, CollectionResult)


@pytest.mark.parametrize("status,code", [(429, "rate_limit"), (503, "unavailable"), (302, "unavailable")])
def test_http_error_is_classified_without_following_redirects(status, code):
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/secret"}, text="private-token")

    result = run_with(handler)
    assert result.status.value == "failed" and result.error_code.value == code
    assert len(seen) == 1 and "private-token" not in repr(result)


@pytest.mark.parametrize("error,code", [
    (httpx.ReadTimeout("private-token"), "timeout"),
    (httpx.ConnectError("private-token"), "unavailable"),
])
def test_transport_error_is_safe(error, code):
    def handler(request):
        raise error

    result = run_with(handler)
    assert result.status.value == "failed" and result.error_code.value == code
    assert "private-token" not in repr(result)


def test_wrong_atom_namespace_is_rejected():
    result = run_with(lambda request: httpx.Response(200, content=b"<feed><entry /></feed>"))
    assert result.status.value == "failed" and result.error_code.value == "invalid_response"


def test_invalid_published_timestamp_is_isolated():
    bad = FEED.replace(b"2026-01-10T08:00:00+08:00", b"not-a-time")
    result = run_with(lambda request: httpx.Response(200, content=bad))
    assert result.status.value == "partial"
    assert [c.external_id for c in result.candidates] == ["hep-ex/0307015"]


def test_duplicate_versions_in_one_batch_keep_one_paper():
    duplicate = feed_for("2601.12345v1").replace(b"</feed>", b"")
    second = feed_for("2601.12345v2").split(b"<entry>", 1)[1].split(b"</feed>", 1)[0]
    payload = duplicate + b"<entry>" + second + b"</feed>"
    result = run_with(lambda request: httpx.Response(200, content=payload))
    assert result.status.value == "success" and result.fetched_count == 2
    assert [c.external_id for c in result.candidates] == ["2601.12345"]
