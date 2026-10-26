"""Deterministic, offline news-event grouping from normalized original items."""

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from aipulse.news.normalize import DeduplicatedNewsItem


@dataclass(frozen=True)
class EventMembership:
    """Trace one original news identity and all batch sources to its event."""

    item_key: tuple[str, str]
    source_ids: tuple[int, ...]
    event_key: str
    reason: str
    needs_review: bool


@dataclass(frozen=True)
class EventGroup:
    """A canonical identity and the original items that share it."""

    event_key: str
    canonical_identity: str
    members: tuple[EventMembership, ...]
    needs_review: bool


def merge_events(items: Iterable[DeduplicatedNewsItem]) -> tuple[EventGroup, ...]:
    """Use strong identities or identical canonical URLs; never match titles."""

    grouped: dict[str, list[DeduplicatedNewsItem]] = {}
    for item in items:
        grouped.setdefault(item.canonical_identity, []).append(item)
    result = []
    for identity in sorted(grouped):
        event_key = sha256(f"aipulse:event:v1:{identity}".encode("utf-8")).hexdigest()
        members = tuple(
            EventMembership(
                item_key=item.item_key, source_ids=item.source_ids,
                event_key=event_key, reason=item.identity_reason,
                needs_review=False,
            )
            for item in sorted(grouped[identity], key=lambda item: item.item_key)
        )
        result.append(EventGroup(
            event_key=event_key, canonical_identity=identity,
            members=members, needs_review=False,
        ))
    return tuple(result)
