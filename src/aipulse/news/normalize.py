"""Pure normalization and batch deduplication of validated news candidates.

The batch result describes a proposed write; it never queries or modifies a DB.
An existing row's original source_id must be preserved by a future repository.
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

from aipulse.news.types import NewsCandidate


_HTTP_URL = TypeAdapter(AnyHttpUrl)
_ARXIV_ID = re.compile(
    r"(?P<base>(?:[0-9]{4}\.[0-9]{4,5}|[A-Za-z][A-Za-z0-9.-]*/[0-9]{7}))"
    r"(?:v[1-9][0-9]*)?\Z"
)
_GITHUB_RELEASE = re.compile(r"/([^/]+)/([^/]+)/releases/tag/([^/?#]+)\Z")
_TRACKING = frozenset({"fbclid", "gclid"})


@dataclass(frozen=True)
class NormalizedNewsItem:
    """A single source hit with stable original and canonical identities."""

    candidate: NewsCandidate
    source_id: int
    canonical_url: str
    content_hash: str
    canonical_identity: str
    identity_reason: str

    @property
    def provider(self) -> str:
        return self.candidate.provider

    @property
    def external_id(self) -> str:
        return self.candidate.external_id

    @property
    def item_key(self) -> tuple[str, str]:
        return self.provider, self.external_id

    @property
    def raw_excerpt(self) -> str:
        return self.candidate.raw_excerpt

    @property
    def published_at(self):
        return self.candidate.published_at

    @property
    def fetched_at(self):
        return self.candidate.fetched_at


@dataclass(frozen=True)
class DeduplicatedNewsItem(NormalizedNewsItem):
    """Latest observed content with the earliest source hit in this batch."""

    source_ids: tuple[int, ...]
    has_content_variants: bool


def normalize_url(url: str | AnyHttpUrl) -> str:
    """Normalize syntax only; never resolve DNS, redirects, or path semantics.

    Business query parameters and their order, including duplicate keys, stay
    intact. A trailing slash outside the root path is significant.
    """

    try:
        parsed_url = _HTTP_URL.validate_python(str(url))
    except ValidationError:
        raise ValueError("Invalid HTTP URL") from None
    if parsed_url.username or parsed_url.password or len(str(parsed_url)) > 2048:
        raise ValueError("Invalid HTTP URL")
    split = urlsplit(str(parsed_url))
    if split.scheme not in {"http", "https"} or split.hostname is None:
        raise ValueError("Invalid HTTP URL")

    host = split.hostname.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    port = split.port
    netloc = host if port is None or (split.scheme, port) in {
        ("http", 80), ("https", 443)
    } else f"{host}:{port}"
    query = "&".join(
        component for component in split.query.split("&")
        if component and not _is_tracking_parameter(component)
    )
    return urlunsplit((split.scheme.lower(), netloc, split.path or "/", query, ""))


def _is_tracking_parameter(component: str) -> bool:
    key = unquote_plus(component.partition("=")[0]).lower()
    return key.startswith("utm_") or key in _TRACKING


def _arxiv_base(value: str) -> str | None:
    match = _ARXIV_ID.fullmatch(value)
    return match.group("base").lower() if match else None


def _canonical_identity(candidate: NewsCandidate, canonical_url: str) -> tuple[str, str]:
    url = urlsplit(canonical_url)
    if candidate.provider == "arxiv":
        base = _arxiv_base(candidate.external_id)
        if base:
            return f"arxiv:{base}", "arxiv_id"

    if url.hostname in {"arxiv.org", "www.arxiv.org"}:
        path = unquote(url.path)
        if path.startswith(("/abs/", "/pdf/")):
            identifier = path[5:]
            if identifier.endswith(".pdf"):
                identifier = identifier[:-4]
            base = _arxiv_base(identifier)
            if base:
                return f"arxiv:{base}", "arxiv_id"

    if url.hostname == "github.com":
        match = _GITHUB_RELEASE.fullmatch(url.path)
        if match:
            owner, repo, tag = match.groups()
            if owner not in {".", ".."} and repo not in {".", ".."}:
                return (
                    f"github-release:{owner.lower()}/{repo.lower()}:{unquote(tag)}",
                    "github_release",
                )

    if candidate.provider == "hn" and url.hostname == "news.ycombinator.com":
        if url.path == "/item" and any(
            part == f"id={candidate.external_id}" for part in url.query.split("&")
        ):
            return f"hn:{candidate.external_id}", "hn_story"

    return f"url:{canonical_url}", "canonical_url"


def normalize_candidate(candidate: NewsCandidate, source_id: int) -> NormalizedNewsItem:
    """Preserve the collector's stable ID and UTC dates; compute derived keys."""

    if not isinstance(candidate, NewsCandidate) or type(source_id) is not int or source_id <= 0:
        raise ValueError("Valid candidate and positive source_id required")
    canonical_url = normalize_url(candidate.original_url)
    identity, reason = _canonical_identity(candidate, canonical_url)
    payload = json.dumps(
        ["v1", candidate.title, candidate.raw_excerpt],
        ensure_ascii=False, separators=(",", ":"),
    )
    return NormalizedNewsItem(
        candidate=candidate, source_id=source_id, canonical_url=canonical_url,
        content_hash=sha256(payload.encode("utf-8")).hexdigest(),
        canonical_identity=identity, identity_reason=reason,
    )


def _winner_key(item: NormalizedNewsItem) -> tuple:
    # The lexical fallback makes equal fetched_at outcomes independent of input order.
    candidate_json = json.dumps(
        item.candidate.model_dump(mode="json"), sort_keys=True,
        ensure_ascii=False, separators=(",", ":"),
    )
    return item.fetched_at, item.content_hash, item.canonical_url, candidate_json


def deduplicate_batch(items: Iterable[NormalizedNewsItem]) -> tuple[DeduplicatedNewsItem, ...]:
    """One logical row per (provider, external_id), sorted for reproducibility.

    The earliest fetched_at determines the first source in this batch. Exact
    timestamp ties use the lowest source_id; the latest observation supplies
    mutable content. A persisted source_id needs repository-level protection.
    """

    grouped: dict[tuple[str, str], list[NormalizedNewsItem]] = {}
    for item in items:
        grouped.setdefault(item.item_key, []).append(item)
    result = []
    for key in sorted(grouped):
        hits = grouped[key]
        winner = max(hits, key=_winner_key)
        first = min(hits, key=lambda hit: (hit.fetched_at, hit.source_id))
        result.append(DeduplicatedNewsItem(
            candidate=winner.candidate, source_id=first.source_id,
            canonical_url=winner.canonical_url, content_hash=winner.content_hash,
            canonical_identity=winner.canonical_identity,
            identity_reason=winner.identity_reason,
            source_ids=tuple(sorted({hit.source_id for hit in hits})),
            has_content_variants=len({hit.content_hash for hit in hits}) > 1,
        ))
    return tuple(result)


def content_changed(previous_hash: str, item: NormalizedNewsItem) -> bool:
    """Compare a persisted hash to the newest observed content hash."""

    return previous_hash != item.content_hash
