"""Bounded GitHub Releases collection for one explicitly configured repository."""

import json
import re
from datetime import datetime, timezone
from typing import Any

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


_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")
_PAGE = re.compile(r"[1-9][0-9]*\Z")


class GitHubReleaseCollector(BaseCollector):
    """Read GitHub's public Releases endpoint without following arbitrary links.

    One instance is configured for one source's prerelease policy. ``cursor`` is
    a decimal page number, never a URL. Each call has finite page, record and
    response byte budgets; any continuation is returned as ``next_cursor``.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        include_prereleases: bool = False,
        token: str | None = None,
        per_page: int = 30,
        max_pages: int = 3,
        max_items: int = 100,
        max_response_bytes: int = 1_048_576,
        timeout: float = 10.0,
    ) -> None:
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        if not 1 <= max_pages <= 10 or not 1 <= max_items <= 1000:
            raise ValueError("Collection budgets are out of range")
        if not 1024 <= max_response_bytes <= 5_242_880 or timeout <= 0:
            raise ValueError("Response size or timeout is out of range")
        self._client = client
        self._include_prereleases = include_prereleases
        self._token = token
        self._per_page = per_page
        self._max_pages = max_pages
        self._max_items = max_items
        self._max_response_bytes = max_response_bytes
        self._timeout = timeout

    async def collect(
        self, source: CollectorSource, *, cursor: str | None = None
    ) -> CollectionResult:
        if source.provider != "github" or not _REPOSITORY.fullmatch(source.external_id):
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)
        if cursor is not None and (
            len(cursor) > 7 or not _PAGE.fullmatch(cursor) or int(cursor) > 1_000_000
        ):
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)

        owner, repo = source.external_id.split("/")
        if repo in {".", ".."}:
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)
        endpoint = f"https://api.github.com/repos/{owner}/{repo}/releases"
        page = int(cursor) if cursor is not None else 1
        candidates: list[NewsCandidate] = []
        fetched_count = 0
        item_error = False
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        for _ in range(self._max_pages):
            remaining = self._max_items - fetched_count
            if remaining <= 0:
                break
            requested = min(self._per_page, remaining)
            try:
                async with self._client.stream(
                    "GET", endpoint, params={"per_page": requested, "page": page},
                    headers=headers, timeout=self._timeout, follow_redirects=False,
                ) as response:
                    if response.status_code in (403, 429):
                        return self._result(source, candidates, fetched_count, CollectorErrorCode.RATE_LIMIT, str(page))
                    if response.status_code != 200:
                        return self._result(source, candidates, fetched_count, CollectorErrorCode.UNAVAILABLE, str(page))
                    if self._content_length_exceeds(response):
                        return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(page))
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_response_bytes:
                            return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(page))
                    try:
                        releases = json.loads(body)
                    except (UnicodeError, ValueError):
                        return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(page))
                    if not isinstance(releases, list) or len(releases) > requested:
                        return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE, str(page))
            except httpx.TimeoutException:
                return self._result(source, candidates, fetched_count, CollectorErrorCode.TIMEOUT, str(page))
            except httpx.RequestError:
                return self._result(source, candidates, fetched_count, CollectorErrorCode.UNAVAILABLE, str(page))
            except Exception:
                # Never echo transport exceptions, response bodies, or auth headers.
                return self._result(source, candidates, fetched_count, CollectorErrorCode.UNEXPECTED, str(page))

            fetched_count += len(releases)
            fetched_at = datetime.now(timezone.utc)
            for release in releases:
                if not isinstance(release, dict):
                    item_error = True
                    continue
                if type(release.get("draft")) is not bool or type(release.get("prerelease")) is not bool:
                    item_error = True
                    continue
                if release["draft"] or (release["prerelease"] and not self._include_prereleases):
                    continue
                try:
                    candidates.append(self._candidate(release, fetched_at))
                except (ValidationError, ValueError, TypeError, KeyError):
                    item_error = True

            try:
                next_page = self._next_page(response, endpoint, page, requested)
            except (ValueError, KeyError):
                return self._result(source, candidates, fetched_count, CollectorErrorCode.INVALID_RESPONSE)

            if next_page is None:
                return self._result(
                    source, candidates, fetched_count,
                    CollectorErrorCode.INVALID_RESPONSE if item_error else None,
                )
            page = next_page

        return self._result(
            source, candidates, fetched_count,
            CollectorErrorCode.INVALID_RESPONSE if item_error else None,
            str(page),
        )

    def _content_length_exceeds(self, response: httpx.Response) -> bool:
        value = response.headers.get("content-length")
        return value is not None and value.isdecimal() and int(value) > self._max_response_bytes

    @staticmethod
    def _candidate(release: dict[str, Any], fetched_at: datetime) -> NewsCandidate:
        release_id = release["id"]
        if type(release_id) is not int or release_id <= 0:
            raise ValueError("Invalid release identity")
        name = release.get("name")
        title = name.strip() if isinstance(name, str) else ""
        if not title:
            title = release["tag_name"]
        body = release.get("body")
        if body is None:
            body = ""
        if not isinstance(body, str):
            raise ValueError("Invalid release body")
        return NewsCandidate(
            provider="github",
            external_id=str(release_id),
            original_url=release["html_url"],
            title=title,
            raw_excerpt=body[:4000],
            published_at=release["published_at"],
            fetched_at=fetched_at,
            metadata={"tag_name": release["tag_name"], "prerelease": release["prerelease"]},
        )

    @staticmethod
    def _next_page(response: httpx.Response, endpoint: str, page: int, requested: int) -> int | None:
        link = response.links.get("next")
        if link is None:
            return None
        url = httpx.URL(link["url"])
        link_endpoint = str(url).split("?", 1)[0]
        canonical_repository_endpoint = re.fullmatch(
            r"https://api\.github\.com/repositories/[1-9][0-9]{0,19}/releases",
            link_endpoint,
        )
        if (
            url.scheme != "https" or url.host != "api.github.com"
            or url.port not in (None, 443) or url.username or url.password or url.fragment
            or (link_endpoint != endpoint and canonical_repository_endpoint is None)
        ):
            raise ValueError("Untrusted pagination origin")
        # GitHub may canonicalize /repos/OWNER/REPO to /repositories/ID in Link.
        # Link supplies only a page number; collect() still requests endpoint.
        pairs = list(url.params.multi_items())
        if len(pairs) != 2 or {key for key, _ in pairs} != {"page", "per_page"}:
            raise ValueError("Invalid pagination parameters")
        next_raw = url.params["page"]
        if len(next_raw) > 7 or not _PAGE.fullmatch(next_raw) or int(next_raw) != page + 1:
            raise ValueError("Invalid next page")
        if page + 1 > 1_000_000:
            raise ValueError("Page limit reached")
        if url.params["per_page"] != str(requested):
            raise ValueError("Unexpected page size")
        return page + 1

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
