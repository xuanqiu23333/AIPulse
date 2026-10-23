"""Test-only scoring runtime gate and exact UUID cleanup helpers."""

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, select, tuple_

from aipulse.config import load_database_settings
from aipulse.models.entities import NewsEvent, NewsItem
from aipulse.storage.db import create_session_factory, create_test_engine
from tests_mysql.mysql.ingestion_support import GuardedSessionFactory
from tests_mysql.mysql.safety import (
    TestRows, UnsafeMysqlIntegration, require_write_permission,
)


@dataclass(frozen=True)
class ScoringRuntime:
    engine: object
    session_factory: object
    guarded_factory: GuardedSessionFactory


def prepare_scoring_runtime(
    *,
    cli_enabled: Callable[[], bool],
    env_value: Callable[[], str | None],
    load_settings: Callable = load_database_settings,
    make_engine: Callable = create_test_engine,
    make_sessions: Callable = create_session_factory,
) -> ScoringRuntime:
    """Reject missing opt-ins before loading credentials or constructing Engine."""

    require_write_permission(cli_enabled=cli_enabled(), env_value=env_value())
    engine = make_engine(load_settings())
    factory = make_sessions(engine)
    return ScoringRuntime(
        engine=engine, session_factory=factory,
        guarded_factory=GuardedSessionFactory(
            factory, cli_enabled=cli_enabled, env_value=env_value,
        ),
    )


async def cleanup_scoring_rows(session, rows: TestRows) -> None:
    """Delete only this run's records, refusing shared events with other items.

    The caller preflights the same connection and owns commit/rollback.
    """

    for statement in rows.cleanup_statements():
        if statement.table.name == "news_event":
            identities = [(key, rows.events[key]) for key in sorted(rows.events)]
            event_ids = (await session.execute(
                select(NewsEvent.id).where(
                    tuple_(NewsEvent.event_key, NewsEvent.title).in_(identities)
                )
            )).scalars().all()
            if event_ids and await session.scalar(
                select(func.count()).select_from(NewsItem).where(
                    NewsItem.event_id.in_(event_ids)
                )
            ):
                raise UnsafeMysqlIntegration(
                    "Owned event still has news items outside the exact cleanup set"
                )
        count = await session.scalar(
            select(func.count()).select_from(statement.table).where(statement.whereclause)
        )
        if count:
            await session.execute(statement)
