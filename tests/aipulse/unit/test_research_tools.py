"""Only MockTransport exercises real fixed-host research parsers."""

import asyncio
from pathlib import Path

import httpx
import pytest

from aipulse.research.state import ResearchRequest
from aipulse.research.tools import ArxivSearch, PublicAPISearch, ResearchRetriever, SearchOutcome


REQUEST = ResearchRequest(topic="AI state workflow")


def run_search(provider, handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
            return await PublicAPISearch(client, provider).search("AI state workflow", REQUEST)
    return asyncio.run(run())


def test_github_search_returns_descriptions_without_following_discovered_urls():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "api.github.com"
        assert request.url.path == "/search/repositories"
        assert request.url.params["per_page"] == "3"
        return httpx.Response(200, json={"items": [{
            "full_name": "langchain-ai/langgraph", "description": "A stateful AI workflow framework",
            "html_url": "http://127.0.0.1/should-never-fetch",
        }]})

    result = run_search("github", handler)
    assert len(calls) == 1 and result.errors == []
    assert result.candidates[0].source_url == "https://github.com/langchain-ai/langgraph"
    assert result.candidates[0].coverage == "metadata"
    assert "README and code not read" in result.candidates[0].quality_note


def test_hn_search_preserves_discussion_identity_and_rejects_private_link():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "hn.algolia.com" and request.url.params["tags"] == "story"
        return httpx.Response(200, json={"hits": [{
            "objectID": "42", "title": "AI workflow", "story_text": "<p>An AI workflow discussion.</p>",
            "url": "http://127.0.0.1/private",
        }]})

    result = run_search("hn", handler)
    assert len(calls) == 1 and result.candidates[0].identity_url is None
    assert result.candidates[0].source_url == "https://news.ycombinator.com/item?id=42"
    assert result.candidates[0].content == "An AI workflow discussion."


def test_arxiv_adapter_reuses_collector_and_fetches_one_bounded_page():
    calls = []
    fixture = Path(__file__).parents[1] / "fixtures" / "arxiv_feed.xml"

    def handler(request):
        calls.append(request)
        assert request.url.host == "export.arxiv.org"
        assert request.url.params["max_results"] == "3" and request.url.params["start"] == "0"
        return httpx.Response(200, content=fixture.read_bytes(), headers={"Content-Type": "application/atom+xml"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ArxivSearch(client).search("AI state workflow", REQUEST)

    result = asyncio.run(run())
    assert len(calls) == 1 and result.candidates
    assert all(candidate.coverage == "abstract" for candidate in result.candidates)


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}), "github_unavailable"),
    (httpx.Response(429), "github_rate_limited"),
    (httpx.Response(200, text="<html>not JSON</html>"), "github_invalid_response"),
    (httpx.Response(200, content=b"x" * 262_145, headers={"Content-Type": "application/json"}), "github_response_too_large"),
])
def test_redirect_rate_limit_content_type_and_size_are_bounded(response, expected):
    calls = []

    def handler(request):
        calls.append(request)
        return response

    result = run_search("github", handler)
    assert result.errors == [expected] and len(calls) == 1


def test_timeout_does_not_expose_driver_details():
    def handler(request):
        raise httpx.ReadTimeout("secret-key-must-not-appear", request=request)

    result = run_search("github", handler)
    assert result.errors == ["github_timeout"]
    assert "secret-key" not in result.model_dump_json()


def test_provider_failure_does_not_discard_other_source_results():
    class Failing:
        async def search(self, query, request):
            raise RuntimeError("sensitive-details")

    class Available:
        async def search(self, query, request):
            return SearchOutcome(errors=["arxiv_timeout"])

    result = asyncio.run(ResearchRetriever([Failing(), Available()]).search("AI", REQUEST))
    assert result.errors == ["provider_unavailable", "arxiv_timeout"]
