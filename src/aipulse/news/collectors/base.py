"""Asynchronous provider adapter boundary without infrastructure coupling."""

from abc import ABC, abstractmethod

from aipulse.news.types import CollectionResult, CollectorSource


class BaseCollector(ABC):
    """Collect one bounded page from a configured source."""

    @abstractmethod
    async def collect(
        self, source: CollectorSource, *, cursor: str | None = None
    ) -> CollectionResult:
        """Return candidates and source status for one page or attempt."""
