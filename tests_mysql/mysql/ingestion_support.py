"""Test-only adapter that preflights the very Session transaction used by ingestion.

The production service controls ``session.begin()``. This adapter inserts the
existing read-only MySQL identity check after that transaction starts and before
the repository can execute its first statement. It does not create an Engine.
"""

import sys
from collections.abc import Callable

from tests_mysql.mysql.safety import (
    UnsafeMysqlIntegration,
    require_write_permission,
    verify_live_test_connection,
)


class GuardedSessionFactory:
    def __init__(
        self,
        session_factory: Callable,
        *,
        cli_enabled: Callable[[], bool],
        env_value: Callable[[], str | None],
        verify_connection: Callable = verify_live_test_connection,
    ) -> None:
        self._session_factory = session_factory
        self._cli_enabled = cli_enabled
        self._env_value = env_value
        self._verify_connection = verify_connection

    def __call__(self):
        return _GuardedSessionContext(self)


class _GuardedSessionContext:
    def __init__(self, factory: GuardedSessionFactory) -> None:
        self._factory = factory
        self._raw = None

    async def __aenter__(self):
        self._raw = self._factory._session_factory()
        session = await self._raw.__aenter__()
        return _GuardedSession(session, self._factory)

    async def __aexit__(self, exc_type, exc, tb):
        return await self._raw.__aexit__(exc_type, exc, tb)


class _GuardedSession:
    def __init__(self, session, factory: GuardedSessionFactory) -> None:
        self._session = session
        self._factory = factory
        self._verified = False

    def begin(self):
        return _GuardedBegin(self)

    async def execute(self, *args, **kwargs):
        if not self._verified:
            raise UnsafeMysqlIntegration("MySQL test transaction was not preflighted")
        return await self._session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        if not self._verified:
            raise UnsafeMysqlIntegration("MySQL test transaction was not preflighted")
        return await self._session.scalar(*args, **kwargs)


class _GuardedBegin:
    def __init__(self, guarded: _GuardedSession) -> None:
        self._guarded = guarded
        self._transaction = None

    async def __aenter__(self):
        factory = self._guarded._factory
        require_write_permission(
            cli_enabled=factory._cli_enabled(), env_value=factory._env_value(),
        )
        self._transaction = self._guarded._session.begin()
        await self._transaction.__aenter__()
        try:
            connection = await self._guarded._session.connection()
            await factory._verify_connection(connection)
        except BaseException:
            await self._transaction.__aexit__(*sys.exc_info())
            raise
        self._guarded._verified = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._guarded._verified = False
        return await self._transaction.__aexit__(exc_type, exc, tb)
