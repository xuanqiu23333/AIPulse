"""Bounded Hacker News story collection from an explicitly selected list."""

import asyncio
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
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


_API = "https://hacker-news.firebaseio.com/v0"
_LISTS = frozenset({"topstories", "newstories"})
_OFFSET = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_BLOCK_TAGS = frozenset({"p", "br", "div", "li", "pre", "blockquote", "h1", "h2", "h3"})


class _PlainText(HTMLParser):
    """Keep only visible text; never retain HTML attributes or script text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._hidden += 1
        elif tag in _BLOCK_TAGS and not self._hidden:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK_TAGS and not self._hidden:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self._parts.append(data)

    def value(self) -> str:
        return " ".join("".join(self._parts).split())


def _plain_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return parser.value()


class HackerNewsCollector(BaseCollector):
    """Read topstories or newstories, then a bounded set of story items.

    A cursor is an offset into the *current* list response. Ranking can move
    between calls; persistent deduplication must use the HN item ID later.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_ids: int = 30,
        max_candidates: int = 20,
        max_requests: int = 31,
        concurrency: int = 3,
        max_response_bytes: int = 1_048_576,
        timeout: float = 10.0,
    ) -> None:
        if not 1 <= max_ids <= 500 or not 1 <= max_candidates <= 1000:
            raise ValueError("Story budgets are out of range")
        if not 2 <= max_requests <= 501 or not 1 <= concurrency <= 10:
            raise ValueError("Request or concurrency budget is out of range")
        if not 1024 <= max_response_bytes <= 5_242_880 or timeout <= 0:
            raise ValueError("Response size or timeout is out of range")
        self._client = client
        self._max_ids = max_ids
        self._max_candidates = max_candidates
        self._max_requests = max_requests
        self._concurrency = concurrency
        self._max_response_bytes = max_response_bytes
        self._timeout = timeout

    async def collect(
        self, source: CollectorSource, *, cursor: str | None = None
    ) -> CollectionResult:
        if source.provider != "hn" or source.external_id not in _LISTS:
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)
        if cursor is not None and (
            len(cursor) > 3 or not _OFFSET.fullmatch(cursor) or int(cursor) > 500
        ):
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE)
        offset = int(cursor) if cursor is not None else 0
        ids, error = await self._fetch_json(f"/{source.external_id}.json")
        if error is not None:
            return self._result(source, (), 0, error, str(offset))
        if not isinstance(ids, list) or len(ids) > 500:
            return self._result(source, (), 0, CollectorErrorCode.INVALID_RESPONSE, str(offset))
        if offset >= len(ids):
            return self._result(source, (), 0, None)

        position = offset
        fetched_count = 0
        requests = 1  # The list request is part of the HTTP budget.
        seen_ids: set[int] = set()
        candidates: list[NewsCandidate] = []
        item_error: CollectorErrorCode | None = None
        while (
            position < len(ids) and fetched_count < self._max_ids
            and len(candidates) < self._max_candidates and requests < self._max_requests
        ):
            capacity = min(
                self._concurrency,
                self._max_ids - fetched_count,
                self._max_candidates - len(candidates),
                self._max_requests - requests,
            )
            batch: list[int] = []
            while (
                len(batch) < capacity and position < len(ids)
                and fetched_count < self._max_ids
            ):
                raw_id = ids[position]
                position += 1
                fetched_count += 1
                if type(raw_id) is not int or raw_id <= 0:
                    item_error = item_error or CollectorErrorCode.INVALID_RESPONSE
                    continue
                if raw_id in seen_ids:
                    continue
                seen_ids.add(raw_id)
                batch.append(raw_id)
            if not batch:
                continue
            requests += len(batch)
            outcomes = await asyncio.gather(
                *(self._fetch_json(f"/item/{item_id}.json") for item_id in batch)
            )
            fetched_at = datetime.now(timezone.utc)
            for item_id, (item, fetch_error) in zip(batch, outcomes):
                if fetch_error is not None:
                    item_error = item_error or fetch_error
                    continue
                candidate, parse_error = self._candidate(item, item_id, fetched_at)
                if parse_error is not None:
                    item_error = item_error or parse_error
                if candidate is not None:
                    candidates.append(candidate)

        next_cursor = str(position) if position < len(ids) else None
        return self._result(source, candidates, fetched_count, item_error, next_cursor)

    async def _fetch_json(self, path: str) -> tuple[Any, CollectorErrorCode | None]:
        try:
            async with self._client.stream(
                "GET", f"{_API}{path}", timeout=self._timeout,
                headers={"Accept": "application/json"}, follow_redirects=False,
            ) as response:
                if response.status_code == 429:
                    return None, CollectorErrorCode.RATE_LIMIT
                if response.status_code != 200:
                    return None, CollectorErrorCode.UNAVAILABLE
                size_header = response.headers.get("content-length")
                if size_header and size_header.isdecimal() and int(size_header) > self._max_response_bytes:
                    return None, CollectorErrorCode.INVALID_RESPONSE
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        return None, CollectorErrorCode.INVALID_RESPONSE
                try:
                    return json.loads(bytes(body)), None
                except (UnicodeError, ValueError):
                    return None, CollectorErrorCode.INVALID_RESPONSE
        except httpx.TimeoutException:
            return None, CollectorErrorCode.TIMEOUT
        except httpx.RequestError:
            return None, CollectorErrorCode.UNAVAILABLE
        except Exception:
            # Never include exception text, response bodies, or request headers.
            return None, CollectorErrorCode.UNEXPECTED

    @staticmethod
    def _candidate(
        item: Any, requested_id: int, fetched_at: datetime
    ) -> tuple[NewsCandidate | None, CollectorErrorCode | None]:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] != requested_id:
            return None, CollectorErrorCode.INVALID_RESPONSE
        for flag in ("deleted", "dead"):
            if flag in item and type(item[flag]) is not bool:
                return None, CollectorErrorCode.INVALID_RESPONSE
        if item.get("deleted") is True or item.get("dead") is True:
            return None, None
        if item.get("type") != "story":
            if "type" not in item:
                return None, CollectorErrorCode.INVALID_RESPONSE
            return None, None
        if type(item.get("time")) is not int:
            return None, CollectorErrorCode.INVALID_RESPONSE
        try:
            published_at = datetime.fromtimestamp(item["time"], timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None, CollectorErrorCode.INVALID_RESPONSE
        if not isinstance(item.get("title"), str):
            return None, CollectorErrorCode.INVALID_RESPONSE
        raw_text = item.get("text")
        if raw_text is None:
            raw_text = ""
        if not isinstance(raw_text, str):
            return None, CollectorErrorCode.INVALID_RESPONSE
        score = item.get("score")
        descendants = item.get("descendants")
        author = item.get("by")
        if (
            (score is not None and (type(score) is not int or score < 0))
            or (descendants is not None and (type(descendants) is not int or descendants < 0))
            or (author is not None and (not isinstance(author, str) or len(author) > 100))
        ):
            return None, CollectorErrorCode.INVALID_RESPONSE
        discussion_url = f"https://news.ycombinator.com/item?id={requested_id}"
        metadata = {
            "score": score,
            "descendants": descendants,
            "author": author,
            "discussion_url": discussion_url,
            "item_type": "story",
        }
        external_url = item.get("url")
        malformed_url = external_url not in (None, "") and not isinstance(external_url, str)
        original_url = external_url if isinstance(external_url, str) and external_url else discussion_url
        try:
            fields = dict(
                provider="hn",
                external_id=str(requested_id),
                title=_plain_text(item["title"]),
                raw_excerpt=_plain_text(raw_text)[:4000],
                published_at=published_at,
                fetched_at=fetched_at,
                metadata=metadata,
            )
            try:
                candidate = NewsCandidate(original_url=original_url, **fields)
            except ValidationError:
                if original_url == discussion_url:
                    raise
                candidate = NewsCandidate(original_url=discussion_url, **fields)
                malformed_url = True
        except (ValueError, TypeError, ValidationError):
            return None, CollectorErrorCode.INVALID_RESPONSE
        return candidate, CollectorErrorCode.INVALID_RESPONSE if malformed_url else None

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
