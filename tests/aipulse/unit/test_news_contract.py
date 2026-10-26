"""Offline contract tests for future source adapters."""

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module

import pytest
from pydantic import ValidationError


def news_types():
    try:
        return import_module("aipulse.news.types")
    except ModuleNotFoundError as exc:
        pytest.fail(f"News contract module is missing: {exc.name}", pytrace=False)


def collector_base():
    try:
        return import_module("aipulse.news.collectors.base")
    except ModuleNotFoundError as exc:
        pytest.fail(f"Collector base module is missing: {exc.name}", pytrace=False)


def candidate_data():
    return {
        "provider": "github",
        "external_id": "12345",
        "original_url": "https://github.com/example/repo/releases/tag/v1",
        "title": "Release v1",
        "raw_excerpt": "One short release note.",
        "published_at": datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc),
        "metadata": {"release_id": 12345, "tags": ["v1"]},
    }


def test_candidate_requires_nonempty_identity_and_title():
    Candidate = news_types().NewsCandidate
    data = candidate_data()
    candidate = Candidate.model_validate(data)
    assert candidate.provider == "github"
    assert candidate.external_id == "12345"
    for name, value in (("provider", "  "), ("external_id", ""), ("title", " ")):
        with pytest.raises(ValidationError):
            Candidate.model_validate({**data, name: value})
    with pytest.raises(ValidationError):
        Candidate.model_validate({k: v for k, v in data.items() if k != "external_id"})
    with pytest.raises(ValidationError):
        Candidate.model_validate({**data, "id": 7})


@pytest.mark.parametrize("url", ["ftp://example.com/x", "javascript:alert(1)", "https://"])
def test_candidate_rejects_non_http_or_invalid_url(url):
    Candidate = news_types().NewsCandidate
    with pytest.raises(ValidationError):
        Candidate.model_validate({**candidate_data(), "original_url": url})


def test_candidate_accepts_http_and_https_without_embedded_credentials():
    Candidate = news_types().NewsCandidate
    data = candidate_data()
    assert str(Candidate.model_validate({**data, "original_url": "http://example.com/x"}).original_url) == "http://example.com/x"
    with pytest.raises(ValidationError):
        Candidate.model_validate({**data, "original_url": "https://user:secret@example.com/x"})


def test_candidate_rejects_naive_timestamps_instead_of_guessing_timezone():
    Candidate = news_types().NewsCandidate
    data = candidate_data()
    for field in ("published_at", "fetched_at"):
        with pytest.raises(ValidationError):
            Candidate.model_validate({**data, field: datetime(2026, 10, 15, 8, 0)})


def test_candidate_normalizes_aware_timestamps_to_utc():
    Candidate = news_types().NewsCandidate
    china = timezone(timedelta(hours=8))
    data = candidate_data()
    data["published_at"] = datetime(2026, 10, 15, 16, 0, tzinfo=china)
    data["fetched_at"] = datetime(2026, 10, 15, 17, 0, tzinfo=china)
    candidate = Candidate.model_validate(data)
    assert candidate.published_at == datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    assert candidate.fetched_at == datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
    assert candidate.published_at.tzinfo is timezone.utc
    assert candidate.fetched_at.tzinfo is timezone.utc


def test_candidate_bounds_excerpt_and_requires_json_metadata():
    Candidate = news_types().NewsCandidate
    data = candidate_data()
    with pytest.raises(ValidationError):
        Candidate.model_validate({**data, "raw_excerpt": "x" * 4001})
    with pytest.raises(ValidationError):
        Candidate.model_validate({**data, "metadata": {"object": object()}})
    with pytest.raises(ValidationError):
        Candidate.model_validate({**data, "metadata": {"not_finite": float("nan")}})
    with pytest.raises(ValidationError):
        Candidate.model_validate({**data, "metadata": {"huge": "x" * 17000}})


def test_result_distinguishes_success_partial_and_failed_pages():
    types = news_types()
    source = types.CollectorSource(source_id=7, provider="github", external_id="owner/repo")
    candidate = types.NewsCandidate.model_validate(candidate_data())
    success = types.CollectionResult(
        source_id=source.source_id, candidates=[candidate], status="success", fetched_count=1,
        next_cursor="page-2",
    )
    partial = types.CollectionResult(
        source_id=source.source_id, candidates=[candidate], status="partial", fetched_count=1,
        error_code="rate_limit", next_cursor="page-2",
    )
    failed = types.CollectionResult(
        source_id=source.source_id, candidates=[], status="failed", fetched_count=0,
        error_code="timeout",
    )
    rejected_page = types.CollectionResult(
        source_id=source.source_id, candidates=[], status="failed", fetched_count=3,
        error_code="invalid_response",
    )
    assert success.error_code is None and success.error_summary is None
    assert partial.error_code.value == "rate_limit" and partial.error_summary
    assert failed.error_code.value == "timeout" and failed.error_summary
    assert rejected_page.fetched_count == 3
    assert partial.next_cursor == "page-2"


def test_result_rejects_inconsistent_status_counts_and_raw_error_text():
    types = news_types()
    candidate = types.NewsCandidate.model_validate(candidate_data())
    base = {"source_id": 7, "candidates": [candidate], "fetched_count": 1}
    invalid = [
        {**base, "status": "success", "error_code": "timeout"},
        {**base, "status": "partial"},
        {**base, "status": "failed", "error_code": "unavailable"},
        {**base, "status": "partial", "error_code": "rate_limit", "fetched_count": 0},
        {"source_id": 7, "candidates": [], "fetched_count": 0, "status": "failed"},
    ]
    for item in invalid:
        with pytest.raises(ValidationError):
            types.CollectionResult.model_validate(item)
    with pytest.raises(ValidationError) as error:
        types.CollectionResult.model_validate({**base, "status": "partial", "error_code": "timeout", "error_summary": "Bearer top-secret"})
    assert "top-secret" not in str(error.value)


def test_collector_errors_have_only_allowlisted_codes_and_safe_messages():
    types = news_types()
    assert {member.value for member in types.CollectorErrorCode} == {
        "timeout", "rate_limit", "invalid_response", "unavailable", "unexpected"
    }
    error = types.CollectorError(types.CollectorErrorCode.RATE_LIMIT)
    assert error.code.value == "rate_limit"
    assert "Bearer" not in str(error)
    with pytest.raises(TypeError):
        types.CollectorError(types.CollectorErrorCode.UNEXPECTED, "Bearer top-secret")
    with pytest.raises(ValueError) as unknown:
        types.CollectorError("Bearer top-secret")
    assert "top-secret" not in str(unknown.value)


def test_base_collector_is_abstract_and_mock_implementation_obeys_contract():
    types = news_types()
    BaseCollector = collector_base().BaseCollector
    with pytest.raises(TypeError):
        BaseCollector()

    class MissingCollector(BaseCollector):
        pass

    with pytest.raises(TypeError):
        MissingCollector()

    class MockCollector(BaseCollector):
        async def collect(self, source, *, cursor=None):
            assert source.source_id == 7
            assert cursor == "page-2"
            return types.CollectionResult(
                source_id=source.source_id, candidates=[], status="success", fetched_count=0
            )

    source = types.CollectorSource(source_id=7, provider="github", external_id="owner/repo")
    result = asyncio.run(MockCollector().collect(source, cursor="page-2"))
    assert isinstance(result, types.CollectionResult)
    assert result.status.value == "success"
