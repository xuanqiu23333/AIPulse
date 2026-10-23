"""Test-only guards and cleanup selectors for real MySQL integration tests."""

import re
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, text, tuple_

from aipulse.models.entities import NewsEvent, NewsItem, NewsSource


EXPECTED_TABLES = frozenset({
    "news_source", "news_event", "news_item", "daily_brief",
    "research_task", "research_evidence", "agent_run_log", "alembic_version",
})
EXPECTED_REVISION = "20261016_0002"
_NAMESPACE = re.compile(r"aipulse-it-[0-9a-f]{32}-$")
_EVENT_KEY = re.compile(r"[0-9a-f]{64}$")


class UnsafeMysqlIntegration(RuntimeError):
    """A test write prerequisite failed before any mutating statement."""


def require_write_permission(*, cli_enabled: bool, env_value: str | None) -> None:
    """Require two independent, exact opt-ins; truthy substitutes are rejected."""
    if cli_enabled is not True or env_value != "1":
        raise UnsafeMysqlIntegration(
            "Real MySQL tests require --run-aipulse-mysql and "
            "AIPULSE_ALLOW_TEST_DB_WRITE=1"
        )


async def verify_live_test_connection(connection) -> None:
    """Read-only preflight of the exact connection about to run test DML."""
    identity = (await connection.execute(text(
        "SELECT DATABASE() AS db_name, CURRENT_USER() AS account_name, "
        "VERSION() AS server_version"
    ))).one()
    if (identity.db_name, identity.account_name, identity.server_version) != (
        "aipulse_test_db", "aipulse_test@localhost", "8.0.44"
    ):
        raise UnsafeMysqlIntegration("Unexpected MySQL test connection identity")

    tables = frozenset((await connection.execute(text(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = 'aipulse_test_db'"
    ))).scalars().all())
    if tables != EXPECTED_TABLES:
        raise UnsafeMysqlIntegration("MySQL test schema does not match approved tables")

    revisions = (await connection.execute(text(
        "SELECT version_num FROM alembic_version"
    ))).scalars().all()
    if revisions != [EXPECTED_REVISION]:
        raise UnsafeMysqlIntegration("Unexpected Alembic test schema revision")


@dataclass
class TestRows:
    """Track only this test's UUID identities for ordered, exact cleanup."""

    __test__ = False
    namespace: str
    sources: set[tuple[str, str]] = field(default_factory=set)
    items: set[tuple[str, str]] = field(default_factory=set)
    events: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _NAMESPACE.fullmatch(self.namespace):
            raise UnsafeMysqlIntegration("Invalid integration test namespace")

    def record_source(self, external_id: str, *, provider: str = "github") -> None:
        self._require_owned(external_id)
        self._require_provider(provider)
        self.sources.add((provider, external_id))

    def record_item(self, external_id: str, *, provider: str = "github") -> None:
        self._require_owned(external_id)
        self._require_provider(provider)
        self.items.add((provider, external_id))

    def record_event(self, event_key: str, title: str) -> None:
        if not _EVENT_KEY.fullmatch(event_key) or not title.startswith(self.namespace):
            raise UnsafeMysqlIntegration("Event is outside this test namespace")
        self.events.setdefault(event_key, title)

    def _require_owned(self, external_id: str) -> None:
        if not external_id.startswith(self.namespace):
            raise UnsafeMysqlIntegration("Record is outside this test namespace")

    @staticmethod
    def _require_provider(provider: str) -> None:
        if provider not in {"github", "arxiv", "hn"}:
            raise UnsafeMysqlIntegration("Unexpected integration test provider")

    def cleanup_statements(self):
        """Delete exact tracked identities, child first; never a whole table."""
        statements = []
        for provider in sorted({provider for provider, _ in self.items}):
            statements.append(delete(NewsItem).where(
                NewsItem.provider == provider,
                NewsItem.external_id.in_(sorted(
                    external_id for item_provider, external_id in self.items
                    if item_provider == provider
                )),
            ))
        if self.events:
            statements.append(delete(NewsEvent).where(
                tuple_(NewsEvent.event_key, NewsEvent.title).in_(
                    [(key, self.events[key]) for key in sorted(self.events)]
                ),
            ))
        for provider in sorted({provider for provider, _ in self.sources}):
            statements.append(delete(NewsSource).where(
                NewsSource.provider == provider,
                NewsSource.external_id.in_(sorted(
                    external_id for source_provider, external_id in self.sources
                    if source_provider == provider
                )),
            ))
        return statements


async def cleanup_owned_rows(session, rows: TestRows) -> None:
    """Skip DML for rolled-back tests; delete only exact committed identities."""
    for statement in rows.cleanup_statements():
        count = await session.scalar(
            select(func.count()).select_from(statement.table).where(statement.whereclause)
        )
        if count:
            await session.execute(statement)
