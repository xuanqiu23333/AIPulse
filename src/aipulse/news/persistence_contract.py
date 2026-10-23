"""Pure field-mapping rules for a later MySQL news repository.

These functions perform no SQL and make no claim about concurrent UPSERT safety.
"""

import json
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

from aipulse.news.normalize import NormalizedNewsItem
from aipulse.news.types import NewsCandidate


_PROVIDER_METADATA_KEYS = {
    "github": frozenset({"tag_name", "prerelease"}),
    "arxiv": frozenset({
        "version", "authors", "categories", "updated_at",
        "primary_category", "doi", "pdf_url",
    }),
    "hn": frozenset({"score", "descendants", "author", "discussion_url", "item_type"}),
}
_TEXT_LIMITS = {
    "tag_name": 191, "updated_at": 64, "primary_category": 100,
    "doi": 500, "pdf_url": 2048, "author": 100,
    "discussion_url": 2048, "item_type": 24,
}
_LIST_LIMITS = {"authors": (20, 200), "categories": (20, 100)}


def metadata_for_storage(candidate: NewsCandidate) -> dict:
    """Keep only bounded fields emitted by the three reviewed collectors.

    Unknown fields, including credentials, headers and raw responses, are
    discarded. An empty object means no approved metadata, never SQL NULL.
    """

    allowed = _PROVIDER_METADATA_KEYS.get(candidate.provider, frozenset())
    result = {}
    for key in sorted(allowed):
        if key not in candidate.metadata:
            continue
        value = candidate.metadata[key]
        if key in _TEXT_LIMITS:
            valid = type(value) is str and len(value) <= _TEXT_LIMITS[key]
        elif key in _LIST_LIMITS:
            max_count, max_text = _LIST_LIMITS[key]
            valid = (
                type(value) is list and len(value) <= max_count
                and all(type(part) is str and len(part) <= max_text for part in value)
            )
        elif key == "prerelease":
            valid = type(value) is bool
        else:
            valid = value is None or (type(value) is int and value >= 0)
        if valid:
            result[key] = deepcopy(value)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 16_384:
        raise ValueError("Source metadata exceeds size limit")
    return result


def to_mysql_utc_datetime(value: datetime) -> datetime:
    """Convert an aware source timestamp to MySQL's naive-UTC DATETIME form."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware source timestamp is required")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def canonical_url_hash(item: NormalizedNewsItem) -> str:
    """Hash the existing normalizer's URL, not the raw URL or event identity."""

    return sha256(item.canonical_url.encode("utf-8")).hexdigest()


def should_update_source_fields(stored_fetched_at: datetime, incoming_fetched_at: datetime) -> bool:
    """Only a strictly later observation may replace persisted source fields.

    ``stored_fetched_at`` is a MySQL naive-UTC DATETIME. This predicate does
    not decide identity, preserve first source_id, or implement an UPSERT.
    """

    if not isinstance(stored_fetched_at, datetime) or stored_fetched_at.tzinfo is not None:
        raise ValueError("Stored fetched_at must be naive UTC")
    return to_mysql_utc_datetime(incoming_fetched_at) > stored_fetched_at
