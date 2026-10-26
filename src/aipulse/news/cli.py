"""Run one explicit, bounded real-news collection and scoring batch."""

import argparse
import asyncio
import math
import os
import re

import httpx

from aipulse.config import DatabaseConfigurationError, load_database_settings
from aipulse.news.manual_run import build_collectors, run_collection, schema_ready
from aipulse.news.repository import SourceConfiguration
from aipulse.news.service import IngestionRequest
from aipulse.storage.db import (
    UnsafeDevelopmentDatabaseError,
    close_engine,
    create_development_engine,
    create_session_factory,
)


_PROVIDERS = ("github", "arxiv", "hn")
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\Z")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Require a mode, explicit providers, and finite collection budgets."""
    parser = argparse.ArgumentParser(description="One bounded AIPulse news collection")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only database and wiring preflight")
    mode.add_argument("--execute", action="store_true", help="collect, persist, and score real news")
    parser.add_argument("--provider", action="append", choices=_PROVIDERS, required=True)
    parser.add_argument("--github-repo", action="append", default=[], metavar="OWNER/REPO")
    parser.add_argument("--arxiv-query", action="append", default=[], metavar="QUERY")
    parser.add_argument("--hn-list", action="append", choices=("topstories", "newstories"), default=[])
    parser.add_argument("--limit-per-source", type=int, default=5, metavar="N")
    parser.add_argument("--http-timeout", type=float, default=10.0, metavar="SECONDS")
    args = parser.parse_args(argv)
    if not 1 <= args.limit_per_source <= 100:
        parser.error("--limit-per-source must be between 1 and 100")
    if not math.isfinite(args.http_timeout) or not 1 <= args.http_timeout <= 30:
        parser.error("--http-timeout must be between 1 and 30 seconds")
    selected = set(args.provider)
    targets = {"github": args.github_repo, "arxiv": args.arxiv_query, "hn": args.hn_list}
    for provider, values in targets.items():
        if provider in selected and not values:
            parser.error(f"--provider {provider} requires its source option")
        if provider not in selected and values:
            parser.error(f"Source option given without --provider {provider}")
    for repo in args.github_repo:
        if (len(repo) > 191 or not _REPOSITORY.fullmatch(repo)
                or repo.rsplit("/", 1)[1] in {".", ".."}):
            parser.error("Invalid --github-repo")
    for query in args.arxiv_query:
        if not query.strip() or len(query) > 191 or any(ord(char) < 32 for char in query):
            parser.error("Invalid --arxiv-query")
    return args


def build_requests(args: argparse.Namespace) -> tuple[IngestionRequest, ...]:
    """Turn selected targets into stable source identities, without database IO."""
    targets = {"github": args.github_repo, "arxiv": args.arxiv_query, "hn": args.hn_list}
    requests = []
    seen = set()
    for provider in args.provider:
        for raw_target in targets[provider]:
            target = raw_target.strip()
            identity = (provider, target)
            if identity in seen:
                continue
            seen.add(identity)
            requests.append(IngestionRequest(SourceConfiguration(
                provider=provider,
                external_id=target,
                source_type=provider,
                name=target,
            )))
    return tuple(requests)


async def _run(args: argparse.Namespace) -> int:
    requests = build_requests(args)
    try:
        settings = load_database_settings()
        engine = create_development_engine(settings)
    except (DatabaseConfigurationError, UnsafeDevelopmentDatabaseError):
        print("Development database configuration is invalid")
        return 2
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(args.http_timeout)) as client:
            collectors = build_collectors(
                client, providers=_PROVIDERS, limit_per_source=args.limit_per_source,
                http_timeout=args.http_timeout, github_token=os.environ.get("GITHUB_TOKEN"),
            )
            print("Collectors assembled: github, arxiv, hn")
            print(f"Selected sources: {len(requests)}; per-source limit: {args.limit_per_source}")
            try:
                ready = await schema_ready(engine)
            except Exception as exc:
                print(f"Development database preflight failed: {type(exc).__name__}")
                return 2
            print(f"Development news schema (0002/0003/0004): {'ready' if ready else 'not ready'}")
            if not ready:
                return 2
            if args.check:
                return 0
            report = await run_collection(
                requests, collectors=collectors,
                session_factory=create_session_factory(engine),
            )
            for source in report.ingestion.sources:
                print(
                    f"source {source.provider}/{source.external_id}: {source.status.value}; "
                    f"fetched={source.fetched_count}, accepted={source.accepted_count}, "
                    f"processed={source.processed_count}, events={source.event_count}"
                    + (f", error={source.error_code}" if source.error_code else "")
                )
            for outcome in report.scores:
                score = outcome.score
                if score is None:
                    print(f"score event={outcome.event_id}: failed; error={outcome.error_code}")
                else:
                    print(
                        f"score event={outcome.event_id}: {outcome.status.value}; "
                        f"category={score.category}, importance={score.importance_score}"
                    )
            print(
                f"Run totals: processed={report.ingestion.processed_count}, "
                f"sources_ok={report.ingestion.success_count}, "
                f"sources_partial={report.ingestion.partial_count}, "
                f"sources_failed={report.ingestion.failed_count}, "
                f"events_scored={sum(outcome.score is not None for outcome in report.scores)}"
            )
            return 1 if (report.ingestion.failed_count or any(
                outcome.score is None for outcome in report.scores
            )) else 0
    finally:
        await close_engine(engine)


def main(argv: list[str] | None = None) -> int:
    """Run once; never start a scheduler or follow returned cursors."""
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
