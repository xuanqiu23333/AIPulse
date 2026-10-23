"""Offline event grouping must preserve original records and strong identities."""

from datetime import datetime, timezone
from importlib import import_module

import pytest

from aipulse.news.types import NewsCandidate


NOW = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)


def modules():
    try:
        return import_module("aipulse.news.normalize"), import_module("aipulse.news.merge")
    except ModuleNotFoundError as exc:
        pytest.fail(f"News processing module is missing: {exc.name}", pytrace=False)


def item(provider, external_id, url, *, title="An update", source_id=1):
    normalize, _ = modules()
    return normalize.normalize_candidate(
        NewsCandidate(
            provider=provider, external_id=external_id, original_url=url,
            title=title, raw_excerpt="Text", published_at=NOW,
            fetched_at=NOW, metadata={},
        ), source_id,
    )


def grouped(*items):
    normalize, merge = modules()
    return merge.merge_events(normalize.deduplicate_batch(items))


def test_arxiv_versions_have_one_event_but_one_original_identity():
    first = item("arxiv", "2601.12345", "https://arxiv.org/abs/2601.12345v1")
    second = item("arxiv", "2601.12345", "https://arxiv.org/abs/2601.12345v2", source_id=2)
    groups = grouped(first, second)
    assert len(groups) == 1
    assert groups[0].canonical_identity == "arxiv:2601.12345"
    assert [member.item_key for member in groups[0].members] == [("arxiv", "2601.12345")]
    assert groups[0].members[0].source_ids == (1, 2)


def test_hn_paper_link_joins_arxiv_with_two_original_items():
    paper = item("arxiv", "2601.12345", "https://arxiv.org/abs/2601.12345v1")
    story = item("hn", "9001", "https://arxiv.org/abs/2601.12345v2?utm_source=hn", source_id=4)
    groups = grouped(story, paper)
    assert len(groups) == 1
    assert {m.item_key for m in groups[0].members} == {("arxiv", "2601.12345"), ("hn", "9001")}
    assert {m.reason for m in groups[0].members} == {"arxiv_id"}
    assert all(m.event_key == groups[0].event_key for m in groups[0].members)


def test_hn_release_link_joins_github_release_by_repo_and_tag():
    release = item("github", "701", "https://github.com/LangChain-AI/LangGraph/releases/tag/v1.0")
    story = item("hn", "9002", "https://github.com/langchain-ai/langgraph/releases/tag/v1.0?fbclid=x", source_id=3)
    groups = grouped(story, release)
    assert len(groups) == 1
    assert groups[0].canonical_identity == "github-release:langchain-ai/langgraph:v1.0"
    assert len(groups[0].members) == 2
    assert {m.reason for m in groups[0].members} == {"github_release"}


def test_hn_text_story_keeps_independent_event_and_traceable_source():
    story = item("hn", "9003", "https://news.ycombinator.com/item?id=9003", source_id=11)
    other = item("hn", "9004", "https://news.ycombinator.com/item?id=9004", source_id=12)
    groups = grouped(story, other)
    assert len(groups) == 2
    assert {group.canonical_identity for group in groups} == {"hn:9003", "hn:9004"}
    assert {m.source_ids for group in groups for m in group.members} == {(11,), (12,)}


def test_similar_titles_for_distinct_papers_do_not_merge():
    first = item("arxiv", "2601.12345", "https://arxiv.org/abs/2601.12345", title="AI Research")
    second = item("arxiv", "2601.12346", "https://arxiv.org/abs/2601.12346", title="AI Research")
    groups = grouped(first, second)
    assert len(groups) == 2
    assert all(group.needs_review is False for group in groups)


def test_identical_normalized_ordinary_url_merges_without_title_similarity():
    first = item("hn", "9005", "https://example.org/post?issue=1&utm_source=hn", title="A")
    second = item("other", "x", "https://EXAMPLE.org:443/post?issue=1#comments", title="B")
    groups = grouped(first, second)
    assert len(groups) == 1
    assert groups[0].canonical_identity == "url:https://example.org/post?issue=1"
    assert {m.item_key for m in groups[0].members} == {("hn", "9005"), ("other", "x")}


def test_equal_title_and_body_with_different_urls_do_not_merge():
    first = item("hn", "9006", "https://example.org/post/1", title="Same title")
    second = item("other", "x", "https://example.org/post/2", title="Same title")
    assert len(grouped(first, second)) == 2


def test_event_key_is_stable_sha256_and_input_order_does_not_change_groups():
    from hashlib import sha256

    paper = item("arxiv", "2601.12345", "https://arxiv.org/abs/2601.12345")
    story = item("hn", "9007", "https://arxiv.org/abs/2601.12345v2")
    text = item("hn", "9008", "https://news.ycombinator.com/item?id=9008")
    groups = grouped(paper, story, text)
    assert groups == grouped(text, story, paper) == grouped(paper, story, text)
    paper_group = next(g for g in groups if g.canonical_identity == "arxiv:2601.12345")
    assert paper_group.event_key == sha256(b"aipulse:event:v1:arxiv:2601.12345").hexdigest()
    assert len(paper_group.event_key) == 64


def test_arxiv_lookalike_domain_cannot_claim_paper_identity():
    paper = item("arxiv", "2601.12345", "https://arxiv.org/abs/2601.12345")
    fake = item("hn", "9009", "https://arxiv.org.evil.example/abs/2601.12345")
    assert len(grouped(paper, fake)) == 2


def test_github_release_like_path_with_different_tag_stays_distinct():
    first = item("github", "701", "https://github.com/acme/agent/releases/tag/v1")
    other = item("hn", "9010", "https://github.com/acme/agent/releases/tag/v2")
    assert len(grouped(first, other)) == 2
