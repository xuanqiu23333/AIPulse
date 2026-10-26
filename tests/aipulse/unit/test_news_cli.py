"""Focused safety check for the manual news entry point."""

from aipulse.news.cli import build_requests, parse_args


def test_manual_request_selection_is_explicit_and_bounded():
    args = parse_args([
        "--check", "--provider", "github", "--github-repo", "owner/repo",
        "--provider", "hn", "--hn-list", "topstories", "--limit-per-source", "5",
    ])

    requests = build_requests(args)

    assert [(request.source.provider, request.source.external_id) for request in requests] == [
        ("github", "owner/repo"), ("hn", "topstories"),
    ]
    assert args.limit_per_source == 5
