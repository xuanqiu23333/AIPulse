"""Compile MySQL statements offline; these tests never create an Engine."""

import re
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module

import pytest
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import UnsupportedCompilationError

from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.persistence_contract import canonical_url_hash
from aipulse.news.types import NewsCandidate


NOW = datetime(2026, 10, 15, 8, 30, 1, 123456, tzinfo=timezone.utc)


def repository():
    try:
        return import_module("aipulse.news.repository")
    except ModuleNotFoundError as exc:
        pytest.fail(f"News repository is missing: {exc.name}", pytrace=False)


def item(*, provider="github", external_id="701", source_id=3, url=None, metadata=None):
    candidate = NewsCandidate(
        provider=provider, external_id=external_id,
        original_url=url or "https://github.com/acme/agent/releases/tag/v1?utm_source=hn",
        title="Release v1", raw_excerpt="The notes", published_at=NOW,
        fetched_at=NOW, metadata=metadata or {"tag_name": "v1", "prerelease": False},
    )
    return deduplicate_batch([normalize_candidate(candidate, source_id)])[0]


def sql(statement):
    return str(statement.compile(dialect=mysql.dialect()))


def test_news_item_insert_maps_first_source_event_and_safe_candidate_fields():
    current = item(metadata={
        "tag_name": "v1", "prerelease": False, "authorization": "Bearer secret",
        "raw_response": {"secret": "hidden"},
    })
    statement = repository().build_item_upsert(current, event_id=41)
    params = statement.compile(dialect=mysql.dialect()).params
    assert params["source_id"] == 3
    assert params["event_id"] == 41
    assert params["provider"] == "github" and params["external_id"] == "701"
    assert params["metadata_json"] == {"tag_name": "v1", "prerelease": False}
    assert params["canonical_url_hash"] == canonical_url_hash(current)
    assert params["url"] == str(current.candidate.original_url)
    assert params["content"] == "The notes"
    assert params["fetched_at"] == datetime(2026, 10, 15, 8, 30, 1, 123456)
    assert params["content_hash"] == current.content_hash


def test_item_upsert_uses_mysql_duplicate_key_and_preserves_first_identity_fields():
    compiled = sql(repository().build_item_upsert(item(), event_id=41))
    assert "ON DUPLICATE KEY UPDATE" in compiled
    update = compiled.split("ON DUPLICATE KEY UPDATE", 1)[1]
    for protected in ("source_id", "created_at", "provider", "external_id"):
        assert not re.search(rf"(?:^|,)\s*{protected}\s*=", update)
    assert "LAST_INSERT_ID(news_item.id)" in update
    assert "content_hash" in update and "metadata_json" in update


def test_every_mutable_source_field_uses_strict_newer_guard_before_fetched_at_assignment():
    update = sql(repository().build_item_upsert(item(), event_id=41)).split(
        "ON DUPLICATE KEY UPDATE", 1
    )[1]
    for field in (
        "title", "url", "content", "published_at", "content_hash",
        "metadata_json", "canonical_url_hash", "fetched_at",
    ):
        match = re.search(rf"(?:^|,)\s*{field}\s*=", update)
        assert match, f"{field} must have an explicit UPSERT assignment"
        assignment = update[match.end():].split(", ", 1)[0]
        assert "VALUES(fetched_at) > news_item.fetched_at" in assignment
        assert "VALUES(fetched_at) >= news_item.fetched_at" not in assignment
    assert update.index("title =") < update.index("fetched_at =")
    assert update.index("metadata_json =") < update.index("fetched_at =")
    assert update.index("event_id =") < update.index("fetched_at =")


def test_unchanged_or_old_event_link_is_not_overwritten_and_event_update_is_checked():
    update = sql(repository().build_item_upsert(item(), event_id=41)).split(
        "ON DUPLICATE KEY UPDATE", 1
    )[1]
    assert "event_id =" in update
    assert "news_item.event_id IS NULL" in update
    assert "VALUES(event_id)" in update
    assert "VALUES(fetched_at) >= news_item.fetched_at" in update
    assert not re.search(r"(?:^|,)\s*event_id\s*=\s*VALUES\(event_id\)", update)


def test_equal_content_hash_does_not_become_a_unique_upsert_target():
    first = item(external_id="701")
    second = item(external_id="702")
    assert first.content_hash == second.content_hash
    assert first.item_key != second.item_key
    assert "ON DUPLICATE KEY UPDATE" in sql(repository().build_item_upsert(second, event_id=41))


def test_source_and_event_upserts_are_mysql_native_and_only_set_last_insert_id_on_duplicate():
    module = repository()
    source = module.SourceConfiguration(
        provider="github", external_id="acme/agent", source_type="github_release",
        name="Acme releases",
    )
    for statement in (
        module.build_source_upsert(source),
        module.build_event_upsert(sha256(b"event").hexdigest(), "Release v1"),
    ):
        update = sql(statement).split("ON DUPLICATE KEY UPDATE", 1)[1].strip()
        assert re.fullmatch(r"id = LAST_INSERT_ID\([a-z_]+\.id\)", update)


def test_mysql_upsert_is_not_mistaken_for_sqlite_concurrency_test():
    statement = repository().build_item_upsert(item(), event_id=41)
    with pytest.raises(UnsupportedCompilationError):
        statement.compile(dialect=sqlite.dialect())
