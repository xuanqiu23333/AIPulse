"""Fixed-host public API adapters; discovery snippets are never called full text."""

import asyncio
import json
import re
from datetime import datetime
from typing import Awaitable, Callable, Protocol

import httpx
from pydantic import Field, ValidationError

from aipulse.news.collectors.arxiv import ArxivCollector, ArxivRateLimiter
from aipulse.news.collectors.hn import _plain_text
from aipulse.news.types import CollectorSource
from aipulse.research.state import Boundary, EvidenceCandidate, ResearchRequest, RESULTS_PER_SOURCE


class SearchOutcome(Boundary):
    candidates: list[EvidenceCandidate] = Field(default_factory=list, max_length=12)
    errors: list[str] = Field(default_factory=list, max_length=8)


class SearchProvider(Protocol):
    async def search(self, query: str, request: ResearchRequest) -> SearchOutcome: ...


def search_terms(query: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,30}|[\u4e00-\u9fff]{2,12}", query)[:6]


def _published(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class LocalNewsSearch:
    def __init__(self, search: Callable[[str, int | None], Awaitable[list[EvidenceCandidate]]]):
        self._search = search

    async def search(self, query: str, request: ResearchRequest) -> SearchOutcome:
        try:
            async with asyncio.timeout(8):
                return SearchOutcome(candidates=(await self._search(query, request.event_id))[:RESULTS_PER_SOURCE])
        except TimeoutError:
            return SearchOutcome(errors=["local_timeout"])
        except Exception:
            return SearchOutcome(errors=["local_unavailable"])


class ArxivSearch:
    def __init__(self, client: httpx.AsyncClient):
        self._collector = ArxivCollector(
            client, rate_limiter=ArxivRateLimiter(), per_page=RESULTS_PER_SOURCE,
            max_pages=1, max_items=RESULTS_PER_SOURCE, timeout=10,
            sort_by="relevance", max_response_bytes=262_144,
        )

    async def search(self, query: str, request: ResearchRequest) -> SearchOutcome:
        terms = search_terms(query)[:4]
        if not terms:
            return SearchOutcome(errors=["arxiv_invalid_query"])
        api_query = " AND ".join(f'all:"{term[:20]}"' for term in terms)
        try:
            async with asyncio.timeout(15):
                result = await self._collector.collect(CollectorSource(
                    source_id=1, provider="arxiv", external_id=api_query,
                ))
            candidates = [EvidenceCandidate(
                source_type="arxiv", source_id=item.external_id,
                source_url=str(item.original_url), title=item.title,
                content=(item.raw_excerpt or "")[:2000], coverage="abstract",
                quality_note="arXiv API abstract; preprint, not independently verified or full-text reviewed.",
                published_at=item.published_at,
            ) for item in result.candidates[:RESULTS_PER_SOURCE]]
            errors = [f"arxiv_{result.error_code.value}"] if result.error_code else []
            return SearchOutcome(candidates=candidates, errors=errors)
        except TimeoutError:
            return SearchOutcome(errors=["arxiv_timeout"])
        except Exception:
            return SearchOutcome(errors=["arxiv_unavailable"])


_ENDPOINTS = {
    "github": "https://api.github.com/search/repositories",
    "hn": "https://hn.algolia.com/api/v1/search",
}


class PublicAPISearch:
    """No caller-controlled endpoint, pagination, redirect or discovered-URL fetch."""

    def __init__(self, client: httpx.AsyncClient, provider: str, *, github_token: str | None = None):
        if provider not in _ENDPOINTS:
            raise ValueError("Unsupported research provider")
        self._client = client
        self._provider = provider
        self._github_token = github_token
        # Adapters live for one research task. Do not probe a limited source on
        # later queries in that task, or share its circuit across future tasks.
        self._rate_limited = False

    @staticmethod
    async def _body(response):
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > 262_144:
                return None
        return body

    async def _get_json(self, params: dict):
        headers = {"Accept": "application/json", "User-Agent": "AIPulse-local-research"}
        if self._provider == "github":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            if self._github_token:
                headers["Authorization"] = "Bearer " + self._github_token
        async with self._client.stream(
            "GET", _ENDPOINTS[self._provider], params=params, headers=headers,
            timeout=10, follow_redirects=False,
        ) as response:
            if response.status_code == 429 or (response.status_code == 403 and (
                response.headers.get("x-ratelimit-remaining") == "0"
                or response.headers.get("retry-after") is not None
            )):
                self._rate_limited = True
                return None, "rate_limited"
            if response.status_code == 403:
                # A permission/authentication 403 is not itself a rate limit.
                body = await self._body(response)
                try:
                    value = json.loads(body) if body is not None else None
                    message = value.get("message", "") if isinstance(value, dict) else ""
                except (ValueError, UnicodeError, TypeError):
                    message = ""
                if isinstance(message, str) and any(marker in message.casefold() for marker in (
                    "rate limit", "secondary rate", "abuse detection",
                )):
                    self._rate_limited = True
                    return None, "rate_limited"
                return None, "unavailable"
            if response.status_code != 200:
                return None, "unavailable"
            if "application/json" not in response.headers.get("content-type", "").casefold():
                return None, "invalid_response"
            body = await self._body(response)
            if body is None:
                return None, "response_too_large"
        try:
            return json.loads(body), None
        except (ValueError, UnicodeError):
            return None, "invalid_response"

    async def search(self, query: str, request: ResearchRequest) -> SearchOutcome:
        if self._rate_limited:
            return SearchOutcome(errors=[f"{self._provider}_rate_limited"])
        safe_query = " ".join(search_terms(query))[:160]
        if not safe_query:
            return SearchOutcome(errors=[f"{self._provider}_invalid_query"])
        params = ({"q": safe_query, "per_page": RESULTS_PER_SOURCE, "page": 1}
                  if self._provider == "github" else
                  {"query": safe_query, "tags": "story", "hitsPerPage": RESULTS_PER_SOURCE, "page": 0})
        try:
            async with asyncio.timeout(12):
                data, error = await self._get_json(params)
            if error:
                return SearchOutcome(errors=[f"{self._provider}_{error}"])
            if not isinstance(data, dict):
                return SearchOutcome(errors=[f"{self._provider}_invalid_response"])
            rows = data.get("items" if self._provider == "github" else "hits")
            if not isinstance(rows, list):
                return SearchOutcome(errors=[f"{self._provider}_invalid_response"])
            candidates = []
            invalid = False
            for row in rows[:RESULTS_PER_SOURCE]:
                try:
                    candidate = self._github(row) if self._provider == "github" else self._hn(row)
                    candidates.append(candidate)
                except (ValueError, TypeError, KeyError, ValidationError):
                    invalid = True
            return SearchOutcome(
                candidates=candidates,
                errors=([f"{self._provider}_invalid_item"] if invalid else []) +
                       (["github_incomplete_results"] if self._provider == "github" and data.get("incomplete_results") is True else []),
            )
        except (TimeoutError, httpx.TimeoutException):
            return SearchOutcome(errors=[f"{self._provider}_timeout"])
        except Exception:
            return SearchOutcome(errors=[f"{self._provider}_unavailable"])

    @staticmethod
    def _github(row) -> EvidenceCandidate:
        name = row["full_name"]
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}/[A-Za-z0-9_.-]{1,100}", name) is None:
            raise ValueError("Invalid repository identity")
        description = row.get("description") or ""
        if not isinstance(description, str):
            raise ValueError("Invalid repository description")
        return EvidenceCandidate(
            source_type="github", source_id=name,
            source_url="https://github.com/" + name, title=name,
            content=description[:2000], coverage="metadata" if description else "title_only",
            quality_note="GitHub repository description returned by Search API; self-reported metadata, README and code not read.",
        )

    @staticmethod
    def _hn(row) -> EvidenceCandidate:
        identity = row["objectID"]
        title = row["title"]
        if (not isinstance(identity, str) or not identity.isdecimal() or len(identity) > 20
                or not isinstance(title, str) or not title.strip()):
            raise ValueError("Invalid HN story")
        text = row.get("story_text") or ""
        if not isinstance(text, str):
            raise ValueError("Invalid HN excerpt")
        excerpt = _plain_text(text)[:2000]
        target = row.get("url")
        try:
            return EvidenceCandidate(
                source_type="hn", source_id=identity,
                source_url=f"https://news.ycombinator.com/item?id={identity}",
                title=title[:512], content=excerpt,
                coverage="excerpt" if excerpt else "title_only",
                quality_note="HN Search story text or title; community material, linked article not fetched.",
                published_at=_published(row.get("created_at")),
                identity_url=target if isinstance(target, str) else None,
            )
        except ValidationError:
            # A rejected discovered URL is never fetched or returned as a source.
            return EvidenceCandidate(
                source_type="hn", source_id=identity,
                source_url=f"https://news.ycombinator.com/item?id={identity}",
                title=title[:512], content=excerpt,
                coverage="excerpt" if excerpt else "title_only",
                quality_note="HN material; invalid linked URL discarded, no linked page fetched.",
            )


class ResearchRetriever:
    def __init__(self, providers: list[SearchProvider]):
        if not 1 <= len(providers) <= 4:
            raise ValueError("Research needs one to four bounded providers")
        self._providers = providers

    async def search(self, query: str, request: ResearchRequest) -> SearchOutcome:
        outcomes = await asyncio.gather(
            *(provider.search(query, request) for provider in self._providers),
            return_exceptions=True,
        )
        candidates, errors = [], []
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                errors.append("provider_unavailable")
            else:
                candidates.extend(outcome.candidates[:RESULTS_PER_SOURCE])
                errors.extend(outcome.errors)
        return SearchOutcome(candidates=candidates[:12], errors=list(dict.fromkeys(errors))[:8])
