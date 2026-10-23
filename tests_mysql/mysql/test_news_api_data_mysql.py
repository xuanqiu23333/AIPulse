"""Opt-in populated news API tests; never run without separate write approval."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select, update

from aipulse.models.entities import NewsEvent
from aipulse.news.normalize import deduplicate_batch, normalize_candidate
from aipulse.news.repository import NewsRepository, SourceConfiguration
from aipulse.news.types import NewsCandidate
from tests_mysql.mysql.api_data_support import (
    build_data_api, normalized_schema_definitions, prepare_api_data_runtime,
)
from tests_mysql.mysql.readonly import capture_readonly_snapshot, verify_readonly_connection
from tests_mysql.mysql.safety import (
    TestRows, UnsafeMysqlIntegration, require_write_permission,
)
from tests_mysql.mysql.scoring_support import cleanup_scoring_rows


pytestmark = pytest.mark.asyncio
OBSERVED_AT = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
BASE_TIME = datetime(2026, 10, 15, 8, 0)
SPECS = (
    # label, score, category, created-hour-offset, original providers
    ("a", 90, "model", 0, ("github",)),
    ("b", 90, "model", 1, ("github",)),
    ("f", 90, "tool", 1, ("github",)),
    ("c", 70, "paper", 2, ("arxiv", "hn")),
    ("d", 0, "discussion", 3, ("hn",)),
    ("e", None, "other", 4, ("github",)),
)


def parse_utc_json_timestamp(value: str) -> datetime:
    """Assert the public HTTP representation is an ISO 8601 UTC timestamp."""
    if not isinstance(value, str):
        raise TypeError("Expected an ISO 8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid ISO 8601 UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Expected an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _event_json_view(body: dict) -> SimpleNamespace:
    """Check the public wire fields without replaying ORM input validators."""
    assert isinstance(body, dict)
    assert type(body["event_id"]) is int and body["event_id"] > 0
    assert isinstance(body["event_key"], str) and len(body["event_key"]) == 64
    assert isinstance(body["title"], str) and isinstance(body["status"], str)
    assert body["category"] is None or isinstance(body["category"], str)
    score = body["importance_score"]
    assert score is None or (type(score) is int and 0 <= score <= 100)
    assert body["score_reason"] is None or isinstance(body["score_reason"], str)
    assert body["rule_version"] is None or isinstance(body["rule_version"], str)
    assert isinstance(body["providers"], list)
    assert all(isinstance(provider, str) for provider in body["providers"])
    assert type(body["item_count"]) is int and body["item_count"] >= 0
    return SimpleNamespace(
        **(body | {
            "created_at": parse_utc_json_timestamp(body["created_at"]),
            "updated_at": parse_utc_json_timestamp(body["updated_at"]),
            "providers": tuple(body["providers"]),
        })
    )


def page_json_view(body: dict) -> SimpleNamespace:
    assert isinstance(body, dict)
    if not {"page", "page_size", "total", "items"} <= body.keys():
        raise ValueError("Incomplete HTTP news page")
    assert type(body["page"]) is int and body["page"] >= 1
    assert type(body["page_size"]) is int and body["page_size"] >= 1
    assert type(body["total"]) is int and body["total"] >= 0
    assert isinstance(body["items"], list)
    return SimpleNamespace(
        page=body["page"], page_size=body["page_size"], total=body["total"],
        items=tuple(_event_json_view(item) for item in body["items"]),
    )


def detail_json_view(body: dict) -> SimpleNamespace:
    event = _event_json_view(body)
    assert body["summary"] is None or isinstance(body["summary"], str)
    assert isinstance(body["sources"], list)
    sources = []
    for source in body["sources"]:
        assert isinstance(source, dict)
        for field in ("provider", "external_id", "title", "original_url", "source_name"):
            assert isinstance(source[field], str)
        sources.append(SimpleNamespace(
            **(source | {
                "published_at": (
                    parse_utc_json_timestamp(source["published_at"])
                    if source["published_at"] is not None else None
                ),
                "fetched_at": parse_utc_json_timestamp(source["fetched_at"]),
            })
        ))
    return SimpleNamespace(**(vars(event) | {
        "summary": body["summary"], "sources": tuple(sources),
    }))


@dataclass
class ApiCase:
    client: httpx.AsyncClient
    ids: dict[str, int]
    namespace: str

    async def page(self, query: str = "") -> SimpleNamespace:
        response = await self.client.get(f"/api/v1/news/events{query}")
        assert response.status_code == 200, response.status_code
        return page_json_view(response.json())


async def _seed(runtime, rows: TestRows) -> dict[str, int]:
    """Use the real Repository for identities, then set fixed display fields."""
    ids = {}
    async with runtime.guarded_write_factory() as session:
        async with session.begin():
            repository = NewsRepository(session)
            sources = {}
            for provider in ("github", "arxiv", "hn"):
                external_id = rows.namespace + provider + "-source"
                rows.record_source(external_id, provider=provider)
                sources[provider] = await repository.ensure_source(SourceConfiguration(
                    provider=provider, external_id=external_id,
                    source_type=provider, name=rows.namespace + provider + " name",
                    url=f"https://example.invalid/{provider}",
                ))

            for label, score, category, hour, providers in SPECS:
                title = rows.namespace + label + " event"
                key = sha256((rows.namespace + label).encode()).hexdigest()
                rows.record_event(key, title)
                event_id = await repository.ensure_event(key, title)
                ids[label] = event_id
                for provider in providers:
                    external_id = rows.namespace + label + "-" + provider
                    rows.record_item(external_id, provider=provider)
                    candidate = NewsCandidate(
                        provider=provider, external_id=external_id,
                        original_url=f"https://example.invalid/{rows.namespace}{label}/{provider}",
                        title=rows.namespace + label + " " + provider + " original",
                        raw_excerpt="Synthetic integration fixture",
                        published_at=OBSERVED_AT, fetched_at=OBSERVED_AT,
                        metadata={"tag_name": "v1"} if provider == "github" else
                                 {"version": "v1"} if provider == "arxiv" else
                                 {"score": 7},
                    )
                    item = deduplicate_batch([
                        normalize_candidate(candidate, sources[provider])
                    ])[0]
                    await repository.upsert_item(item, event_id=event_id)

                created_at = BASE_TIME.replace(hour=BASE_TIME.hour + hour)
                await session.execute(
                    update(NewsEvent).where(NewsEvent.id == event_id).values(
                        created_at=created_at, updated_at=created_at,
                        category=category if score is not None else None,
                        importance_score=score,
                        score_reason=("Synthetic score reason " + label) if score is not None else None,
                        rule_version="api-data-fixture-v1" if score is not None else None,
                    )
                )
    return ids


async def _assert_owned_gone(runtime, rows: TestRows) -> None:
    async with runtime.api_factory() as session:
        await verify_readonly_connection(await session.connection())
        for statement in rows.cleanup_statements():
            count = await session.scalar(
                select(func.count()).select_from(statement.table).where(statement.whereclause)
            )
            if count:
                raise UnsafeMysqlIntegration("CLEANUP_INCOMPLETE: owned UUID rows remain")


@pytest_asyncio.fixture
async def case(pytestconfig):
    # This is a write suite. The directory collection hook skips by default;
    # this second check runs before credentials or either Engine can be built.
    permission = lambda: pytestconfig.getoption("--run-aipulse-mysql")
    permit = lambda: os.environ.get("AIPULSE_ALLOW_TEST_DB_WRITE")
    require_write_permission(cli_enabled=permission(), env_value=permit())
    runtime = prepare_api_data_runtime(cli_enabled=permission, env_value=permit)
    rows = TestRows(f"aipulse-it-{uuid4().hex}-")
    app = build_data_api(runtime)
    before = None
    seed_attempted = False
    try:
        async with app.router.lifespan_context(app):
            try:
                async with runtime.api_engine.connect() as connection:
                    before = await capture_readonly_snapshot(connection)
                if dict(before.counts)["news_event"] != 0:
                    raise UnsafeMysqlIntegration("Populated API suite requires an empty news_event table")

                seed_attempted = True
                ids = await _seed(runtime, rows)
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://aipulse.test"
                ) as client:
                    yield ApiCase(client=client, ids=ids, namespace=rows.namespace)
            finally:
                try:
                    if seed_attempted:
                        if rows.cleanup_statements():
                            # The existing helper checks event references before deleting;
                            # every statement is scoped by this run's UUID identities.
                            async with runtime.guarded_write_factory() as session:
                                async with session.begin():
                                    await cleanup_scoring_rows(session, rows)
                        await _assert_owned_gone(runtime, rows)
                    if seed_attempted and before is not None:
                        async with runtime.api_engine.connect() as connection:
                            after = await capture_readonly_snapshot(connection)
                        if after.counts != before.counts or (
                            normalized_schema_definitions(after)
                            != normalized_schema_definitions(before)
                        ):
                            raise UnsafeMysqlIntegration("CLEANUP_INCOMPLETE: counts or schema changed")
                except Exception as exc:
                    raise AssertionError(
                        f"CLEANUP_INCOMPLETE for {rows.namespace}; inspect only owned identities "
                        f"({type(exc).__name__})"
                    ) from None
    finally:
        await runtime.write_engine.dispose()


async def test_nonempty_list_total_is_event_count(case):
    page = await case.page()
    assert page.total == 6 and len(page.items) == 6
    assert sum(item.item_count for item in page.items) == 7
    assert {item.event_id for item in page.items} == set(case.ids.values())
    scored = next(item for item in page.items if item.event_id == case.ids["a"])
    assert scored.score_reason == "Synthetic score reason a"
    assert scored.rule_version == "api-data-fixture-v1"


async def test_shared_arxiv_hn_event_is_one_list_row(case):
    page = await case.page()
    paper = [item for item in page.items if item.event_id == case.ids["c"]]
    assert len(paper) == 1
    assert paper[0].item_count == 2 and paper[0].providers == ("arxiv", "hn")


async def test_default_score_order_puts_unscored_after_zero(case):
    page = await case.page()
    assert [item.event_id for item in page.items] == [
        case.ids[label] for label in ("f", "b", "a", "c", "d", "e")
    ]
    assert page.items[-2].importance_score == 0
    assert page.items[-1].importance_score is None


async def test_equal_scores_use_created_at_then_id(case):
    page = await case.page("?sort=score")
    assert [item.event_id for item in page.items[:3]] == [
        case.ids["f"], case.ids["b"], case.ids["a"]
    ]
    assert page.items[0].created_at == page.items[1].created_at
    assert page.items[0].event_id > page.items[1].event_id
    assert page.items[1].created_at > page.items[2].created_at


async def test_latest_uses_real_event_created_at(case):
    page = await case.page("?sort=latest")
    assert [item.event_id for item in page.items] == [
        case.ids[label] for label in ("e", "d", "c", "f", "b", "a")
    ]


async def test_category_filter_has_matching_total(case):
    page = await case.page("?category=model")
    assert page.total == 2
    assert [item.event_id for item in page.items] == [case.ids["b"], case.ids["a"]]


async def test_category_and_min_score_combine(case):
    page = await case.page("?category=paper&min_score=70")
    assert page.total == 1 and [item.event_id for item in page.items] == [case.ids["c"]]
    empty = await case.page("?category=paper&min_score=71")
    assert empty.total == 0 and empty.items == ()


async def test_three_pages_of_two_do_not_overlap_or_omit(case):
    pages = [await case.page(f"?page={page}&page_size=2") for page in (1, 2, 3)]
    assert all(page.total == 6 and len(page.items) == 2 for page in pages)
    ids = [item.event_id for page in pages for item in page.items]
    assert ids == [case.ids[label] for label in ("f", "b", "a", "c", "d", "e")]
    assert len(set(ids)) == 6


async def test_detail_returns_both_original_providers(case):
    response = await case.client.get(f"/api/v1/news/events/{case.ids['c']}")
    assert response.status_code == 200
    detail = detail_json_view(response.json())
    assert detail.event_id == case.ids["c"] and detail.item_count == 2
    assert {source.provider for source in detail.sources} == {"arxiv", "hn"}
    assert all(source.external_id.startswith(case.namespace) for source in detail.sources)
    assert all(source.source_name.startswith(case.namespace) for source in detail.sources)
    assert all(str(source.original_url).startswith("https://example.invalid/") for source in detail.sources)


async def test_unscored_detail_and_unknown_id(case):
    response = await case.client.get(f"/api/v1/news/events/{case.ids['e']}")
    assert response.status_code == 200
    detail = detail_json_view(response.json())
    assert detail.importance_score is None
    assert detail.score_reason is None and detail.rule_version is None
    missing = await case.client.get("/api/v1/news/events/987654321")
    assert missing.status_code == 404


async def test_utc_and_response_excludes_internal_fields(case):
    response = await case.client.get(f"/api/v1/news/events/{case.ids['c']}")
    assert response.status_code == 200
    body = response.json()
    detail_json_view(body)
    assert parse_utc_json_timestamp(body["created_at"]) == BASE_TIME.replace(
        hour=10, tzinfo=timezone.utc,
    )
    assert all(parse_utc_json_timestamp(source["fetched_at"]).utcoffset().total_seconds() == 0
               for source in body["sources"])
    assert "metadata_json" not in response.text
    assert "content_hash" not in response.text
    assert "source_id" not in response.text
    assert "password" not in response.text.lower()
