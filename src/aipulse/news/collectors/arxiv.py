"""Bounded, serial collection of one explicitly configured arXiv query."""

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Awaitable, Callable
from xml.parsers import expat

import httpx
from pydantic import ValidationError

from aipulse.news.collectors.base import BaseCollector
from aipulse.news.types import (
    CollectionResult,
    CollectionStatus,
    CollectorErrorCode,
    CollectorSource,
    NewsCandidate,
)


_API = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_IDENTIFIER = re.compile(
    r"(?P<base>(?:[0-9]{4}\.[0-9]{4,5}|[A-Za-z][A-Za-z0-9.-]*/[0-9]{7}))"
    r"(?:v(?P<version>[1-9][0-9]*))?\Z"
)
_OFFSET = re.compile(r"(?:0|[1-9][0-9]*)\Z")


class ArxivRateLimiter:
    """Serialize requests and space their starts by at least three seconds.

    A collector creates one limiter by default. Pass the *same* instance to
    multiple collectors to coordinate their requests within one process.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_start: float | None = None

    @asynccontextmanager
    async def request_slot(self):
        async with self._lock:
            if self._last_start is not None:
                remaining = 3.0 - (self._clock() - self._last_start)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_start = self._clock()
            yield


class ArxivCollector(BaseCollector):
    """Fetch Atom pages for ``CollectorSource.external_id`` as search_query.

    The source identity must contain a nonempty query. The endpoint is fixed;
    untrusted redirects and response URLs are never requested. No automatic
    retries are made, so a failed page can be retried using ``next_cursor``.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        rate_limiter: ArxivRateLimiter | None = None,
        per_page: int = 30,
        max_pages: int = 3,
        max_items: int = 100,
        max_response_bytes: int = 1_048_576,
        timeout: float = 10.0,
        sort_by: str = "submittedDate",
        sort_order: str = "descending",
    ) -> None:
        if not 1 <= per_page <= 100 or not 1 <= max_pages <= 10 or not 1 <= max_items <= 1000:
            raise ValueError("Collection budgets are out of range")
        if not 1024 <= max_response_bytes <= 5_242_880 or timeout <= 0:
            raise ValueError("Response size or timeout is out of range")
        if sort_by not in {"submittedDate", "lastUpdatedDate", "relevance"}:
            raise ValueError("Unsupported arXiv sortBy")
        if sort_order not in {"ascending", "descending"}:
            raise ValueError("Unsupported arXiv sortOrder")
        self._client = client
        self._limiter = rate_limiter or ArxivRateLimiter()
        self._per_page = per_page
        self._max_pages = max_pages
        self._max_items = max_items
        self._max_response_bytes = max_response_bytes
        self._timeout = timeout
        self._sort_by = sort_by
        self._sort_order = sort_order

    async def collect(
        self, source: CollectorSource, *, cursor: str | None = None
    ) -> CollectionResult:
        query = source.external_id
        if (
            source.provider != "arxiv" or not isinstance(query, str)
            or not query.strip() or len(query) > 191
            or any(ord(char) < 32 for char in query)
        ):
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)
        if cursor is not None and (
            len(cursor) > 5 or not _OFFSET.fullmatch(cursor) or int(cursor) > 30_000
        ):
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)

        offset = int(cursor) if cursor is not None else 0
        candidates: list[NewsCandidate] = []
        seen_ids: set[str] = set()
        fetched_count = 0
        item_error = False
        for _ in range(self._max_pages):
            remaining = min(self._max_items - fetched_count, 30_000 - offset)
            if remaining <= 0:
                break
            requested = min(self._per_page, remaining)
            params = {
                "search_query": query.strip(),
                "start": offset,
                "max_results": requested,
                "sortBy": self._sort_by,
                "sortOrder": self._sort_order,
            }
            try:
                async with self._limiter.request_slot():
                    async with self._client.stream(
                        "GET", _API, params=params,
                        headers={"Accept": "application/atom+xml"},
                        timeout=self._timeout, follow_redirects=False,
                    ) as response:
                        if response.status_code == 429:
                            return self._result(source, candidates, fetched_count, CollectorErrorCode.RATE_LIMIT, str(offset))
                        if response.status_code != 200:
                            return self._result(source, candidates, fetched_count, CollectorErrorCode.UNAVAILABLE, str(offset))
                        size_header = response.headers.get("content-length")
                        if size_header and size_header.isdecimal() and int(size_header) > self._max_response_bytes:
                            return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(offset))
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self._max_response_bytes:
                                return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(offset))
            except httpx.TimeoutException:
                return self._result(source, candidates, fetched_count, CollectorErrorCode.TIMEOUT, str(offset))
            except httpx.RequestError:
                return self._result(source, candidates, fetched_count, CollectorErrorCode.UNAVAILABLE, str(offset))
            except Exception:
                return self._result(source, candidates, fetched_count, CollectorErrorCode.UNEXPECTED, str(offset))

            try:
                entries = self._parse_entries(bytes(body), requested)
            except (ValueError, ET.ParseError, expat.ExpatError):
                return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(offset))
            fetched_count += len(entries)
            fetched_at = datetime.now(timezone.utc)
            for entry in entries:
                try:
                    candidate = self._candidate(entry, fetched_at)
                except (ValueError, TypeError, ValidationError):
                    item_error = True
                    continue
                if candidate.external_id not in seen_ids:
                    seen_ids.add(candidate.external_id)
                    candidates.append(candidate)
            offset += len(entries)
            if len(entries) < requested:
                return self._result(
                    source, candidates, fetched_count,
                    CollectorErrorCode.INVALID_RESPONSE if item_error else None,
                )

        return self._result(
            source, candidates, fetched_count,
            CollectorErrorCode.INVALID_RESPONSE if item_error else None,
            str(offset) if offset < 30_000 else None,
        )

    @staticmethod
    def _parse_entries(body: bytes, requested: int) -> list[ET.Element]:
        # Expat sees declarations in any supported XML encoding, including
        # UTF-16. Reject them before ElementTree builds an object graph.
        probe = expat.ParserCreate()

        def reject(*_args):
            raise ValueError("DTD and entities are forbidden")

        probe.StartDoctypeDeclHandler = reject
        probe.EntityDeclHandler = reject
        probe.ExternalEntityRefHandler = reject
        probe.Parse(body, True)
        root = ET.fromstring(body)
        if root.tag != f"{_ATOM}feed":
            raise ValueError("Not an Atom feed")
        entries = root.findall(f"{_ATOM}entry")
        if len(entries) > requested:
            raise ValueError("More entries than requested")
        return entries

    @staticmethod
    def _text(entry: ET.Element, name: str) -> str:
        node = entry.find(f"{_ATOM}{name}")
        if node is None:
            raise ValueError("Missing required Atom field")
        return " ".join("".join(node.itertext()).split())

    @staticmethod
    def _timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timezone is required")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _arxiv_url(value: str, kind: str, paper_id: str | None = None) -> tuple[str, str, int | None]:
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            raise ValueError("Invalid arXiv URL") from None
        if (
            url.scheme not in {"http", "https"} or url.host != "arxiv.org"
            or url.username or url.password or url.query or url.fragment
            or url.port not in (None, 80, 443)
            or not url.path.startswith(f"/{kind}/")
        ):
            raise ValueError("Invalid arXiv URL")
        identifier = url.path[len(kind) + 2:]
        match = _IDENTIFIER.fullmatch(identifier)
        if match is None or (paper_id is not None and identifier != paper_id):
            raise ValueError("Invalid arXiv identifier")
        version = int(match.group("version")) if match.group("version") else None
        return match.group("base"), identifier, version

    @classmethod
    def _candidate(cls, entry: ET.Element, fetched_at: datetime) -> NewsCandidate:
        entry_id = cls._text(entry, "id")
        base_id, full_id, version = cls._arxiv_url(entry_id, "abs")
        original_url = entry_id
        pdf_url = None
        for link in entry.findall(f"{_ATOM}link"):
            href = link.get("href", "")
            if link.get("rel") == "alternate":
                try:
                    cls._arxiv_url(href, "abs", full_id)
                    original_url = href
                except ValueError:
                    pass
            elif link.get("rel") == "related" and link.get("title") == "pdf":
                try:
                    cls._arxiv_url(href, "pdf", full_id)
                    pdf_url = href
                except ValueError:
                    pass

        metadata: dict[str, object] = {"version": version, "authors": [], "categories": []}
        updated = entry.findtext(f"{_ATOM}updated")
        if updated:
            metadata["updated_at"] = cls._timestamp(updated.strip()).isoformat()
        authors = [
            " ".join(name.text.split())[:200]
            for author in entry.findall(f"{_ATOM}author")[:20]
            if (name := author.find(f"{_ATOM}name")) is not None and name.text
        ]
        metadata["authors"] = authors
        metadata["categories"] = [
            category.get("term", "")[:100]
            for category in entry.findall(f"{_ATOM}category")[:20]
            if category.get("term")
        ]
        primary = entry.find(f"{_ARXIV}primary_category")
        if primary is not None and primary.get("term"):
            metadata["primary_category"] = primary.get("term")[:100]
        doi = entry.findtext(f"{_ARXIV}doi")
        if doi:
            metadata["doi"] = doi.strip()[:500]
        if pdf_url is not None:
            metadata["pdf_url"] = pdf_url

        return NewsCandidate(
            provider="arxiv",
            external_id=base_id,
            original_url=original_url,
            title=cls._text(entry, "title"),
            raw_excerpt=cls._text(entry, "summary")[:4000],
            published_at=cls._timestamp(cls._text(entry, "published")),
            fetched_at=fetched_at,
            metadata=metadata,
        )

    @staticmethod
    def _result(
        source: CollectorSource,
        candidates: tuple[NewsCandidate, ...] | list[NewsCandidate],
        fetched_count: int,
        error: CollectorErrorCode | None,
        cursor: str | None = None,
    ) -> CollectionResult:
        status = (
            CollectionStatus.SUCCESS if error is None
            else CollectionStatus.PARTIAL if candidates
            else CollectionStatus.FAILED
        )
        return CollectionResult(
            source_id=source.source_id,
            candidates=tuple(candidates),
            status=status,
            fetched_count=fetched_count,
            next_cursor=cursor,
            error_code=error,
        )
